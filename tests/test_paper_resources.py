"""通过真实对话和 SQLite 验证资源只来源于论文、必须勾选、保留出处和记录身份。"""

import os
import unittest
from unittest.mock import patch

from evidence_workflow import prepare
from paper_resources import html_resource_items, pdf_resource_items
from table_store import LocalTable
from workflow import Conversation


PAPER = "https://arxiv.org/abs/2005.11401v4"
SOURCE = PAPER.replace("/abs/", "/html/")
REPO = "https://github.com/paper-lab/study/tree/main/models/rag"
MODEL = "https://huggingface.co/lab/encoder-v2"
DATA = "https://huggingface.co/datasets/lab/sample"
HTML = f"""<html><head><meta name="citation_title" content="Resource paper"></head>
<nav>Site <a href="https://github.com/site/footer">source</a></nav>
<article><section id="S1"><h1>Resource paper</h1>
<p id="p1">The baseline code is available at https://github.com/baseline-lab/previous.</p>
<p id="p2">Our implementation is available at <a href="{REPO}">the released repository</a>.</p>
<p id="p3">Model <a href="{MODEL}">checkpoint</a>, and benchmark <a href="{DATA}">dataset</a>.</p>
<figure id="F1"><figcaption>Figure 1: Model architecture</figcaption></figure>
</section></article>Outside https://github.com/site/footer</html>"""


class PaperResourcesTests(unittest.TestCase):
    def setUp(self):
        self.table = LocalTable(":memory:")
        self.addCleanup(self.table.close)
        self.chat = Conversation(self.table, ".", "阅读清单")
        env = patch.dict(os.environ, PAPER_AGENT_PROVIDER="rules")
        env.start()
        self.addCleanup(env.stop)

    def test_html_anchor_classification_and_exact_deep_link(self):
        items = html_resource_items(HTML, SOURCE)
        self.assertEqual([i["kind"] for i in items], ["code", "code", "model", "dataset"])
        code = items[1]
        self.assertEqual(code["resource_url"], REPO)
        self.assertIn("Our implementation", code["quote"])
        self.assertEqual(code["source_url"], SOURCE + "#p2")
        self.assertEqual(items[-1]["resource_url"], DATA)

    def test_pdf_wrap_preserves_page_and_subgroup(self):
        text = ("p1\fSee https://github.com/huggingface/transformers/blob/master/\nexamples/rag/. "
                "Also https://gitlab.com/lab/subgroup/encoder/-/tree/master.")
        items = pdf_resource_items(text, SOURCE)
        self.assertEqual([i["resource_url"] for i in items], [
            "https://github.com/huggingface/transformers/blob/master/examples/rag/",
            "https://gitlab.com/lab/subgroup/encoder/-/tree/master"])
        self.assertTrue(all(i["source_url"].endswith("#page=2") for i in items))

    def test_reserved_paths_placeholders_disguised_destinations_and_duplicates(self):
        body = ("https://github.com/orgs/team https://github.com/anonymous/code https://huggingface.co/docs/a "
                "https://example.org/github.com/other/repo https://github.com/abc/team/issues/1 "
                '<a href="https://example.net/login">https://github.com/abc/demo</a> '
                "https://github.com/real/data?token=hidden " + REPO + " " + REPO)
        self.assertEqual([i["value"] for i in html_resource_items(body, SOURCE)], [REPO])

    def test_preview_commit_requires_explicit_selection_and_leaves_identity_column(self):
        self.table.add_field("代码链接", "text")
        before = self.table.upsert(PAPER, {"论文标题": "Resource paper", "论文链接": PAPER, "阅读笔记": "原笔记"}, "old")
        with patch("papers._read_html", return_value=HTML) as reader, patch("papers.resolve_paper", side_effect=AssertionError):
            reply = self.chat.respond("帮我看看论文的代码仓库和数据集链接 " + PAPER)
            reader.assert_called_once()
        items = reply["evidence_preview"]["papers"][0]["items"]
        self.assertEqual([i["kind"] for i in items], ["code", "code", "dataset"])
        self.assertNotIn("代码链接", self.table.rows()[0]["fields"])
        token = reply["pending_id"]
        with self.assertRaisesRegex(ValueError, "勾选"):
            self.chat.respond("确认回填", token)
        with self.assertRaisesRegex(ValueError, "不可用于"):
            self.chat.respond("确认回填", token, evidence_selection=[[1]], evidence_fields=[{"1": "论文链接"}])
        done = self.chat.respond("确认回填", token, evidence_selection=[[1, 2]])
        self.assertEqual(done["evidence_results"][0]["selected_items"], 2)
        row = self.table.get(before["record_id"])
        self.assertEqual(row["fields"]["论文链接"], PAPER)
        self.assertEqual(row["fields"]["阅读笔记"], "原笔记")
        self.assertIn(REPO, row["fields"]["代码链接"])
        self.assertIn(SOURCE + "#p2", row["fields"]["代码链接"])
        self.assertNotIn("baseline-lab", row["fields"]["代码链接"])
        self.assertIn(DATA, row["fields"]["研究笔记"])

    def test_text_confirmation_of_mixed_batch_skips_all_resources(self):
        with patch("papers._read_html", return_value=HTML):
            reply = self.chat.respond("提取论文配套资源和图表 " + PAPER)
        result = self.chat.respond("确认回填", reply["pending_id"])
        self.assertEqual(result["evidence_results"][0]["selected_items"], 1)
        self.assertIn("Figure 1", self.table.rows()[0]["fields"]["研究笔记"])
        self.assertNotIn("github.com", str(self.table.rows()[0]))

    def test_absent_links_have_no_substitute(self):
        with patch("papers._read_html", return_value=HTML):
            plan, _ = prepare(self.table, [PAPER], ["model"])
        items = plan["entries"][0]["items"]
        self.assertEqual([item["resource_url"] for item in items], [MODEL])
        with patch("papers._read_html", return_value=HTML.replace("huggingface.co", "example.net")):
            plan, _ = prepare(self.table, [PAPER], ["model"])
        self.assertEqual(plan["ready"], 0)
        self.assertIn("没有找到", plan["entries"][0]["error"])
        self.assertEqual(self.table.rows(), [])

    def test_hyperlink_column_is_not_used_as_paper_identifier_or_text_evidence(self):
        self.table.add_field("代码链接", "url")
        with patch("papers._read_html", return_value=HTML):
            reply = self.chat.respond("提取论文代码仓库 " + PAPER)
        result = self.chat.respond("确认回填", reply["pending_id"], evidence_selection=[[1]])
        self.assertEqual(result["evidence_results"][0]["status"], "verified")
        fields = self.table.rows()[0]["fields"]
        self.assertEqual(fields["论文链接"], PAPER)
        self.assertNotIn("代码链接", fields)
        self.assertIn(REPO, fields["研究笔记"])
