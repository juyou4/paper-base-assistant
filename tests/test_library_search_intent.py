import unittest
from unittest.mock import patch

from library_search_intent import classify


class ColloquialSearchTests(unittest.TestCase):
    def test_grounded_read_requests_work_offline(self):
        cases = {
            "这张表中有没有哪篇论文讲模型压缩": ("local", "模型压缩"),
            "帮我看看阅读清单里是否有论文提到视觉跟踪": ("local", "视觉跟踪"),
            "我收藏的论文里有没有关于检索增强的": ("local", "检索增强"),
            "在这张表里翻一翻 RAG 相关的论文": ("local", "RAG"),
            "我想找上次收藏的那篇关于长上下文的论文": ("local", "长上下文"),
            "从我收过的论文里挑几篇机器人方向的": ("local", "机器人"),
            "翻翻我存的文章，哪些提到了量化？": ("local", "量化"),
            "神经渲染清单中有没有提到高斯泼溅的论文？": ("local", "高斯泼溅"),
            "网上有没有讲 RAG 的论文？": ("public", "RAG"),
            "去学术网站看看有没有长文本检索的论文": ("public", "长文本检索"),
            "有没有提到知识蒸馏的论文？": ("ask", "知识蒸馏"),
            "这张表能不能帮我推荐点论文": ("incomplete", ""),
        }
        with patch("model_support.enabled", return_value=False), patch("model_support.request_json") as model:
            for message, expected in cases.items():
                with self.subTest(message=message):
                    self.assertEqual(classify(message, "神经渲染清单"), expected)
            model.assert_not_called()

    def test_only_an_unresolved_read_request_uses_one_model_call(self):
        message = "在我收藏的文献里推荐点偏 3DGS 的工作"
        answer = {"scope": "local", "term": "3DGS", "scope_phrase": "我收藏的文献里"}
        with patch("model_support.enabled", return_value=True), patch("model_support.request_json", return_value=answer) as model:
            self.assertEqual(classify(message, "阅读清单"), ("local", "3DGS"))
            model.assert_called_once()
            self.assertEqual(model.call_args.args[1], {"message": message, "table_name": "阅读清单"})
            model.reset_mock()
            self.assertEqual(classify("翻翻我存的文章，哪些提到了量化？", "阅读清单"), ("local", "量化"))
            model.assert_not_called()

    def test_model_cannot_invent_a_source_or_export_a_local_term(self):
        message = "在我收藏的文献里推荐点偏 3DGS 的工作"
        suggestion = {"scope": "local", "term": "3DGS", "scope_phrase": "我收藏的"}
        invalid = [
            {**suggestion, "scope": "public"}, {**suggestion, "scope": "write"},
            {**suggestion, "scope_phrase": "阅读清单"}, {**suggestion, "scope_phrase": []},
            {**suggestion, "term": "NeRF"}, {**suggestion, "term": "3D"}, {**suggestion, "term": "我收藏的"},
            {**suggestion, "term": []}, {**suggestion, "tool": "Crossref"}, None,
        ]
        with patch("model_support.enabled", return_value=True):
            for answer in invalid:
                with self.subTest(answer=answer), patch("model_support.request_json", return_value=answer) as model:
                    self.assertEqual(classify(message, "阅读清单"), ("incomplete", ""))
                    model.assert_called_once()
            with patch("model_support.request_json", side_effect=TimeoutError):
                self.assertEqual(classify(message, "阅读清单"), ("incomplete", ""))
            with patch("model_support.request_json", return_value={"scope": "ask", "term": "RAG", "scope_phrase": ""}):
                self.assertEqual(classify("能推荐点偏 RAG 的论文嘛", "阅读清单"), ("ask", "RAG"))

    def test_exclusions_constraints_and_compound_actions_never_call_model(self):
        with patch("model_support.request_json") as model:
            for message in (
                "别在网上搜索 RAG 的论文", "不用在学术网站找关于量化的论文",
                "我没让你到网上找关于量化的论文", "在网上找我们组会上提到的论文",
                "在当前表里找关于量化的论文，然后收藏起来", "上网搜我们内部项目代号相关的论文",
                "网上有没有关于我的客户的论文", "这张表有没有 2024 年以后讨论量化的论文",
                "麻烦盘一盘这张表一共有几篇关于论文检索的论文",
            ):
                with self.subTest(message=message):
                    self.assertEqual(classify(message, "阅读清单"), ("unsupported", ""))
            model.assert_not_called()

    def test_leaves_document_workspace_and_schema_requests_to_their_tools(self):
        with patch("model_support.request_json") as model:
            for message in ("打开这篇论文找找它的架构图", "在我收藏的那篇论文里找第一张消融图",
                            "查一下表", "查看字段", "这张表里有几篇论文",
                            "麻烦盘一盘这张表一共几篇论文，只报个数就行", "麻烦盘一盘这张表一共几篇论文",
                            "帮我找 https://arxiv.org/abs/1706.03762 的论文", "新建文本列 论文检索"):
                with self.subTest(message=message):
                    self.assertIsNone(classify(message, "阅读清单"))
            model.assert_not_called()
