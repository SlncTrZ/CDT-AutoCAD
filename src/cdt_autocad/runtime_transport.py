"""RuntimeTransport abstraction — provider/execution boundary for migration R2.
Wing: code | Topic: migration-r2-transport | Updated: 2026-10-07 15:30

Typed bounded request/response between the MCP provider process and the
workstation-side runtime agent. Two implementations share one contract:

- ``LocalRuntimeTransport`` — in-process delegate over an AutoCADRuntimePort
  (R1 adapter). Default single-host path; behavior unchanged.
- ``RemoteRuntimeTransport`` — loopback HTTP client to a WorkstationRuntimeAgent
  (see workstation_agent.py). Proves the split-process path on one host;
  split-host deploy comes later.

This module owns NO CAD semantics: the only operation vocabulary is the
``ALLOWED_OPS`` allowlist of AutoCADBackend/Port method names. Payloads are
opaque JSON primitives dispatched by name. PID/fingerprint/recovery semantics
stay inside the reused native implementation behind the R1 Port.

Fencing note (R2): the single mutation-writer authority stays provider-side
(one shared MutationCoordinator owned by the provider process, same instance
as R1). The workstation agent holds NO MutationCoordinator — only a
non-authoritative serial dispatch lock that prevents transport interleaving.
Quarantine/uncertainty decisions flow back to the provider coordinator.
See RemoteAutoCADRuntimeAdapter and the duplicate-prevention test.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import socket
import threading
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from .errors import (
    AutoCADProviderError,
    BackendTimeoutError,
    MutationCompletionUncertainError,
    StateConflictError,
)

_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}

# Single source of truth for the dispatch vocabulary. Names only — no arg
# shapes, no validation, no CAD meaning. Imported by the agent (refuse
# unknown before CAD), the transports and the remote adapter.
ALLOWED_OPS: frozenset[str] = frozenset(
    {
        # port identity (read-only)
        "capabilities",
        "status",
        "runtime_status",
        "health",
        # documents / drawings
        "document_new",
        "document_open",
        "document_info",
        "document_save",
        "document_save_as",
        "document_export_pdf",
        "drawing_audit",
        "drawing_purge",
        "document_configure_units",
        "document_dependencies",
        "artifact_seal",
        # objects
        "object_list",
        "object_get",
        "object_count",
        "object_set_properties",
        "object_delete",
        "object_move",
        "object_copy",
        "object_rotate",
        "object_scale",
        "object_measure",
        "drawing_extents",
        "object_intersections",
        # creation / dimensions / hatch
        "entity_create_line",
        "entity_create_circle",
        "entity_create_arc",
        "entity_create_polyline",
        "entity_create_text",
        "hatch_create",
        "dimension_linear",
        "dimension_aligned",
        "dimension_angular",
        "dimension_radial",
        "dimension_diametric",
        "dimension_ordinate",
        # layers / blocks / xrefs / layouts
        "layer_list",
        "layer_create",
        "layer_set_current",
        "layer_update_state",
        "block_list",
        "block_create",
        "block_insert",
        "xref_list",
        "xref_attach",
        "xref_reload",
        "xref_unload",
        "xref_detach",
        "layout_list",
        "layout_create",
        "layout_set_current",
        # views / viewports
        "viewport_create",
        "viewport_list",
        "viewport_set_scale",
        "viewport_lock",
        "viewport_delete",
        "view_zoom_extents",
        "view_zoom_window",
        "view_screenshot",
        "view_set_direction",
        "view_set_preset",
        "view_set_visual_style",
        # 3D / solids
        "entity_create_3d_polyline",
        "solid_box",
        "solid_cylinder",
        "solid_sphere",
        "solid_cone",
        "solid_torus",
        "solid_wedge",
        "solid_extrude",
        "solid_sweep",
        "solid_revolve",
        "solid_boolean",
        "solid_move",
        "solid_rotate3d",
        "solid_scale3d",
        "solid_mirror3d",
        "solid_inspect",
        "solid_export",
        # transactions
        "transaction_begin",
        "transaction_commit",
        "transaction_rollback",
        "undo",
        "redo",
        # native strong-integrity surface (R3 reads / R4 mutations): the same
        # NativePublicFacade methods the provider calls directly, exposed by
        # name only. PID/fingerprint/recovery semantics stay inside the
        # reused facade implementation on the workstation agent host.
        "native_status",
        "native_document_state",
        "native_snapshot_page",
        "native_metadata_get",
        "native_metadata_query",
        "native_feature_execute",
        "native_batch_create_entities",
        "native_batch_transform_entities",
        "native_metadata_set",
    }
)

#: Ops that change session/document/CAD state and therefore require the
#: provider-side writer lane + uncertainty quarantine on timeout.
MUTATION_OPS: frozenset[str] = frozenset(
    {
        "document_new",
        "document_open",
        "document_save",
        "document_save_as",
        "document_export_pdf",
        "drawing_audit",
        "drawing_purge",
        "document_configure_units",
        "artifact_seal",
        "object_set_properties",
        "object_delete",
        "object_move",
        "object_copy",
        "object_rotate",
        "object_scale",
        "entity_create_line",
        "entity_create_circle",
        "entity_create_arc",
        "entity_create_polyline",
        "entity_create_text",
        "hatch_create",
        "dimension_linear",
        "dimension_aligned",
        "dimension_angular",
        "dimension_radial",
        "dimension_diametric",
        "dimension_ordinate",
        "layer_create",
        "layer_set_current",
        "layer_update_state",
        "block_create",
        "block_insert",
        "xref_attach",
        "xref_reload",
        "xref_unload",
        "xref_detach",
        "layout_create",
        "layout_set_current",
        "viewport_create",
        "viewport_set_scale",
        "viewport_lock",
        "viewport_delete",
        "view_zoom_extents",
        "view_zoom_window",
        "view_set_direction",
        "view_set_preset",
        "view_set_visual_style",
        "entity_create_3d_polyline",
        "solid_box",
        "solid_cylinder",
        "solid_sphere",
        "solid_cone",
        "solid_torus",
        "solid_wedge",
        "solid_extrude",
        "solid_sweep",
        "solid_revolve",
        "solid_boolean",
        "solid_move",
        "solid_rotate3d",
        "solid_scale3d",
        "solid_mirror3d",
        "solid_export",
        "transaction_begin",
        "transaction_commit",
        "transaction_rollback",
        "undo",
        "redo",
        # native strong-integrity mutations enter the provider-side writer
        # lane + uncertainty quarantine like any other mutation op.
        "native_feature_execute",
        "native_batch_create_entities",
        "native_batch_transform_entities",
        "native_metadata_set",
    }
)

MAX_REQUEST_BYTES = 256 * 1024
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
DEFAULT_DEADLINE_MS = 60_000
MAX_DEADLINE_MS = 120_000
MIN_DEADLINE_MS = 100


class RuntimeTransportError(AutoCADProviderError):
    """Base class for typed runtime-boundary failures."""


class RuntimeUnavailableError(RuntimeTransportError):
    """Runtime endpoint unreachable before dispatch — safe to report, never a success."""


class RuntimeAuthError(RuntimeTransportError):
    """Missing/rejected runtime credential. Not retryable without operator action."""

    def __init__(self, message: str):
        super().__init__(message)
        self.retryable = False


class RuntimeGenerationMismatchError(RuntimeTransportError, StateConflictError):
    """Stale runtime generation — result discarded before CAD effect is assumed."""


class RuntimeUncertainError(RuntimeTransportError, MutationCompletionUncertainError):
    """Timeout/disconnect after dispatch started — completion unknown, no blind replay."""


class RuntimeOpRefusedError(ValueError):
    """Unknown/refused op rejected before dispatch — no CAD effect, safe to surface."""


def check_op(op: str) -> str:
    """Validate an op name against the allowlist before any dispatch."""
    name = str(op or "").strip()
    if name not in ALLOWED_OPS:
        raise RuntimeOpRefusedError(f"runtime op refused (not in allowlist): {name!r}")
    return name


def check_deadline(deadline_ms: int | None) -> int:
    """Bound a dispatch deadline; violations fail before dispatch."""
    value = DEFAULT_DEADLINE_MS if deadline_ms is None else int(deadline_ms)
    if not MIN_DEADLINE_MS <= value <= MAX_DEADLINE_MS:
        raise ValueError(
            f"deadline_ms must be within [{MIN_DEADLINE_MS}, {MAX_DEADLINE_MS}], got {value}"
        )
    return value


def check_request_size(payload: dict[str, Any]) -> dict[str, Any]:
    """Bound request bytes before dispatch."""
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    if len(raw) > MAX_REQUEST_BYTES:
        raise RuntimeOpRefusedError(
            f"runtime request oversized: {len(raw)} bytes > {MAX_REQUEST_BYTES}"
        )
    return payload


@dataclass(frozen=True)
class RuntimeRequest:
    op: str
    args: tuple[Any, ...] = ()
    kwargs: dict[str, Any] = field(default_factory=dict)
    deadline_ms: int = DEFAULT_DEADLINE_MS
    expected_generation: str | None = None
    request_id: str = field(default_factory=lambda: uuid4().hex)

    def to_wire(self) -> dict[str, Any]:
        return check_request_size(
            {
                "request_id": self.request_id,
                "op": check_op(self.op),
                "args": list(self.args),
                "kwargs": dict(self.kwargs),
                "deadline_ms": check_deadline(self.deadline_ms),
                "expected_generation": self.expected_generation,
            }
        )


@dataclass(frozen=True)
class RuntimeResponse:
    ok: bool
    result: Any = None
    error_code: str = "ok"
    error_message: str = ""
    generation: str = "unbound"
    completion_unknown: bool = False

    @classmethod
    def from_wire(cls, payload: dict[str, Any]) -> RuntimeResponse:
        if not isinstance(payload, dict):
            raise RuntimeTransportError("malformed runtime response (not an object)")
        return cls(
            ok=bool(payload.get("ok", False)),
            result=payload.get("result"),
            error_code=str(payload.get("error_code") or ("ok" if payload.get("ok") else "error")),
            error_message=str(payload.get("error_message") or ""),
            generation=str(payload.get("generation") or "unbound"),
            completion_unknown=bool(payload.get("completion_unknown", False)),
        )


def raise_for_response(op: str, response: RuntimeResponse) -> Any:
    """Convert a verified wire response into a result or a typed error."""
    if response.ok:
        return response.result
    code = response.error_code
    message = response.error_message or f"runtime op {op!r} failed: {code}"
    if code in {"unauthorized", "forbidden"}:
        raise RuntimeAuthError(message)
    if code in {"generation_mismatch", "stale_generation"}:
        raise RuntimeGenerationMismatchError(message)
    if code in {"unknown_op", "op_refused", "oversized", "bad_request"}:
        raise RuntimeOpRefusedError(message)
    if code in {"dispatch_timeout_uncertain", "uncertain"} or response.completion_unknown:
        raise RuntimeUncertainError(message)
    if code in {"dispatch_timeout_clean", "timeout_clean"}:
        raise BackendTimeoutError(message, retryable=False, completion_unknown=False)
    if code == "unavailable":
        raise RuntimeUnavailableError(message)
    raise RuntimeTransportError(f"{message} [{code}]")


class RuntimeTransport(ABC):
    """Typed provider -> runtime boundary. One op call, one verified response."""

    @abstractmethod
    async def call(
        self,
        op: str,
        args: tuple[Any, ...] = (),
        kwargs: dict[str, Any] | None = None,
        *,
        deadline_ms: int | None = None,
        expected_generation: str | None = None,
    ) -> Any: ...

    @abstractmethod
    async def health(self) -> dict[str, Any]: ...

    @abstractmethod
    async def close(self) -> None: ...


class LocalRuntimeTransport(RuntimeTransport):
    """In-process delegate over an R1 AutoCADRuntimePort. Default single-host path.

    No serialization boundary: args pass through unchanged, so PID/fingerprint
    values keep exact identity. Timeouts after dispatch starts are still
    uncertain (completion_unknown=True) to preserve §5 semantics.
    """

    def __init__(self, runtime: Any, *, generation: str = "local") -> None:
        from .backends.runtime_port import AutoCADRuntimePort

        if not isinstance(runtime, AutoCADRuntimePort):
            raise TypeError("LocalRuntimeTransport requires an AutoCADRuntimePort")
        self._runtime = runtime
        self._generation = str(generation or "local")
        self._closed = False

    @property
    def generation(self) -> str:
        return self._generation

    async def call(
        self,
        op: str,
        args: tuple[Any, ...] = (),
        kwargs: dict[str, Any] | None = None,
        *,
        deadline_ms: int | None = None,
        expected_generation: str | None = None,
    ) -> Any:
        if self._closed:
            raise RuntimeUnavailableError("local runtime transport is closed")
        name = check_op(op)
        bound = check_deadline(deadline_ms)
        if expected_generation is not None and expected_generation != self._generation:
            raise RuntimeGenerationMismatchError(
                f"runtime generation mismatch: expected {expected_generation!r}, "
                f"local generation is {self._generation!r}; result discarded"
            )
        target = getattr(self._runtime, name, None)
        if not callable(target):
            raise RuntimeOpRefusedError(f"runtime op not implemented by local adapter: {name!r}")
        try:
            invoked = target(*args, **(kwargs or {}))
            if asyncio.iscoroutine(invoked):
                # Async dispatch started: a deadline fire now is uncertain.
                result = await asyncio.wait_for(invoked, timeout=bound / 1000.0)
            else:
                # Sync identity reads (capabilities/status/health) complete
                # inline under the wrapped backend's own bounds (R1 unchanged).
                result = invoked
        except TimeoutError as exc:
            # Dispatch already started in-process: completion is unknown.
            raise RuntimeUncertainError(
                f"local runtime op {name!r} exceeded {bound}ms after dispatch; "
                "completion is unknown, blind retry is forbidden"
            ) from exc
        return result

    async def health(self) -> dict[str, Any]:
        if self._closed:
            raise RuntimeUnavailableError("local runtime transport is closed")
        return {
            "transport": "local",
            "reachable": True,
            "generation": self._generation,
            "runtime": self._runtime.health(),
        }

    async def close(self) -> None:
        self._closed = True


def _require_loopback(url: str, *, allow_remote: bool = False) -> str:
    from urllib.parse import urlparse

    host = (urlparse(url).hostname or "").lower()
    if host not in _LOOPBACK_HOSTS and not allow_remote:
        raise RuntimeOpRefusedError(
            f"refusing non-loopback runtime endpoint {host!r}; "
            "split-host deploy is out of scope for R2"
        )
    return url.rstrip("/")


def _no_proxy_opener() -> urllib.request.OpenerDirector:
    # Loopback runtime traffic must never leave the host via an env proxy.
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _preflight_connect(host: str, port: int, timeout_s: float) -> str:
    """One-shot TCP probe before dispatch: refused proves nothing is listening.

    Returns "open" (listener present), "refused" (nothing listening — dispatch
    is provably impossible), or "filtered" (no answer — POST decides; any
    post-connect loss stays completion-unknown).
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(max(0.2, min(2.0, timeout_s)))
    try:
        sock.connect((host, port))
        return "open"
    except ConnectionRefusedError:
        return "refused"
    except OSError:
        return "filtered"
    finally:
        try:
            sock.close()
        except OSError:
            pass


def _endpoint_host_port(base_url: str) -> tuple[str, int]:
    from urllib.parse import urlparse

    parts = urlparse(base_url)
    return (parts.hostname or "127.0.0.1", int(parts.port or 80))


def _post_json(
    url: str, payload: dict[str, Any], *, token: str, timeout_s: float,
) -> tuple[int, bytes]:
    """Blocking HTTP POST executed inside a worker thread (no new dependencies)."""
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=raw,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Content-Length": str(len(raw)),
            "Authorization": f"Bearer {token}",
        },
    )
    try:
        with _no_proxy_opener().open(request, timeout=timeout_s) as response:
            return int(response.status or 200), response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        return int(exc.code or 500), exc.read(MAX_RESPONSE_BYTES + 1)


def _get_json(url: str, *, token: str, timeout_s: float) -> tuple[int, bytes]:
    request = urllib.request.Request(
        url, method="GET", headers={"Authorization": f"Bearer {token}"}
    )
    try:
        with _no_proxy_opener().open(request, timeout=timeout_s) as response:
            return int(response.status or 200), response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as exc:
        return int(exc.code or 500), exc.read(MAX_RESPONSE_BYTES + 1)


def _decode_response(status: int, raw: bytes, *, op: str) -> RuntimeResponse:
    if len(raw) > MAX_RESPONSE_BYTES:
        raise RuntimeTransportError(
            f"runtime response oversized ({len(raw)} bytes); discarded without trust"
        )
    if status in (401, 403):
        raise RuntimeAuthError(
            f"runtime endpoint rejected credentials for op {op!r} (http {status})"
        )
    if status == 404:
        raise RuntimeUnavailableError(f"runtime endpoint has no route for op {op!r}")
    if status >= 500:
        # 5xx after dispatch may mean CAD work started server-side: uncertain.
        raise RuntimeUncertainError(
            f"runtime endpoint error {status} for op {op!r} after dispatch; "
            "completion is unknown, blind retry is forbidden"
        )
    if status >= 400:
        raise RuntimeTransportError(f"runtime endpoint http {status} for op {op!r}")
    try:
        payload = json.loads(raw.decode("utf-8") or "{}")
    except (ValueError, UnicodeDecodeError) as exc:
        raise RuntimeTransportError(f"malformed runtime response for op {op!r}") from exc
    return RuntimeResponse.from_wire(payload)


class RemoteRuntimeTransport(RuntimeTransport):
    """Loopback HTTP client to a WorkstationRuntimeAgent (R2 split-process path).

    Proves the remote boundary on one host; split-host deploy comes later
    (non-loopback refused unless explicitly allowed). Auth is a bearer token
    compared server-side with compare_digest; never logged.

    Timeout rule (§5): any timeout/disconnect once dispatch may have started
    is completion-unknown. Only pre-dispatch failures (unresolvable host,
    refused connection, refused op, refused deadline) are clean errors.
    """

    def __init__(
        self,
        base_url: str,
        auth_token: str,
        *,
        allow_remote: bool = False,
        default_deadline_ms: int = DEFAULT_DEADLINE_MS,
    ) -> None:
        if not str(auth_token or "").strip():
            raise ValueError("RemoteRuntimeTransport requires a non-empty auth_token")
        self._base_url = _require_loopback(base_url, allow_remote=allow_remote)
        # Token is secret: keep it out of repr and logs.
        self._auth_token = str(auth_token)
        self._default_deadline_ms = check_deadline(default_deadline_ms)
        self._closed = False
        self._lock = threading.Lock()

    def __repr__(self) -> str:
        return f"RemoteRuntimeTransport(base_url={self._base_url!r}, auth=<redacted>)"

    async def call(
        self,
        op: str,
        args: tuple[Any, ...] = (),
        kwargs: dict[str, Any] | None = None,
        *,
        deadline_ms: int | None = None,
        expected_generation: str | None = None,
    ) -> Any:
        if self._closed:
            raise RuntimeUnavailableError("remote runtime transport is closed")
        request = RuntimeRequest(
            op=check_op(op),
            args=tuple(args),
            kwargs=dict(kwargs or {}),
            deadline_ms=self._default_deadline_ms if deadline_ms is None else deadline_ms,
            expected_generation=expected_generation,
        )
        wire = request.to_wire()  # bounds op + deadline + bytes before any I/O
        timeout_s = request.deadline_ms / 1000.0
        host, port = _endpoint_host_port(self._base_url)
        preflight = await asyncio.to_thread(_preflight_connect, host, port, timeout_s)
        if preflight == "refused":
            raise RuntimeUnavailableError(
                f"remote runtime refused connection before dispatch "
                f"for op {request.op!r}; nothing could have executed"
            )
        url = f"{self._base_url}/dispatch"
        try:
            status, raw = await asyncio.to_thread(
                _post_json, url, wire, token=self._auth_token, timeout_s=timeout_s
            )
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            reason = getattr(exc, "reason", None) or exc
            if isinstance(reason, ConnectionRefusedError) or isinstance(
                exc, ConnectionRefusedError
            ):
                raise RuntimeUnavailableError(
                    f"remote runtime refused connection before dispatch "
                    f"for op {request.op!r}: {exc}"
                ) from exc
            if isinstance(reason, OSError) and "getaddrinfo" in str(reason).lower():
                raise RuntimeUnavailableError(
                    f"remote runtime host unresolvable before dispatch "
                    f"for op {request.op!r}: {exc}"
                ) from exc
            # Anything else (timeout, reset, truncated body): bytes may have
            # reached the agent and CAD work may have started. Uncertain.
            raise RuntimeUncertainError(
                f"remote runtime op {request.op!r} lost response after dispatch "
                f"({exc}); completion is unknown, blind retry is forbidden"
            ) from exc
        response = await asyncio.to_thread(_decode_response, status, raw, op=request.op)
        if (
            request.expected_generation is not None
            and response.generation != request.expected_generation
        ):
            raise RuntimeGenerationMismatchError(
                f"runtime generation mismatch: expected {request.expected_generation!r}, "
                f"got {response.generation!r}; result for op {request.op!r} discarded"
            )
        return raise_for_response(request.op, response)

    async def health(self) -> dict[str, Any]:
        if self._closed:
            raise RuntimeUnavailableError("remote runtime transport is closed")
        host, port = _endpoint_host_port(self._base_url)
        preflight = await asyncio.to_thread(_preflight_connect, host, port, 5.0)
        if preflight == "refused":
            raise RuntimeUnavailableError("remote runtime refused connection; agent is down")
        try:
            status, raw = await asyncio.to_thread(
                _get_json,
                f"{self._base_url}/health",
                token=self._auth_token,
                timeout_s=5.0,
            )
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise RuntimeUnavailableError(f"remote runtime health unreachable: {exc}") from exc
        response = await asyncio.to_thread(_decode_response, status, raw, op="health")
        if not response.ok:
            raise RuntimeUnavailableError(
                f"remote runtime unhealthy: {response.error_message or response.error_code}"
            )
        result = response.result
        return result if isinstance(result, dict) else {"ok": True, "detail": result}

    async def close(self) -> None:
        self._closed = True


def bearer_matches(presented: str, expected: str) -> bool:
    """Constant-time bearer comparison; empty expected never matches."""
    if not str(expected or ""):
        return False
    return hmac.compare_digest(str(presented or ""), str(expected))
