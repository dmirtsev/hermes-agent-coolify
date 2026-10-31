import json
import sys
import unittest
from unittest.mock import patch
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1]))
from hermes_sakoian_rejection import diagnose_plan
from grounded_narrative import build_catalogue, default_plan, parse_plan


def catalogue():
    value = json.loads((Path(__file__).parent / "fixtures" /
                        "sakoian-aggregate-context.json").read_text())
    return build_catalogue(value["aggregate"]["analyses"], value["aggregate"]["projection"])


class RejectionDiagnosticsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.c = catalogue()
        cls.valid = default_plan(cls.c)

    def diag(self, value):
        return diagnose_plan(value if isinstance(value, str) else json.dumps(value), self.c)

    def test_json_and_text_failures_are_specific(self):
        self.assertEqual(self.diag("" )["code"], "empty_text")
        self.assertEqual(diagnose_plan(b"\xff", self.c)["code"], "invalid_text_type")
        self.assertEqual(self.diag("{bad")["code"], "malformed_json")
        self.assertEqual(self.diag('{"a":1,"a":2}') ["code"], "duplicate_json_key")
        self.assertEqual(self.diag("null")["code"], "wrong_top_level_type")
        self.assertEqual(self.diag('{"x":NaN}') ["code"], "nonfinite_json")

    def test_shape_and_permutation_failures(self):
        self.assertEqual(self.diag({})["code"], "missing_field")
        self.assertEqual(self.diag({**self.valid, "summary": "free prose"})["code"], "extra_field")
        self.assertEqual(self.diag({**self.valid, "schema_version": "x"})["code"], "wrong_schema_version")
        self.assertEqual(self.diag({**self.valid, "catalogue_hash": "0" * 64})["code"], "catalogue_hash_mismatch")
        self.assertEqual(self.diag({**self.valid, "group_order": "G1"})["code"], "wrong_order_type")
        bad = {**self.valid, "event_order": list(self.valid["event_order"])}
        bad["event_order"][0] = bad["event_order"][1]
        self.assertEqual(self.diag(bad)["code"], "duplicate_order_id")
        bad = {**self.valid, "event_order": list(self.valid["event_order"])}
        bad["event_order"][0] = "T-UNKNOWN"
        self.assertEqual(self.diag(bad)["code"], "unknown_order_id")

    def test_valid_plan_returns_neutral_fallback_and_does_not_echo_text(self):
        result = self.diag(self.valid)
        self.assertEqual(result["code"], "plan_validation_failed")
        self.assertNotIn("free prose", json.dumps(result))

    def test_large_plan_is_bounded(self):
        result = self.diag("x" * 32_001)
        self.assertEqual(result["code"], "plan_too_large")
        self.assertEqual(result["details"]["actual_bytes"], 32_001)

    def test_diagnosed_inputs_are_rejected_by_unchanged_parser(self):
        cases = [
            ('\ud800', 'invalid_unicode'),
            ('1' * 5000, 'invalid_json_value'),
            ('{"x":Infinity}', 'nonfinite_json'),
            ('{"x":-Infinity}', 'nonfinite_json'),
            ('{"PRIVATE_MODEL_TEXT":0,"PRIVATE_MODEL_TEXT":1}', 'duplicate_json_key'),
            (json.dumps({**self.valid, 'schema_version':'PRIVATE_MODEL_TEXT'}), 'wrong_schema_version'),
            (json.dumps({**self.valid, 'event_order':[1]}), 'wrong_order_element_type'),
            (json.dumps({**self.valid, 'event_order':[]}), 'missing_order_id'),
            (json.dumps({**self.valid, 'event_order':['PRIVATE_MODEL_TEXT']}), 'unknown_order_id'),
        ]
        for text, expected_code in cases:
            with self.subTest(code=expected_code):
                with self.assertRaises((ValueError, RecursionError)):
                    parse_plan(text, self.c)
                reason = diagnose_plan(text, self.c)
                self.assertEqual(reason['code'], expected_code)
                self.assertNotIn('PRIVATE_MODEL_TEXT', reason['code'] + reason['path'])
        with patch('hermes_sakoian_rejection.json.loads', side_effect=RecursionError):
            self.assertEqual(diagnose_plan('[', self.c)['code'], 'json_nesting_limit')
        # CPython parser nesting limits differ by runtime version.
        deep = '[' * 10000
        with self.assertRaises(ValueError):
            parse_plan(deep, self.c)
        self.assertIn(diagnose_plan(deep, self.c)['code'], ('malformed_json', 'json_nesting_limit'))

    def test_json_position_and_all_permutation_defects_are_private_and_precise(self):
        text = '{"я":1,\n BAD}'
        reason = diagnose_plan(text, self.c)
        self.assertEqual(reason['details'], {'line':2, 'column':2, 'position':9})
        reason = self.diag({**self.valid, 'event_order':['T1','T1','PRIVATE_MODEL_TEXT']})
        self.assertEqual(reason['code'], 'duplicate_order_id')
        self.assertEqual(reason['details']['ids'], ['T1'])
        self.assertEqual(reason['details']['unknown_ids'], ['PRIVATE_MODEL_TEXT'])
        self.assertEqual(reason['details']['missing_ids'], self.valid['event_order'][1:])


if __name__ == "__main__":
    unittest.main()
