"""Feature-streaming live acceptance — feature-local rollback and 300 ms presentation pacing.
Wing: ops | Topic: production-feature-streaming | Updated: 2026-09-11 20:30
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
from cdt_autocad.native_bridge.feature_stream import FeatureStreamExecutor
from cdt_autocad.native_bridge.transport_windows import NamedPipeTransport, pipe_name_for_session

EXPECTED_BRIDGE_VERSION = "0.8.1-g3"
CORRELATION_ID = "feature-stream-live-2026-09-11"


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


def _lines(start: int, count: int, *, y: float) -> list[dict[str, Any]]:
    return [
        {
            "kind": "line",
            "start": [float(start + i) * 4.0, y, 0.0],
            "end": [float(start + i) * 4.0 + 2.0, y, 0.0],
        }
        for i in range(count)
    ]


def _executor(client: NativeBridgeClient, output_dir: Path, name: str) -> FeatureStreamExecutor:
    return FeatureStreamExecutor(client, output_dir / f"{name}.jsonl")


def _run(args: argparse.Namespace) -> dict[str, Any]:
    if os.name != "nt":
        raise RuntimeError("feature-stream live acceptance requires Windows AutoCAD")

    import pythoncom
    import win32com.client

    pythoncom.CoInitialize()
    created_document = None
    disposable_path: Path | None = None
    started = time.perf_counter()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        application = _retry(lambda: win32com.client.GetActiveObject(args.com_progid))
        original_documents = [str(application.Documents.Item(i).Name) for i in range(application.Documents.Count)]
        created_document = _retry(lambda: application.Documents.Add())
        _retry(lambda: created_document.Activate())
        disposable_path = Path(os.environ["LOCALAPPDATA"]) / "Temp" / f"cdt-feature-stream-{time.time_ns()}.dwg"
        _retry(lambda: created_document.SaveAs(str(disposable_path)))
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
        active = [row for row in client.documents_list() if row.get("is_active")]
        if len(active) != 1 or active[0].get("document_pid") != document_pid:
            raise AssertionError("fixture did not bind to exactly one active PID-bearing document")
        runtime_id = str(active[0]["runtime_document_id"])
        baseline = client.document_state(runtime_id, document_pid=document_pid)
        baseline_fp = str(baseline["document_fp"])
        baseline_count = int(baseline["entity_count"])

        feature1 = _executor(client, output.parent, "feature-stream-01").execute(
            runtime_id,
            document_pid=document_pid,
            expected_parent_fp=baseline_fp,
            feature_id="showcase.centerline",
            feature_sequence=1,
            correlation_id=CORRELATION_ID,
            actions=[{"operation": "create_entities", "entities": _lines(10_000, 40, y=10_000.0)}],
        )
        if feature1.status != "COMMITTED" or feature1.recommended_next_delay_ms != 300:
            raise AssertionError("feature 1 did not commit with the production pacing receipt")
        state1 = client.document_state(runtime_id, document_pid=document_pid)
        if state1.get("document_fp") != feature1.final_fp or int(state1["entity_count"]) != baseline_count + 40:
            raise AssertionError("feature 1 persisted state differs from its receipt")

        pacing_started = time.perf_counter()
        time.sleep(feature1.recommended_next_delay_ms / 1000.0)
        pacing_elapsed_ms = (time.perf_counter() - pacing_started) * 1000.0
        if pacing_elapsed_ms < 280.0:
            raise AssertionError("feature presentation pacing did not honor the 300 ms default")

        # Feature 2 intentionally commits its first 32-object micro-chunk, then submits a duplicate
        # of the first geometry in the second micro-chunk. Native validation must reject only this
        # feature and R2 must restore feature 2's predecessor (feature 1), not the original drawing.
        feature2_entities = _lines(20_000, 32, y=20_000.0)
        feature2_entities.append(dict(feature2_entities[0]))
        feature2 = _executor(client, output.parent, "feature-stream-02").execute(
            runtime_id,
            document_pid=document_pid,
            expected_parent_fp=feature1.final_fp,
            feature_id="showcase.curb-edge",
            feature_sequence=2,
            correlation_id=CORRELATION_ID,
            actions=[{"operation": "create_entities", "entities": feature2_entities}],
        )
        if feature2.status != "ROLLED_BACK_VERIFIED":
            raise AssertionError("feature 2 injected geometry error did not rollback")
        if feature2.final_fp != feature1.final_fp:
            raise AssertionError("feature 2 rollback crossed the feature boundary")
        if feature2.failure is None or feature2.failure.get("native_chunk_index") != 1:
            raise AssertionError("feature 2 did not report the failing native chunk")
        runtime_id = feature2.runtime_document_id
        created_document = _retry(lambda: application.ActiveDocument)
        state2 = client.document_state(runtime_id, document_pid=document_pid)
        if state2.get("document_fp") != feature1.final_fp or int(state2["entity_count"]) != baseline_count + 40:
            raise AssertionError("feature 2 rollback did not preserve feature 1 exactly")

        feature3 = _executor(client, output.parent, "feature-stream-03").execute(
            runtime_id,
            document_pid=document_pid,
            expected_parent_fp=feature1.final_fp,
            feature_id="showcase.corner-detail",
            feature_sequence=3,
            correlation_id=CORRELATION_ID,
            actions=[{"operation": "create_entities", "entities": _lines(30_000, 8, y=30_000.0)}],
        )
        if feature3.status != "COMMITTED":
            raise AssertionError("feature 3 could not continue after isolated feature 2 rollback")
        state3 = client.document_state(runtime_id, document_pid=document_pid)
        if state3.get("document_fp") != feature3.final_fp or int(state3["entity_count"]) != baseline_count + 48:
            raise AssertionError("feature 3 persisted state differs from its receipt")
        if client.recoveries_list():
            raise AssertionError("feature-stream acceptance ended with pending recovery state")

        _retry(lambda: created_document.Save())
        summary = {
            "schema_version": 1,
            "status": "PASS",
            "execution_model": "feature-based-chunks-streaming-v1",
            "bridge_version": health["bridge_version"],
            "bridge_process_id": health["process_id"],
            "session_id": session_id,
            "document_pid": document_pid,
            "correlation_id": CORRELATION_ID,
            "baseline_document_fp": baseline_fp,
            "feature_1": {
                "feature_id": feature1.feature_id,
                "status": feature1.status,
                "native_chunks": feature1.native_chunk_count,
                "affected_pids": len(feature1.affected_semantic_pids),
                "post_document_fp": feature1.final_fp,
                "recommended_next_delay_ms": feature1.recommended_next_delay_ms,
                "observed_delay_ms": round(pacing_elapsed_ms, 3),
            },
            "feature_2": {
                "feature_id": feature2.feature_id,
                "status": feature2.status,
                "failed_native_chunk_index": feature2.failure["native_chunk_index"],
                "rollback_document_fp": feature2.final_fp,
                "prior_feature_preserved": feature2.final_fp == feature1.final_fp,
                "recommended_next_delay_ms": feature2.recommended_next_delay_ms,
            },
            "feature_3": {
                "feature_id": feature3.feature_id,
                "status": feature3.status,
                "native_chunks": feature3.native_chunk_count,
                "affected_pids": len(feature3.affected_semantic_pids),
                "post_document_fp": feature3.final_fp,
            },
            "final_entity_count": int(state3["entity_count"]),
            "pending_recoveries": 0,
            "original_documents": original_documents,
            "duration_seconds": round(time.perf_counter() - started, 3),
        }
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
    parser = argparse.ArgumentParser(description="Run production feature-streaming live acceptance")
    parser.add_argument("--output", required=True)
    parser.add_argument("--probe-dll", required=True)
    parser.add_argument("--com-progid", default="AutoCAD.Application.26")
    args = parser.parse_args()
    print(json.dumps(_run(args), sort_keys=True))


if __name__ == "__main__":
    main()
