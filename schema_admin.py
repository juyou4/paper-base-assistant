"""按字段 ID 预览和确认列变更。飞书与本地复用同一计划约定。"""

import hashlib
import hmac
import json
import secrets
import time
import unicodedata
from contextlib import nullcontext
from urllib.parse import quote

from table_store import FeishuTable, LocalTable, TableError


_SIGNING_KEY = secrets.token_bytes(32)
_TYPES = {"text": (1, "Text"), "url": (15, "Url")}
_KNOWN_REMOTE = {"field_id", "field_name", "type", "ui_type", "property", "description",
                 "is_primary", "is_hidden", "is_extend", "is_synced"}


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _fields(table):
    if isinstance(table, LocalTable):
        return [(dict(field, primary=field["id"] == "title",
                      protected=field["id"] == "title" or field["type"] not in _TYPES), {})
                for field in table.schema()]
    if not isinstance(table, FeishuTable):
        raise TableError("该数据源尚不支持字段管理。")
    raw_fields = table._items("/fields")
    primary_ids = {f["field_id"] for f in raw_fields if f.get("is_primary") is True}
    if len(primary_ids) > 1:
        raise TableError("飞书的主列信息不一致，请重新读取字段。")
    result, ids, names = [], {}, set()
    for raw in raw_fields:
        field_id, name = raw["field_id"], raw["field_name"]
        if field_id in ids and ids[field_id] == raw:
            continue
        if name in names or field_id in ids:
            raise TableError("飞书返回的字段 ID 或名称重复，请核对表结构。")
        ids[field_id] = raw
        names.add(name)
        kind = {1: "text", 15: "url", 19: "readonly", 20: "readonly",
                1001: "readonly", 1002: "readonly", 1003: "readonly", 1004: "readonly",
                1005: "readonly"}.get(raw["type"], "unsupported")
        main = field_id in primary_ids
        unknown_primary = not primary_ids and raw.get("is_primary") is not False
        ui = raw.get("ui_type")
        plain = kind in _TYPES and (not ui or ui == _TYPES[kind][1])
        external = any(raw.get(key) not in (None, False) for key in ("is_extend", "is_synced"))
        protected = main or unknown_primary or not plain or bool(raw.get("is_hidden")) or external
        result.append(({"id": field_id, "name": name, "type": kind, "primary": main, "protected": protected}, raw))
    return result


def _select(fields, field_id):
    for field, raw in fields:
        if field["id"] == field_id:
            return field, raw
    raise TableError("目标字段已不存在，请重新读取表结构。")


def inspect_field(table, field_id=None):
    """没有 ID 时列出字段；提供 ID 时返回单个字段。"""
    with table._lock:
        fields = _fields(table)
        return _select(fields, field_id)[0] if field_id is not None else [f for f, _ in fields]


def _remote_body(raw, action, new_name, new_type):
    if action == "delete":
        return None
    # PUT 可能覆盖字段定义。遇到未知配置时交回飞书处理，避免在序列化中丢失属性。
    if any(k not in _KNOWN_REMOTE and v not in (None, "", [], {}) for k, v in raw.items()):
        raise TableError("这列含有当前版本无法保留的飞书配置，请在飞书中管理该列。")
    body = {"field_name": new_name, "type": _TYPES[new_type][0]}
    if raw.get("ui_type"):
        body["ui_type"] = _TYPES[new_type][1]
    prop = raw.get("property")
    if prop is not None:
        if not isinstance(prop, dict) or (action == "change_type" and prop):
            raise TableError("该字段含有类型专属设置，无法保证转换后保留，请先在飞书核对。")
        body["property"] = prop
    desc = raw.get("description")
    if desc is not None:
        if isinstance(desc, str):
            desc = {"text": desc}
        if not isinstance(desc, dict) or set(desc) - {"text", "disable_sync"}:
            raise TableError("字段说明含有暂不支持的格式，请在飞书中修改。")
        body["description"] = desc
    return body


def _validate_change(fields, field, action, new_name, new_type):
    if field["protected"]:
        raise TableError("主列、公式/系统列、同步或扩展列、隐藏列及无法确认类型的列受到保护，不能变更。")
    if not isinstance(action, str) or action not in {"rename", "change_type", "delete"}:
        raise TableError("请选择改名、改类型或删除字段。")
    if action == "rename":
        if (not isinstance(new_name, str) or not new_name or new_name != new_name.strip()
                or any(unicodedata.category(c) == "Cc" for c in new_name)):
            raise TableError("请提供有效的新列名。")
        if new_name == field["name"]:
            raise TableError("新列名与原名相同。")
        normalized = unicodedata.normalize("NFKC", new_name).casefold()
        if any(f["id"] != field["id"] and unicodedata.normalize("NFKC", f["name"]).casefold() == normalized
               for f, _ in fields):
            raise TableError("目标列名已经存在，请换一个名称。")
        if new_type is not None:
            raise TableError("请将改名和改类型分开确认。")
    elif new_name is not None:
        raise TableError("该操作不应提供新列名。")
    if action == "change_type":
        if not isinstance(new_type, str) or new_type not in _TYPES or new_type == field["type"]:
            raise TableError("请选择不同于原类型的文本或超链接类型。")
    elif new_type is not None:
        raise TableError("该操作不应提供新类型。")


def _prepare(table, field_id, action, new_name, new_type):
    fields = _fields(table)
    field, raw = _select(fields, field_id)
    _validate_change(fields, field, action, new_name, new_type)
    local = isinstance(table, LocalTable)
    if not local:
        pending_writes = table._db.execute("SELECT cells FROM fs_pending_creates WHERE scope=?", (table._scope,))
        if any(field["name"] in json.loads(row["cells"]) for row in pending_writes):
            raise TableError("有一条尚未回读确认的写入正在使用本列，请先重试原写入。")
    final_name, final_type = new_name or field["name"], new_type or field["type"]
    body = ({"role_changes": table._role_changes(action, field["name"], new_name, field_id)}
            if local else _remote_body(raw, action, final_name, final_type))
    rows = table.rows() if local else table._items("/records")
    seen = {}
    for row in rows:
        rid = row["id"] if local else row["record_id"]
        cells = row.get("fields") or {}
        if action == "rename" and new_name in cells:
            raise TableError("新列名下已有未映射的数据，请先核对，避免覆盖。")
        value = cells.get(field["name"])
        if rid in seen and seen[rid] != value:
            raise TableError("读取过程中有记录发生改变，请重新生成预览。")
        seen[rid] = value
    affected = sorted(rid for rid, cell in seen.items() if cell not in (None, "", [], {}))
    if action == "change_type" and affected:
        sample = "、".join(affected[:3])
        raise TableError(f"「{field['name']}」有 {len(affected)} 条已填记录（如 {sample}）。仅允许空列调整类型。")
    plan = {"action": action, "field_id": field_id, "old_name": field["name"], "old_type": field["type"],
            "new_name": new_name, "new_type": new_type, "total_rows": len(seen),
            "affected_count": len(affected), "affected_record_ids": affected[:10],
            "impact_scope": "本地全部记录" if local else "飞书当前调用身份可见的记录"}
    scope = str(id(table)) if local else table._scope
    role_changes = body["role_changes"] if local else []
    plan["affected_role_count"] = len(role_changes) if local else None
    plan["snapshot"] = hmac.digest(_SIGNING_KEY, _json([scope, field, raw, plan, seen, role_changes]).encode(), hashlib.sha256).hex()
    return plan, body


def plan_field_change(table, field_id, action, new_name=None, new_type=None):
    """返回待用户确认的计划，包含列定义、影响数量、部分记录 ID 和状态签名。"""
    with table._lock:
        return _prepare(table, field_id, action, new_name, new_type)[0]


def _apply_local(table, plan):
    action, fid = plan["action"], plan["field_id"]
    new, old = plan["new_name"], plan["old_name"]
    if action == "change_type":
        table._db.execute("UPDATE demo_columns SET type=? WHERE id=?", (plan["new_type"], fid))
    else:
        for record in table.rows():
            cells = record["fields"]
            if old in cells:
                value = cells.pop(old)
                if action == "rename":
                    cells[new] = value
                table._db.execute("UPDATE demo_papers SET cells=? WHERE id=?", (_json(cells), record["id"]))
        if action == "rename":
            table._db.execute("UPDATE demo_columns SET name=? WHERE id=?", (new, fid))
        else:
            table._db.execute("DELETE FROM demo_columns WHERE id=?", (fid,))


def _verified(table, plan, body):
    expected = (plan["new_name"] or plan["old_name"], plan["new_type"] or plan["old_type"])
    for delay in ((0,) if isinstance(table, LocalTable) else (0, 0.35, 1)):
        if delay:
            time.sleep(delay)
        actual, raw = next(((f, r) for f, r in _fields(table) if f["id"] == plan["field_id"]), (None, {}))
        if plan["action"] == "delete" and actual is None:
            return None
        if actual and plan["action"] != "delete" and (actual["name"], actual["type"]) == expected:
            if body and body.get("property") and raw.get("property") != body["property"]:
                continue
            if body and body.get("description"):
                desc = raw.get("description")
                desc = {"text": desc} if isinstance(desc, str) else (desc or {})
                if not isinstance(desc, dict) or any(desc.get(k, False if k == "disable_sync" else "") != v
                                                     for k, v in body["description"].items()):
                    continue
            return actual
    raise TableError("字段变更已提交，回读未匹配预期状态。请刷新表结构核对后再发起操作。")


def commit_field_change(table, plan):
    """仅接受当前进程的预览。重新读到的字段或列内数据发生变化时停止。"""
    if not isinstance(plan, dict) or not {"field_id", "action", "snapshot"}.issubset(plan):
        raise TableError("字段计划无效，请重新预览。")
    local = isinstance(table, LocalTable)
    with table._lock, (table._db if local else nullcontext()):
        if local:
            table._db.execute("BEGIN IMMEDIATE")
        now, body = _prepare(table, plan["field_id"], plan["action"], plan.get("new_name"), plan.get("new_type"))
        if now != plan:
            raise TableError("字段定义、影响记录或确认内容已变化，请重新预览。")
        action = plan["action"]
        if local:
            _apply_local(table, plan)
            table._save_role_changes(body["role_changes"])
        else:
            path = table._base + "/fields/" + quote(plan["field_id"], safe="")
            table._request("DELETE" if action == "delete" else "PUT", path, body)
        result = _verified(table, plan, body)
        return {"action": action, "field_id": plan["field_id"], "field": result, "affected_count": plan["affected_count"]}
