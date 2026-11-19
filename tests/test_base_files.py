"""独立文件的身份、回执与重复确认。飞书请求使用本地替身。"""

import hashlib
import json
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from base_files import create_base_file
from table_store import FeishuTable, LarkCliTable, LocalTable, TableError, _RejectedChange


FIELDS = [{"name": "论文标题", "type": "text"}, {"name": "论文链接", "type": "url"}]


class FileAPI:
    def __init__(self):
        self.files = 0
        self.tables = {"tblDefault": "数据表"}
        self.created = []
        self.fail = ""
        self.default_id = "tblDefault"
        self.url = "https://example.feishu.cn/base/baseNew"
        self.rows = []
        self.calls = []

    def __call__(self, method, path, body=None, query=None, **kwargs):
        self.calls.append((method, path))
        if method == "POST" and path == "/bitable/v1/apps":
            if self.fail == "denied":
                raise _RejectedChange("权限不足")
            self.files += 1
            if self.fail == "lost":
                raise TableError("超时")
            return {"data": {"app": {"app_token": "baseNew", "url": self.url,
                                     "default_table_id": self.default_id, "name": body["name"]}}}
        root = "/bitable/v1/apps/baseNew/tables"
        if method == "GET" and path == root:
            return {"data": {"items": [{"table_id": i, "name": n} for i, n in self.tables.items()]}}
        if method == "POST" and path == root:
            self.created.append(body["table"])
            self.tables["tblWork"] = body["table"]["name"]
            return {"data": {"table_id": "tblWork", "field_id_list": ["fldTitle", "fldUrl"]}}
        if method == "GET" and path == root + "/tblWork/fields":
            if self.fail == "read":
                raise TableError("字段尚不可见")
            return {"data": {"items": [dict(f, field_id=i, is_primary=i == "fldTitle") for f, i in
                                       zip(self.created[0]["fields"], ["fldTitle", "fldUrl"])]}}
        if method == "GET" and path == root + "/tblDefault/records":
            return {"data": {"items": self.rows}}
        if method == "PATCH" and path == root + "/tblDefault":
            self.tables["tblDefault"] = body["name"]
            return {"data": {}}
        raise AssertionError((method, path, body, query))


class BaseFilesTests(unittest.TestCase):
    def test_local_sources_in_one_folder_can_reuse_confirmation_id(self):
        with tempfile.TemporaryDirectory() as folder:
            for source_name, note_field in (("first", "甲笔记"), ("second", "乙笔记")):
                with closing(LocalTable(Path(folder) / (source_name + ".sqlite3"))) as source:
                    new, _ = create_base_file(source, "文献", "阅读", FIELDS, "same")
                    with closing(new):
                        self.assertEqual([f["name"] for f in new.schema()], ["论文标题", "论文链接"])
                        new.add_field(note_field, "text")
            for source_name, note_field in (("first", "甲笔记"), ("second", "乙笔记")):
                with closing(LocalTable(Path(folder) / (source_name + ".sqlite3"))) as source:
                    replay, _ = create_base_file(source, "文献", "阅读", FIELDS, "same")
                    with closing(replay):
                        self.assertEqual([f["name"] for f in replay.schema()], ["论文标题", "论文链接", note_field])

    def test_old_receipt_keeps_its_file_when_another_source_reuses_the_id(self):
        with tempfile.TemporaryDirectory() as folder:
            key = "same"
            legacy_path = Path(folder) / "created-bases" / (hashlib.sha256(key.encode()).hexdigest() + ".sqlite3")
            with closing(LocalTable.new_base(legacy_path, "阅读", FIELDS)) as existing:
                existing.add_field("已保存的旧笔记", "text")
                old_table_id = existing.table_id
            with closing(LocalTable(Path(folder) / "old.sqlite3")) as source:
                plan = json.dumps([["LocalTable", "", ""], "文献", "阅读", FIELDS], ensure_ascii=False, sort_keys=True)
                with source._db:
                    source._db.execute("""CREATE TABLE base_file_requests (id TEXT PRIMARY KEY, plan TEXT NOT NULL,
                                         token TEXT, url TEXT, default_table_id TEXT, table_id TEXT)""")
                    source._db.execute("INSERT INTO base_file_requests(id,plan,table_id) VALUES (?,?,?)",
                                       (key, plan, old_table_id))
                replay, _ = create_base_file(source, "文献", "阅读", FIELDS, key)
                with closing(replay):
                    self.assertEqual(replay.schema()[-1]["name"], "已保存的旧笔记")
            with closing(LocalTable(Path(folder) / "new.sqlite3")) as source:
                new, _ = create_base_file(source, "文献", "阅读", FIELDS, key)
                with closing(new):
                    self.assertEqual(len(new.schema()), 2)
                    new_path = Path(new._db_path)
                self.assertNotEqual(new_path, legacy_path)
                new_path.unlink()
                with self.assertRaisesRegex(TableError, "找不到"):
                    create_base_file(source, "文献", "阅读", FIELDS, key)

    def test_local_new_file_is_separate_and_replay_keeps_user_changes(self):
        with tempfile.TemporaryDirectory() as folder:
            src = Path(folder) / "store.sqlite3"
            table = LocalTable(src)
            new, address = create_base_file(table, "研究计划", "阅读任务", FIELDS, "run1")
            self.assertEqual(address, "")
            self.assertEqual([s["name"] for s in new.schema()], ["论文标题", "论文链接"])
            self.assertEqual(len(new.list_tables()), 1)
            self.assertEqual(len(table.schema()), 6)
            new.add_field("笔记", "text")
            new.close()
            table.close()
            table = LocalTable(src)
            new, _ = create_base_file(table, "研究计划", "阅读任务", FIELDS, "run1")
            self.assertEqual(new.name, "阅读任务")
            self.assertEqual(new.schema()[-1]["name"], "笔记")
            new.close()
            with self.assertRaisesRegex(TableError, "其他建表方案"):
                create_base_file(table, "另一个文件", "阅读任务", FIELDS, "run1")
            table.close()

    def remote(self, cli=False):
        table = (LarkCliTable(":memory:", sys.executable, "", "") if cli else
                 FeishuTable(":memory:", "test-app", "test-secret", "baseOld", "tblOld"))
        self.addCleanup(table.close)
        api = FileAPI()
        stub = patch.object(table, "_request", side_effect=api)
        stub.start()
        self.addCleanup(stub.stop)
        return table, api

    def test_remote_new_working_table_and_url_for_both_auth_modes(self):
        for cli in (False, True):
            with self.subTest(cli=cli):
                table, api = self.remote(cli)
                new, url = create_base_file(table, "文献", "阅读", FIELDS, "r")
                self.assertEqual((new.app_token, new.table_id), ("baseNew", "tblWork"))
                self.assertEqual(url, api.url + "?table=tblWork")
                self.assertEqual(api.tables["tblDefault"], "数据表")
                self.assertEqual(len(new.schema()), 2)
                create_base_file(table, "文献", "阅读", FIELDS, "r")
                self.assertEqual((api.files, len(api.created)), (1, 1))
                self.assertFalse(any("baseOld" in path for _, path in api.calls))

    def test_file_receipt_survives_field_readback_failure(self):
        table, api = self.remote()
        api.fail = "read"
        with self.assertRaisesRegex(TableError, "已创建"):
            create_base_file(table, "文献", "阅读", FIELDS, "r")
        api.fail = ""
        new, _ = create_base_file(table, "文献", "阅读", FIELDS, "r")
        self.assertEqual(new.table_id, "tblWork")
        self.assertEqual((api.files, len(api.created)), (1, 1))

    def test_unknown_file_receipt_never_reposts_and_permissions_are_retryable(self):
        table, api = self.remote()
        api.fail = "denied"
        with self.assertRaisesRegex(TableError, "权限"):
            create_base_file(table, "文献", "阅读", FIELDS, "r")
        api.fail = "lost"
        with self.assertRaisesRegex(TableError, "未返回"):
            create_base_file(table, "文献", "阅读", FIELDS, "r")
        api.fail = ""
        with self.assertRaisesRegex(TableError, "待核对"):
            create_base_file(table, "文献", "阅读", FIELDS, "r")
        self.assertEqual((api.files, len(api.created)), (1, 0))

    def test_same_name_requires_returned_default_id_and_empty_rows(self):
        table, api = self.remote()
        api.rows = [{"record_id": "rec1", "fields": {"内容": "用户保存的笔记"}}]
        with self.assertRaisesRegex(TableError, "已有内容"):
            create_base_file(table, "文献", "数据表", FIELDS, "r")
        self.assertEqual(api.tables, {"tblDefault": "数据表"})
        api.rows = []
        new, _ = create_base_file(table, "文献", "数据表", FIELDS, "r")
        self.assertEqual((api.files, new.name, len(api.tables)), (1, "数据表", 2))
        self.assertNotEqual(api.tables["tblDefault"], "数据表")
        other, no_id = self.remote()
        no_id.default_id = ""
        with self.assertRaisesRegex(TableError, "已有名为"):
            create_base_file(other, "文献", "数据表", FIELDS, "r")
        self.assertEqual(no_id.tables, {"tblDefault": "数据表"})

    def test_invalid_plan_or_returned_link_never_writes_a_table(self):
        table, api = self.remote()
        with self.assertRaises(TableError):
            create_base_file(table, "文献", "阅读", [], "first")
        self.assertEqual(api.calls, [])
        api.url = "https://example.feishu.cn/base/baseOther"
        with self.assertRaisesRegex(TableError, "不一致"):
            create_base_file(table, "文献", "阅读", FIELDS, "second")
        self.assertEqual((api.files, len(api.created)), (1, 0))
