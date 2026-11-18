import tempfile
import unittest
from unittest.mock import patch

from collaborator_admin import CollaboratorAdmin
from table_store import LocalTable, TableError
from workflow import Conversation


class ManagementConversationTests(unittest.TestCase):
    def setUp(self):
        self.table = LocalTable(":memory:")
        self.addCleanup(self.table.close)
        workspace = tempfile.TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        self.talk = Conversation(self.table, workspace.name, "阅读清单")

    def test_field_crud_requires_the_corresponding_confirmation(self):
        names = lambda: {f["name"] for f in self.table.schema()}
        first = self.talk.respond("新建文本列 临时笔记")
        self.talk.respond("确认建列", first["pending_id"])
        self.assertIn("临时笔记", self.talk.respond("查看字段")["message"])
        change = self.talk.respond("把字段「临时笔记」改名为「来源备份」")
        self.assertIn("临时笔记", names())
        self.talk.respond("确认变更", change["pending_id"])
        self.assertIn("来源备份", names())
        change = self.talk.respond("把字段「来源备份」的类型改为超链接")
        self.talk.respond("确认变更", change["pending_id"])
        self.assertIn("超链接", self.talk.respond("查看字段「来源备份」")["message"])
        delete = self.talk.respond("删除字段「来源备份」")
        self.talk.respond("取消", delete["pending_id"])
        self.assertIn("来源备份", names())
        delete = self.talk.respond("删除字段「来源备份」")
        self.talk.respond("确认变更", delete["pending_id"])
        self.assertNotIn("来源备份", names())

    def test_ambiguous_deletion_selects_field_then_previews(self):
        ambiguous = self.talk.respond("删除字段笔记")
        self.assertCountEqual(ambiguous["choices"], ["研究笔记", "阅读笔记"])
        chosen = self.talk.respond("研究笔记", ambiguous["pending_id"])
        self.assertIn("研究笔记", chosen["message"])
        self.assertEqual(6, len(self.table.schema()))
        self.talk.respond("取消", chosen["pending_id"])
        self.assertEqual(6, len(self.table.schema()))

    def test_column_name_and_cell_value_disambiguation(self):
        saved = self.talk.respond("保存 arXiv:2005.11401 的论文链接")
        self.talk.respond("arXiv:2005.11401，把『保留此笔记』写进阅读笔记")
        ambiguous = self.talk.respond("把「研究笔记」改成「需要复现」")
        self.assertIn("填写一条记录", ambiguous["choices"])
        follow = self.talk.respond("填写一条记录", ambiguous["pending_id"])
        result = self.talk.respond("刚才那篇", follow["pending_id"])
        self.assertEqual(result["row"]["id"], saved["row"]["id"])
        self.assertEqual(result["row"]["fields"],
                         {**saved["row"]["fields"], "研究笔记": "需要复现", "阅读笔记": "保留此笔记"})
        self.assertEqual(len(self.table.rows()), 1)
        self.assertIn("研究笔记", [f["name"] for f in self.table.schema()])

    def test_edit_missing_paper_reads_only_then_accepts_a_visible_record(self):
        saved = self.talk.respond("保存 arXiv:2005.11401 的论文链接")
        before = self.table.rows()
        statements = []
        self.table._db.set_trace_callback(statements.append)
        with patch("papers.resolve_paper", side_effect=AssertionError("修改单元格只需论文标识")):
            ambiguous = self.talk.respond("把『研究笔记』改成『需要复现』")
            follow = self.talk.respond("填写一条记录", ambiguous["pending_id"])
            missing = self.talk.respond("https://arxiv.org/abs/2499.99999", follow["pending_id"])
            self.assertIn("未找到", missing["message"])
            self.assertEqual(self.table.rows(), before)
            self.assertFalse(any(sql.startswith(("INSERT", "REPLACE", "DELETE", "UPDATE")) for sql in statements))
            retry = self.talk.respond("https://arxiv.org/pdf/2005.11401v2", missing["pending_id"])
        self.assertEqual(retry["row"]["id"], saved["row"]["id"])
        self.assertEqual(self.table.rows()[0]["fields"]["研究笔记"], "需要复现")

    def test_collaborator_changes_are_document_wide_and_bound_to_plan(self):
        target = "reader@example.test"
        self.assertIn("可查看", self.talk.respond(f"查看 {target} 的协作者权限")["message"])
        proposal = self.talk.respond(f"把 {target} 的协作者权限改为可编辑")
        self.assertIn("整份多维表格", proposal["message"])
        with self.assertRaisesRegex(ValueError, "对应"):
            self.talk.respond("确认变更")
        self.assertEqual(CollaboratorAdmin(self.table).lookup(target)["perm"], "view")
        self.talk.respond("确认变更", proposal["pending_id"])
        self.assertEqual(CollaboratorAdmin(self.table).lookup(target)["perm"], "edit")

    def test_role_field_and_record_scope(self):
        self.assertIn("论文审阅人", self.talk.respond("列出自定义角色")["message"])
        for instruction in ("把角色「论文审阅人」对字段「研究笔记」的权限改为隐藏",
                            "将角色「论文审阅人」的行权限设为全部可读、仅本人创建可编辑"):
            proposal = self.talk.respond(instruction)
            self.assertIn("确认变更", proposal["choices"])
            self.talk.respond("确认变更", proposal["pending_id"])
        self.assertTrue(self.talk.respond("查看角色「论文审阅人」的权限")["message"])
