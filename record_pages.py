"""按页展示阅读清单，跨页筛选使用与卡片相同的题名简称和字段文字。"""

import re
import time
import unicodedata

from table_store import LocalTable, TableError
from title_aliases import title_alias_terms


def _text(value):
    return " ".join(unicodedata.normalize("NFKC", str(value)).casefold().split())


def read_page(table, titles, cursor="", query="", progress=""):
    if not isinstance(cursor, str) or len(cursor) > 2048 or len(query) > 240 or progress not in ("", "待读", "在读", "已读"):
        raise ValueError("清单的筛选条件或翻页位置无效。")
    needle = _text(query)
    pattern = None
    if re.fullmatch(r"[a-z0-9]{1,5}", needle):
        pattern = re.compile(r"(?<![a-z0-9])" + re.escape(needle) + r"(?![a-z0-9])")
    elif re.fullmatch(r"[a-z][a-z0-9-]*[-.]\d+(?:\.\d+)*", needle):
        pattern = re.compile(r"(?<![a-z0-9_-])" + re.escape(needle) + r"(?![a-z0-9_-]|\.\d)")
    reading = table.reading_progress() if isinstance(table, LocalTable) else None
    seen, rows, aliases = set(), [], {}
    deadline = time.monotonic() + 12
    while True:
        seen.add(cursor)
        page, cursor, total = table.page(cursor)
        for row in page:
            fields = row["fields"]
            terms = title_alias_terms(next((fields[n] for n in titles if fields.get(n)), ""))
            if progress and (reading or {}).get(row["id"], "待读") != progress:
                continue
            if needle and needle not in terms and not any(
                    pattern.search(_text(v)) if pattern else needle in _text(v) for v in fields.values()):
                continue
            rows.append(row)
            if terms:
                aliases[row["id"]] = terms
        if cursor and cursor in seen:
            raise TableError("阅读清单翻页位置重复，请刷新后重试。")
        if rows or not cursor or time.monotonic() >= deadline or len(seen) >= 20:
            break
    return {"rows": rows, "title_aliases": aliases, "reading_progress": (
        {r["id"]: reading[r["id"]] for r in rows if r["id"] in reading} if reading is not None else None),
        "page": {"next": cursor, "total": total, "query": query, "progress": progress}}
