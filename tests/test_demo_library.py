"""验证演示库的公开来源、首次并发启动和中断恢复。"""

import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import demo_library
from demo_library import prepare_demo
from table_store import LocalTable


class DemoLibraryTests(unittest.TestCase):
    def setUp(self):
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        self.root = Path(root.name)
        self.directory = self.root / "interview-demo"
        self.target = self.directory / "records.sqlite3"

    def test_public_only_seed_opens_as_normal_local_records(self):
        from app import Application

        source = json.loads(demo_library._READINGS.read_text(encoding="utf-8"))
        expected = {
            "https://arxiv.org/abs/1706.03762v7": ("Attention Is All You Need", "BLEU / EN-DE · 28.4",
                                                   "#S6.T2", "#S3.F1", "/Figures/ModalNet-21.png"),
            "https://arxiv.org/abs/2005.11401v4": ("Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks",
                                                  "RAG-Seq. · NQ · 44.5", "#S4.T2.fig1", "#S1.F1", "/RAG-Architecture.svg"),
        }
        app = Application("local", prepare_demo(self.directory))
        self.addCleanup(app.close)
        self.assertFalse((self.root / "records.sqlite3").exists())
        rows = app.table.rows()
        self.assertEqual(len(rows), 2)
        for row in rows:
            fields = row["fields"]
            self.assertEqual(set(fields), {"论文标题", "论文链接", "阅读笔记", "研究笔记"})
            self.assertIn(fields, source)
            title, metric, table_anchor, fig_anchor, image = expected[fields["论文链接"]]
            html_url = fields["论文链接"].replace("/abs/", "/html/")
            self.assertEqual(fields["论文标题"], title)
            self.assertTrue(fields["阅读笔记"].startswith("【指标】"))
            self.assertIn(metric, fields["阅读笔记"])
            self.assertIn(html_url + table_anchor, fields["阅读笔记"])
            self.assertTrue(fields["研究笔记"].startswith("【图】Figure 1 · "))
            self.assertIn(html_url + fig_anchor, fields["研究笔记"])
            self.assertIn("原图：" + html_url + image, fields["研究笔记"])
        chat = app.session(None)[1]
        result = chat.respond("再看看 attention")
        self.assertIn("28.4", result["message"])
        self.assertIn("ModalNet-21.png", chat.respond("研究笔记呢")["message"])

    def test_restart_retains_user_edits_and_reading_progress(self):
        self.assertEqual(prepare_demo(self.directory), self.directory)
        with closing(LocalTable(self.target)) as table:
            row = table.rows()[0]
            url = row["fields"]["论文链接"]
            table.upsert(url, {"阅读笔记": "我刚改过的笔记"}, "edit")
            table.set_reading_progress(row["id"], "待读", "已读")
        with patch.object(LocalTable, "upsert", side_effect=AssertionError("重启不能重新写入示例")):
            prepare_demo(self.directory)
        with closing(LocalTable(self.target)) as table:
            self.assertEqual(len(table.rows()), 2)
            self.assertEqual(table.get(row["id"])["fields"]["阅读笔记"], "我刚改过的笔记")
            self.assertEqual(table.reading_progress()[row["id"]], "已读")

    def test_interruption_cannot_publish_half_seeded_library(self):
        upsert = LocalTable.upsert
        def fail_after_one(table, *args, **kwargs):
            if table.rows():
                raise OSError("模拟第二条记录尚未写完")
            return upsert(table, *args, **kwargs)

        with patch.object(LocalTable, "upsert", fail_after_one), self.assertRaises(OSError):
            prepare_demo(self.directory)
        self.assertFalse(self.target.exists())
        prepare_demo(self.directory)
        with closing(LocalTable(self.target)) as table:
            self.assertEqual(len(table.rows()), 2)

    def test_concurrent_first_start_publishes_one_complete_library(self):
        gate = threading.Barrier(2, timeout=10)
        upsert = LocalTable.upsert
        def start_together(table, *args, **kwargs):
            if not table.rows():
                gate.wait()
            return upsert(table, *args, **kwargs)

        with patch.object(LocalTable, "upsert", start_together), ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(prepare_demo, [self.directory] * 2))
        self.assertEqual(results, [self.directory] * 2)
        self.assertEqual(list(self.directory.iterdir()), [self.target])
        with closing(LocalTable(self.target)) as table:
            self.assertEqual(len({r["fields"]["论文链接"] for r in table.rows()}), 2)
