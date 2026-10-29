from __future__ import annotations

import ast
import json
import os
import threading
import tempfile
import subprocess
import sys
import unittest
from pathlib import Path
from copy import deepcopy
from types import ModuleType, SimpleNamespace
from typing import Any, Optional
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor

import hermes_dialogue_models as models
import hermes_durable_accounting as durable
from hermes_behavior_policy import managed_response_settings

ENABLED = {"HERMES_DIALOGUE_MODEL_CHOICE_ENABLED": "true", "HERMES_RUNTIME_TIER": "economy", "HERMES_FIXED_MODEL_PROVIDER": "openrouter"}
TERRA_ENABLED = {**ENABLED, "DEPLOYMENT_ENVIRONMENT": "test", "HERMES_TERRA_PERIODS_ENABLED": "true"}


class DialogueModelsTests(unittest.TestCase):
    def test_imports_from_installed_package_layout_without_qa_path(self):
        with tempfile.TemporaryDirectory() as directory:
            package = Path(directory) / "agent"
            package.mkdir()
            (package / "__init__.py").write_text("")
            source = Path(__file__).resolve().parents[1]
            for original, target in [("hermes_dialogue_models.py", "dialogue_models.py"),
                                     ("hermes_managed_validation.py", "hermes_managed_validation.py")]:
                (package / target).write_bytes((source / original).read_bytes())
            process = subprocess.run([sys.executable, "-c",
                "from agent.dialogue_models import validate_dialogue_model; assert validate_dialogue_model({}, None) is None"],
                cwd=directory, env={k: v for k, v in os.environ.items() if k != "PYTHONPATH"},
                capture_output=True, text=True)
            self.assertEqual(process.returncode, 0, process.stderr)

    def test_absent_choice_keeps_existing_behavior(self):
        self.assertIsNone(models.validate_dialogue_model({"model": "arbitrary-cosmetic-model"}, None, {}))
        kwargs = {"credential_pool": threading.Lock()}
        result = models.dialogue_agent_settings(None, kwargs, "existing/model", {}, "fallback")
        self.assertIs(result[0], kwargs)
        self.assertEqual(result[1:], ("existing/model", {}, "fallback"))

    def test_validate_choice_before_dispatch(self):
        for code, profile in models.PROFILES.items():
            body = {"model": profile["model"], "tp_dialogue_model": code}
            if code == "gpt_terra":
                body.update(tp_context_budget={"profile": "natal_transit"}, tp_managed_method_context={"synthetic": True})
            self.assertEqual(models.validate_dialogue_model(body, "stable-request", TERRA_ENABLED), code)
            for variant, env in [(body, {}), ({**body, "stream": True}, ENABLED), ({**body, "model": "other/model"}, ENABLED)]:
                with self.assertRaises(models.DialogueModelError):
                    models.validate_dialogue_model(variant, "stable-request", env)
            with self.assertRaises(models.DialogueModelError):
                models.validate_dialogue_model(body, None, ENABLED)
        with self.assertRaises(models.DialogueModelError):
            models.validate_dialogue_model({"tp_dialogue_model": "arbitrary"}, "stable-request", ENABLED)

    def test_profiles_are_per_request_and_pin_only_approved_routes(self):
        credentials = threading.Lock()
        kwargs = {"provider": "openrouter", "base_url": "https://openrouter.ai/api/v1", "credential_pool": credentials,
                  "request_overrides": {"extra_body": {"models": ["other/model"], "provider": {"only": ["old-provider"]}}}}
        with patch.dict(os.environ, ENABLED):
            one = models.dialogue_agent_settings("gpt_luna", kwargs, "old/model", {}, "fallback")
            two = models.dialogue_agent_settings("deepseek_flash", kwargs, "old/model", {}, "fallback")
        self.assertIs(one[0]["credential_pool"], credentials)
        self.assertEqual(kwargs["request_overrides"]["extra_body"]["provider"]["only"], ["old-provider"])
        self.assertEqual(one[0]["request_overrides"]["extra_body"]["provider"], {"only": ["openai"], "allow_fallbacks": False})
        self.assertEqual(two[0]["request_overrides"]["extra_body"]["provider"], {
            "order": ["deepinfra/fp8", "gmicloud/fp8"],
            "only": ["deepinfra/fp8", "gmicloud/fp8"],
            "allow_fallbacks": True,
            "max_price": {"prompt": 0.5, "completion": 1.5},
        })
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
        with patch.dict(os.environ, TERRA_ENABLED):
            self.assertEqual(models.public_dialogue_models(), {"version": 1, "codes": ["deepseek_flash", "gpt_luna", "gpt_terra"]})

    def test_terra_pins_openai_and_medium_reasoning_without_fallback(self):
        kwargs = {"provider": "openrouter", "base_url": "https://openrouter.ai/api/v1",
                  "request_overrides": {"extra_body": {"models": ["other/model"]}}}
        with patch.dict(os.environ, TERRA_ENABLED):
            result = models.dialogue_agent_settings("gpt_terra", kwargs, "old/model", {}, "fallback")
        self.assertEqual(result[1:], ("openai/gpt-6.1-terra", {"effort": "medium"}, None))
        self.assertEqual(result[0]["request_overrides"]["extra_body"]["provider"],
                         {"only": ["openai"], "allow_fallbacks": False,
                          "require_parameters": True, "max_price": {"prompt": 2.5, "completion": 7.5}})
        self.assertEqual(result[0]["request_overrides"]["extra_body"]["reasoning"], {"effort": "medium"})
        self.assertEqual(result[0]["max_tokens"], 8192)

    def test_terra_is_not_a_production_or_natal_route(self):
        body = {"model": "openai/gpt-6.1-terra", "tp_dialogue_model": "gpt_terra",
                "tp_context_budget": {"profile": "natal_transit"}, "tp_managed_method_context": {"synthetic": True}}
        for change in [{"DEPLOYMENT_ENVIRONMENT": "production"}, {"HERMES_TERRA_PERIODS_ENABLED": "false"}]:
            with self.assertRaises(models.DialogueModelError):
                models.validate_dialogue_model(body, "synthetic", {**TERRA_ENABLED, **change})
        for change in [{"tp_context_budget": {"profile": "natal"}}, {"tp_managed_method_context": None}]:
            with self.assertRaises(models.DialogueModelError):
                models.validate_dialogue_model({**body, **change}, "synthetic", TERRA_ENABLED)

    def test_terra_accepts_existing_saved_transit_moment_wire_shape(self):
        body = {"model": "openai/gpt-6.1-terra", "tp_dialogue_model": "gpt_terra",
                "tp_context_budget": {"profile": "natal_transit"},
                "tp_execution_mode": "interpretation_facts_v1", "tp_answer_format": "hermes_managed_v1",
                "messages": [{"role": "system", "content": "managed"}, {"role": "user", "content": json.dumps({
                    "context": {"temporal": {"kind": "transit_moment", "source": {"natalResultId": "n"}}, "source": {"resultId": "n"}},
                    "natalSource": {"resultId": "n"},
                    "facts": {"layers": [{"chartType": "natal", "relationMethod": "base"}, {"chartType": "transit", "relationMethod": "transit"}],
                              "statements": {"format": "typed_rows_v1", "layouts": [["ref", "status", "text"]],
                                             "rows": [[0, "s1", "present", "Натал: Солнце в Овне"],
                                                      [0, "s2", "present", "Транзит: Нептун — квадрат — натал: ASC"]],
                                             "count": 2}},
                })}]}
        self.assertEqual(models.validate_dialogue_model(body, "synthetic", TERRA_ENABLED), "gpt_terra")
        bad = dict(body)
        bad["messages"] = list(body["messages"])
        bad["messages"][1] = {"role": "user", "content": body["messages"][1]["content"].replace("transit_moment", "natal")}
        with self.assertRaises(models.DialogueModelError):
            models.validate_dialogue_model(bad, "synthetic", TERRA_ENABLED)
        for key, value in [("natalSource", None), ("facts", {"layers": [{"chartType": "natal"}], "statements": [{"ref": "s1"}]}),
                           ("tp_dialogue_mode", "free_dialogue")]:
            invalid = dict(body)
            if key == "tp_dialogue_mode": invalid[key] = value
            else:
                user = json.loads(invalid["messages"][1]["content"]); user[key] = value
                invalid["messages"] = [invalid["messages"][0], {"role": "user", "content": json.dumps(user)}]
            with self.assertRaises(models.DialogueModelError):
                models.validate_dialogue_model(invalid, "synthetic", TERRA_ENABLED)

        # The production builder packs facts. Exercise malformed packed rows,
        # mismatched natal identity and a different forecasting method as well.
        original = json.loads(body["messages"][1]["content"])
        mutations = [
            lambda p: p["facts"].update(statements=[]),
            lambda p: p["facts"]["statements"].update(count=3),
            lambda p: p["facts"]["statements"].update(rows=[[2, "s1"]], count=1),
            lambda p: p["natalSource"].update(resultId="another-natal"),
            lambda p: p["context"]["temporal"]["source"].update(natalResultId="another-natal"),
            lambda p: p["context"].update(source=None),
            lambda p: p["context"]["temporal"].pop("source"),
            lambda p: p["facts"]["layers"].append({"chartType": "solar_return", "relationMethod": "solar_return"}),
            lambda p: p["facts"]["layers"][1].update(relationMethod="progression"),
        ]
        for mutate in mutations:
            payload = deepcopy(original)
            mutate(payload)
            invalid = {**body, "messages": [body["messages"][0], {"role": "user", "content": json.dumps(payload)}]}
            with self.assertRaises(models.DialogueModelError):
                models.validate_dialogue_model(invalid, "synthetic", TERRA_ENABLED)

        for field, value in [("tp_execution_mode", "other"), ("tp_answer_format", "other"),
                             ("tp_context_budget", {"profile": "natal"})]:
            with self.assertRaises(models.DialogueModelError):
                models.validate_dialogue_model({**body, field: value}, "synthetic", TERRA_ENABLED)

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
        namespace = {"Optional": Optional, "Any": Any, "os": os, "dialogue_agent_settings": models.dialogue_agent_settings,
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
            return namespace["_create_agent"](owner, ephemeral_system_prompt="same-chart-and-instructions", session_id="same-context", dialogue_model_code=code,
                                               strict_context_only=True, managed_continuation=True,
                                               managed_response_format={"type": "json_object"})
        with patch.dict(os.environ, ENABLED), patch.dict("sys.modules", {"run_agent": run_agent, "gateway.run": gateway, "hermes_cli.tools_config": tools}):
            with ThreadPoolExecutor(max_workers=2) as executor:
                luna, deepseek = list(executor.map(create, ["gpt_luna", "deepseek_flash"]))
        self.assertEqual(luna.model, "openai/gpt-6-luna")
        self.assertEqual(deepseek.model, "deepseek/deepseek-v4-flash")
        self.assertEqual(luna.ephemeral_system_prompt, deepseek.ephemeral_system_prompt)
        self.assertEqual(luna.session_id, deepseek.session_id)
        self.assertIsNone(luna.fallback_model)
        self.assertEqual(luna.max_tokens, deepseek.max_tokens)
        for agent in (luna, deepseek):
            self.assertEqual(agent.request_overrides["response_format"], {"type": "json_object"})
            self.assertEqual(agent.max_iterations, 3)
            self.assertEqual(agent.enabled_toolsets, [])
            self.assertIsNone(agent.session_db)
            self.assertTrue(agent.skip_context_files)
            self.assertTrue(agent.skip_memory)


if __name__ == "__main__":
    unittest.main()
