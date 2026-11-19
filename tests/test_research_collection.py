"""真实的对话入口：混合收藏、跨来源续写以及批次确认。"""

import os
import tempfile
import unittest
from unittest.mock import patch

import papers
from table_store import LocalTable, TableError, _identity
from workflow import Conversation


DOI = "https://doi.org/10.1109/cvpr.2016.90"
IEEE = "https://ieeexplore.ieee.org/document/7780459"
CVF = "https://openaccess.thecvf.com/content_cvpr_2016/html/He_Deep_Residual_Learning_CVPR_2016_paper.html"
OTHER = "https://example.org/another-paper"


def resolve(url, *args, **kwargs):
    if url == OTHER:
        return papers._record(key=_identity(url), url=url, title="Another paper")
    if url.startswith(IEEE):
        return papers._record(key=_identity(IEEE), url=IEEE, aliases=[IEEE])
    return papers._record(key=_identity(DOI), url=DOI, title="Deep Residual Learning for Image Recognition",
                         authors=["Kaiming He"], year="2016", aliases=[CVF, DOI, IEEE])


class ResearchCollectionTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.db = LocalTable(":memory:")
        self.addCleanup(self.db.close)
        self.chat = Conversation(self.db, temp.name, "阅读清单")
        for m in (patch.dict(os.environ, {"PAPER_AGENT_PROVIDER": "rules"}),
                  patch("papers.resolve_paper", side_effect=resolve)):
            m.start()
            self.addCleanup(m.stop)

    def test_mixed_paste_merges_verified_sources_in_either_order(self):
        for first, second in [(IEEE, DOI), (DOI, IEEE)]:
            with self.subTest(first=first):
                db = LocalTable(":memory:")
                self.addCleanup(db.close)
                chat = Conversation(db, ".", "阅读清单")
                batch = chat.respond(f"组会上提到的几篇都先收着\n{first}\n{second}\n{CVF}\n{OTHER}")
                self.assertEqual(len(db.rows()), 2)
                self.assertIn("4/4", batch["message"])
                ids = [card["record_id"] for card in batch["collection"]]
                self.assertEqual(len(ids), 2)
                self.assertNotEqual(*ids)
                note = chat.respond("给第二篇记一句：组会前复现表二")
                self.assertEqual(note["row"]["id"], ids[1])
                self.assertNotIn("阅读笔记", db.get(ids[0])["fields"])
                following = chat.respond("给第一篇记一句：检查模型对照组")
                self.assertEqual(following["row"]["id"], ids[0])
                self.assertEqual(db.get(ids[1])["fields"]["阅读笔记"], "组会前复现表二")
                later = Conversation(db, ".", "阅读清单")
                sources = later.respond("查看这篇的来源链接 " + IEEE)["collection"][0]
                self.assertEqual(sources["title"], "Deep Residual Learning for Image Recognition")
                self.assertEqual(set(sources["sources"]), {DOI, CVF, IEEE})
                self.assertEqual(sources["authors"], ["Kaiming He"])
                self.assertIn("已收录", later.respond(CVF + " 有没有收录这篇")["message"])

    def test_share_fragment_and_doi_are_not_instruction_words(self):
        self.chat.respond("把 " + IEEE + "?utm_source=share#abstract 存进阅读清单")
        self.chat.respond("收藏 10.1109/CVPR.2016.90")
        self.assertEqual(len(self.db.rows()), 1)
        self.assertNotIn("中文摘要", self.db.rows()[0]["fields"])

    def test_literal_note_references_and_existing_paragraphs(self):
        self.chat.respond("收藏 " + CVF)
        for note in ["先对比表三的消融结果", "周五复现表二，先不要更新指标。"]:
            self.chat.respond("给刚才那篇补一句阅读笔记：" + note)
        self.chat.respond("给这篇记一句，周五复现表二，先不要更新指标。")
        self.assertEqual(self.db.rows()[0]["fields"]["阅读笔记"], "先对比表三的消融结果\n\n周五复现表二，先不要更新指标。")
        result = self.chat.respond("给不存在的论文记一句：提取指标")
        self.assertIn("哪篇", result["message"])
        self.assertNotIn("提取指标", self.db.rows()[0]["fields"]["阅读笔记"])

    def test_second_paper_of_batch_and_unrelated_titles(self):
        self.chat.respond("把这两篇都收藏\n" + CVF + "\n" + OTHER)
        self.chat.respond("给第二篇记一句：下周再读")
        target = next(r for r in self.db.rows() if r["fields"]["论文链接"] == OTHER)
        self.assertEqual(target["fields"]["阅读笔记"], "下周再读")
        self.assertNotIn("阅读笔记", next(r for r in self.db.rows() if r["id"] != target["id"])["fields"])

    def test_only_title_prompts_and_no_guess_is_written(self):
        with patch("papers.search_titles", return_value=[{"title": "Deep Residual Learning", "url": DOI, "year": "2016"}]):
            reply = self.chat.respond("帮我收藏 Deep Residual Learning for Image Recognition")
        self.assertEqual(self.db.rows(), [])
        self.chat.respond(reply["choices"][0], reply["pending_id"])
        self.assertEqual(len(self.db.rows()), 1)

    def test_readable_dois_survive_one_failed_item(self):
        with patch("papers.resolve_paper", side_effect=[resolve(DOI), ValueError("无法读取原文")]):
            result = self.chat.respond("都先收藏 " + DOI + " " + OTHER)
        self.assertIn("1/2", result["message"])
        self.assertIn("无法读取", result["collection"][1]["error"])
        self.assertEqual(len(self.db.rows()), 1)

    def test_explicit_input_position_limits_the_write(self):
        self.chat.respond(f"这两篇只收藏第二篇 {CVF} {OTHER}")
        self.assertEqual([r["fields"]["论文链接"] for r in self.db.rows()], [OTHER])

    def test_filter_and_out_of_range_need_an_explicit_choice(self):
        for wording in ["这两篇里只收藏带公开全文的", "收藏第九篇", "除了第一篇都收藏"]:
            reply = self.chat.respond(f"{wording} {CVF} {OTHER}")
            self.assertEqual(self.db.rows(), [])
            self.assertIn("第2篇", reply["choices"])
            self.chat.respond("取消", reply["pending_id"])
        choice = self.chat.respond(f"只收藏其中一篇 {CVF} {OTHER}")
        self.chat.respond("第2篇", choice["pending_id"])
        self.assertEqual([r["fields"]["论文链接"] for r in self.db.rows()], [OTHER])

    def test_changed_old_row_blocks_alias_followup(self):
        first = self.chat.respond("收藏 " + CVF)["row"]
        with self.db._db:
            self.db._db.execute("UPDATE demo_papers SET cells=? WHERE id=?",
                                ('{"论文链接":"https://example.org/changed"}', first["id"]))
        with self.assertRaisesRegex(TableError, "链接发生变化"):
            self.chat.respond("收藏 " + IEEE)
        self.assertEqual(len(self.db.rows()), 1)

    def test_evidence_selection_requires_correct_live_preview(self):
        with self.assertRaisesRegex(ValueError, "预览"):
            self.chat.respond("收藏 " + OTHER, evidence_selection=[[0]])
        self.assertFalse(self.db.rows())
