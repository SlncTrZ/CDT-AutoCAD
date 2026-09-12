"""G3 scale graduation — 10k logical batches keep bounded chunks and avoid provisional whole-document rescans.
Wing: code | Topic: native-g3-scale | Updated: 2026-09-11 19:35
"""

from __future__ import annotations

from pathlib import Path

from cdt_autocad.native_bridge.protocol import MAX_LOGICAL_BATCH_ITEMS

ROOT = Path(__file__).resolve().parents[1]


def test_logical_batch_contract_allows_10k_items_without_enlarging_native_chunks():
    assert MAX_LOGICAL_BATCH_ITEMS == 10_000
    constants = (ROOT / "native/CDT.AutoCAD.Bridge/BridgeConstants.cs").read_text(encoding="utf-8")
    assert "MaxBatchChunkEntities = 32" in constants
    assert "MaxBatchSemanticEntities = 12_288" in constants


def test_batch_and_metadata_provisional_validation_rebuilds_only_affected_rows():
    service = (ROOT / "native/CDT.AutoCAD.Bridge/NativeMutationService.cs").read_text(encoding="utf-8")
    extractor = (ROOT / "native/CDT.AutoCAD.Bridge/NativeSemanticExtractor.cs").read_text(encoding="utf-8")
    assert "BuildProvisionalFromAffected" in extractor
    assert service.count("BuildProvisionalFromAffected(") >= 4
