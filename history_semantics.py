"""只从已核对的记录、显式列偏好和实际操作回执生成会话历史。"""

import hashlib
import json
import os
import re
import uuid
from pathlib import Path
from urllib.parse import urlsplit

from table_store import LocalTable, TableError


def stable_scope(chat, mode, data_dir):
    table = chat.table
    if isinstance(table, LocalTable):
        path = str(table._db_path)
        if path == ":memory:":
            # 两个内存库即使同名也不共享记录；直到该句柄释放前保持同一个作用域。
            if not hasattr(table, "_history_id"):
                with table._lock:
                    if not hasattr(table, "_history_id"):
                        table._history_id = uuid.uuid4().hex
            path = table._history_id
        else:
            path = os.path.normcase(str(Path(path).resolve()))
        parts = ["local", path, table.table_id]
    else:
        parts = ["feishu", table.app_token, table.table_id]
        actor = getattr(chat, "execution_actor", None)
        if actor:
            parts.extend([actor["kind"], actor["key"] or chat.table_view_id])
        if not table.table_id:
            parts.extend([mode, os.path.normcase(str(Path(data_dir).resolve()))])
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False).encode("utf-8")).hexdigest()


def _scope(chat):
    return stable_scope(chat, "local" if isinstance(chat.table, LocalTable) else "feishu", chat.data_dir)


def clear_focus(chat):
    from saved_paper_dialog import _bind
    _bind(chat)
    chat.pending = chat.last_evidence_receipt = chat.workspace_receipt = None


def export_focus(chat):
    focus = chat.saved_focus
    if (not focus or focus.table is not chat.table or not focus.record_id or not (focus.title or focus.url)
            or (focus.last, focus.last_title) != (chat.last, chat.last_title)
            or chat.recent_evidence or chat.collection_batch):
        return None
    return {"scope": _scope(chat), "record_id": focus.record_id, "title": focus.title, "url": focus.url}


def restore_focus(chat, saved):
    """恢复时只接受同表原记录，使用当前可见身份核对后重新建立阅读指代。"""
    import saved_focus
    from saved_paper_dialog import _bind, _identity, column_names

    with chat.lock:
        clear_focus(chat)
        if not isinstance(saved, dict) or saved.get("scope") != _scope(chat):
            return False
        rid, title, url = (saved.get(k) for k in ("record_id", "title", "url"))
        if not all(isinstance(v, str) for v in (rid, title, url)) or not rid or not (title or url):
            return False
        try:
            columns, links, _ = column_names(chat.table.schema())
            row = chat.table.get(rid)
        except (TableError, OSError):
            return False
        if row["id"] != rid or _identity(row, columns, links) != (title, url):
            return False
        _bind(chat, title, url)
        saved_focus.remember(chat, row, title, url)
        return True


_KINDS = {
    "关键数据": "metric", "指标": "metric", "实验指标": "metric",
    "图": "figure", "图片": "figure", "插图": "figure", "图题": "figure",
    "表格": "table", "实验表格": "table", "公式": "formula",
    "代码链接": "code", "代码仓库": "code", "开源代码": "code",
    "模型链接": "model", "模型权重": "model", "模型资源": "model",
    "数据集": "dataset", "数据集链接": "dataset",
}
_KIND = r"(?P<kind>" + "|".join(sorted(_KINDS, key=len, reverse=True)) + ")"
_FIELD = r"(?:「([^「」\r\n]{1,100})」|“([^“”\r\n]{1,100})”|([\w\u4e00-\u9fff -]{1,80}))"
_FUTURE = r"(?:以后|今后|下次|此后|从现在起)"
_PREFIX = r"(?:请)?" + _FUTURE + r"?(?:在这张表里|在本表中)?(?:都)?"
_WRITE_TO = r"(?:写入|写到|填入|填到|放入|放进|放到|放在|存入|存到|记在|记录在|保存到)\s*"
_PREFERENCE = [
    re.compile(_PREFIX + r"(?:优先|默认)(?:将|把)?" + _KIND + _WRITE_TO + _FIELD),
    re.compile(_PREFIX + r"(?:把|将)?" + _KIND + r"(?:都)?(?:优先|默认)" + _WRITE_TO + _FIELD),
    re.compile(r"(?:请)?" + _FUTURE + r"(?:在这张表里|在本表中)?(?:都)?(?:把|将)?" + _KIND + r"(?:都)?" + _WRITE_TO + _FIELD),
]


def parse_preference(message):
    if not isinstance(message, str):
        return None
    for pattern in _PREFERENCE:
        match = pattern.fullmatch(message.strip().rstrip("。！!").strip())
        if match:
            column = next((name.strip() for name in match.groups()[1:] if name and name.strip()), "")
            if column:
                return _KINDS[match["kind"]], column
    return None


def set_preference(chat, kind, column):
    from evidence_workflow import writable_evidence_columns
    if kind not in _KINDS.values() or not isinstance(column, str):
        raise ValueError("请指定一种提取内容和对应的文本列。")
    if column not in writable_evidence_columns(chat.table.schema()):
        raise ValueError("请选择当前表中可存放证据的文本列。")
    chat.history_preferences[kind] = column
    return {"message": f"已记住这张表的偏好，下次预览会优先选择「{column}」。",
            "preference_changed": {"kind": kind, "column": column}}


_STAGE_ACTION = {"evidence_confirm": "evidence_fill", "schema": "field_change"}
_ADMIN_ACTION = {"field": "field_change", "member": "member_permission", "role": "role_permission"}
_CONFIRM = re.compile(r"(?:确认|是的|确定|同意|创建|好(?:的)?|可以|就这样)")
_LABEL = {"name": "名称", "table_name": "数据表", "old_name": "原字段", "new_name": "新字段",
          "old_type": "原类型", "new_type": "新类型", "field_id": "字段 ID", "role": "角色",
          "field": "字段", "before": "变更前", "after": "变更后", "target": "协作者"}


def _action(stage, plan):
    if stage == "admin_commit":
        return _ADMIN_ACTION.get(plan.get("kind"))
    if stage == "workspace_create":
        return {"create_base": "create_base", "create_table": "create_table"}.get(plan.get("op"))
    return _STAGE_ACTION.get(stage)


def _detail(label, value, url=None):
    item = {"label": str(label), "value": str(value)}
    if isinstance(url, str) and url.startswith(("https://", "http://")):
        try:
            address = urlsplit(url)
            if address.hostname and not address.username and not address.password:
                item["url"] = url
        except ValueError:
            pass
    return item


def _record_detail(record_id, url=None):
    return {**_detail("记录 ID", record_id, url), "record_id": record_id}


def _plan_details(action, plan):
    inner = plan.get("plan", plan)
    keys = {"field_change": ("field_id", "old_name", "new_name", "old_type", "new_type", "name"),
            "member_permission": ("target", "before", "after"), "role_permission": ("role", "field", "before", "after"),
            "create_table": ("name",), "create_base": ("name", "table_name")}.get(action, ())
    details = [_detail(_LABEL[k], inner[k]) for k in keys if inner.get(k)]
    if action in {"create_table", "create_base"}:
        details.extend(_detail("字段", f["name"]) for f in inner.get("fields", []) if f.get("name"))
    return details


def _batch_status(states):
    if all(s == "verified" for s in states) and states:
        return "verified"
    if "verified" in states:
        return "partial"
    return "unverified" if "unverified" in states else "failed"


def operation_for(message, stage_before, result, error=None, plan=None):
    """仅识别结构化回执和工作流自身的取消结果。动作输入不代表实际执行成功。"""
    result, plan = result or {}, plan or {}
    action = _action(stage_before, plan)
    summary = result.get("message", "")
    if error:
        if not action or not _CONFIRM.match(message.strip()):
            return None
        # 调用失败时可能已经送达远端，历史不得将缺失回执解释成“未写入”。
        return {"action": action, "status": "unverified", "summary": str(error),
                "details": _plan_details(action, plan)}
    if results := result.get("evidence_results"):
        action = "evidence_fill"
        included = [r for r in results if r["status"] != "excluded"]
        status = _batch_status([r["status"] for r in included])
        if status == "verified" and all(r.get("write_action") == "unchanged" for r in included):
            status = "unchanged"
        details = []
        for paper in results:
            outcome = {"unchanged": "已有内容一致。", "created": "已新增记录。", "updated": "已更新原记录。"}.get(paper.get("write_action"), "")
            details.append(_detail(paper["title"], outcome + paper["message"], paper.get("url")))
            if paper.get("record_id"):
                details.append(_record_detail(paper["record_id"]))
            for item in paper.get("verified_items", []):
                details.append(_detail(item["field"], f"{item['label']} · {item['value']}\n{item['source']}", item["url"]))
    elif preview := result.get("evidence_preview"):
        action, status = "evidence_fill", "preview"
        details = [_detail(p["title"], p.get("error") or f"{len(p.get('items', []))} 项供核对", p.get("url"))
                   for p in preview.get("papers", [])]
    elif stage_before and summary in {"已取消。", "已取消，表格保持原样。", "已取消提取。", "已取消本批回填。"} and not result.get("pending_id"):
        action, status = action or "cancel", "cancelled"
        details = _plan_details(action, plan)
    elif receipt := result.get("write_receipt"):
        action = "collect" if receipt["collection"] else "record_write"
        status = "verified"
        details = [_record_detail(receipt["record_id"], receipt.get("url"))]
        details.extend(_detail(name, value) for name, value in receipt["fields"].items())
    elif batch := result.get("collection_receipts"):
        action, status = "collect", _batch_status([r["status"] for r in batch])
        details = []
        for receipt in batch:
            details.append(_detail(receipt.get("title") or receipt["url"],
                                   "已回读收藏记录" if receipt.get("record_id") else receipt["message"], receipt["url"]))
            if receipt.get("record_id"):
                details.append(_record_detail(receipt["record_id"]))
    elif result.get("verification") and result.get("management_action") in {"field_change", "member_permission", "role_permission"}:
        action = result["management_action"]
        status, details = "verified", _plan_details(action, plan)
    elif (result.get("verification") and result.get("target_changed") and action in {"create_table", "create_base"}):
        status, details = "verified", _plan_details(action, plan)
    else:
        return None
    return {"action": action, "status": status, "summary": summary, "details": details}
