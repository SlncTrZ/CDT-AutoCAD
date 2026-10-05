"""Live crash-recovery acceptance — REQUIRES live AutoCAD 2027 Session 1.

Wing: code | Topic: mp2-hot-reload | Updated: 2026-10-05 21:16

Read-only steady-state half runs against the supervised facade WITHOUT
touching any drawing: supervisor status, worker system_status and document
identity are observed, never mutated. No save, no mutation, no dialog
dismissal, no process kill.

The disruptive half (real acad process replacement, real bridge reload,
disconnect/reconnect under an open tracked transaction) is a printed
procedure for a maintenance window with a DISPOSABLE drawing and explicit
owner approval. It is never executed by this script.

Usage (live run only, owner present)::

    set CDT_AUTOCAD_AUTH_TOKEN=...
    python scripts/run_crash_recovery_live_acceptance.py --live ^
        --supervisor-url http://127.0.0.1:8000 --evidence out.json

Without --live the script exits 2 and runs nothing.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

DISRUPTIVE_PROCEDURE = """\
DISRUPTIVE LIVE PROCEDURE (maintenance window only, owner approval required):

1. Open a DISPOSABLE drawing in AutoCAD 2027 Session 1; never the user's
   production drawing. Record its path, PID and DBMOD before starting.
2. Supervisor restart: terminate the active worker process (not acad.exe),
   observe facade requests fail closed (502/503, no hang), reload/replace
   the worker, and prove traffic resumes on the new generation with
   pending_recovery_count=0 and zero drift on the disposable drawing.
3. Bridge disconnect: terminate acad.exe for the disposable session only,
   observe tracked-transaction loss quarantine fail-closed (no blind retry),
   relaunch acad in the same session, reattach, and reconcile through
   authoritative semantic read-back before any dependent mutation.
4. Uncertain reconcile: inject a lost logical.begin response, rediscover the
   checkpoint only by the originating owner fingerprint, refuse foreign
   adoption, restore the exact predecessor, and end with pending=0.
5. Record evidence JSON with pre/post acad PID, document path, DBMOD,
   Saved, generation ids and pending counts. Any user-drawing mutation,
   auto-save, dialog dismissal or production acad kill FAILS the run.
"""


async def _fetch_supervisor_status(url: str, token: str) -> dict:
    import httpx

    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(
            url.rstrip("/") + "/__cdt/status",
            headers={"authorization": f"Bearer {token}"},
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError("supervisor status is not a JSON object")
        return payload


async def _fetch_worker_status(url: str, token: str) -> dict:
    from fastmcp import Client

    async with Client(url.rstrip("/") + "/mcp", auth=token or None, timeout=30.0) as mcp:
        tools = await mcp.list_tools()
        status_result = await mcp.call_tool("system_status", {})
        status = dict(status_result.structured_content or {})
        info_result = await mcp.call_tool("document_info", {}, raise_on_error=False)
        document = {} if info_result.is_error else dict(info_result.structured_content or {})
        return {"tool_count": len(tools), "system_status": status, "document": document}


def _check_steady_state(supervisor: dict, worker: dict) -> list[str]:
    failures: list[str] = []
    if supervisor.get("phase") != "ready" or not supervisor.get("accepting_requests"):
        failures.append("supervisor is not in ready/accepting state")
    status = worker["system_status"]
    if status.get("provider") != "autocad":
        failures.append("worker provider identity is not autocad")
    if int(status.get("transaction_depth") or 0) != 0:
        failures.append("transaction_depth is not 0: live run requires idle state")
    if int(status.get("pending_recovery_count") or 0) != 0:
        failures.append("pending_recovery_count is not 0: reconcile before live run")
    coordinator = status.get("mutation_coordinator")
    if isinstance(coordinator, dict) and coordinator.get("quarantined") is not False:
        failures.append("mutation coordinator is quarantined: reconcile before live run")
    if status.get("timeout_uncertain") is not False:
        failures.append("timeout_uncertain is set: reconcile before live run")
    if status.get("integrity_uncertain") is not False:
        failures.append("integrity_uncertain is set: reconcile before live run")
    return failures


async def _run_live(args: argparse.Namespace, token: str) -> int:
    supervisor = await _fetch_supervisor_status(args.supervisor_url, token)
    worker = await _fetch_worker_status(args.supervisor_url, token)
    failures = _check_steady_state(supervisor, worker)
    evidence = {
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "kind": "crash-recovery-live-steady-state",
        "supervisor_url": args.supervisor_url,
        "supervisor": supervisor,
        "worker_tool_count": worker["tool_count"],
        "worker_system_status": worker["system_status"],
        "worker_document": worker["document"],
        "steady_state_failures": failures,
        "disruptive_procedure": DISRUPTIVE_PROCEDURE,
    }
    Path(args.evidence).write_text(json.dumps(evidence, indent=2, sort_keys=True), encoding="utf-8")
    print(f"evidence: {args.evidence}")
    print(f"worker tools: {worker['tool_count']}")
    print(f"document: {worker['document'].get('path')}")
    if failures:
        print("STEADY-STATE FAILURES:")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("steady-state PASS (read-only; disruptive procedure NOT executed)")
    print(DISRUPTIVE_PROCEDURE)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--supervisor-url", default="http://127.0.0.1:8000")
    parser.add_argument("--evidence", default="crash-recovery-live.json")
    args = parser.parse_args(argv)
    if not args.live:
        print("refusing: --live not given; nothing was executed", file=sys.stderr)
        return 2
    token = os.environ.get("CDT_AUTOCAD_AUTH_TOKEN", "").strip()
    if not token:
        print("refusing: CDT_AUTOCAD_AUTH_TOKEN is not set", file=sys.stderr)
        return 2
    return asyncio.run(_run_live(args, token))


if __name__ == "__main__":
    raise SystemExit(main())
