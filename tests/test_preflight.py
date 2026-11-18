import tempfile
import unittest
from unittest.mock import patch

import papers
from table_store import LocalTable
from workflow import Conversation


class BeforeWriteTests(unittest.TestCase):
    def setUp(self):
        self.db = LocalTable(":memory:")
        self.addCleanup(self.db.close)
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.chat = Conversation(self.db, temp.name, "阅读清单")
        self.doc = papers._record(key="arxiv:1706.03762", url="https://arxiv.org/abs/1706.03762",
                                  method_original="In this experiment, accuracy decreased by ten points.",
                                  method_quote="accuracy decreased by ten points", method="实验中准确率提高了十个百分点。",
                                  method_source="Methods", method_url="https://arxiv.org/html/1706.03762#S3")

    def test_semantic_failure_never_calls_writer(self):
        verdict = {"方法概述": {"supported": False, "quote": self.doc["method_quote"]}}
        with (patch("papers.resolve_paper", return_value=self.doc),
              patch("model_support.enabled", return_value=True),
              patch("model_support.request_json", return_value=verdict),
              patch.object(self.db, "upsert", wraps=self.db.upsert) as writer):
            with self.assertRaisesRegex(ValueError, "尚|提交前"):
                self.chat.respond("把 arXiv:1706.03762 的方法写入方法概述")
            writer.assert_not_called()
            self.assertEqual(self.db.rows(), [])

    def test_mismatched_paper_source_stops_entire_multi_field_write(self):
        self.doc.update(title="Paper title", method_url="https://arxiv.org/html/2005.11401")
        with patch("papers.resolve_paper", return_value=self.doc), self.assertRaisesRegex(ValueError, "证据"):
            self.chat.respond("把 arXiv:1706.03762 的标题和英文方法存进阅读清单")
        self.assertEqual(self.db.rows(), [])

    def test_approved_rewording_has_two_distinct_receipts(self):
        self.doc["method"] = "实验中准确率下降了十个百分点。"
        verdict = {"方法概述": {"supported": True, "quote": self.doc["method_quote"]}}
        with (patch("papers.resolve_paper", return_value=self.doc),
              patch("model_support.enabled", return_value=True),
              patch("model_support.request_json", return_value=verdict)):
            result = self.chat.respond("把 arXiv:1706.03762 的方法写入方法概述")
        self.assertEqual(result["preflight"], ["方法概述已按原文复核"])
        self.assertIn("回读一致", result["verification"])
