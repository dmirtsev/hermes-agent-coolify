"""Fail-closed execution guard for versioned astrology readings.

Cabinet resolves and retrieves the exact method from TP Knowledge.  This seam
validates that closed package before Hermes sees it, injects one bounded prompt
contract, and returns a receipt that Cabinet can persist with the turn.
"""

from __future__ import annotations

import json
import re
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Iterator


SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
SEMVER_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$")
PROMPT_CONTRACT_VERSION = "1.1.0"
MAX_CONTEXT_BYTES = 180_000
_STRICT_CONTEXT_ACTIVE: ContextVar[bool] = ContextVar(
    "tp_versioned_method_strict_context", default=False
)


class VersionedMethodContextError(ValueError):
    pass


def strict_context_active() -> bool:
    return _STRICT_CONTEXT_ACTIVE.get()


@contextmanager
def strict_context_scope(enabled: bool) -> Iterator[None]:
    token = _STRICT_CONTEXT_ACTIVE.set(bool(enabled))
    try:
        yield
    finally:
        _STRICT_CONTEXT_ACTIVE.reset(token)


@dataclass(frozen=True)
class VersionedMethodGuard:
    prompt: str
    receipt: dict[str, Any]

    @property
    def isolated_session_id(self) -> str:
        """Deterministic audit identity that is never backed by shared history."""
        return f"tp-reading-{self.receipt['request_id']}"


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


def _require_bool(value: Any, label: str) -> bool:
    if not isinstance(value, bool):
        raise VersionedMethodContextError(f"{label} must be boolean")
    return value


def _require_provenance_sources(value: Any) -> list[dict[str, Any]]:
    provenance = _require_mapping(value, "method_version.provenance")
    sources = provenance.get("sources")
    if not isinstance(sources, list) or not sources:
        raise VersionedMethodContextError("published method provenance sources are required")
    allowed_rights = {
        "owned",
        "licensed",
        "public_domain",
        "permission_recorded",
        "restricted",
    }
    normalized = []
    for index, item in enumerate(sources):
        source = _require_mapping(item, f"method_version.provenance.sources[{index}]")
        normalized_source = {
            "source_id": _require_string(source.get("source_id"), "source.source_id"),
            "source_version": _require_string(source.get("source_version"), "source.source_version"),
            "locator": _require_string(source.get("locator"), "source.locator"),
            "rights_status": _require_string(source.get("rights_status"), "source.rights_status"),
        }
        if normalized_source["rights_status"] not in allowed_rights:
            raise VersionedMethodContextError("source.rights_status is invalid")
        normalized.append(normalized_source)
    return normalized


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
    presentation = _require_mapping(profile.get("presentation"), "profile.presentation")
    show_method = _require_bool(presentation.get("show_method"), "profile.presentation.show_method")
    show_sources = _require_bool(presentation.get("show_sources"), "profile.presentation.show_sources")
    detail_level = _require_string(
        presentation.get("detail_level"), "profile.presentation.detail_level"
    )
    if detail_level not in {"simple", "standard", "professional"}:
        raise VersionedMethodContextError("profile.presentation.detail_level is invalid")

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
    method_sources = _require_provenance_sources(method_version.get("provenance"))

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
    retrieved_sources = knowledge.get("sources", [])
    if not isinstance(retrieved_sources, list) or any(
        not isinstance(source, dict) for source in retrieved_sources
    ):
        raise VersionedMethodContextError("knowledge.sources must be objects")

    presentation_instructions = [
        "Структура ответа обязательна: «Расчётный факт», «Трактовка выбранной методики», «Персональная гипотеза», «Ограничения и следующий шаг».",
        (
            f"В конце укажи «Методика: {resolved_ref[0]}@{resolved_ref[1]}»."
            if show_method
            else "Не показывай пользователю техническое имя или версию методики."
        ),
        (
            "В конце добавь «Источники» и перечисли только source_id, source_version и locator из закрытого пакета."
            if show_sources
            else "Не выводи пользователю список источников, но опирайся только на источники закрытого пакета."
        ),
        f"Уровень подробности ответа: {detail_level}.",
    ]

    prompt_payload = {
        "method": {
            "family_id": resolved_ref[0],
            "version": resolved_ref[1],
            "content_hash": resolved_ref[2],
            "steps": steps,
            "rules": rules,
            "author": method_version.get("author"),
            "school": method_version.get("school"),
            "provenance_sources": method_sources,
        },
        "calculation": {
            "contract": calculation.get("calculationContract"),
            "version": calculation.get("contractVersion"),
            "facts": facts,
        },
        "retrieved_context": knowledge.get("context_text", ""),
        "retrieved_sources": retrieved_sources,
        "presentation": presentation,
    }
    prompt = "\n".join(
        [
            f"TP_ASTROLOGY_METHOD_CONTRACT_V{PROMPT_CONTRACT_VERSION}",
            "Это закрытый пакет одного астрологического разбора.",
            "Применяй только указанную точную методику и только переданные расчётные факты, правила и фрагменты.",
            "Не вызывай MCP/RAG для расширения астрологических знаний и не смешивай другие школы или общие трактовки модели.",
            "В ответе ясно разделяй: расчётный факт, интерпретацию выбранной методики и персональную гипотезу.",
            "Не додумывай отсутствующий факт. Назови ограничение и заверши спокойным проверяемым вопросом или следующим шагом.",
            *presentation_instructions,
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
            "context_isolation": "strict_v1",
            "shared_memory_used": False,
            "external_tools_used": False,
        },
    )
