"""Bounded output envelope — bytes/entities budgets, paged reads, token metadata.
Wing: code | Topic: payload-envelope-p03 | Updated: 2026-10-07 11:45
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from cdt_autocad.semantic.canonical import canonical_json

ENVELOPE_VERSION = 1
SNAPSHOT_ENTITIES_MAX = 12_288
SNAPSHOT_BYTES_MAX = 262_144
DELTA_PIDS_MAX = 10_000
# Normative AC-P03 budgets. These mirror native_bridge.protocol
# (MAX_BATCH_CHUNK_ENTITIES / MAX_FRAME_BYTES / MAX_METADATA_QUERY_RESULTS);
# test_payload_envelope_p03 asserts the mirror stays in sync without importing
# the bridge package here (which would circular-import via its __init__).
AFFECTED_CHUNK_ENTITIES = 32
GATEWAY_FRAME_BYTES_MAX = 65_536
METADATA_QUERY_RESULTS_MAX = 1_000


def estimated_tokens(byte_size: int) -> int:
    """Metadata-only size heuristic (bytes/4); never billing, limit or refusal input."""

    if isinstance(byte_size, bool) or not isinstance(byte_size, int) or byte_size < 0:
        raise ValueError("byte_size must be a non-negative integer")
    return byte_size // 4


def canonical_byte_size(payload: Any) -> int:
    """Measure canonical JSON bytes before returning a payload (no silent truncation)."""

    return len(canonical_json(payload).encode("utf-8"))


class SnapshotOversizedError(ValueError):
    """Typed full-snapshot refusal; callers continue with paged reads via next_cursor."""

    code = "SNAPSHOT_OVERSIZED"

    def __init__(
        self,
        *,
        document_fp: str,
        entity_count: int,
        byte_size: int,
        limit: int,
        next_cursor: Mapping[str, Any],
    ):
        self.document_fp = document_fp
        self.entity_count = entity_count
        self.byte_size = byte_size
        self.limit = limit
        self.next_cursor = dict(next_cursor)
        super().__init__(
            f"{self.code}: snapshot has {entity_count} entities / {byte_size} bytes; "
            f"limit is {SNAPSHOT_ENTITIES_MAX} entities / {SNAPSHOT_BYTES_MAX} bytes; "
            f"continue from {dict(next_cursor)}"
        )


class DeltaOversizedError(ValueError):
    """Typed delta refusal; callers narrow scope instead of receiving a truncated delta."""

    code = "DELTA_OVERSIZED"

    def __init__(
        self,
        *,
        document_fp: str,
        entity_count: int,
        byte_size: int,
        limit: int,
        next_cursor: Mapping[str, Any],
    ):
        self.document_fp = document_fp
        self.entity_count = entity_count
        self.byte_size = byte_size
        self.limit = limit
        self.next_cursor = dict(next_cursor)
        super().__init__(
            f"{self.code}: delta has {entity_count} pids / {byte_size} bytes; "
            f"limit is {DELTA_PIDS_MAX} pids; "
            f"continue from {dict(next_cursor)}"
        )


def guard_full_snapshot(
    *,
    document_fp: str,
    entity_count: int,
    byte_size: int,
    limit: int = SNAPSHOT_ENTITIES_MAX,
) -> None:
    """Refuse an oversized full snapshot; never silently truncate it."""

    if entity_count > SNAPSHOT_ENTITIES_MAX or byte_size > SNAPSHOT_BYTES_MAX:
        raise SnapshotOversizedError(
            document_fp=document_fp,
            entity_count=entity_count,
            byte_size=byte_size,
            limit=limit,
            next_cursor={"offset": 0, "limit": min(int(limit), SNAPSHOT_ENTITIES_MAX)},
        )


def guard_delta(
    *,
    document_fp: str,
    pid_count: int,
    byte_size: int = 0,
    limit: int = DELTA_PIDS_MAX,
) -> None:
    """Refuse an oversized delta; never silently drop PIDs from it."""

    if pid_count > DELTA_PIDS_MAX:
        raise DeltaOversizedError(
            document_fp=document_fp,
            entity_count=pid_count,
            byte_size=byte_size,
            limit=limit,
            next_cursor={"offset": 0, "limit": min(int(limit), DELTA_PIDS_MAX)},
        )


def validate_page_args(offset: int, limit: int, *, max_limit: int) -> tuple[int, int]:
    """Validate paged-read bounds before any state is serialized."""

    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ValueError("offset must be an integer >= 0")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= max_limit:
        raise ValueError(f"limit must be an integer in 1..{max_limit}")
    return offset, limit


def slice_page(
    items: Sequence[Any], *, offset: int, limit: int, max_limit: int
) -> tuple[list[Any], bool, int | None]:
    """Slice one page; returns (page, complete_if_source_exhausted, next_offset)."""

    offset, limit = validate_page_args(offset, limit, max_limit=max_limit)
    page = list(items[offset : offset + limit])
    exhausted = offset + len(page) >= len(items)
    return page, exhausted, None if exhausted else offset + len(page)


def build_paged_envelope(
    *,
    items: Sequence[Any],
    offset: int,
    limit: int,
    matched: int,
    source: int,
) -> dict[str, Any]:
    """Build the mandatory paged-response envelope around an already-sliced page.

    Required keys: items/count/offset/limit/matched/source/complete/next_offset.
    matched_count/source_count are compat aliases; envelope_version/byte_size/
    estimated_tokens are additive metadata only.
    """

    page = list(items)
    count = len(page)
    complete = offset + count >= matched
    next_offset = None if complete else offset + count
    page_bytes = canonical_byte_size(page)
    return {
        "envelope_version": ENVELOPE_VERSION,
        "items": page,
        "count": count,
        "offset": int(offset),
        "limit": int(limit),
        "matched": int(matched),
        "source": int(source),
        "matched_count": int(matched),
        "source_count": int(source),
        "complete": bool(complete),
        "next_offset": next_offset,
        "byte_size": page_bytes,
        "estimated_tokens": estimated_tokens(page_bytes),
    }
