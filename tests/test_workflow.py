import http.client
import json
import os
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest.mock import patch

import papers
from app import Application, Handler
from pdf_sections import excerpts_from_text
from table_store import LocalTable
from workflow import Conversation


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.table = LocalTable(":memory:")
        self.addCleanup(self.table.close)
        self.chat = Conversation(self.table, self.tmp.name, "阅读清单")

    def save(self):
        return self.chat.respond("把 https://arxiv.org/abs/2005.11401 存进阅读清单")

    def test_follow_up_disambiguation_and_duplicate(self):
        first = self.save()["row"]
        q = self.chat.respond("把刚才那篇的方法写进笔记")
        self.assertEqual(q["choices"], ["阅读笔记", "研究笔记"])
        self.assertNotIn("研究笔记", self.table.get(first["id"])["fields"])
        r = self.chat.respond("研究笔记", q["pending_id"])
        self.assertTrue(r["evidence"][0]["url"].startswith("https://arxiv.org/"))
        self.assertEqual(r["row"]["id"], first["id"])
        self.assertEqual(self.save()["row"]["id"], first["id"])
        self.assertEqual(len(self.table.rows()), 1)

    def test_missing_paper_and_cancel(self):
        task = self.chat.respond("把方法补进笔记")
        self.assertIn("哪篇论文", task["message"])
        task = self.chat.respond("arXiv:2005.11401", task["pending_id"])
        self.assertIn("研究笔记", task["choices"])
        self.chat.respond("取消", task["pending_id"])
        self.assertEqual(self.table.rows(), [])

    def test_unclear_intent_confirms_paper_and_content_before_write(self):
        before = self.save()["row"]
        request = self.chat.respond("把刚才那个也记进去")
        self.assertIn("Retrieval-Augmented Generation", request["message"])
        self.assertIn("填写方法", request["choices"])
        self.assertEqual(self.table.get(before["id"]), before)
        result = self.chat.respond("填写方法", request["pending_id"])
        self.assertEqual(result["row"]["id"], before["id"])
        self.assertIn("方法概述", result["row"]["fields"])

    def test_schema_requires_confirmation_and_real_fields(self):
        q = self.chat.respond("新建文本列 审阅意见")
        self.assertNotIn("审阅意见", [f["name"] for f in self.table.schema()])
        self.chat.respond("确认建列", q["pending_id"])
        self.save()
        q = self.chat.respond("把「推荐」写进审阅意见")
        result = self.chat.respond("刚才那篇", q["pending_id"])
        self.assertEqual(result["row"]["fields"]["审阅意见"], "推荐")

    def test_explicit_negative_or_other_table_makes_no_changes(self):
        self.chat.respond("不要把 arXiv:2005.11401 存进阅读清单")
        with patch.dict(os.environ, {"PAPER_MODEL_URL": "", "PAPER_MODEL_NAME": ""}):
            with self.assertRaisesRegex(ValueError, "当前连接"):
                self.chat.respond("把 arXiv:2005.11401 存进候选项目表")
        self.assertEqual(self.table.rows(), [])

    def test_second_paper_can_use_live_source_text_after_consent(self):
        doc = papers._record(key="arxiv:1706.03762", title="Second paper", url="https://arxiv.org/abs/1706.03762",
                             method_original="A verified English method passage.", method_quote="method passage",
                             method_source="方法章节", method_url="https://arxiv.org/html/1706.03762")
        with patch("papers.resolve_paper", return_value=doc), patch.dict(os.environ, {"PAPER_MODEL_URL":"", "PAPER_MODEL_NAME":""}):
            q = self.chat.respond("把 arXiv:1706.03762 的方法写进方法概述")
            self.assertEqual(self.table.rows(), [])
            answer = self.chat.respond("使用英文摘录", q["pending_id"])
            self.assertEqual(answer["row"]["fields"]["方法概述"], doc["method_original"])

    def test_pdf_sections_windows_line_endings(self):
        text = ("Abstract\r\n" + "Original abstract evidence. "*6 + "\r\n1 Introduction\r\nIntro.\r\n\f\n"
                + "2 Methods\r\n" + "Original methods evidence. "*6 + "\r\n3 Results\r\n")
        found = excerpts_from_text(text)
        self.assertEqual(found["method"]["page"], 2)
        self.assertIn("Original abstract", found["abstract"]["text"])

    def test_acl_metadata_can_be_read_from_html(self):
        html = '<meta name="citation_title" content="Paper"><div class="acl-abstract"><h5>Abstract</h5><span>A real abstract.</span></div>'
        with patch("papers._read_html", return_value=html):
            answer = papers.resolve_paper("https://aclanthology.org/2020.acl-main.703.pdf", self.tmp.name)
        self.assertEqual(answer["abstract_original"], "A real abstract.")
        self.assertTrue(answer["url"].endswith("703/"))

    def test_http_session_and_local_origin(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.app = Application("local", self.tmp.name)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
            conn.request("GET", "/api/state")
            response = conn.getresponse()
            cookie = response.getheader("Set-Cookie").split(";")[0]
            self.assertEqual(json.loads(response.read())["rows"], [])
            headers = {"Content-Type": "application/json", "Cookie": cookie}
            for msg in ("把 arXiv:2005.11401 存进阅读清单", "给刚才那篇补一句方法概述"):
                conn.request("POST", "/api/chat", json.dumps({"message": msg}), headers)
                result = json.loads(conn.getresponse().read())
                self.assertIn("verification", result)
            conn.request("POST", "/api/chat", "{}", {**headers, "Origin": "https://other.invalid"})
            res = conn.getresponse()
            self.assertEqual(res.status, 403)
            res.read()
            conn.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(2)
            server.app.table.close()
