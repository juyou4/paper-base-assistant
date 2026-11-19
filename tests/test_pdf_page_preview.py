"""通过工作台的预览和原页接口核对来源、页码、会话与确认生命周期。"""

import hashlib
import http.client
import importlib.util
import io
import json
import os
import tempfile
import threading
import unittest
from contextlib import closing
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import papers
import pdf_page_preview
from app import Application, Handler


@unittest.skipUnless(all(importlib.util.find_spec(name) for name in ("pdfplumber", "reportlab", "pypdfium2", "PIL")),
                     "PDF 解析和页图测试需要 pdfplumber、reportlab、pypdfium2 和 Pillow")
class PdfPageTests(unittest.TestCase):
    doi = "https://doi.org/10.1234/demo.001"
    acl = "https://aclanthology.org/P19-1001/"

    def setUp(self):
        from reportlab.pdfgen.canvas import Canvas

        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.directory = Path(folder.name)
        buf = io.BytesIO()
        doc = Canvas(buf, pagesize=(400, 400))
        for n, rgb in enumerate(((1, 0, 0), (0, 1, 0)), 1):
            doc.setFillColorRGB(*rgb)
            doc.rect(0, 0, 400, 400, fill=1)
            doc.setFillColorRGB(0, 0, 0)
            doc.drawString(30, 340, "Test paper" if n == 1 else "Figure 2: Retrieval results.")
            doc.showPage()
        doc.save()
        self.data = buf.getvalue()
        self.app = Application("local", self.directory)
        self.addCleanup(self.app.close)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.app = self.app
        self.addCleanup(self.server.server_close)
        thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 2)
        self.addCleanup(self.server.shutdown)
        env = patch.dict(os.environ, PAPER_AGENT_PROVIDER="rules")
        env.start()
        self.addCleanup(env.stop)

    def request(self, url, body=None, cookie=""):
        binary = isinstance(body, bytes)
        payload = body if binary or body is None else json.dumps(body).encode()
        headers = {"Content-Type": "application/pdf" if binary else "application/json", "Cookie": cookie}
        with closing(http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=30)) as conn:
            conn.request("GET" if body is None else "POST", url, payload, headers)
            reply = conn.getresponse()
            data = reply.read()
            if "json" in reply.getheader("Content-Type"):
                data = json.loads(data)
            return reply.status, data, reply.getheader("Set-Cookie", "").split(";")[0]

    def preview(self, url, cookie):
        status, reply, _ = self.request("/api/chat", {"message": "提取这篇的图表 " + url}, cookie)
        self.assertEqual(status, 200, reply)
        preview = reply["evidence_preview"]
        paper = preview["papers"][0]
        self.assertEqual(paper["status"], "ready", paper)
        index, figure = next((i, c) for i, c in enumerate(paper["items"]) if c["kind"] == "figure")
        self.assertEqual(figure["pdf_page"], 2)
        self.assertEqual(figure["pdf_sha256"], hashlib.sha256(self.data).hexdigest())
        return f"/api/evidence-page/{preview['id']}/0/{index}.png", preview["id"], figure

    def assert_second_page(self, route, cookie):
        from PIL import Image
        status, data, _ = self.request(route, cookie=cookie)
        self.assertEqual(status, 200, data if isinstance(data, dict) else "PNG")
        with Image.open(io.BytesIO(data)) as img:
            self.assertEqual(img.format, "PNG")
            self.assertLessEqual(max(img.size), pdf_page_preview.LONG_EDGE)
            # 第一页整页为红色，第二页才是绿色；校验像素以防第一页/零基下标误差。
            self.assertEqual(img.convert("RGB").getpixel((10, 10)), (0, 255, 0))

    def test_upload_second_page_session_and_batch_scope(self):
        _, draft, cookie = self.request("/api/attachments", self.data)
        self.request(draft["viewer"] + "/bind", {"paper_ref": self.doi, "title": "测试论文"}, cookie)
        route, batch_id, figure = self.preview(self.doi, cookie)
        self.assertEqual(figure["source_url"], draft["viewer"] + "#page=2")
        self.assert_second_page(route, cookie)
        self.assertEqual(self.request(route)[0], 404)
        self.assertEqual(self.request(route.rsplit("/", 1)[0] + "/9999.png", cookie=cookie)[0], 404)
        self.assertEqual(self.app.table.rows(), [])
        self.request("/api/chat", {"message": "取消", "pending_id": batch_id}, cookie)
        self.assertEqual(self.request(route, cookie=cookie)[0], 404)

    def test_public_pdf_cache_only_and_changed_bytes_fail_closed(self):
        _, _, cookie = self.request("/api/state")
        source = self.acl.rstrip("/") + ".pdf"
        cache = papers._pdf_path({"pdf_url": source}, self.directory)
        cache.parent.mkdir()
        cache.write_bytes(self.data)
        with patch("papers._read_html", return_value='<meta name="citation_title" content="公开会议论文">'), \
                patch("papers._open", side_effect=AssertionError("已缓存全文，不应重新下载")):
            route, _, figure = self.preview(self.acl, cookie)
            self.assertEqual(figure["source_url"], source + "#page=2")
            self.assert_second_page(route, cookie)
            cache.write_bytes(self.data.replace(b"%PDF-1.", b"%PDF-2.", 1))
            status, error, _ = self.request(route, cookie=cookie)
            self.assertEqual(status, 422)
            self.assertIn("变化", error["error"])

    def test_unlocated_or_out_of_scope_pages_have_no_preview(self):
        path = self.directory / "fixture.pdf"
        path.write_bytes(self.data)
        items = [{"source_url": "https://example.org/wrong#page=2"}, {"source_url": self.acl + "#page=33"},
                 {"source_url": self.acl + "#page=0"}, {"source_url": self.acl + "#figure1"}]
        self.assertFalse(any("pdf_page" in i for i in pdf_page_preview.annotate(items, self.acl, path)))

    def test_oversized_page_renders_at_bounded_dimensions_and_detects_replacement(self):
        from PIL import Image
        from reportlab.pdfgen.canvas import Canvas
        path = self.directory / "poster.pdf"
        doc = Canvas(str(path), pagesize=(8000, 4000))
        doc.drawString(100, 100, "Poster")
        doc.save()
        before = path.stat()
        item = {"source_url": self.acl + "#page=1"}
        pdf_page_preview.annotate([item], self.acl, path, initial_stat=before)
        png = pdf_page_preview.render(path, 1, item["pdf_sha256"])
        with Image.open(io.BytesIO(png)) as img:
            self.assertEqual(img.size, (1600, 800))
        path.write_bytes(self.data)
        with self.assertRaisesRegex(ValueError, "已变化"):
            pdf_page_preview.annotate([item], self.acl, path, initial_stat=before)

    def test_missing_renderer_keeps_original_source_and_draft(self):
        _, draft, cookie = self.request("/api/attachments", self.data)
        self.request(draft["viewer"] + "/bind", {"paper_ref": self.doi, "title": "测试论文"}, cookie)
        route, batch_id, figure = self.preview(self.doi, cookie)
        with patch("pdf_page_preview.subprocess.run") as worker:
            worker.return_value.returncode = 2
            status, error, _ = self.request(route, cookie=cookie)
        self.assertEqual(status, 422)
        self.assertIn("打开原文", error["error"])
        status, source, _ = self.request(figure["source_url"].split("#")[0], cookie=cookie)
        self.assertEqual((status, source), (200, self.data))
        self.assertEqual(self.request("/api/state", cookie=cookie)[1]["pending_dialog"]["id"], batch_id)
