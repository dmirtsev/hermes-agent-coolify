from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from hermes_openrouter_accounting import (
    accounting_for_request,
    build_openrouter_accounting,
    openrouter_accounting_scope,
    record_current_openrouter_unresolved_attempt,
    record_openrouter_response,
)


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ENV = {
    "HERMES_RUNTIME_TIER": "balanced",
    "HERMES_RUNTIME_ID": "hermes-test-balanced",
}


def ns(**values):
    return SimpleNamespace(**values)


def response(
    generation_id: str | None,
    *,
    model: str | None = "provider/model-executed",
    provider: str | None = None,
    cost=None,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read: int = 0,
    cache_write: int = 0,
    reasoning: int = 0,
    metadata=None,
):
    usage = ns(
        prompt_tokens=input_tokens,
        completion_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
        prompt_tokens_details=ns(
            cached_tokens=cache_read,
            cache_write_tokens=cache_write,
        ),
        completion_tokens_details=ns(reasoning_tokens=reasoning),
        cost=cost,
    )
    return ns(
        id=generation_id,
        model=model,
        provider=provider,
        usage=usage,
        openrouter_metadata=metadata,
    )


def agent(provider: str = "openrouter", model: str = "configured/model"):
    return ns(provider=provider, model=model, base_url="https://openrouter.ai/api/v1")


@contextmanager
def runtime_environment():
    with patch.dict(os.environ, RUNTIME_ENV, clear=False):
        yield


class HermesOpenRouterAccountingTests(unittest.TestCase):
    def build(self, value, request_id: str = "chatcmpl-public"):
        with runtime_environment():
            return build_openrouter_accounting(value, request_id)

    def validate_output(self, value) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "accounting.json"
            path.write_text(json.dumps(value), encoding="utf-8")
            result = subprocess.run(
                ["python3", "scripts/validate_hermes_usage_result.py", str(path)],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_multi_call_tool_turn_aggregates_actual_cost_and_identity(self) -> None:
        value = agent(model="configured/not-request-model")
        metadata = {
            "endpoints": {
                "available": [
                    {"model": "ignored/model", "provider": "Other", "selected": False},
                    {"model": "provider/model-executed", "provider": "OpenAI", "selected": True},
                ]
            }
        }
        record_openrouter_response(
            value,
            response(
                "gen-tool",
                provider="OpenRouter",
                cost="0.0000014",
                input_tokens=100,
                output_tokens=20,
                cache_read=40,
                metadata=metadata,
            ),
        )
        record_openrouter_response(
            value,
            response(
                "gen-final",
                model="provider/model-final",
                provider="Anthropic",
                cost="0.0000016",
                input_tokens=140,
                output_tokens=30,
                reasoning=7,
            ),
        )

        result = self.build(value)
        self.assertEqual(result["model_execution"]["configured_model"], "configured/not-request-model")
        self.assertEqual(result["model_execution"]["executed_providers"], ["OpenAI", "Anthropic"])
        self.assertEqual(result["model_execution"]["provider_reported_models"], ["provider/model-executed", "provider/model-final"])
        self.assertEqual(result["model_execution"]["upstream_generation_ids"], ["gen-tool", "gen-final"])
        self.assertEqual(result["tokens"], {"input": 240, "output": 50, "cache_read": 40, "cache_write": 0, "reasoning": 7, "total": 290})
        self.assertEqual(result["cost"]["status"], "actual")
        self.assertTrue(result["cost"]["fully_reconciled"])
        self.assertEqual(result["cost"]["amount_micro_usd"], 3)
        self.assertEqual(result["cost"]["amount_usd"], "0.000003")
        self.validate_output(result)

    def test_request_rounds_exact_total_once_not_each_generation(self) -> None:
        value = agent()
        record_openrouter_response(value, response("gen-small-1", cost="0.0000004"))
        record_openrouter_response(value, response("gen-small-2", cost="0.0000004"))

        result = self.build(value)
        self.assertEqual(result["generations"][0]["cost"]["amount_micro_usd"], 0)
        self.assertEqual(result["generations"][1]["cost"]["amount_micro_usd"], 0)
        self.assertEqual(result["cost"]["amount_usd"], "0.0000008")
        self.assertEqual(result["cost"]["amount_micro_usd"], 1)
        self.validate_output(result)

    def test_retry_without_response_prevents_false_full_reconciliation(self) -> None:
        value = agent()
        self.assertTrue(record_openrouter_response(value, None))
        record_openrouter_response(value, response("gen-retry", cost="0.001", input_tokens=10, output_tokens=1))
        record_openrouter_response(value, response("gen-success", cost="0.002", input_tokens=20, output_tokens=2))

        result = self.build(value)
        self.assertEqual(len(result["generations"]), 3)
        self.assertEqual(result["cost"]["status"], "cost_unavailable")
        self.assertEqual(result["cost"]["reason"], "upstream_generation_id_missing")
        self.assertNotIn("amount_micro_usd", result["cost"])
        self.validate_output(result)

    def test_missing_cost_with_generation_id_is_pending_for_lookup(self) -> None:
        value = agent()
        record_openrouter_response(value, response("gen-pending", cost=None, input_tokens=12, output_tokens=3))

        result = self.build(value)
        self.assertEqual(result["cost"], {
            "status": "pending",
            "reason": "generation_lookup_pending",
            "generation_ids": ["gen-pending"],
            "fully_reconciled": False,
        })
        self.assertEqual(result["generations"][0]["cost"]["status"], "pending")
        self.validate_output(result)

    def test_missing_cost_and_generation_id_is_unavailable_not_estimated(self) -> None:
        value = agent()
        record_openrouter_response(value, response(None, cost=None, input_tokens=12, output_tokens=3))

        result = self.build(value)
        self.assertEqual(result["cost"]["status"], "cost_unavailable")
        self.assertEqual(result["cost"]["reason"], "upstream_generation_id_missing")
        self.assertNotIn("amount_micro_usd", result["cost"])
        self.validate_output(result)

    def test_duplicate_generation_delivery_is_merged_not_double_charged(self) -> None:
        value = agent()
        record_openrouter_response(value, response("gen-duplicate", cost=None, input_tokens=50, output_tokens=5))
        inserted = record_openrouter_response(
            value,
            response("gen-duplicate", provider="DeepInfra", cost="0.004", input_tokens=50, output_tokens=5),
        )

        result = self.build(value)
        self.assertFalse(inserted)
        self.assertEqual(len(result["generations"]), 1)
        self.assertEqual(result["tokens"]["total"], 55)
        self.assertEqual(result["cost"]["amount_micro_usd"], 4000)
        self.assertEqual(result["model_execution"]["executed_providers"], ["DeepInfra"])
        self.validate_output(result)

    def test_conflicting_duplicate_generation_fails_closed(self) -> None:
        value = agent()
        record_openrouter_response(
            value,
            response("gen-conflict", provider="OpenAI", cost="0.004", input_tokens=50, output_tokens=5),
        )
        record_openrouter_response(
            value,
            response("gen-conflict", provider="DeepInfra", cost="0.005", input_tokens=51, output_tokens=5),
        )

        result = self.build(value)
        self.assertEqual(len(result["generations"]), 1)
        self.assertEqual(result["cost"]["status"], "cost_unavailable")
        self.assertEqual(result["cost"]["reason"], "duplicate_generation_conflict")
        self.assertNotIn("amount_micro_usd", result["cost"])
        self.validate_output(result)

    def test_non_openrouter_provider_has_no_openrouter_extension(self) -> None:
        value = agent(provider="anthropic")
        value.base_url = "https://api.anthropic.com"
        self.assertFalse(record_openrouter_response(value, response("gen-other", cost="1")))
        self.assertIsNone(self.build(value))

    def test_openrouter_evidence_survives_mutable_provider_fallback(self) -> None:
        value = agent()
        record_openrouter_response(value, response("gen-openrouter", cost="0.01"))
        value.provider = "anthropic"
        value.base_url = "https://api.anthropic.com"
        value.model = "claude-fallback"

        result = self.build(value)
        self.assertIsNotNone(result)
        self.assertEqual(result["cost"]["status"], "actual")
        self.assertEqual(result["model_execution"]["configured_model"], "configured/model")
        self.validate_output(result)

    def test_auxiliary_scope_fails_closed(self) -> None:
        value = agent()
        record_openrouter_response(value, response("gen-main", cost="0.01"))
        # The agent's mutable provider may already have changed by fallback;
        # the request still owns earlier OpenRouter spend.
        value.provider = "anthropic"
        value.base_url = "https://api.anthropic.com"
        with openrouter_accounting_scope(value):
            self.assertTrue(record_current_openrouter_unresolved_attempt("auxiliary_test"))

        result = self.build(value)
        self.assertEqual(result["cost"]["status"], "cost_unavailable")
        self.assertEqual(result["cost"]["reason"], "upstream_generation_id_missing")
        self.validate_output(result)

    def test_public_request_id_is_stamped_without_mutating_cached_accounting(self) -> None:
        value = agent()
        record_openrouter_response(value, response("gen-one", cost="0.01", input_tokens=1, output_tokens=1))
        internal = self.build(value, request_id="internal")
        public = accounting_for_request(internal, "chatcmpl-new")

        self.assertEqual(internal["request_id"], "internal")
        self.assertEqual(public["request_id"], "chatcmpl-new")
        self.assertEqual(public["model_execution"]["configured_model"], "configured/model")
        self.validate_output(public)

    def test_stable_billing_id_survives_idempotent_response_replay(self) -> None:
        value = agent()
        record_openrouter_response(value, response("gen-stable", cost="0.01"))
        internal = self.build(value, request_id="hermesacct_stable")

        first = accounting_for_request(internal, "chatcmpl-first")
        replay = accounting_for_request(internal, "chatcmpl-replay")
        self.assertEqual(first["request_id"], "hermesacct_stable")
        self.assertEqual(replay["request_id"], "hermesacct_stable")
        self.assertEqual(first["model_execution"]["upstream_generation_ids"], ["gen-stable"])
        self.assertEqual(replay["model_execution"]["upstream_generation_ids"], ["gen-stable"])

    def test_no_generation_returns_explicit_unavailable_status(self) -> None:
        result = self.build(agent())
        self.assertEqual(result["generations"], [])
        self.assertEqual(result["cost"]["reason"], "no_billable_generations")
        self.validate_output(result)

    def test_validator_rejects_catalog_estimate_presented_as_actual(self) -> None:
        value = agent()
        record_openrouter_response(value, response("gen-one", cost=None, input_tokens=1, output_tokens=1))
        result = self.build(value)
        result["cost"] = {
            "status": "actual",
            "amount_micro_usd": 99,
            "amount_usd": "0.000099",
            "currency": "USD",
            "source": "catalog_estimate",
            "fully_reconciled": True,
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "invalid.json"
            path.write_text(json.dumps(result), encoding="utf-8")
            checked = subprocess.run(
                ["python3", "scripts/validate_hermes_usage_result.py", str(path)],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
        self.assertNotEqual(checked.returncode, 0)

    def test_validator_rejects_false_aggregate_decimal(self) -> None:
        value = agent()
        record_openrouter_response(value, response("gen-one", cost="0.1", input_tokens=1, output_tokens=1))
        result = self.build(value)
        result["cost"]["amount_usd"] = "999"
        result["cost"]["amount_micro_usd"] = 999000000
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "invalid.json"
            path.write_text(json.dumps(result), encoding="utf-8")
            checked = subprocess.run(
                ["python3", "scripts/validate_hermes_usage_result.py", str(path)],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
        self.assertNotEqual(checked.returncode, 0)


if __name__ == "__main__":
    unittest.main()
