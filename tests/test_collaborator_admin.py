"""用虚构成员和模拟飞书响应检查协作者权限流程。"""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from collaborator_admin import CollaboratorAdmin, DEMO_EMAIL, DEMO_OPEN_ID
from table_store import FeishuTable, LarkCliTable, LocalTable, TableError, _TransientError


class LocalCollaboratorsTests(unittest.TestCase):
    def test_preview_readback_and_reopen(self):
        with TemporaryDirectory() as directory:
            filename = Path(directory) / "demo.db"
            table = LocalTable(filename)
            try:
                service = CollaboratorAdmin(table)
                preview = service.plan(DEMO_EMAIL.upper(), "edit")
                self.assertEqual(service.lookup(DEMO_OPEN_ID)["perm"], "view")
                self.assertEqual((preview["before"], preview["after"]), ("view", "edit"))
                self.assertEqual(service.apply(preview)["perm"], "edit")
                self.assertEqual(len(table.rows()), 0)
            finally:
                table.close()
            table = LocalTable(filename)
            try:
                service = CollaboratorAdmin(table)
                self.assertEqual(service.lookup(DEMO_EMAIL)["perm"], "edit")
                self.assertEqual(service.apply(service.plan(DEMO_EMAIL, "view"))["perm"], "view")
            finally:
                table.close()

    def test_require_exact_existing_member_and_stale_plan(self):
        table = LocalTable(":memory:")
        self.addCleanup(table.close)
        service = CollaboratorAdmin(table)
        for target in ("张三", "unknown@example.test", "ou_other", "reader@example.test,other@example.test"):
            with self.subTest(target=target), self.assertRaises(TableError):
                service.plan(target, "edit")
        valid = service.plan(DEMO_EMAIL, "edit")
        for edited in ({**valid, "scope": "other"}, {**valid, "after": "full_access"},
                       {**valid, "member_id": "ou_another"}):
            with self.assertRaises(TableError):
                service.apply(edited)
        service.apply(valid)
        with self.assertRaisesRegex(TableError, "已变化"):
            service.apply(valid)


class RemoteCollaboratorsTests(unittest.TestCase):
    def setUp(self):
        self.table = FeishuTable(":memory:", "test-app", "test-secret", "fake/base", "fake-table")
        self.addCleanup(self.table.close)
        self.rows = [{"member_type": "openid", "member_id": DEMO_OPEN_ID, "type": "user", "perm": "view"}]
        self.calls = []
        self.path = "/drive/v1/permissions/fake%2Fbase/members"
        self.table._request = self.request
        self.admin = CollaboratorAdmin(self.table)

    def request(self, method, path, body=None, query=None):
        self.calls.append((method, path, body, query))
        if method == "GET":
            self.assertEqual((path, query), (self.path, {"type": "bitable", "fields": "type"}))
            return {"data": {"items": [dict(m) for m in self.rows]}}
        if method == "POST":
            self.assertEqual(path, "/contact/v3/users/batch_get_id")
            self.assertEqual(query, {"user_id_type": "open_id"})
            return {"data": {"user_list": [{"email": body["emails"][0], "user_id": DEMO_OPEN_ID}]}}
        self.assertEqual(method, "PUT")
        self.assertEqual(query, {"type": "bitable", "need_notification": "false"})
        self.assertEqual(path, self.path + "/" + DEMO_OPEN_ID)
        self.rows[0]["perm"] = body["perm"]
        return {"data": {"member": dict(self.rows[0])}}

    def test_resolve_email_and_assert_put_then_separate_get(self):
        plan = self.admin.plan(DEMO_EMAIL, "edit")
        self.assertFalse(any(c[0] == "PUT" for c in self.calls))
        actual = self.admin.apply(plan)
        self.assertEqual((actual["member_id"], actual["perm"]), (DEMO_OPEN_ID, "edit"))
        self.assertEqual([c[0] for c in self.calls], ["GET", "POST", "GET", "POST", "PUT", "GET"])
        self.assertEqual(self.calls[-2][2], {"member_type": "openid", "perm": "edit"})

    def test_duplicate_or_non_user_permissions_are_never_updated(self):
        self.rows.append(dict(self.rows[0]))
        with self.assertRaisesRegex(TableError, "多条"):
            self.admin.plan(DEMO_OPEN_ID, "edit")
        self.rows[:] = [{**self.rows[0], "type": "chat"}]
        with self.assertRaisesRegex(TableError, "未找到"):
            self.admin.plan(DEMO_OPEN_ID, "edit")
        self.assertFalse(any(c[0] == "PUT" for c in self.calls))

    def test_changed_permission_stops_before_put(self):
        plan = self.admin.plan(DEMO_OPEN_ID, "edit")
        self.rows[0]["perm"] = "full_access"
        with self.assertRaisesRegex(TableError, "已变化"):
            self.admin.apply(plan)
        self.assertFalse(any(c[0] == "PUT" for c in self.calls))

    def test_direct_email_member_and_unchanged_role_do_not_issue_writes(self):
        self.rows = [{"member_type": "email", "member_id": "Reader@Example.test", "perm": "view"}]
        member = self.admin.apply(self.admin.plan("reader@example.test", "view"))
        self.assertEqual(member["member_id"], "Reader@Example.test")
        self.assertEqual([c[0] for c in self.calls], ["GET", "GET"])

    def test_timeout_after_remote_commit_only_reads_back(self):
        def drop_reply(method, path, body=None, query=None):
            answer = self.request(method, path, body, query)
            if method == "PUT":
                raise TableError("远端响应超时") from _TransientError("超时")
            return answer
        self.table._request = drop_reply
        actual = self.admin.apply(self.admin.plan(DEMO_OPEN_ID, "edit"))
        self.assertEqual(actual["perm"], "edit")
        self.assertEqual([c[0] for c in self.calls].count("PUT"), 1)

    @patch("collaborator_admin.time.sleep")
    def test_update_acknowledgement_alone_is_not_success(self, _):
        def no_change(method, path, body=None, query=None):
            if method == "PUT":
                return {"data": {"member": {**self.rows[0], "perm": "edit"}}}
            return self.request(method, path, body, query)
        self.table._request = no_change
        with self.assertRaisesRegex(TableError, "回读尚未确认"):
            self.admin.apply(self.admin.plan(DEMO_OPEN_ID, "edit"))

    def test_cli_user_identity_never_calls_tenant_only_contact_api(self):
        cli = object.__new__(LarkCliTable)
        cli._base = self.table._base
        cli._request = Mock(side_effect=self.request)
        with self.assertRaisesRegex(TableError, "应用身份"):
            CollaboratorAdmin(cli).lookup(DEMO_EMAIL)
        self.assertEqual(cli._request.call_count, 1)


if __name__ == "__main__":
    unittest.main()
