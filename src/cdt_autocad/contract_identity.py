"""Public contract identity — one source for protocol/version/guide fingerprint.
Wing: code | Topic: mp2-hot-reload | Updated: 2026-09-11 09:40
"""

from __future__ import annotations

import hashlib
from importlib import resources
from pathlib import Path

PROTOCOL_VERSION = "MCP"
CONTRACT_VERSION = "autocad-generic-v1-rc1"
COMMON_CONTRACT_VERSION = "cdt-common-v1-draft"
UPDATED_AT = "2026-09-11"
PUBLIC_TOOL_COUNT = 86
EXECUTION_MODEL = "feature-based-chunks-streaming-v1"


def guide_content() -> str:
    """Load the canonical public tool guide from source or packaged wheel data."""
    source_path = Path(__file__).resolve().parents[2] / "docs" / "TOOL_GUIDE.md"
    if source_path.is_file():
        return source_path.read_text(encoding="utf-8")
    packaged = resources.files("cdt_autocad").joinpath("docs").joinpath("TOOL_GUIDE.md")
    return packaged.read_text(encoding="utf-8")


def contract_material() -> tuple[str, str]:
    """Return canonical guide content and its SHA-256 contract fingerprint from one read."""
    content = guide_content()
    return content, hashlib.sha256(content.encode("utf-8")).hexdigest()


def contract_hash() -> str:
    """Return the canonical public contract SHA-256 fingerprint."""
    return contract_material()[1]
