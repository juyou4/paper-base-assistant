import os
import unittest
from unittest.mock import patch

import model_support
from evidence_intent import classify


MESSAGE = "刚才那篇，作者拿什么成绩说服读者？"
PREVIEW = {"intent": "preview", "kinds": ["metric"], "request_phrase": "拿什么成绩说服读者",
           "previous_reference": "刚才那篇"}


class EvidenceIntentTests(unittest.TestCase):
    def test_current_session_model_and_trace_for_both_providers(self):
        for provider, method in (("codex", "codex_provider.request"),
                                 ("compatible", "model_support._compatible_chat")):
            with self.subTest(provider=provider), patch.dict(os.environ, {
                    "PAPER_AGENT_PROVIDER": provider, "PAPER_MODEL_NAME": "default-model",
                    "PAPER_MODEL_URL": "http://localhost:8811/v1", "PAPER_MODEL_KEY": ""}), \
                    patch(method, return_value=PREVIEW) as request, model_support.using_model("session-model"), \
                    model_support.trace_calls() as steps:
                self.assertEqual(classify(MESSAGE, "阅读清单"), {"kinds": ["metric"], "use_previous": True})
                args = request.call_args.args
                self.assertIn("session-model", args)
                self.assertIn({"message": MESSAGE, "table_name": "阅读清单"}, args)
                self.assertEqual(steps, ["识别论文证据预览意图"])

    def test_output_scope_must_be_grounded_and_closed(self):
        invalid = [
            {"intent": "other"}, {"intent": "write"}, None,
            {**PREVIEW, "url": "https://arxiv.org/abs/2005.11401"},
            {**PREVIEW, "confirm": True}, {**PREVIEW, "field": "读取笔记"},
            {**PREVIEW, "request_phrase": ""}, {**PREVIEW, "request_phrase": "帮我比较模型跑分"},
            {**PREVIEW, "previous_reference": "上周那篇"}, {**PREVIEW, "previous_reference": False},
            {**PREVIEW, "kinds": []}, {**PREVIEW, "kinds": ["metric", "metric"]},
            {**PREVIEW, "kinds": ["write"]}, {**PREVIEW, "kinds": [{}]},
        ]
        with patch.dict(os.environ, {"PAPER_AGENT_PROVIDER": "codex"}):
            for result in invalid:
                with self.subTest(result=result), patch("model_support.request_json", return_value=result):
                    self.assertIsNone(classify(MESSAGE, "阅读清单"))

    def test_unrelated_or_absent_reference_does_not_reuse_context(self):
        for previous in ("", "作者"):
            with self.subTest(previous=previous), patch.dict(os.environ, {"PAPER_AGENT_PROVIDER": "codex"}), \
                    patch("model_support.request_json", return_value={
                        **PREVIEW, "previous_reference": previous, "kinds": ["table", "figure", "metric"]}):
                self.assertEqual(classify(MESSAGE, "阅读清单"),
                                 {"kinds": ["metric", "figure", "table"], "use_previous": False})

    def test_rules_mode_and_provider_error_return_to_clarification(self):
        with patch.dict(os.environ, {"PAPER_AGENT_PROVIDER": "rules"}), patch("model_support.request_json") as model:
            self.assertIsNone(classify(MESSAGE, "阅读清单"))
            model.assert_not_called()
        for error in (ValueError("模型不可用"), TimeoutError()):
            with self.subTest(error=error), patch.dict(os.environ, {"PAPER_AGENT_PROVIDER": "codex"}), \
                    patch("codex_provider.request", side_effect=error), model_support.trace_calls() as steps:
                self.assertIsNone(classify(MESSAGE, "阅读清单"))
                self.assertEqual(steps, [])
