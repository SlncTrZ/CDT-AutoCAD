"""G1 idle-yield invariant — bounded batch mutations yield back to AutoCAD between chunks.
Wing: code | Topic: native-g1-idle-yield | Updated: 2026-09-11 18:10
"""

from __future__ import annotations

from pathlib import Path


def test_bridge_dispatcher_yields_after_every_batch_mutation_attempt():
    source = (
        Path(__file__).resolve().parents[1]
        / "native"
        / "CDT.AutoCAD.Bridge"
        / "BridgeDispatcher.cs"
    ).read_text(encoding="utf-8")

    assert 'request.Operation.StartsWith("entity.batch.", StringComparison.Ordinal)' in source
    assert 'string.Equals(request.Operation, "bridge.logical.begin", StringComparison.Ordinal)' in source
    assert "bool yieldAfterRequest = RequiresIdleYield(pending.Request);" in source
    assert source.count("if (yieldAfterRequest)") >= 2


def test_bridge_health_advertises_idle_yield_only_when_dispatcher_enforces_it():
    root = Path(__file__).resolve().parents[1] / "native" / "CDT.AutoCAD.Bridge"
    dispatcher = (root / "BridgeDispatcher.cs").read_text(encoding="utf-8")
    service = (root / "NativeBridgeService.cs").read_text(encoding="utf-8")

    assert "RequiresIdleYield" in dispatcher
    assert "batch_yield_per_idle = true" in service
