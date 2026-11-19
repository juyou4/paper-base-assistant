import unittest
from unittest.mock import patch

from base_table_dialog import parse


class BaseTableDialogTests(unittest.TestCase):
    def test_create_base_and_its_first_table(self):
        result = parse("请帮我新建一个独立的飞书多维表格「2026 年论文」，首张表叫「实验结果」，"
                       "字段：论文标题（文本）、论文链接（超链接）、准确率（文本）")
        self.assertEqual(result, {
            "op": "create_base", "name": "2026 年论文", "table_name": "实验结果",
            "fields": [{"name": "论文标题", "type": "text"}, {"name": "论文链接", "type": "url"},
                       {"name": "准确率", "type": "text"}],
        })
        self.assertEqual(parse("建一个 Base 叫成果汇总")["table_name"], "论文清单")
        self.assertEqual(parse("新建多维表格「本周」，第一张数据表「方法，证据」")["table_name"], "方法，证据")

    def test_create_table_and_default_schema(self):
        for message in ("在当前多维表格里新建数据表「论文精读」", "新建一张叫论文精读的表",
                        "建个叫「论文精读」的子表", "新建数据表，名字叫论文精读，字段你来设计"):
            with self.subTest(message=message):
                result = parse(message)
                self.assertEqual((result["op"], result["name"]), ("create_table", "论文精读"))
                self.assertEqual([field["name"] for field in result["fields"]],
                                 ["论文标题", "论文链接", "中文摘要", "方法概述", "阅读笔记", "研究笔记"])
        self.assertEqual(parse("帮我建一张论文复现表")["name"], "论文复现表")

    def test_custom_fields_preserve_names_order_and_types(self):
        result = parse("新建数据表「图表」，字段：标题和链接、"
                       "「模型，版本」：文本、原文 URL、PDF标记（文本）")
        self.assertEqual(result["fields"], [
            {"name": "标题", "type": "text"}, {"name": "链接", "type": "url"},
            {"name": "模型，版本", "type": "text"}, {"name": "原文", "type": "url"},
            {"name": "PDF标记", "type": "text"},
        ])

    def test_table_lookup_switch_and_name_questions(self):
        for command in ("列出所有数据表", "当前多维表格里有哪些表？", "现有的多维表格有哪些数据表", "查看数据表列表"):
            self.assertEqual(parse(command), {"op": "list_tables"}, command)
        for command in ("切到数据表「论文精读」", "切换到「论文精读」", "换到子表论文精读", "切换到论文精读"):
            self.assertEqual(parse(command), {"op": "switch_table", "name": "论文精读"}, command)
        for command, missing in [("新建多维表格", "create_base"), ("建一张子表", "create_table"), ("新建表和字段", "create_table"),
                                 ("切换数据表", "switch_table")]:
            self.assertEqual(parse(command)["needs_name"], missing)

    def test_unsafe_or_incomplete_proposals_require_clarification(self):
        for command in ("新建数据表 X，字段：评分（数字）", "新建数据表 X，字段：论文链接、标题",
                        "新建数据表 X，字段：标题、标题", "新建数据表 X，字段：标题、",
                        "新建数据表 X，字段：标题，评分 数字", "新建数据表 X，然后把刚才的论文写进去",
                        "不要新建数据表 X", "新建数据表 X 吗？", "切换到数据表 X 吗？",
                        "新建数据表 X，删除旧表", "切换到多维表格「其他文件」",
                        "新建两张数据表", "建个用来存放论文的表", "新建一个飞书表格", "新建数据表「周报/指标」"):
            with self.subTest(command=command):
                self.assertEqual(parse(command)["op"], "clarify")

    def test_other_intents_stay_in_their_existing_routes(self):
        for command in ("查一下表", "查看字段", "新建文本列 阅读笔记", "新增表格字段",
                        "把「新建数据表」写进阅读笔记", "提取该论文中的表", "切换模型",
                        "收藏 https://arxiv.org/abs/2005.11401v4", "查看角色「审阅人」的权限",
                        "添加论文链接到当前表", "新建文本列 实验结果表", "新增数据表记录", "切换到本地模式"):
            self.assertIsNone(parse(command), command)

    def test_explicit_dimensions_are_planned_without_model(self):
        with patch("model_support.request_json", side_effect=AssertionError("明确列名和维度可直接使用")):
            for tail in ("字段你来设计，用于记录模型、数据集和评价指标",
                         "用于记录模型、数据集和评价指标，字段由你来设计", "用来记录模型、数据集和评价指标"):
                result = parse("新建数据表「模型对比」，" + tail)
                self.assertEqual([f["name"] for f in result["fields"]],
                                 ["论文标题", "论文链接", "模型", "数据集", "评价指标"])
                self.assertEqual(result["field_source"], "按用途中的明确维度规划")
            explicit = parse("新建数据表「模型对比」，字段：模型、数据集、指标")
            self.assertEqual([f["name"] for f in explicit["fields"]], ["模型", "数据集", "指标"])
            aliases = parse("新建数据表「论文」，字段你来设计，用于记录标题、链接和年份")
            self.assertEqual([f["name"] for f in aliases["fields"]], ["论文标题", "论文链接", "年份"])

    def test_model_planning_proposes_only_valid_schema(self):
        question = "新建多维表格「复现评审」，字段你来设计，用于判断下周应该优先复现哪篇论文"
        with patch("model_support.enabled", return_value=False):
            self.assertEqual(parse(question)["op"], "clarify")
        fields = [{"name": "论文标题", "type": "text"}, {"name": "论文链接", "type": "url"},
                  {"name": "复现成本", "type": "text"}]
        with patch("model_support.enabled", return_value=True), patch("model_support.request_json") as request:
            request.return_value = {"fields": fields}
            proposal = parse(question)
            self.assertEqual(proposal["fields"], fields)
            self.assertEqual(proposal["op"], "create_base")
            self.assertEqual(proposal["field_source"], "模型按用途提议")
            self.assertEqual(set(request.call_args.args[1]), {"table_name", "purpose"})
            for invalid in ({"fields": fields, "op": "create"}, {"fields": fields[::-1][1:]},
                            {"fields": fields + [fields[0]]}, {"fields": fields + [{"name": "评分", "type": "number"}]},
                            {"fields": []}, {"fields": [{"name": "仅姓名", "type": "text"}]}, None):
                request.return_value = invalid
                self.assertEqual(parse(question)["op"], "clarify")
            request.side_effect = ValueError("暂时不可用")
            self.assertEqual(parse(question)["op"], "clarify")

