"""Private, fail-closed storage for rejected grounded Sakoian plans.

The accounting journal remains the source of billing evidence.  This sidecar
keeps the rejected provider text and the precise validator diagnosis out of
the public request/result records while binding each capture to that journal.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import sqlite3
import stat
import time
from pathlib import Path
from typing import Any


MAX_CAPTURE_BYTES = 1024 * 1024
MODULE_VERSION = "sakoian-diagnostics-v1"


class DiagnosticsError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _accounting_module():
    if __package__:
        from . import durable_accounting as mod
    else:
        import hermes_durable_accounting as mod
    return mod


def _journal_path() -> Path:
    return Path(_accounting_module()._journal_path())


def _check_path(path: Path, *, directory: bool) -> None:
    info = path.lstat()
    valid_type = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
    if not valid_type or info.st_uid != os.geteuid():
        raise DiagnosticsError("diagnostics_storage_unsafe")
    expected_mode = 0o700 if directory else 0o600
    if stat.S_IMODE(info.st_mode) != expected_mode:
        raise DiagnosticsError("diagnostics_storage_permissions")


def _sidecar_path(*, create: bool = True) -> Path:
    journal = _journal_path().absolute()
    directory = journal.parent / ".hermes-sakoian-diagnostics"
    if create:
        try:
            directory.mkdir(mode=0o700)
        except FileExistsError:
            pass
    _check_path(directory, directory=True)
    suffix = hashlib.sha256(str(journal).encode()).hexdigest()[:16]
    return directory / ("rejections-" + suffix + ".sqlite3")


def _connect(*, create: bool = True) -> sqlite3.Connection:
    path = _sidecar_path(create=create)
    if create:
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        except FileExistsError:
            pass
        else:
            os.close(fd)
    _check_path(path, directory=False)
    conn = sqlite3.connect(path.as_uri() + ("?mode=rw" if create else "?mode=ro"),
                           uri=True, timeout=10, isolation_level=None)
    try:
        conn.row_factory = sqlite3.Row
        if not create:
            conn.execute("PRAGMA query_only=ON")
            return conn
        conn.execute("PRAGMA journal_mode=DELETE")
        conn.execute("PRAGMA synchronous=FULL")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS rejection (
              request_key TEXT PRIMARY KEY,
              diagnostic_id TEXT NOT NULL UNIQUE,
              generation_id TEXT NOT NULL,
              payload_sha256 TEXT NOT NULL,
              catalogue_hash TEXT,
              raw_text TEXT,
              raw_sha256 TEXT NOT NULL,
              raw_bytes INTEGER NOT NULL,
              raw_truncated INTEGER NOT NULL,
              reason_json TEXT NOT NULL,
              expected_plan_json TEXT,
              completion_json TEXT NOT NULL,
              created_at REAL NOT NULL,
              module_version TEXT NOT NULL
            )
        """)
        return conn
    except BaseException:
        conn.close()
        raise


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def _binding(request_key: str, generation_id: str) -> tuple[str, str]:
    if not isinstance(request_key, str) or not request_key:
        raise DiagnosticsError("diagnostics_request_missing")
    if not isinstance(generation_id, str) or not generation_id:
        raise DiagnosticsError("diagnostics_generation_missing")
    try:
        journal = _journal_path().absolute()
        if journal.is_symlink() or not journal.is_file():
            raise DiagnosticsError("diagnostics_accounting_unavailable")
        with contextlib.closing(sqlite3.connect(journal.as_uri() + "?mode=ro", uri=True, timeout=5)) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA query_only=ON")
            req = db.execute(
                "SELECT payload_sha256 FROM accounting_request WHERE request_key=?", (request_key,),
            ).fetchone()
            ev = db.execute(
                "SELECT id FROM accounting_evidence_event "
                "WHERE request_key=? AND kind='generation' AND generation_id=? LIMIT 1",
                (request_key, generation_id),
            ).fetchone()
    except DiagnosticsError:
        raise
    except Exception:
        raise DiagnosticsError("diagnostics_accounting_unavailable") from None
    if req is None:
        raise DiagnosticsError("diagnostics_request_missing")
    if ev is None:
        raise DiagnosticsError("diagnostics_generation_missing")
    return str(req["payload_sha256"]), str(generation_id)


def _save_rejection(*, request_key, raw_text, reason, expected_plan, generation_id, completion):
    payload_hash, generation_id = _binding(request_key, generation_id)
    if not isinstance(reason, dict):
        raise DiagnosticsError("diagnostics_reason_invalid")
    if raw_text is not None and not isinstance(raw_text, str):
        raise DiagnosticsError("diagnostics_content_type")
    raw_bytes_data = (raw_text or "").encode("utf-8", "surrogatepass")
    raw_digest = hashlib.sha256(raw_bytes_data).hexdigest()
    stored = raw_bytes_data[:MAX_CAPTURE_BYTES]
    while True:
        try:
            stored_text = stored.decode("utf-8", "surrogatepass")
            break
        except UnicodeDecodeError:
            stored = stored[:-1]  # At most three trailing bytes of a split codepoint.
    truncated = len(raw_bytes_data) > len(stored)
    if raw_text is None:
        stored_text = None
    diagnostic_id = "sdx_" + hashlib.sha256(
        _json([request_key, payload_hash, generation_id, raw_digest]).encode()
    ).hexdigest()[:24]
    fields = {
        "generation_id": generation_id, "payload_sha256": payload_hash,
        "catalogue_hash": expected_plan.get("catalogue_hash") if expected_plan else None,
        "raw_text": _json(stored_text), "raw_sha256": raw_digest,
        "raw_bytes": len(raw_bytes_data), "raw_truncated": int(truncated),
        "reason_json": _json(reason), "expected_plan_json": _json(expected_plan),
        "completion_json": _json(completion), "module_version": MODULE_VERSION,
    }
    with contextlib.closing(_connect()) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        old = conn.execute("SELECT * FROM rejection WHERE request_key=?", (request_key,)).fetchone()
        if old is not None:
            if any(old[key] != value for key, value in fields.items()):
                raise DiagnosticsError("diagnostics_conflict")
            return str(old["diagnostic_id"])
        fields.update(request_key=request_key, diagnostic_id=diagnostic_id, created_at=time.time())
        conn.execute("INSERT INTO rejection (" + ",".join(fields) + ") VALUES (" +
                     ",".join("?" for _ in fields) + ")", tuple(fields.values()))
    return diagnostic_id


def save_rejection(*, request_key, raw_text, reason, expected_plan, generation_id, completion) -> str:
    """Persist before returning; never expose a raw OS/SQLite/content exception."""
    try:
        return _save_rejection(request_key=request_key, raw_text=raw_text, reason=reason,
            expected_plan=expected_plan, generation_id=generation_id, completion=completion)
    except DiagnosticsError:
        raise
    except Exception:
        raise DiagnosticsError("diagnostics_storage_unavailable") from None


def read_rejection(request_key: str, *, include_plan: bool = False) -> dict:
    """Operator-only read, no new HTTP route; full private content is opt-in."""
    try:
        with contextlib.closing(_connect(create=False)) as conn:
            row = conn.execute("SELECT * FROM rejection WHERE request_key=?", (request_key,)).fetchone()
        if row is None:
            raise DiagnosticsError("diagnostics_not_found")
        reason = json.loads(row["reason_json"])
        result = {key: row[key] for key in (
            "diagnostic_id", "request_key", "generation_id", "payload_sha256", "catalogue_hash",
            "raw_sha256", "raw_bytes", "created_at", "module_version")}
        result.update(raw_truncated=bool(row["raw_truncated"]), diagnostic_status="stored",
                      reason={key:reason[key] for key in ("code", "path") if key in reason})
        if include_plan:
            result.update(raw_text=json.loads(row["raw_text"]), reason=reason,
                          expected_plan=json.loads(row["expected_plan_json"]),
                          completion=json.loads(row["completion_json"]))
        return result
    except DiagnosticsError:
        raise
    except FileNotFoundError:
        raise DiagnosticsError("diagnostics_not_found") from None
    except Exception:
        raise DiagnosticsError("diagnostics_storage_unavailable") from None
