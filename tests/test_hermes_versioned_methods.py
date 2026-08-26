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


def retrieval_v2_fixture(*, status="partial", with_chunks=True):
    value = natal_fixture()
    value["schema_version"] = 2
    knowledge = value["knowledge"]
    knowledge.update(
        {
            "schema_version": 2,
            "contract": "knowledge_retrieval_response_v2",
            "status": status,
            "index_generation": "ctx-rag-20260825-2",
            "warnings": ["reranker_timeout"] if status == "partial" else [],
            "coverage": [
                {
                    "subquery_id": "sun.sign",
                    "required": True,
                    "covered": with_chunks,
                    "chunk_ids": [285] if with_chunks else [],
                }
            ],
            "chunks": (
                [
                    {
                        "chunk_id": 285,
                        "title": "Солнце в Деве",
                        "text": "Экспертный текст о проявлениях Солнца.",
                        "matched_subquery_ids": ["sun.sign"],
                        "citation": {
                            "citation_id": "material:1:chunk:285",
                            "source_locator": "material/1#chunk-285",
                        },
                    }
                ]
                if with_chunks
                else []
            ),
        }
    )
    knowledge["sources"] = (
        [
            {
                "chunk_id": 285,
                "citation": {
                    "citation_id": "material:1:chunk:285",
                    "source_locator": "material/1#chunk-285",
                },
            }
        ]
        if with_chunks
        else []
    )
    return value


class VersionedMethodGuardTests(unittest.TestCase):
    def test_strict_context_scope_is_local_and_resets(self):
        self.assertFalse(strict_context_active())
        with strict_context_scope(True):
            self.assertTrue(strict_context_active())
        self.assertFalse(strict_context_active())

    def test_builds_exact_receipt_and_natural_prompt(self):
        guard = prepare_versioned_method_context(fixture())
        self.assertEqual(guard.receipt["family_id"], "tp.transit.period_guidance")
        self.assertEqual(guard.receipt["retrieval_trace_id"], "trace-1")
        self.assertEqual(guard.isolated_session_id, "tp-reading-hermes-request-1")
        self.assertEqual(guard.receipt["context_isolation"], "strict_v1")
        self.assertEqual(guard.receipt["prompt_contract_version"], "1.4.0")
        self.assertFalse(guard.receipt["shared_memory_used"])
        self.assertFalse(guard.receipt["external_tools_used"])
        self.assertEqual(
            guard.receipt["authorized_memory_item_refs"], ["message-1"]
        )
        self.assertIn("Ответь прямо, естественно и по существу", guard.prompt)
        self.assertIn("Свободно используй знания модели", guard.prompt)
        self.assertIn("единый текст без ссылок", guard.prompt)
        self.assertIn("наиболее гуманный и конструктивный ответ", guard.prompt)
        self.assertIn("гуманная рамка важнее его стиля", guard.prompt)
        self.assertIn("не объявляй трудности неизбежными", guard.prompt)
        self.assertIn("не приписывай человеку дефекты характера", guard.prompt)
        self.assertIn("Не показывай пользователю источники", guard.prompt)
        self.assertNotIn("Структура ответа обязательна", guard.prompt)
        self.assertNotIn("Не вызывай MCP/RAG", guard.prompt)
        self.assertIn("Only this rule", guard.prompt)
        self.assertIn("relevant_dialog_context", guard.prompt)
        self.assertNotIn("source_id", guard.prompt)
        self.assertNotIn("authorized_interaction_memory", guard.prompt)
        self.assertNotIn("fact_id", guard.prompt)
        self.assertNotIn(guard.receipt["content_hash"], guard.prompt)

    def test_accepts_restricted_user_supplied_provenance(self):
        guard = prepare_versioned_method_context(natal_fixture())

        self.assertEqual(
            guard.receipt["family_id"], "author.natal.avessalom_podvodny"
        )
        self.assertNotIn("restricted_user_supplied", guard.prompt)

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

    def test_presentation_switches_remain_data_without_forcing_output_sections(self):
        value = fixture()
        value["profile"]["presentation"]["show_method"] = False
        value["profile"]["presentation"]["show_sources"] = False
        guard = prepare_versioned_method_context(value)
        self.assertNotIn('"show_method"', guard.prompt)
        self.assertNotIn('"show_sources"', guard.prompt)
        self.assertIn('"detail_level": "standard"', guard.prompt)

    def test_accepts_natal_method_with_isolated_natal_memory(self):
        guard = prepare_versioned_method_context(natal_fixture())
        self.assertEqual(
            guard.receipt["family_id"], "author.natal.avessalom_podvodny"
        )
        self.assertEqual(guard.receipt["authorized_memory_scope"], "conversation.natal")
        self.assertNotIn("core.natal_chart", guard.prompt)
        self.assertNotIn("Авессалом Подводный", guard.prompt)

    def test_accepts_partial_retrieval_v2_with_grounded_citations(self):
        guard = prepare_versioned_method_context(retrieval_v2_fixture())

        self.assertEqual(guard.receipt["prompt_contract_version"], "1.4.0")
        self.assertEqual(guard.receipt["retrieval"]["status"], "partial")
        self.assertEqual(
            guard.receipt["retrieval"]["index_generation"],
            "ctx-rag-20260825-2",
        )
        self.assertTrue(guard.receipt["retrieval"]["grounded"])
        self.assertIn("Экспертный текст о проявлениях Солнца.", guard.prompt)
        self.assertNotIn("material:1:chunk:285", guard.prompt)
        self.assertNotIn("reranker_timeout", guard.prompt)
        self.assertNotIn("retrieval_evidence", guard.prompt)
        self.assertIn("единый текст без ссылок", guard.prompt)
        self.assertNotIn("не придумывай citation_id", guard.prompt)
        self.assertNotIn("ставь рядом их citation_id", guard.prompt)

    def test_accepts_completed_retrieval_v2_without_evidence_for_model_first_answer(self):
        guard = prepare_versioned_method_context(
            retrieval_v2_fixture(status="completed", with_chunks=False)
        )

        self.assertFalse(guard.receipt["retrieval"]["grounded"])
        self.assertIn("если подходящих материалов нет", guard.prompt)
        self.assertNotIn("Retrieval evidence отсутствует", guard.prompt)

    def test_rejects_failed_or_empty_partial_retrieval_v2_before_generation(self):
        with self.assertRaisesRegex(VersionedMethodContextError, "retry retrieval"):
            prepare_versioned_method_context(
                retrieval_v2_fixture(status="failed", with_chunks=False)
            )
        with self.assertRaisesRegex(VersionedMethodContextError, "citation evidence"):
            prepare_versioned_method_context(
                retrieval_v2_fixture(status="partial", with_chunks=False)
            )

    def test_rejects_retrieval_v2_chunk_without_citation(self):
        value = retrieval_v2_fixture()
        value["knowledge"]["chunks"][0]["citation"] = {}

        with self.assertRaisesRegex(VersionedMethodContextError, "citation"):
            prepare_versioned_method_context(value)

    def test_rejects_retrieval_v2_coverage_or_sources_without_matching_chunks(self):
        value = retrieval_v2_fixture()
        value["knowledge"]["coverage"][0]["chunk_ids"] = [999]
        with self.assertRaisesRegex(VersionedMethodContextError, "coverage"):
            prepare_versioned_method_context(value)

        value = retrieval_v2_fixture()
        value["knowledge"]["sources"][0]["citation"]["citation_id"] = "foreign"
        with self.assertRaisesRegex(VersionedMethodContextError, "sources citations"):
            prepare_versioned_method_context(value)

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
