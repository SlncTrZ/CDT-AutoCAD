"""D16 shared mutation authority — one COM/native writer and one quarantine boundary.
Wing: code | Topic: d16-shared-mutation-ownership | Updated: 2026-09-19 14:30
"""

from __future__ import annotations

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from cdt_autocad.backends.com_backend import ComBackend
from cdt_autocad.errors import (
    BackendQuarantinedError,
    BackendTimeoutError,
    MutationCompletionUncertainError,
)
from cdt_autocad.mutation_coordinator import MutationCoordinator
from cdt_autocad.server import _classify_error, _run_native_mutation, create_mcp


@pytest.mark.asyncio
async def test_com_writer_serializes_native_writer(settings):
    coordinator = MutationCoordinator()
    backend = ComBackend(
        replace(settings, backend="com", com_call_timeout_seconds=1.0),
        mutation_coordinator=coordinator,
    )
    executor = ThreadPoolExecutor(max_workers=1)
    backend._executor = executor
    com_entered = threading.Event()
    release_com = threading.Event()
    native_ran = threading.Event()

    def com_mutation():
        com_entered.set()
        release_com.wait(1.0)
        return "com-done"

    try:
        com_task = asyncio.create_task(backend._run(com_mutation, may_mutate_document=True))
        assert await asyncio.to_thread(com_entered.wait, 0.5)

        native_task = asyncio.create_task(
            _run_native_mutation(
                coordinator,
                lambda: native_ran.set() or "native-done",
            )
        )
        await asyncio.sleep(0.03)
        assert native_ran.is_set() is False

        release_com.set()
        assert await com_task == "com-done"
        assert await native_task == "native-done"
        assert native_ran.is_set() is True
    finally:
        release_com.set()
        executor.shutdown(wait=True)
        backend._executor = None


@pytest.mark.asyncio
async def test_native_writer_serializes_com_writer(settings):
    coordinator = MutationCoordinator()
    backend = ComBackend(
        replace(settings, backend="com", com_call_timeout_seconds=1.0),
        mutation_coordinator=coordinator,
    )
    executor = ThreadPoolExecutor(max_workers=1)
    backend._executor = executor
    native_entered = threading.Event()
    release_native = threading.Event()
    com_ran = threading.Event()

    def native_mutation():
        native_entered.set()
        release_native.wait(1.0)
        return "native-done"

    try:
        native_task = asyncio.create_task(_run_native_mutation(coordinator, native_mutation))
        assert await asyncio.to_thread(native_entered.wait, 0.5)

        com_task = asyncio.create_task(
            backend._run(lambda: com_ran.set() or "com-done", may_mutate_document=True)
        )
        await asyncio.sleep(0.03)
        assert com_ran.is_set() is False

        release_native.set()
        assert await native_task == "native-done"
        assert await com_task == "com-done"
        assert com_ran.is_set() is True
    finally:
        release_native.set()
        executor.shutdown(wait=True)
        backend._executor = None


@pytest.mark.asyncio
async def test_com_timeout_quarantines_native_writer(settings):
    coordinator = MutationCoordinator()
    backend = ComBackend(
        replace(settings, backend="com", com_call_timeout_seconds=0.03),
        mutation_coordinator=coordinator,
    )
    executor = ThreadPoolExecutor(max_workers=1)
    backend._executor = executor
    release = threading.Event()
    native_ran = threading.Event()

    try:
        with pytest.raises(BackendTimeoutError):
            await backend._run(lambda: release.wait(1.0), may_mutate_document=True)

        with pytest.raises(BackendQuarantinedError, match="quarantined"):
            await _run_native_mutation(
                coordinator,
                lambda: native_ran.set(),
            )
        assert native_ran.is_set() is False
        assert coordinator.status()["quarantined"] is True
        assert coordinator.status()["quarantine_lane"] == "com"
    finally:
        release.set()
        executor.shutdown(wait=True)
        backend._executor = None


@pytest.mark.asyncio
async def test_native_cancellation_after_dispatch_quarantines_com_and_late_completion_does_not_clear(
    settings,
):
    coordinator = MutationCoordinator()
    backend = ComBackend(
        replace(settings, backend="com", com_call_timeout_seconds=1.0),
        mutation_coordinator=coordinator,
    )
    executor = ThreadPoolExecutor(max_workers=1)
    backend._executor = executor
    native_entered = threading.Event()
    release_native = threading.Event()
    com_ran = threading.Event()

    def native_mutation():
        native_entered.set()
        release_native.wait(1.0)
        return "late-native"

    task = asyncio.create_task(_run_native_mutation(coordinator, native_mutation))
    try:
        assert await asyncio.to_thread(native_entered.wait, 0.5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert coordinator.status()["quarantined"] is True
        assert coordinator.status()["quarantine_lane"] == "native"

        release_native.set()
        await asyncio.sleep(0.05)
        assert coordinator.status()["quarantined"] is True

        with pytest.raises(BackendQuarantinedError):
            await backend._run(lambda: com_ran.set(), may_mutate_document=True)
        assert com_ran.is_set() is False
    finally:
        release_native.set()
        executor.shutdown(wait=True)
        backend._executor = None


@pytest.mark.asyncio
async def test_native_cancellation_while_waiting_for_writer_does_not_quarantine():
    coordinator = MutationCoordinator()
    native_ran = threading.Event()

    async with coordinator.writer("com"):
        task = asyncio.create_task(
            _run_native_mutation(
                coordinator,
                lambda: native_ran.set(),
            )
        )
        await asyncio.sleep(0.02)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert native_ran.is_set() is False
    assert coordinator.status()["quarantined"] is False


@pytest.mark.asyncio
async def test_com_process_loss_quarantine_blocks_native_writer(settings):
    coordinator = MutationCoordinator()
    backend = ComBackend(
        replace(settings, backend="com"),
        mutation_coordinator=coordinator,
    )
    native_ran = threading.Event()

    backend._quarantine_integrity(
        "AutoCAD COM application disappeared while a tracked transaction was open"
    )

    with pytest.raises(BackendQuarantinedError, match="quarantined"):
        await _run_native_mutation(
            coordinator,
            lambda: native_ran.set(),
        )
    assert native_ran.is_set() is False
    assert coordinator.status()["quarantine_lane"] == "com"


def test_create_mcp_injects_one_shared_coordinator_into_com_backend(settings):
    app = create_mcp(replace(settings, backend="com"))

    assert app._cdt_backend._mutation_coordinator is app._cdt_mutation_coordinator


def test_native_completion_uncertain_error_is_machine_readable():
    error = MutationCompletionUncertainError("native completion unknown")

    kind, extra, message = _classify_error(error)

    assert kind == "conflict"
    assert extra == {
        "reason": "mutation_completion_uncertain",
        "retryable": False,
        "completion_unknown": True,
    }
    assert message == "native completion unknown"


@pytest.mark.asyncio
async def test_native_unknown_runtime_error_is_normalized_and_quarantines(settings):
    class UnknownNativeError(RuntimeError):
        completion_unknown = True

    coordinator = MutationCoordinator()

    with pytest.raises(MutationCompletionUncertainError, match="bridge response lost"):
        await _run_native_mutation(
            coordinator,
            lambda: (_ for _ in ()).throw(UnknownNativeError("bridge response lost")),
        )

    assert coordinator.status()["quarantined"] is True
    assert coordinator.status()["quarantine_lane"] == "native"


@pytest.mark.asyncio
async def test_native_unknown_completion_quarantines_com_but_validation_refusal_does_not(settings):
    coordinator = MutationCoordinator()
    backend = ComBackend(
        replace(settings, backend="com", com_call_timeout_seconds=1.0),
        mutation_coordinator=coordinator,
    )
    executor = ThreadPoolExecutor(max_workers=1)
    backend._executor = executor
    com_ran = threading.Event()

    try:
        with pytest.raises(ValueError, match="invalid"):
            await _run_native_mutation(
                coordinator,
                lambda: (_ for _ in ()).throw(ValueError("invalid request")),
            )
        assert coordinator.status()["quarantined"] is False

        with pytest.raises(MutationCompletionUncertainError):
            await _run_native_mutation(
                coordinator,
                lambda: (_ for _ in ()).throw(
                    MutationCompletionUncertainError("native completion unknown")
                ),
            )

        assert coordinator.status()["quarantined"] is True
        with pytest.raises(BackendQuarantinedError):
            await backend._run(lambda: com_ran.set(), may_mutate_document=True)
        assert com_ran.is_set() is False
    finally:
        executor.shutdown(wait=True)
        backend._executor = None
