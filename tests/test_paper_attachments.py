import hashlib
import http.client
import io
import json
import os
import tempfile
import threading
import unittest
from contextlib import closing
from http.server import ThreadingHTTPServer
from unittest.mock import patch

from app import Application, Handler
import evidence_extract
import paper_attachments

try:
    from reportlab.pdfgen.canvas import Canvas
except ImportError:
    Canvas = None


@unittest.skipUnless(Canvas, "生成真实的两页 PDF 需要 reportlab")
class AttachmentTests(unittest.TestCase):
    doi = "https://doi.org/10.1234/demo.001"

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.directory = folder.name
        buf = io.BytesIO()
        canvas = Canvas(buf, pagesize=(400, 400))
        canvas.setTitle("Local test paper")
        canvas.drawString(20, 330, "Local test paper")
        canvas.drawString(20, 300, "DOI: 10.1234/demo.001")
        canvas.showPage()
        canvas.drawString(20, 330, "Figure 2: Evaluation curves.")
        canvas.drawString(20, 300, "Our model achieved 71.2% accuracy.")
        canvas.save()
        self.content = buf.getvalue()

    def test_unbound_duplicate_version_and_private_page_evidence(self):
        library = paper_attachments.AttachmentLibrary(self.directory, "owner")
        item = library.receive(self.content, "../private/paper.pdf")
        self.assertEqual(item["filename"], "paper.pdf")
        self.assertEqual(item["id"], hashlib.sha256(self.content).hexdigest())
        self.assertEqual(item["candidates"], [self.doi])
        self.assertEqual(library.receive(self.content, "renamed.pdf")["id"], item["id"])
        self.assertEqual(len(library.list()), 1)
        with paper_attachments.session_scope(self.directory, "owner"):
            self.assertIsNone(paper_attachments.bound_file(self.doi))
            library.bind(item["id"], self.doi, "")
            with patch("papers.resolve_paper", side_effect=AssertionError("no network")):
                result = evidence_extract.extract_evidence(self.doi)
            figure = next(i for i in result["items"] if i["kind"] == "figure")
            source = item["viewer"] + "#page=2"
            self.assertEqual(figure["source_url"], source)
            self.assertEqual(figure["public_source_url"], self.doi)
            self.assertTrue(paper_attachments.matches_source(self.doi, source))
            self.assertFalse(paper_attachments.matches_source("https://doi.org/10.1234/b", source))
            self.assertFalse(paper_attachments.matches_source(self.doi, source.replace("#page=2", "#figure")))
            with self.assertRaises(ValueError):
                library.bind(item["id"], "https://doi.org/10.1234/b", "Wrong paper")
        with paper_attachments.session_scope(self.directory, "other"):
            self.assertIsNone(paper_attachments.bound_file(self.doi))
            self.assertFalse(paper_attachments.matches_source(self.doi, source))
        for value in (b"not a PDF", b"%PDF-fake\n%%EOF"):
            with self.assertRaises(ValueError):
                library.receive(value, "fake.pdf")
        with self.assertRaises(ValueError):
            library.path("../private")

    def test_http_upload_recover_bind_preview_and_readback(self):
        with patch.dict(os.environ, {"PAPER_AGENT_PROVIDER": "rules"}):
            app = Application("local", self.directory)
            self.addCleanup(app.table.close)
            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            server.app = app
            self.addCleanup(server.server_close)
            thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
            thread.start()
            self.addCleanup(thread.join, 2)
            self.addCleanup(server.shutdown)

            def request(path, data=None, cookie="", mime="application/json"):
                body = json.dumps(data).encode() if data is not None and mime == "application/json" else data
                with closing(http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=15)) as conn:
                    conn.request("GET" if body is None else "POST", path, body, {"Content-Type": mime, "Cookie": cookie})
                    response = conn.getresponse()
                    payload = response.read()
                    if "json" in response.getheader("Content-Type", ""):
                        payload = json.loads(payload)
                    return response.status, payload, response.getheader("Set-Cookie", "").split(";")[0]

            status, item, cookie = request("/api/attachments", self.content, mime="application/pdf")
            self.assertEqual(status, 200, item)
            self.assertEqual(request(item["viewer"])[0], 404)
            self.assertEqual(request(item["viewer"] + "/bind", {"paper_ref": self.doi, "title": "Fake"})[0], 404)
            app.sessions.clear()  # 服务进程重启后持原 cookie 恢复附件，提取流程另开。
            _, snapshot, resumed = request("/api/state", cookie=cookie)
            self.assertEqual(resumed, cookie)
            self.assertEqual(snapshot["attachments"][0]["id"], item["id"])
            self.assertEqual(request(item["viewer"], cookie=cookie)[1], self.content)
            bound = request(item["viewer"] + "/bind", {"paper_ref": self.doi, "title": "Local test paper"}, cookie)
            self.assertEqual(bound[0], 200, bound)
            _, preview, _ = request("/api/chat", {"message": "提取这篇论文的关键数据和图表 " + self.doi}, cookie)
            entry = preview["evidence_preview"]["papers"][0]
            self.assertEqual(entry["status"], "ready", entry)
            self.assertFalse(app.table.rows())
            _, done, _ = request("/api/chat", {"message": "确认回填", "pending_id": preview["pending_id"]}, cookie)
            self.assertIn("回填", done["message"])
            rows = app.table.rows()
            self.assertEqual(len(rows), 1, done)
            cells = str(rows[0]["fields"])
            self.assertIn("71.2%", cells)
            self.assertIn("第 2 页", cells)
            self.assertIn(item["id"], cells)
            self.assertIn(self.doi, cells)
            self.assertNotIn("/api/attachments", cells)
            selected = {"record_id": rows[0]["id"], "expected_url": self.doi}
            self.assertEqual(request(item["viewer"] + "/bind", selected, cookie)[0], 200)
            stale = {**selected, "expected_url": "https://doi.org/10.1234/stale"}
            self.assertEqual(request(item["viewer"] + "/bind", stale, cookie)[0], 400)

    def test_keeps_unidentified_file_and_rejects_conflicting_bindings(self):
        library = paper_attachments.AttachmentLibrary(self.directory, "owner")
        item = library.receive(self.content, "a.pdf")
        with self.assertRaisesRegex(ValueError, "标识"):
            library.bind(item["id"], "https://doi.org/10.1234/different", "Another paper")
        buf = io.BytesIO()
        pdf = Canvas(buf)
        pdf.drawString(40, 500, "Untitled draft without a DOI.")
        pdf.save()
        draft = library.receive(buf.getvalue(), "shared.pdf")
        self.assertEqual(draft["candidates"], [])
        self.assertEqual(draft["paper_url"], "")
        restored = paper_attachments.AttachmentLibrary(self.directory, "owner")
        self.assertEqual(restored.path(draft["id"]).read_bytes(), buf.getvalue())
        self.assertIsNone(restored.for_paper("http://example.org/article"))
        self.assertIsNone(restored.for_paper("https://localhost/private.pdf"))
        restored.bind(item["id"], self.doi, "First PDF")
        with self.assertRaisesRegex(ValueError, "另一版本"):
            restored.bind(draft["id"], self.doi, "Different PDF")
        with self.assertRaises(ValueError):
            restored.bind(draft["id"], "https://localhost/private.pdf", "File")
        with self.assertRaises(ValueError):
            restored.bind(draft["id"], self.doi + " https://doi.org/10.1234/b", "Ambiguous")

    def test_publisher_urls_must_match_the_doi_printed_on_the_pdf(self):
        library = paper_attachments.AttachmentLibrary(self.directory, "owner")
        for publisher, doi in [("https://dl.acm.org/doi/abs/", "10.1145/1111111.1111112"),
                               ("https://link.springer.com/article/", "10.1007/demo.123")]:
            with self.subTest(publisher=publisher):
                data = io.BytesIO()
                pdf = Canvas(data)
                pdf.setTitle("Source paper")
                pdf.drawString(50, 600, "DOI: " + doi)
                pdf.showPage()
                pdf.save()
                item = library.receive(data.getvalue(), "source.pdf")
                self.assertEqual(item["candidates"], ["https://doi.org/" + doi])
                with self.assertRaisesRegex(ValueError, "标识"):
                    library.bind(item["id"], publisher + doi + "9", "Another paper")
                self.assertIsNone(library.for_paper(publisher + doi + "9"))
                self.assertEqual(library.bind(item["id"], publisher + doi, "Source paper")["paper_url"],
                                 paper_attachments.normalize_ref(publisher + doi))

    def test_unversioned_reference_cannot_hide_a_conflicting_printed_revision(self):
        data = io.BytesIO()
        pdf = Canvas(data)
        pdf.drawString(50, 600, "arXiv:2010.11929v1")
        pdf.drawString(50, 550, "arXiv:2010.11929")
        pdf.showPage()
        pdf.save()
        library = paper_attachments.AttachmentLibrary(self.directory, "owner")
        item = library.receive(data.getvalue(), "vision.pdf")
        with self.assertRaisesRegex(ValueError, "版本"):
            library.bind(item["id"], "https://arxiv.org/pdf/2010.11929v2.pdf", "Vision paper")
        expected = "https://arxiv.org/abs/2010.11929v1"
        self.assertEqual(library.bind(item["id"], expected, "Vision paper")["paper_url"], expected)
