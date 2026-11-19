"""证据确认后的逐篇持久化和只读复核。远端写入只由原确认流程发起。"""

import contextvars
import hashlib
import json

from evidence_filter import describe
from table_store import RecordNotFound, TableError, _identity


ACTIVE = contextvars.ContextVar("batch_journal", default=None)
STATES = {"queued": "尚未提交", "sent": "已进入提交，回执待核验", "verified": "提交时已核验",
          "unverified": "回执待核验", "failed": "目标校验未通过", "excluded": "已跳过"}


def _digest(value):
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


class BatchJournal:
    def __init__(self, store, owner, scope, thread, batch_id):
        self.store, self.owner, self.scope = store, owner, scope
        self.thread, self.batch_id = thread, batch_id
        self.operation = None

    def start(self, entries, drafts):
        papers = []
        for entry, draft in zip(entries, drafts):
            item = {"title": entry["title"], "url": entry["url"], "record_id": None, "request_id": "",
                    "expected": {}, "fields": [], "selected": [], "retry": "", "state": "excluded"}
            if draft:
                # 完整单元格包含用户之前的笔记。存全值摘要，核验时按字段 ID 找当前名称。
                expected = {f["id"]: [f["type"], _digest(draft["values"][f["name"]])] for f in draft["schema"]}
                chosen = draft.get("chosen_items", entry["items"])
                labels = {"metric": "关键数据", "figure": "图", "table": "原表", "formula": "公式",
                          "code": "代码链接", "model": "模型权重链接", "dataset": "数据集链接"}
                kinds = "和".join(dict.fromkeys(labels[i["kind"]] for i in chosen))
                scope = entry.get("scope", {})
                description = describe(scope) if scope else ""
                for key, values in scope.items():
                    if key.startswith("omit_"):
                        description += "，排除" + describe({key[5:]: values})
                item.update(state="queued", record_id=draft["record_id"], url=draft["target_url"],
                            retry=f"重新预览{entry['url']} 的{kinds}" + (f"，只看{description}" if description else ""),
                            request_id=draft["request_id"], expected=expected,
                            fields=[f["name"] for f in draft["schema"]], selected=[
                                {"label": i["field"], "value": f"{i['label']} · {i['value']}\n{i['source']}",
                                 "url": i.get("public_source_url") or i["source_url"]}
                                for i in chosen])
            papers.append(item)
        self.operation = self.store.start_batch(self.owner, self.scope, self.thread, self.batch_id, papers)

    def mark(self, ordinal, state, record_id=None, note=""):
        self.store.batch_update(self.owner, self.scope, self.operation, ordinal, state=state, record_id=record_id, note=note)


def recheck(store, owner, scope, table, operation=None, *, unresolved_only=False):
    papers = store.batch_items(owner, scope, operation, unresolved_only=unresolved_only)
    if not papers:
        return "当前表没有待处理的回填批次。"
    outcomes = []
    with table._lock:
        schema = None
        candidates = None
        for paper in papers:
            state, rid = paper["state"], paper["record_id"]
            if state in ("queued", "excluded", "failed"):
                outcomes.append(f"{paper['title']} · {STATES[state]}")
                continue
            try:
                if schema is None:
                    schema = {f["id"]: f for f in table.schema()}
                if not rid:
                    rid = table.request_record_id(paper["request_id"])
                via_link = False
                if not rid:
                    # 回执可能在飞书接受请求、客户端取得记录 ID 之前丢失。仅以唯一论文链接定位。
                    from workflow import _fields
                    links = [f["name"] for f in _fields(list(schema.values()), "url")]
                    if candidates is None:
                        if hasattr(table, "identity_rows"):
                            candidates = table.identity_rows({f["name"]: f["type"] for f in schema.values()}, links)
                        else:
                            candidates = table.rows()
                    hits = [r for r in candidates if any(_identity(r["fields"].get(k, "")) == _identity(paper["url"]) for k in links)]
                    if len(hits) != 1:
                        raise RecordNotFound("无法唯一定位原记录，请在表中核对。")
                    rid, via_link = hits[0]["id"], True
                row = table.get(rid)
                expected = json.loads(paper["expected"])
                if row["id"] != rid or any(fid not in schema or schema[fid]["type"] != kind
                        or _digest(row["fields"].get(schema[fid]["name"], "")) != digest
                        for fid, (kind, digest) in expected.items()):
                    observed = "内容或字段已变化，请重新预览。"
                    verdict = "conflict"
                else:
                    observed = ("按唯一论文链接定位，" if via_link else "") + "当前值与确认内容一致。"
                    verdict = "match"
                if via_link and verdict != "match":
                    rid = None
            except RecordNotFound as error:
                observed = f"未能定位原记录。{error}"
                verdict = "missing"
            except (TableError, ValueError, OSError) as error:
                observed = f"当前无法读取。{error}"
                verdict = "unreadable"
            store.batch_update(owner, scope, paper["operation"], paper["ordinal"], observed=observed, verdict=verdict, record_id=rid)
            outcomes.append(f"{paper['title']} · {observed}")
    return "逐篇核验结果\n" + "\n".join(outcomes)
