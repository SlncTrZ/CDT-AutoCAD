"""Native bridge transport — deterministic pipe naming and platform guard.
Wing: code | Topic: native-bridge-n3 | Updated: 2026-09-10 11:08
"""

from __future__ import annotations

import pytest

import cdt_autocad.native_bridge.transport_windows as transport_windows
from cdt_autocad.native_bridge.transport_windows import NamedPipeTransport, pipe_name_for_session


def test_pipe_name_is_bound_to_windows_session():
    assert pipe_name_for_session(1) == "SlncTrZ.CDT.AutoCAD.Bridge.v1.s1"
    assert pipe_name_for_session(0) == "SlncTrZ.CDT.AutoCAD.Bridge.v1.s0"


def test_pipe_name_rejects_invalid_session_id():
    for value in (-1, "1", None):
        with pytest.raises(ValueError):
            pipe_name_for_session(value)  # type: ignore[arg-type]


def test_transport_validates_constructor():
    with pytest.raises(ValueError):
        NamedPipeTransport("")
    with pytest.raises(ValueError):
        NamedPipeTransport("pipe", 0)


def test_non_windows_round_trip_fails_before_transport_import(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(transport_windows.sys, "platform", "linux")
    transport = NamedPipeTransport("test")
    with pytest.raises(RuntimeError, match="only on Windows"):
        transport.round_trip({})


@pytest.mark.skipif(
    transport_windows.sys.platform != "win32", reason="requires real Windows named pipes"
)
@pytest.mark.parametrize(
    "reply_mode", ["silent", "partial_body", "drip_header", "blocked_write", "fragmented"]
)
def test_real_pipe_reply_has_one_deadline_and_closes_after_timeout(reply_mode):
    """A peer that stalls or drips bytes must not retain a native-call worker indefinitely."""
    import struct
    import threading
    import time
    from uuid import uuid4

    import win32file
    import win32pipe

    from cdt_autocad.native_bridge.protocol import encode_frame

    name = "CDT.AutoCAD.TransportTest." + uuid4().hex
    ready = threading.Event()
    stop = threading.Event()
    errors = []
    response = encode_frame({"status": "ok"})

    def serve():
        handle = None
        try:
            handle = win32pipe.CreateNamedPipe(
                rf"\\.\pipe\{name}",
                win32pipe.PIPE_ACCESS_DUPLEX,
                win32pipe.PIPE_TYPE_BYTE | win32pipe.PIPE_READMODE_BYTE | win32pipe.PIPE_WAIT,
                1,
                1024,
                1024,
                0,
                None,
            )
            ready.set()
            win32pipe.ConnectNamedPipe(handle, None)

            if reply_mode == "blocked_write":
                stop.wait(1.0)
                return

            def read_exact(size):
                data = b""
                while len(data) < size:
                    _, chunk = win32file.ReadFile(handle, size - len(data))
                    data += chunk
                return data

            (size,) = struct.unpack("<I", read_exact(4))
            read_exact(size)
            if reply_mode == "silent":
                stop.wait(1.0)
            elif reply_mode == "partial_body":
                win32file.WriteFile(handle, response[:5])
                stop.wait(1.0)
            elif reply_mode == "drip_header":
                for value in response[:4]:
                    if stop.wait(0.06):
                        break
                    win32file.WriteFile(handle, bytes([value]))
                stop.wait(1.0)
            else:
                for piece in (response[:2], response[2:4], response[4:8], response[8:]):
                    win32file.WriteFile(handle, piece)
        except Exception as exc:
            errors.append(exc)
        finally:
            if handle is not None:
                win32file.CloseHandle(handle)

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    assert ready.wait(2.0)
    # Keep this test runnable on the pre-fix source to reproduce the actual stalled read.
    kwargs = (
        {"io_timeout_ms": 100} if "io_timeout_ms" in NamedPipeTransport.__dataclass_fields__ else {}
    )
    started = time.monotonic()
    try:
        transport = NamedPipeTransport(name, connect_timeout_ms=1000, **kwargs)
        if reply_mode == "fragmented":
            assert transport.round_trip({"ping": True}) == {"status": "ok"}
        else:
            with pytest.raises(TimeoutError):
                transport.round_trip(
                    {"data": "x" * 16384} if reply_mode == "blocked_write" else {"ping": True}
                )
            assert time.monotonic() - started < 0.8
    finally:
        stop.set()
        thread.join(2.0)
    assert not thread.is_alive()
    # A slow writer may observe peer disconnect once the client's deadline expires.
    if reply_mode != "drip_header":
        assert not errors


@pytest.mark.asyncio
async def test_native_pipe_deadline_quarantines_shared_writer():
    from cdt_autocad.errors import BackendQuarantinedError, MutationCompletionUncertainError
    from cdt_autocad.mutation_coordinator import MutationCoordinator
    from cdt_autocad.native_bridge.transport_windows import NativePipeDeadlineError
    from cdt_autocad.server import _run_native_mutation

    coordinator = MutationCoordinator()
    later_side_effects = []

    def lost_response():
        raise NativePipeDeadlineError("native bridge I/O deadline exceeded; completion is unknown")

    with pytest.raises(MutationCompletionUncertainError):
        await _run_native_mutation(coordinator, lost_response)
    assert coordinator.status()["quarantine_lane"] == "native"
    for lane in ("com", "native"):
        with pytest.raises(BackendQuarantinedError):
            async with coordinator.writer(lane):
                later_side_effects.append(lane)
    assert later_side_effects == []


@pytest.mark.parametrize("timer", ["connect_timeout_ms", "io_timeout_ms"])
@pytest.mark.parametrize("value", [True, False, 0, -1, 1.5, None, 2147483648])
def test_transport_rejects_invalid_timer_before_io(timer, value):
    with pytest.raises(ValueError):
        NamedPipeTransport("pipe", **{timer: value})
