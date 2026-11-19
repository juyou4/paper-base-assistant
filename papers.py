"""解析论文来源、取得原文证据并缓存已确认的公开论文。"""

import hashlib
import json
import os
import re
import shutil
import tempfile
import time
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

import pdf_sections
import paper_sources
from math_evidence import MathNode
from paper_sources import ARXIV_HOSTS, ARXIV_ID, paper_keys


SAMPLE = json.loads((Path(__file__).parent / "assets" / "sample_paper.json").read_text(encoding="utf-8"))
SAMPLE_ID = "2005.11401"
PAPER_HOSTS = paper_sources.HOSTS
ACL_PAPER = re.compile(r"/([A-Za-z0-9][A-Za-z0-9.-]{2,90}?)(?:\.pdf|/)?$")
MAX_PDF = 50 * 1024 * 1024


def _trusted_url(url):
    try:
        location = urlsplit(url)
        return (location.scheme == "https" and location.hostname in PAPER_HOSTS
                and location.port in (None, 443) and location.username is None and location.password is None)
    except ValueError:
        return False


class _ArxivRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _trusted_url(newurl):
            raise ValueError("论文站点将请求重定向到了未支持的来源。")
        old, new = urlsplit(req.full_url).hostname, urlsplit(newurl).hostname
        if old != new and not {old, new} <= ARXIV_HOSTS:
            raise ValueError("论文请求跳转到了其他站点，无法确认原文来源。")
        old_kind, old_id, _ = paper_sources._identifier(req.full_url)
        new_kind, new_id, _ = paper_sources._identifier(newurl)
        if old_kind == new_kind and old_kind not in {"", "url"} and old_id != new_id:
            if not (old_kind == "arxiv" and re.fullmatch(re.escape(old_id) + r"v\d+", new_id)):
                raise ValueError("论文来源跳转到了其他稿件或版本，请核对链接。")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _open(url, timeout):
    if not _trusted_url(url):
        raise ValueError("仅从支持的官方论文来源自动下载。")
    request = Request(url, headers={"User-Agent": "PaperBaseDemo/1.0 (local paper reader)"})
    return build_opener(_ArxivRedirect()).open(request, timeout=timeout)


def _read_html(url, max_bytes):
    with _open(url, timeout=6) as response:
        data = response.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValueError("论文页面过大。")
    return data.decode("utf-8", errors="replace")


class _PageText(HTMLParser):
    """读取 arXiv 公共页面的引用元数据和带章节标题的正文。"""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.meta = {}
        self.authors = []
        self.blocks = []
        self.tag = ""
        self.parts = []
        self.acl_abstract = ""
        self._acl_depth = 0
        self._in_abstract = False
        self._math = None
        self._sections = []
        self.heading_anchors = {}
        self.arxiv_citation = ""
        self._anchor = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "a":
            self._anchor = (attrs.get("href", ""), [])
        if self._math is not None:
            self._math.start(tag, attrs)
            return
        if tag == "math":
            self._math = MathNode(attrs)
            return
        if tag == "section":
            self._sections.append(attrs.get("id", ""))
        if tag == "div" and (self._acl_depth or "acl-abstract" in attrs.get("class", "").split()):
            self._acl_depth += 1
        if self._acl_depth and tag == "span":
            self._in_abstract = True
        if tag == "meta":
            self.meta[attrs.get("name") or attrs.get("property")] = attrs.get("content", "")
            if attrs.get("name") == "citation_author" and attrs.get("content"):
                self.authors.append(attrs["content"])
        if re.fullmatch(r"h[1-6]", tag) or tag in ("p", "blockquote"):
            self.tag = tag
            self.parts = []
            if tag.startswith("h"):
                anchor = attrs.get("id") or (self._sections[-1] if self._sections else "")
                if anchor:
                    self.heading_anchors[len(self.blocks)] = anchor

    def handle_endtag(self, tag):
        if tag == "a" and self._anchor:
            href, parts = self._anchor
            label = "".join(parts).strip()
            if re.fullmatch(r"arXiv:" + ARXIV_ID, label, re.I):
                ident = label.split(":", 1)[1]
                if paper_sources._identifier(href)[:2] == ("arxiv", ident.lower()):
                    self.arxiv_citation = ident.lower()
            self._anchor = None
        if self._math is not None:
            if tag == "math":
                marked = self._math.marked_text()
                self._math = None
                self.handle_data(marked)
            else:
                self._math.end(tag)
            return
        if tag == "section" and self._sections:
            self._sections.pop()
        if tag == "div" and self._acl_depth:
            self._acl_depth -= 1
            if not self._acl_depth:
                self._in_abstract = False
        if self.tag and tag == self.tag:
            self.blocks.append((tag, " ".join("".join(self.parts).split())))
            self.tag = ""
            self.parts = []

    def handle_data(self, data):
        if self._anchor:
            self._anchor[1].append(data)
        if self._math is not None:
            self._math.text(data)
            return
        if self.tag:
            self.parts.append(data)
        if self._in_abstract:
            self.acl_abstract += data


def english_excerpt(original, max_chars=650):
    """优先取完整首段；长段落在自然句界处结束，限长是软目标。"""
    if not original:
        return ""
    first = next((line.strip() for line in original.splitlines() if line.strip()), "")
    if len(first) <= max_chars:
        return first
    last_end = 0
    for match in re.finditer(r"""[.!?]["'”’)\]]*(?=\s|$)""", first):
        prefix = first[:match.end()]
        if re.search(r"\b(?:e\.g|i\.e|et al|fig|eq|sec|dr|mr|ms|vs)\.$", prefix, re.I):
            continue
        if match.end() > max_chars:
            return first[:last_end or match.end()]
        last_end = match.end()
    return first[:last_end] if last_end else first


def _method_evidence(page):
    method_heading = re.compile(
        r"\b(methods?|methodology|approach|(?:model|system) architecture|proposed model)\b", re.I
    )
    for index, (tag, heading) in enumerate(page.blocks):
        if tag not in {"h2", "h3"} or not method_heading.search(heading) or len(heading) > 130:
            continue
        paragraphs = []
        for next_tag, value in page.blocks[index + 1:]:
            if next_tag in ("h1", "h2") or next_tag == tag:
                break
            if next_tag == "p" and value:
                paragraphs.append(value)
            if sum(map(len, paragraphs)) >= 3000:
                break
        if paragraphs:
            return heading, "\n".join(paragraphs), page.heading_anchors.get(index, "")
    return "", "", ""


def _find_target(text):
    text = str(text).strip()
    match = re.search(r"https?://[^\s<>\"'\x60，。；、！？（）【】\u4e00-\u9fff]+", text, re.I)
    if match:
        url = _trim_address(match[0])
        location = urlsplit(url)
        if not location.hostname or location.username or location.password:
            raise ValueError("请提供不含登录信息的论文链接。")
        if paper_sources._identifier(url)[0] == "arxiv":
            return paper_sources._identifier(url)[1], ""
        if location.hostname.lower() in ARXIV_HOSTS:
            path = location.path.removesuffix(".pdf").rstrip("/")
            arxiv = re.fullmatch(rf"/(?:abs|pdf|html)/({ARXIV_ID})", path, re.I)
            if not arxiv:
                raise ValueError("请提供 arXiv 摘要页、PDF 链接或论文编号。")
            return arxiv[1], ""
        return "", urlunsplit((location.scheme, location.netloc, location.path, location.query, ""))
    doi = re.search(r"(?<![A-Za-z0-9])10\.\d{4,9}/[^\s<>\"'`，。；、！？（）【】\u4e00-\u9fff]+", text, re.I)
    if doi and (identifier := paper_sources._doi(_trim_address(doi[0]))):
        return _find_target(paper_sources.doi_url(identifier))
    bare = re.fullmatch(ARXIV_ID, text, re.I)
    if bare:
        return bare[0], ""
    found = re.search(rf"(?:arxiv\s*[:：]?\s*)?({ARXIV_ID})(?![0-9A-Za-z])", text, re.I)
    if found:
        return found[1], ""
    raise ValueError("请提供论文链接、DOI 或 arXiv 编号。")


def _trim_address(value):
    value = value.rstrip(".,;:!?")
    for left, right in [("(", ")"), ("[", "]"), ("{", "}")]:
        while value.endswith(right) and value.count(right) > value.count(left):
            value = value[:-1]
    return value


def search_titles(query):
    """从 Crossref 获取最多三项题名候选，供用户选择。"""
    try:
        return paper_sources.search_titles(query, _read_html)
    except (ValueError, OSError) as exc:
        raise ValueError("暂时无法访问 Crossref 题录检索，请稍后重试或提供 DOI、论文链接。") from exc


def _pdf_path(paper, data_dir):
    url = paper.get("pdf_url", "")
    if not url or not _trusted_url(url):
        return None
    location = urlsplit(url)
    if paper_sources.trusted_pdf_name(url):
        name = hashlib.sha256(url.encode("utf-8")).hexdigest()
        return Path(data_dir) / "cache" / (location.hostname + "-" + name + ".pdf")
    if location.hostname == "aclanthology.org":
        match = ACL_PAPER.fullmatch(location.path)
        return Path(data_dir) / "cache" / ("acl-" + match[1] + ".pdf") if match and location.path.endswith(".pdf") else None
    if location.hostname not in ARXIV_HOSTS:
        return None
    match = re.fullmatch(rf"/pdf/({ARXIV_ID})", urlsplit(url).path, re.I)
    if not match:
        return None
    return Path(data_dir) / "cache" / (match[1].replace("/", "_") + ".pdf")


def _valid_pdf(path):
    try:
        size = path.stat().st_size
        if size < 12 or size > MAX_PDF:
            return False
        with path.open("rb") as handle:
            if handle.read(5) != b"%PDF-":
                return False
            handle.seek(max(0, size - 4096))
            return b"%%EOF" in handle.read()
    except OSError:
        return False


def cached_pdf(paper, data_dir):
    path = _pdf_path(paper, data_dir)
    return path if path and _valid_pdf(path) else None


def _record(**fields):
    record = dict.fromkeys((
        "key", "url", "title", "abstract_zh", "method", "abstract_source", "method_source",
        "abstract_quote", "method_quote", "abstract_original", "method_original", "pdf_url",
        "cached_pdf", "abstract_url", "method_url",
    ), "")
    record.update(authors=[], year="", doi="", source_note="", source_version="", aliases=[], evidence_urls=[])
    record.update(fields)
    return record


def _pdf_evidence(paper, data_dir):
    if not paper["pdf_url"] or not shutil.which("pdftotext"):
        return
    try:
        found = pdf_sections.read_sections(download_pdf(paper, data_dir))
    except (ValueError, OSError):
        return
    for key, item in found.items():
        if not paper[key + "_original"]:
            paper[key + "_original"] = item["text"]
            paper[key + "_quote"] = english_excerpt(item["text"], 200)
            paper[key + "_source"] = f"原文 PDF 第 {item['page']} 页 · {item['section']}"
            paper[key + "_url"] = paper["pdf_url"] + "#page=" + str(item["page"])


def _acl_paper(url, data_dir, need_content):
    location = urlsplit(url)
    match = ACL_PAPER.fullmatch(location.path)
    if location.hostname != "aclanthology.org" or not match or not re.fullmatch(r"(?:[A-Z]\d\d-\d{3,5}|\d{4}\.[A-Za-z0-9.-]+\.\d+)", match[1], re.I):
        return None
    base = "https://aclanthology.org/" + match[1]
    paper = _record(key="url:" + base + "/", url=base + "/", pdf_url=base + ".pdf")
    try:
        html = _read_html(paper["url"], 1024 * 1024)
        head = _PageText()
        head.feed(html)
        paper["title"] = head.meta.get("citation_title", "")
        paper.update(authors=head.authors, year=head.meta.get("citation_publication_date", "")[:4],
                     source_version="ACL Anthology 公开会议版")
        doi = paper_sources._doi(head.meta.get("citation_doi", ""))
        if doi:
            paper["doi"] = doi
            paper["aliases"].append(paper_sources.doi_url(doi))
        para = " ".join(head.acl_abstract.split())
        if para:
            paper.update(abstract_original=para, abstract_source="ACL Anthology 作者摘要",
                         abstract_quote=english_excerpt(para, 200), abstract_url=paper["url"])
    except HTTPError as exc:
        if exc.code in (404, 410):
            raise ValueError("ACL Anthology 未找到这篇论文。") from exc
    except (ValueError, OSError):
        pass
    if need_content:
        _pdf_evidence(paper, data_dir)
    stored = cached_pdf(paper, data_dir)
    paper["cached_pdf"] = str(stored) if stored else ""
    return paper


def resolve_paper(text_or_url, data_dir, need_content=False, *, find_pdf=False):
    """始终保留来源和版本；来源暂不可读时，仅提供确实知道的字段。"""
    arxiv_id, external = _find_target(text_or_url)
    if external:
        kind, _, landing = paper_sources._identifier(external)
        result = _acl_paper(landing, data_dir, need_content) if kind == "acl" else None
        if result is None:
            info = paper_sources.resolve(external, _read_html, need_content or find_pdf)
            if info is None:
                return _record(key="url:" + external, url=external, aliases=[external])
            result = _record(**info)
            if result["abstract_original"]:
                result["abstract_quote"] = english_excerpt(result["abstract_original"], 220)
            if need_content:
                _pdf_evidence(result, data_dir)
            stored = cached_pdf(result, data_dir)
            result["cached_pdf"] = str(stored) if stored else ""
        result["aliases"] = list(dict.fromkeys([external, result["url"], *result["aliases"]]))
        result["evidence_urls"] = list(dict.fromkeys(u for u in
            [external, *result["evidence_urls"], result["url"], result["pdf_url"]] if u))
        return result
    base_id = re.sub(r"v\d+$", "", arxiv_id, flags=re.I)
    url = "https://arxiv.org/abs/" + arxiv_id
    result = _record(key="arxiv:" + base_id.lower(), url=url, pdf_url="https://arxiv.org/pdf/" + arxiv_id)
    evidence_id = arxiv_id
    result.update(doi="10.48550/arxiv." + base_id.lower(), source_version="arXiv " + arxiv_id)
    original = re.search(r"https?://[^\s<>\"'`，。；、！？（）【】\u4e00-\u9fff]+", str(text_or_url), re.I)
    result["aliases"] = list(dict.fromkeys([*([_trim_address(original[0])] if original else []), url,
                                             paper_sources.doi_url(result["doi"])]))
    if arxiv_id in (SAMPLE_ID, SAMPLE["arxiv_id"]):
        for field in ("title", "abstract_zh", "method", "abstract_source", "method_source", "abstract_quote",
                      "method_quote", "abstract_original", "method_original"):
            result[field] = SAMPLE[field]
        result.update(pdf_url="https://arxiv.org/pdf/" + SAMPLE["arxiv_id"],
                      abstract_url=SAMPLE["original_abstract_url"], method_url=SAMPLE["original_method_url"],
                      source_version="arXiv " + SAMPLE["arxiv_id"])
    else:
        try:
            head = _PageText()
            head.feed(_read_html(url, 1024 * 1024))
            result["title"] = head.meta.get("citation_title", "")
            result.update(authors=head.authors, year=head.meta.get("citation_date", "")[:4])
            if arxiv_id == base_id and re.fullmatch(re.escape(base_id.lower()) + r"v\d+", head.arxiv_citation):
                evidence_id = head.arxiv_citation
                result.update(source_version="arXiv " + evidence_id, pdf_url="https://arxiv.org/pdf/" + evidence_id)
            abstract = next((body for tag, body in head.blocks if tag == "blockquote"), "")
            abstract = re.sub(r"^Abstract\s*:\s*", "", abstract).strip() or head.meta.get("og:description", "")
            if abstract:
                result.update(abstract_original=abstract, abstract_quote=english_excerpt(abstract, 220),
                              abstract_source="arXiv 作者摘要", abstract_url="https://arxiv.org/abs/" + evidence_id)
                if re.search(r"[\u4e00-\u9fff]{4}", abstract):
                    result["abstract_zh"] = abstract
        except HTTPError as exc:
            if exc.code in (404, 410):
                raise ValueError("arXiv 未找到这个编号或版本，请检查论文链接。") from exc
        except (OSError, ValueError):
            pass
        if need_content and result["title"]:
            html_url = "https://arxiv.org/html/" + evidence_id
            try:
                article = _PageText()
                article.feed(_read_html(html_url, 5 * 1024 * 1024))
                heading, body, anchor = _method_evidence(article)
                if body:
                    method_url = html_url + "#" + quote(anchor, safe="") if anchor else html_url
                    result.update(method_original=body, method_quote=english_excerpt(body, 220),
                                  method_source=heading + " · arXiv HTML 正文", method_url=method_url)
                    if re.search(r"[\u4e00-\u9fff]{4}", body):
                        result["method"] = body.split("。")[0] + "。"
            except (OSError, ValueError):
                pass
        if need_content and result["title"] and not result["method_original"]:
            _pdf_evidence(result, data_dir)
    stored = cached_pdf(result, data_dir)
    result["cached_pdf"] = str(stored) if stored else ""
    result["evidence_urls"] = list(dict.fromkeys(u for u in
        [url, result["pdf_url"], result["abstract_url"], result["method_url"]] if u))
    return result


def download_pdf(paper, data_dir):
    """校验首部与结束标记后原子替换缓存，避免中断留下半份 PDF。"""
    ready = cached_pdf(paper, data_dir)
    if ready:
        return ready
    if not paper.get("pdf_url") and paper_sources._identifier(paper.get("url", ""))[0] in {"doi", "ieee"}:
        paper.update(resolve_paper(paper["url"], data_dir, find_pdf=True))
        ready = cached_pdf(paper, data_dir)
        if ready:
            return ready
    target = _pdf_path(paper, data_dir)
    if target is None:
        raise ValueError(paper.get("source_note") or "未确认可下载的公开原文，可提供会议开放页面或 arXiv、ACL Anthology 链接。")
    target.parent.mkdir(parents=True, exist_ok=True)
    workfile = None
    try:
        with _open(paper["pdf_url"], timeout=12) as response:
            length = response.headers.get("Content-Length", "")
            if length.isdigit() and int(length) > MAX_PDF:
                raise ValueError("原文 PDF 超过 50 MB，建议直接从论文官网获取。")
            with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".downloading-", delete=False) as output:
                workfile = Path(output.name)
                total = 0
                deadline = time.monotonic() + 40
                while chunk := response.read(65536):
                    total += len(chunk)
                    if total > MAX_PDF:
                        raise ValueError("原文 PDF 超过 50 MB，建议直接从论文官网获取。")
                    if time.monotonic() > deadline:
                        raise ValueError("下载超时，请稍后重试。")
                    output.write(chunk)
        if not _valid_pdf(workfile):
            raise ValueError("论文站点未返回完整 PDF，请稍后再试。")
        os.replace(workfile, target)
        return target
    except (URLError, TimeoutError) as exc:
        raise ValueError("未取得原文 PDF，请检查网络后重试。") from exc
    finally:
        if workfile is not None:
            workfile.unlink(missing_ok=True)
