"""B4 caller-state binding live acceptance — refuse wrong/stale caller state before mutation.
Wing: ops | Topic: caller-state-binding | Updated: 2026-09-15 15:00
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastmcp import Client

from cdt_autocad import __version__
from cdt_autocad.config import Settings
from cdt_autocad.contract_identity import CONTRACT_VERSION
from cdt_autocad.server import create_mcp

EXPECTED_BRIDGE_VERSION = "0.8.2-mp7"


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


def _payload(result: Any, tool_name: str) -> dict[str, Any]:
    if getattr(result, "is_error", False):
        content = getattr(result, "structured_content", None) or {}
        raise RuntimeError(f"public MCP tool {tool_name} failed: {content}")
    content = getattr(result, "structured_content", None)
    if not isinstance(content, dict):
        raise RuntimeError(f"public MCP tool {tool_name} returned no structured object")
    return content


def _assert_conflict(result: Any, label: str) -> dict[str, Any]:
    if getattr(result, "is_error", False) is not True:
        raise AssertionError(f"{label} unexpectedly reached mutation")
    content = getattr(result, "structured_content", None) or {}
    if content.get("kind") != "conflict":
        raise AssertionError(f"{label} returned unexpected error payload: {content}")
    return content


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    if os.name != "nt":
        raise RuntimeError("B4 live acceptance requires Windows AutoCAD")

    import pythoncom
    import win32com.client

    pythoncom.CoInitialize()
    created_document = None
    disposable_path: Path | None = None
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    try:
        application = _retry(lambda: win32com.client.GetActiveObject(args.com_progid))
        created_document = _retry(lambda: application.Documents.Add())
        _retry(lambda: created_document.Activate())
        disposable_path = Path(os.environ["LOCALAPPDATA"]) / "Temp" / f"cdt-b4-{time.time_ns()}.dwg"
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

        settings = Settings(
            allowed_paths=(disposable_path.parent.resolve(),),
            backend="com",
            com_progid=args.com_progid,
            com_attach_policy="attach_only",
        )
        app = create_mcp(settings)
        async with Client(app) as client:
            help_payload = _payload(await client.call_tool("help", {}), "help")
            if help_payload.get("provider_version") != __version__:
                raise AssertionError("live provider version differs from source identity")
            if help_payload.get("contract_version") != CONTRACT_VERSION:
                raise AssertionError("live contract version differs from source identity")

            native = _payload(
                await client.call_tool("native_integrity_status", {}),
                "native_integrity_status",
            )
            bridge = native.get("bridge") or {}
            active = native.get("active_document") or {}
            if bridge.get("bridge_version") != EXPECTED_BRIDGE_VERSION:
                raise AssertionError(f"unexpected bridge version: {bridge.get('bridge_version')!r}")
            if active.get("document_pid") != document_pid:
                raise AssertionError("public facade did not bind the disposable fixture document")
            baseline_fp = str(active["document_fp"])
            baseline_count = int(active["entity_count"])

            action = {
                "operation": "create_entities",
                "entities": [
                    {
                        "kind": "line",
                        "start": [123456.0, 234567.0, 0.0],
                        "end": [123466.0, 234577.0, 0.0],
                    }
                ],
            }
            common = {
                "feature_id": "acceptance.b4.caller-binding",
                "feature_sequence": 1,
                "correlation_id": f"b4-live-{time.time_ns()}",
                "actions": [action],
            }

            wrong_pid = await client.call_tool(
                "feature_execute",
                {
                    **common,
                    "document_pid": "pid:00000000-0000-4000-8000-000000000000",
                    "expected_parent_fp": baseline_fp,
                },
                raise_on_error=False,
            )
            wrong_pid_error = _assert_conflict(wrong_pid, "wrong document PID")
            after_wrong = _payload(
                await client.call_tool("native_integrity_status", {}),
                "native_integrity_status(after wrong PID)",
            )["active_document"]
            if after_wrong["document_fp"] != baseline_fp or int(after_wrong["entity_count"]) != baseline_count:
                raise AssertionError("wrong-document refusal changed CAD state")

            stale_fp = "sha256:" + "9" * 64
            stale_parent = await client.call_tool(
                "feature_execute",
                {
                    **common,
                    "document_pid": document_pid,
                    "expected_parent_fp": stale_fp,
                },
                raise_on_error=False,
            )
            stale_parent_error = _assert_conflict(stale_parent, "stale parent fingerprint")
            after_stale = _payload(
                await client.call_tool("native_integrity_status", {}),
                "native_integrity_status(after stale parent)",
            )["active_document"]
            if after_stale["document_fp"] != baseline_fp or int(after_stale["entity_count"]) != baseline_count:
                raise AssertionError("stale-parent refusal changed CAD state")

            committed = _payload(
                await client.call_tool(
                    "feature_execute",
                    {
                        **common,
                        "document_pid": document_pid,
                        "expected_parent_fp": baseline_fp,
                    },
                ),
                "feature_execute(valid caller state)",
            )
            if committed.get("status") != "COMMITTED":
                raise AssertionError(f"valid caller state did not commit: {committed}")
            if committed.get("initial_document_fp") != baseline_fp:
                raise AssertionError("valid mutation did not bind caller predecessor fingerprint")
            committed_fp = str(committed["final_document_fp"])
            if committed_fp == baseline_fp:
                raise AssertionError("valid mutation did not advance semantic document state")

            after_commit = _payload(
                await client.call_tool("native_integrity_status", {}),
                "native_integrity_status(after commit)",
            )["active_document"]
            if after_commit["document_fp"] != committed_fp:
                raise AssertionError("public commit receipt differs from independent native status read-back")
            if int(after_commit["entity_count"]) != baseline_count + 1:
                raise AssertionError("valid caller-state feature changed an unexpected entity count")

            replay_stale = await client.call_tool(
                "feature_execute",
                {
                    **common,
                    "feature_sequence": 2,
                    "document_pid": document_pid,
                    "expected_parent_fp": baseline_fp,
                },
                raise_on_error=False,
            )
            replay_stale_error = _assert_conflict(replay_stale, "stale replay predecessor")
            final_state = _payload(
                await client.call_tool("native_integrity_status", {}),
                "native_integrity_status(final)",
            )["active_document"]
            if final_state["document_fp"] != committed_fp or int(final_state["entity_count"]) != baseline_count + 1:
                raise AssertionError("stale replay refusal changed committed CAD state")

        _retry(lambda: created_document.Save())
        summary = {
            "schema_version": 1,
            "status": "PASS",
            "provider_version": __version__,
            "contract_version": CONTRACT_VERSION,
            "bridge_version": EXPECTED_BRIDGE_VERSION,
            "document_pid": document_pid,
            "baseline_document_fp": baseline_fp,
            "committed_document_fp": committed_fp,
            "baseline_entity_count": baseline_count,
            "final_entity_count": baseline_count + 1,
            "wrong_document_refused": True,
            "wrong_document_error": wrong_pid_error.get("error"),
            "stale_parent_refused": True,
            "stale_parent_error": stale_parent_error.get("error"),
            "stale_replay_refused": True,
            "stale_replay_error": replay_stale_error.get("error"),
            "zero_mutation_before_valid_commit": True,
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
    parser = argparse.ArgumentParser(description="Run B4 caller-state binding live acceptance")
    parser.add_argument("--output", required=True)
    parser.add_argument("--probe-dll", required=True)
    parser.add_argument("--com-progid", default="AutoCAD.Application.26")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(_run(args)), sort_keys=True))


if __name__ == "__main__":
    main()
