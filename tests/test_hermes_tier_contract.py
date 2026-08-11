from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "hermes-tiers"
COMMIT = "a" * 40


class HermesTierContractTests(unittest.TestCase):
    def run_command(self, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            args,
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_deployment_manifest_is_valid(self) -> None:
        result = self.run_command(
            "python3",
            "scripts/validate_hermes_tiers_manifest.py",
            str(FIXTURES / "manifest.json"),
            "--deployment-ready",
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_production_template_is_valid_but_not_deployment_ready(self) -> None:
        template = ROOT / "deploy" / "hermes-tiers" / "manifest.production.example.json"
        valid = self.run_command("python3", "scripts/validate_hermes_tiers_manifest.py", str(template))
        self.assertEqual(valid.returncode, 0, valid.stderr)
        ready = self.run_command(
            "python3",
            "scripts/validate_hermes_tiers_manifest.py",
            str(template),
            "--deployment-ready",
        )
        self.assertNotEqual(ready.returncode, 0)
        self.assertIn("wrapper_commit", ready.stderr)

        manifest = json.loads(template.read_text(encoding="utf-8"))
        self.assertEqual(manifest["environment"], "production")
        self.assertNotIn("test", json.dumps(manifest))
        self.assertEqual(
            len({runtime["volume_name"] for runtime in manifest["runtimes"].values()}),
            3,
        )

    def test_duplicate_volume_is_rejected(self) -> None:
        manifest = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
        manifest["runtimes"]["strong"]["volume_name"] = manifest["runtimes"]["economy"]["volume_name"]
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "manifest.json"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            result = self.run_command("python3", "scripts/validate_hermes_tiers_manifest.py", str(path))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("volume_name must be unique", result.stderr)

    def test_fixture_smoke_passes(self) -> None:
        result = self.run_command(
            "python3",
            "scripts/smoke_hermes_tiers.py",
            str(FIXTURES / "manifest.json"),
            "--fixture-dir",
            str(FIXTURES / "health"),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.count("OK "), 3)

    def test_cost_unavailable_usage_contract_is_valid(self) -> None:
        result = self.run_command(
            "python3",
            "scripts/validate_hermes_usage_result.py",
            "deploy/hermes-tiers/usage-result.cost-unavailable.example.json",
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_fixed_model_setup_and_release_evidence_use_actual_yaml(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            config = home / "config.yaml"
            config.write_text("unrelated:\n  keep: true\n", encoding="utf-8")
            env = os.environ.copy()
            env.update(
                {
                    "HERMES_HOME": str(home),
                    "HERMES_FIXED_MODEL_ENABLED": "true",
                    "HERMES_FIXED_MODEL_REQUIRED": "true",
                    "HERMES_RUNTIME_TIER": "balanced",
                    "HERMES_RUNTIME_ID": "hermes-test-balanced",
                    "HERMES_FIXED_MODEL_PROVIDER": "openrouter",
                    "HERMES_FIXED_MODEL_DEFAULT": "example/balanced-model",
                    "HERMES_FIXED_MODEL_MAX_TOKENS": "4096",
                    "HERMES_WRAPPER_COMMIT": COMMIT,
                    "HERMES_DEPLOYMENT_ENVIRONMENT": "test",
                    "HERMES_RELEASE_EVIDENCE_REQUIRED": "true",
                }
            )
            setup = self.run_command("sh", "hermes_fixed_model_setup.sh", env=env)
            self.assertEqual(setup.returncode, 0, setup.stderr)
            written = yaml.safe_load(config.read_text(encoding="utf-8"))
            self.assertEqual(written["unrelated"], {"keep": True})
            self.assertEqual(written["model"]["default"], "example/balanced-model")
            self.assertEqual(written["model"]["max_tokens"], 4096)

            evidence = self.run_command("sh", "hermes_release_evidence.sh", env=env)
            self.assertEqual(evidence.returncode, 0, evidence.stderr)
            payload = json.loads((home / "release.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["routing"]["model"], "example/balanced-model")
            self.assertTrue(payload["routing"]["fixed_model_validated"])

            written["model"]["default"] = "example/wrong-model"
            config.write_text(yaml.safe_dump(written), encoding="utf-8")
            mismatch = self.run_command("sh", "hermes_release_evidence.sh", env=env)
            self.assertNotEqual(mismatch.returncode, 0)
            self.assertIn("fixed model mismatch", mismatch.stderr)


if __name__ == "__main__":
    unittest.main()
