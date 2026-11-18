"""同一张本地表的字段定义、记录值与角色授权必须一起变化。"""

import json
import sqlite3
import unittest
from copy import deepcopy
from pathlib import Path

from role_admin import RoleAdmin
from schema_admin import commit_field_change, plan_field_change
from table_store import LocalTable, TableError
from workflow import Conversation


class SchemaRoleIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.table = LocalTable(":memory:")
        self.addCleanup(self.table.close)
        self.roles = RoleAdmin(self.table)
        self.role = self.roles.list_roles()[0]["name"]
        self.roles.apply(self.roles.plan_field(self.role, "研究笔记", "no_perm"))
        self.talk = Conversation(self.table, Path("."), self.table.name)

    def _role_json(self):
        return json.loads(self.table._db.execute("SELECT body FROM demo_roles WHERE id='rol-local-review'").fetchone()[0])

    def _store(self, detail):
        with self.table._db:
            self.table._db.execute("INSERT OR REPLACE INTO demo_roles VALUES (?,?)",
                (detail["role_id"], json.dumps(detail, ensure_ascii=False, sort_keys=True, separators=(",", ":"))))

    def _confirm(self, command, response):
        draft = self.talk.respond(command)
        self.assertIn(response, draft["choices"])
        return self.talk.respond(response, draft["pending_id"])

    def test_same_conversation_preserves_all_roles_and_other_tables(self):
        second = deepcopy(self._role_json())
        second.update(role_id="rol-second", role_name="合作者")
        second["table_rule_map"]["其他表"] = {"perm": "edit", "field_rule": {"field_perm_mode": "all_edit"}}
        second["table_rule_map"][self.table.name]["field_rule"]["field_perms"]["研究笔记"] = "edit"
        self._store(second)
        unrelated = deepcopy(second)
        unrelated.update(role_id="rol-unrelated", role_name="外组")
        del unrelated["table_rule_map"][self.table.name]
        self._store(unrelated)
        old_unrelated = self.table._db.execute("SELECT body FROM demo_roles WHERE id='rol-unrelated'").fetchone()[0]
        self._confirm("新建文本列 跟进记录", "确认建列")
        for role in (self.role, "合作者"):
            self.assertEqual(self.roles.inspect(role)["fields"]["跟进记录"], "隐藏")
        self._confirm("把字段「研究笔记」改名为「复现实验」", "确认变更")
        self.assertEqual(self.roles.inspect(self.role)["fields"]["复现实验"], "隐藏")
        self.assertEqual(self.roles.inspect("合作者")["fields"]["复现实验"], "可编辑")
        self._confirm("删除字段「复现实验」", "确认变更")
        for role in (self.role, "合作者"):
            state = self.roles.inspect(role)["fields"]
            self.assertEqual(state["论文链接"], "可查看")
            self.assertNotIn("研究笔记", state)
            self.assertNotIn("复现实验", state)
        updated = self.table._db.execute("SELECT body FROM demo_roles WHERE id='rol-second'").fetchone()[0]
        self.assertEqual(json.loads(updated)["table_rule_map"]["其他表"], second["table_rule_map"]["其他表"])
        self.assertEqual(self.table._db.execute("SELECT body FROM demo_roles WHERE id='rol-unrelated'").fetchone()[0],
                         old_unrelated)

    def test_role_changes_between_preview_and_commit_require_new_confirmation(self):
        change = plan_field_change(self.table, "research", "rename", new_name="复现实验")
        self.assertEqual(change["affected_role_count"], 1)
        self.roles.apply(self.roles.plan_field(self.role, "研究笔记", "edit"))
        with self.assertRaisesRegex(TableError, "变化"):
            commit_field_change(self.table, change)
        self.assertEqual(self.roles.inspect(self.role)["fields"]["研究笔记"], "可编辑")

    def test_dependent_row_filters_and_extra_grants_block_schema_mutation(self):
        detail = self._role_json()
        local_rule = detail["table_rule_map"][self.table.name]
        local_rule["record_rule"]["edit_filter_rule_group"] = {"filter_rules": [
            {"filters": [{"field_name": "研究笔记", "operator": "contains"}]}]}
        self._store(detail)
        for action, kwargs in (("delete", {}), ("rename", {"new_name":"复现实验"}),
                               ("change_type", {"new_type":"url"})):
            with self.assertRaisesRegex(TableError, "行筛选或额外规则"):
                plan_field_change(self.table, "research", action, **kwargs)
        del local_rule["record_rule"]["edit_filter_rule_group"]
        local_rule["field_rule"]["allow_edit_and_download_file_fields"] = ["研究笔记"]
        self._store(detail)
        with self.assertRaisesRegex(TableError, "行筛选或额外规则"):
            plan_field_change(self.table, "research", "delete")
        with self.assertRaisesRegex(TableError, "系统字段"):
            self.table.add_field("创建人", "text")

    def test_rollback_includes_schema_cells_and_permissions(self):
        rid = self.table.upsert("arxiv:2005.11401", {"研究笔记":"原值"}, "first")["record_id"]
        change = plan_field_change(self.table, "research", "delete")
        self.table._db.execute("""CREATE TRIGGER test_reject_role BEFORE UPDATE ON demo_roles
            BEGIN SELECT RAISE(ABORT,'模拟权限保存失败'); END""")
        with self.assertRaises(sqlite3.IntegrityError):
            commit_field_change(self.table, change)
        with self.assertRaises(sqlite3.IntegrityError):
            self.table.add_field("未提交字段", "text")
        self.assertEqual(self.table.get(rid)["fields"]["研究笔记"], "原值")
        self.assertEqual(self.roles.inspect(self.role)["fields"]["研究笔记"], "隐藏")
        self.assertNotIn("未提交字段", [f["name"] for f in self.table.schema()])


if __name__ == "__main__":
    unittest.main()
