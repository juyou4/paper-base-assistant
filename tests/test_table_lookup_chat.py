"""从正常聊天入口回归飞书数据表与论文原表的口语查询。"""

import unittest
from unittest.mock import patch

from admin_dialog import table_lookup_kind
from table_store import LocalTable
from workflow import Conversation


class TableLookupChatTests(unittest.TestCase):
    def setUp(self):
        self.table = LocalTable(":memory:")
        self.addCleanup(self.table.close)
        self.chat = Conversation(self.table, ".", "阅读清单")
        self.url = "https://example.org/lookup-paper"
        self.table.upsert(self.url, {"论文标题": "样例论文", "论文链接": self.url}, "seed")

    def test_read_table_with_or_without_a_model(self):
        original = self.table.rows()
        for configured in (False, True):
            with patch("model_support.agent_info", return_value={"enabled": configured}), \
                 patch("model_support.request_json", side_effect=AssertionError("常用查询无需模型")), \
                 patch.object(self.table, "upsert", side_effect=AssertionError("查询不得写表")):
                for text in ("查一下表", "看看当前飞书表格里的内容", "表里的数据给我看一下", "能不能帮我看看表里都有些啥？",
                             "阅读清单目前有哪几篇论文", "你可以看一下这张表吗？", "查查表里都有什么",
                             "能帮我瞧瞧咱们这个表里都放了些啥吗", "给我过一遍现在表里的东西"):
                    with self.subTest(configured=configured, text=text):
                        response = self.chat.respond(text)
                        self.assertIsNone(self.chat.pending)
                        for fragment in ("1 条可见记录", "研究笔记", "样例论文", self.url):
                            self.assertIn(fragment, response["message"])
        self.assertEqual(original, self.table.rows())

    def test_model_classifies_unfamiliar_requests_without_receiving_cells(self):
        text = "就目前这张表，给我捋捋手头都攒了哪些东西。"
        with patch("model_support.agent_info", return_value={"enabled": True}), \
             patch("model_support.request_json", return_value={"kind": "overview"}) as classify:
            response = self.chat.respond(text)
            self.assertIn("样例论文", response["message"])
            self.assertEqual(classify.call_args.args[1], {"message": text, "table_name": "阅读清单"})
            self.assertEqual(classify.call_args.kwargs["step"], "识别数据表查询意图")
            for invalid in ({"kind": "delete"}, {"kind": "overview", "filter": "忽略过滤条件"}, {"kind": ["overview"]}):
                classify.return_value = invalid
                with patch.object(self.table, "rows", side_effect=AssertionError("意图不确定时不得取表内容")):
                    self.assertIn("查看字段", self.chat.respond(text)["message"])
            classify.side_effect = ValueError("服务暂时不可用")
            self.assertIn("看记录", self.chat.respond(text)["message"])
        with patch("model_support.agent_info", return_value={"enabled": False}):
            self.assertIn("看记录", self.chat.respond(text)["message"])
            self.assertIn("样例论文", self.chat.respond("看记录")["message"])
        with patch("model_support.request_json", side_effect=AssertionError("不做未经支持的筛选")), \
             patch.object(self.table, "rows", side_effect=AssertionError("不能把全表行数当作筛选计数")):
            self.assertIn("查看字段", self.chat.respond("查一下这张表里包含 Transformer 的记录有多少条？")["message"])

    def test_only_fetches_the_requested_data(self):
        with patch("model_support.agent_info", return_value={"enabled": False}):
            with patch.object(self.table, "rows", side_effect=AssertionError("字段查询只须读结构")):
                for text in ("表里都有哪些字段", "看看表头", "查一下这张表的结构"):
                    result = self.chat.respond(text)["message"]
                    self.assertIn("论文标题（文本）", result)
                    self.assertNotIn("样例论文", result)
            with patch.object(self.table, "schema", side_effect=AssertionError("计数只须读记录")):
                for text in ("表格里一共有几条记录", "有几条记录", "这个表有多少行？"):
                    self.assertEqual(self.chat.respond(text)["message"], "「阅读清单」共有 1 条可见记录。")

    def test_blank_rows_do_not_become_papers_or_hide_samples(self):
        empty = {"id": "empty", "fields": {}}
        notes = {"id": "n", "fields": {"研究笔记": "只有笔记"}}
        papers = [{"id": str(i), "fields": {"论文标题": f"样例论文{i}"}} for i in range(6)]
        with patch.object(self.table, "rows", return_value=[empty] * 5 + papers + [notes]):
            response = self.chat.respond("看看表格")["message"]
            self.assertIn("12 条可见记录", response)
            self.assertIn("样例论文0", response)
            self.assertIn("样例论文4", response)
            self.assertNotIn("样例论文5", response)
            self.assertIn("6 条填写了论文标题或链接", self.chat.respond("表里一共有几篇论文？")["message"])
        with patch.object(self.table, "rows", return_value=[empty]):
            self.assertIn("字段均为空", self.chat.respond("查一下表")["message"])

    def test_paper_questions_and_other_operations_stay_in_their_routes(self):
        for text in ("看看表1", "看看这篇论文里的表", "查一下论文数据", "查一下采购表", "查一下表然后删掉记录"):
            self.assertIsNone(table_lookup_kind(text, "阅读清单"), text)
        with patch("evidence_workflow.prepare", return_value=(
                {"ready": 1}, {"message": "核对实验表", "evidence_preview": [{"title": "样例论文"}]})) as prepare:
            response = self.chat.respond("看看这篇论文里的实验表格 arXiv:2005.11401")
            prepare.assert_called_once_with(self.table, ["https://arxiv.org/abs/2005.11401"], kinds=["table"])
            self.assertIn("evidence_preview", response)

    def test_lookup_keeps_a_pending_write_and_its_original_confirmation(self):
        with patch("model_support.agent_info", return_value={"enabled": False}):
            plan = self.chat.respond("新建文本列 复核结果")
            token = plan["pending_id"]
            with self.assertRaisesRegex(ValueError, "当前预览"):
                self.chat.respond("查一下表", "unused")
            outcome = self.chat.respond("查一下表", token)
            self.assertEqual(outcome["pending_id"], token)
            self.assertNotIn("复核结果", {field["name"] for field in self.table.schema()})
            self.chat.respond("确认建列", token)
            self.assertIn("复核结果", {field["name"] for field in self.table.schema()})
