"""Fail-closed context validator for tp-astro-analysis Sakoian results.

This is deliberately separate from ``tp_reading_context``.  It validates
provenance and identity, while leaving astrological calculation to Core and
the analysis executor.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

MAX_CONTEXT_BYTES = 180_000
SHA256 = re.compile(r"^[0-9a-f]{64}$")
SHA1 = re.compile(r"^[0-9a-f]{40}$")


class SakoianContextError(ValueError):
    pass


def _obj(v: Any, name: str) -> dict:
    if not isinstance(v, dict):
        raise SakoianContextError(f"{name} must be an object")
    return v


def _str(v: Any, name: str, limit: int = 512) -> str:
    if not isinstance(v, str) or not v.strip() or len(v) > limit:
        raise SakoianContextError(f"{name} is invalid")
    return v


def _hash(v: Any, name: str) -> str:
    s = _str(v, name, 64)
    if not SHA256.fullmatch(s):
        raise SakoianContextError(f"{name} must be a SHA-256 hash")
    return s


def canonical_hash(value: Any) -> str:
    try:
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False).encode()
    except (TypeError, ValueError) as exc:
        raise SakoianContextError("analysis is not finite JSON") from exc
    return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True)
class SakoianAnalysisGuard:
    prompt: str
    receipt: dict
    isolated_session_id: str


def prepare_sakoian_analysis_context(value: Any, *, expected_request_id: str | None = None) -> SakoianAnalysisGuard | None:
    if value is None:
        return None
    ctx = _obj(value, "tp_analysis_context")
    encoded = json.dumps(ctx, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    if len(encoded.encode()) > MAX_CONTEXT_BYTES:
        raise SakoianContextError("tp_analysis_context is too large")
    if ctx.get("schema_version") != "tp-astro-analysis.hermes-context.v1":
        raise SakoianContextError("unsupported tp_analysis_context schema")
    request_id = _str(ctx.get("request_id"), "request_id")
    if expected_request_id is not None and request_id != expected_request_id:
        raise SakoianContextError("analysis request_id does not match idempotency key")
    conversation = _str(ctx.get("conversation_ref"), "conversation_ref")
    scope = _str(ctx.get("access_scope_ref"), "access_scope_ref")
    subject = _str(ctx.get("subject_ref"), "subject_ref")
    chart = _str(ctx.get("chart_ref"), "chart_ref")
    analysis = _obj(ctx.get("analysis"), "analysis")
    analysis_hash = _hash(ctx.get("analysis_hash"), "analysis_hash")
    if canonical_hash(analysis) != analysis_hash:
        raise SakoianContextError("analysis_hash mismatch")
    if analysis.get("schema_version") != "tp-astro-analysis.result.v1":
        raise SakoianContextError("unsupported analysis result schema")
    aid = _str(analysis.get("analysis_id"), "analysis.analysis_id", 256)
    if _str(analysis.get("status"), "analysis.status", 32) not in {"complete", "partial"}:
        raise SakoianContextError("analysis status is not narratable")
    method = _obj(analysis.get("method_ref"), "analysis.method_ref")
    if _str(method.get("method_id"), "method_ref.method_id") != "sakoian_transit_analysis":
        raise SakoianContextError("unexpected analysis method")
    version = _str(method.get("version"), "method_ref.version", 32)
    package_hash = _hash(method.get("package_hash"), "method_ref.package_hash")
    knowledge = _obj(method.get("knowledge_ref"), "method_ref.knowledge_ref")
    if _str(knowledge.get("environment"), "knowledge_ref.environment", 32) != "test":
        raise SakoianContextError("only test Knowledge is accepted")
    knowledge_hash = _hash(knowledge.get("content_hash"), "knowledge_ref.content_hash")
    source = _obj(ctx.get("source"), "source")
    if _str(source.get("producer"), "source.producer", 64) != "core_astrogeo":
        raise SakoianContextError("source producer is not Core")
    core_commit = _str(source.get("core_commit"), "source.core_commit", 40)
    if not SHA1.fullmatch(core_commit):
        raise SakoianContextError("source.core_commit is invalid")
    req_hash = _hash(source.get("request_hash"), "source.request_hash")
    resp_hash = _hash(source.get("response_hash"), "source.response_hash")
    moment = _str(source.get("moment_utc"), "source.moment_utc", 256)
    for key in ("facts", "primary_factors", "derivations", "warnings", "coverage"):
        if key not in analysis:
            raise SakoianContextError(f"analysis.{key} is required")
    factors = analysis["primary_factors"]
    if not isinstance(factors, list) or [x.get("order") for x in factors] != [1, 2, 3, 4, 5]:
        raise SakoianContextError("analysis must retain five ordered primary factors")
    if any(not isinstance(x, dict) or x.get("status") not in {"known", "unknown", "unavailable"} for x in factors):
        raise SakoianContextError("primary factor status is invalid")
    receipt = {
        "schema_version": 1, "request_id": request_id, "family_id": "sakoian_transit_analysis",
        "version": version, "content_hash": knowledge_hash, "executor_package_hash": package_hash,
        "analysis_id": aid, "analysis_hash": analysis_hash, "core_commit": core_commit,
        "request_hash": req_hash, "response_hash": resp_hash, "moment_utc": moment,
        "calculation_contract": "core.transit_snapshot", "prompt_contract_version": "sakoian_v1",
        "mixing_policy": "forbidden", "context_isolation": "strict_v1", "external_tools_used": False,
        "shared_memory_used": False, "access_scope_ref": scope, "subject_ref": subject, "chart_ref": chart,
    }
    prompt = "\n".join([
        "TP_ASTRO_ANALYSIS_SAKOIAN_CONTEXT_V1",
        "Передан закрытый результат Core и отдельный результат методики Сакоян.",
        "Не пересчитывай аспекты, дома или положения. Не добавляй новые астрологические факты.",
        "Сохраняй различия known, unknown, unavailable и предупреждения.",
        "Разделяй расчетный факт, трактовку Сакоян, гипотезу и ограничения.",
        json.dumps({"analysis": analysis, "source": source, "method_ref": method}, ensure_ascii=False, sort_keys=True),
    ])
    return SakoianAnalysisGuard(prompt, receipt, "tp-sakoian-" + request_id)
