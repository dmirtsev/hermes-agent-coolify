from __future__ import annotations

import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import hermes_design_completion as design


def valid_design() -> dict:
    return {
        "concept": "Тихая космическая редакция",
        "design": {
            "preset": "cosmic-night",
            "canvas": "#08111f",
            "surface": "#101c2d",
            "surfaceAlt": "#17263a",
            "ink": "#f5f7fb",
            "muted": "#aab6c7",
            "accent": "#72d5ff",
            "accentContrast": "#071019",
            "gold": "#e7c36d",
            "border": "#30435d",
            "headingStyle": "editorial",
            "radius": "soft",
            "density": "balanced",
            "heroLayout": "split",
        },
        "behavior": {
            "entrance": "soft",
            "cardHover": "lift",
            "stickyNavigation": True,
        },
        "rationale": ["Контрастная типографика", "Спокойная фестивальная атмосфера"],
    }


class FakeWeb:
    @staticmethod
    def json_response(payload, status=200, headers=None):
        return SimpleNamespace(payload=payload, status=status, headers=headers or {})


class FakeRequest:
    content_length = None

    def __init__(self, body, key="design-test-1"):
        self._body = body
        self.headers = {"Idempotency-Key": key}

    async def json(self):
        return self._body


class FakeCompletionClient:
    def __init__(self, calls):
        self._calls = calls
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self._calls.append(kwargs)
        return SimpleNamespace(
            id="gen-design-test",
            model="provider/executed-model",
            provider="TestProvider",
            usage=SimpleNamespace(
                prompt_tokens=40,
                completion_tokens=80,
                total_tokens=120,
                cost="0.00012",
                prompt_tokens_details=SimpleNamespace(cached_tokens=3, cache_write_tokens=0),
                completion_tokens_details=SimpleNamespace(reasoning_tokens=4),
            ),
            openrouter_metadata=None,
            choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(valid_design())))],
        )


class FakeAdapter:
    def __init__(self):
        self.calls = []

    def _check_auth(self, _request):
        return None

    def _create_agent(self):
        client = FakeCompletionClient(self.calls)
        return SimpleNamespace(
            provider="openrouter",
            model="configured/fixed-model",
            base_url="https://openrouter.ai/api/v1",
            _ensure_primary_openai_client=lambda **_kwargs: client,
        )


class HermesDesignCompletionTests(unittest.TestCase):
    def test_request_is_allowlisted_bounded_and_https_only(self):
        parsed = design.validate_design_request({
            "prompt": "Ночной фестиваль",
            "reference_urls": ["https://example.org/reference"],
            "max_tokens": 100_000,
            "temperature": 0.4,
        })
        self.assertEqual(parsed["max_tokens"], design.HARD_MAX_OUTPUT_TOKENS)
        with self.assertRaises(design.DesignRequestError):
            design.validate_design_request({"prompt": "x", "model": "attacker/model"})
        with self.assertRaises(design.DesignRequestError):
            design.validate_design_request({
                "prompt": "x",
                "reference_urls": ["http://internal.example/reference"],
            })

    def test_generated_design_contract_is_strict(self):
        self.assertEqual(design.validate_generated_design(valid_design())["design"]["radius"], "soft")
        invalid = valid_design()
        invalid["design"]["canvas"] = "red"
        with self.assertRaises(design.DesignRequestError):
            design.validate_generated_design(invalid)

    def test_endpoint_executes_fixed_model_once_and_replays(self):
        adapter = FakeAdapter()
        body = {
            "prompt": "Используй утвержденные разделы фестиваля",
            "reference_urls": ["https://example.org/design"],
            "max_tokens": 240,
            "temperature": 0.5,
        }
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ,
            {
                "HERMES_ACCOUNTING_JOURNAL_PATH": str(Path(directory) / "journal.sqlite3"),
                "HERMES_RUNTIME_TIER": "balanced",
                "HERMES_RUNTIME_ID": "hermes-test-balanced",
            },
            clear=False,
        ):
            first = asyncio.run(design.handle_design_completion(adapter, FakeRequest(body), FakeWeb))
            second = asyncio.run(design.handle_design_completion(adapter, FakeRequest(body), FakeWeb))

        self.assertEqual(first.status, 200)
        self.assertEqual(second.status, 200)
        self.assertEqual(second.headers["X-Hermes-Idempotency-Replayed"], "true")
        self.assertEqual(len(adapter.calls), 1)
        self.assertEqual(adapter.calls[0]["model"], "configured/fixed-model")
        self.assertNotIn("tools", adapter.calls[0])
        self.assertEqual(adapter.calls[0]["max_tokens"], 240)
        self.assertEqual(first.payload["usage"]["total_tokens"], 120)
        self.assertEqual(first.payload["hermes_accounting"]["cost"]["status"], "actual")
        self.assertEqual(first.payload["hermes_accounting"]["tokens"]["reasoning"], 4)


if __name__ == "__main__":
    unittest.main()
