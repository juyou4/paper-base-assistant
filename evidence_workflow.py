"""先展示论文证据与目标列，由同一会话确认后逐篇提交。"""

import json
import uuid
from collections import defaultdict

from activity import step
from papers import _find_target
from preflight import _same_article
from table_store import FeishuTable, LocalTable, TableError, _check_fields, _identity


_FIELD_NAMES = {
    "metric": ("关键数据", "实验指标", "关键指标", "实验结果", "指标", "阅读笔记"),
    "figure": ("论文图", "关键图", "图表", "关键图表", "图表摘录", "研究笔记"),
    "table": ("论文表格", "数据表格", "图表", "关键图表", "图表摘录", "研究笔记"),
    "title": ("论文标题", "文献标题", "论文名称", "标题", "题名", "title"),
    "url": ("论文链接", "论文地址", "原文链接", "原文地址", "文献链接", "预印本链接", "URL"),
}
_KIND_NAMES = {"metric": "指标", "figure": "图", "table": "表"}
MAX_PAPERS = 8


def _field(schema, kind, label=None):
    types = {"text", "url"} if kind == "url" else {"text"}
    eligible = {column["name"].casefold(): column for column in schema if column["type"] in types}
    for name in ([label] if label else []) + list(_FIELD_NAMES[kind]):
        if name.casefold() in eligible:
            return eligible[name.casefold()]
    choices = "、".join(_FIELD_NAMES[kind])
    raise ValueError(f"未找到可写的{_KIND_NAMES.get(kind, '论文标识')}列。可用列名为{choices}。")


def _paper_url(ref):
    arxiv_id, url = _find_target(ref)
    return "https://arxiv.org/abs/" + arxiv_id if arxiv_id else url


def _paper_key(url):
    if url.startswith("https://aclanthology.org/"):
        return _identity(url.split("#")[0].rstrip("/").removesuffix(".pdf") + "/")
    return _identity(url)


def _same_source(paper_url, address):
    return bool(address) and _same_article({"url": paper_url, "key": _paper_key(paper_url)}, address)


def _blocks(items):
    """为每列生成可单独比较的证据段，重复提取不追加同一段。"""
    fields = defaultdict(list)
    for item in items:
        lines = [f"【{_KIND_NAMES[item['kind']]}】{item['label']} · {item['value']}"]
        for row in [item.get("headers", [])] + (item.get("rows") or []):
            if row:
                lines.append(" | ".join(str(cell) for cell in row))
        lines.extend([f"原文摘录：{item['quote']}", f"出处：{item['source']}  {item['source_url']}"])
        if item.get("image_url"):
            lines.append("原图：" + item["image_url"])
        block = "\n".join(lines)
        if block not in fields[item["field"]]:
            fields[item["field"]].append(block)
    return fields


def _locate(table, schema, link, title, url, paper_title, rows):
    key = _paper_key(url)
    linked = [r for r in rows if r["fields"].get(link) and _paper_key(r["fields"][link]) == key]
    if len(linked) > 1:
        raise ValueError("同一论文链接对应多条记录，请先核对重复行。")
    if isinstance(table, FeishuTable):
        types = {f["name"]: f["type"] for f in schema}
        row = table._locate(key, types, {link: url})
    elif isinstance(table, LocalTable):
        found = table._db.execute("SELECT id FROM demo_papers WHERE paper_key=?", (key,)).fetchone()
        row = table.get(found["id"]) if found else None
    else:
        row = linked[0] if linked else None
    if linked and (not row or row["id"] != linked[0]["id"]):
        raise ValueError("论文链接与现有记录标识不一致，请先核对记录。")
    if row and row["fields"].get(link) and not linked:
        raise ValueError("原有记录中的链接指向另一篇论文，请先核对记录。")
    if row and row["fields"].get(link) and not _same_source(row["fields"][link], url):
        raise ValueError("已有记录所指的论文版本与本次证据不一致，请先核对版本。")
    if row and not row["fields"].get(link) and (
        not title or row["fields"].get(title, "").strip().casefold() != paper_title.casefold()
    ):
        raise ValueError("之前绑定的记录缺少论文链接，也无法用标题核对，请先检查目标行。")
    if not row and title and any(r["fields"].get(title, "").strip().casefold() == paper_title.casefold() and
                                 not r["fields"].get(link) for r in rows):
        raise ValueError("已有同标题但缺少论文链接的记录，请先补齐链接以确定目标行。")
    return row


def _prepare_one(table, url, entry, schema, rows):
    columns = {"url": _field(schema, "url")}
    try:
        columns["title"] = _field(schema, "title")
    except ValueError:
        pass  # 链接标识足以确定论文，标题列可选。
    for item in entry["items"]:
        column = _field(schema, item["kind"], item["label"])
        if column in columns.values():
            raise ValueError(f"「{column['name']}」用于论文标识，无法同时存放提取结果。")
        item["field"] = column["name"]
    title_col = columns.get("title", {}).get("name")
    link = columns["url"]["name"]
    row = _locate(table, schema, link, title_col, url, entry["title"], rows)
    before = row["fields"] if row else {}
    values = {}
    modes = {}
    for name, blocks in _blocks(entry["items"]).items():
        old = before.get(name, "")
        additions = [b for b in blocks if b not in old]
        modes[name] = ("append" if old else "set") if additions else "unchanged"
        values[name] = "\n\n".join(([old] if old else []) + additions)
    for name, value in ((link, url), (title_col, entry["title"])):
        if name:
            modes[name] = "unchanged" if before.get(name) else "set"
            values[name] = before.get(name) or value
    _check_fields(schema, values)
    entry.update(action=("create" if row is None else
                         "unchanged" if all(m == "unchanged" for m in modes.values()) else "update"),
                 fields=[{"name": name, "mode": mode} for name, mode in modes.items()], status="ready")
    return {"key": _paper_key(url), "link": link, "title_col": title_col,
            "record_id": row["id"] if row else None, "before": {n: before.get(n, "") for n in values},
            "patch": {n: v for n, v in values.items() if modes[n] != "unchanged"},
            "values": values, "schema": [dict(f) for f in schema if f["name"] in values],
            "request_id": uuid.uuid4().hex}


def prepare(table, urls, kinds=None):
    """返回仅驻留服务端的确认计划和浏览器可见的逐篇预览。"""
    from evidence_extract import extract_evidence

    if not urls or len(urls) > MAX_PAPERS:
        raise ValueError(f"请指定 1 至 {MAX_PAPERS} 篇论文。")
    wanted = set(_KIND_NAMES if kinds is None else kinds)
    if not wanted or not wanted <= _KIND_NAMES.keys():
        raise ValueError("请指定关键数据、图或表。")
    tool = getattr(table, "tool_name", None)
    with step("读取回填目标字段和现有记录", tool=tool):
        schema, rows = table.schema(), table.rows()
    entries, drafts, seen = [], [], set()
    for ref in urls:
        entry = {"url": ref, "title": ref, "items": [], "status": "skipped"}
        entries.append(entry)
        try:
            requested = _paper_url(ref)
            key = _paper_key(requested)
            if key in seen:
                raise ValueError("本批次中已出现同一篇论文。")
            seen.add(key)
            with step(f"提取第 {len(entries)} 篇论文的原文证据", tool="论文解析"):
                paper = extract_evidence(requested)
                if not _same_source(requested, paper["url"]):
                    raise ValueError("论文来源与请求的链接或版本不一致。")
                entry.update(title=paper["title"], url=paper["url"])
                if not entry["title"]:
                    raise ValueError("论文缺少可核验的标题。")
                entry["items"] = [dict(item) for item in paper["items"] if item["kind"] in wanted]
                if not entry["items"]:
                    raise ValueError("论文中未取得该类型的可定位原文证据。")
                for item in entry["items"]:
                    if (not all(isinstance(item.get(k), str) and item[k].strip()
                                for k in ("label", "value", "source", "quote", "source_url"))
                            or not _same_source(entry["url"], item["source_url"])):
                        raise ValueError("提取项缺少本篇论文的原文、数值或定位链接。")
            with step(f"核对第 {len(entries)} 篇论文的目标行与列", tool=tool), table._lock:
                draft = _prepare_one(table, entry["url"], entry, schema, rows)
        except (ValueError, TableError, OSError) as error:
            entry["error"] = str(error)
            draft = None
        drafts.append(draft)
    ready = sum(draft is not None for draft in drafts)
    plan = {"ready": ready, "entries": entries, "drafts": drafts}
    message = (f"已处理 {len(entries)} 篇，{ready} 篇可回填。"
               + ("请核对每项原文、数值和目标列，再确认回填。" if ready else "请查看逐篇原因后重新提取。"))
    return plan, {"message": message, "evidence_preview": entries}


def _check_target(table, entry, draft):
    schema = table.schema()
    if any(field not in schema for field in draft["schema"]):
        raise ValueError("目标列已变更，请重新预览。")
    row = _locate(table, schema, draft["link"], draft["title_col"], entry["url"], entry["title"], table.rows())
    if (row["id"] if row else None) != draft["record_id"]:
        raise ValueError("目标行已变更，请重新预览。")
    cells = row["fields"] if row else {}
    if any(cells.get(name, "") != old for name, old in draft["before"].items()):
        raise ValueError("预览后目标单元格已变化，请重新预览。")
    if isinstance(table, FeishuTable):
        pending = table._db.execute(
            "SELECT cells FROM fs_pending_creates WHERE scope=? AND paper_key=?", (table._scope, draft["key"])
        ).fetchone()
        if pending and json.loads(pending["cells"]) != draft["values"] and not draft["record_id"]:
            raise ValueError("同一论文仍有内容不同的待核验新增请求，请先核对原回执。")


def commit(table, plan):
    """使用已确认的服务端快照逐篇提交，同一计划的重复确认返回原回执。"""
    tool = getattr(table, "tool_name", None)
    with table._lock:
        if "result" in plan:
            return plan["result"]
        results = []
        for number, (entry, draft) in enumerate(zip(plan["entries"], plan["drafts"]), 1):
            result = {"title": entry["title"], "url": entry["url"]}
            results.append(result)
            if draft is None:
                result.update(status="skipped", message=entry["error"])
                continue
            try:
                with step(f"复核第 {number} 篇论文的目标与原值", tool=tool):
                    _check_target(table, entry, draft)
            except (ValueError, TableError, OSError) as error:
                result.update(status="failed", message=str(error))
                continue
            try:
                record_id = draft["record_id"]
                if draft["patch"]:
                    with step(f"提交第 {number} 篇论文的已确认字段", tool=tool):
                        write = table.upsert(draft["key"], draft["patch"], draft["request_id"],
                                             update_only=record_id is not None)
                        record_id = write["record_id"]
                result["record_id"] = record_id
                with step(f"回读第 {number} 篇论文的字段值", tool=tool):
                    saved = table.get(record_id)
                    if (draft["record_id"] and saved["id"] != draft["record_id"] or
                            any(saved["fields"].get(n) != v for n, v in draft["values"].items())):
                        raise ValueError("回读记录或字段值与确认内容不符。")
                result.update(status="verified", message=f"已核对 {len(draft['values'])} 个字段，回读一致。")
            except (ValueError, TableError, OSError) as error:
                result.update(status="unverified", message=f"未取得一致的写入回执，请核对表内记录。{error}")
        verified = sum(item["status"] == "verified" for item in results)
        count = f"{verified}/{len(results)} 篇回读一致"
        plan["result"] = {"message": f"本次回填结束，{count}。", "verification": count, "evidence_results": results}
        return plan["result"]
