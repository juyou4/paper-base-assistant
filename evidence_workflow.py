"""先展示论文证据与目标列，由同一会话确认后逐篇提交。"""

import contextvars
import json
import uuid
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

from activity import step
from batch_recovery import ACTIVE as ACTIVE_JOURNAL
import evidence_filter
from paper_resources import KINDS as RESOURCE_KINDS, LABELS as RESOURCE_LABELS, valid_resource
from papers import _find_target
from preflight import _same_article
from source_library import existing_source, local_candidates, remember, remember_evidence_columns
from table_store import FeishuTable, LocalTable, TableError, TargetChanged, _check_fields, _identity


_FIELD_NAMES = {
    "metric": ("关键数据", "实验指标", "关键指标", "实验结果", "指标", "阅读笔记"),
    "figure": ("论文图", "关键图", "图表", "关键图表", "图表摘录", "研究笔记"),
    "table": ("论文表格", "数据表格", "图表", "关键图表", "图表摘录", "研究笔记"),
    "formula": ("论文公式", "关键公式", "公式", "研究笔记", "阅读笔记"),
    "code": ("代码链接", "代码仓库", "开源代码", "项目代码", "资源链接", "研究笔记", "阅读笔记"),
    "model": ("模型链接", "模型权重", "模型资源", "资源链接", "研究笔记", "阅读笔记"),
    "dataset": ("数据集链接", "数据集地址", "数据集", "资源链接", "研究笔记", "阅读笔记"),
    "title": ("论文标题", "文献标题", "论文名称", "标题", "题名", "title"),
    "url": ("论文链接", "论文地址", "原文链接", "原文地址", "文献链接", "预印本链接", "URL"),
}
_KIND_NAMES = {"metric": "指标", "figure": "图", "table": "表", "formula": "公式", **RESOURCE_LABELS}
MAX_PAPERS = 8


def _field(schema, kind, label=None):
    types = {"text", "url"} if kind == "url" else {"text"}
    eligible = {column["name"].casefold(): column for column in schema if column["type"] in types}
    for name in ([label] if label else []) + list(_FIELD_NAMES[kind]):
        if name.casefold() in eligible:
            return eligible[name.casefold()]
    choices = "、".join(_FIELD_NAMES[kind])
    if kind in RESOURCE_KINDS:
        raise ValueError(f"资源证据需要文本列以保存候选链接、原文摘录和出处；可用列名为{choices}。")
    raise ValueError(f"未找到可写的{_KIND_NAMES.get(kind, '论文标识')}列。可用列名为{choices}。")


def _paper_url(ref):
    arxiv_id, url = _find_target(ref)
    return "https://arxiv.org/abs/" + arxiv_id if arxiv_id else url


def _paper_key(url):
    if url.startswith("https://aclanthology.org/"):
        return _identity(url.split("#")[0].rstrip("/").removesuffix(".pdf") + "/")
    return _identity(url)


def _same_source(paper_url, address, evidence_urls=()):
    if isinstance(address, str) and address.startswith("/api/attachments/"):
        from paper_attachments import matches_source
        return matches_source(paper_url, address)
    return bool(address) and _same_article(
        {"url": paper_url, "key": _paper_key(paper_url), "evidence_urls": evidence_urls}, address)


def _blocks(items):
    """为每列生成可单独比较的证据段，重复提取不追加同一段。"""
    fields = defaultdict(list)
    for item in items:
        is_math = item["kind"] == "formula"
        label = f"【{_KIND_NAMES[item['kind']]}】{item['label']}"
        if is_math:
            lines = [label, "$$", item["latex"], "$$"]
        else:
            lines = [f"{label} · {item['value']}"]
        for row in [item.get("headers", [])] + (item.get("rows") or []):
            if row:
                lines.append(" | ".join(str(cell) for cell in row))
        original = item["quote"].strip()
        # 分栏排版的等式需要补齐对齐环境，保留合并前的原文以便复核。
        same_math = is_math and (original == item["latex"] or any(
            original.startswith(left) and original.endswith(right)
            and original[len(left):-len(right)].strip() == item["latex"]
            for left, right in (("$$", "$$"), ("$", "$"), (r"\[", r"\]"), (r"\(", r"\)"))
        ))
        if not same_math:
            lines.append(f"原文摘录：{item['quote']}")
        lines.append(f"出处：{item['source']}  {item.get('public_source_url') or item['source_url']}")
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
        row = table._locate(key, types, {link: url}, check_duplicates=True, rows=rows)
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


def _field_values(items, before, identifiers):
    values = {}
    modes = {}
    for name, blocks in _blocks(items).items():
        old = before.get(name, "")
        additions = [b for b in blocks if b not in old]
        modes[name] = ("append" if old else "set") if additions else "unchanged"
        values[name] = "\n\n".join(([old] if old else []) + additions)
    for name, value in identifiers:
        if name:
            modes[name] = "unchanged" if before.get(name) else "set"
            values[name] = before.get(name) or value
    return values, modes


def writable_evidence_columns(schema):
    from workflow import _fields
    id_names = {f["name"] for kind in ("title", "url") for f in _fields(schema, kind)}
    id_aliases = {name.casefold() for kind in ("title", "url") for name in _FIELD_NAMES[kind]}
    return [c["name"] for c in schema if c["type"] == "text" and c["name"] not in id_names
            and c["name"].casefold() not in id_aliases]


def _prepare_one(table, url, entry, schema, rows, preferences=None):
    columns = {"url": _field(schema, "url")}
    try:
        columns["title"] = _field(schema, "title")
    except ValueError:
        pass  # 链接标识足以确定论文，标题列可选。
    identity_aliases = {name.casefold() for kind in ("title", "url") for name in _FIELD_NAMES[kind]}
    id_names = {col["name"] for col in schema if col["name"].casefold() in identity_aliases}
    writable_columns = writable_evidence_columns(schema)
    for item in entry["items"]:
        preferred = (preferences or {}).get(item["kind"])
        column = preferred if preferred in writable_columns else _field(schema, item["kind"], item["label"])["name"]
        if column not in writable_columns:
            raise ValueError(f"「{column}」用于论文标识，无法同时存放提取结果。")
        item["field"] = column
    title_col = columns.get("title", {}).get("name")
    link = columns["url"]["name"]
    row = _locate(table, schema, link, title_col, url, entry["title"], rows)
    before = row["fields"] if row else {}
    values, modes = _field_values(entry["items"], before, ((link, url), (title_col, entry["title"])))
    _check_fields(schema, values)
    entry.update(action=("create" if row is None else
                         "unchanged" if all(m == "unchanged" for m in modes.values()) else "update"),
                 fields=[{"name": name, "mode": mode} for name, mode in modes.items()],
                 target_columns=writable_columns, status="ready")
    all_schema = [dict(f) for f in schema if f["name"] in id_names or f["name"] in writable_columns]
    all_before = {f["name"]: before.get(f["name"], "") for f in all_schema}
    return {"key": _paper_key(url), "target_url": url, "link": link, "title_col": title_col,
            "record_id": row["id"] if row else None, "before": {n: before.get(n, "") for n in values},
            "all_before": all_before, "writable_columns": writable_columns,
            "patch": {n: v for n, v in values.items() if modes[n] != "unchanged"},
            "values": values, "schema": [dict(f) for f in schema if f["name"] in values],
            "all_schema": all_schema, "request_id": uuid.uuid4().hex}


def _extract_one(number, entry, requested, key, wanted, extract_fn, scope=None):
    with step(f"提取第 {number} 篇论文的原文证据", tool="论文解析"):
        options = {}
        if scope:
            options["scope"] = scope
        if "formula" in wanted:
            options["include_formulas"] = True
        if any(kind in RESOURCE_KINDS for kind in wanted):
            options["include_resources"] = True
        paper = extract_fn(requested, **options)
        evidence_urls = paper.get("evidence_urls", ())
        # CVF 等出版页面可能解析成 DOI。仅接受解析器为该版本核实的原始入口。
        if not (_same_source(requested, paper["url"]) or
                (not key.startswith("arxiv:") and requested in evidence_urls)):
            raise ValueError("论文来源与请求的链接或版本不一致。")
        entry.update(title=paper["title"], url=paper["url"])
        if paper.get("source_version"):
            entry["source_version"] = paper["source_version"]
        if not entry["title"]:
            raise ValueError("论文缺少可核验的标题。")
        items = [dict(item) for item in paper["items"] if item["kind"] in wanted and evidence_filter.matches(item, scope)]
        if scope:
            label = evidence_filter.describe(scope)
            located = {ref for item in items for ref in evidence_filter.refs(item.get("locator") or item["source"])}
            if any((kind, n) not in located for kind in ("tables", "figures") for n in scope.get(kind, ())):
                raise ValueError(f"所指定的「{label}」在本次解析的原文中没有全部定位，请核对编号或实验条件。")
            if not items or "metric" in wanted and not any(i["kind"] == "metric" for i in items):
                raise ValueError(f"在本次解析的原文范围内找不到符合「{label}」的证据，请核对图表编号、指标或实验条件。")
            if len(items) > 60:
                raise ValueError(f"「{label}」匹配超过 60 项，请指定模型行、数据集或指标后再预览。")
            metrics = [item for item in items if item["kind"] == "metric"]
            requested = evidence_filter.requested_metrics(scope)
            covered = set().union(*(evidence_filter.metric_tags(item) for item in metrics))
            if requested - covered:
                missing = evidence_filter.describe({"metrics": sorted(requested - covered)})
                raise ValueError(f"本次解析没有定位到所要求的「{missing}」，请核对指标名称或重新指定范围。")
            if len(metrics) > 1:
                for item in metrics:
                    item["review_required"] = "符合条件的实验单元格有多项，请核对模型行和指标列，勾选需要的数值。"
        entry["items"] = items
        if scope:
            entry["scope"] = scope
        if "formula" in wanted and not any(item["kind"] == "formula" for item in entry["items"]):
            note = "未取得可核对的公式源码。可打开论文原页核对，扫描页公式需要数学 OCR。"
            if not entry["items"]:
                raise ValueError(note)
            entry["notice"] = note
        if not entry["items"]:
            if options.get("include_resources"):
                raise ValueError("在已读取的论文正文中没有找到所请求的 GitHub、GitLab 或 Hugging Face 资源链接。")
            raise ValueError("论文中未取得该类型的可定位原文证据。")
        if options.get("include_resources"):
            missing = [RESOURCE_LABELS[k] for k in RESOURCE_KINDS
                       if k in wanted and not any(i["kind"] == k for i in entry["items"])]
            hints = [entry.get("notice", "")]
            if missing:
                hints.append("本次未找到：" + "、".join(missing) + "。")
            if any(i["kind"] in RESOURCE_KINDS for i in entry["items"]):
                hints.append("资源候选也可能属于基线或引用工作。请核对摘录，勾选要保存的链接。")
            if any(i.get("resource_total") for i in entry["items"]):
                hints.append("每种资源最多展示 12 个不同地址。")
            entry["notice"] = " ".join(filter(None, hints))
        for item in entry["items"]:
            if item["kind"] in RESOURCE_KINDS and not valid_resource(item):
                raise ValueError("资源链接与原文候选类型不一致，请重新预览。")
            if item["kind"] == "formula" and not (
                isinstance(item.get("latex"), str) and item["latex"].strip()
            ):
                raise ValueError("缺少可核对的公式源码，请查看原文。")
            if (not all(isinstance(item.get(k), str) and item[k].strip()
                        for k in ("label", "value", "source", "quote", "source_url"))
                    or not _same_source(entry["url"], item["source_url"], evidence_urls)
                    or (item.get("public_source_url") and
                        not _same_source(entry["url"], item["public_source_url"], evidence_urls))):
                raise ValueError("提取项缺少本篇论文的原文、数值或定位链接。")
        return paper


def prepare(table, urls, kinds=None, *, preferences=None, filters=None):
    """返回仅驻留服务端的确认计划和浏览器可见的逐篇预览。"""
    from evidence_extract import extract_evidence

    if not urls or len(urls) > MAX_PAPERS:
        raise ValueError(f"请指定 1 至 {MAX_PAPERS} 篇论文。")
    if isinstance(kinds, dict) and set(kinds) != set(urls):
        raise ValueError("请为每篇论文指定需要预览的证据类型。")
    scopes = kinds if isinstance(kinds, dict) else dict.fromkeys(urls, ("metric", "figure", "table") if kinds is None else kinds)
    if any(not isinstance(scope, (tuple, list, set, frozenset)) or not scope or
           not set(scope) <= _KIND_NAMES.keys() for scope in scopes.values()):
        raise ValueError("请指定关键数据、图、表、公式或论文资源链接。")
    tool = getattr(table, "tool_name", None)
    with step("读取回填目标字段和现有记录", tool=tool):
        schema = table.schema()
        shared_rows = table.identity_rows({f["name"]: f["type"] for f in schema}) if isinstance(table, FeishuTable) else None
    entries, jobs, seen = [], [], set()
    for idx, ref in enumerate(urls):
        entry = {"url": ref, "title": ref, "items": [], "status": "skipped"}
        entries.append(entry)
        try:
            requested = _paper_url(ref)
            key = _paper_key(requested)
            if key in seen:
                raise ValueError("本批次中已出现同一篇论文。")
            seen.add(key)
            jobs.append((idx, idx + 1, requested, key, scopes[ref]))
        except (ValueError, TableError, OSError) as error:
            entry["error"] = str(error)

    extracted = {}

    def _run(job):
        idx, number, requested, key, wanted = job
        try:
            scope = (filters or {}).get(urls[idx], (filters or {}).get("*"))
            return idx, _extract_one(number, entries[idx], requested, key, wanted, extract_evidence, scope), None
        except (ValueError, TableError, OSError) as error:
            return idx, None, error

    if len(jobs) == 1:
        idx, paper, error = _run(jobs[0])
        extracted[idx] = (paper, error)
    elif len(jobs) > 1:
        with ThreadPoolExecutor(max_workers=min(4, len(jobs))) as pool:
            futures = [pool.submit(contextvars.copy_context().run, _run, job) for job in jobs]
            for future in futures:
                idx, paper, error = future.result()
                extracted[idx] = (paper, error)

    drafts, prepared_keys = [], set()
    job_by_idx = {job[0]: job for job in jobs}
    for idx, entry in enumerate(entries):
        if "error" in entry:
            drafts.append(None)
            continue
        _, number, requested, _, _ = job_by_idx[idx]
        paper, error = extracted[idx]
        if error is not None:
            entry["error"] = str(error)
            drafts.append(None)
            continue
        try:
            with step(f"核对第 {number} 篇论文的目标行与列", tool=tool), table._lock:
                aliases = list(dict.fromkeys([requested, entry["url"], *paper.get("aliases", [])]))
                rows = (local_candidates(table, aliases, entry["title"], schema)
                        if isinstance(table, LocalTable) else shared_rows if shared_rows is not None else table.rows())
                keys = {_paper_key(address) for address in aliases}
                if prepared_keys & keys:
                    raise ValueError("本批次中已出现同一篇论文。")
                anchor = existing_source(table, aliases, rows=rows)
                target = anchor if anchor and _identity(anchor) != _identity(entry["url"]) else entry["url"]
                draft = _prepare_one(table, target, entry, schema, rows, preferences)
                draft["aliases"] = aliases
                draft["bibliography"] = {k: paper[k] for k in ("title", "authors", "year", "doi", "venue", "publication_type")
                                         if paper.get(k)}
                if target != entry["url"]:
                    entry["target_url"] = target
                prepared_keys.update(keys)
        except (ValueError, TableError, OSError) as error:
            entry["error"] = str(error)
            draft = None
        drafts.append(draft)
    ready = sum(draft is not None for draft in drafts)
    plan = {"ready": ready, "entries": entries, "drafts": drafts}
    message = (f"已处理 {len(entries)} 篇，{ready} 篇可回填。"
               + ("请核对原文、提取内容和目标列，再确认回填。" if ready else "请查看逐篇原因后重新提取。"))
    return plan, {"message": message, "evidence_preview": entries}


def _check_target(table, entry, draft):
    schema = table.schema()
    if any(field not in schema for field in draft["schema"]):
        raise ValueError("目标列已变更，请重新预览。")
    if isinstance(table, LocalTable):
        rows = local_candidates(table, draft["aliases"], entry["title"], schema, draft["record_id"])
    elif isinstance(table, FeishuTable):
        rows = table.identity_rows({f["name"]: f["type"] for f in schema})
    else:
        rows = table.rows()
    anchor = existing_source(table, draft["aliases"], rows=rows)
    if anchor and _identity(anchor) != _identity(draft["target_url"]):
        raise ValueError("已收藏论文的目标链接在预览后发生变化，请重新核对。")
    row = _locate(table, schema, draft["link"], draft["title_col"], draft["target_url"], entry["title"], rows)
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


def _parse_overrides(raw, item_count, draft):
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError("目标列设置与原预览不一致，请重新核对预览。")
    if raw and draft is None:
        raise ValueError("目标列设置与原预览不一致，请重新核对预览。")
    allowed = set(draft.get("writable_columns") or ()) if draft else set()
    parsed = {}
    for key, col_name in raw.items():
        if type(key) is int:
            idx = key
        elif isinstance(key, str) and key.isdigit():
            idx = int(key)
        else:
            raise ValueError("目标列设置与原预览不一致，请重新核对预览。")
        if not 0 <= idx < item_count or idx in parsed:
            raise ValueError("目标列设置与原预览不一致，请重新核对预览。")
        if not isinstance(col_name, str) or col_name not in allowed:
            raise ValueError(f"目标列「{col_name}」不可用于写入提取结果，请重新核对预览。")
        parsed[idx] = col_name
    return parsed


def _selected_drafts(plan, selection, field_overrides=None):
    if selection is None and field_overrides is None:
        return plan["drafts"]
    entries, drafts = plan["entries"], plan["drafts"]
    if selection is not None and (not isinstance(selection, list) or len(selection) != len(entries)):
        raise ValueError("勾选范围与本批论文不一致，请重新核对预览。")
    if field_overrides is not None and (not isinstance(field_overrides, list) or len(field_overrides) != len(entries)):
        raise ValueError("目标列设置与本批论文不一致，请重新核对预览。")
    selected = []
    for p_idx, (entry, draft) in enumerate(zip(entries, drafts)):
        items = entry["items"]
        if selection is None:
            indices = list(range(len(items))) if draft is not None else []
        else:
            indices = selection[p_idx]
            if (not isinstance(indices, list) or len(indices) > len(items)
                    or any(type(i) is not int or not 0 <= i < len(items) for i in indices)
                    or len(set(indices)) != len(indices) or (indices and draft is None)):
                raise ValueError("勾选项与原预览不一致，请重新核对预览。")
        overrides = _parse_overrides(field_overrides[p_idx] if field_overrides is not None else None, len(items), draft)
        if not indices or draft is None:
            selected.append(None)
            continue
        all_before = draft.get("all_before", draft["before"])
        all_schema = draft.get("all_schema", draft["schema"])
        chosen_items = [{**items[i], "field": overrides.get(i, items[i]["field"])} for i in sorted(indices)]
        identifiers = ((draft["link"], draft["target_url"]), (draft["title_col"], entry["title"]))
        values, modes = _field_values(chosen_items, all_before, identifiers)
        _check_fields(all_schema, values)
        selected.append({**draft, "values": values, "chosen_items": chosen_items,
                         "before": {name: all_before.get(name, "") for name in values},
                         "schema": [f for f in all_schema if f["name"] in values],
                         "patch": {name: value for name, value in values.items() if modes[name] != "unchanged"}})
    if not any(selected):
        raise ValueError("请至少勾选一项证据，或取消本批回填。")
    return selected


def commit(table, plan, selection=None, field_overrides=None):
    """使用已确认的服务端快照逐篇提交，同一计划的重复确认返回原回执。"""
    tool = getattr(table, "tool_name", None)
    with table._lock:
        if "result" in plan:
            return plan["result"]
        skipped_manual = 0
        if selection is None:
            defaults = [[idx for idx, item in enumerate(entry["items"])
                         if item["kind"] not in RESOURCE_KINDS and not item.get("review_required")]
                        if draft else [] for entry, draft in zip(plan["entries"], plan["drafts"])]
            skipped_manual = sum(len(entry["items"]) - len(default) for entry, default, draft
                                 in zip(plan["entries"], defaults, plan["drafts"]) if draft)
            if skipped_manual:
                if not any(defaults):
                    raise ValueError("请在预览中核对原文，逐项勾选要保存的资源候选或需核对原页的表格。")
                selection = defaults
        drafts = _selected_drafts(plan, selection, field_overrides=field_overrides)
        journal = ACTIVE_JOURNAL.get()
        if journal:
            journal.start(plan["entries"], drafts)
        results = []
        for number, (entry, draft) in enumerate(zip(plan["entries"], drafts), 1):
            result = {"title": entry["title"], "url": entry["url"]}
            results.append(result)
            if draft is None:
                if "error" in entry:
                    result.update(status="skipped", message=entry["error"])
                else:
                    result.update(status="excluded", message="本篇未勾选任何证据。")
                continue
            try:
                with step(f"复核第 {number} 篇论文的目标与原值", tool=tool):
                    _check_target(table, entry, draft)
            except (ValueError, TableError, OSError) as error:
                result.update(status="failed", message=str(error))
                if journal:
                    journal.mark(number, "failed", note=str(error))
                continue
            try:
                record_id = draft["record_id"]
                write_action = "unchanged"
                if draft["patch"]:
                    if journal:
                        journal.mark(number, "sent", record_id)
                    with step(f"提交第 {number} 篇论文的已确认字段", tool=tool):
                        write = table.upsert(draft["key"], draft["patch"], draft["request_id"],
                                             update_only=record_id is not None, expected_record_id=record_id,
                                             expected_fields=draft["before"])
                        record_id = write["record_id"]
                        write_action = write["action"]
                        if journal:
                            journal.mark(number, "sent", record_id)
                result["record_id"] = record_id
                with step(f"回读第 {number} 篇论文的字段值", tool=tool):
                    saved = table.get(record_id)
                    if (draft["record_id"] and saved["id"] != draft["record_id"] or
                            any(saved["fields"].get(n) != v for n, v in draft["values"].items())):
                        raise ValueError("回读记录或字段值与确认内容不符。")
                    remember(table, saved, {**draft.get("bibliography", {}), "url": draft["target_url"]}, draft["aliases"])
                chosen = draft.get("chosen_items", entry["items"])
                remember_evidence_columns(table, draft["schema"], chosen)
                items = len(chosen)
                verified_items = [{"field": item["field"], "label": item["label"], "value": item["value"],
                                   "source": item["source"], "url": item.get("public_source_url") or item["source_url"]}
                                  for item in chosen]
                result.update(status="verified", write_action=write_action, selected_items=items, verified_items=verified_items,
                              message=f"已核对 {items} 项证据和 {len(draft['values'])} 个字段，回读一致。")
            except TargetChanged as error:
                result.update(status="failed", message=str(error))
            except (ValueError, TableError, OSError) as error:
                result.update(status="unverified", message=f"未取得一致的写入回执，请核对表内记录。{error}")
            if journal:
                journal.mark(number, result["status"], result.get("record_id"), result["message"])
        verified = sum(item["status"] == "verified" for item in results)
        total = len(results) if selection is None else sum(draft is not None for draft in drafts)
        count = f"{verified}/{total} 篇回读一致"
        message = f"本次回填结束，{count}。"
        if skipped_manual:
            message += f"另有 {skipped_manual} 项需要手动核对，已跳过。"
        plan["result"] = {"message": message, "verification": count, "evidence_results": results}
        return plan["result"]
