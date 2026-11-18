"""把用户的意图、论文证据和表中真实存在的列连接起来。"""

import re
import threading
import uuid
from urllib.parse import urlencode

import papers
import model_support


ROLES = {
    "url": ("论文链接", "论文地址", "原文地址", "原文链接", "文献链接", "预印本链接", "链接", "url", "link"),
    "title": ("论文标题", "文献标题", "论文名称", "标题", "题名", "题目", "名称", "title", "name"),
    "abstract": ("中文摘要", "论文摘要", "摘要", "简介", "abstract", "summary"),
    "method": ("方法概述", "研究方法", "方法", "技术路线", "method", "approach"),
    "note": ("阅读笔记", "研究笔记", "笔记", "备注", "note"),
}
LABEL = {"url": "论文链接", "title": "论文标题", "abstract": "摘要", "method": "方法", "note": "笔记"}
STORE = r"存|填|写|放|记入|记录|收录|收下|入库|入表|录入|添加|加到|加入|加进|补|登记|导入|保存|收进|更新|同步|收藏"
REFERENCE = re.compile(r"https?://|arxiv\s*[:：]|(?<![\da-z])\d{4}\.\d{4,5}(?!\d)", re.I)


def _clean(value):
    return value.strip().strip("「」『』“”‘’‘”\"'，。、.!！?？ ").strip()


def _normalized(value):
    return re.sub(r"[\s_·-]", "", value).casefold()


def _destination(message):
    hit = re.search(r"(?:写进|写入|填到|填入|填进|补进|补到|补入|放到|放进|记到|记入|存到|存进|保存到)[\s「“\"]*([^，。？！、\n]+?)\s*$", message)
    if not hit:
        return ""
    value = re.sub(r"(?:这|那|一)?[个一]?栏(?:里|中)?$|(?:这|那|一)?列(?:里|中)?$|字段(?:里|中)?$", "", _clean(hit[1]))
    return _clean(value)


def _fields(schema, kind, hint=""):
    valid = [f for f in schema if f["type"] in ({"url", "text"} if kind == "url" else {"text"})]
    if hint:
        exact = [f for f in valid if _normalized(f["name"]) == _normalized(hint)]
        return exact or [f for f in valid if _normalized(hint) in _normalized(f["name"])]
    aliases = ROLES[kind]
    matches = [f for f in valid if _normalized(f["name"]) in [_normalized(a) for a in aliases]]
    if not matches:
        matches = [f for f in valid if any(_normalized(a) in _normalized(f["name"]) for a in aliases)]
    if kind == "url" and not matches:
        matches = [f for f in valid if f["type"] == "url"]
    return matches


def _parse(message, table_name):
    download = bool(re.search(r"下载|保存.{0,3}PDF|取回.{0,3}原文", message, re.I))
    explicit_write = bool(re.search(STORE, re.sub(r"保存.{0,3}PDF", "", message, flags=re.I)))
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
    literal = re.search(r"(?:把|将)\s*[「“\"](.+?)[」”\"]\s*(?:记录|填|写|放|存|记)", message)
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
            "english": bool(re.search(r"英文|原文摘|原文方法|英文原文", message))}


class Conversation:
    def __init__(self, table, data_dir, table_name):
        self.table = table
        self.data_dir = data_dir
        self.table_name = table_name
        self.last = None
        self.pending = None
        self.lock = threading.RLock()

    def _ask(self, plan, message, choices, stage):
        task = self.pending or {"id": uuid.uuid4().hex}
        task.update(plan=plan, stage=stage, message=message, choices=choices)
        self.pending = task
        return {"message": message, "choices": choices, "pending_id": task["id"]}

    def _commit(self, plan):
        paper = plan["paper"]
        schema = self.table.schema()
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
        if not plan["english"] and kinds & {"abstract", "method"}:
            paper = model_support.enrich(paper, kinds & {"abstract", "method"})
        values, evidence = {}, []
        for slot in plan["slots"]:
            if slot.get("skip"):
                continue
            kind, field = slot["kind"], slot["field_name"]
            if kind in {"method", "abstract"}:
                value = (paper.get(kind + "_original") if plan["english"] else
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
            path = papers.download_pdf(paper, self.data_dir)
            answer["download_url"] = "/api/pdf?" + urlencode({"paper": paper["url"]})
            answer["message"] = f"已下载原文 PDF（{path.stat().st_size:,} 字节）。"
        if values:
            write = self.table.upsert(paper["key"], values, plan["request_id"])
            row = self.table.get(write["record_id"])
            if any(row["fields"].get(k) != v for k,v in values.items()):
                raise ValueError("写入已提交，但当前回读值不一致。请刷新表格核对。")
            action = {"created": "已新增 1 条记录", "updated": "已更新原有记录", "unchanged": "已存在相同内容，未新增记录"}[write["action"]]
            wording = "、".join(values)
            answer.update(message=answer.get("message", "") + f"{action}。已核对「{self.table_name}」中的{wording}。",
                          verification=f"回读一致 · {len(values)} 个字段 · {row['id']}", row=row, evidence=evidence)
        self.pending = None
        self.last = paper["url"]
        return answer

    def _load(self, message, plan):
        paper = papers.resolve_paper(message, self.data_dir,
                                     need_content=any(s["kind"] in {"method", "abstract"} for s in plan["slots"]))
        plan["paper"] = paper
        return self._commit(plan)

    def respond(self, message, pending_id=None, request_id=None):
        with self.lock:
            if re.fullmatch(r"(?:取消|算了|不用了|先不写|停止)[。！!]?", message):
                self.pending = None
                return {"message": "已取消，表格保持原样。"}
            if self.pending:
                task = self.pending
                if pending_id and pending_id != task["id"]:
                    raise ValueError("上一项确认已经结束，请刷新后重发指令。")
                stage, plan = task["stage"], task["plan"]
                if stage == "paper":
                    if self.last and re.fullmatch(r"(?:刚才|上一|之前|上面)(?:的)?(?:那|这)?(?:一)?篇(?:论文)?[。！]?", message):
                        return self._load(self.last, plan)
                    if not REFERENCE.search(message):
                        return self._ask(plan, task["message"], [], stage)
                    return self._load(message, plan)
                choice = _clean(message)
                if choice not in task["choices"]:
                    return self._ask(plan, task["message"], task["choices"], stage)
                if stage == "schema":
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
            if pending_id:
                raise ValueError("这项确认已结束。请重新发起指令。")
            if re.search(r"别写|不要|不要改|别存|先别|暂不|不写入|只预览", message):
                return {"message": "已保留原数据。需要写入时，请发论文链接和目标字段。"}
            schema_create = re.fullmatch(r"(?:请|帮我)?(?:新增|新建|创建|添加)\s*(文本|链接|网址|URL)\s*(?:列|字段)\s*[：:]?\s*(.{1,40})", message, re.I)
            if schema_create:
                name = _clean(schema_create[2])
                if not name:
                    raise ValueError("请给新字段起一个名字。")
                kind = "text" if schema_create[1] == "文本" else "url"
                return self._ask({"name": name, "kind": kind},
                                 f"将在「{self.table_name}」增加{schema_create[1]}列「{name}」，确认吗？",
                                 ["确认建列", "取消"], "schema")
            if re.search(r"删除|清空|授权|改权限|改名|重命名", message):
                return {"message": "当前支持新增字段、保存论文和补写记录。此命令未执行。"}
            if len(re.findall(r"https?://", message, re.I)) > 1:
                return {"message": "这条指令含有多个地址。请一次指定一篇论文。"}
            try:
                plan = _parse(message, self.table_name)
            except ValueError:
                if not model_support.enabled():
                    raise
                from intent_model import interpret
                plan = interpret(message, self.table.schema(), self.table_name)
            plan["request_id"] = request_id or uuid.uuid4().hex
            if REFERENCE.search(message):
                return self._load(message, plan)
            if self.last and re.search(r"刚才|上[述一]|这[一篇个项]|那[一篇个项]|它|再|顺便|补[充上一写一句]|接着", message):
                return self._load(self.last, plan)
            return self._ask(plan, "要处理哪篇论文？请发论文链接或 arXiv 编号。", [], "paper")
