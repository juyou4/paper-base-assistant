"""在自然对话入口验证预览范围、指代和提交边界。"""

import unittest
from unittest.mock import patch

from table_store import LocalTable
from workflow import Conversation


RAG = "https://arxiv.org/abs/2005.11401"
BERT = "https://arxiv.org/abs/1810.04805"


class EvidencePhrasingTests(unittest.TestCase):
    def setUp(self):
        self.table = LocalTable(":memory:")
        self.addCleanup(self.table.close)
        self.chat = self.conversation()
        m = patch("evidence_workflow.prepare", side_effect=self.preview)
        self.prepare = m.start()
        self.addCleanup(m.stop)

    def conversation(self):
        return Conversation(self.table, ".", "阅读清单")

    def preview(self, table, urls, kinds):
        return {"ready": len(urls)}, {"message": "待核对", "evidence_preview": [
            {"title": url, "url": url, "status": "ready", "items": []} for url in urls]}

    def test_colloquial_scopes_do_not_need_model(self):
        cases = [
            ("帮我把这篇的分数和图挑出来，等我看完再填", ["metric", "figure"]),
            ("图和跑分先让我过目", ["metric", "figure"]),
            ("原文有哪些图表？", ["figure", "table"]),
            ("准确率有多少？看看原表", ["metric", "table"]),
            ("这篇只看架构图，数据不用了", ["figure"]),
            ("图表先不用，只给我看看数据", ["metric"]),
            ("不要直接写，先看看跑分", ["metric"]),
            ("实验结果给我整理进飞书多维表格", ["metric"]),
        ]
        with patch("evidence_intent.classify") as model:
            for wording, kinds in cases:
                with self.subTest(wording=wording):
                    response = self.conversation().respond(wording + " " + RAG)
                    self.assertIn("pending_id", response)
                    self.prepare.assert_called_with(self.table, [RAG], kinds=kinds)
            model.assert_not_called()
        self.assertFalse(self.table.rows())

    def test_bare_links_scope_and_refinements(self):
        question = self.chat.respond(RAG + "\n" + BERT)
        self.prepare.assert_not_called()
        self.assertIn("预览数据和图表", question["choices"])
        original = self.chat.respond("就先看原图吧", question["pending_id"])
        self.prepare.assert_called_with(self.table, [RAG, BERT], kinds=["figure"])
        narrowed = self.chat.respond("第二篇只看跑分", original["pending_id"])
        self.prepare.assert_called_with(self.table, [BERT], kinds=["metric"])
        self.assertNotEqual(original["pending_id"], narrowed["pending_id"])
        with self.assertRaises(ValueError):
            self.chat.respond("确认回填", original["pending_id"])
        self.chat.respond("取消", narrowed["pending_id"])
        self.chat.respond("刚才那篇有哪些图？")
        self.prepare.assert_called_with(self.table, [BERT], kinds=["figure"])
        self.assertFalse(self.table.rows())

    def test_pasted_sources_without_spaces_preserve_order_and_preview_only(self):
        acl = "https://aclanthology.org/2020.acl-main.703/"
        cvf = "https://openaccess.thecvf.com/content_cvpr_2016/html/He_Deep_Residual_Learning_CVPR_2016_paper.html"
        review = "https://openreview.net/forum?id=VtmBAGCN7o"
        doi = "10.1000/(abc);v1"
        links = [RAG, BERT, acl, cvf, review]
        cases = [
            ("".join(links), links),
            (cvf + acl + review, [cvf, acl, review]),
            ("；".join(links) + "、" + BERT, links),
            (";".join(links), links),
            ("|".join(links), links),
            ("：".join(links), links),
            ("[实验表](" + RAG + ")[只收藏链接](" + acl + ")", [RAG, acl]),
            (doi + RAG + "；" + review + ";" + doi, ["https://doi.org/" + doi, RAG, review]),
            (acl + ";" + doi, [acl, "https://doi.org/" + doi]),
            ("https://example.org/share?redirect=" + RAG + "|" + BERT,
             ["https://example.org/share?redirect=" + RAG, BERT]),
            (review + "#source=" + RAG, [review]),
        ]
        with patch("model_support.enabled", return_value=True), patch("evidence_intent.classify") as classify:
            for pasted, expected in cases:
                with self.subTest(pasted=pasted):
                    chat = self.conversation()
                    result = chat.respond(pasted)
                    self.assertIn("预览数据和图表", result["choices"])
                    chat.respond("只看关键数据", result["pending_id"])
                    self.prepare.assert_called_with(self.table, expected, kinds=["metric"])
            classify.assert_not_called()
        self.assertEqual(self.table.rows(), [])

    def test_glued_batch_limit_applies_before_preview(self):
        links = [f"https://example.org/paper/{n}" for n in range(9)]
        self.chat.respond("提取关键数据 " + "".join(links[:8]) + links[0])
        self.prepare.assert_called_once_with(self.table, links[:8], kinds=["metric"])
        with self.assertRaisesRegex(ValueError, "最多.*8"):
            self.conversation().respond("提取关键数据 " + "".join(links))
        self.assertEqual(self.table.rows(), [])

    def test_pending_single_target_and_literal_note_with_glued_links(self):
        prompt = self.chat.respond("保存论文链接")
        response = self.chat.respond(RAG + BERT, prompt["pending_id"])
        self.assertIn("指定一篇", response["message"])
        self.assertEqual(self.table.rows(), [])
        saved = self.chat.respond(RAG, response["pending_id"])
        note = "比较原文：" + RAG + BERT
        result = self.chat.respond("给刚才那篇记一句：" + note)
        self.assertEqual(result["row"]["id"], saved["row"]["id"])
        self.assertEqual(result["row"]["fields"]["阅读笔记"], note)
        quoted = "另一个参考：" + BERT + RAG
        changed = self.chat.respond("给刚才那篇把「" + quoted + "」写进阅读笔记")
        self.assertEqual(changed["row"]["id"], saved["row"]["id"])
        self.assertEqual(changed["row"]["fields"]["阅读笔记"], quoted)

    def test_bare_paper_can_switch_to_normal_link_save(self):
        question = self.chat.respond(RAG)
        result = self.chat.respond("只收藏链接", question["pending_id"])
        self.assertEqual(result["row"]["fields"]["论文链接"], RAG)
        self.prepare.assert_not_called()

    def test_select_one_of_the_pasted_links(self):
        question = self.chat.respond(RAG + "\n" + BERT)
        self.chat.respond("只看第二篇里的架构图", question["pending_id"])
        self.prepare.assert_called_once_with(self.table, [BERT], kinds=["figure"])

    def test_multi_paper_request_can_narrow_to_saving_a_link(self):
        question = self.chat.respond(RAG + "\n" + BERT)
        next_question = self.chat.respond("只收藏链接", question["pending_id"])
        self.assertFalse(self.table.rows())
        answer = self.chat.respond("第一篇", next_question["pending_id"])
        self.assertEqual(answer["row"]["fields"]["论文链接"], RAG)
        self.prepare.assert_not_called()

    def test_asks_for_missing_reference_then_uses_answer(self):
        question = self.chat.respond("把对比实验的结果和架构图挑出来吧")
        self.assertIn("链接", question["message"])
        self.prepare.assert_not_called()
        self.chat.respond(BERT, question["pending_id"])
        self.prepare.assert_called_with(self.table, [BERT], kinds=["metric", "figure"])

    def test_semantic_gate_is_not_a_fixed_verb_list(self):
        case = "这篇在同行面前拿什么说话？ " + RAG
        with patch("model_support.enabled", return_value=True), patch("evidence_intent.classify",
                return_value={"kinds": ["metric"], "use_previous": False}) as classify:
            self.chat.respond(case)
            classify.assert_called_once_with(case, "阅读清单")
            self.prepare.assert_called_once_with(self.table, [RAG], kinds=["metric"])
        self.assertFalse(self.table.rows())

    def test_question_or_negated_paper_choice_never_saves_a_link(self):
        first = self.chat.respond(RAG + "\n" + BERT)
        choice = self.chat.respond("只收藏链接", first["pending_id"])
        for answer in ("不要第一篇", "是第二篇吗？", "第一篇还是第二篇", "不要这篇 " + RAG):
            with self.subTest(answer=answer):
                self.chat.respond(answer, choice["pending_id"])
                self.assertFalse(self.table.rows())
        self.chat.respond("第一篇", choice["pending_id"])
        self.assertEqual(len(self.table.rows()), 1)

    def test_status_and_negation_do_not_create_the_row_being_queried(self):
        for why in ("现在已经保存链接了吗？", "有没有收录这篇", "我不需要保存这篇的链接"):
            answer = self.chat.respond(RAG + " " + why)
            self.assertNotIn("row", answer)
            self.assertFalse(self.table.rows())
        self.assertIn("论文标题", self.chat.respond("请列出数据表结构")["message"])
        self.chat.respond("保存这篇论文的链接 " + RAG)
        self.assertIn("已收录", self.chat.respond(RAG + " 之前保存了吗？")["message"])

    def test_uncertain_preview_does_not_degrade_into_default_write(self):
        with patch("model_support.enabled", return_value=True), patch("evidence_intent.classify", return_value=None):
            answer = self.chat.respond("帮我看看论文里值得记录的东西，先给我过目 " + RAG)
            self.assertIn("预览数据和图表", answer["choices"])
        self.assertFalse(self.table.rows())

    def test_confirm_requires_scope_and_explicit_authorization(self):
        response = self.chat.respond("查看能说明表现的准确率 " + RAG)
        token = response["pending_id"]
        with patch("evidence_workflow.commit", return_value={"message": "已回读"}) as commit:
            for phrase in ("好", "确认写入？", "是不是确认写入就会填表"):
                self.assertEqual(self.chat.respond(phrase, token), response)
            commit.assert_not_called()
            self.assertEqual(self.chat.respond("确认写入", token)["message"], "已回读")
            self.assertEqual(self.chat.respond("确认写入", token)["message"], "已回读")
            commit.assert_called_once()

    def test_no_model_detour_for_ordinary_requests(self):
        with patch("evidence_intent.classify") as classify:
            self.chat.respond("请不要提取这篇的实验结果 " + RAG)
            self.chat.respond("查看字段「研究笔记」")
            self.chat.respond("把 " + RAG + " 收进阅读清单")
            self.chat.respond("把「指标下周再核验」写进阅读笔记")
            self.assertEqual(len(self.table.rows()), 1)
            classify.assert_not_called()
            self.prepare.assert_not_called()
