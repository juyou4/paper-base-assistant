"""离线验收三种表格后端的相同写入约定。"""

import io
import json
import sys
import tempfile
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from table_store import FeishuTable, LarkCliTable, LocalTable, TableError, RecordNotFound
from tools.feishu_setup import resolve_target, save_target


KEY = "arxiv:2005.11401"
LINK = "https://arxiv.org/abs/2005.11401"


class FakeFeishu:
    """模拟官方响应，不打开网络连接。"""

    def __init__(self):
        self.columns = [
            {"field_id": "link", "field_name": "论文链接", "type": 15},
            {"field_id": "method", "field_name": "方法概述", "type": 1},
            {"field_id": "formula", "field_name": "公式", "type": 20},
        ]
        self.records = {}
        self.tokens = {}
        self.posts = []
        self.drop_create_response = False
        self.stale_readback = False
        self.hide_rows = False

    def __call__(self, method, path, body=None, query=None):
        if path.endswith("/fields"):
            if method == "POST":
                field = {**body, "field_id": f"fld{len(self.columns)}"}
                self.columns.append(field)
                return {"data": {"field": field}}
            if (query or {}).get("page_token") == "next":
                return {"data": {"items": self.columns[1:], "has_more": False}}
            return {"data": {"items": self.columns[:1], "has_more": True, "page_token": "next"}}
        if path.endswith("/records") and method == "GET":
            rows = [] if self.hide_rows else [self.record(i) for i in self.records]
            return {"data": {"items": rows, "has_more": False}}
        if path.endswith("/records") and method == "POST":
            token = query["client_token"]
            if uuid.UUID(token).version != 4:
                raise AssertionError("新增请求需要 UUIDv4")
            self.posts.append(token)
            if token not in self.tokens:
                rid = f"rec{len(self.tokens) + 1}"
                self.tokens[token] = rid
                self.records[rid] = dict(body["fields"])
            if self.drop_create_response:
                self.drop_create_response = False
                raise TableError("模拟已落库但响应丢失")
            return {"data": {"record": self.record(self.tokens[token])}}
        rid = path.rsplit("/", 1)[-1]
        if rid not in self.records:
            raise RecordNotFound("模拟记录已手动删除")
        if method == "PUT":
            self.records[rid].update(body["fields"])
        row = self.record(rid)
        if self.stale_readback and method == "GET":
            row["fields"] = {}
        return {"data": {"record": row}}

    def record(self, rid):
        return {"record_id": rid, "fields": self.records[rid]}


class LocalTableTests(unittest.TestCase):
    def test_upsert_retry_updates_only_requested_fields(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/"demo.sqlite"
            table = LocalTable(path)
            try:
                first = table.upsert(KEY, {"论文链接": LINK}, "a")
                self.assertEqual(first, table.upsert(KEY, {"论文链接": LINK}, "a"))
                changed = table.upsert(KEY + "v4", {"方法概述": "方法"}, "b")
                self.assertEqual(changed, {"record_id": first["record_id"], "action": "updated"})
                self.assertEqual(table.upsert(KEY, {"方法概述": "方法"}, "c")["action"], "unchanged")
                with self.assertRaises(TableError):
                    table.upsert(KEY, {"方法概述": "另一个值"}, "b")
            finally:
                table._db.close()
            other = LocalTable(path)
            try:
                self.assertEqual(len(other.rows()), 1)
                self.assertEqual(other.get(first["record_id"])["fields"], {"论文链接": LINK, "方法概述": "方法"})
            finally:
                other._db.close()

    def test_add_field_types_and_concurrent_create(self):
        table = LocalTable(":memory:")
        self.addCleanup(table._db.close)
        col = table.add_field("项目主页", "url")
        self.assertEqual(table.add_field("项目主页", "url"), col)
        self.assertIn(col, table.schema())
        with self.assertRaises(TableError):
            table.add_field("项目主页", "text")
        with self.assertRaises(TableError):
            table.upsert(KEY, {"项目主页": "样例"}, "bad-url")
        with ThreadPoolExecutor(4) as pool:
            results = list(pool.map(lambda i: table.upsert(KEY, {"项目主页": LINK}, str(i)), range(6)))
        self.assertEqual(sum(row["action"] == "created" for row in results), 1)
        self.assertEqual(len(table.rows()), 1)


class FeishuTableTests(unittest.TestCase):
    def setUp(self):
        self.table = FeishuTable(":memory:", "app", "dummy", "base", "tbl")
        self.addCleanup(self.table._db.close)
        self.remote = FakeFeishu()

    def test_remote_existing_record_pagination_and_partial_update(self):
        self.remote.records["recA"] = {
            "论文链接": {"text": "链接", "link": "https://arxiv.org/pdf/2005.11401v4.pdf"},
            "方法概述": [{"type": "text", "text": "旧方法"}],
        }
        with patch.object(self.table, "_request", self.remote):
            self.assertEqual(self.table.schema()[-1]["type"], "readonly")
            self.assertEqual(self.table.upsert(KEY, {"方法概述": "新方法"}, "m"),
                             {"record_id": "recA", "action": "updated"})
            self.assertEqual(self.table.get("recA")["fields"]["论文链接"], "https://arxiv.org/pdf/2005.11401v4.pdf")
            self.assertEqual(self.remote.posts, [])
            added = self.table.add_field("原文链接", "url")
            self.assertEqual(self.table.add_field("原文链接", "url"), added)
            with self.assertRaises(TableError):
                self.table.add_field("公式", "text")

    def test_remote_retry_uses_same_token_and_verifies_saved_link(self):
        with patch.object(self.table, "_request", self.remote), patch("table_store.time.sleep"):
            self.remote.drop_create_response = True
            with self.assertRaises(TableError):
                self.table.upsert(KEY, {"论文链接": LINK}, "request")
            self.remote.stale_readback = True
            with self.assertRaisesRegex(TableError, "回读"):
                self.table.upsert(KEY, {"论文链接": LINK}, "request")
            self.remote.stale_readback = False
            result = self.table.upsert(KEY, {"论文链接": LINK}, "request")
            self.assertEqual(result["action"], "created")
            self.assertEqual(len(self.remote.posts), 2)
            self.assertEqual(len(set(self.remote.posts)), 1)
            self.assertEqual(len(self.remote.records), 1)

    def test_http_business_error_on_status_200(self):
        replies = [io.BytesIO(b'{"code":0,"tenant_access_token":"dummy","expire":7200}'),
                   io.BytesIO(b'{"code":1254041,"msg":"table not found"}')]
        with patch("table_store.urlopen", side_effect=replies), self.assertRaisesRegex(TableError, "1254041"):
            self.table.schema()

    def test_cli_read_retries_network_exit_and_server_wait_but_never_writes_or_permissions(self):
        cli = LarkCliTable(":memory:", sys.executable, "base", "tbl")
        self.addCleanup(cli.close)
        network = SimpleNamespace(returncode=4, stdout=b"", stderr=b"")
        limited = SimpleNamespace(returncode=1, stdout=b"",
            stderr=b'{"ok":false,"error":{"type":"api","subtype":"rate_limit","retryable":true,"retry_after_seconds":2}}')
        success = SimpleNamespace(returncode=0, stdout=b'{"ok":true,"data":{"items":[]}}', stderr=b"")
        with patch("table_store.subprocess.run", side_effect=[network, limited, success]) as run, patch("table_store.time.sleep") as sleep:
            self.assertEqual(cli._request("GET", "/fields")["data"]["items"], [])
            self.assertEqual(run.call_count, 3)
            self.assertEqual(sleep.call_args_list[-1].args, (2,))
        denied = SimpleNamespace(returncode=3, stdout=b"", stderr=b'{"ok":false,"error":{"type":"authorization","code":91403}}')
        protocol = SimpleNamespace(returncode=4, stdout=b"", stderr=b'{"ok":false,"error":{"type":"network","subtype":"protocol"}}')
        for method, failure in (("GET", denied), ("GET", protocol), ("POST", network), ("PUT", limited)):
            with self.subTest(method=method, failure=failure), patch("table_store.subprocess.run", return_value=failure) as run:
                with patch("table_store.time.sleep") as sleep, self.assertRaises(TableError):
                    cli._request(method, "/records")
                self.assertEqual(run.call_count, 1)
                sleep.assert_not_called()
        with patch("table_store.subprocess.run", return_value=network) as run, patch("table_store.time.sleep"):
            with self.assertRaisesRegex(TableError, "尝试 3 次"):
                cli._request("HEAD", "/fields")
            self.assertEqual(run.call_count, 3)

    def test_http_read_backoff_headers_and_long_wait(self):
        def throttled(seconds):
            return HTTPError("https://unused.invalid", 429, "Too Many Requests",
                {"Retry-After": "1", "x-ogw-ratelimit-reset": str(seconds)}, io.BytesIO(b'{"code":99991400}'))
        with patch("table_store.urlopen", side_effect=[throttled(2), io.BytesIO(b'{"code":0,"data":{}}')]) as send:
            with patch("table_store.time.sleep") as sleep:
                self.assertEqual(self.table._request("GET", "/fields", authenticated=False)["code"], 0)
                sleep.assert_called_once_with(2)
            self.assertEqual(send.call_count, 2)
        with patch("table_store.urlopen", side_effect=throttled(60)) as send, patch("table_store.time.sleep") as sleep:
            with self.assertRaisesRegex(TableError, "至少等待 60 秒"):
                self.table._request("GET", "/fields", authenticated=False)
            sleep.assert_not_called()
            self.assertEqual(send.call_count, 1)
        with patch("table_store.urlopen", side_effect=URLError("timeout")) as send, patch("table_store.time.sleep") as sleep:
            with self.assertRaisesRegex(TableError, "没有自动重发"):
                self.table._request("POST", "/fields", authenticated=False)
            sleep.assert_not_called()
            self.assertEqual(send.call_count, 1)

    def test_permission_denial_explains_operation_and_hides_target(self):
        cli = LarkCliTable(":memory:", sys.executable, "privateBaseToken", "tblX")
        self.addCleanup(cli.close)
        checks = [("GET", "/fields", "可见范围"), ("POST", "/fields", "字段管理权限"),
                  ("POST", "/records", "新增记录"), ("PUT", "/records/rec1", "更新记录")]
        for code in (91403, 1254302):
            for method, suffix, expected in checks:
                path = "/bitable/v1/apps/privateBaseToken/tables/tblX" + suffix
                with self.subTest(code=code, method=method, suffix=suffix):
                    raw = io.BytesIO(json.dumps({"code": code, "msg": "forbidden"}).encode())
                    with patch("table_store.urlopen", return_value=raw), self.assertRaises(TableError) as app_exc:
                        self.table._request(method, path, authenticated=False)
                    err = json.dumps({"ok": False, "error": {"code": code, "message": "privateBaseToken"}}).encode()
                    failed = SimpleNamespace(returncode=1, stdout=b"", stderr=err)
                    with patch("table_store.subprocess.run", return_value=failed), self.assertRaises(TableError) as cli_exc:
                        cli._request(method, path)
                    for error, who in ((app_exc.exception, "当前应用"), (cli_exc.exception, "当前登录用户")):
                        self.assertIn(str(code), str(error))
                        self.assertIn(expected, str(error))
                        self.assertIn(who, str(error))
                        self.assertNotIn("privateBaseToken", str(error))

    def test_new_request_after_lost_ack_recovers_persistent_create(self):
        self.remote.drop_create_response = True
        self.remote.hide_rows = True
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "journal.sqlite"
            table = FeishuTable(path, "app", "dummy", "base", "tbl")
            try:
                with patch.object(table, "_request", self.remote), self.assertRaises(TableError):
                    table.upsert(KEY, {"论文链接": LINK}, "first-request")
            finally:
                table.close()
            table = FeishuTable(path, "app", "dummy", "base", "tbl")
            try:
                with patch.object(table, "_request", self.remote):
                    saved = table.upsert(KEY, {"论文链接": LINK}, "new-request")
                    self.assertEqual(saved["action"], "unchanged")
                    self.assertEqual(table.upsert(KEY, {"方法概述": "后来添加"}, "third-request")["action"], "updated")
                    self.assertEqual(len(self.remote.records), 1)
                    self.assertEqual(len(self.remote.posts), 2)
                    self.assertEqual(len(set(self.remote.posts)), 1)
            finally:
                table.close()

    def test_pending_create_then_different_instruction_keeps_both_values(self):
        self.remote.drop_create_response = True
        self.remote.hide_rows = True
        with patch.object(self.table, "_request", self.remote):
            with self.assertRaises(TableError):
                self.table.upsert(KEY, {"论文链接": LINK}, "lost")
            changed = self.table.upsert(KEY, {"方法概述": "摘要以外的内容"}, "new")
            self.assertEqual(changed["action"], "updated")
            self.assertEqual(self.table.get(changed["record_id"])["fields"],
                             {"论文链接": LINK, "方法概述": "摘要以外的内容"})
            self.assertEqual(len(self.remote.records), 1)

    def test_update_only_does_not_recover_an_unseen_pending_create(self):
        self.remote.drop_create_response = True
        self.remote.hide_rows = True
        with patch.object(self.table, "_request", side_effect=self.remote) as send:
            with self.assertRaises(TableError):
                self.table.upsert(KEY, {"论文链接": LINK}, "pending")
            send.reset_mock()
            with self.assertRaises(RecordNotFound):
                self.table.upsert(KEY, {"方法概述": "需复现"}, "edit", update_only=True)
            self.assertTrue(all(call.args[0] == "GET" for call in send.call_args_list))
            self.remote.hide_rows = False
            change = self.table.upsert(KEY, {"方法概述": "需复现"}, "edit", update_only=True)
        self.assertEqual(change, {"action": "updated", "record_id": "rec1"})
        self.assertEqual(len(self.remote.posts), 1)

    def test_update_only_binds_remote_id_if_deleted_while_updating(self):
        self.remote.records["original"] = {"论文链接": {"link": LINK}}
        def delete_before_put(method, path, body=None, query=None):
            if method == "PUT":
                del self.remote.records["original"]
            return self.remote(method, path, body, query)
        with patch.object(self.table, "_request", side_effect=delete_before_put) as send:
            with self.assertRaises(RecordNotFound):
                self.table.upsert(KEY, {"方法概述": "需复现"}, "edit", update_only=True)
            mutations = [(c.args[0], c.args[1].split("/")[-1]) for c in send.call_args_list if c.args[0] != "GET"]
            self.assertEqual(mutations, [("PUT", "original")])
        self.assertEqual(self.remote.records, {})

    def test_nonstandard_link_fields_and_ambiguous_rows(self):
        self.remote.columns.extend([{"field_id": "src", "field_name": "出处", "type": 15},
                                    {"field_id": "alt", "field_name": "收录位置", "type": 15}])
        self.remote.records["x"] = {"出处": {"link": LINK}}
        self.remote.records["y"] = {"收录位置": {"link": LINK}}
        with patch.object(self.table, "_request", self.remote):
            with self.assertRaisesRegex(TableError, "多条"):
                self.table.upsert(KEY, {"方法概述": "方法"}, "duplicate")
            self.remote.records["y"]["论文链接"] = {"link": "https://arxiv.org/abs/2401.00001"}
            result = self.table.upsert(KEY, {"方法概述": "方法"}, "select")
            self.assertEqual(result["record_id"], "x")
            self.assertEqual(self.remote.posts, [])

    def test_manually_deleted_cached_id_recovers_and_auth_error_does_not_create(self):
        with patch.object(self.table, "_request", self.remote):
            created = self.table.upsert(KEY, {"论文链接": LINK}, "first")
            with patch.object(self.table, "get", side_effect=TableError("403")):
                with self.assertRaisesRegex(TableError, "403"):
                    self.table.upsert(KEY, {"方法概述": "方法"}, "denied")
            self.assertEqual(len(self.remote.posts), 1)
            del self.remote.records[created["record_id"]]
            replacement = self.table.upsert(KEY, {"论文链接": LINK}, "after-delete")
            self.assertEqual(replacement["action"], "created")
            self.assertNotEqual(created["record_id"], replacement["record_id"])
            self.assertEqual(len(self.remote.records), 1)

    def test_same_paper_title_does_not_merge_separate_identifiers(self):
        self.remote.columns.append({"field_id": "title", "field_name": "论文标题", "type": 1})
        with patch.object(self.table, "_request", self.remote):
            one = self.table.upsert(KEY, {"论文标题": "同名文章", "论文链接": LINK}, "1")
            other = "https://arxiv.org/abs/2301.01111"
            two = self.table.upsert(other, {"论文标题": "同名文章", "论文链接": other}, "2")
            self.assertNotEqual(one["record_id"], two["record_id"])
            self.assertEqual(len(self.remote.records), 2)

    def test_duplicate_field_name_and_slow_pagination_never_write(self):
        self.remote.columns.append({"field_id": "another", "field_name": "论文链接", "type": 1})
        with patch.object(self.table, "_request", self.remote):
            with self.assertRaisesRegex(TableError, "同名"):
                self.table.upsert(KEY, {"论文链接": LINK}, "duplicate-field")
            with patch("table_store.time.monotonic", side_effect=[0, 0, 31]), self.assertRaisesRegex(TableError, "耗时"):
                self.table.schema()
            self.assertEqual(self.remote.posts, [])

    def test_cli_uses_user_identity_json_stdin_and_envelope(self):
        table = LarkCliTable(":memory:", sys.executable, "base", "tbl")
        self.addCleanup(table._db.close)

        def run(args, **options):
            _, verb, method, path, *flags = args
            self.assertEqual(verb, "api")
            self.assertEqual(flags[:4], ["--as", "user", "--format", "json"])
            self.assertTrue(path.startswith("/open-apis/bitable/"))
            query = json.loads(flags[flags.index("--params") + 1]) if "--params" in flags else None
            body = json.loads(options["input"]) if options["input"] else None
            if body:
                self.assertEqual(flags[-2:], ["--data", "-"])
            data = self.remote(method, path, body, query)["data"]
            return SimpleNamespace(returncode=0, stdout=json.dumps({"ok": True, "data": data}).encode(), stderr=b"")

        with patch("table_store.subprocess.run", side_effect=run):
            self.assertEqual(table.add_field("实验", "text")["type"], "text")
            self.assertEqual(table.upsert(KEY, {"论文链接": LINK}, "c")["action"], "created")
            self.assertEqual(table.upsert(KEY, {"实验": "实验一"}, "u")["action"], "updated")
            self.assertEqual(len(table.rows()), 1)
            self.assertEqual(table.get("rec1")["fields"]["实验"], "实验一")
        failure = SimpleNamespace(returncode=1, stdout=b"", stderr=b'{"ok":false,"error":{"code":99991679}}')
        with patch("table_store.subprocess.run", return_value=failure), self.assertRaisesRegex(TableError, "99991679"):
            table.schema()


class TargetSetupTests(unittest.TestCase):
    def test_direct_base_url_only_records_target(self):
        with patch("table_store.urlopen", side_effect=AssertionError("直接链接无需联网")):
            target = resolve_target("https://test.feishu.cn/base/testToken?table=tblTarget", "app")
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "feishu-target.json"
            save_target(target, path)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")),
                             {"app_token": "testToken", "table_id": "tblTarget"})

    def test_wiki_requires_bitable_node_and_resolves_single_table(self):
        responses = [{"data": {"node": {"obj_type": "bitable", "obj_token": "testToken"}}},
                     {"data": {"items": [{"name": "阅读清单", "table_id": "tblTarget"}]}}]
        with patch.dict("os.environ", {"FEISHU_APP_ID": "dummy", "FEISHU_APP_SECRET": "dummy"}):
            with patch.object(FeishuTable, "_request", side_effect=responses) as req:
                target = resolve_target("https://test.feishu.cn/wiki/testNode", "app")
                self.assertEqual(target["table_id"], "tblTarget")
                self.assertEqual(target["table_name"], "阅读清单")
                self.assertTrue(all(call.args[0] == "GET" for call in req.call_args_list))
            with patch.object(FeishuTable, "_request", return_value={"data": {"node": {"obj_type": "docx"}}}):
                with self.assertRaises(TableError):
                    resolve_target("https://test.feishu.cn/wiki/testNode", "app")


if __name__ == "__main__":
    unittest.main()
