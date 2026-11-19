"""通过用户收发消息的入口验证来源选择、表内匹配和待确认操作的隔离。"""

import os
import unittest
from contextlib import ExitStack
from unittest.mock import patch

from activity import Run
from library_search import LOCAL, PUBLIC
from table_store import LocalTable
from workflow import Conversation


class LibrarySearchTests(unittest.TestCase):
    def setUp(self):
        self.table = LocalTable(":memory:")
        self.addCleanup(self.table.close)
        self.chat = Conversation(self.table, ".", "阅读清单")
        patches = ExitStack()
        self.addCleanup(patches.close)
        patches.enter_context(patch.dict(os.environ, PAPER_AGENT_PROVIDER="rules"))
        for key, cells in (
            ("a", {"论文标题": "Transformer Basics"}),
            ("b", {"论文标题": "视觉对齐", "阅读笔记": "这次讨论了 Ｔｒａｎｓｆｏｒｍｅｒ 与检测。"}),
            ("c", {"论文标题": "检索增强与知识库", "中文摘要": "关于向量索引。"}),
            ("d", {"研究笔记": "Transformer 组会待办"}),
        ):
            self.table.upsert(key, cells, key)
        self.rows = self.table.rows()
        self.catalog = patches.enter_context(patch("papers.search_titles", return_value=[]))
        patches.enter_context(patch.object(self.table, "upsert", side_effect=AssertionError("检索不能写表")))

    def test_search_all_visible_paper_text_and_identify_actual_matches(self):
        run = Run("read")
        with run.recording():
            result = self.chat.respond("在我收藏的论文里搜一下 Transformer")
        self.assertIn("2 条", result["message"])
        for text in ("Transformer Basics", "视觉对齐", "命中「阅读笔记」", "讨论了 Transformer"):
            self.assertIn(text, result["message"])
        for text in ("检索增强与知识库", "组会待办"):
            self.assertNotIn(text, result["message"])
        self.assertEqual([e["tool"] for e in run.snapshot()["events"]], ["SQLite", "SQLite"])
        self.assertIsNone(self.chat.pending)
        self.catalog.assert_not_called()
        self.assertEqual(self.table.rows(), self.rows)

    def test_unclear_scope_waits_for_source_and_preserves_the_topic(self):
        with patch.object(self.table, "rows", side_effect=AssertionError("未选来源不能查表")):
            offer = self.chat.respond("帮我找找 Transformer 的论文")
            self.assertEqual(offer["choices"], [LOCAL, PUBLIC, "取消"])
            repeated = self.chat.respond(LOCAL)
            self.assertEqual(repeated["pending_id"], offer["pending_id"])
            self.catalog.assert_not_called()
        result = self.chat.respond("查我的收藏", offer["pending_id"])
        self.assertIn("2 条", result["message"])
        self.assertIsNone(self.chat.pending)
        self.catalog.assert_not_called()

    def test_cancel_and_old_source_selections_never_dispatch(self):
        first = self.chat.respond("找找 Transformer")
        self.chat.respond("取消", first["pending_id"])
        again = self.chat.respond("帮我找找检索增强的论文")
        with self.assertRaises(ValueError):
            self.chat.respond(PUBLIC, first["pending_id"])
        self.catalog.assert_not_called()
        self.assertIn("未取得", self.chat.respond(PUBLIC, again["pending_id"])["message"])
        self.catalog.assert_called_once_with("检索增强")

    def test_explicit_public_request_only_passes_the_topic(self):
        with patch.object(self.table, "rows", side_effect=AssertionError("外部检索不应读取清单")):
            for phrase, term in (("去网上找几篇关于知识蒸馏的论文", "知识蒸馏"),
                                 ("检索 Crossref 上关于量化的论文", "量化")):
                with self.subTest(phrase=phrase):
                    self.chat.respond(phrase)
                    self.catalog.assert_called_once_with(term)
                    self.catalog.reset_mock()
        self.assertIsNone(self.chat.pending)

    def test_spoken_scope_followup_and_empty_public_results_can_switch_back(self):
        offer = self.chat.respond("帮我找找 Transformer 的论文")
        empty = self.chat.respond("去网上看看吧", offer["pending_id"])
        self.assertEqual(empty["pending_id"], offer["pending_id"])
        local = self.chat.respond("只看我收藏的", empty["pending_id"])
        self.assertIn("2 条", local["message"])
        self.assertIsNone(self.chat.pending)

    def test_searching_prior_saved_work_cannot_arm_a_write_to_previous_paper(self):
        self.chat.last = "arXiv:2005.11401"
        for phrase in ("我想找上次收藏的那篇关于长上下文的论文", "这张表中有没有哪篇论文讲模型压缩",
                       "我收藏的论文里有没有关于检索增强的"):
            with self.subTest(phrase=phrase):
                result = self.chat.respond(phrase)
                self.assertIn("条包含", result["message"])
                self.assertIsNone(self.chat.pending)
        self.catalog.assert_not_called()

    def test_catalog_failure_can_retry_or_switch_to_local(self):
        offer = self.chat.respond("帮我找找 Transformer 的论文")
        self.catalog.side_effect = ValueError("题录服务超时")
        with self.assertRaisesRegex(ValueError, "超时"):
            self.chat.respond(PUBLIC, offer["pending_id"])
        self.assertEqual(self.chat.pending["id"], offer["pending_id"])
        self.assertIn("2 条", self.chat.respond(LOCAL, offer["pending_id"])["message"])

    def test_read_during_field_preview_cannot_replace_or_approve_it(self):
        plan = self.chat.respond("新增文本列 复核状态")
        for phrase in ("在清单里搜 Transformer", "去网上找几篇关于图神经网络的论文"):
            result = self.chat.respond(phrase, plan["pending_id"])
            self.assertEqual(result["pending_id"], plan["pending_id"])
            self.assertEqual(result["choices"], plan["choices"])
        self.catalog.assert_not_called()
        self.assertNotIn("复核状态", [f["name"] for f in self.table.schema()])

    def test_empty_matches_and_mixed_instructions(self):
        result = self.chat.respond("搜一下清单里的未收录关键词")
        self.assertIn("0 条", result["message"])
        self.assertIn("已查字段", result["message"])
        for phrase in ("在清单里搜 Transformer，然后删除", "去网上找几篇关于知识蒸馏的论文，然后收藏",
                       "搜关于我收藏的 RAG 的论文", "找几篇关于客户代号幽兰的论文",
                       "查找公开题录和我的收藏里的 RAG 论文", "搜索《内部组会保密数据》", "在网上搜研究笔记列"):
            self.chat.respond(phrase)
        self.catalog.assert_not_called()
        self.assertEqual(self.table.rows(), self.rows)
