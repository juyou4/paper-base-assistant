"""阅读清单的本地存储与飞书多维表格适配。仅使用 Python 标准库。"""

import hashlib
import json
import math
import random
import re
import shutil
import sqlite3
import ssl
import subprocess
import threading
import time
import uuid
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen


class TableError(RuntimeError):
    """可以直接显示给演示用户的失败原因。"""


class RecordNotFound(TableError):
    """未找到指定记录或论文。"""


class _TransientError(TableError):
    def __init__(self, message, retry_after=None):
        super().__init__(message)
        self.retry_after = retry_after


def _retry_delay(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        try:
            seconds = parsedate_to_datetime(str(value)).timestamp() - time.time()
        except (TypeError, ValueError, OverflowError, OSError):
            return None
    return max(0, seconds) if math.isfinite(seconds) else None


def _permission_hint(method, path, identity):
    if "/permissions/" in path and "/members" in path:
        right = "查看" if method == "GET" else "管理"
        return f"请核对协作者接口授权，以及{identity}对整份多维表格的协作者{right}权限。"
    if "/roles" in path:
        return f"请核对高级角色接口的授权、目标表的高级权限开关，以及{identity}管理角色的权限。"
    if method == "POST" and path.endswith("/fields") or method in {"PUT", "DELETE"} and "/fields/" in path:
        action = {"POST": "新增字段", "PUT": "更新字段", "DELETE": "删除字段"}[method]
        return (f"{action}需要接口授权，也需要目标文档允许{identity}管理这张数据表的字段。"
                "请在飞书检查文档协作权限；已启用高级权限时，再核对该数据表的字段管理权限。")
    if "/records" in path and method in {"POST", "PUT", "PATCH"} and not path.endswith(("/search", "/batch_get")):
        action = "新增记录" if method == "POST" and path.endswith("/records") else "更新记录"
        return (f"请检查{action}的接口授权和目标文档对{identity}的编辑权限。"
                "已启用高级权限时，还需核对目标行和字段是否允许此次写入。")
    return f"请检查读取接口的授权，以及{identity}对目标文档、数据表和所读字段的可见范围。"


_INITIAL_COLUMNS = (
    ("title", "论文标题", "text"), ("link", "论文链接", "url"),
    ("abstract", "中文摘要", "text"), ("method", "方法概述", "text"),
    ("reading", "阅读笔记", "text"), ("research", "研究笔记", "text"),
)
_IDENTIFIERS = {"论文标识", "论文ID", "arXiv ID", "arXiv编号", "DOI"}
_PAPER_LINKS = {"论文链接", "论文地址", "原文地址", "原文链接", "URL", "论文URL", "预印本链接"}
_ARXIV = re.compile(r"(?:\d{4}\.\d{4,5}|[a-z][a-z0-9.\-]+/\d{7})(?:v\d+)?", re.I)


def _is_url(value):
    try:
        parsed = urlsplit(value)
        return parsed.scheme in {"http", "https"} and bool(parsed.hostname) and not parsed.username
    except ValueError:
        return False


def _identity(value):
    value = value.strip()
    if value.lower().startswith("url:"):
        value = value[4:]
    if value.lower().startswith("arxiv:"):
        value = value[6:]
    if value.lower().startswith("doi:"):
        return "doi:" + value[4:].lower()
    if _is_url(value):
        parsed = urlsplit(value)
        if parsed.hostname.lower() in {"arxiv.org", "www.arxiv.org", "export.arxiv.org", "abs.arxiv.org"}:
            match = re.fullmatch(r"/(?:abs|pdf|html)/(.+?)(?:\.pdf)?/?", parsed.path, re.I)
            if match and _ARXIV.fullmatch(match.group(1)):
                return "arxiv:" + re.sub(r"v\d+$", "", match.group(1).lower())
        if parsed.hostname.lower() in {"doi.org", "dx.doi.org"}:
            return "doi:" + parsed.path.lstrip("/").lower()
        return "url:" + urlunsplit((parsed.scheme, parsed.netloc.lower(), parsed.path or "/", parsed.query, ""))
    if _ARXIV.fullmatch(value):
        return "arxiv:" + re.sub(r"v\d+$", "", value.lower())
    if value.startswith("10.") and "/" in value:
        return "doi:" + value.lower()
    return value


def _check_fields(schema, values):
    types = {field["name"]: field["type"] for field in schema}
    if not isinstance(values, dict) or not values:
        raise TableError("没有可提交的字段。")
    for name, value in values.items():
        if types.get(name) not in {"text", "url"}:
            raise TableError(f"列「{name}」不存在、只读或暂不支持写入。请重新读取表结构。")
        if not isinstance(value, str) or not value.strip():
            raise TableError(f"列「{name}」缺少有效内容。")
        if types[name] == "url" and not _is_url(value):
            raise TableError(f"列「{name}」需要 http 或 https 链接。")
    return types


def _matching_column(schema, name, kind):
    if kind not in {"text", "url"}:
        raise TableError("新建列仅支持文本或超链接类型。")
    if not isinstance(name, str) or not name.strip() or name.strip() != name:
        raise TableError("请输入有效列名，去除首尾空格。")
    match = next((col for col in schema if col["name"] == name), None)
    if match and match["type"] != kind:
        raise TableError(f"表中已有名为「{name}」的其他类型列，请换一个列名。")
    return match


def _fingerprint(key, values, request_id):
    if not isinstance(request_id, str) or not request_id.strip() or not key.strip():
        raise TableError("缺少论文标识或请求编号。")
    payload = json.dumps([key, values], ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _connect(filename):
    if str(filename) != ":memory:":
        Path(filename).parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(filename, timeout=8, check_same_thread=False)
    db.row_factory = sqlite3.Row
    return db


def _mentions_column(rule, identifiers):
    if isinstance(rule, dict):
        return any(key in identifiers or _mentions_column(value, identifiers) for key, value in rule.items())
    if isinstance(rule, list):
        return any(_mentions_column(value, identifiers) for value in rule)
    return isinstance(rule, str) and rule in identifiers


class LocalTable:
    name = "阅读清单"

    def __init__(self, db_path):
        self._db = _connect(db_path)
        self._lock = threading.RLock()
        with self._db:
            self._db.executescript("""
                CREATE TABLE IF NOT EXISTS demo_columns (
                    id TEXT PRIMARY KEY, name TEXT UNIQUE NOT NULL, type TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS demo_papers (
                    id TEXT PRIMARY KEY, paper_key TEXT UNIQUE NOT NULL, cells TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS demo_requests (
                    id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL,
                    record_id TEXT NOT NULL, action TEXT NOT NULL
                );
            """)
            if not self._db.execute("SELECT 1 FROM demo_columns LIMIT 1").fetchone():
                self._db.executemany("INSERT OR IGNORE INTO demo_columns VALUES (?,?,?)", _INITIAL_COLUMNS)

    def schema(self):
        with self._lock:
            return [dict(row) for row in self._db.execute("SELECT * FROM demo_columns ORDER BY rowid")]

    def close(self):
        with self._lock:
            self._db.close()

    def _role_changes(self, action, old=None, new=None, field_id=None):
        """计算当前表依赖的本地角色配置；由同一 SQLite 写事务提交。"""
        if new == "创建人":
            raise TableError("「创建人」是本地角色记录范围使用的系统字段，请换一个列名。")
        if not self._db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='demo_roles'").fetchone():
            return []
        changes = []
        identifiers = {s for s in (old, field_id) if s}
        before_names = {f["name"] for f in self.schema()}
        for row in self._db.execute("SELECT id, body FROM demo_roles ORDER BY id"):
            role = json.loads(row["body"])
            rule = role.get("table_rule_map", {}).get(self.name)
            if rule is None:
                continue
            if not isinstance(rule, dict):
                raise TableError("本地角色的表规则无法解析，请先核对角色。")
            block = rule.get("field_rule") or {}
            if not isinstance(block, dict):
                raise TableError("本地角色的字段权限结构无法解析。")
            if identifiers:
                # 标准逐列授权可以无损迁移；行筛选、附件/选项等额外规则不自动改写。
                additional = {k: v for k, v in block.items() if k not in {"field_perm_mode", "field_perms"}}
                records = rule.get("record_rule") or {}
                filters = ({k: v for k, v in records.items() if k not in {"record_operations", "other_record_all_read"}}
                           if isinstance(records, dict) else records)
                other = {k: v for k, v in rule.items() if k not in {"field_rule", "record_rule", "perm"}}
                if _mentions_column([additional, filters, other], identifiers):
                    raise TableError(f"本地角色「{role.get('role_name', row['id'])}」的行筛选或额外规则依赖「{old}」。请先调整该规则。")
            if block.get("field_perm_mode") != "specify":
                continue
            grants = block.get("field_perms")
            if not isinstance(grants, dict) or set(grants) != before_names:
                raise TableError(f"本地角色「{role.get('role_name', row['id'])}」的逐列授权和现有列不一致，请先核对。")
            if action == "add":
                grants[new] = "no_perm"
            elif action == "rename":
                grants[new] = grants.pop(old)
            elif action == "delete":
                del grants[old]
            else:
                continue
            encoded = json.dumps(role, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            changes.append((row["id"], row["body"], encoded))
        return changes

    def _save_role_changes(self, changes):
        for role_id, old, new in changes:
            updated = self._db.execute("UPDATE demo_roles SET body=? WHERE id=? AND body=?", (new, role_id, old))
            if updated.rowcount != 1:
                raise TableError("提交前本地角色设置已变化，请重新预览。")

    def add_field(self, name, kind):
        with self._lock, self._db:
            self._db.execute("BEGIN IMMEDIATE")
            existing = _matching_column(self.schema(), name, kind)
            if existing:
                return existing
            role_changes = self._role_changes("add", new=name)
            field_id = "fld_" + uuid.uuid4().hex[:16]
            self._db.execute("INSERT INTO demo_columns VALUES (?,?,?)", (field_id, name, kind))
            self._save_role_changes(role_changes)
            return {"id": field_id, "name": name, "type": kind}

    def rows(self):
        with self._lock:
            return [{"id": row["id"], "fields": json.loads(row["cells"])} for row in
                    self._db.execute("SELECT id,cells FROM demo_papers ORDER BY rowid DESC")]

    def get(self, record_id):
        with self._lock:
            row = self._db.execute("SELECT cells FROM demo_papers WHERE id=?", (record_id,)).fetchone()
            if row is None:
                raise RecordNotFound("没有找到这条记录。")
            return {"id": record_id, "fields": json.loads(row["cells"])}

    def upsert(self, key, values, request_id, *, update_only=False):
        key = _identity(key)
        fingerprint = _fingerprint("update-only:" + key if update_only else key, values, request_id)
        with self._lock, self._db:
            self._db.execute("BEGIN IMMEDIATE")
            _check_fields(self.schema(), values)
            prior = self._db.execute("SELECT * FROM demo_requests WHERE id=?", (request_id,)).fetchone()
            if prior:
                if prior["fingerprint"] != fingerprint:
                    raise TableError("这个请求编号已关联到其他内容。请重新提交。")
                return {"record_id": prior["record_id"], "action": prior["action"]}
            row = self._db.execute("SELECT * FROM demo_papers WHERE paper_key=?", (key,)).fetchone()
            if update_only and row is None:
                raise RecordNotFound("未找到这篇论文的已有记录。")
            record_id = row["id"] if row else "rec_" + uuid.uuid4().hex[:16]
            before = json.loads(row["cells"]) if row else {}
            cells = {**before, **values}
            action = ("unchanged" if before == cells else "updated") if row else "created"
            if action == "updated":
                self._db.execute("UPDATE demo_papers SET cells=? WHERE id=?",
                                 (json.dumps(cells, ensure_ascii=False), record_id))
            elif action == "created":
                self._db.execute("INSERT INTO demo_papers VALUES (?,?,?)",
                                 (record_id, key, json.dumps(cells, ensure_ascii=False)))
            self._db.execute("INSERT INTO demo_requests VALUES (?,?,?,?)",
                             (request_id, fingerprint, record_id, action))
            if self.get(record_id)["fields"] != cells:
                raise TableError("本地记录回读不一致，本次写入已撤销。")
            return {"record_id": record_id, "action": action}


def _text(cell):
    if isinstance(cell, str):
        return cell
    if isinstance(cell, dict):
        return cell.get("link") or cell.get("text") or ""
    if isinstance(cell, list):
        return "".join(_text(part) for part in cell)
    return "" if cell is None else str(cell)


def _record(data):
    return {"id": data["record_id"], "fields": {
        name: _text(cell) for name, cell in data.get("fields", {}).items()}}


class FeishuTable:
    name = "飞书多维表格"
    _host = "https://open.feishu.cn/open-apis"
    _timeout = 12

    def __init__(self, db_path, app_id, app_secret, app_token, table_id):
        if not all((app_id, app_secret, app_token, table_id)):
            raise TableError("请设置应用 ID、应用密钥、多维表格 token 和数据表 ID。")
        self._app_id, self._secret = app_id, app_secret
        self._token, self._expires = "", 0
        self._init_state(db_path, app_token, table_id)

    def _init_state(self, db_path, app_token, table_id):
        self._base = f"/bitable/v1/apps/{quote(app_token, safe='')}/tables/{quote(table_id, safe='')}"
        self._scope = hashlib.sha256(self._base.encode()).hexdigest()
        self._db = _connect(db_path)
        self._lock = threading.RLock()
        with self._db:
            self._db.executescript("""
                CREATE TABLE IF NOT EXISTS fs_keys (
                    scope TEXT, paper_key TEXT, record_id TEXT NOT NULL, PRIMARY KEY(scope,paper_key)
                );
                CREATE TABLE IF NOT EXISTS fs_requests (
                    scope TEXT, id TEXT, fingerprint TEXT NOT NULL, token TEXT NOT NULL,
                    action TEXT NOT NULL, record_id TEXT, done INTEGER DEFAULT 0, PRIMARY KEY(scope,id)
                );
                CREATE TABLE IF NOT EXISTS fs_pending_creates (
                    scope TEXT, paper_key TEXT, request_id TEXT NOT NULL, cells TEXT NOT NULL,
                    PRIMARY KEY(scope,paper_key)
                );
            """)

    def close(self):
        with self._lock:
            self._db.close()

    def _request(self, method, path, body=None, query=None, authenticated=True):
        readonly = method in {"GET", "HEAD"}
        attempts = 3 if readonly else 1
        deadline = time.monotonic() + self._timeout + 5
        for attempt in range(1, attempts + 1):
            remaining = max(0.1, deadline - time.monotonic())
            try:
                return self._request_once(method, path, body, query, authenticated, min(self._timeout, remaining))
            except _TransientError as exc:
                if not readonly:
                    raise TableError(f"{exc} 本次请求没有自动重发，请先核对操作结果。") from exc
                delay = exc.retry_after
                if delay is None:
                    delay = 0.35 * 2 ** (attempt - 1) + random.uniform(0, 0.15)
                if attempt == attempts or delay > 4 or time.monotonic() + delay + 0.1 >= deadline:
                    wait = f"请至少等待 {math.ceil(exc.retry_after)} 秒再试。" if exc.retry_after is not None else "请检查网络后稍后重试。"
                    raise TableError(f"{exc} 只读请求已尝试 {attempt} 次。{wait}") from exc
                time.sleep(delay)

    def _request_once(self, method, path, body, query, authenticated, timeout):
        headers = {"Content-Type": "application/json; charset=utf-8"}
        if authenticated:
            with self._lock:
                if time.monotonic() >= self._expires:
                    answer = self._request("POST", "/auth/v3/tenant_access_token/internal",
                                           {"app_id": self._app_id, "app_secret": self._secret}, authenticated=False)
                    self._token = answer.get("tenant_access_token", "")
                    if not self._token:
                        raise TableError("飞书未签发应用访问令牌，请检查应用配置。")
                    self._expires = time.monotonic() + max(1, int(answer.get("expire", 7200)) - 120)
                headers["Authorization"] = "Bearer " + self._token
        url = self._host + path + ("?" + urlencode(query) if query else "")
        data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
        request = Request(url, data=data, headers=headers, method=method)
        status = 200
        try:
            with urlopen(request, timeout=timeout) as response:
                raw = response.read()
                reply_headers = getattr(response, "headers", {}) or {}
        except HTTPError as exc:
            status, raw = exc.code, exc.read()
            reply_headers = exc.headers or {}
        except (URLError, OSError) as exc:
            if isinstance(getattr(exc, "reason", exc), ssl.SSLError):
                raise TableError("飞书 HTTPS 连接校验失败，请检查证书与代理设置。") from exc
            raise _TransientError("连接飞书时发生网络错误或超时。") from exc
        if method == "HEAD" and status < 400 and not raw:
            return {"code": 0, "data": {}}
        try:
            answer = json.loads(raw)
        except (ValueError, UnicodeError) as exc:
            if status in {408, 429, 502, 503, 504}:
                wait = _retry_delay(reply_headers.get("x-ogw-ratelimit-reset") or reply_headers.get("Retry-After"))
                raise _TransientError(f"飞书网关暂时不可用（HTTP {status}）。", wait) from exc
            raise TableError(f"飞书未返回有效 JSON（HTTP {status}）。") from exc
        if not isinstance(answer, dict):
            raise TableError(f"飞书返回的 JSON 格式不符（HTTP {status}）。")
        code = answer.get("code")
        if code == 1254043 or (status == 404 and code in {None, 404} and "/records/" in path):
            raise RecordNotFound("飞书数据表中已找不到该条记录。")
        if status >= 400 or code != 0:
            if str(code) in {"91403", "1254302", "1063002"}:
                hint = _permission_hint(method, path, "当前应用")
                raise TableError(f"飞书拒绝了本次操作（HTTP {status}，业务码 {code}）。{hint}")
            if str(code) == "99991400" or (status in {408, 429, 502, 503, 504} and code in (None, 0, status)):
                wait = _retry_delay(reply_headers.get("x-ogw-ratelimit-reset") or reply_headers.get("Retry-After"))
                raise _TransientError(f"飞书接口暂时限流或网关不可用（HTTP {status}，业务码 {code}）。", wait)
            raise TableError(f"飞书请求失败（HTTP {status}，业务码 {code}）。请检查应用权限、目标表权限及字段设置。")
        return answer

    def _items(self, suffix):
        items, seen = [], set()
        query = {"page_size": 100}
        deadline = time.monotonic() + 30
        while True:
            if time.monotonic() >= deadline:
                raise TableError("飞书分页读取耗时过长。请重试或选择较小的数据表。")
            data = self._request("GET", self._base + suffix, query=query).get("data", {})
            if "items" not in data and suffix == "/records" and data.get("total") == 0 and data.get("has_more") is False:
                return items
            if "items" not in data or not isinstance(data["items"], (list, type(None))):
                raise TableError("飞书缺少有效列表结果，无法确认完整表结构及记录。")
            items.extend(data["items"] or [])
            if not data.get("has_more"):
                return items
            token = data.get("page_token")
            if not token or token in seen:
                raise TableError("飞书分页结果不完整，请重试。")
            seen.add(token)
            query["page_token"] = token

    def schema(self):
        schema = []
        by_name = {}
        for column in self._items("/fields"):
            kind = {1: "text", 15: "url", 19: "readonly", 20: "readonly",
                    1001: "readonly", 1002: "readonly", 1003: "readonly",
                    1004: "readonly", 1005: "readonly"}.get(column.get("type"), "unsupported")
            field = {"id": column["field_id"], "name": column["field_name"], "type": kind}
            name = field["name"]
            if name in by_name:
                if by_name[name] != field:
                    raise TableError(f"飞书返回了同名字段「{name}」，请核对表结构后再提交。")
                continue
            by_name[name] = field
            schema.append(field)
        return schema

    def add_field(self, name, kind):
        with self._lock:
            existing = _matching_column(self.schema(), name, kind)
            if existing:
                return existing
            self._request("POST", self._base + "/fields",
                          {"field_name": name, "type": {"text": 1, "url": 15}[kind]})
            for delay in (0, 0.35, 1):
                if delay:
                    time.sleep(delay)
                saved = _matching_column(self.schema(), name, kind)
                if saved:
                    return saved
            raise TableError(f"飞书已接收建列操作，尚未回读到「{name}」。请重新读取表结构。")

    def rows(self):
        return [_record(row) for row in self._items("/records")]

    def get(self, record_id):
        path = self._base + "/records/" + quote(record_id, safe="")
        return _record(self._request("GET", path)["data"]["record"])

    def _locate(self, key, types, values):
        known = self._db.execute("SELECT record_id FROM fs_keys WHERE scope=? AND paper_key=?",
                                 (self._scope, key)).fetchone()
        if known:
            try:
                return self.get(known["record_id"])
            except RecordNotFound:
                self._db.execute("DELETE FROM fs_keys WHERE scope=? AND paper_key=?", (self._scope, key))
        candidates = {n for n in types if n in _PAPER_LINKS or n in _IDENTIFIERS}
        candidates.update(n for n, v in values.items() if _identity(v) == key)
        rows = self.rows()
        matches = [row for row in rows if any(
            _identity(row["fields"].get(name, "")) == key for name in candidates)]
        if not matches:
            other_links = {name for name, kind in types.items() if kind == "url"} - candidates
            # 允许用「出处」等自定义超链接列；已有其他正式论文标识的行可能只是在引用本文。
            matches = [row for row in rows if not any(row["fields"].get(n) for n in candidates)
                       and any(_identity(row["fields"].get(n, "")) == key for n in other_links)]
        if len(matches) > 1:
            raise TableError("飞书表中有多条记录指向这篇论文，请先核对重复记录。")
        return matches[0] if matches else None

    def _verify(self, record_id, values):
        for delay in (0, 0.35, 1):
            if delay:
                time.sleep(delay)
            actual = self.get(record_id)["fields"]
            if all(actual.get(name) == value for name, value in values.items()):
                return
        raise TableError(f"飞书已接收操作，但回读内容尚未匹配。请核对记录 {record_id} 并重试原请求。")

    def _plan_write(self, key, values, types, request_id, fingerprint, update_only):
        params = (self._scope, request_id)
        with self._db:
            self._db.execute("BEGIN IMMEDIATE")
            op = self._db.execute("SELECT * FROM fs_requests WHERE scope=? AND id=?", params).fetchone()
            if op:
                if op["fingerprint"] != fingerprint:
                    raise TableError("这个请求编号已关联到其他内容。请重新提交。")
                return op, None
            target = (self._scope, key)
            if not update_only:
                pending = self._db.execute("SELECT * FROM fs_pending_creates WHERE scope=? AND paper_key=?", target).fetchone()
                if pending:
                    return None, pending
                # 兼容先前版本已保存请求编号、尚未保存论文级恢复标记的同内容重试。
                older = self._db.execute("""SELECT id FROM fs_requests WHERE scope=? AND fingerprint=?
                    AND action='created' AND done=0 LIMIT 1""", (self._scope, fingerprint)).fetchone()
                if older:
                    return None, {"request_id": older["id"], "cells": json.dumps(values)}
            existing = self._locate(key, types, {} if update_only else values)
            if update_only and not existing:
                raise RecordNotFound("未找到这篇论文的已有记录。")
            record_id = existing["id"] if existing else None
            action = "created"
            if existing:
                action = "unchanged" if all(existing["fields"].get(n) == v for n, v in values.items()) else "updated"
            else:
                self._db.execute("INSERT INTO fs_pending_creates VALUES (?,?,?,?)",
                                 (*target, request_id, json.dumps(values, ensure_ascii=False)))
            self._db.execute("INSERT INTO fs_requests VALUES (?,?,?,?,?,?,0)",
                             (*params, fingerprint, str(uuid.uuid4()), action, record_id))
            return self._db.execute("SELECT * FROM fs_requests WHERE scope=? AND id=?", params).fetchone(), None

    def upsert(self, key, values, request_id, *, update_only=False):
        key = _identity(key)
        fingerprint = _fingerprint("update-only:" + key if update_only else key, values, request_id)
        with self._lock:
            types = _check_fields(self.schema(), values)
            params = (self._scope, request_id)
            op, pending = self._plan_write(key, values, types, request_id, fingerprint, update_only)
            while pending:
                # 即使新指令换了请求编号，也先恢复相同论文的未决新增及其原始字段。
                self.upsert(key, json.loads(pending["cells"]), pending["request_id"])
                op, pending = self._plan_write(key, values, types, request_id, fingerprint, update_only)
            record_id, action = op["record_id"], op["action"]
            if op["done"]:
                return {"record_id": record_id, "action": action}
            # 先落盘 UUID 再发送请求，断网后的同一次新增可用相同幂等键重放。
            if action == "updated" or (action == "created" and not record_id):
                fields = {n: {"text": v, "link": v} if types[n] == "url" else v for n, v in values.items()}
                path = self._base + "/records"
                if action == "created":
                    response = self._request("POST", path, {"fields": fields}, {"client_token": op["token"]})
                    record_id = response["data"]["record"]["record_id"]
                    with self._db:
                        self._db.execute("UPDATE fs_requests SET record_id=? WHERE scope=? AND id=?",
                                         (record_id, *params))
                else:
                    self._request("PUT", path + "/" + quote(record_id, safe=""), {"fields": fields})
            self._verify(record_id, values)
            with self._db:
                self._db.execute("INSERT OR REPLACE INTO fs_keys VALUES (?,?,?)", (self._scope, key, record_id))
                self._db.execute("UPDATE fs_requests SET done=1 WHERE scope=? AND id=?", params)
                self._db.execute("DELETE FROM fs_pending_creates WHERE scope=? AND paper_key=? AND request_id=?",
                                 (self._scope, key, request_id))
            return {"record_id": record_id, "action": action}


class LarkCliTable(FeishuTable):
    """用已登录的官方 CLI 用户身份执行相同的 OpenAPI 请求。"""

    _timeout = 20

    def __init__(self, db_path, cli_cmd, app_token, table_id):
        if not app_token or not table_id:
            raise TableError("请设置多维表格 token 和数据表 ID。")
        if isinstance(cli_cmd, (list, tuple)):
            self._command = [str(part) for part in cli_cmd]
        else:
            self._command = [str(cli_cmd or "lark-cli")]
        executable = shutil.which(self._command[0])
        if not executable:
            raise TableError("找不到官方 lark-cli，请配置可执行文件路径或切换到本地演示。")
        self._command[0] = executable
        if Path(executable).suffix.lower() in {".cmd", ".bat", ".ps1"}:
            # npm 的 Windows 启动器经 shell 转发，直接调用官方 Node 入口以保留参数边界。
            folder = Path(executable).parent
            npm_script = folder / "node_modules" / "@larksuite" / "cli" / "scripts" / "run.js"
            if not npm_script.exists():
                npm_script = folder.parent / "@larksuite" / "cli" / "scripts" / "run.js"
            node = shutil.which("node")
            if not npm_script.is_file() or not node:
                raise TableError("请配置 lark-cli.exe 的完整路径，或使用官方 npm 版启动程序。")
            self._command = [node, str(npm_script), *self._command[1:]]
        self._init_state(db_path, app_token, table_id)

    def _request_once(self, method, path, body, query, authenticated, timeout):
        args = [*self._command, "api", method, "/open-apis" + path, "--as", "user", "--format", "json"]
        if query:
            args.extend(["--params", json.dumps(query, separators=(",", ":"))])
        if body is not None:
            args.extend(["--data", "-"])
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else b""
        try:
            result = subprocess.run(args, input=payload, capture_output=True, timeout=timeout,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except subprocess.TimeoutExpired as exc:
            raise _TransientError("等待飞书 CLI 响应超时。") from exc
        except OSError as exc:
            raise TableError("飞书 CLI 启动失败，请检查可执行文件路径。") from exc
        stream = result.stderr if result.returncode and result.stderr.strip() else result.stdout
        try:
            answer = json.loads(stream)
        except (UnicodeError, ValueError):
            answer = {}
        if result.returncode or not isinstance(answer, dict) or answer.get("ok") is not True:
            err = answer.get("error") if isinstance(answer, dict) else None
            code = err.get("code") if isinstance(err, dict) else None
            if code == 1254043:
                raise RecordNotFound("飞书数据表中已找不到该条记录。")
            if str(code) in {"91403", "1254302", "1063002"}:
                hint = _permission_hint(method, path, "当前登录用户")
                raise TableError(f"飞书 CLI 拒绝了本次操作（退出码 {result.returncode}，业务码 {code}）。{hint}")
            code_text = f"，业务码 {code}" if isinstance(code, int) else ""
            details = err if isinstance(err, dict) else {}
            category, subtype = details.get("type"), details.get("subtype")
            network = (category == "network" and subtype not in {"tls", "protocol"} and
                       (details.get("retryable") is True or subtype in {"timeout", "dns", "transport", "server_error"})
                       or result.returncode == 4 and not category and not code)
            limited = (category == "api" and subtype == "rate_limit" and details.get("retryable") is True
                       or str(code) == "99991400")
            if (network or limited) and details.get("retryable") is not False:
                reason = "暂时限流" if limited else "网络连接异常"
                raise _TransientError(f"飞书 CLI {reason}（退出码 {result.returncode}{code_text}）。",
                                      _retry_delay(details.get("retry_after_seconds")))
            if result.returncode == 4 or category == "network":
                raise TableError(f"飞书 CLI 网络连接失败（退出码 {result.returncode}{code_text}）。请检查网络、代理和 HTTPS 证书设置。")
            raise TableError(f"飞书 CLI 请求失败（退出码 {result.returncode}{code_text}）。请检查用户登录、表格权限和 CLI 版本。")
        if not isinstance(answer.get("data"), dict):
            raise TableError("飞书 CLI 返回格式不符，请检查官方 CLI 版本。")
        return {"code": 0, "data": answer["data"]}
