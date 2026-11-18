import unittest

from dialog_choices import resolve_choice


class DialogChoicesTests(unittest.TestCase):
    def test_spoken_field_selection(self):
        choices = ["阅读笔记", "研究笔记"]
        for phrase, expected in (("就放阅读笔记吧", "阅读笔记"), ("请帮我填到「研究笔记」这一栏", "研究笔记"),
                                 ("我选第二个", "研究笔记"), ("直接用第1列就行", "阅读笔记")):
            with self.subTest(phrase=phrase):
                self.assertEqual(resolve_choice(phrase, "field", choices), expected)

    def test_conversation_options(self):
        options = ["保存论文链接", "填写摘要", "填写方法", "换一篇论文", "取消"]
        self.assertEqual(resolve_choice("只要摘要", "ambiguous_intent", options), "填写摘要")
        self.assertEqual(resolve_choice("换一篇吧", "ambiguous_intent", options), "换一篇论文")
        self.assertEqual(resolve_choice("留英文就行", "language", ["使用英文摘录", "取消"]), "使用英文摘录")

    def test_mixed_uncertain_and_unoffered_answers_never_select(self):
        options = ["阅读笔记", "研究笔记", "取消"]
        for phrase in ("阅读笔记还是研究笔记", "不要阅读笔记", "放研究笔记？", "选第四项", "选第三项", "没问题"):
            with self.subTest(phrase=phrase):
                self.assertNotIn(resolve_choice(phrase, "field", options), options)

    def test_sensitive_confirmations_keep_their_own_contract(self):
        opts = ["确认变更", "取消"]
        self.assertEqual(resolve_choice("确认变更。", "admin_commit", opts), "确认变更")
        for phrase in ("确认变更？", "那就确认变更吧", "第一个", "是的"):
            self.assertNotIn(resolve_choice(phrase, "admin_commit", opts), opts)
        self.assertEqual(resolve_choice("那就取消这次吧", "admin_commit", opts), "取消")
        self.assertEqual(resolve_choice("不用了，谢谢", "evidence_confirm", opts), "取消")
