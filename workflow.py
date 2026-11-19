"""把用户的意图、论文证据和表中真实存在的列连接起来。"""

import re
import threading
import uuid
from urllib.parse import urlencode, urlsplit

import papers
import model_support
import source_library
import saved_focus
from activity import History
from activity import step
from dialog_choices import resolve_choice
from paper_selection import paper_positions, separate_paper_clauses, without_positions
import paper_search_dialog
from preflight import verify_write
from table_store import LocalTable, RecordNotFound, TableError, _identity


ROLES = {
    "url": ("论文链接", "论文地址", "原文地址", "原文链接", "文献链接", "预印本链接", "链接", "url", "link"),
    "title": ("论文标题", "文献标题", "论文名称", "标题", "题名", "题目", "名称", "title", "name"),
    "abstract": ("中文摘要", "论文摘要", "摘要", "简介", "abstract", "summary"),
    "method": ("方法概述", "研究方法", "方法", "技术路线", "method", "approach"),
    "note": ("阅读笔记", "研究笔记", "笔记", "备注", "note"),
}
LABEL = {"url": "论文链接", "title": "论文标题", "abstract": "摘要", "method": "方法", "note": "笔记"}
STORE = r"存|填|写|放|记入|记在|记到|记录|收录|收下|收着|入库|入表|录入|添加|加到|加入|加进|补|登记|导入|保存|收进|更新|同步|收藏"
DOI_START = r"10\.\d{4,9}/"
# 分号也可以是 DOI 正文的一部分；只在后面紧跟另一个标识时作为分隔符。
NEXT_REF = rf"https?://|;(?=(?:doi[:：])?{DOI_START}|arxiv[:：])"
DOI_PATTERN = rf"(?<![a-zA-Z0-9.]){DOI_START}(?:(?!{NEXT_REF})[A-Z0-9._;()/:-])+"
REFERENCE = re.compile(rf"https?://|arxiv\s*[:：]|(?<![\da-z])\d{{4}}\.\d{{4,5}}(?!\d)|{DOI_PATTERN}", re.I)
# URL 参数可以包含完整网址（如 ?redirect=https://…），其他相邻的协议头开始下一条。
URL_CHAR = r"""[^\s<>"'`，。；、！？：（）【】「」『』《》“”‘’|｜,\u200b\ufeff\u4e00-\u9fff]"""
PASTED_URL = rf"https?://(?:=https?://|(?!{NEXT_REF}){URL_CHAR})+"
MARKDOWN_LINK = r"\[[^\[\]\r\n]*\]\((?P<markdown>https?://(?:[^\s()]|\([^\s()]*\))+)\)"
EVIDENCE_REFERENCES = re.compile(
    rf"{MARKDOWN_LINK}|{PASTED_URL}|{DOI_PATTERN}|(?<![a-zA-Z0-9_.]){papers.ARXIV_ID}(?![a-zA-Z0-9.])",
    re.I,
)
EVIDENCE_TERMS = {
    "metric": re.compile(r"指标|数据(?!表|库|集|源)|数值|数字|得分|分数|成绩|跑分|"
                         r"(?:实验|测试|评测|评估|对比|消融)(?:的)?(?:结果|表现|结论)|"
                         r"准确率|正确率|精度|召回率|提升了?多少|(?:多少|几)分|量化结果|关键结果|"
                         r"\b(?:metrics?|scores?|results?|accuracy|BLEU|F1)\b", re.I),
    "figure": re.compile(r"图表|图片|图像|插图|配图|原图|架构图|结构图|曲线|示意图|流程图|"
                         r"图\s*\d+|(?:些|张|看|的|和|与|跟|及|、|只要|只看|摘出|提取|只留)图|图(?=[和跟与及、/表，。！？\s]|(?:先)?(?:不|去掉)|$)|\bfig(?:ures?)?\b", re.I),
    "table": re.compile(r"图表|表格|结果表|实验表|对比表|数据表|原表|表\s*\d+|"
                        r"(?:哪些|几[张个]|的|只要|只看|提取|和|跟|与|及|、|/)表(?=[里中和与，。！？\s]|$)|\btables?\b", re.I),
    "formula": re.compile(r"公式|方程|数学表达式|损失函数|目标函数|概率表达式|计算式|"
                          r"\b(?:latex|equations?|formul(?:a|as|ae))\b", re.I),
    "code": re.compile(r"代码(?:仓库|地址|链接|实现)?|源码|开源实现|项目仓库|仓库链接|"
                       r"开源资源|配套资源|\b(?:github|gitlab|code\s+repositor(?:y|ies))\b", re.I),
    "model": re.compile(r"(?:模型|权重)(?:的)?(?:权重|地址|链接|仓库|资源|下载)|预训练权重|"
                        r"开源资源|配套资源|\b(?:model\s+(?:weights?|links?)|checkpoints?)\b", re.I),
    "dataset": re.compile(r"数据集(?:的)?(?:链接|地址|仓库|资源|下载)|开源资源|配套资源|"
                          r"\bdataset\s+(?:links?|repositor(?:y|ies))\b", re.I),
}
SCOPE_CHOICES = {"预览数据和图表": ["metric", "figure", "table"], "只看关键数据": ["metric"],
                 "只看图表": ["figure", "table"], "只看公式": ["formula"], "查看配套资源": ["code", "model", "dataset"]}
STANDARD_EVIDENCE = ["metric", "figure", "table", "formula"]
PAPER_CONTEXT = re.compile(r"论文|文献|文章|研究里|paper\b|这[一几两三]?[篇份]|那[一几两三]?[篇份]", re.I)
PREVIOUS_REF = re.compile(r"刚才|刚刚|上(?:面|次|一[批篇轮])|这[一几两三]?[批篇份]|那[一几两三]?[批篇份]|它[们俩]?|接着|还是|再看|也看")
REPEAT_SCOPE = re.compile(r"照旧|照[刚上]次|按.{0,6}(?:来|一样|处理)|同样|一样(?:来|处理)|也(?:来|做|跑|处理)一[遍下]|也这么")
PREVIEW_CUES = re.compile(r"预览|过目|核对|确认后|给我.{0,3}(?:看看|看下)|分析|梳理|提炼|捋一捋|盘一盘|"
                          r"值得|有用的|亮点|重点|关键信息|主要发现|实验部分|提取|抽取|摘出|提出来")
PLAIN_SAVE = re.compile(r"收藏|收下|收着|收录|归档|入库|存进|存入|存起来|保存|入表|登记到|纳入|收进|"
                        r"(?:把|将).{0,12}(?:存|放|加)(?:下|起|进|入|到)|也记进去")
NOTE_TEXT = re.compile(r"^(?P<head>.*?(?:记(?:上|下)?(?:一[句条]|个)?(?:阅读|研究)?笔记|"
                       r"(?:做|补|加|添|写)(?:上|下)?(?:一[句条]|个)?(?:阅读|研究)?笔记|"
                       r"记一[句条]|(?:给|帮|替).{0,30}?(?:阅读|研究)笔记(?:里)?(?:加|补|写)一[句条]))"
                       r"\s*[：:，,]\s*(?P<body>\S[\s\S]*)$")
QUOTED_NOTE = re.compile(r"(?:把|将)\s*[「『“‘\"'](.+?)[」』”’\"']\s*(?:记录|填|写|放|存|记)")
ORDINARY_CONTENT = re.compile(r"摘要|简介|方法|标题|题名|网址|链接|下载|原文PDF|阅读笔记|研究笔记")
LINK_ONLY = re.compile(r"(?:请|帮我|那就|还是|仅|只|先|要|想)*(?:收藏|保存|收录|存|留)[一下个]*(?:这[篇些几两]?论文的?)?(?:论文)?链接(?:就好|就行|吧)?[。！!]?")
NO_EXTRACTION = re.compile(r"(?:不要|不用|停止|取消|别|禁止)(?:再)?(?:.{0,8}?(?:提取|抽取|预览|整理|分析)|处理(?:论文|这|它))")
ADMIN_OR_LITERAL = re.compile(r"权限|协作者|角色|改名|重命名|建列|删[除掉]|清空|字段(?:名|类型)|"
                              r"(?:哪些|什么|几|所有)(?:个)?(?:列|栏|字段)|(?:列|字段)(?:叫什么|有哪些)|"
                              r"(?:新增|新建|增加|创建|添加|改|查[看询]|看看).{0,6}(?:列名|列「|字段|一列|文本列|链接列)|"
                              r"(?:把|将)\s*[「『“‘\"'].+?[」』”’\"']\s*(?:记录|填|写|放|存|记)")
ORDINAL = re.compile(r"第\s*([一二三四五六七八1-8])\s*篇")
EVIDENCE_DECISION = re.compile(r"(确认(?:回填|写入|提交)|取消回填)(?:\s+([0-9a-f]{32}))?[。！!]?")
SAVED_QUESTION = re.compile(r"(?:是否(?:已经)?|是不是已经|有没有|有没).{0,12}(?:存|收录|录入|写入|入表)"
                            r"|(?:已经|之前|刚才|现在).{0,16}(?:存|收录|录入|写入|入表).{0,10}(?:吗|没|[?？])"
                            r"|(?:保存|收藏|收录|录入|写入|存)(?:过|好了|了)(?:.{0,8})(?:没|吗|[?？])")
DECLINED_WRITE = re.compile(r"(?:不需要|不想|不打算|不用|不要|无需|别|禁止|不必|暂不).{0,30}?"
                            r"(?:保存|收藏|存|收录|登记|填写|写入|录入|回填|提交|写表|填表|修改)")


def _evidence_urls(message):
    # 用户要求原样写入的笔记可以包含多个参考网址，这些不是待操作的论文。
    literal = QUOTED_NOTE.search(message)
    if literal:
        message = message[:literal.start(1)] + " " + message[literal.end(1):]
    urls = []
    for match in EVIDENCE_REFERENCES.finditer(message):
        value = match["markdown"] or match.group()
        if re.fullmatch(DOI_PATTERN, value, re.I):
            value = "https://doi.org/" + value.rstrip(".,;:")
        arxiv, url = papers._find_target(value)
        target = "https://arxiv.org/abs/" + arxiv if arxiv else url
        if target not in urls:
            urls.append(target)
    if len(urls) > 8:
        raise ValueError("每次最多可处理 8 篇论文，请分批发送。")
    return urls


def _evidence_scope(text):
    # “放进飞书表格”是目的地；只有原文中的表格才是提取对象。
    content = re.sub(r"(?:飞书|多维|当前|这张|目标)(?:多维)?(?:数据)?表格?|(?:到|进|入)(?:对应的?)?表格?", "", text)
    # “收藏公开了代码的论文”描述选篇条件，不能把它解释为提取代码地址。
    if PLAIN_SAVE.search(content):
        content = re.sub(r"(?:会发|有|提供|带有|附带|公开|发布|开源)(?:了)?(?:代码(?:仓库|链接)?|模型权重|数据集链接)的(?:那些)?(?:论文|文章|文献)", "", content)
    wanted, excluded = set(), set()
    for clause in re.split(r"[，,；;。\n]|但是|不过|只(?=要|看|留)", content):
        if re.search(r"(?:先|暂时)?(?:不用|不要|不看)(?:了)?\s*$", clause):
            excluded.update(kind for kind, pattern in EVIDENCE_TERMS.items() if pattern.search(clause))
            continue
        for phrase in re.split(r"(?=不要|不用|不看|别提取|不需要|不提取|去掉|排除|跳过)", clause):
            kinds = {kind for kind, pattern in EVIDENCE_TERMS.items() if pattern.search(phrase)}
            if re.match(r"不要|不用|不看|别提取|不需要|不提取|去掉|排除|跳过", phrase) or re.search(r"(?:不用|不要|不看)(?:了)?$", phrase):
                excluded.update(kinds)
            else:
                wanted.update(kinds)
    return [kind for kind in EVIDENCE_TERMS if kind in wanted - excluded], excluded


def _evidence_kinds(text):
    return _evidence_scope(text)[0]


def _kinds_by_paper(text, urls):
    clauses = separate_paper_clauses(text)
    if len(clauses) < 2:
        return None
    groups = [(paper_positions(clause, len(urls)), _evidence_kinds(clause)) for clause in clauses]
    if not all(positions and kinds for positions, kinds in groups):
        # “第一篇和第二篇都看图”共用句末范围；“第一篇看图，第二篇”仍缺少后者的范围。
        return None if all(positions for positions, _ in groups) and not any(k for _, k in groups[:-1]) else {}
    result = {}
    for positions, kinds in groups:
        for i in positions:
            result[urls[i]] = list(dict.fromkeys(result.get(urls[i], []) + kinds))
    return result


INCOMPLETE_EVIDENCE_SCOPE = "请补全每篇要看的内容，例如“第一篇看图，第二篇看实验数据”。也可以为本批选择统一范围。"


def _preview_positions(text, urls, entries=()):
    positions = paper_positions(text, len(urls), exclude=True)
    if len(urls) < 2 or positions == []:
        return positions
    if re.search(r"前者|后者", text):
        if len(urls) != 2 or re.search(r"不|别|除了|还是|或者", text) or ("前者" in text and "后者" in text):
            return []
        return [int("后者" in text)]
    if not re.search(r"(?:这|那)(?:一)?篇", text):
        return positions
    names = re.findall(r"(?:《([^》]+)》|「([^」]+)」|([A-Za-z][A-Za-z0-9._-]*))\s*(?:的)?[这那](?:一)?篇", text)
    if len(names) != 1 or re.search(r"不要|不看|跳过|排除|除了", text):
        return []
    title, label, word = names[0]
    query = (title or label or word).casefold()
    sites = {"ieee": "ieeexplore.ieee.org", "acl": "aclanthology.org", "arxiv": "arxiv.org",
             "cvf": "openaccess.thecvf.com", "openreview": "openreview.net"}
    matches = []
    for i, url in enumerate(urls):
        name = entries[i].get("title", "").casefold() if i < len(entries) else ""
        if (word and query in sites and urlsplit(url).hostname == sites[query]) or query in name:
            matches.append(i)
    return matches if len(matches) == 1 and positions in (None, matches) else []


def _clean(value):
    return value.strip().strip("「」『』“”‘’‘”\"'，。、.!！?？ ").strip()


def _normalized(value):
    return re.sub(r"[\s_·-]", "", value).casefold()


def _destination(message):
    hit = re.search(r"(?:写进|写入|填到|填入|填进|补进|补到|补入|放到|放进|记到|记入|记在|存到|存进|保存到)\s*([^，。？！、\n]+?)\s*$", message)
    if not hit:
        return ""
    quoted = re.fullmatch(r'[「『“"]([^」』”"]+)[」』”"](?:这(?:一)?[栏列]|字段)?[里中内]?', hit[1].strip())
    if quoted:
        return quoted[1].strip()
    value = re.sub(r"(?:这|那|一)?[个一]?栏(?:里|中)?$|(?:这|那|一)?列(?:里|中)?$|字段(?:里|中)?$|(?<=笔记)[里中]$", "", _clean(hit[1]))
    return _clean(value)


def _fields(schema, kind, hint=""):
    valid = [f for f in schema if f["type"] in ({"url", "text"} if kind == "url" else {"text"})]
    if hint:
        exact = [f for f in valid if _normalized(f["name"]) == _normalized(hint)]
        return exact or [f for f in valid if _normalized(hint) in _normalized(f["name"])]
    if kind == "url":
        valid = [f for f in valid if not re.search(r"资源|代码|源码|仓库|权重|模型|数据集|原图|开源|"
                                                    r"github|gitlab|hugging\s*face|repo|dataset|model|code",
                                                    f["name"], re.I)]
    aliases = ROLES[kind]
    matches = [f for f in valid if _normalized(f["name"]) in [_normalized(a) for a in aliases]]
    if not matches:
        matches = [f for f in valid if any(_normalized(a) in _normalized(f["name"]) for a in aliases)]
    if kind == "url" and not matches:
        matches = [f for f in valid if f["type"] == "url"]
    return matches


def _parse(message, table_name):
    from intent_model import DOWNLOAD, allows_download, allows_english
    # 只把用户在链接和逐字笔记之外说的话当作操作要求。
    literal = QUOTED_NOTE.search(message)
    if literal:
        message = message[:literal.start(1)] + "…" + message[literal.end(1):]
    message = EVIDENCE_REFERENCES.sub(" ", message)
    download = allows_download(message)
    explicit_write = bool(re.search(STORE, DOWNLOAD.sub(" ", message)))
    if download and not explicit_write:
        return {"download": True, "slots": [], "english": False}
    if not explicit_write:
        raise ValueError("可以说“存入这篇论文的链接”“给刚才那篇补一句方法概述”，或“下载刚才那篇论文”。")
    destination = _destination(message)
    table_terms = {table_name, "当前表", "本表", "这张表", "表", "飞书", "飞书表格", "清单", "表格"}
    if destination in table_terms:
        destination = ""
    elif destination and (destination.endswith("表") or destination.endswith("清单")):
        raise ValueError(f"当前连接的是「{table_name}」。请指定当前表，或切换启动配置后再写入。")
    if destination and (REFERENCE.search(destination) or len(destination) > 40):
        destination = ""
    parts = []
    if re.search(r"标题|题名|题目|论文名", message, re.I):
        parts.append("title")
    if re.search(r"链接|网址|地址|\burl\b", message, re.I):
        parts.append("url")
    if re.search(r"摘要|简介|abstract", message, re.I):
        parts.append("abstract")
    if re.search(r"方法|技术路线|模型架构|method", message, re.I):
        parts.append("method")
    if literal:
        parts = ["note"]
    if not literal and re.search(r"贡献|实验结果|数据集|指标|作者|年份|机构|结论|怎么搭|怎么做", message):
        raise ValueError("请说明要填的是论文标题、链接、摘要还是方法；也可以用引号给出要保存的笔记原文。")
    if not parts and "笔记" in message:
        raise ValueError("笔记里要填什么内容？可以说“把刚才那篇的方法写进笔记”，或“把「待读」写进阅读笔记”。")
    default_title = not parts
    if default_title:
        if destination:
            raise ValueError(f"要写入「{destination}」的是什么内容？请明确说“把论文链接写进该列”或指定摘要、方法。")
        parts = ["url", "title"]
    if destination and len(parts) > 1:
        raise ValueError("这条指令同时指定了多种内容和目标列。请逐项指定要写入的列，或直接说“补摘要和方法”。")
    slots = []
    for kind in parts:
        hint = destination if len(parts) == 1 else ""
        slots.append({"kind": kind, "hint": hint, "optional": default_title and kind == "title"})
    return {"download": download, "slots": slots, "literal": literal[1] if literal else "",
            "english": allows_english(message)}


class Conversation:
    def __init__(self, table, data_dir, table_name, base_url="", base_name=""):
        self.table = table
        self.table_view_id = uuid.uuid4().hex
        self.table_tool = getattr(table, "tool_name", None)
        self.data_dir = data_dir
        self.table_name = table_name
        self.base_url = base_url or ""
        self.base_name = base_name
        self.initial_target = (table, table_name, self.base_name, self.base_url)
        self.owned_bases = []
        self.workspace_receipt = None
        self.last = None
        self.last_title = ""
        self.last_library_results = None
        saved_focus.clear(self)
        self.pending = None
        self.last_evidence_receipt = None
        self.recent_evidence = None
        self.recent_saved = []
        self.collection_batch = []
        self.selected_model = None
        self.activity = History()
        self.lock = threading.RLock()

    def select_table(self, table, name, base_name, base_url):
        self.table, self.table_name = table, name
        self.table_view_id = uuid.uuid4().hex
        self.base_name, self.base_url = base_name, base_url
        self.table_tool = getattr(table, "tool_name", None)
        self.last = self.last_title = self.pending = None
        self.last_library_results = None
        saved_focus.clear(self)
        self.last_evidence_receipt = self.recent_evidence = None
        self.recent_saved = []
        self.collection_batch = []

    def close(self):
        with self.lock:
            for table in self.owned_bases:
                table.close()
            self.owned_bases.clear()

    def _ask(self, plan, message, choices, stage):
        task = self.pending or {"id": uuid.uuid4().hex}
        task.update(plan=plan, stage=stage, message=message, choices=choices)
        self.pending = task
        return {"message": message, "choices": choices, "pending_id": task["id"]}

    def _read_schema(self):
        with step("读取数据表字段", tool=self.table_tool):
            return self.table.schema()

    def _table_lookup(self, kind):
        from admin_dialog import NamedLookup, TYPE_LABEL

        if kind == "skip_query":
            return "好的，本次跳过查询。"
        if kind == "clarify":
            return f"你想了解「{self.table_name}」的哪一部分？可以回复“看记录”“查看字段”或“一共有几条记录”。"
        schema = self._read_schema() if kind != "count" else []
        if isinstance(kind, NamedLookup) and kind.kind == "column":
            matches = [f for f in schema if kind.name in (f["name"], f["id"])]
            if not matches:
                matches = [f for f in schema if kind.name in f["name"]]
            if not matches:
                return f"「{self.table_name}」中没有找到「{kind.name}」字段。可以说“查看字段”核对列名。"
            return "\n".join(f"字段「{f['name']}」的类型为{TYPE_LABEL.get(f['type'], f['type'])}，ID 为 {f['id']}。"
                             for f in matches)
        if kind == "columns":
            details = "、".join(f"{f['name']}（{TYPE_LABEL.get(f['type'], f['type'])}）" for f in schema)
            return f"「{self.table_name}」有 {len(schema)} 个字段。\n{details}"
        with step("查询数据表的可见记录", tool=self.table_tool):
            rows = self.table.rows()
        summary = f"「{self.table_name}」共有 {len(rows)} 条可见记录。"
        if kind == "count":
            return summary
        titles = [f["name"] for f in _fields(schema, "title")]
        links = [f["name"] for f in _fields(schema, "url")]
        identities = titles + links
        if isinstance(kind, NamedLookup) and kind.kind == "paper_title":
            if not titles:
                return "当前表缺少可识别的论文标题列，无法按题名核对。"
            wanted = " ".join(kind.name.split()).casefold()
            total = sum(any(" ".join(str(row["fields"].get(col, "")).split()).casefold() == wanted
                            for col in titles) for row in rows)
            if total:
                return f"「{self.table_name}」找到 {total} 条题名为《{kind.name}》的可见记录。"
            return f"「{self.table_name}」可见记录中未找到题名为《{kind.name}》的论文。"
        if kind in ("paper_count", "paper_list"):
            if not identities:
                return summary + "当前表缺少可识别的论文标题或链接字段，无法核对论文数量。"
            papers = [r["fields"] for r in rows if any(r["fields"].get(name) for name in identities)]
            summary += f"其中 {len(papers)} 条填写了论文标题或链接。"
            if kind == "paper_count" or not papers:
                return summary
            columns = "、".join(f["name"] for f in schema[:12]) + ("等" if len(schema) > 12 else "")
            lines = [summary, f"当前字段：{columns}。"]
            for i, fields in enumerate(papers[:20], 1):
                title = next((fields[n] for n in titles if fields.get(n)), "")
                url = next((fields[n] for n in links if fields.get(n)), "")
                lines.append(f"{i}. " + " · ".join(str(value) for value in (title, url) if value))
            if len(papers) > 20:
                lines.append(f"这里显示前 20 条，共 {len(papers)} 条。")
            return "\n".join(lines)
        names = [field["name"] for field in schema]
        columns = "、".join(names[:12]) + ("等" if len(names) > 12 else "")
        lines = [summary, f"{len(names)} 个字段：{columns}。"]
        # 飞书空行也计入记录数，示例优先展示有内容的行，避免前几行空白掩盖实际数据。
        order = list(dict.fromkeys(identities + names))
        previews = []
        for row in rows:
            cells = row["fields"]
            filled = [name for name in order if cells.get(name)]
            if not filled:
                continue
            preview = []
            for name in filled[:2]:
                value = " ".join(str(cells[name]).split())
                preview.append(f"{name}：{value[:100]}" + ("…" if len(value) > 100 else ""))
            previews.append(" · ".join(preview))
            if len(previews) == 5:
                break
        if previews:
            lines.extend(["非空记录预览（最多 5 条）", *previews])
        elif rows:
            lines.append("当前可见记录的字段均为空。")
        return "\n".join(lines)

    def _saved_status(self, message):
        urls = _evidence_urls(message) or self._previous_evidence_urls(message)
        if not urls:
            return {"message": "请发想查询的论文链接，再问我是否已经收录。"}
        columns = _fields(self._read_schema(), "url")
        if not columns:
            return {"message": "当前表缺少可识别的论文链接列，无法按链接核对。"}
        with step("按论文链接查询可见记录", tool=self.table_tool):
            rows = self.table.rows()
        found = {_identity(value) for row in rows for col in columns
                 if (value := row["fields"].get(col["name"])) and isinstance(value, str)}
        return {"message": "\n".join(f"{url}　" + ("已收录" if _identity(url) in found or
                                     source_library.existing_source(self.table, [url], rows) else "当前可见记录中未找到")
                                     for url in urls)}

    def _note_targets(self, head):
        urls = _evidence_urls(head)
        if urls:
            return urls
        named = re.search(r"(?:给|替|为)\s*(.+?)\s*(?:补|记|加|写|添|的(?:阅读|研究)笔记)", head)
        if not named or PREVIOUS_REF.search(named[1]) or ORDINAL.search(named[1]):
            return self._previous_evidence_urls(head, suggested=True)
        name = named[1].strip(" 《》“”「」").casefold()
        if not name or name in {"我", "我也"}:
            return self._previous_evidence_urls(head, suggested=True)
        schema = self._read_schema()
        titles = [f["name"] for f in _fields(schema, "title")]
        links = [f["name"] for f in _fields(schema, "url")]
        return [url for row in self.table.rows()
                if any(name in str(row["fields"].get(k, "")).casefold() for k in titles)
                if (url := next((row["fields"][k] for k in links if row["fields"].get(k)), None))]

    def _show_sources(self, urls):
        if len(urls) != 1:
            return {"message": "请指定一篇已收藏的论文，发链接、DOI，或说明刚才那一批里的第几篇。"}
        rows = self.table.rows()
        anchor = source_library.existing_source(self.table, urls, rows)
        if not anchor:
            return {"message": "当前表中未找到这篇论文的收藏记录。"}
        links = [f["name"] for f in _fields(self._read_schema(), "url")]
        row = next(r for r in rows if any(r["fields"].get(k) == anchor for k in links))
        meta = source_library.stored_metadata(self.table, row, anchor)
        sources = source_library.known_sources(self.table, row, anchor)
        return {"message": "这篇论文已有以下来源。", "collection": [
            source_library.card({**meta, "url": anchor}, sources or [anchor], row)]}

    def _preview_evidence(self, urls, kinds):
        from evidence_workflow import prepare
        if isinstance(kinds, dict):
            if any(url not in kinds for url in urls):
                return self._ask_evidence_scope(urls)
            kinds = {url: kinds[url] for url in urls}
        plan, reply = prepare(self.table, urls, kinds=kinds)
        batch_id = uuid.uuid4().hex
        reply["evidence_preview"] = {"id": batch_id, "papers": reply["evidence_preview"]}
        reply.pop("choices", None)
        self.pending = None
        self.recent_evidence = (list(urls), kinds.copy())
        if len(urls) == 1:
            self.last = urls[0]
            self.last_title = next((paper.get("title") for paper in reply["evidence_preview"]["papers"]
                                    if paper.get("title")), urls[0])
        else:
            self.last = self.last_title = None
        if plan["ready"]:
            reply["pending_id"] = batch_id
            self.pending = {"id": batch_id, "stage": "evidence_confirm", "plan": plan, "reply": reply}
        return reply

    def _previous_evidence_urls(self, message, *, suggested=False, candidates=None):
        urls = candidates
        if urls is None:
            urls = self.recent_evidence[0] if self.recent_evidence else (self.recent_saved or ([self.last] if self.last else []))
        ordered = self.collection_batch if candidates is None and not self.recent_evidence and self.collection_batch else urls
        positions = paper_positions(message, len(ordered))
        if positions is not None:
            return [ordered[index] for index in positions]
        if PREVIOUS_REF.search(message) or suggested:
            if len(urls) > 1 and re.search(r"上(?:一)?篇|[这那](?:一)?篇|它(?![们俩])", message):
                return []
            return urls
        return []

    def _ask_evidence_scope(self, urls, prompt=None):
        choices = list(SCOPE_CHOICES)
        if len(urls) == 1:
            choices.append("只保存链接")
        elif len(urls) > 1:
            choices.append("全部先收藏")
        if prompt:
            self.pending = None
            prompt += "\n" + "\n".join(f"{i}. {url}" for i, url in enumerate(urls, 1))
        return self._ask({"urls": urls}, prompt or "要先核对哪些内容？我会把原文出处和对应的目标列一起列出来。",
                         [*choices, "取消"], "evidence_scope")

    def _start_evidence(self, urls, kinds):
        if urls:
            return self._preview_evidence(urls, kinds)
        return self._ask({"kinds": kinds}, "请发论文链接、DOI 或 arXiv 编号。多篇可以分行发送，也可以指定刚才那一批里的第几篇。",
                         [], "evidence_urls")

    def _ask_evidence_papers(self, urls, kinds):
        self.pending = None
        choices = [f"第{i}篇" for i in range(1, len(urls) + 1)]
        numbered = "\n".join(f"{i}. {url}" for i, url in enumerate(urls, 1))
        return self._ask({"urls": urls, "kinds": kinds},
                         f"当前批次有 {len(urls)} 篇。请按下面的顺序选论文，再核对新的回填预览。\n{numbered}",
                         [*choices, "全部重新预览", "取消"], "evidence_papers")

    def _revise_evidence(self, message, task):
        text = EVIDENCE_REFERENCES.sub(" ", message)
        if ADMIN_OR_LITERAL.search(text):
            return task["reply"]
        urls, previous = self.recent_evidence
        explicit = _evidence_urls(message)
        targets = explicit or urls
        entries = task["reply"]["evidence_preview"]["papers"] if targets == urls else ()
        positions = _preview_positions(text, targets, entries)
        grouped = _kinds_by_paper(text, targets)
        kinds, excluded = _evidence_scope(text)
        changing = explicit or positions is not None or excluded or re.search(r"只|仅|改|换|重新|再看|另", text)
        if not changing or not (explicit or kinds or positions is not None or excluded):
            return task["reply"]
        if positions == []:
            return self._ask_evidence_papers(targets, kinds or previous)
        if positions is not None:
            targets = [targets[index] for index in positions]
        if grouped == {}:
            return self._ask_evidence_scope(targets, INCOMPLETE_EVIDENCE_SCOPE)
        self.pending = None
        if grouped:
            return self._start_evidence(list(grouped), grouped)
        if not kinds and isinstance(previous, dict):
            scopes = {url: [k for k in previous.get(url, []) if k not in excluded] for url in targets}
            scopes = {url: allowed for url, allowed in scopes.items() if allowed}
            return self._start_evidence(list(scopes), scopes) if scopes else self._ask_evidence_scope(targets)
        if not kinds:
            kinds = [kind for kind in previous if kind not in excluded]
        if not kinds:
            return self._ask_evidence_scope(targets)
        return self._start_evidence(targets, kinds)

    def _route_evidence(self, message):
        text = EVIDENCE_REFERENCES.sub(" ", message)
        if ADMIN_OR_LITERAL.search(text):
            return None
        bare = bool(REFERENCE.search(message)) and bool(re.fullmatch(
            r"(?:[\s、，,;；。:：|｜<>《》【】“”‘’（）()\[\].*\-•\u200b\ufeff]|(?:arxiv|doi)\s*[:：]|\d+[.)、])*",
            text, re.I))
        kinds = _evidence_kinds(text)
        if (NO_EXTRACTION.search(text) and not kinds
                or re.match(r"\s*(?:不要|不用|别|取消|停止|先不|不需要)", text)
                and not re.search(r"[，,；;]\s*(?:只|先|但|看下|看看|麻烦|帮我)|只(?:想)?(?:要|看|留)", text)):
            return {"message": "已保留原数据。"}
        # 一般收藏、摘要、管理操作走原有处理；提取目标只看原话中请求的对象。
        if re.search(r"(?:只|仅)(?:需|要|用|想)?(?:存|保存|留|登记|录入|收藏|记)?(?:下|一下)?(?:论文)?(?:标题|链接|摘要|方法)", text):
            return None
        if not kinds and not bare and not (self.recent_evidence and REPEAT_SCOPE.search(text)):
            paper_related = (REFERENCE.search(message) or PAPER_CONTEXT.search(text)
                             or PREVIOUS_REF.search(text) and (self.recent_evidence or self.last))
            if not paper_related:
                return None
            if ORDINARY_CONTENT.search(text) and not re.search(r"过目|确认后|核对后|先.{0,4}(?:看|核对|预览)", text):
                return None
            if PLAIN_SAVE.search(text) and not PREVIEW_CUES.search(text):
                return None
        urls = _evidence_urls(message)
        if urls and kinds:
            grouped = _kinds_by_paper(text, urls)
            positions = _preview_positions(text, urls)
            if positions == []:
                return self._ask_evidence_papers(urls, kinds)
            if grouped:
                urls, kinds = list(grouped), grouped
            elif positions is not None:
                urls = [urls[index] for index in positions]
            if grouped == {}:
                return self._ask_evidence_scope(urls, INCOMPLETE_EVIDENCE_SCOPE)
        previous = False
        if not kinds and self.recent_evidence and REPEAT_SCOPE.search(text):
            kinds = self.recent_evidence[1]
            previous = True
        if not kinds and not bare and model_support.enabled():
            from evidence_intent import classify
            decision = classify(message, self.table_name)
            if decision:
                kinds = decision["kinds"]
                previous = decision["use_previous"]
        if not urls and not re.search(r"换一篇|其他(?:的)?论文|另(?:外)?一篇|别的论文|新论文", text):
            urls = self._previous_evidence_urls(text, suggested=previous or bool(kinds and self.recent_evidence))
        destination = _destination(message)
        current_names = {self.table_name, "当前表", "本表", "这张表", "表", "飞书表", "飞书多维表", "清单"}
        if destination.endswith(("表", "清单")) and destination not in current_names:
            return {"message": f"当前连接的是「{self.table_name}」。请切换到目标表，或明确使用当前表。"}
        return self._start_evidence(urls, kinds) if kinds else self._ask_evidence_scope(urls)

    def _commit(self, plan):
        paper = plan["paper"]
        schema = self._read_schema()
        guard = plan.get("fill_guard")
        if guard and any(field not in schema for field in guard["schema"]):
            raise ValueError("目标字段已变化，请重新查看这篇论文的缺项。")
        for slot in plan["slots"]:
            if slot.get("skip"):
                continue
            if slot.get("field_id"):
                current = next((f for f in schema if f["id"] == slot["field_id"]), None)
                if not current or current["type"] != slot["field_type"] or current["name"] != slot["field_name"]:
                    slot.pop("field_id")
                else:
                    continue
            matches = _fields(schema, slot["kind"], slot["hint"])
            if slot["optional"] and len(matches) != 1:
                slot["skip"] = True
                continue
            if len(matches) != 1:
                if not matches:
                    allowed = {"text", "url"} if slot["kind"] == "url" else {"text"}
                    matches = [f for f in schema if f["type"] in allowed]
                if not matches:
                    raise ValueError("当前表缺少可写的对应类型字段。可以先说“新建文本列 方法概述”。")
                slot["candidates"] = {f["name"]: f for f in matches}
                return self._ask(plan, f"要把{LABEL[slot['kind']]}写入哪一列？选定前不会修改记录。",
                                 list(slot["candidates"]), "field")
            field = matches[0]
            slot.update(field_id=field["id"], field_name=field["name"], field_type=field["type"])
        kinds = {s["kind"] for s in plan["slots"] if not s.get("skip")}
        version_fields = {}
        if kinds & {"method", "abstract"} and paper["key"].startswith("arxiv:"):
            schema_types = {f["name"]: f["type"] for f in schema}
            key = _identity(paper["key"])
            with self.table._lock:
                if hasattr(self.table, "_locate"):
                    existing = self.table._locate(key, schema_types, {}, check_duplicates=True)
                else:
                    found = self.table._db.execute("SELECT id FROM demo_papers WHERE paper_key=?", (key,)).fetchone()
                    existing = self.table.get(found[0]) if found else None
                if existing:
                    for field, address in existing["fields"].items():
                        if not isinstance(address, str) or _identity(address) != key:
                            continue
                        pinned = source_library.arxiv_revision(address)
                        if not pinned:
                            continue
                        for kind in kinds & {"method", "abstract"}:
                            source = paper.get(kind + "_url")
                            if source and source_library.arxiv_revision(source) != pinned:
                                actual = source_library.arxiv_revision(source) or "未标明版本的来源"
                                raise ValueError(f"当前记录固定为 arXiv {pinned}，本次{LABEL[kind]}来自 {actual}。"
                                                 "请使用当前记录所列版本的原文。")
                        version_fields[field] = address
        if not plan["english"] and kinds & {"abstract", "method"}:
            paper = model_support.enrich(paper, kinds & {"abstract", "method"})
        values, evidence = {}, []
        for slot in plan["slots"]:
            if slot.get("skip"):
                continue
            kind, field = slot["kind"], slot["field_name"]
            if kind in {"method", "abstract"}:
                value = (papers.english_excerpt(paper.get(kind + "_original", "")) if plan["english"] else
                         paper.get("abstract_zh" if kind == "abstract" else "method"))
                if not value:
                    if paper.get(kind + "_original") and not plan["english"]:
                        return self._ask(plan, "已找到英文原文。要直接把英文摘录写入所选列吗？",
                                         ["使用英文摘录", "取消"], "language")
                    raise ValueError(f"本次没有取得足以填写「{field}」的原文证据。已保留表中原值。")
                evidence.append({"field": field, "source": paper.get(kind + "_source", ""),
                                 "quote": paper.get(kind + "_quote", ""), "url": paper.get(kind + "_url", "")})
            else:
                value = plan.get("literal") if kind == "note" else paper.get(kind)
                if not value and slot["optional"]:
                    continue
                if not value:
                    raise ValueError(f"论文来源没有提供「{field}」所需内容。")
            if field in values and values[field] != value:
                raise ValueError(f"两项不同内容都被映射到「{field}」。请分别指定两个目标列。")
            values[field] = value
        answer = {}
        if plan["download"]:
            with step("获取论文原文 PDF", tool="论文解析"):
                path = papers.download_pdf(paper, self.data_dir)
            answer["download_url"] = "/api/pdf?" + urlencode({"paper": paper["url"]})
            answer["message"] = f"已下载原文 PDF（{path.stat().st_size:,} 字节）。"
        if values:
            with step("提交前核对目标字段和内容来源", tool="写前核验"):
                checks = verify_write(self.table, paper, plan, values)
            options = {"update_only": True} if plan.get("update_only") else {}
            if version_fields:
                expected_id = source_library.version_target_id(self.table, existing["id"], plan["request_id"])
                options.update(expected_record_id=expected_id, expected_fields=version_fields)
            target_key = plan.get("target_key") or paper["key"]
            if guard:
                if not any(_identity(paper["url"]) == _identity(url) for url in guard["sources"]):
                    raise ValueError("原文来源与已存论文不一致，请先核对记录。")
                target_key = _identity(guard["anchor"])
                options.update(update_only=True, expected_record_id=guard["record_id"],
                               expected_fields={**version_fields, **guard["before"]})
            if plan.get("append_note"):
                schema_by_name = {f["name"]: f["type"] for f in schema}
                with self.table._lock:
                    if hasattr(self.table, "_locate"):
                        existing = self.table._locate(_identity(target_key), schema_by_name, {}, check_duplicates=True)
                    else:
                        found = self.table._db.execute("SELECT id FROM demo_papers WHERE paper_key=?",
                                                       (_identity(target_key),)).fetchone()
                        existing = self.table.get(found[0], paper_key=target_key) if found else None
                    if not existing:
                        return self._ask(plan, "这篇还未收藏。请指定已有论文，或先收藏再补笔记。", [], "paper")
                    existing = self.table.get(existing["id"], paper_key=target_key)
                    field, note = next(iter(values.items()))
                    old = existing["fields"].get(field, "")
                    if note not in old.split("\n\n"):
                        values[field] = old + "\n\n" + note if old else note
                    else:
                        values[field] = old
                    expected = {f["name"]: existing["fields"][f["name"]] for f in schema
                                if f["name"] in existing["fields"] and isinstance(existing["fields"][f["name"]], str)
                                and _identity(existing["fields"][f["name"]]) == _identity(target_key)}
                    expected[field] = old
                    options.update(expected_record_id=existing["id"], expected_fields=expected)
            try:
                with step("按论文标识查重并提交记录", tool=self.table_tool):
                    write = self.table.upsert(target_key, values, plan["request_id"], **options)
                with step("按记录 ID 回读并核对字段值", tool=self.table_tool):
                    row = self.table.get(write["record_id"], paper_key=target_key)
                    if any(row["fields"].get(k) != v for k, v in values.items()):
                        raise ValueError("写入已提交，但当前回读值不一致。请刷新表格核对。")
                    if any(row["fields"].get(k) != v for k, v in version_fields.items()):
                        raise ValueError("回读时论文版本已发生变化，请核对目标记录。")
            except RecordNotFound:
                if not plan.get("update_only"):
                    raise
                # 飞书可能在更新时才发现记录被删除，下一次选择不能沿用绑定旧行的请求。
                plan["request_id"] = uuid.uuid4().hex
                return self._ask(plan, "在当前表可见的记录中未找到这篇论文，没有新增记录。请发另一条已有记录的论文链接或编号。",
                                 [], "paper")
            action = {"created": "已新增 1 条记录", "updated": "已更新原有记录", "unchanged": "已存在相同内容，未新增记录"}[write["action"]]
            wording = "、".join(values)
            answer.update(message=answer.get("message", "") + f"{action}。已核对「{self.table_name}」中的{wording}。",
                          preflight=checks, verification=f"写前核验通过 · 回读一致 · {len(values)} 个字段 · {row['id']}",
                          row=row, evidence=evidence)
            if plan.get("collect"):
                aliases = plan["aliases"]
                source_library.remember(self.table, row, paper, aliases)
                sources = source_library.known_sources(self.table, row, paper["url"])
                metadata = source_library.stored_metadata(self.table, row, paper["url"])
                answer["collection"] = [source_library.card({**paper, **metadata}, sources or aliases, row)]
                if plan.get("version_notice"):
                    answer["collection"][0]["notice"] = plan["version_notice"]
                self.collection_batch = []
        self.pending = None
        self.last = plan["aliases"][0] if plan.get("collect") else paper["url"]
        self.last_title = paper.get("title") or paper["url"]
        self.recent_evidence = None
        self.recent_saved = [self.last]
        if values:
            saved_focus.remember_row(self, row, schema)
        return answer

    def _revisit_local(self, url):
        with self.table._lock:
            if not self.table._db.execute("SELECT 1 FROM demo_papers WHERE paper_key=?",
                                          (_identity(url),)).fetchone():
                return None
            matched = source_library.existing_record(self.table, [url])
            if matched and url == matched[1]:
                row = self.table.get(matched[0]["id"], paper_key=url)
                schema = self._read_schema()
                title = next((row["fields"][f["name"]] for f in _fields(schema, "title")
                              if row["fields"].get(f["name"])), "")
                if title:
                    meta = source_library.stored_metadata(self.table, row, url)
                    sources = source_library.known_sources(self.table, row, url)
                    card = source_library.card({**meta, "title": title, "url": url}, sources or [url], row)
                    filled = [f["name"] for f in schema if row["fields"].get(f["name"])]
                    status = self.table.reading_progress().get(row["id"], "待读")
                    self.last, self.last_title, self.recent_saved = url, title, [url]
                    self.collection_batch, self.pending, self.recent_evidence = [], None, None
                    saved_focus.remember_row(self, row, schema)
                    card["saved_summary"] = f"{status} · 已有{'、'.join(filled)}"
                    return {"message": f"已收藏《{title}》，继续使用原记录。{card['saved_summary']}。可直接给这篇补笔记或预览图表。",
                            "verification": "已按论文标识回读本地原记录", "row": row, "collection": [card]}
        return None

    def _load(self, message, plan):
        urls = _evidence_urls(message)
        if len(urls) != 1:
            raise ValueError("请明确指定一篇要操作的论文。")
        url = urls[0]
        plan.pop("target_key", None)
        # 重访本地清单的原链接时，直接核对已有行；新增入口或不同修订仍需解析。
        if (isinstance(self.table, LocalTable) and not plan.get("download") and not plan.get("update_only")
                and any(s["kind"] == "url" for s in plan["slots"])
                and all(not s.get("hint") and (s["kind"] == "url" or s.get("optional") and s["kind"] == "title")
                        for s in plan["slots"])):
            cached = self._revisit_local(url)
            if cached:
                return cached
        if plan.get("update_only"):
            # 修改用户指定的单元格只需定位论文，直接使用链接或编号，无需抓取论文内容。
            url = source_library.existing_source(self.table, [url]) or url
            paper = {"key": _identity(url), "url": url, "title": self.last_title if url == self.last else ""}
        else:
            need_content = any(s["kind"] in {"method", "abstract"} for s in plan["slots"])
            with step("读取论文原文与出处" if need_content else "读取论文基本信息", tool="论文解析"):
                paper = papers.resolve_paper(url, self.data_dir, need_content=need_content)
            from paper_attachments import bound_file
            local = bound_file(url) if url.lower().startswith("https://") else None
            if local and not paper.get("title"):
                paper = {**paper, "title": local[0]["title"]}
            aliases = list(dict.fromkeys([url, paper["url"], *(paper.get("aliases") or [])]))
            plan["collect"] = bool(plan["slots"]) and all(s["kind"] in {"url", "title"} for s in plan["slots"]) and any(
                s["kind"] == "url" and not s.get("hint") for s in plan["slots"])
            plan["aliases"] = aliases
            if plan["collect"] or len(aliases) > 1:
                with self.table._lock:
                    matched = source_library.existing_record(self.table, aliases)
                    if matched:
                        existing, anchor = matched
                        plan["target_key"] = _identity(anchor)
                        if plan["collect"]:
                            pinned = source_library.arxiv_revision(anchor)
                            current = source_library.arxiv_revision(paper.get("pdf_url") or paper["url"])
                            if pinned and pinned != current:
                                title = next((existing["fields"][f["name"]] for f in _fields(self._read_schema(), "title")
                                              if existing["fields"].get(f["name"])), "")
                                paper = source_library.pinned_metadata(self.table, existing, anchor, title)
                                plan["version_notice"] = (f"本次入口为 {current or '未指定修订号的 arXiv 版本'}，"
                                                          f"已作为来源收藏。当前记录和全文仍指向 {pinned}。")
                            else:
                                paper = {**paper, "url": anchor, "key": _identity(anchor)}
                            if isinstance(self.table, LocalTable):
                                titles = _fields(self._read_schema(), "title")
                                if len(titles) == 1 and existing["fields"].get(titles[0]["name"]):
                                    paper["title"] = existing["fields"][titles[0]["name"]]
                    plan["paper"] = paper
                    return self._commit(plan)
        plan["paper"] = paper
        return self._commit(plan)

    def _fill_missing(self, urls, request_id):
        if len(urls) != 1:
            return {"message": "请指定一篇已收藏论文的链接，再补齐缺项。"}
        with self.table._lock:
            match = source_library.existing_record(self.table, urls)
            if not match:
                return {"message": "当前表里没有这篇论文，请先收藏。"}
            row, anchor = match
            schema = self._read_schema()
            slots, watched = [], []
            before = {name: val for name, val in row["fields"].items() if val == anchor}
            for kind in ("abstract", "method"):
                fields = _fields(schema, kind)
                if len(fields) > 1:
                    return {"message": f"表中有多个{LABEL[kind]}候选列，请用“把{LABEL[kind]}写进指定列”明确目标。"}
                if fields and not row["fields"].get(fields[0]["name"]):
                    field = fields[0]
                    watched.append(field)
                    before[field["name"]] = row["fields"].get(field["name"], "")
                    slots.append({"kind": kind, "hint": field["name"], "optional": False})
            if not slots:
                return {"message": "这条记录没有可补写的空白摘要或方法列。已有内容已保留。", "row": row}
            sources = [anchor, *source_library.known_sources(self.table, row, anchor)]
        guard = {"record_id": row["id"], "anchor": anchor, "before": before, "schema": watched, "sources": sources}
        return self._load(anchor, {"slots": slots, "download": False, "english": False, "fill_guard": guard,
                                   "request_id": request_id or uuid.uuid4().hex})

    def _collect_batch(self, urls, request_id):
        if len(urls) == 1:
            return self._load(urls[0], {**_parse("收藏", self.table_name), "request_id": request_id or uuid.uuid4().hex})
        batch = request_id or uuid.uuid4().hex
        cards, references = {}, {}
        saved = 0
        for index, url in enumerate(urls):
            try:
                item = self._load(url, {**_parse("收藏", self.table_name), "request_id": f"{batch}/{index}"})
                if not item.get("row"):
                    raise ValueError(item["message"])
                card = item["collection"][0]
                key = card["record_id"]
                cards[key] = card
                references.setdefault(key, url)
                saved += 1
            except (TableError, ValueError, OSError) as error:
                cards[url] = {"url": url, "title": url, "error": str(error)}
                references[url] = url
        self.last = self.last_title = self.pending = self.recent_evidence = None
        saved_focus.clear(self)
        # “第几篇”按实际展示的论文卡片排序，不能指向已合并的重复入口。
        self.recent_saved = list(references.values())
        self.collection_batch = list(self.recent_saved)
        count = sum(bool(card.get("record_id")) for card in cards.values())
        return {"message": f"已收藏 {saved}/{len(urls)} 个来源，对应 {count} 条论文记录。",
                "collection": list(cards.values())}

    def _collect_selection(self, text, urls, request_id):
        """明确的排序选择只处理相应链接，有筛选条件时保留原批次供用户选择。"""
        from paper_selection import collection_positions
        positions = collection_positions(text, len(urls), self.table_name)
        if positions:
            return self._collect_batch([urls[i] for i in positions], request_id)
        choices = [f"第{i}篇" for i in range(1, len(urls) + 1)]
        numbered = "\n".join(f"{n}. {url}" for n, url in enumerate(urls, 1))
        return self._ask({"urls": urls}, "要收藏哪些来源？请指定序号或选择全部。\n" + numbered,
                         [*choices, "全部收藏", "取消"], "collection_choice")

    def _prepare_admin(self, command):
        from admin_dialog import prepare
        if command["op"] == "create":
            kind, name = command["type"], command["name"]
            label = "文本" if kind == "text" else "链接"
            return self._ask({"name": name, "kind": kind},
                             f"将在「{self.table_name}」增加{label}列「{name}」，确认吗？",
                             ["确认建列", "取消"], "schema")
        if command["op"] == "unclear_edit":
            return self._ask(command, f"你想把整列「{command['target']}」改名为「{command['new_value']}」，"
                             f"还是只把一篇论文在该列的值改为「{command['new_value']}」？",
                             ["修改整列的名称", "填写一条记录", "取消"], "edit_target")
        labels = {"columns": "查询数据表字段", "column": "查询字段定义", "role_list": "查询自定义角色",
                  "role_read": "查询角色权限", "member_read": "查询协作者权限"}
        with step(labels.get(command["op"], "读取当前设置并预览变更"), tool=self.table_tool):
            task = prepare(self.table, command)
        if "answer" in task:
            self.pending = None
            return {"message": task["answer"]}
        if "ask" in task:
            task["command"] = command
            return self._ask(task, task["ask"], task["choices"], "admin_field")
        return self._ask(task, f"当前连接的数据表为「{self.table_name}」。{task['message']}", ["确认变更", "取消"], "admin_commit")

    def _clarify_intent(self, url, title):
        if not url:
            return self._ask({}, "你指的是哪篇论文？请发链接、DOI 或 arXiv 编号，我会先确认要写的内容。", [], "ambiguous_paper")
        return self._ask({"url": url}, f"你指的是《{title or url}》吗？要把它的什么内容写进「{self.table_name}」？",
                         ["保存论文链接", "填写摘要", "填写方法", "换一篇论文", "取消"], "ambiguous_intent")

    def respond(self, message, pending_id=None, request_id=None, evidence_selection=None, evidence_fields=None):
        with self.lock:
            if evidence_selection is not None or evidence_fields is not None:
                choice = EVIDENCE_DECISION.fullmatch(message)
                if not pending_id or not choice or not choice[1].startswith("确认") or (choice[2] and choice[2] != pending_id):
                    raise ValueError("请在本批预览中选择提取项，并使用对应的确认按钮。")
                if self.pending and self.pending["stage"] != "evidence_confirm":
                    raise ValueError("当前没有可逐项确认的证据预览。")
            if pending_id and self.pending and pending_id != self.pending["id"]:
                raise ValueError("上一项确认已经结束，请使用当前预览。")
            if self.last_library_results:
                from library_search import is_result_reference, open_result
                if is_result_reference(message):
                    opened = open_result(self, message, pending_id)
                    if opened is not None:
                        return opened
                else:
                    self.last_library_results = None
            literal_note = NOTE_TEXT.fullmatch(message.strip())
            has_note_body = literal_note or QUOTED_NOTE.search(message)
            if (isinstance(self.table, LocalTable) and not self.pending and not pending_id and not has_note_body
                    and re.fullmatch(r"(?:请|帮我)?补齐(?:刚才那篇|这篇)?(?:缺失的?|空白的?)?摘要和方法[。！! ]*",
                                     EVIDENCE_REFERENCES.sub(" ", message).strip())):
                return self._fill_missing(_evidence_urls(message) or ([self.last] if self.last else []), request_id)
            if (isinstance(self.table, LocalTable) and not self.pending and not pending_id
                    and not has_note_body and re.fullmatch(r"(?:收藏|保存|收录)[。！! ]*", EVIDENCE_REFERENCES.sub(" ", message).strip())):
                urls = _evidence_urls(message)
                if len(urls) == 1:
                    cached = self._revisit_local(urls[0])
                    if cached:
                        return cached
            if not has_note_body:
                from workspace_actions import handle_workspace
                workspace = handle_workspace(self, message, pending_id)
                if workspace is not None:
                    return workspace
            if not getattr(self.table, "table_id", True):
                return {"message": "请先创建一个多维表格文件，例如“新建多维表格「论文资料库」”。"}
            from intent_guard import guard_paper_request
            from admin_dialog import infer_table_lookup, table_lookup_kind
            if not has_note_body and not REFERENCE.search(message) and (self.pending or not pending_id):
                from saved_compare import handle as compare_saved
                comparison = compare_saved(self, message)
                if comparison is not None:
                    return comparison
                from saved_paper_dialog import handle as revisit_by_title
                saved = revisit_by_title(self, message, pending_id)
                if saved is not None:
                    return saved
            can_query = not has_note_body and (self.pending or not pending_id) and (
                not self.pending or message not in self.pending.get("choices", ()))
            lookup = table_lookup_kind(message, self.table_name) if can_query else None
            if (not has_note_body and not REFERENCE.search(message) and (self.pending or not pending_id)
                    and (not lookup or (self.pending and self.pending["stage"] == "search_source"))):
                from library_search import handle as search_library_or_catalog
                result = search_library_or_catalog(self, message, pending_id)
                if result is not None:
                    return result
            if not lookup:
                guarded = guard_paper_request(self, message, pending_id, request_id)
                if guarded is not None:
                    return guarded
            if can_query:
                progress_query = re.fullmatch(r"(?:看看|列出|查看|查一下|我想看)?(?:我)?(待读|还没读|未读|在读|正在读|已读|读完了?)(?:的)?(?:论文|文献)(?:有哪些)?[？?。]?", message)
                if isinstance(self.table, LocalTable) and progress_query:
                    label = progress_query[1]
                    status = ("待读" if label in ("还没读", "未读") else "在读" if label == "正在读"
                              else "已读" if label.startswith("读完") else label)
                    columns = self._read_schema()
                    names = [f["name"] for role in ("title", "url") for f in _fields(columns, role)]
                    with self.table._lock:
                        progress = self.table.reading_progress()
                        titles = [next((r["fields"][n] for n in names if r["fields"].get(n)), "")
                                  for r in self.table.rows() if progress.get(r["id"], "待读") == status]
                    titles = [t for t in titles if t]
                    reply = {"message": f"「{self.table_name}」有 {len(titles)} 篇{status}论文。" +
                             "".join(f"\n{i}. {t}" for i, t in enumerate(titles[:20], 1)) +
                             ("\n显示前 20 篇。" if len(titles) > 20 else "")}
                    if self.pending:
                        reply.update(pending_id=self.pending["id"], choices=self.pending.get("choices", []))
                    return reply
                lookup = lookup or infer_table_lookup(message, self.table_name)
                # 原有模型字段查询保留其规划、工具追踪；离线模式也能回答相同的口语提问。
                from agent_router import is_candidate
                model_fields = lookup == "columns" and not self.pending and is_candidate(message) and model_support.enabled()
                if lookup and not model_fields:
                    answer = {"message": self._table_lookup(lookup)}
                    if self.pending:
                        answer["pending_id"] = self.pending["id"]
                    return answer
            if re.fullmatch(r"(?:取消(?:吧|此次操作|本次操作)?|算了|不用了|先不写|先[别不](?:回填|提交)了?|停止)[。！!]?", message):
                self.pending = None
                return {"message": "已取消，表格保持原样。"}
            if self.pending:
                task = self.pending
                stage, plan = task["stage"], task["plan"]
                if resolve_choice(message, stage, ["取消"]) == "取消":
                    self.pending = None
                    return {"message": "已取消，表格保持原样。"}
                if stage in {"paper", "single_link"}:
                    wording = EVIDENCE_REFERENCES.sub(" ", message)
                    if DECLINED_WRITE.search(wording) or re.search(
                            r"[?？]|(?:要|可|行|对)吗|是不是|还是说|先别|别存|别写|不要|不用|不选|不是", wording):
                        return self._ask(plan, "请直接指定要处理的那篇论文，或选「取消」。", ["取消"], stage)
                if stage in ("single_link", "paper") and re.search(r"不要|先别|别存|暂不|算了|取消", message):
                    self.pending = None
                    return {"message": "已取消，表格保持原样。"}
                if stage == "evidence_urls":
                    if NO_EXTRACTION.search(message):
                        self.pending = None
                        return {"message": "已取消提取。"}
                    if LINK_ONLY.fullmatch(message):
                        return self._ask({"urls": []}, "要收藏哪一篇？请发一个论文链接。", [], "single_link")
                    urls = _evidence_urls(message) or self._previous_evidence_urls(message)
                    if not urls:
                        return self._ask(plan, task["message"], [], stage)
                    return self._preview_evidence(urls, _evidence_kinds(EVIDENCE_REFERENCES.sub(" ", message)) or plan["kinds"])
                if stage == "evidence_papers":
                    urls = plan["urls"]
                    if message == "全部重新预览":
                        return self._preview_evidence(urls, plan["kinds"])
                    positions = paper_positions(message, len(urls), exclude=True)
                    if not positions:
                        return self._ask(plan, task["message"], task["choices"], stage)
                    return self._preview_evidence([urls[i] for i in positions], plan["kinds"])
                if stage == "evidence_scope":
                    text = EVIDENCE_REFERENCES.sub(" ", message)
                    explicit = _evidence_urls(message)
                    candidates = explicit or plan["urls"]
                    grouped = _kinds_by_paper(text, candidates)
                    positions = _preview_positions(text, candidates)
                    if positions == [] and candidates:
                        return self._ask_evidence_papers(candidates, _evidence_kinds(text) or list(STANDARD_EVIDENCE))
                    if grouped:
                        return self._preview_evidence(list(grouped), grouped)
                    urls = [candidates[i] for i in positions] if positions else explicit or self._previous_evidence_urls(
                        message, suggested=True, candidates=plan["urls"] or None)
                    if grouped == {}:
                        return self._ask_evidence_scope(urls, INCOMPLETE_EVIDENCE_SCOPE)
                    if re.fullmatch(r"(?:全部|全都|都)(?:先)?(?:收藏|收下|保存)(?:起来|下来|吧)?[。！!]?", text.strip()) and urls:
                        self.pending = None
                        return self._collect_batch(urls, request_id)
                    if LINK_ONLY.fullmatch(text.strip()):
                        if len(urls) == 1:
                            self.pending = None
                            return self.respond("保存论文链接 " + urls[0], request_id=request_id)
                        return self._ask({"urls": plan["urls"]}, "要收藏哪一篇？请发一个论文链接或指定第几篇。", [], "single_link")
                    kinds = _evidence_kinds(text)
                    if NO_EXTRACTION.search(text) and not kinds:
                        self.pending = None
                        return {"message": "已取消提取。"}
                    if not kinds and re.fullmatch(r"(?:好[的啊]?|可以|全部|都(?:要|看|行)|都看看|先看看)[。！!]?", message):
                        kinds = list(STANDARD_EVIDENCE)
                    return self._start_evidence(urls, kinds) if kinds else self._ask_evidence_scope(urls)
                if stage == "collection_choice":
                    if pending_id != task["id"]:
                        return self._ask(plan, task["message"], task["choices"], stage)
                    if message == "全部收藏":
                        return self._collect_batch(plan["urls"], request_id)
                    if (paper_positions(message, len(plan["urls"])) and re.fullmatch(
                            r"(?:我选|就选|选择|选|只(?:要|收藏)|收藏)?[、，,和与及\s]*(?:就好|吧)?[。！!]?",
                            without_positions(message).strip())):
                        return self._collect_selection(message, plan["urls"], request_id)
                    return self._ask(plan, task["message"], task["choices"], stage)
                if stage == "single_link":
                    if len(ORDINAL.findall(message)) > 1:
                        return self._ask(plan, "请只指定一篇要收藏的论文。", ["取消"], stage)
                    urls = _evidence_urls(message) or self._previous_evidence_urls(message, candidates=plan["urls"])
                    if len(urls) != 1:
                        return self._ask(plan, task["message"], [], stage)
                    self.pending = None
                    return self.respond("保存论文链接 " + urls[0], request_id=request_id)
                if stage == "evidence_confirm":
                    action = EVIDENCE_DECISION.fullmatch(message)
                    if not action:
                        return self._revise_evidence(message, task)
                    if (action[2] or pending_id) != task["id"]:
                        raise ValueError("请使用这批论文预览下方的确认或取消按钮。")
                    if action[1] == "取消回填":
                        self.pending = None
                        return {"message": "已取消本批回填。"}
                    from evidence_workflow import commit
                    if evidence_selection is None and evidence_fields is None:
                        result = commit(self.table, plan)
                    else:
                        result = commit(self.table, plan, selection=evidence_selection, field_overrides=evidence_fields)
                    self.last_evidence_receipt = (task["id"], result)
                    self.pending = None
                    return result
                if stage == "ambiguous_paper" or (stage == "ambiguous_intent" and REFERENCE.search(message)):
                    urls = _evidence_urls(message)
                    if len(urls) != 1:
                        return self._ask({}, "请指定一篇论文，发送它的链接、DOI 或 arXiv 编号。", [], "ambiguous_paper")
                    with step("读取论文基本信息", tool="论文解析"):
                        source = papers.resolve_paper(urls[0], self.data_dir)
                    return self._clarify_intent(source["url"], source.get("title"))
                if stage == "paper":
                    if self.last and re.fullmatch(r"(?:刚才|上一|之前|上面)(?:的)?(?:那|这)?(?:一)?篇(?:论文)?[。！]?", message):
                        return self._load(self.last, plan)
                    urls = _evidence_urls(message)
                    if len(urls) != 1:
                        return self._ask(plan, "请指定一篇要处理的论文，发送它的链接、DOI 或 arXiv 编号。", [], stage)
                    return self._load(urls[0], plan)
                if stage in {"title_choice", "title_selected"}:
                    return paper_search_dialog.followup(self, message, task, pending_id, request_id)
                choice = resolve_choice(message, stage, task["choices"])
                if choice not in task["choices"]:
                    return self._ask(plan, task["message"], task["choices"], stage)
                if stage == "edit_target":
                    if choice == "修改整列的名称":
                        return self._prepare_admin({"op": "rename", "target": plan["target"], "new_name": plan["new_value"]})
                    resolved = {"download": False, "english": False, "literal": plan["new_value"], "update_only": True,
                                "request_id": request_id or uuid.uuid4().hex,
                                "slots": [{"kind": "note", "hint": plan["target"], "optional": False}]}
                    return self._ask(resolved, "要修改哪篇论文的这一个单元格？请给论文链接、DOI、arXiv 编号，或说“刚才那篇”。",
                                     [], "paper")
                if stage == "ambiguous_intent":
                    if choice == "换一篇论文":
                        return self._clarify_intent(None, None)
                    resolved = _parse(choice, self.table_name)
                    resolved["request_id"] = request_id or uuid.uuid4().hex
                    return self._load(plan["url"], resolved)
                if stage in ("admin_field", "admin_commit") and pending_id != task["id"]:
                    raise ValueError("这项管理操作需要对应的确认，请点击当前对话中的选项。")
                if stage == "admin_field":
                    return self._prepare_admin({**plan["command"], "target": plan["candidates"][choice]})
                if stage == "admin_commit":
                    from admin_dialog import commit
                    self.pending = None
                    actions = {"field": "提交并核对字段变更", "member": "提交并核对协作者权限",
                               "role": "提交并核对角色权限"}
                    with step(actions[plan["kind"]], tool=self.table_tool):
                        message = commit(self.table, plan)
                    return {"message": message, "verification": "写前已核验变更对象与原值，写后已回读"}
                if stage == "schema":
                    if pending_id != task["id"]:
                        raise ValueError("请使用当前对话的建列确认选项。")
                    with step("提交新建字段请求", tool=self.table_tool):
                        f = self.table.add_field(plan["name"], plan["kind"])
                    self.pending = None
                    return {"message": f"已确认表结构，字段「{f['name']}」可以使用。", "verification": "已回读字段定义"}
                if stage == "language":
                    plan["english"] = True
                elif stage == "field":
                    slot = next(s for s in plan["slots"] if "candidates" in s and not s.get("field_id"))
                    field = slot.pop("candidates")[choice]
                    slot.update(field_id=field["id"], field_name=field["name"], field_type=field["type"])
                return self._commit(plan)
            if self.last_evidence_receipt:
                completed_id, receipt = self.last_evidence_receipt
                decision = EVIDENCE_DECISION.fullmatch(message)
                if (decision and decision[1].startswith("确认") and (decision[2] or pending_id) == completed_id
                        and pending_id in (None, completed_id)):
                    return receipt
            if pending_id:
                raise ValueError("这项确认已结束。请重新发起指令。")
            if EVIDENCE_DECISION.fullmatch(message):
                raise ValueError("当前对话没有这批待确认数据，请重新提取并预览。")
            wording = EVIDENCE_REFERENCES.sub(" ", message)
            if literal_note:
                head = literal_note["head"]
                if DECLINED_WRITE.search(head) or re.search(r"(?:别|不要|不用|先不|暂不).{0,12}(?:记|补|写|加)", head):
                    return {"message": "已保留原笔记。"}
                field = "研究笔记" if "研究笔记" in head else "阅读笔记"
                plan = {"slots": [{"kind": "note", "hint": field, "optional": False}],
                        "append_note": True, "download": False, "english": False, "literal": literal_note["body"],
                        "update_only": True, "request_id": request_id or uuid.uuid4().hex}
                urls = self._note_targets(head)
                if len(urls) == 1:
                    return self._load(urls[0], plan)
                return self._ask(plan, "要给哪篇论文追加这条笔记？请发已收藏的论文链接或 DOI。", [], "paper")
            if SAVED_QUESTION.search(wording):
                return self._saved_status(message)
            if re.search(r"(?:查看|看看|查一下|有哪些|显示|列出|打开).{0,16}(?:收藏来源|论文来源|来源入口|来源链接)|"
                         r"(?:论文|这篇).{0,10}有哪些来源", wording):
                return self._show_sources(_evidence_urls(message) or self._previous_evidence_urls(wording, suggested=True))
            if re.fullmatch(r"(?:请|帮我)?(?:列出|查看|查询|看下)(?:一下|下)?(?:当前|这张|本)?(?:数据表|表格|表)(?:的)?结构[。！!?？]?", wording.strip()):
                return self._prepare_admin({"op": "columns"})
            if re.match(r"^(?:请问|我想知道|讲讲|告诉我)?\s*(?:如何|怎么才能|怎样|要怎么|能否介绍|怎么把|怎么将)", message):
                return {"message": "可以先说“查看字段”或“查看角色「论文审阅人」的权限”。需要执行时，请直接指定目标和新值。此处仅说明用法。"}
            if not REFERENCE.search(message) and (query := paper_search_dialog.title_request(message)):
                return paper_search_dialog.start(self, *query)
            if PLAIN_SAVE.search(wording) and not (DECLINED_WRITE.search(wording) or
                    re.search(r"[?？]|要不要|能否|可不可以|是否|删除|清空|建列|权限", wording)):
                urls = _evidence_urls(message)
                remainder = re.sub(r"(?:暂时|暂|先)?(?:不用|不要|先不|不|别).{0,4}(?:提取|分析|读全文|提数据|整理)(?:了|图表|数据)?", "", wording)
                if len(urls) > 1 and not _evidence_kinds(remainder) and not re.search(r"摘要|方法|笔记|预览|核对", remainder):
                    _parse(remainder, self.table_name)
                    return self._collect_selection(remainder, urls, request_id)
            evidence = self._route_evidence(message)
            if evidence is not None:
                return evidence
            if DECLINED_WRITE.search(wording) or re.search(r"别写|不要|不要改|别存|先别|暂不|不写入|只预览|不用提取|取消提取", message):
                return {"message": "已保留原数据。需要写入时，请发论文链接和目标字段。"}
            from admin_dialog import parse_admin
            if model_support.agent_info()["enabled"]:
                from agent_router import is_candidate, route
                if is_candidate(message):
                    routed = route(message, self._read_schema(), self.table_name)
                    if routed:
                        if routed["kind"] == "clarify":
                            return {"message": routed["message"]}
                        return self._prepare_admin(routed["command"])
            command = parse_admin(message)
            if command:
                return self._prepare_admin(command)
            schema_create = re.fullmatch(r"(?:请|帮我)?(?:新增|新建|创建|添加)\s*(文本|链接|网址|URL)\s*(?:列|字段)\s*[：:]?\s*(.{1,40})", message, re.I)
            if schema_create:
                name = _clean(schema_create[2])
                if not name:
                    raise ValueError("请给新字段起一个名字。")
                kind = "text" if schema_create[1] == "文本" else "url"
                return self._prepare_admin({"op": "create", "name": name, "type": kind})
            if re.search(r"删除|清空|授权|改权限|改名|重命名", message):
                return {"message": "请明确要管理的对象。例如“删除字段「临时备注」”或“查看角色「论文审阅人」的权限”。"}
            urls = _evidence_urls(message)
            if len(urls) > 1:
                return {"message": "这条指令含有多个地址。请一次指定一篇论文。"}
            if (not REFERENCE.search(message) and re.search(r"那个|这个|刚才的|上面的|也(?:记|加|放|填|存)进去", message)
                    and re.search(r"记|存|填|写|放|加|录|改", message)
                    and not re.search(r"标题|题名|摘要|方法|笔记|链接|网址|原文|下载", message)):
                return self._clarify_intent(self.last, self.last_title)
            try:
                plan = _parse(message, self.table_name)
            except ValueError:
                if not model_support.enabled():
                    raise
                from intent_model import interpret
                plan = interpret(message, self._read_schema(), self.table_name)
            else:
                if model_support.agent_info()["enabled"]:
                    from intent_model import FIELD_DIRECTION, review_plan
                    columns = None
                    if plan["slots"] and re.search(rf"{FIELD_DIRECTION}|在.{{1,40}}(?:记|写|填|补)", wording):
                        columns = [field["name"] for field in self._read_schema()]
                    review_plan(message, self.table_name, plan, columns=columns)
            plan["request_id"] = request_id or uuid.uuid4().hex
            if urls:
                return self._load(urls[0], plan)
            if self.last and re.search(r"刚才|上[述一]|这[一篇个项]|那[一篇个项]|它|再|顺便|补[充上一写一句]|接着", message):
                return self._load(self.last, plan)
            return self._ask(plan, "要处理哪篇论文？请发论文链接、DOI 或 arXiv 编号。", [], "paper")
