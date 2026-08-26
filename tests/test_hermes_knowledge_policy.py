import unittest

from hermes_knowledge_policy import (
    KNOWLEDGE_AUGMENTED,
    MODEL_ONLY,
    KnowledgePolicyError,
    prepare_knowledge_policy,
)


class HermesKnowledgePolicyTests(unittest.TestCase):
    def test_absent_policy_preserves_legacy_behavior(self):
        self.assertIsNone(prepare_knowledge_policy(None))

    def test_model_only_disables_external_tools(self):
        guard = prepare_knowledge_policy(
            {"schema_version": 1, "mode": MODEL_ONLY}
        )
        self.assertTrue(guard.tools_disabled)
        self.assertEqual(guard.receipt["mode"], MODEL_ONLY)
        self.assertFalse(guard.receipt["external_knowledge_tools_used"])

    def test_augmented_mode_keeps_tool_path_available(self):
        guard = prepare_knowledge_policy(
            {"schema_version": 1, "mode": KNOWLEDGE_AUGMENTED}
        )
        self.assertFalse(guard.tools_disabled)
        self.assertIsNone(guard.receipt["external_knowledge_tools_used"])

    def test_unknown_or_unversioned_policy_fails_closed(self):
        for value in (
            {"mode": MODEL_ONLY},
            {"schema_version": 1, "mode": "automatic"},
            MODEL_ONLY,
        ):
            with self.subTest(value=value):
                with self.assertRaises(KnowledgePolicyError):
                    prepare_knowledge_policy(value)


if __name__ == "__main__":
    unittest.main()
