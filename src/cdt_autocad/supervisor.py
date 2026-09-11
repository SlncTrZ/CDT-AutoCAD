"""Hot reload supervisor — stable authenticated endpoint over replaceable FastMCP workers.
Wing: code | Topic: mp2-hot-reload | Updated: 2026-09-11 09:35
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import hmac
import json
import os
import socket
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
import uvicorn
from fastmcp import Client

from .contract_identity import CONTRACT_VERSION, PROTOCOL_VERSION, contract_hash
from .hot_reload import (
    ReloadSupervisor,
    ReloadUnavailableError,
    WorkerGeneration,
    WorkerHealth,
    WorkerSpec,
)
from .runtime_identity import policy_fingerprint

_EXPECTED_TOOL_COUNT = 50
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
_HOP_BY_HOP_HEADERS = {
    b"connection",
    b"keep-alive",
    b"proxy-authenticate",
    b"proxy-authorization",
    b"te",
    b"trailers",
    b"transfer-encoding",
    b"upgrade",
}
_WORKER_EXCLUDED_NAMES = {"hot_reload.py", "supervisor.py"}


@dataclass
class _ProcessState:
    process: subprocess.Popen[bytes]
    stdout_handle: Any
    stderr_handle: Any


class SubprocessWorkerLauncher:
    """Spawn one stateless localhost FastMCP worker per generation."""

    def __init__(
        self,
        *,
        repo_root: Path,
        log_dir: Path,
        worker_python: str | None = None,
        port_start: int = 18100,
        port_end: int = 18299,
        stop_timeout_seconds: float = 5.0,
    ):
        if port_start <= 0 or port_end < port_start:
            raise ValueError("invalid worker port range")
        self.repo_root = repo_root.resolve()
        self.log_dir = log_dir.resolve()
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.worker_python = worker_python or sys.executable
        self.port_start = port_start
        self.port_end = port_end
        self.stop_timeout_seconds = stop_timeout_seconds
        self._next_port = port_start

    async def start(self, spec: WorkerSpec, env: dict[str, str]) -> WorkerGeneration:
        port = self._allocate_port()
        stdout_path = self.log_dir / f"worker-{spec.generation_id}.stdout.log"
        stderr_path = self.log_dir / f"worker-{spec.generation_id}.stderr.log"
        stdout_handle = stdout_path.open("ab", buffering=0)
        stderr_handle = stderr_path.open("ab", buffering=0)
        try:
            process = subprocess.Popen(
                [
                    self.worker_python,
                    "-m",
                    "cdt_autocad.worker",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                ],
                cwd=str(self.repo_root),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=stdout_handle,
                stderr=stderr_handle,
            )
        except Exception:
            stdout_handle.close()
            stderr_handle.close()
            raise
        return WorkerGeneration(
            generation=spec.generation,
            generation_id=spec.generation_id,
            build_id=spec.build_id,
            base_url=f"http://127.0.0.1:{port}",
            process_id=process.pid,
            opaque=_ProcessState(process, stdout_handle, stderr_handle),
        )

    async def stop(self, worker: WorkerGeneration) -> None:
        state = worker.opaque
        if not isinstance(state, _ProcessState):
            return
        process = state.process
        try:
            if process.poll() is None:
                process.terminate()
                try:
                    await asyncio.to_thread(process.wait, self.stop_timeout_seconds)
                except subprocess.TimeoutExpired:
                    process.kill()
                    await asyncio.to_thread(process.wait, self.stop_timeout_seconds)
        finally:
            state.stdout_handle.close()
            state.stderr_handle.close()

    def is_alive(self, worker: WorkerGeneration) -> bool:
        state = worker.opaque
        return isinstance(state, _ProcessState) and state.process.poll() is None

    def _allocate_port(self) -> int:
        attempts = self.port_end - self.port_start + 1
        for _ in range(attempts):
            port = self._next_port
            self._next_port += 1
            if self._next_port > self.port_end:
                self._next_port = self.port_start
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                try:
                    probe.bind(("127.0.0.1", port))
                except OSError:
                    continue
            return port
        raise RuntimeError("no free localhost worker port in configured range")


class McpWorkerProbe:
    """Health-check a worker through its real MCP contract, then require safe activation state."""

    def __init__(
        self,
        *,
        auth_token: str,
        startup_timeout_seconds: float = 15.0,
        retry_interval_seconds: float = 0.1,
        attempt_timeout_seconds: float = 2.0,
    ):
        if startup_timeout_seconds <= 0:
            raise ValueError("startup_timeout_seconds must be > 0")
        if retry_interval_seconds <= 0:
            raise ValueError("retry_interval_seconds must be > 0")
        if attempt_timeout_seconds <= 0:
            raise ValueError("attempt_timeout_seconds must be > 0")
        self.auth_token = auth_token
        self.startup_timeout_seconds = startup_timeout_seconds
        self.retry_interval_seconds = retry_interval_seconds
        self.attempt_timeout_seconds = attempt_timeout_seconds

    async def startup(self, worker: WorkerGeneration) -> WorkerHealth:
        deadline = time.monotonic() + self.startup_timeout_seconds
        last_error: Exception | None = None
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            state = worker.opaque
            if isinstance(state, _ProcessState) and state.process.poll() is not None:
                raise RuntimeError("worker process exited before startup health")
            attempt_timeout = min(self.attempt_timeout_seconds, remaining)
            try:
                return await asyncio.wait_for(
                    self._snapshot(
                        worker,
                        activate_runtime=False,
                        request_timeout_seconds=attempt_timeout,
                    ),
                    timeout=attempt_timeout,
                )
            except Exception as exc:
                last_error = exc
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                await asyncio.sleep(min(self.retry_interval_seconds, remaining))
        raise RuntimeError("worker startup health probe timed out") from last_error

    async def activation(self, worker: WorkerGeneration) -> WorkerHealth:
        return await self._snapshot(
            worker,
            activate_runtime=True,
            request_timeout_seconds=self.startup_timeout_seconds,
        )

    async def _snapshot(
        self,
        worker: WorkerGeneration,
        *,
        activate_runtime: bool,
        request_timeout_seconds: float,
    ) -> WorkerHealth:
        url = worker.base_url.rstrip("/") + "/mcp"
        async with Client(
            url,
            auth=self.auth_token or None,
            timeout=request_timeout_seconds,
        ) as client:
            tools = await client.list_tools()
            status_result = await client.call_tool("system_status", {})
            status = status_result.structured_content or {}
            if activate_runtime and status.get("backend") == "com":
                info = await client.call_tool("document_info", {}, raise_on_error=False)
                if info.is_error:
                    raise RuntimeError("COM runtime activation/readiness probe failed")
                status_result = await client.call_tool("system_status", {})
                status = status_result.structured_content or {}

        runtime = status.get("runtime") or {}
        provider_build = status.get("provider_build") or {}
        bridge = status.get("native_bridge") or {}
        return WorkerHealth(
            generation_id=str(status.get("runtime_generation") or ""),
            build_id=str(provider_build.get("id") or ""),
            protocol_version=str(status.get("protocol_version") or ""),
            contract_version=str(status.get("contract_version") or ""),
            contract_hash=str(status.get("contract_hash") or ""),
            tool_count=len(tools),
            startup_ready=bool(status.get("provider") == "autocad" and len(tools) > 0),
            runtime_ready=bool(runtime.get("ready")),
            transaction_depth=int(status.get("transaction_depth") or 0),
            timeout_uncertain=bool(status.get("timeout_uncertain")),
            pending_recovery_count=int(status.get("pending_recovery_count") or 0),
            bridge_ready=bool(bridge.get("ready")),
            policy_fingerprint=str(status.get("runtime_policy_fingerprint") or ""),
        )


class SupervisorProxyApp:
    """Minimal ASGI proxy that authenticates before routing and leases one generation per request."""

    def __init__(
        self,
        supervisor: ReloadSupervisor,
        *,
        auth_token: str,
        upstream_client: httpx.AsyncClient | None = None,
    ):
        self.supervisor = supervisor
        self.auth_token = auth_token
        self._client = upstream_client or httpx.AsyncClient(timeout=None)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] == "lifespan":
            await self._handle_lifespan(receive, send)
            return
        if scope["type"] != "http":
            return
        if not self._authorized(scope.get("headers", [])):
            await self._json(send, 401, {"error": "unauthorized"})
            return
        if scope.get("path") == "/__cdt/status":
            await self._json(send, 200, self.supervisor.status())
            return

        try:
            async with self.supervisor.lease() as worker:
                body = await self._read_body(receive)
                await self._proxy(scope, send, worker, body)
        except ReloadUnavailableError:
            await self._json(
                send,
                503,
                {"error": "reload_in_progress"},
                headers=[(b"retry-after", b"1")],
            )
        except httpx.HTTPError:
            await self._json(send, 502, {"error": "active_worker_unavailable"})

    def _authorized(self, headers: list[tuple[bytes, bytes]]) -> bool:
        if not self.auth_token:
            return True
        supplied = ""
        for key, value in headers:
            if key.lower() == b"authorization":
                supplied = value.decode("latin-1")
                break
        return hmac.compare_digest(supplied, f"Bearer {self.auth_token}")

    async def _proxy(
        self,
        scope,
        send,
        worker: WorkerGeneration,
        body: bytes,
    ) -> None:
        path = quote(str(scope.get("path") or "/"), safe="/%:@")
        query = bytes(scope.get("query_string") or b"").decode("latin-1")
        target = worker.base_url.rstrip("/") + path + (f"?{query}" if query else "")
        headers = [
            (key.decode("latin-1"), value.decode("latin-1"))
            for key, value in scope.get("headers", [])
            if key.lower() not in _HOP_BY_HOP_HEADERS | {b"host", b"content-length"}
        ]
        async with self._client.stream(
            str(scope.get("method") or "GET"),
            target,
            headers=headers,
            content=body,
        ) as response:
            response_headers = [
                (key.encode("latin-1"), value.encode("latin-1"))
                for key, value in response.headers.multi_items()
                if key.lower().encode("latin-1") not in _HOP_BY_HOP_HEADERS
            ]
            await send(
                {
                    "type": "http.response.start",
                    "status": response.status_code,
                    "headers": response_headers,
                }
            )
            if response.is_stream_consumed:
                await send(
                    {
                        "type": "http.response.body",
                        "body": response.content,
                        "more_body": False,
                    }
                )
            else:
                async for chunk in response.aiter_raw():
                    await send({"type": "http.response.body", "body": chunk, "more_body": True})
                await send({"type": "http.response.body", "body": b"", "more_body": False})

    @staticmethod
    async def _read_body(receive) -> bytes:
        chunks: list[bytes] = []
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                break
            if message["type"] != "http.request":
                continue
            chunks.append(bytes(message.get("body") or b""))
            if not message.get("more_body", False):
                break
        return b"".join(chunks)

    @staticmethod
    async def _json(send, status: int, payload: dict[str, Any], headers=None) -> None:
        body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        response_headers = [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode("ascii")),
            *(headers or []),
        ]
        await send({"type": "http.response.start", "status": status, "headers": response_headers})
        await send({"type": "http.response.body", "body": body, "more_body": False})

    async def _handle_lifespan(self, receive, send) -> None:
        while True:
            message = await receive()
            if message["type"] == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            elif message["type"] == "lifespan.shutdown":
                await self.aclose()
                await send({"type": "lifespan.shutdown.complete"})
                return


class SourceWatcher:
    """Poll source identity and request one reload for each observed build change."""

    def __init__(self, repo_root: Path, *, poll_seconds: float = 0.5):
        if poll_seconds <= 0:
            raise ValueError("poll_seconds must be > 0")
        self.repo_root = repo_root.resolve()
        self.poll_seconds = poll_seconds

    async def run(self, supervisor: ReloadSupervisor, stop_event: asyncio.Event) -> None:
        observed = source_build_id(self.repo_root)
        while not stop_event.is_set():
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=self.poll_seconds)
                break
            except asyncio.TimeoutError:
                pass
            current = source_build_id(self.repo_root)
            if current == observed:
                continue
            observed = current
            await supervisor.reload(current)


def source_build_id(repo_root: Path) -> str:
    root = repo_root.resolve()
    candidates: list[Path] = []
    package_root = root / "src" / "cdt_autocad"
    if package_root.is_dir():
        candidates.extend(
            path
            for path in package_root.rglob("*.py")
            if "__pycache__" not in path.parts and path.name not in _WORKER_EXCLUDED_NAMES
        )
    for relative in (
        "docs/TOOL_GUIDE.md",
        "pyproject.toml",
        "pylock.windows.toml" if sys.platform == "win32" else "pylock.linux.toml",
    ):
        path = root / relative
        if path.is_file():
            candidates.append(path)
    digest = hashlib.sha256()
    for path in sorted(set(candidates), key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(relative)
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return "sha256:" + digest.hexdigest()


def _validate_launch(host: str, auth_token: str, allow_remote_http: bool) -> None:
    if not auth_token:
        raise SystemExit("Refusing supervised HTTP transport without CDT_AUTOCAD_AUTH_TOKEN")
    if host not in _LOOPBACK_HOSTS and not allow_remote_http:
        raise SystemExit(
            "Refusing non-loopback supervisor bind; set CDT_AUTOCAD_ALLOW_REMOTE_HTTP=true explicitly"
        )


async def _serve(args: argparse.Namespace) -> None:
    repo_root = Path(args.repo_root).resolve()
    frozen_env = dict(os.environ)
    auth_token = frozen_env.get("CDT_AUTOCAD_AUTH_TOKEN", "").strip()
    allow_remote = frozen_env.get("CDT_AUTOCAD_ALLOW_REMOTE_HTTP", "").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    _validate_launch(args.host, auth_token, allow_remote)
    policy_fp = policy_fingerprint(frozen_env)
    launcher = SubprocessWorkerLauncher(
        repo_root=repo_root,
        log_dir=Path(args.runtime_dir) / "workers",
        port_start=args.worker_port_start,
        port_end=args.worker_port_end,
    )
    probe = McpWorkerProbe(auth_token=auth_token, startup_timeout_seconds=args.startup_timeout)
    supervisor = ReloadSupervisor(
        launcher=launcher,
        probe=probe,
        frozen_env=frozen_env,
        policy_fingerprint=policy_fp,
        expected_protocol_version=PROTOCOL_VERSION,
        expected_contract_version=CONTRACT_VERSION,
        expected_contract_hash=contract_hash(),
        expected_tool_count=_EXPECTED_TOOL_COUNT,
        require_bridge=args.require_bridge,
        drain_timeout_seconds=args.drain_timeout,
    )
    await supervisor.bootstrap(source_build_id(repo_root))
    proxy = SupervisorProxyApp(supervisor, auth_token=auth_token)
    stop_event = asyncio.Event()
    watcher = SourceWatcher(repo_root, poll_seconds=args.watch_interval)
    watcher_task = asyncio.create_task(watcher.run(supervisor, stop_event))
    config = uvicorn.Config(
        proxy,
        host=args.host,
        port=args.port,
        log_level=args.log_level,
        access_log=True,
    )
    server = uvicorn.Server(config)
    try:
        await server.serve()
    finally:
        stop_event.set()
        watcher_task.cancel()
        await asyncio.gather(watcher_task, return_exceptions=True)
        try:
            await supervisor.shutdown()
        finally:
            await proxy.aclose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run CDT AutoCAD fail-safe hot reload supervisor")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--repo-root", default=str(Path.cwd()))
    parser.add_argument("--runtime-dir", default=str(Path.cwd() / ".runtime" / "hot-reload"))
    parser.add_argument("--watch-interval", type=float, default=0.5)
    parser.add_argument("--drain-timeout", type=float, default=10.0)
    parser.add_argument("--startup-timeout", type=float, default=15.0)
    parser.add_argument("--worker-port-start", type=int, default=18100)
    parser.add_argument("--worker-port-end", type=int, default=18299)
    parser.add_argument("--require-bridge", action="store_true")
    parser.add_argument("--log-level", default="info")
    args = parser.parse_args()
    asyncio.run(_serve(args))


if __name__ == "__main__":
    main()
