"""MP-G10 ACIS acceptance — adversarial booleans plus 10/100-part live soak.
Wing: ops | Topic: acis-acceptance | Updated: 2026-09-12 19:35
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import statistics
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

from cdt_autocad import __version__
from cdt_autocad.backends.com_backend import ComBackend
from cdt_autocad.config import Settings
from cdt_autocad.contract_identity import CONTRACT_VERSION, PUBLIC_TOOL_COUNT, contract_hash


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(len(ordered) * fraction) - 1))
    return ordered[index]


def _working_set_bytes(process_id: int) -> int | None:
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


def _window_ping(hwnd: int, *, timeout_ms: int = 2_000) -> float:
    import win32con
    import win32gui

    started = time.perf_counter()
    win32gui.SendMessageTimeout(
        hwnd,
        0,
        0,
        0,
        win32con.SMTO_ABORTIFHUNG,
        timeout_ms,
    )
    return (time.perf_counter() - started) * 1000.0


async def _runtime_identity(backend: ComBackend) -> tuple[int, int]:
    def _sync() -> tuple[int, int]:
        import win32process

        app = backend._app()
        hwnd = int(app.HWND)
        _thread_id, process_id = win32process.GetWindowThreadProcessId(hwnd)
        return int(process_id), hwnd

    return await backend._run(_sync, may_mutate_document=False)


async def _document_counts(backend: ComBackend) -> tuple[int, str, int]:
    def _sync() -> tuple[int, str, int]:
        doc = backend._doc()
        return (
            int(doc.ModelSpace.Count),
            str(doc.GetVariable("CMDNAMES") or "").strip(),
            int(doc.GetVariable("CMDACTIVE")),
        )

    return await backend._run(_sync, may_mutate_document=False)


async def _close_document(backend: ComBackend, name: str) -> None:
    await backend._run(lambda: backend._app().Documents.Item(name).Close(False))


async def _run_tier(
    backend: ComBackend,
    *,
    count: int,
    hwnd: int,
    max_call_ms: float,
    max_memory_growth_bytes: int,
) -> dict[str, Any]:
    created = await backend.document_new()
    name = str(created["name"])
    process_id, current_hwnd = await _runtime_identity(backend)
    if current_hwnd != hwnd:
        raise AssertionError("AutoCAD HWND changed unexpectedly before ACIS soak tier")
    memory_before = _working_set_bytes(process_id)
    latencies: list[float] = []
    ui_pings: list[float] = []
    try:
        baseline_count, names, active = await _document_counts(backend)
        if names or active != 0:
            raise AssertionError(f"AutoCAD was not idle before tier {count}: CMDNAMES={names!r} CMDACTIVE={active}")
        for index in range(count):
            x = float(index % 20) * 3.0
            y = float(index // 20) * 3.0
            started = time.perf_counter()
            solid = await backend.solid_box(x, y, 0.0, 1.0, 1.0, 1.0)
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            latencies.append(elapsed_ms)
            if solid["type"] != "3DSOLID" or float(solid["volume"]) <= 0.0:
                raise AssertionError(f"tier {count} returned invalid 3DSOLID at index {index}")
            if (index + 1) % 10 == 0 or index + 1 == count:
                ui_pings.append(_window_ping(hwnd))
        final_count, names, active = await _document_counts(backend)
        if final_count != baseline_count + count:
            raise AssertionError(
                f"tier {count} model-space count mismatch: baseline={baseline_count} final={final_count}"
            )
        if names or active != 0:
            raise AssertionError(f"AutoCAD was not idle after tier {count}: CMDNAMES={names!r} CMDACTIVE={active}")
        max_latency = max(latencies, default=0.0)
        if max_latency > max_call_ms:
            raise AssertionError(
                f"tier {count} exceeded ACIS call bound: max={max_latency:.3f}ms bound={max_call_ms:.3f}ms"
            )
        memory_loaded = _working_set_bytes(process_id)
    finally:
        await _close_document(backend, name)
    time.sleep(0.5)
    memory_after_close = _working_set_bytes(process_id)
    memory_growth = (
        None
        if memory_before is None or memory_after_close is None
        else memory_after_close - memory_before
    )
    if memory_growth is not None and memory_growth > max_memory_growth_bytes:
        raise AssertionError(
            f"tier {count} retained too much working set after close: {memory_growth} bytes"
        )
    return {
        "parts": count,
        "process_id": process_id,
        "latency_ms": {
            "mean": round(statistics.fmean(latencies), 3),
            "p95": round(_percentile(latencies, 0.95), 3),
            "max": round(max(latencies, default=0.0), 3),
        },
        "ui_ping_ms": {
            "count": len(ui_pings),
            "max": round(max(ui_pings, default=0.0), 3),
        },
        "memory": {
            "working_set_before_bytes": memory_before,
            "working_set_loaded_bytes": memory_loaded,
            "working_set_after_close_bytes": memory_after_close,
            "retained_delta_bytes": memory_growth,
        },
        "idle_verified": True,
        "modelspace_delta": count,
    }


async def _run_boolean_stress(backend: ComBackend, *, hwnd: int) -> dict[str, Any]:
    created = await backend.document_new()
    name = str(created["name"])
    latencies: list[float] = []
    volumes: list[float] = []
    try:
        for index in range(10):
            x = float(index) * 12.0
            box = await backend.solid_box(x, 0.0, 0.0, 8.0, 8.0, 8.0)
            cylinder = await backend.solid_cylinder(x, 0.0, 0.0, 1.5, 8.0)
            started = time.perf_counter()
            result = await backend.solid_boolean(box["handle"], cylinder["handle"], "subtract")
            latencies.append((time.perf_counter() - started) * 1000.0)
            volume = float(result["volume"])
            if not 0.0 < volume < float(box["volume"]):
                raise AssertionError(f"boolean stress volume invalid at iteration {index}: {volume}")
            volumes.append(volume)
            if result["tool_exists_after"] is not False:
                raise AssertionError("successful subtract did not consume Boolean tool solid")
            _window_ping(hwnd)
        _count, names, active = await _document_counts(backend)
        if names or active != 0:
            raise AssertionError("AutoCAD did not return idle after Boolean stress")
        return {
            "iterations": 10,
            "latency_ms": {
                "mean": round(statistics.fmean(latencies), 3),
                "p95": round(_percentile(latencies, 0.95), 3),
                "max": round(max(latencies), 3),
            },
            "min_result_volume": min(volumes),
            "max_result_volume": max(volumes),
            "idle_verified": True,
        }
    finally:
        await _close_document(backend, name)


async def _run_near_tangent(backend: ComBackend, *, hwnd: int) -> dict[str, Any]:
    created = await backend.document_new()
    name = str(created["name"])
    try:
        box = await backend.solid_box(0.0, 0.0, 0.0, 10.0, 10.0, 10.0)
        cylinder = await backend.solid_cylinder(6.99, 0.0, 0.0, 2.0, 10.0)
        started = time.perf_counter()
        result = await backend.solid_boolean(box["handle"], cylinder["handle"], "subtract")
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        volume = float(result["volume"])
        initial = float(box["volume"])
        if not 0.0 < volume <= initial:
            raise AssertionError(
                f"near-tangent subtract returned invalid volume: initial={initial} final={volume}"
            )
        ping_ms = _window_ping(hwnd)
        _count, names, active = await _document_counts(backend)
        if names or active != 0:
            raise AssertionError("AutoCAD did not return idle after near-tangent Boolean")
        return {
            "gap_or_overlap": "0.01-unit overlap against box half-length + cylinder radius",
            "operation": "subtract",
            "initial_volume": initial,
            "result_volume": volume,
            "latency_ms": round(elapsed_ms, 3),
            "ui_ping_ms": round(ping_ms, 3),
            "idle_verified": True,
        }
    finally:
        await _close_document(backend, name)


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    if sys.platform != "win32":
        raise RuntimeError("MP-G10 ACIS acceptance requires Windows + live AutoCAD")
    settings = replace(
        Settings.from_env(),
        backend="com",
        com_attach_policy="attach_only",
        com_progid=args.com_progid,
        com_call_timeout_seconds=60.0,
    )
    backend = ComBackend(settings)
    started = time.perf_counter()
    try:
        process_id, hwnd = await _runtime_identity(backend)
        process_before = process_id
        tiers = {
            "10": await _run_tier(
                backend,
                count=10,
                hwnd=hwnd,
                max_call_ms=args.max_call_ms,
                max_memory_growth_bytes=args.max_memory_growth_bytes,
            ),
            "100": await _run_tier(
                backend,
                count=100,
                hwnd=hwnd,
                max_call_ms=args.max_call_ms,
                max_memory_growth_bytes=args.max_memory_growth_bytes,
            ),
        }
        boolean_stress = await _run_boolean_stress(backend, hwnd=hwnd)
        near_tangent = await _run_near_tangent(backend, hwnd=hwnd)
        process_after, hwnd_after = await _runtime_identity(backend)
        if process_after != process_before or hwnd_after != hwnd:
            raise AssertionError("AutoCAD process/window identity changed during ACIS acceptance")
        if backend.status().get("integrity_uncertain"):
            raise AssertionError(
                f"backend integrity became uncertain: {backend.status().get('integrity_uncertain_reason')}"
            )
        git_result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=args.repo_root,
            capture_output=True,
            text=True,
            check=False,
        )
        summary = {
            "schema_version": 1,
            "status": "PASS",
            "finding_id": "MP-G10",
            "provider_version": __version__,
            "contract_version": CONTRACT_VERSION,
            "contract_hash": contract_hash(),
            "public_tool_count": PUBLIC_TOOL_COUNT,
            "source_head": git_result.stdout.strip() if git_result.returncode == 0 else None,
            "autocad": {
                "progid": args.com_progid,
                "process_id": process_id,
                "hwnd": hwnd,
                "process_stable": True,
            },
            "acceptance_bounds": {
                "max_single_acis_call_ms": args.max_call_ms,
                "max_retained_working_set_growth_bytes": args.max_memory_growth_bytes,
                "ui_ping_timeout_ms": 2_000,
            },
            "soak_tiers": tiers,
            "boolean_stress": boolean_stress,
            "near_tangent": near_tangent,
            "injected_boolean_failure": "covered by tests/test_com_3d.py::test_boolean_failure_quarantines_destructive_acis_state",
            "duration_seconds": round(time.perf_counter() - started, 3),
        }
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return summary
    finally:
        backend.shutdown()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run live MP-G10 ACIS adversarial/soak acceptance")
    parser.add_argument("--repo-root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--output", required=True)
    parser.add_argument("--com-progid", default="AutoCAD.Application.26")
    parser.add_argument("--max-call-ms", type=float, default=5_000.0)
    parser.add_argument("--max-memory-growth-bytes", type=int, default=512 * 1024 * 1024)
    args = parser.parse_args()
    if args.max_call_ms <= 0 or args.max_memory_growth_bytes <= 0:
        raise SystemExit("acceptance bounds must be positive")
    summary = asyncio.run(_run(args))
    print(json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    main()
