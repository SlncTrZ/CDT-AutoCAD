"""Runtime identity — supervised generation/build/policy and native bridge readiness metadata.
Wing: code | Topic: mp2-hot-reload | Updated: 2026-09-11 09:18
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

_POLICY_KEYS = (
    "CDT_AUTOCAD_ALLOWED_PATHS",
    "CDT_AUTOCAD_BACKEND",
    "CDT_AUTOCAD_ALLOW_REMOTE_HTTP",
    "CDT_AUTOCAD_COM_PROGID",
    "CDT_AUTOCAD_COM_ATTACH_POLICY",
    "CDT_AUTOCAD_MAX_DXF_BYTES",
    "CDT_AUTOCAD_CALL_TIMEOUT",
    "CDT_AUTOCAD_RENDER_TIMEOUT",
    "CDT_AUTOCAD_UNDO_DEPTH",
    "CDT_AUTOCAD_TRANSACTION_DEPTH",
    "CDT_AUTOCAD_COM_TIMEOUT",
)


def _bool_env(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def policy_fingerprint(env: Mapping[str, str]) -> str:
    """Hash non-secret policy/config identity while representing auth only by configured/not-configured."""

    payload = {key: str(env.get(key, "")) for key in _POLICY_KEYS}
    payload["CDT_AUTOCAD_AUTH_CONFIGURED"] = bool(str(env.get("CDT_AUTOCAD_AUTH_TOKEN", "")))
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _bridge_dll_path() -> Path | None:
    configured = os.environ.get("CDT_AUTOCAD_BRIDGE_DLL", "").strip()
    if configured:
        path = Path(configured).expanduser()
        return path if path.is_file() else None
    if sys.platform != "win32":
        return None
    appdata = os.environ.get("APPDATA", "").strip()
    if not appdata:
        return None
    path = (
        Path(appdata)
        / "Autodesk"
        / "ApplicationPlugins"
        / "CDT.AutoCAD.Bridge.bundle"
        / "Contents"
        / "Windows"
        / "CDT.AutoCAD.Bridge.dll"
    )
    return path if path.is_file() else None


def bridge_build_identity() -> dict[str, Any]:
    path = _bridge_dll_path()
    if path is None:
        return {"available": False, "sha256": None}
    return {"available": True, "sha256": _sha256_file(path)}


def _current_windows_session_id() -> int:
    if sys.platform != "win32":
        raise RuntimeError("Windows session identity is available only on Windows")
    session_id = ctypes.c_uint32()
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    if not kernel32.ProcessIdToSessionId(os.getpid(), ctypes.byref(session_id)):
        raise RuntimeError("unable to resolve current Windows session")
    return int(session_id.value)


def _autocad_sessions() -> set[int] | None:
    if sys.platform != "win32":
        return set()
    try:
        import win32ts

        sessions: set[int] = set()
        for session_id, _pid, process_name, _sid in win32ts.WTSEnumerateProcesses(None, 1, 0):
            if str(process_name).lower() == "acad.exe":
                sessions.add(int(session_id))
        return sessions
    except Exception:
        return None


def probe_native_bridge_status(*, timeout_ms: int = 500) -> dict[str, Any]:
    """Bounded staged bridge probe; a modal/busy bridge cannot block system_status indefinitely."""

    required = _bool_env(os.environ.get("CDT_AUTOCAD_REQUIRE_NATIVE_BRIDGE"))
    base = {
        "required": required,
        "ready": False,
        "pending_recovery_count": 0,
        "bridge_version": None,
        "protocol_version": None,
    }
    if sys.platform != "win32":
        return {**base, "reason": "not_windows"}

    try:
        session_id = _current_windows_session_id()
    except Exception:
        return {**base, "reason": "session_identity_unavailable"}

    sessions = _autocad_sessions()
    if sessions is not None:
        if not sessions:
            return {**base, "session_id": session_id, "reason": "autocad_not_running"}
        if session_id not in sessions:
            return {
                **base,
                "session_id": session_id,
                "autocad_sessions": sorted(sessions),
                "reason": "autocad_different_session",
            }

    bridge_build = bridge_build_identity()
    if not bridge_build["available"]:
        return {**base, "session_id": session_id, "reason": "bridge_missing"}

    connect_timeout_ms = max(50, min(timeout_ms, 1_000))
    hard_timeout_seconds = max(0.25, connect_timeout_ms / 1000.0 + 0.75)
    try:
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "cdt_autocad.bridge_probe",
                "--session-id",
                str(session_id),
                "--connect-timeout-ms",
                str(connect_timeout_ms),
            ],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=hard_timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {**base, "session_id": session_id, "reason": "autocad_busy_or_modal"}
    except Exception:
        return {**base, "session_id": session_id, "reason": "bridge_probe_failed"}

    try:
        payload = json.loads(completed.stdout.strip() or "{}")
    except json.JSONDecodeError:
        return {**base, "session_id": session_id, "reason": "bridge_probe_invalid"}
    if completed.returncode != 0 or not payload.get("ok"):
        reason = (
            "protocol_mismatch"
            if payload.get("error_code") == "PROTOCOL_VERSION_MISMATCH"
            else "bridge_unavailable"
        )
        return {**base, "session_id": session_id, "reason": reason}

    from cdt_autocad.native_bridge.protocol import NATIVE_PROTOCOL_VERSION

    protocol = payload.get("protocol")
    if protocol != NATIVE_PROTOCOL_VERSION:
        return {
            **base,
            "session_id": session_id,
            "protocol_version": protocol,
            "reason": "protocol_mismatch",
        }
    return {
        **base,
        "ready": True,
        "pending_recovery_count": int(payload.get("pending_recovery_count") or 0),
        "bridge_version": payload.get("bridge_version"),
        "protocol_version": protocol,
        "session_id": session_id,
        "reason": None,
    }


@dataclass(frozen=True)
class RuntimeIdentity:
    generation: str
    provider_build_id: str
    policy_fingerprint: str
    supervised: bool

    @classmethod
    def from_env(cls) -> RuntimeIdentity:
        return cls(
            generation=os.environ.get("CDT_AUTOCAD_RUNTIME_GENERATION", "static").strip()
            or "static",
            provider_build_id=os.environ.get("CDT_AUTOCAD_RUNTIME_BUILD_ID", "unbound").strip()
            or "unbound",
            policy_fingerprint=os.environ.get(
                "CDT_AUTOCAD_RUNTIME_POLICY_FP", "unbound"
            ).strip()
            or "unbound",
            supervised=_bool_env(os.environ.get("CDT_AUTOCAD_RUNTIME_SUPERVISED")),
        )

    def status(self, *, backend_name: str) -> dict[str, Any]:
        bridge = bridge_build_identity()
        bridge_runtime = probe_native_bridge_status(
            timeout_ms=max(
                50,
                int(os.environ.get("CDT_AUTOCAD_BRIDGE_STATUS_TIMEOUT_MS", "500")),
            )
        )
        return {
            "runtime_generation": self.generation,
            "provider_build": {"id": self.provider_build_id},
            "runtime_policy_fingerprint": self.policy_fingerprint,
            "reload_supervised": self.supervised,
            "supported_profile": "com-live" if backend_name == "com" else "headless-ezdxf",
            "bridge_build": bridge,
            "native_bridge": bridge_runtime,
            "pending_recovery_count": int(bridge_runtime["pending_recovery_count"]),
        }
