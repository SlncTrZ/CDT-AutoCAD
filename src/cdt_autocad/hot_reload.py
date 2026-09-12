"""Hot reload core — single-writer generation fencing, drain and fail-safe promotion.
Wing: code | Topic: mp2-hot-reload | Updated: 2026-09-11 09:20
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from contextlib import AbstractAsyncContextManager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import uuid4


@dataclass(frozen=True)
class WorkerSpec:
    generation: int
    generation_id: str
    build_id: str


@dataclass
class WorkerGeneration:
    generation: int
    generation_id: str
    build_id: str
    base_url: str
    process_id: int | None = None
    opaque: Any = None


@dataclass(frozen=True)
class WorkerHealth:
    generation_id: str
    build_id: str
    protocol_version: str
    contract_version: str
    contract_hash: str
    tool_count: int
    startup_ready: bool
    runtime_ready: bool
    transaction_depth: int
    timeout_uncertain: bool
    pending_recovery_count: int
    bridge_ready: bool
    policy_fingerprint: str


@dataclass(frozen=True)
class ReloadResult:
    ok: bool
    reload_id: str
    old_generation: str | None
    new_generation: str | None
    error_code: str | None = None


@dataclass(frozen=True)
class SupervisorEvent:
    event_type: str
    timestamp_utc: str
    reload_id: str | None
    phase: str
    active_generation: str | None
    candidate_generation: str | None = None
    outcome: str | None = None
    error_code: str | None = None
    latency_ms: float | None = None

    def to_dict(self) -> dict[str, object]:
        return {key: value for key, value in asdict(self).items() if value is not None}


class SupervisorEventSink(Protocol):
    def emit(self, event: SupervisorEvent) -> None: ...


class JsonSupervisorEventSink:
    def __init__(self, logger: logging.Logger | None = None):
        self._logger = logger or logging.getLogger("cdt_autocad.hot_reload")

    def emit(self, event: SupervisorEvent) -> None:
        self._logger.info(
            json.dumps(event.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        )


class WorkerLauncher(Protocol):
    async def start(self, spec: WorkerSpec, env: dict[str, str]) -> WorkerGeneration: ...

    async def stop(self, worker: WorkerGeneration) -> None: ...

    def is_alive(self, worker: WorkerGeneration) -> bool: ...


class WorkerProbe(Protocol):
    async def startup(self, worker: WorkerGeneration) -> WorkerHealth: ...

    async def activation(self, worker: WorkerGeneration) -> WorkerHealth: ...


class ReloadUnavailableError(RuntimeError):
    """Raised when the public request gate is closed for deterministic drain/promotion."""


class _Lease(AbstractAsyncContextManager[WorkerGeneration]):
    def __init__(self, supervisor: ReloadSupervisor):
        self._supervisor = supervisor
        self._worker: WorkerGeneration | None = None

    async def __aenter__(self) -> WorkerGeneration:
        self._worker = await self._supervisor._acquire_lease()
        return self._worker

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._worker is not None:
            await self._supervisor._release_lease(self._worker)
            self._worker = None


class ReloadSupervisor:
    """Own one authoritative worker generation and promote replacements fail-safely."""

    def __init__(
        self,
        *,
        launcher: WorkerLauncher,
        probe: WorkerProbe,
        frozen_env: dict[str, str],
        policy_fingerprint: str,
        expected_protocol_version: str,
        expected_contract_version: str,
        expected_contract_hash: str,
        expected_tool_count: int,
        require_bridge: bool,
        drain_timeout_seconds: float = 10.0,
        event_sink: SupervisorEventSink | None = None,
    ):
        if drain_timeout_seconds <= 0:
            raise ValueError("drain_timeout_seconds must be > 0")
        self._launcher = launcher
        self._probe = probe
        self._frozen_env = dict(frozen_env)
        self._policy_fingerprint = policy_fingerprint
        self._expected_protocol_version = expected_protocol_version
        self._expected_contract_version = expected_contract_version
        self._expected_contract_hash = expected_contract_hash
        self._expected_tool_count = expected_tool_count
        self._require_bridge = require_bridge
        self._drain_timeout_seconds = drain_timeout_seconds
        self._event_sink = event_sink or JsonSupervisorEventSink()
        self._event_dropped_count = 0
        self._reload_lock = asyncio.Lock()
        self._condition = asyncio.Condition()
        self._current: WorkerGeneration | None = None
        self._accepting = False
        self._phase = "stopped"
        self._active_counts: dict[str, int] = {}
        self._next_generation = 1
        self._reload_successes = 0
        self._reload_failures = 0
        self._last_error_code: str | None = None

    @property
    def event_dropped_count(self) -> int:
        return self._event_dropped_count

    def lease(self) -> AbstractAsyncContextManager[WorkerGeneration]:
        return _Lease(self)

    async def bootstrap(self, build_id: str) -> WorkerGeneration:
        async with self._reload_lock:
            if self._current is not None:
                raise RuntimeError("supervisor is already bootstrapped")
            spec = self._allocate_spec(build_id)
            self._phase = "starting"
            worker = await self._launcher.start(spec, self._worker_env(spec))
            try:
                startup = await self._probe.startup(worker)
                self._validate_startup_health(worker, startup)
                activation = await self._probe.activation(worker)
                self._validate_activation_health(worker, activation)
            except Exception:
                await self._launcher.stop(worker)
                self._phase = "stopped"
                raise
            async with self._condition:
                self._current = worker
                self._active_counts.setdefault(worker.generation_id, 0)
                self._accepting = True
                self._phase = "ready"
                self._condition.notify_all()
            self._emit("generation.bootstrapped", None, candidate=worker, outcome="success")
            return worker

    async def reload(self, build_id: str) -> ReloadResult:
        async with self._reload_lock:
            old = self._current
            if old is None:
                raise RuntimeError("supervisor is not bootstrapped")
            reload_id = uuid4().hex
            started = time.perf_counter()
            candidate: WorkerGeneration | None = None
            try:
                spec = self._allocate_spec(build_id)
                self._phase = "starting"
                self._emit("reload.started", reload_id, candidate=None)
                candidate = await self._launcher.start(spec, self._worker_env(spec))
                startup = await self._probe.startup(candidate)
                self._validate_startup_health(candidate, startup)

                async with self._condition:
                    self._accepting = False
                    self._phase = "draining"
                    self._condition.notify_all()
                self._emit("reload.fenced", reload_id, candidate=candidate)
                await self._wait_for_drain(old)

                old_health = await self._probe.activation(old)
                if not self._safe_state(old_health):
                    raise _ReloadFailure("OLD_GENERATION_UNSAFE")
                candidate_health = await self._probe.activation(candidate)
                self._validate_activation_health(candidate, candidate_health)

                async with self._condition:
                    self._phase = "promoting"
                    self._current = candidate
                    self._active_counts.setdefault(candidate.generation_id, 0)
                    self._accepting = True
                    self._phase = "ready"
                    self._condition.notify_all()
                await self._launcher.stop(old)
                self._active_counts.pop(old.generation_id, None)
                self._reload_successes += 1
                self._last_error_code = None
                latency_ms = (time.perf_counter() - started) * 1000.0
                self._emit(
                    "reload.completed",
                    reload_id,
                    candidate=candidate,
                    outcome="success",
                    latency_ms=latency_ms,
                )
                return ReloadResult(
                    ok=True,
                    reload_id=reload_id,
                    old_generation=old.generation_id,
                    new_generation=candidate.generation_id,
                )
            except TimeoutError:
                return await self._fail_reload(
                    old,
                    candidate,
                    reload_id,
                    "DRAIN_TIMEOUT",
                    started,
                )
            except _ReloadFailure as exc:
                return await self._fail_reload(
                    old,
                    candidate,
                    reload_id,
                    exc.code,
                    started,
                )
            except Exception:
                return await self._fail_reload(
                    old,
                    candidate,
                    reload_id,
                    "STARTUP_HEALTH_FAILED" if self._phase == "starting" else "RELOAD_FAILED",
                    started,
                )

    async def wait_for_phase(self, phase: str, *, timeout: float) -> None:
        async def _wait() -> None:
            async with self._condition:
                while self._phase != phase:
                    await self._condition.wait()

        await asyncio.wait_for(_wait(), timeout=timeout)

    def status(self) -> dict[str, Any]:
        current = self._current
        active_generation = current.generation_id if current is not None else None
        return {
            "phase": self._phase,
            "active_generation": active_generation,
            "active_build_id": current.build_id if current is not None else None,
            "active_process_id": current.process_id if current is not None else None,
            "active_process_alive": bool(current is not None and self._launcher.is_alive(current)),
            "accepting_requests": self._accepting,
            "active_request_count": (
                self._active_counts.get(active_generation, 0) if active_generation is not None else 0
            ),
            "reload_successes": self._reload_successes,
            "reload_failures": self._reload_failures,
            "last_error_code": self._last_error_code,
            "policy_fingerprint": self._policy_fingerprint,
            "event_dropped_count": self._event_dropped_count,
        }

    async def shutdown(self) -> None:
        async with self._reload_lock:
            async with self._condition:
                self._accepting = False
                self._phase = "stopping"
                self._condition.notify_all()
            current = self._current
            if current is not None:
                await self._wait_for_drain(current)
                await self._launcher.stop(current)
            self._current = None
            self._phase = "stopped"

    async def _acquire_lease(self) -> WorkerGeneration:
        async with self._condition:
            if not self._accepting or self._current is None:
                raise ReloadUnavailableError("provider reload is draining or unavailable")
            worker = self._current
            self._active_counts[worker.generation_id] = (
                self._active_counts.get(worker.generation_id, 0) + 1
            )
            return worker

    async def _release_lease(self, worker: WorkerGeneration) -> None:
        async with self._condition:
            count = self._active_counts.get(worker.generation_id, 0)
            self._active_counts[worker.generation_id] = max(0, count - 1)
            self._condition.notify_all()

    async def _wait_for_drain(self, worker: WorkerGeneration) -> None:
        async def _wait() -> None:
            async with self._condition:
                while self._active_counts.get(worker.generation_id, 0) > 0:
                    await self._condition.wait()

        await asyncio.wait_for(_wait(), timeout=self._drain_timeout_seconds)

    async def _fail_reload(
        self,
        old: WorkerGeneration,
        candidate: WorkerGeneration | None,
        reload_id: str,
        error_code: str,
        started: float,
    ) -> ReloadResult:
        if candidate is not None and candidate.generation_id != old.generation_id:
            await self._launcher.stop(candidate)
        async with self._condition:
            self._current = old
            self._accepting = True
            self._phase = "ready"
            self._condition.notify_all()
        self._reload_failures += 1
        self._last_error_code = error_code
        self._emit(
            "reload.failed",
            reload_id,
            candidate=candidate,
            outcome="error",
            error_code=error_code,
            latency_ms=(time.perf_counter() - started) * 1000.0,
        )
        return ReloadResult(
            ok=False,
            reload_id=reload_id,
            old_generation=old.generation_id,
            new_generation=None,
            error_code=error_code,
        )

    def _allocate_spec(self, build_id: str) -> WorkerSpec:
        generation = self._next_generation
        self._next_generation += 1
        return WorkerSpec(
            generation=generation,
            generation_id=f"g{generation:06d}",
            build_id=build_id,
        )

    def _worker_env(self, spec: WorkerSpec) -> dict[str, str]:
        env = dict(self._frozen_env)
        env.update(
            {
                "CDT_AUTOCAD_RUNTIME_GENERATION": spec.generation_id,
                "CDT_AUTOCAD_RUNTIME_BUILD_ID": spec.build_id,
                "CDT_AUTOCAD_RUNTIME_POLICY_FP": self._policy_fingerprint,
                "CDT_AUTOCAD_RUNTIME_SUPERVISED": "1",
            }
        )
        return env

    def _validate_startup_health(self, worker: WorkerGeneration, health: WorkerHealth) -> None:
        if (
            not health.startup_ready
            or health.generation_id != worker.generation_id
            or health.build_id != worker.build_id
            or health.protocol_version != self._expected_protocol_version
            or health.contract_version != self._expected_contract_version
            or health.contract_hash != self._expected_contract_hash
            or health.tool_count != self._expected_tool_count
            or health.policy_fingerprint != self._policy_fingerprint
        ):
            raise _ReloadFailure("STARTUP_HEALTH_FAILED")

    def _validate_activation_health(self, worker: WorkerGeneration, health: WorkerHealth) -> None:
        self._validate_startup_health(worker, health)
        if not self._safe_state(health):
            raise _ReloadFailure("ACTIVATION_HEALTH_FAILED")

    def _safe_state(self, health: WorkerHealth) -> bool:
        return bool(
            health.runtime_ready
            and health.transaction_depth == 0
            and not health.timeout_uncertain
            and health.pending_recovery_count == 0
            and (health.bridge_ready or not self._require_bridge)
        )

    def _emit(
        self,
        event_type: str,
        reload_id: str | None,
        *,
        candidate: WorkerGeneration | None = None,
        outcome: str | None = None,
        error_code: str | None = None,
        latency_ms: float | None = None,
    ) -> None:
        event = SupervisorEvent(
            event_type=event_type,
            timestamp_utc=datetime.now(UTC).isoformat(),
            reload_id=reload_id,
            phase=self._phase,
            active_generation=(
                self._current.generation_id if self._current is not None else None
            ),
            candidate_generation=(candidate.generation_id if candidate is not None else None),
            outcome=outcome,
            error_code=error_code,
            latency_ms=latency_ms,
        )
        try:
            self._event_sink.emit(event)
        except Exception:
            self._event_dropped_count += 1


class _ReloadFailure(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)
