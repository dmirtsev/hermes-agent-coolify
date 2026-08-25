import copy
import hashlib
import unittest

from hermes_versioned_methods import (
    VersionedMethodContextError,
    prepare_versioned_method_context,
    strict_context_active,
    strict_context_scope,
)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def fixture(
    family_id="tp.transit.period_guidance",
    *,
    domain="transit",
    task_type="period_guidance",
    calculation_contract="core.transit_bands",
    memory_scope="conversation.transit",
    knowledge_base_slug="tp-transit-period-guidance-v1",
):
    method_ref = {
        "family_id": family_id,
        "version": "1.0.0",
        "content_hash": digest(family_id),
    }
    return {
        "schema_version": 1,
        "request_id": "hermes-request-1",
        "conversation_id": "conversation-1",
        "domain": domain,
        "task_type": task_type,
        "profile": {
            "revision": 2,
            "mixing_policy": "forbidden",
            "presentation": {
                "detail_level": "standard",
                "show_method": True,
                "show_sources": True,
            },
        },
        "resolution": {"resolved": method_ref},
        "calculation": {
            "calculationContract": calculation_contract,
            "contractVersion": "1.2.0",
            "factRefs": ["transit-band-1"],
            "facts": [{"fact_id": "transit-band-1", "aspect": "square"}],
        },
        "knowledge": {
            "resolved_method": method_ref,
            "retrieval_trace_id": "trace-1",
            "method_version": {
                **method_ref,
                "status": "published",
                "author": {"id": "tp.editorial"},
                "school": None,
                "provenance": {
                    "sources": [
                        {
                            "source_id": "tp.transit.editorial_core",
                            "source_version": "2026.08",
                            "locator": "rules/versioned-transit-mvp",
                            "rights_status": "owned",
                        }
                    ]
                },
                "method": {
                    "steps": [{"id": "read", "position": 1, "instruction": "Read"}],
                    "rule_refs": ["rule-1"],
                    "chunk_collections": [knowledge_base_slug],
                    "synthesis_policy": "single_method_only",
                },
            },
            "knowledge_base_slugs": [knowledge_base_slug],
            "rules": [{"stable_ref": "rule-1", "rule_text": "Only this rule"}],
            "sources": [],
            "context_text": "Scoped context",
        },
        "memory_context": {
            "schema_version": 1,
            "scope": memory_scope,
            "conversation_id": "conversation-1",
            "selection_policy": "recent_non_mock_same_context",
            "items": [
                {
                    "memory_item_ref": "message-1",
                    "role": "user",
                    "content": "Хочу продолжить наблюдение этой темы.",
                    "created_at": "2026-08-20T10:00:00Z",
                }
            ],
        },
    }


def natal_fixture():
    value = fixture(
        "author.natal.avessalom_podvodny",
        domain="natal",
        task_type="general_reading",
        calculation_contract="core.natal_chart",
        memory_scope="conversation.natal",
        knowledge_base_slug="tp-natal-planets-avessalom-podvodny-v1",
    )
    value["calculation"] = {
        "calculationContract": "core.natal_chart",
        "contractVersion": "1.0.0",
        "factRefs": ["natal-sun-1"],
        "facts": [{"fact_id": "natal-sun-1", "object": "Sun", "sign": "Aries"}],
    }
    value["knowledge"]["method_version"]["method"]["rule_refs"] = []
    value["knowledge"]["rules"] = []
    value["knowledge"]["method_version"]["author"] = {
        "id": "avessalom-podvodny",
        "display_name": "Авессалом Подводный",
    }
    value["knowledge"]["method_version"]["provenance"] = {
        "sources": [
            {
                "source_id": "material:1",
                "source_version": "1",
                "locator": "tp-natal-planets-avessalom-podvodny-v1",
                "rights_status": "restricted_user_supplied",
            }
        ]
    }
    return value


class VersionedMethodGuardTests(unittest.TestCase):
    def test_strict_context_scope_is_local_and_resets(self):
        self.assertFalse(strict_context_active())
        with strict_context_scope(True):
            self.assertTrue(strict_context_active())
        self.assertFalse(strict_context_active())

    def test_builds_exact_receipt_and_closed_prompt(self):
        guard = prepare_versioned_method_context(fixture())
        self.assertEqual(guard.receipt["family_id"], "tp.transit.period_guidance")
        self.assertEqual(guard.receipt["retrieval_trace_id"], "trace-1")
        self.assertEqual(guard.isolated_session_id, "tp-reading-hermes-request-1")
        self.assertEqual(guard.receipt["context_isolation"], "strict_v1")
        self.assertEqual(guard.receipt["prompt_contract_version"], "1.2.0")
        self.assertFalse(guard.receipt["shared_memory_used"])
        self.assertFalse(guard.receipt["external_tools_used"])
        self.assertEqual(
            guard.receipt["authorized_memory_item_refs"], ["message-1"]
        )
        self.assertIn("Не вызывай MCP/RAG", guard.prompt)
        self.assertIn("Only this rule", guard.prompt)
        self.assertIn("Методика: tp.transit.period_guidance@1.0.0", guard.prompt)
        self.assertIn("source_id", guard.prompt)
        self.assertIn("authorized_interaction_memory", guard.prompt)

    def test_accepts_restricted_user_supplied_provenance(self):
        guard = prepare_versioned_method_context(natal_fixture())

        self.assertEqual(
            guard.receipt["family_id"], "author.natal.avessalom_podvodny"
        )
        self.assertIn("restricted_user_supplied", guard.prompt)

    def test_rejects_memory_outside_the_bounded_conversation_scope(self):
        value = fixture()
        value["memory_context"]["scope"] = "conversation.other"
        with self.assertRaisesRegex(VersionedMethodContextError, "scope"):
            prepare_versioned_method_context(value)

        value = fixture()
        value["memory_context"]["items"] = value["memory_context"]["items"] * 7
        with self.assertRaisesRegex(VersionedMethodContextError, "item limit"):
            prepare_versioned_method_context(value)

        value = fixture()
        value["memory_context"]["conversation_id"] = "conversation-2"
        with self.assertRaisesRegex(VersionedMethodContextError, "differs"):
            prepare_versioned_method_context(value)

    def test_presentation_switches_are_enforced_in_closed_prompt(self):
        value = fixture()
        value["profile"]["presentation"]["show_method"] = False
        value["profile"]["presentation"]["show_sources"] = False
        guard = prepare_versioned_method_context(value)
        self.assertIn("Не показывай пользователю техническое имя", guard.prompt)
        self.assertIn("Не выводи пользователю список источников", guard.prompt)

    def test_accepts_natal_method_with_isolated_natal_memory(self):
        guard = prepare_versioned_method_context(natal_fixture())
        self.assertEqual(
            guard.receipt["family_id"], "author.natal.avessalom_podvodny"
        )
        self.assertEqual(guard.receipt["authorized_memory_scope"], "conversation.natal")
        self.assertIn("core.natal_chart", guard.prompt)
        self.assertIn("Авессалом Подводный", guard.prompt)

    def test_rejects_cross_domain_memory_and_calculation_contract(self):
        value = natal_fixture()
        value["memory_context"]["scope"] = "conversation.transit"
        with self.assertRaisesRegex(VersionedMethodContextError, "scope"):
            prepare_versioned_method_context(value)

        value = natal_fixture()
        value["calculation"]["calculationContract"] = "core.transit_bands"
        with self.assertRaisesRegex(VersionedMethodContextError, "calculation contract"):
            prepare_versioned_method_context(value)

    def test_rejects_published_method_without_source_provenance(self):
        value = fixture()
        value["knowledge"]["method_version"]["provenance"] = {"sources": []}
        with self.assertRaisesRegex(
            VersionedMethodContextError,
            "published method provenance sources are required",
        ):
            prepare_versioned_method_context(value)

    def test_rejects_cross_method_retrieval(self):
        value = fixture()
        value["knowledge"]["resolved_method"] = {
            "family_id": "tp.transit.reflective_comparison",
            "version": "1.0.0",
            "content_hash": digest("other"),
        }
        with self.assertRaises(VersionedMethodContextError):
            prepare_versioned_method_context(value)

    def test_rejects_rule_leakage(self):
        value = fixture()
        value["knowledge"]["rules"].append(
            {"stable_ref": "foreign-rule", "rule_text": "Foreign"}
        )
        with self.assertRaises(VersionedMethodContextError):
            prepare_versioned_method_context(value)

    def test_two_method_fixtures_produce_distinct_receipts(self):
        primary = prepare_versioned_method_context(fixture())
        comparison_value = fixture("tp.transit.reflective_comparison")
        comparison_value["knowledge"]["method_version"]["method"]["chunk_collections"] = [
            "tp-transit-reflective-demo-v1"
        ]
        comparison_value["knowledge"]["knowledge_base_slugs"] = [
            "tp-transit-reflective-demo-v1"
        ]
        comparison = prepare_versioned_method_context(comparison_value)
        self.assertNotEqual(primary.receipt["family_id"], comparison.receipt["family_id"])
        self.assertNotEqual(primary.receipt["content_hash"], comparison.receipt["content_hash"])

    def test_rejects_unreviewed_synthesis(self):
        value = copy.deepcopy(fixture())
        value["profile"]["mixing_policy"] = "allowlisted_synthesis"
        with self.assertRaises(VersionedMethodContextError):
            prepare_versioned_method_context(value)


if __name__ == "__main__":
    unittest.main()
