import json
from contextlib import closing
import os
import sqlite3
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

import hermes_durable_accounting as accounting
import hermes_sakoian_diagnostics as diagnostics


class DiagnosticsStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.journal = Path(self.tmp.name) / "accounting.sqlite3"
        self.env = patch.dict(os.environ, {"HERMES_ACCOUNTING_JOURNAL_PATH": str(self.journal)})
        self.env.start()
        accounting.ensure_journal()
        with closing(sqlite3.connect(self.journal)) as db, db:
            db.execute("INSERT INTO accounting_request(request_key,payload_sha256,state,created_at,updated_at) VALUES(?,?,?,?,?)", ("r1", "payload-hash", "completed", 0, 0))
            db.execute("INSERT INTO accounting_evidence_event(request_key,kind,generation_id,evidence_json,created_at) VALUES(?,?,?,?,?)", ("r1", "generation", "gen1", "{}", 0))

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def save(self, text="plan Ω 😀", **kw):
        args = dict(request_key="r1", raw_text=text, reason={"code": "duplicate_event", "path": "event_order"}, expected_plan={"catalogue_hash": "abc"}, generation_id="gen1", completion={"finish_reason": "stop"})
        args.update(kw)
        return diagnostics.save_rejection(**args)

    def test_private_capture_and_metadata_does_not_leak_raw_plan(self):
        self.save("секретный план")
        view = diagnostics.read_rejection("r1")
        self.assertNotIn("raw_text", view)
        self.assertNotIn("expected_plan", view)
        self.assertEqual(view["reason"]["code"], "duplicate_event")
        full = diagnostics.read_rejection("r1", include_plan=True)
        self.assertEqual(full["raw_text"], "секретный план")
        self.assertEqual(full["expected_plan"]["catalogue_hash"], "abc")
        side = next((self.journal.parent / ".hermes-sakoian-diagnostics").glob("rejections-*.sqlite3"))
        self.assertEqual(stat.S_IMODE(side.parent.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(side.stat().st_mode), 0o600)

    def test_unicode_surrogate_and_33k_are_retained(self):
        text = "é" * 33000 + "\ud800"
        self.save(text)
        full = diagnostics.read_rejection("r1", include_plan=True)
        self.assertEqual(full["raw_text"], text)
        self.assertEqual(full["raw_bytes"], len(text.encode("utf-8", "surrogatepass")))
        self.assertFalse(full["raw_truncated"])

    def test_large_capture_is_explicitly_truncated_but_hash_is_full(self):
        text = "x" * (1024 * 1024 + 100)
        self.save(text)
        full = diagnostics.read_rejection("r1", include_plan=True)
        import hashlib
        self.assertTrue(full["raw_truncated"])
        self.assertEqual(full["raw_bytes"], len(text))
        self.assertEqual(full["raw_sha256"], hashlib.sha256(text.encode()).hexdigest())
        self.assertLessEqual(len(full["raw_text"].encode()), 1024 * 1024)

    def test_idempotent_same_capture_and_conflict(self):
        first = self.save("same")
        self.assertEqual(first, self.save("same"))
        with self.assertRaises(diagnostics.DiagnosticsError) as ctx:
            self.save("different")
        self.assertEqual(ctx.exception.code, "diagnostics_conflict")
        with self.assertRaises(diagnostics.DiagnosticsError) as ctx:
            self.save("same", generation_id="another")
        self.assertEqual(ctx.exception.code, "diagnostics_generation_missing")

    def test_missing_request_and_generation_are_rejected(self):
        with self.assertRaises(diagnostics.DiagnosticsError) as ctx:
            diagnostics.save_rejection(request_key="none", raw_text="x", reason={}, expected_plan=None, generation_id="gen1", completion={})
        self.assertEqual(ctx.exception.code, "diagnostics_request_missing")
        with self.assertRaises(diagnostics.DiagnosticsError) as ctx:
            diagnostics.save_rejection(request_key="r1", raw_text="x", reason={}, expected_plan=None, generation_id="none", completion={})
        self.assertEqual(ctx.exception.code, "diagnostics_generation_missing")

    def test_fresh_process_can_read_sidecar(self):
        self.save("fresh-process")
        code = "import hermes_sakoian_diagnostics as d; print(d.read_rejection('r1')['diagnostic_status'])"
        env = dict(os.environ)
        env["PYTHONPATH"] = os.getcwd()
        out = subprocess.check_output([sys.executable, "-c", code], env=env, text=True)
        self.assertEqual(out.strip(), "stored")

    def test_unsafe_sidecar_symlink_is_rejected(self):
        directory = self.journal.parent / ".hermes-sakoian-diagnostics"
        directory.mkdir(mode=0o700)
        import hashlib
        side = directory / ("rejections-" + hashlib.sha256(str(self.journal).encode()).hexdigest()[:16] + ".sqlite3")
        side.symlink_to(self.journal)
        with self.assertRaises(diagnostics.DiagnosticsError) as ctx:
            self.save("unsafe")
        self.assertEqual(ctx.exception.code, "diagnostics_storage_unsafe")

    def test_private_details_return_only_on_explicit_read(self):
        reason = {'code':'wrong_schema_version', 'path':'$.schema_version',
                  'details':{'actual':'PRIVATE_MODEL_TEXT'}}
        self.save('PRIVATE_MODEL_TEXT', reason=reason, completion={'finish_reason':'PRIVATE_MODEL_TEXT'})
        self.assertNotIn('PRIVATE_MODEL_TEXT', json.dumps(diagnostics.read_rejection('r1')))
        self.assertEqual(diagnostics.read_rejection('r1', include_plan=True)['reason'], reason)

    def test_conflicting_binding_and_diagnosis_cannot_overwrite_first_record(self):
        self.save('original')
        for kwargs in ({'reason':{'code':'changed'}}, {'completion':{}},
                       {'expected_plan':{'catalogue_hash':'different'}}):
            with self.subTest(kwargs=kwargs), self.assertRaises(diagnostics.DiagnosticsError) as exc:
                self.save('original', **kwargs)
            self.assertEqual(exc.exception.code, 'diagnostics_conflict')
        with closing(sqlite3.connect(self.journal)) as db, db:
            db.execute("UPDATE accounting_request SET payload_sha256='different' WHERE request_key='r1'")
        with self.assertRaises(diagnostics.DiagnosticsError) as exc:
            self.save('original')
        self.assertEqual(exc.exception.code, 'diagnostics_conflict')
        self.assertEqual(diagnostics.read_rejection('r1', include_plan=True)['raw_text'], 'original')

    def test_read_is_readonly_and_does_not_create_missing_storage(self):
        with self.assertRaises(diagnostics.DiagnosticsError) as exc:
            diagnostics.read_rejection('r1')
        self.assertEqual(exc.exception.code, 'diagnostics_not_found')
        self.assertFalse((self.journal.parent / '.hermes-sakoian-diagnostics').exists())
        self.save()
        side = diagnostics._sidecar_path(create=False)
        original = side.read_bytes()
        diagnostics.read_rejection('r1', include_plan=True)
        self.assertEqual(side.read_bytes(), original)

    def test_unsafe_permissions_and_nonregular_storage_fail_closed(self):
        self.save()
        side = diagnostics._sidecar_path()
        side.chmod(0o644)
        with self.assertRaises(diagnostics.DiagnosticsError) as exc:
            self.save()
        self.assertEqual(exc.exception.code, 'diagnostics_storage_permissions')
        self.assertEqual(stat.S_IMODE(side.stat().st_mode), 0o644)
        side.chmod(0o600)
        side.unlink()
        os.mkfifo(side, 0o600)
        with self.assertRaises(diagnostics.DiagnosticsError) as exc:
            self.save()
        self.assertEqual(exc.exception.code, 'diagnostics_storage_unsafe')

    def test_os_and_sqlite_errors_are_safe(self):
        for error in (OSError('PRIVATE_MODEL_TEXT'), sqlite3.OperationalError('PRIVATE_MODEL_TEXT')):
            with self.subTest(error=type(error).__name__), patch.object(diagnostics, '_connect', side_effect=error):
                with self.assertRaises(diagnostics.DiagnosticsError) as exc:
                    self.save()
                self.assertEqual(str(exc.exception), 'diagnostics_storage_unavailable')


if __name__ == "__main__":
    unittest.main()
