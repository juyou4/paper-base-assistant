"""从真实对话入口核验表内查询，不触发论文下载、数据提取或写入。"""

import unittest
from unittest.mock import patch

from admin_dialog import table_lookup_kind
from table_store import LocalTable
from workflow import Conversation


class CasualQueryTests(unittest.TestCase):
    def setUp(self):
        self.table = LocalTable(":memory:")
        self.addCleanup(self.table.close)
        self.chat = Conversation(self.table, ".", "阅读清单")
        self.url = "https://example.org/paper-a"
        self.table.upsert(self.url, {"论文标题": "A Study of Trees", "论文链接": self.url}, "a")
        self.table.upsert("note-only", {"研究笔记": "其他待办"}, "b")
        rules = patch.dict("os.environ", {"PAPER_AGENT_PROVIDER": "rules"})
        rules.start()
        self.addCleanup(rules.stop)

    def test_asks_which_papers_or_how_many_instead_of_a_new_extraction(self):
        before = self.table.rows()
        with patch("evidence_workflow.prepare", side_effect=AssertionError("查询不应提取论文")), \
             patch.object(self.table, "upsert", side_effect=AssertionError("查询不应写表")):
            for message in ("有哪些论文", "我们目前都收藏了哪些文献", "帮我看下我存了哪些论文", "表里有哪些文章？",
                            "我收藏的论文有哪些", "别动数据，我只想看看表里有哪些论文", "列出论文列表"):
                with self.subTest(message=message):
                    result = self.chat.respond(message)
                    self.assertIn("A Study of Trees", result["message"])
                    self.assertIn(self.url, result["message"])
                    self.assertNotIn("其他待办", result["message"])
                    self.assertNotIn("pending_id", result)
            for message in ("我现在收藏了几篇论文？", "有几篇？", "我的论文库里一共收录了多少篇", "当前库里有几篇"):
                with self.subTest(message=message):
                    self.assertIn("1 条填写了论文标题或链接", self.chat.respond(message)["message"])
            self.assertIn("2 条可见记录", self.chat.respond("表里存了什么？")["message"])
        self.assertEqual(before, self.table.rows())

    def test_field_questions_read_schema_only_in_both_modes(self):
        with patch.object(self.table, "rows", side_effect=AssertionError("查类型无需读取记录")):
            for phrase in ("帮我查一下，「论文链接」这一栏具体是什么类型？", "论文链接是什么类型", "查下论文链接这列是文本还是超链接"):
                self.assertIn("「论文链接」的类型为超链接", self.chat.respond(phrase)["message"])
            both = self.chat.respond("笔记这列是什么类型")["message"]
            self.assertIn("「研究笔记」", both)
            self.assertIn("「阅读笔记」", both)
            self.assertIn("没有找到", self.chat.respond("这张表的「审稿人」是什么类型")["message"])
            for phrase in ("表里都有啥列", "都有些什么列"):
                self.assertIn("论文链接（超链接）", self.chat.respond(phrase)["message"])
                with patch("model_support.agent_info", return_value={"enabled": True}), \
                     patch("agent_router.request_json", return_value={"op": "columns"}):
                    self.assertIn("论文链接（超链接）", self.chat.respond(phrase)["message"])

    def test_exact_title_checks_visible_local_rows_only(self):
        with patch("papers.search_titles", side_effect=AssertionError("表内按题名核对无需访问外部书目")):
            present = self.chat.respond("我之前收藏过《a study OF Trees》吗？")
            self.assertIn("找到 1 条", present["message"])
            self.assertNotIn("pending_id", present)
            self.assertIn("可见记录中未找到", self.chat.respond("表里有没有《Study》？")["message"])
            self.assertIsNone(table_lookup_kind("搜索《A Study of Trees》并收藏", "阅读清单"))

    def test_reads_during_a_pending_write_keep_the_same_confirmation(self):
        plan = self.chat.respond("新增文本列 复核结果")
        for phrase in ("有几篇？", "论文链接是什么类型", "表里都有些什么列", "有哪些论文", "表里有《A Study of Trees》吗"):
            with self.subTest(message=phrase):
                self.assertEqual(self.chat.respond(phrase, plan["pending_id"])["pending_id"], plan["pending_id"])
                self.assertNotIn("复核结果", [f["name"] for f in self.table.schema()])
        self.chat.respond("确认建列", plan["pending_id"])
        self.assertIn("复核结果", [f["name"] for f in self.table.schema()])

    def test_mixed_requests_and_filters_do_not_answer_with_an_unfiltered_list(self):
        before = self.table.rows()
        with patch.object(self.table, "rows", side_effect=AssertionError("不能将所有记录当作筛选结果")), \
             patch("model_support.request_json", side_effect=AssertionError("已知不支持的查询无需模型")):
            for phrase in ("查一下表然后删掉记录", "查一下表，顺便写入笔记", "查一下这张表里2025年发表的论文有几篇",
                           "查一下这张表里有哪些未读论文"):
                with self.subTest(message=phrase):
                    result = self.chat.respond(phrase)
                    self.assertNotIn("A Study of Trees", result["message"])
                    self.assertNotIn("pending_id", result)
        self.assertEqual(before, self.table.rows())

    def test_declined_queries_do_not_call_storage_or_model(self):
        with patch.object(self.table, "rows", side_effect=AssertionError("用户拒绝查看记录")), \
             patch.object(self.table, "schema", side_effect=AssertionError("用户拒绝查看字段")), \
             patch("model_support.request_json", side_effect=AssertionError("明确否定不需要模型")):
            for sentence in ("不许查这张表的数据", "先别看这张表有哪些列", "我不想查论文清单"):
                self.assertIn("跳过查询", self.chat.respond(sentence)["message"])
