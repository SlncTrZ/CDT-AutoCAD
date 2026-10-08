"""Workstation-side runtime agent — minimal R2 executor on .171.
Wing: code | Topic: migration-r2-agent | Updated: 2026-10-07 15:35

Authenticated persistent listener that owns the native execution side of the
RuntimeTransport boundary. Scope is deliberately minimal (migration Phase B):

- bearer-authenticated HTTP listener (loopback by default) + heartbeat;
- runtime generation minted at agent start (restart creates a new one);
- process/session discovery (pid, Windows session, acad.exe sessions, best-effort);
- approved application/bridge attachment = the injected R1 adapter that reuses
  the current ComBackend/native bridge implementation;
- bounded adapter dispatch by op name from the shared allowlist.

Explicitly NOT in this agent: MCP server, engineering semantics, PID/
fingerprint logic (all stays inside the reused adapter), and — critically —
NO MutationCoordinator. Writer authority stays provider-side (single shared
instance, same as R1); the agent only serializes dispatch with a plain lock
so transport interleaving cannot overlap two native calls. Quarantine and
uncertainty decisions flow back to the provider coordinator over the wire.
A duplicate-prevention test asserts this module never imports MutationCoordinator.
"""

from __future__ import annotations

import asyncio
import base64
import ctypes
import json
import os
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

from .runtime_transport import (
    ALLOWED_OPS,
    MAX_REQUEST_BYTES,
    MAX_RESPONSE_BYTES,
    bearer_matches,
    check_deadline,
)

_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}

_HEALTH_OPS = frozenset({"capabilities", "status", "runtime_status", "health"})


def _to_jsonable(value: Any) -> Any:
    """Convert adapter results to JSON-safe payloads without interpreting them."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, bytes):
        return {"__bytes_b64": base64.b64encode(value).decode("ascii")}
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return _to_jsonable(to_dict())
    if isinstance(value, (tuple, list)):
        return [_to_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _to_jsonable(item) for key, item in value.items()}
    return str(value)


def _current_process_session() -> dict[str, Any]:
    identity: dict[str, Any] = {"pid": os.getpid()}
    if os.name != "nt":
        return {**identity, "windows_session": None, "note": "non-windows"}
    try:
        session_id = ctypes.c_uint32()
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        if kernel32.ProcessIdToSessionId(os.getpid(), ctypes.byref(session_id)):
            identity["windows_session"] = int(session_id.value)
        else:
            identity["windows_session"] = None
    except Exception:
        identity["windows_session"] = None
    try:
        import win32ts  # type: ignore[import-not-found]

        sessions: set[int] = set()
        for session_id, _pid, process_name, _sid in win32ts.WTSEnumerateProcesses(None, 1, 0):
            if str(process_name).lower() == "acad.exe":
                sessions.add(int(session_id))
        identity["autocad_sessions"] = sorted(sessions)
    except Exception:
        identity["autocad_sessions"] = None
    return identity


def _adapter_health_summary(adapter: Any) -> dict[str, Any]:
    try:
        health = adapter.health()
        return health if isinstance(health, dict) else {"detail": str(health)}
    except Exception as exc:
        return {"available": False, "error": str(exc)}


@dataclass
class WorkstationAgentConfig:
    host: str = "127.0.0.1"
    port: int = 0  # 0 = ephemeral; actual port read back after start
    auth_token: str = ""
    allow_remote_bind: bool = False
    max_request_bytes: int = MAX_REQUEST_BYTES

    def __post_init__(self) -> None:
        if not str(self.auth_token or "").strip():
            raise ValueError("WorkstationRuntimeAgent requires a non-empty auth_token")
        if self.host.lower() not in _LOOPBACK_HOSTS and not self.allow_remote_bind:
            raise ValueError(
                f"refusing non-loopback agent bind {self.host!r} without allow_remote_bind"
            )
        if not 0 <= self.port <= 65535:
            raise ValueError("port must be within [0, 65535]")
        if self.max_request_bytes <= 0:
            raise ValueError("max_request_bytes must be > 0")


class WorkstationRuntimeAgent:
    """Owns one R1 adapter + one runtime generation behind an authed listener."""

    def __init__(self, adapter: Any, config: WorkstationAgentConfig) -> None:
        from .backends.runtime_port import AutoCADRuntimePort

        if not isinstance(adapter, AutoCADRuntimePort):
            raise TypeError("WorkstationRuntimeAgent requires an AutoCADRuntimePort adapter")
        self._adapter = adapter
        self._config = config
        self._generation = f"gen-{uuid4().hex}"
        self._started_at = time.time()
        self._dispatch_lock = threading.Lock()  # serializes native dispatch only
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._session = _current_process_session()

    @property
    def generation(self) -> str:
        return self._generation

    @property
    def adapter(self) -> Any:
        return self._adapter

    @property
    def base_url(self) -> str:
        if self._server is None:
            raise RuntimeError("agent is not started")
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    # -- runtime surface (no engineering semantics) --

    def heartbeat(self) -> dict[str, Any]:
        return {
            "generation": self._generation,
            "uptime_s": round(time.time() - self._started_at, 3),
            "session": dict(self._session),
            "adapter": _adapter_health_summary(self._adapter),
        }

    def dispatch(
        self,
        op: str,
        args: list[Any] | None = None,
        kwargs: dict[str, Any] | None = None,
        *,
        expected_generation: str | None = None,
        deadline_ms: int | None = None,
    ) -> dict[str, Any]:
        """Run one allowlisted adapter op and wrap it in the wire envelope."""
        name = str(op or "").strip()
        if name not in ALLOWED_OPS:
            return self._envelope(
                False, None, "unknown_op", f"op refused (not in allowlist): {name!r}", False
            )
        if expected_generation is not None and expected_generation != self._generation:
            # Wrong generation refuses BEFORE touching CAD.
            return self._envelope(
                False,
                None,
                "generation_mismatch",
                f"stale runtime generation: expected {expected_generation!r}, "
                f"agent generation is {self._generation!r}",
                False,
            )
        try:
            bound_ms = check_deadline(deadline_ms)
        except ValueError as exc:
            return self._envelope(False, None, "bad_request", str(exc), False)
        target = getattr(self._adapter, name, None)
        if not callable(target):
            return self._envelope(
                False, None, "unknown_op", f"op not implemented by adapter: {name!r}", False
            )
        # Non-authoritative serialization: prevents transport overlap only.
        # Writer authority + quarantine stay provider-side.
        with self._dispatch_lock:
            try:
                result = self._run_bounded(target, tuple(args or ()), dict(kwargs or {}), bound_ms)
            except TimeoutError as exc:
                # Deadline fired after dispatch started: completion unknown.
                return self._envelope(
                    False, None, "dispatch_timeout_uncertain", str(exc), True
                )
            except Exception as exc:
                uncertain = bool(getattr(exc, "completion_unknown", False))
                code = "uncertain" if uncertain else "backend_error"
                return self._envelope(False, None, code, str(exc), uncertain)
        try:
            return self._envelope(True, _to_jsonable(result), "ok", "", False)
        except Exception as exc:
            return self._envelope(
                False, None, "backend_error", f"result not serializable: {exc}", False
            )

    def _envelope(
        self,
        ok: bool,
        result: Any,
        code: str,
        message: str,
        completion_unknown: bool,
    ) -> dict[str, Any]:
        return {
            "ok": ok,
            "result": result,
            "error_code": code,
            "error_message": message,
            "generation": self._generation,
            "completion_unknown": completion_unknown,
        }

    @staticmethod
    def _run_bounded(target: Any, args: tuple[Any, ...], kwargs: dict[str, Any], bound_ms: int) -> Any:
        """Run a (possibly async) adapter callable with a hard deadline."""
        outcome: dict[str, Any] = {}

        def _invoke() -> None:
            try:
                res = target(*args, **kwargs)
                if asyncio.iscoroutine(res):
                    res = asyncio.run(res)
                outcome["result"] = res
            except BaseException as exc:  # noqa: BLE001 — transported, not interpreted
                outcome["error"] = exc

        worker = threading.Thread(target=_invoke, daemon=True)
        worker.start()
        worker.join(timeout=bound_ms / 1000.0)
        if worker.is_alive():
            raise TimeoutError(
                f"adapter op exceeded {bound_ms}ms after dispatch; "
                "completion is unknown, blind retry is forbidden"
            )
        if "error" in outcome:
            raise outcome["error"]
        return outcome.get("result")

    # -- listener lifecycle --

    def start(self) -> str:
        if self._server is not None:
            raise RuntimeError("agent is already started")
        agent = self

        class _Handler(BaseHTTPRequestHandler):
            server_version = "CDT-WorkstationAgent/0.1"

            def _authed(self) -> bool:
                presented = self.headers.get("Authorization", "")
                scheme, _, token = presented.partition(" ")
                if scheme.lower() != "bearer":
                    return False
                return bearer_matches(token.strip(), agent._config.auth_token)

            def _send(self, status: int, payload: dict[str, Any]) -> None:
                raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
                if len(raw) > MAX_RESPONSE_BYTES:
                    raw = json.dumps(
                        agent._envelope(False, None, "oversized", "response oversized", False),
                        separators=(",", ":"),
                    ).encode("utf-8")
                    status = 500
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                try:
                    self.wfile.write(raw)
                except OSError:
                    pass  # client went away (e.g. transport deadline fired first);
                    # the uncertainty is already fail-closed on the provider side.

            def _refuse_auth(self) -> None:
                self._send(
                    401,
                    agent._envelope(False, None, "unauthorized", "invalid bearer token", False),
                )

            def do_GET(self) -> None:  # noqa: N802 — stdlib handler naming
                if not self._authed():
                    self._refuse_auth()
                    return
                path = urlparse(self.path).path.rstrip("/") or "/"
                if path == "/health":
                    self._send(200, agent._envelope(True, agent.heartbeat(), "ok", "", False))
                elif path == "/heartbeat":
                    self._send(200, agent._envelope(True, agent.heartbeat(), "ok", "", False))
                elif path == "/status":
                    self._send(
                        200,
                        agent._envelope(
                            True,
                            {
                                "heartbeat": agent.heartbeat(),
                                "adapter_status": _to_jsonable(
                                    agent._adapter.runtime_status()
                                    if hasattr(agent._adapter, "runtime_status")
                                    else None
                                ),
                            },
                            "ok",
                            "",
                            False,
                        ),
                    )
                else:
                    self._send(
                        404, agent._envelope(False, None, "not_found", f"no route: {path}", False)
                    )

            def do_POST(self) -> None:  # noqa: N802 — stdlib handler naming
                if not self._authed():
                    self._refuse_auth()
                    return
                path = urlparse(self.path).path.rstrip("/") or "/"
                if path != "/dispatch":
                    self._send(
                        404, agent._envelope(False, None, "not_found", f"no route: {path}", False)
                    )
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    length = 0
                if length <= 0 or length > agent._config.max_request_bytes:
                    self._send(
                        400,
                        agent._envelope(
                            False, None, "oversized", "request body missing or oversized", False
                        ),
                    )
                    return
                try:
                    body = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
                except (ValueError, UnicodeDecodeError):
                    self._send(
                        400,
                        agent._envelope(False, None, "bad_request", "malformed JSON body", False),
                    )
                    return
                if not isinstance(body, dict):
                    self._send(
                        400,
                        agent._envelope(False, None, "bad_request", "body must be an object", False),
                    )
                    return
                envelope = agent.dispatch(
                    body.get("op", ""),
                    body.get("args"),
                    body.get("kwargs"),
                    expected_generation=body.get("expected_generation"),
                    deadline_ms=body.get("deadline_ms"),
                )
                if envelope.get("error_code") == "unknown_op":
                    self._send(400, envelope)
                else:
                    self._send(200, envelope)

            def log_message(self, *args: Any) -> None:
                pass  # quiet by design; heartbeat/discovery carry observability

        server = ThreadingHTTPServer((self._config.host, self._config.port), _Handler)
        server.daemon_threads = True
        self._server = server
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05})
        thread.daemon = True
        thread.start()
        self._thread = thread
        return self.base_url

    def stop(self) -> None:
        server, thread = self._server, self._thread
        self._server = None
        self._thread = None
        if server is not None:
            server.shutdown()
            server.server_close()
        if thread is not None:
            thread.join(timeout=5.0)
