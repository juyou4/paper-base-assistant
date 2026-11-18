"""用正常对话入口验证普通单篇补写在飞书外部编辑后仍能辨认目标。"""

import os
import tempfile
import unittest
from copy import deepcopy
from unittest.mock import patch

import papers
from table_store import FeishuTable, TableError
from test_table_store import FakeFeishu, LINK
from workflow import Conversation


class SinglePaperIdentityTests(unittest.TestCase):
    def setUp(self):
        self.remote = FakeFeishu()
        self.remote.columns.append({"field_id": "title", "field_name": "论文标题", "type": 1})
        self.table = FeishuTable(":memory:", "dummy", "dummy", "base", "tbl")
        self.addCleanup(self.table.close)
        http = patch.object(self.table, "_request", side_effect=self.remote)
        self.http = http.start()
        self.addCleanup(http.stop)
        provider = patch.dict(os.environ, {"PAPER_AGENT_PROVIDER": "rules"})
        provider.start()
        self.addCleanup(provider.stop)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.chat = Conversation(self.table, folder.name, "阅读清单")
        self.transformer = "https://arxiv.org/abs/1706.03762"

    def save(self, message=f"把 {LINK} 存进阅读清单"):
        self.rid = self.chat.respond(message)["row"]["id"]
        self.http.reset_mock()

    def assert_followup_is_blocked(self):
        before = deepcopy(self.remote.records)
        try:
            reply = self.chat.respond("给刚才那篇补一句方法概述")
        except (TableError, ValueError) as error:
            self.assertTrue(str(error).strip())
        else:
            self.assertTrue(reply.get("message"))
            self.assertNotIn("verification", reply)
            self.assertNotIn("row", reply)
        self.assertEqual(self.remote.records, before)
        self.assertTrue(all(call.args[0] == "GET" for call in self.http.call_args_list))

    def test_method_is_not_written_after_row_was_repurposed(self):
        self.save()
        row = self.remote.records[self.rid]
        row["论文标题"] = "Attention Is All You Need"
        row["论文链接"] = {"text": self.transformer, "link": self.transformer}
        self.assert_followup_is_blocked()

    def test_cached_title_cannot_override_a_changed_paper_link(self):
        self.save()
        self.remote.records[self.rid]["论文链接"] = {"text": "原文", "link": self.transformer}
        self.assert_followup_is_blocked()

    def test_cached_id_alone_cannot_identify_a_row_with_cleared_link_and_title(self):
        self.save()
        self.remote.records[self.rid].pop("论文链接")
        self.remote.records[self.rid].pop("论文标题")
        self.assert_followup_is_blocked()

    def test_second_record_with_same_link_makes_followup_ambiguous(self):
        self.save()
        self.remote.records["rec2"] = {"论文链接": {"text": "RAG", "link": LINK}, "方法概述": "另一行已有内容"}
        self.assert_followup_is_blocked()

    def test_equivalent_versioned_pdf_link_still_identifies_the_same_paper(self):
        self.save()
        paper_link = {"text": "PDF", "link": "https://arxiv.org/pdf/2005.11401v4.pdf"}
        self.remote.records[self.rid]["论文链接"] = paper_link.copy()
        reply = self.chat.respond("给刚才那篇补一句方法概述")
        self.assertEqual(reply["row"]["id"], self.rid)
        self.assertEqual(reply["row"]["fields"]["方法概述"], papers.SAMPLE["method"])
        self.assertEqual(self.remote.records[self.rid]["论文链接"], paper_link)
        self.assertEqual(len(self.remote.records), 1)

    def test_method_is_written_to_the_same_paper_with_other_rows_preserved(self):
        self.save()
        other = {"论文标题": "Attention Is All You Need", "论文链接": {"text": "原文", "link": self.transformer},
                 "方法概述": "已有笔记"}
        self.remote.records["rec2"] = deepcopy(other)
        original = deepcopy(self.remote.records[self.rid])
        reply = self.chat.respond("给刚才那篇补一句方法概述")
        self.assertEqual(reply["row"]["id"], self.rid)
        self.assertIn("回读一致", reply["verification"])
        self.assertEqual(self.remote.records, {
            self.rid: {**original, "方法概述": papers.SAMPLE["method"]}, "rec2": other,
        })

    def test_user_selected_text_link_column_follows_rename_but_not_replacement(self):
        column = self.remote.columns[0]
        column.update(field_name="资料入口", type=1)
        self.save(f"把 {LINK} 的链接写进资料入口")
        row = self.remote.records[self.rid]
        self.assertEqual(row["资料入口"], LINK)

        # 飞书改列名会同步更换返回的单元格键，字段 ID 保持不变。
        column["field_name"] = "来源入口"
        row["来源入口"] = row.pop("资料入口")
        reply = self.chat.respond("给刚才那篇补一句方法概述")
        self.assertEqual(reply["row"]["id"], self.rid)
        self.assertEqual(row["方法概述"], papers.SAMPLE["method"])

        row.update(来源入口=self.transformer, 方法概述="另一篇已有的方法")
        self.http.reset_mock()
        self.assert_followup_is_blocked()

    def test_legacy_record_id_needs_current_paper_identity(self):
        self.save()
        # 旧数据库只有 fs_keys 映射，升级后新增的来源字段表对旧记录为空。
        with self.table._db:
            self.table._db.execute("DELETE FROM fs_key_sources")
        row = self.remote.records[self.rid]
        old_link = row["论文链接"]
        row["论文链接"] = {"text": "原文", "link": self.transformer}
        self.assert_followup_is_blocked()

        row["论文链接"] = old_link
        reply = self.chat.respond("给刚才那篇补一句方法概述")
        self.assertEqual(reply["row"]["id"], self.rid)
        self.assertEqual(row["方法概述"], papers.SAMPLE["method"])

    def test_interrupted_update_and_completed_request_recheck_the_current_identity(self):
        self.save()
        message = "给刚才那篇补一句方法概述"
        row = self.remote.records[self.rid]
        def interrupt(method, path, body=None, query=None):
            if method == "PUT":
                raise TableError("模拟写入超时")
            return self.remote(method, path, body, query)

        self.http.side_effect = interrupt
        with self.assertRaisesRegex(TableError, "超时"):
            self.chat.respond(message, request_id="retry-method")
        self.http.side_effect = self.remote
        for completed in (False, True):
            with self.subTest(completed=completed):
                if completed:
                    row["论文链接"] = {"link": LINK}
                    self.chat.respond(message, request_id="retry-method")
                row["论文链接"] = {"link": self.transformer}
                before = deepcopy(row)
                self.http.reset_mock()
                with self.assertRaises(TableError):
                    self.chat.respond(message, request_id="retry-method")
                self.assertEqual(row, before)
                self.assertTrue(all(call.args[0] == "GET" for call in self.http.call_args_list))

    def test_final_readback_checks_identity_even_if_written_method_still_matches(self):
        self.save()
        upsert = self.table.upsert
        def repurpose_after_write(*args, **kwargs):
            receipt = upsert(*args, **kwargs)
            self.remote.records[self.rid]["论文链接"] = {"link": self.transformer}
            return receipt

        with patch.object(self.table, "upsert", side_effect=repurpose_after_write), self.assertRaises(TableError):
            self.chat.respond("给刚才那篇补一句方法概述")
        self.assertEqual(self.remote.records[self.rid]["方法概述"], papers.SAMPLE["method"])

    def test_duplicate_custom_text_link_after_field_rename_stops_update(self):
        column = self.remote.columns[0]
        column.update(field_name="资料入口", type=1)
        self.save(f"把 {LINK} 的链接写进资料入口")
        column["field_name"] = "归档来源"
        row = self.remote.records[self.rid]
        row["归档来源"] = row.pop("资料入口")
        self.remote.records["another"] = {"归档来源": LINK}
        self.assert_followup_is_blocked()

    def test_legacy_row_cannot_be_identified_by_citation_when_primary_link_is_cleared(self):
        self.save()
        with self.table._db:
            self.table._db.execute("DELETE FROM fs_key_sources")
        self.remote.columns.append({"field_id": "ref", "field_name": "参考文献", "type": 15})
        row = self.remote.records[self.rid]
        row["参考文献"] = row.pop("论文链接")
        self.assert_followup_is_blocked()

    def test_title_only_record_can_be_updated_until_its_verified_title_changes(self):
        self.save(f"把 {LINK} 的标题填入论文标题")
        row = self.remote.records[self.rid]
        self.assertFalse(row.get("论文链接"))
        reply = self.chat.respond("给刚才那篇补一句方法概述")
        self.assertEqual(reply["row"]["id"], self.rid)
        row.update(论文标题="另一篇论文", 方法概述="保留原值")
        self.http.reset_mock()
        self.assert_followup_is_blocked()


if __name__ == "__main__":
    unittest.main()
