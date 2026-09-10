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
