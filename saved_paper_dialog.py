"""按题名回看当前表中的论文；仅使用当前数据表的可见记录。"""

import re
import unicodedata

from activity import step
import saved_focus
from table_store import RecordNotFound
from title_aliases import match_title_alias


_PREFIX = r"(?:(?:麻烦你?|请|帮我|给我|我想|我想要|我想再|我想要再|能不能|可以|先|再|重新)\s*)*"
_VIEW = r"(?:看(?:一看|一下|一眼|看|下)?|回看(?:一下)?|查看(?:一下)?|打开|回顾(?:一下)?|翻看(?:一下)?|翻(?:一翻|一下|下))"
_SAVED = r"(?:(?:我(?:们)?|刚才|上次|之前|昨天|已经|已|刚刚|刚)\s*)*(?:收藏|收录|保存|存)(?:过|好|下来|起来|了)?的"
_HISTORY = (r"(?:刚才|刚刚|上次|之前|刚)(?:(?:看|读|搜|查|提取|填(?:写|表)?|写入?|录入)"
            r"(?:过|了|到表里|进表里)?的?)?(?:那篇|这篇)?")
_LOOK = re.compile(rf"^{_PREFIX}(?:{_VIEW}\s*(?:(?:{_SAVED}|{_HISTORY})\s*)?|{_SAVED}\s*)(.+?)$")
_FIND_AGAIN = re.compile(r"^(?:请|帮我|给我|我想)*再(?:帮我|给我)?(?:搜(?:索|搜|一搜)?|找(?:找|一找)?|查(?:查|一查)?)"
                         r"(?:一下|下)?\s*(.+)$")
_LATIN_TITLE = re.compile(r"[A-Za-z0-9][A-Za-z0-9\s.+#&:’'()/,_-]{0,198}[A-Za-z0-9+#)]")
_QUOTED = re.compile(r'[《「“"]([^》」”"\n]{1,200})[》」”"]')
_DATA_FIELD = re.compile(r"\s*(?:的|里的)\s*(摘要|中文摘要|方法|方法概述|研究方法|阅读笔记|研究笔记|笔记|详情|记录|资源链接|代码链接|代码仓库|模型链接|数据集链接|链接)$")
_NOT_TITLE = re.compile(r"网上|联网|公开题录|Crossref|网络上|全网|按.{0,4}排序|然后|顺便|和之前|查一下表|"
                        r"列|字段|权限|表格|原文|公式|图表|实验数据|表\s*\d|图\s*\d|提取|下载|收藏|写入|填入|删除|修改|"
                        r"所有|哪些|几篇|多少|关于|相关的|刚才|上次|之前|这张表|这篇|那篇|我存的|不要|取消|[，；;？?]", re.I)
_ORDINAL = re.compile(r"(?:请|帮我)?(?:看看|看一下|看|选|查看|打开)?第\s*([零一二两三四五六七八九十百千]+|\d{1,4})\s*(?:篇|条|项|个)(?:吧)?")


def read_ordinal(message):
    match = _ORDINAL.fullmatch(message.strip().rstrip("。！!"))
    if not match:
        return None
    value = match[1].replace("两", "二")
    if value.isdigit():
        return int(value) - 1
    # 展示的候选最多十条，范围外的序号也应由这份列表响应。
    return "一二三四五六七八九十".index(value) if len(value) == 1 and value in "一二三四五六七八九十" else 10


def title_hint(message, *, allow_bare=False):
    sentence = message.strip().rstrip("？?。！!")
    text = sentence.rstrip("吧呢呀啊").strip()
    hit = _LOOK.fullmatch(text) or _FIND_AGAIN.fullmatch(text)
    if not hit:
        if not allow_bare:
            return None
        field = _DATA_FIELD.search(text)
        if field:
            term = re.sub(r"\s*(?:这|那)篇(?:论文)?$", "", text[:field.start()]).strip()
            if _LATIN_TITLE.fullmatch(term):
                return term, field[1]
        if _QUOTED.fullmatch(text):
            return _QUOTED.fullmatch(text)[1], None
        if _LATIN_TITLE.fullmatch(text) and (sentence.endswith("呢") or (
                re.fullmatch(r"[A-Z][A-Z0-9-]{2,11}", text) and text not in {"PDF", "DOI", "URL", "API", "JSON"})):
            return text, None
        return None
    term = hit[1].strip()
    field = _DATA_FIELD.search(term)
    wanted = field[1] if field else None
    term = _DATA_FIELD.sub("", term)
    term = re.sub(r"^(?:这篇|那篇)\s*|\s*(?:这篇|那篇)(?:论文|文献)?$", "", term).strip()
    quoted = _QUOTED.fullmatch(term)
    if quoted:
        return quoted[1], wanted
    if _NOT_TITLE.search(term):
        return None
    if _LATIN_TITLE.fullmatch(term):
        return term, wanted
    # 普通“看看摘要、看看表”仍由各自的专用流程处理。
    if (re.search(r"再看|回看|回顾|收藏|收录|保存|存的", text) and re.fullmatch(r"[\w -]{2,80}", term)
            and not re.search(r"摘要|方法|笔记|记录|数据|论文|来源|这[张份个篇]|那[张份个篇]|表|链接|标题", term)):
        return term, wanted
    return None


def _norm(value):
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return " ".join(re.findall(r"\w+", text))


def _names(chat, wanted):
    return column_names(chat._read_schema(), wanted)


def column_names(schema, wanted=None):
    from workflow import _fields
    titles = [f["name"] for f in _fields(schema, "title")]
    links = [f["name"] for f in _fields(schema, "url")]
    role = {"摘要": "abstract", "中文摘要": "abstract", "方法": "method", "方法概述": "method", "研究方法": "method",
            "研究笔记": "note", "阅读笔记": "note", "笔记": "note", "链接": "url"}.get(wanted)
    wanted_names = [f["name"] for f in _fields(schema, role)] if role else None
    if wanted in {"研究笔记", "阅读笔记"}:
        wanted_names = [n for n in wanted_names if n == wanted]
    return titles, links, wanted_names


def _identity(row, titles, links):
    fields = row["fields"]
    return tuple(next((str(fields[k]) for k in names if fields.get(k)), "") for names in (titles, links))


def matching_rows(rows, term, titles, links):
    needle = _norm(term)
    if not needle:
        return []
    # 短英文词只匹配完整单词，避免用 RAG 查到 storage。
    pattern = None
    versioned = re.fullmatch(r"[A-Za-z][A-Za-z0-9-]*[-.]\d+(?:\.\d+)*", term)
    if versioned:
        pattern = re.compile(r"(?<![\w-])" + re.escape(term) + r"(?![\w-]|\.\d)", re.I)
    elif re.fullmatch(r"[a-z0-9]{1,5}", needle):
        pattern = re.compile(r"(?<![a-z0-9])" + re.escape(needle) + r"(?![a-z0-9])")
    matches = []
    for row in rows:
        title, url = _identity(row, titles, links)
        normalized = _norm(title)
        haystack = unicodedata.normalize("NFKC", title) if versioned else normalized
        literal = pattern.search(haystack) if pattern else needle in haystack
        alias = match_title_alias(term, title)
        if literal or alias:
            matches.append((normalized == needle, row, title, url, alias))
    return [m for m in matches if m[0]] or matches


def _bind(chat, title="", url=""):
    saved_focus.clear(chat)
    chat.last_library_results = None
    chat.last, chat.last_title = url or None, title
    chat.recent_saved, chat.collection_batch, chat.recent_evidence = ([url] if url else []), [], None


def _open(chat, row, title, url, titles, links, pending, wanted, wanted_names, alias=None):
    lines = [f"已打开「{chat.table_name}」中收藏的《{title or url}》。"]
    if alias and alias.kind == "initials":
        lines.append("简称对应题名中的 “" + alias.evidence + "”。")
    details = [(key, str(value)) for key, value in row["fields"].items() if key not in titles + links and value
               and (wanted_names is None or key in wanted_names)]
    if wanted in {"资源链接", "代码链接", "代码仓库", "模型链接", "数据集链接"}:
        from saved_compare import _values
        values = _values(chat._read_schema(), "代码链接" if wanted == "代码仓库" else wanted, row)
        details = [(v["field"], v["value"]) for v in values]
        wanted_names = [v["field"] for v in values]
    for key, value in details:
        lines.append(f"{key}：{value[:1200]}" + ("…" if len(value) > 1200 else ""))
    if not details and not (wanted == "链接" and url):
        lines.append(f"这条记录还没有保存{wanted}。" if wanted_names is not None else "这条记录还没有填写其他内容。")
    if url:
        lines.append("论文链接：" + url)
    reply = {"message": "\n".join(lines), "saved_record_id": row["id"]}
    if pending:
        saved_focus.remember(chat, row, title, url, pending=pending)
        reply.update(pending_id=pending["id"], choices=pending.get("choices", []))
    else:
        # 只在用户已经选定一条真实记录时绑定后续“这篇”的目标。
        _bind(chat, title, url)
        saved_focus.remember(chat, row, title, url)
    return reply


def open_saved_identity(chat, identity, *, pending=None, wanted=None):
    rid, title, url = identity
    titles, links, wanted_names = _names(chat, wanted)
    with step("回读选中的已收藏论文", tool=chat.table_tool):
        try:
            row = chat.table.get(rid)
        except RecordNotFound:
            row = None
    if row is not None and row["id"] == rid and _identity(row, titles, links) == (title, url):
        return _open(chat, row, title, url, titles, links, pending, wanted, wanted_names)
    reply = {"message": "这条收藏已被移除，或原题名、链接已变化。请重新查询当前表。"}
    if pending:
        reply.update(pending_id=pending["id"], choices=pending.get("choices", []))
    else:
        _bind(chat)
    return reply


def handle(chat, message, pending_id):
    followup = saved_focus.handle(chat, message)
    if followup is not None:
        return followup
    task = chat.pending
    choosing = task and task["stage"] == "saved_title_choice"
    if choosing:
        plan = task["plan"]
        choice = message.strip().rstrip("。！!")
        index = None
        if choice in plan["labels"]:
            index = plan["labels"].index(choice)
        else:
            index = read_ordinal(choice)
        if index is not None:
            if task["id"] != pending_id or not 0 <= index < len(plan["rows"]):
                return chat._ask(plan, task["message"], task["choices"], task["stage"])
            reply = open_saved_identity(chat, plan["rows"][index], wanted=plan["wanted"])
            chat.pending = None
            return reply
    if read_ordinal(message) is not None:
        return None
    hint = title_hint(message, allow_bare=not task)
    if not hint:
        return None
    term, wanted = hint
    titles, links, wanted_names = _names(chat, wanted)
    with step("按题名回看当前表的收藏记录", tool=chat.table_tool):
        hits = matching_rows(chat.table.rows(), term, titles, links)
    if choosing:
        task = chat.pending = None
    if len(hits) == 1:
        _, row, title, url, alias = hits[0]
        return _open(chat, row, title, url, titles, links, task, wanted, wanted_names, alias)
    if hits:
        rows = [(row["id"], title, url) for _, row, title, url, _ in hits[:10]]
        labels = [f"{i}. {title}" for i, (_, title, _) in enumerate(rows, 1)]
        if len({title for _, title, _ in rows}) < len(rows):
            labels = [f"{label} · {url or rid}" for label, (rid, _, url) in zip(labels, rows)]
        msg = f"「{chat.table_name}」有 {len(hits)} 条题名匹配「{term}」的收藏。"
        if len(hits) > len(rows):
            msg += "先列前 10 条，可以补充题名关键词缩小范围。"
        if not task:
            _bind(chat)
            return chat._ask({"rows": rows, "labels": labels, "wanted": wanted}, msg + "你想看哪一篇？\n" + "\n".join(labels),
                             [*labels, "取消"], "saved_title_choice")
        msg += "\n" + "\n".join(labels) + "\n可以直接用完整题名查看。"
    else:
        msg = (f"在当前表「{chat.table_name}」没有找到题名或简称匹配「{term}」的论文。"
               f"\n要连摘要和笔记一起查，可以说“在清单里搜 {term}”。也可以说“列出所有数据表”，切回收藏时用的表。")
    if task:
        return {"message": msg, "pending_id": task["id"], "choices": task.get("choices", [])}
    _bind(chat)
    return {"message": msg}
