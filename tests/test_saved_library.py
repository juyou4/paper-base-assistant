import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app import Application
from table_store import LocalTable, TargetChanged
from workflow import Conversation


URL = "https://arxiv.org/abs/2005.11401"


class SavedLibraryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env = patch.dict(os.environ, PAPER_AGENT_PROVIDER="rules")
        env.start()
        self.addCleanup(env.stop)

    def test_old_row_reopens_offline_with_its_title_notes_and_progress(self):
        app = Application("local", self.tmp.name)
        try:
            record = app.table.upsert(URL, {"论文标题": "我的 RAG 题名", "论文链接": URL, "研究笔记": "先看消融"}, "old")
            rid = record["record_id"]
            app.table.set_reading_progress(rid, "待读", "在读")
        finally:
            app.close()
        reopened = Application("local", self.tmp.name)
        self.addCleanup(reopened.close)
        chat = reopened.session(None)[1]
        self.assertEqual(reopened.state(chat)["reading_progress"], {rid: "在读"})
        with patch("papers.resolve_paper", side_effect=AssertionError("已有原链接应可离线复用")):
            result = chat.respond("收藏 " + URL)
            self.assertEqual(result["row"]["id"], rid)
            self.assertIn("在读", result["message"])
            self.assertEqual(result["collection"][0]["title"], "我的 RAG 题名")
            self.assertIn("我的 RAG 题名", chat.respond("查看在读论文")["message"])
            note = chat.respond("给这篇记一句：上次看到表二")
            self.assertEqual(note["row"]["id"], rid)
        self.assertEqual(len(reopened.table.rows()), 1)
        self.assertEqual(reopened.table.get(rid)["fields"]["研究笔记"], "先看消融")
        with patch.dict(os.environ, PAPER_AGENT_PROVIDER="codex"), patch(
                "codex_provider.request", side_effect=AssertionError("已存原链接可直接从库中读取")):
            self.assertEqual(chat.respond("收藏 " + URL)["row"]["id"], rid)

    def test_fill_refuses_changed_cell(self):
        table = LocalTable(Path(self.tmp.name) / "records.sqlite3")
        self.addCleanup(table.close)
        chat = Conversation(table, self.tmp.name, "阅读清单")
        rid = table.upsert(URL, {"论文链接": URL, "论文标题": "RAG", "中文摘要": "我的摘要"}, "old")["record_id"]
        msg = "补齐缺失的摘要和方法 " + URL
        upsert = table.upsert
        def concurrent_edit(*args, **kwargs):
            upsert(URL, {"方法概述": "他人写入"}, "edit")
            return upsert(*args, **kwargs)
        with patch.object(table, "upsert", side_effect=concurrent_edit), self.assertRaises(TargetChanged):
            chat.respond(msg)
        self.assertEqual(table.get(rid)["fields"]["方法概述"], "他人写入")

    def test_fill_only_missing_on_original_row(self):
        table = LocalTable(":memory:")
        self.addCleanup(table.close)
        chat = Conversation(table, self.tmp.name, "阅读清单")
        rid = table.upsert(URL, {"论文链接": URL, "论文标题": "RAG", "中文摘要": "我的摘要"}, "old")["record_id"]
        msg = "补齐缺失的摘要和方法 " + URL
        result = chat.respond(msg)
        self.assertEqual(result["row"]["id"], rid)
        self.assertEqual(result["row"]["fields"]["中文摘要"], "我的摘要")
        self.assertTrue(result["row"]["fields"]["方法概述"])
        with patch("papers.resolve_paper", side_effect=AssertionError("没有缺项，无需解析")):
            self.assertIn("没有可补写", chat.respond(msg)["message"])
        missing = chat.respond("补齐缺失的摘要和方法 https://arxiv.org/abs/1706.03762")
        self.assertIn("没有这篇", missing["message"])
        self.assertEqual(len(table.rows()), 1)

    def test_progress_is_retryable_and_conflicting_change_is_rejected(self):
        table = LocalTable(":memory:")
        self.addCleanup(table.close)
        rid = table.upsert(URL, {"论文链接": URL}, "p")["record_id"]
        table.set_reading_progress(rid, "待读", "在读")
        table.set_reading_progress(rid, "待读", "在读")
        with self.assertRaises(TargetChanged):
            table.set_reading_progress(rid, "待读", "已读")
        self.assertEqual(table.reading_progress()[rid], "在读")
