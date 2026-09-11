"""Structured diagnostics — bounded correlation events separate from recovery truth.
Wing: code | Topic: mp0-t00-observability | Updated: 2026-09-10 23:24
"""

from __future__ import annotations

import json
import logging
import threading
import time
from contextvars import ContextVar
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Protocol
from uuid import uuid4

from fastmcp.server.middleware import Middleware, MiddlewareContext


@dataclass(frozen=True)
class DiagnosticContext:
    """Correlation state propagated through one provider request."""

    request_id: str
    operation: str
    backend: str
    provider_version: str
    generation: str
    job_id: str | None = None
    document_pid: str | None = None
    runtime_document_id: str | None = None
    recovery_id: str | None = None
    bridge_version: str | None = None
    autocad_version: str | None = None
    scope: str | None = None


@dataclass(frozen=True)
class DiagnosticEvent:
    """Fixed-schema diagnostic event; never used as a recovery journal record."""

    event_type: str
    timestamp_utc: str
    request_id: str
    operation: str
    backend: str
    provider_version: str
    generation: str
    outcome: str | None = None
    latency_ms: float | None = None
    error_class: str | None = None
    job_id: str | None = None
    document_pid: str | None = None
    runtime_document_id: str | None = None
    recovery_id: str | None = None
    bridge_version: str | None = None
    autocad_version: str | None = None
    scope: str | None = None

    def to_dict(self) -> dict[str, object]:
        """Return a compact JSON-safe representation without arbitrary payload fields."""
        return {key: value for key, value in asdict(self).items() if value is not None}


class DiagnosticSink(Protocol):
    """Best-effort telemetry sink; sink failure must not become recovery state."""

    def emit(self, event: DiagnosticEvent) -> None: ...


class JsonLoggingSink:
    """Write one compact structured event through the standard logging pipeline."""

    def __init__(self, logger: logging.Logger | None = None):
        self._logger = logger or logging.getLogger("cdt_autocad.diagnostics")

    def emit(self, event: DiagnosticEvent) -> None:
        self._logger.info(
            json.dumps(event.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        )


class DiagnosticEmitter:
    """Best-effort emitter with an explicit dropped-event counter."""

    def __init__(self, sink: DiagnosticSink | None = None):
        self._sink = sink or JsonLoggingSink()
        self._lock = threading.Lock()
        self._dropped_events = 0

    @property
    def dropped_events(self) -> int:
        with self._lock:
            return self._dropped_events

    def emit(self, event: DiagnosticEvent) -> bool:
        try:
            self._sink.emit(event)
        except Exception:
            with self._lock:
                self._dropped_events += 1
            return False
        return True


_CURRENT_DIAGNOSTIC_CONTEXT: ContextVar[DiagnosticContext | None] = ContextVar(
    "cdt_autocad_diagnostic_context",
    default=None,
)


def current_diagnostic_context() -> DiagnosticContext | None:
    """Return the active request correlation context for deeper provider layers."""
    return _CURRENT_DIAGNOSTIC_CONTEXT.get()


def _event(
    context: DiagnosticContext,
    event_type: str,
    *,
    outcome: str | None = None,
    latency_ms: float | None = None,
    error_class: str | None = None,
) -> DiagnosticEvent:
    return DiagnosticEvent(
        event_type=event_type,
        timestamp_utc=datetime.now(UTC).isoformat(),
        request_id=context.request_id,
        operation=context.operation,
        backend=context.backend,
        provider_version=context.provider_version,
        generation=context.generation,
        outcome=outcome,
        latency_ms=latency_ms,
        error_class=error_class,
        job_id=context.job_id,
        document_pid=context.document_pid,
        runtime_document_id=context.runtime_document_id,
        recovery_id=context.recovery_id,
        bridge_version=context.bridge_version,
        autocad_version=context.autocad_version,
        scope=context.scope,
    )


class ProviderDiagnosticMiddleware(Middleware):
    """Emit bounded tool lifecycle events and propagate one correlation context."""

    def __init__(
        self,
        *,
        backend_name: str,
        provider_version: str,
        sink: DiagnosticSink | None = None,
        generation: str = "static",
    ):
        self.emitter = DiagnosticEmitter(sink)
        self.backend_name = backend_name
        self.provider_version = provider_version
        self.generation = generation

    async def on_call_tool(self, context: MiddlewareContext, call_next):
        operation = str(getattr(context.message, "name", "unknown"))
        diagnostic_context = DiagnosticContext(
            request_id=uuid4().hex,
            operation=operation,
            backend=self.backend_name,
            provider_version=self.provider_version,
            generation=self.generation,
        )
        token = _CURRENT_DIAGNOSTIC_CONTEXT.set(diagnostic_context)
        started = time.perf_counter()
        self.emitter.emit(_event(diagnostic_context, "tool.started"))
        try:
            result = await call_next(context)
        except Exception as exc:
            latency_ms = (time.perf_counter() - started) * 1000.0
            self.emitter.emit(
                _event(
                    diagnostic_context,
                    "tool.failed",
                    outcome="exception",
                    latency_ms=latency_ms,
                    error_class=type(exc).__name__,
                )
            )
            raise
        else:
            latency_ms = (time.perf_counter() - started) * 1000.0
            outcome = "error" if bool(getattr(result, "is_error", False)) else "success"
            self.emitter.emit(
                _event(
                    diagnostic_context,
                    "tool.completed",
                    outcome=outcome,
                    latency_ms=latency_ms,
                )
            )
            return result
        finally:
            _CURRENT_DIAGNOSTIC_CONTEXT.reset(token)
