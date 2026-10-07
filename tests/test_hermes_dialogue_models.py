from __future__ import annotations

import ast
import os
import threading
import tempfile
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, Optional
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor

import hermes_dialogue_models as models
import hermes_durable_accounting as durable
from hermes_behavior_policy import managed_response_settings

ENABLED = {"HERMES_DIALOGUE_MODEL_CHOICE_ENABLED": "true", "HERMES_RUNTIME_TIER": "economy", "HERMES_FIXED_MODEL_PROVIDER": "openrouter"}


class DialogueModelsTests(unittest.TestCase):
    def test_absent_choice_keeps_existing_behavior(self):
        self.assertIsNone(models.validate_dialogue_model({"model": "arbitrary-cosmetic-model"}, None, {}))
        kwargs = {"credential_pool": threading.Lock()}
        result = models.dialogue_agent_settings(None, kwargs, "existing/model", {}, "fallback")
        self.assertIs(result[0], kwargs)
        self.assertEqual(result[1:], ("existing/model", {}, "fallback"))

    def test_validate_choice_before_dispatch(self):
        for code, profile in models.PROFILES.items():
            body = {"model": profile["model"], "tp_dialogue_model": code}
            self.assertEqual(models.validate_dialogue_model(body, "stable-request", ENABLED), code)
            for variant, env in [(body, {}), ({**body, "stream": True}, ENABLED), ({**body, "model": "other/model"}, ENABLED)]:
                with self.assertRaises(models.DialogueModelError):
                    models.validate_dialogue_model(variant, "stable-request", env)
            with self.assertRaises(models.DialogueModelError):
                models.validate_dialogue_model(body, None, ENABLED)
        with self.assertRaises(models.DialogueModelError):
            models.validate_dialogue_model({"tp_dialogue_model": "arbitrary"}, "stable-request", ENABLED)

    def test_profiles_are_per_request_and_do_not_clone_credentials_or_allow_fallbacks(self):
        credentials = threading.Lock()
        kwargs = {"provider": "openrouter", "base_url": "https://openrouter.ai/api/v1", "credential_pool": credentials,
                  "request_overrides": {"extra_body": {"models": ["other/model"], "provider": {"only": ["old-provider"]}}}}
        with patch.dict(os.environ, ENABLED):
            one = models.dialogue_agent_settings("gpt_luna", kwargs, "old/model", {}, "fallback")
            two = models.dialogue_agent_settings("deepseek_flash", kwargs, "old/model", {}, "fallback")
        self.assertIs(one[0]["credential_pool"], credentials)
        self.assertEqual(kwargs["request_overrides"]["extra_body"]["provider"]["only"], ["old-provider"])
        self.assertEqual(one[0]["request_overrides"]["extra_body"]["provider"], {"only": ["openai"], "allow_fallbacks": False})
        self.assertEqual(two[0]["request_overrides"]["extra_body"]["provider"]["only"], ["novita/fp8"])
        self.assertNotIn("models", one[0]["request_overrides"]["extra_body"])
        self.assertEqual(one[1:], ("openai/gpt-6-luna", {"effort": "high"}, None))
        self.assertEqual(one[0]["max_tokens"], two[0]["max_tokens"])

    def test_other_runtime_and_provider_cannot_use_choice(self):
        for change in [{"HERMES_RUNTIME_TIER": "strong"}, {"HERMES_FIXED_MODEL_PROVIDER": "other"}]:
            with self.assertRaises(models.DialogueModelError):
                models.validate_dialogue_model({"tp_dialogue_model": "gpt_luna", "model": "openai/gpt-6-luna"}, "request", {**ENABLED, **change})
        with patch.dict(os.environ, ENABLED), self.assertRaises(models.DialogueModelError):
            models.dialogue_agent_settings("gpt_luna", {"provider": "other", "base_url": "https://other.invalid"}, "model", {}, None)

    def test_public_capability_contains_only_codes_after_activation(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(models.public_dialogue_models())
        with patch.dict(os.environ, ENABLED):
            self.assertEqual(models.public_dialogue_models(), {"version": 1, "codes": ["deepseek_flash", "gpt_luna"]})

    def test_journal_replays_one_result_and_rejects_model_change_under_same_key(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {
            **ENABLED, "HERMES_RUNTIME_ID": "test-economy-choice", "HERMES_ACCOUNTING_JOURNAL_PATH": str(Path(folder) / "journal.sqlite3"),
        }):
            body = {"model": "openai/gpt-6-luna", "tp_dialogue_model": "gpt_luna", "messages": [{"role": "user", "content": "same question"}]}
            digest = durable.request_payload_sha256(body)
            self.assertEqual(durable.begin_request("cabinet-choice", digest)["state"], "claimed")
            durable.complete_request("cabinet-choice", digest, {"final_response": "original answer"}, {"total_tokens": 4})
            replay = durable.begin_request("cabinet-choice", digest)
            self.assertEqual(replay["state"], "completed")
            self.assertEqual(replay["result"]["final_response"], "original answer")
            changed = {**body, "model": "deepseek/deepseek-v4-flash", "tp_dialogue_model": "deepseek_flash"}
            with self.assertRaises(durable.RequestConflictError):
                durable.begin_request("cabinet-choice", durable.request_payload_sha256(changed))


PINNED_API = Path(os.environ.get("HERMES_PATCHED_API_SOURCE", "/opt/hermes/gateway/platforms/api_server.py"))


@unittest.skipUnless(PINNED_API.is_file(), "requires the built image or reviewed patched upstream source")
class PatchedConstructorTests(unittest.TestCase):
    def test_real_patched_constructor_uses_selected_model_and_shared_context_concurrently(self):
        tree = ast.parse(PINNED_API.read_text())
        adapter = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "APIServerAdapter")
        constructor = next(node for node in adapter.body if isinstance(node, ast.FunctionDef) and node.name == "_create_agent")
        code = ast.fix_missing_locations(ast.Module(body=[constructor], type_ignores=[]))
        namespace = {"Optional": Optional, "Any": Any, "os": os,
                     "dialogue_agent_settings": models.dialogue_agent_settings,
                     "managed_response_settings": managed_response_settings}
        exec(compile(code, str(PINNED_API), "exec"), namespace)
        run_agent = ModuleType("run_agent")
        run_agent.AIAgent = lambda **kwargs: SimpleNamespace(**kwargs)
        gateway = ModuleType("gateway.run")
        gateway._resolve_runtime_agent_kwargs = lambda: {"provider": "openrouter", "base_url": "https://openrouter.ai/api/v1"}
        gateway._resolve_gateway_model = lambda: "existing/model"
        gateway._load_gateway_config = lambda: {}
        gateway.GatewayRunner = SimpleNamespace(_load_reasoning_config=lambda: {}, _load_fallback_model=lambda: "fallback")
        tools = ModuleType("hermes_cli.tools_config")
        tools._get_platform_tools = lambda *args: []
        owner = SimpleNamespace(_ensure_session_db=lambda: None)
        def create(code):
            return namespace["_create_agent"](owner, ephemeral_system_prompt="same-chart-and-instructions", session_id="same-context", dialogue_model_code=code)
        with patch.dict(os.environ, ENABLED), patch.dict("sys.modules", {"run_agent": run_agent, "gateway.run": gateway, "hermes_cli.tools_config": tools}):
            with ThreadPoolExecutor(max_workers=2) as executor:
                luna, deepseek = list(executor.map(create, ["gpt_luna", "deepseek_flash"]))
        self.assertEqual(luna.model, "openai/gpt-6-luna")
        self.assertEqual(deepseek.model, "deepseek/deepseek-v4-flash")
        self.assertEqual(luna.ephemeral_system_prompt, deepseek.ephemeral_system_prompt)
        self.assertEqual(luna.session_id, deepseek.session_id)
        self.assertIsNone(luna.fallback_model)
        self.assertEqual(luna.max_tokens, deepseek.max_tokens)


if __name__ == "__main__":
    unittest.main()
