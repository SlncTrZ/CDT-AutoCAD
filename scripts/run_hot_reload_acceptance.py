"""MP-2 acceptance runner — edit→watch→reload loops with injected startup failures.
Wing: code | Topic: mp2-hot-reload | Updated: 2026-09-11 10:05
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import httpx
from fastmcp import Client

from cdt_autocad.contract_identity import (
    CONTRACT_VERSION,
    PROTOCOL_VERSION,
    PUBLIC_TOOL_COUNT,
    contract_hash,
)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _copy_fixture(repo_root: Path, fixture_root: Path) -> None:
    shutil.copytree(repo_root / "src", fixture_root / "src")
    (fixture_root / "docs").mkdir(parents=True)
    (fixture_root / "config").mkdir(parents=True)
    shutil.copy2(repo_root / "docs" / "TOOL_GUIDE.md", fixture_root / "docs" / "TOOL_GUIDE.md")
    shutil.copy2(
        repo_root / "config" / "autocad_command_presets.json",
        fixture_root / "config" / "autocad_command_presets.json",
    )
    shutil.copy2(repo_root / "pyproject.toml", fixture_root / "pyproject.toml")
    lock = "pylock.windows.toml" if sys.platform == "win32" else "pylock.linux.toml"
    shutil.copy2(repo_root / lock, fixture_root / lock)


def _stop_supervisor_process(process: subprocess.Popen[bytes]) -> None:
    """Stop the supervisor and its active worker tree so fixture cleanup is deterministic."""
    if sys.platform == "win32":
        if process.poll() is None:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
                check=False,
            )
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        if sys.platform == "win32":
            process.kill()
        else:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        process.wait(timeout=5)


async def _wait_status(
    client: httpx.AsyncClient,
    url: str,
    token: str,
    predicate,
    *,
    timeout: float,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    last: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        try:
            response = await client.get(
                url + "/__cdt/status",
                headers={"authorization": f"Bearer {token}"},
            )
            if response.status_code == 200:
                last = response.json()
                if predicate(last):
                    return last
        except httpx.HTTPError:
            pass
        await asyncio.sleep(0.05)
    raise TimeoutError(f"supervisor status predicate timed out; last={last}")


async def _mcp_status(url: str, token: str) -> tuple[dict[str, Any], int]:
    async with Client(url + "/mcp", auth=token, timeout=15.0) as client:
        tools = await client.list_tools()
        result = await client.call_tool("system_status", {})
        return result.structured_content or {}, len(tools)


def _assert_public_identity(status: dict[str, Any], tool_count: int) -> None:
    if tool_count != PUBLIC_TOOL_COUNT:
        raise AssertionError(f"expected {PUBLIC_TOOL_COUNT} tools, got {tool_count}")
    if status.get("protocol_version") != PROTOCOL_VERSION:
        raise AssertionError(
            f"protocol mismatch: expected {PROTOCOL_VERSION!r}, got {status.get('protocol_version')!r}"
        )
    if status.get("contract_version") != CONTRACT_VERSION:
        raise AssertionError(
            f"contract mismatch: expected {CONTRACT_VERSION!r}, got {status.get('contract_version')!r}"
        )
    expected_hash = contract_hash()
    if status.get("contract_hash") != expected_hash:
        raise AssertionError(
            f"contract hash mismatch: expected {expected_hash!r}, got {status.get('contract_hash')!r}"
        )


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    repo_root = Path(args.repo_root).resolve()
    output = Path(args.output).resolve()
    token = secrets.token_urlsafe(32)
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="cdt-autocad-mp2-") as temp_dir:
        fixture_root = Path(temp_dir) / "repo"
        _copy_fixture(repo_root, fixture_root)
        public_port = _free_port()
        worker_start = max(20000, public_port + 10)
        worker_end = worker_start + 120
        runtime_dir = Path(temp_dir) / "runtime"
        env = dict(os.environ)
        existing_pythonpath = env.get("PYTHONPATH", "")
        absolute_pythonpath: list[str] = []
        for entry in existing_pythonpath.split(os.pathsep):
            if not entry:
                continue
            candidate = Path(entry)
            if not candidate.is_absolute():
                candidate = (repo_root / candidate).resolve()
            absolute_pythonpath.append(str(candidate))
        env["PYTHONPATH"] = os.pathsep.join(
            [str(fixture_root / "src"), *absolute_pythonpath]
        )
        env.update(
            {
                "CDT_AUTOCAD_AUTH_TOKEN": token,
                "CDT_AUTOCAD_ALLOWED_PATHS": str(fixture_root),
                "CDT_AUTOCAD_BACKEND": args.backend,
                "CDT_AUTOCAD_ALLOW_REMOTE_HTTP": "false",
                "CDT_AUTOCAD_COM_ATTACH_POLICY": "attach_only",
                "CDT_AUTOCAD_COM_PROGID": args.com_progid,
                "CDT_AUTOCAD_REQUIRE_NATIVE_BRIDGE": "1" if args.require_bridge else "0",
            }
        )
        acceptance_document = None
        acceptance_document_created = False
        if args.backend == "com":
            if sys.platform != "win32":
                raise RuntimeError("COM hot-reload acceptance requires Windows")
            import win32com.client

            acad = win32com.client.GetActiveObject(args.com_progid)
            if int(acad.Documents.Count) == 0:
                acceptance_document = acad.Documents.Add()
                acceptance_document_created = True

        command = [
            sys.executable,
            "-m",
            "cdt_autocad.supervisor",
            "--host",
            "127.0.0.1",
            "--port",
            str(public_port),
            "--repo-root",
            str(fixture_root),
            "--runtime-dir",
            str(runtime_dir),
            "--watch-interval",
            "0.05",
            "--drain-timeout",
            "3",
            "--startup-timeout",
            "15",
            "--worker-port-start",
            str(worker_start),
            "--worker-port-end",
            str(worker_end),
            "--log-level",
            "warning",
        ]
        if args.require_bridge:
            command.append("--require-bridge")
        supervisor_log = Path(temp_dir) / "supervisor.log"
        with supervisor_log.open("wb") as log:
            process = subprocess.Popen(
                command,
                cwd=str(fixture_root),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=sys.platform != "win32",
            )
        url = f"http://127.0.0.1:{public_port}"
        http = httpx.AsyncClient(timeout=5.0)
        generations: list[str] = []
        successful_builds: list[str] = []
        failure_generations: list[str] = []
        incidental_success_failures: list[dict[str, Any]] = []
        try:
            try:
                initial = await _wait_status(
                    http,
                    url,
                    token,
                    lambda status: bool(status.get("accepting_requests")),
                    timeout=30.0,
                )
            except Exception as exc:
                if process.poll() is not None:
                    log_tail = supervisor_log.read_text(encoding="utf-8", errors="replace")[-4000:]
                    raise RuntimeError(
                        "supervisor exited before acceptance startup; log tail:\n" + log_tail
                    ) from exc
                raise
            status, tool_count = await _mcp_status(url, token)
            _assert_public_identity(status, tool_count)
            if status.get("runtime_generation") != initial.get("active_generation"):
                raise AssertionError("MCP generation does not match supervisor generation")
            generations.append(str(initial["active_generation"]))
            successful_builds.append(str(initial["active_build_id"]))

            probe_file = fixture_root / "src" / "cdt_autocad" / "_hot_reload_probe.py"
            for cycle in range(1, args.success_cycles + 1):
                promoted = False
                for attempt in range(1, args.success_retries + 2):
                    before = await _wait_status(
                        http,
                        url,
                        token,
                        lambda state: bool(state.get("accepting_requests")),
                        timeout=5.0,
                    )
                    previous_generation = str(before["active_generation"])
                    previous_successes = int(before["reload_successes"])
                    previous_failures = int(before["reload_failures"])
                    probe_file.write_text(
                        f"PROBE = ({cycle}, {attempt})\n",
                        encoding="utf-8",
                    )
                    outcome = await _wait_status(
                        http,
                        url,
                        token,
                        lambda state, previous_successes=previous_successes, previous_failures=previous_failures: (
                            int(state.get("reload_successes") or 0) > previous_successes
                            or int(state.get("reload_failures") or 0) > previous_failures
                        ),
                        timeout=30.0,
                    )
                    if (
                        int(outcome.get("reload_successes") or 0) > previous_successes
                        and outcome.get("active_generation") != previous_generation
                        and bool(outcome.get("accepting_requests"))
                    ):
                        mcp, tools = await _mcp_status(url, token)
                        _assert_public_identity(mcp, tools)
                        if mcp.get("runtime_generation") != outcome.get("active_generation"):
                            raise AssertionError("stable MCP endpoint did not expose promoted generation")
                        generations.append(str(outcome["active_generation"]))
                        successful_builds.append(str(outcome["active_build_id"]))
                        promoted = True
                        break

                    if int(outcome.get("reload_failures") or 0) <= previous_failures:
                        raise AssertionError("success-cycle reload changed state without success/failure accounting")
                    if outcome.get("active_generation") != previous_generation:
                        raise AssertionError("failed success-cycle candidate replaced the healthy generation")
                    mcp, tools = await _mcp_status(url, token)
                    _assert_public_identity(mcp, tools)
                    if mcp.get("runtime_generation") != previous_generation:
                        raise AssertionError("failed success-cycle candidate disrupted the stable MCP generation")
                    incidental_success_failures.append(
                        {
                            "cycle": cycle,
                            "attempt": attempt,
                            "preserved_generation": previous_generation,
                            "last_error_code": outcome.get("last_error_code"),
                        }
                    )
                    if attempt > args.success_retries:
                        raise AssertionError(
                            f"success cycle {cycle} exceeded retry budget {args.success_retries}"
                        )
                if not promoted:
                    raise AssertionError(f"success cycle {cycle} did not promote a generation")

            server_file = fixture_root / "src" / "cdt_autocad" / "server.py"
            valid_server = server_file.read_text(encoding="utf-8")
            for cycle in range(1, args.failure_cycles + 1):
                before = await _wait_status(
                    http,
                    url,
                    token,
                    lambda state: bool(state.get("accepting_requests")),
                    timeout=5.0,
                )
                previous_generation = str(before["active_generation"])
                previous_failures = int(before["reload_failures"])
                server_file.write_text(
                    f"this is invalid syntax for injected failure {cycle} !!!\n" + valid_server,
                    encoding="utf-8",
                )
                failed = await _wait_status(
                    http,
                    url,
                    token,
                    lambda state, previous_failures=previous_failures: int(state.get("reload_failures") or 0) > previous_failures,
                    timeout=30.0,
                )
                if failed.get("active_generation") != previous_generation:
                    raise AssertionError("failed reload replaced the last healthy generation")
                mcp, tools = await _mcp_status(url, token)
                _assert_public_identity(mcp, tools)
                if mcp.get("runtime_generation") != previous_generation:
                    raise AssertionError("last healthy generation did not remain serviceable")
                failure_generations.append(previous_generation)

                prior_successes = int(failed.get("reload_successes") or 0)
                server_file.write_text(valid_server, encoding="utf-8")
                recovered = await _wait_status(
                    http,
                    url,
                    token,
                    lambda state, prior_successes=prior_successes, previous_generation=previous_generation: (
                        int(state.get("reload_successes") or 0) > prior_successes
                        and state.get("active_generation") != previous_generation
                        and bool(state.get("accepting_requests"))
                    ),
                    timeout=30.0,
                )
                mcp, tools = await _mcp_status(url, token)
                _assert_public_identity(mcp, tools)
                if mcp.get("runtime_generation") != recovered.get("active_generation"):
                    raise AssertionError("restored source did not promote a healthy generation")
                generations.append(str(recovered["active_generation"]))
                successful_builds.append(str(recovered["active_build_id"]))

            final = await _wait_status(
                http,
                url,
                token,
                lambda state: bool(state.get("accepting_requests")),
                timeout=5.0,
            )
            mcp_final, final_tool_count = await _mcp_status(url, token)
            _assert_public_identity(mcp_final, final_tool_count)
            summary = {
                "schema_version": 1,
                "status": "PASS",
                "backend": args.backend,
                "require_bridge": bool(args.require_bridge),
                "success_cycles_requested": args.success_cycles,
                "failure_cycles_requested": args.failure_cycles,
                "reload_successes": int(final["reload_successes"]),
                "reload_failures": int(final["reload_failures"]),
                "active_generation": final["active_generation"],
                "active_build_id": final["active_build_id"],
                "active_process_alive": final["active_process_alive"],
                "mcp_runtime_generation": mcp_final.get("runtime_generation"),
                "mcp_provider_build": (mcp_final.get("provider_build") or {}).get("id"),
                "mcp_tool_count": final_tool_count,
                "mcp_protocol_version": mcp_final.get("protocol_version"),
                "mcp_contract_version": mcp_final.get("contract_version"),
                "mcp_contract_hash": mcp_final.get("contract_hash"),
                "pending_recovery_count": mcp_final.get("pending_recovery_count"),
                "native_bridge": mcp_final.get("native_bridge"),
                "generations_seen": generations,
                "successful_builds_seen": successful_builds,
                "failure_preserved_generations": failure_generations,
                "incidental_success_failures": incidental_success_failures,
                "success_retry_budget": args.success_retries,
                "duration_seconds": round(time.perf_counter() - started, 3),
                "public_url_stable": True,
                "auth_token_redacted": True,
                "autocad_bootstrap_document_created": acceptance_document_created,
            }
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            return summary
        finally:
            await http.aclose()
            _stop_supervisor_process(process)
            if acceptance_document is not None:
                try:
                    acceptance_document.Close(False)
                except Exception:
                    pass
            if not output.exists() and supervisor_log.exists():
                preserved_log = output.with_suffix(output.suffix + ".supervisor.log")
                preserved_log.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(supervisor_log, preserved_log)
            if process.returncode not in {0, -15, 1} and not output.exists():
                raise RuntimeError(
                    "supervisor acceptance process failed; "
                    + supervisor_log.read_text(encoding="utf-8", errors="replace")[-4000:]
                )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run MP-2 hot reload acceptance loops")
    parser.add_argument("--repo-root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--output", required=True)
    parser.add_argument("--success-cycles", type=int, default=20)
    parser.add_argument("--failure-cycles", type=int, default=10)
    parser.add_argument("--success-retries", type=int, default=3)
    parser.add_argument("--backend", choices=("ezdxf", "com"), default="ezdxf")
    parser.add_argument("--require-bridge", action="store_true")
    parser.add_argument("--com-progid", default="AutoCAD.Application.26")
    args = parser.parse_args()
    if args.success_cycles < 0 or args.failure_cycles < 0 or args.success_retries < 0:
        raise SystemExit("cycle counts and success retry budget must be non-negative")
    summary = asyncio.run(_run(args))
    print(json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    main()
