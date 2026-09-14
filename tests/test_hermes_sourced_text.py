import unittest
from hermes_sourced_text import sourced_text_mode


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

    def test_rejects_tools_stream_history_and_oversized_content(self):
        for change in ({"tools": [{"name": "terminal"}]}, {"stream": True},
                       {"tool_choice": "auto"}, {"tp_reading_context": {}},
                       {"tp_execution_mode": "unknown"},
                       {"messages": [{"role": "user", "content": "x"}]},
                       {"messages": [{"role": "system", "content": "x"},
                                     {"role": "user", "content": "x" * 28000}]}):
            with self.subTest(change=tuple(change)):
                with self.assertRaises(ValueError):
                    sourced_text_mode({**self.body(), **change})
