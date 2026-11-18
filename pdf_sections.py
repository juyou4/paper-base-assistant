"""在没有结构化网页版时，使用可选的 Poppler 读取论文 PDF 的章节和页码。"""

import re
import shutil
import subprocess


SECTION = re.compile(r"(?m)^[ \t]{0,10}(\d+(?:\.\d+)*|[IVX]+)\.?[ \t]+([A-Z][\w,: -]{1,70}?)[ \t]*$")
METHOD = re.compile(r"\b(?:methods?|methodology|approach|architecture|proposed model)\b", re.I)


def excerpts_from_text(text):
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    pages = text.split("\f")
    joined = "\f".join(pages)
    headings = list(SECTION.finditer(joined))
    found = {}
    abstract = re.search(r"(?im)^[ \t]*(?:abstract|摘\s*要)[ \t]*[.:：]?[ \t]*$", joined)
    starts = []
    if abstract:
        starts.append(("abstract", "Abstract", abstract))
    method = next((h for h in headings if METHOD.search(h[2])), None)
    if method:
        starts.append(("method", method[2], method))
    for key, name, anchor in starts:
        depth = anchor[1].count(".") if key == "method" else 10
        end = next((h.start() for h in headings if h.start() > anchor.end() and h[1].count(".") <= depth), len(joined))
        body = re.sub(r"\s+", " ", joined[anchor.end():end]).strip()[:3500]
        if len(body) >= 65:
            page = joined.count("\f", 0, anchor.start()) + 1
            found[key] = {"text": body, "page": page, "section": name.strip()}
    return found


def read_sections(path):
    command = shutil.which("pdftotext")
    if not command:
        return {}
    try:
        proc = subprocess.run([command, "-f", "1", "-l", "18", "-enc", "UTF-8", "-raw", str(path), "-"],
                              capture_output=True, timeout=12, check=True)
    except (OSError, subprocess.SubprocessError):
        return {}
    return excerpts_from_text(proc.stdout.decode("utf-8", "replace"))
