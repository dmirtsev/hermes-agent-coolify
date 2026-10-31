import copy
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

if __name__ == '__main__': unittest.main()
