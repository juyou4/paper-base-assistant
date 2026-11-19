"""用独立数据目录覆盖真实 SQLite 的重启、用户与数据表隔离。"""

import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor

from workspace_history import WorkspaceHistory


OWNER = "浏览器甲"
SCOPE = "表一"
FOCUS = {"record_id": "row_1", "title": "测试论文", "url": "https://example.org/paper/1"}


def operation(status="verified", **extra):
    return {"action": "回填论文", "status": status, "summary": "论文笔记回读一致",
            "details": [{"label": "出处", "value": "第 3 页", "url": FOCUS["url"]}], **extra}


class WorkspaceHistoryTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.directory = tmp.name
        self.history = WorkspaceHistory(tmp.name)
        self.space = OWNER, SCOPE
        self.first = self.history.snapshot(*self.space)["active_id"]

    def test_reopen_restores_transcript_focus_preferences_and_table_operations(self):
        h = self.history
        h.record_turn(*self.space, self.first, "r1", "我想读这篇", "已打开", FOCUS, [operation()])
        h.add_preference(*self.space, "优先看方法")
        other = h.new_conversation(*self.space)["active_id"]
        reopened = WorkspaceHistory(self.directory)
        current = reopened.snapshot(*self.space)
        self.assertEqual(current["active_id"], other)
        self.assertEqual(current["messages"], [])
        self.assertIsNone(current["focus"])
        self.assertEqual(len(current["operations"]), 1)
        selected = reopened.select_conversation(*self.space, self.first)
        self.assertEqual([m["text"] for m in selected["messages"]], ["我想读这篇", "已打开"])
        self.assertEqual(selected["focus"], FOCUS)
        self.assertEqual(selected["conversations"][1]["title"], "我想读这篇")
        self.assertEqual([p["text"] for p in selected["preferences"]], ["优先看方法"])
        self.assertEqual(set(reopened.active_context(*self.space)), {"active_id", "settings", "focus", "preferences"})
        self.assertTrue(reopened.has_owner(OWNER))
        self.assertFalse(reopened.has_owner("其他浏览器"))

    def test_scope_and_owner_isolate_selection_mutation_and_operation_reads(self):
        h = self.history
        h.record_turn(*self.space, self.first, "r1", "甲的消息", "回复", FOCUS, [operation()])
        pid = h.add_preference(*self.space, "表一偏好")["preferences"][0]["id"]
        for other in ((OWNER, "另一张表"), ("浏览器乙", SCOPE)):
            with self.subTest(other=other):
                snap = h.snapshot(*other)
                self.assertNotEqual(snap["active_id"], self.first)
                self.assertFalse(snap["messages"] or snap["operations"] or snap["preferences"] or snap["focus"])
                for method, args in ((h.select_conversation, (self.first,)), (h.delete_conversation, (self.first,)),
                                     (h.delete_preference, (pid,)), (h.set_focus, (self.first, None)),
                                     (h.record_operation, (self.first, "r2", operation()))):
                    with self.assertRaises(ValueError):
                        method(*other, *args)
                h.clear_conversations(*other)
        self.assertEqual(len(h.snapshot(*self.space)["messages"]), 2)

    def test_history_disabled_keeps_operations_and_resume_switch_clears_old_focus(self):
        h = self.history
        h.record_turn(*self.space, self.first, "r1", "第一条", "已打开", FOCUS)
        h.set_settings(*self.space, save_history=False)
        h.record_turn(*self.space, self.first, "r2", "不保存我", "回复不保存", FOCUS, [operation()])
        snap = h.snapshot(*self.space)
        self.assertEqual([m["text"] for m in snap["messages"]], ["第一条", "已打开"])
        self.assertEqual(len(snap["operations"]), 1)
        self.assertIsNone(snap["focus"])
        h.set_settings(*self.space, save_history=True)
        self.assertIsNone(h.snapshot(*self.space)["focus"])
        h.set_focus(*self.space, self.first, FOCUS)
        other = h.new_conversation(*self.space)["active_id"]
        h.set_focus(*self.space, other, FOCUS)
        h.set_settings(*self.space, resume_focus=False)
        h.set_focus(*self.space, other, FOCUS)
        h.set_settings(*self.space, resume_focus=True)
        self.assertIsNone(h.snapshot(*self.space)["focus"])
        self.assertIsNone(h.select_conversation(*self.space, self.first)["focus"])
        with self.assertRaises(ValueError):
            h.set_settings(*self.space, save_history="true")

    def test_delete_or_clear_conversations_keeps_operations_and_preference_policy(self):
        h = self.history
        p = h.add_preference(*self.space, "先看摘要")["preferences"][0]
        self.assertEqual(h.add_preference(*self.space, "先看摘要")["preferences"], [p])
        with self.assertRaises(ValueError):
            h.add_preference(*self.space, "甲" * 241)
        self.assertFalse(h.set_settings(*self.space, use_preferences=False)["settings"]["use_preferences"])
        h.record_operation(*self.space, self.first, "once", operation())
        other = h.new_conversation(*self.space)["active_id"]
        deleted = h.delete_conversation(*self.space, other)
        self.assertEqual(deleted["active_id"], self.first)
        cleared = h.clear_conversations(*self.space)
        self.assertNotEqual(cleared["active_id"], self.first)
        self.assertEqual(len(cleared["conversations"]), 1)
        self.assertEqual(len(cleared["operations"]), 1)
        self.assertEqual(cleared["preferences"], [p])
        self.assertEqual(h.delete_preference(*self.space, p["id"])["preferences"], [])

    def test_concurrent_retries_are_idempotent_and_receipts_only_advance(self):
        def submit(_):
            another = WorkspaceHistory(self.directory)
            another.record_turn(*self.space, self.first, "r", "原文", "回复", FOCUS,
                                [operation("unverified", key="batch-1"), operation("cancelled")])
        with ThreadPoolExecutor(max_workers=5) as pool:
            list(pool.map(submit, range(12)))
        h = self.history
        initial = h.snapshot(*self.space)
        self.assertEqual(len(initial["messages"]), 2)
        self.assertEqual(len(initial["operations"]), 2)
        first = next(o for o in initial["operations"] if o["status"] == "unverified")
        h.record_operation(*self.space, self.first, "new-request", operation("verified", key="batch-1"))
        h.record_operation(*self.space, self.first, "late-request", operation("failed", key="batch-1"))
        final = h.snapshot(*self.space)
        same = next(o for o in final["operations"] if o["id"] == first["id"])
        self.assertEqual(same["status"], "verified")
        self.assertNotIn("key", same)
        self.assertEqual(len(final["operations"]), 2)

    def test_simultaneous_first_start_creates_one_active_conversation(self):
        with tempfile.TemporaryDirectory() as directory:
            ready = threading.Barrier(8)
            def start(_):
                ready.wait(timeout=10)
                return WorkspaceHistory(directory).active_context(*self.space)["active_id"]
            with ThreadPoolExecutor(max_workers=8) as pool:
                ids = set(pool.map(start, range(8)))
            self.assertEqual(len(ids), 1)

    def test_only_display_payload_and_safe_source_links_survive(self):
        receipt = operation(raw_plan={"access_token": "forbidden"})
        receipt["details"] = [{"label": "网页", "value": "旧地址", "url": "javascript:bad", "secret": "raw-payload"},
                              {"label": "授权", "value": "Authorization: Bearer my-private-token"},
                              {"label": "正常出处", "value": "引用", "url": FOCUS["url"]}]
        self.history.record_turn(*self.space, self.first, "request",
                                 "api_key=do-not-copy https://example.org/?token=secret-query",
                                 "Authorization: Basic encoded-private-credentials",
                                 {**FOCUS, "pending_id": "private-draft"}, [receipt])
        snap = self.history.snapshot(*self.space)
        words = str(snap)
        for secret in ("raw-payload", "javascript:bad", "forbidden", "my-private-token", "do-not-copy",
                       "secret-query", "encoded-private-credentials", "private-draft"):
            self.assertNotIn(secret, words)
        self.assertEqual(snap["focus"], FOCUS)
        self.assertEqual(snap["operations"][0]["details"][2]["url"], FOCUS["url"])

    def test_failure_rolls_back_the_whole_turn(self):
        with self.assertRaises(ValueError):
            self.history.record_turn(*self.space, self.first, "r1", "原文", "回复", FOCUS,
                                     [operation(), operation(details={"broken": "format"})])
        view = self.history.snapshot(*self.space)
        self.assertFalse(view["messages"] or view["operations"] or view["focus"])
        self.history.record_turn(*self.space, self.first, "r1", "原文", "回复", FOCUS, [operation()])
        self.assertEqual(len(self.history.snapshot(*self.space)["operations"]), 1)

    def test_unrestorable_row_identity_still_keeps_confirmed_remote_receipt(self):
        self.history.record_turn(*self.space, self.first, "finished", "确认", "完成",
                                 {**FOCUS, "url": "换成了一个无效地址"}, [operation()])
        view = self.history.snapshot(*self.space)
        self.assertIsNone(view["focus"])
        self.assertEqual(len(view["messages"]), 2)
        self.assertEqual(view["operations"][0]["status"], "verified")

    def test_large_histories_return_bounded_pages_without_skips_or_duplicates(self):
        h = self.history
        for i in range(106):
            h.record_turn(*self.space, self.first, str(i), f"问 {i}", f"答 {i}", operations=[
                operation("unverified", summary=f"记录 {i}", key=str(i))])
        view = h.snapshot(*self.space)
        self.assertEqual(len(view["messages"]), 200)
        self.assertEqual(len(view["operations"]), 40)
        self.assertEqual(view["messages"][0]["text"], "问 6")
        self.assertEqual(view["operations"][0]["summary"], "记录 105")
        # 当前读者翻页时发生的新写入和状态更新，应留在它们原来的时间位置。
        h.record_turn(*self.space, self.first, "new", "另一个问题", "另一句回答", operations=[operation()])
        h.record_operation(*self.space, self.first, "verified", operation(key="4", summary="记录 4"))
        messages = h.page(*self.space, self.first, "messages", view["more_messages"])
        self.assertEqual([i["text"] for i in messages["items"]],
                         [s for i in range(6) for s in (f"问 {i}", f"答 {i}")])
        self.assertIsNone(messages["before"])
        full = view["operations"][:]
        cursor = view["more_operations"]
        while cursor:
            page = h.page(*self.space, self.first, "operations", cursor)
            self.assertLessEqual(len(page["items"]), 40)
            full.extend(page["items"])
            cursor = page["before"]
        self.assertEqual([o["summary"] for o in full], [f"记录 {i}" for i in reversed(range(106))])
        self.assertEqual(len({o["id"] for o in full}), 106)
        self.assertEqual(full[-5]["status"], "verified")

    def test_pages_are_bound_to_owner_table_conversation_and_item_kind(self):
        h = self.history
        for i in range(3):
            h.record_turn(*self.space, self.first, str(i), "问题", "答复", operations=[operation()])
        view = h.snapshot(*self.space, recent_messages=1, recent_operations=1)
        msg, op = view["more_messages"], view["more_operations"]
        with self.assertRaises(ValueError):
            h.page(*self.space, self.first, "operations", msg)
        with self.assertRaises(ValueError):
            h.page(*self.space, self.first, "messages", op)
        with self.assertRaises(ValueError):
            h.snapshot(*self.space, recent_messages=20000)
        for other in ((OWNER, "表二"), ("另一浏览器", SCOPE)):
            active = h.snapshot(*other)["active_id"]
            for kind, cursor in (("messages", msg), ("operations", op)):
                with self.assertRaises(ValueError):
                    h.page(*other, active, kind, cursor)
        new = h.new_conversation(*self.space)["active_id"]
        with self.assertRaises(ValueError):
            h.page(*self.space, self.first, "operations", op)
        with self.assertRaises(ValueError):
            h.page(*self.space, new, "operations", op)

    def test_only_explicit_matching_record_id_is_linkable(self):
        details = [{"label": "记录 ID", "value": "r1", "record_id": "r1"},
                   {"label": "普通列", "value": "r2", "record_id": "r2"},
                   {"label": "记录 ID", "value": "r3", "record_id": "r4"},
                   {"label": "记录 ID", "value": "r5"}]
        self.history.record_operation(*self.space, self.first, "r", operation(details=details))
        items = self.history.snapshot(*self.space)["operations"][0]["details"]
        self.assertEqual([i.get("record_id") for i in items], ["r1", None, None, None])

    def test_v1_migration_preserves_existing_history_and_adds_paging_indexes(self):
        self.history.record_turn(*self.space, self.first, "r", "存量数据", "已保存", FOCUS, [operation()])
        with sqlite3.connect(self.history.path) as db:
            db.execute("DROP INDEX turn_pages")
            db.execute("DROP INDEX operation_pages")
            db.execute("PRAGMA user_version=1")
        db.close()
        migrated = WorkspaceHistory(self.directory)
        current = migrated.snapshot(*self.space)
        self.assertEqual(current["active_id"], self.first)
        self.assertEqual(current["messages"][0]["text"], "存量数据")
        self.assertEqual(current["focus"], FOCUS)
        self.assertEqual(len(current["operations"]), 1)
        with sqlite3.connect(migrated.path) as db:
            self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 2)
            names = {i[0] for i in db.execute("SELECT name FROM sqlite_master WHERE type='index'")}
            self.assertTrue({"turn_pages", "operation_pages"} <= names)
        db.close()

    def test_future_database_version_is_not_downgraded(self):
        with sqlite3.connect(self.history.path) as db:
            db.execute("PRAGMA user_version=9")
        db.close()
        with self.assertRaisesRegex(ValueError, "较新"):
            WorkspaceHistory(self.directory)
