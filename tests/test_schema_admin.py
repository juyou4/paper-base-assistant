import copy
import json
import sqlite3
import sys
import unittest
from unittest.mock import patch

from schema_admin import commit_field_change, inspect_field, plan_field_change
from table_store import FeishuTable, LarkCliTable, LocalTable, TableError


class LocalSchemaTests(unittest.TestCase):
    def setUp(self):
        self.table = LocalTable(":memory:")
        self.addCleanup(self.table.close)
        self.one = self.table.upsert("arxiv:2005.11401", {"阅读笔记": "第一行", "论文标题": "标题"}, "one")["record_id"]
        self.two = self.table.upsert("arxiv:2301.00001", {"论文标题": "另一篇"}, "two")["record_id"]

    def test_rename_and_delete_only_migrate_the_selected_column(self):
        self.assertEqual(inspect_field(self.table, "reading")["name"], "阅读笔记")
        plan = plan_field_change(self.table, "reading", "rename", new_name="复现建议")
        self.assertEqual((plan["affected_count"], plan["total_rows"]), (1, 2))
        self.assertEqual(plan["affected_record_ids"], [self.one])
        self.assertEqual(self.table.get(self.one)["fields"]["阅读笔记"], "第一行")
        result = commit_field_change(self.table, json.loads(json.dumps(plan)))
        self.assertEqual(result["field"]["id"], "reading")
        self.assertEqual(self.table.get(self.one)["fields"], {"论文标题": "标题", "复现建议": "第一行"})
        self.assertEqual(self.table.get(self.two)["fields"], {"论文标题": "另一篇"})
        delete = plan_field_change(self.table, "reading", "delete")
        self.assertIsNone(commit_field_change(self.table, delete)["field"])
        self.assertEqual(self.table.get(self.one)["fields"], {"论文标题": "标题"})
        self.assertEqual(len(self.table.rows()), 2)
        with self.assertRaisesRegex(TableError, "不存在"):
            inspect_field(self.table, "reading")

    def test_no_type_change_on_populated_fields_and_no_mutation_of_primary(self):
        with self.assertRaisesRegex(TableError, "1 条已填"):
            plan_field_change(self.table, "reading", "change_type", new_type="url")
        with self.assertRaisesRegex(TableError, "保护"):
            plan_field_change(self.table, "title", "delete")
        with self.assertRaisesRegex(TableError, "已经存在"):
            plan_field_change(self.table, "reading", "rename", new_name="研究笔记")
        fid = self.table.add_field("网址草稿", "text")["id"]
        for kind in ("url", "text"):
            plan = plan_field_change(self.table, fid, "change_type", new_type=kind)
            self.assertEqual(commit_field_change(self.table, plan)["field"]["type"], kind)

    def test_stale_values_or_mutated_plan_abort(self):
        plan = plan_field_change(self.table, "reading", "delete")
        mutated = dict(plan, new_name="被改动", action="rename")
        with self.assertRaisesRegex(TableError, "变化"):
            commit_field_change(self.table, mutated)
        self.table.upsert("arxiv:2005.11401", {"阅读笔记": "内容变了，影响行数相同"}, "extra")
        with self.assertRaisesRegex(TableError, "变化"):
            commit_field_change(self.table, plan)
        self.assertEqual(self.table.get(self.one)["fields"]["阅读笔记"], "内容变了，影响行数相同")

    def test_storage_error_rolls_back_column_values_together(self):
        self.table._db.execute("""CREATE TRIGGER test_fail BEFORE UPDATE ON demo_columns
            BEGIN SELECT RAISE(ABORT, '模拟列改名失败'); END;""")
        plan = plan_field_change(self.table, "reading", "rename", new_name="复盘")
        with self.assertRaises(sqlite3.IntegrityError):
            commit_field_change(self.table, plan)
        self.assertEqual(inspect_field(self.table, "reading")["name"], "阅读笔记")
        self.assertEqual(self.table.get(self.one)["fields"]["阅读笔记"], "第一行")


class FakeFieldAPI:
    def __init__(self):
        self.columns = [
            {"field_id": "main", "field_name": "论文标题", "type": 1, "ui_type": "Text", "is_primary": True},
            {"field_id": "custom", "field_name": "笔记", "type": 1, "ui_type": "Text",
             "description": {"text": "内部说明", "disable_sync": True}, "property": {"test_setting": True}},
            {"field_id": "calc", "field_name": "统计", "type": 20, "ui_type": "Formula"},
            {"field_id": "auto", "field_name": "创建时间", "type": 1001},
            {"field_id": "link", "field_name": "出处", "type": 15, "ui_type": "Url",
             "is_extend": False, "is_synced": False},
        ]
        self.records = [{"record_id": "rec_a", "fields": {"笔记": [{"type": "text", "text": "原文"}]}}]
        self.writes = []

    def request(self, method, path, body=None, query=None, authenticated=True):
        if method == "GET":
            collection = self.columns if path.endswith("/fields") else self.records
            return {"data": {"items": copy.deepcopy(collection), "has_more": False}}
        fid = path.rsplit("/", 1)[-1]
        field = next(c for c in self.columns if c["field_id"] == fid)
        self.writes.append((method, path, copy.deepcopy(body)))
        if method == "DELETE":
            self.columns.remove(field)
        elif method == "PUT":
            old = field["field_name"]
            field.update(body)
            for row in self.records:
                if old in row["fields"]:
                    row["fields"][field["field_name"]] = row["fields"].pop(old)
        return {"data": {}}


class RemoteSchemaTests(unittest.TestCase):
    def test_http_and_cli_use_verified_fields_and_protected_types(self):
        for cls, args in ((FeishuTable, ("app", "dummy", "base", "tbl")),
                          (LarkCliTable, (sys.executable, "base", "tbl"))):
            table, api = cls(":memory:", *args), FakeFieldAPI()
            self.addCleanup(table.close)
            with self.subTest(cls=cls), patch.object(table, "_request", side_effect=api.request):
                for fid in ("main", "calc", "auto"):
                    with self.assertRaisesRegex(TableError, "保护"):
                        plan_field_change(table, fid, "delete")
                plan = plan_field_change(table, "custom", "rename", new_name="阅读笔记")
                self.assertEqual(api.writes, [])
                self.assertEqual(plan["affected_count"], 1)
                self.assertEqual(commit_field_change(table, plan)["field"]["name"], "阅读笔记")
                method, path, payload = api.writes[0]
                self.assertEqual(method, "PUT")
                self.assertTrue(path.endswith("/fields/custom"))
                self.assertEqual(payload, {"field_name": "阅读笔记", "type": 1, "ui_type": "Text",
                                          "description": {"text": "内部说明", "disable_sync": True},
                                          "property": {"test_setting": True}})
                convert = plan_field_change(table, "link", "change_type", new_type="text")
                self.assertEqual(commit_field_change(table, convert)["field"]["type"], "text")
                remove = plan_field_change(table, "custom", "delete")
                self.assertIsNone(commit_field_change(table, remove)["field"])
                self.assertEqual(api.writes[-1][0], "DELETE")

    def test_remote_definition_raw_cell_and_affected_ids_are_rechecked(self):
        table, api = FeishuTable(":memory:", "app", "dummy", "base", "tbl"), FakeFieldAPI()
        self.addCleanup(table.close)
        with patch.object(table, "_request", side_effect=api.request):
            for change in ("metadata", "cells"):
                plan = plan_field_change(table, "custom", "rename", new_name="新笔记")
                if change == "metadata":
                    api.columns[1]["description"]["text"] = "已经改过说明"
                else:
                    api.records[0]["fields"]["笔记"][0]["text"] = "原文改变了"
                with self.assertRaisesRegex(TableError, "变化"):
                    commit_field_change(table, plan)
            api.columns[1]["new_unrecognized_option"] = {"test": True}
            with self.assertRaisesRegex(TableError, "配置"):
                plan_field_change(table, "custom", "rename", new_name="新笔记")
            self.assertEqual(api.writes, [])

    def test_remote_unknown_primary_and_incomplete_ack(self):
        table, api = FeishuTable(":memory:", "app", "dummy", "base", "tbl"), FakeFieldAPI()
        self.addCleanup(table.close)
        with patch.object(table, "_request", side_effect=api.request):
            del api.columns[0]["is_primary"]
            self.assertTrue(inspect_field(table, "custom")["protected"])
            api.columns[0]["is_primary"] = True
            for flag in ("is_synced", "is_extend"):
                api.columns[-1][flag] = True
                with self.assertRaisesRegex(TableError, "保护"):
                    plan_field_change(table, "link", "delete")
                api.columns[-1][flag] = False
            plan = plan_field_change(table, "link", "delete")
            with patch.object(table, "_request", side_effect=lambda m,p,*a,**k: api.request(m,p,*a,**k) if m=="GET" else {"data":{}}):
                with patch("schema_admin.time.sleep"), self.assertRaisesRegex(TableError, "回读未匹配"):
                    commit_field_change(table, plan)

    def test_unfinished_write_blocks_a_change_to_the_field_it_needs(self):
        table, api = FeishuTable(":memory:", "app", "dummy", "base", "tbl"), FakeFieldAPI()
        self.addCleanup(table.close)
        with patch.object(table, "_request", side_effect=api.request):
            plan = plan_field_change(table, "custom", "rename", new_name="阅读笔记")
            with table._db:
                table._db.execute("INSERT INTO fs_pending_creates VALUES(?,?,?,?)",
                                  (table._scope, "arxiv:2005.11401", "busy", json.dumps({"笔记": "写入中"})))
            with self.assertRaisesRegex(TableError, "尚未回读"):
                commit_field_change(table, plan)
            self.assertEqual(api.writes, [])


if __name__ == "__main__":
    unittest.main()
