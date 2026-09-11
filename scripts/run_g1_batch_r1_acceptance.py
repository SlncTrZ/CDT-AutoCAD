"""G1 live recovery acceptance — exact R1 compensation for batch transform and block insert."""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any

from cdt_autocad.native_bridge.client import NativeBridgeClient
from cdt_autocad.native_bridge.transport_windows import NamedPipeTransport, pipe_name_for_session
from run_g1_batch_acceptance import (
    EXPECTED_BRIDGE_VERSION,
    _retry,
    _session_id,
    _wait_command_idle,
    _wait_file,
)


def _checkpoint(receipt: dict[str, Any]) -> dict[str, str]:
    value = receipt.get("recovery_checkpoint")
    if not isinstance(value, dict):
        raise AssertionError("committed batch mutation did not expose recovery checkpoint")
    return {key: str(value[key]) for key in ("checkpoint_id", "checkpoint_artifact_fp", "expected_restore_fp")}


def _run(args: argparse.Namespace) -> dict[str, Any]:
    if os.name != "nt":
        raise RuntimeError("G1 R1 acceptance requires Windows AutoCAD")

    import pythoncom
    import win32com.client

    pythoncom.CoInitialize()
    document = None
    fixture_path: Path | None = None
    started = time.perf_counter()
    try:
        app = _retry(lambda: win32com.client.GetActiveObject(args.com_progid))
        document = _retry(lambda: app.Documents.Add())
        _retry(lambda: document.Activate())
        fixture_path = Path(os.environ["LOCALAPPDATA"]) / "Temp" / f"cdt-g1-r1-{time.time_ns()}.dwg"
        _retry(lambda: document.SaveAs(str(fixture_path)), timeout=20.0)
        _retry(lambda: document.SetVariable("FILEDIA", 0))
        _retry(lambda: document.SetVariable("CMDECHO", 0))

        probe = Path(args.probe_dll).resolve()
        report = probe.parent / "reports" / "n4-semantic-fixture.json"
        report.unlink(missing_ok=True)
        document.SendCommand(f'_.NETLOAD\n"{probe}"\n')
        _wait_command_idle(document)
        document.SendCommand("CDT_N4_CREATE_FIXTURE\n")
        _wait_command_idle(document)
        _wait_file(report)
        fixture = json.loads(report.read_text(encoding="utf-8"))["payload"]
        document_pid = str(fixture["document_pid"])

        session_id = _session_id()
        client = NativeBridgeClient(NamedPipeTransport(pipe_name_for_session(session_id), connect_timeout_ms=5_000))
        health = _retry(client.health, timeout=20.0)
        if health.get("bridge_version") != EXPECTED_BRIDGE_VERSION:
            raise AssertionError("unexpected bridge version")
        active = [item for item in client.documents_list() if item.get("is_active")]
        if len(active) != 1 or active[0].get("document_pid") != document_pid:
            raise AssertionError("fixture did not become the single active PID-bound document")
        runtime_id = str(active[0]["runtime_document_id"])
        snapshot = client.document_snapshot(runtime_id, document_pid=document_pid)
        parent_fp = snapshot.fingerprints.document_fp
        if parent_fp is None:
            raise AssertionError("fixture has no document fingerprint")
        fixture_entities = fixture["entities"]
        targets = tuple(
            str(fixture_entities[label]["pid"])
            for label in ("line", "circle", "arc", "lwpolyline")
        )
        definition_pid = str(fixture_entities["block_definition"]["pid"])
        baseline_count = int(_retry(lambda: document.ModelSpace.Count, timeout=10.0))

        transform_started = time.perf_counter()
        transformed = client.batch_transform_chunk(
            runtime_id,
            document_pid=document_pid,
            expected_parent_fp=parent_fp,
            semantic_pids=targets,
            transform={"kind": "translate", "delta": [77.0, 33.0, 0.0]},
        )
        if transformed.get("outcome") != "COMMITTED_VERIFIED":
            raise AssertionError("batch transform did not commit before R1 test")
        transform_checkpoint = _checkpoint(transformed)
        if not client.recoveries_list():
            raise AssertionError("committed transform checkpoint was not pending before R1")
        transform_rollback = client.resolve_recovery(
            runtime_id,
            document_pid=document_pid,
            checkpoint_id=transform_checkpoint["checkpoint_id"],
            checkpoint_artifact_fp=transform_checkpoint["checkpoint_artifact_fp"],
            expected_restore_fp=parent_fp,
            strategy="R1_COMPENSATE",
        )
        transform_ms = (time.perf_counter() - transform_started) * 1000.0
        if transform_rollback.get("outcome") != "ROLLED_BACK_VERIFIED" or transform_rollback.get("post_document_fp") != parent_fp:
            raise AssertionError(f"batch transform R1 did not restore exact predecessor: {transform_rollback}")
        if client.recoveries_list():
            raise AssertionError("batch transform R1 left pending recovery")
        if int(_retry(lambda: document.ModelSpace.Count, timeout=10.0)) != baseline_count:
            raise AssertionError("batch transform R1 changed ModelSpace membership")

        inserts = tuple(
            {
                "definition_pid": definition_pid,
                "position": [1800.0 + index * 20.0, 2300.0 + index * 10.0, 0.0],
                "rotation": 0.1 * index,
                "scale": 1.0 + 0.1 * index,
            }
            for index in range(4)
        )
        insert_started = time.perf_counter()
        inserted = client.batch_insert_blocks_chunk(
            runtime_id,
            document_pid=document_pid,
            expected_parent_fp=parent_fp,
            inserts=inserts,
        )
        if inserted.get("outcome") != "COMMITTED_VERIFIED":
            raise AssertionError("block insert batch did not commit before R1 test")
        if int(_retry(lambda: document.ModelSpace.Count, timeout=10.0)) != baseline_count + 4:
            raise AssertionError("block insert commit did not create expected ModelSpace delta")
        insert_checkpoint = _checkpoint(inserted)
        if not client.recoveries_list():
            raise AssertionError("committed block insert checkpoint was not pending before R1")
        insert_rollback = client.resolve_recovery(
            runtime_id,
            document_pid=document_pid,
            checkpoint_id=insert_checkpoint["checkpoint_id"],
            checkpoint_artifact_fp=insert_checkpoint["checkpoint_artifact_fp"],
            expected_restore_fp=parent_fp,
            strategy="R1_COMPENSATE",
        )
        insert_ms = (time.perf_counter() - insert_started) * 1000.0
        if insert_rollback.get("outcome") != "ROLLED_BACK_VERIFIED" or insert_rollback.get("post_document_fp") != parent_fp:
            raise AssertionError(f"block insert R1 did not restore exact predecessor: {insert_rollback}")
        if client.recoveries_list():
            raise AssertionError("block insert R1 left pending recovery")
        final_count = int(_retry(lambda: document.ModelSpace.Count, timeout=10.0))
        if final_count != baseline_count:
            raise AssertionError("block insert R1 did not erase committed references exactly")
        final_snapshot = client.document_snapshot(runtime_id, document_pid=document_pid)
        if final_snapshot.fingerprints.document_fp != parent_fp:
            raise AssertionError("final fingerprint differs from original after both R1 compensations")

        summary = {
            "schema_version": 1,
            "status": "PASS",
            "bridge_version": health["bridge_version"],
            "bridge_process_id": health["process_id"],
            "session_id": session_id,
            "document_pid": document_pid,
            "runtime_document_id": runtime_id,
            "baseline_modelspace_count": baseline_count,
            "final_modelspace_count": final_count,
            "expected_restore_fp": parent_fp,
            "transform_r1": {"status": "ROLLED_BACK_VERIFIED", "latency_ms": round(transform_ms, 3), "affected_count": len(targets)},
            "insert_r1": {"status": "ROLLED_BACK_VERIFIED", "latency_ms": round(insert_ms, 3), "affected_count": 4},
            "pending_recoveries": len(client.recoveries_list()),
            "duration_seconds": round(time.perf_counter() - started, 3),
        }
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return summary
    finally:
        if document is not None:
            try:
                _retry(lambda: document.Close(False), timeout=5.0)
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
