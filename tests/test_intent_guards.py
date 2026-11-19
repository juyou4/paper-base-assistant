"""通过正式对话入口检查阅读、讨论和明确写入的边界。"""

import os
import tempfile
import unittest
from unittest.mock import patch

import papers
from table_store import LocalTable
from workflow import Conversation


RAG = "https://arxiv.org/abs/2005.11401"


class IntentGuardTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.table = LocalTable(":memory:")
        self.addCleanup(self.table.close)
        self.chat = Conversation(self.table, folder.name, "阅读清单")
        setting = patch.dict(os.environ, {"PAPER_AGENT_PROVIDER": "rules"})
        setting.start()
        self.addCleanup(setting.stop)

    def test_discussion_delay_and_retraction_never_write(self):
        phrases = ("如果我让你收藏这篇会怎么样", "要是这篇跟机器人相关就收藏",
                   "试着把摘要填表的话会怎样", "打个比方，把论文标题写入表格",
                   "等我看完再收藏", "下周再收藏", "我没有让你收藏它", "只有符合标准才入库",
                   "暂时不收藏", "本来想收藏，现在先不弄",
                   "把这篇放进清单，算了", "刚才说的是“收藏论文”，这是举个例子")
        with patch("papers.resolve_paper", side_effect=AssertionError("无需读取原文")):
            for phrase in phrases:
                with self.subTest(phrase=phrase):
                    result = self.chat.respond(phrase + " " + RAG)
                    self.assertNotIn("row", result)
                    self.assertEqual(self.table.rows(), [])

    def test_membership_questions_look_up_without_creating(self):
        for expected in ("未找到", "已收录"):
            for phrase in ("这篇在不在我的收藏里", "之前保存了这篇么", "清单里有没有这篇",
                           "是不是已经收藏了", "收没收过这篇"):
                with self.subTest(phrase=phrase, expected=expected):
                    reply = self.chat.respond(RAG + " " + phrase)
                    self.assertIn(expected, reply["message"])
                    self.assertEqual(len(self.table.rows()), int(expected == "已收录"))
            if expected == "未找到":
                self.chat.respond("收藏 " + RAG)

    def test_capability_question_needs_its_own_confirmation(self):
        preview = self.chat.respond("可以帮我收藏这篇吗？ " + RAG)
        self.assertEqual(preview["choices"], ["确认收藏", "取消"])
        self.assertEqual(self.table.rows(), [])
        query = self.chat.respond("那你先看看这篇是不是已经收藏了", preview["pending_id"])
        self.assertIn("未找到", query["message"])
        self.assertEqual(query["pending_id"], preview["pending_id"])
        self.chat.respond("确认收藏")
        self.assertEqual(self.table.rows(), [])
        with self.assertRaises(ValueError):
            self.chat.respond("确认收藏", "错误编号")
        self.chat.respond("取消", preview["pending_id"])
        self.assertEqual(self.table.rows(), [])
        fresh = self.chat.respond("可不可以把这篇论文收藏起来？ " + RAG)
        self.assertNotEqual(fresh["pending_id"], preview["pending_id"])
        row = self.chat.respond("确认收藏", fresh["pending_id"])["row"]
        self.assertEqual(self.table.get(row["id"])["fields"]["论文链接"], RAG)

    def test_read_abstract_then_maybe_save(self):
        for phrase in ("看看这篇摘要，之后再存", "这篇不是要收藏，只想看看摘要", "这篇的摘要是什么？",
                       "把这篇收藏起来之前我想先看看摘要", "把这篇收藏一下，不对，先看摘要"):
            with self.subTest(phrase=phrase):
                reply = self.chat.respond(phrase + " " + RAG)
                self.assertIn("维基百科", reply["message"])
                self.assertTrue(reply["evidence"][0]["url"].startswith("https://arxiv.org/"))
                self.assertEqual(self.table.rows(), [])

    def test_narrow_link_save_does_not_also_fill_abstract_or_download(self):
        with patch("papers.download_pdf", side_effect=AssertionError("不应下载 PDF")):
            for phrase in ("只保存论文链接，摘要先不用", "不想下载 PDF，只收藏链接",
                           "只存链接不写摘要", "不保存摘要和方法，只存链接", "先收藏链接，下周再填摘要"):
                with self.subTest(phrase=phrase):
                    reply = self.chat.respond(phrase + " " + RAG)
                    self.assertEqual(reply["row"]["fields"], {"论文链接": RAG})

    def test_quoted_notes_stay_literal_but_the_outer_sentence_controls_the_write(self):
        row = self.chat.respond("收藏 " + RAG)["row"]
        phrase = "如果复现成功就写入；先不收藏。"
        self.chat.respond("给刚才那篇记一句：" + phrase)
        self.chat.respond("给刚才那篇把「这篇在不在收藏里？」写进研究笔记")
        before = self.table.get(row["id"])["fields"]
        self.assertEqual(before["阅读笔记"], phrase)
        self.assertEqual(before["研究笔记"], "这篇在不在收藏里？")
        self.chat.respond("假如给刚才那篇记一句：不应覆盖")
        self.assertEqual(self.table.get(row["id"])["fields"], before)

    def test_download_and_language_in_verbatim_note_do_not_trigger_other_actions(self):
        row = self.chat.respond("收藏 " + RAG)["row"]
        with patch("papers.download_pdf", side_effect=AssertionError("笔记中的下载并非授权")):
            self.chat.respond("给刚才那篇把「下周再下载英文 PDF」写进研究笔记")
            self.chat.respond("给刚才那篇做笔记：下载英文 PDF 前先核对作者")
        fields = self.table.get(row["id"])["fields"]
        self.assertEqual(fields["研究笔记"], "下周再下载英文 PDF")
        self.assertEqual(fields["阅读笔记"], "下载英文 PDF 前先核对作者")
        with patch("papers.search_titles", side_effect=AssertionError("原样笔记不能发起外部查询")):
            self.chat.respond("给刚才那篇把「去网上找几篇关于知识蒸馏的论文」写进研究笔记")
            self.assertEqual(self.table.get(row["id"])["fields"]["研究笔记"], "去网上找几篇关于知识蒸馏的论文")

    def test_paper_request_waiting_for_a_target_cannot_consume_deferred_link(self):
        question = self.chat.respond("保存论文链接")
        guarded = self.chat.respond("等我下周再收藏 " + RAG, question["pending_id"])
        self.assertEqual(guarded["pending_id"], question["pending_id"])
        self.assertEqual(self.table.rows(), [])
        self.chat.respond(RAG, question["pending_id"])
        self.assertEqual(len(self.table.rows()), 1)

    def test_discussion_without_a_link_does_not_arm_next_pasted_link(self):
        reply = self.chat.respond("如果我想收藏会怎么样")
        self.assertNotIn("pending_id", reply)
        preview = self.chat.respond(RAG)
        self.assertIn("预览数据和图表", preview["choices"])
        self.assertEqual(self.table.rows(), [])

    def test_cancel_pending_link_save_in_everyday_speech(self):
        plan = self.chat.respond("保存论文链接")
        result = self.chat.respond("算了，先不存了", plan["pending_id"])
        self.assertNotIn("pending_id", result)
        self.assertIsNone(self.chat.pending)
        self.assertEqual(self.table.rows(), [])

    def test_read_then_collect_uses_the_new_paper_and_keeps_pending_focus(self):
        first = "https://example.org/paper/first"
        second = "https://example.org/paper/second"

        def resolve(url, *args, **kwargs):
            return papers._record(key=url, url=url, title=url.rsplit("/", 1)[-1], abstract_zh="示例摘要")

        with patch("papers.resolve_paper", side_effect=resolve):
            self.chat.respond("收藏 " + first)
            waiting = self.chat.respond("保存论文链接")
            preview = self.chat.respond("看看摘要 " + second, waiting["pending_id"])
            self.assertEqual(preview["pending_id"], waiting["pending_id"])
            self.assertEqual(self.chat.last, first)
            self.chat.respond("取消", waiting["pending_id"])
            self.chat.respond("看看摘要 " + second)
            self.assertEqual(len(self.table.rows()), 1)
            saved = self.chat.respond("收藏它")
            self.assertEqual(saved["row"]["fields"]["论文链接"], second)
            self.assertEqual(len(self.table.rows()), 2)
            self.chat.respond(f"看看这两篇的摘要 {first} {second}")
            self.assertIsNone(self.chat.last)
            self.assertIn("哪篇", self.chat.respond("收藏它")["message"])
