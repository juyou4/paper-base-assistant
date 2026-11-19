import unittest
from contextlib import ExitStack
from unittest.mock import patch

from saved_compare import handle
from table_store import LocalTable
from workflow import Conversation


FIRST = "RAG: Retrieval and Generation"
SECOND = "视觉和语言：多模态论文"
URL_A = "https://arxiv.org/abs/2005.11401"
URL_B = "https://doi.org/10.1234/vision"
METRIC = "【指标】测试集准确率 · 81%\n原文摘录：81% on test\n\nacross runs\n出处：第 4 页  " + URL_A
FIGURE = "【图】Figure 2 · Attention map\n出处：第 7 页  " + URL_B


class SavedCompareTests(unittest.TestCase):
    def setUp(self):
        self.table = LocalTable(":memory:")
        self.addCleanup(self.table.close)
        self.chat = Conversation(self.table, ".", "阅读清单")
        self.table.add_field("关键指标", "text")
        self.table.upsert(URL_A, {"论文标题": FIRST, "论文链接": URL_A, "方法概述": "先检索，再生成。",
                                "阅读笔记": "个人阅读感想\n\n" + METRIC + "\n\n稍后的个人笔记"}, "one")
        self.table.upsert(URL_B, {"论文标题": SECOND, "论文链接": URL_B, "关键指标": "0",
                                "研究笔记": FIGURE}, "two")
        self.guards = ExitStack()
        self.addCleanup(self.guards.close)
        for target in ("papers.resolve_paper", "papers.search_titles", "model_support.request_json"):
            self.guards.enter_context(patch(target, side_effect=AssertionError("比较仅回读已保存的记录")))

    def test_two_papers_snapshot_and_requested_values_have_provenance(self):
        with patch.object(self.table, "rows", wraps=self.table.rows) as rows, \
                patch.object(self.table, "schema", wraps=self.table.schema) as schema, \
                patch.object(self.table, "upsert", side_effect=AssertionError("比较不可写表")):
            reply = handle(self.chat, f"请对比一下我收藏的《{FIRST}》和《{SECOND}》的方法/关键数据/图表")
        rows.assert_called_once()
        schema.assert_called_once()
        data = reply["saved_comparison"]
        self.assertEqual([(p["title"], p["url"]) for p in data["papers"]], [(FIRST, URL_A), (SECOND, URL_B)])
        self.assertEqual(data["fields"], [
            {"label": "方法", "values": [[{"field": "方法概述", "value": "先检索，再生成。"}], []]},
            {"label": "关键数据", "values": [[{"field": "阅读笔记", "value": METRIC}], [{"field": "关键指标", "value": "0"}]]},
            {"label": "图表", "values": [[], [{"field": "研究笔记", "value": FIGURE}]]},
        ])
        for expected in (FIRST, SECOND, URL_A, URL_B, "未保存", "先检索，再生成。", METRIC, FIGURE):
            self.assertIn(expected, reply["message"])
        self.assertNotIn("个人阅读感想", reply["message"])
        self.assertNotIn("稍后的个人笔记", reply["message"])

    def test_alias_and_notes_include_only_requested_real_fields(self):
        reply = handle(self.chat, f"把我收藏的 RAG 和《{SECOND}》的研究笔记对比一下")
        self.assertEqual(reply["saved_comparison"]["fields"], [
            {"label": "研究笔记", "values": [[], [{"field": "研究笔记", "value": FIGURE}]]},
        ])
        self.assertNotIn(METRIC, reply["message"])
        self.assertNotIn("个人阅读感想", reply["message"])

    def test_ambiguous_missing_and_same_record_never_form_a_pair(self):
        duplicate = "RAG: A Separate Study"
        self.table.upsert("https://doi.org/10.1234/new", {"论文标题": duplicate}, "more")
        reply = handle(self.chat, "比较我收藏的《RAG》和《未收录题名》的摘要")
        for text in ("RAG", "2 条记录", FIRST, duplicate, "未收录题名", "没有匹配", "完整题名"):
            self.assertIn(text, reply["message"])
        self.assertNotIn("saved_comparison", reply)
        same = handle(self.chat, f"对比我收藏的《Retrieval》和《{FIRST}》的关键数据")
        self.assertIn("同一条收藏", same["message"])
        self.assertNotIn("saved_comparison", same)
        missing_link = handle(self.chat, f"对比《{duplicate}》与《{SECOND}》的摘要")
        self.assertIn("未保存论文链接", missing_link["message"])

    def test_pending_and_existing_reference_are_preserved(self):
        task = {"id": "original", "stage": "field", "plan": {"url": URL_B}, "choices": ["研究笔记", "取消"]}
        self.chat.pending = task
        self.chat.last = URL_B
        before = self.table.rows()
        for query in (f"对照一下我收藏的《RAG》和《{SECOND}》的笔记", "对比我收藏的《无结果甲》和《无结果乙》"):
            with self.subTest(query=query):
                reply = handle(self.chat, query)
                self.assertIs(self.chat.pending, task)
                self.assertEqual(reply["pending_id"], "original")
                self.assertEqual(reply["choices"], task["choices"])
                self.assertEqual(self.chat.last, URL_B)
                self.assertEqual(self.table.rows(), before)
        self.assertIsNone(handle(self.chat, "研究笔记"))

    def test_comparison_binds_two_ordinals_and_clears_the_old_singular_focus(self):
        first = self.chat.respond("再看看《RAG》")
        self.assertIn(FIRST, first["message"])
        query = f"对比我收藏的两篇论文：《RAG》和《{SECOND}》在方法上有什么区别"
        self.chat.respond(query)
        opened = self.chat.respond("打开第二篇")
        self.assertIn(SECOND, opened["message"])
        self.assertEqual(self.chat.last, URL_B)
        self.chat.respond(query)
        unclear = self.chat.respond("它的笔记呢")
        self.assertIn("先指定", unclear["message"])
        self.assertNotIn(FIGURE, unclear["message"])

    def test_other_comparisons_do_not_read_table_and_unclear_titles_ask_for_quotes(self):
        with patch.object(self.table, "rows", side_effect=AssertionError("范围未定不可查表")):
            for query in ("对比一下 RAG 和 BM25 的价格", "它和上次那个有何区别？", "不要对比我收藏的两篇论文",
                          "对比网上《RAG》和《BM25》的方法"):
                with self.subTest(query=query):
                    self.assertIsNone(handle(self.chat, query))
            reply = handle(self.chat, "对比我收藏的 Retrieval and Generation 和 视觉和语言 的方法")
            self.assertIn("分别标出", reply["message"])
            unsupported = handle(self.chat, "对比一下我收藏的《RAG》和《BM25》的作者履历")
            self.assertIn("已保存", unsupported["message"])
            mixed = self.chat.respond("对比我收藏的《RAG》和《BM25》的摘要然后再写入")
            self.assertIn("只读取", mixed["message"])
            self.assertIsNone(self.chat.pending)
