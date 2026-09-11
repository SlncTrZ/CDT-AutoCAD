"""MP0-T00 diagnostics tests — structured events and correlation propagation.
Wing: code | Topic: mp0-t00-observability | Updated: 2026-09-10 23:20
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from fastmcp import Client

from cdt_autocad.config import Settings
from cdt_autocad.diagnostics import DiagnosticEvent, DiagnosticSink
from cdt_autocad.server import create_mcp


@dataclass
class RecordingSink(DiagnosticSink):
    events: list[DiagnosticEvent] = field(default_factory=list)

    def emit(self, event: DiagnosticEvent) -> None:
        self.events.append(event)


class FailingSink(DiagnosticSink):
    def emit(self, event: DiagnosticEvent) -> None:
        raise OSError("telemetry sink unavailable")


@pytest.mark.asyncio
async def test_tool_call_emits_correlated_start_and_completion_events(tmp_path):
    sink = RecordingSink()
    app = create_mcp(
        Settings(allowed_paths=(tmp_path,)),
        diagnostic_sink=sink,
    )

    async with Client(app) as client:
        result = await client.call_tool("system_status", {})

    assert result.is_error is False
    events = [event for event in sink.events if event.operation == "system_status"]
    assert [event.event_type for event in events] == ["tool.started", "tool.completed"]
    assert events[0].request_id == events[1].request_id
    assert events[0].request_id
    assert all(event.backend == "ezdxf" for event in events)
    assert all(event.provider_version for event in events)
    assert events[1].outcome == "success"
    assert events[1].latency_ms is not None
    assert events[1].latency_ms >= 0


@pytest.mark.asyncio
async def test_telemetry_sink_failure_does_not_fail_tool_call(tmp_path):
    app = create_mcp(
        Settings(allowed_paths=(tmp_path,)),
        diagnostic_sink=FailingSink(),
    )

    async with Client(app) as client:
        result = await client.call_tool("system_status", {})

    assert result.is_error is False
    assert result.structured_content["provider"] == "autocad"
