"""官网题录、官方 PDF 地址与本机实际解析的字节必须同时吻合。"""

import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

import evidence_extract
import paper_attachments
import papers


ACL = "https://aclanthology.org/2020.acl-main.703/"
CVF = "https://openaccess.thecvf.com/content_cvpr_2017/html/Test_Conference_CVPR_2017_paper.html"
PDF_URLS = {ACL: ACL.rstrip("/") + ".pdf", CVF: CVF.replace("/html/", "/papers/").replace(".html", ".pdf")}
PDF_BYTES = b"%PDF-1.4\ncontrolled parser fixture\n%%EOF"


class ProceedingsCacheTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        for context in (paper_attachments.session_scope(self.root, "reader"),
                        patch("evidence_extract.shutil.which", return_value="pdftotext"),
                        patch("evidence_extract._pdf_tables", side_effect=lambda *args: []),
                        patch("evidence_extract.subprocess.run", side_effect=self.parse_pdf)):
            context.__enter__()
            self.addCleanup(context.__exit__, None, None, None)

    def parse_pdf(self, args, **options):
        self.assertEqual(Path(args[-2]).read_bytes(), PDF_BYTES)
        text = b"Study\fFigure 2: Accuracy of our model.\nOur code: https://github.com/study-lab/main-model"
        return CompletedProcess(args, 0, stdout=text)

    def online(self, entry):
        page = ('<meta name="citation_title" content="Conference Paper">'
                '<meta name="citation_publication_date" content="2020">'
                '<meta name="citation_conference_title" content="Conference">'
                f'<a href="{PDF_URLS[entry]}">PDF</a>')
        response = io.BytesIO(PDF_BYTES)
        response.headers = {}
        with patch("papers._read_html", return_value=page), patch("papers._open", return_value=response) as download:
            paper = evidence_extract.extract_evidence(entry)
        self.assertEqual(download.call_args.args[0], PDF_URLS[entry])
        return paper

    def offline(self, entry, **options):
        with patch("papers._read_html", side_effect=OSError("offline")), patch("papers._open", side_effect=AssertionError):
            return evidence_extract.extract_evidence(entry, **options)

    def test_official_acl_and_cvf_are_reparsed_with_page_numbers_and_newly_requested_links(self):
        for entry in (ACL, CVF):
            with self.subTest(entry=entry):
                live = self.online(entry)
                self.assertNotIn("此前保存", live["source_version"])
                self.assertEqual([item["kind"] for item in live["items"]], ["figure"])
                restored = self.offline(entry, include_resources=True)
                self.assertEqual((restored["title"], restored["url"]), (live["title"], live["url"]))
                self.assertIn("此前保存", restored["source_version"])
                self.assertEqual({item["kind"] for item in restored["items"]}, {"code", "figure"})
                for item in restored["items"]:
                    self.assertEqual(item["source_url"], PDF_URLS[entry] + "#page=2")
                    self.assertEqual(item["pdf_page"], 2)
                    self.assertEqual(item["pdf_sha256"], hashlib.sha256(PDF_BYTES).hexdigest())

    def test_unregistered_pdf_and_another_workspace_cannot_replace_official_metadata(self):
        file = papers._pdf_path({"pdf_url": PDF_URLS[ACL]}, self.root)
        file.parent.mkdir()
        file.write_bytes(PDF_BYTES)
        with self.assertRaises(ValueError):
            self.offline(ACL)
        file.unlink()
        self.online(ACL)
        for entry in (ACL.replace("703", "704"), ACL + "?version=other"):
            with self.subTest(entry=entry), self.assertRaises(ValueError):
                self.offline(entry)
        with paper_attachments.session_scope(self.root / "other", "reader"), self.assertRaises(ValueError):
            self.offline(ACL)

    def test_hash_mismatch_and_malformed_or_mismatched_manifest_are_rejected(self):
        self.online(CVF)
        manifest, = (self.root / "cache").glob("proceedings-*.json")
        record = json.loads(manifest.read_bytes())
        for payload in (b"{", b"[]", json.dumps({**record, "entry": ACL}).encode(),
                        json.dumps({**record, "pdf_url": PDF_URLS[ACL]}).encode(),
                        json.dumps({**record, "title": None}).encode()):
            with self.subTest(payload=payload[:15]):
                manifest.write_bytes(payload)
                with self.assertRaises(ValueError):
                    self.offline(CVF)
        manifest.write_text(json.dumps(record), encoding="utf-8")
        path = papers.cached_pdf({"pdf_url": PDF_URLS[CVF]}, self.root)
        path.write_bytes(PDF_BYTES.replace(b"fixture", b"changed"))
        with self.assertRaises(ValueError):
            self.offline(CVF)

    def test_manifest_write_failure_leaves_successful_online_extraction_usable(self):
        with patch("proceedings_cache.json.dump", side_effect=OSError("read-only")):
            result = self.online(ACL)
        self.assertEqual(result["items"][0]["kind"], "figure")
        self.assertEqual(list((self.root / "cache").glob("*.json")), [])
        with self.assertRaises(ValueError):
            self.offline(ACL)
