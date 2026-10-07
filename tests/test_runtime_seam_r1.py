"""R1 runtime seam — delegate parity, shared coordinator, fail-closed pipe.
Wing: code | Topic: migration-r1-seam | Updated: 2026-10-07 14:40
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest
from fastmcp import Client

from cdt_autocad.backends.base import AutoCADBackend
from cdt_autocad.backends.com_backend import ComBackend
from cdt_autocad.backends.local_runtime import LocalAutoCADRuntimeAdapter
from cdt_autocad.backends.runtime_port import AutoCADRuntimePort
from cdt_autocad.contract_identity import PUBLIC_TOOL_COUNT
from cdt_autocad.errors import UnsupportedCapabilityError
from cdt_autocad.mutation_coordinator import MutationCoordinator
from cdt_autocad.native_bridge.public_runtime import NativePublicFacade
from cdt_autocad.native_bridge.transport_windows import (
    DEFAULT_CONNECT_TIMEOUT_MS,
    DEFAULT_IO_TIMEOUT_MS,
    NativePipeDeadlineError,
    pipe_name_for_session,
)
from cdt_autocad.server import create_mcp


class _RecordingBackend(AutoCADBackend):
    """Minimal recording stand-in covering the exercised seam surface."""

    def __init__(self, coordinator: MutationCoordinator, settings: Any = None):
        self._mutation_coordinator = coordinator
        self.settings = settings
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    @property
    def name(self) -> str:
        return "com"

    @property
    def runtime_available(self) -> bool:
        return False

    def capabilities(self) -> dict[str, dict[str, Any]]:
        self.calls.append(("capabilities", (), {}))
        return {"common.object.query": {"supported": True}}

    def status(self) -> dict[str, Any]:
        self.calls.append(("status", (), {}))
        return {"backend": "com", "runtime_available": False, "connected": False}

    async def document_new(self) -> dict[str, Any]:
        self.calls.append(("document_new", (), {}))
        return {"ok": True}

    async def document_open(self, path: str) -> dict[str, Any]:
        self.calls.append(("document_open", (path,), {}))
        return {"ok": True, "path": path}

    async def document_info(self) -> dict[str, Any]:
        self.calls.append(("document_info", (), {}))
        return {"ok": True}

    async def document_save(self, path: str | None = None) -> dict[str, Any]:
        self.calls.append(("document_save", (path,), {}))
        return {"ok": True}

    async def document_save_as(self, path: str) -> dict[str, Any]:
        self.calls.append(("document_save_as", (path,), {}))
        return {"ok": True}

    async def document_export_pdf(self, path: str, layout: str | None = None) -> dict[str, Any]:
        self.calls.append(("document_export_pdf", (path, layout), {}))
        return {"ok": True}

    async def drawing_audit(self) -> dict[str, Any]:
        self.calls.append(("drawing_audit", (), {}))
        return {"ok": True}

    async def drawing_purge(self) -> dict[str, Any]:
        self.calls.append(("drawing_purge", (), {}))
        return {"ok": True}

    async def document_configure_units(
        self,
        insertion_units: str | None = None,
        measurement: str | None = None,
        linear_format: str | None = None,
        linear_precision: int | None = None,
    ) -> dict[str, Any]:
        self.calls.append(("document_configure_units", (), {}))
        return {"ok": True}

    async def object_list(
        self,
        type_filter: str | None = None,
        layer_filter: str | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> list[Any]:
        self.calls.append(("object_list", (type_filter, layer_filter, limit, offset), {}))
        return []

    async def object_get(self, object_id: str) -> Any:
        self.calls.append(("object_get", (object_id,), {}))
        raise KeyError(f"entity not found: {object_id}")

    async def object_count(
        self,
        type_filter: str | None = None,
        layer_filter: str | None = None,
    ) -> int:
        self.calls.append(("object_count", (type_filter, layer_filter), {}))
        return 0

    async def object_set_properties(self, object_id: str, **kwargs: Any) -> Any:
        self.calls.append(("object_set_properties", (object_id,), kwargs))
        raise KeyError(object_id)

    async def object_delete(self, object_id: str) -> dict[str, Any]:
        self.calls.append(("object_delete", (object_id,), {}))
        raise KeyError(object_id)

    async def object_move(
        self, object_id: str, dx: float, dy: float, dz: float = 0.0
    ) -> Any:
        self.calls.append(("object_move", (object_id, dx, dy, dz), {}))
        raise KeyError(object_id)

    async def object_copy(
        self, object_id: str, dx: float, dy: float, dz: float = 0.0
    ) -> Any:
        self.calls.append(("object_copy", (object_id, dx, dy, dz), {}))
        raise KeyError(object_id)

    async def object_rotate(
        self, object_id: str, base_x: float, base_y: float, angle_deg: float
    ) -> Any:
        self.calls.append(("object_rotate", (object_id, base_x, base_y, angle_deg), {}))
        raise KeyError(object_id)

    async def object_scale(
        self, object_id: str, base_x: float, base_y: float, factor: float
    ) -> Any:
        self.calls.append(("object_scale", (object_id, base_x, base_y, factor), {}))
        raise KeyError(object_id)

    async def entity_create_line(self, **kwargs: Any) -> Any:
        self.calls.append(("entity_create_line", (), kwargs))
        raise NotImplementedError

    async def entity_create_circle(self, **kwargs: Any) -> Any:
        raise NotImplementedError

    async def entity_create_arc(self, **kwargs: Any) -> Any:
        raise NotImplementedError

    async def entity_create_polyline(self, **kwargs: Any) -> Any:
        raise NotImplementedError

    async def entity_create_text(self, **kwargs: Any) -> Any:
        raise NotImplementedError

    async def hatch_create(self, **kwargs: Any) -> Any:
        raise NotImplementedError

    async def dimension_linear(self, **kwargs: Any) -> Any:
        raise NotImplementedError

    async def dimension_aligned(self, **kwargs: Any) -> Any:
        raise NotImplementedError

    async def dimension_angular(self, **kwargs: Any) -> Any:
        raise NotImplementedError

    async def dimension_radial(self, **kwargs: Any) -> Any:
        raise NotImplementedError

    async def dimension_diametric(self, **kwargs: Any) -> Any:
        raise NotImplementedError

    async def dimension_ordinate(self, **kwargs: Any) -> Any:
        raise NotImplementedError

    async def layer_list(self) -> list[Any]:
        self.calls.append(("layer_list", (), {}))
        return []

    async def layer_create(self, name: str, color: int = 7) -> Any:
        raise NotImplementedError

    async def layer_set_current(self, name: str) -> dict[str, Any]:
        raise NotImplementedError

    async def layer_update_state(self, name: str, **kwargs: Any) -> Any:
        raise NotImplementedError

    async def block_list(self, *args: Any, **kwargs: Any) -> list[Any]:
        self.calls.append(("block_list", args, kwargs))
        return []

    async def block_create(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    async def block_insert(self, *args: Any, **kwargs: Any) -> Any:
        raise NotImplementedError

    async def layout_list(self) -> dict[str, Any]:
        self.calls.append(("layout_list", (), {}))
        return {"layouts": []}

    async def layout_create(self, name: str) -> dict[str, Any]:
        raise NotImplementedError

    async def layout_set_current(self, name: str) -> dict[str, Any]:
        raise NotImplementedError

    async def object_measure(self, object_id: str) -> dict[str, Any]:
        raise NotImplementedError

    async def drawing_extents(self) -> dict[str, Any]:
        self.calls.append(("drawing_extents", (), {}))
        return {"ok": True}

    async def object_intersections(
        self, first_id: str, second_id: str, extend_mode: str = "none"
    ) -> dict[str, Any]:
        raise NotImplementedError

    async def transaction_begin(self) -> dict[str, Any]:
        self.calls.append(("transaction_begin", (), {}))
        return {"ok": True}

    async def transaction_commit(self) -> dict[str, Any]:
        self.calls.append(("transaction_commit", (), {}))
        return {"ok": True}

    async def transaction_rollback(self) -> dict[str, Any]:
        self.calls.append(("transaction_rollback", (), {}))
        return {"ok": True}

    async def undo(self) -> dict[str, Any]:
        raise NotImplementedError

    async def redo(self) -> dict[str, Any]:
        raise NotImplementedError


def _adapter(settings: Any) -> tuple[LocalAutoCADRuntimeAdapter, _RecordingBackend]:
    coordinator = MutationCoordinator()
    inner = _RecordingBackend(coordinator, settings)
    facade = NativePublicFacade(settings)
    return LocalAutoCADRuntimeAdapter(
        inner, mutation_coordinator=coordinator, native_facade=facade
    ), inner


def test_adapter_implements_runtime_port_without_redefining_contract():
    coordinator = MutationCoordinator()
    inner = _RecordingBackend(coordinator)
    adapter = LocalAutoCADRuntimeAdapter(
        inner,
        mutation_coordinator=coordinator,
        native_facade=NativePublicFacade.__new__(NativePublicFacade),
    )
    assert isinstance(adapter, AutoCADBackend)
    assert isinstance(adapter, AutoCADRuntimePort)
    assert adapter.backend is inner
    assert adapter.mutation_coordinator is coordinator
    assert adapter._mutation_coordinator is coordinator
    # Full inherited contract surface is present via explicit 1:1 delegation.
    for method in (
        "document_new",
        "document_open",
        "document_info",
        "object_list",
        "object_count",
        "transaction_begin",
        "drawing_extents",
        "runtime_status",
        "health",
    ):
        assert callable(getattr(adapter, method)), method


@pytest.mark.asyncio
async def test_adapter_delegates_one_to_one(settings):
    adapter, inner = _adapter(settings)
    assert adapter.name == "com"
    assert adapter.capabilities() == {"common.object.query": {"supported": True}}
    assert adapter.status()["backend"] == "com"
    assert adapter.runtime_status() == adapter.status()
    health = adapter.health()
    assert health["backend"] == "com"
    assert health["mutation_coordinator"]["quarantined"] is False

    assert await adapter.document_info() == {"ok": True}
    assert await adapter.object_count(None, None) == 0
    assert await adapter.drawing_extents() == {"ok": True}
    assert await adapter.transaction_begin() == {"ok": True}
    assert [name for name, _, _ in inner.calls].count("status") >= 2
    assert ("document_info", (), {}) in inner.calls


def test_adapter_refuses_duplicate_coordinator(settings):
    first = MutationCoordinator()
    second = MutationCoordinator()
    inner = _RecordingBackend(first, settings)
    with pytest.raises(ValueError, match="duplicate MutationCoordinator"):
        LocalAutoCADRuntimeAdapter(
            inner,
            mutation_coordinator=second,
            native_facade=NativePublicFacade(settings),
        )


def test_pipe_transport_budgets_and_fail_closed_semantics_preserved():
    assert DEFAULT_CONNECT_TIMEOUT_MS == 5_000
    assert DEFAULT_IO_TIMEOUT_MS == 60_000
    assert NativePipeDeadlineError.completion_unknown is True
    assert pipe_name_for_session(1) == "SlncTrZ.CDT.AutoCAD.Bridge.v1.s1"
    with pytest.raises(ValueError):
        pipe_name_for_session(-1)


def test_native_facade_fails_closed_when_pipe_absent(settings):
    adapter, _ = _adapter(replace(settings, backend="com"))
    with pytest.raises((RuntimeError, UnsupportedCapabilityError)):
        adapter.native_facade.status()


def test_create_mcp_com_uses_single_shared_coordinator(settings):
    app = create_mcp(replace(settings, backend="com"))
    runtime = app._cdt_runtime
    assert isinstance(runtime, LocalAutoCADRuntimeAdapter)
    assert isinstance(runtime.backend, ComBackend)
    assert runtime.backend.name == "com"
    assert runtime.mutation_coordinator is app._cdt_mutation_coordinator
    assert runtime.backend._mutation_coordinator is app._cdt_mutation_coordinator
    assert isinstance(runtime.native_facade, NativePublicFacade)


def test_create_mcp_ezdxf_path_unchanged(settings):
    app = create_mcp(settings)
    assert app._cdt_runtime is None
    assert app._cdt_backend.name == "ezdxf"


@pytest.mark.asyncio
async def test_public_tool_surface_still_87_after_seam(settings):
    for backend_name in ("ezdxf", "com"):
        app = create_mcp(replace(settings, backend=backend_name))
        async with Client(app) as client:
            names = {tool.name for tool in await client.list_tools()}
        assert len(names) == PUBLIC_TOOL_COUNT == 87
