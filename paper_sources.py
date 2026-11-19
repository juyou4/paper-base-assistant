"""论文出版标识、官方题录与可核验的公开全文入口。仅依赖标准库。"""

import json
import re
import unicodedata
from functools import lru_cache
from html.parser import HTMLParser
from urllib.error import HTTPError
from urllib.parse import parse_qs, quote, unquote, urlencode, urljoin, urlsplit, urlunsplit


ARXIV_ID = r"(?:\d{4}\.\d{4,5}|[a-z][a-z0-9.-]*/\d{7})(?:v[1-9]\d*)?"
ARXIV_HOSTS = {"arxiv.org", "www.arxiv.org", "export.arxiv.org", "abs.arxiv.org"}
DOI = re.compile(r"10\.\d{4,9}/[^\s<>\x22?#\\]+", re.I)
CVF = "openaccess.thecvf.com"
IEEE = "ieeexplore.ieee.org"
REVIEW = "openreview.net"
CROSSREF = "https://api.crossref.org/works"
HOSTS = ARXIV_HOSTS | {CVF, IEEE, REVIEW, "api.openreview.net", "api2.openreview.net",
                       "api.crossref.org", "aclanthology.org"}
CVF_PATH = re.compile(r"(/(?:content_[A-Za-z0-9_]+|content/[A-Za-z0-9_]+)(?:/[A-Za-z0-9_-]+)?)/(html|papers)/([^/\\?#%]+_paper)\.(html|pdf)$")
REVIEW_ID = re.compile(r"[A-Za-z0-9_-]{3,100}")
ACL_ID = re.compile(r"(?:[A-Z]\d\d-\d{3,5}|\d{4}\.[A-Za-z0-9.-]+\.\d+)", re.I)


def _doi(value):
    return value.casefold() if isinstance(value, str) and len(value) <= 256 and DOI.fullmatch(value) else ""


def doi_url(value):
    return "https://doi.org/" + quote(value, safe="/.:()_-")


def _identifier(url):
    try:
        loc = urlsplit(url)
        if (loc.scheme not in {"http", "https"} or not loc.hostname or loc.username is not None
                or loc.password is not None or loc.port not in {None, 443, 80}):
            return "", "", ""
    except ValueError:
        return "", "", ""
    host, path = loc.hostname.lower(), loc.path
    if host in {"doi.org", "dx.doi.org", "www.doi.org"}:
        doi = _doi(unquote(path.lstrip("/")))
        if doi:
            ax = re.fullmatch(r"10\.48550/arxiv\.(" + ARXIV_ID + ")", doi, re.I)
            if ax:
                return "arxiv", ax[1], "https://arxiv.org/abs/" + ax[1]
            return "doi", doi, doi_url(doi)
    # 出版页明示 DOI 时只查注册题录，不请求站点的订阅全文。
    publisher_paths = {
        "dl.acm.org": r"/doi/(?:(?:abs|full|pdf|epdf)/)?(10\.1145/.+)",
        "link.springer.com": r"/(?:article|chapter)/(10\.1007/.+)",
    }
    publisher = publisher_paths.get(host)
    if publisher:
        match = re.fullmatch(publisher, unquote(path).rstrip("/"), re.I)
        if match and (doi := _doi(match[1])):
            return "doi", doi, doi_url(doi)
    if host in ARXIV_HOSTS:
        ax = re.fullmatch(r"/(?:abs|pdf|html)/(" + ARXIV_ID + r")(?:\.pdf)?/?", path, re.I)
        if ax:
            return "arxiv", ax[1].lower(), "https://arxiv.org/abs/" + ax[1].lower()
    if host == CVF:
        cv = CVF_PATH.fullmatch(path)
        if cv and ((cv[2], cv[4]) in {("html", "html"), ("papers", "pdf")}):
            landing = cv[1] + "/html/" + cv[3] + ".html"
            return "cvf", landing, "https://" + CVF + landing
    if host == IEEE:
        ieee = re.fullmatch(r"/(?:abstract/)?document/(\d{1,12})/?", path)
        if ieee:
            return "ieee", ieee[1], "https://" + IEEE + "/document/" + ieee[1]
        if path.lower() in {"/stamp/stamp.jsp", "/stamp/stamp.jsp/"}:
            ids = parse_qs(loc.query).get("arnumber", [])
            if len(ids) == 1 and re.fullmatch(r"\d{1,12}", ids[0]):
                return "ieee", ids[0], "https://" + IEEE + "/document/" + ids[0]
    if host == REVIEW and path.rstrip("/") in {"/forum", "/pdf"}:
        ids = parse_qs(loc.query).get("id", [])
        if len(ids) == 1 and REVIEW_ID.fullmatch(ids[0]):
            return "openreview", ids[0], "https://" + REVIEW + "/forum?id=" + ids[0]
    if host == "aclanthology.org" or (host in {"www.aclweb.org", "aclweb.org"} and path.startswith("/anthology/")):
        ident = path.removeprefix("/anthology/").strip("/").removesuffix(".pdf")
        if ACL_ID.fullmatch(ident):
            if re.fullmatch(r"[A-Za-z]\d\d-\d+", ident):
                ident = ident.upper()
            return "acl", ident, "https://aclanthology.org/" + ident + "/"
    address = urlunsplit((loc.scheme, loc.netloc.lower(), path or "/", loc.query, ""))
    return "url", address, address


def paper_keys(value):
    """返回作品级身份键；版本、页码、证据链接由调用者分别核对。"""
    if not isinstance(value, str):
        return set()
    value = value.strip()
    if value.startswith("url:"):
        value = value[4:]
    for prefix in ("ieee:", "cvf:", "openreview:"):
        if value.startswith(prefix):
            if prefix == "ieee:":
                value = "https://" + IEEE + "/document/" + value[5:]
            elif prefix == "cvf:":
                value = "https://" + CVF + value[4:]
            else:
                value = "https://" + REVIEW + "/forum?id=" + value[11:]
            break
    if value.lower().startswith("doi:"):
        value = value[4:].strip()
    if _doi(value):
        value = doi_url(value)
    if value.lower().startswith("arxiv:"):
        value = value[6:].strip()
    if re.fullmatch(ARXIV_ID, value, re.I):
        value = "https://arxiv.org/abs/" + value
    kind, ident, canonical = _identifier(value)
    if kind == "arxiv":
        return {"arxiv:" + re.sub(r"v\d+$", "", ident, flags=re.I)}
    if kind in {"acl", "url"}:
        return {"url:" + canonical}
    return {kind + ":" + ident} if kind else set()


def trusted_pdf_name(url):
    """限官方论文附件路径，避免将普通网页、补充材料或第三方链接作为主文。"""
    loc = urlsplit(url)
    if loc.scheme != "https" or loc.hostname not in {CVF, REVIEW}:
        return False
    if loc.hostname == CVF:
        return _identifier(url)[0] == "cvf" and loc.path.endswith(".pdf")
    return ((loc.path == "/pdf" and _identifier(url)[0] == "openreview")
            or bool(re.fullmatch(r"/pdf/(?:[0-9a-f]{40}|[0-9a-f]{64})\.pdf", loc.path)))


def _plain(value):
    return " ".join(value.split()) if isinstance(value, str) else ""


class _Page(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.meta = {}
        self.authors = []
        self.links = []
        self.abstract = []
        self.depth = 0
        self.anchor = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "meta":
            name = (a.get("name") or a.get("property") or "").lower()
            val = _plain(a.get("content", ""))
            if name == "citation_author" and val:
                self.authors.append(val)
            else:
                self.meta[name] = val
        elif tag == "div" and (self.depth or a.get("id") == "abstract"):
            self.depth += 1
        elif tag == "a":
            self.anchor = (a.get("href", ""), [])

    def handle_data(self, data):
        if self.depth:
            self.abstract.append(data)
        if self.anchor:
            self.anchor[1].append(data)

    def handle_endtag(self, tag):
        if tag == "div" and self.depth:
            self.depth -= 1
        elif tag == "a" and self.anchor:
            href, nodes = self.anchor
            self.links.append((href, _plain("".join(nodes))))
            self.anchor = None


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.text = []

    def handle_data(self, text):
        self.text.append(text)


def _text(html):
    p = _Text()
    p.feed(html if isinstance(html, str) else "")
    return _plain(" ".join(p.text))


def _get(read, url, limit=2 * 1024 * 1024):
    try:
        return read(url, limit)
    except (ValueError, OSError):
        return ""


def _json(raw):
    try:
        doc = json.loads(raw)
        return doc if isinstance(doc, dict) else {}
    except (TypeError, ValueError):
        return {}


def _crossref(read, doi):
    url = CROSSREF + "/" + quote(doi, safe="/")
    message = _json(_get(read, url)).get("message", {})
    return message if isinstance(message, dict) and _doi(message.get("DOI", "")) == doi else {}


def _candidates(read, title):
    query = urlencode({"query.title": title[:400], "rows": 5, "select":
        "DOI,title,author,published,container-title,resource,page,type"})
    doc = _json(read(CROSSREF + "?" + query, 2 * 1024 * 1024))
    return doc.get("message", {}).get("items", [])


def _year(meta):
    dates = meta.get("published", {}).get("date-parts") or [[]]
    return str(dates[0][0]) if dates[0] else ""


def _first(meta, field):
    items = meta.get(field) or []
    return _text(items[0]) if isinstance(items, list) and items else ""


def _authors(meta):
    return [_plain(" ".join(filter(None, (a.get("given"), a.get("family"))))) or _plain(a.get("name", ""))
            for a in meta.get("author", []) if isinstance(a, dict)]


def citation_venue(tags):
    """只有出版页明示期刊或会议时，才记录相应的引用类型。"""
    journal = _plain(tags.get("citation_journal_title"))
    conference = _plain(tags.get("citation_conference_title"))
    if journal and not conference:
        return {"publication_type": "journal", "venue": journal}
    if conference and not journal:
        return {"publication_type": "conference", "venue": conference}
    return {}


def search_titles(query, read):
    title = _plain(query)
    if not title:
        return []
    results, seen = [], set()
    for meta in _candidates(read, title):
        doi, label = _doi(meta.get("DOI", "")), _first(meta, "title")
        if doi and label and doi not in seen:
            results.append(dict(title=label, url=doi_url(doi), authors=_authors(meta), year=_year(meta)))
            seen.add(doi)
        if len(results) == 3:
            break
    return results


def _normalized(value):
    return "".join(c for c in unicodedata.normalize("NFKD", value).casefold() if c.isalnum())


def _author(name):
    family, comma, given = name.partition(",")
    return _normalized(given + " " + family if comma else name)


def _venue(text):
    folded = text.casefold()
    for short, phrase in [("CVPR", "computer vision and pattern recognition"),
                          ("ICCV", "international conference on computer vision"),
                          ("WACV", "winter conference on applications of computer vision")]:
        if short.casefold() in folded or phrase in folded:
            return short + ("W" if "workshop" in folded else "")
    return ""


def _matches_cvf(meta, page):
    venue = _venue(_first(meta, "container-title"))
    return (bool(venue and page["authors"] and page["year"] and meta.get("type") == "proceedings-article")
            and _normalized(_first(meta, "title")) == _normalized(page["title"])
            and [_author(a) for a in _authors(meta)] == [_author(a) for a in page["authors"]]
            and _year(meta) == page["year"] and venue == _venue(page["venue"]))


def _cvf_page(read, url):
    page = _Page()
    page.feed(_get(read, url))
    values = page.meta
    pdf = next((urljoin(url, href) for href, label in page.links if label.casefold() == "pdf"), "")
    if not pdf:
        pdf = values.get("citation_pdf_url", "")
    if _identifier(pdf)[:2] != _identifier(url)[:2] or not trusted_pdf_name(pdf):
        pdf = ""
    date = re.search(r"\b\d{4}\b", values.get("citation_publication_date", ""))
    return dict(title=values.get("citation_title", ""), authors=page.authors, year=date[0] if date else "",
                abstract=_plain(" ".join(page.abstract)), venue=values.get("citation_conference_title", ""),
                pdf_url=pdf, url=url, doi=_doi(values.get("citation_doi", "")))


@lru_cache(maxsize=4)
def _conference_directory(read, url):
    # 同一批会议论文共享目录；限流或读取失败时保留重试机会。
    directory = _Page()
    directory.feed(read(url, 16 * 1024 * 1024))
    entries = {}
    for href, title in directory.links:
        if title and "/html/" in href:
            kind, _, landing = _identifier(urljoin(url, href))
            if kind == "cvf":
                entries.setdefault(_normalized(title), set()).add(landing)
    if not entries:
        raise ValueError("会议目录暂不可读。")
    return entries


def _cvf_for_published(read, meta):
    venue, year = _venue(_first(meta, "container-title")), _year(meta)
    if venue not in {"CVPR", "ICCV", "WACV"} or not re.fullmatch(r"20\d\d", year):
        return None
    url = "https://" + CVF + "/" + venue + year + "?day=all"
    title = _normalized(_first(meta, "title"))
    try:
        links = _conference_directory(read, url).get(title, set())
    except (ValueError, OSError):
        return None
    if len(links) == 1:
        page = _cvf_page(read, next(iter(links)))
        if _matches_cvf(meta, page):
            return page
    return None


def _set_cvf(result, page):
    result["aliases"].append(page["url"])
    result["evidence_urls"].append(page["url"])
    result.update(title=page["title"], authors=page["authors"], year=page["year"],
                  pdf_url=page["pdf_url"], source_version="CVF 公开录用稿")
    if page.get("venue"):
        result.update(publication_type="conference", venue=result.get("venue") or page["venue"])
    if page["abstract"]:
        result.update(abstract_original=page["abstract"], abstract_source="CVF 作者摘要", abstract_url=page["url"])


def _set_crossref(result, meta):
    doi = _doi(meta.get("DOI", ""))
    landing = doi_url(doi)
    result.update(doi=doi, url=landing, key="doi:" + doi, title=_first(meta, "title"),
                  authors=_authors(meta), year=_year(meta), venue=_first(meta, "container-title"),
                  publication_type={"journal-article": "journal", "proceedings-article": "conference"}.get(meta.get("type"), ""))
    result["aliases"].append(landing)
    resource = meta.get("resource", {}).get("primary", {}).get("URL", "")
    kind, _, primary = _identifier(resource)
    if kind in {"acl", "ieee", "cvf", "openreview"}:
        result["aliases"].append(primary)
    abstract = _text(meta.get("abstract", ""))
    if abstract:
        result.update(abstract_original=abstract, abstract_source="Crossref 出版方摘要", abstract_url=landing)
        result["evidence_urls"].append(landing)
    return kind, primary


def _review(read, result, ident):
    statuses = set()
    for host in ["api2.openreview.net", "api.openreview.net"]:
        try:
            raw = read("https://" + host + "/notes?" + urlencode({"id": ident}), 2 * 1024 * 1024)
        except HTTPError as exc:
            statuses.add(exc.code)
            continue
        except (ValueError, OSError):
            continue
        notes = _json(raw).get("notes", [])
        note = next((n for n in notes if n.get("id") == ident and n.get("forum") == ident), None)
        if not note:
            continue
        contents = note.get("content", {})
        def field(name):
            val = contents.get(name)
            return val.get("value") if isinstance(val, dict) else val
        title = _plain(field("title"))
        if not title:
            continue
        result.update(title=title, authors=[a for a in (field("authors") or []) if isinstance(a, str)])
        abstract = _plain(field("abstract"))
        if abstract:
            result.update(abstract_original=abstract, abstract_source="OpenReview 作者摘要", abstract_url=result["url"])
        venue = _plain(field("venue"))
        year = re.search(r"\b(?:19|20)\d\d\b", venue or _plain(field("venueid")))
        result.update(year=year[0] if year else "", source_version=venue or "OpenReview 公开投稿")
        attachment = field("pdf")
        if isinstance(attachment, str) and attachment:
            pdf = urljoin("https://openreview.net/", attachment)
            if trusted_pdf_name(pdf) and (_identifier(pdf)[1] == ident or urlsplit(pdf).path.startswith("/pdf/")):
                result["pdf_url"] = pdf
        return
    page = _Page()
    page.feed(_get(read, result["url"]))
    pdf = page.meta.get("citation_pdf_url", "")
    if page.meta.get("citation_title") and _identifier(pdf)[:2] == ("openreview", ident) and trusted_pdf_name(pdf):
        result.update(title=page.meta["citation_title"], authors=page.authors, pdf_url=pdf,
                      year=page.meta.get("citation_publication_date", "")[:4], source_version="OpenReview 公开投稿")
        return
    if 429 in statuses:
        result["source_note"] = "OpenReview 暂时限流，只能收藏此入口。可稍后重试或改用可访问的论文版本。"
    else:
        result["source_note"] = "未从 OpenReview 取得公开论文元数据，请核对链接或改用可访问的论文版本。"


def _ieee(read, result, ident):
    raw = _get(read, result["url"])
    page = _Page()
    page.feed(raw)
    title = page.meta.get("citation_title", "")
    doi = _doi(page.meta.get("citation_doi", ""))
    embedded = {}
    marker = re.search(r"(?:xplGlobal\.document\.metadata|global\.document\.metadata)\s*=\s*", raw)
    if marker:
        try:
            data, _ = json.JSONDecoder().raw_decode(raw[marker.end():])
            if isinstance(data, dict) and str(data.get("articleNumber", "")) == ident:
                embedded = data
        except ValueError:
            pass
    title = title or _plain(embedded.get("title", ""))
    doi = doi or _doi(embedded.get("doi") or "")
    if title:
        result.update(title=title, authors=page.authors, **citation_venue(page.meta))
        abstract = _plain(embedded.get("abstract", "")) or page.meta.get("citation_abstract", "")
        if abstract:
            result.update(abstract_original=abstract, abstract_source="IEEE 出版方摘要", abstract_url=result["url"])
    if doi:
        meta = _crossref(read, doi)
        primary = meta.get("resource", {}).get("primary", {}).get("URL", "")
        if _identifier(primary)[:2] == ("ieee", ident):
            return meta
    return {}


def resolve(url, read, need_content=False):
    kind, ident, landing = _identifier(url)
    if kind not in {"doi", "cvf", "ieee", "openreview"}:
        return None
    key = "doi:" + ident if kind == "doi" else "url:" + landing
    result = dict(key=key, url=landing, aliases=[url, landing], evidence_urls=[landing],
                  title="", authors=[], year="", doi="", pdf_url="", source_version="", source_note="")
    meta, page = {}, None
    if kind == "doi":
        result["doi"] = ident
        meta = _crossref(read, ident)
    elif kind == "cvf":
        page = _cvf_page(read, landing)
        if page["title"]:
            if page["doi"]:
                candidate = _crossref(read, page["doi"])
                if _matches_cvf(candidate, page):
                    meta = candidate
            elif page["authors"]:
                try:
                    matches = [m for m in _candidates(read, page["title"]) if _matches_cvf(m, page) and _doi(m.get("DOI", ""))]
                except (OSError, ValueError):
                    matches = []
                if len(matches) == 1:
                    meta = matches[0]
    elif kind == "ieee":
        meta = _ieee(read, result, ident)
    else:
        _review(read, result, ident)
    if meta:
        primary_kind, primary = _set_crossref(result, meta)
        if page is None and primary_kind == "cvf":
            candidate = _cvf_page(read, primary)
            if _matches_cvf(meta, candidate):
                page = candidate
        if page is None and (need_content or primary_kind == "ieee"):
            page = _cvf_for_published(read, meta)
        if primary_kind == "acl":
            result.update(pdf_url=primary.rstrip("/") + ".pdf", source_version="ACL Anthology 公开会议版")
            result["evidence_urls"].append(primary)
    if page and page["title"]:
        _set_cvf(result, page)
    if result.get("pdf_url"):
        result["evidence_urls"].append(result["pdf_url"])
    elif not result["source_note"]:
        result["source_note"] = ("已取得题录，尚未确认可访问的公开全文。可提供论文的会议开放页面。"
                                 if result["title"] else "已保留论文入口，暂未取得可信题录或公开全文。")
    for key in ["aliases", "evidence_urls"]:
        result[key] = list(dict.fromkeys(result[key]))
    return result
