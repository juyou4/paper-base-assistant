import unittest
import importlib.util
import tempfile
from pathlib import Path
from unittest.mock import patch

import evidence_extract as extract


class EvidenceExtractionTests(unittest.TestCase):
    base = "https://arxiv.org/html/2401.90909v1"

    def parse(self, body):
        return extract._article_evidence("<article><h1>A paper</h1>" + body + "</article>", self.base)[1]

    def test_multirow_metrics_keep_row_and_full_column_context(self):
        items = self.parse("""
        <figure id="T1"><figcaption>Table 1: Evaluation.</figcaption><table>
        <thead><tr><th rowspan="2">Encoder</th><th colspan="2">Accuracy (%)</th></tr>
        <tr><th>Validation</th><th>Test</th></tr></thead>
        <tbody><tr><th>Base</th><td>71.1</td><td>68.5</td></tr>
        <tr><th>Large</th><td>76.8</td><td>72.6</td></tr></tbody></table></figure>
        <figure id="F1"><img src="2401.90909v1/chart.png"/>
        <figcaption>Figure 1: Accuracy by size.</figcaption></figure>""")
        self.assertEqual(items[0]["headers"], ["Encoder", "Accuracy (%) / Validation", "Accuracy (%) / Test"])
        metric = next(i for i in items if i["label"] == "Large · Accuracy (%) / Test")
        self.assertEqual(metric["value"], "72.6")
        self.assertEqual(metric["source_url"], self.base + "#T1")
        self.assertEqual(items[-1]["image_url"], self.base + "/chart.png")
        self.assertEqual(items[0]["rows"][-1], ["Large", "76.8", "72.6"])

    def test_ambiguous_column_does_not_emit_scalar(self):
        items = self.parse("""
        <figure><figcaption>Table 2: Unspecified splits.</figcaption>
        <table><tr><th>Model</th><th colspan="2">BLEU</th></tr>
        <tr><td>Ours</td><td>30.1</td><td>28.8</td></tr></table></figure>""")
        self.assertEqual([i["kind"] for i in items], ["table"])
        self.assertEqual(items[0]["rows"], [["Ours", "30.1", "28.8"]])

    def test_rag_nested_figures_preserve_table_anchor(self):
        items = self.parse("""
        <figure id="outer"><figure id="child"><table><tr><th>Model</th><th>F1</th></tr>
        <tr><td>Delta</td><td>73.2</td></tr></table><figcaption>Table 2: F1 results.</figcaption>
        </figure></figure><figure id="arch"><object data="https://example.com/private.png"></object>
        <figcaption>Figure 1: Architecture.</figcaption></figure>""")
        self.assertTrue(items[0]["source_url"].endswith("#child"))
        self.assertNotIn("image_url", items[-1])

    def test_both_verified_snapshots_are_version_bound(self):
        with patch.object(extract.papers, "_read_html", side_effect=OSError):
            for version in extract.OFFLINE_VERSIONS.values():
                paper = extract.extract_evidence(version)
                self.assertTrue(paper["url"].endswith(version))
                for i in paper["items"]:
                    self.assertIn(version, i["source_url"])
                    self.assertIn("预核", i["source"])
                self.assertEqual({i["kind"] for i in paper["items"]}, {"table", "metric", "figure"})
            with patch.object(extract.papers, "resolve_paper", return_value={"title": "", "pdf_url": ""}):
                with self.assertRaises(ValueError):
                    extract.extract_evidence("1706.03762v1")

    def test_live_data_takes_priority(self):
        html = """<h1>Current paper</h1><figure id="plot"><object data="2005.11401v4/arch.svg"></object>
        <figcaption>Figure 2: Current plot.</figcaption></figure>"""
        with patch.object(extract.papers, "_read_html", return_value=html):
            paper = extract.extract_evidence("2005.11401v4")
        self.assertEqual(paper["title"], "Current paper")
        self.assertEqual(paper["items"][0]["image_url"], "https://arxiv.org/html/2005.11401v4/arch.svg")
        self.assertNotIn("预核", paper["items"][0]["source"])

    def test_pdf_page_links_and_reported_results(self):
        items = extract._pdf_items("Title\fFigure 2: Scaling curves.\n\nOur model achieved 71.2% accuracy.",
                                   "https://aclanthology.org/P19-1001.pdf")
        self.assertEqual([i["kind"] for i in items], ["figure", "metric"])
        self.assertTrue(all(i["source_url"].endswith("#page=2") for i in items))
        self.assertIn("71.2%", items[-1]["quote"])

    def test_pdf_merged_header_grid_has_no_standalone_scalars(self):
        items = extract._pdf_grid([["Model", "BLEU", None], ["Ours", "21.2", "25.1"]],
                                  "Table 1", "Translation", "PDF", "https://arxiv.org/pdf/2401.90909", "")
        self.assertEqual([i["kind"] for i in items], ["table"])
        self.assertEqual(items[0]["rows"], [["Ours", "21.2", "25.1"]])

    @unittest.skipUnless(importlib.util.find_spec("pdfplumber") and importlib.util.find_spec("reportlab"),
                         "真实网格 PDF 用例需要 pdfplumber 与 reportlab")
    def test_pdfplumber_reads_real_cells_from_second_page(self):
        from reportlab.pdfgen.canvas import Canvas
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "paper.pdf"
            pdf = Canvas(str(path), pagesize=(400, 400))
            pdf.drawString(20, 320, "Synthetic paper for parser test")
            pdf.showPage()
            pdf.drawString(20, 330, "Table 2: Test accuracy")
            for x in (20, 120, 220):
                pdf.line(x, 250, x, 310)
            for y in (250, 270, 290, 310):
                pdf.line(20, y, 220, y)
            for y, row in zip((297, 277, 257), [("Model", "Accuracy"), ("Small", "73.5"), ("Large", "81.2")]):
                for x, value in zip((23, 123), row):
                    pdf.drawString(x, y, value)
            pdf.save()
            paper = {"title": "Synthetic", "pdf_url": "https://aclanthology.org/P19-1001.pdf"}
            result = extract._pdf_tables(path, paper, "Title\fTable 2: Test accuracy")
        self.assertEqual(result[0]["label"], "Table 2")
        self.assertEqual(result[0]["headers"], ["Model", "Accuracy"])
        self.assertEqual(result[0]["rows"][1], ["Large", "81.2"])
        metric = next(i for i in result if i["label"] == "Large · Accuracy")
        self.assertEqual(metric["value"], "81.2")
        self.assertTrue(metric["source_url"].endswith(".pdf#page=2"))

    def test_untrusted_source_rejected_before_network(self):
        with patch.object(extract.papers, "_open", side_effect=AssertionError), self.assertRaises(ValueError):
            extract.extract_evidence("https://example.org/a.pdf")


if __name__ == "__main__":
    unittest.main()
