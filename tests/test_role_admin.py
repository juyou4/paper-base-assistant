"""不连接真实租户；验证增量请求以及本地角色预览、漂移与回读。"""

import json
import tempfile
import threading
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from role_admin import RoleAdmin, ROW_LABEL
from table_store import LocalTable, TableError


class FakeBase:
    _base = "/bitable/v1/apps/base-test/tables/table-1"
    _lock = threading.RLock()

    def __init__(self):
        self.enabled = True
        self.puts = []
        self.no_apply = False
        self.alter_other_table = False
        self.fields = [
            {"field_id": "fld-1", "field_name": "标题", "type": 1},
            {"field_id": "fld-2", "field_name": "论文链接", "type": 15},
            {"field_id": "fld-3", "field_name": "作者公式", "type": 20},
            {"field_id": "fld-4", "field_name": "登记人", "type": 1003},
        ]
        self.role = {"role_id": "role-01", "role_name": "组内阅读", "role_type": "custom_role",
                     "base_rule_map": {"download": True}, "table_rule_map": {
                         "测试表": {"perm": "edit", "field_rule": {"field_perm_mode": "all_edit"},
                                   "record_rule": {"record_operations": ["add"], "other_record_all_read": True},
                                   "view_rule": {"allow_edit": False}},
                         "其他表": {"perm": "read_only"}}}

    def _request(self, method, path, body=None, query=None):
        if method == "GET":
            if path == "/bitable/v1/apps/base-test":
                return {"code": 0, "data": {"app": {"is_advanced": self.enabled}}}
            if path == "/bitable/v1/apps/base-test/tables":
                return {"code": 0, "data": {"items": [{"table_id": "table-1", "name": "测试表"}]}}
            if path == "/base/v3/bases/base-test/roles":
                roles = [{"role_id": "admin", "role_type": "editor", "role_name": "系统编辑者"},
                         {"role_id": self.role["role_id"], "role_name": self.role["role_name"]}]
                return {"code": 0, "data": {"data": json.dumps({"base_roles": roles})}}
            if path == "/base/v3/bases/base-test/roles/role-01":
                return {"code": 0, "data": {"code": 0, "data": json.dumps(self.role)}}
        if method == "PUT" and path == "/base/v3/bases/base-test/roles/role-01":
            self.puts.append(deepcopy(body))
            if not self.no_apply:
                self.role["table_rule_map"]["测试表"].update(body["table_rule_map"]["测试表"])
            if self.alter_other_table:
                self.role["table_rule_map"]["其他表"] = {"perm": "edit"}
            return {"code": 0, "data": {"code": 0, "data": ""}}
        raise AssertionError(f"意外的请求 {method} {path}")

    def _items(self, suffix):
        assert suffix == "/fields"
        return list(self.fields)


class TestRoleAdmin(unittest.TestCase):
    def test_local_preview_and_persistence(self):
        with tempfile.TemporaryDirectory() as work:
            db = Path(work) / "demo.sqlite"
            table = LocalTable(db)
            try:
                admin = RoleAdmin(table, "无关别名")
                role = admin.list_roles()[0]["name"]
                plan = admin.plan_field(role, "阅读笔记", "no_perm")
                self.assertEqual(admin.inspect(role)["fields"]["阅读笔记"], "可查看")
                self.assertIn("重新读取", admin.apply(plan)["summary"])
            finally:
                table.close()
            table = LocalTable(db)
            try:
                admin = RoleAdmin(table)
                self.assertEqual(admin.inspect(role)["fields"]["阅读笔记"], "隐藏")
                for scope in ("own_edit_all_read", "own_only", "all_edit"):
                    admin.apply(admin.plan_rows(role, scope))
                    self.assertEqual(admin.inspect(role)["rows"], ROW_LABEL[scope])
            finally:
                table.close()

    def test_remote_incremental_field_and_drift(self):
        table = FakeBase()
        admin = RoleAdmin(table)
        self.assertEqual(admin.list_roles(), [{"id": "role-01", "name": "组内阅读"}])
        plan = admin.plan_field("组内阅读", "论文链接", "no_perm")
        self.assertEqual(table.puts, [])
        table.fields.append({"field_id": "fld-5", "field_name": "新增列", "type": 1})
        with self.assertRaisesRegex(TableError, "变化"):
            admin.apply(plan)
        plan = admin.plan_field("组内阅读", "论文链接", "no_perm")
        admin.apply(plan)
        self.assertEqual(len(table.puts), 1)

        update = table.puts[0]
        self.assertEqual(set(update), {"role_name", "role_type", "table_rule_map"})
        self.assertEqual(set(update["table_rule_map"]), {"测试表"})
        rule = update["table_rule_map"]["测试表"]
        self.assertEqual(set(rule), {"field_rule"})
        self.assertEqual(rule["field_rule"]["field_perms"],
                         {"论文链接": "no_perm", "作者公式": "read", "标题": "edit", "新增列": "edit"})
        self.assertEqual(table.role["table_rule_map"]["其他表"], {"perm": "read_only"})
        self.assertTrue(table.role["base_rule_map"]["download"])
        self.assertFalse(table.role["table_rule_map"]["测试表"]["view_rule"]["allow_edit"])

    def test_other_table_change_invalidates_readback(self):
        table = FakeBase()
        table.alter_other_table = True
        admin = RoleAdmin(table)
        with patch("role_admin.time.sleep"), self.assertRaisesRegex(TableError, "回读未匹配"):
            admin.apply(admin.plan_rows("role-01", "own_only"))
        self.assertEqual(len(table.puts), 1)

    def test_rows_preserve_record_operations_and_creator_field(self):
        table = FakeBase()
        admin = RoleAdmin(table)
        for scope in ("own_only", "own_edit_all_read", "all_edit"):
            admin.apply(admin.plan_rows("role-01", scope))
            rule = table.puts[-1]["table_rule_map"]["测试表"]["record_rule"]
            self.assertEqual(rule["record_operations"], ["add"])
            self.assertEqual(rule["other_record_all_read"], scope != "own_only")
            self.assertIsNone(rule["read_filter_rule_group"])
            if scope == "all_edit":
                self.assertIsNone(rule["edit_filter_rule_group"])
            else:
                filters = rule["edit_filter_rule_group"]["filter_rules"]
                self.assertEqual(filters[0]["filters"], [{"field_name": "登记人", "operator": "contains"}])
            self.assertEqual(admin.inspect("role-01")["rows"], ROW_LABEL[scope])
        table.fields = table.fields[:-1]
        with self.assertRaisesRegex(TableError, "创建人"):
            admin.plan_rows("role-01", "own_only")

    def test_reject_unknown_config_or_disabled_base_and_check_readback(self):
        table = FakeBase()
        admin = RoleAdmin(table)
        table.enabled = False
        with self.assertRaisesRegex(TableError, "未启用"):
            admin.list_roles()
        table.enabled = True
        with self.assertRaisesRegex(TableError, "字段类型"):
            admin.plan_field("role-01", "作者公式", "edit")
        with self.assertRaisesRegex(TableError, "普通字段"):
            admin.plan_field("role-01", "登记人", "read")
        with self.assertRaisesRegex(TableError, "未唯一匹配"):
            admin.plan_rows("系统编辑者", "all_edit")
        table.role["table_rule_map"]["测试表"]["record_rule"]["edit_filter_rule_group"] = {"complex": True}
        with self.assertRaisesRegex(TableError, "定制行筛选"):
            admin.plan_rows("role-01", "own_only")
        table.no_apply = True
        with patch("role_admin.time.sleep"), self.assertRaisesRegex(TableError, "回读未匹配"):
            admin.apply(admin.plan_field("role-01", "论文链接", "read"))
        self.assertEqual(len(table.puts), 1)


if __name__ == "__main__":
    unittest.main()
