"""Windows Named Pipe transport — one-request-per-connection with bounded overlapped I/O.
Wing: code | Topic: native-bridge-n3 | Updated: 2026-09-30
"""

from __future__ import annotations

import io
import math
import struct
import sys
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .protocol import MAX_FRAME_BYTES, BridgeProtocolError, decode_frame, encode_frame

DEFAULT_CONNECT_TIMEOUT_MS = 5_000
DEFAULT_IO_TIMEOUT_MS = 60_000
_ERROR_IO_PENDING = 997


class NativePipeDeadlineError(TimeoutError):
    """I/O timed out after connection; remote mutation completion is unknown."""

    completion_unknown = True


def pipe_name_for_session(session_id: int) -> str:
    """Return the frozen N3 per-Windows-session pipe name."""
    if not isinstance(session_id, int) or session_id < 0:
        raise ValueError("session_id must be a non-negative integer")
    return f"SlncTrZ.CDT.AutoCAD.Bridge.v1.s{session_id}"


@dataclass(frozen=True)
class NamedPipeTransport:
    """A timeout after dispatch is unknown completion, never proof of CAD cancellation."""

    pipe_name: str
    connect_timeout_ms: int = DEFAULT_CONNECT_TIMEOUT_MS
    io_timeout_ms: int = DEFAULT_IO_TIMEOUT_MS

    def __post_init__(self) -> None:
        if not isinstance(self.pipe_name, str) or not self.pipe_name.strip():
            raise ValueError("pipe_name must be a non-empty string")
        for name in ("connect_timeout_ms", "io_timeout_ms"):
            value = getattr(self, name)
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or not 0 < value <= 2_147_483_647
            ):
                raise ValueError(f"{name} must be a positive Windows timer integer")

    def round_trip(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        if sys.platform != "win32":
            raise RuntimeError("NamedPipeTransport is available only on Windows")

        import pywintypes
        import win32file
        import win32pipe

        # Validate/encode before opening a connection or causing any transport side effect.
        frame = encode_frame(payload)
        path = rf"\\.\pipe\{self.pipe_name}"
        deadline = time.monotonic() + self.connect_timeout_ms / 1000.0
        handle = None
        last_error = None
        while handle is None and time.monotonic() < deadline:
            remaining_ms = max(1, math.ceil((deadline - time.monotonic()) * 1000))
            try:
                win32pipe.WaitNamedPipe(path, min(remaining_ms, 250))
                handle = win32file.CreateFile(
                    path,
                    win32file.GENERIC_READ | win32file.GENERIC_WRITE,
                    0,
                    None,
                    win32file.OPEN_EXISTING,
                    win32file.FILE_FLAG_OVERLAPPED,
                    None,
                )
            except pywintypes.error as exc:
                last_error = exc
                winerror = getattr(exc, "winerror", exc.args[0] if exc.args else None)
                if winerror not in {2, 121, 231}:
                    raise RuntimeError("native bridge pipe is unavailable") from exc
                remaining = deadline - time.monotonic()
                if remaining > 0:
                    time.sleep(min(0.02, remaining))
        if handle is None:
            raise RuntimeError("native bridge pipe is unavailable") from last_error

        try:
            # One deadline covers write + header + body; progress cannot renew the budget.
            io_deadline = time.monotonic() + self.io_timeout_ms / 1000.0
            written = self._io(win32file, handle, frame, io_deadline)
            if written != len(frame):
                raise RuntimeError("native bridge pipe write was incomplete")

            header = self._read_exact(win32file, handle, 4, io_deadline)
            (length,) = struct.unpack("<I", header)
            if length == 0:
                raise BridgeProtocolError("INVALID_FRAME_LENGTH", "response frame is empty")
            if length > MAX_FRAME_BYTES:
                raise BridgeProtocolError("FRAME_TOO_LARGE", "response frame exceeds limit")
            body = self._read_exact(win32file, handle, length, io_deadline)
            return decode_frame(io.BytesIO(header + body))
        finally:
            win32file.CloseHandle(handle)

    @staticmethod
    def _io(win32file: Any, handle: Any, data: bytes | int, deadline: float) -> bytes | int:
        """Retain buffers/OVERLAPPED until local I/O completes, including cancellation drain."""
        import pywintypes
        import win32event

        if time.monotonic() >= deadline:
            raise NativePipeDeadlineError(
                "native bridge I/O deadline exceeded; completion is unknown"
            )
        overlapped = pywintypes.OVERLAPPED()
        event = win32event.CreateEvent(None, True, False, None)
        overlapped.hEvent = event
        pending = False
        buffer = win32file.AllocateReadBuffer(data) if isinstance(data, int) else data
        try:
            if isinstance(data, int):
                status, _ = win32file.ReadFile(handle, buffer, overlapped)
            else:
                status, _ = win32file.WriteFile(handle, buffer, overlapped)
            pending = status == _ERROR_IO_PENDING
            if status not in (0, _ERROR_IO_PENDING):
                raise RuntimeError("native bridge pipe I/O failed")
            if pending:
                remaining_ms = max(0, math.ceil((deadline - time.monotonic()) * 1000))
                wait = win32event.WaitForSingleObject(event, remaining_ms)
                if wait == win32event.WAIT_TIMEOUT:
                    raise NativePipeDeadlineError(
                        "native bridge I/O deadline exceeded; completion is unknown"
                    )
                if wait != win32event.WAIT_OBJECT_0:
                    raise RuntimeError("native bridge pipe I/O wait failed")
            count = win32file.GetOverlappedResult(handle, overlapped, False)
            pending = False
            return bytes(buffer[:count]) if isinstance(data, int) else count
        finally:
            try:
                if pending:
                    # This I/O was issued on this thread. CancelIo is supported by the
                    # pinned pywin32; it cancels local transport I/O, not native CAD work.
                    try:
                        win32file.CancelIo(handle)
                    finally:
                        try:
                            win32file.GetOverlappedResult(handle, overlapped, True)
                        except pywintypes.error:
                            pass  # terminal cancellation/broken-pipe result, buffer is now safe
            finally:
                win32file.CloseHandle(event)

    @classmethod
    def _read_exact(cls, win32file: Any, handle: Any, size: int, deadline: float) -> bytes:
        chunks: list[bytes] = []
        remaining = size
        while remaining:
            chunk = cls._io(win32file, handle, remaining, deadline)
            if not chunk:
                raise BridgeProtocolError("TRUNCATED_FRAME", "pipe response ended early")
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)
