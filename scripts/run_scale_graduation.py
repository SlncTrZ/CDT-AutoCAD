"""G3 scale graduation — 100/1k/5k/10k success plus beginning/middle/end rollback on real AutoCAD.
Wing: ops | Topic: native-g3-scale | Updated: 2026-09-11 19:45
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
from cdt_autocad.native_bridge.protocol import MAX_BATCH_CHUNK_ENTITIES, MAX_LOGICAL_BATCH_ITEMS
from cdt_autocad.native_bridge.transport_windows import NamedPipeTransport, pipe_name_for_session

EXPECTED_BRIDGE_VERSION = "0.8.2-mp7"
EXPECTED_SEMANTIC_LIMIT = 12_288
ALLOWED_TIERS = {100, 1_000, 5_000, 10_000}


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


def _wait_command_idle(document: Any, *, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        names = str(_retry(lambda: document.GetVariable("CMDNAMES"), timeout=2.0) or "").strip()
        if not names:
            return
        time.sleep(0.1)
    raise RuntimeError("AutoCAD command did not return to idle")


def _wait_file(path: Path, *, timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.is_file() and path.stat().st_size > 0:
            return
        time.sleep(0.1)
    raise RuntimeError(f"fixture report was not written: {path}")


def _memory_working_set_bytes(process_id: int) -> int | None:
    try:
        import win32api
        import win32con
        import win32process

        handle = win32api.OpenProcess(
            win32con.PROCESS_QUERY_INFORMATION | win32con.PROCESS_VM_READ,
            False,
            process_id,
        )
        try:
            return int(win32process.GetProcessMemoryInfo(handle).get("WorkingSetSize", 0))
        finally:
            handle.Close()
    except Exception:
        return None


def _batch_entities(start_index: int, count: int, *, y_base: float) -> tuple[dict[str, Any], ...]:
    result: list[dict[str, Any]] = []
    for index in range(start_index, start_index + count):
        x = float(index) * 7.0
        family = index % 4
        if family == 0:
            result.append({"kind": "line", "start": [x, y_base, 0.0], "end": [x + 3.0, y_base, 0.0]})
        elif family == 1:
            result.append({"kind": "circle", "center": [x, y_base + 15.0, 0.0], "radius": 1.25})
        elif family == 2:
            result.append(
                {
                    "kind": "arc",
                    "center": [x, y_base + 30.0, 0.0],
                    "radius": 1.75,
                    "start_angle": 0.0,
                    "end_angle": 1.0,
                }
            )
        else:
            result.append(
                {
                    "kind": "lwpolyline",
                    "points": [[x, y_base + 45.0], [x + 3.0, y_base + 45.0], [x + 1.5, y_base + 47.0]],
                    "closed": True,
                }
            )
    return tuple(result)


def _chunk_sizes(entity_count: int) -> list[int]:
    remaining = entity_count
    sizes: list[int] = []
    while remaining:
        size = min(MAX_BATCH_CHUNK_ENTITIES, remaining)
        sizes.append(size)
        remaining -= size
    return sizes


def _logical_binding(begin: dict[str, Any], parent_fp: str) -> dict[str, str]:
    if begin.get("status") != "OPEN" or begin.get("pre_document_fp") != parent_fp:
        raise AssertionError("logical begin did not bind the exact predecessor fingerprint")
    raw = begin.get("logical_transaction")
    if not isinstance(raw, dict):
        raise AssertionError("logical begin did not return checkpoint binding")
    required = ("checkpoint_id", "checkpoint_artifact_fp", "expected_restore_fp")
    if any(not isinstance(raw.get(key), str) or not raw[key] for key in required):
        raise AssertionError("logical begin returned incomplete checkpoint binding")
    if raw["expected_restore_fp"] != parent_fp:
        raise AssertionError("logical checkpoint restore fingerprint differs from predecessor")
    return {key: str(raw[key]) for key in required}


def _assert_no_pending(client: NativeBridgeClient) -> None:
    recoveries = client.recoveries_list()
    if recoveries:
        raise AssertionError(f"pending native recovery state remains: {recoveries!r}")


def _run_failure_case(
    client: NativeBridgeClient,
    runtime_document_id: str,
    document_pid: str,
    baseline_fp: str,
    baseline_entity_count: int,
    baseline_modelspace_count: int,
    *,
    entity_count: int,
    fault_chunk_index: int,
    start_index: int,
    y_base: float,
    application: Any,
) -> tuple[str, Any, dict[str, Any]]:
    sizes = _chunk_sizes(entity_count)
    begin = client.begin_logical_batch(
        runtime_document_id,
        document_pid=document_pid,
        expected_parent_fp=baseline_fp,
    )
    binding = _logical_binding(begin, baseline_fp)
    current_fp = baseline_fp
    committed_chunks = 0
    next_index = 0
    chunk_latencies_ms: list[float] = []
    fault_latency_ms = 0.0

    for chunk_index, size in enumerate(sizes):
        fault = chunk_index == fault_chunk_index
        started = time.perf_counter()
        receipt = client.batch_create_chunk(
            runtime_document_id,
            document_pid=document_pid,
            expected_parent_fp=current_fp,
            entities=_batch_entities(start_index + next_index, size, y_base=y_base),
            fault_stage="after_apply_before_commit" if fault else None,
            logical_transaction=binding,
        )
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        if fault:
            fault_latency_ms = elapsed_ms
            if receipt.get("outcome") != "ROLLED_BACK_VERIFIED":
                raise AssertionError("injected logical chunk fault did not R0 rollback")
            if receipt.get("post_document_fp") != current_fp:
                raise AssertionError("injected logical chunk fault did not restore the last committed chunk state")
            break
        if receipt.get("outcome") != "COMMITTED_VERIFIED":
            raise AssertionError("pre-fault logical chunk did not commit")
        if receipt.get("pre_document_fp") != current_fp:
            raise AssertionError("logical chunk fingerprint chain is discontinuous")
        if receipt.get("recovery_checkpoint") != binding:
            raise AssertionError("logical chunk switched checkpoint binding")
        current_fp = str(receipt["post_document_fp"])
        committed_chunks += 1
        chunk_latencies_ms.append(round(elapsed_ms, 3))
        next_index += size

    recovery_started = time.perf_counter()
    recovered = client.resolve_recovery(
        runtime_document_id,
        document_pid=document_pid,
        checkpoint_id=binding["checkpoint_id"],
        checkpoint_artifact_fp=binding["checkpoint_artifact_fp"],
        expected_restore_fp=baseline_fp,
        strategy="R2_CHECKPOINT_RESTORE",
    )
    recovery_latency_ms = (time.perf_counter() - recovery_started) * 1000.0
    rollback = recovered.get("rollback") or {}
    if (
        recovered.get("outcome") != "ROLLED_BACK_VERIFIED"
        or rollback.get("status") != "ROLLED_BACK_VERIFIED"
        or rollback.get("expected_restore_fp") != baseline_fp
        or rollback.get("actual_restore_fp") != baseline_fp
    ):
        raise AssertionError("logical fault recovery did not restore exact batch predecessor")
    rebound_runtime_id = str(recovered.get("runtime_document_id") or "")
    if not rebound_runtime_id:
        raise AssertionError("logical R2 recovery did not return rebound runtime_document_id")

    rebound_document = _retry(lambda: application.ActiveDocument, timeout=20.0)
    state = client.document_state(rebound_runtime_id, document_pid=document_pid)
    if state.get("document_fp") != baseline_fp or int(state["entity_count"]) != baseline_entity_count:
        raise AssertionError("post-R2 compact state differs from logical predecessor")
    if int(_retry(lambda: rebound_document.ModelSpace.Count, timeout=10.0)) != baseline_modelspace_count:
        raise AssertionError("post-R2 ModelSpace count differs from logical predecessor")
    _assert_no_pending(client)

    return rebound_runtime_id, rebound_document, {
        "fault_chunk_index": fault_chunk_index,
        "fault_position_ratio": round(fault_chunk_index / max(1, len(sizes) - 1), 4),
        "committed_chunks_before_fault": committed_chunks,
        "fault_latency_ms": round(fault_latency_ms, 3),
        "recovery_latency_ms": round(recovery_latency_ms, 3),
        "max_pre_fault_chunk_latency_ms": round(max(chunk_latencies_ms), 3) if chunk_latencies_ms else 0.0,
        "exact_predecessor_restored": True,
        "runtime_rebound": rebound_runtime_id != runtime_document_id,
    }


def _run_success_case(
    client: NativeBridgeClient,
    runtime_document_id: str,
    document_pid: str,
    baseline_fp: str,
    baseline_entity_count: int,
    *,
    entity_count: int,
    start_index: int,
    y_base: float,
) -> dict[str, Any]:
    sizes = _chunk_sizes(entity_count)
    begin = client.begin_logical_batch(
        runtime_document_id,
        document_pid=document_pid,
        expected_parent_fp=baseline_fp,
    )
    binding = _logical_binding(begin, baseline_fp)
    current_fp = baseline_fp
    affected: list[str] = []
    latencies_ms: list[float] = []
    next_index = 0
    started_total = time.perf_counter()

    for chunk_index, size in enumerate(sizes):
        started = time.perf_counter()
        receipt = client.batch_create_chunk(
            runtime_document_id,
            document_pid=document_pid,
            expected_parent_fp=current_fp,
            entities=_batch_entities(start_index + next_index, size, y_base=y_base),
            logical_transaction=binding,
        )
        latencies_ms.append(round((time.perf_counter() - started) * 1000.0, 3))
        if receipt.get("outcome") != "COMMITTED_VERIFIED":
            raise AssertionError(f"success tier chunk {chunk_index} did not commit")
        if receipt.get("pre_document_fp") != current_fp:
            raise AssertionError("success tier fingerprint chain is discontinuous")
        if receipt.get("recovery_checkpoint") != binding:
            raise AssertionError("success tier chunk switched checkpoint binding")
        pids = receipt.get("affected_semantic_pids")
        if not isinstance(pids, list) or len(pids) != size or len(set(pids)) != size:
            raise AssertionError("success tier returned invalid affected PID set")
        affected.extend(str(pid) for pid in pids)
        current_fp = str(receipt["post_document_fp"])
        next_index += size

    state = client.document_state(runtime_document_id, document_pid=document_pid)
    if state.get("document_fp") != current_fp:
        raise AssertionError("independent final compact-state fingerprint differs from chunk chain")
    if int(state["entity_count"]) != baseline_entity_count + entity_count:
        raise AssertionError("independent final compact-state entity count differs from committed tier")
    if len(affected) != entity_count or len(set(affected)) != entity_count:
        raise AssertionError("success tier did not produce exact unique affected semantic PID count")

    finalized = client.finalize_recovery(
        runtime_document_id,
        document_pid=document_pid,
        checkpoint_id=binding["checkpoint_id"],
        checkpoint_artifact_fp=binding["checkpoint_artifact_fp"],
        accepted_post_fp=current_fp,
    )
    if finalized.get("status") != "FINALIZED":
        raise AssertionError("success tier logical checkpoint did not finalize")
    _assert_no_pending(client)

    sorted_latencies = sorted(latencies_ms)
    p95_index = max(0, int(len(sorted_latencies) * 0.95) - 1)
    return {
        "chunks": len(sizes),
        "chunk_sizes": sizes,
        "total_seconds": round(time.perf_counter() - started_total, 3),
        "sum_chunk_roundtrip_ms": round(sum(latencies_ms), 3),
        "max_chunk_roundtrip_ms": round(max(latencies_ms), 3),
        "p95_chunk_roundtrip_ms": round(sorted_latencies[p95_index], 3),
        "final_document_fp": current_fp,
        "final_entity_count": int(state["entity_count"]),
        "unique_affected_pids": len(set(affected)),
        "checkpoint_finalized": True,
    }


def _run(args: argparse.Namespace) -> dict[str, Any]:
    if os.name != "nt":
        raise RuntimeError("scale graduation requires Windows AutoCAD")
    if args.entity_count not in ALLOWED_TIERS:
        raise ValueError(f"entity_count must be one of {sorted(ALLOWED_TIERS)}")
    if args.entity_count > MAX_LOGICAL_BATCH_ITEMS:
        raise ValueError("entity_count exceeds public logical-batch contract")

    import pythoncom
    import win32com.client

    pythoncom.CoInitialize()
    created_document = None
    disposable_path: Path | None = None
    started = time.perf_counter()
    try:
        application = _retry(lambda: win32com.client.GetActiveObject(args.com_progid), timeout=30.0)
        original_documents = [str(application.Documents.Item(index).Name) for index in range(application.Documents.Count)]
        created_document = _retry(lambda: application.Documents.Add())
        _retry(lambda: created_document.Activate())
        disposable_path = Path(os.environ["LOCALAPPDATA"]) / "Temp" / f"cdt-scale-{args.entity_count}-{time.time_ns()}.dwg"
        _retry(lambda: created_document.SaveAs(str(disposable_path)), timeout=30.0)
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
        client = NativeBridgeClient(
            NamedPipeTransport(pipe_name_for_session(session_id), connect_timeout_ms=8_000)
        )
        health = client.health()
        if health.get("bridge_version") != EXPECTED_BRIDGE_VERSION:
            raise AssertionError(f"unexpected bridge version: {health.get('bridge_version')!r}")
        if health.get("max_batch_chunk_entities") != MAX_BATCH_CHUNK_ENTITIES:
            raise AssertionError("native chunk limit differs from contract")
        if health.get("max_batch_semantic_entities") != EXPECTED_SEMANTIC_LIMIT:
            raise AssertionError("native semantic scale limit differs from contract")
        if health.get("batch_yield_per_idle") is not True or health.get("logical_batch_atomic") is not True:
            raise AssertionError("bridge does not truthfully advertise G3 scale invariants")

        active = [item for item in client.documents_list() if item.get("is_active")]
        if len(active) != 1 or active[0].get("document_pid") != document_pid:
            raise AssertionError("fixture did not bind to exactly one active PID-bearing document")
        runtime_document_id = str(active[0]["runtime_document_id"])
        state0 = client.document_state(runtime_document_id, document_pid=document_pid)
        baseline_fp = str(state0["document_fp"])
        baseline_entity_count = int(state0["entity_count"])
        baseline_modelspace_count = int(_retry(lambda: created_document.ModelSpace.Count))
        if baseline_entity_count + args.entity_count > EXPECTED_SEMANTIC_LIMIT:
            raise AssertionError("tier exceeds native semantic capacity")

        process_id = int(health["process_id"])
        memory_before = _memory_working_set_bytes(process_id)
        process_identity_before = (process_id, int(health["bridge_instance_id"].replace("-", "")[:8], 16))
        sizes = _chunk_sizes(args.entity_count)
        fault_indices = {
            "beginning": 0,
            "middle": len(sizes) // 2,
            "end": len(sizes) - 1,
        }
        failures: dict[str, Any] = {}
        for ordinal, (label, fault_index) in enumerate(fault_indices.items()):
            runtime_document_id, created_document, evidence = _run_failure_case(
                client,
                runtime_document_id,
                document_pid,
                baseline_fp,
                baseline_entity_count,
                baseline_modelspace_count,
                entity_count=args.entity_count,
                fault_chunk_index=fault_index,
                start_index=(ordinal + 1) * 1_000_000,
                y_base=50_000.0 + ordinal * 5_000.0,
                application=application,
            )
            failures[label] = evidence

        success = _run_success_case(
            client,
            runtime_document_id,
            document_pid,
            baseline_fp,
            baseline_entity_count,
            entity_count=args.entity_count,
            start_index=9_000_000,
            y_base=80_000.0,
        )
        created_document = _retry(lambda: application.ActiveDocument)
        final_modelspace_count = int(_retry(lambda: created_document.ModelSpace.Count))
        if final_modelspace_count != baseline_modelspace_count + args.entity_count:
            raise AssertionError("final COM ModelSpace count differs from accepted success tier")
        if str(_retry(lambda: created_document.GetVariable("CMDNAMES")) or "").strip():
            raise AssertionError("AutoCAD remained busy after scale acceptance")
        if int(_retry(lambda: created_document.GetVariable("CMDACTIVE"))) != 0:
            raise AssertionError("AutoCAD command activity remained non-zero after scale acceptance")
        _retry(lambda: created_document.Save(), timeout=30.0)

        health_after = client.health()
        if int(health_after["process_id"]) != process_id:
            raise AssertionError("native/AutoCAD process identity changed unexpectedly during one tier")
        process_identity_after = (
            int(health_after["process_id"]),
            int(str(health_after["bridge_instance_id"]).replace("-", "")[:8], 16),
        )
        if process_identity_after != process_identity_before:
            raise AssertionError("bridge instance changed unexpectedly during one tier")
        memory_after = _memory_working_set_bytes(process_id)
        _assert_no_pending(client)

        summary = {
            "schema_version": 1,
            "status": "PASS",
            "tier_entities": args.entity_count,
            "bridge_version": health["bridge_version"],
            "bridge_process_id": process_id,
            "bridge_instance_id": health["bridge_instance_id"],
            "session_id": session_id,
            "document_pid": document_pid,
            "runtime_document_id_final": runtime_document_id,
            "original_documents": original_documents,
            "limits": {
                "logical_batch_items": MAX_LOGICAL_BATCH_ITEMS,
                "native_chunk_entities": MAX_BATCH_CHUNK_ENTITIES,
                "native_semantic_entities": EXPECTED_SEMANTIC_LIMIT,
            },
            "baseline": {
                "entity_count": baseline_entity_count,
                "modelspace_count": baseline_modelspace_count,
                "document_fp": baseline_fp,
            },
            "failure_injection": failures,
            "success": success,
            "responsiveness": {
                "continuous_ui_block_proxy": "max native chunk round-trip; bridge enforces one batch mutation per AutoCAD Idle tick",
                "max_chunk_roundtrip_ms": success["max_chunk_roundtrip_ms"],
                "p95_chunk_roundtrip_ms": success["p95_chunk_roundtrip_ms"],
                "cmdnames_idle": True,
                "cmdactive_zero": True,
            },
            "memory": {
                "working_set_before_bytes": memory_before,
                "working_set_after_bytes": memory_after,
                "working_set_delta_bytes": None if memory_before is None or memory_after is None else memory_after - memory_before,
            },
            "recovery": {
                "pending_recoveries": 0,
                "all_positions_exact_predecessor": all(item["exact_predecessor_restored"] for item in failures.values()),
                "runtime_rebound_observed": any(item["runtime_rebound"] for item in failures.values()),
            },
            "process_stability": {
                "same_bridge_process": True,
                "same_bridge_instance": True,
            },
            "duration_seconds": round(time.perf_counter() - started, 3),
        }
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return summary
    finally:
        if created_document is not None:
            try:
                full_name = str(getattr(created_document, "FullName", "") or "")
                if disposable_path is not None and full_name and Path(full_name).resolve() == disposable_path.resolve():
                    _retry(lambda: created_document.Close(False), timeout=10.0)
            except Exception:
                pass
        if disposable_path is not None:
            try:
                disposable_path.unlink(missing_ok=True)
            except OSError:
                pass
        pythoncom.CoUninitialize()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one G3 scale graduation tier on AutoCAD Session 1")
    parser.add_argument("--entity-count", type=int, required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--probe-dll", required=True)
    parser.add_argument("--com-progid", default="AutoCAD.Application.26")
    args = parser.parse_args()
    print(json.dumps(_run(args), sort_keys=True))


if __name__ == "__main__":
    main()
