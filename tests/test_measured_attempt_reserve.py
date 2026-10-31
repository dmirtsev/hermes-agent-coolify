import copy
import io
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import hermes_context_budget as budget
import hermes_durable_accounting as durable


def admission(messages):
    return {'contract': budget.CONTRACT, 'model': 'openai/gpt-6-luna',
            'route': 'route', 'counter': 'counter', 'counterRevision': 'rev',
            'accuracy': 'calibrated', 'inputTokens': sum(len(m['content']) for m in messages),
            'inputBudget': 128000, 'completionTokens': 8192, 'marginTokens': 6400,
            'contextWindow': 1050000, 'messagesSha256': budget.digest(messages),
            'admitted': True, 'reason': None}


class AttemptReserveTests(unittest.TestCase):
    def setUp(self):
        self.messages = [{'role':'system','content':'full system'}, {'role':'user','content':'full input'}]
        self.descriptor = {'contract': budget.ATTEMPT_RESERVE_CONTRACT, 'requestId': 'r1',
            'callbackUrl': 'https://test.astrogeoagent.ru/api/internal/hermes/attempt-reserve',
            'token':'a'*64, 'initialAdmission':admission(self.messages)}
        self.body = {'tp_request_id':'r1','tp_attempt_reserve':self.descriptor,
                     'model':'openai/gpt-6-luna','messages':self.messages,
                     'response_format':{'type':'json_object'}}
        self.agent = SimpleNamespace(_tp_context_budget_body=self.body, _tp_context_model_calls=0,
                                     _tp_managed_transit_context={}, _tp_managed_repair_feedback='repair')
        self.kwargs = {'model':self.body['model'], 'messages':copy.deepcopy(self.messages),
            'max_tokens':8192, 'response_format':self.body['response_format'],
            'extra_body':{'provider':{'only':['openai'], 'allow_fallbacks':False,
                                     'max_price':budget.PRICE_CAPS[self.body['model']]},
                          'service_tier':'default','reasoning':{'effort':'high'}}}
        self.scope = patch.object(durable, 'request_key_for_agent', return_value='r1')
        self.count = patch.object(budget, 'prepare_admission', side_effect=lambda body: admission(body['messages']))
        self.scope.start(); self.count.start()
        self.addCleanup(self.scope.stop); self.addCleanup(self.count.stop)

    def dispatch(self):
        budget.admit_provider_call(self.agent, self.kwargs)

    def retry(self):
        self.kwargs['messages'] = self.messages + [{'role':'user','content':'repair'}]
        self.dispatch()

    def view(self, count=1, status='actual'):
        return {'accounting':{'generations':[{'cost':{'status':status}} for _ in range(count)]},
                'evidence':{'has_unresolved_attempt':False}}

    def test_both_output_parameters_are_rejected_before_transport(self):
        for contract, completion in ((budget.CONTRACT, 8192), (budget.CONTRACT_V2, 32768)):
            for first, second in ((8192, 32768), (32768, 8192)):
                with self.subTest(contract=contract, first=first, second=second):
                    self.agent._tp_context_model_calls = 0
                    self.body['tp_context_budget'] = {'contract': contract, 'completionTokens': completion}
                    self.descriptor['initialAdmission']['contract'] = contract
                    self.descriptor['initialAdmission']['completionTokens'] = completion
                    self.kwargs['max_tokens'] = first
                    self.kwargs['max_completion_tokens'] = second
                    with self.assertRaisesRegex(budget.ContextBudgetError, 'provider_contract_changed'):
                        self.dispatch()
                    self.assertEqual(self.agent._tp_context_model_calls, 0)

    def test_v2_output_ceiling_is_combined_and_frozen_across_retry(self):
        initial = {**admission(self.messages), 'contract': budget.CONTRACT_V2, 'completionTokens': 32768}
        retried = {**initial, 'messagesSha256': budget.digest(self.messages + [{'role':'user','content':'repair'}])}
        self.descriptor['initialAdmission'] = initial
        self.body['tp_context_admission'] = initial
        self.body['tp_context_budget'] = {'contract': budget.CONTRACT_V2, 'completionTokens': 32768}
        self.count.stop()
        with patch.object(budget, 'prepare_admission', side_effect=[initial, retried]), \
             patch.object(budget, '_require_known_previous_attempt'), \
             patch.object(budget, '_authorize_retry'):
            self.dispatch()
            self.assertEqual(self.kwargs['max_tokens'], 32768)
            self.retry()
            self.assertEqual(self.kwargs['max_tokens'], 32768)

    def test_price_band_frozen_and_retry_crosses_only_after_approval(self):
        self.descriptor['pricePolicyVersion'] = budget.BANDED_PRICE_POLICY
        low = {**admission(self.messages), 'inputTokens':271999, 'inputBudget':320000, 'marginTokens':16000}
        high = {**low, 'inputTokens':272000, 'messagesSha256':budget.digest(self.messages + [{'role':'user','content':'repair'}])}
        self.descriptor['initialAdmission'] = low
        self.count.stop()
        with patch.object(budget, 'prepare_admission', side_effect=[low, high]), \
             patch.object(budget, '_require_known_previous_attempt'), \
             patch.object(budget, '_authorize_retry') as approve:
            self.dispatch()
            self.assertEqual(self.kwargs['extra_body']['provider']['max_price'], {'prompt':0.125,'completion':0.5})
            self.retry()
            approve.assert_called_once()
            self.assertEqual(self.kwargs['extra_body']['provider']['max_price'], {'prompt':0.25,'completion':0.75})
        self.assertEqual(budget.attempt_price_cap('openai/gpt-6-luna',272000,None), {'prompt':0.125,'completion':0.5})
        self.assertEqual(budget.attempt_price_cap('openai/gpt-6-luna',271999,budget.BANDED_PRICE_POLICY), {'prompt':0.125,'completion':0.5})
        self.assertEqual(budget.attempt_price_cap('openai/gpt-6-luna',272000,budget.BANDED_PRICE_POLICY), {'prompt':0.25,'completion':0.75})

    def test_denied_retry_does_not_raise_provider_cap(self):
        self.descriptor['pricePolicyVersion'] = budget.BANDED_PRICE_POLICY
        low = {**admission(self.messages), 'inputTokens':271999, 'inputBudget':320000, 'marginTokens':16000}
        high = {**low, 'inputTokens':272000, 'messagesSha256':budget.digest(self.messages + [{'role':'user','content':'repair'}])}
        self.descriptor['initialAdmission'] = low
        self.count.stop()
        with patch.object(budget, 'prepare_admission', side_effect=[low, high]), \
             patch.object(budget, '_require_known_previous_attempt'), \
             patch.object(budget, '_authorize_retry', side_effect=budget.ContextBudgetError('denied')):
            self.dispatch()
            with self.assertRaisesRegex(budget.ContextBudgetError,'denied'): self.retry()
            self.assertEqual(self.kwargs['extra_body']['provider']['max_price'], {'prompt':0.125,'completion':0.5})

    def test_first_has_no_callback_and_retries_receive_fresh_receipts(self):
        with patch.object(budget, '_authorize_retry') as authorize, patch.object(durable, 'get_request_view', side_effect=[self.view(), self.view(2)]):
            self.dispatch(); authorize.assert_not_called()
            self.retry(); self.retry()
            self.assertEqual([x.args[2] for x in authorize.call_args_list], [2,3])
            receipt = authorize.call_args_list[0].args[1]
            self.assertEqual(receipt['messagesSha256'],budget.digest(self.kwargs['messages']))
            self.assertGreater(receipt['inputTokens'],self.descriptor['initialAdmission']['inputTokens'])
            self.assertEqual(self.agent._tp_context_model_calls,3)

    def test_invalid_descriptor_initial_receipt_and_scope_fail_before_call(self):
        for field, value in [('callbackUrl','https://evil.invalid/'),('token','bad'),('requestId','other')]:
            with self.subTest(field=field):
                original=self.descriptor[field]; self.descriptor[field]=value
                with self.assertRaises(budget.ContextBudgetError): self.dispatch()
                self.descriptor[field]=original
        self.descriptor['initialAdmission']['model']='wrong'
        with self.assertRaisesRegex(budget.ContextBudgetError,'initial_mismatch'): self.dispatch()
        self.descriptor['initialAdmission']=admission(self.messages)
        with patch.object(durable,'request_key_for_agent',return_value='different'):
            with self.assertRaisesRegex(budget.ContextBudgetError,'request_mismatch'): self.dispatch()
        self.assertEqual(self.agent._tp_context_model_calls,0)

    def test_unknown_previous_and_denied_or_timeout_do_not_increment(self):
        self.dispatch()
        for view in (self.view(status='pending'), self.view(count=0),
                     {'accounting':self.view()['accounting'],'evidence':{'has_unresolved_attempt':True}}):
            with patch.object(durable,'get_request_view',return_value=view), patch.object(budget,'_authorize_retry') as authorize:
                with self.assertRaisesRegex(budget.ContextBudgetError,'previous_attempt_unknown'): self.retry()
                authorize.assert_not_called()
        for error in ('hermes_attempt_reserve_denied','hermes_attempt_reserve_unavailable'):
            with patch.object(durable,'get_request_view',return_value=self.view()), patch.object(budget,'_authorize_retry',side_effect=budget.ContextBudgetError(error)):
                with self.assertRaisesRegex(budget.ContextBudgetError,error): self.retry()
                self.assertEqual(self.agent._tp_context_model_calls,1)

    def test_callback_no_redirect_and_strict_response(self):
        receipt=admission(self.messages)
        response=SimpleNamespace(status=200, read=lambda n:json.dumps({'approved':True,'requestId':'r1','attempt':2,'messagesSha256':receipt['messagesSha256']}).encode())
        response.__enter__=lambda s:s
        # Special methods are looked up on the type.
        class Response:
            status=200
            def __enter__(self): return self
            def __exit__(self,*args): pass
            def read(self,n): return response.read(n)
        opener=SimpleNamespace(open=lambda *a,**k:Response())
        with patch.object(budget.urllib.request,'build_opener',return_value=opener) as build:
            budget._authorize_retry(self.descriptor,receipt,2)
            self.assertIsInstance(build.call_args.args[0],budget._NoRedirect)
        with patch.object(budget.urllib.request,'build_opener',return_value=SimpleNamespace(open=lambda *a,**k: (_ for _ in ()).throw(TimeoutError()))):
            with self.assertRaisesRegex(budget.ContextBudgetError,'unavailable'): budget._authorize_retry(self.descriptor,receipt,2)

    def test_callback_distinguishes_insufficient_denial_and_unknown_errors(self):
        receipt=admission(self.messages)
        for status, payload, expected in (
            (402, {"error":"hermes_attempt_reserve_insufficient"}, "hermes_attempt_reserve_insufficient"),
            (409, {"error":"hermes_attempt_reserve_denied"}, "hermes_attempt_reserve_denied"),
            (402, {"error":"other"}, "hermes_attempt_reserve_invalid"),
            (401, {"error":"hermes_attempt_reserve_denied"}, "hermes_attempt_reserve_invalid"),
            (503, {}, "hermes_attempt_reserve_unavailable"),
        ):
            error=budget.urllib.error.HTTPError('https://test.invalid',status,'',{},io.BytesIO(json.dumps(payload).encode()))
            opener=SimpleNamespace(open=lambda *a, **k: (_ for _ in ()).throw(error))
            with self.subTest(status=status, payload=payload), patch.object(budget.urllib.request,'build_opener',return_value=opener):
                with self.assertRaisesRegex(budget.ContextBudgetError, '^'+expected+'$'):
                    budget._authorize_retry(self.descriptor,receipt,2)

if __name__ == '__main__': unittest.main()
