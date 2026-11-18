"""模型只提供已授权动作的语义解析，不接触表或确认状态。"""

import unittest
from tempfile import TemporaryDirectory
from unittest.mock import patch

from agent_router import is_candidate, route


SCHEMA = [{"id": "f1", "name": "研究笔记", "type": "text"},
          {"id": "f2", "name": "阅读笔记", "type": "text"},
          {"id": "f3", "name": "论文链接", "type": "url"}]


class AgentRouterTests(unittest.TestCase):
    def answer(self, message, command):
        with patch("agent_router.request_json", return_value=command) as model:
            result = route(message, SCHEMA, "阅读清单")
        return result, model

    def test_model_first_routes_management_speech(self):
        cases = [
            ("看下这张表目前都有哪些列", "read", {"op": "columns"}),
            ("列一下这张表的所有字段", "read", {"op": "columns"}),
            ("看看「论文链接」这栏的类型", "read", {"op": "column", "target": "论文链接"}),
            ("帮我查一下，「论文链接」这一栏具体是什么类型？", "read", {"op": "column", "target": "论文链接"}),
            ("这张表目前都有哪些列?", "read", {"op": "columns"}),
            ("请给我加一栏，名字叫「试录备注」，类型选文本", "command",
             {"op": "create", "name": "试录备注", "type": "text"}),
            ("麻烦给这张表加一栏，叫「复现难度」，用文本", "command",
             {"op": "create", "name": "复现难度", "type": "text"}),
            ("新增链接字段 原文地址", "command", {"op": "create", "name": "原文地址", "type": "url"}),
            ("给研究笔记这一栏换叫复核备注", "command",
             {"op": "rename", "target": "研究笔记", "new_name": "复核备注"}),
            ("把「阅读笔记」那一栏的名字换成「选读记录」", "command",
             {"op": "rename", "target": "阅读笔记", "new_name": "选读记录"}),
            ("麻烦把阅读笔记那一栏给我去掉", "command", {"op": "delete", "target": "阅读笔记"}),
        ]
        for sentence, kind, command in cases:
            with self.subTest(sentence=sentence):
                result, model = self.answer(sentence, command)
                self.assertEqual(result, {"kind": kind, "command": command})
                model.assert_called_once()
                payload = model.call_args.args[1]
                self.assertEqual(set(payload), {"message", "table_name", "columns"})
                self.assertEqual(set(payload["columns"][0]), {"name", "type"})

    def test_read_and_prohibition_cannot_authorize_a_mutation(self):
        for sentence in ("如何删除研究笔记这一列？", "先别移除「阅读笔记」这一栏",
                         "只想知道删除阅读笔记列会怎样", "如果删除「阅读笔记」这一列",
                         "把研究笔记列删掉，然后再增设文本列备份",
                         "把其他表的「阅读笔记」字段删掉", "先看看「阅读笔记」这栏再删掉"):
            with self.subTest(sentence=sentence):
                result, model = self.answer(sentence, {"op": "delete", "target": "阅读笔记"})
                self.assertEqual(result["kind"], "clarify")
                model.assert_not_called()
        result, _ = self.answer("查询「阅读笔记」列", {"op": "delete", "target": "阅读笔记"})
        self.assertEqual(result["kind"], "clarify")

    def test_ambiguous_fragment_and_cell_vs_column(self):
        result, _ = self.answer("把笔记这一列删掉", {"op": "delete", "target": "笔记"})
        self.assertEqual(result["command"]["target"], "笔记")
        guessed, _ = self.answer("把笔记这一列删掉", {"op": "delete", "target": "研究笔记"})
        self.assertEqual(guessed["kind"], "clarify")
        ambiguous, model = self.answer("把「阅读笔记」那一列改成「已读」",
                                       {"op": "rename", "target": "阅读笔记", "new_name": "已读"})
        self.assertEqual(ambiguous["kind"], "clarify")
        model.assert_not_called()

    def test_names_types_and_action_match_the_original(self):
        for message, proposed in [
            ("删除「阅读笔记」这一列", {"op": "delete", "target": "笔记"}),
            ("把阅读笔记这一列改叫研究笔记", {"op": "rename", "target": "研究笔记", "new_name": "阅读笔记"}),
            ("把阅读笔记这一列改叫复核时间", {"op": "rename", "target": "笔记", "new_name": "时间"}),
            ("加一列叫「来源链接」", {"op": "create", "name": "来源链接", "type": "url"}),
            ("添加文本字段 采购日期", {"op": "create", "name": "采购日期", "type": "url"}),
            ("请给我加一栏，名字叫「状态」，类型选文本？", {"op": "create", "name": "状态", "type": "text"}),
            ("请给我加一栏，名字叫「状态」，类型选文本或链接", {"op": "create", "name": "状态", "type": "text"}),
            ("请给我加一栏，名字叫「状态」，类型选文本", {"op": "create", "name": "状态", "type": "url"}),
            ("添加文本字段", {"op": "create", "name": "文本", "type": "text"}),
            ("查看采购清单有哪些字段", {"op": "columns"}),
            ("删掉「研究笔记」这一列", {"op": "delete", "target": "研究笔记", "confirm": True}),
            ("看看这张表的列名", {"op": "shell", "command": "lark-cli ..."}),
            ("看看这张表的列名", {"op": ["columns"]}),
        ]:
            with self.subTest(message=message, proposed=proposed):
                self.assertEqual(self.answer(message, proposed)[0]["kind"], "clarify")

    def test_other_workflows_stay_in_their_existing_route(self):
        for message in ("把刚才那篇的方法写进笔记列", "查看角色「论文审阅人」对字段的权限", "把字段「审阅意见」的类型改为超链接",
                        "把 reader@example.test 的协作者权限改为可编辑", "确认建列", "取消"):
            with self.subTest(message=message):
                self.assertFalse(is_candidate(message))
                result, model = self.answer(message, {"op": "delete", "target": "阅读笔记"})
                self.assertIsNone(result)
                model.assert_not_called()

    def test_provider_error_cannot_fall_through_to_a_mutation(self):
        with patch("agent_router.request_json", side_effect=ValueError("模型连接失败")):
            with self.assertRaisesRegex(ValueError, "模型连接失败"):
                route("把阅读笔记这一列删掉", SCHEMA, "阅读清单")

    def test_conversation_keeps_confirmation_and_ambiguous_selection(self):
        from table_store import LocalTable
        from workflow import Conversation

        table = LocalTable(":memory:")
        self.addCleanup(table.close)
        with TemporaryDirectory() as directory, patch("model_support.agent_info", return_value={"enabled": True}):
            talk = Conversation(table, directory, "阅读清单")
            with patch("agent_router.request_json", side_effect=[
                {"op": "columns"}, {"op": "create", "name": "复现难度", "type": "text"},
                {"op": "delete", "target": "笔记"},
            ]) as model:
                self.assertIn("研究笔记", talk.respond("看下这张表目前都有哪些列")["message"])
                plan = talk.respond("麻烦给这张表加一栏，叫「复现难度」，用文本")
                self.assertIn("确认建列", plan["choices"])
                self.assertNotIn("复现难度", {f["name"] for f in table.schema()})
                talk.respond("确认建列", plan["pending_id"])
                self.assertIn("复现难度", {f["name"] for f in table.schema()})
                ambiguous = talk.respond("把笔记这一列删掉")
                self.assertCountEqual(ambiguous["choices"], ["阅读笔记", "研究笔记"])
                preview = talk.respond("研究笔记", ambiguous["pending_id"])
                self.assertIn("确认变更", preview["choices"])
                talk.respond("取消", preview["pending_id"])
                self.assertIn("研究笔记", {f["name"] for f in table.schema()})
                self.assertEqual(model.call_count, 3)


if __name__ == "__main__":
    unittest.main()
