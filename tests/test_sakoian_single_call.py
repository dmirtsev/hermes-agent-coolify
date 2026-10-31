import unittest
from types import SimpleNamespace
from unittest.mock import Mock,patch
from hermes_analysis_context import narrate_sakoian_once,SakoianContextError

class SingleNarrationTests(unittest.TestCase):
    def agent(self,reason='stop',text='Five groups'):
        client=Mock();client.with_options.return_value=client
        client.chat.completions.create.return_value=SimpleNamespace(choices=[SimpleNamespace(finish_reason=reason,message=SimpleNamespace(content=text,tool_calls=None))])
        agent=SimpleNamespace(provider='openrouter',model='deepseek/deepseek-v4-flash',_ensure_primary_openai_client=Mock(return_value=client))
        return agent,client
    def recording(self):
        import sys
        name='agent.openrouter_accounting' if 'agent.openrouter_accounting' in sys.modules else 'hermes_openrouter_accounting'
        return patch(name+'.record_openrouter_response')
    def test_one_call_uses_hard_bound_and_disables_hidden_retries(self):
        agent,c=self.agent()
        with self.recording() as record:r=narrate_sakoian_once(agent,'question','closed facts')
        self.assertTrue(r['completed']);self.assertEqual(c.chat.completions.create.call_count,1)
        c.with_options.assert_called_once_with(max_retries=0,timeout=90)
        body=c.chat.completions.create.call_args.kwargs
        self.assertEqual(body['max_tokens'],4096);self.assertFalse(body['extra_body']['reasoning']['enabled']);self.assertFalse(body['extra_body']['provider']['allow_fallbacks']);self.assertNotIn('tools',body);record.assert_called_once()
    def test_truncation_and_empty_response_never_continue(self):
        for reason,text in [('length','partial'),('stop',''),('tool_calls','')]:
            with self.subTest(reason=reason,text=text):
                agent,c=self.agent(reason,text)
                with self.recording():r=narrate_sakoian_once(agent,'q','facts')
                self.assertTrue(r['failed']);self.assertFalse(r['completed']);self.assertEqual(c.chat.completions.create.call_count,1)
    def test_wrong_route_rejected_before_provider(self):
        agent,c=self.agent();agent.model='other/model'
        with self.assertRaises(SakoianContextError):narrate_sakoian_once(agent,'q','facts')
        c.chat.completions.create.assert_not_called()
