"""用应用会话复现选错范围、查询被当成收藏和刷新后丢失预览的问题。"""

import os
import tempfile
import unittest
from unittest.mock import patch

from app import Application


PAPERS = ["https://arxiv.org/abs/" + ident for ident in ("2005.11401", "1706.03762", "1810.04805")]


def extract(url, **kwargs):
    items = []
    for kind, label in [("metric", "准确率"), ("figure", "Figure 1"), ("table", "Table 1"), ("formula", "损失函数")]:
        items.append(dict(kind=kind, label=label, source=label, quote="example 0.85", value="0.85",
                          latex="x=0.85", source_url=url.replace("/abs/", "/html/") + "#S1"))
    return dict(url=url, title="Paper " + url.rsplit("/", 1)[1], items=items)


class RefinedConversationTests(unittest.TestCase):
    def setUp(self):
        pool = tempfile.TemporaryDirectory()
        self.addCleanup(pool.cleanup)
        config = patch.dict(os.environ, {"PAPER_AGENT_PROVIDER": "rules"})
        config.start()
        self.addCleanup(config.stop)
        self.app = Application("local", pool.name)
        self.addCleanup(self.app.close)
        self.cookie, self.chat = self.app.session(None)
        fake = patch("evidence_extract.extract_evidence", side_effect=extract)
        fake.start()
        self.addCleanup(fake.stop)

    def snapshot(self):
        return self.app.state(self.app.session(self.cookie)[1])

    def test_revise_per_paper_and_never_commit_a_superseded_selection(self):
        draft = self.chat.respond("看看关键数据和图表 " + " ".join(PAPERS))
        changed = self.chat.respond("第一篇的图和第三篇的实验数据", draft["pending_id"])
        previews = changed["evidence_preview"]["papers"]
        self.assertEqual([(p["url"], [i["kind"] for i in p["items"]]) for p in previews],
                         [(PAPERS[0], ["figure"]), (PAPERS[2], ["metric"])])
        self.assertEqual(self.snapshot()["pending_dialog"]["evidence_preview"], changed["evidence_preview"])
        with self.assertRaises(ValueError):
            self.chat.respond("确认回填", draft["pending_id"])
        final = self.chat.respond("只留最后一篇", changed["pending_id"])
        self.assertEqual([i["kind"] for i in final["evidence_preview"]["papers"][0]["items"]], ["metric"])
        self.assertEqual(self.snapshot()["rows"], [])
        self.chat.respond("确认回填", final["pending_id"], evidence_selection=[[0]])
        rows = self.snapshot()["rows"]
        self.assertEqual([r["fields"]["论文链接"] for r in rows], [PAPERS[2]])
        self.assertIsNone(self.snapshot()["pending_dialog"])

    def test_remove_an_evidence_type_or_exclude_a_paper(self):
        draft = self.chat.respond("提取关键数据、图表和公式 " + " ".join(PAPERS))
        changed = self.chat.respond("去掉公式", draft["pending_id"])
        self.assertEqual({i["kind"] for i in changed["evidence_preview"]["papers"][0]["items"]}, {"metric", "figure", "table"})
        new = self.chat.respond("跳过第二篇", changed["pending_id"])
        self.assertEqual([p["url"] for p in new["evidence_preview"]["papers"]], [PAPERS[0], PAPERS[2]])
        self.assertFalse(self.snapshot()["rows"])

    def test_out_of_bounds_requires_explicit_new_preview(self):
        draft = self.chat.respond("看看实验数据 " + " ".join(PAPERS))
        ambiguous = self.chat.respond("只留第五篇", draft["pending_id"])
        self.assertNotEqual(ambiguous["pending_id"], draft["pending_id"])
        self.assertNotIn("evidence_preview", ambiguous)
        self.assertNotIn("确认", ambiguous["choices"])
        with self.assertRaises(ValueError):
            self.chat.respond("确认回填", draft["pending_id"])
        narrowed = self.chat.respond("第1、3篇", ambiguous["pending_id"])
        self.assertEqual([p["url"] for p in narrowed["evidence_preview"]["papers"]], [PAPERS[0], PAPERS[2]])
        self.assertFalse(self.snapshot()["rows"])

    def test_scoped_mixed_request_after_pasting_urls(self):
        message = self.chat.respond(" ".join(PAPERS))
        result = self.chat.respond("第一篇看图，第二篇看表格，第三篇只看准确率", message["pending_id"])
        self.assertEqual([[i["kind"] for i in p["items"]] for p in result["evidence_preview"]["papers"]],
                         [["figure"], ["table"], ["metric"]])
        self.assertFalse(self.snapshot()["rows"])

    def test_partial_per_paper_request_waits_for_the_missing_scope(self):
        old = self.chat.respond("看看实验数据 " + " ".join(PAPERS[:2]))
        clarify = self.chat.respond("第一篇看图，第二篇", old["pending_id"])
        self.assertNotIn("evidence_preview", clarify)
        with self.assertRaises(ValueError):
            self.chat.respond("确认回填", old["pending_id"])
        scoped = self.chat.respond("第一篇看图，第二篇看实验数据", clarify["pending_id"])
        self.assertEqual([[i["kind"] for i in p["items"]] for p in scoped["evidence_preview"]["papers"]],
                         [["figure"], ["metric"]])

    def test_unresolved_singular_source_does_not_retarget_the_batch(self):
        old = self.chat.respond("看看实验数据 " + " ".join(PAPERS[:2]))
        clarify = self.chat.respond("只看 IEEE 那篇的公式", old["pending_id"])
        self.assertNotIn("evidence_preview", clarify)
        self.assertEqual(clarify["choices"][:2], ["第1篇", "第2篇"])
        scoped = self.chat.respond("第2篇", clarify["pending_id"])
        self.assertEqual([p["url"] for p in scoped["evidence_preview"]["papers"]], [PAPERS[1]])
        self.assertEqual([i["kind"] for i in scoped["evidence_preview"]["papers"][0]["items"]], ["formula"])

    def test_search_browse_and_explicit_save_are_distinct(self):
        matches = [dict(title="Example Paper " + str(i), url=url, authors=["Author"], year="2024")
                   for i, url in enumerate(PAPERS[:2], 1)]
        with patch("papers.search_titles", return_value=matches) as query:
            found = self.chat.respond("帮我找几篇关于检索增强的论文")
        query.assert_called_once_with("检索增强")
        self.assertEqual([c["url"] for c in found["collection"]], PAPERS[:2])
        viewing = self.chat.respond("第二篇", found["pending_id"])
        self.assertEqual(viewing["collection"][0]["url"], PAPERS[1])
        self.assertFalse(self.snapshot()["rows"])
        self.assertEqual(self.snapshot()["pending_dialog"]["choices"], ["收藏这篇论文", "取消"])
        with self.assertRaises(ValueError):
            self.chat.respond("收藏这篇论文", found["pending_id"])
        self.chat.respond("取消", viewing["pending_id"])
        self.assertFalse(self.snapshot()["rows"])
        with patch("papers.search_titles", return_value=matches):
            again = self.chat.respond("搜索《Example Paper》")
        saved = self.chat.respond("收藏第1篇", again["pending_id"])
        self.assertEqual(saved["row"]["fields"]["论文链接"], PAPERS[0])
