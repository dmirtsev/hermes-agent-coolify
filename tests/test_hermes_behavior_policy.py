import hashlib
import unittest
from types import SimpleNamespace
from hermes_behavior_policy import validate_behavior_policy, install_managed_system_prompt
from hermes_behavior_policy import managed_response_settings


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

    def test_json_admission_is_exact_for_managed_policy(self):
        body = fixture()
        body["response_format"] = {"type": "json_object"}
        self.assertEqual(validate_behavior_policy(body, "request-1"), body["tp_hermes_policy"])
        for value in (None, "json_object", {}, {"type": "text"},
                      {"type": "json_object", "schema": {}}):
            with self.subTest(value=value):
                body["response_format"] = value
                with self.assertRaisesRegex(ValueError, "invalid_response_format"):
                    validate_behavior_policy(body, "request-1")

    def test_legacy_planner_json_does_not_enter_managed_policy(self):
        for mode in ("sourced_text_v1", None):
            body = {"model": "hermes-agent", "response_format": {"type": "json_object"},
                    "messages": [{"role": "system", "content": "Return a planner JSON"},
                                 {"role": "user", "content": "Current workspace"}]}
            if mode is not None:
                body["tp_execution_mode"] = mode
            self.assertIsNone(validate_behavior_policy(body, "planner-request"))
        body["tp_answer_format"] = "hermes_managed_v1"
        with self.assertRaisesRegex(ValueError, "hermes_policy_required"):
            validate_behavior_policy(body, "planner-request")

    def test_format_is_request_local_and_preserves_production_route(self):
        shared = {"provider": "openrouter", "base_url": "https://openrouter.ai/api/v1",
                  "credential_pool": object(), "request_overrides": {"extra_body": {"service_tier": "default"}}}
        before = dict(shared["request_overrides"])
        current = managed_response_settings(shared, {"type": "json_object"})
        self.assertEqual(current["request_overrides"]["response_format"], {"type": "json_object"})
        self.assertEqual(current["request_overrides"]["extra_body"], {"service_tier": "default"})
        self.assertEqual(shared["request_overrides"], before)
        self.assertIs(current["credential_pool"], shared["credential_pool"])
        self.assertNotIn("response_format", shared["request_overrides"])
        self.assertIs(managed_response_settings(shared, None), shared)


if __name__ == "__main__":
    unittest.main()
