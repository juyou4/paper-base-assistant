"""从正常对话入口验证表内追问、实时回读与失效边界。"""

import os
import unittest
from contextlib import ExitStack, contextmanager
from copy import deepcopy
from unittest.mock import patch

from table_store import FeishuTable, LocalTable, RecordNotFound
from test_table_store import FakeFeishu
from workflow import Conversation


URL = "https://arxiv.org/abs/2005.11401"
OTHER = "https://example.org/other"


class SavedFocusTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, PAPER_AGENT_PROVIDER="rules")
        env.start()
        self.addCleanup(env.stop)
        self.table = LocalTable(":memory:")
        self.addCleanup(self.table.close)
        self.chat = Conversation(self.table, ".", "阅读清单")

    def seed(self):
        return self.table.upsert(URL, {"论文标题": "Retrieval Generation", "论文链接": URL,
                                      "中文摘要": "自己的原摘要", "阅读笔记": "我的阅读批注",
                                      "研究笔记": "研究设计问题"}, "initial")["record_id"]

    @contextmanager
    def only_read(self):
        with ExitStack() as stack:
            for target in ("papers.search_titles", "papers.resolve_paper", "papers.download_pdf",
                           "evidence_extract.extract_evidence", "codex_provider.request"):
                stack.enter_context(patch(target, side_effect=AssertionError("表内追问不应调用 " + target)))
            stack.enter_context(patch.object(self.table, "upsert", side_effect=AssertionError("只允许表内回读")))
            yield

    def test_short_followups_reread_only_current_field_and_row(self):
        rid = self.seed()
        self.chat.respond("再看看 retrieval")
        self.table.upsert(URL, {"中文摘要": "同事刚修订的摘要"}, "new")
        with self.only_read(), patch.object(self.table, "rows", side_effect=AssertionError("按 ID 单行回读")), \
                patch.object(self.table, "get", wraps=self.table.get) as get:
            for phrase, included, excluded in [
                ("它的摘要呢？", "同事刚修订的摘要", "我的阅读批注"),
                ("笔记呢", "我的阅读批注", "同事刚修订的摘要"),
                ("那它的研究笔记写了什么？", "研究设计问题", "我的阅读批注"),
                ("看看刚才那篇的方法", "还没有保存方法", "研究设计问题"),
                ("刚才那篇", "我的阅读批注", "自己的原摘要"),
            ]:
                with self.subTest(phrase=phrase):
                    answer = self.chat.respond(phrase)
                    self.assertEqual(answer["saved_record_id"], rid)
                    self.assertIn(included, answer["message"])
                    self.assertNotIn(excluded, answer["message"])
                    get.assert_called_once_with(rid)
                    get.reset_mock()

    def test_save_and_repeat_link_establish_focus_without_a_title_command(self):
        created = self.chat.respond("收藏 " + URL)
        rid = created["row"]["id"]
        self.table.upsert(URL, {"研究笔记": "准备复现"}, "note")
        with self.only_read():
            self.assertIn("准备复现", self.chat.respond("它的笔记呢")["message"])
            later = Conversation(self.table, ".", "阅读清单")
            self.assertEqual(later.respond("收藏 " + URL)["row"]["id"], rid)
            self.assertIn("准备复现", later.respond("研究笔记呢")["message"])

    def test_feishu_adapter_uses_direct_read_of_selected_row_and_current_column_values(self):
        table = FeishuTable(":memory:", "app", "dummy", "base", "table")
        self.addCleanup(table.close)
        fake = FakeFeishu()
        fake.columns.append({"field_id": "title", "field_name": "论文标题", "type": 1})
        fake.records["chosen"] = {"论文标题": "Retrieval Generation", "论文链接": {"link": URL},
                                  "方法概述": "第一次记录"}
        chat = Conversation(table, ".", "飞书测试清单")
        with self.only_read(), patch.object(table, "_request", side_effect=fake) as api:
            chat.respond("再看看 retrieval")
            fake.records["chosen"]["方法概述"] = "刚在飞书修改的描述"
            api.reset_mock()
            self.assertIn("刚在飞书修改的描述", chat.respond("它的技术路线呢")["message"])
        paths = [call.args[1] for call in api.call_args_list]
        self.assertTrue(any(path.endswith("/records/chosen") for path in paths))
        self.assertFalse(any(path.endswith("/records") for path in paths))
        self.assertEqual({call.args[0] for call in api.call_args_list}, {"GET"})

    def test_changed_link_and_deleted_row_do_not_resolve_a_replacement(self):
        self.seed()
        self.chat.respond("再看看 retrieval")
        self.table.upsert(URL, {"论文链接": OTHER, "中文摘要": "另一篇的内容"}, "replace")
        with self.only_read():
            stale = self.chat.respond("摘要呢")
            self.assertIn("重新", stale["message"])
            self.assertNotIn("另一篇的内容", stale["message"])
            self.assertIsNone(self.chat.last)
            self.chat.respond("再看看 retrieval")
            with patch.object(self.table, "get", side_effect=RecordNotFound("已删除")):
                gone = self.chat.respond("笔记呢")
            self.assertNotIn("saved_record_id", gone)
            self.assertNotIn("我的阅读批注", gone["message"])
            self.assertIsNone(self.chat.last)

    def test_unselected_or_switched_table_does_not_guess_a_record(self):
        self.seed()
        other = LocalTable(":memory:")
        self.addCleanup(other.close)
        other.upsert(OTHER, {"论文标题": "另一张表的唯一记录", "研究笔记": "不该看到"}, "other")
        with self.only_read():
            self.assertNotIn("saved_record_id", self.chat.respond("它的摘要呢"))
            self.chat.respond("再看看 retrieval")
            self.chat.select_table(other, "别的清单", "", "")
            with patch.object(other, "rows", side_effect=AssertionError("没有明确选择不能猜")), \
                    patch.object(other, "get", side_effect=AssertionError("不能跨表回读")):
                answer = self.chat.respond("笔记呢")
            self.assertIn("别的清单", answer["message"])
            self.assertNotIn("不该看到", answer["message"])
            self.chat.select_table(self.table, "阅读清单", "", "")
            self.assertNotIn("saved_record_id", self.chat.respond("刚才那篇呢"))

    def test_followup_during_admin_preview_preserves_exact_plan(self):
        rid = self.seed()
        self.chat.respond("再看看 retrieval")
        offer = self.chat.respond("新增文本列 复核状态")
        pending = deepcopy(self.chat.pending)
        with self.only_read():
            result = self.chat.respond("阅读笔记呢", offer["pending_id"])
        self.assertEqual(result["saved_record_id"], rid)
        self.assertEqual(result["pending_id"], offer["pending_id"])
        self.assertEqual(result["choices"], offer["choices"])
        self.assertEqual(self.chat.pending, pending)
        self.assertNotIn("复核状态", {field["name"] for field in self.table.schema()})

    def test_side_read_focus_expires_with_preview_and_never_changes_its_write_target(self):
        first_id = self.seed()
        other_id = self.table.upsert(OTHER, {"论文标题": "Second Paper", "论文链接": OTHER,
                                           "阅读笔记": "第二篇的批注"}, "second")["record_id"]
        self.chat.respond("再看看 retrieval")
        offer = self.chat.respond("新增文本列 复核状态")
        pending = deepcopy(self.chat.pending)
        with self.only_read():
            self.chat.respond("看看 Second Paper", offer["pending_id"])
            reading = self.chat.respond("笔记呢", offer["pending_id"])
            self.assertEqual(reading["saved_record_id"], other_id)
            self.assertNotIn("我的阅读批注", reading["message"])
            self.assertEqual(self.chat.last, URL)
            self.assertEqual(self.chat.pending, pending)
        self.table.upsert(OTHER, {"论文链接": URL}, "replaced")
        with self.only_read():
            changed = self.chat.respond("刚才那篇", offer["pending_id"])
            self.assertNotIn("saved_record_id", changed)
            self.assertNotIn("saved_record_id", self.chat.respond("阅读笔记呢", offer["pending_id"]))
            self.assertEqual(self.chat.last, URL)
            self.assertEqual(self.chat.pending, pending)
            self.chat.respond("取消", offer["pending_id"])
            self.assertEqual(self.chat.respond("研究笔记呢")["saved_record_id"], first_id)

    def test_multiple_titles_must_be_selected_before_showing_notes(self):
        self.seed()
        self.table.upsert(OTHER, {"论文标题": "Retrieval Survey", "研究笔记": "另一篇的思考"}, "other")
        self.chat.respond("再看看 retrieval generation")
        with self.only_read():
            choices = self.chat.respond("再看看 retrieval")
            ask = self.chat.respond("它的笔记呢", choices["pending_id"])
            self.assertEqual(ask["choices"], choices["choices"])
            self.assertNotIn("saved_record_id", ask)
            self.assertNotIn("我的阅读批注", ask["message"])
            chosen = next(label for label in choices["choices"] if "Survey" in label)
            self.chat.respond(chosen, choices["pending_id"])
            result = self.chat.respond("研究笔记呢")
        self.assertIn("另一篇的思考", result["message"])
        self.assertNotIn("研究设计问题", result["message"])
