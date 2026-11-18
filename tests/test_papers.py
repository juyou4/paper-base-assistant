import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import model_support
import papers


class TestPapers(unittest.TestCase):
    def test_offline_reference_and_versioned_link(self):
        with tempfile.TemporaryDirectory() as data, patch.object(papers, "_open", side_effect=AssertionError):
            paper = papers.resolve_paper("把 https://arxiv.org/pdf/2005.11401v4.pdf 放进论文表", data, True)
            self.assertEqual(paper["key"], "arxiv:2005.11401")
            self.assertIn("DPR", paper["method"])
            self.assertTrue(paper["abstract_original"] and paper["method_original"])
            self.assertIsNone(papers.cached_pdf(paper, data))
            target = Path(data) / "cache" / "2005.11401v4.pdf"
            target.parent.mkdir()
            target.write_bytes(b"%PDF-incomplete")
            self.assertIsNone(papers.cached_pdf(paper, data))
            target.write_bytes(b"%PDF-1.5\nfixture\n%%EOF")
            self.assertEqual(papers.download_pdf(paper, data), target)

    def test_live_page_parser_with_fixed_responses(self):
        abstract = '<meta name="citation_title" content="Example"><blockquote>Abstract:Test abstract.</blockquote>'
        article = '<h2>3 Model Architecture</h2><p>The test model uses two encoders.</p><h2>4 Results</h2><p>Scores.</p>'
        with tempfile.TemporaryDirectory() as data, patch.object(papers, "_read_html", side_effect=[abstract, article]):
            paper = papers.resolve_paper("arXiv:2301.12345", data, True)
        self.assertEqual(paper["abstract_original"], "Test abstract.")
        self.assertEqual(paper["method_original"], "The test model uses two encoders.")
        self.assertEqual(paper["abstract_zh"], "")
        self.assertEqual(paper["method"], "")
        self.assertEqual(paper["method_url"], "https://arxiv.org/html/2301.12345")

    def test_inline_math_is_read_once_and_heading_link_comes_from_html(self):
        abstract = '<meta name="citation_title" content="Example"><blockquote>Abstract:Test abstract.</blockquote>'
        article = (
            '<section id="method-3"><h2>Model Architecture</h2>'
            '<p>Inputs <math alttext="x_{1}"><semantics><msub><mi>x</mi><mn>1</mn></msub>'
            '<annotation>x_{1}</annotation></semantics></math> have weights '
            '<math><semantics><mi>w</mi><mo>+</mo><mn>1</mn><annotation>w+1</annotation>'
            '<annotation-xml><span>duplicate content</span></annotation-xml></semantics></math>.</p>'
            r'<p>Variance <math><semantics><msup><mi>s</mi><mn>2</mn></msup>'
            r'<annotation encoding="application/x-tex">\sigma^2</annotation></semantics></math>.</p>'
            '</section><section id="results"><h2>Results</h2></section>'
        )
        with tempfile.TemporaryDirectory() as data, patch.object(papers, "_read_html", side_effect=[abstract, article]):
            paper = papers.resolve_paper("2301.12345", data, True)
        first_line = r"Inputs \(x_{1}\) have weights w+1."
        self.assertEqual(paper["method_original"], first_line + "\n" + r"Variance \(\sigma^2\).")
        self.assertEqual(paper["method_quote"], first_line)
        self.assertTrue(paper["method_url"].endswith("#method-3"))

    def test_english_excerpt_ends_at_paragraph_or_sentence(self):
        original = "We encode subscripts. The decoder uses (x_{1}, ..., x_{m}) tokens.\nMore text."
        self.assertEqual(papers.english_excerpt(original), original.splitlines()[0])
        self.assertEqual(papers.english_excerpt(original, 32), "We encode subscripts.")
        self.assertEqual(papers.english_excerpt("Refer to Fig. 1 for the equation. More results.", 18),
                         "Refer to Fig. 1 for the equation.")
        self.assertEqual(papers.english_excerpt("An unpunctuated math fragment", 10), "An unpunctuated math fragment")

    def test_external_link_is_saved_without_fetch(self):
        with tempfile.TemporaryDirectory() as data, patch.object(papers, "_open", side_effect=AssertionError):
            paper = papers.resolve_paper("https://example.com/paper.pdf", data, True)
            self.assertEqual(paper["url"], "https://example.com/paper.pdf")
            self.assertFalse(paper["pdf_url"])


class TestModels(unittest.TestCase):
    def setUp(self):
        self.env = {"PAPER_MODEL_URL": "http://localhost:9988/v1", "PAPER_MODEL_NAME": "example"}
        self.paper = {"abstract_zh": "", "method": "", "abstract_original": "The test corpus contains twenty documents.",
                      "abstract_source": "作者摘要", "method_original": ""}

    def test_disabled_and_no_evidence(self):
        with patch.dict(os.environ, {}, clear=True), patch.object(model_support, "_request", side_effect=AssertionError):
            self.assertFalse(model_support.enabled())
            self.assertEqual(self.paper, model_support.enrich(self.paper, {"abstract"}))
        with patch.dict(os.environ, self.env, clear=True), patch.object(model_support, "_request", side_effect=AssertionError):
            self.assertEqual(self.paper, model_support.enrich(self.paper, {"method"}))
            sample = papers.resolve_paper("2005.11401", ".")
            self.assertEqual(sample, model_support.enrich(sample, {"abstract", "method"}))

    def test_compatible_response_and_exact_evidence(self):
        item = {"abstract": {"value": "测试语料包含二十份文档。", "quote": "contains twenty documents"}}
        response = {"choices": [{"message": {"content": json.dumps(item, ensure_ascii=False)}}]}
        payload = io.BytesIO(json.dumps(response, ensure_ascii=False).encode("utf-8"))
        with patch.dict(os.environ, self.env, clear=True), patch.object(model_support, "build_opener") as opener:
            opener.return_value.open.return_value = payload
            result = model_support.enrich(self.paper, {"abstract", "method"})
            request = opener.return_value.open.call_args.args[0]
        self.assertEqual(request.full_url, "http://localhost:9988/v1/chat/completions")
        self.assertNotIn("Authorization", request.headers)
        messages = json.loads(request.data)["messages"]
        self.assertEqual(json.loads(messages[1]["content"]), {"abstract": self.paper["abstract_original"]})
        self.assertEqual(result["abstract_zh"], item["abstract"]["value"])
        self.assertEqual(result["abstract_quote"], item["abstract"]["quote"])
        self.assertIn("引文已核对", result["abstract_source"])
        self.assertEqual(self.paper["abstract_zh"], "")

    def test_unmatched_quote_does_not_write(self):
        item = {"abstract": {"value": "模型给出的解释。", "quote": "invented evidence from nowhere"}}
        with patch.dict(os.environ, self.env, clear=True), patch.object(model_support, "_request", return_value=item):
            with self.assertRaisesRegex(ValueError, "引文"):
                model_support.enrich(self.paper, {"abstract"})
        self.assertEqual(self.paper["abstract_zh"], "")

    def test_network_failure_is_visible(self):
        with patch.dict(os.environ, self.env, clear=True), patch.object(model_support, "build_opener") as opener:
            opener.return_value.open.side_effect = TimeoutError()
            with self.assertRaisesRegex(ValueError, "连接模型接口失败"):
                model_support.enrich(self.paper, {"abstract"})


if __name__ == "__main__":
    unittest.main()
