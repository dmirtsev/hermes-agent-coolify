from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from hermes_runtime_status import runtime_status_write_is_foreign


ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / "patch_hermes_runtime_status.py"
GUARD = ROOT / "hermes_runtime_status.py"


class RuntimeStatusGuardTests(unittest.TestCase):
    def test_old_container_cannot_overwrite_replacement_status(self) -> None:
        old = {"pid": 3564, "start_time": "old"}
        replacement = {"pid": 161, "start_time": "new"}

        self.assertFalse(
            runtime_status_write_is_foreign(old, replacement, "starting")
        )
        self.assertTrue(
            runtime_status_write_is_foreign(replacement, old, "stopped")
        )
        self.assertTrue(
            runtime_status_write_is_foreign(replacement, old, object())
        )

    def test_same_runtime_and_legacy_records_remain_writable(self) -> None:
        current = {"pid": 161, "start_time": "new"}

        self.assertFalse(
            runtime_status_write_is_foreign(dict(current), current, "running")
        )
        self.assertFalse(
            runtime_status_write_is_foreign({"pid": 161}, current, "stopped")
        )

    def test_pid_reuse_with_new_start_time_is_foreign(self) -> None:
        self.assertTrue(
            runtime_status_write_is_foreign(
                {"pid": 161, "start_time": "old"},
                {"pid": 161, "start_time": "new"},
                "stopped",
            )
        )

    def test_patch_is_fail_closed_and_applies_once(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            temp_root = Path(directory)
            status = temp_root / "gateway" / "status.py"
            guard = temp_root / "agent" / "runtime_status_guard.py"
            status.parent.mkdir()
            guard.parent.mkdir()
            guard.write_text(GUARD.read_text(encoding="utf-8"), encoding="utf-8")
            status.write_text(
                "from utils import atomic_json_write\n\n"
                "def write_runtime_status(path, payload, current_record, gateway_state):\n"
                "    _write_json_file(path, payload)\n",
                encoding="utf-8",
            )
            env = {
                **os.environ,
                "HERMES_SOURCE_ROOT": str(temp_root),
                "HERMES_STATUS_PATH": str(status),
            }
            first = subprocess.run(
                [sys.executable, str(PATCH)],
                env=env,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(first.returncode, 0, first.stderr)
            patched = status.read_text(encoding="utf-8")
            self.assertIn("runtime_status_write_is_foreign", patched)
            self.assertIn("_read_json_file(path)", patched)

            second = subprocess.run(
                [sys.executable, str(PATCH)],
                env=env,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(second.returncode, 0)
            self.assertIn("already present", second.stderr)
