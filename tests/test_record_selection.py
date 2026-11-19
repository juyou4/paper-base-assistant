"""用阅读清单中实际展示的身份，复核多篇选择、切表和格式导出。"""

import unittest
from unittest.mock import patch

from saved_compare import comparison_data, read_selection
from research_exports import export_records
from table_store import LocalTable
from workflow import Conversation


class RecordSelectionTests(unittest.TestCase):
    def setUp(self):
        self.table = LocalTable(":memory:")
        self.addCleanup(self.table.close)
        self.chat = Conversation(self.table, ".", "对照测试表")
        self.refs = []
        for i, title in enumerate(("Alpha Study", "Beta Research", "Gamma Results", "Delta Notes")):
            url = f"https://example.org/paper-{i}"
            body = {"论文标题": title, "论文链接": url, "中文摘要": f"值 {i} | 条件\n换行"}
            rid = self.table.upsert(url, body, str(i))["record_id"]
            self.refs.append({"id": rid, "url": url, "title": title})

    def test_four_ordered_columns_and_markdown_keep_sources(self):
        refs = self.refs[::-1]
        with patch.object(self.table, "upsert", side_effect=AssertionError("只读对照")):
            selection = read_selection(self.chat, self.table.schema(), refs)
            result = comparison_data("对照测试表", self.table.schema(), selection, ["摘要"])
        self.assertEqual([p["title"] for p in result["papers"]], [r["title"] for r in refs])
        self.assertIn(r"值 3 \| 条件<br>换行", result["markdown"])
        self.assertIn(refs[1]["url"], result["markdown"])
        for bad in ([refs[0]], [*refs, refs[0]], [refs[0], refs[0]]):
            with self.assertRaises(ValueError):
                read_selection(self.chat, self.table.schema(), bad)

    def test_changed_record_and_changed_table(self):
        ref = self.refs[0]
        self.table.upsert(ref["url"], {"论文标题": "替换题名"}, "renamed")
        with self.assertRaisesRegex(ValueError, "重新选择"):
            read_selection(self.chat, self.table.schema(), self.refs[:2])
        old = self.chat.table_view_id
        self.chat.select_table(self.table, "表", "", "")
        self.assertNotEqual(self.chat.table_view_id, old)

    def test_resource_urls_are_not_implicit_paper_identifiers(self):
        from workflow import _fields
        columns = [{"name": name, "type": "url"} for name in ("代码链接", "数据集链接", "GitHub repo")]
        self.assertEqual(_fields(columns, "url"), [])
        self.assertEqual(_fields(columns + [{"name": "论文地址", "type": "text"}], "url"),
                         [{"name": "论文地址", "type": "text"}])

    def test_explicit_publication_type_round_trips_even_without_metadata_cache(self):
        self.table.add_field("出版类型", "text")
        for ref, kind in zip(self.refs, ("会议论文", "期刊论文", "预印本")):
            self.table.upsert(ref["url"], {"出版类型": kind}, "set-type-" + ref["id"])
        data = export_records(self.table, self.table.schema(), self.table.rows(), "ris", "清单")
        self.assertEqual(data["content"].count("TY  - CPAPER"), 1)
        self.assertEqual(data["content"].count("TY  - JOUR"), 1)
        self.assertEqual(data["content"].count("TY  - GEN"), 2)

    def test_group_request_retains_order_and_resources_use_existing_fields(self):
        q = "对比我收藏的《Alpha》和《Delta》、 《Gamma》的摘要"
        result = self.chat.respond(q)
        self.assertEqual([p["title"] for p in result["saved_comparison"]["papers"]],
                         [self.refs[i]["title"] for i in (0, 3, 2)])
        self.assertEqual(self.chat.respond("打开第三篇")["saved_record_id"], self.refs[2]["id"])
        self.table.upsert(self.refs[2]["url"], {"研究笔记": "【代码仓库】GitHub · authors/test · https://github.com/authors/test\n出处：论文第 2 页\n\n私人笔记"}, "saved-code")
        with patch("evidence_workflow.prepare", side_effect=AssertionError("回看不重新提取")):
            msg = self.chat.respond("它的代码链接呢")["message"]
        self.assertIn("github.com/authors/test", msg)
        self.assertNotIn("私人笔记", msg)
