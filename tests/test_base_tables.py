"""多维表格内的建表、唯一回执、切表及旧阅读清单兼容。"""

import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from table_store import FeishuTable, LarkCliTable, LocalTable, TableError, _RejectedChange


FIELDS = [{"name": "论文标题", "type": "text"}, {"name": "论文链接", "type": "url"}]


class LocalBaseTablesTests(unittest.TestCase):
    def test_old_table_persists_and_child_writes_are_isolated(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "existing.sqlite3"
            # 模拟升级前只存在字段与论文表的数据库。
            db = sqlite3.connect(path)
            with db:
                db.execute("CREATE TABLE demo_columns(id TEXT PRIMARY KEY,name TEXT UNIQUE,type TEXT)")
                db.execute("INSERT INTO demo_columns VALUES ('title','历史标题','text')")
                db.execute("CREATE TABLE demo_papers(id TEXT PRIMARY KEY,paper_key TEXT UNIQUE,cells TEXT)")
                db.execute("""INSERT INTO demo_papers VALUES ('old','old','{"历史标题":"已读"}')""")
            db.close()
            root = LocalTable(path)
            try:
                child = root.create_table("会议论文", FIELDS, "req-1")
                receipt = child.upsert("demo", {"论文标题": "A"}, "record-1")
                sibling = child.create_table("研讨班", FIELDS, "req-2")
                sibling.upsert("demo", {"论文标题": "B"}, "record-1")
                self.assertEqual(child.get(receipt["record_id"])["fields"]["论文标题"], "A")
                self.assertEqual(root.rows()[0]["id"], "old")
                self.assertIs(root, child.open_table(root.table_id))
                self.assertIs(child, root.create_table("会议论文", FIELDS, "req-1"))
                with self.assertRaisesRegex(TableError, "已有"):
                    root.create_table("会议论文", FIELDS, "different-id")
                with self.assertRaisesRegex(TableError, "其他建表"):
                    root.create_table("另一张", FIELDS, "req-1")
                table_id = child.table_id
            finally:
                root.close()
            reopened = LocalTable(path)
            try:
                self.assertEqual(len(reopened.list_tables()), 3)
                self.assertEqual(reopened.open_table(table_id).rows()[0]["fields"]["论文标题"], "A")
                self.assertEqual(reopened.schema()[0]["name"], "历史标题")
            finally:
                reopened.close()

    def test_one_name_one_table_under_concurrent_confirmations(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "tables.sqlite3"
            roots = [LocalTable(path), LocalTable(path)]
            ids, errors = [], []
            def run(store):
                try:
                    ids.append(store.create_table("项目", FIELDS, "same-id").table_id)
                except Exception as err:
                    errors.append(str(err))
            jobs = [threading.Thread(target=run, args=(root,)) for root in roots]
            try:
                for job in jobs:
                    job.start()
                for job in jobs:
                    job.join()
                self.assertEqual(errors, [])
                self.assertEqual(len(ids), 2)
                self.assertEqual(*ids)
                self.assertEqual(len(roots[0].list_tables()), 2)
            finally:
                for root in roots:
                    root.close()

    def test_new_base_primary_field_and_invalid_plan(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "new.db"
            root = LocalTable.new_base(path, "研究资料", FIELDS)
            self.assertEqual(root.list_tables(), [{"id":root.table_id, "name":"研究资料"}])
            self.assertEqual(root.schema()[0]["id"], "title")
            for bad in ([], FIELDS + FIELDS, [{"name":"链接", "type":"url"}]):
                with self.assertRaises(TableError):
                    root.create_table("新表", bad, "invalid")
            root.close()
            with self.assertRaises(TableError):
                LocalTable.new_base(path, "不同", FIELDS)

    def test_missing_previously_created_table_does_not_become_an_empty_table(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "records.db"
            root = LocalTable(path)
            try:
                child = root.create_table("阅读笔记", FIELDS, "req")
                child_path, child_id = Path(child._db_path), child.table_id
            finally:
                root.close()
            child_path.unlink()
            root = LocalTable(path)
            try:
                with self.assertRaisesRegex(TableError, "恢复存储目录"):
                    root.open_table(child_id)
                self.assertFalse(child_path.exists())
            finally:
                root.close()


class FeishuBaseTablesTests(unittest.TestCase):
    def setUp(self):
        self.table = FeishuTable(":memory:", "app", "secret", "baseA", "tblOriginal")
        self.addCleanup(self.table.close)
        self.tables = [{"name":"原表", "table_id":"tblOriginal"}]
        self.fields = [
            {"field_id":"f2", "field_name":"论文链接", "is_primary":False, "type":15},
            {"field_id":"f1", "field_name":"论文标题", "is_primary":True, "type":1},
        ]
        self.commands = []
        def request(method, path, body=None, query=None):
            if method == "POST":
                self.commands.append((path, body))
                self.tables.append({"name":body["table"]["name"], "table_id":"tblCreated"})
                return {"data":{"table_id":"tblCreated","field_id_list":["f1","f2"]}}
            return {"data":{"items": self.fields if path.endswith("/fields") else self.tables}}
        self.table._request = Mock(side_effect=request)

    def test_create_uses_single_native_request_and_readback_by_id(self):
        created = self.table.create_table("新表", FIELDS, "id")
        self.assertEqual(created.table_id, "tblCreated")
        self.assertEqual(self.commands, [("/bitable/v1/apps/baseA/tables",{"table":{"name":"新表","fields":[
            {"field_name":"论文标题","type":1},{"field_name":"论文链接","type":15}]}})])
        self.assertNotEqual(self.table._scope, created._scope)
        self.assertEqual(self.table.table_id, "tblOriginal")
        self.assertEqual(self.table.create_table("新表",FIELDS,"id").table_id, created.table_id)
        self.assertEqual(len(self.commands),1)
        with self.assertRaisesRegex(TableError, "已有"):
            self.table.create_table("新表", FIELDS, "another")
        self.fields[0]["type"] = 1
        with self.assertRaisesRegex(TableError,"不一致"):
            self.table.create_table("新表",FIELDS,"id")
        self.assertEqual(len(self.commands),1)
        self.assertNotEqual(created.for_base("baseB").open_table("tblCreated")._scope, created._scope)

    def test_lost_receipt_and_permission_denial(self):
        def fail(method, path, body=None, query=None):
            if method == "POST":
                raise TableError("网络断开")
            return {"data":{"items":[]}}
        self.table._request = Mock(side_effect=fail)
        with self.assertRaisesRegex(TableError,"网络"):
            self.table.create_table("甲",FIELDS,"id")
        with self.assertRaisesRegex(TableError,"未取得确定回执"):
            self.table.create_table("甲",FIELDS,"id")
        with self.assertRaisesRegex(TableError,"待核对"):
            self.table.create_table("甲",FIELDS,"new-id")
        def denied(method, path, body=None, query=None):
            if method == "POST":
                raise _RejectedChange("权限不足")
            return {"data":{"items":[]}}
        self.table._request = Mock(side_effect=denied)
        for _ in range(2):
            with self.assertRaisesRegex(TableError,"权限不足"):
                self.table.create_table("乙",FIELDS,"permission-test")
        self.assertEqual(sum(c.args[0]=="POST" for c in self.table._request.call_args_list),2)

    def test_concurrent_confirmations_hold_name_until_readback(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "receipts.db"
            first = FeishuTable(path, "app", "secret", "baseA", "tblOriginal")
            second = FeishuTable(path, "app", "secret", "baseA", "tblOriginal")
            verifying, release = threading.Event(), threading.Event()
            results = []

            def remote(method, path, body=None, query=None):
                if path.endswith("/fields"):
                    verifying.set()
                    if not release.wait(3):
                        raise AssertionError("回读未被释放")
                return self.table._request(method, path, body, query)

            def create_first():
                try:
                    results.append(first.create_table("研究进展", FIELDS, "req-a").table_id)
                except Exception as error:
                    results.append(str(error))

            first._request = remote
            second._request = Mock(return_value={"data": {"items": []}})
            worker = threading.Thread(target=create_first)
            worker.start()
            try:
                self.assertTrue(verifying.wait(3))
                with self.assertRaisesRegex(TableError, "待核对"):
                    second.create_table("研究进展", FIELDS, "req-b")
                second._request.assert_not_called()
            finally:
                release.set()
                worker.join()
                first.close()
                second.close()
            self.assertEqual(results, ["tblCreated"])
            self.assertEqual(len(self.commands), 1)

    def test_failed_identity_check_does_not_consume_confirmation(self):
        table = FeishuTable(":memory:", "app", "secret", "baseA", "tblOriginal")
        self.addCleanup(table.close)
        with patch.object(table, "list_tables", return_value=[]), patch(
                "table_store.urlopen", side_effect=OSError("offline")) as send:
            for _ in range(2):
                with self.assertRaisesRegex(TableError, "业务请求尚未发出"):
                    table.create_table("新表", FIELDS, "same-confirmation")
            self.assertEqual(send.call_count, 2)
            self.assertTrue(all("/auth/" in call.args[0].full_url for call in send.call_args_list))

    def test_unbound_identity_and_native_cli_scope(self):
        with self.assertRaisesRegex(TableError, "不完整"):
            FeishuTable(":memory:", "app", "secret", "token", "")
        import sys
        cli = LarkCliTable(":memory:", sys.executable, "", "")
        self.addCleanup(cli.close)
        with self.assertRaisesRegex(TableError, "未绑定"):
            cli.list_tables()
        child = cli.for_base("created")
        self.assertIsInstance(child, LarkCliTable)
        self.assertEqual(child._command, cli._command)
        self.assertEqual(child.app_token, "created")
        child.close()
        self.assertTrue(cli._db.execute("SELECT 1").fetchone())
