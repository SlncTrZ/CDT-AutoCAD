"""Marketing integrity demo — synthetic public-MCP feature-streaming showcase.
Wing: ops | Topic: marketing-demo | Updated: 2026-09-11 21:54
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import ctypes
import json
import os
import sys
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastmcp import Client

from cdt_autocad.config import Settings
from cdt_autocad.contract_identity import CONTRACT_VERSION, EXECUTION_MODEL, PUBLIC_TOOL_COUNT
from cdt_autocad.server import create_mcp

EXPECTED_BRIDGE_VERSION = "0.8.1-g3"
EXPECTED_PROVIDER_VERSION = "0.4.0rc1"
PRODUCT_PRESENTATION_DELAY_MS = 300
DEFAULT_RECORDING_DELAY_MS = 900
SCENE_BOUNDS = (950.0, 950.0, 1850.0, 1450.0)
DEMO_LAYER = "CDT-MARKETING"


def _line(start: tuple[float, float], end: tuple[float, float]) -> dict[str, object]:
    return {
        "kind": "line",
        "start": [float(start[0]), float(start[1]), 0.0],
        "end": [float(end[0]), float(end[1]), 0.0],
    }


def _circle(x: float, y: float, radius: float = 20.0) -> dict[str, object]:
    return {"kind": "circle", "center": [float(x), float(y), 0.0], "radius": float(radius)}


def feature_1_entities() -> list[dict[str, object]]:
    """Return a 40-line synthetic technical lattice that necessarily spans two native chunks."""
    entities = [
        _line((1000, 1000), (1800, 1000)),
        _line((1800, 1000), (1800, 1400)),
        _line((1800, 1400), (1000, 1400)),
        _line((1000, 1400), (1000, 1000)),
    ]
    entities.extend(_line((x, 1000), (x, 1400)) for x in range(1100, 1800, 100))
    entities.extend(_line((1000, y), (1800, y)) for y in range(1100, 1400, 100))
    for row in range(3):
        y = 1000 + row * 100
        for column in range(8):
            x = 1000 + column * 100
            entities.append(_line((x + 15, y + 15), (x + 85, y + 85)))
    entities.extend(
        [
            _line((1215, 1315), (1285, 1385)),
            _line((1515, 1315), (1585, 1385)),
        ]
    )
    if len(entities) != 40:
        raise AssertionError("marketing feature 1 must remain exactly 40 entities")
    return entities


def feature_2_rollback_entities() -> list[dict[str, object]]:
    """Return 32 unique circles followed by one exact duplicate in chunk two."""
    entities = [
        _circle(1050 + column * 100, 1050 + row * 100)
        for row in range(4)
        for column in range(8)
    ]
    entities.append(dict(entities[0]))
    return entities


def feature_3_entities() -> list[dict[str, object]]:
    """Return the accepted post-recovery node set used for the final camera frame."""
    return [
        _circle(1050 + column * 100, y)
        for y in (1050, 1350)
        for column in range(8)
    ]


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


def _wait_command_idle(document: Any, *, timeout: float = 45.0) -> None:
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
    raise RuntimeError(f"fixture report was not written: {path.name}")


def _windows_session_id() -> int:
    value = ctypes.c_uint32()
    if not ctypes.windll.kernel32.ProcessIdToSessionId(os.getpid(), ctypes.byref(value)):
        raise RuntimeError("could not resolve Windows session id")
    return int(value.value)


def _resolve_under_root(value: str) -> Path:
    path = Path(value).expanduser()
    return path.resolve() if path.is_absolute() else (ROOT / path).resolve()


def _append_event(path: Path, event: str, **payload: Any) -> None:
    row = {"time": datetime.now().astimezone().isoformat(), "event": event, **payload}
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def _payload(result: Any, tool_name: str) -> dict[str, Any]:
    if getattr(result, "is_error", False):
        content = getattr(result, "structured_content", None) or {}
        raise RuntimeError(f"public MCP tool {tool_name} failed: {content}")
    content = getattr(result, "structured_content", None)
    if not isinstance(content, dict):
        raise RuntimeError(f"public MCP tool {tool_name} returned no structured object")
    return content


def _save_mcp_image(result: Any, path: Path) -> bool:
    for part in getattr(result, "content", ()):
        data = getattr(part, "data", None)
        mime = str(getattr(part, "mimeType", "") or getattr(part, "mime_type", ""))
        if not isinstance(data, str) or mime != "image/png":
            continue
        try:
            decoded = base64.b64decode(data, validate=True)
        except ValueError:
            continue
        if not decoded.startswith(b"\x89PNG\r\n\x1a\n"):
            continue
        path.write_bytes(decoded)
        return True
    return False


def _prepare_autocad_window(application: Any) -> None:
    """Restore/maximize AutoCAD for camera capture without changing drawing semantics."""
    import win32con
    import win32gui

    hwnd = int(_retry(lambda: application.HWND, timeout=10.0, delay=0.1))
    win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    win32gui.ShowWindow(hwnd, win32con.SW_MAXIMIZE)
    try:
        win32gui.SetForegroundWindow(hwnd)
    except Exception:
        pass
    time.sleep(0.25)


def _close_stale_demo_documents(application: Any, output_dir: Path) -> list[str]:
    """Close only prior synthetic marketing drawings owned by this runner."""
    output_root = str(output_dir.resolve()).casefold()
    closed: list[str] = []
    documents = _retry(lambda: application.Documents, timeout=10.0, delay=0.1)
    count = int(_retry(lambda: documents.Count, timeout=10.0, delay=0.1))
    for index in range(count - 1, -1, -1):
        document = _retry(lambda index=index: documents.Item(index), timeout=10.0, delay=0.1)
        full_name = str(
            _retry(lambda document=document: getattr(document, "FullName", ""), timeout=10.0, delay=0.1)
            or ""
        )
        if not full_name:
            continue
        candidate = Path(full_name)
        if (
            candidate.name.startswith("CDT-AutoCAD-Marketing-")
            and str(candidate.parent.resolve()).casefold() == output_root
        ):
            _retry(lambda document=document: document.Close(False), timeout=10.0, delay=0.1)
            closed.append(str(candidate))
    for candidate in output_dir.glob("CDT-AutoCAD-Marketing-*.dwg"):
        try:
            candidate.unlink()
        except FileNotFoundError:
            pass
    return closed


def _close_only_demo_document(application: Any, demo_path: Path) -> None:
    wanted = str(demo_path.resolve()).casefold()
    documents = _retry(lambda: application.Documents, timeout=10.0, delay=0.1)
    count = int(_retry(lambda: documents.Count, timeout=10.0, delay=0.1))
    for index in range(count - 1, -1, -1):
        document = _retry(lambda index=index: documents.Item(index), timeout=10.0, delay=0.1)
        full_name = str(
            _retry(lambda document=document: getattr(document, "FullName", ""), timeout=10.0, delay=0.1)
            or ""
        )
        if full_name and str(Path(full_name).resolve()).casefold() == wanted:
            _retry(lambda document=document: document.Close(False), timeout=10.0, delay=0.1)
            return


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    if os.name != "nt":
        raise RuntimeError("marketing integrity demo requires Windows AutoCAD")
    if not PRODUCT_PRESENTATION_DELAY_MS <= args.recording_delay_ms <= 5_000:
        raise ValueError("recording-delay-ms must be between 300 and 5000")

    import pythoncom
    import win32com.client

    output_dir = _resolve_under_root(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    sealed_dir = output_dir / "sealed"
    sealed_dir.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    demo_path = output_dir / f"CDT-AutoCAD-Marketing-{run_id}.dwg"
    result_path = output_dir / "latest-result.json"
    event_path = output_dir / "latest-events.jsonl"
    screenshot_path = output_dir / "latest-frame.png"
    event_path.unlink(missing_ok=True)
    screenshot_path.unlink(missing_ok=True)

    application = None
    created_document = None
    success = False
    started = time.perf_counter()
    pythoncom.CoInitialize()
    try:
        session_id = _windows_session_id()
        _append_event(event_path, "session", session_id=session_id)
        if session_id == 0:
            raise RuntimeError("marketing demo must run in the interactive Windows user session, not Session 0")

        application = _retry(lambda: win32com.client.GetActiveObject(args.com_progid))
        _prepare_autocad_window(application)
        stale_documents = _close_stale_demo_documents(application, output_dir)
        _append_event(event_path, "stale_demo_cleanup", closed_documents=len(stale_documents))
        documents = _retry(lambda: application.Documents, timeout=10.0, delay=0.1)
        document_count = int(_retry(lambda: documents.Count, timeout=10.0, delay=0.1))
        original_documents = [
            str(
                _retry(
                    lambda index=index: documents.Item(index).Name,
                    timeout=10.0,
                    delay=0.1,
                )
            )
            for index in range(document_count)
        ]
        template_path = (
            Path(args.template).expanduser().resolve()
            if args.template
            else Path(os.environ["LOCALAPPDATA"])
            / "Autodesk"
            / "AutoCAD 2027"
            / "R26.0"
            / "enu"
            / "Template"
            / "acad.dwt"
        )
        if not template_path.is_file():
            raise RuntimeError(f"AutoCAD 2027 template not found: {template_path}")
        created_document = _retry(lambda: documents.Add(str(template_path)))
        _retry(lambda: created_document.Activate())
        _retry(lambda: created_document.SaveAs(str(demo_path)))
        _append_event(event_path, "document_created", demo_path=str(demo_path))

        probe_dll = _resolve_under_root(args.probe_dll)
        if not probe_dll.is_file():
            raise RuntimeError(f"PID bootstrap probe DLL not found: {probe_dll}")
        trusted_paths = str(_retry(lambda: created_document.GetVariable("TRUSTEDPATHS")))
        if str(probe_dll.parent).casefold() not in trusted_paths.casefold():
            raise RuntimeError("PID bootstrap probe directory is not already trusted by AutoCAD")
        report_path = probe_dll.parent / "reports" / "n4-semantic-fixture.json"
        report_path.unlink(missing_ok=True)
        original_filedia = int(_retry(lambda: created_document.GetVariable("FILEDIA")))
        original_cmdecho = int(_retry(lambda: created_document.GetVariable("CMDECHO")))
        try:
            _retry(lambda: created_document.SetVariable("FILEDIA", 0))
            _retry(lambda: created_document.SetVariable("CMDECHO", 0))
            created_document.SendCommand(f'_.NETLOAD\n"{probe_dll}"\n')
            _wait_command_idle(created_document)
            created_document.SendCommand("CDT_N4_CREATE_FIXTURE\n")
            _wait_command_idle(created_document)
            _wait_file(report_path)
        finally:
            _retry(lambda: created_document.SetVariable("FILEDIA", original_filedia))
            _retry(lambda: created_document.SetVariable("CMDECHO", original_cmdecho))
        fixture = json.loads(report_path.read_text(encoding="utf-8"))["payload"]
        document_pid = str(fixture["document_pid"])
        created_document = _retry(lambda: application.ActiveDocument)
        _retry(lambda: created_document.SaveAs(str(demo_path)))
        _append_event(event_path, "pid_bootstrap", document_pid=document_pid)

        settings = Settings(
            allowed_paths=(ROOT.resolve(),),
            backend="com",
            com_progid=args.com_progid,
            com_attach_policy="attach_only",
        )
        app = create_mcp(settings)
        async with Client(app) as client:
            tools = list(await client.list_tools())
            if len(tools) != PUBLIC_TOOL_COUNT:
                raise AssertionError(f"expected {PUBLIC_TOOL_COUNT} public tools, got {len(tools)}")

            help_payload = _payload(await client.call_tool("help", {}), "help")
            if help_payload.get("provider_version") != EXPECTED_PROVIDER_VERSION:
                raise AssertionError("marketing runner provider version does not match the RC identity")
            if help_payload.get("contract_version") != CONTRACT_VERSION:
                raise AssertionError("marketing runner contract identity mismatch")
            if help_payload.get("execution_model") != EXECUTION_MODEL:
                raise AssertionError("marketing runner execution model mismatch")

            native = _payload(await client.call_tool("native_integrity_status", {}), "native_integrity_status")
            bridge = native.get("bridge") or {}
            active = native.get("active_document") or {}
            if native.get("route") != "native-managed-bridge" or native.get("fallback") is not False:
                raise AssertionError("strong-integrity public route silently weakened")
            if bridge.get("bridge_version") != EXPECTED_BRIDGE_VERSION:
                raise AssertionError(f"unexpected bridge version: {bridge.get('bridge_version')!r}")
            if active.get("document_pid") != document_pid:
                raise AssertionError("public native facade is not bound to the synthetic demo document")
            baseline_fp = str(active["document_fp"])
            baseline_count = int(active["entity_count"])
            _append_event(
                event_path,
                "native_ready",
                bridge_version=bridge.get("bridge_version"),
                baseline_entity_count=baseline_count,
            )

            await client.call_tool(
                "document_configure_units",
                {
                    "insertion_units": "millimeters",
                    "measurement": "metric",
                    "linear_format": "decimal",
                    "linear_precision": 2,
                },
            )
            layers = _payload(await client.call_tool("layer_list", {}), "layer_list")
            layer_rows = layers.get("result") if "result" in layers else layers
            layer_names = {
                str(row.get("name", "")).casefold()
                for row in (layer_rows if isinstance(layer_rows, list) else [])
                if isinstance(row, dict)
            }
            if DEMO_LAYER.casefold() not in layer_names:
                await client.call_tool("layer_create", {"name": DEMO_LAYER, "color": 7})
            await client.call_tool("layer_set_current", {"name": DEMO_LAYER})
            await client.call_tool(
                "view_zoom_window",
                {"x1": SCENE_BOUNDS[0], "y1": SCENE_BOUNDS[1], "x2": SCENE_BOUNDS[2], "y2": SCENE_BOUNDS[3]},
            )

            feature_1 = _payload(
                await client.call_tool(
                    "feature_execute",
                    {
                        "feature_id": "marketing.integrity.lattice",
                        "feature_sequence": 1,
                        "correlation_id": run_id,
                        "actions": [{"operation": "create_entities", "entities": feature_1_entities()}],
                    },
                ),
                "feature_execute(feature_1)",
            )
            if feature_1.get("status") != "COMMITTED" or int(feature_1.get("native_chunk_count", 0)) != 2:
                raise AssertionError("marketing feature 1 did not commit as a two-chunk feature")
            if int(feature_1.get("recommended_next_delay_ms", -1)) != PRODUCT_PRESENTATION_DELAY_MS:
                raise AssertionError("public feature pacing recommendation changed unexpectedly")
            _append_event(
                event_path,
                "feature_1_committed",
                native_chunks=feature_1.get("native_chunk_count"),
                affected=len(feature_1.get("affected_semantic_pids") or []),
            )
            time.sleep(args.recording_delay_ms / 1000.0)

            feature_2 = _payload(
                await client.call_tool(
                    "feature_execute",
                    {
                        "feature_id": "marketing.integrity.injected-failure",
                        "feature_sequence": 2,
                        "correlation_id": run_id,
                        "actions": [
                            {"operation": "create_entities", "entities": feature_2_rollback_entities()}
                        ],
                    },
                ),
                "feature_execute(feature_2)",
            )
            failure = feature_2.get("failure") or {}
            if feature_2.get("status") != "ROLLED_BACK_VERIFIED":
                raise AssertionError("injected marketing feature did not end in verified rollback")
            if int(failure.get("native_chunk_index", -1)) != 1:
                raise AssertionError("injected failure did not occur in the second native chunk")
            if feature_2.get("final_document_fp") != feature_1.get("final_document_fp"):
                raise AssertionError("failed marketing feature crossed the feature-local rollback boundary")
            _append_event(
                event_path,
                "feature_2_rolled_back",
                failed_native_chunk_index=failure.get("native_chunk_index"),
                prior_feature_preserved=True,
            )
            time.sleep(args.recording_delay_ms / 1000.0)

            feature_3 = _payload(
                await client.call_tool(
                    "feature_execute",
                    {
                        "feature_id": "marketing.integrity.recovery-nodes",
                        "feature_sequence": 3,
                        "correlation_id": run_id,
                        "actions": [{"operation": "create_entities", "entities": feature_3_entities()}],
                    },
                ),
                "feature_execute(feature_3)",
            )
            if feature_3.get("status") != "COMMITTED":
                raise AssertionError("marketing feature 3 could not continue after verified rollback")
            _append_event(
                event_path,
                "feature_3_committed",
                native_chunks=feature_3.get("native_chunk_count"),
                affected=len(feature_3.get("affected_semantic_pids") or []),
            )
            time.sleep(args.recording_delay_ms / 1000.0)

            await client.call_tool(
                "view_zoom_window",
                {"x1": SCENE_BOUNDS[0], "y1": SCENE_BOUNDS[1], "x2": SCENE_BOUNDS[2], "y2": SCENE_BOUNDS[3]},
            )
            await client.call_tool("document_save", {})
            seal = _payload(
                await client.call_tool("artifact_seal", {"destination_dir": str(sealed_dir)}),
                "artifact_seal",
            )

            screenshot_saved = False
            try:
                _prepare_autocad_window(application)
                screenshot_result = await client.call_tool("view_screenshot", {})
                screenshot_saved = _save_mcp_image(screenshot_result, screenshot_path)
            except Exception as exc:
                _append_event(event_path, "screenshot_warning", error_type=type(exc).__name__)

            final_native = _payload(
                await client.call_tool("native_integrity_status", {}), "native_integrity_status(final)"
            )
            final_active = final_native.get("active_document") or {}
            expected_final_count = baseline_count + 40 + 16
            if int(final_active.get("entity_count", -1)) != expected_final_count:
                raise AssertionError("final semantic entity count does not match committed features")

            result = {
                "schema_version": 1,
                "status": "PASS",
                "purpose": "synthetic marketing integrity demo",
                "clean_ip_fixture": True,
                "source_assets": "repo-owned synthetic geometry only",
                "run_id": run_id,
                "session_id": session_id,
                "provider_version": EXPECTED_PROVIDER_VERSION,
                "contract_version": CONTRACT_VERSION,
                "public_tool_count": PUBLIC_TOOL_COUNT,
                "execution_model": EXECUTION_MODEL,
                "bridge_version": bridge.get("bridge_version"),
                "baseline_document_fp": baseline_fp,
                "baseline_entity_count": baseline_count,
                "feature_1": {
                    "status": feature_1.get("status"),
                    "native_chunks": feature_1.get("native_chunk_count"),
                    "affected_pids": len(feature_1.get("affected_semantic_pids") or []),
                    "post_document_fp": feature_1.get("final_document_fp"),
                    "recommended_next_delay_ms": feature_1.get("recommended_next_delay_ms"),
                },
                "feature_2": {
                    "status": feature_2.get("status"),
                    "failed_native_chunk_index": failure.get("native_chunk_index"),
                    "prior_feature_preserved": True,
                    "rollback_document_fp": feature_2.get("final_document_fp"),
                },
                "feature_3": {
                    "status": feature_3.get("status"),
                    "native_chunks": feature_3.get("native_chunk_count"),
                    "affected_pids": len(feature_3.get("affected_semantic_pids") or []),
                    "post_document_fp": feature_3.get("final_document_fp"),
                },
                "final_entity_count": int(final_active["entity_count"]),
                "recording_delay_ms": int(args.recording_delay_ms),
                "product_recommended_delay_ms": PRODUCT_PRESENTATION_DELAY_MS,
                "demo_dwg": str(demo_path),
                "sealed_artifact": {
                    "path": seal.get("sealed_path"),
                    "sha256": seal.get("sha256"),
                    "size": seal.get("size"),
                },
                "screenshot": str(screenshot_path) if screenshot_saved else None,
                "original_documents": original_documents,
                "duration_seconds": round(time.perf_counter() - started, 3),
            }
            result_path.write_text(
                json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            _append_event(event_path, "pass", final_entity_count=result["final_entity_count"])
            success = True
            return result
    finally:
        if application is not None and (not args.keep_open or not success):
            try:
                _close_only_demo_document(application, demo_path)
            except Exception:
                pass
        pythoncom.CoUninitialize()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the synthetic CDT-AutoCAD marketing integrity demo in interactive Session 1"
    )
    parser.add_argument("--output-dir", default="artifacts/marketing")
    parser.add_argument(
        "--probe-dll",
        default="prototypes/pid-native/bin/Release/net10.0-windows/CDT.AutoCAD.PidProbe.dll",
    )
    parser.add_argument("--com-progid", default="AutoCAD.Application.26")
    parser.add_argument("--template", default="")
    parser.add_argument("--recording-delay-ms", type=int, default=DEFAULT_RECORDING_DELAY_MS)
    parser.add_argument("--keep-open", action="store_true")
    args = parser.parse_args()

    output_dir = _resolve_under_root(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    result_path = output_dir / "latest-result.json"
    try:
        result = asyncio.run(_run(args))
        if sys.stdout is not None:
            print(json.dumps(result, sort_keys=True))
        return 0
    except Exception as exc:
        failure = {
            "schema_version": 1,
            "status": "FAIL",
            "error_type": type(exc).__name__,
            "error": str(exc)[:1000],
            "time": datetime.now().astimezone().isoformat(),
        }
        result_path.write_text(
            json.dumps(failure, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        if sys.stderr is not None:
            print(json.dumps(failure, sort_keys=True), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
