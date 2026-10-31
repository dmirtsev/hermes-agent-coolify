import copy
import hashlib
import json
import unittest
from hermes_analysis_context import (
    SakoianContextError, canonical_hash, prepare_sakoian_analysis_context,
)


def fixture():
    analysis = {
        "schema_version": "tp-astro-analysis.result.v1", "analysis_id": "analysis_a",
        "status": "complete", "method_ref": {"method_id": "sakoian_transit_analysis", "version": "0.3.0", "package_hash": "a"*64,
        "knowledge_ref": {"environment": "test", "content_hash": "b"*64}},
        "facts": [], "primary_factors": [{"order": i, "status": "known"} for i in range(1,6)],
        "derivations": [], "warnings": [], "coverage": {"completed_steps": [1,2,3,4,5]},
    }
    source={"producer":"core_astrogeo", "core_commit":"c"*40, "request_hash":"d"*64, "response_hash":"e"*64, "moment_utc":"2026-01-01T00:00:00Z"}
    return {"schema_version":"tp-astro-analysis.hermes-context.v1", "request_id":"r1", "conversation_ref":"conv1", "access_scope_ref":"scope1", "subject_ref":"subject1", "chart_ref":"chart1", "analysis":analysis, "analysis_hash":canonical_hash(analysis), "source":source}


class SakoianContextTests(unittest.TestCase):
    def test_valid_context_receipt_and_prompt(self):
        guard=prepare_sakoian_analysis_context(fixture(), expected_request_id="r1")
        self.assertIn("TP_ASTRO_ANALYSIS_SAKOIAN_CONTEXT_V1", guard.prompt)
        self.assertFalse(guard.receipt["external_tools_used"])
        self.assertEqual(guard.isolated_session_id, "tp-sakoian-r1")

    def test_rejects_hash_and_wrong_method(self):
        bad=fixture();bad["analysis_hash"]="f"*64
        with self.assertRaises(SakoianContextError): prepare_sakoian_analysis_context(bad)
        bad=fixture();bad["analysis"]["method_ref"]["method_id"]="period_guidance";bad["analysis_hash"]=canonical_hash(bad["analysis"])
        with self.assertRaises(SakoianContextError): prepare_sakoian_analysis_context(bad)

    def test_rejects_shared_period_context_and_wrong_source(self):
        bad=fixture();bad["source"]["producer"]="synthetic_fixture"
        with self.assertRaises(SakoianContextError): prepare_sakoian_analysis_context(bad)
        with self.assertRaises(SakoianContextError): prepare_sakoian_analysis_context(fixture(), expected_request_id="other")

    def test_rejects_malformed_primary_factors_and_oversize(self):
        bad=fixture();bad["analysis"]["primary_factors"]=[];bad["analysis_hash"]=canonical_hash(bad["analysis"])
        with self.assertRaises(SakoianContextError): prepare_sakoian_analysis_context(bad)
        bad=fixture();bad["padding"]="x"*180001
        with self.assertRaises(SakoianContextError): prepare_sakoian_analysis_context(bad)


if __name__ == "__main__": unittest.main()
