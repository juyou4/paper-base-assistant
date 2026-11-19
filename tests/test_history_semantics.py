"""从真实的 SQLite 和正常对话入口核对表级历史、阅读指代和回执。"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import history_semantics as history
from evidence_workflow import commit, prepare
from table_store import FeishuTable, LocalTable
from test_evidence_workflow import LINK, OTHER, extracted
from workflow import Conversation


class HistorySemanticsTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.directory = Path(folder.name)
        self.chat = self.connect()
        env = patch.dict(os.environ, PAPER_AGENT_PROVIDER="rules")
        env.start()
        self.addCleanup(env.stop)

    def connect(self, path="records.sqlite3"):
        table = LocalTable(self.directory / path)
        self.addCleanup(table.close)
        chat = Conversation(table, self.directory, "论文清单")
        self.addCleanup(chat.close)
        return chat

    def seed_focus(self):
        row = self.chat.table.upsert(LINK, {"论文标题": "Retrieval-Augmented Generation", "论文链接": LINK,
                                            "阅读笔记": "我的原始笔记"}, "seed")
        self.chat.respond("再看看 RAG")
        return row["record_id"]

    def test_scope_and_focus_survive_process_and_must_reread_identity(self):
        rid = self.seed_focus()
        original = history.export_focus(self.chat)
        fresh = self.connect()
        self.assertEqual(history.stable_scope(self.chat, "local", self.directory),
                         history.stable_scope(fresh, "local", self.directory))
        self.chat.table.upsert(LINK, {"阅读笔记": "最新手工笔记"}, "edit")
        with patch.object(fresh.table, "get", wraps=fresh.table.get) as read:
            self.assertTrue(history.restore_focus(fresh, original))
            read.assert_called_once_with(rid)
        self.assertIn("最新手工笔记", fresh.respond("它的阅读笔记呢")["message"])
        self.chat.table.upsert(LINK, {"论文标题": "已改为另一篇"}, "rename")
        self.assertFalse(history.restore_focus(fresh, original))
        self.assertIsNone(fresh.last)

    def test_scope_isolates_tables_and_contains_no_tenant_token(self):
        self.seed_focus()
        saved = history.export_focus(self.chat)
        another = self.connect("other.sqlite3")
        with patch.object(another.table, "get", side_effect=AssertionError("跨表不可读取")):
            self.assertFalse(history.restore_focus(another, saved))
        table = FeishuTable(":memory:", "app", "secret", "token-secret-proof", "tbl")
        self.addCleanup(table.close)
        remote = Conversation(table, self.directory, "论文清单")
        scope = history.stable_scope(remote, "feishu", self.directory)
        self.assertRegex(scope, "^[0-9a-f]{64}$")
        self.assertEqual(scope, history.stable_scope(remote, "feishu-cli", self.directory))
        table._set_target("token-secret-proof", "other")
        self.assertNotEqual(scope, history.stable_scope(remote, "feishu", self.directory))

    def test_clear_resets_ordinal_pending_and_old_confirmations(self):
        self.seed_focus()
        self.chat.respond("新建文本列 实验室")
        self.chat.workspace_receipt = self.chat.last_evidence_receipt = ("old", {})
        self.chat.last_library_results = {"rows": []}
        history.clear_focus(self.chat)
        self.assertTrue(all(value is None for value in (
            self.chat.pending, self.chat.last_library_results, self.chat.saved_focus,
            self.chat.last, self.chat.workspace_receipt, self.chat.last_evidence_receipt)))
        self.assertTrue(self.chat.table.rows())

    def test_explicit_preferences_only_and_identity_fields_protected(self):
        for text in ("优先将代码链接写入研究笔记", "代码链接优先写入「研究笔记」", "以后把代码链接优先写入「研究笔记」"):
            self.assertEqual(history.parse_preference(text), ("code", "研究笔记"))
        self.assertEqual(history.parse_preference("在这张表里优先将表格写入阅读笔记"), ("table", "阅读笔记"))
        for text in ("把代码链接写入研究笔记", "以后把阅读笔记内容发送给模型", "把代码链接优先写入研究笔记并执行"):
            rule = history.parse_preference(text)
            if rule:
                with self.assertRaises(ValueError):
                    history.set_preference(self.chat, *rule)
        for col in ("论文标题", "论文链接", "未创建的字段"):
            with self.assertRaises(ValueError):
                history.set_preference(self.chat, "code", col)
        self.assertEqual(self.chat.history_preferences, {})

    def test_preference_applies_to_preview_only_and_manual_override_wins(self):
        self.chat.table.add_field("实验结论", "text")
        history.set_preference(self.chat, "metric", "实验结论")
        with patch("evidence_extract.extract_evidence", side_effect=extracted):
            preview = self.chat.respond("提取这篇的关键指标 " + LINK)
        paper = preview["evidence_preview"]["papers"][0]
        self.assertEqual({i["field"] for i in paper["items"]}, {"实验结论"})
        self.assertEqual(self.chat.table.rows(), [])
        pending = self.chat.pending
        result = self.chat.respond("确认回填", pending["id"], evidence_selection=[[0]],
                                   evidence_fields=[{"0": "研究笔记"}])
        event = history.operation_for("确认回填", "evidence_confirm", result, plan=pending["plan"])
        self.assertEqual((event["action"], event["status"]), ("evidence_fill", "verified"))
        fields = [d for d in event["details"] if d["label"] == "研究笔记"]
        self.assertEqual(len(fields), 1)
        self.assertIn("44.1", fields[0]["value"])
        self.assertNotIn("45.5", str(event))
        self.assertTrue(fields[0]["url"].endswith("#T1"))
        self.assertIn(result["evidence_results"][0]["record_id"], str(event["details"]))
        rid = result["evidence_results"][0]["record_id"]
        self.assertIn({"label": "记录 ID", "value": rid, "record_id": rid}, event["details"])
        self.assertNotIn("实验结论", self.chat.table.rows()[0]["fields"])
        self.assertEqual(self.chat.history_preferences["metric"], "实验结论")

    def test_reading_does_not_create_a_write_receipt_and_note_writes_only_its_field(self):
        self.seed_focus()
        shown = self.chat.respond("它的阅读笔记呢")
        self.assertIsNone(history.operation_for("它的阅读笔记呢", None, shown))
        renewed = self.chat.respond("收藏 " + LINK)
        self.assertIn("verification", renewed)
        self.assertIsNone(history.operation_for("收藏", None, renewed))
        msg = "给这篇记一句：重点复现实验"
        actual = self.chat.respond(msg)
        report = history.operation_for(msg, None, actual)
        self.assertEqual((report["action"], report["status"]), ("record_write", "verified"))
        self.assertEqual([d["label"] for d in report["details"]], ["记录 ID", "阅读笔记"])
        self.assertIn("重点复现实验", report["details"][1]["value"])

    def test_record_buttons_require_receipt_id_marker_even_if_user_field_has_same_name(self):
        write = {"message": "已写入", "write_receipt": {"collection": False, "record_id": "rec_real", "url": LINK,
                                                     "fields": {"记录 ID": "rec_user_input"}}}
        fields = history.operation_for("", None, write)["details"]
        self.assertEqual([(d["value"], d.get("record_id")) for d in fields],
                         [("rec_real", "rec_real"), ("rec_user_input", None)])
        result = {"message": "已处理", "collection_receipts": [
            {"title": "真实行", "url": LINK, "record_id": "rec_batch", "status": "verified"},
            {"title": "未完成", "url": OTHER, "message": "尚无回执", "status": "unverified"}]}
        marked = [d for d in history.operation_for("", None, result)["details"] if "record_id" in d]
        self.assertEqual(marked, [{"label": "记录 ID", "record_id": "rec_batch", "value": "rec_batch"}])

    def test_missing_preferred_column_falls_back_without_changing_schema(self):
        self.chat.history_preferences = {"metric": "失效列"}
        with patch("evidence_extract.extract_evidence", side_effect=extracted):
            reply = self.chat.respond("提取这篇的关键指标 " + LINK)
        self.assertTrue(all(i["field"] != "失效列" for i in reply["evidence_preview"]["papers"][0]["items"]))
        self.assertEqual(history.operation_for("", None, reply)["status"], "preview")

    def test_partial_batch_does_not_report_rejected_draft_as_written(self):
        with patch("evidence_extract.extract_evidence", side_effect=extracted):
            plan, _ = prepare(self.chat.table, [LINK, OTHER])
        self.chat.table.upsert(OTHER, {"论文链接": OTHER, "论文标题": "用户刚录入"}, "new-target")
        result = commit(self.chat.table, plan, [[1], [0]])
        report = history.operation_for("确认回填", "evidence_confirm", result)
        self.assertEqual(report["status"], "partial")
        self.assertIn("45.5", str(report["details"]))
        self.assertNotIn("44.1", str(report["details"]))
        self.assertEqual(len(self.chat.table.rows()), 2)

    def test_admin_success_cancel_and_read_during_preview(self):
        saved = self.chat.respond("新建文本列 对照实验")
        task = self.chat.pending
        with self.subTest("read"):
            read = self.chat.respond("查看字段", task["id"])
            self.assertIsNone(history.operation_for("查看字段", task["stage"], read, plan=task["plan"]))
        receipt = self.chat.respond("确认建列", saved["pending_id"])
        verified = history.operation_for("确认建列", task["stage"], receipt, plan=task["plan"])
        self.assertEqual((verified["action"], verified["status"]), ("field_change", "verified"))
        self.assertIn("对照实验", str(verified["details"]))
        self.chat.respond("删除字段「对照实验」")
        task = self.chat.pending
        cancel = self.chat.respond("取消", task["id"])
        self.assertEqual(history.operation_for("取消", task["stage"], cancel, plan=task["plan"])["status"], "cancelled")
        self.assertIsNone(history.operation_for("取消", None, self.chat.respond("取消")))

    def test_management_and_workspace_only_report_actual_results(self):
        for ask, action in (("把 reader@example.test 的协作者权限改为可编辑", "member_permission"),
                            ("把角色「论文审阅人」对字段「研究笔记」的权限改为隐藏", "role_permission"),
                            ("新建数据表「验收」", "create_table"),
                            ("新建多维表格「新的研究库」，首张表叫「新论文」", "create_base")):
            with self.subTest(ask=ask):
                prompt = self.chat.respond(ask)
                task = self.chat.pending
                self.assertIsNotNone(task, prompt)
                self.assertIsNone(history.operation_for(ask, None, prompt))
                answer = prompt["choices"][0]
                reply = self.chat.respond(answer, prompt["pending_id"])
                event = history.operation_for(answer, task["stage"], reply, plan=task["plan"])
                self.assertEqual((event["action"], event["status"]), (action, "verified"))
                self.assertTrue(event["details"])
