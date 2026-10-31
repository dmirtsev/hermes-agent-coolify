import unittest

from scripts.validate_v41_calibration import validate


class V41CalibrationValidationTests(unittest.TestCase):
    def test_valid_shape_is_only_a_reviewable_candidate(self):
        document = {
            "model": "deepseek/deepseek-v4.1-flash",
            "syntheticOnly": True,
            "contextWindow": 142592,
            "cases": [{
                "model": "deepseek/deepseek-v4.1-flash",
                "provider": "example",
                "referenceTokens": 100,
                "providerInputTokens": 101,
                "admissionUpperTokens": 110,
                "messagesSha256": "a" * 64,
                "generationId": "synthetic-id",
            }],
        }
        self.assertEqual(validate(document), [])

    def test_missing_window_and_evidence_fail(self):
        self.assertTrue(validate({"model": "deepseek/deepseek-v4.1-flash"}))


if __name__ == "__main__":
    unittest.main()
