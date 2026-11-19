"""从聊天入口核对模型给出的字段名、查询范围及模型故障时的只读回退。"""

import unittest
from unittest.mock import patch

from table_store import LocalTable
from workflow import Conversation


class AgentGroundingTests(unittest.TestCase):
    def setUp(self):
        self.table = LocalTable(":memory:")
        self.addCleanup(self.table.close)
        self.chat = Conversation(self.table, ".", "阅读清单")
        configured = patch("model_support.agent_info", return_value={"enabled": True})
        configured.start()
        self.addCleanup(configured.stop)

    def test_model_must_copy_whole_bare_names_before_a_preview(self):
        self.table.add_field("状态", "text")
        self.table.add_field("新状态", "text")
        before = self.table.schema()
        cases = [
            ("新增文本字段 新进度", {"op": "create", "name": "进度", "type": "text"}),
            ("新增文本字段 新进度", {"op": "create", "name": "新增文本字段 新进度", "type": "text"}),
            ("把新状态这一列删掉", {"op": "delete", "target": "状态"}),
            ("把新状态这一列改叫新进度", {"op": "rename", "target": "新状态", "new_name": "进度"}),
        ]
        for message, proposal in cases:
            with self.subTest(message=message, proposal=proposal), patch("agent_router.request_json", return_value=proposal):
                self.assertNotIn("pending_id", self.chat.respond(message))
                self.assertIsNone(self.chat.pending)
                self.assertEqual(self.table.schema(), before)

    def test_complete_labels_survive_natural_word_order_and_explicit_confirmation(self):
        create = {"op": "create", "name": "新状态", "type": "text"}
        for message in ("请添加一个名为新状态的文本字段", "新增新状态这个文本字段",
                        "麻烦给这张表加一栏，名字叫新状态，类型选文本"):
            with self.subTest(message=message), patch("agent_router.request_json", return_value=create):
                preview = self.chat.respond(message)
                self.assertIn("「新状态」", preview["message"])
                self.chat.respond("取消", preview["pending_id"])
                self.assertNotIn("新状态", [f["name"] for f in self.table.schema()])
        with patch("agent_router.request_json", return_value=create):
            preview = self.chat.respond("新增文本字段 新状态")
            self.chat.respond("确认建列", preview["pending_id"])
        self.table.add_field("状态", "text")
        with patch("agent_router.request_json", return_value={"op": "rename", "target": "新状态", "new_name": "新进度"}):
            preview = self.chat.respond("把新状态这一列改个名字叫新进度")
            self.assertIn("「新状态」改名为「新进度」", preview["message"])
            self.chat.respond("确认变更", preview["pending_id"])
        names = {f["name"] for f in self.table.schema()}
        self.assertTrue({"状态", "新进度"} <= names)
        self.assertNotIn("新状态", names)

    def test_recognized_schema_read_still_works_when_provider_fails(self):
        with patch("agent_router.request_json", side_effect=ValueError("模型暂时不可用")) as agent, \
                patch.object(self.table, "rows", side_effect=AssertionError("查列不应读取记录")):
            response = self.chat.respond("查一下这张表的字段")
        agent.assert_called_once()
        self.assertIn("论文标题", response["message"])
        self.assertIsNone(self.chat.pending)

    def test_agent_cannot_turn_a_count_into_sample_records(self):
        with patch("model_support.request_json", return_value={"kind": "overview"}) as model, \
                patch.object(self.table, "rows", side_effect=AssertionError("分类冲突应先澄清")):
            for message in ("麻烦盘一盘这张表一共几篇论文，只报个数就行", "帮我估算一下这张表有多少条记录"):
                with self.subTest(message=message):
                    self.assertIn("你想了解", self.chat.respond(message)["message"])
            self.assertEqual(model.call_count, 2)
        with patch("model_support.request_json", return_value={"kind": "paper_count"}):
            self.assertIn("0 条填写了论文标题或链接", self.chat.respond("麻烦盘一盘这张表一共几篇论文")["message"])

    def test_venue_filtered_count_never_returns_total(self):
        with patch("model_support.request_json", side_effect=AssertionError("已识别的筛选不交给模型猜")), \
                patch.object(self.table, "rows", side_effect=AssertionError("全表计数不能充作筛选结果")):
            self.assertIn("你想了解", self.chat.respond("帮我数数这张表里有几篇 CVPR 的论文")["message"])
