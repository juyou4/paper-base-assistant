"""论文证据回填覆盖真实 SQLite 与模拟飞书接口。"""

import unittest
from unittest.mock import patch

from evidence_workflow import commit, prepare
from table_store import FeishuTable, LocalTable, TableError


LINK = "https://arxiv.org/abs/2005.11401v4"
OTHER = "https://arxiv.org/abs/1706.03762v7"


def extracted(url):
    origin = url.replace("/abs/", "/html/") + "#T1"
    basic = {"source_url": origin, "source": "Table 1", "quote": "Demo · 44.1"}
    return {"title": "论文 " + url.rsplit("/", 1)[1], "url": url,
            "items": [
                dict(basic, kind="metric", label="NQ", value="44.1"),
                dict(basic, kind="metric", label="WQ", value="45.5", quote="WQ · 45.5"),
                dict(basic, kind="table", label="Table 1", value="实验表格", headers=["Model", "NQ"],
                     rows=[["Demo", "44.1"]]),
                dict(basic, kind="figure", label="Figure 1", value="示意图",
                     image_url=url.replace("/abs/", "/html/") + "/figure.png"),
            ]}


class EvidenceWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.table = LocalTable(":memory:")
        self.addCleanup(self.table.close)
        extractor = patch("evidence_extract.extract_evidence", side_effect=extracted)
        self.extract = extractor.start()
        self.addCleanup(extractor.stop)

    def test_batch_preview_exact_field_and_confirmation(self):
        self.table.add_field("NQ", "text")
        self.table.add_field("WQ", "text")
        plan, reply = prepare(self.table, [LINK, OTHER])
        self.assertEqual(self.table.rows(), [])
        preview = reply["evidence_preview"]
        self.assertEqual([e["action"] for e in preview], ["create", "create"])
        self.assertEqual([i["field"] for i in preview[0]["items"]], ["NQ", "WQ", "研究笔记", "研究笔记"])
        result = commit(self.table, plan)
        self.assertEqual([i["status"] for i in result["evidence_results"]], ["verified", "verified"])
        self.assertEqual(result, commit(self.table, plan))
        records = self.table.rows()
        self.assertEqual(len(records), 2)
        self.assertEqual({r["fields"]["论文链接"] for r in records}, {LINK, OTHER})
        for row in records:
            cells = row["fields"]
            self.assertIn("44.1", cells["NQ"])
            self.assertIn("45.5", cells["WQ"])
            self.assertIn("Demo | 44.1", cells["研究笔记"])
            self.assertIn("/figure.png", cells["研究笔记"])
            self.assertIn(cells["论文链接"].rsplit("/", 1)[1], cells["NQ"])

    def test_append_preserves_notes_and_repeat_is_read_only(self):
        rec = self.table.upsert(LINK, {"论文链接": LINK, "研究笔记": "用户笔记", "中文摘要": "个人摘要"}, "r")
        plan, _ = prepare(self.table, [LINK], kinds=["figure"])
        self.assertEqual(plan["entries"][0]["action"], "update")
        with patch.object(self.table, "upsert", wraps=self.table.upsert) as send:
            self.assertEqual(commit(self.table, plan)["evidence_results"][0]["status"], "verified")
            self.assertEqual(set(send.call_args.args[1]), {"研究笔记", "论文标题"})
        cells = self.table.get(rec["record_id"])["fields"]
        self.assertTrue(cells["研究笔记"].startswith("用户笔记\n"))
        self.assertNotIn("阅读笔记", cells)
        self.assertEqual(cells["中文摘要"], "个人摘要")
        repeat, _ = prepare(self.table, [LINK], kinds=["figure"])
        self.assertEqual(repeat["entries"][0]["action"], "unchanged")
        with patch.object(self.table, "upsert", side_effect=AssertionError("不应重写相同内容")):
            self.assertEqual(commit(self.table, repeat)["evidence_results"][0]["status"], "verified")

    def test_partial_drift_and_duplicate_paper(self):
        plan, _ = prepare(self.table, [LINK, OTHER, LINK], kinds=["metric"])
        self.table.upsert(OTHER, {"论文链接": OTHER, "阅读笔记": "别人刚填写"}, "r")
        results = commit(self.table, plan)["evidence_results"]
        self.assertEqual([r["status"] for r in results], ["verified", "failed", "skipped"])
        self.assertEqual(self.extract.call_count, 2)
        self.assertTrue(any(r["fields"].get("阅读笔记") == "别人刚填写" for r in self.table.rows()))

    def test_old_row_target_change_stops_write(self):
        self.table.upsert(LINK, {"论文链接": LINK, "阅读笔记": "先前数据"}, "a")
        plan, _ = prepare(self.table, [LINK], kinds=["metric"])
        self.table.upsert(LINK, {"阅读笔记": "刚写的内容"}, "b")
        result = commit(self.table, plan)
        self.assertEqual(result["evidence_results"][0]["status"], "failed")
        self.assertEqual(self.table.rows()[0]["fields"]["阅读笔记"], "刚写的内容")

    def test_schema_changed_after_preview_or_missing(self):
        plan, _ = prepare(self.table, [LINK], kinds=["metric"])
        self.table._db.execute("DELETE FROM demo_columns WHERE name='阅读笔记'")
        self.table._db.commit()
        self.assertEqual(commit(self.table, plan)["evidence_results"][0]["status"], "failed")
        missing, reply = prepare(self.table, [LINK], kinds=["metric"])
        self.assertEqual(missing["ready"], 0)
        self.assertIn("阅读笔记", reply["evidence_preview"][0]["error"])
        self.assertEqual(self.table.rows(), [])

    def test_wrong_source_and_unavailable_paper(self):
        def bad(url):
            if url == OTHER:
                raise ValueError("全文暂不可用")
            paper = extracted(url)
            paper["items"][0]["source_url"] = OTHER
            return paper
        self.extract.side_effect = bad
        plan, reply = prepare(self.table, [LINK, OTHER])
        self.assertEqual(plan["ready"], 0)
        self.assertEqual([p["status"] for p in reply["evidence_preview"]], ["skipped", "skipped"])
        self.assertEqual(self.table.rows(), [])

    def test_other_pinned_version_is_never_mixed_with_current_evidence(self):
        old = LINK.replace("v4", "v1")
        self.table.upsert(old, {"论文链接": old}, "a")
        plan, reply = prepare(self.table, [LINK], kinds=["figure"])
        self.assertEqual(plan["ready"], 0)
        self.assertIn("版本", reply["evidence_preview"][0]["error"])
        self.assertEqual(self.table.rows()[0]["fields"], {"论文链接": old})

    def test_bound_but_repurposed_row_must_be_checked(self):
        self.table.upsert(LINK, {"论文标题": "另一篇论文"}, "a")
        plan, reply = prepare(self.table, [LINK], kinds=["metric"])
        self.assertEqual(plan["ready"], 0)
        self.assertIn("目标行", reply["evidence_preview"][0]["error"])
        self.assertEqual(self.table.rows()[0]["fields"], {"论文标题": "另一篇论文"})

    def test_mock_feishu_reads_first_and_verifies_remote_row(self):
        table = FeishuTable(":memory:", "app", "dummy", "base", "tbl")
        self.addCleanup(table.close)
        calls, records = [], {}
        columns = [{"field_id": str(i), "field_name": k, "type": 15 if i == 0 else 1}
                   for i, k in enumerate(("论文链接", "阅读笔记"))]
        def request(method, path, body=None, query=None):
            calls.append(method)
            if path.endswith("/fields"):
                return {"data": {"items": columns}}
            if method == "POST":
                records["r1"] = {"record_id": "r1", "fields": body["fields"]}
                return {"data": {"record": records["r1"]}}
            if path.endswith("/records"):
                return {"data": {"items": list(records.values())}}
            return {"data": {"record": records["r1"]}}
        with patch.object(table, "_request", side_effect=request):
            plan, _ = prepare(table, [LINK], kinds=["metric"])
            self.assertTrue(all(method == "GET" for method in calls))
            result = commit(table, plan)
            self.assertEqual(result["evidence_results"][0]["status"], "verified")
            self.assertEqual(calls.count("POST"), 1)
            self.assertIn("44.1", records["r1"]["fields"]["阅读笔记"])

    def test_write_with_uncertain_receipt_does_not_claim_failure_or_success(self):
        plan, _ = prepare(self.table, [LINK])
        with patch.object(self.table, "upsert", side_effect=TableError("连接中断")):
            result = commit(self.table, plan)
        self.assertEqual(result["evidence_results"][0]["status"], "unverified")
        self.assertIn("0/1", result["verification"])
