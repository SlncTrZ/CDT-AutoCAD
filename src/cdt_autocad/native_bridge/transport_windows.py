"""Windows Named Pipe transport — synchronous one-request-per-connection N3 client transport.
Wing: code | Topic: native-bridge-n3 | Updated: 2026-09-10 11:08
"""

from __future__ import annotations

import io
import struct
import sys
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .protocol import MAX_FRAME_BYTES, BridgeProtocolError, decode_frame, encode_frame

DEFAULT_CONNECT_TIMEOUT_MS = 5_000


def pipe_name_for_session(session_id: int) -> str:
    """Return the frozen N3 per-Windows-session pipe name."""

    if not isinstance(session_id, int) or session_id < 0:
        raise ValueError("session_id must be a non-negative integer")
    return f"SlncTrZ.CDT.AutoCAD.Bridge.v1.s{session_id}"


@dataclass(frozen=True)
class NamedPipeTransport:
    """Concrete Windows transport; protocol validation remains in NativeBridgeClient."""

    pipe_name: str
    connect_timeout_ms: int = DEFAULT_CONNECT_TIMEOUT_MS

    def __post_init__(self) -> None:
        if not isinstance(self.pipe_name, str) or not self.pipe_name.strip():
            raise ValueError("pipe_name must be a non-empty string")
        if not isinstance(self.connect_timeout_ms, int) or self.connect_timeout_ms <= 0:
            raise ValueError("connect_timeout_ms must be a positive integer")

    def round_trip(self, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        if sys.platform != "win32":
            raise RuntimeError("NamedPipeTransport is available only on Windows")

        import pywintypes
        import win32file
        import win32pipe

        path = rf"\\.\pipe\{self.pipe_name}"
        deadline = time.monotonic() + self.connect_timeout_ms / 1000.0
        handle = None
        last_error = None
        while handle is None and time.monotonic() < deadline:
            remaining_ms = max(1, int((deadline - time.monotonic()) * 1000))
            try:
                win32pipe.WaitNamedPipe(path, min(remaining_ms, 250))
                handle = win32file.CreateFile(
                    path,
                    win32file.GENERIC_READ | win32file.GENERIC_WRITE,
                    0,
                    None,
                    win32file.OPEN_EXISTING,
                    0,
                    None,
                )
            except pywintypes.error as exc:
                last_error = exc
                winerror = getattr(exc, "winerror", exc.args[0] if exc.args else None)
                if winerror not in {2, 121, 231}:
                    raise RuntimeError("native bridge pipe is unavailable") from exc
                time.sleep(0.02)
        if handle is None:
            raise RuntimeError("native bridge pipe is unavailable") from last_error

        try:
            frame = encode_frame(payload)
            _, written = win32file.WriteFile(handle, frame)
            if isinstance(written, int) and written != len(frame):
                raise RuntimeError("native bridge pipe write was incomplete")

            header = self._read_exact(win32file, handle, 4)
            (length,) = struct.unpack("<I", header)
            if length == 0:
                raise BridgeProtocolError("INVALID_FRAME_LENGTH", "response frame is empty")
            if length > MAX_FRAME_BYTES:
                raise BridgeProtocolError("FRAME_TOO_LARGE", "response frame exceeds limit")
            body = self._read_exact(win32file, handle, length)
            return decode_frame(io.BytesIO(header + body))
        finally:
            win32file.CloseHandle(handle)

    @staticmethod
    def _read_exact(win32file: Any, handle: Any, size: int) -> bytes:
        chunks: list[bytes] = []
        remaining = size
        while remaining:
            _, chunk = win32file.ReadFile(handle, remaining)
            if not chunk:
                raise BridgeProtocolError("TRUNCATED_FRAME", "pipe response ended early")
            chunks.append(bytes(chunk))
            remaining -= len(chunk)
        return b"".join(chunks)
