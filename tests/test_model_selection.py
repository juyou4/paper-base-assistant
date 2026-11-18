"""通过本地 HTTP 验证模型切换、会话归属和待确认任务，全程使用临时 SQLite。"""

import http.client
import json
import os
import tempfile
import threading
import unittest
from contextlib import closing
from http.server import ThreadingHTTPServer
from unittest.mock import patch

from app import Application, Handler


class ModelSelectionHTTPTests(unittest.TestCase):
    def setUp(self):
        for mocker in (
            patch.dict(os.environ, {"PAPER_AGENT_PROVIDER": "codex", "PAPER_MODEL_NAME": "model-a"}),
            patch("codex_provider.available_models", return_value=["model-a", "model-b"]),
        ):
            mocker.start()
            self.addCleanup(mocker.stop)
        provider = patch("codex_provider.request", return_value={"op": "columns"})
        self.provider = provider.start()
        self.addCleanup(provider.stop)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        app = Application("local", folder.name)
        self.addCleanup(app.table.close)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.app = app
        self.addCleanup(self.server.server_close)
        thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 2)
        self.addCleanup(self.server.shutdown)

    def request(self, method, path, body=None, cookie=None, headers=None, expected=200):
        address = f"http://127.0.0.1:{self.server.server_port}"
        request_headers = {"Origin": address}
        encoded = None
        if body is not None:
            request_headers["Content-Type"] = "application/json"
            encoded = json.dumps(body, ensure_ascii=False).encode("utf-8")
        if cookie:
            request_headers["Cookie"] = cookie
        request_headers.update(headers or {})
        with closing(http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=4)) as connection:
            connection.request(method, path, encoded, request_headers)
            response = connection.getresponse()
            new_cookie = response.getheader("Set-Cookie", "").partition(";")[0]
            answer = json.loads(response.read())
            self.assertEqual(response.status, expected, answer)
        return answer, new_cookie

    def state(self, cookie=None):
        return self.request("GET", "/api/state", cookie=cookie)

    def switch(self, cookie, model):
        response, _ = self.request("POST", "/api/model", {"model": model}, cookie)
        self.assertEqual(response["agent"]["model"], model)
        self.assertIsInstance(response["message"], str)
        self.assertTrue(response["message"].strip())
        return response["agent"]

    def test_selected_model_reaches_provider_and_trace_for_each_browser(self):
        initial, first = self.state()
        self.assertTrue(first)
        info = initial["capabilities"]["agent"]
        self.assertCountEqual(info["models"], ["model-a", "model-b"])
        self.assertIs(info["selectable"], True)
        self.assertEqual(info["model"], "model-a")
        self.switch(first, "model-b")
        independent, second = self.state()
        self.assertTrue(second)
        self.assertNotEqual(first, second)
        self.assertEqual(independent["capabilities"]["agent"]["model"], "model-a")
        self.assertEqual(self.state(first)[0]["capabilities"]["agent"]["model"], "model-b")
        self.provider.assert_not_called()
        command = {"message": "看下这张表目前都有哪些列"}
        for cookie, model in ((first, "model-b"), (second, "model-a"), (first, "model-b")):
            with self.subTest(model=model):
                self.provider.reset_mock()
                result, _ = self.request("POST", "/api/chat", command, cookie)
                self.assertIn("论文链接", result["message"])
                self.assertEqual(result["agent"]["model"], model)
                self.assertIn("识别字段管理操作", result["agent"]["steps"])
                self.provider.assert_called_once()
                args, kwargs = self.provider.call_args
                self.assertEqual(kwargs.get("model") if len(args) < 3 else args[2], model)

    def test_rejected_selection_keeps_previous_model(self):
        _, cookie = self.state()
        self.switch(cookie, "model-b")
        for body, headers, status in (
            ({"model": "model-a"}, {"Origin": "https://outside.invalid"}, 403),
            ({"model": "model-a"}, {"Content-Type": "text/plain"}, 415),
            ({"model": "model-unlisted"}, {}, 400),
            ({"model": 123}, {}, 400),
            ({}, {}, 400),
        ):
            with self.subTest(body=body, headers=headers):
                error, _ = self.request("POST", "/api/model", body, cookie, headers, status)
                self.assertTrue(error["error"])
                self.assertEqual(self.state(cookie)[0]["capabilities"]["agent"]["model"], "model-b")
        self.provider.assert_not_called()

    def test_switch_preserves_pending_creation_without_committing_it(self):
        _, cookie = self.state()
        self.provider.return_value = {"op": "create", "name": "评阅意见", "type": "text"}
        plan, _ = self.request("POST", "/api/chat", {"message": "请给我加一栏，名字叫「评阅意见」，类型选文本"}, cookie)
        self.assertIn("确认建列", plan["choices"])
        self.assertEqual(plan["agent"]["model"], "model-a")
        self.switch(cookie, "model-b")
        state, _ = self.state(cookie)
        self.assertEqual(state["rows"], [])
        self.assertNotIn("评阅意见", [f["name"] for f in state["fields"]])
        _, other = self.state()
        confirm = {"message": "确认建列", "pending_id": plan["pending_id"]}
        self.request("POST", "/api/chat", confirm, other, expected=400)
        self.assertNotIn("评阅意见", [f["name"] for f in self.state(cookie)[0]["fields"]])
        accepted, _ = self.request("POST", "/api/chat", confirm, cookie)
        self.assertTrue(accepted["verification"])
        final, _ = self.state(cookie)
        self.assertEqual([f["type"] for f in final["fields"] if f["name"] == "评阅意见"], ["text"])
        self.assertEqual(final["rows"], [])
        self.assertEqual(final["capabilities"]["agent"]["model"], "model-b")
        self.provider.assert_called_once()


if __name__ == "__main__":
    unittest.main()
