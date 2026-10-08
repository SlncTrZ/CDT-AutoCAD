from __future__ import annotations

from pathlib import Path

import pytest

from cdt_autocad.config import Settings


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        allowed_paths=(tmp_path.resolve(),),
        max_dxf_bytes=5 * 1024 * 1024,
        call_timeout_seconds=5.0,
        auth_token="",
        allow_remote_http=False,
    )
