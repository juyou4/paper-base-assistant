"""不同真实收藏入口的标识、公开全文及失败路径回归。"""

import io
import json
import tempfile
import unittest
from urllib.error import HTTPError
from urllib.request import Request
from unittest.mock import patch

import paper_sources
import papers


DOI = "10.1109/cvpr.2016.90"
LANDING = "https://doi.org/" + DOI
IEEE = "https://ieeexplore.ieee.org/document/7780459"
CVF = "https://openaccess.thecvf.com/content_cvpr_2016/html/He_Deep_Residual_Learning_CVPR_2016_paper.html"
CVPDF = CVF.replace("/html/", "/papers/").replace(".html", ".pdf")
TITLE = "Deep Residual Learning for Image Recognition"
CV_PAGE = f"""<meta name="citation_title" content="{TITLE}">
<meta name="citation_author" content="He, Kaiming"><meta name="citation_author" content="Zhang, Xiangyu">
<meta name="citation_publication_date" content="2016"><meta name="citation_conference_title" content="CVPR">
<div id="abstract">The article's own abstract.</div><a href="{CVPDF}">pdf</a>"""
META = {"DOI": DOI, "title": [TITLE], "type": "proceedings-article", "container-title": ["CVPR"],
        "author": [{"given": "Kaiming", "family": "He"}, {"given": "Xiangyu", "family": "Zhang"}],
        "published": {"date-parts": [[2016]]}, "resource": {"primary": {"URL": IEEE + "/"}}}
FORUM = "https://openreview.net/forum?id=YicbFdNTTy"


def metadata(url, limit):
    if url == CVF:
        return CV_PAGE
    if url == "https://openaccess.thecvf.com/CVPR2016?day=all":
        return f'<dt><a href="{CVF}">{TITLE}</a></dt>'
    if url.startswith(paper_sources.CROSSREF + "?"):
        return json.dumps({"message": {"items": [META]}})
    if url.casefold() == paper_sources.CROSSREF + "/" + DOI:
        return json.dumps({"message": META})
    raise AssertionError(url)


class SourceTests(unittest.TestCase):
    def test_doi_cvf_page_and_pdf_point_to_verified_same_conference(self):
        with tempfile.TemporaryDirectory() as work, patch.object(papers, "_read_html", side_effect=metadata):
            for url in [DOI.upper(), CVF, CVPDF, LANDING]:
                with self.subTest(url=url):
                    paper = papers.resolve_paper(url, work)
                    self.assertEqual((paper["key"], paper["url"], paper["pdf_url"]), ("doi:" + DOI, LANDING, CVPDF))
                    self.assertTrue({LANDING, CVF, IEEE} <= set(paper["aliases"]))
                    self.assertTrue({CVF, CVPDF} <= set(paper["evidence_urls"]))
                    self.assertEqual(paper["abstract_source"], "CVF 作者摘要")
                    self.assertEqual(paper["year"], "2016")
                    self.assertEqual(len(paper["authors"]), 2)
                    self.assertEqual(paper["source_version"], "CVF 公开录用稿")

    def test_pure_keys_keep_openreview_case_and_accept_research_link_forms(self):
        with patch.object(papers, "_open", side_effect=AssertionError):
            self.assertEqual(papers.paper_keys(" DOI:10.1109/CVPR.2016.90 "), papers.paper_keys(LANDING))
            self.assertEqual(papers.paper_keys(CVF), papers.paper_keys(CVPDF + "#page=4"))
            self.assertEqual(papers.paper_keys(IEEE + "?ref=inbox"), papers.paper_keys(
                "https://ieeexplore.ieee.org/stamp/stamp.jsp?arnumber=7780459"))
            self.assertEqual(papers.paper_keys("https://openreview.net/pdf?id=YicbFdNTTy"), papers.paper_keys(FORUM))
            self.assertNotEqual(papers.paper_keys(FORUM), papers.paper_keys(FORUM.lower()))
            self.assertEqual(papers.paper_keys("10.48550/arXiv.2010.11929"), papers.paper_keys("arxiv:2010.11929v2"))
            self.assertEqual(papers.paper_keys("https://aclanthology.org/P19-1001.pdf"),
                             papers.paper_keys("https://aclanthology.org/P19-1001/"))
            self.assertEqual(papers.paper_keys("https://www.aclweb.org/anthology/p19-1001"),
                             papers.paper_keys("https://aclanthology.org/P19-1001/"))
            for address in [IEEE, CVF, FORUM, LANDING]:
                key = next(iter(papers.paper_keys(address)))
                self.assertEqual(papers.paper_keys(key), {key})

    def test_unrelated_candidate_does_not_merge_or_lose_the_open_version(self):
        def unrelated(url, limit):
            if url.startswith(paper_sources.CROSSREF + "?"):
                wrong = dict(META, author=[{"given": "Different", "family": "Writer"}])
                return json.dumps({"message": {"items": [wrong]}})
            return metadata(url, limit)
        with tempfile.TemporaryDirectory() as work, patch.object(papers, "_read_html", side_effect=unrelated):
            paper = papers.resolve_paper(CVF, work)
            self.assertEqual(paper["url"], CVF)
            self.assertEqual(paper["aliases"], [CVF])
            self.assertEqual(paper["pdf_url"], CVPDF)

    def test_ieee_id_and_doi_require_publisher_identifier_to_agree(self):
        def ieee(url, limit):
            if url == IEEE:
                return '<meta name="citation_title" content="Title"><meta name="citation_doi" content="' + DOI + '">'
            return metadata(url, limit)
        with tempfile.TemporaryDirectory() as work, patch.object(papers, "_read_html", side_effect=ieee):
            paper = papers.resolve_paper(IEEE, work)
            self.assertEqual(paper["url"], LANDING)
            self.assertIn(IEEE, paper["aliases"])
        with tempfile.TemporaryDirectory() as work, patch.object(papers, "_read_html", return_value="<html>Verify</html>"):
            paper = papers.resolve_paper(IEEE, work)
            self.assertEqual((paper["key"], paper["url"],paper["pdf_url"]), ("url:" + IEEE, IEEE, ""))
            self.assertTrue(paper["source_note"])

    def test_review_uses_its_own_note_and_attached_pdf(self):
        attachment = "https://openreview.net/pdf/" + "a" * 40 + ".pdf"
        note = {"id": "YicbFdNTTy", "forum": "YicbFdNTTy", "content": {
            "title": {"value": "Vision Transformer"}, "authors": {"value": ["Alexey Dosovitskiy"]},
            "pdf": {"value": attachment}, "venue": {"value": "ICLR 2021 Oral"},
            "abstract": {"value": "Authors' summary."}}}
        with tempfile.TemporaryDirectory() as work, patch.object(papers, "_read_html",
                                                                return_value=json.dumps({"notes": [note]})):
            p = papers.resolve_paper(FORUM.replace("/forum", "/pdf"), work)
            self.assertEqual((p["title"], p["year"], p["pdf_url"]), ("Vision Transformer", "2021", attachment))
            self.assertIn(attachment, p["evidence_urls"])
            self.assertTrue(papers._pdf_path(p, work))
            note["forum"] = "Different"
            with patch.object(papers, "_read_html", return_value=json.dumps({"notes": [note]})):
                self.assertFalse(papers.resolve_paper(FORUM, work)["pdf_url"])

    def test_limits_and_metadata_only_sources_are_explicit(self):
        error = HTTPError(FORUM, 429, "Too Many Requests", {}, None)
        with tempfile.TemporaryDirectory() as work, patch.object(papers, "_read_html", side_effect=error):
            p = papers.resolve_paper(FORUM, work)
            self.assertIn("限流", p["source_note"])
            self.assertFalse(p["pdf_url"])
        meta = dict(META, **{"container-title": ["Other proceedings"], "resource": {},
                              "link": [{"URL": "https://publisher.invalid/subscriber-only.pdf"}]})
        with tempfile.TemporaryDirectory() as work, patch.object(papers, "_read_html",
                return_value=json.dumps({"message": meta})):
            p = papers.resolve_paper(LANDING, work, find_pdf=True)
            self.assertEqual(p["title"], TITLE)
            self.assertFalse(p["pdf_url"])

    def test_input_title_candidates_are_not_automatically_merged(self):
        variants = [dict(META, DOI=f"10.5555/example.{n}") for n in range(5)]
        with patch.object(papers, "_read_html", return_value=json.dumps({"message": {"items": variants}})):
            candidates = papers.search_titles(TITLE)
        self.assertEqual(len(candidates), 3)
        self.assertEqual(candidates[0], dict(title=TITLE, authors=["Kaiming He", "Xiangyu Zhang"], year="2016",
                                             url="https://doi.org/10.5555/example.0"))

    def test_explicit_acm_springer_doi_uses_registry_without_fetching_publisher(self):
        for source, doi in [
            ("https://dl.acm.org/doi/pdf/10.1145/362384.362685", "10.1145/362384.362685"),
            ("https://link.springer.com/article/10.1007/BF00152086", "10.1007/bf00152086"),
        ]:
            meta = dict(META, DOI=doi, resource={}, **{"container-title": ["A Journal"]})
            with patch.object(papers, "_read_html", return_value=json.dumps({"message": meta})) as fetch:
                p = papers.resolve_paper(source, ".")
                self.assertEqual(p["key"], "doi:" + doi)
                self.assertEqual(papers.paper_keys(source), {p["key"]})
                fetch.assert_called_once_with(paper_sources.CROSSREF + "/" + doi, 2 * 1024 * 1024)
                self.assertIn(source, p["aliases"])
                self.assertFalse(p["pdf_url"])

    def test_actual_arxiv_version_is_pinned_to_the_source_pdf(self):
        page = ('<meta name="citation_title" content="ViT"><blockquote>Summary.</blockquote>'
                '<a href="https://arxiv.org/abs/2010.11929v2">arXiv:2010.11929v2</a>')
        with tempfile.TemporaryDirectory() as work, patch.object(papers, "_read_html", return_value=page):
            p = papers.resolve_paper("10.48550/arXiv.2010.11929", work)
            self.assertEqual(p["pdf_url"], "https://arxiv.org/pdf/2010.11929v2")
            v1 = papers.resolve_paper("2010.11929v1", work)
            self.assertEqual(v1["pdf_url"], "https://arxiv.org/pdf/2010.11929v1")
            self.assertNotIn(p["pdf_url"], v1["evidence_urls"])

    def test_public_download_rejects_wrong_content_and_cross_site_redirect(self):
        with tempfile.TemporaryDirectory() as work:
            html = io.BytesIO(b"<html>Authentication needed</html>")
            html.headers = {}
            with patch.object(papers, "_open", return_value=html):
                with self.assertRaisesRegex(ValueError, "PDF"):
                    papers.download_pdf({"pdf_url": CVPDF}, work)
            self.assertIsNone(papers.cached_pdf({"pdf_url": CVPDF}, work))
            pdf = io.BytesIO(b"%PDF-1.7\nmock body\n%%EOF")
            pdf.headers = {}
            with patch.object(papers, "_open", return_value=pdf):
                self.assertTrue(papers.download_pdf({"pdf_url": CVPDF}, work).is_file())
            for address in ["https://example.org/file.pdf", "https://api.crossref.org/pdf/2005.11401",
                            "https://user@openreview.net/pdf?id=YicbFdNTTy"]:
                self.assertIsNone(papers._pdf_path({"pdf_url": address}, work))
            with self.assertRaises(ValueError):
                papers._ArxivRedirect().redirect_request(Request(CVPDF), None, 302, "", {}, FORUM)
            with self.assertRaisesRegex(ValueError, "版本"):
                papers._ArxivRedirect().redirect_request(Request("https://arxiv.org/pdf/2010.11929v1"),
                                                         None, 302, "", {}, "https://arxiv.org/pdf/2010.11929v2")


if __name__ == "__main__":
    unittest.main()
