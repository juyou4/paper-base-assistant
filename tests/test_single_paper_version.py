"""收藏同篇多个修订版时，普通对话补写也核对所读版本。"""

import os
import tempfile
import unittest
from unittest.mock import patch

import papers
from table_store import LocalTable
from workflow import Conversation


BASE = "https://arxiv.org/abs/2010.11929"


def paper(url, *_, **__):
    version = "v1" if url.endswith("v1") else "v2"
    actual = BASE + version
    value = "The reported pre-training cost is " + ("743" if version == "v1" else "224") + " exaFLOPs."
    return papers._record(key="arxiv:2010.11929", url=url, title="Vision Transformer " + version,
                         aliases=[url, BASE], pdf_url=actual.replace("/abs/", "/pdf/"),
                         source_version="arXiv 2010.11929" + version, authors=["Author " + version],
                         method_original=value, method_quote=value, method_source="Methods",
                         method_url=actual.replace("/abs/", "/html/") + "#S2",
                         abstract_original=value, abstract_quote=value, abstract_source="Abstract",
                         abstract_url=actual)


class SinglePaperVersionTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.table = LocalTable(":memory:")
        self.addCleanup(self.table.close)
        self.chat = Conversation(self.table, folder.name, "阅读清单")
        for mock in [patch("papers.resolve_paper", side_effect=paper),
                     patch.dict(os.environ, {"PAPER_AGENT_PROVIDER": "rules"})]:
            mock.start()
            self.addCleanup(mock.stop)

    def test_collecting_v2_links_the_second_source_without_replacing_pinned_metadata(self):
        first = self.chat.respond("收藏 " + BASE + "v1")
        second = self.chat.respond("收藏 " + BASE + "v2")
        self.assertEqual(second["row"], first["row"])
        card = second["collection"][0]
        self.assertIn(BASE + "v2", card["sources"])
        self.assertIn("v2", card["notice"])
        self.assertEqual(card["authors"], ["Author v1"])
        self.assertEqual(card["fulltext_url"], BASE.replace("/abs/", "/pdf/") + "v1")
        self.assertTrue(card["version"].endswith("v1"))
        saved = self.chat.respond("查看刚才那篇的来源链接")["collection"][0]
        self.assertEqual(saved["fulltext_url"], card["fulltext_url"])
        self.assertEqual(saved["title"], first["row"]["fields"]["论文标题"])
        self.assertEqual(len(self.table.rows()), 1)

    def test_abstract_and_method_from_explicit_or_latest_v2_never_update_v1(self):
        old = self.chat.respond("收藏 " + BASE + "v1")["row"]
        with patch.object(self.table, "upsert", wraps=self.table.upsert) as write:
            for url in (BASE + "v2", BASE):
                for kind in ("英文摘要", "英文方法"):
                    with self.subTest(url=url, kind=kind), self.assertRaisesRegex(ValueError, "v1.*v2"):
                        self.chat.respond("给 " + url + " 补" + kind)
            write.assert_not_called()
        self.assertEqual(self.table.rows(), [old])
        result = self.chat.respond("给 " + BASE + "v1 补英文方法")
        self.assertEqual(result["row"]["id"], old["id"])
        self.assertIn("743", result["row"]["fields"]["方法概述"])

    def test_same_version_multi_field_create_can_be_retried(self):
        message = "把 " + BASE + "v1 的链接和英文摘要存进阅读清单"
        first = self.chat.respond(message, request_id="save-v1")
        second = self.chat.respond(message, request_id="save-v1")
        self.assertEqual(second["row"], first["row"])
        self.assertEqual(len(self.table.rows()), 1)
