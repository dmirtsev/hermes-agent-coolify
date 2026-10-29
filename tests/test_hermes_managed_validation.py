import copy
import json
import unittest

from hermes_managed_validation import (
    ManagedValidationContextError, managed_transit_issues,
    managed_transit_repair_prompt, prepare_managed_transit_context,
)


def native(ident, planet, point, instants):
    return {"id": ident, "transit_planet": {"code": planet},
            "natal_point": {"code": point}, "aspect": {"code": "opposition"},
            "time_window": {"exact_times": [{"at_utc": time} for time in instants]}}


def body(packed=False):
    bands = [native("b1", "mercury", "true_node", ["2026-11-01T09:47:00Z"]),
             native("b2", "neptune", "neptune", ["2026-11-23T00:17:00Z", "2027-01-01T15:10:00Z"]),
             native("b3", "mercury", "true_node", ["2026-11-15T14:00:00Z"])]
    calculation = {"contextType": "period_context_v1", "calculationId": "saved-period-calculation",
                   "resultId": "saved-period-result", "facts": bands, "factRefs": [b["id"] for b in bands]}
    statements = [{"ref": "s1", "text": "Натал: Сатурн: дом 10.", "layerId": "base_natal", "status": "present"}]
    for i in range(len(bands)):
        statements.append({"ref": "s"+str(i+2), "text": "Транзит: нативный интервал.", "status": "present",
                           "aspectScope": "between_layers", "fromLayerId": "transit_period", "toLayerId": "base_natal"})
    facts = {"source": {"calculationId": calculation["calculationId"], "resultId": calculation["resultId"]},
             "layers": [{"layerId": "base_natal", "chartType": "natal", "relationMethod": "base"}],
             "statements": statements}
    period = copy.deepcopy(calculation)
    if packed:
        def pack(rows):
            keys = list(rows[0])
            return {"format": "typed_rows_v1", "count": len(rows), "layouts": [keys],
                    "rows": [[0]+[row[key] for key in keys] for row in rows]}
        # Heterogeneous statement layouts, as on the actual admitted wire.
        facts["statements"] = {"format": "typed_rows_v1", "count": len(statements),
            "layouts": [list(statements[0]), list(statements[1])],
            "rows": [[0]+list(statements[0].values())]+[[1]+list(row.values()) for row in statements[1:]]}
        period["facts"] = pack(bands)
    user = {"natalSource": {"resultId": "main"}, "facts": facts,
            "context": {"source": {"resultId": "saved-natal-result"}, "transitPeriod": period},
            "history": [{"role": "user", "content": "Complete original conversation"}]}
    method = {"profile": {}, "selection": {}, "resolution": {}, "knowledge": {}}
    user['context']['method'] = method
    return {"tp_answer_format": "hermes_managed_v1", "tp_execution_mode": "interpretation_facts_v1",
            "tp_dialogue_mode": "standard", "tp_managed_method_context": {
                **method, "calculation": calculation, "memory_context": {"items": []}},
            "messages": [{"role": "system", "content": "Original full system"},
                         {"role": "user", "content": json.dumps(user, ensure_ascii=False)}]}


def reply(text="{{s2}}. Этот фактор помогает внимательнее относиться к договорённостям.", refs=None):
    return json.dumps({"mode": "interpretation", "paragraphs": [{"text": text, "refs": ["s2"] if refs is None else refs}]}, ensure_ascii=False)


class ManagedTransitValidationTests(unittest.TestCase):
    def test_synthesis_requested_by_system_survives_automatic_repair(self):
        value = body(True)
        value["messages"][0]["content"] += ' Use presentation="period_synthesis_v1" for period synthesis.'
        context = prepare_managed_transit_context(value)
        self.assertTrue(context.synthesis)
        prompt = managed_transit_repair_prompt(["unmarked_native_precision"], context)
        self.assertIn("presentation=period_synthesis_v1", prompt)
        self.assertIn("Preserve the requested period synthesis and depth", prompt)
        self.assertNotIn("Каждый абзац с расчётным основанием начинай", prompt)
        skeleton = json.JSONDecoder().raw_decode(prompt[prompt.index('{"mode": "interpretation"'):])[0]
        self.assertEqual(skeleton["presentation"], "period_synthesis_v1")
        self.assertNotIn("{{", skeleton["paragraphs"][0]["text"])
        self.assertEqual(managed_transit_issues(json.dumps(skeleton, ensure_ascii=False), context), [])
        skeleton["paragraphs"][0]["text"] += " Возможный акцент 15.11.2026."
        self.assertIn("unmarked_native_precision", managed_transit_issues(json.dumps(skeleton, ensure_ascii=False), context))

    def test_user_text_cannot_enable_synthesis_repair(self):
        value = body()
        value["messages"][1]["content"] = value["messages"][1]["content"].replace("Complete original conversation", "period_synthesis_v1")
        context = prepare_managed_transit_context(value)
        self.assertFalse(context.synthesis)
        prompt = managed_transit_repair_prompt(["unmarked_native_precision"], context)
        self.assertIn("Каждый абзац с расчётным основанием начинай", prompt)
        self.assertNotIn('"presentation": "period_synthesis_v1"', prompt)

    def test_feedback_uses_real_native_ref_and_valid_russian_skeleton(self):
        context = prepare_managed_transit_context(body())
        prompt = managed_transit_repair_prompt(["unmarked_native_precision"], context)
        self.assertIn("без заголовка", prompt)
        self.assertIn("без даты", prompt)
        self.assertIn("«прохождение»", prompt)
        self.assertNotIn('"refs": ["sN"]', prompt)
        start = prompt.index('{"mode": "interpretation"')
        skeleton, _ = json.JSONDecoder().raw_decode(prompt[start:])
        self.assertEqual(managed_transit_issues(json.dumps(skeleton), context), [])

    def test_conservative_heading_and_cross_sentence_repeat_triggers_are_diagnosable(self):
        self.assertIn("unmarked_native_precision", managed_transit_issues(
            reply("1 ноября: два практических вывода. {{s2}}"), body()))
        self.assertIn("unprovided_repeat_pass", managed_transit_issues(
            reply("Повторно проверьте документы. Это прохождение просит паузы. {{s2}}"), body()))

    def test_full_native_context_and_core_scope_are_not_confused_with_saved_ids(self):
        for packed in (False, True):
            original = body(packed)
            before = copy.deepcopy(original)
            context = prepare_managed_transit_context(original)
            self.assertEqual(len(context.refs), 4)
            self.assertEqual(len(context.intervals), 3)
            self.assertEqual(managed_transit_issues(reply(), context), [])
            self.assertEqual(original, before)

    def test_unmarked_motion_aspect_dayparts_and_false_repeat_are_all_detected(self):
        text = "{{s2}}. Повторное прохождение: ретроградный Меркурий в конфликте с узлом. Утро и середина дня. Луна в секстиле с Венерой."
        issues = managed_transit_issues(reply(text), body())
        for code in ("unprovided_repeat_pass", "unmarked_native_motion", "unprovided_day_segment", "unmarked_native_calculation"):
            self.assertIn(code, issues)

    def test_repeat_requires_every_cited_identity_to_have_two_distinct_native_instants(self):
        cases = [(["s2"], False), (["s3"], True), (["s2", "s3"], False), (["s2", "s4"], True)]
        for refs, allowed in cases:
            text = " ".join("{{"+ref+"}}" for ref in refs)+" Повторное прохождение требует внимания."
            issues = managed_transit_issues(reply(text, refs), body())
            self.assertEqual("unprovided_repeat_pass" not in issues, allowed, refs)

    def test_repeat_cannot_borrow_ref_only_marker_only_or_another_paragraph(self):
        for text, refs in [("{{s2}} Повторное прохождение.", ["s2", "s3"]),
                           ("{{s3}} Повторное прохождение.", ["s2"]),
                           ("Повторное прохождение.", ["s3"])]:
            self.assertIn("unprovided_repeat_pass", managed_transit_issues(reply(text, refs), body()))
        content = json.dumps({"mode": "interpretation", "paragraphs": [
            {"text": "{{s3}}", "refs": ["s3"]}, {"text": "Повторное прохождение.", "refs": []}]})
        self.assertIn("unprovided_repeat_pass", managed_transit_issues(content, body()))

    def test_duplicate_exact_instant_is_not_two_passes(self):
        original = body()
        calculation = original["tp_managed_method_context"]["calculation"]
        calculation["facts"][2]["time_window"]["exact_times"] = copy.deepcopy(calculation["facts"][0]["time_window"]["exact_times"])
        user = json.loads(original["messages"][1]["content"])
        user["context"]["transitPeriod"] = copy.deepcopy(calculation)
        original["messages"][1]["content"] = json.dumps(user)
        self.assertIn("unprovided_repeat_pass", managed_transit_issues(reply("{{s2}} {{s4}} Повторное прохождение.", ["s2", "s4"]), original))

    def test_theory_does_not_bypass_temporal_repeat_or_motion_checks(self):
        for prose, code in [
            ("Если ретроградный Меркурий требует внимания.", "unmarked_native_motion"),
            ("Если интервал начинается сегодня.", "unmarked_interval_boundary"),
            ("Если сегодня заканчивается интервал.", "unmarked_interval_boundary"),
            ("Если утром стоит подумать.", "unprovided_day_segment"),
            ("Если повторное прохождение происходит.", "unprovided_repeat_pass"),
            ("Если 1 ноября стоит подумать.", "unmarked_native_precision"),
        ]:
            self.assertIn(code, managed_transit_issues(reply("{{s2}} "+prose), body()))
        self.assertIn('unmarked_native_calculation',managed_transit_issues(reply(
            'Если это пример. Транзит Меркурия в квадрате к Луне.'),body()))

    def test_inflected_motion_dayparts_and_numeric_dates(self):
        for prose in ("при прямом движении", "прямого движения", "в обратном движении", "стационарное положение"):
            self.assertIn("unmarked_native_motion", managed_transit_issues(reply("{{s2}} "+prose), body()))
        for prose in ("первой половине дня", "вторая половина дня", "середина дня", "днём", "ночью", "вечером"):
            self.assertIn("unprovided_day_segment", managed_transit_issues(reply("{{s2}} "+prose), body()))
        for prose in ("01.11.2026", "2026-11-01", "1 ноября", "12:30", "1°", "орбисом 2°"):
            self.assertIn("unmarked_native_precision", managed_transit_issues(reply("{{s2}} "+prose), body()))

    def test_cabinet_retains_authority_for_explicit_natal_calculations(self):
        self.assertEqual(managed_transit_issues(reply("{{s1}} Натальный Сатурн в десятом доме служит опорой.", ["s1"]), body()), [])
        self.assertEqual(managed_transit_issues(reply("В общей теории Луна в секстиле с Венерой означает пример.", []), body()), [])
        self.assertEqual(managed_transit_issues(reply("Спасибо, обсудим дальше.", []), body()), [])

    def test_structure_and_references(self):
        for content in ("{", "[]", "null", "{}", json.dumps({"mode": "interpretation", "paragraphs": []})):
            self.assertTrue(managed_transit_issues(content, body()))
        for content in (reply("{{s99}}", ["s99"]), reply("{{s2}}", []), reply("{{foreign}}", ["s2"]),
                        json.dumps({"mode": "facts", "items": [{"refs": [{}]}]})):
            self.assertIn("managed_references_invalid", managed_transit_issues(content, body()))
        self.assertEqual(managed_transit_issues(json.dumps({"mode": "facts", "items": [{"refs": ["s2"]}]}), body()), [])

    def test_nonperiod_and_free_dialogue_unchanged(self):
        for mode in ("free_dialogue", {"code": "free_dialogue"}):
            original = body(); original["tp_dialogue_mode"] = mode; original["messages"] = []
            self.assertIsNone(prepare_managed_transit_context(original))
            self.assertEqual(managed_transit_issues("not json", original), [])
        self.assertIsNone(prepare_managed_transit_context({}))

    def test_recognized_bad_context_rejected_before_model_dispatch(self):
        original = body(); original["stream"] = True
        with self.assertRaises(ManagedValidationContextError): prepare_managed_transit_context(original)
        original = body(); original["tp_managed_method_context"]["calculation"]["facts"][0]["id"] = "wrong"
        with self.assertRaises(ManagedValidationContextError): prepare_managed_transit_context(original)
        original = body(True); user = json.loads(original["messages"][1]["content"])
        user["facts"]["statements"]["rows"][0].append("extra")
        original["messages"][1]["content"] = json.dumps(user)
        with self.assertRaises(ManagedValidationContextError): prepare_managed_transit_context(original)

    def test_feedback_does_not_contain_rejected_draft_or_replace_original_context(self):
        original = body(); before = copy.deepcopy(original)
        issues = managed_transit_issues(reply("{{s2}} Повторное прохождение."), original)
        prompt = managed_transit_repair_prompt(issues)
        self.assertIn("unprovided_repeat_pass", prompt)
        self.assertIn("full published conversation", prompt)
        self.assertIn("SAME original question", prompt)
        self.assertNotIn("Меркурий", prompt)
        self.assertEqual(original, before)


if __name__ == "__main__":
    unittest.main()
