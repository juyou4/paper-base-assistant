"""从本地搜索结果按原序号回看记录，验证写入预览和公开候选的优先级。"""

import unittest
from unittest.mock import patch

from library_search import LOCAL
from table_store import LocalTable
from workflow import Conversation


class LibraryResultOpenTests(unittest.TestCase):
    def setUp(self):
        self.table = LocalTable(":memory:")
        self.addCleanup(self.table.close)
        self.chat = Conversation(self.table, ".", "阅读清单")
        rules = patch.dict("os.environ", PAPER_AGENT_PROVIDER="rules")
        rules.start()
        self.addCleanup(rules.stop)
        catalog = patch("papers.search_titles", side_effect=AssertionError("回看表内结果不能联网检索"))
        self.catalog = catalog.start()
        self.addCleanup(catalog.stop)

    def seed(self, number, title="同名论文"):
        link = f"https://example.org/saved-{number}"
        fields = {"论文标题": title, "论文链接": link, "阅读笔记": f"RAG 实验批次 {number}"}
        return self.table.upsert(link, fields, "seed-" + str(number))["record_id"]

    def test_followups_open_the_original_rows_even_after_reordering(self):
        older, newer = self.seed(1), self.seed(2)
        result = self.chat.respond("在我的清单里搜 RAG")
        self.assertIn("2 条", result["message"])
        self.assertIsNone(self.chat.pending)
        self.seed(3)
        with patch.object(self.table, "rows", side_effect=AssertionError("不能重跑搜索后按新序号开记录")), \
                patch.object(self.table, "upsert", side_effect=AssertionError("查看不写表")):
            second = self.chat.respond("打开第二篇")
            first = self.chat.respond("看看第一篇")
        self.assertEqual(second["saved_record_id"], older)
        self.assertIn("RAG 实验批次 1", second["message"])
        self.assertEqual(first["saved_record_id"], newer)
        self.assertIn("RAG 实验批次 2", first["message"])

    def test_one_result_after_choosing_local_source_can_be_opened(self):
        record_id = self.seed(1, "ＡＢＣ  原始题名")
        scope = self.chat.respond("帮我找找 RAG 的论文")
        result = self.chat.respond(LOCAL, scope["pending_id"])
        self.assertIn("ABC 原始题名", result["message"])
        self.assertIsNone(self.chat.pending)
        opened = self.chat.respond("打开这篇")
        self.assertEqual(opened["saved_record_id"], record_id)
        self.assertIn("ＡＢＣ  原始题名", opened["message"])

    def test_modified_identity_cannot_open_another_paper(self):
        self.seed(1)
        self.chat.respond("在我的清单里搜 RAG")
        self.table.upsert("https://example.org/saved-1", {"论文标题": "改为另一篇"}, "rename-paper")
        reply = self.chat.respond("打开第一篇")
        self.assertNotIn("saved_record_id", reply)
        self.assertNotIn("RAG 实验批次", reply["message"])
        self.assertIn("重新", reply["message"])

    def test_unselected_results_do_not_reuse_a_previously_opened_record(self):
        self.table.upsert("older", {"论文标题": "此前的论文", "阅读笔记": "上一条私有笔记"}, "older")
        self.assertIn("上一条私有笔记", self.chat.respond("打开《此前的论文》")["message"])
        self.seed(1, "新结果")
        self.seed(2, "另一条新结果")
        self.chat.respond("在清单里搜 RAG")
        self.assertIsNone(self.chat.last)
        reply = self.chat.respond("它的笔记呢")
        self.assertNotIn("saved_record_id", reply)
        self.assertNotIn("上一条私有笔记", reply["message"])
        self.assertIn("指定", reply["message"])

    def test_abbreviation_matches_the_original_title_and_shows_evidence(self):
        paper = "Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks"
        rid = self.table.upsert("alias", {"论文标题": paper}, "alias")["record_id"]
        self.table.upsert("noise", {"论文标题": "Storage Systems", "中文摘要": "coverage"}, "noise")
        result = self.chat.respond("在清单里搜 RAG")
        self.assertIn("找到 1 条", result["message"])
        self.assertIn("命中「论文标题」", result["message"])
        self.assertIn("RAG 对应题名中的 “Retrieval-Augmented Generation”", result["message"])
        self.assertNotIn("Storage Systems", result["message"])
        self.assertEqual(self.chat.respond("打开第一篇")["saved_record_id"], rid)

    def test_more_than_ten_only_exposes_displayed_positions(self):
        ids = [self.seed(i, f"实验 {i:02}") for i in range(12)]
        result = self.chat.respond("在清单里搜 RAG")
        self.assertIn("找到 12 条", result["message"])
        self.assertIn("前 10 条", result["message"])
        self.assertNotIn("实验 00", result["message"])
        with patch.object(self.table, "get", side_effect=AssertionError("不应猜测未展示行")):
            reply = self.chat.respond("打开第十一篇")
            self.assertIn("10", reply["message"])
        self.assertEqual(self.chat.respond("打开第十篇")["saved_record_id"], ids[2])

    def test_local_open_preserves_existing_write_preview(self):
        older, _ = self.seed(1), self.seed(2)
        self.chat.last = "https://example.org/previous"
        plan = self.chat.respond("新增文本列 复核状态")
        token = plan["pending_id"]
        self.assertEqual(self.chat.respond("在清单里搜 RAG", token)["pending_id"], token)
        opened = self.chat.respond("打开第二篇", token)
        self.assertEqual(opened["saved_record_id"], older)
        self.assertEqual(opened["pending_id"], token)
        self.assertEqual(opened["choices"], plan["choices"])
        self.assertEqual(self.chat.last, "https://example.org/previous")
        self.assertNotIn("复核状态", [f["name"] for f in self.table.schema()])
        self.chat.respond("确认建列", token)
        self.assertIn("复核状态", [f["name"] for f in self.table.schema()])

    def test_open_public_candidate_keeps_its_own_ordinals(self):
        self.seed(1)
        self.chat.respond("在清单里搜 RAG")
        self.catalog.side_effect = None
        self.catalog.return_value = [{"title": "Public Article", "url": "https://example.org/catalog-first"}]
        offer = self.chat.respond("去网上搜 Transformer 的论文")
        local = self.chat.respond("在清单里搜 RAG", offer["pending_id"])
        self.assertEqual(local["choices"], offer["choices"])
        chosen = self.chat.respond("打开第一篇", offer["pending_id"])
        self.assertNotIn("saved_record_id", chosen)
        self.assertIn("Public Article", chosen["message"])
        self.catalog.assert_called_once()
