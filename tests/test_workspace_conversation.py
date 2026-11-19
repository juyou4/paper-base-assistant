"""通过工作台的真实会话验证建表确认、目标隔离和后续论文写入。"""

import os
import sys
import tempfile
import unittest
from unittest.mock import patch

from app import Application


class WorkspaceConversationTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = folder.name
        config = patch.dict(os.environ, {"PAPER_AGENT_PROVIDER": "rules", "FEISHU_APP_TOKEN": "",
                                         "FEISHU_TABLE_ID": "", "LARK_CLI": sys.executable})
        config.start()
        self.addCleanup(config.stop)

    def application(self, mode="local"):
        app = Application(mode, self.folder)
        self.addCleanup(app.close)
        return app

    def test_child_table_confirmation_scope_cancellation_and_exact_fields(self):
        app = self.application()
        cookie, chat = app.session(None)
        _, other = app.session(None)
        initial = app.state(chat)
        command = "新建数据表「收件箱」，字段：标题（文本）、来源（超链接）"
        question = chat.respond(command)
        confirm, cancel = question["choices"]
        pending = question["pending_id"]
        refreshed = app.state(app.session(cookie)[1])
        self.assertEqual(refreshed["workspace_pending"], {
            "id": pending, "choices": question["choices"], "message": question["message"]})
        self.assertEqual((refreshed["table_name"], refreshed["fields"], refreshed["rows"]),
                         (initial["table_name"], initial["fields"], initial["rows"]))
        self.assertIsNone(app.state(other)["workspace_pending"])
        for query, expected in [("先查一下表", "条可见记录"), ("查看字段", "论文标题"),
                                ("当前多维表格有哪些子表？", initial["table_name"])]:
            with self.subTest(query=query):
                answer = chat.respond(query, pending)
                self.assertIn(expected, answer["message"])
                self.assertNotIn("收件箱", answer["message"])
                self.assertEqual((answer["pending_id"], answer["choices"]), (pending, question["choices"]))
                self.assertEqual(app.state(chat)["workspace_pending"], refreshed["workspace_pending"])
        self.assertEqual(chat.respond(confirm)["pending_id"], pending)
        with self.assertRaises(ValueError):
            chat.respond(confirm, "不属于当前预览的编号")
        with self.assertRaises(ValueError):
            other.respond(confirm, pending)
        chat.respond(cancel, pending)
        self.assertIsNone(app.state(chat)["workspace_pending"])
        self.assertNotIn("收件箱", chat.respond("列出所有数据表")["message"])
        question = chat.respond(command)
        self.assertNotEqual(question["pending_id"], pending)
        result = chat.respond(confirm, question["pending_id"])
        self.assertTrue(result["target_changed"])
        self.assertEqual(chat.respond(confirm, question["pending_id"]), result)
        self.assertEqual(app.state(chat)["table_name"], "收件箱")
        self.assertEqual([(f["name"], f["type"]) for f in app.state(chat)["fields"]],
                         [("标题", "text"), ("来源", "url")])
        self.assertEqual(app.state(other), initial)
        self.assertIn("收件箱", other.respond("列出所有数据表")["message"])
        self.assertNotIn("pending_id", other.respond(command))
        self.assertEqual(other.respond("切换到数据表「收件箱」")["target_changed"], True)
        self.assertEqual(app.state(other)["table_name"], "收件箱")

    def test_independent_file_switch_back_and_followup_paper(self):
        app = self.application()
        cookie, chat = app.session(None)
        _, other = app.session(None)
        original = app.state(chat)
        request = chat.respond("新建一个多维表格")
        self.assertIn("叫什么", request["message"])
        answer = chat.respond("查一下表", request["pending_id"])
        self.assertIn("条可见记录", answer["message"])
        self.assertEqual((answer["pending_id"], answer["choices"]), (request["pending_id"], ["取消"]))
        self.assertEqual(app.state(app.session(cookie)[1])["workspace_pending"]["message"], request["message"])
        file_plan = chat.respond("CVPR 文献库", request["pending_id"])
        self.assertIn("CVPR 文献库", file_plan["message"])
        refreshed = app.state(app.session(cookie)[1])
        self.assertEqual(refreshed["workspace_pending"]["id"], file_plan["pending_id"])
        self.assertEqual(refreshed["workspace_pending"]["message"], file_plan["message"])
        self.assertEqual(refreshed["table_name"], original["table_name"])
        saved = chat.respond("确认新建多维表格", file_plan["pending_id"])
        self.assertTrue(saved["target_changed"])
        state = app.state(chat)
        self.assertEqual((state["base_name"], state["table_name"]), ("CVPR 文献库", "论文清单"))
        self.assertNotIn(original["table_name"], chat.respond("列出所有数据表")["message"])
        paper = chat.respond("收藏 https://arxiv.org/abs/2005.11401")["row"]
        self.assertEqual(app.state(chat)["rows"][0]["id"], paper["id"])
        draft = chat.respond("新建数据表「对比实验」，字段你来设计，用于记录模型、数据集和评价指标")
        self.assertIn("按用途中的明确维度规划", draft["message"])
        for field in ("模型", "数据集", "评价指标"):
            self.assertIn(field, draft["message"])
        chat.respond("确认新建数据表", draft["pending_id"])
        state = app.state(chat)
        self.assertEqual((state["base_name"], state["table_name"], state["rows"]), ("CVPR 文献库", "对比实验", []))
        self.assertIsNone(state["workspace_pending"])
        self.assertEqual([f["name"] for f in state["fields"]], ["论文标题", "论文链接", "模型", "数据集", "评价指标"])
        self.assertIsNone(chat.last)
        chat.respond("切换到数据表「论文清单」")
        self.assertEqual(app.state(chat)["rows"][0]["id"], paper["id"])
        self.assertEqual(app.state(other), original)
        self.assertNotIn("对比实验", other.respond("列出所有数据表")["message"])
        chat.respond("切回初始表")
        self.assertEqual(app.state(chat), original)

    def test_unbound_cli_can_preview_a_file_without_remote_table_access(self):
        app = self.application("feishu-cli")
        _, chat = app.session(None)
        with patch.object(chat.table, "_request", side_effect=AssertionError("预览阶段不应访问飞书")):
            initial = app.state(chat)
            self.assertFalse(initial["target_bound"])
            self.assertEqual((initial["rows"], initial["fields"]), ([], []))
            self.assertNotIn("pending_id", chat.respond("新建数据表「实验」"))
            self.assertNotIn("pending_id", chat.respond("收藏 https://arxiv.org/abs/2005.11401"))
            name_request = chat.respond("新建多维表格")
            lookup = chat.respond("查一下表", name_request["pending_id"])
            self.assertIn("尚未绑定数据表", lookup["message"])
            self.assertEqual(lookup["pending_id"], name_request["pending_id"])
            self.assertEqual(app.state(chat)["workspace_pending"]["message"], name_request["message"])
            chat.respond("取消", name_request["pending_id"])
            proposal = chat.respond("新建多维表格「研究」，首张表叫「初筛」，字段：论文标题、论文链接")
            self.assertIn("确认新建多维表格", proposal["choices"])
            self.assertIn("初筛", proposal["message"])
            lookup = chat.respond("当前表有几条记录？", proposal["pending_id"])
            self.assertIn("尚未绑定数据表", lookup["message"])
            self.assertEqual(lookup["choices"], proposal["choices"])
            self.assertEqual(app.state(chat)["workspace_pending"]["id"], proposal["pending_id"])
            chat.respond("取消", proposal["pending_id"])
            self.assertEqual(app.state(chat), initial)

    def test_evidence_preview_keeps_target_until_cancel_then_allows_creation(self):
        app = self.application()
        _, chat = app.session(None)
        initial = app.state(chat)
        url = "https://arxiv.org/abs/2005.11401v4"
        payload = {"url": url, "title": "回填验收样例", "items": [
            {"kind": "metric", "label": "NQ", "value": "44.1", "quote": "NQ · 44.1",
             "source": "Table 1", "source_url": url.replace("/abs/", "/html/") + "#T1"}]}
        command = "新建数据表「暂存」"
        with patch("evidence_extract.extract_evidence", return_value=payload):
            preview = chat.respond("看看这篇的跑分 " + url)
        batch = preview["evidence_preview"]
        self.assertEqual(batch["id"], preview["pending_id"])
        self.assertEqual(batch["papers"][0]["status"], "ready")
        self.assertIsNone(app.state(chat)["workspace_pending"])
        for interrupt in ("先列出所有子表", command, "切回初始表"):
            with self.subTest(interrupt=interrupt):
                reply = chat.respond(interrupt, batch["id"])
                self.assertEqual(reply["pending_id"], batch["id"])
                self.assertEqual(app.state(chat)["table_name"], initial["table_name"])
                self.assertEqual(app.state(chat)["rows"], [])
        cancelled = chat.respond("取消回填", batch["id"])
        self.assertIn("已取消", cancelled["message"])
        self.assertIsNone(chat.pending)
        self.assertNotIn("暂存", chat.respond("列出所有数据表")["message"])
        draft = chat.respond(command)
        with self.assertRaises(ValueError):
            chat.respond("确认回填", batch["id"])
        self.assertEqual(app.state(chat)["workspace_pending"]["id"], draft["pending_id"])
        chat.respond("确认新建数据表", draft["pending_id"])
        self.assertEqual((app.state(chat)["table_name"], app.state(chat)["rows"]), ("暂存", []))
        self.assertIn("暂存", chat.respond("列出所有数据表")["message"])

    def test_model_suggestions_and_explicit_columns_have_distinct_previews(self):
        app = self.application()
        cookie, chat = app.session(None)
        fields = [{"name": "论文标题", "type": "text"}, {"name": "论文链接", "type": "url"},
                  {"name": "复现工作量", "type": "text"}]
        with patch("model_support.enabled", return_value=True), \
             patch("model_support.request_json", return_value={"fields": fields}) as model:
            proposed = chat.respond("新建数据表「复现评审」，字段你来设计，用于判断下周应该优先复现哪篇论文")
            self.assertIn("模型按用途提议", proposed["message"])
            self.assertIn("复现工作量（文本）", proposed["message"])
            self.assertEqual(app.state(app.session(cookie)[1])["workspace_pending"]["message"], proposed["message"])
            chat.respond("取消", proposed["pending_id"])
            explicit = chat.respond("新建数据表「复现评审」，字段：论文标题、论文链接、运行成本")
            self.assertIn("运行成本（文本）", explicit["message"])
            self.assertNotIn("复现工作量", explicit["message"])
            self.assertNotIn("模型按用途提议", explicit["message"])
            model.assert_called_once()
        self.assertNotIn("复现评审", chat.respond("列出所有数据表", explicit["pending_id"])["message"])
