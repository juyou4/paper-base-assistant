"""记住已核实的出版入口；每次复用时用当前表行重新核对身份。"""

import json
import re

import papers
from table_store import TargetChanged, _PAPER_LINKS, _identity, _is_url


def _scope(table):
    return getattr(table, "_scope", "local")


def _registry(table):
    table._db.execute("""CREATE TABLE IF NOT EXISTS library_sources (
        scope TEXT NOT NULL, record_id TEXT NOT NULL, source_key TEXT NOT NULL,
        source_url TEXT NOT NULL, anchor_key TEXT NOT NULL,
        PRIMARY KEY(scope,record_id,source_key,source_url)
    )""")
    table._db.execute("""CREATE TABLE IF NOT EXISTS library_metadata (
        scope TEXT NOT NULL, record_id TEXT NOT NULL, anchor_key TEXT NOT NULL, metadata TEXT NOT NULL,
        PRIMARY KEY(scope,record_id,anchor_key)
    )""")


def _keys(url):
    return papers.paper_keys(url) if isinstance(url, str) and _is_url(url) else set()


def known_sources(table, row, anchor):
    with table._lock:
        if not table._db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='library_sources'").fetchone():
            return []
        return [r[0] for r in table._db.execute(
            "SELECT DISTINCT source_url FROM library_sources WHERE scope=? AND record_id=? AND anchor_key=?",
            (_scope(table), row["id"], _identity(anchor)))]


def stored_metadata(table, row, anchor):
    with table._lock:
        if not table._db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='library_metadata'").fetchone():
            return {}
        hit = table._db.execute("SELECT metadata FROM library_metadata WHERE scope=? AND record_id=? AND anchor_key=?",
                                (_scope(table), row["id"], _identity(anchor))).fetchone()
        return json.loads(hit[0]) if hit else {}


def existing_source(table, urls, rows=None):
    match = existing_record(table, urls, rows)
    return match[1] if match else None


def existing_record(table, urls, rows=None):
    keys = set().union(*(_keys(url) for url in urls))
    if not keys:
        return None
    with table._lock:
        _registry(table)
        marks = ",".join("?" for _ in keys)
        matches = table._db.execute(
            f"SELECT record_id,anchor_key FROM library_sources WHERE scope=? AND source_key IN ({marks})",
            (_scope(table), *keys)).fetchall()
        anchors = {row["record_id"]: row["anchor_key"] for row in matches}
        candidates = {}
        for row in table.rows() if rows is None else rows:
            links = [v for n, v in row["fields"].items() if n in _PAPER_LINKS and isinstance(v, str) and _is_url(v)]
            anchor = anchors.get(row["id"])
            if anchor:
                original = next((url for url in links if _identity(url) == anchor), None)
                if not original:
                    raise TargetChanged("已收藏记录的论文链接发生变化，请先核对目标行。")
                candidates[row["id"]] = (row, original)
            else:
                for link in links:
                    if _keys(link) & keys:
                        candidates[row["id"]] = (row, link)
                        break
        if len(candidates) > 1:
            raise TargetChanged("多个已收藏记录与这篇论文的来源相符，请先核对重复行。")
        return next(iter(candidates.values()), None)


def arxiv_revision(address):
    """返回能从官方地址确定的完整修订号，作品级编号不能当作版本证据。"""
    if not address or not any(k.startswith("arxiv:") for k in papers.paper_keys(address)):
        return ""
    arxiv, _ = papers._find_target(address)
    return arxiv.casefold() if arxiv and re.search(r"v\d+$", arxiv, re.I) else ""


def pinned_metadata(table, row, anchor, title):
    revision = arxiv_revision(anchor)
    stored = stored_metadata(table, row, anchor)
    if arxiv_revision(stored.get("pdf_url")) != revision:
        stored = {}
    return {**stored, "url": anchor, "key": _identity(anchor), "title": title, "source_note": "",
            "pdf_url": "https://arxiv.org/pdf/" + revision, "source_version": "arXiv " + revision}


def version_target_id(table, record_id, request_id):
    """回放一次已新增的请求时，保留新增前“没有目标行”的幂等前提。"""
    with table._lock:
        if hasattr(table, "_scope"):
            prior = table._db.execute("SELECT action,record_id FROM fs_requests WHERE scope=? AND id=?",
                                      (table._scope, request_id)).fetchone()
        else:
            prior = table._db.execute("SELECT action,record_id FROM demo_requests WHERE id=?", (request_id,)).fetchone()
    return None if prior and prior["action"] == "created" and prior["record_id"] == record_id else record_id


def remember(table, row, paper, aliases):
    """索引与真实链接列有绑定的记录，旧表或自定义列仍可沿用原有查重流程。"""
    anchors = [v for n, v in row["fields"].items() if n in _PAPER_LINKS and isinstance(v, str) and _is_url(v)]
    anchor = next((url for url in anchors if _identity(url) == _identity(paper["url"])), None)
    if not anchor:
        return
    with table._lock, table._db:
        _registry(table)
        entries = [(_scope(table), row["id"], key, url, _identity(anchor))
                   for url in dict.fromkeys([anchor, *aliases]) for key in _keys(url)]
        table._db.executemany("INSERT OR IGNORE INTO library_sources VALUES(?,?,?,?,?)", entries)
        previous = stored_metadata(table, row, anchor)
        for name in ("title", "authors", "year", "doi", "pdf_url", "source_version", "source_note", "venue", "publication_type"):
            if paper.get(name):
                previous[name] = paper[name]
        if previous.get("pdf_url"):
            previous.pop("source_note", None)
        table._db.execute("INSERT OR REPLACE INTO library_metadata VALUES(?,?,?,?)",
                          (_scope(table), row["id"], _identity(anchor), json.dumps(previous, ensure_ascii=False)))


def card(paper, aliases, row=None):
    from paper_attachments import bound_file
    urls = list(dict.fromkeys(url for url in aliases if isinstance(url, str) and _is_url(url)))
    revision = arxiv_revision(paper["url"])
    local = next((item for url in urls if url.lower().startswith("https://")
                  and (not revision or arxiv_revision(url) == revision) and (item := bound_file(url))), None)
    return {"title": paper.get("title") or "题名待补", "url": paper["url"],
            "authors": paper.get("authors") or [], "year": paper.get("year") or "",
            "sources": urls, "record_id": row["id"] if row else "",
            "version": "用户关联的 PDF" if local else paper.get("source_version", ""),
            "notice": "" if local or paper.get("pdf_url") else paper.get("source_note", ""),
            "fulltext_url": "/api/attachments/" + local[0]["id"] if local else paper.get("pdf_url", ""),
            "reading_status": "已关联本地 PDF" if local else ("有全文入口" if paper.get("pdf_url") else "待补全文")}
