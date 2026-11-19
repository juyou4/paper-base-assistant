"""从单轮对话生成建多维表格文件、建数据表或切换数据表的操作草案。"""

import re
from http.client import HTTPException

import model_support
from table_store import _INITIAL_COLUMNS, _PAPER_LINKS, _PAPER_TITLES, TableError, validate_table_definition


_POLITE = r"(?:(?:麻烦(?:你)?|劳烦|请(?:你)?|帮(?:我|忙)|给我|我想(?:要)?|我要|先)\s*)*"
_SCOPE = r"(?:在(?:当前|现在|已连接|这[个份张]|本)(?:的)?(?:飞书)?(?:多维表格|Base)(?:文件)?(?:里(?:面)?|下(?:面)?|中|内)?\s*)?"
_MAKE = r"(?:新建|创建|新增|添加|建立|新开|开设|建|加|开|弄|做)"
_COUNT = r"(?:一\s*)?(?:个|份|张)?\s*"
_BASE = r"(?:飞书(?:的)?)?(?:多维表格(?:文件|文档)?|Base(?:文件)?)"
_TABLE = r"(?:数据表|子表|工作表|新表|表格?)"
_KIND = rf"(?P<kind>{_BASE}|{_TABLE})"
_LABEL = r"(?:就叫|名字(?:叫(?:做)?|为)|名称(?:叫|为)|名(?:称)?为|叫(?:做)?|命名为|取名(?:为)?|表名(?:为|叫)?|文件名(?:为|叫)?)"
_QUOTED = r'(?:「[^「」]+」|“[^“”]+”|《[^《》]+》|"[^"]+"|『[^『』]+』)'
_NAME = r"[\w\u3400-\u9fff][\w\u3400-\u9fff .·+\-]*"
_MAKE_PREFIX = _POLITE + _SCOPE + _MAKE + r"\s*" + _COUNT + r"(?:全新(?:的)?|独立(?:的)?|新(?:的)?)?"
_DIRECT = re.compile(_MAKE_PREFIX + _KIND + r"(?P<tail>.*)", re.I | re.S)
_NAMED = re.compile(_MAKE_PREFIX + rf"(?:{_LABEL}\s*)?(?P<name>{_QUOTED}|{_NAME}?)\s*(?:的(?:新)?)?"
                    + _KIND + r"(?P<tail>\s*(?:[,，;；\n].*)?)", re.I | re.S)
_PURPOSE_BEFORE_KIND = re.compile(_MAKE_PREFIX + r"(?P<purpose>(?:用来|用于).+?)的" + _KIND
                                  + rf"(?:\s*[,，]?\s*{_LABEL}\s*(?P<name>{_QUOTED}|{_NAME}))?", re.I | re.S)
_LIST = re.compile(_POLITE + r"(?:(?:看(?:看|下|一下)?|查看|查询|列出|列一下|显示)\s*)?"
                   + r"(?:(?:当前|现在|现有|这[份个张]|已连接)(?:的)?\s*)?"
                   + rf"(?:(?:飞书)?(?:多维表格|Base)(?:文件)?(?:里(?:面)?|下(?:面)?|中|内)?(?:的)?\s*)?"
                   + r"(?:(?:都|总共)?有(?:哪些|哪几张|几张|什么)|(?:全部|所有|已有))\s*(?:数据表|子表|工作表|表格?)", re.I)
_LIST_SHORT = re.compile(_POLITE + r"(?:列出(?:所有)?(?:子表|数据表)|(?:查看|显示|列出)(?:子表|数据表)列表|"
                         r"(?:看看|查查|看一下|看下)?(?:这个|这份|当前)(?:文件|库)(?:里(?:面)?|中|内)?"
                         r"(?:都|一共|总共)?有(?:哪几张|哪些|几张)(?:子表|数据表|表))")
_SWITCH = re.compile(_POLITE + r"(?:切换|切|换)\s*(?:到|至|回|成|一下)?\s*"
                     + rf"(?:(?P<kind>{_BASE}|{_TABLE})\s*)?(?P<tail>.*)", re.I | re.S)
_OPEN_TABLE = re.compile(_POLITE + rf"(?:打开|切到|切回|切换到|换到|换回)\s*(?P<name>{_QUOTED}|{_NAME}?)"
                         + r"\s*(?:这张|那张)(?:子表|数据表|工作表|表格?)", re.I)
_NEGATIVE = re.compile(r"(?:不(?:要|用|必|需|能|想)|别|勿|暂不|先不|取消|如果|假设|怎么|如何|是否|能不能|可否|可不可以)")
_QUESTION_END = re.compile(r"(?:[?？]|吗|么|嘛|行不行|好不好|可不可以|可以不|行不|要不要)\s*[。！!]?$")
_EXTRA = re.compile(r"然后|接着|顺便|同时|再把|并把|并将|写入|填入|回填|导入|迁移|清空|覆盖|删除|删掉|授权|分享给|共享给|发送给|https?://|换个账号|其他租户", re.I)
_FIELD_HEAD = re.compile(r"^(?:(?:请)?(?:自动)?(?:设置|创建|新建|新增|添加|包含|包括|设|带有|带|含有|含)\s*(?:(?:以下|这些|如下|这些)?\s*)?)?"
                        r"(?:字段|列名|列|表头)(?:列表)?\s*(?:包括|包含|含有?|分别是|为|是|有)?\s*[:：]?\s*", re.I)
_FIRST_TABLE = re.compile(r"^(?:(?:首张|第一张|首个|第一个|第一份|初始|默认)(?:数据表|子表|表格?|工作表)|(?:数据表|子表))"
                         + rf"\s*(?:{_LABEL}|[:：]|(?=[「“《『\"]))\s*(.*)$", re.I)
_TEMPLATE = re.compile(r"(?:(?:用|使用|按|采用)\s*(?:默认|论文|论文阅读)(?:的)?(?:模板|字段)(?:模板)?|"
                       r"(?:字段|表头)(?:由)?(?:让)?(?:你|助手|Agent)(?:来)?(?:设计|规划|决定|定)|"
                       r"(?:并)?(?:自动|自行)(?:设计|规划|创建)(?:合适的?)?(?:字段|表头)|"
                       r"(?:和|并)(?:新建|创建|设计)?(?:对应的?)?(?:字段|表头))", re.I)
_PURPOSE = r"(?:用途(?:是|为)?|用于|用来|目的是)\s*[:：]?\s*(?P<purpose>.+)"
_DESIGN_FIRST = re.compile(rf"{_TEMPLATE.pattern}\s*[,，;；。\n ]*{_PURPOSE}", re.S | re.I)
_PURPOSE_FIRST = re.compile(rf"{_PURPOSE}[，,;；。\n]\s*{_TEMPLATE.pattern}", re.S | re.I)
_PURPOSE_ONLY = re.compile(_PURPOSE, re.S)
_TYPE = {"文本": "text", "文字": "text", "text": "text", "超链接": "url", "链接": "url", "网址": "url", "url": "url"}
_PAPER_FIELD = (r"(?:论文)?标题|(?:论文|代码|原文)?链接|中文摘要|方法概述|阅读笔记|研究笔记|关键数据|出处|摘要|方法|"
                r"模型(?:名称)?|数据集|评价指标|指标(?:值)?|得分|准确率|年份|时间|复现状态")
_FIELD_SPLIT = re.compile(rf"({_QUOTED})|[、,，;；\n]|以及|(?<=[」”》）)])和|和(?=[「“《]|(?:{_PAPER_FIELD})(?:$|\s|[（(,，;；、]))")
_SEPARATOR = re.compile(rf"({_QUOTED})|[,，;；\n]")


def _clarify(message, needs_name=None):
    result = {"op": "clarify", "message": message}
    if needs_name:
        result["needs_name"] = needs_name
    return result


def _name(value):
    value = value.strip()
    if not value or not value.isprintable():
        return ""
    if re.fullmatch(_QUOTED, value):
        return value[1:-1].strip()
    if not re.fullmatch(_NAME, value) or _EXTRA.search(value) or _NEGATIVE.search(value):
        return ""
    return value


def _default_fields():
    return [{"name": name, "type": kind} for _, name, kind in _INITIAL_COLUMNS]


def _parts(value, delimiters, maxsplit=None):
    # 引号内的逗号属于用户给出的名称。
    segments, start = [], 0
    for match in delimiters.finditer(value):
        if not match[1]:
            segments.append(value[start:match.start()].strip())
            start = match.end()
            if maxsplit and len(segments) == maxsplit:
                break
    segments.append(value[start:].strip())
    return segments


def _fields(value):
    result, seen = [], set()
    for segment in _parts(value, _FIELD_SPLIT):
        label, field_type = segment, None
        typed = re.fullmatch(r"(.+?)\s*[（(]([^()（）]+)[）)]", segment)
        if not typed:
            typed = re.fullmatch(r"(.+?)\s*(?:[:：=]|的?类型(?:是|为))\s*(.+)", segment)
        if not typed:
            typed = re.fullmatch(r"(.+?)\s+(文本|文字|text|url|超链接|链接|网址|数字|日期|单选|多选|附件|公式)", segment, re.I)
        if typed:
            label, given_type = typed.groups()
            field_type = _TYPE.get(given_type.strip().casefold())
            if not field_type:
                raise ValueError(f"字段「{label.strip()}」使用了「{given_type.strip()}」类型。当前建表支持文本和超链接，请调整类型。")
        elif re.search(r"(?:数字|日期|单选|多选|附件|公式|复选框|人员|货币|数值)(?:型|类型|列|字段)", segment):
            raise ValueError("当前建表支持文本和超链接，请明确每一列的名称和类型。")
        if re.search(r"[\u3400-\u9fff]和[\u3400-\u9fff]", label) and not re.fullmatch(_QUOTED, label):
            raise ValueError("请用顿号分隔不同字段；字段名含“和”时可以用引号括起来。")
        name = _name(label)
        if not name or len(name) > 100 or name.casefold() in seen:
            raise ValueError("请提供完整且互不重复的字段名，例如“字段：论文标题（文本）、论文链接（超链接）”。")
        seen.add(name.casefold())
        is_link = name in _PAPER_LINKS or name.endswith(("链接", "网址"))
        result.append({"name": name, "type": field_type or ("url" if is_link else "text")})
    return result


def _named_value(value):
    return _name(re.sub(rf"^(?:{_LABEL}\s*)?[:：]?\s*", "", value).strip())


def supply_name(request, answer):
    """把追问中补充的名字与前一轮的用途或列定义合并。"""
    value = re.sub(r"[。！!]$", "", answer.strip())
    if _QUESTION_END.search(value):
        return None
    if value.endswith("吧") and (re.match(_LABEL, value) or value[:-1].endswith(("」", "”", "》", '"', "』"))):
        value = value[:-1]
    if not re.match(_LABEL + "|" + _QUOTED, value) and re.match(
            r"确认|取消|先等等|帮我|我要|请问|收藏|提取|搜索|删除|查看|写入|回填", value):
        return None
    name = _named_value(value)
    if not name or len(name) > 100 or "「" in name or "」" in name:
        return None
    prefix = {"create_table": "新建数据表", "create_base": "新建多维表格", "switch_table": "切换到数据表"}
    details = request.get("details", "")
    return parse(f"{prefix[request['needs_name']]}「{name}」" + (f"，{details}" if details else ""))


_COLUMN = r"(?:字段|列)"
_ADD = re.compile(rf"(?:增加|新增|加上|添加|补上|加|补)(?:一?[个项]|一)?\s*(?P<kind>文本|超链接)?"
                  + rf"{_COLUMN}\s*(?:叫(?:做)?|名为|：|:)?\s*(?P<fields>.+)")
_ADD_LAST = re.compile(rf"(?:增加|新增|加上|添加|补上|加|补)(?:一?[个项])?\s*(?:{_LABEL}\s*)?"
                       + rf"(?P<fields>{_QUOTED}|{_NAME}?)\s*(?:这(?:一|个)?|的)?{_COLUMN}")
_DROP = re.compile(rf"(?:去掉|删掉|删除|移除|拿掉|取消)\s*(?:{_COLUMN}\s*)?(.+)")
_OMIT = re.compile(rf"(?:先)?(?:不要|不用)(?P<name>{_QUOTED}|{_NAME}?)(?:这(?:一|个)?)?{_COLUMN}(?:了)?")
_RENAME = re.compile(r"(?:把|将)?\s*(.+?)\s*(?:重命名为|改名(?:为|叫)?|更名为|改成|改为|换成)\s*(.+)")
_REPLACE_FIELDS = re.compile(r"(?:字段|列|表头)(?:全部)?(?:替换为|换成|改为|改成|只要|只留)\s*[:：]?\s*(.+)")
_KEEP = re.compile(r"(?:只保留|只留下|只留)(?:以下(?:这些)?(?:字段|列))?\s*[:：]?\s*(.+?)(?:这(?:几个字段|几列))?")
_REVISION_REF = re.compile(r"^(?:字段|列)\s*|\s*(?:(?:这(?:一|个)?)?(?:列|字段))$")


def revise(plan, message):
    """只修改待建表的草案，每次从原字段生成副本。"""
    text = re.sub(r"^(?:先别建(?:表)?[，,]\s*)?" + _POLITE + r"(?:再|先)?\s*", "", message.strip())
    text = text.rstrip("。！!").strip()
    plain = re.sub(_QUOTED, " ", text)
    omit = _OMIT.fullmatch(text)
    if _QUESTION_END.search(plain) or re.search(r"如果|假设|先别|请问|然后|接着|顺便|并把|同时|"
                 r"(?:当前|已有|现有|原来|已经有)的?(?:数据)?表|原表|这张表里|https?://", plain):
        return None
    if "不要" in plain and not omit:
        return None
    fields = [f.copy() for f in plan["fields"]]
    change = dict(plan, fields=fields)
    replace = _REPLACE_FIELDS.fullmatch(text)
    # “加一列……”与“加一个字段……”都只会添加到草案。
    addition = _ADD.fullmatch(text) or _ADD_LAST.fullmatch(text)
    drop = _DROP.fullmatch(text) or omit
    rename = _RENAME.fullmatch(text)
    keep = _KEEP.fullmatch(text)
    if replace:
        change["fields"] = _fields(replace[1])
    elif keep:
        wanted = {_name(word) for word in _parts(keep[1], _FIELD_SPLIT)}
        if not wanted <= {field["name"] for field in fields}:
            raise ValueError("请从当前预览中选择要保留的列，按列名用顿号分开。")
        change["fields"] = [field for field in fields if field["name"] in wanted]
    elif addition:
        if addition.re is _ADD_LAST and addition["fields"] in {"文本", "超链接", "数字", "日期"}:
            raise ValueError("新增字段叫什么？可以说“加一列代码仓库（超链接）”。")
        added = _fields(addition["fields"])
        explicit_type = addition.groupdict().get("kind")
        if explicit_type:
            if any(marker in addition["fields"] for marker in ("：", ":", "(", "（")):
                raise ValueError("请只在一处指定新增列的类型。")
            for field in added:
                field["type"] = _TYPE[explicit_type]
        fields.extend(added)
    elif drop or rename:
        ref = (drop or rename)[1].strip()
        if rename and ref in {"表名", "新表", "数据表名", "子表名", "首张表名", "文件名", "多维表格文件名"}:
            key = "name"
            if ref in {"文件名", "多维表格文件名"}:
                if plan["op"] != "create_base":
                    raise ValueError("当前方案只创建一张子表。请指定新数据表的表名。")
            elif plan["op"] == "create_base":
                key = "table_name"
            name = _name(rename[2])
            if not name or len(name) > 100:
                raise ValueError("请提供 1 至 100 字的名称。")
            change[key] = name
        else:
            type_change = bool(rename and ref.endswith("类型"))
            if type_change:
                ref = re.sub(r"的?类型$", "", ref)
            if not any(f["name"] == _name(ref) for f in fields):
                ref = _REVISION_REF.sub("", ref)
            ref = _name(ref)
            field = next((f for f in fields if f["name"] == ref), None)
            if not field:
                raise ValueError("在待建表的预览里找不到这个字段，请使用预览中的列名。")
            if drop:
                fields.remove(field)
            else:
                to = rename[2].strip()
                if type_change or (to in set(_TYPE) | {"数字", "日期", "单选", "附件", "公式"} and not re.search(r"改名|更名|重命名", text)):
                    if to not in _TYPE:
                        raise ValueError("建表支持文本和超链接两种类型，请重新指定。")
                    field["type"] = _TYPE[to]
                else:
                    name = _name(to)
                    if not name or len(name) > 100:
                        raise ValueError("请明确给出新列名，或指定文本、超链接类型。")
                    field["name"] = name
    else:
        return None
    if len({f["name"].casefold() for f in change["fields"]}) != len(change["fields"]):
        raise ValueError("有重复的字段名，请使用不同的名称。")
    try:
        validate_table_definition(change.get("table_name", change["name"]), change["fields"])
    except TableError as error:
        raise ValueError(str(error)) from None
    if change["fields"] != plan["fields"]:
        change["field_source"] = "按你的修改"
    return change


def _design_fields(title, purpose):
    """显式列举的维度直接解析，自由用途仅让推理端提议字段定义。"""
    dimensions = re.fullmatch(r"(?:记录|收集|保存|整理|跟踪|追踪)\s*(?:每篇(?:论文|文献)的)?(.+)", purpose)
    if dimensions:
        names = _parts(dimensions[1], _FIELD_SPLIT)
        if (len(names) >= 2 and all(re.fullmatch(r"[\w\u3400-\u9fff ·-]{1,20}", word)
                                    and not re.search(r"和|是否|应该|需要|如何|怎样|以便|上[的有]|分别|哪[些个]", word)
                                    for word in names)):
            fields = _default_fields()[:2]
            seen = {name.casefold() for name in _PAPER_LINKS | _PAPER_TITLES}
            for name in names:
                if name.casefold() not in seen:
                    fields.append({"name": name, "type": "url" if name.endswith(("链接", "网址")) else "text"})
                    seen.add(name.casefold())
            return fields, "按用途中的明确维度规划"
    hint = "请补充想记录的几项信息，例如“字段你来设计，用于记录模型、数据集和评价指标”，再一起预览。"
    if not model_support.enabled():
        raise ValueError(hint)
    try:
        result = model_support.request_json(
            "为本次新建的论文阅读数据表设计字段。表名、用途作为输入数据。"
            "只输出一个 JSON 对象，唯一键 fields，列出完整的字段方案。"
            '每项仅含 name、type，类型只用 text 或 url。首列使用文本，'
            "将论文标题、论文链接以及与该用途相关的最少必要字段列入方案。"
            "无足够信息返回空数组。不要输出操作、工具、权限或记录数据。",
            {"table_name": title, "purpose": purpose}, step="根据用途规划字段",
        )
    except (OSError, ValueError, HTTPException):
        raise ValueError(hint) from None
    fields = result.get("fields") if isinstance(result, dict) and set(result) == {"fields"} else None
    if not isinstance(fields, list) or not 1 <= len(fields) <= 32:
        raise ValueError(hint)
    seen = set()
    for field in fields:
        if not isinstance(field, dict) or set(field) != {"name", "type"}:
            raise ValueError(hint)
        name = field["name"]
        if (not isinstance(name, str) or not name or len(name) > 100 or name != _name(name)
                or name.casefold() in seen or field["type"] not in ("text", "url")):
            raise ValueError(hint)
        seen.add(name.casefold())
    if (fields[0]["type"] != "text"
            or sum(f["name"] in _PAPER_TITLES and f["type"] == "text" for f in fields) != 1
            or sum(f["name"] in _PAPER_LINKS and f["type"] == "url" for f in fields) != 1):
        raise ValueError(hint)
    return fields, "模型按用途提议"


def parse(message):
    """只返回无副作用的计划。None 表示交由原有论文、行、字段和权限流程处理。"""
    text = message.strip().rstrip(" 。！？!?？").strip()
    if _LIST.fullmatch(text) or _LIST_SHORT.fullmatch(text):
        return {"op": "list_tables"}
    plain = re.sub(_QUOTED, " ", text)
    # “用于判断是否值得复现”里的“是否”描述用途，其他取消和复合操作仍然需要澄清。
    intent = re.sub(r"((?:用于|用来|目的是)[^，。；;]*?)是否", r"\1", plain)
    forbidden = _EXTRA.search(plain) or _NEGATIVE.search(intent) or _QUESTION_END.search(re.sub(_QUOTED, " ", message.strip()))
    if re.match(_POLITE + _NEGATIVE.pattern + r".{0,10}" + _MAKE + _COUNT + rf"(?:{_BASE}|{_TABLE})", plain, re.I):
        return _clarify("如果想建表，请单独指定新表的名字。")
    opened = _OPEN_TABLE.fullmatch(text)
    if opened and not forbidden and _name(opened["name"]):
        return {"op": "switch_table", "name": _name(opened["name"])}
    switch = _SWITCH.fullmatch(text)
    if switch and (switch["kind"] or re.fullmatch(_QUOTED, switch["tail"].strip())
                   or re.match(_POLITE + r"(?:切换|切|换)(?:到|至|回)", text)):
        if (not switch["kind"] and not re.fullmatch(_QUOTED, switch["tail"].strip())
                and re.search(r"模型|账号|模式|主题|视图|语言|^(?:gpt-|claude-|deepseek-)", switch["tail"], re.I)):
            return None
        if forbidden:
            return _clarify("要切换时请直接指定一张已存在的数据表。")
        if switch["kind"] and re.fullmatch(_BASE, switch["kind"], re.I):
            return _clarify("可切换当前多维表格中的数据表。请指定子表名，或说“列出所有数据表”。")
        name = _named_value(switch["tail"])
        if not name:
            return _clarify("要切换到哪张数据表？请告诉我已有的数据表名。",
                            "switch_table" if not switch["tail"].strip() else None)
        return {"op": "switch_table", "name": name}
    purpose_first = _PURPOSE_BEFORE_KIND.fullmatch(text)
    if purpose_first and not forbidden:
        is_base = re.fullmatch(_BASE, purpose_first["kind"], re.I)
        if not purpose_first["name"]:
            return {**_clarify("这个多维表格文件叫什么？" if is_base else "这张数据表叫什么？",
                               "create_base" if is_base else "create_table"), "details": purpose_first["purpose"]}
        kind = "新建多维表格" if is_base else "新建数据表"
        name = purpose_first["name"]
        return parse(f"{kind}{name}，{purpose_first['purpose']}")
    match = _DIRECT.fullmatch(text) or _NAMED.fullmatch(text)
    if not match:
        return None
    named = match.groupdict().get("name") or ""
    # “新建文本列 XX表”“把论文加到表”“新增表格记录”仍走现有字段或论文操作。
    if (re.match(r"\s*(?:的)?(?:字段|列名|表头|头|这一列|记录|单元格)", match["tail"])
            or (named and not re.fullmatch(_QUOTED, named) and re.search(
                r"^(?:字段|列|超?链接列|文本列)|(?:到|进|入)(?:当前|本|这张|该)?(?:数据)?$", named))):
        return None
    if named in {"飞书", "电子", "Excel"} and match["kind"] in ("表格", "表"):
        return _clarify("要单独创建文件可以说“新建多维表格”；要在当前文件内加一张表可以说“新建数据表”。")
    if forbidden:
        return _clarify("请单独说明要创建的表名及字段。确认创建并切换后，就能继续导入论文。")
    is_base = bool(re.fullmatch(_BASE, match["kind"], re.I))
    op = "create_base" if is_base else "create_table"
    tail = match["tail"].strip()
    name = _named_value(named)
    if named and not re.fullmatch(_QUOTED, named) and re.search(r"用于|用来|存放|收集|(?:两|二|三|[2-9])(?:个|份|张)$", named):
        return _clarify("请指定一个明确的表名，例如“新建数据表「论文精读」”。")
    if named and match["kind"] in ("表", "表格") and (named + match["kind"]) in text and not re.fullmatch(_QUOTED, named):
        name += match["kind"]
    tail = tail.strip(" ,，;；\n")
    # 消费表名和可选的首张子表名；字段列表使用余下的整段以保留各列。
    if not name and tail:
        first = re.match(rf"^(?:{_LABEL}\s*)?[:：]?\s*({_QUOTED}|[^,，;；\n]+)", tail)
        if (first and not _FIELD_HEAD.match(first[1]) and not _TEMPLATE.fullmatch(first[1])
                and not _PURPOSE_ONLY.fullmatch(first[1]) and not _FIRST_TABLE.fullmatch(first[1])):
            name = _name(first[1])
            tail = tail[first.end():].strip(" ,，;；\n")
            if not name:
                return _clarify("请给出明确表名，例如“新建数据表「论文精读」”。")
    if not name:
        request = _clarify("新多维表格文件叫什么？" if is_base else "新数据表叫什么？", op)
        if tail:
            request["details"] = tail
        return request
    if len(name) > 100:
        return _clarify("请将表名控制在 100 个字符以内。")
    table_name = "论文清单"
    prefix = _parts(tail, _SEPARATOR, maxsplit=1)
    first_table = _FIRST_TABLE.fullmatch(prefix[0])
    if first_table:
        table_name = _name(first_table[1])
        if not is_base or not table_name or len(table_name) > 100:
            return _clarify("新建多维表格文件时可指定首张数据表，例如“首张数据表叫论文清单”。")
        tail = prefix[1].strip() if len(prefix) == 2 else ""
    fields = _default_fields()
    field_source = None
    design = _DESIGN_FIRST.fullmatch(tail) or _PURPOSE_FIRST.fullmatch(tail) or _PURPOSE_ONLY.fullmatch(tail)
    if design:
        try:
            fields, field_source = _design_fields(table_name if is_base and first_table else name, design["purpose"])
        except ValueError as error:
            return _clarify(str(error))
    elif tail and not _TEMPLATE.fullmatch(tail):
        head = _FIELD_HEAD.match(tail)
        if not head or not tail[head.end():].strip():
            return _clarify("请用“字段：论文标题、论文链接、阅读笔记”列出字段，或写“使用论文模板”。")
        try:
            fields = _fields(tail[head.end():])
        except ValueError as error:
            return _clarify(str(error))
    try:
        validate_table_definition(table_name if is_base else name, fields)
    except TableError as error:
        return _clarify(str(error))
    plan = {"op": op, "name": name, "fields": fields}
    if field_source:
        plan["field_source"] = field_source
    if is_base:
        plan["table_name"] = table_name
    return plan
