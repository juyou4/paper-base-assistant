"""为漏识别的口语检索请求提取来源和主题。该模块只生成只读计划。"""

import re
from http.client import HTTPException

import model_support


_HISTORY = (r"(?:(?:我(?:们)?(?:自己)?(?:之前|以前|上次|刚才|已经|已)?|之前|以前|上次|刚才|已经|曾经)"
            r"(?:收藏|收录|保存|收|存)(?:进来|下来|起来)?(?:过|的|了)|(?:收藏|收录|保存|存)(?:过的|下来的))")
_LOCAL = re.compile(_HISTORY + r"|收藏夹|阅读清单|论文清单|论文库|文献库|笔记库|库里|清单[里中内]|"
                    r"(?:这张|本|当前|飞书(?:的)?)(?:数据)?表格?|表(?:格)?[里中内]")
_PUBLIC = re.compile(r"Crossref|公开(?:的)?(?:论文)?(?:题录|文献库|书目)|网上|全网|互联网上?|"
                     r"公开文献|学术网站|外部(?:的)?(?:平台|网站|文献库|数据库)?", re.I)
_PAPERS = re.compile(r"论文|文献|文章|题录")
_FIND = re.compile(r"找|搜|查找|检索|查一查|查一下|翻(?:翻|一翻|一下)|挑|推荐|盘一盘|挑一挑")
_QUESTION = re.compile(r"有没有|有没(?:有)?|是否有|哪些|哪(?:些|几)?篇")
_COUNT = re.compile(r"(?:一共|总共|总计|共有)(?:有)?(?:几|多少)|有(?:几|多少)\s*(?:篇|条|行)|篇数|记录数|论文总数")
_TOPICAL = re.compile(r"关于|有关|相关|提(?:到|及)|谈(?:到|及)|讨论|围绕|涉及|方向|讲")
_ONE_PAPER = re.compile(r"(?:这|那|刚才那|前面那|上一篇|该|这几|那几)篇|这篇|原文|本文|作者的|"
                        r"(?:论文|文章|文献)(?:中|里|内)")
_EVIDENCE = re.compile(r"实验(?:数据|结果)|(?:原|架构|结构|消融)图|原表|公式|曲线|"
                       r"(?:第[\d一二三四五六七八九十]+[张个]?|这张|那张)[^，。；]{0,5}[图表]|[图表]\s*\d")
_EXPLICIT_ACTION = re.compile(r"写入|填入|回填|导入|保存|收藏|收录|存进|存入|存到|加入|收进|写进|"
                              r"记到|填到|删除|删掉|改成|重命名|移除|清空|授权|添加|新建|提取|抽取|下载")
_NO_SEARCH = re.compile(r"不要|不用|不必|不需要|不想|不打算|没(?:有)?(?:让|叫|要求)|别(?:再|在|从|帮|去|到|查|找|搜|翻|挑)|先不|暂不|"
                        r"并非要|不是要|取消|如果|假如|要是|假设|要不要")
_FILTER = re.compile(r"排序|排名|最新|最近|最早|近\s*[几一二三四五十\d]+\s*年|(?:19|20)\d{2}\s*(?=年|以[前后]|之[前后])|"
                     r"未读|已读|按(?:时间|年份|作者)|前[一二三四五十\d]+[篇条]|被引|引用量|影响因子")
_PRIVATE = re.compile(r"我(?:们)?的|内部|保密|私人|私密|私有|客户|未公开|项目代号|"
                      r"(?:我(?:们)?(?:组|实验室)|组会|笔记|草稿)(?:上|里|中|的|提[到及]|记录)")
_REFERENCE = re.compile(r"https?://|10\.\d{4,9}/|arxiv[:：]|\b\d{4}\.\d{4,5}\b", re.I)
_OTHER_TASK = re.compile(r"^(?:帮我|请|麻烦|先|给我|我想)*(?:创建|新建|新增|删除|重命名|记录|补写|提取|抽取|下载)|"
                         r"(?:的|里的|中的)(?:架构图|原图|原表|公式|实验数据|实验结果)|查看权限|表头|列名|字段类型")
_TERM = re.compile(r"[\w][\w\s./+#&:’'()\-]{0,119}")
_NO_TERM = re.compile(r"^(?:(?:相关|关于|方向|有没?有|哪一?篇|几篇|一些|几条|论文|文献|文章|表格?|"
                      r"先前|当前|上次|之前|刚才|哪[几些]?|这[些篇个]|那[些篇个]|想|推荐|看看|看一下|"
                      r"帮(?:我|忙)|麻烦|一下|数据|字段|记录|内容|东西|笔记|什么|工作|的|点|几|吗|呢|嘛)\s*)+$")
_CHATTER = re.compile(r"我想|帮我|(?:几|哪)篇|有没|是否|[里中内]面|的(?:那|这|文章|论文|文献)|"
                      r"完以[后前]|(?:先|再)(?:把|去|看)|然后|顺便|并且|[，,。；;？！?]")
_TOPIC_END = r"(?:(?:方面|方向|相关|有关)?的?(?:论文|文献|文章|研究)|(?:方面|方向|相关|有关)的?|的)?"
_PATTERNS = [
    re.compile(r"(?:关于|有关(?!于)|讲(?:的?是)?|讨论(?:过)?|提(?:到|及)(?:了|过)?|谈(?:到|及)(?:了|过)?|围绕|涉及(?:到)?)"
               r"\s*(.+?)\s*" + _TOPIC_END + "$"),
    re.compile(r"(?:跟|与|和)\s*(.+?)\s*(?:(?:相关|有关)(?:的)?|方向的)(?:论文|文章|文献)?$"),
    re.compile(r"(?:翻一翻|翻翻|翻一下|挑(?:一挑|选|出)?|搜一搜|找找|查找|搜(?:索)?|找|有没有|是否有|有哪些)"
               r"\s*(?:(?:几|一|两)篇|一些)?\s*(.+?)\s*(?:相关的?|方向的?|方面的?|的(?:论文|文献|文章))"
               r"(?:论文|文献|文章)?$"),
]
_PROMPT = (
    "判断用户要按什么关键词查找论文。只输出 JSON，键为 scope、term、scope_phrase。"
    "scope 只能为 local（已收藏/当前表）、public（明确去公开题录）、ask（未指明或两个范围都有）。"
    "term 必须逐字复制原话中要搜的单个主题；没有主题则填空串。scope_phrase 为原话中的来源短语，"
    "ask 时填空串。不翻译、不增补，不选择字段、不提供工具调用。"
)


def classify(message, table_name):
    """明确的口语检索才占用这个回合；其他任务交回已有流程。"""
    if (not isinstance(message, str) or not 0 < len(message) <= 600
            or _REFERENCE.search(message) or _OTHER_TASK.search(message)):
        return None
    text = message.strip().rstrip("。！？?! ").rstrip("吗呢嘛").strip()
    local = {m[0] for m in _LOCAL.finditer(text)}
    if table_name and table_name in text:
        local.add(table_name)
    public = {m[0] for m in _PUBLIC.finditer(text)}
    if not (local or public or _PAPERS.search(text)):
        return None
    if not (_FIND.search(text) or (_QUESTION.search(text) and
            (_TOPICAL.search(text) or (public and _PAPERS.search(text))))
            or ((local or public) and _TOPICAL.search(text) and re.search(r"看|查|翻", text))):
        return None
    if _ONE_PAPER.search(text) and (not local and not public or _EVIDENCE.search(text)):
        return None
    if not _PAPERS.search(text) and not _TOPICAL.search(text) and not re.search(r"翻|找|搜|挑", text):
        return None
    if local and not public and _COUNT.search(text):
        if _TOPICAL.search(text) or re.search(r"(?:几|多少)篇\s*[^，。；]{1,40}?的(?:论文|文章|文献)", text):
            return "unsupported", ""
        return None  # 由表统计核对用户要的是篇数、记录数还是行数。

    # “上次收藏的”是来源；“找到后收藏”是又一个动作。先剔除已定位来源再区分两者。
    source_words = sorted(local | public, key=len, reverse=True)
    rest = text
    for word in source_words:
        rest = rest.replace(word, " ")
    if (_NO_SEARCH.search(text) or _FILTER.search(text) or _EXPLICIT_ACTION.search(rest)
            or re.search(r"然后|接着|顺便|同时|并且", rest)):
        return "unsupported", ""
    if (not local and _PRIVATE.search(text)) or (public and _PRIVATE.search(rest)):
        return "unsupported", ""
    scope = "ask" if bool(local) == bool(public) else ("local" if local else "public")

    def grounded(term):
        if (not isinstance(term, str) or term != term.strip() or term not in rest or not _TERM.fullmatch(term)
                or _NO_TERM.fullmatch(term) or _CHATTER.search(term) or _LOCAL.search(term)
                or _PUBLIC.search(term) or _EXPLICIT_ACTION.search(term)):
            return False
        # 一个缩写须来自独立的原词，不能把 GraphRAG 裁成 RAG。
        left = r"(?<![a-zA-Z0-9_])" if term[0].isascii() else ""
        right = r"(?![a-zA-Z0-9_])" if term[-1].isascii() else ""
        return bool(re.search(left + re.escape(term) + right, message))

    topic_text = re.sub(r"(?<=文)有(?:哪几篇|哪些)$|(?<=章)有(?:哪几篇|哪些)$", "", rest)
    for pattern in _PATTERNS:
        match = pattern.search(topic_text)
        if match:
            term = match[1].strip(" \t《》「」“”\"")
            if grounded(term):
                return scope, term

    if model_support.enabled():
        try:
            result = model_support.request_json(_PROMPT, {"message": message, "table_name": table_name},
                                                step="识别论文检索范围和主题")
        except (ValueError, OSError, HTTPException):
            result = None
        if isinstance(result, dict) and set(result) == {"scope", "term", "scope_phrase"}:
            phrase = result["scope_phrase"]
            anchored = (phrase == "" if scope == "ask" else isinstance(phrase, str) and 0 < len(phrase) <= 50
                        and phrase in text and any(cue in phrase for cue in (local if scope == "local" else public)))
            if result["scope"] == scope and anchored and grounded(result["term"]):
                return scope, result["term"]
    return "incomplete", ""
