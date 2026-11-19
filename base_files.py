"""新建独立多维表格文件；按确认编号持久化云文件与目标数据表的回执。"""

import hashlib
import json
import re
import tempfile
from pathlib import Path
from urllib.parse import quote

from table_store import FeishuTable, LocalTable, TableError, _RejectedChange, validate_table_definition
from tools.feishu_setup import TargetLink, _parse_target


def _local_path(source, key):
    path = source._db.execute("PRAGMA database_list").fetchone()["file"]
    if path:
        root = Path(path).parent / "created-bases"
    else:
        if not hasattr(source, "_base_files_directory"):
            source._base_files_directory = tempfile.TemporaryDirectory(prefix="paper-base-files-")
        root = Path(source._base_files_directory.name)
    owner = source._db.execute("SELECT db_name FROM local_base_origins WHERE id=?", (key,)).fetchone()
    if owner:
        scoped_key = json.dumps([owner["db_name"], key], ensure_ascii=False).encode()
        return root / ("v2-" + hashlib.sha256(scoped_key).hexdigest() + ".sqlite3")
    # 旧确认沿用原文件名，保留建表后用户添加的内容。
    return root / (hashlib.sha256(key.encode()).hexdigest() + ".sqlite3")


def _file_url(url, token, table_id=""):
    # 新文件只接受与创建回执中 token 一致的飞书官方地址。
    try:
        target = _parse_target(url)
    except ValueError:
        raise TableError("飞书创建回执缺少可核验的文件地址，请在云空间核对。") from None
    if target.kind != "base" or target.token != token:
        raise TableError("飞书返回的文件地址与新文件标识不一致，请在云空间核对。")
    return TargetLink(target.host, "base", token, table_id, "").address()


def _reserve(source, key, fingerprint):
    with source._db:
        source._db.execute("""CREATE TABLE IF NOT EXISTS base_file_requests (
            id TEXT PRIMARY KEY, plan TEXT NOT NULL, token TEXT, url TEXT,
            default_table_id TEXT, table_id TEXT)""")
        first = source._db.execute("INSERT OR IGNORE INTO base_file_requests(id,plan) VALUES (?,?)",
                                   (key, fingerprint)).rowcount == 1
        if isinstance(source, LocalTable):
            source._db.execute("CREATE TABLE IF NOT EXISTS local_base_origins (id TEXT PRIMARY KEY, db_name TEXT NOT NULL)")
            if first:
                # 与确认记录一起提交。即使新文件创建中断，重试也能使用同一条路径。
                db_name = Path(source._db.execute("PRAGMA database_list").fetchone()["file"]).name
                source._db.execute("INSERT INTO local_base_origins (id,db_name) VALUES (?,?)", (key, db_name))
        receipt = source._db.execute("SELECT * FROM base_file_requests WHERE id=?", (key,)).fetchone()
    if receipt["plan"] != fingerprint:
        raise TableError("该确认编号已用于其他建表方案，请重新预览。")
    return first, receipt


def _reserve_default_name(base, table_name, default_id):
    # 仅处理本次创建回执明确标出的系统默认表，不删除表、字段或记录。
    if not default_id:
        return
    matches = [t for t in base.list_tables() if t["name"] == table_name]
    if not any(t["id"] == default_id for t in matches):
        return
    current = base.open_table(default_id)
    if any(any(value not in ("", None, []) for value in row["fields"].values()) for row in current.rows()):
        raise TableError("同名的默认数据表已有内容，请在飞书中为它改名后重试原确认。")
    backup = "平台默认表（" + default_id + "）"
    current._request("PATCH", "/bitable/v1/apps/" + quote(base.app_token, safe="") +
                     "/tables/" + quote(default_id, safe=""), {"name": backup})
    if not any(t["id"] == default_id and t["name"] == backup for t in base.list_tables()):
        raise TableError("尚未回读到系统默认表的新名称，请稍后重试原确认。")


def create_base_file(table, name, table_name, fields, request_id):
    """返回新工作表和该表的飞书 URL；本地模式的 URL 为空。"""
    if (not isinstance(name, str) or not name.strip() or len(name) > 255
            or not all(char.isprintable() for char in name)):
        raise TableError("请提供 1 至 255 个字符的多维表格文件名。")
    if not isinstance(request_id, str) or not request_id.strip():
        raise TableError("缺少新建多维表格的确认编号。")
    if not isinstance(table, (LocalTable, FeishuTable)):
        raise TableError("当前连接尚不支持创建独立的多维表格。")
    name = name.strip()
    validate_table_definition(table_name, fields)
    scope = [type(table).__name__, getattr(table, "_app_id", ""), getattr(table, "_scope", "")]
    fingerprint = json.dumps([scope, name, table_name, fields], ensure_ascii=False, sort_keys=True)
    with table._lock:
        first, receipt = _reserve(table, request_id, fingerprint)
        if isinstance(table, LocalTable):
            path = _local_path(table, request_id)
            if receipt["table_id"]:
                if not path.is_file():
                    raise TableError("找不到已创建的本地多维表格文件，请检查存储目录。")
                return LocalTable(path), ""
            dest = LocalTable.new_base(path, table_name, fields)
            with table._db:
                table._db.execute("UPDATE base_file_requests SET table_id=? WHERE id=?",
                                  (dest.table_id, request_id))
            return dest, ""

        token, url, default_id = receipt["token"], receipt["url"], receipt["default_table_id"]
        if first:
            # 此 OpenAPI 没有 client_token。先占用确认编号，超时后不能凭同名认领文件。
            try:
                data = table._request("POST", "/bitable/v1/apps", {"name": name})
            except _RejectedChange as exc:
                with table._db:
                    table._db.execute("DELETE FROM base_file_requests WHERE id=? AND token IS NULL", (request_id,))
                raise TableError(f"飞书拒绝新建文件。请检查当前身份的建表权限后再确认。{exc}") from exc
            except TableError as exc:
                raise TableError(f"飞书未返回新文件回执。请检查创建权限及云空间，再决定是否发起新请求。{exc}") from exc
            payload = data.get("data") if isinstance(data, dict) else None
            app = payload.get("app") if isinstance(payload, dict) else None
            if not isinstance(app, dict) or app.get("name", name) != name:
                raise TableError("飞书的创建回执与确认的文件不符，请先在云空间核对。")
            token, url, default_id = app.get("app_token"), app.get("url"), app.get("default_table_id")
            if not isinstance(token, str) or not re.fullmatch(r"[a-zA-Z0-9_-]+", token):
                raise TableError("飞书未返回新文件标识，请先到云空间核对，不能重复发送此确认。")
            if not isinstance(default_id, str) or not re.fullmatch(r"tbl[a-zA-Z0-9_-]+", default_id):
                default_id = ""
            with table._db:
                table._db.execute("UPDATE base_file_requests SET token=?,url=?,default_table_id=? WHERE id=?",
                                  (token, url if isinstance(url, str) else "", default_id, request_id))
        elif not token:
            raise TableError("新文件创建结果待核对，本确认不会再次新建。请先查看飞书云空间。")

        base_url = _file_url(url, token)
        base = table.for_base(token)
        try:
            if receipt["table_id"]:
                dest = base.open_table(receipt["table_id"])
            else:
                _reserve_default_name(base, table_name, default_id)
                dest = base.create_table(table_name, fields, request_id)
                with table._db:
                    table._db.execute("UPDATE base_file_requests SET table_id=? WHERE id=?",
                                      (dest.table_id, request_id))
        except (TableError, OSError, ValueError) as exc:
            raise TableError(f"文件「{name}」已创建。工作表尚待核验，可用原确认继续。\n{base_url}\n{exc}") from exc
        return dest, _file_url(url, token, dest.table_id)
