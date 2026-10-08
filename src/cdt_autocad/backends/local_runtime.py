"""Local single-host runtime adapter — R1 seam over existing COM/native code.
Wing: code | Topic: migration-r1-seam | Updated: 2026-10-07 14:35
"""

from __future__ import annotations

from typing import Any

from ..models import BlockInfo, EntityInfo, LayerInfo
from ..mutation_coordinator import MutationCoordinator
from ..native_bridge.public_runtime import NativePublicFacade
from .base import AutoCADBackend
from .runtime_port import AutoCADRuntimePort


class LocalAutoCADRuntimeAdapter(AutoCADBackend, AutoCADRuntimePort):
    """Single-host adapter: 1:1 delegation over the current COM/native stack.

    Wraps exactly one existing AutoCADBackend (ComBackend on the default path),
    one existing NativePublicFacade and ONE injected shared MutationCoordinator.
    Transport stays provider-local: NamedPipeTransport + pipe_name_for_session +
    existing connect/I/O timeouts and coordinator fencing are untouched — this class
    creates no transport of its own and never retries pipe deadlines.

    A coordinator mismatch between the adapter and the wrapped backend is refused
    at construction so no duplicate coordinator instance can exist on this path.
    """

    def __init__(
        self,
        backend: AutoCADBackend,
        *,
        mutation_coordinator: MutationCoordinator,
        native_facade: NativePublicFacade | None = None,
    ) -> None:
        if not isinstance(backend, AutoCADBackend):
            raise TypeError("LocalAutoCADRuntimeAdapter requires an AutoCADBackend")
        if not isinstance(mutation_coordinator, MutationCoordinator):
            raise TypeError("LocalAutoCADRuntimeAdapter requires one shared MutationCoordinator")
        inner_coordinator = getattr(backend, "_mutation_coordinator", None)
        if inner_coordinator is not None and inner_coordinator is not mutation_coordinator:
            raise ValueError(
                "LocalAutoCADRuntimeAdapter refuses a duplicate MutationCoordinator: "
                "the wrapped backend must share the injected coordinator instance"
            )
        if native_facade is None:
            settings = getattr(backend, "settings", None)
            if settings is None:
                raise ValueError("native_facade is required when the backend has no settings")
            native_facade = NativePublicFacade(settings)
        if not isinstance(native_facade, NativePublicFacade):
            raise TypeError("native_facade must be a NativePublicFacade")
        self._backend = backend
        self._mutation_coordinator = mutation_coordinator
        self._native_facade = native_facade

    # -- port surface: escape hatch + shared authority + runtime identity --

    @property
    def backend(self) -> AutoCADBackend:
        return self._backend

    @property
    def mutation_coordinator(self) -> MutationCoordinator:
        return self._mutation_coordinator

    @property
    def native_facade(self) -> NativePublicFacade:
        return self._native_facade

    @property
    def name(self) -> str:
        return self._backend.name

    @property
    def runtime_available(self) -> bool:
        return bool(getattr(self._backend, "runtime_available", False))

    @property
    def settings(self) -> Any:
        return getattr(self._backend, "settings", None)

    def shutdown(self) -> None:
        target = getattr(self._backend, "shutdown", None)
        if callable(target):
            target()

    def capabilities(self) -> dict[str, dict[str, Any]]:
        return self._backend.capabilities()

    def status(self) -> dict[str, Any]:
        return self._backend.status()

    def runtime_status(self) -> dict[str, Any]:
        return self._backend.status()

    def health(self) -> dict[str, Any]:
        coordinator = self._mutation_coordinator.status()
        return {
            "backend": self._backend.name,
            "runtime_available": bool(getattr(self._backend, "runtime_available", False)),
            "quarantined": bool(coordinator.get("quarantined", False)),
            "mutation_coordinator": coordinator,
        }

    # -- native strong-integrity surface (R3/R4): sync 1:1 delegation over the
    # -- SAME NativePublicFacade instance the provider uses directly. No new
    # -- semantics: PID/fingerprint binding, recovery and read-back stay inside
    # -- the facade. Writer fencing for these ops stays at the caller layer
    # -- (server _run_native_mutation), exactly as on the direct provider path.

    def native_status(self) -> dict[str, Any]:
        return self._native_facade.status()

    def native_document_state(self) -> dict[str, Any]:
        return self._native_facade.document_state()

    def native_snapshot_page(self, offset: int = 0, limit: int = 50) -> dict[str, Any]:
        return self._native_facade.snapshot_page(offset, limit)

    def native_metadata_get(self, semantic_pid: str, namespace: str) -> dict[str, Any]:
        return self._native_facade.metadata_get(semantic_pid, namespace)

    def native_metadata_query(
        self,
        namespace: str,
        path: str | None = None,
        equals: Any = None,
        limit: int = 200,
        offset: int = 0,
    ) -> dict[str, Any]:
        return self._native_facade.metadata_query(
            namespace, path=path, equals=equals, limit=limit, offset=offset
        )

    def native_feature_execute(
        self,
        document_pid: str,
        expected_parent_fp: str,
        feature_id: str,
        feature_sequence: int,
        correlation_id: str,
        actions: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return self._native_facade.feature_execute(
            document_pid=document_pid,
            expected_parent_fp=expected_parent_fp,
            feature_id=feature_id,
            feature_sequence=feature_sequence,
            correlation_id=correlation_id,
            actions=actions,
        )

    def native_batch_create_entities(
        self,
        entities: list[dict[str, Any]],
        document_pid: str,
        expected_parent_fp: str,
    ) -> dict[str, Any]:
        return self._native_facade.batch_create_entities(
            entities,
            document_pid=document_pid,
            expected_parent_fp=expected_parent_fp,
        )

    def native_batch_transform_entities(
        self,
        semantic_pids: list[str],
        transform: dict[str, Any],
        document_pid: str,
        expected_parent_fp: str,
    ) -> dict[str, Any]:
        return self._native_facade.batch_transform_entities(
            semantic_pids,
            transform,
            document_pid=document_pid,
            expected_parent_fp=expected_parent_fp,
        )

    def native_metadata_set(
        self,
        semantic_pid: str,
        namespace: str,
        value: Any,
        document_pid: str,
        expected_parent_fp: str,
    ) -> dict[str, Any]:
        return self._native_facade.metadata_set(
            semantic_pid,
            namespace,
            value,
            document_pid=document_pid,
            expected_parent_fp=expected_parent_fp,
        )

    # -- AutoCADBackend contract: explicit 1:1 delegation, no semantics change --

    async def document_new(self) -> dict[str, Any]:
        return await self._backend.document_new()

    async def document_open(self, path: str) -> dict[str, Any]:
        return await self._backend.document_open(path)

    async def document_info(self) -> dict[str, Any]:
        return await self._backend.document_info()

    async def document_save(self, path: str | None = None) -> dict[str, Any]:
        return await self._backend.document_save(path)

    async def document_save_as(self, path: str) -> dict[str, Any]:
        return await self._backend.document_save_as(path)

    async def document_export_pdf(self, path: str, layout: str | None = None) -> dict[str, Any]:
        return await self._backend.document_export_pdf(path, layout)

    async def drawing_audit(self) -> dict[str, Any]:
        return await self._backend.drawing_audit()

    async def drawing_purge(self) -> dict[str, Any]:
        return await self._backend.drawing_purge()

    async def document_configure_units(
        self,
        insertion_units: str | None = None,
        measurement: str | None = None,
        linear_format: str | None = None,
        linear_precision: int | None = None,
    ) -> dict[str, Any]:
        return await self._backend.document_configure_units(
            insertion_units=insertion_units,
            measurement=measurement,
            linear_format=linear_format,
            linear_precision=linear_precision,
        )

    async def document_dependencies(self) -> dict[str, Any]:
        return await self._backend.document_dependencies()

    async def artifact_seal(self, destination_dir: str | None = None) -> dict[str, Any]:
        return await self._backend.artifact_seal(destination_dir)

    async def object_list(
        self,
        type_filter: str | None = None,
        layer_filter: str | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> list[EntityInfo]:
        return await self._backend.object_list(type_filter, layer_filter, limit, offset)

    async def object_get(self, object_id: str) -> EntityInfo:
        return await self._backend.object_get(object_id)

    async def object_count(
        self,
        type_filter: str | None = None,
        layer_filter: str | None = None,
    ) -> int:
        return await self._backend.object_count(type_filter, layer_filter)

    async def object_set_properties(self, object_id: str, **kwargs: Any) -> EntityInfo:
        return await self._backend.object_set_properties(object_id, **kwargs)

    async def object_delete(self, object_id: str) -> dict[str, Any]:
        return await self._backend.object_delete(object_id)

    async def object_move(self, object_id: str, dx: float, dy: float, dz: float = 0.0) -> EntityInfo:
        return await self._backend.object_move(object_id, dx, dy, dz)

    async def object_copy(self, object_id: str, dx: float, dy: float, dz: float = 0.0) -> EntityInfo:
        return await self._backend.object_copy(object_id, dx, dy, dz)

    async def object_rotate(
        self, object_id: str, base_x: float, base_y: float, angle_deg: float
    ) -> EntityInfo:
        return await self._backend.object_rotate(object_id, base_x, base_y, angle_deg)

    async def object_scale(
        self, object_id: str, base_x: float, base_y: float, factor: float
    ) -> EntityInfo:
        return await self._backend.object_scale(object_id, base_x, base_y, factor)

    async def entity_create_line(self, **kwargs: Any) -> EntityInfo:
        return await self._backend.entity_create_line(**kwargs)

    async def entity_create_circle(self, **kwargs: Any) -> EntityInfo:
        return await self._backend.entity_create_circle(**kwargs)

    async def entity_create_arc(self, **kwargs: Any) -> EntityInfo:
        return await self._backend.entity_create_arc(**kwargs)

    async def entity_create_polyline(self, **kwargs: Any) -> EntityInfo:
        return await self._backend.entity_create_polyline(**kwargs)

    async def entity_create_text(self, **kwargs: Any) -> EntityInfo:
        return await self._backend.entity_create_text(**kwargs)

    async def hatch_create(self, **kwargs: Any) -> EntityInfo:
        return await self._backend.hatch_create(**kwargs)

    async def dimension_linear(self, **kwargs: Any) -> EntityInfo:
        return await self._backend.dimension_linear(**kwargs)

    async def dimension_aligned(self, **kwargs: Any) -> EntityInfo:
        return await self._backend.dimension_aligned(**kwargs)

    async def dimension_angular(self, **kwargs: Any) -> EntityInfo:
        return await self._backend.dimension_angular(**kwargs)

    async def dimension_radial(self, **kwargs: Any) -> EntityInfo:
        return await self._backend.dimension_radial(**kwargs)

    async def dimension_diametric(self, **kwargs: Any) -> EntityInfo:
        return await self._backend.dimension_diametric(**kwargs)

    async def dimension_ordinate(self, **kwargs: Any) -> EntityInfo:
        return await self._backend.dimension_ordinate(**kwargs)

    async def layer_list(self) -> list[LayerInfo]:
        return await self._backend.layer_list()

    async def layer_create(self, name: str, color: int = 7) -> LayerInfo:
        return await self._backend.layer_create(name, color)

    async def layer_set_current(self, name: str) -> dict[str, Any]:
        return await self._backend.layer_set_current(name)

    async def layer_update_state(
        self,
        name: str,
        *,
        is_on: bool | None = None,
        is_frozen: bool | None = None,
        is_locked: bool | None = None,
        color: int | None = None,
        linetype: str | None = None,
        lineweight: int | None = None,
    ) -> LayerInfo:
        return await self._backend.layer_update_state(
            name,
            is_on=is_on,
            is_frozen=is_frozen,
            is_locked=is_locked,
            color=color,
            linetype=linetype,
            lineweight=lineweight,
        )

    async def block_list(
        self,
        include_xref_dependent: bool = False,
        include_xrefs: bool = True,
        name_filter: str | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> list[BlockInfo]:
        return await self._backend.block_list(
            include_xref_dependent, include_xrefs, name_filter, limit, offset
        )

    async def block_create(
        self, name: str, object_ids: list[str], base_x: float = 0.0, base_y: float = 0.0
    ) -> BlockInfo:
        return await self._backend.block_create(name, object_ids, base_x, base_y)

    async def block_insert(self, name: str, x: float, y: float, **kwargs: Any) -> EntityInfo:
        return await self._backend.block_insert(name, x, y, **kwargs)

    async def xref_list(self) -> list[dict[str, Any]]:
        return await self._backend.xref_list()

    async def xref_attach(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return await self._backend.xref_attach(*args, **kwargs)

    async def xref_reload(self, name: str) -> dict[str, Any]:
        return await self._backend.xref_reload(name)

    async def xref_unload(self, name: str) -> dict[str, Any]:
        return await self._backend.xref_unload(name)

    async def xref_detach(self, name: str) -> dict[str, Any]:
        return await self._backend.xref_detach(name)

    async def layout_list(self) -> dict[str, Any]:
        return await self._backend.layout_list()

    async def layout_create(self, name: str) -> dict[str, Any]:
        return await self._backend.layout_create(name)

    async def layout_set_current(self, name: str) -> dict[str, Any]:
        return await self._backend.layout_set_current(name)

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
        return await self._backend.viewport_create(
            layout, center_x, center_y, width, height, view_center_x, view_center_y, scale
        )

    async def viewport_list(self, layout: str | None = None) -> dict[str, Any]:
        return await self._backend.viewport_list(layout)

    async def viewport_set_scale(self, handle: str, scale: float) -> dict[str, Any]:
        return await self._backend.viewport_set_scale(handle, scale)

    async def viewport_lock(self, handle: str, locked: bool = True) -> dict[str, Any]:
        return await self._backend.viewport_lock(handle, locked)

    async def viewport_delete(self, handle: str, force: bool = False) -> dict[str, Any]:
        return await self._backend.viewport_delete(handle, force)

    async def view_zoom_extents(self) -> dict[str, Any]:
        return await self._backend.view_zoom_extents()

    async def view_zoom_window(
        self, x1: float, y1: float, x2: float, y2: float
    ) -> dict[str, Any]:
        return await self._backend.view_zoom_window(x1, y1, x2, y2)

    async def view_screenshot(self) -> bytes:
        return await self._backend.view_screenshot()

    async def view_set_direction(self, dx: float, dy: float, dz: float) -> dict[str, Any]:
        return await self._backend.view_set_direction(dx, dy, dz)

    async def view_set_preset(self, preset: str) -> dict[str, Any]:
        return await self._backend.view_set_preset(preset)

    async def view_set_visual_style(self, style: str) -> dict[str, Any]:
        return await self._backend.view_set_visual_style(style)

    async def entity_create_3d_polyline(
        self, points: list[list[float]], closed: bool = False
    ) -> dict[str, Any]:
        return await self._backend.entity_create_3d_polyline(points, closed)

    async def solid_box(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return await self._backend.solid_box(*args, **kwargs)

    async def solid_cylinder(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return await self._backend.solid_cylinder(*args, **kwargs)

    async def solid_sphere(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return await self._backend.solid_sphere(*args, **kwargs)

    async def solid_cone(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return await self._backend.solid_cone(*args, **kwargs)

    async def solid_torus(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return await self._backend.solid_torus(*args, **kwargs)

    async def solid_wedge(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return await self._backend.solid_wedge(*args, **kwargs)

    async def solid_extrude(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return await self._backend.solid_extrude(*args, **kwargs)

    async def solid_sweep(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return await self._backend.solid_sweep(*args, **kwargs)

    async def solid_revolve(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return await self._backend.solid_revolve(*args, **kwargs)

    async def solid_boolean(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return await self._backend.solid_boolean(*args, **kwargs)

    async def solid_move(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return await self._backend.solid_move(*args, **kwargs)

    async def solid_rotate3d(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return await self._backend.solid_rotate3d(*args, **kwargs)

    async def solid_scale3d(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return await self._backend.solid_scale3d(*args, **kwargs)

    async def solid_mirror3d(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return await self._backend.solid_mirror3d(*args, **kwargs)

    async def solid_inspect(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return await self._backend.solid_inspect(*args, **kwargs)

    async def solid_export(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return await self._backend.solid_export(*args, **kwargs)

    async def object_measure(self, object_id: str) -> dict[str, Any]:
        return await self._backend.object_measure(object_id)

    async def drawing_extents(self) -> dict[str, Any]:
        return await self._backend.drawing_extents()

    async def object_intersections(
        self, first_id: str, second_id: str, extend_mode: str = "none"
    ) -> dict[str, Any]:
        return await self._backend.object_intersections(first_id, second_id, extend_mode)

    async def transaction_begin(self) -> dict[str, Any]:
        return await self._backend.transaction_begin()

    async def transaction_commit(self) -> dict[str, Any]:
        return await self._backend.transaction_commit()

    async def transaction_rollback(self) -> dict[str, Any]:
        return await self._backend.transaction_rollback()

    async def undo(self) -> dict[str, Any]:
        return await self._backend.undo()

    async def redo(self) -> dict[str, Any]:
        return await self._backend.redo()
