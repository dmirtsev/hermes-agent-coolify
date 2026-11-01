import copy
import os
import unittest
from unittest.mock import patch

import hermes_context_budget as budget


TEST_URL = "https://test.astrogeoagent.ru/api/internal/hermes/attempt-reserve"
PRODUCTION_URL = "https://astrotochka.ru/api/internal/hermes/attempt-reserve"


class ProductionCallbackTests(unittest.TestCase):
    def body(self, url):
        return {"tp_request_id": "synthetic-operation", "tp_attempt_reserve": {
            "contract": budget.ATTEMPT_RESERVE_CONTRACT,
            "requestId": "synthetic-operation", "token": "a" * 64,
            "callbackUrl": url, "initialAdmission": {},
        }}

    def test_descriptor_uses_only_its_own_deployment(self):
        for deployment, own, other in (("test", TEST_URL, PRODUCTION_URL),
                                       ("production", PRODUCTION_URL, TEST_URL)):
            with self.subTest(deployment=deployment), patch.dict(os.environ, {
                "DEPLOYMENT_ENVIRONMENT": deployment,
                "HERMES_ATTEMPT_RESERVE_CALLBACK_URL": own,
            }, clear=True):
                self.assertEqual(budget._attempt_descriptor(self.body(own))["callbackUrl"], own)
                with self.assertRaisesRegex(budget.ContextBudgetError, "callback_invalid"):
                    budget._attempt_descriptor(self.body(other))

    def test_copied_test_configuration_fails_closed_in_production(self):
        with patch.dict(os.environ, {
            "DEPLOYMENT_ENVIRONMENT": "production",
            "HERMES_ATTEMPT_RESERVE_CALLBACK_URL": TEST_URL,
        }, clear=True):
            with self.assertRaisesRegex(budget.ContextBudgetError, "callback_invalid"):
                budget._attempt_descriptor(self.body(PRODUCTION_URL))

    def test_unknown_deployment_cannot_send_callback(self):
        for deployment in (None, "staging", "PRODUCTION", "production "):
            env = {} if deployment is None else {"DEPLOYMENT_ENVIRONMENT": deployment}
            with patch.dict(os.environ, env, clear=True):
                with self.assertRaisesRegex(budget.ContextBudgetError, "environment_invalid"):
                    budget._attempt_descriptor(self.body(PRODUCTION_URL))
                # Historical requests without measured control remain valid.
                self.assertIsNone(budget._attempt_descriptor({}))

    def test_callback_is_not_a_user_controlled_url(self):
        urls = ["http://astrotochka.ru/api/internal/hermes/attempt-reserve",
                "https://astrotochka.ru.evil.invalid/api/internal/hermes/attempt-reserve",
                "https://user@astrotochka.ru/api/internal/hermes/attempt-reserve",
                PRODUCTION_URL + "?target=other", PRODUCTION_URL + "#fragment",
                PRODUCTION_URL + "/", "http://127.0.0.1/internal", "file:///etc/passwd"]
        with patch.dict(os.environ, {"DEPLOYMENT_ENVIRONMENT": "production"}, clear=True):
            for url in urls:
                with self.subTest(url=url), self.assertRaisesRegex(budget.ContextBudgetError, "callback_invalid"):
                    budget._attempt_descriptor(self.body(url))

    def test_descriptor_is_not_rewritten_and_historical_price_is_retained(self):
        body = self.body(PRODUCTION_URL)
        before = copy.deepcopy(body)
        with patch.dict(os.environ, {"DEPLOYMENT_ENVIRONMENT": "production"}, clear=True):
            actual = budget._attempt_descriptor(body)
        self.assertEqual(body, before)
        self.assertNotIn("pricePolicyVersion", actual)
