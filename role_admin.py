"""飞书 Base v3 自定义角色权限：预览、重新核对、增量提交与回读。"""

import hashlib
import json
import time
from copy import deepcopy
from urllib.parse import quote

from table_store import LocalTable, TableError


FIELD_LABEL = {"read": "可查看", "edit": "可编辑", "no_perm": "隐藏", "create": "可新增"}
ROW_LABEL = {"all_edit": "全部记录可读写", "own_edit_all_read": "全部可读，仅本人创建的可编辑",
             "own_only": "仅本人创建的记录可读写"}
BASE_MODE = {"all_edit": "edit", "all_read": "read", "no_perm": "no_perm"}
SYSTEM_TYPES = {1001, 1002, 1003, 1004}
READONLY_TYPES = {19, 20, 1005}


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _payload(value, allow_empty=False):
    """官方 v3 接口还可能返回嵌套的业务信封或字符串形式的 JSON。"""
    for _ in range(6):
        if allow_empty and value in ("", None):
            return {}
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except ValueError as error:
                raise TableError("飞书返回的角色配置无法解析。") from error
        elif isinstance(value, dict):
            if value.get("code", 0) not in (0, "0"):
                raise TableError(f"飞书拒绝了角色操作（业务码 {value['code']}），请检查管理员权限。")
            if "data" not in value:
                return value
            value = value["data"]
        else:
            break
    raise TableError("当前飞书接口未返回支持的高级权限格式。")


def _empty(group):
    return group is None or group == {} or (isinstance(group, dict) and
            group.get("filter_rules") == [] and set(group) <= {"conjunction", "filter_rules"})


def _own_group(name):
    return {"conjunction": "and", "filter_rules": [{"conjunction": "and", "filters": [
        {"field_name": name, "operator": "contains"}]}]}


def _is_own(group, name):
    if not name or not isinstance(group, dict) or set(group) - {"conjunction", "filter_rules"}:
        return False
    rules = group.get("filter_rules", [])
    if not isinstance(rules, list) or len(rules) != 1 or not isinstance(rules[0], dict):
        return False
    if set(rules[0]) - {"conjunction", "filters"}:
        return False
    filters = rules[0].get("filters", [])
    if not isinstance(filters, list) or len(filters) != 1 or not isinstance(filters[0], dict):
        return False
    cond = filters[0]
    if set(cond) - {"field_name", "operator", "filter_values", "field_type", "reference_type", "field_ui_type", "is_invalid"}:
        return False
    return (cond.get("field_name") == name and cond.get("operator") == "contains"
            and not cond.get("filter_values") and not cond.get("is_invalid"))


def _rows_mode(rule, creator):
    if not isinstance(rule, dict) or not isinstance(rule.get("other_record_all_read"), bool):
        return "custom"
    edit = rule.get("edit_filter_rule_group")
    extra = rule.get("read_filter_rule_group")
    if _empty(edit) and (rule["other_record_all_read"] or _empty(extra)):
        return "all_edit"
    if _is_own(edit, creator):
        if rule["other_record_all_read"]:
            return "own_edit_all_read"
        if _empty(extra) or _is_own(extra, creator):
            return "own_only"
    return "custom"


def _field_values(rule, fields):
    block = rule.get("field_rule", {})
    if not isinstance(block, dict):
        raise TableError("当前角色的字段权限格式无法可靠展开。")
    mode = block.get("field_perm_mode")
    adjustable = [f for f in fields if f["type"] not in SYSTEM_TYPES]
    if mode == "specify":
        values = block.get("field_perms")
        if (not isinstance(values, dict) or set(values) != {f["name"] for f in adjustable}
                or any(value not in FIELD_LABEL for value in values.values())):
            raise TableError("角色的逐列权限不完整或包含未知规则，无法可靠保留其他列。请先在飞书核对。")
        return dict(values)
    if mode not in BASE_MODE:
        raise TableError("当前角色的字段权限模式无法可靠展开。")
    default = BASE_MODE[mode]
    return {f["name"]: "read" if default == "edit" and f["type"] in READONLY_TYPES else default for f in adjustable}


class RoleAdmin:
    def __init__(self, table, table_name=None):
        self.table = table
        self.local = isinstance(table, LocalTable)
        if self.local:
            self.name = table.name
        else:
            parts = table._base.split("/")
            if len(parts) != 7 or parts[1:4] != ["bitable", "v1", "apps"] or parts[5] != "tables":
                raise TableError("未找到已绑定的飞书数据表。")
            self.app_path = "/".join(parts[:5])
            self.path = "/base/v3/bases/" + parts[4] + "/roles"
            self.table_id = parts[6]

    def _init_local(self):
        seed = {"role_id": "rol-local-review", "role_name": "论文审阅人", "role_type": "custom_role",
                "base_rule_map": {"copy": False, "download": False},
                "table_rule_map": {self.name: {"perm": "edit", "field_rule": {"field_perm_mode": "all_read"},
                    "record_rule": {"record_operations": [], "other_record_all_read": True},
                    "view_rule": {"allow_edit": False, "visibility": {"all_visible": True}}}}}
        with self.table._lock, self.table._db:
            self.table._db.execute("CREATE TABLE IF NOT EXISTS demo_roles (id TEXT PRIMARY KEY, body TEXT NOT NULL)")
            self.table._db.execute("INSERT OR IGNORE INTO demo_roles VALUES (?,?)", (seed["role_id"], _json(seed)))

    def _enabled(self):
        if self.local:
            self._init_local()
        else:
            meta = _payload(self.table._request("GET", self.app_path)).get("app", {})
            if meta.get("is_advanced") is not True:
                raise TableError("目标多维表格尚未启用高级权限，或无法确认其状态。请由表管理员先在飞书中核对。")

    def list_roles(self):
        self._enabled()
        if self.local:
            with self.table._lock:
                records = [json.loads(r[0]) for r in self.table._db.execute("SELECT body FROM demo_roles ORDER BY id")]
        else:
            listing = _payload(self.table._request("GET", self.path))
            records = listing.get("base_roles")
            if not isinstance(records, list) or listing.get("has_more"):
                raise TableError("未取得完整的 Base v3 自定义角色列表。")
        roles = []
        for item in records:
            role = _payload(item)
            if not self.local and "role_type" not in role and role.get("role_id"):
                role = _payload(self.table._request("GET", self.path + "/" + quote(role["role_id"], safe="")))
            if role.get("role_type") != "custom_role":
                continue
            if not role.get("role_id") or not role.get("role_name"):
                raise TableError("自定义角色缺少名称或 ID。")
            roles.append({"id": role["role_id"], "name": role["role_name"]})
        return roles

    def _target(self):
        if self.local:
            fields = [{"id": f["id"], "name": f["name"], "type": {"text": 1, "url": 15, "readonly": 20}.get(f["type"], -1)}
                      for f in self.table.schema()]
            return self.name, fields + [{"id": "local-creator", "name": "创建人", "type": 1003}]
        query, seen = {"page_size": 100}, set()
        while True:
            page = _payload(self.table._request("GET", self.app_path + "/tables", query=query))
            target = next((r for r in page.get("items", []) if r.get("table_id") == self.table_id), None)
            if target:
                break
            token = page.get("page_token")
            if not page.get("has_more") or not token or token in seen:
                raise TableError("没有找到当前绑定数据表的真实名称。")
            seen.add(token)
            query = {"page_size": 100, "page_token": token}
        fields = [{"id": f["field_id"], "name": f["field_name"], "type": f["type"]} for f in self.table._items("/fields")]
        return target["name"], fields

    def _state(self, role_key):
        candidates = [r for r in self.list_roles() if role_key in (r["id"], r["name"])]
        if len(candidates) != 1:
            raise TableError("未唯一匹配自定义角色。请先查看角色列表；表管理员可在飞书「高级权限」中新增自定义角色并设置当前表权限。")
        role_id = candidates[0]["id"]
        if self.local:
            with self.table._lock:
                row = self.table._db.execute("SELECT body FROM demo_roles WHERE id=?", (role_id,)).fetchone()
            role = json.loads(row[0])
        else:
            role = _payload(self.table._request("GET", self.path + "/" + quote(role_id, safe="")))
        if role.get("role_type") != "custom_role" or role.get("role_id") != role_id:
            raise TableError("返回的角色类型或 ID 与计划不符。")
        name, fields = self._target()
        if len({f["name"] for f in fields}) != len(fields):
            raise TableError("当前数据表有重名列，无法按名称安全配置权限。")
        rules = role.get("table_rule_map")
        rule = rules.get(name) if isinstance(rules, dict) else None
        if not isinstance(rule, dict):
            raise TableError("该自定义角色尚未配置当前表。请在飞书高级权限中为此角色设置此表，并重新预览。")
        return role, name, fields, rule

    def inspect(self, role):
        return self._describe(*self._state(role))

    @staticmethod
    def _describe(detail, name, fields, rule):
        creator = next((f["name"] for f in fields if f["type"] == 1003), "")
        perm = rule.get("perm")
        rows = _rows_mode(rule.get("record_rule"), creator) if perm == "edit" else "custom"
        labels = {"edit": "可编辑", "read_only": "可阅读", "no_perm": "不可访问", "manage": "可管理"}
        field_rules = _field_values(rule, fields) if perm in {"read_only", "edit"} else {}
        row_label = ROW_LABEL.get(rows, "依角色已有表级或自定义筛选规则")
        if perm == "read_only" and (rule.get("record_rule") or {}).get("other_record_all_read") is True:
            row_label = "全部记录可读"
        field_labels = {k: FIELD_LABEL[v] for k, v in field_rules.items()}
        summary = f"角色「{detail['role_name']}」· {name}：表权限{labels.get(perm, '未知')}，记录范围为{row_label}。"
        if field_labels:
            summary += " 各列：" + "，".join(f"{k} {v}" for k, v in field_labels.items()) + "。"
        return {"role": detail["role_name"], "table": name, "fields": field_labels, "rows": row_label,
                "table_permission": labels.get(perm, "未知"), "summary": summary}

    def plan_field(self, role, field, permission):
        return self._plan(role, "field", field, permission)[0]

    def plan_rows(self, role, scope):
        return self._plan(role, "rows", "", scope)[0]

    def _plan(self, role_key, kind, field, to):
        detail, name, fields, rule = self._state(role_key)
        if rule.get("perm") not in {"edit", "read_only"}:
            raise TableError("当前角色没有可调节的表权限。本操作不会提升、降低或创建表级权限。")
        if kind == "field":
            if to not in {"edit", "read", "no_perm"}:
                raise TableError("字段权限可选可编辑、可查看或隐藏。")
            values = _field_values(rule, fields)
            column = next((f for f in fields if f["name"] == field), None)
            if not column or field not in values:
                raise TableError("请选择当前表的一个普通字段。系统字段无法单独调权。")
            if to == "edit" and (rule["perm"] != "edit" or column["type"] in READONLY_TYPES or column["type"] == -1):
                raise TableError("当前表的角色上限或字段类型不支持可编辑。")
            record_rule = rule.get("record_rule") or {}
            used_in_filter = any(f.get("field_name") == field for group in
                    ("edit_filter_rule_group", "read_filter_rule_group")
                    for g in (record_rule.get(group) or {}).get("filter_rules", [])
                    for f in g.get("filters", []))
            if used_in_filter and (to == "no_perm" or (to == "read" and "add" in (record_rule.get("record_operations") or []))):
                raise TableError("当前记录筛选依赖这个字段，所选权限与已有行规则冲突。")
            if to != "edit" and any(field in rule["field_rule"].get(key, []) for key in
                    ("allow_edit_and_modify_option_fields", "allow_edit_and_download_file_fields")):
                raise TableError("当前字段另有选项或附件操作授权，需先在飞书核对关联影响。")
            before = FIELD_LABEL[values[field]]
            after = FIELD_LABEL[to]
            values[field] = to
            change = {"field_rule": {**deepcopy(rule["field_rule"]), "field_perm_mode": "specify", "field_perms": values}}
            impact = f"仅修改当前角色的「{field}」列"
            if before != after and rule["field_rule"]["field_perm_mode"] != "specify":
                impact += "，同时将本表切换为逐列配置，保留其他现有列的权限"
        elif kind == "rows" and to in ROW_LABEL:
            if rule["perm"] != "edit":
                raise TableError("当前表的角色上限是只读，无法授予可编辑记录范围。")
            creators = [f["name"] for f in fields if f["type"] == 1003]
            creator = creators[0] if len(creators) == 1 else ""
            if to != "all_edit" and not creator:
                raise TableError("设置仅本人创建需要当前表具有可唯一定位的「创建人」系统类型字段，请在飞书中核对。")
            mode = _rows_mode(rule.get("record_rule"), creator)
            if mode == "custom":
                raise TableError("角色带有其他定制行筛选，当前版本无法完整呈现替换影响，请在飞书中管理。")
            before, after = ROW_LABEL[mode], ROW_LABEL[to]
            group = None if to == "all_edit" else _own_group(creator)
            record = deepcopy(rule["record_rule"])
            record.update(edit_filter_rule_group=group, other_record_all_read=to != "own_only",
                          read_filter_rule_group=None)
            change = {"record_rule": record}
            impact = "修改当前角色成员在本表可读取及编辑的记录范围"
        else:
            raise TableError("不支持的角色权限调整。")
        binding = "local" if self.local else self.table._base
        snapshot = [binding, detail, name, sorted(fields, key=lambda f: f["id"])]
        digest = hashlib.sha256(_json(snapshot).encode()).hexdigest()
        plan = {"role": detail["role_name"], "role_id": detail["role_id"], "table": name, "kind": kind,
                "field": field, "target": to, "before": before, "after": after, "fingerprint": digest,
                "summary": f"角色「{detail['role_name']}」· {name}：{impact}，从「{before}」调整为「{after}」。"}
        return plan, change, detail

    def apply(self, plan):
        with self.table._lock:
            fresh, change, detail = self._plan(plan["role_id"], plan["kind"], plan["field"], plan["target"])
            if fresh != plan:
                raise TableError("确认前表结构、角色或权限已变化，请重新预览。")
            if fresh["before"] == fresh["after"]:
                return {**fresh, "summary": "该角色已经具备所请求的权限，本次无需修改。"}
            name, role_id = fresh["table"], fresh["role_id"]
            original = deepcopy(detail)
            if self.local:
                old = _json(detail)
                detail["table_rule_map"][name].update(change)
                with self.table._db:
                    saved = self.table._db.execute("UPDATE demo_roles SET body=? WHERE id=? AND body=?",
                                                    (_json(detail), role_id, old))
                    if saved.rowcount != 1:
                        raise TableError("角色在提交时发生变化，请重新预览。")
            else:
                body = {"role_name": fresh["role"], "role_type": "custom_role", "table_rule_map": {name: change}}
                response = self.table._request("PUT", self.path + "/" + quote(role_id, safe=""), body)
                _payload(response, allow_empty=True)
            for delay in (0, 0.35, 1):
                if delay:
                    time.sleep(delay)
                current, current_name, columns, current_rule = self._state(role_id)
                saved = self._describe(current, current_name, columns, current_rule)
                actual = saved["fields"].get(fresh["field"]) if fresh["kind"] == "field" else saved["rows"]
                all_fields_ok = (fresh["kind"] != "field" or saved["fields"] ==
                                 {k: FIELD_LABEL[v] for k, v in change["field_rule"]["field_perms"].items()})
                if actual == fresh["after"] and all_fields_ok and self._preserved(original, current, name, fresh["kind"]):
                    return {**fresh, "summary": fresh["summary"] + " 已重新读取并核对权限。"}
            raise TableError("权限更新已提交，但回读未匹配，请重新查看当前角色，勿直接重复提交。")

    @staticmethod
    def _preserved(old_role, new_role, table_name, kind):
        """核对已知的其他授权；允许平台维护角色级时间戳等元数据。"""
        for key in set(old_role) | set(new_role):
            if key.endswith("_rule_map") and key != "table_rule_map" and old_role.get(key) != new_role.get(key):
                return False
        old_tables = dict(old_role["table_rule_map"])
        new_tables = dict(new_role["table_rule_map"])
        if table_name not in new_tables:
            return False
        old_table = dict(old_tables.pop(table_name))
        new_table = dict(new_tables.pop(table_name))
        changed = "field_rule" if kind == "field" else "record_rule"
        old_block = dict(old_table.pop(changed, None) or {})
        new_block = dict(new_table.pop(changed, None) or {})
        editable_keys = ({"field_perm_mode", "field_perms"} if kind == "field" else
                         {"edit_filter_rule_group", "other_record_all_read", "read_filter_rule_group"})
        for key in editable_keys:
            old_block.pop(key, None)
            new_block.pop(key, None)
        return old_tables == new_tables and old_table == new_table and old_block == new_block
