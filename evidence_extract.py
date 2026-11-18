"""从论文原文提取带定位的标量、表格行列与插图，不推算图中未标出的数值。"""

import re
import shutil
import subprocess
import tempfile
from collections import Counter
from html.parser import HTMLParser
from urllib.parse import quote, urljoin, urlsplit

import papers
from math_evidence import FormulaReader, MathNode, formula_item


NUMBER = re.compile(r"[-+−]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:\s*(?:±|\+/-)\s*\d+(?:\.\d+)?)?\s*%?")
CAPTION = re.compile(r"^(Fig(?:ure)?\.?|Table|图|表)\s*([\dA-Z]+(?:[.\-]\d+)*)\s*[:.：\-]\s*(.*)", re.I)
RESULT = re.compile(r"\b(?:achiev\w*|obtain\w*|reach\w*|outperform\w*|score\w*|accuracy|BLEU|ROUGE|F1|exact.match)\b", re.I)
MEASURE = re.compile(r"\b\d+(?:\.\d+)?\s*(?:%|BLEU\b|points?\b|F1\b)|\b(?:accuracy|score|BLEU|ROUGE|F1)\s+(?:of\s+)?\d+\.\d+", re.I)
MAX_COLS, MAX_ROWS = 32, 100
OFFLINE_VERSIONS = {"1706.03762": "1706.03762v7", "2005.11401": "2005.11401v4"}


def _clean(value):
    return " ".join(value.split())


def _item(kind, label, value, source, url, evidence, **extra):
    return dict(kind=kind, label=label, value=value, source=source, source_url=url, quote=evidence, **extra)


def _span(value):
    try:
        number = int(value or "1")
    except ValueError:
        return 0
    return number if 1 <= number <= MAX_ROWS else 0


class _Article(HTMLParser):
    """仅读取正文标题、图题和表格结构。嵌套的子图分别保留自身标题与锚点。"""

    def __init__(self):
        super().__init__()
        self.figures, self.stack = [], []
        self.title, self.heading, self.meta_title = "", None, ""
        self.cell, self.row, self.caption = None, None, None
        self.in_head = False
        self.math = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "h1":
            self.heading = []
        if tag == "meta" and a.get("name") == "citation_title":
            self.meta_title = a.get("content", "")
        if tag == "figure" or (tag == "table" and not self.stack):
            self.stack.append(dict(tag=tag, anchor=a.get("id", ""), image="", caption="", rows=[], invalid=False))
        if not self.stack:
            return
        if self.math is not None:
            self.math.start(tag, a)
            return
        if tag == "math":
            self.math = MathNode(a)
            return
        f = self.stack[-1]
        if tag in ("figcaption", "caption"):
            self.caption = []
        if tag in ("img", "object") and not f["image"]:
            f["image"] = a.get("src" if tag == "img" else "data") or ""
        if tag == "thead":
            self.in_head = True
        if tag == "tr":
            self.row = []
        if tag in ("td", "th") and self.row is not None:
            self.cell = ([], _span(a.get("rowspan")), _span(a.get("colspan")), tag)
        if tag == "br":
            self.handle_data(" ")

    def handle_data(self, text):
        if self.math is not None:
            self.math.text(text)
            return
        if self.heading is not None:
            self.heading.append(text)
        if self.caption is not None:
            self.caption.append(text)
        if self.cell is not None:
            self.cell[0].append(text)

    def handle_endtag(self, tag):
        if tag == "h1" and self.heading is not None:
            if not self.title:
                self.title = _clean("".join(self.heading))
            self.heading = None
        if self.math is not None:
            if tag == "math":
                markup = self.math.marked_text()
                self.math = None
                self.handle_data(markup)
            else:
                self.math.end(tag)
            return
        if not self.stack:
            return
        f = self.stack[-1]
        if tag in ("figcaption", "caption") and self.caption is not None:
            f["caption"] = _clean("".join(self.caption))
            self.caption = None
        if tag in ("td", "th") and self.cell is not None and self.row is not None:
            parts, height, width, tag_name = self.cell
            self.row.append((_clean("".join(parts)), height, width, tag_name))
            self.cell = None
        if tag == "tr" and self.row is not None:
            if self.row and len(f["rows"]) < MAX_ROWS:
                f["rows"].append((self.in_head, self.row))
            elif self.row:
                f["invalid"] = True
            self.row = None
        if tag == "thead":
            self.in_head = False
        if tag == f["tag"]:
            if f["caption"]:
                self.figures.append(f)
            self.stack.pop()


def _grid(block):
    """展开合并表头并组合各级列名。保留不规则表的原始单元格，禁止为其猜测指标。"""
    source_rows = block["rows"]
    original = [[cell[0] for cell in cells] for _, cells in source_rows]
    if not original:
        return [], [], False
    grid, ranges, header_rows = [], [], 0
    placed = {}
    safe = not block["invalid"]
    for y, (in_head, cells) in enumerate(source_rows):
        x = 0
        for value, height, width, _ in cells:
            while (y, x) in placed:
                x += 1
            if not height or not width or x + width > MAX_COLS or y + height > len(source_rows):
                return [], original, False
            for dy in range(height):
                for dx in range(width):
                    pos = y + dy, x + dx
                    if pos in placed:
                        return [], original, False
                    placed[pos] = value
            if width > 1:
                ranges.append((y, value))
            x += width
        columns = [col for row, col in placed if row == y]
        row = [placed.get((y, col), "") for col in range(max(columns, default=0) + 1)]
        grid.append(row)
        parent_spans = y > 0 and any(height > 1 or width > 1 for _, height, width, _ in source_rows[y - 1][1])
        if y == header_rows and (in_head or (not any(NUMBER.fullmatch(c) for c in row)
                                             and (y == 0 or parent_spans))):
            header_rows += 1
    if not header_rows or header_rows == len(grid):
        return [], original, False
    width = max(map(len, grid))
    if any(len(row) != width for row in grid):
        return [], original, False
    headers = [" / ".join(dict.fromkeys(row[col] for row in grid[:header_rows] if row[col]))
               for col in range(width)]
    # 数据中跨列的单值含义可能不同，整表可供核对，避免生成标量。
    if any(y >= header_rows and NUMBER.fullmatch(value) for y, value in ranges):
        safe = False
    return headers, grid[header_rows:], safe


def _scalar_items(table, all_rows=None, title=""):
    headers, rows = table["headers"], table["rows"]
    counts = Counter(header.casefold() for header in headers)
    def identity(row):
        # 行维度从首列至第一个数字单元格，允许“类别 + 模型”等多级行标。
        names = []
        for idx, value in enumerate(row):
            if NUMBER.fullmatch(value):
                break
            if value and value not in ("-", "–", "—", "/"):
                names.append((idx, value))
        return names
    candidates = [(row, identity(row)) for row in reversed(rows)]
    duplicates = Counter(tuple(identity(row)) for row in (all_rows or rows))
    terms = set(re.findall(r"[a-z][a-z0-9-]{2,}", title.casefold()))
    def priority(candidate):
        tokens = set(re.findall(r"[a-z][a-z0-9-]{2,}", " ".join(v for _, v in candidate[1]).casefold()))
        return ("ours" in tokens or "our" in tokens, len(tokens & terms))
    candidates.sort(key=priority, reverse=True)
    result = []
    for row, names in candidates:
        if not names or duplicates[tuple(names)] != 1:
            continue
        row_name = " / ".join(value for _, value in names)
        for col in range(names[-1][0] + 1, len(headers)):
            header, value = headers[col], row[col]
            if header and counts[header.casefold()] == 1 and NUMBER.fullmatch(value):
                label = f"{row_name} · {header}"
                result.append(_item("metric", label, value, table["source"], table["source_url"],
                                    f"{row_name} | {header} | {value}"))
            if len(result) >= 4:
                return result
    return result


def _image_url(base, image):
    if not image:
        return ""
    joined = urljoin(base, image)
    link, origin = urlsplit(joined), urlsplit(base)
    try:
        trusted = papers._trusted_url(joined)
    except ValueError:
        return ""
    if trusted and link.hostname == origin.hostname and re.search(r"\.(png|jpe?g|webp|gif|svg)$", link.path, re.I):
        return joined
    return ""


def _table_item(label, caption, source, url, headers, rows):
    sample = rows if len(rows) <= 12 else rows[:6] + rows[-6:]
    if len(rows) > len(sample):
        caption += f"（预览已解析的 {len(rows)} 行中的前 6 行与后 6 行）"
    detail = " | ".join(sample[-1]) if sample else caption
    return _item("table", label, caption, source, url, detail or caption, headers=headers, rows=sample)


def _article_evidence(html, url, preferred_table=None, *, include_formulas=False):
    doc = _Article()
    doc.feed(html)
    doc.close()
    figures, tables = [], []
    for block in doc.figures:
        matched = CAPTION.match(block["caption"])
        if not matched:
            continue
        label = matched[1] + " " + matched[2]
        anchor = block["anchor"]
        src = url + ("#" + quote(anchor, safe="") if anchor else "")
        source = "arXiv HTML · " + label
        caption = (matched[3] or label)[:420]
        if matched[1].casefold() in ("table", "表") and block["rows"]:
            headers, rows, safe = _grid(block)
            item = _table_item(label, caption, source, src, headers, rows)
            if block["invalid"]:
                item["value"] += f"（解析范围为原表前 {MAX_ROWS} 行）"
            metrics = _scalar_items(item, rows, doc.title or doc.meta_title) if safe else []
            tables.append((label == preferred_table, bool(metrics), item, metrics))
        elif matched[1].lower().startswith(("fig", "图")) and not any(f["label"] == label for f in figures):
            item = _item("figure", label, caption, source, src, caption)
            image = _image_url(url, block["image"])
            if image:
                item["image_url"] = image
            figures.append(item)
    tables.sort(key=lambda entry: entry[:2], reverse=True)
    items = []
    # 有标量的内容表优先；留出图与表各自的展示空间。
    for _, _, table, metrics in tables[:2]:
        items.append(table)
        items.extend(metrics[:2])
    items.extend(figures[:2])
    if include_formulas:
        reader = FormulaReader(url)
        reader.feed(html)
        reader.close()
        items.extend(reader.items())
    if not any(i["kind"] == "metric" for i in items):
        page = papers._PageText()
        page.feed(html)
        for _, paragraph in ((t, p) for t, p in page.blocks if t == "p"):
            for excerpt in _result_sentences(paragraph):
                items.append(_item("metric", "正文报告的结果", excerpt, "arXiv HTML · 正文", url, excerpt))
                if sum(i["kind"] == "metric" for i in items) >= 2:
                    return doc.title or doc.meta_title, items
    return doc.title or doc.meta_title, items


def _result_sentences(text):
    return [value for value in re.split(r"(?<=[.!?。])\s+", _clean(text))
            if 12 <= len(value) <= 380 and RESULT.search(value) and MEASURE.search(value)]


def _snapshot(version, *, include_formulas=False):
    """两个带版本的公开表格片段，按作者表中的行列抄录。链接直达该版本原始图表。"""
    base = "https://arxiv.org/html/" + version
    if version == "1706.03762v7":
        title = "Attention Is All You Need"
        table_label, table_anchor = "Table 2", "S6.T2"
        caption = "WMT 2014 机器翻译结果（截取 Transformer 两行的 BLEU 得分）"
        headers = ["Model", "BLEU / EN-DE", "BLEU / EN-FR"]
        rows = [["Transformer (base model)", "27.3", "38.1"], ["Transformer (big)", "28.4", "41.8"]]
        figure_anchor, image, fig_caption = "S3.F1", "Figures/ModalNet-21.png", "The Transformer - model architecture."
    else:
        title = papers.SAMPLE["title"]
        table_label, table_anchor = "Table 1", "S4.T2.fig1"
        caption = "开放域问答测试结果（截取 RAG 两行的 NQ、WQ、CT 得分）"
        headers = ["Model", "NQ", "WQ", "CT"]
        rows = [["RAG-Token", "44.1", "45.5", "50.0"], ["RAG-Seq.", "44.5", "45.2", "52.2"]]
        figure_anchor, image, fig_caption = "S1.F1", "RAG-Architecture.svg", "Overview of our approach."
    origin = "预核原文摘录 · " + version + " · "
    table = _table_item(table_label, caption, origin + table_label, base + "#" + table_anchor, headers, rows)
    figure = _item("figure", "Figure 1", fig_caption, origin + "Figure 1", base + "#" + figure_anchor,
                   fig_caption, image_url=base + "/" + image)
    items = [table, *_scalar_items(table)[:3], figure]
    if include_formulas:
        if version == "1706.03762v7":
            label, anchor = "公式 (1)", "S3.E1"
            tex = r"\mathrm{Attention}(Q,K,V)=\mathrm{softmax}(\frac{QK^{T}}{\sqrt{d_{k}}})V"
        else:
            label, anchor = "RAG-Token 独立公式", "S2.Ex2"
            tex = (r"p_{\text{\tiny{RAG-Token}}}(y|x)\;\approx\;\prod_{i}^{N}\;\sum_{z\in\text{top-}k(p(\cdot|x))}"
                   r"p_{\eta}(z|x)p_{\theta}(y_{i}|x,z,y_{1:i-1})")
        items.append(formula_item(label, tex, True, origin + label, base + "#" + anchor))
    return title, items


def _pdf_items(data, pdf_url):
    items = []
    for page, content in enumerate(data.split("\f"), 1):
        source = f"原文 PDF 第 {page} 页"
        link = pdf_url + "#page=" + str(page)
        for line in content.splitlines():
            caption = CAPTION.match(_clean(line))
            if caption and caption[3]:
                kind = "table" if caption[1].lower() in ("table", "表") else "figure"
                if sum(i["kind"] == kind for i in items) < 2:
                    label = caption[1] + " " + caption[2]
                    items.append(_item(kind, label, caption[3], source + " · " + label, link, caption[0]))
        for paragraph in re.split(r"\n\s*\n", content):
            for sentence in _result_sentences(paragraph):
                if sum(i["kind"] == "metric" for i in items) < 3:
                    items.append(_item("metric", "原文报告的结果", sentence, source, link, sentence))
    return items


def _pdf_grid(cells, label, caption, source, url, title):
    """仅为带明确表头、行标和数值的规则 PDF 网格生成标量。合并单元格按原布局供审阅。"""
    if not 2 <= len(cells) <= MAX_ROWS or not 2 <= len(cells[0]) <= MAX_COLS:
        return []
    if any(len(row) != len(cells[0]) for row in cells):
        return []
    rows = [[_clean(cell or "") for cell in row] for row in cells]
    if not any(NUMBER.fullmatch(value) for row in rows[1:] for value in row[1:]):
        return []
    headers, data = rows[0], rows[1:]
    if any(NUMBER.fullmatch(value) for value in headers):
        return []  # 首行像数据行；可能是跨页的无表头残片。
    item = _table_item(label, caption, source, url, headers, data)
    clear = (all(headers[1:]) and all(cell is not None for row in cells for cell in row)
             and all(row[0] and not NUMBER.fullmatch(row[0]) for row in data)
             and any(NUMBER.fullmatch(value) for value in data[0][1:]))
    return [item, *(_scalar_items(item, data, title)[:2] if clear else [])]


def _nearby_caption(page, box):
    """在网格正上方或下方找表题；没有可识别表题时使用页内序号。"""
    x0, top, x1, bottom = box
    for start, end in [(max(page.bbox[1], top - 36), top), (bottom, min(page.bbox[3], bottom + 36))]:
        if end <= start:
            continue
        strip = page.crop((x0, start, x1, end), strict=False).extract_text() or ""
        for line in strip.splitlines():
            match = CAPTION.match(_clean(line))
            if match and match[1].casefold() in ("table", "表"):
                return match[1] + " " + match[2], match[3]
    return "", ""


def _pdf_tables(path, paper, text):
    try:
        import pdfplumber
    except ImportError:
        return []
    pages = text.split("\f")[:20]
    candidates = [n for n, page in enumerate(pages, 1) if re.search(r"(?im)^\s*(?:table|表)\s*[A-Z\d]+[.:：]", page)]
    selected = candidates[:8] or list(range(1, 9))
    result = []
    try:
        with pdfplumber.open(path, pages=selected) as doc:
            for page in doc.pages:
                source = f"原文 PDF 第 {page.page_number} 页"
                url = f"{paper['pdf_url']}#page={page.page_number}"
                # 使用实际网格线确定边界；跨栏排版的普通段落不能作为数据单元格。
                for idx, table in enumerate(page.find_tables()[:6], 1):
                    if len(table.cells) > MAX_ROWS * MAX_COLS:
                        continue
                    label, caption = _nearby_caption(page, table.bbox)
                    label = label or f"第 {page.page_number} 页表格 {idx}"
                    result.extend(_pdf_grid(table.extract(), label, caption or label,
                                            source + " · " + label, url, paper["title"]))
                    if sum(i["kind"] == "table" for i in result) >= 2:
                        return result
                page.close()
    except Exception:
        # PDFMiner 对损坏字体/内容流的异常分布在多个模块，仍保留已提取的可靠表格。
        return result
    return result


def _pdf_evidence(paper):
    executable = shutil.which("pdftotext")
    with tempfile.TemporaryDirectory() as work:
        pdf = papers.download_pdf(paper, work)
        text = ""
        if executable:
            try:
                output = subprocess.run([executable, "-f", "1", "-l", "32", "-raw", "-enc", "UTF-8",
                                         str(pdf), "-"], timeout=18, check=True, capture_output=True)
                text = output.stdout.decode("utf-8", errors="replace")
            except (OSError, subprocess.SubprocessError):
                pass
        structured = _pdf_tables(pdf, paper, text)
        covered = {(i["source_url"], i["label"].casefold()) for i in structured if i["kind"] == "table"}
        captions = [i for i in _pdf_items(text, paper["pdf_url"])
                    if (i["source_url"], i["label"].casefold()) not in covered]
        if not text and not structured:
            raise ValueError("未能读取 PDF 数据，请检查原文，并确认已安装 Poppler 或 pdfplumber。")
        return structured + captions


def extract_evidence(url: str, *, include_formulas=False) -> dict:
    arxiv, external = papers._find_target(url)
    if arxiv:
        canonical = "https://arxiv.org/abs/" + arxiv
        article = "https://arxiv.org/html/" + arxiv
        base_id = re.sub(r"v\d+$", "", arxiv)
        sample = OFFLINE_VERSIONS.get(base_id)
        try:
            html = papers._read_html(article, 5 * 1024 * 1024)
            preferred = {"1706.03762": "Table 2", "2005.11401": "Table 1"}.get(base_id)
            title, items = _article_evidence(html, article, preferred, include_formulas=include_formulas)
        except (OSError, ValueError):
            title, items = "", []
        if title and items:
            return {"title": title, "url": canonical, "items": items}
        if sample and arxiv in (base_id, sample):
            title, items = _snapshot(sample, include_formulas=include_formulas)
            return {"title": title, "url": "https://arxiv.org/abs/" + sample, "items": items}
    elif not papers._trusted_url(external) or urlsplit(external).hostname != "aclanthology.org":
        raise ValueError("提取数据与图表支持 arXiv 和 ACL Anthology 论文链接。")
    with tempfile.TemporaryDirectory() as work:
        paper = papers.resolve_paper(url, work)
    if not paper.get("pdf_url") or not paper.get("title"):
        raise ValueError("无法从论文来源确认标题和 PDF 地址，请检查论文链接。")
    items = _pdf_evidence(paper)
    if not items:
        raise ValueError("原文中未找到可核对的数据或图表；扫描版 PDF 可能需要 OCR。")
    return {"title": paper["title"], "url": paper["url"], "items": items}
