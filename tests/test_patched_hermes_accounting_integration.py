from __future__ import annotations

import asyncio
import contextvars
import json
import os
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch


PINNED_SOURCE = Path("/opt/hermes/agent/transports/chat_completions.py")


@unittest.skipUnless(PINNED_SOURCE.is_file(), "runs against the built Hermes image")
class PatchedHermesAccountingIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        sys.path.insert(0, "/opt/hermes")

    def test_transport_retains_upstream_evidence_and_enables_routing_metadata(self) -> None:
        from agent.transports.chat_completions import ChatCompletionsTransport

        class Profile:
            name = "openrouter"
            fixed_temperature = None

            def prepare_messages(self, messages):
                return messages

            def get_max_tokens(self, model):
                return None

            def build_api_kwargs_extras(self, **kwargs):
                return {}, {"extra_headers": {"x-grok-conv-id": "session-1"}}

            def build_extra_body(self, **kwargs):
                return {}

        transport = ChatCompletionsTransport()
        kwargs = transport.build_kwargs(
            "configured/model",
            [{"role": "user", "content": "test"}],
            provider_profile=Profile(),
            request_overrides={
                "extra_headers": {
                    "X-OpenRouter-Metadata": "disabled",
                    "X-Caller-Trace": "trace-1",
                }
            },
        )
        self.assertEqual(kwargs["extra_headers"]["x-grok-conv-id"], "session-1")
        self.assertEqual(kwargs["extra_headers"]["X-Caller-Trace"], "trace-1")
        self.assertEqual(kwargs["extra_headers"]["X-OpenRouter-Metadata"], "enabled")

        usage = SimpleNamespace(
            prompt_tokens=10,
            completion_tokens=2,
            total_tokens=12,
            cost="0.000012",
            prompt_tokens_details=SimpleNamespace(cached_tokens=3, cache_write_tokens=0),
            completion_tokens_details=SimpleNamespace(reasoning_tokens=1),
        )
        raw = SimpleNamespace(
            id="gen-transport",
            model="executed/model",
            provider="OpenAI",
            usage=usage,
            openrouter_metadata=None,
            choices=[SimpleNamespace(
                finish_reason="stop",
                message=SimpleNamespace(
                    content="ok",
                    tool_calls=None,
                    reasoning=None,
                    reasoning_content=None,
                    reasoning_details=None,
                ),
            )],
        )
        normalized = transport.normalize_response(raw)
        evidence = normalized.provider_data["upstream_generation"]
        self.assertEqual(evidence["generation_id"], "gen-transport")
        self.assertEqual(evidence["executed_model"], "executed/model")
        self.assertEqual(str(evidence["_actual_cost"]), "0.000012")

    def test_internal_accounting_read_is_protected_and_omits_stored_result(self) -> None:
        from agent.durable_accounting import (
            begin_request,
            complete_request,
            request_payload_sha256,
        )
        from gateway.platforms.api_server import APIServerAdapter

        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {
                "HERMES_ACCOUNTING_JOURNAL_PATH": str(Path(directory) / "journal.sqlite3"),
                "HERMES_ACCOUNTING_INTERNAL_TOKEN": "internal-test-token",
            },
            clear=False,
        ):
            digest = request_payload_sha256({"messages": ["private prompt"]})
            begin_request("cabinet-safe-read", digest)
            complete_request(
                "cabinet-safe-read",
                digest,
                {"final_response": "private assistant response"},
                {"total_tokens": 1},
            )

            adapter = object.__new__(APIServerAdapter)
            adapter._api_key = "gateway-token"

            class Request:
                match_info = {"request_key": "cabinet-safe-read"}

                def __init__(self, token):
                    self.headers = {"Authorization": f"Bearer {token}"}

            unauthorized = asyncio.run(
                adapter._handle_internal_accounting_get(Request("wrong-token"))
            )
            self.assertEqual(unauthorized.status, 401)
            response = asyncio.run(
                adapter._handle_internal_accounting_get(Request("internal-test-token"))
            )
            self.assertEqual(response.status, 200)
            payload = json.loads(response.text)
            rendered = json.dumps(payload)
            self.assertNotIn("private prompt", rendered)
            self.assertNotIn("private assistant response", rendered)
            self.assertNotIn("internal-test-token", rendered)
            self.assertEqual(payload["state"], "completed")

    def test_agent_bound_request_key_records_from_blank_worker_context(self) -> None:
        from agent.durable_accounting import (
            begin_request,
            current_request_key,
            get_request_view,
            request_payload_sha256,
        )
        from agent.openrouter_accounting import record_openrouter_response
        from gateway.platforms.api_server import APIServerAdapter

        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {
                "HERMES_ACCOUNTING_JOURNAL_PATH": str(Path(directory) / "journal.sqlite3"),
            },
            clear=False,
        ):
            key = "integration-cross-thread"
            begin_request(key, request_payload_sha256({"messages": ["thread"]}))
            agent = SimpleNamespace(
                provider="openrouter",
                model="configured/model",
                base_url="https://openrouter.ai/api/v1",
                session_prompt_tokens=3,
                session_completion_tokens=2,
                session_total_tokens=5,
                session_cache_read_tokens=0,
                session_cache_write_tokens=0,
                session_reasoning_tokens=0,
                session_id="integration-session",
            )
            response = SimpleNamespace(
                id="gen-integration-thread",
                model="provider/model",
                provider="Provider",
                usage=SimpleNamespace(
                    prompt_tokens=3,
                    completion_tokens=2,
                    total_tokens=5,
                    cost="0.00001",
                    prompt_tokens_details=None,
                    completion_tokens_details=None,
                ),
                openrouter_metadata=None,
            )

            def worker():
                self.assertIsNone(current_request_key())
                return record_openrouter_response(agent, response)

            def run_conversation(**_kwargs):
                with ThreadPoolExecutor(max_workers=1) as executor:
                    self.assertTrue(executor.submit(
                        contextvars.Context().run, worker
                    ).result())
                return {"final_response": "ok", "completed": True}

            agent.run_conversation = run_conversation
            adapter = object.__new__(APIServerAdapter)
            adapter._create_agent = lambda **_kwargs: agent
            result, usage = asyncio.run(
                adapter._run_agent(
                    user_message="thread",
                    conversation_history=[],
                    accounting_request_key=key,
                )
            )
            self.assertEqual(result["final_response"], "ok")
            self.assertEqual(
                usage["_hermes_openrouter_accounting"]["cost"]["status"], "actual"
            )
            view = get_request_view(key)
            self.assertEqual(view["evidence"]["event_count"], 1)
            self.assertEqual(
                view["evidence"]["generation_ids"], ["gen-integration-thread"]
            )

    def test_hidden_stream_retry_fails_closed_after_later_success(self) -> None:
        import httpx

        from agent.chat_completion_helpers import interruptible_streaming_api_call
        from agent.openrouter_accounting import build_openrouter_accounting

        class Stream:
            def __init__(self, chunks=None, error=None, generation_id=None):
                self._chunks = chunks or []
                self._error = error
                self.response = SimpleNamespace(
                    headers={"X-Generation-Id": generation_id} if generation_id else {}
                )

            def __iter__(self):
                if self._error is not None:
                    raise self._error
                return iter(self._chunks)

        usage = SimpleNamespace(
            prompt_tokens=5,
            completion_tokens=1,
            total_tokens=6,
            cost="0.00001",
            prompt_tokens_details=SimpleNamespace(cached_tokens=0, cache_write_tokens=0),
            completion_tokens_details=SimpleNamespace(reasoning_tokens=0),
        )
        content_chunk = SimpleNamespace(
            id="gen-stream-success",
            model="executed/model",
            provider="DeepInfra",
            model_extra={},
            openrouter_metadata=None,
            choices=[SimpleNamespace(
                finish_reason="stop",
                delta=SimpleNamespace(
                    content="ok", reasoning_content=None, reasoning=None, tool_calls=None
                ),
            )],
            usage=None,
        )
        usage_chunk = SimpleNamespace(
            id="gen-stream-success",
            model="executed/model",
            provider="DeepInfra",
            model_extra={},
            openrouter_metadata=None,
            choices=[],
            usage=usage,
        )
        streams = iter([
            Stream(error=httpx.RemoteProtocolError("dropped"), generation_id="gen-possibly-billed"),
            Stream([content_chunk, usage_chunk], generation_id="gen-stream-success"),
        ])

        fake_agent = SimpleNamespace(
            provider="openrouter",
            model="configured/model",
            base_url="https://openrouter.ai/api/v1",
            api_mode="chat_completions",
            _interrupt_requested=False,
            stream_delta_callback=None,
            reasoning_callback=None,
        )
        fake_agent._create_request_openai_client = lambda **kwargs: SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(create=lambda **request_kwargs: next(streams))
            )
        )
        for name in (
            "_touch_activity", "_capture_rate_limits", "_capture_credits",
            "_stream_diag_capture_response", "_check_openrouter_cache_status",
            "_fire_reasoning_delta", "_fire_stream_delta", "_fire_tool_gen_started",
            "_emit_stream_drop", "_close_request_openai_client",
            "_abort_request_openai_client", "_replace_primary_openai_client",
            "_buffer_status", "_log_stream_retry",
        ):
            setattr(fake_agent, name, Mock())
        fake_agent._stream_diag_init = lambda: {
            "chunks": 0, "bytes": 0, "first_chunk_at": None
        }
        fake_agent._is_provider_stream_parse_error = lambda error: False

        with patch.dict(os.environ, {
            "HERMES_STREAM_RETRIES": "1",
            "HERMES_RUNTIME_TIER": "balanced",
            "HERMES_RUNTIME_ID": "hermes-test-balanced",
        }, clear=False):
            returned = interruptible_streaming_api_call(
                fake_agent, {"model": "configured/model", "messages": []}
            )
            accounting = build_openrouter_accounting(fake_agent, "hermesacct_stream")

        self.assertEqual(returned.id, "gen-stream-success")
        self.assertEqual(accounting["cost"]["status"], "cost_unavailable")
        self.assertEqual(accounting["cost"]["reason"], "upstream_generation_id_missing")
        self.assertEqual(accounting["model_execution"]["upstream_generation_ids"], ["gen-stream-success"])

    def test_auxiliary_path_marks_request_unresolved(self) -> None:
        from agent.auxiliary_client import _build_call_kwargs
        from agent.openrouter_accounting import (
            build_openrouter_accounting,
            openrouter_accounting_scope,
        )

        fake_agent = SimpleNamespace(
            provider="openrouter",
            model="configured/model",
            base_url="https://openrouter.ai/api/v1",
        )
        with openrouter_accounting_scope(fake_agent):
            kwargs = _build_call_kwargs(
                "openrouter", "configured/model", [{"role": "user", "content": "x"}]
            )
        self.assertEqual(kwargs["model"], "configured/model")
        accounting = build_openrouter_accounting(fake_agent, "hermesacct_aux")
        self.assertEqual(accounting["cost"]["status"], "cost_unavailable")
        self.assertEqual(accounting["cost"]["reason"], "upstream_generation_id_missing")

    def test_direct_summary_paths_are_accounted_and_metadata_enabled(self) -> None:
        source = Path("/opt/hermes/agent/chat_completion_helpers.py").read_text(encoding="utf-8")
        self.assertEqual(source.count("record_openrouter_response(agent, summary_response)"), 2)
        self.assertIn('record_openrouter_unresolved_attempt(agent, "iteration_summary_failed")', source)
        self.assertGreaterEqual(source.count('"X-OpenRouter-Metadata": "enabled"'), 2)

    def test_non_streaming_gateway_exposes_accounting_separate_from_cosmetic_model(self) -> None:
        import jsonschema

        from agent.openrouter_accounting import build_openrouter_accounting, record_openrouter_response
        from gateway.platforms.api_server import APIServerAdapter

        fixed_agent = SimpleNamespace(
            provider="openrouter",
            model="fixed/configured-model",
            base_url="https://openrouter.ai/api/v1",
        )
        raw = SimpleNamespace(
            id="gen-gateway",
            model="executed/provider-model",
            provider="DeepInfra",
            usage=SimpleNamespace(
                prompt_tokens=8,
                completion_tokens=2,
                total_tokens=10,
                cost="0.00001",
                prompt_tokens_details=SimpleNamespace(cached_tokens=0, cache_write_tokens=0),
                completion_tokens_details=SimpleNamespace(reasoning_tokens=0),
            ),
            openrouter_metadata=None,
        )
        record_openrouter_response(fixed_agent, raw)
        with patch.dict(os.environ, {
            "HERMES_RUNTIME_TIER": "economy",
            "HERMES_RUNTIME_ID": "hermes-test-economy",
        }, clear=False):
            internal = build_openrouter_accounting(fixed_agent, "internal")

        adapter = object.__new__(APIServerAdapter)
        adapter._api_key = ""
        adapter._model_name = "hermes-agent"
        adapter._check_auth = lambda request: None

        async def run_agent(**kwargs):
            return (
                {"final_response": "ok", "completed": True},
                {
                    "input_tokens": 8,
                    "output_tokens": 2,
                    "total_tokens": 10,
                    "_hermes_openrouter_accounting": internal,
                },
            )

        adapter._run_agent = run_agent

        class Request:
            headers = {}

            async def json(self):
                return {
                    "model": "caller/cosmetic-model",
                    "messages": [{"role": "user", "content": "test"}],
                    "stream": False,
                }

        response = asyncio.run(adapter._handle_chat_completions(Request()))
        payload = json.loads(response.text)
        self.assertEqual(payload["model"], "caller/cosmetic-model")
        self.assertEqual(payload["hermes_accounting"]["model_execution"]["configured_model"], "fixed/configured-model")
        self.assertEqual(payload["hermes_accounting"]["model_execution"]["provider_reported_models"], ["executed/provider-model"])
        self.assertEqual(payload["hermes_accounting"]["cost"]["amount_micro_usd"], 10)
        schema = json.loads((Path("/repo") / "deploy/hermes-tiers/usage-result.schema.json").read_text(encoding="utf-8"))
        jsonschema.Draft202012Validator.check_schema(schema)
        jsonschema.validate(payload["hermes_accounting"], schema)

    def test_idempotency_replay_keeps_stable_accounting_request_id(self) -> None:
        from agent.openrouter_accounting import build_openrouter_accounting, record_openrouter_response
        from gateway.platforms.api_server import APIServerAdapter

        fixed_agent = SimpleNamespace(
            provider="openrouter",
            model="fixed/configured-model",
            base_url="https://openrouter.ai/api/v1",
        )
        record_openrouter_response(fixed_agent, SimpleNamespace(
            id="gen-stable",
            model="executed/provider-model",
            provider="DeepInfra",
            usage=SimpleNamespace(
                prompt_tokens=1,
                completion_tokens=1,
                total_tokens=2,
                cost="0.00001",
                prompt_tokens_details=SimpleNamespace(cached_tokens=0, cache_write_tokens=0),
                completion_tokens_details=SimpleNamespace(reasoning_tokens=0),
            ),
            openrouter_metadata=None,
        ))
        with patch.dict(os.environ, {
            "HERMES_RUNTIME_TIER": "economy",
            "HERMES_RUNTIME_ID": "hermes-test-economy",
        }, clear=False):
            internal = build_openrouter_accounting(fixed_agent, "hermesacct_stable")

        adapter = object.__new__(APIServerAdapter)
        adapter._api_key = ""
        adapter._model_name = "hermes-agent"
        adapter._check_auth = lambda request: None
        calls = {"count": 0}

        async def run_agent(**kwargs):
            calls["count"] += 1
            return (
                {"final_response": "ok", "completed": True},
                {
                    "input_tokens": 1,
                    "output_tokens": 1,
                    "total_tokens": 2,
                    "_hermes_openrouter_accounting": internal,
                },
            )

        adapter._run_agent = run_agent

        class Request:
            headers = {"Idempotency-Key": "accounting-stable-replay-test"}

            async def json(self):
                return {
                    "model": "caller/model",
                    "messages": [{"role": "user", "content": "same"}],
                    "stream": False,
                }

        first = json.loads(asyncio.run(adapter._handle_chat_completions(Request())).text)
        second = json.loads(asyncio.run(adapter._handle_chat_completions(Request())).text)
        self.assertEqual(calls["count"], 1)
        self.assertNotEqual(first["id"], second["id"])
        self.assertEqual(first["hermes_accounting"]["request_id"], "hermesacct_stable")
        self.assertEqual(
            first["hermes_accounting"]["request_id"],
            second["hermes_accounting"]["request_id"],
        )
        self.assertEqual(
            first["hermes_accounting"]["model_execution"]["upstream_generation_ids"],
            second["hermes_accounting"]["model_execution"]["upstream_generation_ids"],
        )

    def test_non_streaming_responses_api_exposes_same_accounting_contract(self) -> None:
        from agent.openrouter_accounting import build_openrouter_accounting, record_openrouter_response
        from gateway.platforms.api_server import APIServerAdapter

        fixed_agent = SimpleNamespace(
            provider="openrouter",
            model="fixed/configured-model",
            base_url="https://openrouter.ai/api/v1",
        )
        record_openrouter_response(fixed_agent, SimpleNamespace(
            id="gen-responses",
            model="executed/provider-model",
            provider="OpenAI",
            usage=SimpleNamespace(
                prompt_tokens=6,
                completion_tokens=4,
                total_tokens=10,
                cost="0.00002",
                prompt_tokens_details=SimpleNamespace(cached_tokens=0, cache_write_tokens=0),
                completion_tokens_details=SimpleNamespace(reasoning_tokens=0),
            ),
            openrouter_metadata=None,
        ))
        with patch.dict(os.environ, {
            "HERMES_RUNTIME_TIER": "strong",
            "HERMES_RUNTIME_ID": "hermes-test-strong",
        }, clear=False):
            internal = build_openrouter_accounting(fixed_agent, "internal")

        adapter = object.__new__(APIServerAdapter)
        adapter._api_key = ""
        adapter._model_name = "hermes-agent"
        adapter._check_auth = lambda request: None

        async def run_agent(**kwargs):
            return (
                {
                    "final_response": "ok",
                    "completed": True,
                    "messages": [
                        {"role": "user", "content": "test"},
                        {"role": "assistant", "content": "ok"},
                    ],
                },
                {
                    "input_tokens": 6,
                    "output_tokens": 4,
                    "total_tokens": 10,
                    "_hermes_openrouter_accounting": internal,
                },
            )

        adapter._run_agent = run_agent

        class Request:
            headers = {}

            async def json(self):
                return {
                    "model": "caller/cosmetic-model",
                    "input": "test",
                    "stream": False,
                    "store": False,
                }

        response = asyncio.run(adapter._handle_responses(Request()))
        payload = json.loads(response.text)
        self.assertEqual(payload["model"], "caller/cosmetic-model")
        self.assertEqual(payload["hermes_accounting"]["request_id"], payload["id"])
        self.assertEqual(payload["hermes_accounting"]["cost"]["status"], "actual")
        self.assertEqual(payload["hermes_accounting"]["cost"]["amount_micro_usd"], 20)


if __name__ == "__main__":
    unittest.main()
