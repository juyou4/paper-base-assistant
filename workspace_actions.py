"""在一个对话中预览、创建和切换论文的写入目标。"""

import re

from activity import step
from admin_dialog import table_lookup_kind
from base_table_dialog import _LIST, _LIST_SHORT, parse, revise, supply_name
from table_store import TableError
from tools.feishu_setup import _parse_target


CONFIRM = {"create_table": "确认新建数据表", "create_base": "确认新建多维表格"}
_ACCEPT_PREVIEW = re.compile(r"(?:(?:可以|没问题)[，,]\s*)?(?:就)?按(?:这个|这份|刚才的|当前)(?:方案|预览)?(?:来)?(?:建|创建)(?:吧|就行)?[。！!]?")


def _confirmed(text, label):
    return text.rstrip("。！!") == label or bool(_ACCEPT_PREVIEW.fullmatch(text))


def target_link(table, origin_url):
    if not origin_url or not getattr(table, "table_id", ""):
        return ""
    try:
        base = _parse_target(origin_url)
    except ValueError:
        return ""
    if base.kind != "base" or base.token != getattr(table, "app_token", ""):
        return ""
    if base.table_id != table.table_id:
        base = base._replace(table_id=table.table_id, view_id="")
    return base.address()


def _tables(chat):
    with step("读取当前多维表格的数据表目录", tool=chat.table_tool):
        return chat.table.list_tables()


def _preview_or_switch(chat, command, *, revision=False):
    op = command["op"]
    if op == "clarify":
        if command.get("needs_name"):
            return chat._ask(command, command["message"], ["取消"], "workspace_name")
        return {"message": command["message"]}
    if op != "create_base" and not getattr(chat.table, "table_id", True):
        return {"message": "还没有当前数据表。请先说“新建多维表格「论文资料库」”。"}
    if op == "list_tables":
        tables = _tables(chat)
        lines = [f"· {t['name']}（{t['id']}）" for t in tables]
        return {"message": f"当前数据表是「{chat.table_name}」。此文件内可见 {len(tables)} 张数据表。"
                           + ("\n" + "\n".join(lines) if lines else "")}
    name = command["name"]
    same_target = revision and name == chat.pending["plan"]["name"]
    if op in {"create_table", "switch_table"} and not same_target:
        tables = _tables(chat)
        found = [t for t in tables if t["name"] == name or (op == "switch_table" and t["id"] == name)]
        if op == "switch_table":
            if len(found) != 1:
                return {"message": "请用“列出所有数据表”查看此文件内的名称，然后按名称或数据表 ID 切换。"}
            chosen = found[0]
            with step("打开指定的数据表", tool=chat.table_tool):
                table = chat.table.open_table(chosen["id"])
            return _activate(chat, table, chosen["name"], chat.base_name, chat.base_url)
        if found:
            return {"message": f"当前多维表格已有「{name}」。可以说“切换到数据表「{name}」”，或换一个新表名。"}
    if revision:
        # 新方案使用独立的确认编号，浏览器里的旧按钮随即失效。
        chat.pending = None
    return chat._ask(command, _describe(chat, command) + "\n可以继续调整，例如“再加一列年份”。确认后切换到新表。",
                     [CONFIRM[op], "取消"], "workspace_create")


def _describe(chat, plan):
    name = plan["name"]
    if plan["op"] == "create_base":
        location = f"将新建独立多维表格文件「{name}」，并在里面准备数据表「{plan['table_name']}」。"
    else:
        owner = f"多维表格「{chat.base_name}」" if chat.base_name else f"「{chat.table_name}」所在的多维表格"
        location = f"将在{owner}中新建数据表「{name}」。"
    columns = "、".join(f"{f['name']}（{'超链接' if f['type'] == 'url' else '文本'}）" for f in plan["fields"])
    source = f"（{plan['field_source']}）" if plan.get("field_source") else ""
    return f"{location}\n字段{source}为 {columns}。"


def _activate(chat, table, name, base_name, base_url):
    chat.select_table(table, name, base_name, base_url)
    chat.workspace_receipt = None
    label = f"「{base_name}」的" if base_name else ""
    return {"message": f"当前对话已切换到{label}数据表「{name}」。", "target_changed": True,
            "workspace_url": target_link(table, base_url)}


def handle_workspace(chat, message, pending_id):
    text = message.strip()
    task = chat.pending
    receipt = chat.workspace_receipt
    if not task and receipt and pending_id == receipt[0] and _confirmed(text, receipt[1]):
        return receipt[2]
    if task and task["stage"] in {"workspace_name", "workspace_create"}:
        if re.fullmatch(r"(?:取消(?:创建|建表|这次(?:建表|操作))?|算了|不用了|(?:先)?(?:不|别|不用)建(?:表)?(?:了)?)[吧。！!]*", text):
            chat.pending = None
            return {"message": "已取消。"}
        query = text.rstrip(" 。！？!?？").strip()
        if _LIST.fullmatch(query) or _LIST_SHORT.fullmatch(query):
            info = _preview_or_switch(chat, {"op": "list_tables"})
            return {**info, "pending_id": task["id"], "choices": task["choices"]}
        lookup = table_lookup_kind(text, chat.table_name)
        if lookup:
            if getattr(chat.table, "table_id", True):
                answer = chat._table_lookup(lookup)
            else:
                answer = "当前会话尚未绑定数据表。"
            return {"message": answer, "pending_id": task["id"], "choices": task["choices"]}
        if task["stage"] == "workspace_name":
            cmd = parse(text)
            if cmd is None:
                cmd = supply_name(task["plan"], text)
            if cmd is None:
                return chat._ask(task["plan"], task["message"], task["choices"], task["stage"])
            chat.pending = None
            return _preview_or_switch(chat, cmd)
        plan = task["plan"]
        confirm = CONFIRM[plan["op"]]
        if not _confirmed(text, confirm) or pending_id != task["id"] or confirm not in task["choices"]:
            if not task.get("attempted"):
                try:
                    restore = text == "保留原方案" and text in task["choices"] and pending_id == task["id"]
                    updated = plan if restore else revise(plan, text)
                    if updated is not None and (restore or updated != plan):
                        preview = _preview_or_switch(chat, updated, revision=True)
                        if "pending_id" in preview:
                            return preview
                        raise ValueError(preview["message"])
                except ValueError as error:
                    # 用户要求修改但无法落实时，先撤回确认按钮；可以再改或显式恢复。
                    chat.pending = None
                    return chat._ask(plan, f"{error}\n可以继续调整，或选“保留原方案”重新预览。",
                                     ["保留原方案", "取消"], "workspace_create")
            return chat._ask(plan, task["message"], task["choices"], task["stage"])
        try:
            task["attempted"] = True
            with step("创建并回读新文件和字段" if plan["op"] == "create_base" else "创建并回读数据表和字段",
                      tool=chat.table_tool):
                if plan["op"] == "create_base":
                    from base_files import create_base_file
                    target, url = create_base_file(chat.table, plan["name"], plan["table_name"], plan["fields"], task["id"])
                    chat.owned_bases.append(target)
                    result = _activate(chat, target, plan["table_name"], plan["name"], url)
                else:
                    target = chat.table.create_table(plan["name"], plan["fields"], task["id"])
                    result = _activate(chat, target, plan["name"], chat.base_name, chat.base_url)
        except TableError as error:
            task["message"] = f"{_describe(chat, plan)}\n上次建表尚待核验。{error}\n请先核对目标。需要继续时，请使用这份原确认。"
            raise
        result["verification"] = f"已回读新数据表及 {len(plan['fields'])} 个字段"
        chat.workspace_receipt = (task["id"], confirm, result)
        return result
    if not task and pending_id:
        return None
    if text in {"切回初始表", "切回最初的数据表"}:
        if task:
            return {"message": "当前有待确认操作。请先完成或取消，再切换数据表。",
                    "pending_id": task["id"], "choices": task.get("choices", [])}
        if not getattr(chat.initial_target[0], "table_id", True):
            return {"message": "本次会话从空工作区启动，最初没有绑定数据表。"}
        return _activate(chat, *chat.initial_target)
    cmd = parse(text)
    if cmd is None:
        return None
    if task:
        if cmd["op"] == "list_tables":
            return {**_preview_or_switch(chat, cmd), "pending_id": task["id"], "choices": task.get("choices", [])}
        return {"message": "当前有待确认操作。请先完成或取消，再更换写入目标。",
                "pending_id": task["id"], "choices": task.get("choices", [])}
    return _preview_or_switch(chat, cmd)
