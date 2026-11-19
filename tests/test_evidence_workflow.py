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

    def test_select_items_in_shared_column_and_exclude_whole_paper(self):
        self.table.upsert(LINK, {"论文链接": LINK, "阅读笔记": "我自己的笔记"}, "r")
        plan, _ = prepare(self.table, [LINK, OTHER])
        result = commit(self.table, plan, selection=[[1, 3], []])
        self.assertEqual([p["status"] for p in result["evidence_results"]], ["verified", "excluded"])
        self.assertIn("1/1", result["verification"])
        self.assertEqual(len(self.table.rows()), 1)
        cells = self.table.rows()[0]["fields"]
        self.assertIn("我自己的笔记", cells["阅读笔记"])
        self.assertIn("45.5", cells["阅读笔记"])
        self.assertNotIn("44.1", cells["阅读笔记"])
        self.assertIn("Figure 1", cells["研究笔记"])
        self.assertNotIn("【表】", cells["研究笔记"])
        with patch.object(self.table, "upsert", side_effect=AssertionError("已经完成的批次不能重写")):
            self.assertEqual(commit(self.table, plan, selection=[[0], [0]]), result)

    def test_invalid_selection_anywhere_rejects_entire_request_before_first_write(self):
        plan, _ = prepare(self.table, [LINK, OTHER, LINK])
        bad = ([[0], []], [[0], [8], []], [[0], [-1], []], [[0], [True], []],
               [[0], [1.0], []], [[0], [1, 1], []], [[0], "1", []], [[0], [], [0]],
               {"url": LINK, "value": "任意文字"}, [[], [], []])
        with patch.object(self.table, "upsert", side_effect=AssertionError("非法勾选不应写入")):
            for selection in bad:
                with self.subTest(selection=selection), self.assertRaises(ValueError):
                    commit(self.table, plan, selection=selection)
        self.assertFalse(self.table.rows())
        self.assertEqual(commit(self.table, plan, selection=[[0], [], []])["evidence_results"][0]["status"], "verified")

    def test_drift_checks_only_selected_columns_and_never_overwrites_other_edits(self):
        self.table.upsert(LINK, {"论文链接": LINK, "研究笔记": "旧笔记"}, "first")
        plan, _ = prepare(self.table, [LINK])
        self.table.upsert(LINK, {"研究笔记": "别人刚更新"}, "edit")
        with patch.object(self.table, "upsert", wraps=self.table.upsert) as send:
            self.assertEqual(commit(self.table, plan, selection=[[0]])["evidence_results"][0]["status"], "verified")
            self.assertNotIn("研究笔记", send.call_args.args[1])
            self.assertNotIn("研究笔记", send.call_args.kwargs["expected_fields"])
        self.assertEqual(self.table.rows()[0]["fields"]["研究笔记"], "别人刚更新")
        second, _ = prepare(self.table, [LINK])
        self.table.upsert(LINK, {"阅读笔记": "我修改了目标列"}, "edit2")
        self.assertEqual(commit(self.table, second, selection=[[1]])["evidence_results"][0]["status"], "failed")

    def test_publisher_alias_preserves_original_link_and_uses_verified_pdf_page(self):
        doi = "https://doi.org/10.1109/CVPR.2016.90"
        ieee = "https://ieeexplore.ieee.org/document/7780459"
        pdf = ("https://openaccess.thecvf.com/content_cvpr_2016/papers/"
               "He_Deep_Residual_Learning_CVPR_2016_paper.pdf")
        row = self.table.upsert(ieee, {"论文链接": ieee, "研究笔记": "会前留的笔记"}, "r")
        item = dict(kind="metric", label="Top-1", value="25.03", quote="Top-1 error 25.03",
                    source="CVF 第 4 页", source_url=pdf + "#page=4")
        self.extract.side_effect = lambda url: dict(title="Deep Residual Learning for Image Recognition",
            url=url, aliases=[ieee, doi], evidence_urls=[url, pdf], items=[item])
        plan, reply = prepare(self.table, [doi, ieee], kinds=["metric"])
        self.assertEqual([e["status"] for e in reply["evidence_preview"]], ["ready", "skipped"])
        self.assertEqual(reply["evidence_preview"][0]["target_url"], ieee)
        results = commit(self.table, plan, selection=[[0], []])["evidence_results"]
        self.assertEqual(results[0]["record_id"], row["record_id"])
        self.assertEqual(len(self.table.rows()), 1)
        fields = self.table.rows()[0]["fields"]
        self.assertEqual((fields["论文链接"], fields["研究笔记"]), (ieee, "会前留的笔记"))
        self.assertIn(pdf + "#page=4", fields["阅读笔记"])

        second, _ = prepare(self.table, [doi], kinds=["metric"])
        self.table.upsert(doi, {"论文链接": doi}, "new")
        self.assertEqual(commit(self.table, second, selection=[[0]])["evidence_results"][0]["status"], "failed")

        # 同篇的另一个出版入口不代表这份证据实际来自该版本。
        self.extract.side_effect = lambda url: dict(title="ResNet", url=url, aliases=[pdf], items=[item])
        unverified, reply = prepare(self.table, [doi], kinds=["metric"])
        self.assertEqual(unverified["ready"], 0)
        self.assertIn("定位链接", reply["evidence_preview"][0]["error"])

    def test_cvf_landing_resolving_to_canonical_doi_requires_verified_version_url(self):
        page = ("https://openaccess.thecvf.com/content_cvpr_2016/html/"
                "He_Deep_Residual_Learning_CVPR_2016_paper.html")
        pdf = page.replace("/html/", "/papers/").removesuffix(".html") + ".pdf"
        doi = "https://doi.org/10.1109/cvpr.2016.90"
        row = self.table.upsert(doi, {"论文链接": doi}, "existing")
        item = dict(kind="metric", label="Top-1", value="25.03", quote="Top-1 error 25.03",
                    source="Table 2", source_url=pdf + "#page=5")
        paper = dict(title="Deep Residual Learning for Image Recognition", url=doi,
                     evidence_urls=[doi, pdf], aliases=[doi, page], items=[item])
        self.extract.side_effect = lambda _: paper
        unverified, _ = prepare(self.table, [page], kinds=["metric"])
        self.assertEqual(unverified["ready"], 0)
        paper["evidence_urls"].append(page)
        plan, _ = prepare(self.table, [page], kinds=["metric"])
        receipt = commit(self.table, plan, selection=[[0]])["evidence_results"][0]
        self.assertEqual((receipt["status"], receipt["record_id"]), ("verified", row["record_id"]))
        self.assertEqual(len(self.table.rows()), 1)

    def test_latex_survives_preview_confirm_readback_and_repeat(self):
        latex = "\\begin{aligned}\ny &= \\frac{x_1}{2} \\\\\nz &= x^{2}\n\\end{aligned}"
        item = dict(kind="formula", label="Equation 1", value=latex, latex=latex, quote=latex,
                    source="式 (1)", source_url=LINK.replace("/abs/", "/html/") + "#eq1")

        def formula(url, *, include_formulas):
            self.assertTrue(include_formulas)
            return {"url": url, "title": "示例", "items": [item]}

        self.extract.side_effect = formula
        for field in ("研究笔记", "论文公式"):
            with self.subTest(field=field):
                if field == "论文公式":
                    self.table.add_field(field, "text")
                before = self.table.rows()
                plan, response = prepare(self.table, [LINK], kinds=["formula"])
                preview = response["evidence_preview"][0]["items"][0]
                self.assertEqual((preview["field"], preview["latex"]), (field, latex))
                self.assertEqual(self.table.rows(), before)
                receipt = commit(self.table, plan)["evidence_results"][0]
                self.assertEqual(receipt["status"], "verified")
                saved = self.table.get(receipt["record_id"])["fields"][field]
                self.assertIn("$$\n" + latex + "\n$$", saved)
                self.assertIn(item["source_url"], saved)
                again, _ = prepare(self.table, [LINK], kinds=["formula"])
                self.assertEqual(again["entries"][0]["action"], "unchanged")

    def test_missing_math_source_is_visible_before_confirming(self):
        self.extract.side_effect = lambda url, **_: extracted(url)
        only, response = prepare(self.table, [LINK], kinds=["formula"])
        self.assertEqual(only["ready"], 0)
        self.assertIn("公式源码", response["evidence_preview"][0]["error"])
        mixed, response = prepare(self.table, [LINK], kinds=["metric", "formula"])
        self.assertEqual(mixed["ready"], 1)
        paper = response["evidence_preview"][0]
        self.assertIn("公式源码", paper["notice"])
        self.assertEqual({i["kind"] for i in paper["items"]}, {"metric"})
        self.assertFalse(self.table.rows())

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

    def test_concurrent_batch_extraction_preserves_order_and_context(self):
        import threading
        import activity

        barrier = threading.Barrier(2, timeout=3)

        def parallel_extract(url):
            barrier.wait()
            return extracted(url)

        self.extract.side_effect = parallel_extract
        run = activity.Run("req-concurrent")
        with run.recording():
            plan, reply = prepare(self.table, [LINK, OTHER], kinds=["metric"])
        self.assertEqual(plan["ready"], 2)
        self.assertEqual([p["url"] for p in reply["evidence_preview"]], [LINK, OTHER])
        labels = [e["label"] for e in run.snapshot()["events"]]
        self.assertIn("提取第 1 篇论文的原文证据", labels)
        self.assertIn("提取第 2 篇论文的原文证据", labels)

    def test_field_overrides_route_to_custom_column_and_guard_drift(self):
        self.table.add_field("实验结果", "text")
        self.table.upsert(LINK, {"论文链接": LINK, "实验结果": "已有记录"}, "seed")
        plan, reply = prepare(self.table, [LINK, OTHER], kinds=["metric"])
        preview = reply["evidence_preview"]
        self.assertIn("实验结果", preview[0]["target_columns"])
        self.assertNotIn("论文链接", preview[0]["target_columns"])
        self.assertNotIn("论文标题", preview[0]["target_columns"])

        for bad in ([[{"0": "实验结果"}]], [{"0": "论文链接"}, None], [{"0": "论文标题"}, None],
                    [{"0": "不存在列"}, None], [{"99": "实验结果"}, None]):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                commit(self.table, plan, selection=[[0], [0]], field_overrides=bad)

        result = commit(self.table, plan, selection=[[0], [1]],
                        field_overrides=[{"0": "实验结果"}, {"1": "研究笔记"}])
        self.assertEqual([r["status"] for r in result["evidence_results"]], ["verified", "verified"])
        by_url = {r["fields"]["论文链接"]: r["fields"] for r in self.table.rows()}
        self.assertIn("已有记录", by_url[LINK]["实验结果"])
        self.assertIn("44.1", by_url[LINK]["实验结果"])
        self.assertNotIn("阅读笔记", by_url[LINK])
        self.assertIn("45.5", by_url[OTHER]["研究笔记"])

        # 覆盖后的目标列同样受并发漂移保护。
        plan2, _ = prepare(self.table, [LINK], kinds=["metric"])
        self.table.upsert(LINK, {"实验结果": "他人刚改动"}, "drift")
        failed = commit(self.table, plan2, selection=[[1]], field_overrides=[{"1": "实验结果"}])
        self.assertEqual(failed["evidence_results"][0]["status"], "failed")
        link_row = next(r for r in self.table.rows() if r["fields"].get("论文链接") == LINK)
        self.assertEqual(link_row["fields"]["实验结果"], "他人刚改动")

    def test_workflow_confirm_supports_evidence_fields(self):
        from workflow import Conversation

        self.table.add_field("自定义数据列", "text")
        chat = Conversation(self.table, ".", "阅读清单")
        preview_reply = chat.respond(f"提取关键数据 {LINK}")
        pid = preview_reply["pending_id"]
        with self.assertRaises(ValueError):
            chat.respond("取消回填 " + pid, pending_id=pid, evidence_fields=[{"0": "自定义数据列"}])
        confirmed = chat.respond("确认回填 " + pid, pending_id=pid,
                                 evidence_selection=[[0]], evidence_fields=[{"0": "自定义数据列"}])
        self.assertEqual(confirmed["evidence_results"][0]["status"], "verified")
        self.assertIn("44.1", self.table.rows()[0]["fields"]["自定义数据列"])
