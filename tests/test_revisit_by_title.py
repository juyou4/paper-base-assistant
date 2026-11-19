"""重现搜到论文、入表，然后用半个标题重看原记录的连续对话。"""

import json
import os
import unittest
from unittest.mock import patch
from copy import deepcopy

import papers
from table_store import FeishuTable, LocalTable, RecordNotFound, _identity
from test_table_store import FakeFeishu
from workflow import Conversation


TITLE = "Attention Is All You Need"
URL = "https://arxiv.org/abs/1706.03762"
NOTE = "下次组会重点讲编码器第六层"
ABSTRACT = "已核对的自注意力机制摘要"
OTHER = "https://example.org/attention-survey"
OTHER_TITLE = "Attention Mechanisms for Vision"


class RevisitByTitleTests(unittest.TestCase):
    def setUp(self):
        self.table = LocalTable(":memory:")
        self.addCleanup(self.table.close)
        self.chat = Conversation(self.table, ".", "阅读清单")
        env = patch.dict(os.environ, PAPER_AGENT_PROVIDER="rules")
        env.start()
        self.addCleanup(env.stop)

    def save_via_chat(self):
        paper = papers._record(key=_identity(URL), url=URL, title=TITLE, year="2017")
        with patch("papers.search_titles", return_value=[paper]) as remote, patch("papers.resolve_paper", return_value=paper):
            hits = self.chat.respond("去网上找几篇关于 Attention Is All You Need 的论文")
            self.chat.respond("收藏第一篇", hits["pending_id"])
            remote.assert_called_once_with(TITLE)
        self.chat.respond("给刚才那篇记一句：" + NOTE)
        self.table.upsert(URL, {"中文摘要": ABSTRACT}, "reviewed-abstract")

    def test_short_title_reopens_existing_content_without_search_or_new_write(self):
        self.save_via_chat()
        before = self.table.rows()
        for phrase, chat in (("再看看attention", self.chat),
                             ("我之前收藏的 attention 呢", Conversation(self.table, ".", "阅读清单"))):
            with self.subTest(phrase=phrase), patch("papers.search_titles", side_effect=AssertionError("重看不应查外部题录")), \
                    patch("papers.resolve_paper", side_effect=AssertionError("表中已有记录")), \
                    patch("evidence_extract.extract_evidence", side_effect=AssertionError("重看无需重新提取原文")), \
                    patch.object(self.table, "upsert", side_effect=AssertionError("重看不可写表")):
                reply = chat.respond(phrase)
                # 消息与卡片是用户可见的内容，不能只在后端返回隐藏的 row。
                shown = json.dumps([reply["message"], reply.get("collection")], ensure_ascii=False)
                self.assertIn(TITLE, shown)
                self.assertTrue(ABSTRACT in shown or NOTE in shown, shown)
        self.assertEqual(self.table.rows(), before)

    def test_explicit_public_search_still_searches_after_collecting_same_paper(self):
        self.save_via_chat()
        before = self.table.rows()
        with patch("papers.search_titles", return_value=[]) as remote:
            self.chat.respond("去网上找几篇关于 Attention 的论文")
            remote.assert_called_once_with("Attention")
        self.assertEqual(self.table.rows(), before)

    def seed_overlapping_titles(self):
        self.table.upsert(URL, {"论文标题": TITLE, "论文链接": URL, "中文摘要": ABSTRACT}, "one")
        self.table.upsert(OTHER, {"论文标题": OTHER_TITLE, "论文链接": OTHER, "中文摘要": "视觉领域综述摘要"}, "two")

    def test_two_matches_require_a_choice_and_bind_the_chosen_record(self):
        self.seed_overlapping_titles()
        with patch("papers.search_titles", side_effect=AssertionError("表内消歧不能联网")), \
                patch("papers.resolve_paper", side_effect=AssertionError("候选来自当前表")):
            offer = self.chat.respond("再看看attention")
            self.assertIn(TITLE, offer["message"])
            self.assertIn(OTHER_TITLE, offer["message"])
            self.assertIsNone(self.chat.last)
            index = next(i for i, label in enumerate(offer["choices"], 1) if TITLE in label)
            answer = self.chat.respond(f"选第{index}篇", offer["pending_id"])
            self.assertIn(TITLE, answer["message"])
            self.assertIn(ABSTRACT, answer["message"])
            self.assertNotIn(OTHER_TITLE, answer["message"])
            note = self.chat.respond("给这篇记一句：只更新刚选中的论文")
            self.assertEqual(note["row"]["fields"]["论文链接"], URL)
            other = next(row for row in self.table.rows() if row["fields"]["论文链接"] == OTHER)
            self.assertNotIn("阅读笔记", other["fields"])

    def test_renamed_record_requires_new_selection(self):
        self.seed_overlapping_titles()
        offer = self.chat.respond("再看看attention")
        label = next(label for label in offer["choices"] if TITLE in label)
        self.table.upsert(URL, {"论文标题": "已更换的论文"}, "changed")
        with patch("papers.search_titles", side_effect=AssertionError("选择失效不能触发外搜")):
            answer = self.chat.respond(label, offer["pending_id"])
        self.assertIsNone(self.chat.last)
        self.assertNotIn(ABSTRACT, answer["message"])
        self.assertRegex(answer["message"], "变|重新")

    def test_acronym_opens_from_another_chat_and_skips_embedded_letters(self):
        title = "Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks"
        self.table.upsert(URL, {"论文标题": title, "论文链接": URL, "中文摘要": ABSTRACT}, "real-rag")
        self.table.upsert(OTHER, {"论文标题": "Data Storage for GraphRAG", "论文链接": OTHER}, "noise")
        with patch("papers.search_titles", side_effect=AssertionError("简称只查当前表")):
            for phrase in ("再看看rag", "RAG", "RAG 那篇的摘要呢"):
                chat = Conversation(self.table, ".", "阅读清单")
                result = chat.respond(phrase)
                self.assertIn(title, result["message"])
                self.assertIn(ABSTRACT, result["message"])
                self.assertNotIn("GraphRAG", result["message"])
                self.assertIn("简称", result["message"])

    def test_acronym_collisions_require_selection_even_with_an_explicit_name(self):
        self.table.upsert(URL, {"论文标题": "Retrieval-Augmented Generation", "论文链接": URL}, "expanded")
        self.table.upsert(OTHER, {"论文标题": "RAG in Education", "论文链接": OTHER}, "explicit")
        result = self.chat.respond("再看看RAG")
        self.assertEqual(len(result["choices"]), 3)
        self.assertNotIn("saved_record_id", result)
        with patch.object(self.table, "get", side_effect=RecordNotFound("已删除")):
            gone = self.chat.respond("打开第一篇", result["pending_id"])
        self.assertIn("重新", gone["message"])
        self.assertIsNone(self.chat.pending)

    def test_versioned_title_does_not_open_a_different_model(self):
        self.table.upsert(OTHER, {"论文标题": "Evaluating GPT-3.5", "论文链接": OTHER}, "new-model")
        self.assertNotIn("saved_record_id", self.chat.respond("再看看 GPT-3"))
        self.assertIn("0 条", self.chat.respond("在清单里搜 GPT-3")["message"])

    def test_asking_for_abstract_only_never_downloads_or_exposes_other_notes(self):
        self.table.upsert(URL, {"论文标题": TITLE, "论文链接": URL, "研究笔记": NOTE}, "no-abstract")
        with patch("papers.search_titles", side_effect=AssertionError("表内重看不能联网")), \
                patch("papers.resolve_paper", side_effect=AssertionError("用户只要表中现有摘要")), \
                patch("papers.download_pdf", side_effect=AssertionError("缺少已存摘要不能下载 PDF")):
            msg = "再看看attention的摘要"
            missing = self.chat.respond(msg)["message"]
            self.assertIn("摘要", missing)
            self.assertRegex(missing, "没有|未|暂无")
            self.assertNotIn(NOTE, missing)
            self.table.upsert(URL, {"中文摘要": ABSTRACT}, "has-abstract")
            shown = self.chat.respond(msg)["message"]
            self.assertIn(ABSTRACT, shown)
            self.assertNotIn(NOTE, shown)

    def test_reading_during_a_preview_keeps_pending_operation_and_previous_binding(self):
        self.table.upsert(URL, {"论文标题": TITLE, "论文链接": URL, "阅读笔记": NOTE}, "mine")
        for stage in ("admin", "evidence"):
            chat = Conversation(self.table, ".", "阅读清单")
            chat.last, chat.last_title, chat.recent_saved = OTHER, OTHER_TITLE, [OTHER]
            if stage == "admin":
                offer = chat.respond("新增文本列 复核状态")
            else:
                with patch("evidence_workflow.prepare", return_value=(
                    {"ready": 1}, {"message": "请核对", "evidence_preview": [{"title": OTHER_TITLE}]}
                )):
                    offer = chat.respond("提取关键数据 " + OTHER)
                self.assertNotIn("choices", chat.pending)
            pending = deepcopy(chat.pending)
            binding = deepcopy((chat.last, chat.last_title, chat.recent_saved, chat.recent_evidence))
            with self.subTest(stage=stage), patch("evidence_workflow.commit", side_effect=AssertionError("重看不能确认")), \
                    patch("papers.search_titles", side_effect=AssertionError("不能外搜")):
                result = chat.respond("再看看attention", offer["pending_id"])
                self.assertIn(NOTE, result["message"])
                self.assertEqual(result["pending_id"], pending["id"])
                self.assertEqual(chat.pending, pending)
                self.assertEqual((chat.last, chat.last_title, chat.recent_saved, chat.recent_evidence), binding)

    def test_missing_title_never_leaves_the_connected_table(self):
        self.table.upsert(OTHER, {"论文标题": "Another paper", "论文链接": OTHER}, "another")
        with patch("papers.search_titles", side_effect=AssertionError("无匹配时不能自动外搜")), \
                patch("papers.resolve_paper", side_effect=AssertionError("未指定要解析的来源")):
            result = self.chat.respond("再看看attention")
        self.assertIn("阅读清单", result["message"])
        self.assertRegex(result["message"], "没有|未找到|查不到")
        self.assertIsNone(self.chat.last)
        self.assertEqual(len(self.table.rows()), 1)

    def test_revisit_reads_feishu_records_including_a_manually_edited_note(self):
        table = FeishuTable(":memory:", "app", "dummy", "base", "tbl")
        self.addCleanup(table.close)
        remote = FakeFeishu()
        remote.columns.extend([
            {"field_id": "title", "field_name": "论文标题", "type": 1},
            {"field_id": "note", "field_name": "阅读笔记", "type": 1},
        ])
        remote.records = {"saved": {"论文标题": TITLE, "论文链接": URL, "阅读笔记": NOTE}}
        chat = Conversation(table, ".", "飞书测试表")
        with patch.object(table, "_request", side_effect=remote) as http, \
                patch("papers.search_titles", side_effect=AssertionError("表内重看不查询书目")), \
                patch("papers.resolve_paper", side_effect=AssertionError("无需重读来源")):
            self.assertIn(NOTE, chat.respond("再看看attention")["message"])
            remote.records["saved"]["阅读笔记"] = "刚在飞书补过的新笔记"
            self.assertIn("刚在飞书补过的新笔记", chat.respond("再看看attention")["message"])
        self.assertEqual({call.args[0] for call in http.call_args_list}, {"GET"})
