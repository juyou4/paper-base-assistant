"""从工作台 HTTP 入口验证重启续读、跨表隔离、真实写后回读及旧确认失效。"""

import http.client
import json
import os
import tempfile
import threading
import unittest
import uuid
from contextlib import closing
from http.server import ThreadingHTTPServer
from unittest.mock import patch

from app import Application, Handler


PAPER = "https://arxiv.org/abs/2005.11401v4"
TITLE = "Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks"
HTML = """<article><h1>Resource paper</h1><p id="code">Our code is available at
<a href="https://github.com/paper-lab/model">the repository</a>.</p>
<figure id="F1"><figcaption>Figure 1: A model overview.</figcaption></figure></article>"""


class WorkspaceHistoryHTTPTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.directory = tmp.name
        env = patch.dict(os.environ, PAPER_AGENT_PROVIDER="rules")
        env.start()
        self.addCleanup(env.stop)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.app = Application("local", tmp.name)
        self.addCleanup(lambda: self.server.app.close())
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.state, self.cookie = self.http("/api/state")

    def http(self, path, data=None, cookie=None, status=200):
        headers = {"Content-Type": "application/json"}
        if cookie:
            headers["Cookie"] = cookie
        with closing(http.client.HTTPConnection(*self.server.server_address, timeout=8)) as conn:
            conn.request("GET" if data is None else "POST", path,
                         None if data is None else json.dumps(data).encode(), headers)
            reply = conn.getresponse()
            payload = json.loads(reply.read())
            self.assertEqual(reply.status, status, payload)
            return payload, reply.getheader("Set-Cookie", "").split(";")[0]

    def chat(self, message, **body):
        return self.http("/api/chat", {"message": message, **body}, self.cookie)[0]

    def history(self):
        return self.http("/api/workspace-history", cookie=self.cookie)[0]

    def change(self, action, **payload):
        state = self.history()
        return self.http("/api/workspace-history", {"action": action, "scope": state["scope"],
                         "conversation_id": state["active_id"], **payload}, self.cookie)[0]

    def restart(self):
        self.server.app.close()
        self.server.app = Application("local", self.directory)

    def test_restart_restores_readable_history_and_verified_focus_only_in_own_browser(self):
        self.server.app.table.upsert(PAPER, {"论文标题": TITLE, "论文链接": PAPER, "阅读笔记": "重读表二。"}, "seed")
        self.chat("再看看 RAG")
        before = self.history()
        self.assertEqual(before["focus"]["title"], TITLE)
        self.restart()
        reopened = self.history()
        self.assertEqual(reopened["active_id"], before["active_id"])
        self.assertEqual(reopened["messages"], before["messages"])
        self.assertEqual(reopened["focus"]["title"], TITLE)
        self.assertIn("重读表二", self.chat("它的阅读笔记呢")["message"])
        stranger, _ = self.http("/api/workspace-history")
        self.assertEqual(stranger["messages"], [])
        self.assertIsNone(stranger["focus"])
        self.assertNotEqual(stranger["active_id"], reopened["active_id"])
        self.change("settings", resume_focus=False)
        self.restart()
        self.assertIsNone(self.history()["focus"])
        self.assertGreater(len(self.history()["messages"]), 0)

    def test_confirmed_receipt_after_restart_and_transcript_deletion(self):
        self.server.app.table.add_field("源码地址", "text")
        self.assertIn("已为当前表保存偏好", self.chat("记住：代码链接优先写入「源码地址」")["message"])
        with patch("papers._read_html", return_value=HTML):
            preview = self.chat("提取这篇的代码仓库和图表 " + PAPER)
        items = preview["evidence_preview"]["papers"][0]["items"]
        code = next(i for i, item in enumerate(items) if item["kind"] == "code")
        self.assertEqual(items[code]["field"], "源码地址")
        self.assertEqual(self.server.app.table.rows(), [])
        result = self.chat("确认", pending_id=preview["pending_id"], evidence_selection=[[code]])
        again = self.chat("确认", pending_id=preview["pending_id"], evidence_selection=[[code]])
        self.assertEqual(result["verification"], again["verification"])
        self.restart()
        state = self.history()
        verified = [op for op in state["operations"] if op["status"] == "verified"]
        self.assertEqual(len(verified), 1, state["operations"])
        row = self.server.app.table.rows()[0]
        self.assertIn("https://github.com/paper-lab/model", row["fields"]["源码地址"])
        self.assertIn(row["id"], str(verified[0]["details"]))
        self.assertIn("源码地址", str(verified[0]["details"]))
        self.assertIn("#code", str(verified[0]["details"]))
        self.http("/api/chat", {"message": "确认回填", "pending_id": preview["pending_id"]}, self.cookie, 400)
        cleared = self.change("clear_history")
        self.assertEqual(cleared["messages"], [])
        self.assertEqual(cleared["operations"], state["operations"])
        self.assertEqual(len(self.server.app.table.rows()), 1)

    def test_switch_thread_and_table_invalidates_stale_choice_and_isolates_preferences(self):
        old = self.history()
        self.change("add_preference", text="指标优先写入「阅读笔记」")
        a = self.change("new")
        self.http("/api/chat", {"message": "看下这张表", "history_scope": old["scope"],
                  "history_conversation_id": old["active_id"]}, self.cookie, 400)
        self.assertEqual(self.history()["messages"], [])
        self.change("open", target_id=old["active_id"])
        with patch("papers._read_html", return_value=HTML):
            pending = self.chat("提取这篇的图表 " + PAPER)
        self.change("open", target_id=a["active_id"])
        self.http("/api/chat", {"message": "确认回填", "pending_id": pending["pending_id"]}, self.cookie, 400)
        self.assertEqual(self.server.app.table.rows(), [])
        self.server.app.table.upsert(PAPER, {"论文标题": TITLE, "论文链接": PAPER, "阅读笔记": "原数据表笔记"}, "seed")
        self.chat("再看看 RAG")
        self.server.app.table.create_table("收件箱", [{"name": "标题", "type": "text"}, {"name": "链接", "type": "url"}],
                                           uuid.uuid4().hex)
        self.chat("切换到数据表「收件箱」")
        other = self.history()
        self.assertNotEqual(other["scope"], old["scope"])
        self.assertEqual(other["preferences"], [])
        self.http("/api/workspace-history", {"action":"open", "target_id":old["active_id"],
                  "scope":other["scope"], "conversation_id":other["active_id"]}, self.cookie, 400)
        self.chat("切回初始表")
        self.assertEqual(self.history()["active_id"], a["active_id"])
        self.assertIn("原数据表笔记", self.chat("它的阅读笔记呢")["message"])

    def test_replaying_created_table_acknowledgement_does_not_create_another_table(self):
        history = self.history()
        preview = self.chat("新建数据表「研读笔记」，字段：标题（文本）、来源（超链接）")
        payload = {"message": preview["choices"][0], "request_id": str(uuid.uuid4()), "pending_id": preview["pending_id"],
                   "history_scope": history["scope"], "history_conversation_id": history["active_id"]}
        first = self.http("/api/chat", payload, self.cookie)[0]
        again = self.http("/api/chat", payload, self.cookie)[0]
        self.assertTrue(first["target_changed"])
        self.assertEqual(first["history_scope"], again["history_scope"])
        self.assertEqual(first["verification"], again["verification"])
        self.assertEqual(len(self.server.app.table.list_tables()), 2)

    def test_rechecking_identical_evidence_reports_unchanged_without_resubmitting(self):
        def run():
            with patch("papers._read_html", return_value=HTML):
                result = self.chat("提取这篇的图表 " + PAPER)
            return self.chat("确认回填", pending_id=result["pending_id"])
        run()
        with patch.object(self.server.app.table, "upsert", side_effect=AssertionError("一致记录应只读")):
            result = run()
        self.assertEqual(result["evidence_results"][0]["write_action"], "unchanged")
        events = self.history()["operations"]
        self.assertTrue(any(op["status"] == "unchanged" and "已有内容一致" in str(op["details"]) for op in events))

    def test_explicit_memory_accepts_casual_field_wording_without_saving_current_orders(self):
        self.chat("记住：把代码链接放在研究笔记")
        self.chat("以后把指标记在阅读笔记")
        before = self.history()["preferences"]
        self.assertEqual(len(before), 2)
        self.chat("这次的代码链接写入研究笔记")
        self.assertEqual(self.history()["preferences"], before)
