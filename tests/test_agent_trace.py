import os
import tempfile
import unittest
from unittest.mock import patch

import model_support
from app import Application


class AgentTraceTests(unittest.TestCase):
    def setUp(self):
        settings = {"PAPER_AGENT_PROVIDER": "codex", "PAPER_MODEL_NAME": "test-model"}
        env = patch.dict(os.environ, settings)
        env.start()
        self.addCleanup(env.stop)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.app = Application("local", folder.name)
        self.addCleanup(self.app.table.close)
        _, self.chat = self.app.session(None)

    def test_only_completed_model_requests_appear_in_the_current_trace(self):
        with patch("codex_provider.request", return_value={"op": "columns"}) as request:
            with model_support.trace_calls() as steps:
                result = self.chat.respond("看下这张表目前都有哪些列")
            self.assertIn("论文标题", result["message"])
            self.assertEqual(steps, ["识别字段管理操作"])
            self.assertEqual(request.call_count, 1)
            with model_support.trace_calls() as next_steps:
                self.chat.respond("取消")
            self.assertEqual(next_steps, [])

    def test_model_refusal_stops_a_known_write_before_commit(self):
        with patch("codex_provider.request", return_value={"decision": "clarify"}):
            with self.assertRaisesRegex(ValueError, "未能确认"):
                self.chat.respond("把 arXiv:2005.11401 存入清单")
        self.assertEqual(self.app.table.rows(), [])
        with patch("codex_provider.request", return_value={"decision": "accept"}):
            with model_support.trace_calls() as steps:
                response = self.chat.respond("把 arXiv:2005.11401 存入清单")
        self.assertIn("写前核验通过", response["verification"])
        self.assertEqual(steps, ["核对论文操作计划"])


if __name__ == "__main__":
    unittest.main()
