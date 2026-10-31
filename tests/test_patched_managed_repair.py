"""Actual pinned agent loop and accounting; no external provider calls."""
import asyncio
import copy
import json
import os
import sys
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from test_hermes_managed_validation import body, reply


@unittest.skipUnless(Path('/opt/hermes/agent/conversation_loop.py').is_file(), 'built pinned runtime only')
class PatchedManagedRepairTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, '/opt/hermes')

    def run_loop(self, candidates, *, overflow=False, validator_failure=False, reserve=None, completion=8192):
        from gateway.platforms.api_server import APIServerAdapter
        from run_agent import AIAgent
        supplied = body(True)
        supplied.update(model='openai/gpt-6-luna', tp_dialogue_model='gpt_luna',
            response_format={'type':'json_object'},
            tp_context_budget={'contract':'hermes-context-budget-v1','profile':'natal_transit','inputBudget':128000})
        if completion == 32768:
            supplied['tp_context_budget'] = {'contract':'hermes-context-budget-v2','profile':'natal_transit', 'inputBudget':320000, 'completionTokens':32768}
        request_key = None
        def admission_receipt(messages):
            from agent.context_budget import digest
            return {'contract':supplied['tp_context_budget']['contract'],'model':supplied['model'],
                'route':'test','counter':'test','counterRevision':'1','accuracy':'calibrated',
                'inputTokens':(271999 if len(messages) == len(supplied['messages']) else 272000) if completion == 32768 else sum(len(m['content']) for m in messages), 'inputBudget':320000 if completion == 32768 else 128000,
                'completionTokens':completion,'marginTokens':16000 if completion == 32768 else 6400,'contextWindow':1000000,
                'messagesSha256':digest(messages),'admitted':True,'reason':None}
        if reserve is not None:
            from agent.durable_accounting import begin_request
            request_key='reserve-loop-'+uuid.uuid4().hex
            begin_request(request_key, 'a'*64)
            supplied['tp_request_id']=request_key
            supplied['tp_attempt_reserve']={'contract':'hermes-attempt-reserve-v1',
                'requestId':request_key,'callbackUrl':'https://test.astrogeoagent.ru/api/internal/hermes/attempt-reserve',
                'token':'a'*64,'initialAdmission':admission_receipt(supplied['messages'])}
            if completion == 32768:
                supplied['tp_attempt_reserve']['pricePolicyVersion']='hermes-provider-ceilings-v2'
                supplied['tp_context_admission']=admission_receipt(supplied['messages'])
        agent = AIAgent(model=supplied['model'], provider='openrouter',
            api_key='synthetic-unused-key', base_url='https://example.invalid/v1',
            enabled_toolsets=[], max_iterations=3, max_tokens=8192,
            skip_context_files=True, skip_memory=True, quiet_mode=True,
            request_overrides={'response_format':supplied['response_format'], 'extra_body':{
                'provider':{'only':['openai'],'allow_fallbacks':False},
                'service_tier':'default','reasoning':{'effort':'high'}}},
            ephemeral_system_prompt='FORBIDDEN-HIDDEN-SYSTEM')
        agent.prefill_messages=[]
        agent._api_max_retries=1
        calls=[]
        def fake_call(kwargs,*args,**kw):
            calls.append(copy.deepcopy(kwargs))
            content, finish = candidates[min(len(calls)-1,len(candidates)-1)]
            if isinstance(content,Exception):raise content
            return SimpleNamespace(id='gen-managed-'+uuid.uuid4().hex,
                model=supplied['model'],provider='OpenAI',openrouter_metadata=None,
                usage=SimpleNamespace(prompt_tokens=10,completion_tokens=20,total_tokens=30,cost='0.00001',
                    prompt_tokens_details=SimpleNamespace(cached_tokens=0,cache_write_tokens=0),
                    completion_tokens_details=SimpleNamespace(reasoning_tokens=0)),
                choices=[SimpleNamespace(finish_reason=finish,message=SimpleNamespace(
                    content=content,tool_calls=None,reasoning=None,reasoning_content=None,reasoning_details=None))])
        agent._interruptible_api_call=fake_call
        agent._interruptible_streaming_api_call=fake_call
        adapter=object.__new__(APIServerAdapter)
        adapter._create_agent=lambda **kw:agent
        checks=[]
        def admission(actual):
            checks.append(copy.deepcopy(actual['messages']))
            return ({**admission_receipt(actual['messages']), 'admitted':not(overflow and len(checks)>1), 'reason':'hermes_context_input_overflow' if overflow and len(checks)>1 else None})
        import agent.conversation_loop as loop
        original_guard=loop.admit_provider_call
        def capture_guard(a,kwargs):
            try:return original_guard(a,kwargs)
            except Exception:
                print('SYNTHETIC_GUARD_DIAGNOSTIC '+json.dumps({'model':kwargs.get('model'),'max_tokens':kwargs.get('max_tokens'),'max_completion_tokens':kwargs.get('max_completion_tokens'),'tools':bool(kwargs.get('tools')),'provider_calls':getattr(a,'_tp_context_model_calls',None)}))
                raise
        class CallbackResponse:
            status=200
            def __init__(self,request): self.request=request
            def __enter__(self): return self
            def __exit__(self,*args): pass
            def read(self,limit):
                data=json.loads(self.request.data)
                return json.dumps({'approved':reserve['outcome']=='approved',
                    'requestId':data['requestId'],'attempt':data['attempt'],
                    'messagesSha256':data['admission']['messagesSha256']}).encode()
        def callback(request,timeout):
            data=json.loads(request.data)
            reserve['receipts'].append((data['attempt'],copy.deepcopy(data['admission'])))
            if reserve['outcome']=='unavailable': raise TimeoutError()
            return CallbackResponse(request)
        with patch('agent.context_budget.urllib.request.build_opener',return_value=SimpleNamespace(open=callback)), patch('agent.conversation_loop.admit_provider_call',side_effect=capture_guard), patch('agent.context_budget.prepare_admission',side_effect=admission), patch.dict(os.environ,{
                'HERMES_RUNTIME_TIER':'economy','HERMES_RUNTIME_ID':'synthetic-managed-test'}):
            if validator_failure:
                with patch('agent.context_budget.managed_transit_issues',side_effect=RuntimeError('synthetic internal failure')):
                    result,usage=asyncio.run(adapter._run_agent(user_message=supplied['messages'][1]['content'],
                        conversation_history=[],strict_context_only=True,
                        managed_system_prompt=supplied['messages'][0]['content'],
                        managed_response_format=supplied['response_format'],managed_continuation=True,
                        context_budget_body=supplied, accounting_request_key=request_key))
            else:
                result,usage=asyncio.run(adapter._run_agent(user_message=supplied['messages'][1]['content'],
                    conversation_history=[],strict_context_only=True,
                    managed_system_prompt=supplied['messages'][0]['content'],
                    managed_response_format=supplied['response_format'],managed_continuation=True,
                    context_budget_body=supplied, accounting_request_key=request_key))
        return result,usage,calls,checks,supplied

    def test_reservation_gate_in_real_loop_preserves_accounting_and_context(self):
        bad=reply('{{s2}} Повторное прохождение утром.')
        for outcome in ('approved','denied','unavailable'):
            with self.subTest(outcome=outcome):
                reserve={'outcome':outcome,'receipts':[]}
                result,usage,calls,_,original=self.run_loop([(bad,'stop'),(reply(),'stop')],reserve=reserve)
                self.assertEqual(len(calls),2 if outcome=='approved' else 1)
                self.assertEqual(calls[0]['messages'],original['messages'])
                self.assertEqual(calls[0].get('max_tokens',calls[0].get('max_completion_tokens')),8192)
                self.assertEqual(len(reserve['receipts']),1)
                attempt,receipt=reserve['receipts'][0]
                self.assertEqual(attempt,2)
                self.assertGreater(receipt['inputTokens'],original['tp_attempt_reserve']['initialAdmission']['inputTokens'])
                if outcome=='approved':
                    self.assertEqual(calls[1]['messages'][:2],original['messages'])
                    self.assertEqual(calls[1].get('max_tokens',calls[1].get('max_completion_tokens')),8192)
                else:
                    self.assertEqual(result['error'],'hermes_attempt_reserve_'+outcome)
                accounting=usage['_hermes_openrouter_accounting']
                self.assertEqual(accounting['cost']['amount_micro_usd'],len(calls)*10)

    def test_full_output_and_funded_price_band_in_pinned_loop(self):
        bad=reply('{{s2}} Повторное прохождение утром.')
        for outcome in ('approved','denied','unavailable'):
            with self.subTest(outcome=outcome):
                reserve={'outcome':outcome,'receipts':[]}
                result,usage,calls,_,original=self.run_loop([(bad,'stop'),(reply(),'stop')], reserve=reserve, completion=32768)
                self.assertEqual(len(calls),2 if outcome=='approved' else 1)
                self.assertEqual(len(reserve['receipts']),1)
                self.assertEqual(reserve['receipts'][0][1]['inputTokens'],272000)
                self.assertEqual(original['tp_context_admission']['inputTokens'],271999)
                self.assertEqual(calls[0].get('max_tokens',calls[0].get('max_completion_tokens')),32768)
                self.assertFalse('max_tokens' in calls[0] and 'max_completion_tokens' in calls[0])
                self.assertEqual(calls[0]['extra_body']['provider']['max_price'],{'prompt':0.125,'completion':0.5})
                if outcome=='approved':
                    self.assertEqual(calls[1].get('max_tokens',calls[1].get('max_completion_tokens')),32768)
                    self.assertFalse('max_tokens' in calls[1] and 'max_completion_tokens' in calls[1])
                    self.assertEqual(calls[1]['extra_body']['provider']['max_price'],{'prompt':0.25,'completion':0.75})
                else:
                    self.assertEqual(result['error'],'hermes_attempt_reserve_'+outcome)
                self.assertEqual(usage['_hermes_openrouter_accounting']['cost']['amount_micro_usd'],len(calls)*10)

    def test_reservation_failure_response_keeps_actual_charge(self):
        from gateway.platforms.api_server import APIServerAdapter
        for outcome in ('denied','unavailable'):
            with self.subTest(outcome=outcome):
                result,usage,calls,_,_=self.run_loop(
                    [(reply('{{s2}} Повторное прохождение утром.'),'stop')],
                    reserve={'outcome':outcome,'receipts':[]})
                adapter=object.__new__(APIServerAdapter)
                adapter._api_key='';adapter._model_name='hermes-agent';adapter._check_auth=lambda request:None
                adapter._run_agent=AsyncMock(return_value=(result,usage))
                key='reserve-failure-response-'+uuid.uuid4().hex
                class Request:
                    headers={'Idempotency-Key':key}
                    async def json(self): return {'model':'caller/model','messages':[{'role':'user','content':'same'}],'stream':False}
                response=asyncio.run(adapter._handle_chat_completions(Request()))
                payload=json.loads(response.text)
                self.assertEqual(payload['error']['code'],'hermes_attempt_reserve_'+outcome)
                self.assertEqual(payload['hermes_accounting']['cost']['amount_micro_usd'],10)
                self.assertEqual(len(calls),1)

    def test_normal_and_length_bad_draft_repair_with_full_original_context(self):
        bad=reply('{{s2}}. Повторное прохождение: ретроградный Меркурий утром.')
        good=reply()
        for finish in ('stop','length'):
            result,usage,calls,checks,original=self.run_loop([(bad,finish),(good,'stop')])
            self.assertEqual(result['final_response'],good)
            self.assertEqual(len(calls),2)
            self.assertEqual(calls[0]['messages'],original['messages'])
            self.assertEqual(calls[1]['messages'][:2],original['messages'])
            self.assertEqual(len(calls[1]['messages']),3)
            self.assertIn('unprovided_repeat_pass',calls[1]['messages'][2]['content'])
            self.assertNotIn(bad,json.dumps(calls[1]['messages'],ensure_ascii=False))
            self.assertEqual(checks,[call['messages'] for call in calls])
            accounting=usage['_hermes_openrouter_accounting']
            self.assertEqual(len(accounting['model_execution']['upstream_generation_ids']),2)
            self.assertEqual(accounting['cost']['amount_micro_usd'],20)

    def test_three_bad_drafts_never_publish_or_dispatch_a_fourth(self):
        bad=reply('{{s2}} Повторное прохождение утром.')
        for finish in ('stop','length'):
            result,usage,calls,_,_=self.run_loop([(bad,finish)])
            self.assertEqual(len(calls),3)
            self.assertEqual(result['final_response'],'')
            self.assertEqual(result['error'],'managed_transit_validation_exhausted')
            self.assertFalse(result['completed'])
            self.assertNotIn(bad,json.dumps(result['messages'],ensure_ascii=False))
            self.assertEqual(usage['_hermes_openrouter_accounting']['cost']['amount_micro_usd'],30)

    def test_validator_internal_failure_stops_once_with_accounting(self):
        result,usage,calls,_,_=self.run_loop([(reply(),'stop')],validator_failure=True)
        self.assertEqual(len(calls),1)
        self.assertEqual(result['final_response'],'')
        self.assertEqual(result['error'],'managed_transit_validation_unavailable')
        self.assertEqual(usage['_hermes_openrouter_accounting']['cost']['amount_micro_usd'],10)

    def test_retry_overflow_stops_before_second_paid_dispatch(self):
        result,usage,calls,checks,original=self.run_loop([(reply('{{s2}} Повторное прохождение.'),'stop')],overflow=True)
        self.assertEqual(len(calls),1)
        self.assertGreaterEqual(len(checks),2)
        self.assertEqual(checks[1][:2],original['messages'])
        self.assertEqual(usage['_hermes_openrouter_accounting']['cost']['amount_micro_usd'],10)

    def test_two_generations_have_one_replayable_gateway_receipt(self):
        from gateway.platforms.api_server import APIServerAdapter
        result,usage,calls,_,_=self.run_loop([(reply('{{s2}} Повторное прохождение.'),'stop'),(reply(),'stop')])
        adapter=object.__new__(APIServerAdapter)
        adapter._api_key='';adapter._model_name='hermes-agent';adapter._check_auth=lambda request:None
        adapter._run_agent=AsyncMock(return_value=(result,usage))
        key='managed-two-generation-replay-'+uuid.uuid4().hex
        class Request:
            headers={'Idempotency-Key':key}
            async def json(self):return {'model':'caller/model','messages':[{'role':'user','content':'same'}],'stream':False}
        first=json.loads(asyncio.run(adapter._handle_chat_completions(Request())).text)
        second=json.loads(asyncio.run(adapter._handle_chat_completions(Request())).text)
        self.assertEqual(adapter._run_agent.await_count,1)
        self.assertEqual(first['hermes_accounting'],second['hermes_accounting'])
        self.assertEqual(first['hermes_accounting']['cost']['amount_micro_usd'],20)
        self.assertEqual(len(first['hermes_accounting']['model_execution']['upstream_generation_ids']),2)

    def test_safe_failures_are_non_success_responses_with_one_replayable_actual_receipt(self):
        from gateway.platforms.api_server import APIServerAdapter
        for validator_failure, generations in ((False,3),(True,1)):
            bad=reply('{{s2}} Повторное прохождение утром.')
            result,usage,calls,_,_=self.run_loop([(bad,'stop')],validator_failure=validator_failure)
            self.assertEqual(len(calls),generations)
            adapter=object.__new__(APIServerAdapter)
            adapter._api_key='';adapter._model_name='hermes-agent';adapter._check_auth=lambda request:None
            adapter._run_agent=AsyncMock(return_value=(result,usage))
            key='managed-safe-failure-replay-'+uuid.uuid4().hex
            class Request:
                headers={'Idempotency-Key':key}
                async def json(self):return {'model':'caller/model','messages':[{'role':'user','content':'same'}],'stream':False}
            first_response=asyncio.run(adapter._handle_chat_completions(Request()))
            second_response=asyncio.run(adapter._handle_chat_completions(Request()))
            self.assertEqual(first_response.status,502)
            self.assertEqual(second_response.status,502)
            first=json.loads(first_response.text);second=json.loads(second_response.text)
            self.assertEqual(first['error']['code'],'agent_incomplete')
            self.assertNotIn('choices',first)
            self.assertNotIn(bad,json.dumps(first,ensure_ascii=False))
            self.assertEqual(adapter._run_agent.await_count,1)
            self.assertEqual(first['hermes_accounting'],second['hermes_accounting'])
            self.assertEqual(first['hermes_accounting']['cost']['amount_micro_usd'],generations*10)
            self.assertEqual(len(first['hermes_accounting']['model_execution']['upstream_generation_ids']),generations)


if __name__=='__main__':unittest.main()
