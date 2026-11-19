"""通过当前会话选定的记录，回答“它的摘要呢”等表内追问。"""

import re
from dataclasses import dataclass

_PAPER = (r"(?:它|(?:刚才|刚刚|刚|之前|上次|上面)?(?:选中|打开|回看|收藏|看过|读过)?的?"
          r"[这那]一?篇(?:论文|文献)?|上一篇(?:论文|文献)?)")
_FIELD = r"(?:中文摘要|论文摘要|摘要|方法概述|研究方法|技术路线|方法|阅读笔记|研究笔记|笔记|论文链接|原文链接|链接|记录|详情)"
_READ = r"(?:再|也|先)?(?:看看|看一下|看一眼|看下|查看|打开|回看|显示|读一下)"
_QUESTION = re.compile(
    rf"(?:(?:请问|请|帮我|给我|我想|能不能|可以|那么|那)\s*)*(?P<read>{_READ})?\s*(?P<paper>{_PAPER})?"
    rf"\s*(?:里保存的|已存的|里的|的|还有|有没有|有)?\s*(?P<field>{_FIELD})?"
    r"(?P<ending>是什么|写了什么|有哪些|呢|呢呀|吧|吗)?"
)


@dataclass(frozen=True)
class _Focus:
    table: object
    record_id: str
    title: str
    url: str
    last: str | None
    last_title: str | None


def clear(chat):
    chat.saved_focus = None
    chat.saved_preview_focus = None


def remember(chat, row, title, url, *, pending=None):
    focus = _Focus(chat.table, row["id"], title, url, chat.last, chat.last_title) if title or url else None
    if pending:
        # 待确认期间另读的论文只用于阅读，待确认的写入目标仍由原工作流持有。
        chat.saved_preview_focus = (pending["id"], pending["stage"], focus)
    else:
        chat.saved_focus = focus
        chat.saved_preview_focus = None


def remember_row(chat, row, schema):
    """收藏、按链接重看或写入完成后，以本次回读的真实行建立指代。"""
    from saved_paper_dialog import _identity
    from workflow import _fields

    titles = [f["name"] for f in _fields(schema, "title")]
    links = [f["name"] for f in _fields(schema, "url")]
    remember(chat, row, *_identity(row, titles, links))


def _answer(chat, message):
    reply = {"message": message}
    if chat.pending:
        reply.update(pending_id=chat.pending["id"], choices=chat.pending.get("choices", []))
    return reply


def handle(chat, message):
    text = message.strip().rstrip("？?。！!").strip()
    match = _QUESTION.fullmatch(text)
    if not match or not (match["paper"] or match["field"] and (match["read"] or match["ending"])):
        return None
    task = chat.pending
    if task and (text in task.get("choices", ()) or (
            task["stage"] in {"paper", "single_link"} and match["paper"] and not match["field"] and not match["read"])):
        return None  # 用户正在回答已有流程中的明确选项或目标论文。
    if task and task["stage"] in {"saved_title_choice", "title_choice", "title_selected", "collection_choice"}:
        return _answer(chat, "请先完成当前的论文选择，再查看所选记录。")

    preview = chat.saved_preview_focus
    during_preview = bool(task and preview and preview[:2] == (task["id"], task["stage"]))
    focus = preview[2] if during_preview else chat.saved_focus
    if (not focus or focus.table is not chat.table or (chat.last, chat.last_title) != (focus.last, focus.last_title)
            or not during_preview and (chat.collection_batch or chat.recent_evidence)):
        return _answer(chat, f"请先指定「{chat.table_name}」里的一篇收藏，例如“再看看《论文标题》”。")

    from saved_paper_dialog import _bind, open_saved_identity
    wanted = {"技术路线": "方法", "论文摘要": "摘要", "论文链接": "链接", "原文链接": "链接"}.get(match["field"], match["field"])
    reply = open_saved_identity(chat, (focus.record_id, focus.title, focus.url), pending=task, wanted=wanted)
    if task and not reply.get("saved_record_id"):
        if not during_preview:
            _bind(chat)
        elif focus == chat.saved_focus:
            chat.saved_focus = None
        chat.saved_preview_focus = (task["id"], task["stage"], None)
    return reply
