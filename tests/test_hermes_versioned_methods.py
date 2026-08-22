import copy
import hashlib
import unittest

from hermes_versioned_methods import (
    VersionedMethodContextError,
    prepare_versioned_method_context,
)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def fixture(family_id="tp.transit.period_guidance"):
    method_ref = {
        "family_id": family_id,
        "version": "1.0.0",
        "content_hash": digest(family_id),
    }
    return {
        "schema_version": 1,
        "request_id": "hermes-request-1",
        "domain": "transit",
        "task_type": "period_guidance",
        "profile": {
            "revision": 2,
            "mixing_policy": "forbidden",
            "presentation": {"show_method": True, "show_sources": True},
        },
        "resolution": {"resolved": method_ref},
        "calculation": {
            "calculationContract": "core.transit_bands",
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
                "method": {
                    "steps": [{"id": "read", "position": 1, "instruction": "Read"}],
                    "rule_refs": ["rule-1"],
                    "chunk_collections": ["tp-transit-period-guidance-v1"],
                    "synthesis_policy": "single_method_only",
                },
            },
            "knowledge_base_slugs": ["tp-transit-period-guidance-v1"],
            "rules": [{"stable_ref": "rule-1", "rule_text": "Only this rule"}],
            "sources": [],
            "context_text": "Scoped context",
        },
    }


class VersionedMethodGuardTests(unittest.TestCase):
    def test_builds_exact_receipt_and_closed_prompt(self):
        guard = prepare_versioned_method_context(fixture())
        self.assertEqual(guard.receipt["family_id"], "tp.transit.period_guidance")
        self.assertEqual(guard.receipt["retrieval_trace_id"], "trace-1")
        self.assertIn("Не вызывай MCP/RAG", guard.prompt)
        self.assertIn("Only this rule", guard.prompt)

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
