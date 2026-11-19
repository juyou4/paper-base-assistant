"""保留执行身份与飞书针对当前目标实际返回过的权限结果。"""

import contextvars
import hashlib
import json
import subprocess
from datetime import datetime, timezone


ACTIVE = contextvars.ContextVar("feishu_actor", default=None)
RIGHTS = {"read_records": "查询记录", "read_fields": "查询字段", "create_record": "新增记录", "edit_record": "更新记录",
          "create_field": "新增字段", "edit_field": "修改字段", "delete_field": "删除字段",
          "edit_member": "调整文件协作者", "edit_role": "调整高级角色", "create_table": "创建数据表", "create_base": "创建文件"}


def _key(*parts):
    return hashlib.sha256(json.dumps(parts).encode()).hexdigest()


def actor_for(table):
    from table_store import FeishuTable, LarkCliTable
    if not isinstance(table, FeishuTable):
        return {"key": "local", "kind": "local", "label": "本机工作台"}
    if not isinstance(table, LarkCliTable):
        app = getattr(table, "_app_id", "")
        return {"key": _key("app", app) if app else "", "kind": "app",
                "label": "飞书应用" + (" · …" + app[-6:] if app else "（身份待核实）")}
    try:
        response = subprocess.run([*table._command, "auth", "status", "--json"], input=b"", capture_output=True,
                                  timeout=6, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if response.returncode == 0:
            status = json.loads(response.stdout)
            info = status.get("data", status)
            user = info.get("identities", {}).get("user", {})
            app, who = info.get("appId"), user.get("openId")
            if app and who and user.get("available") is not False and user.get("status") == "ready":
                return {"key": _key("cli", info.get("brand"), app, who), "kind": "user",
                        "label": "飞书用户 · " + str(user.get("userName") or "当前账号")[:80] + "（…" + who[-6:] + "）"}
    except (OSError, ValueError, subprocess.SubprocessError, AttributeError, TypeError):
        pass
    return {"key": "", "kind": "user", "label": "飞书用户（当前登录身份待核实）"}


def access_kind(method, path):
    if "/records" in path:
        if method == "GET" or path.endswith(("/records/search", "/records/batch_get")):
            return "read_records"
        return {"POST": "create_record", "PUT": "edit_record", "PATCH": "edit_record"}.get(method)
    if "/fields" in path:
        return {"GET": "read_fields", "POST": "create_field", "PUT": "edit_field", "PATCH": "edit_field",
                "DELETE": "delete_field"}.get(method)
    if method != "GET":
        if "/permissions/" in path and "/members" in path:
            return "edit_member"
        if "/roles" in path:
            return "edit_role"
        if path.endswith("/tables"):
            return "create_table"
        if path == "/bitable/v1/apps":
            return "create_base"
    return None


def observe(table, kind, status, reason=""):
    actor = ACTIVE.get()
    if actor and actor["key"] and kind:
        with table._lock:
            if not hasattr(table, "_access_results"):
                table._access_results = {}
            table._access_results[(actor["key"], table._scope, kind)] = {
                "status": status, "reason": reason, "time": datetime.now(timezone.utc).isoformat(timespec="seconds")}


def connection(chat):
    actor = chat.execution_actor
    local = actor["kind"] == "local"
    results = getattr(chat.table, "_access_results", {})
    scope = getattr(chat.table, "_scope", "")
    with chat.table._lock:
        rights = [{"kind": kind, "label": label, **results.get((actor["key"], scope, kind),
                  {"status": "local" if local else "unknown"})} for kind, label in RIGHTS.items()]
    return {"actor": actor["label"], "kind": actor["kind"], "known": bool(actor["key"]), "table": chat.table_name,
            "base": chat.base_name or ("本地论文库" if local else chat.table.app_token or "尚未选择文件"),
            "table_id": getattr(chat.table, "table_id", ""), "rights": rights}


def confirmation(chat):
    from history_semantics import _action
    pending = chat.pending
    if not pending:
        return None
    action = _action(pending["stage"], pending["plan"])
    scopes = {"evidence_fill": "勾选论文的目标行和字段", "field_change": "当前数据表的字段结构",
              "member_permission": "整份多维表格文件的协作者权限", "role_permission": "高级角色在指定数据表的可见或可编辑范围",
              "create_table": "当前文件中的新数据表", "create_base": "新的多维表格文件"}
    if action not in scopes and pending["stage"] not in {"field", "english", "collection_choice"}:
        return None
    info = connection(chat)
    target = {k: info[k] for k in ("actor", "base", "table", "table_id")}
    plan = pending["plan"]
    if action == "create_base":
        target.update(base=plan["name"], table=plan["table_name"], table_id="")
    elif action == "create_table":
        target.update(table=plan["name"], table_id="")
    return {**target, "impact": scopes.get(action, "所选记录的目标字段")}
