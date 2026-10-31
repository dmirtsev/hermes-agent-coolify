import json
import tempfile
import unittest
from pathlib import Path

import hermes_analysis_context as module
from hermes_analysis_context import SakoianContextError, canonical_hash, prepare_sakoian_analysis_context

ROOT = Path(__file__).parents[1]
REAL = Path(__file__).parent / "fixtures" / "sakoian-context.json"

def fixture():
    value = json.loads(REAL.read_text(encoding="utf-8"))
    analysis = value["analysis"]
    return {"schema_version":"tp-astro-analysis.hermes-context.v1",
            "request_id":analysis["request_id"], "conversation_ref":"test-conversation-core",
            "access_scope_ref":analysis["access_scope_ref"], "subject_ref":analysis["subject_ref"],
            "chart_ref":analysis["chart_ref"], "analysis":analysis,
            "analysis_hash":canonical_hash(analysis), "source":value["source"]}

def install_test_manifest():
    value = fixture(); analysis = value["analysis"]
    handle = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump({"executor_package_hash":analysis["method_ref"]["package_hash"],
               "knowledge_ref":analysis["method_ref"]["knowledge_ref"],
               "rule_refs":[d["rule_ref"] for d in analysis["derivations"]],
               "core_commits":[value["source"]["core_commit"]]}, handle)
    handle.close()
    module.MANIFEST_PATH = Path(handle.name)
    import sys
    if "agent.sakoian_analysis_context" in sys.modules:
        sys.modules["agent.sakoian_analysis_context"].MANIFEST_PATH = Path(handle.name)
    return Path(handle.name)

class SakoianContextTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = install_test_manifest()

    @classmethod
    def tearDownClass(cls): cls.manifest.unlink(missing_ok=True)

    def test_real_core_result_has_five_groups_and_receipt(self):
        value=fixture(); guard=prepare_sakoian_analysis_context(value, expected_request_id=value["request_id"])
        self.assertEqual([x["order"] for x in value["analysis"]["primary_factors"]], [1,2,3,4,5])
        self.assertEqual(value["analysis"]["coverage"]["completed_steps"], [1,2,3,4,5])
        self.assertEqual(value["analysis"]["natal_pair"]["status"], "present")
        self.assertFalse(guard.receipt["external_tools_used"]); self.assertFalse(guard.receipt["shared_memory_used"])

    def test_unknown_and_not_applicable_are_distinct(self):
        value=fixture(); a=value["analysis"]; a["primary_factors"][4]["status"]="unknown"
        a["coverage"]={"profile":"primary","completed_steps":[1,2,3,4],"omitted_steps":[5]}; a["status"]="partial"; value["analysis_hash"]=canonical_hash(a)
        prepare_sakoian_analysis_context(value, expected_request_id=value["request_id"])
        value=fixture(); a=value["analysis"]; a["natal_pair"]={"status":"not_applicable","aspect_types":[]}; a["activation_kind"]="self_return"
        next(f for f in a["facts"] if f["fact_id"] == "natal.pair")["value"] = a["natal_pair"]
        value["analysis_hash"]=canonical_hash(a)
        prepare_sakoian_analysis_context(value, expected_request_id=value["request_id"])

    def test_rejects_identity_hash_and_source_tampering(self):
        value=fixture(); value["analysis_hash"]="f"*64
        with self.assertRaises(SakoianContextError): prepare_sakoian_analysis_context(value, expected_request_id=value["request_id"])
        value=fixture(); value["subject_ref"]="other-subject"
        with self.assertRaises(SakoianContextError): prepare_sakoian_analysis_context(value, expected_request_id=value["request_id"])
        value=fixture(); value["source"]["producer"]="synthetic_fixture"
        with self.assertRaises(SakoianContextError): prepare_sakoian_analysis_context(value, expected_request_id=value["request_id"])

    def test_rejects_malformed_evidence_and_unpinned_release(self):
        value=fixture(); value["analysis"]["facts"]=[]; value["analysis_hash"]=canonical_hash(value["analysis"])
        with self.assertRaises(SakoianContextError): prepare_sakoian_analysis_context(value, expected_request_id=value["request_id"])
        value=fixture(); value["analysis"]["method_ref"]["package_hash"]="a"*64; value["analysis_hash"]=canonical_hash(value["analysis"])
        with self.assertRaises(SakoianContextError): prepare_sakoian_analysis_context(value, expected_request_id=value["request_id"])

if __name__ == "__main__": unittest.main()
