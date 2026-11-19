"""PDF 网格证据须保留原页，缺少表题或表头时由用户明确选择。"""

import os
import unittest
from unittest.mock import patch

from evidence_extract import _pdf_grid, _pdf_items
from table_store import LocalTable
from workflow import Conversation


PAPER = "https://arxiv.org/abs/2005.11401v4"
PDF = PAPER.replace("/abs/", "/pdf/")
PAGE = PDF + "#page=6"
TITLE = "Grid review fixture"
SOURCE = "原文 PDF 第 6 页"
PAGE_LABEL = "第 6 页表格 2"
CLEAN_CELLS = [["Model", "Accuracy"], ["Small", "73.5"], ["Large", "81.2"]]
BROKEN_CELLS = [[None, "", ""], ["Den", "seNet", "121"], ["", "ResN", "34"]]


def grid(cells, label):
    return _pdf_grid(cells, label, label, SOURCE, PAGE, TITLE)


class UncertainPdfGridTests(unittest.TestCase):
    def test_clean_captioned_grid_retains_attributed_metrics(self):
        items = grid(CLEAN_CELLS, "Table 2")
        self.assertEqual(items[0]["rows"], CLEAN_CELLS[1:])
        self.assertFalse(any(item.get("review_required") for item in items))
        metrics = {item["label"]: item["value"] for item in items if item["kind"] == "metric"}
        self.assertEqual(metrics, {"Small · Accuracy": "73.5", "Large · Accuracy": "81.2"})

    def test_graph_fragments_and_grids_missing_caption_or_header_require_review(self):
        for cells, label in ((BROKEN_CELLS, PAGE_LABEL), (CLEAN_CELLS, PAGE_LABEL),
                             (BROKEN_CELLS, "Table 2")):
            with self.subTest(cells=cells, label=label):
                items = grid(cells, label)
                self.assertEqual([item["kind"] for item in items], ["table"])
                table = items[0]
                self.assertTrue(table.get("review_required"))
                self.assertEqual(table["source_url"], PAGE)
                self.assertEqual(table["rows"], cells[1:])

    def preview(self, items):
        table = LocalTable(":memory:")
        self.addCleanup(table.close)
        env = patch.dict(os.environ, PAPER_AGENT_PROVIDER="rules")
        env.start()
        self.addCleanup(env.stop)
        chat = Conversation(table, ".", "阅读清单")
        with patch("evidence_extract.extract_evidence", return_value={"title": TITLE, "url": PAPER, "items": items}):
            reply = chat.respond("提取论文的图表 " + PAPER)
        self.assertTrue(reply["evidence_preview"]["papers"][0]["items"][0]["review_required"])
        self.assertEqual(table.rows(), [])
        return chat, table, reply["pending_id"]

    def test_text_confirmation_of_mixed_evidence_writes_only_the_trusted_figure(self):
        figure = _pdf_items("Figure 1: Architecture of our model.", PDF)
        self.assertEqual([item["kind"] for item in figure], ["figure"])
        chat, table, token = self.preview(grid(BROKEN_CELLS, PAGE_LABEL) + figure)
        result = chat.respond("确认回填", token)["evidence_results"][0]
        self.assertEqual((result["status"], result["selected_items"]), ("verified", 1))
        fields = table.rows()[0]["fields"]
        self.assertEqual(fields["论文链接"], PAPER)
        self.assertIn("Architecture of our model", fields["研究笔记"])
        self.assertNotIn(PAGE_LABEL, fields["研究笔记"])
        self.assertNotIn(PAGE, fields["研究笔记"])

    def test_text_confirmation_with_only_uncertain_grid_waits_for_explicit_selection(self):
        chat, table, token = self.preview(grid(BROKEN_CELLS, PAGE_LABEL))
        with self.assertRaisesRegex(ValueError, "勾选"):
            chat.respond("确认回填", token)
        self.assertEqual(table.rows(), [])
        result = chat.respond("确认回填", token, evidence_selection=[[0]])["evidence_results"][0]
        self.assertEqual((result["status"], result["selected_items"]), ("verified", 1))
        fields = table.rows()[0]["fields"]
        self.assertEqual(fields["论文链接"], PAPER)
        self.assertIn(PAGE_LABEL, fields["研究笔记"])
        self.assertIn(PAGE, fields["研究笔记"])
