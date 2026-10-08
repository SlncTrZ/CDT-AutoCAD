"""U1 floor-plan hardening — regressions for production chunk execution blockers.
Wing: code | Topic: floorplan-u1-hardening | Updated: 2026-09-17 13:15
"""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

import cdt_autocad.backends.com_backend as cb
from cdt_autocad.backends.com_backend import ComBackend
from cdt_autocad.errors import StateConflictError
from cdt_autocad.native_bridge.client import NativeBridgeClient
from cdt_autocad.native_bridge.protocol import (
    NATIVE_PROTOCOL_VERSION,
    BatchCreateEntitySpec,
    BridgeRequest,
)
from cdt_autocad.native_bridge.public_runtime import NativePublicFacade

REQUEST_ID = "11111111-1111-4111-8111-111111111111"
RUNTIME = "22222222-2222-4222-8222-222222222222"
DOC = "doc:33333333-3333-4333-8333-333333333333"
FP = "sha256:" + "a" * 64


def _payload(operation: str, params: dict) -> dict:
    return {
        "protocol": NATIVE_PROTOCOL_VERSION,
        "request_id": REQUEST_ID,
        "operation": operation,
        "params": params,
    }


def test_d10_protocol_has_explicit_document_identity_bootstrap_without_pid_assertion():
    request = BridgeRequest.from_dict(
        _payload(
            "bridge.document.identity.initialize",
            {"runtime_document_id": RUNTIME},
        )
    )

    assert request.operation == "bridge.document.identity.initialize"
    assert request.params.runtime_document_id == RUNTIME
    assert request.params.document_pid is None

    with pytest.raises(Exception, match="INVALID_PARAMS"):
        BridgeRequest.from_dict(
            _payload(
                "bridge.document.identity.initialize",
                {"runtime_document_id": RUNTIME, "document_pid": DOC},
            )
        )


def test_d10_client_validates_identity_bootstrap_readback():
    class Transport:
        def __init__(self):
            self.requests = []

        def round_trip(self, payload):
            self.requests.append(dict(payload))
            return {
                "protocol": NATIVE_PROTOCOL_VERSION,
                "request_id": payload["request_id"],
                "ok": True,
                "result": {
                    "runtime_document_id": RUNTIME,
                    "document_pid": DOC,
                    "initialized": True,
                    "readback_verified": True,
                    "scope": "empty-current-space-only",
                    "document_fp_schema_version": 3,
                    "document_fp": FP,
                    "entity_count": 0,
                },
            }

    transport = Transport()
    client = NativeBridgeClient(transport, request_id_factory=lambda: REQUEST_ID)

    result = client.initialize_document_identity(RUNTIME)

    assert result["document_pid"] == DOC
    assert result["document_fp"] == FP
    assert transport.requests[0]["operation"] == "bridge.document.identity.initialize"
    assert transport.requests[0]["params"] == {"runtime_document_id": RUNTIME}


def test_d10_public_facade_bootstraps_only_the_active_runtime_document(settings, tmp_path):
    class Client:
        def documents_list(self):
            return [
                {
                    "runtime_document_id": RUNTIME,
                    "document_pid": None,
                    "name": "Drawing1.dwg",
                    "file_name": "",
                    "is_active": True,
                }
            ]

        def initialize_document_identity(self, runtime_document_id):
            assert runtime_document_id == RUNTIME
            return {
                "runtime_document_id": RUNTIME,
                "document_pid": DOC,
                "initialized": True,
                "readback_verified": True,
                "scope": "empty-current-space-only",
                "document_fp_schema_version": 3,
                "document_fp": FP,
                "entity_count": 0,
            }

    facade = NativePublicFacade(
        replace(settings, backend="com"),
        client_factory=Client,
        journal_root=tmp_path,
    )

    result = facade.bootstrap_document_identity()

    assert result["document_pid"] == DOC
    assert result["document_fp"] == FP
    assert result["readback_verified"] is True
    assert result["fallback"] is False
    assert result["route"] == "native-managed-bridge"
    assert list(tmp_path.iterdir()) == []


def test_d14_batch_create_accepts_generic_drawing_primitives_with_style():
    specs = [
        BatchCreateEntitySpec.from_dict(
            {
                "kind": "line",
                "start": [0, 0, 0],
                "end": [1000, 0, 0],
                "layer": "A-WALL",
                "color_index": 7,
            }
        ),
        BatchCreateEntitySpec.from_dict(
            {
                "kind": "text",
                "text": "BEDROOM",
                "position": [1000, 1500, 0],
                "height": 250,
                "rotation": 0.0,
                "layer": "A-TEXT",
                "color_index": 2,
            }
        ),
        BatchCreateEntitySpec.from_dict(
            {
                "kind": "mtext",
                "text": "CONCEPT / APPROVED ASSUMPTION",
                "position": [0, -1000, 0],
                "height": 180,
                "rotation": 0.0,
                "width": 4000,
                "layer": "A-NOTE",
            }
        ),
        BatchCreateEntitySpec.from_dict(
            {
                "kind": "aligned_dimension",
                "xline1": [0, 0, 0],
                "xline2": [3000, 0, 0],
                "dim_line_point": [0, -500, 0],
                "layer": "A-DIMS",
            }
        ),
        BatchCreateEntitySpec.from_dict(
            {
                "kind": "linear_dimension",
                "xline1": [0, 0, 0],
                "xline2": [0, 2500, 0],
                "dim_line_point": [-500, 0, 0],
                "rotation": 1.5707963267948966,
                "layer": "A-DIMS",
            }
        ),
    ]

    assert [spec.kind for spec in specs] == [
        "line",
        "text",
        "mtext",
        "aligned_dimension",
        "linear_dimension",
    ]
    assert specs[0].layer == "A-WALL"
    assert specs[1].text == "BEDROOM"
    assert specs[2].width == 4000.0
    assert specs[3].dim_line_point == (0.0, -500.0, 0.0)
    assert specs[4].rotation == pytest.approx(1.5707963267948966)

    with pytest.raises(Exception, match="INVALID_PARAMS"):
        BatchCreateEntitySpec.from_dict(
            {
                "kind": "text",
                "text": "",
                "position": [0, 0, 0],
                "height": 250,
                "rotation": 0,
            }
        )


@pytest.mark.asyncio
async def test_d12_document_save_returns_verified_persisted_clean_receipt(settings, monkeypatch, tmp_path):
    backend = ComBackend(replace(settings, backend="com"))
    target = tmp_path / "verified-save.dwg"

    class Doc:
        Name = target.name
        FullName = str(target)
        Saved = True

        def Save(self):
            self.Saved = True

        def GetVariable(self, name):
            assert name == "DBMOD"
            return 0

    async def inline_run(func, **_kwargs):
        return func()

    monkeypatch.setattr(backend, "_run", inline_run)
    monkeypatch.setattr(backend, "_doc", lambda: Doc())

    result = await backend.document_save()

    assert result["ok"] is True
    assert result["saved"] is True
    assert result["dbmod"] == 0
    assert result["persisted_clean"] is True
    assert result["postcondition_verified"] is True


@pytest.mark.asyncio
async def test_d12_document_save_refuses_dirty_postcondition(settings, monkeypatch, tmp_path):
    backend = ComBackend(replace(settings, backend="com"))
    target = tmp_path / "dirty-save.dwg"

    class Doc:
        Name = target.name
        FullName = str(target)
        Saved = False

        def Save(self):
            self.Saved = False

        def GetVariable(self, name):
            assert name == "DBMOD"
            return 1

    async def inline_run(func, **_kwargs):
        return func()

    monkeypatch.setattr(backend, "_run", inline_run)
    monkeypatch.setattr(backend, "_doc", lambda: Doc())

    with pytest.raises(StateConflictError, match="persisted-clean"):
        await backend.document_save()

    assert backend.status()["integrity_uncertain"] is True


def test_d13_cached_live_app_repairs_connected_status(settings, monkeypatch):
    app = SimpleNamespace(
        Name="AutoCAD",
        Version="26.0s (LMS Tech)",
        Caption="Autodesk AutoCAD 2027",
        FullName=r"C:\Program Files\Autodesk\AutoCAD 2027\acad.exe",
    )
    monkeypatch.setattr(cb, "_WIN32", True)
    monkeypatch.setattr(cb, "_COM_IMPORTS_OK", True)
    backend = ComBackend(replace(settings, backend="com"))
    try:
        generation = backend._generation
        backend._apps[generation] = app
        backend._connected = False
        backend._application_metadata = None

        assert backend._app() is app
        assert backend.status()["connected"] is True
        assert backend.status()["application"]["release"] == "2027"
    finally:
        backend.shutdown()
