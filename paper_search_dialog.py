"""在 Crossref 展示题录候选，用户明确收藏后才交给记录写入流程。"""

import re

import papers
from activity import step
from source_library import card


_QUERY = re.compile(r"^(?:我想要?|想要|我要|麻烦|请|帮我|给我|先|再|重新|\s)*"
                    r"(?P<verb>收藏|收录|保存|查找|搜索|查询|搜搜|搜|找找|找)"
                    r"(?:一下|一找|一搜|几篇|两篇|一篇|下)?\s*(?:这篇论文|论文|文献)?\s*"
                    r"(?P<text>.+?)\s*[。！？?]?$", re.I)
_QUOTED = re.compile(r"[《「“\"]([^》」”\"\n]{2,200})[》」”\"]")
_TOPIC = re.compile(r"(?:关于|有关|跟|与)\s*(.{2,160}?)\s*(?:相关)?的(?:论文|文献|文章)")
_SAVE = {"收藏", "收录", "保存"}
_PRIVATE_QUERY = re.compile(r"(?:我(?:们)?(?:的|已|之前)?(?:收藏|收录|保存|笔记)|清单[里内中]|收藏夹|"
                            r"(?:项目|客户)(?:内部)?代号|(?:内部|私密|保密)(?:项目|组会|笔记|数据|资料)|"
                            r"(?:研究笔记|阅读笔记|论文链接|中文摘要)(?:这[一]?)?[列栏])")
_PICK = re.compile(r"(?:请|帮我|就|先)*(?P<action>收藏|保存|收录|看看|查看|看|选|打开)?\s*"
                   r"(?:第\s*)?(?P<n>[一二两三1-3])\s*(?:篇|项|条|个)?(?:论文|文献)?(?:吧|就好)?")


def title_request(text):
    match = _QUERY.fullmatch(text.strip())
    if not match:
        return None
    value = match["text"].strip()
    topic = _TOPIC.fullmatch(value)
    quoted = _QUOTED.fullmatch(value)
    if quoted:
        query = quoted[1]
    elif topic and match["verb"] not in _SAVE:
        query = topic[1]
    elif re.fullmatch(r"[A-Za-z][\w\s:'’+.,&()/-]{4,199}", value, re.ASCII):
        query = value
    else:
        return None
    return query.strip(), match["verb"] in _SAVE


def start(chat, query, saving):
    if _PRIVATE_QUERY.search(query):
        return {"message": "检索词里包含清单、字段或内部资料的描述。请单独给出要公开检索的论文题名或关键词，也可以说“在清单里搜……”查自己的收藏。"}
    with step("按题名或关键词查询书目候选", tool="Crossref"):
        matches = papers.search_titles(query)
    if not matches:
        return {"message": "未取得可核对的题录，请补充论文标题、DOI 或链接。"}
    labels = []
    for i, paper in enumerate(matches, 1):
        author = next(iter(paper.get("authors") or ()), "")
        details = " · ".join(x for x in (author, str(paper.get("year") or "")) if x)
        labels.append(f"{i}. {paper['title']}" + (f"（{details}）" if details else ""))
    if saving:
        prompt = "你指的是哪篇？选中后收藏到当前数据表。"
        choices = labels
    else:
        prompt = f"找到 {len(matches)} 个题录候选。可以查看来源，也可以说“收藏第几篇”。"
        choices = [f"查看第{i}篇" for i in range(1, len(matches) + 1)]
    reply = chat._ask({"papers": matches, "labels": labels, "saving": saving},
                      prompt + "\n" + "\n".join(labels), [*choices, "取消"], "title_choice")
    if not saving:
        reply["collection"] = [card(paper, [paper["url"]]) for paper in matches]
    return reply


def followup(chat, message, task, pending_id, request_id):
    plan = task["plan"]
    if pending_id != task["id"]:
        return chat._ask(plan, task["message"], task["choices"], task["stage"])
    if task["stage"] == "title_selected":
        if message in {"收藏这篇论文", "收藏这篇", "保存这篇"}:
            chat.pending = None
            return chat._collect_batch([plan["paper"]["url"]], request_id)
    else:
        match = _PICK.fullmatch(message.strip(" 。！"))
        if message in plan["labels"]:
            index = plan["labels"].index(message)
            save = plan["saving"]
        elif match:
            n = match["n"].replace("两", "二")
            index = int(n) - 1 if n.isdigit() else "一二三".index(n)
            action = match["action"]
            save = action in _SAVE or (plan["saving"] and action in {None, "选"})
        else:
            index = -1
        if 0 <= index < len(plan["papers"]):
            paper = plan["papers"][index]
            if save:
                chat.pending = None
                return chat._collect_batch([paper["url"]], request_id)
            chat.pending = None
            reply = chat._ask({"paper": paper}, f"已选中《{paper['title']}》。可以打开来源核对，或确认收藏到「{chat.table_name}」。",
                              ["收藏这篇论文", "取消"], "title_selected")
            reply["collection"] = [card(paper, [paper["url"]])]
            return reply
    return chat._ask(plan, task["message"], task["choices"], task["stage"])
