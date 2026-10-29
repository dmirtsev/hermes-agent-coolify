import copy
import json
import unittest

from hermes_context_rows import ContextRowsError, unpack_context_rows
from hermes_context_budget import digest, prepare_admission, ContextBudgetError
from test_hermes_context_budget import v2_body, pack_rows, MODEL, PROFILE


def dictionary_rows(source, derive=False):
    """Independent wire-fixture writer; production Hermes only decodes."""
    layouts, rules, rows = [], [], []
    for record in source:
        item = copy.deepcopy(record)
        derived = []
        if derive and item.get("layerId") == "base_natal" and isinstance(item.get("phrase"), str) and item.get("text") == "Натал: " + item["phrase"]:
            del item["text"]
            derived = [{"field": "text", "from": "phrase", "prefix": "Натал: "}]
        columns = sorted(item)
        pair = (columns, derived)
        existing = next((i for i in range(len(layouts)) if (layouts[i], rules[i]) == pair), None)
        if existing is None:
            existing = len(layouts)
            layouts.append(columns)
            rules.append(derived)
        rows.append([existing]+[item[key] for key in columns])
    dictionaries = [{} for _ in layouts]
    for layout, columns in enumerate(layouts):
        group = [row for row in rows if row[0] == layout]
        for column in range(len(columns)):
            values, indices = [], []
            for row in group:
                value = row[column+1]
                # Type-sensitive: Python bool/int equality must not conflate JSON.
                key = json.dumps(value, ensure_ascii=False, sort_keys=True)
                keys = [json.dumps(v, ensure_ascii=False, sort_keys=True) for v in values]
                if key not in keys:
                    values.append(value)
                    keys.append(key)
                indices.append(keys.index(key))
            if len(values) == len(group):
                continue
            dictionaries[layout][str(column)] = values
            for row, index in zip(group, indices):
                row[column+1] = index
    return {"format": "typed_rows_v2", "layouts": layouts, "rows": rows, "count": len(source),
            "dictionaries": dictionaries, "derivedFields": rules, "sourceSha256": digest(source)}


class SharedContextRowsTests(unittest.TestCase):
    def test_types_order_optional_fields_and_natal_prefix_roundtrip(self):
        source = [{"ref": "s1", "layerId": "base_natal", "phrase": "Полный факт", "text": "Натал: Полный факт", "value": False},
                  {"ref": "s2", "layerId": "base_natal", "phrase": "Полный факт", "text": "Натал: Полный факт", "value": 0},
                  {"ref": "s3", "layerId": "base_natal", "phrase": "Другой факт", "text": "Натал: Другой факт", "value": [None, True, {"precision": 0.40000123456789}]},
                  {"ref": "s4", "text": "Independent original", "value": "0"}]
        packed = dictionary_rows(source, True)
        before = copy.deepcopy(packed)
        decoded = unpack_context_rows(packed)
        self.assertEqual(decoded, source)
        self.assertIs(type(decoded[0]["value"]), bool)
        self.assertIs(type(decoded[1]["value"]), int)
        self.assertEqual(packed, before)
        self.assertNotIn("text", packed["layouts"][0])

    def test_dictionary_indices_are_unambiguous_even_for_integer_values(self):
        source = [{"number": 100, "raw": "a"}, {"number": 100, "raw": "b"}, {"number": -1, "raw": "c"}]
        packed = dictionary_rows(source)
        self.assertEqual(unpack_context_rows(packed), source)
        column = packed["layouts"][0].index("number")
        for index in (-1, 2, 0.5, True, "0", None):
            bad = copy.deepcopy(packed)
            bad["rows"][0][column+1] = index
            with self.subTest(index=index), self.assertRaises(ContextRowsError):
                unpack_context_rows(bad)

    def test_dictionary_headers_shapes_and_duplicates_fail_closed(self):
        packed = dictionary_rows([{"value": {"x": [True, None]}}, {"value": {"x": [True, None]}}])
        variants = [None, [], [{} , {}], [{"01": [1]}], [{"-1": [1]}], [{"1": [1]}], [{"0": []}], [{"0": [1, 1]}], [{"0": "wrong"}], [{"0": [0, 0.0]}], [{"1٢": [1]}]]
        for dictionary in variants:
            bad = {**packed, "dictionaries": dictionary}
            with self.subTest(dictionary=dictionary), self.assertRaises(ContextRowsError):
                unpack_context_rows(bad)
        bad = copy.deepcopy(packed)
        del bad["dictionaries"]
        with self.assertRaises(ContextRowsError):
            unpack_context_rows(bad)

    def test_only_exact_constant_natal_derivation_is_allowed(self):
        source = [{"layerId": "base_natal", "phrase": "Exact", "text": "Натал: Exact"}]*2
        packed = dictionary_rows(source, True)
        for rule in ({"field": "text", "from": "phrase", "prefix": "Invented: "},
                     {"field": "text", "from": "text", "prefix": "Натал: "},
                     {"field": "phrase", "from": "text", "prefix": "Натал: "},
                     {"field": "text", "from": "phrase", "prefix": "Натал: ", "extra": True}):
            bad = copy.deepcopy(packed)
            bad["derivedFields"][0] = [rule]
            with self.subTest(rule=rule), self.assertRaises(ContextRowsError):
                unpack_context_rows(bad)
        for altered in ([{"layerId": "other", "phrase": "Exact", "text": "Натал: Exact"}]*2,
                        [{"layerId": "base_natal", "phrase": 42, "text": "Натал: 42"}]*2):
            bad = {"format": "typed_rows_v2", "layouts": [["layerId", "phrase"]],
                   "rows": [[0, row["layerId"], row["phrase"]] for row in altered],
                   "count": len(altered), "dictionaries": [{}], "sourceSha256": digest(altered),
                   "derivedFields": [[{"field": "text", "from": "phrase", "prefix": "Натал: "}]]}
            with self.assertRaises(ContextRowsError):
                unpack_context_rows(bad)

    def test_hash_tamper_target_present_and_v1_dictionary_smuggling_are_rejected(self):
        packed = dictionary_rows([{"layerId": "base_natal", "phrase": "Exact", "text": "Натал: Exact"}]*2, True)
        with self.assertRaises(ContextRowsError):
            unpack_context_rows({**packed, "sourceSha256": "0"*64})
        with self.assertRaises(ContextRowsError):
            unpack_context_rows({**packed, "format": "typed_rows_v1"})
        plain = dictionary_rows([{"layerId": "base_natal", "phrase": "Exact", "text": "Натал: Exact"}]*2)
        plain["derivedFields"] = [[{"field": "text", "from": "phrase", "prefix": "Натал: "}]]
        with self.assertRaises(ContextRowsError):
            unpack_context_rows(plain)

    def test_native_optional_dictionaries_keep_old_period_v2_compatible(self):
        value = v2_body()
        user = json.loads(value["messages"][1]["content"])
        original = user["context"]["transitPeriod"]["facts"]
        rows = unpack_context_rows(original)
        self.assertEqual(unpack_context_rows(original), rows)
        packed = dictionary_rows(rows)
        packed.update(format="period_facts_v2", encoding="period_lossless_v2", aliases=[[] for _ in packed["layouts"]])
        del packed["derivedFields"]
        self.assertEqual(unpack_context_rows(packed), rows)
        packed["derivedFields"] = [[] for _ in packed["layouts"]]
        with self.assertRaises(ContextRowsError):
            unpack_context_rows(packed)

    def test_470_rows_all_statements_history_and_existing_admission_contract(self):
        value = v2_body(470)
        user = json.loads(value["messages"][1]["content"])
        native = unpack_context_rows(user["context"]["transitPeriod"]["facts"])
        original_statements = unpack_context_rows(user["facts"]["statements"])
        for statement in original_statements[:41]:
            statement["phrase"] = statement["text"]
            statement["text"] = "Натал: " + statement["phrase"]
        packed = dictionary_rows(native)
        packed.update(format="period_facts_v2", encoding="period_lossless_v2", aliases=[[] for _ in packed["layouts"]])
        del packed["derivedFields"]
        user["context"]["transitPeriod"]["facts"] = packed
        user["facts"]["statements"] = dictionary_rows(original_statements, True)
        history_before = copy.deepcopy(user["history"])
        value["messages"][1]["content"] = json.dumps(user, ensure_ascii=False)
        receipt = prepare_admission(value, PROFILE, lambda *args: 128000)
        self.assertTrue(receipt["admitted"])
        self.assertEqual(user["history"], history_before)
        self.assertEqual(unpack_context_rows(user["facts"]["statements"]), original_statements)
        self.assertEqual(unpack_context_rows(packed), native)
        self.assertEqual(len(native), 470)
        self.assertEqual(len(original_statements), 511)
