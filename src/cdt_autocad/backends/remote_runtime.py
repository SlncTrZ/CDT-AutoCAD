"""Remote single-writer-safe runtime adapter — R2 split-process path over RuntimeTransport.
Wing: code | Topic: migration-r2-remote | Updated: 2026-10-07 15:40

Reuses the native implementation WITHOUT duplication: the workstation agent
owns the exact same R1 adapter/ComBackend/native-bridge stack in its own
process, and this class only forwards typed op names + JSON args over the
RuntimeTransport boundary. No CAD validation, PID, fingerprint, recovery or
COM logic lives here — unknown completion, generation and fencing flow
through the same typed errors as the local path.

Fencing placement (R2): the ONE writer authority is the provider-side shared
MutationCoordinator injected here (same instance pattern as R1). Mutation ops
enter ``coordinator.writer("remote")`` before transport dispatch, so a second
provider-side writer still serializes and a quarantined coordinator still
blocks later mutation while leaving reads available. The agent holds NO
coordinator (plain serial lock only — see workstation_agent.py), so no
duplicate quarantine authority can diverge across the process boundary.
Timeout/disconnect after dispatch starts quarantines the provider coordinator
and raises completion-unknown; blind replay stays forbidden.

Loopback-topology rule (found by R4 parity): the agent-side backend/adapter
pair must own a DISTINCT coordinator object from the provider-side one, even
when both live in one process for loopback tests. Sharing a single
MutationCoordinator across the transport boundary double-enters the same
asyncio writer lock (provider ``writer("remote")`` held across the HTTP
round-trip while the agent worker awaits ``writer("com")``) and deadlocks.
Split-host deployment satisfies this rule structurally (separate processes);
loopback harnesses must construct the agent side with its own coordinator.
Uncertainty still fences both sides independently.
"""

from __future__ import annotations

import asyncio
from typing import Any

from ..models import BlockInfo, EntityInfo, LayerInfo
from ..mutation_coordinator import MutationCoordinator
from ..runtime_transport import (
    ALLOWED_OPS,
    DEFAULT_DEADLINE_MS,
    MUTATION_OPS,
    BackendTimeoutError,
    MutationCompletionUncertainError,
    RuntimeOpRefusedError,
    RuntimeTransport,
    RuntimeTransportError,
    RuntimeUnavailableError,
    RuntimeUncertainError,
    check_deadline,
)
from .base import AutoCADBackend
from .runtime_port import AutoCADRuntimePort


def _entity(data: Any, *, op: str) -> EntityInfo:
    if isinstance(data, EntityInfo):
        return data
    if not isinstance(data, dict):
        raise RuntimeTransportError(f"remote op {op!r} returned malformed entity")
    try:
        return EntityInfo(
            id=str(data["id"]),
            type=str(data["type"]),
            layer=str(data["layer"]),
            color=int(data["color"]),
            linetype=str(data["linetype"]),
            visible=bool(data["visible"]),
            properties=dict(data.get("properties") or {}),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeTransportError(f"remote op {op!r} returned malformed entity: {exc}") from exc


def _entity_list(data: Any, *, op: str) -> list[EntityInfo]:
    if not isinstance(data, list):
        raise RuntimeTransportError(f"remote op {op!r} returned malformed entity list")
    return [_entity(item, op=op) for item in data]


def _layer(data: Any, *, op: str) -> LayerInfo:
    if isinstance(data, LayerInfo):
        return data
    if not isinstance(data, dict):
        raise RuntimeTransportError(f"remote op {op!r} returned malformed layer")
    try:
        return LayerInfo(
            name=str(data["name"]),
            color=int(data["color"]),
            linetype=str(data["linetype"]),
            lineweight=int(data["lineweight"]),
            is_on=bool(data["is_on"]),
            is_frozen=bool(data["is_frozen"]),
            is_locked=bool(data["is_locked"]),
            is_current=bool(data["is_current"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeTransportError(f"remote op {op!r} returned malformed layer: {exc}") from exc


def _block(data: Any, *, op: str) -> BlockInfo:
    if isinstance(data, BlockInfo):
        return data
    if not isinstance(data, dict):
        raise RuntimeTransportError(f"remote op {op!r} returned malformed block")
    try:
        base = data["base_point"]
        return BlockInfo(
            name=str(data["name"]),
            base_point=(float(base[0]), float(base[1]), float(base[2])),
            entity_count=int(data["entity_count"]),
            attribute_count=int(data["attribute_count"]),
            is_xref=bool(data["is_xref"]),
        )
    except (KeyError, TypeError, ValueError, IndexError) as exc:
        raise RuntimeTransportError(f"remote op {op!r} returned malformed block: {exc}") from exc


def _bytes_result(data: Any, *, op: str) -> bytes:
    import base64

    if isinstance(data, bytes):
        return data
    if isinstance(data, dict) and isinstance(data.get("__bytes_b64"), str):
        try:
            return base64.b64decode(data["__bytes_b64"])
        except ValueError as exc:
            raise RuntimeTransportError(f"remote op {op!r} returned bad bytes: {exc}") from exc
    raise RuntimeTransportError(f"remote op {op!r} returned malformed bytes")


class RemoteAutoCADRuntimeAdapter(AutoCADBackend, AutoCADRuntimePort):
    """Provider-side adapter: full backend contract over a RuntimeTransport.

    Constructor takes the ONE shared provider MutationCoordinator (no new
    authority is created) and the expected runtime generation observed from
    the agent heartbeat (None pins nothing; a pinned value refuses stale
    generations before trusting results).
    """

    def __init__(
        self,
        transport: RuntimeTransport,
        *,
        mutation_coordinator: MutationCoordinator,
        backend_name: str = "com",
        expected_generation: str | None = None,
        default_deadline_ms: int = DEFAULT_DEADLINE_MS,
        settings: Any = None,
    ) -> None:
        from ..runtime_transport import RuntimeTransport as _Transport

        if not isinstance(transport, _Transport):
            raise TypeError("RemoteAutoCADRuntimeAdapter requires a RuntimeTransport")
        if not isinstance(mutation_coordinator, MutationCoordinator):
            raise TypeError("RemoteAutoCADRuntimeAdapter requires one shared MutationCoordinator")
        if str(backend_name or "").strip() not in {"com", "ezdxf"}:
            raise ValueError("backend_name must be 'com' or 'ezdxf'")
        self._transport = transport
        self._mutation_coordinator = mutation_coordinator
        self._backend_name = str(backend_name)
        self._expected_generation = expected_generation
        self._default_deadline_ms = check_deadline(default_deadline_ms)
        self._settings = settings
        self._last_available = False

    # -- port surface --

    @property
    def backend(self) -> AutoCADBackend:
        # No in-process backend exists on this side by design; the native
        # implementation lives in the agent process. Fail closed instead of
        # fabricating a local escape hatch.
        raise RuntimeUnavailableError(
            "remote adapter has no in-process backend; native execution lives "
            "in the workstation agent process"
        )

    @property
    def mutation_coordinator(self) -> MutationCoordinator:
        return self._mutation_coordinator

    @property
    def expected_generation(self) -> str | None:
        return self._expected_generation

    def pin_generation(self, generation: str) -> None:
        value = str(generation or "").strip()
        if not value:
            raise ValueError("generation pin must be non-empty")
        self._expected_generation = value

    @property
    def name(self) -> str:
        return self._backend_name

    @property
    def runtime_available(self) -> bool:
        return self._last_available

    @property
    def settings(self) -> Any:
        return self._settings

    def shutdown(self) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(self._transport.close())
        else:
            loop.create_task(self._transport.close())

    def capabilities(self) -> dict[str, dict[str, Any]]:
        raise RuntimeUnavailableError(
            "remote capabilities() is async-only; await the transport call instead"
        )

    def status(self) -> dict[str, Any]:
        raise RuntimeUnavailableError(
            "remote status() is async-only; await the transport call instead"
        )

    async def capabilities_async(self) -> dict[str, dict[str, Any]]:
        result = await self._call("capabilities")
        return result if isinstance(result, dict) else {}

    async def status_async(self) -> dict[str, Any]:
        result = await self._call("status")
        return result if isinstance(result, dict) else {}

    async def runtime_status(self) -> dict[str, Any]:
        result = await self._call("runtime_status")
        if isinstance(result, dict):
            return result
        return await self._call("status")

    async def health(self) -> dict[str, Any]:
        try:
            result = await self._transport.health()
        except RuntimeTransportError:
            self._last_available = False
            raise
        self._last_available = True
        base: dict[str, Any] = {
            "backend": self._backend_name,
            "transport": "remote",
            "runtime_available": True,
        }
        if isinstance(result, dict):
            base["agent"] = result
        coordinator = self._mutation_coordinator.status()
        base["quarantined"] = bool(coordinator.get("quarantined", False))
        base["mutation_coordinator"] = coordinator
        return base

    # -- dispatch core: no CAD semantics, only fencing + uncertainty --

    async def _call(self, op: str, *args: Any, **kwargs: Any) -> Any:
        if op not in ALLOWED_OPS:
            raise RuntimeOpRefusedError(f"remote op refused (not in allowlist): {op!r}")
        dispatched = False
        try:
            if op in MUTATION_OPS:
                async with self._mutation_coordinator.writer("remote"):
                    dispatched = True
                    result = await self._transport.call(
                        op,
                        args,
                        kwargs,
                        deadline_ms=self._default_deadline_ms,
                        expected_generation=self._expected_generation,
                    )
            else:
                result = await self._transport.call(
                    op,
                    args,
                    kwargs,
                    deadline_ms=self._default_deadline_ms,
                    expected_generation=self._expected_generation,
                )
        except (RuntimeUncertainError, MutationCompletionUncertainError) as exc:
            if op in MUTATION_OPS:
                self._mutation_coordinator.quarantine("remote", str(exc))
            self._last_available = True  # endpoint answered; CAD outcome unknown
            if isinstance(exc, MutationCompletionUncertainError):
                raise
            raise MutationCompletionUncertainError(str(exc)) from exc
        except BackendTimeoutError as exc:
            if bool(exc.completion_unknown) and op in MUTATION_OPS:
                self._mutation_coordinator.quarantine("remote", str(exc))
                raise MutationCompletionUncertainError(str(exc)) from exc
            raise
        except asyncio.CancelledError:
            if op in MUTATION_OPS and dispatched:
                self._mutation_coordinator.quarantine(
                    "remote",
                    f"remote op {op!r} cancelled after dispatch; completion is unknown",
                )
            raise
        self._last_available = True
        return result

    # -- AutoCADBackend contract: thin forwarders, zero CAD logic --

    async def document_new(self) -> dict[str, Any]:
        return await self._call("document_new")

    async def document_open(self, path: str) -> dict[str, Any]:
        return await self._call("document_open", path)

    async def document_info(self) -> dict[str, Any]:
        return await self._call("document_info")

    async def document_save(self, path: str | None = None) -> dict[str, Any]:
        return await self._call("document_save", path)

    async def document_save_as(self, path: str) -> dict[str, Any]:
        return await self._call("document_save_as", path)

    async def document_export_pdf(self, path: str, layout: str | None = None) -> dict[str, Any]:
        return await self._call("document_export_pdf", path, layout)

    async def drawing_audit(self) -> dict[str, Any]:
        return await self._call("drawing_audit")

    async def drawing_purge(self) -> dict[str, Any]:
        return await self._call("drawing_purge")

    async def document_configure_units(
        self,
        insertion_units: str | None = None,
        measurement: str | None = None,
        linear_format: str | None = None,
        linear_precision: int | None = None,
    ) -> dict[str, Any]:
        return await self._call(
            "document_configure_units",
            insertion_units=insertion_units,
            measurement=measurement,
            linear_format=linear_format,
            linear_precision=linear_precision,
        )

    async def document_dependencies(self) -> dict[str, Any]:
        return await self._call("document_dependencies")

    async def artifact_seal(self, destination_dir: str | None = None) -> dict[str, Any]:
        return await self._call("artifact_seal", destination_dir)

    async def object_list(
        self,
        type_filter: str | None = None,
        layer_filter: str | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> list[EntityInfo]:
        return _entity_list(
            await self._call("object_list", type_filter, layer_filter, limit, offset),
            op="object_list",
        )

    async def object_get(self, object_id: str) -> EntityInfo:
        return _entity(await self._call("object_get", object_id), op="object_get")

    async def object_count(
        self,
        type_filter: str | None = None,
        layer_filter: str | None = None,
    ) -> int:
        return int(await self._call("object_count", type_filter, layer_filter))

    async def object_set_properties(self, object_id: str, **kwargs: Any) -> EntityInfo:
        return _entity(
            await self._call("object_set_properties", object_id, **kwargs),
            op="object_set_properties",
        )

    async def object_delete(self, object_id: str) -> dict[str, Any]:
        return await self._call("object_delete", object_id)

    async def object_move(self, object_id: str, dx: float, dy: float, dz: float = 0.0) -> EntityInfo:
        return _entity(await self._call("object_move", object_id, dx, dy, dz), op="object_move")

    async def object_copy(self, object_id: str, dx: float, dy: float, dz: float = 0.0) -> EntityInfo:
        return _entity(await self._call("object_copy", object_id, dx, dy, dz), op="object_copy")

    async def object_rotate(
        self, object_id: str, base_x: float, base_y: float, angle_deg: float
    ) -> EntityInfo:
        return _entity(
            await self._call("object_rotate", object_id, base_x, base_y, angle_deg),
            op="object_rotate",
        )

    async def object_scale(
        self, object_id: str, base_x: float, base_y: float, factor: float
    ) -> EntityInfo:
        return _entity(
            await self._call("object_scale", object_id, base_x, base_y, factor),
            op="object_scale",
        )

    async def entity_create_line(self, **kwargs: Any) -> EntityInfo:
        return _entity(await self._call("entity_create_line", **kwargs), op="entity_create_line")

    async def entity_create_circle(self, **kwargs: Any) -> EntityInfo:
        return _entity(
            await self._call("entity_create_circle", **kwargs), op="entity_create_circle"
        )

    async def entity_create_arc(self, **kwargs: Any) -> EntityInfo:
        return _entity(await self._call("entity_create_arc", **kwargs), op="entity_create_arc")

    async def entity_create_polyline(self, **kwargs: Any) -> EntityInfo:
        return _entity(
            await self._call("entity_create_polyline", **kwargs), op="entity_create_polyline"
        )

    async def entity_create_text(self, **kwargs: Any) -> EntityInfo:
        return _entity(await self._call("entity_create_text", **kwargs), op="entity_create_text")

    async def hatch_create(self, **kwargs: Any) -> EntityInfo:
        return _entity(await self._call("hatch_create", **kwargs), op="hatch_create")

    async def dimension_linear(self, **kwargs: Any) -> EntityInfo:
        return _entity(await self._call("dimension_linear", **kwargs), op="dimension_linear")

    async def dimension_aligned(self, **kwargs: Any) -> EntityInfo:
        return _entity(await self._call("dimension_aligned", **kwargs), op="dimension_aligned")

    async def dimension_angular(self, **kwargs: Any) -> EntityInfo:
        return _entity(await self._call("dimension_angular", **kwargs), op="dimension_angular")

    async def dimension_radial(self, **kwargs: Any) -> EntityInfo:
        return _entity(await self._call("dimension_radial", **kwargs), op="dimension_radial")

    async def dimension_diametric(self, **kwargs: Any) -> EntityInfo:
        return _entity(
            await self._call("dimension_diametric", **kwargs), op="dimension_diametric"
        )

    async def dimension_ordinate(self, **kwargs: Any) -> EntityInfo:
        return _entity(await self._call("dimension_ordinate", **kwargs), op="dimension_ordinate")

    async def layer_list(self) -> list[LayerInfo]:
        raw = await self._call("layer_list")
        if not isinstance(raw, list):
            raise RuntimeTransportError("remote op 'layer_list' returned malformed list")
        return [_layer(item, op="layer_list") for item in raw]

    async def layer_create(self, name: str, color: int = 7) -> LayerInfo:
        return _layer(await self._call("layer_create", name, color), op="layer_create")

    async def layer_set_current(self, name: str) -> dict[str, Any]:
        return await self._call("layer_set_current", name)

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
        return _layer(
            await self._call(
                "layer_update_state",
                name,
                is_on=is_on,
                is_frozen=is_frozen,
                is_locked=is_locked,
                color=color,
                linetype=linetype,
                lineweight=lineweight,
            ),
            op="layer_update_state",
        )

    async def block_list(
        self,
        include_xref_dependent: bool = False,
        include_xrefs: bool = True,
        name_filter: str | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> list[BlockInfo]:
        raw = await self._call(
            "block_list", include_xref_dependent, include_xrefs, name_filter, limit, offset
        )
        if not isinstance(raw, list):
            raise RuntimeTransportError("remote op 'block_list' returned malformed list")
        return [_block(item, op="block_list") for item in raw]

    async def block_create(
        self, name: str, object_ids: list[str], base_x: float = 0.0, base_y: float = 0.0
    ) -> BlockInfo:
        return _block(
            await self._call("block_create", name, object_ids, base_x, base_y),
            op="block_create",
        )

    async def block_insert(self, name: str, x: float, y: float, **kwargs: Any) -> EntityInfo:
        return _entity(await self._call("block_insert", name, x, y, **kwargs), op="block_insert")

    async def xref_list(self) -> list[dict[str, Any]]:
        return await self._call("xref_list")

    async def xref_attach(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return await self._call("xref_attach", *args, **kwargs)

    async def xref_reload(self, name: str) -> dict[str, Any]:
        return await self._call("xref_reload", name)

    async def xref_unload(self, name: str) -> dict[str, Any]:
        return await self._call("xref_unload", name)

    async def xref_detach(self, name: str) -> dict[str, Any]:
        return await self._call("xref_detach", name)

    async def layout_list(self) -> dict[str, Any]:
        return await self._call("layout_list")

    async def layout_create(self, name: str) -> dict[str, Any]:
        return await self._call("layout_create", name)

    async def layout_set_current(self, name: str) -> dict[str, Any]:
        return await self._call("layout_set_current", name)

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
        return await self._call(
            "viewport_create",
            layout,
            center_x,
            center_y,
            width,
            height,
            view_center_x,
            view_center_y,
            scale,
        )

    async def viewport_list(self, layout: str | None = None) -> dict[str, Any]:
        return await self._call("viewport_list", layout)

    async def viewport_set_scale(self, handle: str, scale: float) -> dict[str, Any]:
        return await self._call("viewport_set_scale", handle, scale)

    async def viewport_lock(self, handle: str, locked: bool = True) -> dict[str, Any]:
        return await self._call("viewport_lock", handle, locked)

    async def viewport_delete(self, handle: str, force: bool = False) -> dict[str, Any]:
        return await self._call("viewport_delete", handle, force)

    async def view_zoom_extents(self) -> dict[str, Any]:
        return await self._call("view_zoom_extents")

    async def view_zoom_window(
        self, x1: float, y1: float, x2: float, y2: float
    ) -> dict[str, Any]:
        return await self._call("view_zoom_window", x1, y1, x2, y2)

    async def view_screenshot(self) -> bytes:
        return _bytes_result(await self._call("view_screenshot"), op="view_screenshot")

    async def view_set_direction(self, dx: float, dy: float, dz: float) -> dict[str, Any]:
        return await self._call("view_set_direction", dx, dy, dz)

    async def view_set_preset(self, preset: str) -> dict[str, Any]:
        return await self._call("view_set_preset", preset)

    async def view_set_visual_style(self, style: str) -> dict[str, Any]:
        return await self._call("view_set_visual_style", style)

    async def entity_create_3d_polyline(
        self, points: list[list[float]], closed: bool = False
    ) -> dict[str, Any]:
        return await self._call("entity_create_3d_polyline", points, closed)

    async def solid_box(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return await self._call("solid_box", *args, **kwargs)

    async def solid_cylinder(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return await self._call("solid_cylinder", *args, **kwargs)

    async def solid_sphere(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return await self._call("solid_sphere", *args, **kwargs)

    async def solid_cone(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return await self._call("solid_cone", *args, **kwargs)

    async def solid_torus(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return await self._call("solid_torus", *args, **kwargs)

    async def solid_wedge(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return await self._call("solid_wedge", *args, **kwargs)

    async def solid_extrude(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return await self._call("solid_extrude", *args, **kwargs)

    async def solid_sweep(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return await self._call("solid_sweep", *args, **kwargs)

    async def solid_revolve(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return await self._call("solid_revolve", *args, **kwargs)

    async def solid_boolean(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return await self._call("solid_boolean", *args, **kwargs)

    async def solid_move(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return await self._call("solid_move", *args, **kwargs)

    async def solid_rotate3d(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return await self._call("solid_rotate3d", *args, **kwargs)

    async def solid_scale3d(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return await self._call("solid_scale3d", *args, **kwargs)

    async def solid_mirror3d(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return await self._call("solid_mirror3d", *args, **kwargs)

    async def solid_inspect(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return await self._call("solid_inspect", *args, **kwargs)

    async def solid_export(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return await self._call("solid_export", *args, **kwargs)

    async def object_measure(self, object_id: str) -> dict[str, Any]:
        return await self._call("object_measure", object_id)

    async def drawing_extents(self) -> dict[str, Any]:
        return await self._call("drawing_extents")

    async def object_intersections(
        self, first_id: str, second_id: str, extend_mode: str = "none"
    ) -> dict[str, Any]:
        return await self._call("object_intersections", first_id, second_id, extend_mode)

    async def transaction_begin(self) -> dict[str, Any]:
        return await self._call("transaction_begin")

    async def transaction_commit(self) -> dict[str, Any]:
        return await self._call("transaction_commit")

    async def transaction_rollback(self) -> dict[str, Any]:
        return await self._call("transaction_rollback")

    async def undo(self) -> dict[str, Any]:
        return await self._call("undo")

    async def redo(self) -> dict[str, Any]:
        return await self._call("redo")

    # -- native strong-integrity surface (R3/R4): thin forwarders, zero CAD --
    # -- logic. Reads bypass the writer lane; mutations enter the shared    --
    # -- provider coordinator via _call (MUTATION_OPS) so quarantine blocks  --
    # -- later mutation while reads stay available. Results are facade dicts --
    # -- (JSON-safe); they pass through without reinterpretation.            --

    async def native_status(self) -> dict[str, Any]:
        return await self._call("native_status")

    async def native_document_state(self) -> dict[str, Any]:
        return await self._call("native_document_state")

    async def native_snapshot_page(
        self, offset: int = 0, limit: int = 50
    ) -> dict[str, Any]:
        return await self._call("native_snapshot_page", offset, limit)

    async def native_metadata_get(
        self, semantic_pid: str, namespace: str
    ) -> dict[str, Any]:
        return await self._call("native_metadata_get", semantic_pid, namespace)

    async def native_metadata_query(
        self,
        namespace: str,
        path: str | None = None,
        equals: Any = None,
        limit: int = 200,
        offset: int = 0,
    ) -> dict[str, Any]:
        return await self._call(
            "native_metadata_query",
            namespace,
            path=path,
            equals=equals,
            limit=limit,
            offset=offset,
        )

    async def native_feature_execute(
        self,
        document_pid: str,
        expected_parent_fp: str,
        feature_id: str,
        feature_sequence: int,
        correlation_id: str,
        actions: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return await self._call(
            "native_feature_execute",
            document_pid,
            expected_parent_fp,
            feature_id,
            feature_sequence,
            correlation_id,
            actions,
        )

    async def native_batch_create_entities(
        self,
        entities: list[dict[str, Any]],
        document_pid: str,
        expected_parent_fp: str,
    ) -> dict[str, Any]:
        return await self._call(
            "native_batch_create_entities", entities, document_pid, expected_parent_fp
        )

    async def native_batch_transform_entities(
        self,
        semantic_pids: list[str],
        transform: dict[str, Any],
        document_pid: str,
        expected_parent_fp: str,
    ) -> dict[str, Any]:
        return await self._call(
            "native_batch_transform_entities",
            semantic_pids,
            transform,
            document_pid,
            expected_parent_fp,
        )

    async def native_metadata_set(
        self,
        semantic_pid: str,
        namespace: str,
        value: Any,
        document_pid: str,
        expected_parent_fp: str,
    ) -> dict[str, Any]:
        return await self._call(
            "native_metadata_set",
            semantic_pid,
            namespace,
            value,
            document_pid,
            expected_parent_fp,
        )
