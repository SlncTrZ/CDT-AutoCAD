"""G1-B/C live acceptance — batch transform + PID-bound block insert on real AutoCAD."""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any

from run_g1_batch_acceptance import (
    EXPECTED_BRIDGE_VERSION,
    _finalize_chunk,
    _retry,
    _session_id,
    _wait_command_idle,
    _wait_file,
)

from cdt_autocad.native_bridge.client import NativeBridgeClient
from cdt_autocad.native_bridge.transport_windows import NamedPipeTransport, pipe_name_for_session


def _run(args: argparse.Namespace) -> dict[str, Any]:
    if os.name != "nt":
        raise RuntimeError("G1-B/C live acceptance requires Windows AutoCAD")

    import pythoncom
    import win32com.client

    pythoncom.CoInitialize()
    created_document = None
    fixture_document_path: Path | None = None
    started = time.perf_counter()
    try:
        application = _retry(lambda: win32com.client.GetActiveObject(args.com_progid))
        created_document = _retry(lambda: application.Documents.Add())
        _retry(lambda: created_document.Activate())
        fixture_document_path = Path(os.environ["LOCALAPPDATA"]) / "Temp" / f"cdt-g1-bc-{time.time_ns()}.dwg"
        _retry(lambda: created_document.SaveAs(str(fixture_document_path)), timeout=20.0)
        _retry(lambda: created_document.SetVariable("FILEDIA", 0))
        _retry(lambda: created_document.SetVariable("CMDECHO", 0))

        probe_dll = Path(args.probe_dll).resolve()
        if not probe_dll.is_file():
            raise RuntimeError(f"fixture probe DLL not found: {probe_dll}")
        trusted_paths = str(_retry(lambda: created_document.GetVariable("TRUSTEDPATHS")))
        if str(probe_dll.parent).lower() not in trusted_paths.lower():
            raise RuntimeError("fixture probe directory is not already trusted")

        report_path = probe_dll.parent / "reports" / "n4-semantic-fixture.json"
        report_path.unlink(missing_ok=True)
        created_document.SendCommand(f'_.NETLOAD\n"{probe_dll}"\n')
        _wait_command_idle(created_document)
        created_document.SendCommand("CDT_N4_CREATE_FIXTURE\n")
        _wait_command_idle(created_document)
        _wait_file(report_path)
        fixture = json.loads(report_path.read_text(encoding="utf-8"))["payload"]
        document_pid = str(fixture["document_pid"])

        session_id = _session_id()
        client = NativeBridgeClient(NamedPipeTransport(pipe_name_for_session(session_id), connect_timeout_ms=5_000))
        health = _retry(client.health, timeout=20.0)
        if health.get("bridge_version") != EXPECTED_BRIDGE_VERSION:
            raise AssertionError(f"unexpected bridge version: {health.get('bridge_version')!r}")
        operations = set(health.get("mutation_operations", []))
        for required in ("entity.batch.create", "entity.batch.transform", "entity.batch.insert_blocks"):
            if required not in operations:
                raise AssertionError(f"bridge does not advertise {required}")

        active = [item for item in client.documents_list() if item.get("is_active")]
        if len(active) != 1 or active[0].get("document_pid") != document_pid:
            raise AssertionError("fixture did not bind to one active PID-bearing document")
        runtime_document_id = str(active[0]["runtime_document_id"])
        initial = client.document_snapshot(runtime_document_id, document_pid=document_pid)
        parent_fp = initial.fingerprints.document_fp
        if parent_fp is None:
            raise AssertionError("initial fixture has no document fingerprint")

        fixture_entities = fixture["entities"]
        target_pids = tuple(
            str(fixture_entities[label]["pid"])
            for label in ("line", "circle", "arc", "lwpolyline")
        )
        definition_pid = str(fixture_entities["block_definition"]["pid"])
        baseline_count = int(_retry(lambda: created_document.ModelSpace.Count, timeout=10.0))

        transform_fault_started = time.perf_counter()
        transform_fault = client.batch_transform_chunk(
            runtime_document_id,
            document_pid=document_pid,
            expected_parent_fp=parent_fp,
            semantic_pids=target_pids,
            transform={"kind": "translate", "delta": [1.0, 0.0, 0.0]},
            fault_stage="after_apply_before_commit",
        )
        transform_fault_ms = (time.perf_counter() - transform_fault_started) * 1000.0
        if transform_fault.get("outcome") != "ROLLED_BACK_VERIFIED" or transform_fault.get("post_document_fp") != parent_fp:
            raise AssertionError("G1-B injected R0 rollback did not restore exact predecessor")
        if int(_retry(lambda: created_document.ModelSpace.Count, timeout=10.0)) != baseline_count:
            raise AssertionError("G1-B R0 rollback changed ModelSpace count")
        if client.recoveries_list():
            raise AssertionError("G1-B R0 rollback left pending recovery")

        transform_results: list[dict[str, Any]] = []
        for transform in (
            {"kind": "translate", "delta": [50.0, 25.0, 0.0]},
            {"kind": "rotate_z", "center": [0.0, 0.0, 0.0], "angle": 0.2},
            {"kind": "scale_uniform", "center": [0.0, 0.0, 0.0], "factor": 1.1},
        ):
            call_started = time.perf_counter()
            receipt = client.batch_transform_chunk(
                runtime_document_id,
                document_pid=document_pid,
                expected_parent_fp=parent_fp,
                semantic_pids=target_pids,
                transform=transform,
            )
            latency_ms = (time.perf_counter() - call_started) * 1000.0
            if receipt.get("outcome") != "COMMITTED_VERIFIED":
                raise AssertionError(f"G1-B {transform['kind']} was not COMMITTED_VERIFIED: {receipt}")
            if tuple(receipt.get("affected_semantic_pids", ())) != target_pids:
                raise AssertionError("G1-B affected PID order changed")
            parent_fp = str(receipt["post_document_fp"])
            _finalize_chunk(client, runtime_document_id, document_pid, receipt)
            if client.recoveries_list():
                raise AssertionError("G1-B accepted transform left pending recovery")
            transform_results.append({"kind": transform["kind"], "latency_ms": round(latency_ms, 3), "post_document_fp": parent_fp})

        insert_fault_started = time.perf_counter()
        insert_fault = client.batch_insert_blocks_chunk(
            runtime_document_id,
            document_pid=document_pid,
            expected_parent_fp=parent_fp,
            inserts=(
                {"definition_pid": definition_pid, "position": [1400.0, 2100.0, 0.0], "rotation": 0.0, "scale": 1.0},
            ),
            fault_stage="after_apply_before_commit",
        )
        insert_fault_ms = (time.perf_counter() - insert_fault_started) * 1000.0
        if insert_fault.get("outcome") != "ROLLED_BACK_VERIFIED" or insert_fault.get("post_document_fp") != parent_fp:
            raise AssertionError("G1-C injected R0 rollback did not restore exact predecessor")
        if int(_retry(lambda: created_document.ModelSpace.Count, timeout=10.0)) != baseline_count:
            raise AssertionError("G1-C R0 rollback leaked block reference")
        if client.recoveries_list():
            raise AssertionError("G1-C R0 rollback left pending recovery")

        inserts = tuple(
            {
                "definition_pid": definition_pid,
                "position": [1500.0 + index * 20.0, 2200.0 + index * 10.0, 0.0],
                "rotation": 0.15 * index,
                "scale": 1.0 + 0.1 * index,
            }
            for index in range(4)
        )
        insert_started = time.perf_counter()
        insert_receipt = client.batch_insert_blocks_chunk(
            runtime_document_id,
            document_pid=document_pid,
            expected_parent_fp=parent_fp,
            inserts=inserts,
        )
        insert_latency_ms = (time.perf_counter() - insert_started) * 1000.0
        if insert_receipt.get("outcome") != "COMMITTED_VERIFIED":
            raise AssertionError(f"G1-C insert batch was not COMMITTED_VERIFIED: {insert_receipt}")
        inserted_pids = tuple(str(value) for value in insert_receipt.get("affected_semantic_pids", ()))
        if len(inserted_pids) != 4 or len(set(inserted_pids)) != 4:
            raise AssertionError("G1-C did not return four unique inserted PIDs")
        parent_fp = str(insert_receipt["post_document_fp"])
        _finalize_chunk(client, runtime_document_id, document_pid, insert_receipt)
        if client.recoveries_list():
            raise AssertionError("G1-C accepted insert left pending recovery")

        final_count = int(_retry(lambda: created_document.ModelSpace.Count, timeout=10.0))
        if final_count != baseline_count + 4:
            raise AssertionError("G1-C ModelSpace count does not match committed insert count")
        final_snapshot = client.document_snapshot(runtime_document_id, document_pid=document_pid)
        final_pids = {entity.semantic_pid for entity in final_snapshot.entities}
        if not set(target_pids).issubset(final_pids) or not set(inserted_pids).issubset(final_pids):
            raise AssertionError("G1-B/C final semantic snapshot lost target or inserted PID identity")
        _retry(lambda: created_document.Save())

        summary = {
            "schema_version": 1,
            "status": "PASS",
            "bridge_version": health["bridge_version"],
            "bridge_process_id": health["process_id"],
            "session_id": session_id,
            "runtime_document_id": runtime_document_id,
            "document_pid": document_pid,
            "target_pids": list(target_pids),
            "definition_pid": definition_pid,
            "transform_fault": {"outcome": "ROLLED_BACK_VERIFIED", "latency_ms": round(transform_fault_ms, 3)},
            "transform_commits": transform_results,
            "insert_fault": {"outcome": "ROLLED_BACK_VERIFIED", "latency_ms": round(insert_fault_ms, 3)},
            "insert_commit": {"count": 4, "unique_pids": len(set(inserted_pids)), "latency_ms": round(insert_latency_ms, 3)},
            "baseline_modelspace_count": baseline_count,
            "final_modelspace_count": final_count,
            "final_document_fp": parent_fp,
            "pending_recoveries": len(client.recoveries_list()),
            "public_tool_contract_changed": False,
            "duration_seconds": round(time.perf_counter() - started, 3),
        }
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return summary
    finally:
        if created_document is not None:
            try:
                _retry(lambda: created_document.Close(False), timeout=5.0)
            except Exception:
                pass
        if fixture_document_path is not None:
            try:
                fixture_document_path.unlink(missing_ok=True)
            except OSError:
                pass
        pythoncom.CoUninitialize()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run G1-B/C live acceptance in AutoCAD Session 1")
    parser.add_argument("--output", required=True)
    parser.add_argument("--probe-dll", required=True)
    parser.add_argument("--com-progid", default="AutoCAD.Application.26")
    args = parser.parse_args()
    summary = _run(args)
    print(json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    main()
