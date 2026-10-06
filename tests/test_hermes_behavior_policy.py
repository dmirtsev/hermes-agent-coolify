import hashlib
import unittest
from types import SimpleNamespace
from hermes_behavior_policy import validate_behavior_policy, install_managed_system_prompt


def fixture():
    text = "Опубликованная инструкция Гермесу."
    return {"tp_execution_mode": "interpretation_facts_v1", "tp_answer_format": "hermes_managed_v1",
            "messages": [{"role": "system", "content": text}, {"role": "user", "content": "Вопрос"}],
            "tools": [], "tool_choice": "none", "tp_hermes_policy": {
                "version_id": "hermes-v1", "version": 1, "sha256": "a"*64,
                "system_sha256": hashlib.sha256(text.encode()).hexdigest()}}


class BehaviorPolicyTests(unittest.TestCase):
    def test_requires_exact_system_bytes_and_isolated_durable_request(self):
        body = fixture()
        self.assertEqual(validate_behavior_policy(body, "request-1"), body["tp_hermes_policy"])
        body["messages"][0]["content"] += " invisible instruction"
        with self.assertRaisesRegex(ValueError, "hash_mismatch"):
            validate_behavior_policy(body, "request-1")
        for key, value in (("stream", True), ("tools", [{"type": "function"}]),
                           ("tp_reading_context", {})):
            body = fixture()
            body[key] = value
            with self.assertRaises(ValueError):
                validate_behavior_policy(body, "request-1")
        with self.assertRaises(ValueError):
            validate_behavior_policy(fixture(), None)

    def test_request_local_replacement_preserves_other_agents(self):
        one = SimpleNamespace(_cached_system_prompt="CORE-SENTINEL", ephemeral_system_prompt="extra")
        two = SimpleNamespace(_cached_system_prompt="other", ephemeral_system_prompt="other-extra")
        install_managed_system_prompt(one, "Published")
        self.assertEqual(one._cached_system_prompt, "Published")
        self.assertIsNone(one.ephemeral_system_prompt)
        self.assertEqual(two._cached_system_prompt, "other")

    def test_legacy_request_is_unchanged(self):
        self.assertIsNone(validate_behavior_policy({"messages": []}, None))
        with self.assertRaises(ValueError):
            validate_behavior_policy({"tp_answer_format": "hermes_managed_v1"}, "request-1")


if __name__ == "__main__":
    unittest.main()
