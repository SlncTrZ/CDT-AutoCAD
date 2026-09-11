"""Supervised FastMCP worker — stateless localhost process owned by the MP-2 supervisor.
Wing: code | Topic: mp2-hot-reload | Updated: 2026-09-11 09:36
"""

from __future__ import annotations

import argparse

from .config import Settings
from .server import _validate_http_launch, create_mcp

_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one supervised CDT AutoCAD worker generation")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    if args.host not in _LOOPBACK_HOSTS:
        raise SystemExit("supervised worker must bind loopback only")
    settings = Settings.from_env()
    _validate_http_launch(settings, args.host)
    app = create_mcp(settings)
    app.run(
        transport="streamable-http",
        host=args.host,
        port=args.port,
        stateless_http=True,
        json_response=True,
        show_banner=False,
    )


if __name__ == "__main__":
    main()
