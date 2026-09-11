"""G1 live recovery acceptance — activation-safe R2 checkpoint restore for a batch mutation."""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any

from cdt_autocad.native_bridge.client import NativeBridgeClient
from cdt_autocad.native_bridge.transport_windows import NamedPipeTransport, pipe_name_for_session
from run_g1_batch_acceptance import EXPECTED_BRIDGE_VERSION, _retry, _session_id, _wait_command_idle, _wait_file


def _run(args: argparse.Namespace) -> dict[str, Any]:
    if os.name != "nt":
        raise RuntimeError("G1 R2 acceptance requires Windows AutoCAD")
    import pythoncom
    import win32com.client

    pythoncom.CoInitialize()
    app = None
    original_document = None
    fixture_path: Path | None = None
    restored_runtime_id: str | None = None
    started = time.perf_counter()
    try:
        app = _retry(lambda: win32com.client.GetActiveObject(args.com_progid))
        original_document = _retry(lambda: app.Documents.Add())
        _retry(lambda: original_document.Activate())
        fixture_path = Path(os.environ["LOCALAPPDATA"]) / "Temp" / f"cdt-g1-r2-{time.time_ns()}.dwg"
        _retry(lambda: original_document.SaveAs(str(fixture_path)), timeout=20.0)
        _retry(lambda: original_document.SetVariable("FILEDIA", 0))
        _retry(lambda: original_document.SetVariable("CMDECHO", 0))

        probe = Path(args.probe_dll).resolve()
        report = probe.parent / "reports" / "n4-semantic-fixture.json"
        report.unlink(missing_ok=True)
        original_document.SendCommand(f'_.NETLOAD\n"{probe}"\n')
        _wait_command_idle(original_document)
        original_document.SendCommand("CDT_N4_CREATE_FIXTURE\n")
        _wait_command_idle(original_document)
        _wait_file(report)
        fixture = json.loads(report.read_text(encoding="utf-8"))["payload"]
        document_pid = str(fixture["document_pid"])
        definition_pid = str(fixture["entities"]["block_definition"]["pid"])

        session_id = _session_id()
        client = NativeBridgeClient(NamedPipeTransport(pipe_name_for_session(session_id), connect_timeout_ms=5_000))
        health = _retry(client.health, timeout=20.0)
        if health.get("bridge_version") != EXPECTED_BRIDGE_VERSION:
            raise AssertionError("unexpected bridge version")
        active = [item for item in client.documents_list() if item.get("is_active")]
        if len(active) != 1 or active[0].get("document_pid") != document_pid:
            raise AssertionError("fixture did not become the active PID-bound document")
        runtime_id = str(active[0]["runtime_document_id"])
        before = client.document_snapshot(runtime_id, document_pid=document_pid)
        parent_fp = before.fingerprints.document_fp
        if parent_fp is None:
            raise AssertionError("fixture has no document fingerprint")
        baseline_count = int(_retry(lambda: original_document.ModelSpace.Count, timeout=10.0))

        committed = client.batch_insert_blocks_chunk(
            runtime_id,
            document_pid=document_pid,
            expected_parent_fp=parent_fp,
            inserts=(
                {"definition_pid": definition_pid, "position": [2600.0, 2600.0, 0.0], "rotation": 0.25, "scale": 1.2},
                {"definition_pid": definition_pid, "position": [2640.0, 2620.0, 0.0], "rotation": 0.5, "scale": 1.4},
            ),
        )
        if committed.get("outcome") != "COMMITTED_VERIFIED":
            raise AssertionError("batch insert did not commit before R2 test")
        if int(_retry(lambda: original_document.ModelSpace.Count, timeout=10.0)) != baseline_count + 2:
            raise AssertionError("batch insert commit did not change ModelSpace by two")
        checkpoint = committed.get("recovery_checkpoint")
        if not isinstance(checkpoint, dict):
            raise AssertionError("batch insert did not expose checkpoint")

        r2_started = time.perf_counter()
        rollback = client.resolve_recovery(
            runtime_id,
            document_pid=document_pid,
            checkpoint_id=str(checkpoint["checkpoint_id"]),
            checkpoint_artifact_fp=str(checkpoint["checkpoint_artifact_fp"]),
            expected_restore_fp=parent_fp,
            strategy="R2_CHECKPOINT_RESTORE",
        )
        r2_ms = (time.perf_counter() - r2_started) * 1000.0
        if rollback.get("outcome") != "ROLLED_BACK_VERIFIED" or rollback.get("post_document_fp") != parent_fp:
            raise AssertionError(f"batch R2 did not restore exact predecessor: {rollback}")
        restored_runtime_id = str(rollback.get("runtime_document_id") or "")
        if not restored_runtime_id or restored_runtime_id == runtime_id:
            raise AssertionError("R2 did not rebind to a new runtime_document_id")
        if client.recoveries_list():
            raise AssertionError("batch R2 left pending recovery")
        restored = client.document_snapshot(restored_runtime_id, document_pid=document_pid)
        if restored.fingerprints.document_fp != parent_fp:
            raise AssertionError("restored document fingerprint differs from checkpoint predecessor")

        # The original COM document object was closed by R2; reacquire the restored file and verify membership.
        restored_com = None
        for index in range(app.Documents.Count):
            candidate = app.Documents.Item(index)
            if str(_retry(lambda c=candidate: c.FullName, timeout=5.0)).lower() == str(fixture_path).lower():
                restored_com = candidate
                break
        if restored_com is None:
            raise AssertionError("R2 restored document is not visible through AutoCAD COM")
        final_count = int(_retry(lambda: restored_com.ModelSpace.Count, timeout=10.0))
        if final_count != baseline_count:
            raise AssertionError("R2 restored ModelSpace membership differs from predecessor")
        _retry(lambda: restored_com.Close(False), timeout=10.0)
        original_document = None

        summary = {
            "schema_version": 1,
            "status": "PASS",
            "bridge_version": health["bridge_version"],
            "bridge_process_id": health["process_id"],
            "session_id": session_id,
            "document_pid": document_pid,
            "original_runtime_document_id": runtime_id,
            "restored_runtime_document_id": restored_runtime_id,
            "expected_restore_fp": parent_fp,
            "baseline_modelspace_count": baseline_count,
            "final_modelspace_count": final_count,
            "affected_count": 2,
            "r2_status": "ROLLED_BACK_VERIFIED",
            "r2_latency_ms": round(r2_ms, 3),
            "pending_recoveries": len(client.recoveries_list()),
            "duration_seconds": round(time.perf_counter() - started, 3),
        }
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return summary
    finally:
        if original_document is not None:
            try:
                _retry(lambda: original_document.Close(False), timeout=5.0)
            except Exception:
                pass
        if fixture_path is not None:
            try:
                fixture_path.unlink(missing_ok=True)
            except OSError:
                pass
        pythoncom.CoUninitialize()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--probe-dll", required=True)
    parser.add_argument("--com-progid", default="AutoCAD.Application.26")
    args = parser.parse_args()
    print(json.dumps(_run(args), sort_keys=True))


if __name__ == "__main__":
    main()
