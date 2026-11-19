import csv
import io
import json
import re
import tempfile
import unittest
from unittest.mock import patch

import papers
import source_library
from research_exports import export_records, markdown_cell
from table_store import LocalTable


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.table = LocalTable(":memory:")
        self.addCleanup(self.table.close)
        self.schema = self.table.schema()

    def export(self, rows, fmt, schema=None):
        with patch.object(self.table, "rows", side_effect=AssertionError("不得重新枚举清单")), \
                patch("papers._read_html", side_effect=AssertionError("导出不可联网")):
            return export_records(self.table, self.schema if schema is None else schema, rows, fmt, "当前表")

    def test_verified_crossref_types_survive_collection(self):
        rows = []
        for i, (registry_type, venue) in enumerate([("journal-article", "中文期刊"), ("proceedings-article", "V Conf"),
                                                    ("posted-content", ""), ("book-chapter", "来源未分类")]):
            doi = f"10.1234/ref.{i}"
            url = "https://doi.org/" + doi
            source = {"DOI": doi, "title": [f"Title {i}"], "type": registry_type, "container-title": [venue],
                      "author": [{"given": "J.", "family": "Smith"}, {"given": "王", "family": "亦"}],
                      "published": {"date-parts": [[2025]]}}
            with tempfile.TemporaryDirectory() as tmp, patch("papers._read_html", return_value=json.dumps({"message": source})):
                paper = papers.resolve_paper(url, tmp)
            row = {"id": f"rec{i}", "fields": {"论文链接": url, "论文标题": f"Title {i}"}}
            source_library.remember(self.table, row, paper, [url])
            rows.append(row)
        bib = self.export(rows, "bibtex")["content"]
        self.assertEqual(re.findall(r"^@(\w+)", bib, re.M), ["article", "inproceedings", "misc", "misc"])
        self.assertIn("journal = {中文期刊}", bib)
        self.assertIn("booktitle = {V Conf}", bib)
        self.assertIn("author = {J. Smith and 王 亦}", bib)
        ris = self.export(rows, "ris")["content"]
        self.assertEqual(re.findall(r"^TY  - (\w+)", ris, re.M), ["JOUR", "CPAPER", "GEN", "GEN"])
        self.assertIn("AU  - 王 亦\r\nPY  - 2025", ris)
        self.assertIn("JO  - 中文期刊", ris)
        self.assertIn("T2  - V Conf", ris)

    def test_duplicates_special_characters_and_missing_metadata(self):
        row = {"id": "a", "fields": {"论文标题": "Mix } 写法 {\\ &% #_\r\nTY  - JOUR",
                                     "论文链接": "https://example.org/p?q=x_y&a=2"}}
        result = self.export([row, dict(row, id="b"), {"id": "blank", "fields": {}},
                              {"id": "note", "fields": {"研究笔记": "待审"}}], "bib")
        self.assertEqual(result["count"], 2)
        self.assertIn("2 条引文缺少作者或年份", result["notice"])
        self.assertIn("1 条记录", result["notice"])
        keys = re.findall(r"^@misc\{([^,]+)", result["content"], re.M)
        self.assertEqual(len(set(keys)), 2)
        self.assertNotRegex(result["content"], r"\b(?:author|year)\s*=")
        self.assertIn(r"{\char125}", result["content"])
        self.assertIn("url = {https://example.org/p?q=x_y&a=2}", result["content"])
        ris = self.export([row], "ris")["content"]
        self.assertEqual(re.findall(r"^TY  - (\w+)", ris, re.M), ["GEN"])
        self.assertNotRegex(ris, r"(?m)^(AU|PY)  - ")

    def test_export_cannot_recover_unselected_rows_hidden_fields_or_stale_metadata(self):
        old = {"id": "row1", "fields": {"论文链接": "https://doi.org/10.1122/old", "论文标题": "旧题名"}}
        source_library.remember(self.table, old, {"url": old["fields"]["论文链接"],
                                "title": "旧题名", "year": "1998", "authors": ["Old Author"]}, [])
        changed = {"id": "row1", "fields": {"论文链接": "https://doi.org/10.1234/new", "论文标题": "新题名",
                                           "研究笔记": "不可见笔记"}}
        schema = [f for f in self.schema if f["name"] in {"论文链接", "论文标题"}]
        for fmt in ("bibtex", "ris", "csv", "markdown"):
            content = self.export([changed], fmt, schema)["content"]
            self.assertNotRegex(content, "不可见笔记|旧题名|10.1122/old|1998|Old Author")
        title_only = self.export([old], "bibtex", [self.schema[0]])["content"]
        self.assertNotRegex(title_only, r"Old Author|1998|https://")

    def test_csv_preserves_columns_quotes_and_newlines_and_neutralizes_formulas(self):
        schema = self.schema + [{"name": "=x", "type": "text"}, {"name": "作者", "type": "text"}]
        row = {"id": "a", "fields": {"论文标题": " \t=1+1", "阅读笔记": '引号", 第二行\r\n第三行',
                                     "研究笔记": "＠测试", "=x": "-cmd()", "作者": "Smith, J.; Li, T."}}
        result = self.export([row], "csv", schema)
        self.assertTrue(result["content"].encode().startswith(b"\xef\xbb\xbf"))
        csv_row = list(csv.DictReader(io.StringIO(result["content"][1:], newline="")))[0]
        self.assertEqual(csv_row["阅读笔记"], row["fields"]["阅读笔记"])
        self.assertEqual(csv_row["作者"], "Smith, J.; Li, T.")
        self.assertEqual(csv_row["论文标题"], "' \t=1+1")
        self.assertEqual(csv_row["研究笔记"], "'＠测试")
        self.assertEqual(csv_row["'=x"], "'-cmd()")

    def test_markdown_keeps_primary_and_verified_sources_and_escapes_embedded_markup(self):
        url = "https://example.org/article?id=main&edition=2"
        mirror = "https://example.org/repository/main"
        row = {"id": "A", "fields": {"论文标题": "[稿件]", "论文链接": url, "研究笔记": "<script>\n![X](图片)"}}
        source_library.remember(self.table, row, {"url": url}, [mirror])
        md = self.export([row], "md")["content"]
        self.assertIn(f"<{url}>", md)
        self.assertIn(f"<{mirror}>", md)
        self.assertIn(r"\[稿件\]", md)
        self.assertIn(r"&lt;script&gt;", md)
        self.assertIn(r"\!\[X\]", md)
        self.assertEqual(markdown_cell("A|B\n<script>"), r"A\|B<br>&lt;script&gt;")

    def test_empty_export_does_not_create_a_source_registry(self):
        row = {"id": "A", "fields": {"论文链接": "https://example.org/p"}}
        before = list(self.table._db.execute("select name from sqlite_master order by name"))
        self.assertEqual(self.export([], "ris")["count"], 0)
        self.assertEqual(self.export([row], "bibtex")["count"], 1)
        self.assertEqual(list(self.table._db.execute("select name from sqlite_master order by name")), before)
        with self.assertRaises(ValueError):
            self.export([], "doc")
