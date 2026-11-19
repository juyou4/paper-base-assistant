"""依据当前会话的待确认提取项，按需渲染该项对应的 PDF 原页。"""

import hashlib
import io
import math
import re
import subprocess
import sys
import threading
from pathlib import Path


ROUTE = re.compile(r"/api/evidence-page/([0-9a-f]{32})/([0-7])/(0|[1-9][0-9]{0,3})\.png")
MAX_PAGE = 32  # 与 PDF 正文提取范围一致
MAX_BYTES = 50 * 1024 * 1024
LONG_EDGE = 1600
_SLOTS = threading.BoundedSemaphore(2)
_DIGEST = re.compile(r"[0-9a-f]{64}")


def annotate(items, source_url, path, *, initial_stat=None):
    """只给出处与实际读取文件一致的证据项添加可视核对信息。"""
    eligible = []
    for item in items:
        base, _, fragment = item["source_url"].rpartition("#")
        match = re.fullmatch(r"page=([1-9][0-9]?)", fragment)
        if base == source_url and match and int(match[1]) <= MAX_PAGE:
            eligible.append((item, int(match[1])))
    if eligible:
        digest = hashlib.sha256()
        with Path(path).open("rb") as pdf:
            for chunk in iter(lambda: pdf.read(1 << 20), b""):
                digest.update(chunk)
        if initial_stat:
            current = Path(path).stat()
            if (current.st_ino, current.st_size, current.st_mtime_ns) != (
                    initial_stat.st_ino, initial_stat.st_size, initial_stat.st_mtime_ns):
                raise ValueError("提取期间 PDF 已变化，请重新提取后核对原页。")
        fingerprint = digest.hexdigest()
        for item, page in eligible:
            item.update(pdf_page=page, pdf_sha256=fingerprint)
    return items


def evidence_file(chat, library, batch_id, paper_index, item_index):
    import papers

    with chat.lock:
        task = chat.pending
        if not task or task["stage"] != "evidence_confirm" or task["id"] != batch_id:
            return None
        try:
            paper = task["plan"]["entries"][paper_index]
            item = paper["items"][item_index]
        except IndexError:
            return None
        page = item.get("pdf_page")
        digest = item.get("pdf_sha256", "")
        base, _, fragment = item["source_url"].rpartition("#")
        if (type(page) is not int or not 1 <= page <= MAX_PAGE or fragment != f"page={page}"
                or not isinstance(digest, str) or not _DIGEST.fullmatch(digest)):
            return None
        if base.startswith("/api/attachments/"):
            bound = library.for_paper(paper["url"])
            if not bound or bound["id"] != digest or bound["viewer"] != base:
                return None
            path = library.path(digest)
        else:
            path = papers.cached_pdf({"pdf_url": base}, library.data_dir)
        return (path, page, digest) if path else None


def render(path, page, digest):
    """在独立进程渲染；并发 PDFium 调用、损坏 PDF 或超时不影响服务进程。"""
    if not _SLOTS.acquire(blocking=False):
        raise ValueError("另有原页正在渲染，请稍后重试，或打开原文。")
    try:
        child = subprocess.run(
            [sys.executable, "-B", str(Path(__file__).resolve()), str(path), str(page), digest],
            capture_output=True, timeout=18, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError("原页渲染超时或不可用，可以打开原文核对。") from exc
    finally:
        _SLOTS.release()
    if child.returncode == 2:
        raise ValueError("显示 PDF 原页需要 pypdfium2 和 Pillow，可以先打开原文。")
    if child.returncode == 3:
        raise ValueError("PDF 文件已变化，请重新提取后核对。")
    if child.returncode or not child.stdout.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("无法显示该页，请打开原文核对。")
    return child.stdout


def _render_worker(path, page_number, digest):
    from contextlib import closing
    try:
        import pypdfium2 as pdfium
        from PIL import Image
    except ImportError:
        return 2
    with path.open("rb") as handle:
        data = handle.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES or hashlib.sha256(data).hexdigest() != digest:
        return 3
    if not 1 <= page_number <= MAX_PAGE:
        return 1
    with closing(pdfium.PdfDocument(data)) as doc:
        if page_number > len(doc):
            return 1
        with closing(doc[page_number - 1]) as page:
            width, height = page.get_size()
            if not all(math.isfinite(n) and n > 0 for n in (width, height)):
                return 1
            scale = min(2.0, LONG_EDGE / max(width, height))
            with closing(page.render(scale=scale)) as bitmap, bitmap.to_pil() as image:
                encoded = io.BytesIO()
                image.save(encoded, format="PNG")
                sys.stdout.buffer.write(encoded.getvalue())
    return 0


if __name__ == "__main__":
    sys.exit(_render_worker(Path(sys.argv[1]), int(sys.argv[2]), sys.argv[3]))
