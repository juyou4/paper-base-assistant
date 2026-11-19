"""从读到的正文和 HTML 锚点收集资源候选，保留原始路径与出处，不请求外部仓库。"""

import re
from html.parser import HTMLParser
from urllib.parse import quote, urlsplit, urlunsplit


KINDS = ("code", "model", "dataset")
LABELS = {"code": "代码仓库", "model": "模型资源", "dataset": "数据集"}
_HOST = {"github.com": "GitHub", "gitlab.com": "GitLab", "huggingface.co": "Hugging Face"}
_RESERVED = set(("about account accounts apps blog collections community contact dashboard discussions "
                 "docs enterprises events explore features groups help issues join login marketplace "
                 "models new notifications organizations orgs papers pricing projects pulls search "
                 "security settings signup spaces support tasks topics trending users").split())
_PLACEHOLDER = {"anonymous", "anon", "yourname", "yourusername", "your-org", "your-repo", "example", "placeholder"}
_URL = re.compile(r"(?<![\w./:@-])(?:https?://)?(?:www\.)?(?:github\.com|gitlab\.com|huggingface\.co)/"
                  r"""[A-Za-z0-9_~!$&*+,;=:@%./?#-]+""", re.I)
_NAME = re.compile(r"[\w-][\w.-]{0,99}", re.ASCII)
_SECRET_QUERY = re.compile(r"(?:^|[&;])(?:access_token|token|key|signature|authorization|x-amz-[^=]+)=", re.I)
_PDF_WRAP = re.compile(r"(?P<start>(?:https?://)?(?:www\.)?(?:github\.com|gitlab\.com|huggingface\.co)/"
                       r"(?:[\w.%~-]+/)*)[ \t]*\r?\n[ \t]*(?P<next>[\w.%~-]+/[\w./%~-]+)")
_BLOCK_TAGS = {"article", "section", "div", "p", "aside", "li", "dt", "dd", "figcaption", "footer", "body"}
_SKIP = {"head", "nav", "script", "style", "template", "svg", "math"}
_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}


def _location(url):
    """只接受可核对的资源路径；资源 URL 与论文的出处 URL 分开存。"""
    address = url.strip()
    if not re.match(r"^https?://", address, re.I):
        address = "https://" + address
    try:
        parts = urlsplit(address)
        host = parts.netloc.lower().removeprefix("www.")
        if (host not in _HOST or len(address) > 1500 or re.search(r"[\s\\<>]", address)
                or _SECRET_QUERY.search(parts.query)):
            return None
        path = parts.path.strip("/").split("/")
    except ValueError:
        return None
    kind = "code"
    if host == "huggingface.co":
        kind = {"datasets": "dataset", "spaces": "code", "models": "model"}.get(path[0], "model")
        if path[0] in {"datasets", "spaces", "models"}:
            path = path[1:]
    if (len(path) < 2 or path[0].lower() in _RESERVED or any(not _NAME.fullmatch(p)
            or p.lower() in _PLACEHOLDER or p in {".", ".."} for p in path[:2])
            or any(p in {".", ".."} for p in path) or re.search(r"%(?:2e|2f|5c|0[ad])", parts.path, re.I)):
        return None
    if host == "github.com" and len(path) > 2 and path[2] in {"issues", "pull", "pulls", "discussions"}:
        return None
    label = _HOST[host] + " · " + "/".join(path[:2])
    resource_url = urlunsplit(("https", host, parts.path, parts.query, parts.fragment))
    return kind, label, resource_url


def _candidate(address, excerpt, source, origin):
    target = _location(address)
    if not target:
        return None
    kind, label, url = target
    return {"kind": kind, "label": label, "resource_url": url, "value": url, "source": source,
            "source_url": origin, "quote": " ".join(excerpt.split())}


def valid_resource(item):
    url = item.get("resource_url")
    target = _location(url) if isinstance(url, str) else None
    return bool(target and target[0] == item["kind"] and url == item.get("value") == target[2])


def _from_text(text, source, url, anchors=()):
    hits = []
    for start, end, href in anchors:
        if not re.match(r"^https?://", href, re.I):
            continue
        quote_text = text[max(0, start - 100):min(len(text), end + 65)].strip()
        if quote_text:
            hits.append(_candidate(href, quote_text, source, url))
    for match in _URL.finditer(text):
        if any(start <= match.start() < end for start, end, _ in anchors):
            continue
        address = match[0].rstrip(".,;:!?")
        snippet = text[max(0, match.start() - 100):min(len(text), match.end() + 65)].strip()
        hits.append(_candidate(address, snippet, source, url))
    return [item for item in hits if item]


def _unique(items):
    seen = set()
    counts = dict.fromkeys(KINDS, 0)
    results = []
    for item in items:
        key = item["resource_url"]
        if key not in seen:
            seen.add(key)
            kind = item["kind"]
            counts[kind] += 1
            if counts[kind] <= 12:
                results.append(item)
    for item in results:
        if counts[item["kind"]] > 12:
            item["resource_total"] = counts[item["kind"]]
    return results


def pdf_resource_items(text, pdf_url):
    items = []
    for number, page in enumerate(text.split("\f")[:32], 1):
        # 仅连接斜杠后有后续路径的换行，不跨页或把下一句误拼进地址。
        page = _PDF_WRAP.sub(r"\g<start>\g<next>", page)
        items.extend(_from_text(page, f"PDF 第 {number} 页", pdf_url + f"#page={number}"))
    return _unique(items)


class _ResourceHTML(HTMLParser):
    def __init__(self, url):
        super().__init__()
        self.url = url
        self.stack = []
        self.parts = []
        self.size = 0
        self.links = []
        self.link = None
        self.items = []
        self.article_seen = False

    def flush(self):
        in_article = any(tag == "article" for tag, _ in self.stack)
        if self.parts and (in_article or not self.article_seen):
            anchor = next((value for _, value in reversed(self.stack) if value), "")
            origin = self.url + ("#" + quote(anchor, safe=".-_:") if anchor else "")
            self.items.extend(_from_text("".join(self.parts), "论文 HTML" + (" · " + anchor if anchor else ""),
                                         origin, self.links))
        self.parts, self.size, self.links, self.link = [], 0, [], None

    def handle_starttag(self, tag, attrs):
        if tag in _VOID:
            if tag == "br":
                self.handle_data(" ")
            return
        attrs = dict(attrs)
        if tag in _BLOCK_TAGS or tag in _SKIP:
            self.flush()
        if tag == "article" and not self.article_seen:
            self.items.clear()
            self.article_seen = True
        self.stack.append((tag, attrs.get("id", "")))
        if tag == "a":
            self.link = (self.size, attrs.get("href") or "")

    def handle_endtag(self, tag):
        if tag == "a" and self.link:
            start, href = self.link
            self.links.append((start, self.size, href))
            self.link = None
        if tag in _BLOCK_TAGS or tag in _SKIP:
            self.flush()
        if self.stack and self.stack[-1][0] == tag:
            self.stack.pop()

    def handle_data(self, data):
        if not any(t in _SKIP for t, _ in self.stack):
            self.parts.append(data)
            self.size += len(data)


def html_resource_items(html, article_url):
    parser = _ResourceHTML(article_url)
    parser.feed(html)
    parser.close()
    parser.flush()
    return _unique(parser.items)
