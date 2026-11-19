"""在当前收藏表对照两篇论文已经存下的字段。"""

import re

from activity import step
from evidence_workflow import _FIELD_NAMES, _KIND_NAMES
from saved_paper_dialog import _identity, column_names, matching_rows


_LABELS = {
    "方法": ("方法", "方法概述", "研究方法", "技术路线"),
    "摘要": ("摘要", "论文摘要", "中文摘要"),
    "关键数据": ("关键数据", "实验数据", "实验结果", "关键指标", "实验指标", "指标"),
    "图表": ("图表", "关键图表", "图表摘录", "图题"),
    "公式": ("公式", "关键公式"),
    "资源链接": ("资源链接", "开源资源"),
    "代码链接": ("代码链接", "代码仓库", "开源代码"),
    "模型链接": ("模型链接", "模型权重"), "数据集链接": ("数据集链接", "数据链接"),
    "笔记": ("笔记",), "阅读笔记": ("阅读笔记",), "研究笔记": ("研究笔记",),
}
_TERMS = {term: label for label, terms in _LABELS.items() for term in terms}
_FIELD = "|".join(sorted(_TERMS, key=len, reverse=True))
_SEPARATOR = r"\s*(?:、|，|,|/|／|和|与|及|以及)\s*"
_FIELD_END = re.compile(rf"(?:的|在|[,，]\s*(?:只看|比较|对照)?)\s*((?:{_FIELD})(?:{_SEPARATOR}(?:{_FIELD}))*)"
                        r"(?:上)?(?:有(?:什么|何|哪些)(?:区别|差别|不同|异同))?$")
_SCOPE = (r"(?:我(?:们)?(?:的)?|之前|上次|已|已经|刚才)*"
          r"(?:(?:收藏|保存|收录|存)(?:过|好|下来|了)?的|(?:阅读清单|收藏夹|这张表|当前表|论文库|文献库)(?:里面|里|中|内)?的?)")
_ACTION = r"(?:对比|比较|对照|比一比)(?:一下|下|看看)?"
_POLITE = r"(?:(?:请问|请|麻烦你?|帮我|给我|我想|能不能|可以|先)\s*)*"
_COMPARE = re.compile(rf"^{_POLITE}(?:(?:在|从)?(?P<scope>{_SCOPE})\s*)?{_ACTION}\s*(?P<body>.+)$")
_REVERSED = re.compile(rf"^{_POLITE}把\s*(?P<body>.+?)\s*(?:拿来|进行|做个)?{_ACTION}$")
_SOURCE_PREFIX = re.compile(rf"^(?:在|从)?{_SCOPE}\s*")
_QUOTED = re.compile(r'《[^《》\n]{1,200}》|「[^「」\n]{1,200}」|“[^“”\n]{1,200}”|"[^"\n]{1,200}"')
_AND = re.compile(r"以及|和|与|跟|、|\s+and\s+|\s+vs\.?\s+|\s+&\s+", re.I)
_PLAIN = re.compile(r"[\w][\w .+:’'()_-]{0,199}")
_OTHER = re.compile(r"https?://|网上|全网|外部|查原文|读原文|原文中|重新提取|同时|然后|接着|顺便|"
                    r"写入|回填|删除|修改|授权|不要|不用|禁止|如果|假设|先别|第[一二两三\d]+篇")
_EVIDENCE = {"关键数据": ("metric",), "图表": ("figure", "table"), "公式": ("formula",),
             "资源链接": ("code", "model", "dataset"),
             "代码链接": ("code",), "模型链接": ("model",), "数据集链接": ("dataset",)}
_BLOCK = re.compile(r"(?m)^【(?:" + "|".join(_KIND_NAMES.values()) + r")】")
_HELP = "请用《》分别标出 2 至 4 篇已收藏论文的题名或简称，例如“对比我收藏的《论文甲》和《论文乙》的方法和关键数据”。"


def _request(message):
    text = message.strip().rstrip(" ？?。！!").removesuffix("吧").strip()
    match = _COMPARE.fullmatch(text) or _REVERSED.fullmatch(text)
    if not match:
        return None
    body, scoped = _SOURCE_PREFIX.subn("", match["body"], count=1)
    scoped = scoped or match.groupdict().get("scope")
    body = re.sub(r"^(?:这?[两二三四2-4]篇(?:论文|文献|文章)?|论文|文献)\s*[：:]?\s*(?=[《「“\"])", "", body)
    masked = _QUOTED.sub(lambda quoted: " " * len(quoted[0]), body)
    if _OTHER.search(masked):
        if scoped and re.search(r"写入|回填|删除|修改|授权", masked):
            return "对照只读取当前表的已存字段。请把要比较的两篇题名和字段单独发来。"
        return None
    wanted = _FIELD_END.search(masked)
    labels = list(dict.fromkeys(_TERMS[t] for t in re.split(_SEPARATOR, wanted[1]))) if wanted else ["方法", "关键数据", "笔记"]
    pair = body[:wanted.start()].strip() if wanted else body.strip()
    pair = re.sub(r"(?<=[》」”\"])\s*(?:这|那)[两二三四2-4]篇(?:论文|文献|文章)?$", "", pair)
    quotes = list(_QUOTED.finditer(pair))
    if not scoped and len(quotes) < 2:
        return None
    if len(quotes) >= 3:
        separators = [pair[a.end():b.start()].strip() for a, b in zip(quotes, quotes[1:])]
        if (len(quotes) > 4 or pair[:quotes[0].start()].strip() or pair[quotes[-1].end():].strip()
                or not all(re.fullmatch(r"(?:、|，|,)?\s*(?:和|与|跟|及|以及|and|&)?", s, re.I) for s in separators)):
            return [], labels
        return [q[0][1:-1].strip() for q in quotes], labels
    if quotes and pair[quotes[-1].end():].strip().startswith("的"):
        return ("可以比较已保存的摘要、方法、关键数据、图表、公式和笔记。想先看哪一项？"
                if scoped else None)
    # 书名号内的“和”、and 或 & 都是题名原文。引号外有多个分隔符时请用户标清范围。
    joins = [join for join in _AND.finditer(pair) if not any(q.start() <= join.start() < q.end() for q in quotes)]
    if len(joins) != 1:
        return ([], labels)
    left, right = pair[:joins[0].start()].strip(), pair[joins[0].end():].strip()
    hints = []
    for name in (left, right):
        if _QUOTED.fullmatch(name):
            hints.append(name[1:-1].strip())
        elif _PLAIN.fullmatch(name) and not re.search(r"的(?:价格|权限|区别|优劣|全文)|[这那]两篇", name):
            hints.append(name)
        else:
            return ([], labels)
    return (hints if all(hints) else [], labels)


def _tagged_notes(value, tags):
    blocks = []
    positions = [m.start() for m in _BLOCK.finditer(value)] + [len(value)]
    for start, end in zip(positions, positions[1:]):
        block = value[start:end]
        if block.startswith(tags):
            source = re.search(r"(?m)^出处：[^\r\n]+(?:\r?\n原图：[^\r\n]+)?", block)
            # 摘录本身可能含空行；已确认的证据段以出处（和可选的原图链接）结束。
            blocks.append(block[:source.end()] if source else re.split(r"\r?\n[ \t]*\r?\n", block, 1)[0].rstrip())
    return "\n\n".join(blocks)


def _values(schema, label, row):
    fields = row["fields"]
    kinds = _EVIDENCE.get(label)
    if kinds:
        notes = set(column_names(schema, "笔记")[2])
        tagged = tuple(f"【{_KIND_NAMES[kind]}】" for kind in kinds if kind in _KIND_NAMES)
        names = {name for kind in kinds for name in _FIELD_NAMES.get(kind, ())} | notes | set(_LABELS[label])
    else:
        names = set(column_names(schema, label)[2])
    values = []
    for column in schema:
        name = column["name"]
        if name not in names or fields.get(name) is None:
            continue
        value = str(fields[name])
        if kinds and (name in notes or name == "资源链接" and label != "资源链接"):
            # 专用列保存原值；通用笔记只引用已带相应类型标签的原文段落。
            value = _tagged_notes(value, tagged)
        if value.strip():
            values.append({"field": name, "value": value})
    return values


def read_selection(chat, schema, references, minimum=2, maximum=4):
    if not isinstance(references, list) or not minimum <= len(references) <= maximum:
        raise ValueError(f"请选择 {minimum} 至 {maximum} 条当前表的论文。")
    titles, links, _ = column_names(schema)
    selected = []
    seen = set()
    for ref in references:
        if (not isinstance(ref, dict) or set(ref) != {"id", "title", "url"} or
                not all(isinstance(v, str) for v in ref.values()) or not ref["id"] or
                ref["id"] in seen or not (ref["title"] or ref["url"])):
            raise ValueError("请选择不同的、可识别的论文记录。")
        row = chat.table.get(ref["id"])
        if row["id"] != ref["id"] or _identity(row, titles, links) != (ref["title"], ref["url"]):
            raise ValueError("选中论文的题名或链接已变化。请刷新清单后重新选择。")
        seen.add(ref["id"])
        selected.append((row, ref["title"], ref["url"]))
    return selected


def comparison_data(table_name, schema, selected, labels):
    from research_exports import markdown_cell
    papers = [{"record_id": row["id"], "title": title, "url": url} for row, title, url in selected]
    fields = [{"label": label, "values": [_values(schema, label, row) for row, _, _ in selected]}
              for label in labels]
    cells = [["比较项", *[p["title"] or p["url"] for p in papers]],
             ["---"] * (len(papers) + 1), ["论文链接", *[p["url"] or "未保存" for p in papers]]]
    for field in fields:
        cells.append([field["label"], *["\n\n".join(item["field"] + " · " + item["value"] for item in values)
                                       or "未保存" for values in field["values"]]])
    markdown = "\n".join("| " + " | ".join(markdown_cell(cell) for cell in row) + " |" for row in cells)
    return {"table_name": table_name, "papers": papers, "fields": fields, "markdown": markdown}


def handle(chat, message):
    if not isinstance(message, str) or not 0 < len(message) <= 800 or (
            chat.pending and message in chat.pending.get("choices", ())):
        return None
    request = _request(message)
    if request is None:
        return None
    reply = {"pending_id": chat.pending["id"], "choices": chat.pending.get("choices", [])} if chat.pending else {}
    if isinstance(request, str):
        return {**reply, "message": request}
    hints, labels = request
    if not hints:
        return {**reply, "message": _HELP}
    schema = chat._read_schema()
    titles, links, _ = column_names(schema)
    with step("按题名回读当前表中的收藏", tool=chat.table_tool):
        rows = chat.table.rows()
        matches = [matching_rows(rows, hint, titles, links) for hint in hints]
    problems = []
    for hint, hits in zip(hints, matches):
        if not hits:
            problems.append(f"「{hint}」在当前表没有匹配记录。")
        elif len(hits) > 1:
            shared = len({hit[2] for hit in hits}) != len(hits)
            examples = "、".join(f"《{title}》" + (f"（{url or row['id']}）" if shared else "")
                               for _, row, title, url, _ in hits[:3])
            problems.append(f"「{hint}」匹配到 {len(hits)} 条记录，包括{examples}。")
            if shared:
                problems.append("存在同名记录，请先核对链接区分重复行。")
    if problems:
        return {**reply, "message": "\n".join([*problems, "请核对当前表，并用两篇完整题名重新对比。"])}
    selected = [hits[0][1:4] for hits in matches]
    if len({row["id"] for row, _, _ in selected}) < len(selected):
        return {**reply, "message": "多个题名指向当前表的同一条收藏，请指定不同的论文。"}
    data = comparison_data(chat.table_name, schema, selected, labels)
    papers = data["papers"]
    lines = [f"「{chat.table_name}」里的 {len(papers)} 篇收藏", *[
        f"{n}. 《{paper['title']}》\n   {paper['url'] or '未保存论文链接'}" for n, paper in enumerate(papers, 1)]]
    for field in data["fields"]:
        lines.append("\n" + field["label"])
        for index, cell in enumerate(field["values"], 1):
            content = "\n".join(f"「{val['field']}」{val['value']}" for val in cell) or "未保存"
            lines.append(f"第{index}篇 · {content}")
    if not chat.pending:
        from library_search import remember_results
        from saved_paper_dialog import _bind
        _bind(chat)
        remember_results(chat, [(p["record_id"], p["title"], p["url"]) for p in papers], None)
        lines.append("\n可以说“打开第一篇”或“打开第二篇”查看对应记录。")
    return {**reply, "message": "\n".join(lines), "saved_comparison": data}
