"""Native bridge probe subprocess — bounded by the parent supervisor/status timeout.
Wing: code | Topic: mp2-hot-reload | Updated: 2026-09-11 10:18
"""

from __future__ import annotations

import argparse
import json

from .native_bridge.client import NativeBridgeClient
from .native_bridge.transport_windows import NamedPipeTransport, pipe_name_for_session


def main() -> None:
    parser = argparse.ArgumentParser(description="Probe the staged AutoCAD native bridge")
    parser.add_argument("--session-id", type=int, required=True)
    parser.add_argument("--connect-timeout-ms", type=int, default=300)
    args = parser.parse_args()
    try:
        client = NativeBridgeClient(
            NamedPipeTransport(
                pipe_name_for_session(args.session_id),
                connect_timeout_ms=args.connect_timeout_ms,
            )
        )
        health = client.health()
        recoveries = client.recoveries_list()
        payload = {
            "ok": True,
            "protocol": health.get("protocol"),
            "bridge_version": health.get("bridge_version"),
            "windows_session_id": health.get("windows_session_id"),
            "pending_recovery_count": len(recoveries),
        }
        print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    except Exception as exc:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error_type": type(exc).__name__,
                    "error_code": getattr(exc, "code", None),
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
