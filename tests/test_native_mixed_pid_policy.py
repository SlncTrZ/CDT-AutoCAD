"""Mixed managed/unmanaged PID boundary — explicit native refusal policy contract.
Wing: code | Topic: native-mixed-pid-policy | Updated: 2026-09-12
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_required_entity_pid_refuses_unmanaged_entities_with_explicit_policy_code():
    source = (ROOT / "native/CDT.AutoCAD.Bridge/EntityPidReader.cs").read_text(encoding="utf-8")

    assert '"UNMANAGED_ENTITY_PRESENT"' in source
    assert "explicit adoption is required before native semantic read or mutation" in source
    assert '"ENTITY_PID_MISSING"' not in source


def test_native_scans_do_not_auto_adopt_missing_entity_pids():
    reader = (ROOT / "native/CDT.AutoCAD.Bridge/EntityPidReader.cs").read_text(encoding="utf-8")
    extractor = (ROOT / "native/CDT.AutoCAD.Bridge/NativeSemanticExtractor.cs").read_text(encoding="utf-8")
    store = (ROOT / "native/CDT.AutoCAD.Bridge/EntityPidStore.cs").read_text(encoding="utf-8")

    assert "ReadRequired(entity, transaction)" in extractor
    assert "ReadRequired(entity, transaction)" in store
    assert "EntityPidStore.Set" not in reader
