import copy
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import hermes_context_budget as budget


MODEL = "openai/gpt-6-luna"
PROFILE = {MODEL: {"route": "test:openai", "counter": "fake", "counterRevision": "1",
    "contextWindow": 142592, "accuracy": "calibrated", "evidenceSha256": "a" * 64}}


def body(limit=128000):
    return {"model": MODEL, "tp_dialogue_model": "gpt_luna", "tp_answer_format": "hermes_managed_v1",
        "tp_context_budget": {"contract": budget.CONTRACT, "profile": "natal_transit", "inputBudget": limit},
        "messages": [{"role": "system", "content": "Полная инструкция"}, {"role": "user", "content": "Полная история и все интервалы"}]}


class ContextBudgetTests(unittest.TestCase):
    def test_full_128k_needs_window_142592(self):
        receipt = budget.prepare_admission(body(), PROFILE, lambda *args: 128000)
        self.assertTrue(receipt["admitted"])
        self.assertEqual(receipt["marginTokens"], 6400)
        self.assertEqual(receipt["completionTokens"], 8192)
        smaller = copy.deepcopy(PROFILE)
        smaller[MODEL]["contextWindow"] -= 1
        self.assertEqual(budget.prepare_admission(body(), smaller, lambda *args: 128000)["reason"], "hermes_context_window_overflow")

    def test_input_overflow_does_not_drop_a_row(self):
        original = body()
        receipt = budget.prepare_admission(original, PROFILE, lambda *args: 128001)
        self.assertFalse(receipt["admitted"])
        self.assertEqual(original, body())

    def test_reference_counter_does_not_enable_a_route(self):
        profiles = copy.deepcopy(PROFILE)
        profiles[MODEL]["accuracy"] = "reference"
        self.assertFalse(budget.prepare_admission(body(), profiles, lambda *args: 1)["admitted"])

    def test_receipt_is_bound_to_full_messages_and_route(self):
        original = body()
        receipt = budget.prepare_admission(original, PROFILE, lambda *args: 99)
        original["tp_context_admission"] = receipt
        budget.require_admission(original, receipt)
        changed = copy.deepcopy(receipt)
        changed["messagesSha256"] = "0" * 64
        with self.assertRaises(budget.ContextBudgetError):
            budget.require_admission(original, changed)

    def test_each_attempt_checks_actual_final_messages_and_is_bounded(self):
        original = body()
        agent = SimpleNamespace()
        budget.install_context_budget(agent, original)
        kwargs = {"model": MODEL, "messages": original["messages"], "max_tokens": 8192, "extra_body": {"provider": {"only": ["openai"], "allow_fallbacks": False, "max_price": budget.PRICE_CAPS[MODEL]}, "service_tier": "default", "reasoning": {"effort": "high"}}}
        receipt = budget.prepare_admission(original, PROFILE, lambda *args: 77)
        with patch.object(budget, "prepare_admission", return_value=receipt) as count:
            for _ in range(3):
                budget.admit_provider_call(agent, kwargs)
            with self.assertRaises(budget.ContextBudgetError):
                budget.admit_provider_call(agent, kwargs)
            self.assertEqual(count.call_count, 3)
        # Manual continuation is a new operation, not the fourth automatic call.
        fresh = SimpleNamespace()
        budget.install_context_budget(fresh, original)
        with patch.object(budget, "prepare_admission", return_value=receipt):
            budget.admit_provider_call(fresh, kwargs)

    def test_hidden_context_mutation_is_rejected_before_provider(self):
        original = body()
        agent = SimpleNamespace()
        budget.install_context_budget(agent, original)
        with self.assertRaises(budget.ContextBudgetError):
            budget.admit_provider_call(agent, {"model": MODEL, "max_tokens": 8192,
                "messages": original["messages"] + [{"role": "user", "content": "hidden"}]})
        self.assertEqual(agent._tp_context_model_calls, 0)

    def test_committed_route_evidence_is_complete_and_tamper_checked(self):
        from pathlib import Path
        with patch.object(budget, "CERTIFICATES", Path("registry/context_budget_routes.json")):
            budget._load_certificates.cache_clear()
            profiles = budget._load_certificates()
            self.assertEqual(profiles[MODEL]["accuracy"], "calibrated")
            self.assertEqual(profiles["deepseek/deepseek-v4-flash"]["framingUpperCorrection"], 128)
        budget._load_certificates.cache_clear()

    def test_valid_length_part_stops_automatic_replacement(self):
        self.assertTrue(budget.readable_managed_part('{"mode":"interpretation","paragraphs":[{"text":"{{s1}}. Возможная тема.","refs":["s1"]}]}', True))
        self.assertFalse(budget.readable_managed_part('{"mode":"interpretation","paragraphs":[', True))
        self.assertFalse(budget.readable_managed_part('', True))
        self.assertTrue(budget.readable_managed_part('Законченная часть ответа.', False))


if __name__ == "__main__":
    unittest.main()
