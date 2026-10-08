"""AC-P03 bounded output envelope — oversized refusal, paging, compat, frame, stray.
Covers the owner-approved review: compact state default, full snapshot opt-in,
affected default post-commit, paged detail reads, bytes/entities bounds, metadata-only
token estimates, typed oversized refusals, no silent truncation, additive-only compat,
64 KiB gateway frame and stray capture at rolling pre-extract + finalize.
Wing: code | Topic: payload-envelope-p03 | Updated: 2026-10-07 11:46
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy

import pytest
from fastmcp import Client

from cdt_autocad.envelope import (
    AFFECTED_CHUNK_ENTITIES,
    DELTA_PIDS_MAX,
    ENVELOPE_VERSION,
    GATEWAY_FRAME_BYTES_MAX,
    METADATA_QUERY_RESULTS_MAX,
    SNAPSHOT_BYTES_MAX,
    SNAPSHOT_ENTITIES_MAX,
    DeltaOversizedError,
    SnapshotOversizedError,
    build_paged_envelope,
    canonical_byte_size,
    estimated_tokens,
    guard_delta,
    guard_full_snapshot,
)
from cdt_autocad.native_bridge.client import NativeBridgeClient
from cdt_autocad.native_bridge.logical_batch import NativeLogicalBatchExecutor
from cdt_autocad.native_bridge.protocol import (
    MAX_BATCH_CHUNK_ENTITIES,
    MAX_FRAME_BYTES,
    MAX_METADATA_QUERY_RESULTS,
    NATIVE_PROTOCOL_VERSION,
    encode_frame,
)
from cdt_autocad.semantic.fingerprint import (
    DOCUMENT_FINGERPRINT_SCHEMA_VERSION,
    fingerprint_document,
)
from cdt_autocad.semantic.models import EntitySemanticState, SemanticSnapshot
from cdt_autocad.server import create_mcp

RUNTIME_ID = "11111111-1111-4111-8111-111111111111"
DOCUMENT_PID = "doc:22222222-2222-4222-8222-222222222222"
PARENT_FP = "sha256:" + "a" * 64
CHECKPOINT_ID = "cp:66666666-6666-4666-8666-666666666666"
CHECKPOINT_ART_FP = "sha256:" + "d" * 64

PAGED_KEYS = {
    "items",
    "count",
    "offset",
    "limit",
    "matched",
    "source",
    "complete",
    "next_offset",
}


def _entity(index: int, *, style_note: str | None = None) -> EntitySemanticState:
    style: dict[str, object] = {}
    if style_note is not None:
        style = {"note": style_note}
    return EntitySemanticState(
        semantic_pid=f"pid:test-{index:05d}",
        native_handle=None,
        entity_type="LINE",
        layer="0",
        geometry={"start": [float(index), 0.0, 0.0], "end": [float(index) + 1.0, 0.0, 0.0]},
        style=style,
        metadata={},
    )


def _snapshot(count: int, *, style_note: str | None = None) -> SemanticSnapshot:
    snapshot = SemanticSnapshot(
        snapshot_id="snap:envelope-test",
        document_pid=DOCUMENT_PID,
        units="mm",
        current_space="Model",
        saved=True,
        entities=tuple(_entity(index, style_note=style_note) for index in range(count)),
    )
    return SemanticSnapshot(
        snapshot_id=snapshot.snapshot_id,
        document_pid=snapshot.document_pid,
        units=snapshot.units,
        current_space=snapshot.current_space,
        saved=snapshot.saved,
        entities=snapshot.entities,
        relations=snapshot.relations,
        styles=snapshot.styles,
        extents=snapshot.extents,
        fingerprints=snapshot.fingerprints,
    )


def _wire_snapshot(snapshot: SemanticSnapshot) -> dict:
    document_fp = fingerprint_document(snapshot)
    return {
        "schema_version": 1,
        "document_fp_schema_version": DOCUMENT_FINGERPRINT_SCHEMA_VERSION,
        "runtime_document_id": RUNTIME_ID,
        "document_pid": snapshot.document_pid,
        "document": {
            "units": snapshot.units,
            "current_space": snapshot.current_space,
            "saved": snapshot.saved,
            "extents": None,
        },
        "entities": [
            {
                "semantic_pid": entity.semantic_pid,
                "native_handle": None,
                "entity_type": entity.entity_type,
                "layer": entity.layer,
                "geometry": dict(entity.geometry),
                "bbox": None,
                "metrics": None,
                "style": dict(entity.style or {}),
                "hierarchy": None,
                "metadata": {},
            }
            for entity in snapshot.entities
        ],
        "relations": [],
        "styles": [],
        "document_fp": document_fp,
    }


class _SnapshotTransport:
    def __init__(self, wire: dict):
        self.wire = wire
        self.requests: list[dict] = []

    def round_trip(self, payload: Mapping) -> Mapping:
        self.requests.append(deepcopy(dict(payload)))
        return {
            "protocol": NATIVE_PROTOCOL_VERSION,
            "request_id": payload["request_id"],
            "ok": True,
            "result": deepcopy(self.wire),
        }


class _MetadataTransport:
    """Native-shaped metadata.query peer honoring the wire limit with a truncated flag."""

    def __init__(self, total: int):
        self.matches = [
            {"semantic_pid": f"pid:meta-{index:04d}", "value": {"tag": index}}
            for index in range(total)
        ]
        self.requests: list[dict] = []

    def round_trip(self, payload: Mapping) -> Mapping:
        self.requests.append(deepcopy(dict(payload)))
        params = payload["params"]
        limit = int(params["limit"])
        kept = self.matches[:limit]
        return {
            "protocol": NATIVE_PROTOCOL_VERSION,
            "request_id": payload["request_id"],
            "ok": True,
            "result": {
                "schema_version": 1,
                "document_pid": DOCUMENT_PID,
                "namespace": params["namespace"],
                "path": params.get("path"),
                "count": len(kept),
                "scanned_entities": len(self.matches),
                "limit": limit,
                "truncated": len(self.matches) > limit,
                "items": deepcopy(kept),
            },
        }


def test_envelope_bounds_are_normative() -> None:
    assert SNAPSHOT_ENTITIES_MAX == 12_288
    assert SNAPSHOT_BYTES_MAX == 262_144
    assert DELTA_PIDS_MAX == 10_000
    assert AFFECTED_CHUNK_ENTITIES == MAX_BATCH_CHUNK_ENTITIES == 32
    assert GATEWAY_FRAME_BYTES_MAX == MAX_FRAME_BYTES == 65_536
    assert METADATA_QUERY_RESULTS_MAX == MAX_METADATA_QUERY_RESULTS == 1_000
    assert ENVELOPE_VERSION == 1


def test_full_snapshot_within_bounds_passes_unchanged() -> None:
    snapshot = _snapshot(3)
    expected_fp = fingerprint_document(snapshot)
    transport = _SnapshotTransport(_wire_snapshot(snapshot))
    client = NativeBridgeClient(transport)

    result = client.document_snapshot(RUNTIME_ID, document_pid=DOCUMENT_PID)

    assert result.fingerprints.document_fp == expected_fp
    assert [entity.semantic_pid for entity in result.entities] == [
        entity.semantic_pid for entity in snapshot.entities
    ]


def test_snapshot_oversized_by_entities_refuses_typed() -> None:
    snapshot = _snapshot(SNAPSHOT_ENTITIES_MAX + 1)
    transport = _SnapshotTransport(_wire_snapshot(snapshot))
    client = NativeBridgeClient(transport)

    with pytest.raises(SnapshotOversizedError) as exc_info:
        client.document_snapshot(RUNTIME_ID, document_pid=DOCUMENT_PID)

    error = exc_info.value
    assert error.code == "SNAPSHOT_OVERSIZED"
    assert error.entity_count == SNAPSHOT_ENTITIES_MAX + 1
    assert error.byte_size > 0
    assert error.limit == SNAPSHOT_ENTITIES_MAX
    assert set(error.next_cursor) == {"offset", "limit"}
    assert "SNAPSHOT_OVERSIZED" in str(error)


def test_snapshot_oversized_by_bytes_refuses_typed() -> None:
    snapshot = _snapshot(1000, style_note="x" * 300)
    byte_size = canonical_byte_size(snapshot.to_dict())
    assert len(snapshot.entities) <= SNAPSHOT_ENTITIES_MAX
    assert byte_size > SNAPSHOT_BYTES_MAX
    transport = _SnapshotTransport(_wire_snapshot(snapshot))
    client = NativeBridgeClient(transport)

    with pytest.raises(SnapshotOversizedError) as exc_info:
        client.document_snapshot(RUNTIME_ID, document_pid=DOCUMENT_PID)

    error = exc_info.value
    assert error.code == "SNAPSHOT_OVERSIZED"
    assert error.entity_count == 1000
    assert error.byte_size > SNAPSHOT_BYTES_MAX
    assert error.document_fp == fingerprint_document(snapshot)


def test_delta_oversized_refuses_typed() -> None:
    with pytest.raises(DeltaOversizedError) as exc_info:
        guard_delta(
            document_fp=PARENT_FP,
            pid_count=DELTA_PIDS_MAX + 1,
            byte_size=1024,
        )

    error = exc_info.value
    assert error.code == "DELTA_OVERSIZED"
    assert error.document_fp == PARENT_FP
    assert error.entity_count == DELTA_PIDS_MAX + 1
    assert error.byte_size == 1024
    assert error.limit == DELTA_PIDS_MAX
    assert set(error.next_cursor) == {"offset", "limit"}

    guard_delta(document_fp=PARENT_FP, pid_count=DELTA_PIDS_MAX, byte_size=10**9)
    guard_full_snapshot(document_fp=PARENT_FP, entity_count=10, byte_size=10)


def test_estimated_tokens_is_metadata_only_and_never_refuses() -> None:
    assert estimated_tokens(0) == 0
    assert estimated_tokens(262_143) == 262_143 // 4
    assert estimated_tokens(262_144) == 65_536
    # Refusal keys on bytes/entities only: a max-bytes snapshot carries a huge
    # token estimate yet still passes, while an entity overflow with a tiny
    # token estimate still refuses.
    guard_full_snapshot(
        document_fp=PARENT_FP, entity_count=1, byte_size=SNAPSHOT_BYTES_MAX
    )
    with pytest.raises(SnapshotOversizedError):
        guard_full_snapshot(document_fp=PARENT_FP, entity_count=SNAPSHOT_ENTITIES_MAX + 1, byte_size=4)


def test_snapshot_pagination_round_trip() -> None:
    snapshot = _snapshot(70)
    transport = _SnapshotTransport(_wire_snapshot(snapshot))
    client = NativeBridgeClient(transport)

    seen: list[str] = []
    offset = 0
    envelopes: list[dict] = []
    while True:
        _, envelope = client.document_snapshot_page(
            RUNTIME_ID, document_pid=DOCUMENT_PID, offset=offset, limit=32
        )
        assert PAGED_KEYS <= set(envelope)
        envelopes.append(envelope)
        seen.extend(item["semantic_pid"] for item in envelope["items"])
        if envelope["complete"]:
            assert envelope["next_offset"] is None
            break
        assert envelope["next_offset"] == offset + len(envelope["items"])
        offset = envelope["next_offset"]

    assert seen == [entity.semantic_pid for entity in snapshot.entities]
    assert [envelope["count"] for envelope in envelopes] == [32, 32, 6]
    assert envelopes[-1]["matched"] == envelopes[-1]["source"] == 70


def test_snapshot_page_is_transport_slice_not_binding_state() -> None:
    snapshot = _snapshot(10)
    transport = _SnapshotTransport(_wire_snapshot(snapshot))
    client = NativeBridgeClient(transport)

    page, envelope = client.document_snapshot_page(
        RUNTIME_ID, document_pid=DOCUMENT_PID, offset=0, limit=4
    )

    assert len(page.entities) == 4 == envelope["count"]
    assert page.fingerprints.document_fp is None
    assert envelope["document_fp"] == fingerprint_document(snapshot)


def test_metadata_query_offset_pagination_round_trip() -> None:
    transport = _MetadataTransport(total=5)
    client = NativeBridgeClient(transport)

    first = client.metadata_query(
        RUNTIME_ID, document_pid=DOCUMENT_PID, namespace="customer.meta.v1", limit=2
    )
    # Default path stays wire-identical except additive envelope keys.
    assert first["items"] == transport.matches[:2]
    assert first["count"] == 2
    assert first["limit"] == 2
    assert first["truncated"] is True
    assert first["complete"] is False
    assert first["next_offset"] == 2
    assert first["envelope_version"] == ENVELOPE_VERSION
    assert PAGED_KEYS <= set(first)

    seen: list[dict] = []
    offset: int | None = 0
    while offset is not None:
        page = client.metadata_query(
            RUNTIME_ID,
            document_pid=DOCUMENT_PID,
            namespace="customer.meta.v1",
            limit=2,
            offset=offset,
        )
        assert PAGED_KEYS <= set(page)
        seen.extend(page["items"])
        offset = page["next_offset"]

    assert seen == transport.matches
    wire_limits = [request["params"]["limit"] for request in transport.requests]
    # Over-fetch keeps the wire contract unchanged: limit+offset per page.
    assert wire_limits == [2, 2, 4, 6]


def test_no_silent_truncation_on_paged_and_oversized_paths() -> None:
    envelope = build_paged_envelope(items=[{"a": 1}], offset=0, limit=32, matched=5, source=9)
    assert envelope["complete"] is False
    assert envelope["next_offset"] == 1
    assert envelope["matched"] == 5
    assert envelope["source"] == 9

    done = build_paged_envelope(items=[{"a": 1}], offset=4, limit=32, matched=5, source=9)
    assert done["complete"] is True
    assert done["next_offset"] is None

    with pytest.raises(SnapshotOversizedError):
        guard_full_snapshot(document_fp=PARENT_FP, entity_count=10**6, byte_size=1)
    with pytest.raises(DeltaOversizedError):
        guard_delta(document_fp=PARENT_FP, pid_count=10**6)


def test_compat_old_client_parses_and_fp_v3_unchanged() -> None:
    assert DOCUMENT_FINGERPRINT_SCHEMA_VERSION == 3
    before = _snapshot(4)
    fp_before = fingerprint_document(before)
    guard_full_snapshot(
        document_fp=fp_before,
        entity_count=len(before.entities),
        byte_size=canonical_byte_size(before.to_dict()),
    )
    # Envelope guard never mutates state or the v3 fingerprint domain.
    assert fingerprint_document(before) == fp_before

    transport = _MetadataTransport(total=2)
    client = NativeBridgeClient(transport)
    result = client.metadata_query(
        RUNTIME_ID, document_pid=DOCUMENT_PID, namespace="customer.meta.v1"
    )
    for key in (
        "schema_version",
        "document_pid",
        "namespace",
        "count",
        "scanned_entities",
        "limit",
        "truncated",
        "items",
    ):
        assert key in result
    assert result["count"] == 2
    assert result["truncated"] is False
    assert result["complete"] is True


@pytest.mark.asyncio
async def test_object_query_keeps_old_fields_and_adds_envelope(settings) -> None:
    app = create_mcp(settings)
    async with Client(app) as client:
        await client.call_tool("document_new", {})
        created = await client.call_tool("entity_create_line", {"x1": 0, "y1": 0, "x2": 5, "y2": 0})
        assert created.is_error is False
        result = await client.call_tool("object_query", {"limit": 200, "offset": 0})

    payload = result.structured_content or {}
    for key in ("items", "count", "matched_count", "source_count", "offset", "limit", "complete"):
        assert key in payload
    assert payload["count"] == 1
    assert PAGED_KEYS <= set(payload)
    assert payload["matched"] == payload["matched_count"]
    assert payload["source"] == payload["source_count"]
    assert payload["envelope_version"] == ENVELOPE_VERSION
    assert payload["estimated_tokens"] == payload["byte_size"] // 4


@pytest.mark.asyncio
async def test_object_list_stays_list_compatible(settings) -> None:
    app = create_mcp(settings)
    async with Client(app) as client:
        await client.call_tool("document_new", {})
        await client.call_tool("entity_create_line", {"x1": 0, "y1": 0, "x2": 5, "y2": 0})
        result = await client.call_tool("object_list", {"limit": 200, "offset": 0})

    payload = result.structured_content or {}
    rows = payload.get("result", payload)
    assert isinstance(rows, list)
    assert len(rows) == 1


def test_gateway_frame_64kib_forces_paging_for_large_snapshots() -> None:
    with pytest.raises(Exception, match="FRAME_TOO_LARGE"):
        encode_frame({"pad": "x" * (MAX_FRAME_BYTES + 1)})

    small = encode_frame({"pad": "x" * 100})
    assert 0 < len(small) <= MAX_FRAME_BYTES
    # A max-size snapshot can never travel in one gateway frame: paging is required.
    assert SNAPSHOT_BYTES_MAX > GATEWAY_FRAME_BYTES_MAX


class _StrayLogicalClient:
    """Strict pre_fp-binding peer; stray mutates native truth without touching receipts."""

    def __init__(self, *, stray_phase: str | None = None):
        assert stray_phase in {None, "mid_batch", "before_finalize"}
        self.stray_phase = stray_phase
        self.native_fp = PARENT_FP
        self.dispatched = 0
        self.finalize_calls = 0
        self.recovery_calls = 0
        self.state_calls = 0
        self.owner_request_id: str | None = None

    def _binding(self) -> dict[str, str]:
        assert isinstance(self.owner_request_id, str)
        return {
            "checkpoint_id": CHECKPOINT_ID,
            "checkpoint_artifact_fp": CHECKPOINT_ART_FP,
            "expected_restore_fp": PARENT_FP,
            "owner_request_id": self.owner_request_id,
        }

    def begin_logical_batch(self, runtime_document_id: str, **kwargs: object) -> dict[str, object]:
        self.owner_request_id = str(kwargs["request_id"])
        return {
            "schema_version": 1,
            "status": "OPEN",
            "document_pid": DOCUMENT_PID,
            "pre_document_fp": PARENT_FP,
            "logical_transaction": self._binding(),
        }

    def _chain(self, index: int) -> str:
        return "sha256:" + f"{index + 1:064d}"

    def batch_create_chunk(self, runtime_document_id: str, **kwargs: object) -> dict[str, object]:
        if kwargs["expected_parent_fp"] != self.native_fp:
            raise RuntimeError(
                f"STATE_DRIFT: rolling pre-extract predecessor mismatch "
                f"(stray mutation detected before chunk {self.dispatched})"
            )
        entities = list(kwargs["inserts"] if "inserts" in kwargs else kwargs["entities"])
        post = self._chain(self.dispatched)
        self.dispatched += 1
        self.native_fp = post
        if self.stray_phase == "mid_batch" and self.dispatched == 1:
            # Out-of-band stray: native truth moves without a receipt, so the
            # next rolling pre-extract must refuse.
            self.native_fp = "sha256:" + "f" * 64
        pids = [f"pid:created-{self.dispatched}-{n}" for n in range(len(entities))]
        return {
            "schema_version": 1,
            "operation": "entity.batch.create",
            "outcome": "COMMITTED_VERIFIED",
            "document_pid": DOCUMENT_PID,
            "pre_document_fp": kwargs["expected_parent_fp"],
            "provisional_document_fp": post,
            "post_document_fp": post,
            "affected_semantic_pids": pids,
            "recovery_checkpoint": self._binding(),
        }

    def document_state(self, runtime_document_id: str, **kwargs: object) -> dict[str, object]:
        self.state_calls += 1
        observed = self.native_fp
        if self.stray_phase == "before_finalize":
            observed = "sha256:" + "e" * 64  # stray landed after the last chunk
        return {
            "schema_version": 1,
            "runtime_document_id": runtime_document_id,
            "document_pid": DOCUMENT_PID,
            "document_fp_schema_version": 3,
            "document_fp": observed,
            "entity_count": 64,
        }

    def finalize_recovery(self, runtime_document_id: str, **kwargs: object) -> dict[str, object]:
        self.finalize_calls += 1
        return {"schema_version": 1, "status": "FINALIZED", "checkpoint_id": CHECKPOINT_ID}

    def recoveries_list(self) -> list[dict[str, object]]:
        return []

    def resolve_recovery(self, runtime_document_id: str, **kwargs: object) -> dict[str, object]:
        self.recovery_calls += 1
        return {
            "schema_version": 1,
            "operation": "logical.batch",
            "outcome": "ROLLED_BACK_VERIFIED",
            "document_pid": DOCUMENT_PID,
            "runtime_document_id": runtime_document_id,
            "checkpoint_id": CHECKPOINT_ID,
            "rollback": {
                "strategy": "R2_CHECKPOINT_RESTORE",
                "status": "ROLLED_BACK_VERIFIED",
                "expected_restore_fp": PARENT_FP,
                "actual_restore_fp": PARENT_FP,
            },
        }


def _forty_lines() -> tuple[dict[str, object], ...]:
    return tuple(
        {"kind": "line", "start": [float(index), 0.0, 0.0], "end": [float(index) + 1.0, 0.0, 0.0]}
        for index in range(40)
    )


def test_affected_stray_caught_at_rolling_preextract(tmp_path) -> None:
    client = _StrayLogicalClient(stray_phase="mid_batch")
    executor = NativeLogicalBatchExecutor(client, tmp_path / "stray-mid.jsonl")

    result = executor.create_entities(
        RUNTIME_ID,
        document_pid=DOCUMENT_PID,
        expected_parent_fp=PARENT_FP,
        entities=_forty_lines(),
    )

    assert result.status == "ROLLED_BACK_VERIFIED"
    assert result.final_fp == PARENT_FP
    assert client.finalize_calls == 0
    assert client.recovery_calls == 1


def test_affected_stray_caught_at_finalize(tmp_path) -> None:
    client = _StrayLogicalClient(stray_phase="before_finalize")
    executor = NativeLogicalBatchExecutor(client, tmp_path / "stray-final.jsonl")

    result = executor.create_entities(
        RUNTIME_ID,
        document_pid=DOCUMENT_PID,
        expected_parent_fp=PARENT_FP,
        entities=_forty_lines(),
    )

    assert result.status == "ROLLED_BACK_VERIFIED"
    assert result.final_fp == PARENT_FP
    assert client.finalize_calls == 0
    assert client.recovery_calls == 1
    assert client.state_calls >= 1
