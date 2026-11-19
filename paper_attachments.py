"""将本地 PDF 按浏览器会话存储，显式关联论文后才可作为该论文的证据。"""

import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
from contextlib import closing, contextmanager
from contextvars import ContextVar
from pathlib import Path
from urllib.parse import urlsplit

import papers


MAX_BYTES = 50 * 1024 * 1024
DIGEST = re.compile(r"[0-9a-f]{64}")
PAGE = re.compile(r"/api/attachments/([0-9a-f]{64})#page=([1-9]\d*)")
DOI = re.compile(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", re.I)
ACTIVE = ContextVar("paper_attachment_library", default=None)


def normalize_ref(value):
    value = str(value or "").strip()
    if (not value or len(value) > 2048 or re.search(r"\s", re.sub(r"^(?:arxiv|doi)\s*:\s*", "", value, flags=re.I))
            or len(re.findall(r"https?://", value, re.I)) > 1):
        raise ValueError("请为附件选择一篇论文，只填写一个标识或论文链接。")
    if DOI.fullmatch(value):
        value = "https://doi.org/" + value
    arxiv, url = papers._find_target(value)
    if arxiv:
        return "https://arxiv.org/abs/" + arxiv
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or not parsed.hostname or "." not in parsed.hostname
            or parsed.hostname.endswith((".localhost", ".local")) or re.fullmatch(r"[\d.]+", parsed.hostname)
            or parsed.username or parsed.password):
        raise ValueError("请指定论文的 HTTPS 出版页面、DOI 或 arXiv 编号。")
    if parsed.hostname in {"doi.org", "dx.doi.org"}:
        return "https://doi.org/" + parsed.path.lstrip("/").casefold()
    return url


def _consistent_identifier(url, candidates):
    # 出版页（如 ACM、Springer）与 doi.org 上的同一 DOI 是可直接核对的标识。
    target_keys = papers.paper_keys(url)
    for prefix in ("doi:", "arxiv:"):
        target = {key for key in target_keys if key.startswith(prefix)}
        if not target:
            continue
        known = [candidate for candidate in candidates
                 if any(key.startswith(prefix) for key in papers.paper_keys(candidate))]
        if not known:
            continue
        matching = [candidate for candidate in known if papers.paper_keys(candidate) & target]
        if not matching:
            return False
        if prefix == "arxiv:":
            target_id, _ = papers._find_target(url)
            revisions = [papers._find_target(candidate)[0].lower() for candidate in matching]
            pinned = {value for value in revisions if re.search(r"v\d+$", value)}
            if pinned and re.search(r"v\d+$", target_id, re.I) and target_id.lower() not in pinned:
                return False
    return True


def _inspect(path):
    title, first_page = "", ""
    try:
        import pdfplumber
    except ImportError:
        pdfplumber = None
    if pdfplumber:
        try:
            with pdfplumber.open(path, pages=[1]) as doc:
                if not doc.pages:
                    raise ValueError("empty")
                title = str((doc.metadata or {}).get("Title") or "")
                first_page = doc.pages[0].extract_text() or ""
        except Exception as exc:
            raise ValueError("无法读取 PDF 首页，请检查文件是否损坏或需要密码。") from exc
    else:
        command = shutil.which("pdftotext")
        if not command:
            raise ValueError("校验附件需要安装 pdfplumber 或 Poppler 的 pdftotext。")
        try:
            result = subprocess.run([command, "-f", "1", "-l", "1", "-enc", "UTF-8", str(path), "-"],
                                    check=True, timeout=18, capture_output=True)
            first_page = result.stdout.decode("utf-8", errors="replace")
        except (OSError, subprocess.SubprocessError) as exc:
            raise ValueError("无法读取 PDF 首页，请检查文件是否损坏或需要密码。") from exc
    # 首页标识也可能是正文中的引用。所有候选必须经用户确认关联。
    first_page = re.split(r"(?im)^\s*(?:references|bibliography|参考文献)\s*$", first_page)[0][:8000]
    candidates = []
    for item in re.finditer(r"(?:https?://(?:dx\.)?doi\.org/|\bdoi\s*[:：]\s*)(" + DOI.pattern + ")",
                            first_page, re.I):
        candidates.append(normalize_ref(item[1].rstrip(".,")))
    for item in re.finditer(r"\barxiv\s*:\s*(" + papers.ARXIV_ID + r")", first_page, re.I):
        candidates.append(normalize_ref(item[1]))
    return " ".join(title.split())[:400], list(dict.fromkeys(candidates))[:5]


class AttachmentLibrary:
    def __init__(self, directory, cookie):
        owner = hashlib.sha256(cookie.encode()).hexdigest()
        self.directory = Path(directory) / "attachments" / owner
        self.index = self.directory / "index.sqlite3"

    def _db(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.index, timeout=12)
        db.row_factory = sqlite3.Row
        db.execute("""CREATE TABLE IF NOT EXISTS files (
            digest TEXT PRIMARY KEY, filename TEXT NOT NULL, suggested_title TEXT NOT NULL,
            candidates TEXT NOT NULL, paper_url TEXT UNIQUE, title TEXT)""")
        return db

    def _file(self, digest):
        if not isinstance(digest, str) or not DIGEST.fullmatch(digest):
            raise ValueError("附件标识无效。")
        return self.directory / (digest + ".pdf")

    @staticmethod
    def _public(row):
        return {"id": row["digest"], "filename": row["filename"], "title": row["title"] or row["suggested_title"],
                "candidates": json.loads(row["candidates"]), "paper_url": row["paper_url"] or "",
                "viewer": "/api/attachments/" + row["digest"]}

    def list(self):
        if not self.index.is_file():
            return []
        with closing(self._db()) as db:
            return [self._public(row) for row in db.execute("SELECT * FROM files ORDER BY rowid DESC")]

    def receive(self, content, filename):
        if not 0 < len(content) <= MAX_BYTES or not content.startswith(b"%PDF-") or b"%%EOF" not in content[-4096:]:
            raise ValueError("请选择完整的 PDF 文件，文件大小上限为 50 MB。")
        digest = hashlib.sha256(content).hexdigest()
        destination = self._file(digest)
        filename = str(filename or "document.pdf").replace("\\", "/").split("/")[-1]
        filename = re.sub(r"[\x00-\x1f\x7f]", "", filename)[:200] or "document.pdf"
        with closing(self._db()) as db:
            row = db.execute("SELECT * FROM files WHERE digest=?", (digest,)).fetchone()
            if row and destination.is_file():
                return self._public(row)
            with tempfile.NamedTemporaryFile(dir=self.directory, suffix=".pdf", delete=False) as handle:
                tmp = Path(handle.name)
            try:
                tmp.write_bytes(content)
                title, candidates = _inspect(tmp)
                os.replace(tmp, destination)
                with db:
                    db.execute("INSERT OR IGNORE INTO files VALUES(?,?,?,?,NULL,NULL)",
                               (digest, filename, title, json.dumps(candidates)))
            finally:
                tmp.unlink(missing_ok=True)
            return self._public(db.execute("SELECT * FROM files WHERE digest=?", (digest,)).fetchone())

    def path(self, digest):
        path = self._file(digest)
        if not path.is_file() or not self.index.is_file():
            raise FileNotFoundError("当前会话没有这份附件。")
        with closing(self._db()) as db:
            if not db.execute("SELECT 1 FROM files WHERE digest=?", (digest,)).fetchone():
                raise FileNotFoundError("当前会话没有这份附件。")
        return path

    def bind(self, digest, paper_url, title):
        self.path(digest)
        url = normalize_ref(paper_url)
        with closing(self._db()) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM files WHERE digest=?", (digest,)).fetchone()
            title = str(title or row["suggested_title"] or "").strip()[:400]
            if not title:
                raise ValueError("请核对并补充论文标题，再关联附件。")
            if row["paper_url"] and row["paper_url"] != url:
                raise ValueError("这份 PDF 已关联其他论文，请核对原文件和目标。")
            if not _consistent_identifier(url, json.loads(row["candidates"])):
                raise ValueError("PDF 首页的标识与目标论文或版本不一致，请核对首页与论文链接。")
            if db.execute("SELECT 1 FROM files WHERE paper_url=? AND digest<>?", (url, digest)).fetchone():
                raise ValueError("该论文地址已关联另一版本的 PDF，请核对版本并提供对应版本的论文链接。")
            db.execute("UPDATE files SET paper_url=?,title=? WHERE digest=?", (url, title, digest))
            return self._public(db.execute("SELECT * FROM files WHERE digest=?", (digest,)).fetchone())

    def for_paper(self, url):
        if not self.index.is_file():
            return None
        try:
            key = normalize_ref(url)
        except ValueError:
            return None
        with closing(self._db()) as db:
            row = db.execute("SELECT * FROM files WHERE paper_url=?", (key,)).fetchone()
            if row and self._file(row["digest"]).is_file():
                return self._public(row)
        return None


@contextmanager
def session_scope(directory, cookie):
    token = ACTIVE.set(AttachmentLibrary(directory, cookie))
    try:
        yield
    finally:
        ACTIVE.reset(token)


def bound_file(url):
    library = ACTIVE.get()
    if library:
        item = library.for_paper(url)
        if item:
            return item, library.path(item["id"])
    return None


def matches_source(paper_url, address):
    match = PAGE.fullmatch(address or "")
    if not match:
        return False
    binding = bound_file(paper_url)
    return bool(binding and binding[0]["id"] == match[1])
