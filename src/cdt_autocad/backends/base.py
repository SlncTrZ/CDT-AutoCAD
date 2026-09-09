"""Focused backend contracts for the AutoCAD provider.
Wing: code | Topic: autocad-a3 | Updated: 2026-09-09 16:13
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from ..errors import UnsupportedCapabilityError
from ..models import BlockInfo, EntityInfo, LayerInfo


class IdentityContract(ABC):
    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def capabilities(self) -> dict[str, dict[str, Any]]: ...

    @abstractmethod
    def status(self) -> dict[str, Any]: ...


class DocumentContract(ABC):
    @abstractmethod
    async def document_new(self) -> dict[str, Any]: ...

    @abstractmethod
    async def document_open(self, path: str) -> dict[str, Any]: ...

    @abstractmethod
    async def document_info(self) -> dict[str, Any]: ...

    @abstractmethod
    async def document_save(self, path: str | None = None) -> dict[str, Any]: ...

    @abstractmethod
    async def document_save_as(self, path: str) -> dict[str, Any]: ...

    @abstractmethod
    async def document_export_pdf(self, path: str, layout: str | None = None) -> dict[str, Any]: ...

    @abstractmethod
    async def drawing_audit(self) -> dict[str, Any]: ...

    @abstractmethod
    async def drawing_purge(self) -> dict[str, Any]: ...


class ObjectQueryContract(ABC):
    @abstractmethod
    async def object_list(
        self,
        type_filter: str | None = None,
        layer_filter: str | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> list[EntityInfo]: ...

    @abstractmethod
    async def object_get(self, object_id: str) -> EntityInfo: ...

    @abstractmethod
    async def object_count(
        self,
        type_filter: str | None = None,
        layer_filter: str | None = None,
    ) -> int: ...


class ObjectMutationContract(ABC):
    @abstractmethod
    async def object_set_properties(self, object_id: str, **kwargs: Any) -> EntityInfo: ...

    @abstractmethod
    async def object_delete(self, object_id: str) -> dict[str, Any]: ...

    @abstractmethod
    async def object_move(self, object_id: str, dx: float, dy: float, dz: float = 0.0) -> EntityInfo: ...

    @abstractmethod
    async def object_copy(self, object_id: str, dx: float, dy: float, dz: float = 0.0) -> EntityInfo: ...

    @abstractmethod
    async def object_rotate(
        self, object_id: str, base_x: float, base_y: float, angle_deg: float
    ) -> EntityInfo: ...

    @abstractmethod
    async def object_scale(
        self, object_id: str, base_x: float, base_y: float, factor: float
    ) -> EntityInfo: ...


class EntityCreationContract(ABC):
    @abstractmethod
    async def entity_create_line(self, **kwargs: Any) -> EntityInfo: ...

    @abstractmethod
    async def entity_create_circle(self, **kwargs: Any) -> EntityInfo: ...

    @abstractmethod
    async def entity_create_arc(self, **kwargs: Any) -> EntityInfo: ...

    @abstractmethod
    async def entity_create_polyline(self, **kwargs: Any) -> EntityInfo: ...

    @abstractmethod
    async def entity_create_text(self, **kwargs: Any) -> EntityInfo: ...

    @abstractmethod
    async def hatch_create(self, **kwargs: Any) -> EntityInfo: ...

    @abstractmethod
    async def dimension_linear(self, **kwargs: Any) -> EntityInfo: ...

    @abstractmethod
    async def dimension_aligned(self, **kwargs: Any) -> EntityInfo: ...


class LayerContract(ABC):
    @abstractmethod
    async def layer_list(self) -> list[LayerInfo]: ...

    @abstractmethod
    async def layer_create(self, name: str, color: int = 7) -> LayerInfo: ...

    @abstractmethod
    async def layer_set_current(self, name: str) -> dict[str, Any]: ...


class BlockContract(ABC):
    @abstractmethod
    async def block_list(self) -> list[BlockInfo]: ...

    @abstractmethod
    async def block_create(
        self, name: str, object_ids: list[str], base_x: float = 0.0, base_y: float = 0.0
    ) -> BlockInfo: ...

    @abstractmethod
    async def block_insert(self, name: str, x: float, y: float, **kwargs: Any) -> EntityInfo: ...


class LayoutContract(ABC):
    @abstractmethod
    async def layout_list(self) -> dict[str, Any]: ...

    @abstractmethod
    async def layout_create(self, name: str) -> dict[str, Any]: ...

    @abstractmethod
    async def layout_set_current(self, name: str) -> dict[str, Any]: ...


class ViewContract(ABC):
    """A2 live-view extension staged below the public A1 MCP surface."""

    async def viewport_create(
        self,
        layout: str,
        center_x: float,
        center_y: float,
        width: float,
        height: float,
        view_center_x: float,
        view_center_y: float,
        scale: float = 1.0,
    ) -> dict[str, Any]:
        raise UnsupportedCapabilityError(
            "autocad.viewport.manage", "Viewport management requires the live COM backend."
        )

    async def viewport_list(self, layout: str | None = None) -> dict[str, Any]:
        raise UnsupportedCapabilityError(
            "autocad.viewport.manage", "Viewport management requires the live COM backend."
        )

    async def viewport_set_scale(self, handle: str, scale: float) -> dict[str, Any]:
        raise UnsupportedCapabilityError(
            "autocad.viewport.manage", "Viewport management requires the live COM backend."
        )

    async def viewport_lock(self, handle: str, locked: bool = True) -> dict[str, Any]:
        raise UnsupportedCapabilityError(
            "autocad.viewport.manage", "Viewport management requires the live COM backend."
        )

    async def viewport_delete(self, handle: str, force: bool = False) -> dict[str, Any]:
        raise UnsupportedCapabilityError(
            "autocad.viewport.manage", "Viewport management requires the live COM backend."
        )

    async def view_zoom_extents(self) -> dict[str, Any]:
        raise UnsupportedCapabilityError(
            "autocad.view.zoom", "Live view control requires the COM backend."
        )

    async def view_zoom_window(
        self, x1: float, y1: float, x2: float, y2: float
    ) -> dict[str, Any]:
        raise UnsupportedCapabilityError(
            "autocad.view.zoom", "Live view control requires the COM backend."
        )

    async def view_screenshot(self) -> bytes:
        raise UnsupportedCapabilityError(
            "autocad.viewport.capture", "Live viewport capture requires the COM backend."
        )

    async def view_set_direction(self, dx: float, dy: float, dz: float) -> dict[str, Any]:
        raise UnsupportedCapabilityError(
            "autocad.view.3d", "3D live view control requires the COM backend."
        )


class SpatialCurveContract(ABC):
    """A3 supporting 3D-curve geometry for native modeling workflows."""

    async def entity_create_3d_polyline(
        self, points: list[list[float]], closed: bool = False
    ) -> dict[str, Any]:
        raise UnsupportedCapabilityError(
            "autocad.geometry.3d_polyline",
            "Native 3D polyline creation requires the live COM backend.",
        )


class SolidContract(ABC):
    """A3 native ACIS solid surface with fail-closed non-COM defaults."""

    @staticmethod
    def _solid_refusal() -> UnsupportedCapabilityError:
        return UnsupportedCapabilityError(
            "autocad.solid.acis", "Native ACIS 3D solids require the live COM backend."
        )

    async def solid_box(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        raise self._solid_refusal()

    async def solid_cylinder(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        raise self._solid_refusal()

    async def solid_sphere(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        raise self._solid_refusal()

    async def solid_cone(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        raise self._solid_refusal()

    async def solid_torus(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        raise self._solid_refusal()

    async def solid_wedge(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        raise self._solid_refusal()

    async def solid_extrude(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        raise self._solid_refusal()

    async def solid_sweep(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        raise self._solid_refusal()

    async def solid_revolve(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        raise self._solid_refusal()

    async def solid_boolean(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        raise self._solid_refusal()

    async def solid_move(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        raise self._solid_refusal()

    async def solid_rotate3d(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        raise self._solid_refusal()

    async def solid_scale3d(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        raise self._solid_refusal()

    async def solid_mirror3d(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        raise self._solid_refusal()

    async def solid_inspect(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        raise self._solid_refusal()


class TransactionContract(ABC):
    @abstractmethod
    async def transaction_begin(self) -> dict[str, Any]: ...

    @abstractmethod
    async def transaction_commit(self) -> dict[str, Any]: ...

    @abstractmethod
    async def transaction_rollback(self) -> dict[str, Any]: ...

    @abstractmethod
    async def undo(self) -> dict[str, Any]: ...

    @abstractmethod
    async def redo(self) -> dict[str, Any]: ...


class AutoCADBackend(
    IdentityContract,
    DocumentContract,
    ObjectQueryContract,
    ObjectMutationContract,
    EntityCreationContract,
    LayerContract,
    BlockContract,
    LayoutContract,
    ViewContract,
    SpatialCurveContract,
    SolidContract,
    TransactionContract,
):
    """Composition surface for staged AutoCAD backends."""
