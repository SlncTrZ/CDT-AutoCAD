"""Shared mutation coordinator — one provider-local COM/native writer and quarantine.
Wing: code | Topic: d16-shared-mutation-ownership | Updated: 2026-09-19 14:32
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from .errors import BackendQuarantinedError


class MutationCoordinator:
    """Serialize provider-local CAD writers and retain the first uncertainty quarantine."""

    def __init__(self) -> None:
        self._writer_lock = asyncio.Lock()
        self._state_lock = threading.Lock()
        self._active_lane: str | None = None
        self._quarantine_lane: str | None = None
        self._quarantine_reason: str | None = None

    def status(self) -> dict[str, object]:
        with self._state_lock:
            return {
                "active_lane": self._active_lane,
                "quarantined": self._quarantine_reason is not None,
                "quarantine_lane": self._quarantine_lane,
                "quarantine_reason": self._quarantine_reason,
            }

    def quarantine(self, lane: str, reason: str) -> None:
        lane = str(lane).strip()
        reason = str(reason).strip()
        if not lane or not reason:
            raise ValueError("mutation quarantine requires non-empty lane and reason")
        with self._state_lock:
            if self._quarantine_reason is None:
                self._quarantine_lane = lane
                self._quarantine_reason = reason

    def ensure_writable(self) -> None:
        with self._state_lock:
            lane = self._quarantine_lane
            reason = self._quarantine_reason
        if reason is not None:
            raise BackendQuarantinedError(
                "CAD mutation coordinator is quarantined after "
                f"{lane or 'unknown'} uncertainty: {reason}. "
                "Read-only verification remains available; restart the provider before later mutation."
            )

    @asynccontextmanager
    async def writer(self, lane: str) -> AsyncIterator[None]:
        lane = str(lane).strip()
        if not lane:
            raise ValueError("mutation writer lane must be non-empty")

        self.ensure_writable()
        await self._writer_lock.acquire()
        try:
            self.ensure_writable()
            with self._state_lock:
                self._active_lane = lane
            yield
        finally:
            with self._state_lock:
                if self._active_lane == lane:
                    self._active_lane = None
            self._writer_lock.release()
