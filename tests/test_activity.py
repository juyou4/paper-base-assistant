"""通过本地 HTTP 和可暂停的模拟推理，验证请求执行期间的真实进度。"""

import http.client
import json
import os
import tempfile
import threading
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from http.server import ThreadingHTTPServer
from unittest.mock import patch

from app import Application, Handler


class ActivityHTTPTests(unittest.TestCase):
    query = "看下这张表目前都有哪些列"

    def setUp(self):
        for mocker in (
            patch.dict(os.environ, {"PAPER_AGENT_PROVIDER": "codex", "PAPER_MODEL_NAME": "model-a"}),
            patch("codex_provider.available_models", return_value=["model-a"]),
        ):
            mocker.start()
            self.addCleanup(mocker.stop)
        provider = patch("codex_provider.request")
        self.provider = provider.start()
        self.addCleanup(provider.stop)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        app = Application("local", directory.name)
        self.addCleanup(app.table.close)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.app = app
        self.addCleanup(self.server.server_close)
        worker = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
        worker.start()
        self.addCleanup(worker.join, 2)
        self.addCleanup(self.server.shutdown)

    def request(self, method, path, cookie=None, data=None, expected=200):
        headers = {"Origin": f"http://127.0.0.1:{self.server.server_port}"}
        if cookie:
            headers["Cookie"] = cookie
        body = None
        if data is not None:
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        # 模型阻塞期间 GET 须自行返回；每个请求使用独立的 TCP 连接。
        timeout = 3 if method == "GET" else 12
        with closing(http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=timeout)) as conn:
            conn.request(method, path, body, headers)
            response = conn.getresponse()
            session = response.getheader("Set-Cookie", "").partition(";")[0]
            payload = json.loads(response.read())
            accepted = expected if isinstance(expected, tuple) else (expected,)
            self.assertIn(response.status, accepted, payload)
        return payload, session

    def chat(self, cookie, request_id, message, expected=200):
        return self.request("POST", "/api/chat", cookie,
                            {"message": message, "request_id": request_id}, expected)[0]

    def progress(self, cookie, request_id):
        snapshot = self.request("GET", "/api/activity?request_id=" + request_id, cookie)[0]
        self.assertEqual(snapshot["request_id"], request_id)
        self.assertIsInstance(snapshot["events"], list)
        self.assertIsInstance(snapshot["finished"], bool)
        ids = set()
        for event in snapshot["events"]:
            self.assertIsNotNone(event["id"])
            self.assertNotIn(event["id"], ids)
            ids.add(event["id"])
            self.assertIsInstance(event["label"], str)
            self.assertTrue(event["label"].strip())
            self.assertIn(event["status"], {"running", "done", "failed"})
            self.assertIsInstance(event["elapsed_ms"], (int, float))
            self.assertGreaterEqual(event["elapsed_ms"], 0)
        return snapshot

    def assert_empty(self, snapshot):
        self.assertFalse(snapshot["finished"])
        self.assertEqual(snapshot["events"], [])

    def test_running_is_pollable_and_scoped_to_cookie_even_for_the_same_uuid(self):
        _, owner = self.request("GET", "/api/state")
        _, other = self.request("GET", "/api/state")
        self.assertTrue(owner and other and owner != other)
        request_id = str(uuid.uuid4())
        self.assert_empty(self.progress(owner, request_id))
        entered, release = threading.Event(), threading.Event()

        def model(_instructions, payload, model=None):
            if payload["message"] == self.query:
                entered.set()
                if not release.wait(9):
                    raise ValueError("模拟模型等待超时")
                return {"op": "columns"}
            return {"op": "column", "target": "论文链接"}

        self.provider.side_effect = model
        with ThreadPoolExecutor(max_workers=1) as pool:
            chat = pool.submit(self.chat, owner, request_id, self.query)
            try:
                self.assertTrue(entered.wait(5), "聊天未进入模拟推理")
                live = self.progress(owner, request_id)
                self.assertFalse(live["finished"])
                active = {e["id"]: e for e in live["events"] if e["status"] == "running"}
                self.assertTrue(active)
                self.assertTrue(any("模型" in e["label"] for e in active.values()))
                self.assertFalse(chat.done())
                duplicate = self.chat(owner, request_id, self.query, expected=(400, 409))
                self.assertTrue(duplicate["error"])
                self.assertFalse(self.progress(owner, request_id)["finished"])
                self.assertEqual(self.provider.call_count, 1)
                self.assert_empty(self.progress(other, request_id))
                second = self.chat(other, request_id, "查看「论文链接」这栏的类型")
                other_final = self.progress(other, request_id)
                self.assertEqual(second["activity"], other_final)
                self.assertTrue(other_final["finished"])
                self.assertFalse(self.progress(owner, request_id)["finished"])
            finally:
                release.set()
            completed = chat.result(timeout=5)
        final = self.progress(owner, request_id)
        self.assertTrue(final["finished"])
        self.assertEqual(completed["activity"], final)
        done = {e["id"]: e for e in final["events"]}
        self.assertTrue(all(e["status"] == "done" for e in done.values()))
        for event_id, event in active.items():
            self.assertEqual(done[event_id]["label"], event["label"])
            self.assertGreaterEqual(done[event_id]["elapsed_ms"], event["elapsed_ms"])
        self.assertEqual(other_final, self.progress(other, request_id))

    def test_failed_attempt_is_retained_and_retry_gets_fresh_status(self):
        _, cookie = self.request("GET", "/api/state")
        failed_id, independent_id = str(uuid.uuid4()), str(uuid.uuid4())
        self.provider.side_effect = [ValueError("模型暂时不可用"), {"op": "columns"}, {"op": "columns"}]
        error = self.chat(cookie, failed_id, "把阅读笔记这一列删掉", expected=400)
        first = self.progress(cookie, failed_id)
        self.assertTrue(error["error"])
        self.assertIn("识别字段管理操作（失败）", error["agent"]["steps"])
        self.assertEqual(error["agent"]["model"], "model-a")
        self.assertIsNone(self.request("GET", "/api/state", cookie)[0]["pending_dialog"])
        self.assertEqual(error["activity"], first)
        self.assertTrue(first["finished"])
        self.assertIn("failed", [e["status"] for e in first["events"]])
        self.assertTrue(any("模型" in e["label"] and e["status"] == "failed" for e in first["events"]))
        self.assertNotIn("running", [e["status"] for e in first["events"]])
        self.assert_empty(self.progress(cookie, independent_id))
        independent = self.chat(cookie, independent_id, self.query)["activity"]
        self.assertEqual(independent, self.progress(cookie, independent_id))
        self.assertEqual(first, self.progress(cookie, failed_id))
        retry = self.chat(cookie, failed_id, self.query)["activity"]
        self.assertTrue(retry["finished"])
        self.assertTrue(retry["events"])
        self.assertEqual({e["status"] for e in retry["events"]}, {"done"})
        self.assertEqual(retry, self.progress(cookie, failed_id))
        self.assertEqual(independent, self.progress(cookie, independent_id))
        self.assertEqual(self.provider.call_count, 3)

    def test_read_only_fallback_reports_the_actual_failed_attempt(self):
        _, cookie = self.request("GET", "/api/state")
        self.provider.side_effect = ValueError("模型暂时不可用")
        result = self.chat(cookie, str(uuid.uuid4()), self.query)
        self.assertIn("论文标题", result["message"])
        self.assertEqual(result["agent"]["steps"], ["识别字段管理操作（失败）"])
        self.assertIn("failed", [event["status"] for event in result["activity"]["events"]])
        self.assertNotIn("pending_id", result)

    def test_invalid_poll_ids_and_unexpected_error_are_isolated(self):
        _, cookie = self.request("GET", "/api/state")
        key = str(uuid.uuid4())
        for query in ("", "?request_id=not-a-uuid", f"?request_id={key}&request_id={key}"):
            with self.subTest(query=query):
                error, _ = self.request("GET", "/api/activity" + query, cookie, expected=400)
                self.assertTrue(error["error"])
        self.provider.assert_not_called()
        self.assert_empty(self.progress(cookie, key))
        self.provider.side_effect = RuntimeError("模拟下游不应公开的诊断")
        error = self.chat(cookie, key, self.query, expected=500)
        self.assertNotIn("模拟下游不应公开的诊断", json.dumps(error, ensure_ascii=False))
        retained = self.progress(cookie, key)
        self.assertEqual(error["activity"], retained)
        self.assertTrue(retained["finished"])
        self.assertIn("failed", [event["status"] for event in retained["events"]])


if __name__ == "__main__":
    unittest.main()
