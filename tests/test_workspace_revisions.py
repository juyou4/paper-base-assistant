"""对话内改建表草案，验证旧按钮、跨表边界和实际创建结果。"""

import os
import tempfile
import unittest
from unittest.mock import patch

from app import Application
from table_store import TableError


class WorkspaceRevisionTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        env = patch.dict(os.environ, {"PAPER_AGENT_PROVIDER": "rules"})
        env.start()
        self.addCleanup(env.stop)
        self.app = Application("local", folder.name)
        self.addCleanup(self.app.close)
        self.cookie, self.chat = self.app.session(None)
        self.send = self.chat.respond

    def assert_dialog_after_refresh(self, reply):
        state = self.app.state(self.app.session(self.cookie)[1])
        expected = None if reply is None else {
            "id": reply["pending_id"], "message": reply["message"], "choices": reply["choices"]}
        self.assertEqual(state["pending_dialog"], expected)
        self.assertEqual(state["workspace_pending"], expected)

    def test_delayed_name_retains_purpose_and_fields(self):
        question = self.send("我想建个用来记录模型、数据集和评价指标的表")
        self.assertIn("叫什么", question["message"])
        plan = self.send("就叫模型对比吧", question["pending_id"])
        self.assertEqual([f["name"] for f in self.chat.pending["plan"]["fields"]],
                         ["论文标题", "论文链接", "模型", "数据集", "评价指标"])
        self.assertIn("模型对比", plan["message"])
        self.send("先别建了")
        question = self.send("帮我弄个表，字段是标题、链接")
        plan = self.send("叫「周会」", question["pending_id"])
        self.assertEqual(self.chat.pending["plan"]["fields"],
                         [{"name": "标题", "type": "text"}, {"name": "链接", "type": "url"}])
        self.assertIn("周会", plan["message"])

    def test_edit_preview_without_changing_the_current_table(self):
        plan = self.send("建张数据表「初筛」，字段：标题、链接、阅读笔记")
        original = self.app.table.schema()
        for instruction in ("先别建，先加一列代码链接", "把「阅读笔记」这列改名为「审阅意见」",
                            "不用链接这一列了", "把新表改名为「模型对比」"):
            previous = plan["pending_id"]
            plan = self.send(instruction, previous)
            self.assertNotEqual(previous, plan["pending_id"], instruction)
            self.assertIn("确认新建数据表", plan["choices"])
            with self.assertRaisesRegex(ValueError, "当前预览"):
                self.send("确认新建数据表", previous)
            self.assertEqual(self.app.table.schema(), original)
            self.assertEqual(len(self.app.table.list_tables()), 1)
        self.send("确认新建数据表", plan["pending_id"])
        self.assertEqual(self.chat.table_name, "模型对比")
        self.assertEqual([(f["name"], f["type"]) for f in self.chat.table.schema()],
                         [("标题", "text"), ("审阅意见", "text"), ("代码链接", "url")])
        self.send("切回初始表")
        self.assertTrue(self.send("打开「模型对比」那张子表")["target_changed"])
        self.assertIn("模型对比", self.send("这个文件里有哪几张表？")["message"])
        self.assertTrue(self.send("切到「模型对比」")["target_changed"])

    def test_invalid_edit_withdraws_confirmation_until_revised_or_restored(self):
        plan = self.send("新建数据表「初选」，字段：标题、链接")
        invalid = self.send("去掉标题", plan["pending_id"])
        self.assert_dialog_after_refresh(invalid)
        self.assertEqual(invalid["choices"], ["保留原方案", "取消"])
        self.assertNotEqual(plan["pending_id"], invalid["pending_id"])
        self.send("确认新建数据表", invalid["pending_id"])
        self.assertEqual(len(self.chat.table.list_tables()), 1)
        restored = self.send("保留原方案", invalid["pending_id"])
        self.assert_dialog_after_refresh(restored)
        self.assertIn("链接（超链接）", restored["message"])
        self.assertNotEqual(restored["pending_id"], invalid["pending_id"])
        invalid = self.send("把链接的类型改成日期", restored["pending_id"])
        fixed = self.send("字段换成：论文标题、论文链接、年份", invalid["pending_id"])
        self.assertIn("确认新建数据表", fixed["choices"])
        self.send("确认新建数据表", fixed["pending_id"])
        self.assertEqual([f["name"] for f in self.chat.table.schema()], ["论文标题", "论文链接", "年份"])
        self.assert_dialog_after_refresh(None)

    def test_base_filename_and_first_table_can_be_revised_separately(self):
        plan = self.send("新建多维表格「周报」，首张表叫「收件箱」")
        rename_file = self.send("把文件名改成组会", plan["pending_id"])
        rename_table = self.send("首张表名改成精读", rename_file["pending_id"])
        self.send("确认新建多维表格", rename_table["pending_id"])
        self.assertEqual((self.chat.base_name, self.chat.table_name), ("组会", "精读"))

    def test_keep_selected_columns_preserves_original_types_and_order(self):
        draft = self.send("新建表「复现」，字段：论文标题、代码仓库（超链接）、备注")
        added = self.send("再加一个叫年份的字段", draft["pending_id"])
        self.assertEqual(self.chat.pending["plan"]["fields"][-1]["name"], "年份")
        slim = self.send("只保留论文标题、代码仓库这几列", added["pending_id"])
        self.assertEqual(self.chat.pending["plan"]["fields"], [
            {"name": "论文标题", "type": "text"}, {"name": "代码仓库", "type": "url"}])
        self.send("确认新建数据表", slim["pending_id"])
        self.assertEqual(len(self.chat.table.schema()), 2)

    def test_uncertain_creation_keeps_its_original_receipt(self):
        plan = self.send("建张数据表「初筛」")
        with patch.object(self.chat.table, "create_table", side_effect=TableError("请求结果未知")):
            with self.assertRaises(TableError):
                self.send("确认新建数据表", plan["pending_id"])
        recovery = self.app.state(self.chat)["pending_dialog"]
        self.assertEqual(recovery["id"], plan["pending_id"])
        self.assertIn("请求结果未知", recovery["message"])
        self.assertIn("论文链接（超链接）", recovery["message"])
        self.assertEqual(recovery["choices"], plan["choices"])
        stalled = self.send("把表名改成第一次复现", plan["pending_id"])
        self.assertEqual(stalled["pending_id"], plan["pending_id"])
        self.assertEqual(self.chat.pending["plan"]["name"], "初筛")
        saved = self.send("确认新建数据表", plan["pending_id"])
        self.assertTrue(saved["target_changed"])
        self.assert_dialog_after_refresh(None)

    def test_cancel_then_recreate_cannot_reuse_withdrawn_confirmations(self):
        question = self.send("帮我弄个表")
        self.assert_dialog_after_refresh(question)
        draft = self.send("就叫周报吧", question["pending_id"])
        invalid = self.send("去掉不存在的字段", draft["pending_id"])
        self.assert_dialog_after_refresh(invalid)
        self.send("取消这次建表", invalid["pending_id"])
        self.assert_dialog_after_refresh(None)
        fresh = self.send("新建数据表「周报」，字段：论文标题、论文链接")
        self.assert_dialog_after_refresh(fresh)
        for old in (question, draft, invalid):
            with self.assertRaisesRegex(ValueError, "当前预览"):
                self.send("确认新建数据表", old["pending_id"])
        self.assertEqual(len(self.app.table.list_tables()), 1)
        self.send("确认新建数据表", fresh["pending_id"])
        self.assert_dialog_after_refresh(None)

    def test_questions_and_mixed_actions_leave_the_original_draft_intact(self):
        plan = self.send("帮我弄个表叫周报")
        for question in ("再加一列年份可以吗？", "给当前表加列年份", "加一列年份然后再删除已有表", "加个字段叫年份行不行"):
            reply = self.send(question, plan["pending_id"])
            self.assertEqual(reply["pending_id"], plan["pending_id"])
            self.assertEqual(self.chat.pending["plan"]["name"], "周报")
        self.assertNotIn("年份", [f["name"] for f in self.chat.pending["plan"]["fields"]])

    def test_explicit_spoken_confirmation_requires_current_preview(self):
        plan = self.send("新建表「对比」，字段：论文标题、论文链接")
        for text in ("行吧", "就按这个方案建吧？", "没问题，按这个方案建吧，另外给同事授权"):
            self.assertEqual(self.send(text, plan["pending_id"])["pending_id"], plan["pending_id"])
        self.assertEqual(self.send("按这个方案建吧")["pending_id"], plan["pending_id"])
        result = self.send("没问题，就按这个方案建吧！", plan["pending_id"])
        self.assertTrue(result["target_changed"])
        self.assertEqual(self.send("没问题，就按这个方案建吧！", plan["pending_id"]), result)
        self.assertEqual(len(self.chat.table.list_tables()), 2)
