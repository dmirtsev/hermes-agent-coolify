"""Ownership guard for the persistent Hermes runtime-status record.

Coolify may overlap an old and a replacement container while they share
``HERMES_HOME``.  The replacement claims the status file with ``starting``;
after that, a writer with a different process identity must not overwrite its
state during teardown.
"""

from __future__ import annotations

import os
import socket
from typing import Any, Mapping


def runtime_status_owner_id() -> str:
    """Return a container-local identity used for status-file ownership.

    Coolify may set the same hostname for every replacement container.  A PID
    namespace identifier stays distinct across those overlapping containers;
    hostname is only a portability fallback outside Linux namespaces.
    """
    try:
        return os.readlink("/proc/self/ns/pid")
    except OSError:
        return socket.gethostname()


def runtime_status_write_is_foreign(
    existing: Mapping[str, Any] | None,
    current: Mapping[str, Any],
    gateway_state: Any,
) -> bool:
    """Return whether a status write belongs to an older runtime instance.

    ``starting`` is the explicit ownership claim made by a replacement
    gateway.  Legacy or incomplete records remain writable for compatibility;
    only a complete, conflicting owner is fail-closed.
    """

    if gateway_state == "starting" or not isinstance(existing, Mapping):
        return False

    existing_owner = existing.get("owner_id")
    current_owner = current.get("owner_id") or runtime_status_owner_id()
    if existing_owner is not None and str(existing_owner) != str(current_owner):
        return True

    existing_pid = existing.get("pid")
    current_pid = current.get("pid")
    if existing_pid is None or current_pid is None:
        return False
    if str(existing_pid) != str(current_pid):
        return True

    existing_start = existing.get("start_time")
    current_start = current.get("start_time")
    return (
        existing_start is not None
        and current_start is not None
        and str(existing_start) != str(current_start)
    )
