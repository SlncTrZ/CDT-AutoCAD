"""MP-2 runtime identity — generation/build/policy and bridge status contract.
Wing: code | Topic: mp2-hot-reload | Updated: 2026-09-11 09:12
"""

from __future__ import annotations

import subprocess
from dataclasses import replace

import pytest
from fastmcp import Client

import cdt_autocad.runtime_identity as runtime_identity
from cdt_autocad.runtime_identity import RuntimeIdentity, policy_fingerprint
from cdt_autocad.server import create_mcp


def test_policy_fingerprint_is_stable_without_exposing_auth_value():
    common = {
        "CDT_AUTOCAD_ALLOWED_PATHS": "/tmp/a:/tmp/b",
        "CDT_AUTOCAD_BACKEND": "ezdxf",
        "CDT_AUTOCAD_ALLOW_REMOTE_HTTP": "false",
        "CDT_AUTOCAD_AUTH_TOKEN": "first-secret",
    }
    changed_secret = {**common, "CDT_AUTOCAD_AUTH_TOKEN": "second-secret"}
    changed_root = {**common, "CDT_AUTOCAD_ALLOWED_PATHS": "/tmp/c"}

    assert policy_fingerprint(common) == policy_fingerprint(changed_secret)
    assert policy_fingerprint(common) != policy_fingerprint(changed_root)


def test_bridge_probe_timeout_is_bounded_and_reports_busy_or_modal(monkeypatch):
    monkeypatch.setattr(runtime_identity.sys, "platform", "win32")
    monkeypatch.setenv("CDT_AUTOCAD_REQUIRE_NATIVE_BRIDGE", "1")
    monkeypatch.setattr(runtime_identity, "_current_windows_session_id", lambda: 1)
    monkeypatch.setattr(runtime_identity, "_autocad_sessions", lambda: {1})
    monkeypatch.setattr(
        runtime_identity,
        "bridge_build_identity",
        lambda: {"available": True, "sha256": "bridge-hash"},
    )

    def timeout_probe(*_args, **kwargs):
        assert 0 < float(kwargs["timeout"]) <= 1.75
        raise subprocess.TimeoutExpired(cmd="bridge-probe", timeout=kwargs["timeout"])

    monkeypatch.setattr(runtime_identity.subprocess, "run", timeout_probe)

    result = runtime_identity.probe_native_bridge_status(timeout_ms=500)

    assert result["required"] is True
    assert result["ready"] is False
    assert result["pending_recovery_count"] == 0
    assert result["session_id"] == 1
    assert result["reason"] == "autocad_busy_or_modal"


def test_runtime_identity_reads_supervisor_generation(monkeypatch):
    monkeypatch.setenv("CDT_AUTOCAD_RUNTIME_GENERATION", "g000042")
    monkeypatch.setenv("CDT_AUTOCAD_RUNTIME_BUILD_ID", "sha256:build")
    monkeypatch.setenv("CDT_AUTOCAD_RUNTIME_POLICY_FP", "sha256:policy")
    monkeypatch.setenv("CDT_AUTOCAD_RUNTIME_SUPERVISED", "1")

    identity = RuntimeIdentity.from_env()

    assert identity.generation == "g000042"
    assert identity.provider_build_id == "sha256:build"
    assert identity.policy_fingerprint == "sha256:policy"
    assert identity.supervised is True


@pytest.mark.asyncio
async def test_system_status_exposes_generation_build_and_policy(monkeypatch, settings):
    monkeypatch.setenv("CDT_AUTOCAD_RUNTIME_GENERATION", "g000007")
    monkeypatch.setenv("CDT_AUTOCAD_RUNTIME_BUILD_ID", "sha256:provider-build")
    monkeypatch.setenv("CDT_AUTOCAD_RUNTIME_POLICY_FP", "sha256:policy")
    monkeypatch.setenv("CDT_AUTOCAD_RUNTIME_SUPERVISED", "1")
    monkeypatch.setattr(
        runtime_identity,
        "bridge_build_identity",
        lambda: {"available": False, "sha256": None},
    )
    monkeypatch.setattr(
        runtime_identity,
        "probe_native_bridge_status",
        lambda **_kwargs: {
            "required": False,
            "ready": False,
            "pending_recovery_count": 0,
            "bridge_version": None,
            "protocol_version": None,
            "reason": "test_fixture",
        },
    )
    app = create_mcp(replace(settings, backend="ezdxf"))

    async with Client(app) as client:
        result = await client.call_tool("system_status", {})

    payload = result.structured_content or {}
    assert payload["runtime_generation"] == "g000007"
    assert payload["provider_build"]["id"] == "sha256:provider-build"
    assert payload["runtime_policy_fingerprint"] == "sha256:policy"
    assert payload["reload_supervised"] is True
    assert payload["supported_profile"] == "headless-ezdxf"
    assert payload["pending_recovery_count"] == 0
    assert payload["bridge_build"]["available"] is False


@pytest.mark.asyncio
async def test_diagnostics_use_runtime_generation(monkeypatch, settings):
    class RecordingSink:
        def __init__(self):
            self.events = []

        def emit(self, event):
            self.events.append(event)

    monkeypatch.setenv("CDT_AUTOCAD_RUNTIME_GENERATION", "g000009")
    monkeypatch.setenv("CDT_AUTOCAD_RUNTIME_BUILD_ID", "sha256:provider-build")
    sink = RecordingSink()
    app = create_mcp(replace(settings, backend="ezdxf"), diagnostic_sink=sink)

    async with Client(app) as client:
        await client.call_tool("system_status", {})

    assert sink.events
    assert {event.generation for event in sink.events} == {"g000009"}
