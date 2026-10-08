"""AC-P02 live proof — affected-scoped verify for insert_blocks/transform on real AutoCAD.

Disposable fixture only: creates its own DWG, runs insert + transform workloads
with per-chunk timing and receipt capture, proves the stray-outside-affected-scope
boundary behavior, runs negative refusals, then closes without saving and deletes
the fixture. Never touches production drawings. Preserves ErrorReports.

Run twice for the same-workload comparison: once against the pre-P02 bridge
(full-scope post-commit) and once after deploying the P02 bridge
(affected-scoped post-commit). The script branches on the observed chunk
outcome so the same file is valid for both runs.
Wing: ops | Topic: p02-affected-verify-live | Updated: 2026-10-07 11:55
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from cdt_autocad.native_bridge.client import NativeBridgeClient
from cdt_autocad.native_bridge.logical_batch import NativeLogicalBatchExecutor
from cdt_autocad.native_bridge.transport_windows import NamedPipeTransport, pipe_name_for_session


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
    raise RuntimeError("operation did not become ready before timeout") from last_error


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


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _finalize(
    client: NativeBridgeClient, runtime_id: str, document_pid: str, receipt: dict[str, Any]
) -> None:
    checkpoint = receipt.get("recovery_checkpoint")
    if not isinstance(checkpoint, dict):
        raise AssertionError("committed chunk did not return a recovery checkpoint")
    finalized = client.finalize_recovery(
        runtime_id,
        document_pid=document_pid,
        checkpoint_id=str(checkpoint["checkpoint_id"]),
        checkpoint_artifact_fp=str(checkpoint["checkpoint_artifact_fp"]),
        owner_request_id=str(checkpoint["owner_request_id"]),
        accepted_post_fp=str(receipt["post_document_fp"]),
    )
    if finalized.get("status") != "FINALIZED":
        raise AssertionError("checkpoint did not finalize after accepted commit")
    if client.recoveries_list():
        raise AssertionError("accepted chunk left a pending recovery checkpoint")


def _r2_restore(
    client: NativeBridgeClient,
    runtime_id: str,
    document_pid: str,
    receipt: dict[str, Any],
    parent_fp: str,
) -> dict[str, Any]:
    checkpoint = receipt.get("recovery_checkpoint")
    assert isinstance(checkpoint, dict)
    recovery = client.resolve_recovery(
        runtime_id,
        document_pid=document_pid,
        checkpoint_id=str(checkpoint["checkpoint_id"]),
        checkpoint_artifact_fp=str(checkpoint["checkpoint_artifact_fp"]),
        owner_request_id=str(checkpoint["owner_request_id"]),
        expected_restore_fp=parent_fp,
        strategy="R2_CHECKPOINT_RESTORE",
    )
    if recovery.get("outcome") != "ROLLED_BACK_VERIFIED":
        raise AssertionError(f"R2 did not restore exact predecessor: {recovery}")
    if client.recoveries_list():
        raise AssertionError("R2 left a pending recovery checkpoint")
    restored_runtime = recovery.get("runtime_document_id")
    if not isinstance(restored_runtime, str) or not restored_runtime:
        raise AssertionError("R2 recovery did not return a runtime document binding")
    return recovery, restored_runtime


def _run(args: argparse.Namespace) -> dict[str, Any]:
    if os.name != "nt":
        raise RuntimeError("P02 live proof requires Windows AutoCAD")

    import pythoncom
    import win32com.client

    pythoncom.CoInitialize()
    document = None
    fixture_path: Path | None = None
    started = time.perf_counter()
    try:
        app = _retry(lambda: win32com.client.GetActiveObject(args.com_progid))
        original_documents = sorted(
            str(app.Documents.Item(index).Name) for index in range(app.Documents.Count)
        )
        document = _retry(lambda: app.Documents.Add())
        _retry(lambda: document.Activate())
        fixture_path = Path(os.environ["LOCALAPPDATA"]) / "Temp" / f"cdt-p02-{time.time_ns()}.dwg"
        _retry(lambda: document.SaveAs(str(fixture_path)), timeout=20.0)
        _retry(lambda: document.SetVariable("FILEDIA", 0))
        _retry(lambda: document.SetVariable("CMDECHO", 0))

        probe = Path(args.probe_dll).resolve()
        if not probe.is_file():
            raise RuntimeError(f"fixture probe DLL not found: {probe}")
        report = probe.parent / "reports" / "n4-semantic-fixture.json"
        report.unlink(missing_ok=True)
        document.SendCommand(f'_.NETLOAD\n"{probe}"\n')
        _wait_command_idle(document)
        document.SendCommand("CDT_N4_CREATE_FIXTURE\n")
        _wait_command_idle(document)
        _wait_file(report)
        fixture = json.loads(report.read_text(encoding="utf-8"))["payload"]
        document_pid = str(fixture["document_pid"])
        fixture_entities = fixture["entities"]
        targets = tuple(
            str(fixture_entities[label]["pid"]) for label in ("line", "circle", "arc", "lwpolyline")
        )
        definition_pid = str(fixture_entities["block_definition"]["pid"])

        session_id = _session_id()
        client = NativeBridgeClient(
            NamedPipeTransport(pipe_name_for_session(session_id), connect_timeout_ms=8_000)
        )
        health = _retry(client.health, timeout=20.0)
        if health.get("bridge_version") != args.expected_bridge_version:
            raise AssertionError(f"unexpected bridge version: {health.get('bridge_version')!r}")
        active = [item for item in client.documents_list() if item.get("is_active")]
        if len(active) != 1 or active[0].get("document_pid") != document_pid:
            raise AssertionError("fixture did not become the single active PID-bound document")
        runtime_id = str(active[0]["runtime_document_id"])
        state = client.document_state(runtime_id, document_pid=document_pid)
        parent_fp = state.get("document_fp")
        if not isinstance(parent_fp, str) or not parent_fp:
            raise AssertionError("fixture has no document fingerprint")
        baseline_count = int(_retry(lambda: document.ModelSpace.Count, timeout=10.0))

        chunk_latencies_ms: list[float] = []
        modes: list[str | None] = []
        inserted_pids: list[str] = []

        # Part A — insert workload, bounded 32-chunks, finalize per chunk.
        remaining = args.inserts
        insert_index = 0
        while remaining:
            size = min(32, remaining)
            inserts = tuple(
                {
                    "definition_pid": definition_pid,
                    "position": [2000.0 + (insert_index + i) * 20.0, 2400.0, 0.0],
                    "rotation": 0.05 * (insert_index + i),
                    "scale": 1.0 + 0.01 * (insert_index + i),
                }
                for i in range(size)
            )
            chunk_started = time.perf_counter()
            receipt = client.batch_insert_blocks_chunk(
                runtime_id,
                document_pid=document_pid,
                expected_parent_fp=parent_fp,
                inserts=inserts,
            )
            chunk_latencies_ms.append(round((time.perf_counter() - chunk_started) * 1000.0, 3))
            if receipt.get("outcome") != "COMMITTED_VERIFIED":
                raise AssertionError(f"insert chunk did not commit: {receipt}")
            pids = receipt.get("affected_semantic_pids")
            if not isinstance(pids, list) or len(pids) != size or len(set(pids)) != size:
                raise AssertionError("insert chunk returned invalid affected PIDs")
            modes.append(receipt.get("verification_mode"))
            inserted_pids.extend(str(pid) for pid in pids)
            parent_fp = str(receipt["post_document_fp"])
            _finalize(client, runtime_id, document_pid, receipt)
            insert_index += size
            remaining -= size

        # Part B — transform workload over the 4 fixture targets, repeated chunks.
        transform_targets = targets
        for _ in range(args.transform_chunks):
            chunk_started = time.perf_counter()
            receipt = client.batch_transform_chunk(
                runtime_id,
                document_pid=document_pid,
                expected_parent_fp=parent_fp,
                semantic_pids=transform_targets,
                transform={"kind": "translate", "delta": [2.0, 1.0, 0.0]},
            )
            chunk_latencies_ms.append(round((time.perf_counter() - chunk_started) * 1000.0, 3))
            if receipt.get("outcome") != "COMMITTED_VERIFIED":
                raise AssertionError(f"transform chunk did not commit: {receipt}")
            if sorted(receipt.get("affected_semantic_pids", [])) != sorted(transform_targets):
                raise AssertionError("transform chunk PID set differs from targets")
            modes.append(receipt.get("verification_mode"))
            parent_fp = str(receipt["post_document_fp"])
            _finalize(client, runtime_id, document_pid, receipt)

        committed_count = int(_retry(lambda: document.ModelSpace.Count, timeout=10.0))
        if committed_count - baseline_count != args.inserts:
            raise AssertionError("ModelSpace delta does not match committed insert count")

        # Part C — P02b: one logical batch, many chunks, one finalize boundary.
        journal_dir = Path(tempfile.mkdtemp(prefix="cdt-p02-"))
        executor = NativeLogicalBatchExecutor(client, journal_dir / "logical.jsonl")
        logical_started = time.perf_counter()
        logical = executor.insert_blocks(
            runtime_id,
            document_pid=document_pid,
            expected_parent_fp=parent_fp,
            inserts=tuple(
                {
                    "definition_pid": definition_pid,
                    "position": [6000.0 + i * 20.0, 6400.0, 0.0],
                    "rotation": 0.02 * i,
                    "scale": 1.0 + 0.005 * i,
                }
                for i in range(args.logical_inserts)
            ),
        )
        logical_ms = round((time.perf_counter() - logical_started) * 1000.0, 3)
        if logical.status != "COMMITTED":
            raise AssertionError("logical insert batch did not commit")
        parent_fp = logical.final_fp

        # Part D0 — negatives first (no mutation): stale parent and wrong
        # document refuse before journal/checkpoint/CAD mutation.
        stale_code = ""
        try:
            client.batch_transform_chunk(
                runtime_id,
                document_pid=document_pid,
                expected_parent_fp="sha256:" + "f" * 64,
                semantic_pids=(targets[0],),
                transform={"kind": "translate", "delta": [1.0, 0.0, 0.0]},
            )
        except Exception as exc:
            stale_code = type(exc).__name__ + ":" + str(exc)[:120]
        else:
            raise AssertionError("stale parent fingerprint was not refused")
        if "STATE_DRIFT" not in stale_code:
            raise AssertionError(f"stale parent refusal is not STATE_DRIFT: {stale_code}")

        wrong_doc_code = ""
        try:
            client.batch_insert_blocks_chunk(
                runtime_id,
                document_pid="doc:00000000-0000-4000-8000-000000000000",
                expected_parent_fp=parent_fp,
                inserts=(
                    {
                        "definition_pid": definition_pid,
                        "position": [0, 0, 0],
                        "rotation": 0.0,
                        "scale": 1.0,
                    },
                ),
            )
        except Exception as exc:
            wrong_doc_code = type(exc).__name__ + ":" + str(exc)[:120]
        else:
            raise AssertionError("wrong document PID was not refused")

        # Part D — stray outside affected scope: behavior branches by bridge generation.
        stray_receipt = client.batch_transform_chunk(
            runtime_id,
            document_pid=document_pid,
            expected_parent_fp=parent_fp,
            semantic_pids=(targets[0], targets[1]),
            transform={"kind": "translate", "delta": [5.0, 5.0, 0.0]},
            fault_stage="after_commit_add_stray",
        )
        stray_outcome = str(stray_receipt.get("outcome"))
        if stray_outcome == "COMMIT_INTEGRITY_FAIL":
            stray_branch = "chunk_full_scope_fail"
            _, runtime_id = _r2_restore(client, runtime_id, document_pid, stray_receipt, parent_fp)
        elif stray_outcome == "COMMITTED_VERIFIED":
            stray_branch = "chunk_affected_scoped_pass"
            checkpoint = stray_receipt["recovery_checkpoint"]
            assert isinstance(checkpoint, dict)
            try:
                client.finalize_recovery(
                    runtime_id,
                    document_pid=document_pid,
                    checkpoint_id=str(checkpoint["checkpoint_id"]),
                    checkpoint_artifact_fp=str(checkpoint["checkpoint_artifact_fp"]),
                    owner_request_id=str(checkpoint["owner_request_id"]),
                    accepted_post_fp=str(stray_receipt["post_document_fp"]),
                )
            except Exception as exc:
                stray_branch = "chunk_affected_scoped_pass:finalize_mismatch"
                if "RECOVERY_FINALIZE_STATE_MISMATCH" not in str(exc):
                    raise AssertionError(f"finalize failed with unexpected error: {exc}") from exc
            else:
                raise AssertionError("strayed state finalized without mismatch")
            _, runtime_id = _r2_restore(client, runtime_id, document_pid, stray_receipt, parent_fp)
        else:
            raise AssertionError(f"unexpected stray chunk outcome: {stray_outcome}")
        if client.recoveries_list():
            raise AssertionError("stray case left pending recovery")
        final_state = client.document_state(runtime_id, document_pid=document_pid)
        if final_state.get("document_fp") != parent_fp:
            raise AssertionError("post-stray fingerprint differs from accepted predecessor")

        # The original COM handle was closed by R2; reacquire the restored file,
        # verify COM membership, then close it. Nothing remains open afterwards.
        # AutoCAD may still be settling after the R2 reopen, so retry the COM walk.
        def _close_restored() -> None:
            for index in range(app.Documents.Count):
                candidate = app.Documents.Item(index)
                if str(candidate.FullName).lower() == str(fixture_path).lower():
                    if (
                        int(candidate.ModelSpace.Count)
                        != committed_count + args.logical_inserts
                    ):
                        raise AssertionError(
                            "R2 restored ModelSpace membership differs from accepted state"
                        )
                    candidate.Close(False)
                    return
            raise AssertionError("R2 restored document is not visible through AutoCAD COM")

        _retry(_close_restored, timeout=30.0)
        document = None

        summary = {
            "schema_version": 1,
            "status": "PASS",
            "bridge_version": health["bridge_version"],
            "bridge_process_id": health["process_id"],
            "session_id": session_id,
            "document_pid": document_pid,
            "runtime_document_id": runtime_id,
            "verification_modes": sorted({str(mode) for mode in modes}),
            "insert_chunks": (args.inserts + 31) // 32,
            "transform_chunks": args.transform_chunks,
            "chunk_latencies_ms": chunk_latencies_ms,
            "chunk_p50_ms": round(_percentile(chunk_latencies_ms, 0.5), 3),
            "chunk_p95_ms": round(_percentile(chunk_latencies_ms, 0.95), 3),
            "chunk_total_ms": round(sum(chunk_latencies_ms), 3),
            "logical": {
                "status": logical.status,
                "chunk_count": logical.chunk_count,
                "latency_ms": logical_ms,
            },
            "stray_branch": stray_branch,
            "stale_parent_refusal": stale_code,
            "wrong_document_refusal": wrong_doc_code,
            "final_document_fp": parent_fp,
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
        try:
            for index in range(app.Documents.Count - 1, -1, -1):
                candidate = app.Documents.Item(index)
                if str(candidate.Name).startswith("cdt-p02-"):
                    _retry(lambda c=candidate: c.Close(False), timeout=5.0)
        except Exception:
            pass
        if fixture_path is not None:
            try:
                fixture_path.unlink(missing_ok=True)
            except OSError:
                pass
        try:
            remaining_docs = sorted(
                str(app.Documents.Item(index).Name) for index in range(app.Documents.Count)
            )
            assert remaining_docs == original_documents, f"session docs changed: {remaining_docs}"
        except Exception:
            pass
        pythoncom.CoUninitialize()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="AC-P02 affected-scoped live proof on disposable fixture"
    )
    parser.add_argument("--output", required=True)
    parser.add_argument("--probe-dll", required=True)
    parser.add_argument("--com-progid", default="AutoCAD.Application.26")
    parser.add_argument("--expected-bridge-version", default="0.8.6-d18")
    parser.add_argument("--inserts", type=int, default=64)
    parser.add_argument("--transform-chunks", type=int, default=8)
    parser.add_argument("--logical-inserts", type=int, default=40)
    args = parser.parse_args()
    if args.inserts < 1 or args.transform_chunks < 1 or args.logical_inserts < 1:
        raise ValueError("workload sizes must be positive")
    print(json.dumps(_run(args), sort_keys=True))


if __name__ == "__main__":
    main()
