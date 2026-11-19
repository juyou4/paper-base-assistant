"""批次中的论文序号只指向展示过的论文，歧义和排除不能扩大收藏范围。"""

import unittest
from unittest.mock import patch

import papers
from paper_selection import paper_positions
from table_store import LocalTable
from workflow import Conversation


class PaperSelectionBoundsTests(unittest.TestCase):
    urls = ["https://arxiv.org/abs/" + n for n in ("2005.11401", "1706.03762", "1902.00002")]

    def setUp(self):
        self.table = LocalTable(":memory:")
        self.addCleanup(self.table.close)
        self.chat = Conversation(self.table, ".", "阅读清单")
        for mock in (patch.dict("os.environ", {"PAPER_AGENT_PROVIDER": "rules"}),
                     patch("papers.resolve_paper", side_effect=lambda url, *_, **__: papers._record(
                         key=url, url=url, title="论文 " + url.rsplit("/", 1)[-1]))):
            mock.start()
            self.addCleanup(mock.stop)

    def test_uncertain_or_negative_collection_waits_without_storing_any_paper(self):
        for phrase in ("第一篇以外的", "不保留第一篇，只选第二篇", "前两篇都不选", "第1篇和第2项",
                       "第一篇到", "会发代码的论文", "第一篇以后"):
            with self.subTest(phrase=phrase):
                result = self.chat.respond("收藏这几篇，" + phrase + " " + " ".join(self.urls))
                self.assertIn("pending_id", result)
                self.assertIn("第2篇", result["choices"])
                self.assertEqual(self.table.rows(), [])
                self.chat.respond("取消", result["pending_id"])

    def test_positive_range_stores_the_selected_rows(self):
        result = self.chat.respond("收藏第2到第3篇 " + " ".join(self.urls))
        self.assertEqual([card["url"] for card in result["collection"]], self.urls[1:])
        self.assertCountEqual([row["fields"]["论文链接"] for row in self.table.rows()], self.urls[1:])

    def test_revision_excludes_papers_or_requests_a_new_selection(self):
        def preview(table, urls, kinds):
            return {"ready": len(urls)}, {"message": "预览", "evidence_preview": [{"title": u} for u in urls]}

        with patch("evidence_workflow.prepare", side_effect=preview) as read:
            for phrase, expected in (("第一篇除外", self.urls[1:]), ("只看第一篇以外的", self.urls[1:]),
                                     ("前两篇都不选", self.urls[2:]), ("不要再提取第一篇", self.urls[1:])):
                with self.subTest(phrase=phrase):
                    draft = self.chat.respond("提取关键数据 " + " ".join(self.urls))
                    changed = self.chat.respond(phrase, draft["pending_id"])
                    self.assertEqual(read.call_args.args[1], expected)
                    self.assertNotEqual(changed["pending_id"], draft["pending_id"])
                    self.chat.respond("取消回填", changed["pending_id"])
            for phrase in ("不保留第一篇，只选第二篇", "第1篇和第2项", "第一篇到第", "倒数第4篇"):
                draft = self.chat.respond("提取关键数据 " + " ".join(self.urls))
                read.reset_mock()
                change = self.chat.respond(phrase, draft["pending_id"])
                self.assertIn("第2篇", change["choices"], phrase)
                self.assertNotEqual(change["pending_id"], draft["pending_id"])
                read.assert_not_called()
                self.chat.respond("取消", change["pending_id"])
        self.assertEqual(self.table.rows(), [])

    def test_positions_distinguish_evidence_types_from_papers(self):
        for phrase in ("第一篇不要公式", "第一篇只看指标，不看公式"):
            self.assertEqual(paper_positions(phrase, 3, exclude=True), [0])
        for phrase, expected in (("倒数第二篇", [2]), ("第二、四篇", [1, 3]), ("第二到第四篇", [1, 2, 3])):
            self.assertEqual(paper_positions(phrase, 4), expected)
