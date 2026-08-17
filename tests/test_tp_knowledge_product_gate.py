import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class TpKnowledgeProductGateTests(unittest.TestCase):
    def test_runtime_registers_dedicated_product_tool(self):
        setup = (ROOT / "tp_knowledge_mcp_setup.sh").read_text(encoding="utf-8")
        self.assertGreaterEqual(setup.count("product_knowledge_answer_context"), 2)

    def test_routing_policy_fails_closed_for_product_recommendations(self):
        policy = (ROOT / "docs" / "HERMES_SOURCE_ROUTING_POLICY.md").read_text(encoding="utf-8")
        self.assertIn("hermes_product_gate.state is hermes_recommendable", policy)
        self.assertIn("do not present the function as available", policy)


if __name__ == "__main__":
    unittest.main()
