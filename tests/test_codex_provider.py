import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import codex_provider as provider


def events(*items):
    return "\n".join(json.dumps(event) for event in items)


def result(value):
    text = json.dumps({"result_json": json.dumps(value)})
    return {"type": "item.completed", "item": {"type": "agent_message", "text": text}}


class CodexProviderTests(unittest.TestCase):
    def test_catalog_projects_visible_slugs_in_priority_order_and_caches(self):
        directory = {
            "models": [
                {"slug": "second", "visibility": "list", "priority": 5, "base_instructions": "private"},
                {"slug": "internal", "visibility": "hide", "priority": 0},
                {"slug": "first", "visibility": "list", "priority": 1},
                {"slug": "second", "visibility": "list", "priority": 3},
                {"slug": "--unsafe", "visibility": "list"},
                {"slug": "old"},
                {"slug": "unranked", "visibility": "list", "priority": "unexpected"},
                None,
            ]
        }
        self.addCleanup(provider._catalog.cache_clear)
        provider._catalog.cache_clear()
        def fake_list(args, **options):
            self.assertEqual(args[:3], ["official-codex", "debug", "models"])
            self.assertIn('model_provider="openai"', args)
            self.assertIn("mcp_servers={}", args)
            self.assertIn("shell_tool", args)
            self.assertNotIn("FEISHU_APP_SECRET", options["env"])
            self.assertFalse(options.get("shell"))
            self.assertEqual(options["creationflags"], provider._NO_WINDOW)
            self.assertEqual(list(Path(options["cwd"]).iterdir()), [])
            return subprocess.CompletedProcess(args, 0, json.dumps(directory).encode(), b"")
        env = {"FEISHU_APP_SECRET": "private", "PAPER_MODEL_NAME": "unlisted", "CODEX_HOME": "account1"}
        with patch.dict(os.environ, env), patch.object(provider, "find_cli", return_value="official-codex"), \
                patch.object(provider, "_features", return_value={"shell_tool"}), \
                patch.object(provider.time, "monotonic", return_value=10), \
                patch.object(subprocess, "run", side_effect=fake_list) as run:
            expected = ["first", "second", "unranked"]
            self.assertEqual(provider.available_models(), expected)
            self.assertEqual(provider.available_models(), expected)
            self.assertEqual(run.call_count, 1)
            with patch.dict(os.environ, {"CODEX_HOME": "account2"}):
                self.assertEqual(provider.available_models(), expected)
            self.assertEqual(run.call_count, 2)

    def test_unsupported_or_invalid_catalog_falls_back_only_to_explicit_setting(self):
        failures = (subprocess.CompletedProcess([], 1, b"unsupported", b"redacted"),
                    subprocess.CompletedProcess([], 0, b"broken JSON", b""),
                    subprocess.CompletedProcess([], 0, b'{"models": "broken"}', b""),
                    subprocess.CompletedProcess([], 0, b'{"models":[{"slug":"internal","visibility":"hide"}]}', b""),
                    subprocess.TimeoutExpired("codex", 8))
        self.addCleanup(provider._catalog.cache_clear)
        with patch.object(provider, "find_cli", return_value="official"), \
                patch.object(provider, "_features", return_value=set()):
            for failure in failures:
                provider._catalog.cache_clear()
                with self.subTest(failure=type(failure).__name__), \
                        patch.object(subprocess, "run", side_effect=[failure]), \
                        patch.dict(os.environ, {"PAPER_MODEL_NAME": "my-model"}):
                    self.assertEqual(provider.available_models(), ["my-model"])
        with patch.object(provider, "find_cli", side_effect=ValueError("missing")), \
                patch.dict(os.environ, {"PAPER_MODEL_NAME": "secret text"}):
            self.assertEqual(provider.available_models(), [])

    def test_isolated_invocation_and_structured_response(self):
        captured = {}
        def fake_run(args, **kwargs):
            directory = Path(kwargs["cwd"])
            captured["directory"] = directory
            self.assertEqual(list(directory.iterdir()), [])
            schema = Path(args[args.index("--output-schema") + 1])
            self.assertEqual(json.loads(schema.read_bytes()), provider._SCHEMA)
            self.assertEqual(args[:2], ["official-codex", "exec"])
            self.assertEqual(args[-1], "-")
            self.assertIn("--ignore-user-config", args)
            self.assertIn("--ephemeral", args)
            self.assertIn("read-only", args)
            self.assertIn('web_search="disabled"', args)
            self.assertIn("mcp_servers={}", args)
            self.assertIn("skills.include_instructions=false", args)
            self.assertEqual({args[i+1] for i, flag in enumerate(args) if flag=="--disable"}, set(provider._OFF))
            self.assertEqual(args[args.index("-m")+1], "gpt-6.2-sol")
            self.assertFalse(kwargs.get("shell"))
            self.assertNotIn("FEISHU_APP_SECRET", kwargs["env"])
            self.assertNotIn("OPENAI_API_KEY", kwargs["env"])
            body = json.loads(kwargs["input"])
            self.assertEqual(body, {"task":"返回长度","data":{"source":"原文"}})
            self.assertNotIn("原文", " ".join(args))
            data = events({"type":"thread.started"},
                          {"type":"item.completed","item":{"type":"error","message":"Code Mode is unavailable because code-mode host is disabled."}},
                          {"type":"turn.started"}, result({"length":2}), {"type":"turn.completed"})
            return subprocess.CompletedProcess(args, 0, data.encode(), b"")
        with patch.dict(os.environ, {"FEISHU_APP_SECRET":"not-to-inherit","OPENAI_API_KEY":"not-to-inherit"}), \
                patch.object(provider,"find_cli",return_value="official-codex"), \
                patch.object(provider,"_features",return_value=set(provider._OFF)|{"skip_host_skill_discovery"}), \
                patch.object(subprocess,"run",side_effect=fake_run):
            self.assertEqual(provider.request("返回长度", {"source":"原文"}, "gpt-6.2-sol"), {"length":2})
        self.assertFalse(captured["directory"].exists())

    def test_rejects_tool_execution_and_turn_errors_even_if_final_json_is_valid(self):
        for kind in ("command_execution","mcp_tool_call","web_search","error","unexpected"):
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                provider._answer(events({"type":"turn.started"},
                    {"type":"item.started","item":{"type":kind,"message":"Code Mode is unavailable because code-mode host is disabled."}},
                    result({"accepted":True}),{"type":"turn.completed"}))
        with self.assertRaises(ValueError):
            provider._answer(events({"type":"turn.started"}, result({"ok":True}), {"type":"turn.failed"}))

    def test_rejects_unfinished_and_invalid_structure(self):
        for transcript in (events({"type":"turn.started"},result({"a":1})),
                           events({"type":"turn.started"},result([1]),{"type":"turn.completed"}),
                           events({"type":"turn.started"},{"type":"item.completed","item":{"type":"agent_message","text":"[]"}},
                                  {"type":"turn.completed"})):
            with self.subTest(transcript=transcript), self.assertRaises(ValueError):
                provider._answer(transcript)

    def test_cli_priority_and_incompatible_explicit_binary(self):
        with tempfile.TemporaryDirectory() as root:
            explicit, runtime = (Path(root)/name for name in ("selected.exe","runtime.exe"))
            explicit.touch()
            runtime.touch()
            with patch.dict(os.environ, {"PAPER_CODEX_CLI":str(explicit),"CODEX_APP_RUNTIME_CLI_PATH":str(runtime)}):
                with patch.object(provider,"_features",return_value={"shell_tool"}):
                    self.assertEqual(Path(provider.find_cli()), explicit.resolve())
                with patch.object(provider,"_features",return_value=set()), self.assertRaisesRegex(ValueError,"新版"):
                    provider.find_cli()

    def test_invalid_model_and_binary_errors_do_not_reveal_provider_details(self):
        with self.assertRaisesRegex(ValueError,"模型名称"):
            provider.request("a", {}, "--inject arg")
        with patch.object(provider,"find_cli",return_value="official"), \
                patch.object(provider,"_features",return_value=set()), \
                patch.object(subprocess,"run",return_value=subprocess.CompletedProcess([],1,b"private",b"secret")):
            with self.assertRaises(ValueError) as ctx:
                provider.request("a", {}, "model")
            self.assertNotIn("secret",str(ctx.exception))
            self.assertNotIn("private",str(ctx.exception))
