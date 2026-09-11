"""A-01 timeout safety — fence unknown COM completion before later mutations.
Wing: code | Topic: mp0-a01-timeout-safety | Updated: 2026-09-11 07:04
"""

from __future__ import annotations

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from cdt_autocad.backends.com_backend import ComBackend
from cdt_autocad.errors import BackendQuarantinedError, BackendTimeoutError
from cdt_autocad.server import _classify_error


@pytest.mark.asyncio
async def test_mutation_timeout_keeps_single_executor_and_blocks_later_mutation(settings):
    backend = ComBackend(replace(settings, backend="com", com_call_timeout_seconds=0.03))
    executor = ThreadPoolExecutor(max_workers=1)
    backend._executor = executor
    entered = threading.Event()
    release = threading.Event()
    second_mutation_ran = threading.Event()

    def hangs_after_dispatch():
        entered.set()
        release.wait(1.0)
        return "late-commit"

    try:
        with pytest.raises(BackendTimeoutError) as caught:
            await backend._run(hangs_after_dispatch, may_mutate_document=True)

        assert entered.is_set()
        assert backend._executor is executor
        assert backend.status()["timeout_uncertain"] is True
        assert caught.value.retryable is False
        assert caught.value.completion_unknown is True

        with pytest.raises(BackendQuarantinedError, match="quarantined"):
            await backend._run(
                lambda: second_mutation_ran.set(),
                may_mutate_document=True,
            )
        assert second_mutation_ran.is_set() is False
    finally:
        release.set()
        executor.shutdown(wait=True)
        backend._executor = None


@pytest.mark.asyncio
async def test_mutation_queued_before_timeout_is_fenced_before_dispatch(settings):
    backend = ComBackend(replace(settings, backend="com", com_call_timeout_seconds=0.03))
    executor = ThreadPoolExecutor(max_workers=1)
    backend._executor = executor
    entered = threading.Event()
    release = threading.Event()
    second_mutation_ran = threading.Event()

    def first_mutation():
        entered.set()
        release.wait(1.0)

    first_task = asyncio.create_task(
        backend._run(first_mutation, may_mutate_document=True)
    )
    try:
        assert await asyncio.to_thread(entered.wait, 0.5)
        second_task = asyncio.create_task(
            backend._run(
                lambda: second_mutation_ran.set(),
                may_mutate_document=True,
            )
        )

        with pytest.raises(BackendTimeoutError):
            await first_task
        with pytest.raises(BackendQuarantinedError):
            await second_task
        assert second_mutation_ran.is_set() is False
    finally:
        release.set()
        executor.shutdown(wait=True)
        backend._executor = None


@pytest.mark.asyncio
async def test_read_only_timeout_is_retryable_without_quarantine(settings):
    backend = ComBackend(replace(settings, backend="com", com_call_timeout_seconds=0.03))
    executor = ThreadPoolExecutor(max_workers=1)
    backend._executor = executor
    release = threading.Event()

    def slow_read():
        release.wait(1.0)
        return "late-read"

    try:
        with pytest.raises(BackendTimeoutError) as caught:
            await backend._run(slow_read, may_mutate_document=False)
        assert caught.value.retryable is True
        assert caught.value.completion_unknown is False
        assert backend.status()["timeout_uncertain"] is False
    finally:
        release.set()
        executor.shutdown(wait=True)
        backend._executor = None


@pytest.mark.asyncio
async def test_quarantine_persists_after_late_completion(settings):
    backend = ComBackend(replace(settings, backend="com", com_call_timeout_seconds=0.03))
    executor = ThreadPoolExecutor(max_workers=1)
    backend._executor = executor
    release = threading.Event()
    later_mutation_ran = threading.Event()

    def first_mutation():
        release.wait(1.0)

    try:
        with pytest.raises(BackendTimeoutError):
            await backend._run(first_mutation, may_mutate_document=True)
        release.set()
        await backend._run(lambda: "barrier", may_mutate_document=False)

        assert backend.status()["uncertain_call_running"] is False
        assert backend.status()["timeout_uncertain"] is True
        with pytest.raises(BackendQuarantinedError, match="restart the provider"):
            await backend._run(
                lambda: later_mutation_ran.set(),
                may_mutate_document=True,
            )
        assert later_mutation_ran.is_set() is False
    finally:
        release.set()
        executor.shutdown(wait=True)
        backend._executor = None


@pytest.mark.asyncio
async def test_quarantined_readback_serializes_behind_old_writer_without_clearing_latch(settings):
    backend = ComBackend(replace(settings, backend="com", com_call_timeout_seconds=0.03))
    executor = ThreadPoolExecutor(max_workers=1)
    backend._executor = executor
    entered = threading.Event()
    release = threading.Event()
    order: list[str] = []

    def old_mutation():
        entered.set()
        release.wait(1.0)
        order.append("old-done")

    try:
        with pytest.raises(BackendTimeoutError):
            await backend._run(old_mutation, may_mutate_document=True)
        assert entered.is_set()

        release.set()
        result = await backend._run(
            lambda: order.append("readback") or "verified-read",
            may_mutate_document=False,
        )

        assert result == "verified-read"
        assert order == ["old-done", "readback"]
        assert backend._executor is executor
        assert backend.status()["timeout_uncertain"] is True
    finally:
        release.set()
        executor.shutdown(wait=True)
        backend._executor = None


@pytest.mark.asyncio
async def test_document_new_cannot_clear_quarantine(settings):
    backend = ComBackend(replace(settings, backend="com", com_call_timeout_seconds=0.03))
    executor = ThreadPoolExecutor(max_workers=1)
    backend._executor = executor
    release = threading.Event()

    try:
        with pytest.raises(BackendTimeoutError):
            await backend._run(lambda: release.wait(1.0), may_mutate_document=True)
        release.set()
        await backend._run(lambda: "barrier", may_mutate_document=False)

        with pytest.raises(BackendQuarantinedError, match="restart the provider"):
            await backend.document_new()
        assert backend.status()["timeout_uncertain"] is True
    finally:
        release.set()
        executor.shutdown(wait=True)
        backend._executor = None


@pytest.mark.asyncio
async def test_request_cancellation_after_mutation_dispatch_latches_quarantine(settings):
    backend = ComBackend(replace(settings, backend="com", com_call_timeout_seconds=5.0))
    executor = ThreadPoolExecutor(max_workers=1)
    backend._executor = executor
    entered = threading.Event()
    release = threading.Event()

    def mutation():
        entered.set()
        release.wait(1.0)

    task = asyncio.create_task(backend._run(mutation, may_mutate_document=True))
    try:
        assert await asyncio.to_thread(entered.wait, 0.5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert backend.status()["timeout_uncertain"] is True
        with pytest.raises(BackendQuarantinedError):
            await backend._run(lambda: None, may_mutate_document=True)
    finally:
        release.set()
        executor.shutdown(wait=True)
        backend._executor = None


def test_unknown_completion_timeout_is_non_retryable_in_public_error_contract():
    error = BackendTimeoutError(
        "mutation may still complete",
        retryable=False,
        completion_unknown=True,
    )

    kind, extra, message = _classify_error(error)

    assert kind == "timeout"
    assert extra == {"retryable": False, "completion_unknown": True}
    assert message == "mutation may still complete"
