"""区分查收藏与查公开题录，并仅在当前数据表的论文文本列中查找关键词。"""

import re
import unicodedata

from activity import step
import paper_search_dialog
from title_aliases import match_title_alias


LOCAL = "查当前阅读清单"
PUBLIC = "查公开论文题录"
_FILLER = r"(?:(?:麻烦你?|能不能|能否|可以|请|帮我|替我|给我|我想|先|去|在|从|到)\s*)*"
_LOCAL = (r"(?:(?:我(?:们)?(?:之前|已经|已)?(?:收藏|保存|收录)的(?:论文|文献|文章))|"
          r"(?:我(?:们)?的|当前(?:的)?|这[张份]|本)?(?:阅读清单|论文清单|收藏夹|论文库|文献库|清单|数据表|表格|表(?=[里内中])))"
          r"(?:里面|里边|里|内|中)?")
_PUBLIC = r"(?:网上|全网|公开(?:的)?(?:论文)?(?:题录|文献库)|Crossref)(?:\s*[里中上])?"
_VERB = r"(?:搜(?:索|一搜|搜)?|查找|检索|找(?:一找|找)?|查(?!看)(?:一查|查|询)?)(?:一下|下)?"
_INTRO = re.compile(rf"^{_FILLER}(?P<before>{_LOCAL}|{_PUBLIC})?\s*{_FILLER}(?P<verb>{_VERB})\s*"
                    rf"{_FILLER}(?P<after>{_LOCAL}|{_PUBLIC})?\s*(?P<rest>.*)$", re.I)
_TOPIC = re.compile(r"^(?:(?:几|一|两|三)篇\s*)?(?:(?:关于|有关|和|跟|与)\s*)?"
                    r"(?P<term>.+?)\s*(?:(?:相关|有关)的?|方面的|的)?(?:论文|文献|文章)$", re.I)
_UNSUPPORTED = re.compile(r"然后|顺便|并且|接着|同时|并(?:收藏|保存|写入)|如果|假如|不要|不用|先别|排序|排名|"
                          r"近[几一二三五十0-9]+年|(?:19|20)\d\d年|未读|已读|按(?:时间|年份|作者)|有几篇|有多少|[，,;；。！？?\n]")
_RESERVED = re.compile(r"^(?:这|那|刚才|上面|相关|该|这些|那些|一下|几篇|几条|[一两三]篇|论文|文献|文章|"
                       r"表|字段|数据|记录|链接|摘要|公式|图表|结果|权限|收藏|收藏的|我的|关于|有关)+$")
_TERM = re.compile(r"[\w][\w\s.+#&:'’/\-()]{0,158}[\w+#)]|[A-Za-z\u4e00-\u9fff]", re.UNICODE)
_QUOTES = {"《": "》", "「": "」", "“": "”", '"': '"'}
_CHOOSE_LOCAL = re.compile(r"(?:那就|就|先|只)?(?:查|搜|看)?(?:一下)?(?:当前阅读清单|(?:我(?:的)?)?(?:收藏(?:夹|的)?|清单)|我的|飞书表里|表里)(?:面)?(?:看看|找找|就好|吧)?")
_CHOOSE_PUBLIC = re.compile(r"(?:那就|就|先|只)?(?:去|查|搜|看)?(?:一下)?(?:公开论文题录|公开题录|公开的|网上|全网|Crossref)(?:搜(?:搜)?|查查|找找|看看)?(?:吧)?", re.I)
_OPEN_SINGLE = re.compile(r"(?:打开|看看|查看|看一下)(?:这篇|这一篇|这条|唯一的那篇)(?:论文|记录)?[吧。！! ]*")


def request(message):
    """只消费有检索动词的完整问句，保留明确题名、收藏及提供链接的专用流程。"""
    match = _INTRO.fullmatch(message.strip().rstrip("。！？?!").rstrip("吗呢").strip())
    if not match:
        return None
    scopes = [s for s in (match["before"], match["after"]) if s]
    local = any(re.fullmatch(_LOCAL, s) for s in scopes)
    public = any(re.fullmatch(_PUBLIC, s, re.I) for s in scopes)
    rest = match["rest"].strip()
    if match["after"] and rest.startswith("的"):
        rest = rest[1:].strip()
    if local and re.search(r"(?:多少|几)[条行]|记录数|字段类型|这[一]?[列栏]", rest):
        return None
    if not scopes:
        # 明确给出引用题名或“找几篇关于…的论文”沿用公开书目检索。
        legacy = paper_search_dialog.title_request(message)
        if legacy and (rest[:1] in _QUOTES or re.match(r"(?:几|一|两|三)篇|关于|有关", rest)):
            if re.search(r"我(?:们)?(?:之前|已)?(?:的|收藏|保存|收录)|表里|清单", legacy[0]):
                return "unsupported", ""
            return None
        if not re.search(r"论文|文献|文章", rest) and (
                match["verb"] in {"查询", "查找", "查一下", "查下", "查"}
                or not re.fullmatch(r"[A-Za-z][\w +#./()\-]*", rest)):
            return None
    topic = _TOPIC.fullmatch(rest)
    if not scopes and not topic and match["verb"].startswith("查"):
        return None
    term = (topic["term"] if topic else rest).strip()
    if term[:1] in _QUOTES and term.endswith(_QUOTES[term[0]]):
        term = term[1:-1].strip()
    if not term or _RESERVED.fullmatch(term) or re.search(r"^(?:有没|是否|都有|有哪|有些|有啥|什么|哪些|多少)", term):
        return ("incomplete", "") if scopes else None
    if re.search(r"^(?:出|一篇|几篇|一下|看)|有没有|提到|是否有|翻翻|聊过|上次收藏|那篇关于", term):
        return None
    if _UNSUPPORTED.search(rest) or not _TERM.fullmatch(term) or re.search(
            r"https?://|10\.\d{4,9}/|arxiv[:：]|(?:网上|表里|清单里|收藏夹里)|"
            r"(?:提取|删除|清空|写入|回填|授权|帮我|并保存|并收藏)", term, re.I):
        return ("unsupported", "") if scopes else None
    # 简单“查一下表”归表格概览，“找几篇论文”需要补充主题。
    if not scopes and re.search(r"这张表|表格|表里|字段|列名|这篇|那篇|上述|刚才", term):
        return None
    scope = "ask" if local == public else ("local" if local else "public")
    return scope, term


def _clean(value):
    return " ".join(unicodedata.normalize("NFKC", str(value or "")).split())


def _result_context(chat, task):
    return (chat.table, getattr(chat.table, "app_token", ""), getattr(chat.table, "table_id", ""),
            chat.last, chat.last_title, task and task["id"], task and task["stage"])


def remember_results(chat, identities, task):
    chat.last_library_results = {"context": _result_context(chat, task), "rows": identities}


def is_result_reference(message):
    from saved_paper_dialog import read_ordinal
    return read_ordinal(message) is not None or bool(_OPEN_SINGLE.fullmatch(message.strip()))


def open_result(chat, message, pending_id):
    results = getattr(chat, "last_library_results", None)
    if not results:
        return None
    task = chat.pending
    if results["context"] != _result_context(chat, task):
        chat.last_library_results = None
        return None
    if pending_id != (task["id"] if task else None):
        return None
    from saved_paper_dialog import open_saved_identity, read_ordinal
    index = read_ordinal(message)
    shown = len(results["rows"])
    if index is None:
        if not _OPEN_SINGLE.fullmatch(message.strip()):
            return None
        index = 0 if shown == 1 else -1
    if not 0 <= index < shown:
        text = f"刚才列出了 {shown} 条匹配记录。请按显示的序号选择，例如“打开第1篇”。"
        reply = {"message": text}
        if task:
            reply.update(pending_id=task["id"], choices=task.get("choices", []))
        return reply
    reply = open_saved_identity(chat, results["rows"][index], pending=task)
    if reply.get("saved_record_id"):
        # 打开同一结果列表中的记录后，仍可翻看这个列表的另一条。
        results["context"] = _result_context(chat, task)
        chat.last_library_results = results
    else:
        chat.last_library_results = None
    return reply


def _matching_field(fields, searchable, titles, term, needle, boundary):
    for name in searchable:
        value = _clean(fields.get(name))
        folded = value.casefold()
        if boundary:
            match = boundary.search(folded)
            index = match.start() if match else -1
        else:
            index = folded.find(needle)
        if index >= 0:
            if name in titles:
                return name, ""
            start, stop = max(0, index - 24), min(len(value), index + len(needle) + 44)
            return name, ("…" if start else "") + value[start:stop] + ("…" if stop < len(value) else "")
        if name in titles and (alias := match_title_alias(term, fields.get(name))):
            return name, f"{term} 对应题名中的 “{alias.evidence}”。"
    return None


def search_table(chat, term, *, source_selected=False):
    from saved_paper_dialog import _bind, _identity
    from workflow import ROLES, _fields

    chat.last_library_results = None
    task = None if source_selected else chat.pending
    if not task:
        _bind(chat)
    schema = chat._read_schema()
    names = {role: [f["name"] for f in _fields(schema, role)] for role in ROLES}
    searchable = list(dict.fromkeys(names["title"] + names["abstract"] + names["method"] + names["note"]))
    if not searchable or not (names["title"] or names["url"]):
        return {"message": f"「{chat.table_name}」需要有论文标题或链接，以及可检索的标题、摘要、方法或笔记文本列。"}
    needle = _clean(term).casefold()
    boundary = (re.compile(r"(?<![a-z0-9])" + re.escape(needle) + r"(?![a-z0-9])")
                if re.fullmatch(r"[a-z0-9]{1,5}", needle) else None)
    if re.fullmatch(r"[a-z][a-z0-9-]*[-.]\d+(?:\.\d+)*", needle):
        boundary = re.compile(r"(?<![\w-])" + re.escape(needle) + r"(?![\w-]|\.\d)")
    with step("按关键词筛选当前表的可见论文", tool=chat.table_tool):
        rows = chat.table.rows()
        hits = []
        count = 0
        alias_count = 0
        for row in rows:
            fields = row["fields"]
            title, url = _identity(row, names["title"], names["url"])
            if not (title or url):
                continue
            found = _matching_field(fields, searchable, names["title"], term, needle, boundary)
            if found:
                count += 1
                if found[0] in names["title"] and found[1]:
                    alias_count += 1
                if len(hits) < 10:
                    hits.append(((row["id"], title, url), *found))
    summary = f"在「{chat.table_name}」中找到 {count} 条包含「{term}」的论文记录。"
    if alias_count:
        summary = f"在「{chat.table_name}」中找到 {count} 条匹配「{term}」的论文记录，其中 {alias_count} 条命中题名简称。"
    if not hits:
        return {"message": summary + "\n已查字段：" + "、".join(searchable) + "。"}
    lines = [summary]
    for i, ((_, title, url), field, excerpt) in enumerate(hits, 1):
        lines.append(f"{i}. {_clean(title or url)}（命中「{field}」）" + (f"\n   {excerpt}" if excerpt else ""))
    if count > len(hits):
        lines.append("这里显示前 10 条。")
    # 有待选论文或字段时，序号优先属于那份预览；纯确认阶段可继续只读回看。
    if not task or task["stage"] in {"schema", "admin_commit", "evidence_confirm"}:
        remember_results(chat, [identity for identity, _, _ in hits], task)
        lines.append("可以说“打开第一篇”查看收藏记录。")
    return {"message": "\n".join(lines)}


def handle(chat, message, pending_id):
    task = chat.pending
    if task and task["stage"] == "search_source":
        choice = message.strip(" 。！？?!")
        use_local = _CHOOSE_LOCAL.fullmatch(choice)
        if use_local or _CHOOSE_PUBLIC.fullmatch(choice):
            if task["id"] != pending_id:
                return chat._ask(task["plan"], task["message"], task["choices"], task["stage"])
            term = task["plan"]["term"]
            if use_local:
                reply = search_table(chat, term, source_selected=True)
                chat.pending = None
                return reply
            # 先完成题录查询，失败时仍可重试该选择。下一步使用独立的确认编号。
            chat.pending = None
            try:
                result = paper_search_dialog.start(chat, term, False)
            except Exception:
                chat.pending = task
                raise
            if chat.pending is None:
                chat.pending = task
                return chat._ask(task["plan"], result["message"] + " 也可以改查自己的收藏。",
                                 [LOCAL, PUBLIC, "取消"], task["stage"])
            return result
    query = request(message)
    if query is None and paper_search_dialog.title_request(message) is None:
        from library_search_intent import classify
        query = classify(message, chat.table_name)
    if not query:
        return None
    scope, term = query
    if scope == "local":
        reply = search_table(chat, term)
    elif scope in ("incomplete", "unsupported"):
        reply = {"message": "请给一个要查的主题或题名，例如“在清单里搜 Transformer”。可以按关键词查当前表或公开题录。"}
    elif task:
        reply = {"message": "当前还有一项待确认。请先完成或取消，再开始新的题录检索。"}
    elif scope == "public":
        return paper_search_dialog.start(chat, term, False)
    else:
        return chat._ask({"term": term}, f"你要找「{term}」。是查自己收藏的论文，还是去网上查公开题录？",
                         [LOCAL, PUBLIC, "取消"], "search_source")
    if task:
        reply.update(pending_id=task["id"], choices=task.get("choices", []))
    return reply
