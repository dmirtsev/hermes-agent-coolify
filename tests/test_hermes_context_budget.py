import copy
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import hermes_context_budget as budget


MODEL = "openai/gpt-6-luna"
PROFILE = {MODEL: {"route": "test:openai", "counter": "fake", "counterRevision": "1",
    "contextWindow": 142592, "accuracy": "calibrated", "evidenceSha256": "a" * 64}}


def body(limit=128000):
    return {"model": MODEL, "tp_dialogue_model": "gpt_luna", "tp_answer_format": "hermes_managed_v1",
        "tp_context_budget": {"contract": budget.CONTRACT, "profile": "natal_transit", "inputBudget": limit},
        "messages": [{"role": "system", "content": "Полная инструкция"}, {"role": "user", "content": "Полная история и все интервалы"}]}


def pack_rows(rows, v2=False):
    layouts, packed = [], []
    for row in rows:
        columns = sorted(row)
        if columns not in layouts:
            layouts.append(columns)
        packed.append([layouts.index(columns)] + [row[key] for key in columns])
    result = {"format": "typed_rows_v1", "layouts": layouts, "rows": packed,
              "count": len(rows), "sourceSha256": budget.digest(rows)}
    if v2:
        result.update(format="period_facts_v2", encoding="period_lossless_v2", aliases=[[] for _ in layouts])
    return result


def v2_body(count=3):
    from test_hermes_managed_validation import body as transit_body
    value = transit_body()
    user = json.loads(value["messages"][1]["content"])
    calc = value["tp_managed_method_context"]["calculation"]
    native = []
    for index in range(count):
        row = copy.deepcopy(calc["facts"][index % 3])
        row["fact_id"] = "canonical-interval-" + str(index)
        del row["id"]
        row["opaque"] = {"extra": [None, True, False, 0, "", 0.40000123456789]}
        row["band"] = {"exact_times": copy.deepcopy(row["time_window"]["exact_times"])}
        native.append(row)
    calc["facts"], calc["factRefs"] = native, [row["fact_id"] for row in native]
    user["context"]["transitPeriod"] = copy.deepcopy(calc)
    user["context"]["transitPeriod"]["facts"] = pack_rows(native, True)
    statements = [user["facts"]["statements"][0]]
    # Many natal rows deliberately shift the global period namespace.
    statements += [{"ref": "s"+str(i+2), "text": "Full natal statement "+str(i),
                    "status": "present", "layerId": "base_natal"} for i in range(40)]
    for index, row in enumerate(native):
        statements.append({"ref": "s"+str(index+42), "status": "present", "title": "Транзитный интервал",
                           "aspectScope": "between_layers", "fromLayerId": "transit_period",
                           "toLayerId": "base_natal", "reviewPriority": "required", "sourceRef": row["fact_id"]})
    user["facts"]["statements"] = pack_rows(statements)
    history = [{"role": "assistant" if i % 2 else "user", "content": str(i)+": "+"Полная история без сокращений. "*64} for i in range(24)]
    user["history"] = pack_rows(history)
    value["tp_managed_method_context"]["memory_context"]["items"] = copy.deepcopy(history)
    value["tp_context_encoding"] = "period_lossless_v2"
    value.update(model=MODEL, tp_dialogue_model="gpt_luna",
        tp_context_budget={"contract": budget.CONTRACT, "profile": "natal_transit", "inputBudget": 128000})
    value["messages"][1]["content"] = json.dumps(user, ensure_ascii=False)
    return value


def mutate_user(value, mutate):
    user = json.loads(value["messages"][1]["content"])
    mutate(user)
    value["messages"][1]["content"] = json.dumps(user, ensure_ascii=False)
    return value


class ContextBudgetTests(unittest.TestCase):
    def test_period_facts_v2_is_lossless_and_version_checked(self):
        packed = {"format": "period_facts_v2", "encoding": "period_lossless_v2",
                  "layouts": [["id", "exact_times"]],
                  "rows": [[0, "i1", ["2027-01-01T00:00:00Z"]]], "count": 1, "aliases": [[]],
                  "sourceSha256": budget.digest([{"id": "i1", "exact_times": ["2027-01-01T00:00:00Z"]}])}
        self.assertEqual(budget._unpack_rows(packed),
                         [{"id": "i1", "exact_times": ["2027-01-01T00:00:00Z"]}])
        with self.assertRaisesRegex(budget.ContextBudgetError, "hermes_context_rows_invalid"):
            budget._unpack_rows({**packed, "encoding": "period_lossless_v1"})

    def test_v2_all_470_intervals_natal_and_24_messages_reach_counter_unchanged(self):
        value = v2_body(470)
        before = copy.deepcopy(value)
        counted = []
        receipt = budget.prepare_admission(value, PROFILE, lambda model, messages, fmt: counted.append(copy.deepcopy(messages)) or 128000)
        self.assertTrue(receipt["admitted"])
        self.assertEqual(value, before)
        self.assertEqual(counted, [value["messages"]])
        user = json.loads(counted[0][1]["content"])
        rows = budget._unpack_rows(user["facts"]["statements"])
        self.assertEqual(len(rows), 511)
        self.assertEqual(rows[41]["ref"], "s42")
        self.assertEqual(rows[41]["sourceRef"], "canonical-interval-0")
        self.assertTrue(all(row["reviewPriority"] == "required" for row in rows[41:]))
        self.assertEqual(len(budget._unpack_rows(user["history"])), 24)
        context = budget.prepare_managed_transit_context(value)
        self.assertEqual(len(context.intervals), 470)
        self.assertEqual(len(context.base_refs), 41)

    def test_v2_nested_alias_restores_original_types_precision_and_opaque_fields(self):
        value = v2_body()
        user = json.loads(value["messages"][1]["content"])
        packed = user["context"]["transitPeriod"]["facts"]
        band_column = packed["layouts"][0].index("band") + 1
        for row in packed["rows"]:
            del row[band_column]["exact_times"]
        packed["aliases"] = [[[["band", "exact_times"], ["time_window", "exact_times"]]]]
        original = value["tp_managed_method_context"]["calculation"]["facts"]
        before = copy.deepcopy(packed)
        self.assertEqual(budget._unpack_rows(packed), original)
        self.assertEqual(packed, before)
        value["messages"][1]["content"] = json.dumps(user, ensure_ascii=False)
        self.assertTrue(budget.prepare_admission(value, PROFILE, lambda *args: 500)["admitted"])
        for alias in ([[["band", "exact_times"], ["absent"]]],
                      [[["time_window", "exact_times"], ["band", "exact_times"]]],
                      [[["band", "exact_times"], ["band", "exact_times"]]]):
            broken = copy.deepcopy(packed)
            broken["aliases"] = [alias]
            with self.assertRaises(budget.ContextBudgetError):
                budget._unpack_rows(broken)

    def test_v2_rejects_missing_duplicate_shifted_natal_or_tampered_refs_before_counting(self):
        def change_statements(user, change):
            rows = budget._unpack_rows(user["facts"]["statements"])
            change(rows)
            user["facts"]["statements"] = pack_rows(rows)
        mutations = [
            lambda rows: rows.pop(),
            lambda rows: rows.append(copy.deepcopy(rows[-1])),
            lambda rows: rows[41].update(sourceRef="canonical-interval-1"),
            lambda rows: rows[0].update(sourceRef="canonical-interval-0"),
            lambda rows: rows[41].update(ref="s1"),
            lambda rows: rows[41].pop("sourceRef"),
            lambda rows: rows[41].pop("fromLayerId"),
            lambda rows: rows[41].update(text="duplicated native prose"),
        ]
        for change in mutations:
            with self.subTest(change=change):
                value = mutate_user(v2_body(), lambda user: change_statements(user, change))
                with self.assertRaises(budget.ContextBudgetError):
                    budget.prepare_admission(value, PROFILE, lambda *args: self.fail("must reject before counter"))

    def test_v2_detects_native_digest_tamper_missing_rows_and_encoding_mismatch(self):
        value = v2_body()
        mutations = [
            lambda user: user["context"]["transitPeriod"]["facts"].update(sourceSha256="0"*64),
            lambda user: user["context"]["transitPeriod"]["facts"]["rows"].pop(),
            lambda user: user["context"]["transitPeriod"]["facts"].update(encoding="unknown"),
            lambda user: user["context"]["transitPeriod"]["facts"].pop("sourceSha256"),
        ]
        for mutation in mutations:
            with self.assertRaises(budget.ContextBudgetError):
                budget.prepare_admission(mutate_user(copy.deepcopy(value), mutation), PROFILE, lambda *args: 500)
        value.pop("tp_context_encoding")
        with self.assertRaises(budget.ContextBudgetError):
            budget.prepare_admission(value, PROFILE, lambda *args: 500)

    def test_v2_missing_history_is_rejected_and_overflow_never_changes_the_wire(self):
        value = v2_body(470)
        before = copy.deepcopy(value)
        receipt = budget.prepare_admission(value, PROFILE, lambda *args: 128001)
        self.assertEqual(receipt["reason"], "hermes_context_input_overflow")
        self.assertEqual(value, before)
        def truncate(user):
            history = budget._unpack_rows(user["history"])
            user["history"] = pack_rows(history[:-1])
        with self.assertRaisesRegex(budget.ContextBudgetError, "history_mismatch"):
            budget.prepare_admission(mutate_user(value, truncate), PROFILE, lambda *args: 500)

    def test_actual_repair_wire_is_counted_and_keeps_the_complete_native_json(self):
        from test_hermes_managed_validation import body as transit_body, reply
        original=transit_body(True)
        original.update(model=MODEL,tp_dialogue_model='gpt_luna',response_format={'type':'json_object'},
            tp_context_budget={'contract':budget.CONTRACT,'profile':'natal_transit','inputBudget':128000})
        before=copy.deepcopy(original);agent=SimpleNamespace()
        budget.install_context_budget(agent,original)
        kwargs={'model':MODEL,'messages':original['messages'],'max_tokens':8192,
            'response_format':original['response_format'],'extra_body':{
            'provider':{'only':['openai'],'allow_fallbacks':False,'max_price':budget.PRICE_CAPS[MODEL]},
            'service_tier':'default','reasoning':{'effort':'high'}}}
        real_prepare=budget.prepare_admission;receipts=[];counted=[]
        def prepare(actual):
            def counter(model,messages,fmt):counted.append(copy.deepcopy(messages));return 500
            receipt=real_prepare(actual,PROFILE,counter);receipts.append(receipt);return receipt
        with patch.object(budget,'prepare_admission',side_effect=prepare):
            budget.admit_provider_call(agent,kwargs)
            action,feedback=budget.managed_completion_action(agent,reply('{{s2}} Повторное прохождение.'))
            self.assertEqual(action,'retry')
            kwargs['messages']=[original['messages'][0],{**original['messages'][1],
                'content':original['messages'][1]['content']+'\n\n'+feedback}]
            kwargs['max_tokens']=16384  # Pinned length recovery must not grow the contract.
            budget.admit_provider_call(agent,kwargs)
        self.assertEqual(kwargs['messages'],original['messages']+[{'role':'user','content':feedback}])
        self.assertEqual(kwargs['messages'],counted[-1])
        self.assertEqual(receipts[-1]['messagesSha256'],budget.digest(kwargs['messages']))
        self.assertEqual(kwargs['max_tokens'],8192)
        self.assertEqual(original,before)
        for mutated in (kwargs['messages']+[{'role':'assistant','content':'hidden'}],
                        [original['messages'][0],{'role':'user','content':original['messages'][1]['content']+'\n\n'+feedback+' hidden'}]):
            kwargs['messages']=mutated
            with self.assertRaisesRegex(budget.ContextBudgetError,'repair_messages_changed'):
                budget.admit_provider_call(agent,kwargs)
        self.assertEqual(agent._tp_context_model_calls,2)

    def test_full_128k_needs_window_142592(self):
        receipt = budget.prepare_admission(body(), PROFILE, lambda *args: 128000)
        self.assertTrue(receipt["admitted"])
        self.assertEqual(receipt["marginTokens"], 6400)
        self.assertEqual(receipt["completionTokens"], 8192)
        smaller = copy.deepcopy(PROFILE)
        smaller[MODEL]["contextWindow"] -= 1
        self.assertEqual(budget.prepare_admission(body(), smaller, lambda *args: 128000)["reason"], "hermes_context_window_overflow")

    def test_input_overflow_does_not_drop_a_row(self):
        original = body()
        receipt = budget.prepare_admission(original, PROFILE, lambda *args: 128001)
        self.assertFalse(receipt["admitted"])
        self.assertEqual(original, body())

    def test_reference_counter_does_not_enable_a_route(self):
        profiles = copy.deepcopy(PROFILE)
        profiles[MODEL]["accuracy"] = "reference"
        self.assertFalse(budget.prepare_admission(body(), profiles, lambda *args: 1)["admitted"])

    def test_receipt_is_bound_to_full_messages_and_route(self):
        original = body()
        receipt = budget.prepare_admission(original, PROFILE, lambda *args: 99)
        original["tp_context_admission"] = receipt
        budget.require_admission(original, receipt)
        changed = copy.deepcopy(receipt)
        changed["messagesSha256"] = "0" * 64
        with self.assertRaises(budget.ContextBudgetError):
            budget.require_admission(original, changed)

    def test_each_attempt_checks_actual_final_messages_and_is_bounded(self):
        original = body()
        agent = SimpleNamespace()
        budget.install_context_budget(agent, original)
        kwargs = {"model": MODEL, "messages": original["messages"], "max_tokens": 8192, "extra_body": {"provider": {"only": ["openai"], "allow_fallbacks": False, "max_price": budget.PRICE_CAPS[MODEL]}, "service_tier": "default", "reasoning": {"effort": "high"}}}
        receipt = budget.prepare_admission(original, PROFILE, lambda *args: 77)
        with patch.object(budget, "prepare_admission", return_value=receipt) as count:
            for _ in range(3):
                budget.admit_provider_call(agent, kwargs)
            with self.assertRaises(budget.ContextBudgetError):
                budget.admit_provider_call(agent, kwargs)
            self.assertEqual(count.call_count, 3)
        # Manual continuation is a new operation, not the fourth automatic call.
        fresh = SimpleNamespace()
        budget.install_context_budget(fresh, original)
        with patch.object(budget, "prepare_admission", return_value=receipt):
            budget.admit_provider_call(fresh, kwargs)

    def test_hidden_context_mutation_is_rejected_before_provider(self):
        original = body()
        agent = SimpleNamespace()
        budget.install_context_budget(agent, original)
        with self.assertRaises(budget.ContextBudgetError):
            budget.admit_provider_call(agent, {"model": MODEL, "max_tokens": 8192,
                "messages": original["messages"] + [{"role": "user", "content": "hidden"}]})
        self.assertEqual(agent._tp_context_model_calls, 0)

    def test_native_accounting_session_must_match_the_isolated_operation(self):
        original = body()
        agent = SimpleNamespace(session_id="synthetic-operation")
        budget.install_context_budget(agent, original)
        kwargs = {"model": MODEL, "messages": original["messages"], "max_tokens": 8192,
            "extra_body": {**agent.request_overrides["extra_body"], "provider": {
                "only": ["openai"], "allow_fallbacks": False, "max_price": budget.PRICE_CAPS[MODEL]},
                "service_tier": "default", "reasoning": {"effort": "high"},
                "session_id": agent.session_id}}
        with patch.object(budget, "prepare_admission", return_value={"admitted": True}):
            budget.admit_provider_call(agent, kwargs)
        kwargs["extra_body"]["session_id"] = "another-operation"
        with self.assertRaisesRegex(budget.ContextBudgetError, "provider_route_changed"):
            budget.admit_provider_call(agent, kwargs)
        self.assertEqual(agent._tp_context_model_calls, 1)

    def test_agent_uses_the_admitted_window_without_compacting(self):
        original = body()
        original["tp_context_admission"] = {"contextWindow": 1050000}
        agent = SimpleNamespace(context_compressor=SimpleNamespace(context_length=8192))
        budget.install_context_budget(agent, original)
        self.assertEqual(agent.context_compressor.context_length, 1050000)
        self.assertFalse(agent.compression_enabled)
        self.assertEqual(agent.request_overrides["extra_body"]["provider"]["max_price"], budget.PRICE_CAPS[MODEL])

    def test_committed_route_evidence_is_complete_and_tamper_checked(self):
        from pathlib import Path
        with patch.object(budget, "CERTIFICATES", Path("registry/context_budget_routes.json")):
            budget._load_certificates.cache_clear()
            profiles = budget._load_certificates()
            self.assertEqual(profiles[MODEL]["accuracy"], "calibrated")
            self.assertEqual(profiles["deepseek/deepseek-v4-flash"]["framingUpperCorrection"], 128)
        budget._load_certificates.cache_clear()

    def test_valid_length_part_stops_automatic_replacement(self):
        self.assertTrue(budget.readable_managed_part('{"mode":"interpretation","paragraphs":[{"text":"{{s1}}. Возможная тема.","refs":["s1"]}]}', True))
        self.assertFalse(budget.readable_managed_part('{"mode":"interpretation","paragraphs":[', True))
        self.assertFalse(budget.readable_managed_part('', True))
        self.assertTrue(budget.readable_managed_part('Законченная часть ответа.', False))


if __name__ == "__main__":
    unittest.main()
