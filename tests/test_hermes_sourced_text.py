import unittest
from hermes_sourced_text import sourced_text_mode, strict_context_execution_mode


class SourcedTextTests(unittest.TestCase):
    def body(self):
        return {"tp_execution_mode": "sourced_text_v1", "messages": [
            {"role": "system", "content": "Return sourced JSON"},
            {"role": "user", "content": "Article text"},
        ]}

    def test_regular_requests_are_unchanged(self):
        self.assertFalse(sourced_text_mode({"messages": []}))

    def test_accepts_bounded_plain_text(self):
        self.assertTrue(sourced_text_mode(self.body()))
        body = self.body()
        body["messages"][1]["content"] = "x" * 160000
        self.assertTrue(sourced_text_mode(body))

    def test_rejects_tools_stream_history_and_oversized_content(self):
        for change in ({"tools": [{"name": "terminal"}]}, {"stream": True},
                       {"tool_choice": "auto"}, {"tp_reading_context": {}},
                       {"tp_execution_mode": "unknown"},
                       {"messages": [{"role": "user", "content": "x"}]},
                       {"messages": [{"role": "system", "content": "x"},
                                     {"role": "user", "content": "x" * 200000}]}):
            with self.subTest(change=tuple(change)):
                with self.assertRaises(ValueError):
                    sourced_text_mode({**self.body(), **change})


class InterpretationFactsModeTests(unittest.TestCase):
    def body(self):
        return {"tp_execution_mode": "interpretation_facts_v1", "tools": [], "tool_choice": "none",
                "messages": [{"role": "system", "content": "Use verified facts"},
                             {"role": "user", "content": "Supplied facts"}]}

    def test_separate_mode_without_extraction_override(self):
        self.assertEqual(strict_context_execution_mode(self.body()), "interpretation_facts_v1")
        self.assertFalse(sourced_text_mode(self.body()))

    def test_rejects_shared_tools_stream_reading_or_history(self):
        for change in ({"tools": [{"name": "memory"}]}, {"tool_choice": "auto"}, {"stream": True},
                       {"tp_reading_context": {}},
                       {"messages": [*self.body()["messages"], {"role": "assistant", "content": "old"}]}):
            with self.subTest(change=tuple(change)), self.assertRaises(ValueError):
                strict_context_execution_mode({**self.body(), **change})

    def test_bound_uses_utf8_bytes(self):
        body = self.body()
        body["messages"][1]["content"] = "я" * 65536
        with self.assertRaisesRegex(ValueError, "128 KiB"):
            strict_context_execution_mode(body)
        body["messages"][1]["content"] = "я" * 30000
        self.assertEqual(strict_context_execution_mode(body), "interpretation_facts_v1")
