"""Lossless wire rows shared by admission and managed-answer validation.

V1 remains readable for already deployed Cabinet clients. V2 requires a digest
of every reconstructed native row and explicit ordered field aliases.
"""
from __future__ import annotations

import copy
import hashlib
import re

if __package__:
    from .durable_accounting import canonical_request_payload
else:
    from hermes_durable_accounting import canonical_request_payload


class ContextRowsError(ValueError):
    pass


def _invalid():
    raise ContextRowsError("hermes_context_rows_invalid")


def _path(value, path):
    current = value
    for key in path:
        if isinstance(current, dict) and isinstance(key, str) and key in current:
            current = current[key]
        elif isinstance(current, list) and type(key) is int and 0 <= key < len(current):
            current = current[key]
        else:
            _invalid()
    return current


def _valid_path(path):
    return isinstance(path, list) and bool(path) and all(
        isinstance(key, str) or (type(key) is int and key >= 0) for key in path)


def unpack_context_rows(value):
    if isinstance(value, list) and all(isinstance(row, dict) for row in value):
        return value
    if not isinstance(value, dict) or value.get("format") not in {"typed_rows_v1", "typed_rows_v2", "period_facts_v2"}:
        _invalid()
    period = value["format"] == "period_facts_v2"
    typed_v2 = value["format"] == "typed_rows_v2"
    v2 = period or typed_v2
    layouts, rows = value.get("layouts"), value.get("rows")
    if (not isinstance(layouts, list) or not isinstance(rows, list)
            or type(value.get("count")) is not int or value["count"] != len(rows)):
        _invalid()
    if any(not isinstance(columns, list) or any(not isinstance(c, str) for c in columns)
           or len(columns) != len(set(columns)) for columns in layouts):
        _invalid()
    if period and (value.get("encoding") != "period_lossless_v2"
                   or not isinstance(value.get("aliases"), list) or len(value["aliases"]) != len(layouts)):
        _invalid()
    if v2 and (not isinstance(value.get("sourceSha256"), str)
               or not re.fullmatch(r"[0-9a-f]{64}", value["sourceSha256"])):
        _invalid()
    dictionaries = value.get("dictionaries")
    if "dictionaries" in value:
        if (not v2 or not isinstance(dictionaries, list) or len(dictionaries) != len(layouts)
                or any(not isinstance(dictionary, dict) for dictionary in dictionaries)):
            _invalid()
        for layout, dictionary in enumerate(dictionaries):
            for column, entries in dictionary.items():
                if (not isinstance(column, str) or not re.fullmatch(r"0|[1-9][0-9]*", column)
                        or len(column) > len(str(len(layouts[layout]))) or int(column) >= len(layouts[layout]) or not isinstance(entries, list) or not entries
                        or len({canonical_request_payload(entry) for entry in entries}) != len(entries)):
                    _invalid()
    elif typed_v2:
        _invalid()
    derived = value.get("derivedFields")
    if "derivedFields" in value:
        if not typed_v2 or not isinstance(derived, list) or len(derived) != len(layouts):
            _invalid()
        for layout, rules in enumerate(derived):
            if (not isinstance(rules, list) or len(rules) > 1 or any(
                    rule != {"field": "text", "from": "phrase", "prefix": "Натал: "}
                    or "text" in layouts[layout] or "phrase" not in layouts[layout] or "layerId" not in layouts[layout]
                    for rule in rules)):
                _invalid()
    result = []
    for row in rows:
        if not isinstance(row, list) or not row or type(row[0]) is not int or not 0 <= row[0] < len(layouts):
            _invalid()
        columns = layouts[row[0]]
        if (not isinstance(columns, list) or len(columns) != len(row) - 1
                or any(not isinstance(c, str) for c in columns) or len(columns) != len(set(columns))):
            _invalid()
        cells = copy.deepcopy(row[1:])
        if dictionaries is not None:
            for column, entries in dictionaries[row[0]].items():
                index = cells[int(column)]
                if type(index) is not int or not 0 <= index < len(entries):
                    _invalid()
                cells[int(column)] = copy.deepcopy(entries[index])
        decoded = dict(zip(columns, cells))
        if derived is not None:
            for rule in derived[row[0]]:
                if decoded.get("layerId") != "base_natal" or "text" in decoded or not isinstance(decoded.get("phrase"), str):
                    _invalid()
                decoded["text"] = "Натал: " + decoded["phrase"]
        if period:
            aliases = value["aliases"][row[0]]
            if not isinstance(aliases, list):
                _invalid()
            for alias in aliases:
                if (not isinstance(alias, list) or len(alias) != 2
                        or not _valid_path(alias[0]) or not _valid_path(alias[1])):
                    _invalid()
                target, source = alias
                parent = _path(decoded, target[:-1])
                if not isinstance(parent, dict) or not isinstance(target[-1], str) or target[-1] in parent:
                    _invalid()
                parent[target[-1]] = copy.deepcopy(_path(decoded, source))
        result.append(decoded)
    digest = value.get("sourceSha256")
    if digest is not None and hashlib.sha256(canonical_request_payload(result).encode("utf-8")).hexdigest() != digest:
        raise ContextRowsError("hermes_context_rows_digest_mismatch")
    return result
