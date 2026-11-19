"""查询和调整多维表格文档的现有个人协作者权限。"""

import hashlib
import re
import time
from urllib.parse import quote

from table_store import LarkCliTable, LocalTable, TableError, _TransientError


_OPEN_ID = re.compile(r"ou_[a-zA-Z0-9_-]{1,120}")
_EMAIL = re.compile(r"[a-zA-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[a-zA-Z0-9-]+(?:\.[a-zA-Z0-9-]+)+")
_EDITABLE_PERMS = {"view", "edit"}
DEMO_EMAIL = "reader@example.test"
DEMO_OPEN_ID = "ou_demo_reader"


def _target(value):
    target = value.strip() if isinstance(value, str) else ""
    if _OPEN_ID.fullmatch(target):
        return "openid", target
    if len(target) <= 254 and _EMAIL.fullmatch(target):
        return "email", target.casefold()
    raise TableError("请提供一位现有个人协作者的明确邮箱或完整 Open ID（ou_ 开头）。")


def _check_permission(value):
    if value not in _EDITABLE_PERMS:
        raise TableError("可选择的协作者权限为可查看（view）或可编辑（edit）。")


class CollaboratorAdmin:
    def __init__(self, table):
        if isinstance(table, LocalTable):
            table = table._root
        self.table = table
        self.local = isinstance(table, LocalTable)
        if self.local:
            with table._lock, table._db:
                table._db.execute("""CREATE TABLE IF NOT EXISTS demo_document_collaborators (
                    member_id TEXT PRIMARY KEY, email TEXT UNIQUE COLLATE NOCASE NOT NULL,
                    perm TEXT NOT NULL CHECK(perm IN ('view','edit','full_access'))
                )""")
                table._db.execute("INSERT OR IGNORE INTO demo_document_collaborators VALUES (?,?,?)",
                                  (DEMO_OPEN_ID, DEMO_EMAIL, "view"))
                filename = table._db.execute("PRAGMA database_list").fetchone()[2]
            scope = "local:" + (filename or str(id(table)))
        else:
            match = re.fullmatch(r"/bitable/v1/apps/([^/]+)/tables/[^/]+", table._base)
            if not match:
                raise TableError("当前连接缺少多维表格文档标识。")
            self.path = f"/drive/v1/permissions/{match[1]}/members"
            scope = "feishu:" + self.path
        self.scope = hashlib.sha256(scope.encode()).hexdigest()

    def _list(self):
        answer = self.table._request("GET", self.path, query={"type": "bitable", "fields": "type"})
        items = answer.get("data", {}).get("items")
        if not isinstance(items, list):
            raise TableError("飞书未返回可核验的协作者列表。")
        return [item for item in items if isinstance(item, dict) and item.get("type") in (None, "user")]

    def _open_id_for_email(self, email):
        if isinstance(self.table, LarkCliTable):
            raise TableError("协作者列表按 Open ID 返回。当前用户身份请提供此人的 ou_ 标识；"
                             "通过邮箱查询通讯录需要应用身份。")
        reply = self.table._request("POST", "/contact/v3/users/batch_get_id",
                                    body={"emails": [email], "include_resigned": False},
                                    query={"user_id_type": "open_id"})
        users = reply.get("data", {}).get("user_list")
        if not isinstance(users, list):
            raise TableError("飞书未返回可核验的邮箱查询结果。")
        ids = {user["user_id"] for user in users if isinstance(user, dict)
               and str(user.get("email", "")).casefold() == email
               and _OPEN_ID.fullmatch(str(user.get("user_id", "")))}
        if len(ids) != 1:
            raise TableError("该邮箱未唯一匹配飞书用户。请提供此人的完整 Open ID。")
        return ids.pop()

    def lookup(self, target):
        """只返回指定成员。姓名、群组和部门不能作为个人协作者目标。"""
        kind, identifier = _target(target)
        if self.local:
            column = "email" if kind == "email" else "member_id"
            with self.table._lock:
                record = self.table._db.execute(
                    f"SELECT member_id, perm FROM demo_document_collaborators WHERE {column}=?",
                    (identifier,)).fetchone()
            matches = [{"member_type": "openid", **dict(record)}] if record else []
        else:
            items = self._list()
            if kind == "email":
                matches = [m for m in items if m.get("member_type") == "email"
                           and str(m.get("member_id", "")).casefold() == identifier]
                if not matches:
                    identifier = self._open_id_for_email(identifier)
                    kind = "openid"
            if kind == "openid":
                matches = [m for m in items if m.get("member_type") == kind and m.get("member_id") == identifier]
        if not matches:
            raise TableError("未找到这位现有个人协作者。")
        if len(matches) != 1:
            raise TableError("同一成员对应多条权限记录。请在飞书分享面板核对。")
        member = matches[0]
        if member.get("perm") not in _EDITABLE_PERMS | {"full_access"}:
            raise TableError("服务器返回了无法确认的协作者权限。")
        return {"member_type": member["member_type"], "member_id": member["member_id"],
                "perm": member["perm"], "perm_type": member.get("perm_type") or ""}

    def plan(self, target, new_perm):
        """生成仅供当前文档与当前协作者使用的变更预览，不写权限。"""
        _check_permission(new_perm)
        member = self.lookup(target)
        _check_permission(member["perm"])
        return {"scope": self.scope, "target": target.strip(), "member_type": member["member_type"],
                "member_id": member["member_id"], "perm_type": member["perm_type"],
                "before": member["perm"], "after": new_perm}

    def _check_plan(self, plan):
        if not isinstance(plan, dict) or plan.get("scope") != self.scope:
            raise TableError("此确认不属于当前多维表格文档，请重新预览权限。")
        _check_permission(plan.get("before"))
        _check_permission(plan.get("after"))
        member = self.lookup(plan.get("target"))
        if (any(member[key] != plan.get(key) for key in ("member_id", "member_type", "perm_type"))
                or member["perm"] != plan["before"]):
            raise TableError("协作者身份或原权限在预览后已变化。请重新查询并确认。")
        return member

    def apply(self, plan):
        """在用户确认后，检查预览前提、更新权限并回读。"""
        with self.table._lock:
            member = self._check_plan(plan)
            if member["perm"] == plan["after"]:
                return member
            if self.local:
                with self.table._db:
                    changed = self.table._db.execute(
                        "UPDATE demo_document_collaborators SET perm=? WHERE member_id=? AND perm=?",
                        (plan["after"], member["member_id"], plan["before"])).rowcount
                    if changed != 1:
                        raise TableError("原权限已变化，请重新预览。")
                return self._read_back(member, plan["after"])
            body = {"member_type": member["member_type"], "perm": plan["after"]}
            if member["perm_type"]:
                body["perm_type"] = member["perm_type"]
            path = self.path + "/" + quote(member["member_id"], safe="")
            try:
                self.table._request("PUT", path, body=body, query={"type": "bitable", "need_notification": "false"})
            except TableError as exc:
                if not isinstance(exc.__cause__, _TransientError):
                    raise
                # 请求可能已生效，只回读，避免把另一位管理员刚调整的权限覆盖掉。
            return self._read_back(member, plan["after"])

    def _read_back(self, member, expected):
        for delay in (0, 0.2, 0.6):
            if delay:
                time.sleep(delay)
            current = self.lookup(member["member_id"])
            if (current["member_type"] == member["member_type"] and current["perm_type"] == member["perm_type"]
                    and current["perm"] == expected):
                return current
        raise TableError("权限请求已发送，回读尚未确认目标值。请重新查询协作者权限。")
