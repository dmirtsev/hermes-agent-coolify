import json
import hashlib
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from pathlib import Path

import hermes_analysis_context as module


def catalogue():
    """Build the exact catalogue shape from the shared aggregate fixture."""
    from grounded_narrative import build_catalogue
    fixture = (Path(__file__).parent / "fixtures" /
               "sakoian-aggregate-context.json")
    value = json.loads(fixture.read_text())
    return build_catalogue(value["aggregate"]["analyses"],
                           value["aggregate"]["projection"])


class GroundedPlanTests(unittest.TestCase):
    def test_default_plan_is_a_complete_catalogue_permutation(self):
        from grounded_narrative import default_plan

        value = catalogue()
        plan = default_plan(value)
        self.assertEqual(plan["schema_version"],
                         "tp-astro-analysis.narrative-plan.v1")
        self.assertEqual(plan["catalogue_hash"], value["catalogue_hash"])
        self.assertEqual(plan["group_order"],
                         [x["group_id"] for x in value["groups"]])
        self.assertEqual(plan["event_order"],
                         [x["event_ref"] for x in value["events"]])

    def test_parser_rejects_unknown_duplicate_omitted_and_invented_text(self):
        from grounded_narrative import parse_plan

        value = catalogue()
        valid = {
            "schema_version": "tp-astro-analysis.narrative-plan.v1",
            "catalogue_hash": value["catalogue_hash"],
            "group_order": [g["group_id"] for g in value["groups"]],
            "event_order": [e["event_ref"] for e in value["events"]],
        }
        self.assertEqual(parse_plan(json.dumps(valid), value), valid)
        cases = [
            {**valid, "event_order": [valid["event_order"][0]] * 2 + valid["event_order"][2:]},
            {**valid, "event_order": valid["event_order"][:-1]},
            {**valid, "event_order": valid["event_order"][:-1] + ["TX"]},
            {**valid, "group_order": valid["group_order"] + ["GX"]},
        ]
        for bad in cases:
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    parse_plan(json.dumps(bad), value)
        with self.assertRaises(ValueError):
            parse_plan(json.dumps({**valid, "summary": "Юпитер доминирует"}), value)

    def test_render_is_deterministic_and_hashable(self):
        from grounded_narrative import default_plan, render_plan

        value = catalogue()
        plan = default_plan(value)
        rendered = render_plan(plan, value)
        self.assertIsInstance(rendered, str)
        self.assertTrue(rendered.strip())
        self.assertEqual(rendered, render_plan(plan, value))
        self.assertEqual(hashlib.sha256(rendered.encode()).hexdigest(),
                         hashlib.sha256(rendered.encode("utf-8")).hexdigest())

    def test_narrate_accepts_only_a_valid_grounded_plan(self):
        from grounded_narrative import default_plan

        value = catalogue()
        plan = default_plan(value)
        client = Mock()
        client.with_options.return_value = client
        client.chat.completions.create.return_value = SimpleNamespace(
            usage=SimpleNamespace(prompt_tokens=4, completion_tokens=3,
                                  total_tokens=7),
            choices=[SimpleNamespace(
                finish_reason="stop",
                message=SimpleNamespace(content=json.dumps(plan), tool_calls=None),
            )],
        )
        agent = SimpleNamespace(
            provider="openrouter", model="deepseek/deepseek-v4-flash",
            _ensure_primary_openai_client=Mock(return_value=client),
        )
        import sys
        accounting = ("agent.openrouter_accounting" if
                      "agent.openrouter_accounting" in sys.modules else
                      "hermes_openrouter_accounting")
        with patch(accounting + ".record_openrouter_response"):
            result = module.narrate_sakoian_once(
                agent, "question", "closed facts", catalogue=value)
        self.assertTrue(result["completed"])
        self.assertEqual(result["api_calls"], 1)
        self.assertIn("tp_analysis_validation", result)
        self.assertEqual(result["tp_analysis_validation"]["status"], "passed")
        self.assertTrue(result["final_response"])
        client.chat.completions.create.assert_called_once()

    def test_narrate_rejects_wrong_plan_without_leaking_model_text(self):
        value = catalogue()
        wrong = {
            "schema_version": "tp-astro-analysis.narrative-plan.v1",
            "catalogue_hash": value["catalogue_hash"],
            "group_order": [g["group_id"] for g in value["groups"]],
            "event_order": ["T1", "T1", "T3"],
        }
        client = Mock()
        client.with_options.return_value = client
        client.chat.completions.create.return_value = SimpleNamespace(
            usage=SimpleNamespace(prompt_tokens=4, completion_tokens=9,
                                  total_tokens=13),
            choices=[SimpleNamespace(
                finish_reason="stop",
                message=SimpleNamespace(
                    content=json.dumps(wrong), tool_calls=None),
            )],
        )
        agent = SimpleNamespace(
            provider="openrouter", model="deepseek/deepseek-v4-flash",
            _ensure_primary_openai_client=Mock(return_value=client),
        )
        import sys
        accounting = ("agent.openrouter_accounting" if
                      "agent.openrouter_accounting" in sys.modules else
                      "hermes_openrouter_accounting")
        with patch(accounting + ".record_openrouter_response"):
            result = module.narrate_sakoian_once(
                agent, "question", "closed facts", catalogue=value)
        self.assertFalse(result["completed"])
        self.assertTrue(result["failed"])
        self.assertEqual(result["final_response"], "")
        self.assertEqual(result["messages"], [])
        self.assertEqual(result["error"], "sakoian_narration_evidence_mismatch")
        self.assertEqual(result["tp_analysis_validation"]["status"], "rejected")
        self.assertEqual(client.chat.completions.create.call_count, 1)


if __name__ == "__main__":
    unittest.main()
