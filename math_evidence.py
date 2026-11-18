"""读取论文里已有的 TeX 源码、编号和定位信息。"""

import re
from html.parser import HTMLParser
from urllib.parse import quote


MAX_TEX = 4096
MAX_ITEMS = 8
_TEX_ENCODINGS = {"application/x-tex", "application/tex", "text/tex", "text/x-tex", "application/x-latex"}
_ENVIRONMENT = (r"(?:align(?:at)?|gather|multline|equation|[pbBvV]?matrix)\*?"
                r"|aligned(?:at)?|gathered|split|[dr]?cases|smallmatrix|array")
_OPEN = re.compile(r"(?P<ticks>\x60+)|(?P<fence>~{3,})|(?P<math>\$\$?|"
                   r"\\\[|\\\(|\\begin\{(?:" + _ENVIRONMENT + r")\})")
_VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}


def _escaped(text, pos):
    prev = pos
    while prev and text[prev - 1] == "\\":
        prev -= 1
    return (pos - prev) % 2 == 1


def _matching_env_end(text, start, mark, close):
    """矩阵、分段函数可同类嵌套，保留整个外层环境。"""
    name = re.escape(mark[len(r"\begin"):])
    depth = 1
    tokens = re.compile(r"\\(begin|end)" + name)
    for token in tokens.finditer(text, start, start + MAX_TEX + len(close)):
        if _escaped(text, token.start()):
            continue
        depth += 1 if token[1] == "begin" else -1
        if depth == 0:
            return token.start()
    return -1


def markdown_formulas(text):
    """按源码顺序读取显式数学片段，保留换行与转义，忽略代码和单独的货币符号。"""
    pos = 0
    while match := _OPEN.search(text, pos):
        start, begin = match.span()
        mark = match[0]
        pos = begin
        if _escaped(text, start):
            continue
        if match["ticks"] or match["fence"]:
            fenced = len(mark) >= 3 and not text[text.rfind("\n", 0, start) + 1:start].strip()
            if fenced:
                close = re.search(r"(?m)^[ \t]{0,3}" + re.escape(mark[0]) + "{" + str(len(mark)) + r",}[ \t]*\r?$", text[begin:])
                pos = begin + close.end() if close else len(text)
            elif match["ticks"]:
                end = re.search(r"(?<!\x60)" + re.escape(mark) + r"(?!\x60)", text[begin:])
                if end:
                    pos = begin + end.end()
            continue
        env = mark.startswith(r"\begin")
        close = mark.replace(r"\begin", r"\end", 1) if env else {r"\(": r"\)", r"\[": r"\]", "$": "$", "$$": "$$"}[mark]
        if mark == "$" and (begin == len(text) or text[begin].isspace()):
            continue
        if env:
            end = _matching_env_end(text, begin, mark, close)
        else:
            end = text.find(close, begin, begin + MAX_TEX + len(close))
            while end >= 0 and _escaped(text, end):
                end = text.find(close, end + len(close), begin + MAX_TEX + len(close))
        if end < 0:
            continue
        inner = text[begin:end]
        if mark == "$" and (not inner or inner[-1].isspace() or "\n" in inner
                            or text[end+1:end+2].isdigit() or text[end:end+2] == "$$"):
            continue
        raw = text[start:end + len(close)]
        tex = raw if env else inner.strip()
        if tex and len(tex) <= MAX_TEX:
            yield tex, mark not in ("$", r"\("), raw
        pos = end + len(close)


class MathNode:
    """原文优先取 alttext，缺失时读取带 TeX 编码的 annotation。"""

    def __init__(self, attrs):
        self.alt = (attrs.get("alttext") or "").strip()
        self.tex_parts, self.text_parts = [], []
        self.annotation = None

    def start(self, tag, attrs):
        if tag in {"annotation", "annotation-xml"}:
            self.annotation = (attrs.get("encoding") or "").lower() in _TEX_ENCODINGS

    def end(self, tag):
        if tag in {"annotation", "annotation-xml"}:
            self.annotation = None

    def text(self, text):
        if self.annotation:
            self.tex_parts.append(text)
        elif self.annotation is None:
            self.text_parts.append(text)

    def latex(self):
        tex = self.alt or "".join(self.tex_parts).strip()
        return tex if len(tex) <= MAX_TEX else ""

    def marked_text(self):
        tex = self.latex()
        if not tex:
            return "".join(self.text_parts)
        if re.fullmatch(r"[-+−]?(?:\d+(?:[.,]\d+)*)(?:\\?%)?", tex):
            return tex.replace(r"\%", "%")
        return r"\(" + tex + r"\)"


def formula_item(label, tex, display, source, url, original=None):
    return {"kind": "formula", "label": label, "value": tex, "latex": tex, "display": display,
            "source": source, "source_url": url, "quote": original or tex}


class FormulaReader(HTMLParser):
    """识别原文数学节点、LaTeXML 对齐行以及正文中以 Markdown 形式留下的公式。"""

    def __init__(self, url):
        super().__init__(convert_charrefs=True)
        self.url = url
        self.frames = []
        self.node = self.row = self.group = self.paragraph = None
        self.block, self.inline = [], []

    def _add(self, tex, display, anchor, *, number="", original=None):
        if not tex or len(tex) > MAX_TEX:
            return
        target = self.block if display else self.inline
        if any(item["latex"] == tex for item in target):
            return
        if not number and (tag := re.search(r"\\tag(\*?)\{([A-Za-z0-9.-]{1,24})\}", tex)):
            number = tag[2] if tag[1] else "(" + tag[2] + ")"
        label = "公式 " + number if number else ("独立公式" if display else "行内公式")
        url = self.url + ("#" + quote(anchor, safe="") if anchor else "")
        target.append(formula_item(label, tex, display, "arXiv HTML · " + label, url, original))
        if len(target) > MAX_ITEMS:
            if not display:
                # 优先保留含等号、运算或函数的行内公式，单个变量仍可从原表/原文查看。
                target.sort(key=lambda item: (
                    bool(re.search(r"=|\\(?:approx|propto|leq?|geq?)\b", item["latex"])),
                    bool(re.search(r"[+\-*/(]|\\(?:frac|sum|prod|sqrt|int)\b", item["latex"])),
                    bool(re.search(r"[_^]", item["latex"]))), reverse=True)
            target.pop()

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        parent = self.frames[-1] if self.frames else {}
        anchor = attrs.get("id") or parent.get("anchor", "")
        classes = (attrs.get("class") or "").split()
        ignored = parent.get("ignored") or tag in {"code", "pre", "script", "style", "nav", "template"}
        if tag in _VOID_TAGS:
            if self.paragraph is not None and tag == "br" and not ignored:
                self.paragraph.append("\n")
            return
        frame = {"tag": tag, "anchor": anchor, "ignored": ignored}
        self.frames.append(frame)
        if ignored:
            if self.paragraph is not None and not parent.get("ignored"):
                # 代码内外的数学定界符不可配对。
                self._flush_text(anchor=parent.get("anchor", ""))
            return
        if self.node is not None:
            self.node.start(tag, attrs)
        elif tag == "math":
            self.node = MathNode(attrs)
            frame["math"] = True
            frame["display"] = attrs.get("display") == "block"
        elif tag == "table" and any(c in classes for c in ("ltx_equation", "ltx_equationgroup")) and self.group is None:
            self.group = []
            frame["group"] = True
        elif tag == "tr" and self.group is not None:
            self.row = {"parts": [], "numbers": [], "anchor": anchor}
            frame["row"] = True
        elif self.row is not None and any(c.startswith("ltx_tag_equation") for c in classes):
            frame["eqnum"] = True
        elif tag in {"p", "td", "th", "figcaption", "li", "blockquote"} and self.paragraph is None:
            self.paragraph = []
            frame["paragraph"] = True

    def handle_data(self, text):
        if self.frames and self.frames[-1]["ignored"]:
            return
        if self.node is not None:
            self.node.text(text)
        elif self.row and any(f.get("eqnum") for f in self.frames):
            self.row["numbers"].append(text)
        elif self.paragraph is not None:
            self.paragraph.append(text)

    def _flush_text(self, anchor):
        for tex, display, raw in markdown_formulas("".join(self.paragraph)):
            self._add(tex, display, anchor, original=raw)
        self.paragraph.clear()

    def _emit_group(self, anchor):
        if any(r.get("missing") for r in self.group):
            return
        rows = [r for r in self.group if r["parts"]]
        # 子编号分别定位。没有编号的对齐行共同组成一个多行等式。
        units = [[r] for r in rows] if len(rows) > 1 and all(r["numbers"] for r in rows) else [rows]
        for unit in units:
            if not unit:
                continue
            original = "\n".join(" ".join(r["parts"]) for r in unit)
            lines = [" & ".join(r["parts"]) for r in unit]
            tex = lines[0] if len(unit) == 1 and len(unit[0]["parts"]) == 1 else (
                "\\begin{aligned}\n" + " \\\\\n".join(lines) + "\n\\end{aligned}")
            number = " ".join("".join(r["numbers"]).strip() for r in unit).strip()
            self._add(tex, True, unit[0]["anchor"] if len(unit) == 1 else anchor,
                      number=number, original=original)

    def handle_endtag(self, tag):
        if not self.frames or tag in _VOID_TAGS:
            return
        if self.frames[-1]["tag"] != tag:
            return
        frame = self.frames.pop()
        if frame["ignored"]:
            return
        if frame.get("math"):
            tex = self.node.latex()
            if self.row is not None:
                if tex:
                    self.row["parts"].append(tex)
                else:
                    self.row["missing"] = True
            else:
                self._add(tex, frame["display"], frame["anchor"])
            self.node = None
        elif self.node is not None:
            self.node.end(tag)
        elif frame.get("row"):
            self.group.append(self.row)
            self.row = None
        elif frame.get("group"):
            self._emit_group(frame["anchor"])
            self.group = None
        elif frame.get("paragraph"):
            self._flush_text(frame["anchor"])
            self.paragraph = None

    def items(self):
        display_tex = {item["latex"] for item in self.block}
        return (self.block + [item for item in self.inline if item["latex"] not in display_tex])[:MAX_ITEMS]
