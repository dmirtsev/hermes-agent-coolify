"""Caller-controlled TP Knowledge policy for Hermes requests."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping


POLICY_CONTRACT = "tp_knowledge_policy_v1"
MODEL_ONLY = "model_only"
KNOWLEDGE_AUGMENTED = "knowledge_augmented"
_ALLOWED_MODES = {MODEL_ONLY, KNOWLEDGE_AUGMENTED}
_MAX_POLICY_BYTES = 2_048


class KnowledgePolicyError(ValueError):
    """The caller supplied an invalid or unsupported policy."""


@dataclass(frozen=True)
class KnowledgePolicyGuard:
    mode: str

    @property
    def tools_disabled(self) -> bool:
        return self.mode == MODEL_ONLY

    @property
    def receipt(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "contract": POLICY_CONTRACT,
            "mode": self.mode,
            "external_knowledge_tools_used": False if self.tools_disabled else None,
        }


def prepare_knowledge_policy(value: Any) -> KnowledgePolicyGuard | None:
    """Validate a request policy, preserving legacy behavior when absent."""

    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise KnowledgePolicyError("tp_knowledge_policy must be an object")
    try:
        serialized = json.dumps(value, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise KnowledgePolicyError("tp_knowledge_policy is not valid JSON") from exc
    if len(serialized.encode("utf-8")) > _MAX_POLICY_BYTES:
        raise KnowledgePolicyError("tp_knowledge_policy is too large")
    if value.get("schema_version") != 1:
        raise KnowledgePolicyError("unsupported tp_knowledge_policy schema")
    mode = value.get("mode")
    if mode not in _ALLOWED_MODES:
        raise KnowledgePolicyError("unsupported tp_knowledge_policy mode")
    return KnowledgePolicyGuard(mode=str(mode))
