"""在论文写入之前区分查询、预览、假设以及用户明确排除的操作。"""

import re

import papers
from activity import step


_SAVE = r"(?:保存|收藏|收录|收下|收着|入库|入表|归档|登记|录入|存(?!在))"
_WRITE = rf"(?:{_SAVE}|加到|加入|加进|放到|放入|放进|填|写|记一[句条]|记笔记|记录|补|提交|同步|更新|改)"
_NEG = r"(?:先不|暂时不|暂不|不用|不需要|无需|不想|不打算|不要|不必|不急(?:着)?|不是要|别(?:急着)?|不)"
_NO_WRITE = re.compile(r"没(?:有)?(?:让你|叫你|说过?要|打算|准备|必要|要求)(?:帮我|直接)?\s*" + _WRITE + "|" +
                       _NEG + r"(?:再|给我|直接|自动|马上|急着|忙着|把|给|这篇|论文|链接|摘要|先|就|往表里|往飞书里|动){0,5}" + _WRITE)
_HYPOTHETICAL = re.compile(r"如果|要是|假如|假设|假使|打个比方|举个例子|比如(?:说)?|倘若|的话.{0,10}(?:会|能|就)|"
                           r"会(?:怎么[样办]|发生什么|有什么影响)|(?:只有|只要|满足|符合|能搜到|能找到)[^，。；]{1,24}(?:才|就)")
_LATER = re.compile(r"(?:等.{0,18}(?:再|才)|(?:待会[儿]?|一会[儿]?|之后|以后|后面|明天|下周|改天|晚[点些]|回头)"
                    r"(?:我|你)?再?)\s*(?:来|帮我|把.{0,8})?" + _WRITE)
_BEFORE_WRITE = re.compile(_WRITE + r"[^，。；]{0,12}(?:之前|前先)|(?:先给我|先让我|先)(?:核对|过目|预览|看一下|看看|看下)"
                           r".{0,16}(?:效果|结果|会写|写哪些)|(?:不对|算了|先等等)[，,\s]*(?:先|还是)?看")
_RETRACT = re.compile(r"(?:算了|先等等|等一下|先缓缓|先放一放|先不弄|先不做|先不动|先等等看)[吧啊了。！!\s]*$")
_QUESTION = re.compile(r"[？?]|要不要|能否|是否|行不行|能不能|可不可以|能不能够|会不会|(?:吗|么|嘛|可否|行吗)\s*[。！!]?\s*$")
_STATUS = re.compile(r"在不在.{0,12}(?:收藏|清单|表里|表中|表格)|"
                     r"(?:表里|表格里|库里|收藏夹里|清单里)(?:面)?(?:已经)?(?:有(?:没有|没)?|是不是有)(?:了)?这篇|"
                     r"(?:收没收|存没存|录没录)(?:过)?|(?:有没(?:有)?|是不是|是否)(?:已经)?[^，。；,;]{0,6}" + _SAVE + r"|"
                     r"(?:当时|之前|以前|先前)(?:已经|有)" + _SAVE + r"|"
                     rf"(?:{_SAVE}|收)(?:过|了|好|的)[^，。；,;]{{0,12}}(?:么|嘛|了吗|没[有]?|[？?])")
_READ = re.compile(r"只(?:是)?(?:想|要)?(?:看|读|了解|查)|看(?:看|下|一下)|先看|查看|读一下|想知道|给我看|是什么|讲了什么|展示")
_META = {"title": r"标题|题名|题目", "url": r"链接|网址|地址", "abstract": r"摘要|简介", "method": r"方法|技术路线"}
_LABEL = {"title": "标题", "url": "论文链接", "abstract": "摘要", "method": "方法"}
_FIELD = r"(?:摘要|方法(?:概述)?|链接|标题|笔记|图表|数据|公式|(?:原文\s*)?PDF|全文)"
_FIELD_LIST = rf"{_FIELD}(?:(?:和|与|及|、){_FIELD})*"
_EXCLUDED = re.compile(rf"(?:{_FIELD_LIST}(?:先|暂时)?{_NEG}(?:用|要|看|读|填|写|存|提取|下载)?|"
                       rf"(?:先|暂时)?{_NEG}(?:再|用|要|填|写|保存|存|看|读|记录|提取|下载|分析)*\s*{_FIELD_LIST}"
                       rf"|(?:先|暂时)?{_NEG}把{_FIELD_LIST}(?:存|填|写)(?:进[去来]?|入|到表里))"
                       r"(?:了|吧)?", re.I)
_COLLECT_ONLY = re.compile(rf"(?:请|先|仅|只(?:需|要|想)?|帮我|麻烦|就|直接|把|将|这[几两]?篇|的|论文|文献|链接|标题|和|、|\s)*"
                           rf"{_SAVE}(?:一下|下|起来|到阅读清单|到表里|进阅读清单|这[几两]?篇|论文|文献|的|链接|标题|和|、|就好|就行|吧|\s)*")
_ASK_SAVE = re.compile(rf"(?:请问|那|我)?(?:能否|能不能|可以|可不可以|能)(?:帮我|给我)?(?:把|将)?"
                       rf"(?:这篇|这个|它|论文|链接|的|\s)*{_SAVE}"
                       rf"(?:下|一下|起来|到阅读清单|到表里|这篇|这个|它|论文|链接|的|吗|么|嘛|呢|\s)*[？?。]?")
_PAPER_STAGES = {"paper", "single_link", "field", "language", "ambiguous_paper", "ambiguous_intent"}
_CANCEL_PAPER = re.compile(r"(?:算了[，,、 ]*)?(?:先|暂时)?(?:不|别|不要|不用)(?:再|继续)?"
                           r"(?:保存|收藏|写入|填表|回填|写|填|存|录入)(?:了|啦|吧)?[。！!]?")


def _excluded_field(clause):
    if _EXCLUDED.fullmatch(clause):
        return True
    later = _LATER.match(clause)
    return bool(later and re.fullmatch(_FIELD_LIST, clause[later.end():].strip(), re.I))


def _content_preview(chat, urls, roles, text):
    if not urls:
        return {"message": "请把论文链接和想查看的内容一起发来，例如“看看这篇的摘要 + 链接”。"}
    lines = []
    evidence = []
    for url in urls:
        with step("读取论文内容供查看", tool="论文解析"):
            paper = papers.resolve_paper(url, chat.data_dir, need_content="method" in roles)
        lines.append(paper.get("title") or url)
        for role in roles:
            if role in ("title", "url"):
                value = paper.get(role)
            else:
                original = paper.get(role + "_original")
                summary = paper.get("abstract_zh" if role == "abstract" else "method")
                value = original if re.search(r"原文|英文", text) else summary or original
                if value and paper.get(role + "_url"):
                    evidence.append({"field": _LABEL[role], "source": paper.get(role + "_source", ""),
                                     "quote": paper.get(role + "_quote", ""), "url": paper[role + "_url"]})
            if role != "title" or not value:
                lines.append(f"{_LABEL[role]}：{value or '该来源暂未提供可核对的内容。'}")
    if chat.pending is None:
        # “刚才看的那篇”指向这次阅读，收藏批次仍可按原展示顺序索引。
        chat.recent_evidence = None
        chat.recent_saved = []
        chat.last = urls[0] if len(urls) == 1 else None
        chat.last_title = lines[0] if len(urls) == 1 else None
    return {"message": "\n".join(lines), "evidence": evidence}


def guard_paper_request(chat, message, pending_id=None, request_id=None):
    """返回只读答复、受限确认或缩小后的操作结果。None 交还现有对话流程。"""
    from admin_dialog import table_lookup_kind
    from workflow import (ADMIN_OR_LITERAL, EVIDENCE_REFERENCES, NOTE_TEXT, PAPER_CONTEXT, PREVIOUS_REF,
                          QUOTED_NOTE, REFERENCE, SAVED_QUESTION, _evidence_kinds, _evidence_urls)
    task = chat.pending
    if task and task["stage"] in _PAPER_STAGES | {"paper_save_offer"} and _CANCEL_PAPER.fullmatch(message.strip()):
        chat.pending = None
        return {"message": "已取消这项待处理的论文操作。"}
    if task and task["stage"] == "paper_save_offer":
        if message in {"取消", "算了", "不用了", "不存了", "先不收藏", "先不存"}:
            chat.pending = None
            return {"message": "已取消收藏。"}
        if message == "确认收藏" and pending_id == task["id"]:
            chat.pending = None
            try:
                return chat.respond("收藏 " + task["plan"]["url"], request_id=task["id"])
            except Exception:
                if chat.pending is None:
                    chat.pending = task
                raise
        lookup = table_lookup_kind(message, chat.table_name)
        if lookup:
            return {"message": chat._table_lookup(lookup), "choices": task["choices"], "pending_id": task["id"]}
        if _STATUS.search(message) or SAVED_QUESTION.search(message):
            result = chat._saved_status(message if REFERENCE.search(message) else task["plan"]["url"])
            return {**result, "choices": task["choices"], "pending_id": task["id"]}
        return chat._ask(task["plan"], task["message"], task["choices"], task["stage"])
    if (pending_id and not task) or (task and task["stage"] not in _PAPER_STAGES):
        return None
    # 引号中的笔记和冒号后的逐字记录均为数据；只分析它们之外的要求。
    note = NOTE_TEXT.fullmatch(message.strip())
    instruction = note["head"] if note else message
    quote = QUOTED_NOTE.search(instruction)
    if quote:
        instruction = instruction[:quote.start(1)] + "…" + instruction[quote.end(1):]
    text = EVIDENCE_REFERENCES.sub(" ", instruction).strip()
    text = re.sub(r"《[^《》\n]*》", "《…》", text)
    if (not text or (not quote and ADMIN_OR_LITERAL.search(text))
            or not (task or REFERENCE.search(instruction) or PAPER_CONTEXT.search(text) or re.search(r"收藏|收录", text)
                    or (chat.last or chat.recent_evidence or chat.collection_batch) and PREVIOUS_REF.search(text))):
        return None
    if not REFERENCE.search(instruction) and table_lookup_kind(instruction, chat.table_name):
        return None
    has_write = re.search(_WRITE, text)
    if "来源" in text and not has_write:
        return None  # 让原有收藏来源查询返回同篇论文的全部入口卡片。
    if has_write and _HYPOTHETICAL.search(text):
        result = {"message": "这是一个带条件或假设的操作。要执行时，请指定要处理的论文和写入内容。"}
    elif _STATUS.search(text) or SAVED_QUESTION.search(text):
        result = chat._saved_status(instruction)
    else:
        clauses = [c.strip() for c in re.split(r"[，,。；;\n]+|(?<=链接)(?=(?:先不|不要|别|不写))", text) if c.strip()]
        excluded = [c for c in clauses if _excluded_field(c)]
        positive = [c for c in clauses if c not in excluded]
        if excluded and len(positive) == 1 and not task and _COLLECT_ONLY.fullmatch(positive[0]):
            # 仅当目标论文确定、肯定部分单纯要求收藏时，才能安全移除被排除的附加动作。
            urls = _evidence_urls(instruction) or chat._previous_evidence_urls(text)
            if urls and not any(re.search(r"链接|标题", c) for c in excluded):
                return chat.respond(positive[0] + " " + " ".join(urls), request_id=request_id)
        deferred = bool(_LATER.search(text) or _BEFORE_WRITE.search(text))
        declined = bool(_NO_WRITE.search(text) or _RETRACT.search(text))
        question = bool(has_write and _QUESTION.search(text))
        evidence = _evidence_kinds(text)
        if not (note or quote) and _READ.search(text) and not evidence and (declined or deferred or not has_write):
            roles = [role for role, pattern in _META.items() if re.search(pattern, " ".join(positive))]
            if roles:
                urls = _evidence_urls(instruction) or chat._previous_evidence_urls(text)
                result = _content_preview(chat, urls, roles, text)
            elif deferred or declined:
                result = {"message": "想看论文的哪一部分？可以说“看看摘要”或“提取实验数据和图表”。"}
            else:
                return None
        elif not (note or quote) and evidence and (declined or deferred) and re.search(r"看|预览|过目|提取|抽取|整理", text):
            # 提取可以与“暂时别写”并存；此分支最多进入只读预览。
            if task:
                result = {"message": "当前仍在等你选择写入目标。可先取消，再单独预览这篇论文的证据。"}
            else:
                result = chat._route_evidence(message) or {"message": "可以指定要预览的实验数据、图表或公式。"}
        elif question and not (declined or deferred) and not task and _ASK_SAVE.fullmatch(text):
            urls = _evidence_urls(instruction) or chat._previous_evidence_urls(text)
            if len(urls) == 1:
                return chat._ask({"url": urls[0]}, f"可以收藏这篇论文。请核对地址后确认：\n{urls[0]}",
                                 ["确认收藏", "取消"], "paper_save_offer")
            result = {"message": "请明确指定要收藏的论文链接。"}
        elif declined or deferred or question or excluded and has_write:
            result = {"message": "本次未写入。需要执行时，请明确指定要保存的论文和内容。"}
        else:
            return None
    if task:
        result.update(pending_id=task["id"], choices=task.get("choices", []))
    return result
