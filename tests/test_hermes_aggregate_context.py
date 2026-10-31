import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import hermes_analysis_context as module
from aggregate_projection import aggregate_identity, project
from hermes_analysis_context import SakoianContextError, canonical_hash, prepare_sakoian_context

ROOT = Path(__file__).parents[1]


def aggregate_fixture():
    return json.loads((ROOT/'tests/fixtures/sakoian-aggregate-context.json').read_text())


def reseal(value):
    aggregate = value['aggregate']
    aggregate['aggregate_id'] = aggregate_identity(aggregate)
    aggregate['aggregate_hash'] = canonical_hash({k:v for k,v in aggregate.items() if k!='aggregate_hash'})
    value['aggregate_hash'] = aggregate['aggregate_hash']


class AggregateContextTests(unittest.TestCase):
    def setUp(self):
        self.manifest = patch.object(module, 'MANIFEST_PATH', ROOT/'hermes_sakoian_manifest.json')
        self.manifest.start(); self.addCleanup(self.manifest.stop)

    def prepare(self, value):
        return prepare_sakoian_context(value, expected_request_id=value['request_id'])

    def test_five_real_results_create_one_compact_context(self):
        value = aggregate_fixture(); guard = self.prepare(value)
        self.assertEqual(guard.receipt['aspect_count'], 5)
        self.assertEqual(guard.receipt['group_count'], 3)
        self.assertEqual(guard.receipt['aggregate_hash'], value['aggregate_hash'])
        self.assertEqual(guard.receipt['response_hash'], value['aggregate']['source']['response_hash'])
        self.assertEqual(guard.receipt['prompt_contract_version'], 'sakoian_aggregate_v1')
        self.assertFalse(guard.receipt['external_tools_used']); self.assertFalse(guard.receipt['shared_memory_used'])
        self.assertNotIn('"analyses":', guard.prompt)
        self.assertNotIn('"evidence_refs":', guard.prompt)
        self.assertLess(len(guard.prompt.encode()), 18000)
        for event in value['aggregate']['projection']['events']:
            self.assertIn(event['analysis_id'], guard.prompt)

    def test_mutated_semantics_rejected_even_when_rehashed(self):
        for case in ('extra','aggregate_identity','member_identity','coverage','projection','source','method','duplicate','order'):
            with self.subTest(case=case):
                value=aggregate_fixture(); a=value['aggregate']
                if case=='extra': a['extra']='no'
                elif case=='aggregate_identity': a['chart_ref']='other'
                elif case=='member_identity': a['analyses'][0]['subject_ref']='other'
                elif case=='coverage': a['coverage']['omitted_count']=1
                elif case=='projection': a['projection']['groups']=[]
                elif case=='source': a['source']['response_hash']='f'*64
                elif case=='method': a['method_ref']['package_hash']='0'*64
                elif case=='duplicate': a['analyses'][1]=copy.deepcopy(a['analyses'][0]);a['projection']=project(a['analyses'])
                else: a['analyses'].reverse()
                reseal(value)
                with self.assertRaises(SakoianContextError): self.prepare(value)

    def test_context_digest_and_idempotency_are_checked(self):
        value=aggregate_fixture(); value['aggregate_hash']='f'*64
        with self.assertRaises(SakoianContextError): self.prepare(value)
        value=aggregate_fixture()
        with self.assertRaises(SakoianContextError):
            prepare_sakoian_context(value, expected_request_id='other')
        value['conversation_ref']=''
        with self.assertRaises(SakoianContextError): self.prepare(value)

    def test_cardinality_and_size_never_silently_trim(self):
        for count in (1,25):
            value=aggregate_fixture();a=value['aggregate'];a['analyses']=(a['analyses']*5)[:count];reseal(value)
            with self.assertRaises(SakoianContextError): self.prepare(value)
        with patch.object(module,'MAX_AGGREGATE_CONTEXT_BYTES',100):
            with self.assertRaises(SakoianContextError): self.prepare(aggregate_fixture())
        with patch.object(module,'MAX_AGGREGATE_PROMPT_BYTES',100):
            with self.assertRaises(SakoianContextError): self.prepare(aggregate_fixture())

    def test_vendored_projection_and_schema_are_present(self):
        self.assertTrue((ROOT/'hermes_sakoian_aggregate.schema.json').is_file())
        value=aggregate_fixture();self.assertEqual(value['aggregate']['projection'], project(value['aggregate']['analyses']))
