"""D18 live acceptance — atomic/rollback-capable document PID bootstrap.
Wing: ops | Topic: d18-pid-bootstrap | Updated: 2026-09-19 16:49
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from cdt_autocad.native_bridge.client import BridgeRemoteError, NativeBridgeClient
from cdt_autocad.native_bridge.transport_windows import (
    NamedPipeTransport,
    pipe_name_for_session,
)

EXPECTED_BRIDGE_VERSION = "0.8.6-d18"


def _session_id() -> int:
    value = ctypes.c_ulong()
    if not ctypes.windll.kernel32.ProcessIdToSessionId(os.getpid(), ctypes.byref(value)):
        raise RuntimeError("could not resolve Windows session id")
    return int(value.value)


def _retry(action: Callable[[], Any], *, timeout: float = 30.0, delay: float = 0.1) -> Any:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            return action()
        except Exception as exc:
            last_error = exc
            time.sleep(delay)
    raise RuntimeError("D18 acceptance operation did not become ready") from last_error


def _expect_remote_error(action: Callable[[], Any], expected_code: str) -> str:
    try:
        action()
    except BridgeRemoteError as exc:
        if exc.code != expected_code:
            raise AssertionError(
                f"expected bridge error {expected_code!r}, got {exc.code!r}"
            ) from exc
        return exc.code
    raise AssertionError(f"expected bridge error {expected_code!r}, but operation succeeded")


def _row_for_path(rows: list[dict[str, Any]], target: Path) -> dict[str, Any]:
    expected = str(target.resolve()).casefold()
    matches = [
        row
        for row in rows
        if str(row.get("file_name") or "").casefold() == expected
    ]
    if len(matches) != 1:
        raise AssertionError(
            f"expected exactly one bridge row for {target}, got {len(matches)}"
        )
    return matches[0]


def _run(args: argparse.Namespace) -> dict[str, Any]:
    if os.name != "nt":
        raise RuntimeError("D18 live acceptance requires Windows AutoCAD")

    import pythoncom
    import win32com.client

    pythoncom.CoInitialize()
    doc_a = None
    doc_b = None
    path_a: Path | None = None
    path_b: Path | None = None
    started = time.perf_counter()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)

    try:
        app = _retry(
            lambda: win32com.client.GetActiveObject(args.com_progid),
            timeout=45.0,
        )
        path_a = output.parent / f"d18-a-{time.time_ns()}.dwg"
        path_b = output.parent / f"d18-b-{time.time_ns()}.dwg"

        doc_a = _retry(lambda: app.Documents.Add())
        _retry(lambda: doc_a.SaveAs(str(path_a)), timeout=30.0)
        doc_b = _retry(lambda: app.Documents.Add())
        _retry(lambda: doc_b.SaveAs(str(path_b)), timeout=30.0)

        client = NativeBridgeClient(
            NamedPipeTransport(
                pipe_name_for_session(_session_id()),
                connect_timeout_ms=8_000,
            )
        )
        health = _retry(client.health, timeout=30.0)
        if health.get("bridge_version") != EXPECTED_BRIDGE_VERSION:
            raise AssertionError(
                f"unexpected bridge version: {health.get('bridge_version')!r}"
            )

        # Discover A while it is active, then switch to B before dispatching A's bootstrap.
        _retry(lambda: doc_a.Activate())
        discovered = client.documents_list()
        row_a = _row_for_path(discovered, path_a)
        row_b = _row_for_path(discovered, path_b)
        if row_a.get("is_active") is not True or row_a.get("document_pid") is not None:
            raise AssertionError("D18 A fixture did not start as active and uninitialized")
        if row_b.get("document_pid") is not None:
            raise AssertionError("D18 B fixture unexpectedly already has document PID metadata")
        runtime_a = str(row_a["runtime_document_id"])
        runtime_b = str(row_b["runtime_document_id"])

        _retry(lambda: doc_b.Activate())
        non_active_error = _expect_remote_error(
            lambda: client.initialize_document_identity(runtime_a),
            "DOCUMENT_NOT_ACTIVE",
        )
        after_switch = client.documents_list()
        row_a_after_switch = _row_for_path(after_switch, path_a)
        row_b_after_switch = _row_for_path(after_switch, path_b)
        if row_a_after_switch.get("document_pid") is not None:
            raise AssertionError("non-active bootstrap refusal left an orphan PID on document A")
        if row_b_after_switch.get("document_pid") is not None:
            raise AssertionError("non-active bootstrap refusal changed document B lineage")

        dbmod_before_fault = int(_retry(lambda: doc_b.GetVariable("DBMOD")))
        fault_error = _expect_remote_error(
            lambda: client.initialize_document_identity(
                runtime_b,
                fault_stage="after_pid_before_verify",
            ),
            "BOOTSTRAP_FAULT_INJECTED",
        )
        after_fault = client.documents_list()
        row_b_after_fault = _row_for_path(after_fault, path_b)
        if row_b_after_fault.get("document_pid") is not None:
            raise AssertionError("faulted bootstrap committed an orphan document PID")
        dbmod_after_fault = int(_retry(lambda: doc_b.GetVariable("DBMOD")))
        if int(_retry(lambda: doc_b.ModelSpace.Count)) != 0:
            raise AssertionError("faulted bootstrap changed the predecessor entity state")

        initialized = client.initialize_document_identity(runtime_b)
        document_pid = str(initialized["document_pid"])
        document_fp = str(initialized["document_fp"])
        if initialized.get("initialized") is not True:
            raise AssertionError("successful D18 bootstrap did not create a new PID")
        if initialized.get("readback_verified") is not True:
            raise AssertionError("successful D18 bootstrap did not verify PID readback")
        if initialized.get("entity_count") != 0:
            raise AssertionError("D18 bootstrap scope drifted from the empty current space")

        after_success = client.documents_list()
        final_a = _row_for_path(after_success, path_a)
        final_b = _row_for_path(after_success, path_b)
        if final_a.get("document_pid") is not None:
            raise AssertionError("successful B bootstrap unexpectedly changed A lineage")
        if final_b.get("document_pid") != document_pid:
            raise AssertionError("successful bootstrap PID was not durably rediscovered")

        state = client.document_state(runtime_b, document_pid=document_pid)
        if (
            state.get("document_fp") != document_fp
            or int(state.get("entity_count", -1)) != 0
        ):
            raise AssertionError("successful D18 bootstrap fingerprint readback mismatch")

        _retry(lambda: doc_b.Save(), timeout=30.0)
        saved = bool(_retry(lambda: doc_b.Saved))
        dbmod_after_save = int(_retry(lambda: doc_b.GetVariable("DBMOD")))
        if not saved or dbmod_after_save != 0:
            raise AssertionError("D18 fixture did not reach persisted-clean state after success")

        summary = {
            "schema_version": 1,
            "finding_id": "D18",
            "status": "PASS",
            "bridge_version": health["bridge_version"],
            "session_id": _session_id(),
            "non_active_runtime_error": non_active_error,
            "non_active_zero_pid_side_effect_verified": True,
            "fault_error": fault_error,
            "fault_rollback_pid_null_verified": True,
            "dbmod_before_fault": dbmod_before_fault,
            "dbmod_after_fault": dbmod_after_fault,
            "dbmod_preserved_by_abort": dbmod_after_fault == dbmod_before_fault,
            "exact_predecessor_identity_restored": True,
            "entity_state_unchanged_after_fault": True,
            "successful_document_pid": document_pid,
            "successful_document_fp": document_fp,
            "successful_readback_verified": initialized["readback_verified"],
            "successful_entity_count": initialized["entity_count"],
            "other_document_pid_remained_null": True,
            "persisted_clean_after_success": saved and dbmod_after_save == 0,
            "duration_seconds": round(time.perf_counter() - started, 3),
        }
        output.write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return summary
    finally:
        for doc in (doc_b, doc_a):
            if doc is None:
                continue
            try:
                if not bool(_retry(lambda doc=doc: doc.Saved, timeout=5.0)):
                    _retry(lambda doc=doc: doc.Save(), timeout=10.0)
                _retry(lambda doc=doc: doc.Close(False), timeout=10.0)
            except Exception:
                pass
        for target in (path_b, path_a):
            if target is None:
                continue
            try:
                target.unlink(missing_ok=True)
            except OSError:
                pass
        pythoncom.CoUninitialize()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run D18 atomic document PID bootstrap acceptance on AutoCAD 2027"
    )
    parser.add_argument("--output", required=True)
    parser.add_argument("--com-progid", default="AutoCAD.Application.26")
    args = parser.parse_args()
    print(json.dumps(_run(args), sort_keys=True))


if __name__ == "__main__":
    main()
