"""浏览器私有、按表隔离的本机研读历史。每次调用在独立 SQLite 事务中完成。"""

import hashlib
import json
import re
import sqlite3
import uuid
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit


_SETTINGS = ("save_history", "use_preferences", "resume_focus")
_PAGE_SIZES = {"messages": 100, "operations": 40}
_SECRET = re.compile(
    r"(?i)(\bbearer\s+|\b(?:[\w-]*api[_-]?key|[\w-]*app[_-]?secret|password|refresh[_-]?token|"
    r"access[_-]?token|authorization)\b[\"']?\s*[:=]\s*[\"']?|"
    r"[?&](?:token|signature|x-amz-signature)=)(?:(?:bearer|basic|token)\s+)?[^\s\"',;&]+"
)
_KEY = re.compile(r"\b(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9_]{24,}|github_pat_\w{24,})\b")
_PARAM_SECRETS = {"token", "access_token", "refresh_token", "api_key", "apikey", "signature", "x-amz-signature"}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS spaces(
    id INTEGER PRIMARY KEY, owner TEXT NOT NULL, scope TEXT NOT NULL, active TEXT,
    save_history INTEGER NOT NULL DEFAULT 1, use_preferences INTEGER NOT NULL DEFAULT 1,
    resume_focus INTEGER NOT NULL DEFAULT 1, UNIQUE(owner,scope)
);
CREATE TABLE IF NOT EXISTS conversations(
    id TEXT PRIMARY KEY, space INTEGER NOT NULL REFERENCES spaces(id), title TEXT NOT NULL,
    updated TEXT NOT NULL, focus TEXT
);
CREATE INDEX IF NOT EXISTS conversation_list ON conversations(space,updated DESC);
CREATE TABLE IF NOT EXISTS turns(
    seq INTEGER PRIMARY KEY, conversation TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    request_id TEXT NOT NULL, created TEXT NOT NULL, user_text TEXT NOT NULL, assistant_text TEXT NOT NULL,
    UNIQUE(conversation,request_id)
);
CREATE TABLE IF NOT EXISTS preferences(
    id TEXT PRIMARY KEY, space INTEGER NOT NULL REFERENCES spaces(id), text TEXT NOT NULL,
    created TEXT NOT NULL, UNIQUE(space,text)
);
CREATE TABLE IF NOT EXISTS operations(
    id TEXT PRIMARY KEY, space INTEGER NOT NULL REFERENCES spaces(id),
    conversation TEXT REFERENCES conversations(id) ON DELETE SET NULL, event_key TEXT NOT NULL,
    action TEXT NOT NULL, status TEXT NOT NULL, summary TEXT NOT NULL, details TEXT NOT NULL,
    created TEXT NOT NULL, UNIQUE(space,event_key)
);
CREATE INDEX IF NOT EXISTS operation_list ON operations(space,created DESC);
"""
_PAGE_INDEXES = """
CREATE INDEX IF NOT EXISTS turn_pages ON turns(conversation,seq DESC);
CREATE INDEX IF NOT EXISTS operation_pages ON operations(space,created DESC,id DESC);
"""


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _identifier(value):
    if not isinstance(value, str) or not value.strip() or len(value) > 512:
        raise ValueError("缺少有效的工作台标识。")
    return value


def _display(value, limit):
    """仅接收用于展示的文本，并对常见凭据表达做遮盖。"""
    if not isinstance(value, str):
        raise ValueError("历史内容应为文本。")
    text = _KEY.sub("[已隐藏]", _SECRET.sub(r"\1[已隐藏]", value.replace("\x00", "")))
    return text if len(text) <= limit else text[:limit] + "…（内容已截短）"


def _link(value):
    if not isinstance(value, str) or len(value) > 2048 or re.search(r"[\s\\\x00-\x1f]", value):
        return ""
    try:
        url = urlsplit(value)
        if (url.scheme not in ("http", "https") or not url.hostname or url.username is not None
                or url.password is not None or any(k.lower() in _PARAM_SECRETS for k, _ in parse_qsl(url.query))):
            return ""
        return value
    except ValueError:
        return ""


def _focus(data):
    if data is None:
        return None
    if not isinstance(data, dict):
        raise ValueError("论文焦点格式无效。")
    record = _identifier(data.get("record_id"))
    title, url = _display(data.get("title", ""), 1024), _link(data.get("url", ""))
    if (data.get("url") and not url) or not (title or url):
        raise ValueError("论文焦点缺少有效的题名或原文链接。")
    return json.dumps({"record_id": record, "title": title, "url": url}, ensure_ascii=False)


def _details(entries):
    if not isinstance(entries, (list, tuple)):
        raise ValueError("操作明细格式无效。")
    result = []
    for entry in entries[:160]:
        if not isinstance(entry, dict):
            raise ValueError("操作明细格式无效。")
        item = {"label": _display(entry.get("label", ""), 200), "value": _display(entry.get("value", ""), 4000)}
        url = _link(entry.get("url"))
        if url:
            item["url"] = url
        if (item["label"] == "记录 ID" and 0 < len(item["value"]) <= 200
                and entry.get("record_id") == item["value"]):
            item["record_id"] = item["value"]
        result.append(item)
    if len(entries) > 160:
        result.append({"label": "其余明细", "value": f"另有 {len(entries)-160} 项未保存，请查看原记录。"})
    return json.dumps(result, ensure_ascii=False)


class WorkspaceHistory:
    def __init__(self, data_dir):
        directory = Path(data_dir)
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / "workspace-history.sqlite3"
        with closing(self._open()) as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version > 2:
                raise ValueError("本机工作台历史来自较新的版本。")
            if version < 2:
                schema = _SCHEMA if version == 0 else ""
                db.executescript("BEGIN IMMEDIATE;\n" + schema + _PAGE_INDEXES + "\nPRAGMA user_version=2; COMMIT;")

    def _open(self):
        db = sqlite3.connect(self.path, timeout=12)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        return db

    def has_owner(self, owner):
        with closing(self._open()) as db:
            return bool(db.execute("SELECT 1 FROM spaces WHERE owner=? LIMIT 1", (_identifier(owner),)).fetchone())

    @staticmethod
    def _new(db, space):
        identity = uuid.uuid4().hex
        db.execute("INSERT INTO conversations(id,space,title,updated) VALUES(?,?,?,?)",
                   (identity, space, "新对话", _now()))
        db.execute("UPDATE spaces SET active=? WHERE id=?", (identity, space))
        return identity

    @contextmanager
    def _access(self, owner, scope):
        with closing(self._open()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            keys = (_identifier(owner), _identifier(scope))
            db.execute("INSERT INTO spaces(owner,scope) VALUES(?,?) ON CONFLICT(owner,scope) DO NOTHING", keys)
            row = db.execute("SELECT id,active FROM spaces WHERE owner=? AND scope=?", keys).fetchone()
            if not row["active"]:
                self._new(db, row["id"])
            yield db, row["id"]

    @staticmethod
    def _conversation(db, space, identity):
        row = db.execute("SELECT * FROM conversations WHERE space=? AND id=?", (space, _identifier(identity))).fetchone()
        if row is None:
            raise ValueError("当前工作区中找不到这段对话。")
        return row

    @staticmethod
    def _cursor(space, conversation, kind, anchor):
        prefix = f"{space}:{conversation}:{kind}:{anchor}"
        return f"{anchor}." + hashlib.sha256(prefix.encode()).hexdigest()[:24]

    @classmethod
    def _page(cls, db, space, conversation, kind, before, size):
        if kind not in _PAGE_SIZES or type(size) is not int or not 1 <= size <= 2 * _PAGE_SIZES[kind]:
            raise ValueError("历史记录的分页参数无效。")
        anchor = None
        if before is not None:
            if not isinstance(before, str) or not re.fullmatch(r"[a-f0-9]{1,32}\.[a-f0-9]{24}", before):
                raise ValueError("历史记录的翻页位置无效。")
            anchor = before.split(".", 1)[0]
            if before != cls._cursor(space, conversation, kind, anchor):
                raise ValueError("翻页位置与当前表或对话不一致。")
        if kind == "messages":
            if anchor and (not anchor.isdecimal() or len(anchor) > 19 or int(anchor) >= 2**63):
                raise ValueError("历史记录的翻页位置无效。")
            bounds = " AND seq<?" if anchor else ""
            args = (conversation, int(anchor), size + 1) if anchor else (conversation, size + 1)
            rows = db.execute("SELECT seq,user_text,assistant_text,created FROM turns WHERE conversation=?" +
                              bounds + " ORDER BY seq DESC LIMIT ?", args).fetchall()
            items = []
            for turn in reversed(rows[:size]):
                for role in ("user", "assistant"):
                    if turn[role + "_text"]:
                        items.append({"role": role, "text": turn[role + "_text"], "created": turn["created"]})
            key = "seq"
        else:
            bounds = ""
            args = (space, size + 1)
            if anchor:
                start = db.execute("SELECT created FROM operations WHERE id=? AND space=?", (anchor, space)).fetchone()
                if not start:
                    raise ValueError("当前工作区中找不到这个翻页位置。")
                bounds = " AND (created,id)<(?,?)"
                args = (space, start["created"], anchor, size + 1)
            rows = db.execute("SELECT id,action,status,summary,details,created FROM operations WHERE space=?" +
                              bounds + " ORDER BY created DESC,id DESC LIMIT ?", args).fetchall()
            items = [{**dict(r), "details": json.loads(r["details"])} for r in rows[:size]]
            key = "id"
        cursor = cls._cursor(space, conversation, kind, rows[size - 1][key]) if len(rows) > size else None
        return {"items": items, "before": cursor}

    @classmethod
    def _view(cls, db, space, full=True, recent_messages=100, recent_operations=40):
        row = db.execute("SELECT * FROM spaces WHERE id=?", (space,)).fetchone()
        identity = row["active"]
        settings = {name: bool(row[name]) for name in _SETTINGS}
        focus = db.execute("SELECT focus FROM conversations WHERE id=? AND space=?", (identity, space)).fetchone()[0]
        prefs = [dict(r) for r in db.execute("SELECT id,text,created FROM preferences WHERE space=? ORDER BY rowid", (space,))]
        view = {"active_id": identity, "preferences": prefs, "settings": settings,
                "focus": json.loads(focus) if focus and settings["save_history"] and settings["resume_focus"] else None}
        if full:
            view["conversations"] = [dict(r) for r in db.execute(
                "SELECT id,title,updated FROM conversations WHERE space=? ORDER BY updated DESC,rowid DESC", (space,))]
            for kind, size in (("messages", recent_messages), ("operations", recent_operations)):
                page = cls._page(db, space, identity, kind, None, size)
                view[kind], view["more_" + kind] = page["items"], page["before"]
        return view

    def active_context(self, owner, scope):
        with self._access(owner, scope) as (db, space):
            return self._view(db, space, full=False)

    def snapshot(self, owner, scope, recent_messages=100, recent_operations=40):
        with self._access(owner, scope) as (db, space):
            return self._view(db, space, recent_messages=recent_messages, recent_operations=recent_operations)

    def page(self, owner, scope, conversation_id, kind, before):
        with self._access(owner, scope) as (db, space):
            active = db.execute("SELECT active FROM spaces WHERE id=?", (space,)).fetchone()[0]
            if active != conversation_id:
                raise ValueError("当前对话已经切换，请重新打开历史。")
            if not isinstance(kind, str) or kind not in _PAGE_SIZES:
                raise ValueError("请选择对话消息或操作记录。")
            return self._page(db, space, active, kind, before, _PAGE_SIZES[kind])

    def new_conversation(self, owner, scope):
        with self._access(owner, scope) as (db, space):
            self._new(db, space)
            return self._view(db, space)

    def select_conversation(self, owner, scope, conversation_id):
        with self._access(owner, scope) as (db, space):
            self._conversation(db, space, conversation_id)
            db.execute("UPDATE spaces SET active=? WHERE id=?", (conversation_id, space))
            return self._view(db, space)

    def delete_conversation(self, owner, scope, conversation_id):
        with self._access(owner, scope) as (db, space):
            self._conversation(db, space, conversation_id)
            db.execute("DELETE FROM conversations WHERE space=? AND id=?", (space, conversation_id))
            if conversation_id == db.execute("SELECT active FROM spaces WHERE id=?", (space,)).fetchone()[0]:
                last = db.execute("SELECT id FROM conversations WHERE space=? ORDER BY updated DESC,rowid DESC LIMIT 1", (space,)).fetchone()
                if last:
                    db.execute("UPDATE spaces SET active=? WHERE id=?", (last["id"], space))
                else:
                    self._new(db, space)
            return self._view(db, space)

    def clear_conversations(self, owner, scope):
        with self._access(owner, scope) as (db, space):
            db.execute("DELETE FROM conversations WHERE space=?", (space,))
            self._new(db, space)
            return self._view(db, space)

    def set_settings(self, owner, scope, **changes):
        if not changes or any(k not in _SETTINGS or type(v) is not bool for k, v in changes.items()):
            raise ValueError("历史设置只接受指定的开关。")
        with self._access(owner, scope) as (db, space):
            for key, enabled in changes.items():
                db.execute(f"UPDATE spaces SET {key}=? WHERE id=?", (enabled, space))
            if changes.get("resume_focus") is False or changes.get("save_history") is False:
                db.execute("UPDATE conversations SET focus=NULL WHERE space=?", (space,))
            return self._view(db, space)

    def add_preference(self, owner, scope, text):
        if not isinstance(text, str) or not (0 < len(text.strip()) <= 240):
            raise ValueError("请输入 1 至 240 字的偏好。")
        with self._access(owner, scope) as (db, space):
            db.execute("INSERT INTO preferences VALUES(?,?,?,?) ON CONFLICT(space,text) DO NOTHING",
                       (uuid.uuid4().hex, space, _display(text.strip(), 240), _now()))
            return self._view(db, space)

    def delete_preference(self, owner, scope, preference_id):
        with self._access(owner, scope) as (db, space):
            changed = db.execute("DELETE FROM preferences WHERE space=? AND id=?", (space, _identifier(preference_id)))
            if not changed.rowcount:
                raise ValueError("当前工作区中找不到这条偏好。")
            return self._view(db, space)

    def set_focus(self, owner, scope, conversation_id, focus):
        with self._access(owner, scope) as (db, space):
            self._conversation(db, space, conversation_id)
            prefs = db.execute("SELECT save_history,resume_focus FROM spaces WHERE id=?", (space,)).fetchone()
            value = _focus(focus) if all(prefs) else None
            db.execute("UPDATE conversations SET focus=? WHERE id=?", (value, conversation_id))
            return self._view(db, space)

    @staticmethod
    def _operation(db, space, conversation_id, request_id, index, operation, created):
        action = _display(operation["action"], 160)
        status = _display(operation["status"], 80)
        summary = _display(operation["summary"], 4096)
        details = _details(operation.get("details", []))
        key = ("event:" + _identifier(operation["key"]) if operation.get("key")
               else f"request:{request_id}:{index}")
        digest = hashlib.sha256(key.encode()).hexdigest()
        db.execute("""INSERT INTO operations(id,space,conversation,event_key,action,status,summary,details,created)
                      VALUES(?,?,?,?,?,?,?,?,?)
                      ON CONFLICT(space,event_key) DO UPDATE SET action=excluded.action,status=excluded.status,
                          summary=excluded.summary,details=excluded.details
                      WHERE operations.status IN ('unverified','failed','preview','partial')
                        AND (excluded.status != 'unverified' OR operations.status = 'unverified')""",
                   (uuid.uuid4().hex, space, conversation_id, digest, action, status, summary, details, created))

    def record_turn(self, owner, scope, conversation_id, request_id, user_text, assistant_text, focus=None, operations=()):
        request_id = _identifier(request_id)
        with self._access(owner, scope) as (db, space):
            self._conversation(db, space, conversation_id)
            row = db.execute("SELECT save_history,resume_focus FROM spaces WHERE id=?", (space,)).fetchone()
            created = _now()
            if row["save_history"]:
                user, reply = _display(user_text, 8192), _display(assistant_text, 32768)
                inserted = db.execute("""INSERT INTO turns(conversation,request_id,created,user_text,assistant_text)
                     VALUES(?,?,?,?,?) ON CONFLICT(conversation,request_id) DO NOTHING""",
                     (conversation_id, request_id, created, user, reply))
                if inserted.rowcount:
                    earlier = db.execute("SELECT 1 FROM turns WHERE conversation=? AND seq<>? LIMIT 1",
                                         (conversation_id, inserted.lastrowid)).fetchone()
                    title = "" if earlier else " ".join(user.split())[:48]
                    try:
                        saved_focus = _focus(focus) if row["resume_focus"] else None
                    except ValueError:
                        # 手工改过的表行可能缺少有效身份；丢弃阅读位置，保留已产生的操作回执。
                        saved_focus = None
                    db.execute("UPDATE conversations SET updated=?, focus=?, title=COALESCE(NULLIF(?,''),title) WHERE id=?",
                               (created, saved_focus, title, conversation_id))
            for index, operation in enumerate(operations):
                self._operation(db, space, conversation_id, request_id, index, operation, created)

    def record_operation(self, owner, scope, conversation_id, request_id, operation):
        with self._access(owner, scope) as (db, space):
            self._conversation(db, space, conversation_id)
            self._operation(db, space, conversation_id, _identifier(request_id), 0, operation, _now())
