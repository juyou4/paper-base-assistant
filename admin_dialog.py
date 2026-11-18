"""解析当前表的管理指令，把只读查询、变更预览和确认提交分开。"""

import re



TYPES = {"文本": "text", "链接": "url", "超链接": "url", "网址": "url", "url": "url"}
PERMS = {"可看": "view", "查看": "view", "可查看": "view", "只读": "view", "阅读": "view",
         "编辑": "edit", "可编辑": "edit", "可写": "edit"}
PERM_LABEL = {"view": "可查看", "edit": "可编辑", "full_access": "可管理"}
TYPE_LABEL = {"text": "文本", "url": "超链接", "readonly": "只读", "unsupported": "其他类型"}
QUERY = r"(?:查看|查询|看看|显示|列出)"


def _unquote(value):
    return value.strip(" \t「」『』“”‘’\"'《》：:")


def _column(value):
    value = re.sub(r"^(?:字段|列)\s*", "", value.strip())
    value = re.sub(r"(?:这一列|这列|字段|列)$", "", value.strip())
    return _unquote(value)


def parse_admin(message):
    text = re.sub(r"^(?:请问|请|帮我|麻烦)\s*", "", message.strip().rstrip("。！？?!")).strip()
    if any(word in text for word in ("如果", "能不能", "如何", "怎么才能", "不允许", "不要", "不用", "别改", "假设")):
        return None
    if "角色" in text:
        return parse_role(text)
    if "协作者" in text or ("权限" in text and re.search(r"@|ou_", text)):
        return parse_member(text)
    if re.fullmatch(r"(?:查看|查询|看看|显示|列出)(?:当前表|本表|全部|所有|现有|表格)?(?:的)?(?:表结构|字段|列)(?:列表)?|(?:当前表|本表|表格)?有哪些(?:字段|列)", text):
        return {"op": "columns"}
    m = re.fullmatch(QUERY + r"\s*(?:字段|列)\s*(.+)", text)
    if m:
        return {"op": "column", "target": _column(m[1])}
    m = re.fullmatch(r"(?:删除|移除)\s*(?:(?:字段|列)\s*(.+)|(.+?)(?:这(?:一)?列|字段|列))", text)
    if m:
        return {"op": "delete", "target": _column(m[1] or m[2])}
    m = re.fullmatch(r"(?:把|将|给)?\s*(.+?)(?:的名称|的名字)?\s*(?:重命名为|改名为|更名为|改名叫)\s*(.+)", text)
    if m:
        return {"op": "rename", "target": _column(m[1]), "new_name": _unquote(m[2])}
    m = re.fullmatch(r"(?:把|将)?\s*(.+?)(?:的类型|类型)?\s*(?:改为|改成|调整为|设为)\s*(文本|超链接|链接|网址|URL)(?:类型|字段|列)?", text, re.I)
    if m:
        return {"op": "change_type", "target": _column(m[1]), "new_type": TYPES[m[2].lower()]}
    m = re.fullmatch(r"(?:把|将)\s*[「『“‘\"'](.+?)[」』”’\"']\s*(?:改为|改成)\s*[「『“‘\"'](.+?)[」』”’\"']", text)
    if m:
        return {"op": "unclear_edit", "target": m[1], "new_value": m[2]}
    return None


def parse_member(text):
    match = re.search(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}|(?<![A-Za-z0-9_])ou_[a-zA-Z0-9_-]+", text)
    if not match:
        raise ValueError("要查询或调整哪位协作者？请用明确的邮箱或 open_id，例如“查看 reader@example.test 的协作者权限”。")
    target = match[0]
    if len(re.findall(r"@|ou_", text)) != 1:
        raise ValueError("请一次指定一位协作者。")
    if re.match(QUERY, text) and not re.search(r"改为|设为|改成|调整为", text):
        return {"op": "member_read", "target": target}
    option = re.search(r"(?:改为|设为|调为|调整为|改成|设置为)\s*(可编辑|可查看|查看|只读|可写|可看|编辑|阅读)(?:权限)?$", text)
    if option and not re.search(r"移除|删除|所有|公开|转移|继承|其他表", text):
        prior = re.search(r"从\s*(可编辑|可查看|查看|只读|可写|可看|编辑|阅读)", text)
        return {"op": "member_change", "target": target, "permission": PERMS[option[1]],
                "expected_before": PERMS[prior[1]] if prior else None}
    raise ValueError("请明确是查看现有权限，还是把这位已有协作者调为可查看或可编辑。")


def parse_role(text):
    if re.fullmatch(QUERY + r"(?:全部|现有|所有|高级|自定义)?角色(?:列表)?", text) or text == "有哪些角色":
        return {"op": "role_list"}
    name = re.search(r"角色\s*[「“\"]([^」”\"]+)[」”\"]|角色\s*([^\s「」“”]+?)(?=的|对|在|\s|$)", text)
    if not name:
        raise ValueError("请给出高级角色的确切名称，例如“查看角色「论文审阅人」的权限”。")
    role = (name[1] or name[2]).strip()
    if re.match(QUERY, text) and not re.search(r"改为|设为|改成|调整为", text):
        return {"op": "role_read", "role": role}
    col = re.search(r"(?:字段|列)\s*[「“\"]([^」”\"]+)[」”\"]|(?:字段|列)\s*([^\s「」“”]+?)(?=的权限|权限|\s)", text)
    if col:
        perm = re.search(r"(?:改为|设为|改成|调整为)\s*(只读|可查看|可编辑|可写|隐藏|不可见)$", text)
        if perm:
            permission = {"可编辑": "edit", "可写": "edit", "隐藏": "no_perm", "不可见": "no_perm"}.get(perm[1], "read")
            return {"op": "role_column", "role": role, "field": (col[1] or col[2]).strip(), "permission": permission}
    if re.search(r"行|记录", text) and re.search(r"改为|设为|改成|调整为", text):
        if re.search(r"全部可读[，,、 ]*仅本人创建(?:的记录)?可编辑", text):
            scope = "own_edit_all_read"
        elif re.search(r"(?:仅|只)(?:可)?(?:读写|查看和编辑)本人创建(?:的记录)?$", text):
            scope = "own_only"
        elif re.search(r"全部(?:记录)?可读写$", text):
            scope = "all_edit"
        else:
            raise ValueError("请同时说明能看哪些行、能改哪些行，例如“将角色「论文审阅人」的行权限改为全部可读、仅本人创建可编辑”。")
        return {"op": "role_rows", "role": role, "scope": scope}
    raise ValueError("可以查看角色权限，或指定某列的查看/编辑/隐藏权限；调整行范围时请同时说明可读与可编辑范围。")


def prepare_role(table, request):
    from role_admin import RoleAdmin
    api = RoleAdmin(table)
    op = request["op"]
    if op == "role_list":
        roles = api.list_roles()
        return {"answer": "当前可管理的自定义角色：" + ("、".join(r["name"] for r in roles) or "暂无") + "。"}
    if op == "role_read":
        data = api.inspect(request["role"])
        return {"answer": data.get("summary") or "角色权限：" + str(data)}
    if op == "role_column":
        plan = api.plan_field(request["role"], request["field"], request["permission"])
    else:
        plan = api.plan_rows(request["role"], request["scope"])
    return {"kind": "role", "plan": plan, "message": plan["summary"] + "。确认后提交。"}


def commit_role(table, plan):
    from role_admin import RoleAdmin
    RoleAdmin(table).apply(plan)
    return "已回读当前角色规则，确认指定的权限变更生效。"


def _field_candidates(table, target):
    from schema_admin import inspect_field
    fields = inspect_field(table)
    exact = [f for f in fields if target in (f["id"], f["name"])]
    return exact or [f for f in fields if target and target in f["name"]]


def prepare(table, request):
    op = request["op"]
    if op.startswith("role_"):
        return prepare_role(table, request)
    if op == "columns":
        from schema_admin import inspect_field
        columns = inspect_field(table)
        return {"answer": "当前字段：" + "、".join(f"{f['name']}（{TYPE_LABEL.get(f['type'],f['type'])}）" for f in columns)}
    if op.startswith("member_"):
        from collaborator_admin import CollaboratorAdmin
        api = CollaboratorAdmin(table)
        if op == "member_read":
            member = api.lookup(request["target"])
            return {"answer": f"协作者 {request['target']} 在整份多维表格中的权限为{PERM_LABEL.get(member['perm'],member['perm'])}。"}
        plan = api.plan(request["target"], request["permission"])
        if request.get("expected_before") and request["expected_before"] != plan["before"]:
            raise ValueError("指令中给出的原权限与实际权限不同。请先查询这位协作者的权限。")
        old, new = (PERM_LABEL.get(plan[k], plan[k]) for k in ("before", "after"))
        if old == new:
            return {"answer": f"{request['target']} 已经是{old}。"}
        return {"kind": "member", "plan": plan,
                "message": f"将已有协作者 {request['target']} 对整份多维表格的权限从「{old}」改为「{new}」。确认后提交。"}
    matches = _field_candidates(table, request["target"])
    if len(matches) > 1:
        return {"ask": "你指的是哪一个字段？", "choices": [f["name"] for f in matches],
                "candidates": {f["name"]: f["id"] for f in matches}}
    if not matches:
        raise ValueError(f"当前表找不到「{request['target']}」字段，请先查看表结构。")
    field = matches[0]
    if op == "column":
        return {"answer": f"字段「{field['name']}」的类型为{TYPE_LABEL.get(field['type'],field['type'])}，ID 为 {field['id']}。"}
    from schema_admin import plan_field_change
    plan = plan_field_change(table, field["id"], op, new_name=request.get("new_name"), new_type=request.get("new_type"))
    if op == "rename":
        change = f"将「{field['name']}」改名为「{plan['new_name']}」"
    elif op == "change_type":
        change = f"将「{field['name']}」由{TYPE_LABEL[field['type']]}改为{TYPE_LABEL[plan['new_type']]}"
    else:
        change = f"删除「{field['name']}」整列及其中的现有值"
    return {"kind": "field", "plan": plan,
            "message": f"准备{change}。当前可见记录里，该列有 {plan['affected_count']} 条非空值。确认后提交。"}


def commit(table, task):
    if task["kind"] == "member":
        from collaborator_admin import CollaboratorAdmin
        result = CollaboratorAdmin(table).apply(task["plan"])
        return f"已回读确认，指定协作者的权限为{PERM_LABEL.get(result['perm'],result['perm'])}。"
    if task["kind"] == "role":
        return commit_role(table, task["plan"])
    from schema_admin import commit_field_change
    report = commit_field_change(table, task["plan"])
    return "已回读字段定义，确认字段变更生效。" if report["action"] != "delete" else "已回读字段列表，确认指定的字段已删除。"
