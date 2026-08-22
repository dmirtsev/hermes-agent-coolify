"""Fail-closed execution guard for versioned astrology readings.

Cabinet resolves and retrieves the exact method from TP Knowledge.  This seam
validates that closed package before Hermes sees it, injects one bounded prompt
contract, and returns a receipt that Cabinet can persist with the turn.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any


SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
SEMVER_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$")
PROMPT_CONTRACT_VERSION = "1.0.0"
MAX_CONTEXT_BYTES = 180_000


class VersionedMethodContextError(ValueError):
    pass


@dataclass(frozen=True)
class VersionedMethodGuard:
    prompt: str
    receipt: dict[str, Any]


def _require_mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise VersionedMethodContextError(f"{label} must be an object")
    return value


def _require_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise VersionedMethodContextError(f"{label} is required")
    return value.strip()


def _require_unique_strings(value: Any, label: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise VersionedMethodContextError(f"{label} must contain strings")
    if len(set(value)) != len(value):
        raise VersionedMethodContextError(f"{label} contains duplicates")
    return value


def _method_ref(value: Any, label: str) -> tuple[str, str, str]:
    ref = _require_mapping(value, label)
    family_id = _require_string(ref.get("family_id"), f"{label}.family_id")
    version = _require_string(ref.get("version"), f"{label}.version")
    content_hash = _require_string(ref.get("content_hash"), f"{label}.content_hash")
    if not SEMVER_RE.fullmatch(version) or not SHA256_RE.fullmatch(content_hash):
        raise VersionedMethodContextError(f"{label} has invalid version or hash")
    return family_id, version, content_hash


def prepare_versioned_method_context(value: Any) -> VersionedMethodGuard | None:
    if value is None:
        return None
    context = _require_mapping(value, "tp_reading_context")
    encoded = json.dumps(context, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if len(encoded.encode("utf-8")) > MAX_CONTEXT_BYTES:
        raise VersionedMethodContextError("tp_reading_context is too large")
    if context.get("schema_version") != 1:
        raise VersionedMethodContextError("unsupported tp_reading_context schema")

    request_id = _require_string(context.get("request_id"), "request_id")
    domain = _require_string(context.get("domain"), "domain")
    task_type = _require_string(context.get("task_type"), "task_type")
    if (domain, task_type) != ("transit", "period_guidance"):
        raise VersionedMethodContextError("unsupported astrology reading domain/task")

    profile = _require_mapping(context.get("profile"), "profile")
    mixing_policy = _require_string(profile.get("mixing_policy"), "profile.mixing_policy")
    if mixing_policy not in {"forbidden", "explicit_compare"}:
        raise VersionedMethodContextError("unreviewed method synthesis is forbidden")
    if not isinstance(profile.get("revision"), int) or profile["revision"] < 1:
        raise VersionedMethodContextError("profile.revision must be positive")

    resolution = _require_mapping(context.get("resolution"), "resolution")
    resolved_ref = _method_ref(resolution.get("resolved"), "resolution.resolved")
    knowledge = _require_mapping(context.get("knowledge"), "knowledge")
    retrieved_ref = _method_ref(knowledge.get("resolved_method"), "knowledge.resolved_method")
    if resolved_ref != retrieved_ref:
        raise VersionedMethodContextError("resolved and retrieved methods differ")

    method_version = _require_mapping(knowledge.get("method_version"), "method_version")
    version_ref = _method_ref(method_version, "method_version")
    if version_ref != resolved_ref:
        raise VersionedMethodContextError("method payload identity differs")
    if method_version.get("status") not in {"published", "deprecated"}:
        raise VersionedMethodContextError("method version is not published")

    method = _require_mapping(method_version.get("method"), "method")
    expected_rule_refs = _require_unique_strings(method.get("rule_refs"), "method.rule_refs")
    rules = knowledge.get("rules")
    if not isinstance(rules, list) or any(not isinstance(rule, dict) for rule in rules):
        raise VersionedMethodContextError("knowledge.rules must be objects")
    actual_rule_refs = [_require_string(rule.get("stable_ref"), "rule.stable_ref") for rule in rules]
    if expected_rule_refs != actual_rule_refs or len(set(actual_rule_refs)) != len(actual_rule_refs):
        raise VersionedMethodContextError("retrieved rules are outside the exact method")

    expected_bases = _require_unique_strings(method.get("chunk_collections"), "method.chunk_collections")
    actual_bases = _require_unique_strings(knowledge.get("knowledge_base_slugs"), "knowledge.knowledge_base_slugs")
    if expected_bases != actual_bases:
        raise VersionedMethodContextError("retrieved knowledge bases are outside the exact method")

    calculation = _require_mapping(context.get("calculation"), "calculation")
    fact_refs = _require_unique_strings(calculation.get("factRefs"), "calculation.factRefs")
    facts = calculation.get("facts")
    if not isinstance(facts, list) or len(facts) != len(fact_refs):
        raise VersionedMethodContextError("calculation facts and refs must align")
    if len(facts) > 48:
        raise VersionedMethodContextError("too many calculation facts")

    retrieval_trace_id = _require_string(
        knowledge.get("retrieval_trace_id"), "knowledge.retrieval_trace_id"
    )
    steps = method.get("steps")
    if not isinstance(steps, list) or not steps:
        raise VersionedMethodContextError("method steps are required")

    prompt_payload = {
        "method": {
            "family_id": resolved_ref[0],
            "version": resolved_ref[1],
            "content_hash": resolved_ref[2],
            "steps": steps,
            "rules": rules,
            "author": method_version.get("author"),
            "school": method_version.get("school"),
        },
        "calculation": {
            "contract": calculation.get("calculationContract"),
            "version": calculation.get("contractVersion"),
            "facts": facts,
        },
        "retrieved_context": knowledge.get("context_text", ""),
        "sources": knowledge.get("sources", []),
        "presentation": profile.get("presentation", {}),
    }
    prompt = "\n".join(
        [
            f"TP_ASTROLOGY_METHOD_CONTRACT_V{PROMPT_CONTRACT_VERSION}",
            "Это закрытый пакет одного астрологического разбора.",
            "Применяй только указанную точную методику и только переданные расчётные факты, правила и фрагменты.",
            "Не вызывай MCP/RAG для расширения астрологических знаний и не смешивай другие школы или общие трактовки модели.",
            "В ответе ясно разделяй: расчётный факт, интерпретацию выбранной методики и персональную гипотезу.",
            "Не додумывай отсутствующий факт. Назови ограничение и заверши спокойным проверяемым вопросом или следующим шагом.",
            "Закрытый пакет:",
            json.dumps(prompt_payload, ensure_ascii=False, sort_keys=True),
        ]
    )
    return VersionedMethodGuard(
        prompt=prompt,
        receipt={
            "schema_version": 1,
            "request_id": request_id,
            "family_id": resolved_ref[0],
            "version": resolved_ref[1],
            "content_hash": resolved_ref[2],
            "retrieval_trace_id": retrieval_trace_id,
            "prompt_contract_version": PROMPT_CONTRACT_VERSION,
            "mixing_policy": mixing_policy,
        },
    )
