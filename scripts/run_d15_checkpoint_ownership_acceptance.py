"""D15 live acceptance — checkpoint ownership and lost-response recovery on AutoCAD 2027.
Wing: ops | Topic: native-d15-checkpoint-ownership | Updated: 2026-09-19 14:20
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any
from uuid import uuid4

from cdt_autocad.native_bridge.client import (
    BridgeRemoteError,
    NativeBridgeClient,
)
from cdt_autocad.native_bridge.protocol import owner_request_fingerprint
from cdt_autocad.native_bridge.transport_windows import (
    NamedPipeTransport,
    pipe_name_for_session,
)

EXPECTED_BRIDGE_VERSION = "0.8.5-d15"


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
    raise RuntimeError("acceptance operation did not become ready before timeout") from last_error


class _DropLogicalBeginResponseOnce:
    """Deliver one real logical.begin to the bridge, then hide its response from the caller."""

    def __init__(self, transport: NamedPipeTransport):
        self._transport = transport
        self.dropped = False

    def round_trip(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        response = self._transport.round_trip(payload)
        if not self.dropped and payload.get("operation") == "bridge.logical.begin":
            self.dropped = True
            raise TimeoutError("D15 acceptance intentionally dropped logical.begin response")
        return response


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


def _run(args: argparse.Namespace) -> dict[str, Any]:
    if os.name != "nt":
        raise RuntimeError("D15 live acceptance requires Windows AutoCAD")

    import pythoncom
    import win32com.client

    pythoncom.CoInitialize()
    created_document = None
    disposable_path: Path | None = None
    started = time.perf_counter()
    try:
        application = _retry(
            lambda: win32com.client.GetActiveObject(args.com_progid),
            timeout=45.0,
        )
        created_document = _retry(lambda: application.Documents.Add())
        _retry(lambda: created_document.Activate())
        disposable_path = (
            Path(os.environ["LOCALAPPDATA"])
            / "Temp"
            / f"cdt-d15-{time.time_ns()}.dwg"
        )
        _retry(lambda: created_document.SaveAs(str(disposable_path)), timeout=20.0)

        session_id = _session_id()
        pipe_name = pipe_name_for_session(session_id)
        base_transport = NamedPipeTransport(pipe_name, connect_timeout_ms=8_000)
        client = NativeBridgeClient(base_transport)
        health = _retry(client.health, timeout=30.0)
        if health.get("bridge_version") != EXPECTED_BRIDGE_VERSION:
            raise AssertionError(
                f"unexpected bridge version: {health.get('bridge_version')!r}"
            )

        active_rows = [row for row in client.documents_list() if row.get("is_active") is True]
        if len(active_rows) != 1:
            raise AssertionError("fixture did not expose exactly one active AutoCAD document")
        runtime_document_id = str(active_rows[0]["runtime_document_id"])
        if active_rows[0].get("document_pid") is not None:
            raise AssertionError("fresh D15 fixture unexpectedly already has document PID metadata")

        initialized = client.initialize_document_identity(runtime_document_id)
        document_pid = str(initialized["document_pid"])
        baseline_fp = str(initialized["document_fp"])
        baseline_count = int(initialized["entity_count"])
        if baseline_count != 0:
            raise AssertionError("D15 fixture must start with an empty current space")

        owner_a = str(uuid4())
        owner_b = str(uuid4())
        owner_a_fp = owner_request_fingerprint(owner_a)
        drop_transport = _DropLogicalBeginResponseOnce(
            NamedPipeTransport(pipe_name, connect_timeout_ms=8_000)
        )
        lost_response_client = NativeBridgeClient(drop_transport)
        try:
            lost_response_client.begin_logical_batch(
                runtime_document_id,
                document_pid=document_pid,
                expected_parent_fp=baseline_fp,
                request_id=owner_a,
            )
        except TimeoutError:
            pass
        else:
            raise AssertionError("D15 fixture failed to simulate the lost logical.begin response")
        if not drop_transport.dropped:
            raise AssertionError("logical.begin response was not actually dropped after delivery")

        reconnect = NativeBridgeClient(
            NamedPipeTransport(pipe_name, connect_timeout_ms=8_000)
        )
        pending = reconnect.recoveries_list()
        if any("owner_request_id" in row for row in pending):
            raise AssertionError("recovery.list disclosed a raw checkpoint owner request id")
        owned = [
            row
            for row in pending
            if row.get("operation") == "logical.batch"
            and row.get("document_pid") == document_pid
            and row.get("expected_restore_fp") == baseline_fp
            and row.get("owner_request_fp") == owner_a_fp
        ]
        if len(owned) != 1:
            raise AssertionError(
                "originating owner could not uniquely rediscover its persisted checkpoint"
            )
        checkpoint = owned[0]
        checkpoint_id = str(checkpoint["checkpoint_id"])
        artifact_fp = str(checkpoint["checkpoint_artifact_fp"])
        manifest_path = (
            Path(os.environ["LOCALAPPDATA"])
            / "CDT-AutoCAD"
            / "native-recovery"
            / f"{checkpoint_id[3:]}.json"
        )
        manifest_text = manifest_path.read_text(encoding="utf-8")
        if owner_a in manifest_text:
            raise AssertionError("checkpoint manifest persisted the raw owner request id")
        if owner_a_fp not in manifest_text:
            raise AssertionError("checkpoint manifest did not persist the owner fingerprint")

        foreign_begin_error = _expect_remote_error(
            lambda: reconnect.begin_logical_batch(
                runtime_document_id,
                document_pid=document_pid,
                expected_parent_fp=baseline_fp,
                request_id=owner_b,
            ),
            "RECOVERY_PENDING",
        )
        foreign_resolve_error = _expect_remote_error(
            lambda: reconnect.resolve_recovery(
                runtime_document_id,
                document_pid=document_pid,
                checkpoint_id=checkpoint_id,
                checkpoint_artifact_fp=artifact_fp,
                owner_request_id=owner_b,
                expected_restore_fp=baseline_fp,
                strategy="R2_CHECKPOINT_RESTORE",
            ),
            "RECOVERY_BINDING_MISMATCH",
        )
        foreign_finalize_error = _expect_remote_error(
            lambda: reconnect.finalize_recovery(
                runtime_document_id,
                document_pid=document_pid,
                checkpoint_id=checkpoint_id,
                checkpoint_artifact_fp=artifact_fp,
                owner_request_id=owner_b,
                accepted_post_fp=baseline_fp,
            ),
            "RECOVERY_BINDING_MISMATCH",
        )

        foreign_binding = {
            "checkpoint_id": checkpoint_id,
            "checkpoint_artifact_fp": artifact_fp,
            "expected_restore_fp": baseline_fp,
            "owner_request_id": owner_b,
        }
        foreign_chunk_error = _expect_remote_error(
            lambda: reconnect.batch_create_chunk(
                runtime_document_id,
                document_pid=document_pid,
                expected_parent_fp=baseline_fp,
                entities=(
                    {
                        "kind": "line",
                        "start": [0.0, 0.0, 0.0],
                        "end": [10.0, 0.0, 0.0],
                    },
                ),
                logical_transaction=foreign_binding,
            ),
            "RECOVERY_BINDING_MISMATCH",
        )

        state_after_foreign = reconnect.document_state(
            runtime_document_id,
            document_pid=document_pid,
        )
        if (
            state_after_foreign.get("document_fp") != baseline_fp
            or int(state_after_foreign["entity_count"]) != baseline_count
        ):
            raise AssertionError(
                "foreign checkpoint operation changed CAD state before refusal"
            )
        pending_after_foreign = reconnect.recoveries_list()
        if (
            len(pending_after_foreign) != 1
            or "owner_request_id" in pending_after_foreign[0]
            or pending_after_foreign[0].get("owner_request_fp") != owner_a_fp
        ):
            raise AssertionError(
                "foreign checkpoint operation changed persisted recovery ownership"
            )

        recovered = reconnect.resolve_recovery(
            runtime_document_id,
            document_pid=document_pid,
            checkpoint_id=checkpoint_id,
            checkpoint_artifact_fp=artifact_fp,
            owner_request_id=owner_a,
            expected_restore_fp=baseline_fp,
            strategy="R2_CHECKPOINT_RESTORE",
        )
        rollback = recovered.get("rollback")
        if not isinstance(rollback, Mapping):
            raise AssertionError("owner recovery did not return rollback evidence")
        if (
            recovered.get("outcome") != "ROLLED_BACK_VERIFIED"
            or rollback.get("status") != "ROLLED_BACK_VERIFIED"
            or rollback.get("expected_restore_fp") != baseline_fp
            or rollback.get("actual_restore_fp") != baseline_fp
        ):
            raise AssertionError("originating owner did not restore the exact predecessor")

        rebound_runtime_id = str(recovered.get("runtime_document_id") or "")
        if not rebound_runtime_id:
            raise AssertionError("owner R2 recovery did not return a rebound runtime document")
        final_state = reconnect.document_state(
            rebound_runtime_id,
            document_pid=document_pid,
        )
        if (
            final_state.get("document_fp") != baseline_fp
            or int(final_state["entity_count"]) != baseline_count
        ):
            raise AssertionError("owner recovery did not preserve the exact baseline state")
        final_pending = reconnect.recoveries_list()
        if final_pending:
            raise AssertionError("D15 acceptance ended with pending recovery checkpoints")

        summary = {
            "schema_version": 1,
            "finding_id": "D15",
            "status": "PASS",
            "bridge_version": health["bridge_version"],
            "session_id": session_id,
            "document_pid": document_pid,
            "baseline_document_fp": baseline_fp,
            "baseline_entity_count": baseline_count,
            "checkpoint_id": checkpoint_id,
            "owner_fingerprint_exposure_verified": True,
            "raw_owner_absent_from_manifest_verified": True,
            "lost_begin_response_simulated": True,
            "originating_owner_rediscovery_verified": True,
            "foreign_begin_error": foreign_begin_error,
            "foreign_resolve_error": foreign_resolve_error,
            "foreign_finalize_error": foreign_finalize_error,
            "foreign_chunk_error": foreign_chunk_error,
            "foreign_refusal_before_cad_mutation_verified": True,
            "owner_r2_exact_restore_verified": True,
            "pending_recoveries": 0,
            "duration_seconds": round(time.perf_counter() - started, 3),
        }
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return summary
    finally:
        try:
            application = win32com.client.GetActiveObject(args.com_progid)
            active = application.ActiveDocument
            if active is not None:
                full_name = str(getattr(active, "FullName", "") or "")
                if disposable_path is not None and full_name:
                    if Path(full_name).resolve() == disposable_path.resolve():
                        _retry(lambda: active.Close(False), timeout=5.0)
        except Exception:
            pass
        if disposable_path is not None:
            try:
                disposable_path.unlink(missing_ok=True)
            except OSError:
                pass
        pythoncom.CoUninitialize()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run D15 checkpoint-ownership live acceptance on AutoCAD 2027"
    )
    parser.add_argument("--output", required=True)
    parser.add_argument("--com-progid", default="AutoCAD.Application.26")
    args = parser.parse_args()
    print(json.dumps(_run(args), sort_keys=True))


if __name__ == "__main__":
    main()
