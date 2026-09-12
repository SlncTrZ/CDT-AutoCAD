"""G2/G3 live acceptance — metadata integrity and logical batch atomicity on real AutoCAD.
Wing: ops | Topic: native-g2-g3-acceptance | Updated: 2026-09-11 19:30
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

EXPECTED_BRIDGE_VERSION = "0.8.2-mp7"
EXPECTED_CHUNK_LIMIT = 32
EXPECTED_SEMANTIC_LIMIT = 12_288
METADATA_NAMESPACE = "customer.acceptance.v1"


def _session_id() -> int:
    value = ctypes.c_ulong()
    if not ctypes.windll.kernel32.ProcessIdToSessionId(os.getpid(), ctypes.byref(value)):
        raise RuntimeError("could not resolve Windows session id")
    return int(value.value)


def _retry(action: Callable[[], Any], *, timeout: float = 20.0, delay: float = 0.1) -> Any:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            return action()
        except Exception as exc:
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


def _memory_working_set_bytes(process_id: int) -> int | None:
    try:
        import win32api
        import win32con
        import win32process

        handle = win32api.OpenProcess(win32con.PROCESS_QUERY_INFORMATION | win32con.PROCESS_VM_READ, False, process_id)
        try:
            counters = win32process.GetProcessMemoryInfo(handle)
            return int(counters.get("WorkingSetSize", 0))
        finally:
            handle.Close()
    except Exception:
        return None


def _batch_entities(start_index: int, count: int, *, y_base: float) -> tuple[dict[str, Any], ...]:
    rows: list[dict[str, Any]] = []
    for index in range(start_index, start_index + count):
        x = float(index) * 9.0
        family = index % 4
        if family == 0:
            rows.append({"kind": "line", "start": [x, y_base, 0.0], "end": [x + 4.0, y_base, 0.0]})
        elif family == 1:
            rows.append({"kind": "circle", "center": [x, y_base + 20.0, 0.0], "radius": 1.5})
        elif family == 2:
            rows.append(
                {
                    "kind": "arc",
                    "center": [x, y_base + 40.0, 0.0],
                    "radius": 2.0,
                    "start_angle": 0.0,
                    "end_angle": 1.1,
                }
            )
        else:
            rows.append(
                {
                    "kind": "lwpolyline",
                    "points": [[x, y_base + 60.0], [x + 4.0, y_base + 60.0], [x + 2.0, y_base + 63.0]],
                    "closed": True,
                }
            )
    return tuple(rows)


def _logical_binding(receipt: dict[str, Any]) -> dict[str, str]:
    raw = receipt.get("logical_transaction")
    if not isinstance(raw, dict):
        raise AssertionError("logical begin did not return checkpoint binding")
    required = ("checkpoint_id", "checkpoint_artifact_fp", "expected_restore_fp")
    if any(not isinstance(raw.get(name), str) or not raw[name] for name in required):
        raise AssertionError("logical begin checkpoint binding is incomplete")
    return {name: str(raw[name]) for name in required}


def _run_logical_create(
    client: NativeBridgeClient,
    runtime_document_id: str,
    document_pid: str,
    parent_fp: str,
    *,
    start_index: int,
    entity_count: int,
    y_base: float,
) -> tuple[str, list[float], list[str]]:
    begin = client.begin_logical_batch(
        runtime_document_id,
        document_pid=document_pid,
        expected_parent_fp=parent_fp,
    )
    if begin.get("status") != "OPEN" or begin.get("pre_document_fp") != parent_fp:
        raise AssertionError("logical begin did not bind the exact predecessor fingerprint")
    binding = _logical_binding(begin)
    if binding["expected_restore_fp"] != parent_fp:
        raise AssertionError("logical checkpoint restore fingerprint differs from predecessor")

    current_fp = parent_fp
    latencies_ms: list[float] = []
    affected: list[str] = []
    offset = 0
    while offset < entity_count:
        size = min(EXPECTED_CHUNK_LIMIT, entity_count - offset)
        started = time.perf_counter()
        receipt = client.batch_create_chunk(
            runtime_document_id,
            document_pid=document_pid,
            expected_parent_fp=current_fp,
            entities=_batch_entities(start_index + offset, size, y_base=y_base),
            logical_transaction=binding,
        )
        latencies_ms.append(round((time.perf_counter() - started) * 1000.0, 3))
        if receipt.get("outcome") != "COMMITTED_VERIFIED":
            raise AssertionError(f"logical batch chunk {offset // EXPECTED_CHUNK_LIMIT} did not commit")
        if receipt.get("pre_document_fp") != current_fp:
            raise AssertionError("logical batch chunk fingerprint chain is discontinuous")
        if receipt.get("recovery_checkpoint") != binding:
            raise AssertionError("logical batch chunk switched checkpoint binding")
        pids = receipt.get("affected_semantic_pids")
        if not isinstance(pids, list) or len(pids) != size or len(set(pids)) != size:
            raise AssertionError("logical batch chunk returned invalid affected PID set")
        affected.extend(str(pid) for pid in pids)
        current_fp = str(receipt["post_document_fp"])
        offset += size

    finalized = client.finalize_recovery(
        runtime_document_id,
        document_pid=document_pid,
        checkpoint_id=binding["checkpoint_id"],
        checkpoint_artifact_fp=binding["checkpoint_artifact_fp"],
        accepted_post_fp=current_fp,
    )
    if finalized.get("status") != "FINALIZED":
        raise AssertionError("logical batch checkpoint did not finalize")
    if client.recoveries_list():
        raise AssertionError("accepted logical batch left a pending recovery checkpoint")
    return current_fp, latencies_ms, affected


def _run(args: argparse.Namespace) -> dict[str, Any]:
    if os.name != "nt":
        raise RuntimeError("G2/G3 live acceptance requires Windows AutoCAD")

    import pythoncom
    import win32com.client

    pythoncom.CoInitialize()
    created_document = None
    disposable_path: Path | None = None
    started = time.perf_counter()
    try:
        application = _retry(lambda: win32com.client.GetActiveObject(args.com_progid))
        original_documents = [str(application.Documents.Item(index).Name) for index in range(application.Documents.Count)]
        created_document = _retry(lambda: application.Documents.Add())
        _retry(lambda: created_document.Activate())
        disposable_path = Path(os.environ["LOCALAPPDATA"]) / "Temp" / f"cdt-g23-{time.time_ns()}.dwg"
        _retry(lambda: created_document.SaveAs(str(disposable_path)), timeout=20.0)
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
        fixture = json.loads(report_path.read_text(encoding="utf-8"))["payload"]
        document_pid = str(fixture["document_pid"])
        line_pid = str(fixture["entities"]["line"]["pid"])

        session_id = _session_id()
        client = NativeBridgeClient(
            NamedPipeTransport(pipe_name_for_session(session_id), connect_timeout_ms=8_000)
        )
        health = client.health()
        if health.get("bridge_version") != EXPECTED_BRIDGE_VERSION:
            raise AssertionError(f"unexpected bridge version: {health.get('bridge_version')!r}")
        required_health = {
            "document_fp_schema_version": 3,
            "metadata_in_document_fp": True,
            "batch_chunk_atomic": True,
            "batch_yield_per_idle": True,
            "cross_chunk_atomic": False,
            "logical_batch_atomic": True,
            "max_batch_chunk_entities": EXPECTED_CHUNK_LIMIT,
            "max_batch_semantic_entities": EXPECTED_SEMANTIC_LIMIT,
        }
        for key, expected in required_health.items():
            if health.get(key) != expected:
                raise AssertionError(f"health field {key!r} expected {expected!r}, got {health.get(key)!r}")

        active = [item for item in client.documents_list() if item.get("is_active")]
        if len(active) != 1 or active[0].get("document_pid") != document_pid:
            raise AssertionError("fixture did not bind to exactly one active PID-bearing document")
        runtime_document_id = str(active[0]["runtime_document_id"])
        state0 = client.document_state(runtime_document_id, document_pid=document_pid)
        baseline_count = int(state0["entity_count"])
        baseline_fp = str(state0["document_fp"])
        baseline_modelspace_count = int(_retry(lambda: created_document.ModelSpace.Count))

        # G2: commit + independent readback + query + finalize.
        metadata_value = {"part_no": "G23-LIVE", "revision": 1, "qa": {"approved": True}}
        if client.metadata_get(
            runtime_document_id,
            document_pid=document_pid,
            semantic_pid=line_pid,
            namespace=METADATA_NAMESPACE,
        ).get("found"):
            raise AssertionError("fresh fixture unexpectedly already contains acceptance metadata")
        metadata_receipt = client.metadata_set(
            runtime_document_id,
            document_pid=document_pid,
            expected_parent_fp=baseline_fp,
            semantic_pid=line_pid,
            namespace=METADATA_NAMESPACE,
            value=metadata_value,
        )
        if metadata_receipt.get("outcome") != "COMMITTED_VERIFIED":
            raise AssertionError("metadata commit was not COMMITTED_VERIFIED")
        metadata_fp = str(metadata_receipt["post_document_fp"])
        state1 = client.document_state(runtime_document_id, document_pid=document_pid)
        if state1.get("document_fp") != metadata_fp or metadata_fp == baseline_fp:
            raise AssertionError("metadata did not advance the v3 document fingerprint")
        readback = client.metadata_get(
            runtime_document_id,
            document_pid=document_pid,
            semantic_pid=line_pid,
            namespace=METADATA_NAMESPACE,
        )
        if readback.get("found") is not True or readback.get("value") != metadata_value:
            raise AssertionError("metadata independent readback differs from committed value")
        query = client.metadata_query(
            runtime_document_id,
            document_pid=document_pid,
            namespace=METADATA_NAMESPACE,
            path="qa.approved",
            equals=True,
            limit=20,
        )
        query_pids = {str(item["semantic_pid"]) for item in query.get("items", [])}
        if line_pid not in query_pids:
            raise AssertionError("metadata query did not return the tagged semantic PID")
        metadata_checkpoint = metadata_receipt["recovery_checkpoint"]
        client.finalize_recovery(
            runtime_document_id,
            document_pid=document_pid,
            checkpoint_id=str(metadata_checkpoint["checkpoint_id"]),
            checkpoint_artifact_fp=str(metadata_checkpoint["checkpoint_artifact_fp"]),
            accepted_post_fp=metadata_fp,
        )

        # G2: injected R0 rollback must preserve exact fingerprint and value.
        fault = client.metadata_set(
            runtime_document_id,
            document_pid=document_pid,
            expected_parent_fp=metadata_fp,
            semantic_pid=line_pid,
            namespace=METADATA_NAMESPACE,
            value={"part_no": "FAULT", "revision": 999},
            fault_stage="after_apply_before_commit",
        )
        if fault.get("outcome") != "ROLLED_BACK_VERIFIED" or fault.get("post_document_fp") != metadata_fp:
            raise AssertionError("metadata R0 fault did not restore exact predecessor fingerprint")
        if client.metadata_get(
            runtime_document_id,
            document_pid=document_pid,
            semantic_pid=line_pid,
            namespace=METADATA_NAMESPACE,
        ).get("value") != metadata_value:
            raise AssertionError("metadata R0 fault changed persisted metadata")

        # G2: explicit post-commit R1 compensation restores the accepted predecessor.
        temporary = client.metadata_set(
            runtime_document_id,
            document_pid=document_pid,
            expected_parent_fp=metadata_fp,
            semantic_pid=line_pid,
            namespace=METADATA_NAMESPACE,
            value={"part_no": "TEMP", "revision": 2},
        )
        temporary_checkpoint = temporary["recovery_checkpoint"]
        r1 = client.resolve_recovery(
            runtime_document_id,
            document_pid=document_pid,
            checkpoint_id=str(temporary_checkpoint["checkpoint_id"]),
            checkpoint_artifact_fp=str(temporary_checkpoint["checkpoint_artifact_fp"]),
            expected_restore_fp=metadata_fp,
            strategy="R1_COMPENSATE",
        )
        if r1.get("outcome") != "ROLLED_BACK_VERIFIED" or r1.get("post_document_fp") != metadata_fp:
            raise AssertionError("metadata R1 compensation did not restore exact predecessor")

        # G3: commit one chunk, fail the next, then restore the whole logical predecessor with R2.
        logical_begin = client.begin_logical_batch(
            runtime_document_id,
            document_pid=document_pid,
            expected_parent_fp=metadata_fp,
        )
        logical_binding = _logical_binding(logical_begin)
        first = client.batch_create_chunk(
            runtime_document_id,
            document_pid=document_pid,
            expected_parent_fp=metadata_fp,
            entities=_batch_entities(50_000, 32, y_base=20_000.0),
            logical_transaction=logical_binding,
        )
        if first.get("outcome") != "COMMITTED_VERIFIED":
            raise AssertionError("G3 fault fixture first chunk did not commit")
        first_fp = str(first["post_document_fp"])
        second = client.batch_create_chunk(
            runtime_document_id,
            document_pid=document_pid,
            expected_parent_fp=first_fp,
            entities=_batch_entities(50_032, 8, y_base=20_000.0),
            fault_stage="after_apply_before_commit",
            logical_transaction=logical_binding,
        )
        if second.get("outcome") != "ROLLED_BACK_VERIFIED" or second.get("post_document_fp") != first_fp:
            raise AssertionError("G3 injected middle-chunk R0 did not preserve the last committed chunk state")
        r2 = client.resolve_recovery(
            runtime_document_id,
            document_pid=document_pid,
            checkpoint_id=logical_binding["checkpoint_id"],
            checkpoint_artifact_fp=logical_binding["checkpoint_artifact_fp"],
            expected_restore_fp=metadata_fp,
            strategy="R2_CHECKPOINT_RESTORE",
        )
        rollback = r2.get("rollback") or {}
        if (
            r2.get("outcome") != "ROLLED_BACK_VERIFIED"
            or rollback.get("status") != "ROLLED_BACK_VERIFIED"
            or rollback.get("actual_restore_fp") != metadata_fp
        ):
            raise AssertionError("G3 R2 did not restore the entire logical predecessor")
        runtime_document_id = str(r2["runtime_document_id"])
        created_document = _retry(lambda: application.ActiveDocument)
        state_after_r2 = client.document_state(runtime_document_id, document_pid=document_pid)
        if state_after_r2.get("document_fp") != metadata_fp:
            raise AssertionError("G3 R2 compact state differs from the logical predecessor")
        if int(_retry(lambda: created_document.ModelSpace.Count)) != baseline_modelspace_count:
            raise AssertionError("G3 whole-batch R2 leaked the first committed chunk into ModelSpace")

        memory_before = _memory_working_set_bytes(int(health["process_id"]))
        count_before_scale = int(state_after_r2["entity_count"])
        fp100, lat100, pids100 = _run_logical_create(
            client,
            runtime_document_id,
            document_pid,
            metadata_fp,
            start_index=100_000,
            entity_count=100,
            y_base=30_000.0,
        )
        state100 = client.document_state(runtime_document_id, document_pid=document_pid)
        if int(state100["entity_count"]) != count_before_scale + 100 or state100.get("document_fp") != fp100:
            raise AssertionError("100-entity logical batch compact state is inconsistent")

        fp1000, lat1000, pids1000 = _run_logical_create(
            client,
            runtime_document_id,
            document_pid,
            fp100,
            start_index=200_000,
            entity_count=1000,
            y_base=40_000.0,
        )
        state1000 = client.document_state(runtime_document_id, document_pid=document_pid)
        if int(state1000["entity_count"]) != count_before_scale + 1100 or state1000.get("document_fp") != fp1000:
            raise AssertionError("1000-entity logical batch compact state is inconsistent")
        memory_after = _memory_working_set_bytes(int(health["process_id"]))

        if len(set(pids100)) != 100 or len(set(pids1000)) != 1000:
            raise AssertionError("scale acceptance did not produce unique persistent semantic PIDs")
        if client.recoveries_list():
            raise AssertionError("G2/G3 acceptance ended with pending recovery checkpoints")

        _retry(lambda: created_document.Save(), timeout=20.0)
        summary = {
            "schema_version": 1,
            "status": "PASS",
            "bridge_version": health["bridge_version"],
            "bridge_process_id": health["process_id"],
            "session_id": session_id,
            "document_pid": document_pid,
            "runtime_document_id": runtime_document_id,
            "bound_file_name": str(created_document.FullName),
            "original_documents": original_documents,
            "semantic": {
                "document_fp_schema_version": 3,
                "baseline_entity_count": baseline_count,
                "final_entity_count": int(state1000["entity_count"]),
                "final_document_fp": fp1000,
                "compact_state_verified_above_32_entities": int(state1000["entity_count"]) > 32,
            },
            "g2_metadata": {
                "namespace": METADATA_NAMESPACE,
                "fingerprint_advanced": metadata_fp != baseline_fp,
                "query_match_verified": True,
                "r0_verified": True,
                "r1_verified": True,
            },
            "g3_atomicity": {
                "batch_yield_per_idle": health["batch_yield_per_idle"],
                "logical_batch_atomic": health["logical_batch_atomic"],
                "middle_chunk_failure_r2_exact_restore": True,
                "cross_chunk_atomic_flag": health["cross_chunk_atomic"],
            },
            "benchmark": {
                "tier_100": {
                    "entities": 100,
                    "chunks": len(lat100),
                    "total_chunk_roundtrip_ms": round(sum(lat100), 3),
                    "max_chunk_roundtrip_ms": round(max(lat100), 3),
                    "p95_chunk_roundtrip_ms": round(sorted(lat100)[max(0, int(len(lat100) * 0.95) - 1)], 3),
                },
                "tier_1000": {
                    "entities": 1000,
                    "chunks": len(lat1000),
                    "total_chunk_roundtrip_ms": round(sum(lat1000), 3),
                    "max_chunk_roundtrip_ms": round(max(lat1000), 3),
                    "p95_chunk_roundtrip_ms": round(sorted(lat1000)[max(0, int(len(lat1000) * 0.95) - 1)], 3),
                },
                "continuous_ui_block_proxy": "max_chunk_roundtrip_ms; each batch request is enforced to one AutoCAD Idle tick",
                "working_set_before_bytes": memory_before,
                "working_set_after_bytes": memory_after,
                "working_set_delta_bytes": (
                    None if memory_before is None or memory_after is None else memory_after - memory_before
                ),
            },
            "pending_recoveries": 0,
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
                    _retry(lambda: created_document.Close(False), timeout=5.0)
            except Exception:
                pass
        if disposable_path is not None:
            try:
                disposable_path.unlink(missing_ok=True)
            except OSError:
                pass
        pythoncom.CoUninitialize()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run G2 metadata + G3 logical atomicity live acceptance")
    parser.add_argument("--output", required=True)
    parser.add_argument("--probe-dll", required=True)
    parser.add_argument("--com-progid", default="AutoCAD.Application.26")
    args = parser.parse_args()
    summary = _run(args)
    print(json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    main()
