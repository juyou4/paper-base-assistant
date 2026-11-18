"""从对话入口验证批次、用户确认和会话隔离。"""

import unittest
from unittest.mock import patch

from table_store import LocalTable
from workflow import Conversation


class EvidenceConversationTests(unittest.TestCase):
    def setUp(self):
        self.table = LocalTable(":memory:")
        self.addCleanup(self.table.close)
        self.chat = Conversation(self.table, ".", "阅读清单")
        self.plan = {"ready": 2}
        fake = patch("evidence_workflow.prepare", side_effect=lambda *a, **kw: (
            self.plan, {"message": "请核对", "evidence_preview": [{"title": "甲"}, {"title": "乙"}]}))
        self.prepare = fake.start()
        self.addCleanup(fake.stop)

    def test_batch_scope_and_confirmation_id(self):
        preview = self.chat.respond("批量提取两篇论文的关键数据和图表")
        response = self.chat.respond("arXiv:2005.11401v4、https://arxiv.org/abs/1706.03762v7", preview["pending_id"])
        self.prepare.assert_called_once_with(self.table, [
            "https://arxiv.org/abs/2005.11401v4", "https://arxiv.org/abs/1706.03762v7",
        ], kinds=["metric", "figure", "table"])
        token = response["pending_id"]
        self.assertEqual(token, response["evidence_preview"]["id"])
        other = Conversation(self.table, ".", "阅读清单")
        with patch("evidence_workflow.commit", return_value={"message": "已回读"}) as commit:
            self.assertEqual(response, self.chat.respond("先看看"))
            with self.assertRaises(ValueError):
                other.respond("确认回填 " + token)
            with self.assertRaises(ValueError):
                self.chat.respond("确认回填 " + "f" * 32, token)
            commit.assert_not_called()
            self.assertEqual(self.chat.respond("确认回填 " + token, token)["message"], "已回读")
            self.assertEqual(self.chat.respond("确认回填 " + token, token)["message"], "已回读")
            commit.assert_called_once_with(self.table, self.plan)

    def test_explicit_figures_and_cancel(self):
        self.chat.last = "https://arxiv.org/abs/2005.11401"
        response = self.chat.respond("只提取刚才那篇的图表，先别写入")
        self.prepare.assert_called_once_with(self.table, [self.chat.last], kinds=["figure", "table"])
        token = response["pending_id"]
        with patch("evidence_workflow.commit") as commit:
            self.chat.respond("取消回填 " + token)
            self.assertIsNone(self.chat.pending)
            commit.assert_not_called()

    def test_bounded_batch_and_explicit_prohibition(self):
        with self.assertRaisesRegex(ValueError, "8"):
            self.chat.respond("提取实验数据 " + " ".join(f"arXiv:2005.{10000+i}" for i in range(9)))
        self.chat.respond("不要提取 arXiv:2005.11401 的关键数据")
        self.prepare.assert_not_called()
