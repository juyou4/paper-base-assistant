"""按同一官方论文入口和已解析 PDF 的指纹，恢复本机的会议题录。"""

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

import paper_sources
import papers


def _addresses(entry):
    kind, _, landing = paper_sources._identifier(entry)
    if kind == "cvf":
        pdf = landing.replace("/html/", "/papers/").removesuffix(".html") + ".pdf"
    elif kind == "acl":
        pdf = landing.rstrip("/") + ".pdf"
    else:
        return None
    if entry == pdf or entry == landing or (kind == "acl" and entry == landing.rstrip("/")):
        return landing, pdf
    return None


def _record_path(directory, entry):
    key = hashlib.sha256(entry.encode("utf-8")).hexdigest()
    return Path(directory) / "cache" / ("proceedings-" + key + ".json")


BIBLIOGRAPHY = ("title", "url", "pdf_url", "doi", "source_version", "year", "venue", "publication_type")


def _valid(record, entry):
    addresses = _addresses(entry)
    if not addresses or not isinstance(record, dict):
        return False
    landing, pdf = addresses
    strings = (*BIBLIOGRAPHY, "entry", "sha256")
    if set(record) != {*strings, "version", "authors"} or not all(isinstance(record[k], str) for k in strings):
        return False
    doi = record["doi"]
    authors = record.get("authors")
    return (type(record.get("version")) is int and record["version"] == 1 and record["entry"] == entry
            and record["pdf_url"] == pdf and record["title"].strip() and len(record["title"]) <= 4096
            and record["source_version"].strip() and (not doi or doi == paper_sources._doi(doi))
            and (record["url"] == landing or (doi and record["url"] == paper_sources.doi_url(doi)))
            and re.fullmatch(r"[a-f0-9]{64}", record["sha256"])
            and isinstance(authors, list) and all(isinstance(author, str) for author in authors))


def remember(directory, entry, paper, items):
    addresses = _addresses(entry)
    if not addresses:
        return
    # 只有刚从实际文件提取并成功绑定了原页的项才能建立记录。
    digest = next((i.get("pdf_sha256") for i in items if i.get("pdf_page")
                   and i["source_url"].partition("#")[0] == addresses[1]), None)
    record = {k: paper.get(k, "") for k in BIBLIOGRAPHY}
    record.update(version=1, entry=entry, sha256=digest, authors=paper.get("authors", []))
    if not _valid(record, entry):
        return
    target = _record_path(directory, entry)
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent, mode="w", encoding="utf-8", delete=False) as f:
            tmp = Path(f.name)
            json.dump(record, f, ensure_ascii=False)
        os.replace(tmp, target)
    except OSError:
        pass  # 可选缓存写入失败时仍返回本次在线提取结果。
    finally:
        if tmp is not None:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass


def restore(directory, entry):
    addresses = _addresses(entry)
    if not addresses:
        return None
    try:
        with _record_path(directory, entry).open("rb") as f:
            data = f.read(65537)
        if len(data) > 65536:
            return None
        record = json.loads(data)
        if not _valid(record, entry):
            return None
        path = papers.cached_pdf(record, directory)
        if path is None:
            return None
        digest = hashlib.sha256()
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                digest.update(chunk)
        if digest.hexdigest() != record["sha256"]:
            return None
    except (ValueError, OSError, RecursionError):
        return None
    aliases = list(dict.fromkeys([entry, addresses[0], record["url"]]))
    if record["doi"]:
        aliases = list(dict.fromkeys([*aliases, paper_sources.doi_url(record["doi"])]))
    record.update(aliases=aliases, evidence_urls=[*aliases, addresses[1]],
                  source_version=record["source_version"] + " · 此前保存的会议版本，已核对 PDF 指纹并重新提取")
    return record, path
