"""Safe, deterministic diagnostics for rejected grounded narration plans.

This module is deliberately a second pass.  It never widens the acceptance
contract in :mod:`grounded_narrative`; it only explains why that contract was
not met. Only code/path are public; details belong in the private sidecar.
"""
import json
import math
from collections import Counter

MAX_BYTES = 32_000


def _result(code, path=None, **details):
    # ``code`` and ``path`` are the only fields suitable for an HTTP response.
    # ``details`` is retained for the private diagnostic sidecar.
    out = {"code": code, "path": path or "$"}
    if details:
        out["details"] = details
    return out


def _duplicate_hook(duplicates):
    def hook(pairs):
        seen = {}
        for key, value in pairs:
            if key in seen:
                duplicates.append(key)
            seen[key] = value
        return seen
    return hook


def _actual(value):
    # The raw plan preserves the exact value. Avoid recursively serializing a
    # model-controlled container again inside the reason envelope.
    if isinstance(value, (dict, list)):
        return {"type":type(value).__name__, "length":len(value)}
    return value


def diagnose_plan(raw_text, catalogue):
    """Return a stable reason for a plan rejected by ``parse_plan``.

    Only strings match the parser's input contract. Details may contain
    model-supplied values and must never be included in the HTTP response.
    """
    if not isinstance(raw_text, str):
        return _result("invalid_text_type", "$", actual=type(raw_text).__name__)
    if not raw_text:
        return _result("empty_text", "$")
    try:
        encoded = raw_text.encode("utf-8")
    except UnicodeEncodeError:
        return _result("invalid_unicode", "$")
    if len(encoded) > MAX_BYTES:
        return _result("plan_too_large", "$", actual_bytes=len(encoded), max_bytes=MAX_BYTES)

    duplicates = []
    class _NonFinite(ValueError):
        pass
    def finite_float(text):
        value = float(text)
        if not math.isfinite(value):
            raise _NonFinite()
        return value
    try:
        value = json.loads(raw_text, object_pairs_hook=_duplicate_hook(duplicates),
                           parse_float=finite_float,
                           parse_constant=lambda x: (_ for _ in ()).throw(_NonFinite(x)))
    except json.JSONDecodeError as exc:
        return _result("malformed_json", "$", line=exc.lineno, column=exc.colno,
                       position=exc.pos)
    except _NonFinite:
        return _result("nonfinite_json", "$")
    except RecursionError:
        return _result("json_nesting_limit", "$")
    except ValueError:
        return _result("invalid_json_value", "$")
    if duplicates:
        # Arbitrary key names stay private, just like the raw plan.
        return _result("duplicate_json_key", "$", keys=sorted(set(k for k in duplicates if isinstance(k, str)))[:32])
    if not isinstance(value, dict):
        return _result("wrong_top_level_type", "$", actual=type(value).__name__)

    try:
        from .grounded_narrative import PLAN_SCHEMA, default_plan
    except ImportError:
        from grounded_narrative import PLAN_SCHEMA, default_plan
    try:
        expected = default_plan(catalogue)
    except Exception:
        return _result("catalogue_unavailable", "$")

    keys = set(value)
    extra = sorted(keys - set(expected))
    missing = sorted(set(expected) - keys)
    if missing:
        return _result("missing_field", "$", fields=missing, extra_fields=extra)
    if extra:
        return _result("extra_field", "$", fields=extra)
    if value.get("schema_version") != PLAN_SCHEMA:
        return _result("wrong_schema_version", "$.schema_version",
                       expected=PLAN_SCHEMA, actual=_actual(value.get("schema_version")))
    if value.get("catalogue_hash") != expected["catalogue_hash"]:
        return _result("catalogue_hash_mismatch", "$.catalogue_hash",
                       expected=expected["catalogue_hash"], actual=_actual(value.get("catalogue_hash")))

    for field in ("group_order", "event_order"):
        actual = value.get(field)
        path = "$." + field
        if not isinstance(actual, list):
            return _result("wrong_order_type", path, actual=type(actual).__name__)
        if any(not isinstance(item, str) for item in actual):
            return _result("wrong_order_element_type", path,
                           indices=[i for i, item in enumerate(actual) if not isinstance(item, str)])
        expected_ids = list(expected[field])
        expected_set = set(expected_ids)
        actual_set = set(actual)
        duplicates_ids = sorted([x for x, n in Counter(actual).items() if n > 1])
        unknown = sorted(actual_set - expected_set)
        missing_ids = sorted(expected_set - actual_set)
        if duplicates_ids:
            return _result("duplicate_order_id", path, ids=duplicates_ids,
                           unknown_ids=unknown, missing_ids=missing_ids)
        if unknown:
            return _result("unknown_order_id", path, ids=unknown, missing_ids=missing_ids)
        if missing_ids:
            return _result("missing_order_id", path, ids=missing_ids[:32])
        if len(actual) != len(expected_ids):
            return _result("wrong_order_length", path, expected=len(expected_ids), actual=len(actual))
    # The plan is structurally valid.  The caller may have rejected it while
    # rendering; retain a neutral reason rather than inventing one here.
    return _result("plan_validation_failed", "$")

