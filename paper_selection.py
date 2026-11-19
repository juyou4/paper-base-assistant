"""在当前展示的论文候选内解析序号，区分未指定和无法确定的范围。"""

import re


_NUMBER = r"[零一二两三四五六七八九十\d]+"
_ABSOLUTE = rf"第\s*{_NUMBER}(?:\s*篇)?(?:\s*(?:[、,，和与及]|到|至|[-~～])\s*(?:第\s*)?{_NUMBER}(?:\s*篇)?)*\s*篇?"
_RELATIVE = rf"(?:最前|最后|前|后|倒数第)\s*{_NUMBER}\s*篇"
_REFERENCE = re.compile(rf"(?P<absolute>{_ABSOLUTE})|(?P<relative>{_RELATIVE})")
_NEGATIVE = r"(?:不(?:要|用|想|需要|能|必)?|无需|别|勿)(?:再|先)?"
_PAPER_ACTION = r"(?:保留|提取|选择|选|看|留|收藏|保存|收录|处理|算|加)"
_OMIT = rf"排除|剔除|略过|忽略|跳过|去掉|舍弃|不包括|不含|{_NEGATIVE}{_PAPER_ACTION}|不要|不用|不需要"
_EXCLUDE_BEFORE = re.compile(rf"(?:除了?|{_OMIT})\s*$")
_EXCLUDE_AFTER = re.compile(rf"^\s*(?:也|都|先|暂时|暂|一律)*(?:[以之]外|除外|{_OMIT})"
                           r"(?:了|吧)?(?=$|[\s，,。；;！!]|的|都|全|只|仅|其[余他]|剩[下余])")
_EVIDENCE_OBJECT = re.compile(r"\s*的?\s*(?:图|实验表|表格|公式|指标|数据|结果|方法|摘要|准确率)")
_UNRESOLVED = re.compile(rf"(?:倒数(?:第)?|第|前|后|最后|最前)\s*{_NUMBER}|第[几多少]|某一篇")
_OTHER_UNIT = re.compile(r"\s*(?:个|项|条|张|章|节|页|幅|列|行|篇以[上内下]|篇左右)")
_UNFINISHED_TAIL = re.compile(r"\s*(?:[之以][前后]|[之以]内|以上|以下|左右|"
                              r"(?:至|到|[-~～]|和|与|以及|及|、)\s*(?:第\s*)?(?:[。！？!?；;，,]|$))")
_CLAUSE_BOUNDARY = re.compile(r"\s*(?:[,，;；、]|以及|然后|而|和|与)\s*(?=(?:第|前|后|最后|倒数第)\s*" + _NUMBER + ")")


def separate_paper_clauses(text):
    return _CLAUSE_BOUNDARY.split(text)


def without_positions(text):
    return _REFERENCE.sub(lambda match: " " if "篇" in match[0] else match[0], text)


def _number(value):
    if value.isdecimal():
        return int(value)
    return {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8}.get(value, 0)


def paper_positions(text, count, *, exclude=False):
    """返回从零起算的下标。None 表示未指定论文范围，空列表要求重新选择。"""
    matches = [match for match in _REFERENCE.finditer(text) if "篇" in match[0]]
    remainder = without_positions(text)
    # “第1篇和第2项”可被连续序号匹配到“第2”，但“项”不是论文的量词。
    if _UNRESOLVED.search(remainder) or any(_OTHER_UNIT.match(text[match.end():]) or
                                             _UNFINISHED_TAIL.match(text[match.end():]) for match in matches):
        return []
    if not matches:
        return None
    if re.search(r"或者|还是|或|也许|如果|除非|可能|是否|是不是|要不要", text):
        return []
    positions = []
    negative = False
    for match in matches:
        prefix = text[:match.start()]
        suffix = text[match.end():]
        negative_before = _EXCLUDE_BEFORE.search(prefix)
        if negative_before and _EVIDENCE_OBJECT.match(suffix):
            # “不看第一篇的图”没有排除整篇。由交互澄清该篇的证据范围。
            return []
        rejected = bool(negative_before or _EXCLUDE_AFTER.match(suffix))
        if rejected and (not exclude or len(matches) != 1):
            return []
        negative = negative or rejected
        part = match[0]
        numbers = [_number(v) for v in re.findall(_NUMBER, part)]
        if not all(1 <= n <= count for n in numbers):
            return []
        if match["relative"]:
            n = numbers[0]
            if part.startswith("倒数"):
                numbers = [count - n + 1]
            elif "前" in part:
                numbers = range(1, n + 1)
            else:
                numbers = range(count - n + 1, count + 1)
        elif re.search(r"到|至|[-~～]", part):
            if len(numbers) != 2 or numbers[0] > numbers[1]:
                return []
            numbers = range(numbers[0], numbers[1] + 1)
        positions.extend(n - 1 for n in numbers)
    if negative:
        return [i for i in range(count) if i not in positions]
    return list(dict.fromkeys(positions))


_COLLECTION_WORD = re.compile(
    r"请(?:你)?|麻烦(?:你)?|帮我|给我|我想(?:要)?|我要|一起|都|全部|全都|所有|分别|逐(?:个|篇)|批量|"
    r"这(?:些|几个|几篇|批)|这[一二两三四五六七八\d]+篇|这些|下面|以下|上述|上面|它们|这|"
    r"只|仅|就|先|要|选择|选|把|将|和|与|及|的|里(?:面)?|中|一次性|"
    r"收藏(?:起来)?|保存(?:下来)?|收录|收下|收着|收好|存(?:入|进|到|下|起来)?|归档|入库|入表|"
    r"加(?:入|到|进)|放(?:到|入|进)|记录|登记|到|在|"
    r"论文|文献|文章|来源|链接|网址|地址|标题|题名|本表|当前(?:的)?(?:数据表|表格?|清单)|"
    r"我的|阅读清单|飞书(?:多维)?表格?|一下|就好|即可|吧|[\s,，:：；;、。.!！]+")
_SHARED_BATCH = re.compile(r"^(?:(?:在)?(?:组会|会议|讨论|课|聊天)(?:上|中|时)|(?:老师|导师|同学|同事|朋友|我)(?:刚才|之前)?)"
                           r"(?:提到|提及|分享|推荐|转发|发来)的(?:这|那)?(?:几篇|几个链接)")


def collection_positions(text, count, table_name):
    """收藏动作会立即写记录。只有序号、目标表和收藏语义都清楚时才执行。"""
    positions = paper_positions(text, count)
    if positions == []:
        return []
    rest = without_positions(text)
    rest = _SHARED_BATCH.sub("这几篇", rest.strip())
    if table_name:
        rest = re.sub(r"(到|入|进|在)\s*[「“]?" + re.escape(table_name) + r"[」”]?", r"\1阅读清单", rest)
    for cardinal in re.findall(rf"({_NUMBER})\s*篇", rest):
        if _number(cardinal) != count:
            return []
    rest = re.sub(rf"(?:这)?{_NUMBER}\s*篇", "这几篇", rest)
    # “只保存链接”限定内容；没有序号的“只收藏一篇”限定论文，需要用户选定。
    if positions is None and re.search(r"只|仅", re.sub(
            r"(?:只|仅)(?:要)?(?:保存|收藏|存|收录)?(?:论文)?链接", "", rest)):
        return []
    if _COLLECTION_WORD.sub("", rest):
        return []
    return list(range(count)) if positions is None else positions
