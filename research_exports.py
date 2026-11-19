"""从当前可见记录导出书目和阅读笔记，补充的题录须与同一条记录的入口匹配。"""

import csv
import hashlib
import html
import io
import json
import re
import unicodedata

import paper_sources
import source_library
from table_store import _PAPER_LINKS, _PAPER_TITLES, _is_url


FORMATS = {
    "bibtex": ("bib", "application/x-bibtex"),
    "ris": ("ris", "application/x-research-info-systems"),
    "csv": ("csv", "text/csv"),
    "markdown": ("md", "text/markdown"),
}
TYPES = {"journal": ("article", "JOUR", "期刊论文"), "conference": ("inproceedings", "CPAPER", "会议论文")}
_TITLE_NAMES = ("论文标题", "文献标题", "标题", *sorted(_PAPER_TITLES - {"论文标题", "文献标题", "标题"}))
_LINK_NAMES = ("论文链接", "文献链接", "原文链接", *sorted(_PAPER_LINKS - {"论文链接", "文献链接", "原文链接"}))


def _text(value):
    if value is None:
        return ""
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    return str(value).replace("\x00", "")


def _line(text):
    return " ".join(_text(text).split())


def _field(cells, names):
    return next((_line(cells[n]) for n in names if _line(cells.get(n))), "")


def _url(value):
    return value if isinstance(value, str) and not re.search(r'[\s<>"\\\x00-\x1f\x7f]', value) and _is_url(value) else ""


def _paper(table, row, cells):
    title = _field(cells, _TITLE_NAMES)
    anchor = _url(_field(cells, _LINK_NAMES))
    meta, sources = {}, []
    if anchor:
        meta = source_library.stored_metadata(table, row, anchor)
        revision = source_library.arxiv_revision(anchor)
        cached_revision = source_library.arxiv_revision(meta.get("pdf_url"))
        if revision and cached_revision and revision != cached_revision:
            meta = {}
        sources = list(dict.fromkeys(u for u in (anchor, *source_library.known_sources(table, row, anchor))
                                     if _url(u) and (not revision or source_library.arxiv_revision(u) in {"", revision})))
    # 有具体的手填题名且与之前的题录冲突时，使用本行内容，避免把旧作者、刊名误配给新题名。
    if title and meta.get("title") and title.casefold() != _line(meta["title"]).casefold():
        meta = {}
        sources = [anchor] if anchor else []
    title = title or _line(meta.get("title"))
    authors = next((cells[n] for n in ("论文作者", "作者", "Authors", "authors", "Author", "author") if cells.get(n)), "")
    authors = re.split(r"[;；、\r\n]|\s+and\s+", authors) if authors else meta.get("authors", [])
    if not isinstance(authors, list):
        authors = []
    year = _field(cells, ("发表年份", "出版年份", "年份", "Year", "year")) or _line(meta.get("year"))
    doi_field = _field(cells, ("DOI", "doi"))
    d_kind, d_id, _ = paper_sources._identifier(doi_field)
    doi = (d_id if d_kind == "doi" else paper_sources._doi(doi_field)) or paper_sources._doi(meta.get("doi"))
    kind, ident, _ = paper_sources._identifier(anchor)
    if kind == "doi":
        doi = ident
    journal = _field(cells, ("期刊名称", "期刊", "Journal", "journal"))
    conference = _field(cells, ("会议名称", "会议", "Conference", "conference", "booktitle"))
    pub_type = meta.get("publication_type", "")
    stated_type = _field(cells, ("出版类型", "文献类型", "publication_type")).casefold()
    if stated_type:
        pub_type = {"期刊论文": "journal", "期刊": "journal", "article": "journal", "journal": "journal",
                    "journal-article": "journal", "会议论文": "conference", "会议": "conference",
                    "inproceedings": "conference", "proceedings-article": "conference",
                    "conference": "conference"}.get(stated_type, "")
    venue = _field(cells, ("出版来源", "venue", "Venue")) or _line(meta.get("venue"))
    if journal or conference:
        derived = "journal" if journal and not conference else "conference" if conference and not journal else ""
        pub_type = derived if not stated_type or derived == pub_type else ""
        venue = journal or conference
    return dict(title=title, url=anchor, doi=doi, authors=[_line(a) for a in authors if isinstance(a, str) and a.strip()],
                year=year if re.fullmatch(r"[1-9]\d{3}", year) else "", venue=venue, kind=pub_type, sources=sources)


_LATEX = str.maketrans({
    "\\": r"{\textbackslash}", "{": r"{\char123}", "}": r"{\char125}",
    "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#", "_": r"\_",
    "~": r"{\textasciitilde}", "^": r"{\textasciicircum}",
})


def _bibtex(papers):
    entries, keys = [], set()
    for paper in papers:
        # 引文键只含 ASCII；同标题异版本、相同元数据的多条记录都得到不同键。
        seed = paper["doi"] or paper["url"] or paper["title"]
        hint = re.sub(r"[^A-Za-z0-9]", "", unicodedata.normalize("NFKD", paper["title"]).encode("ascii", "ignore").decode())[:18]
        base = (hint or "paper") + hashlib.sha256(seed.encode()).hexdigest()[:8]
        key, suffix = base, 1
        while key in keys:
            suffix += 1
            key = f"{base}_{suffix}"
        keys.add(key)
        entry = TYPES.get(paper["kind"], ("misc",))[0]
        fields = {"title": paper["title"].translate(_LATEX)}
        names = [author.translate(_LATEX) for author in paper["authors"]]
        fields["author"] = " and ".join("{" + a + "}" if re.search(r"\band\b", a, re.I) else a for a in names)
        fields["year"] = paper["year"]
        if entry != "misc":
            fields["journal" if entry == "article" else "booktitle"] = paper["venue"].translate(_LATEX)
        # URL/DOI 由文献管理器逐字读取，保留查询参数和下划线，同时编码会破坏条目边界的字符。
        for name in ("doi", "url"):
            fields[name] = paper[name].replace("{", "%7B").replace("}", "%7D")
        lines = [f"  {tag} = {{{val}}}" for tag, val in fields.items() if val]
        entries.append(f"@{entry}{{{key},\n" + ",\n".join(lines) + "\n}")
    return "\n\n".join(entries) + ("\n" if entries else "")


def _ris(papers):
    lines = []
    for paper in papers:
        tags = [("TY", TYPES.get(paper["kind"], ("", "GEN"))[1]), ("TI", paper["title"])]
        tags.extend(("AU", author) for author in paper["authors"])
        tags.extend((("PY", paper["year"]), ("DO", paper["doi"]), ("UR", paper["url"])))
        if paper["kind"] in TYPES:
            tags.append(("JO" if paper["kind"] == "journal" else "T2", paper["venue"]))
        lines.extend(f"{tag}  - {_line(val)}" for tag, val in tags if val)
        lines.extend(["ER  - ", ""])
    return "\r\n".join(lines) + ("\r\n" if lines else "")


def _excel_text(value):
    raw = _text(value)
    normalized = unicodedata.normalize("NFKC", raw).lstrip(" \t\r\n\ufeff")
    if normalized.startswith(("=", "+", "-", "@")) or raw.startswith(("\t", "\n", "\r")):
        return "'" + raw
    return raw


def _bibliography_fields(paper):
    return {"作者": "; ".join(paper["authors"]), "年份": paper["year"], "DOI": paper["doi"],
            "出版来源": paper["venue"], "出版类型": TYPES[paper["kind"]][2] if paper["kind"] in TYPES else ""}


def _csv(rows, columns):
    out = io.StringIO(newline="")
    writer = csv.writer(out)
    extra = [_bibliography_fields(paper) for _, paper in rows]
    names = list(columns)
    for metadata in extra:
        names.extend(name for name, value in metadata.items() if value and name not in names)
    writer.writerow([_excel_text(n) for n in names])
    for (cells, _), metadata in zip(rows, extra):
        writer.writerow([_excel_text(cells.get(n) or metadata.get(n, "")) for n in names])
    return "\ufeff" + out.getvalue()


def _md(value):
    return re.sub(r"([\\*_\x60\[\]#!|~])", r"\\\1", html.escape(value, quote=False))


def markdown_cell(value):
    """将原始单元格作为文字放入 Markdown 管道表格，保留换行。"""
    return "<br>".join(_md(line) for line in _text(value).splitlines())


def _markdown(rows, table_name):
    lines = ["# " + _md(_line(table_name) or "论文清单"), ""]
    for index, (cells, paper) in enumerate(rows, 1):
        lines.extend(["## " + _md(paper["title"] or f"第 {index} 条记录"), ""])
        metadata = _bibliography_fields(paper)
        for name in dict.fromkeys((*metadata, *cells)):
            value = cells.get(name) or metadata.get(name, "")
            if not _text(value).strip() or (name in _TITLE_NAMES and _line(value) == paper["title"]):
                continue
            if name in _LINK_NAMES and value == paper["url"]:
                continue
            lines.extend(["**" + _md(_line(name)) + "**", ""])
            lines.extend("> " + _md(line) for line in _text(value).splitlines())
            lines.append("")
        if paper["sources"]:
            lines.extend(["**来源链接**", ""])
            lines.extend("- <" + s + ">" for s in paper["sources"])
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def export_records(table, schema, rows, fmt, table_name):
    """仅使用调用者传入的可见行列和本地已核实的来源，返回 UTF-8 可编码文本。"""
    fmt = {"bib": "bibtex", "md": "markdown"}.get(fmt, fmt)
    if fmt not in FORMATS:
        raise ValueError("请选择 BibTeX、RIS、CSV 或 Markdown 格式。")
    columns = [f["name"] for f in schema]
    records, omitted = [], 0
    for row in rows:
        cells = {n: _text(row["fields"].get(n)) for n in columns}
        if not any(value.strip() for value in cells.values()):
            continue
        paper = _paper(table, row, cells)
        if fmt in {"bibtex", "ris"} and not any(paper[k] for k in ("title", "url", "doi")):
            omitted += 1
            continue
        records.append((cells, paper))
    if fmt == "csv":
        content = _csv(records, columns)
    elif fmt == "markdown":
        content = _markdown(records, table_name)
    else:
        papers = [p for _, p in records]
        content = _bibtex(papers) if fmt == "bibtex" else _ris(papers)
    ext, mime = FORMATS[fmt]
    result = {"content": content, "extension": ext, "mime": mime, "count": len(records)}
    notices = []
    if omitted:
        notices.append(f"{omitted} 条记录缺少题名、论文链接或 DOI，已略过。")
    if fmt in {"bibtex", "ris"}:
        incomplete = sum(not p["year"] or not p["authors"] for _, p in records)
        if incomplete:
            notices.append(f"{incomplete} 条引文缺少作者或年份，已保留可核实的字段。")
    if notices:
        result["notice"] = " ".join(notices)
    return result
