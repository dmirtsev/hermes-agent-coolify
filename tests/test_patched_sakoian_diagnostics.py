"""Real patched HTTP handler, fake SDK, and private temporary journals only."""
import asyncio
import copy
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch


@unittest.skipUnless(Path('/opt/hermes/agent/transports/chat_completions.py').is_file(),
                     'runs against the built Hermes image')
class PatchedSakoianDiagnosticsTests(unittest.TestCase):
    def exercise(self, raw, reason_code, *, finish='stop', storage_failure=False):
        from gateway.platforms.api_server import APIServerAdapter
        from agent import sakoian_analysis_context, sakoian_diagnostics, durable_accounting
        from test_hermes_analysis_context import install_test_manifest
        manifest = install_test_manifest()
        self.addCleanup(manifest.unlink, missing_ok=True)
        context = json.loads((Path(__file__).parent / 'fixtures' /
                              'sakoian-aggregate-context.json').read_text())
        request_key = context['request_id']
        client = Mock()
        client.with_options.return_value = client
        client.chat.completions.create.return_value = SimpleNamespace(
            id='diagnostics-unbilled-fixture', model='deepseek/deepseek-v4-flash',
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=12, total_tokens=22),
            choices=[SimpleNamespace(finish_reason=finish,
                message=SimpleNamespace(content=raw, tool_calls=None))])
        agent = SimpleNamespace(provider='openrouter', model='deepseek/deepseek-v4-flash',
            base_url='https://openrouter.ai/api/v1',
            _ensure_primary_openai_client=Mock(return_value=client),
            run_conversation=Mock(side_effect=AssertionError('no agent loop')))

        class Request:
            headers = {'Authorization':'Bearer local-fixture', 'Idempotency-Key':request_key}
            async def json(self):
                return {'stream':False, 'tools':[], 'messages':[{'role':'user','content':'Q'}],
                        'tp_analysis_context':context}

        def adapter():
            obj = object.__new__(APIServerAdapter)
            obj._api_key = 'local-fixture'
            obj._model_name = 'test/model'
            obj._create_agent = lambda **kwargs: agent
            return obj

        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ,
                {'HERMES_ACCOUNTING_JOURNAL_PATH':str(Path(directory) / 'accounting.sqlite')}):
            if storage_failure:
                with patch.object(sakoian_diagnostics, 'save_rejection',
                        side_effect=sakoian_diagnostics.DiagnosticsError('diagnostics_storage_unavailable')):
                    response = asyncio.run(adapter()._handle_chat_completions(Request()))
            else:
                response = asyncio.run(adapter()._handle_chat_completions(Request()))
            # A fresh handler uses durable replay; no second model request.
            replay = asyncio.run(adapter()._handle_chat_completions(Request()))
            body = json.loads(response.text)
            self.assertEqual(response.status, 502)
            self.assertEqual(response.status, replay.status)
            self.assertEqual(body['tp_analysis_validation'],
                             json.loads(replay.text)['tp_analysis_validation'])
            validation = body['tp_analysis_validation']
            self.assertEqual(validation['reason_code'], reason_code)
            self.assertEqual(validation['status'], 'rejected')
            self.assertNotIn('choices', body)
            self.assertEqual(body['hermes_accounting']['tokens']['total'], 22)
            self.assertEqual(client.chat.completions.create.call_count, 1)
            agent.run_conversation.assert_not_called()
            with closing(sqlite3.connect(Path(directory) / 'accounting.sqlite')) as db:
                self.assertEqual(db.execute('SELECT count(*) FROM accounting_evidence_event '
                    "WHERE kind='generation'").fetchone()[0], 1)
                stored_result = json.loads(db.execute('SELECT result_json FROM accounting_request').fetchone()[0])
            self.assertEqual(stored_result['final_response'], '')
            self.assertEqual(stored_result['messages'], [])
            self.assertNotIn('PRIVATE_MODEL_TEXT', response.text)
            self.assertNotIn('PRIVATE_MODEL_TEXT', json.dumps(stored_result))
            self.assertNotIn('PRIVATE_MODEL_TEXT', json.dumps(durable_accounting.get_request_view(request_key)))
            if storage_failure:
                self.assertEqual(validation['diagnostic_status'], 'unavailable')
                self.assertEqual(validation['diagnostic_error'], 'diagnostics_storage_unavailable')
                self.assertNotIn('diagnostic_id', validation)
                return
            self.assertEqual(validation['diagnostic_status'], 'stored')
            record = sakoian_diagnostics.read_rejection(request_key, include_plan=True)
            self.assertEqual(record['raw_text'], raw)
            self.assertEqual(record['reason']['code'], reason_code)
            self.assertEqual(record['diagnostic_id'], validation['diagnostic_id'])
            self.assertEqual(record['generation_id'], 'diagnostics-unbilled-fixture')
            self.assertEqual(record['raw_sha256'], hashlib.sha256(raw.encode('utf-8', 'surrogatepass')).hexdigest())
            # Reopen in a fresh interpreter to demonstrate persistence beyond process memory.
            child = subprocess.run([sys.executable, '-c',
                'import json,sys;from agent.sakoian_diagnostics import read_rejection;'
                'print(json.dumps(read_rejection(sys.argv[1], include_plan=True)))', request_key],
                capture_output=True, text=True, check=True)
            self.assertEqual(json.loads(child.stdout)['raw_text'], raw)
            safe = sakoian_diagnostics.read_rejection(request_key)
            self.assertNotIn('raw_text', safe)
            self.assertNotIn('PRIVATE_MODEL_TEXT', json.dumps(safe))

    def test_precise_reasons_survive_real_handler_and_durable_replay(self):
        from grounded_narrative import default_plan
        from test_sakoian_grounded_call import catalogue
        plan = default_plan(catalogue())
        cases = [('{\n PRIVATE_MODEL_TEXT', 'malformed_json'),
                 ('{"catalogue_hash":1e999}', 'nonfinite_json'),
                 ('{"x":1,"x":2}', 'duplicate_json_key'),
                 ('[[]', 'malformed_json')]
        for field, value, reason in (
                ('catalogue_hash', 'PRIVATE_MODEL_TEXT', 'catalogue_hash_mismatch'),
                ('summary', 'PRIVATE_MODEL_TEXT', 'extra_field'),
                ('event_order', plan['event_order'][:-1], 'missing_order_id'),
                ('event_order', ['T1'] * len(plan['event_order']), 'duplicate_order_id'),
                ('event_order', ['PRIVATE_MODEL_TEXT'] + plan['event_order'][1:], 'unknown_order_id')):
            wrong = copy.deepcopy(plan)
            wrong[field] = value
            cases.append((json.dumps(wrong), reason))
        for raw, reason in cases:
            with self.subTest(reason=reason):
                self.exercise(raw, reason)

    def test_truncation_saves_the_received_text_and_completion_reason(self):
        self.exercise('PRIVATE_MODEL_TEXT truncated', 'completion_finish_reason', finish='length')

    def test_storage_failure_is_visible_without_losing_accounting_or_retrying(self):
        self.exercise('PRIVATE_MODEL_TEXT', 'malformed_json', storage_failure=True)


if __name__ == '__main__':
    unittest.main()
