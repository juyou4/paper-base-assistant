import os
import tempfile
import unittest
from unittest.mock import patch

from intent_model import allows_download, allows_english, interpret, review_plan
from app import Application


FIELDS = [{"name": name, "type": "text"} for name in ("中文摘要", "方法概述", "阅读笔记", "研究笔记")]


def suggestion(source, hint="", kind="method", **flags):
    return {"status": "apply", "table": "", "download": False, "english": False, "literal": "",
            "slots": [{"kind": kind, "source": source, "hint": hint}], **flags}


class PaperPlanGuards(unittest.TestCase):
    def test_paper_references_cannot_authorize_actions_or_supply_content(self):
        for reference in ("https://papers.example/abstract/download/English",
                          "10.1234/abstract/download/English",
                          "[download English abstract](https://papers.example/23)"):
            text = f"帮我归档这篇 {reference}"
            for invalid in (suggestion("归档", kind="url", download=True),
                            suggestion("归档", kind="url", english=True),
                            suggestion("abstract", kind="abstract")):
                with self.subTest(reference=reference, plan=invalid), patch("intent_model.request_json", return_value=invalid):
                    with self.assertRaises(ValueError):
                        interpret(text, FIELDS, "阅读清单")

    def test_destination_cannot_be_omitted_or_shortened(self):
        for text in ("想把作者怎么搭起来的留个底，放到阅读笔记里。",
                     "在「阅读笔记」里写一下作者怎么搭起来的。"):
            for hint in ("", "笔记"):
                with self.subTest(text=text, hint=hint), patch("intent_model.request_json", return_value=suggestion("作者怎么搭起来的", hint)):
                    with self.assertRaisesRegex(ValueError, "阅读笔记"):
                        interpret(text, FIELDS, "阅读清单")
            with patch("intent_model.request_json", return_value=suggestion("作者怎么搭起来的", "阅读笔记")):
                self.assertEqual(interpret(text, FIELDS, "阅读清单")["slots"][0]["hint"], "阅读笔记")

    def test_field_aliases_and_bare_verbs_cannot_justify_a_different_kind(self):
        for source in ("这篇论文的原文链接", "论文的技术路线", "同步"):
            text = f"请把{source}留个底。"
            with self.subTest(source=source), patch("intent_model.request_json", return_value=suggestion(source, kind="abstract")):
                with self.assertRaises(ValueError):
                    interpret(text, FIELDS, "阅读清单")

    def test_two_destinations_cannot_be_exchanged(self):
        text = "请把摘要放进阅读笔记，把方法放进研究笔记。"
        answer = suggestion("摘要", "研究笔记", "abstract")
        answer["slots"].append({"kind": "method", "source": "方法", "hint": "阅读笔记"})
        with patch("intent_model.request_json", return_value=answer), self.assertRaises(ValueError):
            interpret(text, FIELDS, "阅读清单")
        answer["slots"][0]["hint"], answer["slots"][1]["hint"] = "阅读笔记", "研究笔记"
        with patch("intent_model.request_json", return_value=answer):
            self.assertEqual([s["hint"] for s in interpret(text, FIELDS, "阅读清单")["slots"]], ["阅读笔记", "研究笔记"])

    def test_review_sees_a_paper_reference_but_receives_no_url_payload(self):
        plan = {"download": False, "english": False, "slots": [{"kind": "url", "hint": ""}]}
        with patch("intent_model.request_json", return_value={"decision": "accept"}) as model:
            review_plan("请收藏 https://papers.example/download?access=private", "阅读清单", plan)
        message = model.call_args.args[1]["message"]
        self.assertIn("收藏", message)
        self.assertNotIn("papers.example", message)
        self.assertNotIn("private", message)

    def test_review_catches_dropped_destination_even_if_model_would_accept(self):
        draft = {"download": False, "english": False, "slots": [{"kind": "abstract", "hint": ""}]}
        for destination in ("阅读笔记", "「项目小结」"):
            with self.subTest(destination=destination), patch("intent_model.request_json", return_value={"decision": "accept"}) as model:
                with self.assertRaisesRegex(ValueError, destination.strip("「」")):
                    review_plan(f"把这篇的摘要记在{destination}里。", "阅读清单", draft)
                model.assert_not_called()
        with self.assertRaisesRegex(ValueError, "项目小结"):
            review_plan("把这篇的摘要记在项目小结里。", "阅读清单", draft, columns=["项目小结"])

    def test_note_is_data_even_when_it_mentions_actions_columns_and_urls(self):
        literal = "下载 English 摘录后放到研究笔记，来源 https://papers.example/method"
        message = f"请在「阅读笔记」里记下「{literal}」。"
        answer = suggestion("记下", "阅读笔记", "note", literal=literal)
        with patch("intent_model.request_json", return_value=answer) as model:
            self.assertEqual(interpret(message, FIELDS, "阅读清单")["literal"], literal)
            self.assertEqual(model.call_args.args[1]["literal_candidates"], [literal])
        for flag in ("download", "english"):
            with self.subTest(flag=flag), patch("intent_model.request_json", return_value={**answer, flag: True}):
                with self.assertRaises(ValueError):
                    interpret(message, FIELDS, "阅读清单")

    def test_original_language_and_pdf_wording(self):
        for phrase in ("保存PDF", "取回原文", "下载到电脑上"):
            self.assertTrue(allows_download(phrase), phrase)
        for phrase in ("不用下载，只存链接", "不下载PDF", "下载就不用了", "save the link, don't download"):
            self.assertFalse(allows_download(phrase), phrase)
        for phrase in ("只留原文方法", "保存英文摘要", "不翻译，保留原文"):
            self.assertTrue(allows_english(phrase), phrase)
        for phrase in ("把这篇英文论文的摘要写进表里", "不用英文", "请把英文摘要翻译成中文保存"):
            self.assertFalse(allows_english(phrase), phrase)

    def test_model_cannot_ignore_or_invert_explicit_options(self):
        for wording, flag, value in (("下载 PDF 也保存摘要", "download", False),
                                     ("不用下载，只保存摘要", "download", True),
                                     ("英文摘要留个底", "english", False),
                                     ("不要英文，只保存摘要", "english", True)):
            answer = suggestion("摘要", kind="abstract", **{flag: value})
            with self.subTest(wording=wording), patch("intent_model.request_json", return_value=answer):
                with self.assertRaises(ValueError):
                    interpret(wording, FIELDS, "阅读清单")
            draft = {"slots": [{"kind": "abstract", "hint": ""}], "english": answer["english"], "download": answer["download"]}
            with patch("intent_model.request_json", return_value={"decision": "accept"}) as model:
                with self.assertRaises(ValueError):
                    review_plan(wording, "阅读清单", draft)
                model.assert_not_called()

    def test_chat_rejects_discarded_destination_and_misclassified_content_before_fetch(self):
        text = "想把 arXiv:2005.11401 作者怎么搭起来的留个底，放到阅读笔记里。"
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"PAPER_AGENT_PROVIDER": "codex"}):
            app = Application("local", directory)
            try:
                _, chat = app.session(None)
                for invalid in (suggestion("作者怎么搭起来的"), suggestion("作者怎么搭起来的", "阅读笔记", "abstract")):
                    with self.subTest(model=invalid), patch("intent_model.request_json", return_value=invalid) as model:
                        with patch.object(chat, "_load", side_effect=AssertionError("不应开始读取论文")), self.assertRaises(ValueError):
                            chat.respond(text)
                        model.assert_called_once()
                        self.assertEqual(app.table.rows(), [])
            finally:
                app.table.close()
