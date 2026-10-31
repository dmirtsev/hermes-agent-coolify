import contextvars
import os
import tempfile
import unittest
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import hermes_durable_accounting as durable
import hermes_openrouter_accounting as openrouter_accounting


def _payload(value: str) -> str:
    return durable.request_payload_sha256({"model": "hermes-agent", "messages": [value]})


def _pending_generation(generation_id: str) -> dict:
    return {
        "generation_id": generation_id,
        "executed_provider": None,
        "executed_model": None,
        "tokens": {
            "input": 0,
            "output": 0,
            "cache_read": 0,
            "cache_write": 0,
            "reasoning": 0,
            "total": 0,
        },
        "_actual_cost": None,
    }


class ProviderError(RuntimeError):
    def __init__(self, status_code: int, body: dict):
        super().__init__("provider request failed")
        self.status_code = status_code
        self.body = body


class ProviderResponse:
    def __init__(self, status_code=429):
        self.status_code = status_code
        self.headers = {"retry-after": "3"}


class DeepSeekTransientError(RuntimeError):
    def __init__(self, status_code=429):
        super().__init__("provider temporarily unavailable")
        self.status_code = status_code
        self.response = ProviderResponse(status_code)
        self.body = {"error": {"metadata": {"provider_name": "DeepInfra"}}}


def _openrouter_credit_error() -> ProviderError:
    return ProviderError(
        402,
        {
            "error": {
                "message": "Insufficient credits",
                "code": 402,
                "metadata": {"limit_source": "openrouter_credits"},
            }
        },
    )


class DurableAccountingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.environment = patch.dict(
            os.environ,
            {
                "HERMES_ACCOUNTING_JOURNAL_PATH": str(Path(self.temp.name) / "journal.sqlite3"),
                "HERMES_RUNTIME_TIER": "balanced",
                "HERMES_RUNTIME_ID": "test-balanced-1",
            },
            clear=False,
        )
        self.environment.start()
        durable.ensure_journal()

    def tearDown(self):
        self.environment.stop()
        self.temp.cleanup()

    def test_restart_replays_completed_result_without_second_claim(self):
        digest = _payload("hello")
        self.assertEqual(durable.begin_request("cabinet-1", digest)["state"], "claimed")
        usage = {"total_tokens": 3, "_hermes_openrouter_accounting": {"cost": {"status": "actual"}}}
        durable.complete_request("cabinet-1", digest, {"final_response": "ok"}, usage)

        # Every call opens SQLite afresh: this exercises process-independent state.
        replay = durable.begin_request("cabinet-1", digest)
        self.assertEqual(replay["state"], "completed")
        self.assertEqual(replay["result"]["final_response"], "ok")
        self.assertEqual(replay["usage"]["total_tokens"], 3)
        with self.assertRaises(durable.RequestConflictError):
            durable.complete_request(
                "cabinet-1", digest, {"final_response": "changed"}, usage
            )

    def test_safe_view_reports_only_verified_managed_terminal_failures(self):
        for index, code in enumerate(("managed_transit_validation_exhausted", "managed_transit_validation_unavailable")):
            key=f"managed-terminal-{index}"; digest=_payload(key)
            durable.begin_request(key,digest)
            durable.record_generation_evidence(_pending_generation(f"gen-{index}"), "configured/model", request_key=key)
            durable.complete_request(key,digest,{"failed":True,"completed":False,"final_response":"", "error":code,
                "messages":[{"content":"private text"}], "chain":"private chain"}, {"total_tokens":1})
            view=durable.get_request_view(key)
            self.assertEqual(view["state"],"completed")
            self.assertEqual(view["terminal_outcome"],{"code":code,"answer_available":False})
            self.assertNotIn("private text",str(view));self.assertNotIn("private chain",str(view))
            self.assertIn("accounting",view)
            self.assertEqual(view["evidence"]["generation_ids"],[f"gen-{index}"])
            self.assertEqual(view["accounting"]["model_execution"]["upstream_generation_ids"],[f"gen-{index}"])

    def test_safe_view_does_not_infer_terminal_outcome_from_partial_or_other_results(self):
        base={"failed":True,"completed":False,"final_response":"", "error":"managed_transit_validation_exhausted"}
        for index, override in enumerate(({"failed":False},{"completed":True},{"final_response":"answer"},
                {"error":"other_error"},{"completed":None},{"final_response":None},{"error":None})):
            key=f"not-managed-terminal-{index}";digest=_payload(key)
            durable.begin_request(key,digest);durable.complete_request(key,digest,{**base,**override},{"total_tokens":1})
            self.assertNotIn("terminal_outcome",durable.get_request_view(key))

    def test_same_key_in_flight_and_different_payload_are_blocked(self):
        digest = _payload("one")
        durable.begin_request("cabinet-2", digest)
        with self.assertRaises(durable.RequestInFlightError):
            durable.begin_request("cabinet-2", digest)
        with self.assertRaises(durable.RequestConflictError):
            durable.begin_request("cabinet-2", _payload("two"))

    def test_frozen_completed_payload_replays_without_new_json_format(self):
        old = {"model": "test/model", "messages": ["current facts"]}
        digest = durable.request_payload_sha256(old)
        durable.begin_request("frozen-format", digest)
        durable.complete_request("frozen-format", digest, {"final_response": "saved"}, {"total_tokens": 1})
        self.assertEqual(durable.begin_request("frozen-format", digest)["result"]["final_response"], "saved")
        updated = {**old, "response_format": {"type": "json_object"}}
        with self.assertRaises(durable.RequestConflictError):
            durable.begin_request("frozen-format", durable.request_payload_sha256(updated))

    def test_canonical_hash_contract_is_stable_for_unicode_and_numbers(self):
        payload = {"z": "ёлка", "a": [1.0, -0.0, 1e-7, {"é": "ok"}]}
        self.assertEqual(
            durable.canonical_request_payload(payload),
            '{"a":[1,0,1e-7,{"é":"ok"}],"z":"ёлка"}',
        )
        self.assertEqual(
            durable.request_payload_sha256(payload),
            "abcea386650cd2d640c0f3da14be6ee91051d9998da6485cc7f2480bb7025d80",
        )

    def test_legacy_payload_is_checked_by_gateway_and_replays_exactly(self):
        payload = {"model": "test/model", "messages": ["x"], "fraction": 1e-6}
        legacy = durable.legacy_request_payload_sha256(payload)
        current = durable.request_payload_sha256(payload)
        self.assertNotEqual(legacy, current)
        durable.begin_request("old-request", legacy)
        self.assertFalse(durable.is_exact_completed_request("old-request", payload))
        durable.complete_request("old-request", legacy, {"final_response": "saved"}, {"total_tokens": 1})
        self.assertTrue(durable.is_exact_completed_request("old-request", payload))
        self.assertFalse(durable.is_exact_completed_request("old-request", {**payload, "model": "different"}))
        self.assertFalse(durable.is_exact_completed_request("missing", payload))
        view = durable.seal_not_dispatched("old-request", current, legacy_payload_sha256=legacy)
        self.assertEqual(view["state"], "completed")
        replay = durable.begin_request("old-request", current, legacy_payload_sha256=legacy)
        self.assertEqual(replay["result"]["final_response"], "saved")
        changed = {**payload, "model": "different"}
        with self.assertRaises(durable.RequestConflictError):
            durable.seal_not_dispatched("old-request", durable.request_payload_sha256(changed),
                legacy_payload_sha256=durable.legacy_request_payload_sha256(changed))
        with self.assertRaises(durable.RequestConflictError):
            durable.begin_request("old-request", durable.request_payload_sha256(changed),
                legacy_payload_sha256=durable.legacy_request_payload_sha256(changed))

    def test_ecmascript_unicode_and_number_vectors(self):
        payload = {"\ue000": 1e-6, "😀": [1e-7, 1e20, 1e21, -0.0, 9007199254740993]}
        self.assertEqual(durable.canonical_request_payload(payload),
            '{"😀":[1e-7,100000000000000000000,1e+21,0,9007199254740992],"\ue000":0.000001}')

    def test_failed_execution_is_fail_closed_on_retry(self):
        digest = _payload("failure")
        durable.begin_request("cabinet-3", digest)
        durable.fail_request("cabinet-3", digest, "agent_execution_failed")
        with self.assertRaises(durable.RequestUnresolvedError):
            durable.begin_request("cabinet-3", digest)

    def test_openrouter_credit_rejection_is_terminal_and_replayable(self):
        key = "cabinet-provider-rejected"
        digest = _payload("provider rejected")
        durable.begin_request(key, digest)
        durable.record_unresolved_evidence(
            "provider_attempt_failed", "configured/model", request_key=key
        )

        terminal = durable.complete_provider_rejected_unbilled(
            key, digest, _openrouter_credit_error()
        )

        self.assertIsNotNone(terminal)
        result, usage = terminal
        self.assertEqual(
            result["_hermes_terminal_error"]["code"],
            "provider_temporarily_unavailable",
        )
        self.assertEqual(usage["total_tokens"], 0)
        replay = durable.begin_request(key, digest)
        self.assertEqual(replay["state"], "completed")
        self.assertEqual(replay["result"], result)
        view = durable.get_request_view(key)
        self.assertEqual(view["state"], "provider_rejected_unbilled")
        self.assertFalse(view["terminal_outcome"]["billable"])
        self.assertIsNone(view["accounting"])

    def test_only_explicit_openrouter_credit_rejection_is_unbilled(self):
        cases = (
            ProviderError(
                402,
                {"error": {"metadata": {"limit_source": "other_limit"}}},
            ),
            ProviderError(
                429,
                {
                    "error": {
                        "metadata": {"limit_source": "openrouter_credits"}
                    }
                },
            ),
            TimeoutError("provider timeout"),
        )
        for index, error in enumerate(cases):
            key = f"cabinet-not-credit-{index}"
            digest = _payload(key)
            durable.begin_request(key, digest)
            self.assertIsNone(
                durable.complete_provider_rejected_unbilled(key, digest, error)
            )
            self.assertEqual(durable.get_request_view(key)["state"], "in_flight")

    def test_generation_evidence_blocks_unbilled_terminal_outcome(self):
        key = "cabinet-credit-after-generation"
        digest = _payload("generation exists")
        durable.begin_request(key, digest)
        durable.record_generation_evidence(
            _pending_generation("gen-before-402"),
            "configured/model",
            request_key=key,
        )

        self.assertIsNone(
            durable.complete_provider_rejected_unbilled(
                key, digest, _openrouter_credit_error()
            )
        )
        self.assertEqual(durable.get_request_view(key)["state"], "in_flight")

    def test_seal_not_dispatched_fences_a_late_http_retry(self):
        digest = _payload("never-arrived")
        view = durable.seal_not_dispatched("cabinet-never-arrived", digest)
        self.assertEqual(view["state"], "not_dispatched")
        repeated = durable.seal_not_dispatched("cabinet-never-arrived", digest)
        self.assertEqual(repeated["state"], "not_dispatched")
        with self.assertRaises(durable.RequestUnresolvedError):
            durable.begin_request("cabinet-never-arrived", digest)

    def test_seal_cannot_override_a_received_request(self):
        digest = _payload("received")
        durable.begin_request("cabinet-received", digest)
        view = durable.seal_not_dispatched("cabinet-received", digest)
        self.assertEqual(view["state"], "in_flight")
        with self.assertRaises(durable.RequestConflictError):
            durable.seal_not_dispatched("cabinet-received", _payload("different"))

    def test_evidence_survives_before_completion(self):
        digest = _payload("evidence")
        durable.begin_request("cabinet-4", digest)
        with durable.durable_request_scope("cabinet-4"):
            durable.record_generation_evidence(
                {
                    **_pending_generation("gen-4"),
                    "executed_provider": "ExampleProvider",
                    "executed_model": "example/model",
                    "tokens": {
                        "input": 10,
                        "output": 5,
                        "cache_read": 2,
                        "cache_write": 0,
                        "reasoning": 1,
                        "total": 15,
                    },
                    "_actual_cost": Decimal("0.00125"),
                },
                "configured/model",
            )
        view = durable.get_request_view("cabinet-4")
        self.assertEqual(view["state"], "in_flight")
        self.assertEqual(view["accounting"]["cost"]["amount_usd"], "0.00125")
        self.assertNotIn("result", view)

    def test_multi_generation_pending_reconciliation_uses_exact_values(self):
        digest = _payload("multi")
        durable.begin_request("cabinet-5", digest)
        with durable.durable_request_scope("cabinet-5"):
            durable.record_generation_evidence(_pending_generation("gen-a"), "configured/model")
            durable.record_generation_evidence(_pending_generation("gen-b"), "configured/model")

        calls = []

        def lookup(generation_id):
            calls.append(generation_id)
            number = 1 if generation_id == "gen-a" else 2
            return {
                "executed_provider": f"provider-{number}",
                "executed_model": f"model-{number}",
                "tokens": {
                    "input": 10 * number,
                    "output": 5 * number,
                    "cache_read": number,
                    "cache_write": 0,
                    "reasoning": 0,
                    "total": 15 * number,
                },
                "cost": Decimal(f"0.00{number}"),
            }

        reconciled = durable.reconcile_request("cabinet-5", lookup=lookup)
        self.assertEqual(calls, ["gen-a", "gen-b"])
        self.assertEqual(reconciled["reconciliation"]["status"], "actual")
        self.assertEqual(reconciled["accounting"]["cost"]["amount_usd"], "0.003")
        self.assertEqual(durable.get_request_view("cabinet-5")["accounting"]["tokens"]["total"], 45)

    def test_404_429_and_5xx_remain_pending_without_estimate(self):
        for index, status in enumerate((404, 429, 500, 502), start=1):
            key = f"cabinet-error-{index}"
            durable.begin_request(key, _payload(key))
            with durable.durable_request_scope(key):
                durable.record_generation_evidence(_pending_generation(f"gen-{status}"), "configured/model")

            def lookup(_generation_id, response_status=status):
                raise durable.LookupError(f"openrouter_http_{response_status}", response_status)

            reconciled = durable.reconcile_request(key, lookup=lookup)
            self.assertEqual(reconciled["reconciliation"]["status"], "pending")
            self.assertEqual(reconciled["accounting"]["cost"]["status"], "pending")
            self.assertNotIn("amount_usd", reconciled["accounting"]["cost"])
            self.assertEqual(
                reconciled["reconciliation"]["lookups"][0]["http_status"], status
            )

    def test_openrouter_lookup_uses_known_generation_and_native_values(self):
        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return (
                    b'{"data":{"id":"gen-http","total_cost":"0.0017",'
                    b'"native_tokens_prompt":21,"native_tokens_completion":4,'
                    b'"native_tokens_cached":3,"native_tokens_reasoning":2,'
                    b'"provider_name":"Provider","model":"provider/model"}}'
                )

        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "not-a-real-key"}), patch.object(
            durable.urllib.request, "urlopen", return_value=Response()
        ) as urlopen:
            value = durable._lookup_generation_http("gen-http")
        request = urlopen.call_args.args[0]
        self.assertIn("generation?id=gen-http", request.full_url)
        self.assertEqual(value["cost"], Decimal("0.0017"))
        self.assertEqual(value["tokens"]["total"], 25)
        self.assertEqual(value["tokens"]["cache_read"], 3)
        self.assertEqual(value["executed_model"], "provider/model")

    def test_openrouter_lookup_maps_http_failures_without_body_or_secret(self):
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "not-a-real-key"}):
            for status in (404, 429, 500, 502):
                error = urllib.error.HTTPError(
                    f"https://openrouter.example/generation?id=gen-{status}",
                    status,
                    "failure",
                    {},
                    None,
                )
                with patch.object(durable.urllib.request, "urlopen", side_effect=error):
                    with self.assertRaises(durable.LookupError) as raised:
                        durable._lookup_generation_http(f"gen-{status}")
                self.assertEqual(raised.exception.status_code, status)
                self.assertEqual(raised.exception.code, f"openrouter_http_{status}")
                self.assertNotIn("not-a-real-key", str(raised.exception))

    def test_missing_generation_id_requires_manual_review(self):
        durable.begin_request("cabinet-6", _payload("missing"))
        with durable.durable_request_scope("cabinet-6"):
            durable.record_unresolved_evidence("provider_attempt_failed", "configured/model")
        reconciled = durable.reconcile_request(
            "cabinet-6", lookup=lambda _generation_id: self.fail("lookup must not run")
        )
        self.assertEqual(reconciled["reconciliation"]["status"], "manual_review")
        self.assertEqual(reconciled["accounting"]["cost"]["status"], "cost_unavailable")

    def test_known_generation_is_reconciled_even_when_another_id_is_missing(self):
        durable.begin_request("cabinet-7", _payload("mixed"))
        with durable.durable_request_scope("cabinet-7"):
            durable.record_unresolved_evidence("provider_attempt_failed", "configured/model")
            durable.record_generation_evidence(_pending_generation("gen-known"), "configured/model")
        calls = []

        def lookup(generation_id):
            calls.append(generation_id)
            return {
                "executed_provider": "Provider",
                "executed_model": "provider/model",
                "tokens": {**_pending_generation("unused")["tokens"], "total": 2},
                "cost": Decimal("0.001"),
            }

        reconciled = durable.reconcile_request("cabinet-7", lookup=lookup)
        self.assertEqual(calls, ["gen-known"])
        self.assertEqual(reconciled["reconciliation"]["status"], "manual_review")
        known = next(
            generation
            for generation in reconciled["accounting"]["generations"]
            if generation["generation_id"] == "gen-known"
        )
        self.assertEqual(known["cost"]["status"], "actual")

    def test_internal_auth_uses_dedicated_token_without_exposing_it(self):
        with patch.dict(os.environ, {"HERMES_ACCOUNTING_INTERNAL_TOKEN": "internal-secret"}):
            self.assertTrue(durable.internal_authorized("Bearer internal-secret", "gateway-secret"))
            self.assertFalse(durable.internal_authorized("Bearer gateway-secret", "gateway-secret"))

    def test_request_key_rejects_path_delimiters_and_control_characters(self):
        for value in ("contains/slash", "contains space", "line\nbreak", ""):
            with self.assertRaises(durable.RequestKeyError):
                durable.normalize_request_key(value)

    def test_durable_evidence_write_failure_is_non_retryable_interrupt(self):
        agent = type(
            "Agent",
            (),
            {
                "provider": "openrouter",
                "model": "configured/model",
                "base_url": "https://openrouter.ai/api/v1",
            },
        )()
        response = type(
            "Response",
            (),
            {"id": "gen-write-failure", "model": "provider/model", "usage": None},
        )()
        durable.begin_request("cabinet-write-failure", _payload("write-failure"))
        with durable.durable_agent_request_scope(agent, "cabinet-write-failure"):
            with patch.object(
                openrouter_accounting,
                "_record_durable_generation",
                side_effect=durable.JournalError("disk_unavailable"),
            ):
                with self.assertRaises(
                    openrouter_accounting.DurableAccountingWriteError
                ) as raised:
                    contextvars.Context().run(
                        openrouter_accounting.record_openrouter_response,
                        agent,
                        response,
                    )
        self.assertIsInstance(raised.exception, InterruptedError)
        self.assertNotIn("disk_unavailable", str(raised.exception))

    def test_agent_binding_crosses_blank_thread_context(self):
        key = "cabinet-cross-thread"
        durable.begin_request(key, _payload("thread"))
        agent = type(
            "Agent",
            (),
            {
                "provider": "openrouter",
                "model": "configured/model",
                "base_url": "https://openrouter.ai/api/v1",
            },
        )()
        response = type(
            "Response",
            (),
            {"id": "gen-cross-thread", "model": "provider/model", "usage": None},
        )()

        def worker():
            self.assertIsNone(durable.current_request_key())
            return openrouter_accounting.record_openrouter_response(agent, response)

        with durable.durable_agent_request_scope(agent, key):
            with ThreadPoolExecutor(max_workers=1) as executor:
                self.assertTrue(executor.submit(contextvars.Context().run, worker).result())
        view = durable.get_request_view(key)
        self.assertEqual(view["evidence"]["event_count"], 1)
        self.assertEqual(view["evidence"]["generation_ids"], ["gen-cross-thread"])

    def test_concurrent_request_keys_are_isolated_when_agent_is_reused(self):
        keys = ("cabinet-concurrent-a", "cabinet-concurrent-b")
        for key in keys:
            durable.begin_request(key, _payload(key))
        agent = type(
            "Agent",
            (),
            {
                "provider": "openrouter",
                "model": "configured/model",
                "base_url": "https://openrouter.ai/api/v1",
            },
        )()

        def run(key):
            generation_id = "gen-" + key.rsplit("-", 1)[-1]
            response = type(
                "Response",
                (),
                {"id": generation_id, "model": "provider/model", "usage": None},
            )()
            with durable.durable_agent_request_scope(agent, key):
                openrouter_accounting.record_openrouter_response(agent, response)
                accounting = openrouter_accounting.build_openrouter_accounting(
                    agent, "acct-" + key, durable_request_key=key
                )
                return accounting["model_execution"]["upstream_generation_ids"]

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(run, key) for key in keys]
            results = [future.result() for future in futures]
        self.assertEqual(results, [["gen-a"], ["gen-b"]])
        self.assertEqual(
            durable.get_request_view(keys[0])["evidence"]["generation_ids"],
            ["gen-a"],
        )
        self.assertEqual(
            durable.get_request_view(keys[1])["evidence"]["generation_ids"],
            ["gen-b"],
        )

    def test_transient_provider_failure_is_failed_pending_and_replayable(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {"HERMES_ACCOUNTING_JOURNAL_PATH": str(Path(folder) / "j.sqlite3")}):
            key = "transient-key"
            digest = _payload("transient")
            self.assertEqual(durable.begin_request(key, digest)["state"], "claimed")
            result, _ = durable.complete_provider_dispatch_unknown(key, digest, DeepSeekTransientError())
            self.assertEqual(result["_hermes_terminal_error"]["dispatch_outcome"], "dispatch_unknown")
            self.assertEqual(durable.get_request_view(key)["state"], "failed")
            self.assertIsNone(durable.get_request_view(key)["accounting"])
            self.assertEqual(durable.get_request_view(key)["failure_code"], "provider_temporarily_unavailable")
            replay = durable.begin_request(key, digest)
            self.assertEqual(replay["state"], "failed")
            self.assertEqual(replay["result"]["_hermes_terminal_error"]["dispatch_outcome"], "dispatch_unknown")


if __name__ == "__main__":
    unittest.main()
