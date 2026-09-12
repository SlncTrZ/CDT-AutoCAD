"""G1-A live acceptance — generic batch-create chunks on real AutoCAD.
Wing: ops | Topic: generic-batch-g1 | Updated: 2026-09-11 12:45
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

from cdt_autocad.native_bridge.client import NativeBridgeClient
from cdt_autocad.native_bridge.transport_windows import NamedPipeTransport, pipe_name_for_session

EXPECTED_BRIDGE_VERSION = "0.6.0-g1"
EXPECTED_CHUNK_LIMIT = 32
EXPECTED_SEMANTIC_LIMIT = 2048


def _session_id() -> int:
    value = ctypes.c_ulong()
    if not ctypes.windll.kernel32.ProcessIdToSessionId(os.getpid(), ctypes.byref(value)):
        raise RuntimeError("could not resolve Windows session id")
    return int(value.value)


def _retry(action: Callable[[], Any], *, timeout: float = 15.0, delay: float = 0.1) -> Any:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            return action()
        except Exception as exc:  # COM busy HRESULTs are intentionally retried only in this harness.
            last_error = exc
            time.sleep(delay)
    raise RuntimeError("acceptance operation did not become ready before timeout") from last_error


def _wait_command_idle(document: Any, *, timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        names = str(_retry(lambda: document.GetVariable("CMDNAMES"), timeout=2.0) or "").strip()
        if not names:
            return
        time.sleep(0.1)
    raise RuntimeError("AutoCAD command did not return to idle")


def _wait_file(path: Path, *, timeout: float = 20.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.is_file() and path.stat().st_size > 0:
            return
        time.sleep(0.1)
    raise RuntimeError(f"fixture report was not written: {path}")


def _batch_entities(start_index: int, count: int) -> tuple[dict[str, Any], ...]:
    entities: list[dict[str, Any]] = []
    for index in range(start_index, start_index + count):
        column = index * 12.0
        family = index % 4
        if family == 0:
            entities.append(
                {
                    "kind": "line",
                    "start": [column, 1000.0, 0.0],
                    "end": [column + 5.0, 1000.0, 0.0],
                }
            )
        elif family == 1:
            entities.append(
                {
                    "kind": "circle",
                    "center": [column, 1100.0, 0.0],
                    "radius": 1.0 + (index % 3),
                }
            )
        elif family == 2:
            entities.append(
                {
                    "kind": "arc",
                    "center": [column, 1200.0, 0.0],
                    "radius": 2.0 + (index % 2),
                    "start_angle": 0.0,
                    "end_angle": 1.25,
                }
            )
        else:
            entities.append(
                {
                    "kind": "lwpolyline",
                    "points": [
                        [column, 1300.0],
                        [column + 4.0, 1300.0],
                        [column + 2.0, 1303.0],
                    ],
                    "closed": True,
                }
            )
    return tuple(entities)


def _finalize_chunk(
    client: NativeBridgeClient,
    runtime_document_id: str,
    document_pid: str,
    receipt: dict[str, Any],
) -> None:
    checkpoint = receipt.get("recovery_checkpoint")
    if not isinstance(checkpoint, dict):
        raise AssertionError("successful batch chunk did not return a recovery checkpoint")
    finalized = client.finalize_recovery(
        runtime_document_id,
        document_pid=document_pid,
        checkpoint_id=str(checkpoint["checkpoint_id"]),
        checkpoint_artifact_fp=str(checkpoint["checkpoint_artifact_fp"]),
        accepted_post_fp=str(receipt["post_document_fp"]),
    )
    if finalized.get("status") != "FINALIZED":
        raise AssertionError("batch checkpoint did not finalize after accepted commit")


def _run(args: argparse.Namespace) -> dict[str, Any]:
    if os.name != "nt":
        raise RuntimeError("G1 live acceptance requires Windows AutoCAD")

    import pythoncom
    import win32com.client

    pythoncom.CoInitialize()
    created_document = None
    fixture_document_path: Path | None = None
    started = time.perf_counter()
    try:
        application = _retry(lambda: win32com.client.GetActiveObject(args.com_progid))
        original_documents = [str(application.Documents.Item(index).Name) for index in range(application.Documents.Count)]
        created_document = _retry(lambda: application.Documents.Add())
        _retry(lambda: created_document.Activate())
        fixture_document_path = (
            Path(os.environ["LOCALAPPDATA"])
            / "Temp"
            / f"cdt-g1-batch-{time.time_ns()}.dwg"
        )
        _retry(lambda: created_document.SaveAs(str(fixture_document_path)), timeout=20.0)
        _retry(lambda: created_document.SetVariable("FILEDIA", 0))
        _retry(lambda: created_document.SetVariable("CMDECHO", 0))

        probe_dll = Path(args.probe_dll).resolve()
        if not probe_dll.is_file():
            raise RuntimeError(f"fixture probe DLL not found: {probe_dll}")
        trusted_paths = str(_retry(lambda: created_document.GetVariable("TRUSTEDPATHS")))
        if str(probe_dll.parent).lower() not in trusted_paths.lower():
            raise RuntimeError("fixture probe build directory is not already in AutoCAD TRUSTEDPATHS")

        report_path = probe_dll.parent / "reports" / "n4-semantic-fixture.json"
        report_path.unlink(missing_ok=True)
        created_document.SendCommand(f'_.NETLOAD\n"{probe_dll}"\n')
        _wait_command_idle(created_document)
        created_document.SendCommand("CDT_N4_CREATE_FIXTURE\n")
        _wait_command_idle(created_document)
        _wait_file(report_path)
        fixture_report = json.loads(report_path.read_text(encoding="utf-8"))
        fixture_payload = fixture_report["payload"]
        document_pid = str(fixture_payload["document_pid"])

        session_id = _session_id()
        client = NativeBridgeClient(
            NamedPipeTransport(pipe_name_for_session(session_id), connect_timeout_ms=5_000)
        )
        health = _retry(client.health, timeout=20.0)
        if health.get("bridge_version") != EXPECTED_BRIDGE_VERSION:
            raise AssertionError(f"unexpected bridge version: {health.get('bridge_version')!r}")
        if health.get("batch_chunk_atomic") is not True or health.get("cross_chunk_atomic") is not False:
            raise AssertionError("bridge batch atomicity flags are not truthful")
        if health.get("max_batch_chunk_entities") != EXPECTED_CHUNK_LIMIT:
            raise AssertionError("bridge chunk limit differs from G1 contract")
        if health.get("max_batch_semantic_entities") != EXPECTED_SEMANTIC_LIMIT:
            raise AssertionError("bridge semantic verification limit differs from G1 contract")
        if "entity.batch.create" not in health.get("mutation_operations", []):
            raise AssertionError("bridge does not advertise entity.batch.create")

        documents = client.documents_list()
        active = [item for item in documents if item.get("is_active")]
        if len(active) != 1 or active[0].get("document_pid") != document_pid:
            raise AssertionError("fixture did not bind to exactly one active PID-bearing document")
        bound_file_name = str(active[0].get("file_name") or "")
        if not bound_file_name or not Path(bound_file_name).is_absolute():
            raise AssertionError("fixture document is not bound to a fully-qualified DWG path")
        runtime_document_id = str(active[0]["runtime_document_id"])
        initial = client.document_snapshot(
            runtime_document_id,
            document_pid=document_pid,
        )
        parent_fp = initial.fingerprints.document_fp
        if parent_fp is None:
            raise AssertionError("initial fixture has no document fingerprint")

        baseline_modelspace_count = int(
            _retry(lambda: created_document.ModelSpace.Count, timeout=10.0)
        )
        if args.entity_count < 1:
            raise ValueError("entity_count must be positive")
        if baseline_modelspace_count + args.entity_count > EXPECTED_SEMANTIC_LIMIT:
            raise ValueError("entity_count would exceed the bounded G1 semantic verification limit")
        remaining = args.entity_count
        chunk_sizes: list[int] = []
        while remaining:
            chunk_size = min(EXPECTED_CHUNK_LIMIT, remaining)
            chunk_sizes.append(chunk_size)
            remaining -= chunk_size
        fault_chunk_number = min(3, len(chunk_sizes))
        chunk_latencies_ms: list[float] = []
        affected_pids: list[str] = []
        fault_latency_ms_value = 0.0
        next_index = 0
        for chunk_number, chunk_size in enumerate(chunk_sizes, start=1):
            if chunk_number == fault_chunk_number:
                before_fault_count = int(
                    _retry(lambda: created_document.ModelSpace.Count, timeout=10.0)
                )
                fault_started = time.perf_counter()
                fault = client.batch_create_chunk(
                    runtime_document_id,
                    document_pid=document_pid,
                    expected_parent_fp=parent_fp,
                    entities=_batch_entities(10_000, 4),
                    fault_stage="after_apply_before_commit",
                )
                fault_latency_ms_value = (time.perf_counter() - fault_started) * 1000.0
                if fault.get("outcome") != "ROLLED_BACK_VERIFIED":
                    raise AssertionError("injected pre-commit batch fault did not verify R0 rollback")
                if fault.get("post_document_fp") != parent_fp:
                    raise AssertionError("batch R0 rollback did not restore exact predecessor fingerprint")
                if int(_retry(lambda: created_document.ModelSpace.Count, timeout=10.0)) != before_fault_count:
                    raise AssertionError("batch R0 rollback leaked entities into ModelSpace")
                if client.recoveries_list():
                    raise AssertionError("verified R0 rollback left a pending recovery checkpoint")

            entities = _batch_entities(next_index, chunk_size)
            chunk_started = time.perf_counter()
            receipt = client.batch_create_chunk(
                runtime_document_id,
                document_pid=document_pid,
                expected_parent_fp=parent_fp,
                entities=entities,
            )
            latency_ms = (time.perf_counter() - chunk_started) * 1000.0
            chunk_latencies_ms.append(round(latency_ms, 3))
            if receipt.get("outcome") != "COMMITTED_VERIFIED":
                raise AssertionError(f"batch chunk {chunk_number} was not COMMITTED_VERIFIED")
            pids = receipt.get("affected_semantic_pids")
            if not isinstance(pids, list) or len(pids) != chunk_size or len(set(pids)) != chunk_size:
                raise AssertionError("batch chunk returned invalid affected PID receipts")
            affected_pids.extend(str(pid) for pid in pids)
            parent_fp = str(receipt["post_document_fp"])
            _finalize_chunk(client, runtime_document_id, document_pid, receipt)
            if client.recoveries_list():
                raise AssertionError("accepted batch chunk left a pending recovery checkpoint")
            next_index += chunk_size

        final_modelspace_count = int(
            _retry(lambda: created_document.ModelSpace.Count, timeout=10.0)
        )
        if final_modelspace_count - baseline_modelspace_count != args.entity_count:
            raise AssertionError("COM ModelSpace count does not match committed G1 entity count")
        if len(affected_pids) != args.entity_count or len(set(affected_pids)) != args.entity_count:
            raise AssertionError("G1 acceptance did not produce the expected unique persistent PID count")

        _retry(lambda: created_document.Save())
        summary = {
            "schema_version": 1,
            "status": "PASS",
            "bridge_version": health["bridge_version"],
            "bridge_process_id": health["process_id"],
            "session_id": session_id,
            "document_pid": document_pid,
            "runtime_document_id": runtime_document_id,
            "fixture_path": fixture_payload["fixture_path"],
            "bound_file_name": bound_file_name,
            "original_documents": original_documents,
            "chunk_sizes": chunk_sizes,
            "committed_entities": args.entity_count,
            "unique_affected_pids": len(set(affected_pids)),
            "batch_chunk_atomic": health["batch_chunk_atomic"],
            "cross_chunk_atomic": health["cross_chunk_atomic"],
            "max_batch_chunk_entities": health["max_batch_chunk_entities"],
            "max_batch_semantic_entities": health["max_batch_semantic_entities"],
            "chunk_latencies_ms": chunk_latencies_ms,
            "max_chunk_latency_ms": round(max(chunk_latencies_ms), 3),
            "fault_injection": {
                "stage": "after_apply_before_commit",
                "outcome": "ROLLED_BACK_VERIFIED",
                "predecessor_fp_restored": True,
                "modelspace_leak": False,
                "latency_ms": round(fault_latency_ms_value, 3),
            },
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
    parser = argparse.ArgumentParser(description="Run G1-A batch-create live acceptance in AutoCAD Session 1")
    parser.add_argument("--output", required=True)
    parser.add_argument("--probe-dll", required=True)
    parser.add_argument("--com-progid", default="AutoCAD.Application.26")
    parser.add_argument("--entity-count", type=int, default=100)
    args = parser.parse_args()
    summary = _run(args)
    print(json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    main()
