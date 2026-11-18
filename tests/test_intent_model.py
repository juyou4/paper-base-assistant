import unittest
from unittest.mock import patch

from intent_model import interpret


COLUMNS = [
    {"id": "u", "name": "论文链接", "type": "url"},
    {"id": "n", "name": "阅读笔记", "type": "text"},
    {"id": "n2", "name": "研究笔记", "type": "text"},
    {"id": "f", "name": "统计公式", "type": "readonly"},
]


def plan(kind="method", source="作者怎么搭起来的", hint="笔记", **kwargs):
    result = {"status": "apply", "table": "", "english": False, "download": False,
              "literal": "", "slots": [{"kind": kind, "source": source, "hint": hint}]}
    result.update(kwargs)
    return result


class IntentModelTests(unittest.TestCase):
    def test_colloquial_method_keeps_ambiguous_note_destination(self):
        text = "想把作者怎么搭起来的留个底，放到笔记里。"
        with patch("intent_model.request_json", return_value=plan()) as request:
            result = interpret(text, COLUMNS, "阅读清单")
        self.assertEqual(result["slots"], [{"kind": "method", "hint": "笔记", "optional": False}])
        self.assertFalse(result["english"])
        self.assertEqual(request.call_args.args[1]["columns"]["阅读笔记"], "text")
        self.assertNotIn("rows", request.call_args.args[1])

    def test_literal_must_be_verbatim_and_grounded(self):
        text = "上次那篇，请在阅读笔记里记下「下周复现」。"
        suggestion = plan("note", "记下", "阅读笔记", literal="下周复现")
        with patch("intent_model.request_json", return_value=suggestion):
            self.assertEqual(interpret(text, COLUMNS, "阅读清单")["literal"], "下周复现")
        for fragment in ("复现", "这个方法很好"):
            with patch("intent_model.request_json", return_value={**suggestion, "literal": fragment}):
                with self.assertRaises(ValueError):
                    interpret(text, COLUMNS, "阅读清单")
        quoted_column = "在「阅读笔记」记下「下周复现」。"
        with patch("intent_model.request_json", return_value={**suggestion, "literal": "阅读笔记"}):
            with self.assertRaises(ValueError):
                interpret(quoted_column, COLUMNS, "阅读清单")

    def test_read_delete_and_other_table_block_even_if_model_would_apply(self):
        for text in ("只看摘要，别写入", "删掉阅读笔记，顺便存这篇论文", "请把刚才那篇归入财务清单里。", "怎样保存论文"):
            with self.subTest(text=text), patch("intent_model.request_json", side_effect=AssertionError):
                with self.assertRaises(ValueError):
                    interpret(text, COLUMNS, "阅读清单")

    def test_model_cannot_supply_commands_values_or_extra_privileges(self):
        text = "想把作者怎么搭起来的留个底，放到笔记里。"
        for invalid in (plan(sql="delete"), plan(download=True), plan(english=True), plan(source="留个底"),
                        plan(kind="command"), plan(source="另一项操作"), plan(hint="秘密列"),
                        plan(literal="额外值"), plan(table="我的私有表"),
                        plan(slots=[{"kind": "method", "source": "留个底", "hint": "", "value": "猜测"}])):
            with self.subTest(model=invalid), patch("intent_model.request_json", return_value=invalid):
                with self.assertRaises(ValueError):
                    interpret(text, COLUMNS, "阅读清单")

    def test_download_alone_and_explicit_readonly_column(self):
        with patch("intent_model.request_json", return_value=plan(download=True, slots=[])):
            self.assertEqual(interpret("想要下载上次那篇的 PDF", COLUMNS, "阅读清单")["slots"], [])
        with patch("intent_model.request_json", return_value=plan(hint="统计公式")), self.assertRaises(ValueError):
            interpret("想把作者怎么搭起来的留个底，放到统计公式里。", COLUMNS, "阅读清单")

    def test_model_flags_uncertainty_or_read_intent(self):
        for status in ("read", "clarify", "foreign_table", "unsupported"):
            with self.subTest(status=status), patch("intent_model.request_json", return_value={"status": status}):
                with self.assertRaises(ValueError):
                    interpret("收下前能先判断一下吗？", COLUMNS, "阅读清单")


if __name__ == "__main__":
    unittest.main()
