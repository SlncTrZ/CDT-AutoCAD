"""Headless DXF backend for the CDT_Engineer AutoCAD provider.
Wing: code | Topic: autocad-a3-dimensions | Updated: 2026-09-09 20:44

The dual-engine shape and several edge-case choices are informed by the MIT-licensed
U-C4N/Autocad-MCP reference, but this implementation is normalized to the CDT A0/A1
contract rather than reproducing the upstream server surface.
"""

from __future__ import annotations

import asyncio
import gzip
import io
import math
import os
import tempfile
from collections.abc import Callable
from importlib.util import find_spec
from pathlib import Path
from typing import Any, TypeVar

import ezdxf
from ezdxf.math import Matrix44

from ..config import Settings
from ..errors import (
    BackendQuarantinedError,
    BackendTimeoutError,
    StateConflictError,
    UnsupportedCapabilityError,
)
from ..models import BlockInfo, Capability, EntityInfo, LayerInfo
from ..security import resolve_dxf_path, resolve_pdf_path
from .base import AutoCADBackend

_T = TypeVar("_T")

_UNIT_NAMES = {
    0: "unitless",
    1: "inches",
    2: "feet",
    3: "miles",
    4: "mm",
    5: "cm",
    6: "m",
    7: "km",
    8: "microinches",
    9: "mils",
    10: "yards",
    11: "angstroms",
    12: "nm",
    13: "microns",
    14: "dm",
}


def _point(value: Any) -> list[float]:
    if hasattr(value, "x"):
        return [float(value.x), float(value.y), float(getattr(value, "z", 0.0))]
    seq = list(value)
    return [float(seq[0]), float(seq[1]), float(seq[2]) if len(seq) > 2 else 0.0]


def _entity_info(entity: Any) -> EntityInfo:
    entity_type = entity.dxftype()
    properties: dict[str, Any] = {}

    if entity_type == "LINE":
        properties = {
            "start": _point(entity.dxf.start),
            "end": _point(entity.dxf.end),
            "coordinate_frame": "wcs",
        }
    elif entity_type == "CIRCLE":
        properties = {
            "center": _point(entity.dxf.center),
            "radius": float(entity.dxf.radius),
            "coordinate_frame": "ocs",
        }
    elif entity_type == "ARC":
        properties = {
            "center": _point(entity.dxf.center),
            "radius": float(entity.dxf.radius),
            "start_angle": float(entity.dxf.start_angle),
            "end_angle": float(entity.dxf.end_angle),
            "coordinate_frame": "ocs",
        }
    elif entity_type == "LWPOLYLINE":
        properties = {
            "points": [[float(p[0]), float(p[1])] for p in entity.get_points()],
            "closed": bool(entity.closed),
            "coordinate_frame": "ocs",
        }
    elif entity_type == "TEXT":
        properties = {
            "text": str(entity.dxf.text),
            "insert": _point(entity.dxf.insert),
            "height": float(entity.dxf.height),
            "rotation": float(entity.dxf.get("rotation", 0.0)),
            "coordinate_frame": "ocs",
        }
    elif entity_type == "INSERT":
        properties = {
            "block_name": str(entity.dxf.name),
            "insert": _point(entity.dxf.insert),
            "x_scale": float(entity.dxf.get("xscale", 1.0)),
            "y_scale": float(entity.dxf.get("yscale", 1.0)),
            "rotation": float(entity.dxf.get("rotation", 0.0)),
        }
    elif entity_type == "DIMENSION":
        properties = {
            "dimstyle": str(entity.dxf.get("dimstyle", "Standard")),
            "text": str(entity.dxf.get("text", "<>")),
        }
    elif entity_type == "HATCH":
        properties = {
            "pattern_name": str(entity.dxf.get("pattern_name", "SOLID")),
            "solid_fill": bool(entity.dxf.get("solid_fill", 0)),
        }

    return EntityInfo(
        id=str(entity.dxf.get("handle", "?")),
        type=entity_type,
        layer=str(entity.dxf.get("layer", "0")),
        color=int(entity.dxf.get("color", 256)),
        linetype=str(entity.dxf.get("linetype", "ByLayer")),
        visible=not bool(entity.dxf.get("invisible", False)),
        properties=properties,
    )


def _layer_info(layer: Any, current_layer: str) -> LayerInfo:
    return LayerInfo(
        name=str(layer.dxf.name),
        color=abs(int(layer.dxf.get("color", 7))),
        linetype=str(layer.dxf.get("linetype", "Continuous")),
        lineweight=int(layer.dxf.get("lineweight", -3)),
        is_on=not layer.is_off(),
        is_frozen=bool(layer.is_frozen()),
        is_locked=bool(layer.is_locked()),
        is_current=str(layer.dxf.name) == current_layer,
    )


def _audit_entry(entry: Any) -> dict[str, Any]:
    entity = getattr(entry, "entity", None)
    return {
        "code": getattr(entry, "code", None),
        "message": str(getattr(entry, "message", entry)),
        "entity": str(entity.dxf.get("handle", "?")) if entity is not None else None,
    }


class EzdxfBackend(AutoCADBackend):
    def __init__(self, settings: Settings):
        self.settings = settings
        self._doc: Any | None = None
        self._doc_path: Path | None = None
        self._dirty = False
        self._current_layer = "0"
        self._current_space = "Model"
        self._quarantined = False
        self._generation = 0
        self._undo_stack: list[bytes] = []
        self._redo_stack: list[bytes] = []
        self._transaction_stack: list[bytes] = []
        self._lock = asyncio.Lock()

    @property
    def name(self) -> str:
        return "ezdxf"

    def capabilities(self) -> dict[str, dict[str, Any]]:
        render_available = find_spec("matplotlib") is not None
        capabilities = {
            "common.document.new": Capability(True, "native"),
            "common.document.open": Capability(True, "native"),
            "common.document.save": Capability(True, "dxf"),
            "common.object.query": Capability(True, "native"),
            "common.object.modify": Capability(True, "native"),
            "common.organization.layers": Capability(True, "native"),
            "common.transaction.rollback": Capability(True, "compressed_snapshot"),
            "common.transaction.undo": Capability(
                self.settings.undo_depth > 0,
                "compressed_snapshot" if self.settings.undo_depth > 0 else None,
                None if self.settings.undo_depth > 0 else "CDT_AUTOCAD_UNDO_DEPTH_is_0",
            ),
            "autocad.dxf.read": Capability(True, "native"),
            "autocad.dxf.write": Capability(True, "native"),
            "autocad.dwg.read": Capability(False, reason="live_autocad_or_converter_required"),
            "autocad.dwg.write": Capability(False, reason="live_autocad_required"),
            "autocad.blocks": Capability(True, "native"),
            "autocad.layouts": Capability(True, "native"),
            "autocad.dimensions.linear": Capability(True, "native"),
            "autocad.dimensions.aligned": Capability(True, "native"),
            "autocad.dimensions.advanced": Capability(
                False, reason="A3_2_staged_not_public"
            ),
            "autocad.hatch": Capability(True, "native"),
            "autocad.audit": Capability(True, "native"),
            "autocad.audit.detail": Capability(True, "native"),
            "autocad.purge": Capability(True, "native"),
            "autocad.pdf.export": Capability(
                render_available,
                "rendered" if render_available else None,
                None if render_available else "optional_dependency_missing:matplotlib",
            ),
            "autocad.live_ui": Capability(False, reason="COM_backend_required"),
            "autocad.viewport.manage": Capability(False, reason="COM_backend_required"),
            "autocad.view.zoom": Capability(False, reason="COM_backend_required"),
            "autocad.viewport.capture": Capability(False, reason="COM_backend_required"),
            "autocad.view.3d": Capability(False, reason="COM_backend_required"),
            "autocad.geometry.3d_polyline": Capability(False, reason="COM_backend_required"),
            "autocad.solid.acis": Capability(False, reason="COM_backend_required"),
            "autocad.solid.loft": Capability(False, reason="COM_backend_required"),
        }
        return {key: value.to_dict() for key, value in capabilities.items()}

    def status(self) -> dict[str, Any]:
        return {
            "backend": self.name,
            "document_open": self._doc is not None,
            "document_path": str(self._doc_path) if self._doc_path else None,
            "dirty": self._dirty,
            "current_layer": self._current_layer,
            "current_space": self._current_space,
            "quarantined": self._quarantined,
            "undo_depth": max(0, len(self._undo_stack) - 1),
            "redo_depth": len(self._redo_stack),
            "transaction_depth": len(self._transaction_stack),
        }

    def _require_doc(self) -> Any:
        if self._doc is None:
            raise StateConflictError("No document open; call document_new or document_open first")
        return self._doc

    def _sync_document_context(self) -> None:
        doc = self._require_doc()
        self._current_layer = str(doc.header.get("$CLAYER", "0"))
        if int(doc.header.get("$TILEMODE", 1)):
            self._current_space = "Model"
        else:
            self._current_space = str(doc.active_layout().name)

    def _space(self) -> Any:
        doc = self._require_doc()
        if self._current_space == "Model":
            return doc.modelspace()
        return doc.layouts.get(self._current_space)

    def _find_layout(self, raw_name: str) -> str | None:
        wanted = str(raw_name or "").strip().lower()
        if not wanted:
            return None
        return next(
            (name for name in self._require_doc().layouts.names() if name.lower() == wanted),
            None,
        )

    def _find_block(self, raw_name: str) -> str | None:
        wanted = str(raw_name or "").strip().lower()
        if not wanted:
            return None
        return next(
            (block.name for block in self._require_doc().blocks if block.name.lower() == wanted),
            None,
        )

    def _get_entity(self, object_id: str) -> Any:
        entity = self._require_doc().entitydb.get(str(object_id))
        if entity is None or not entity.is_alive:
            raise KeyError(f"entity not found: {object_id}")
        return entity

    def _serialize_doc(self, doc: Any | None = None) -> bytes:
        stream = io.StringIO()
        (doc or self._require_doc()).write(stream)
        return gzip.compress(stream.getvalue().encode("utf-8"), compresslevel=3)

    def _restore_snapshot(self, snapshot: bytes) -> None:
        raw = gzip.decompress(snapshot).decode("utf-8")
        self._doc = ezdxf.read(io.StringIO(raw))
        self._generation += 1
        self._sync_document_context()
        self._dirty = True

    def _reset_history_baseline(self) -> None:
        self._undo_stack.clear()
        self._redo_stack.clear()
        self._transaction_stack.clear()
        if self.settings.undo_depth > 0 and self._doc is not None:
            self._undo_stack.append(self._serialize_doc())

    def _record_mutation(self) -> None:
        self._dirty = True
        if self.settings.undo_depth <= 0 or self._doc is None:
            return
        self._undo_stack.append(self._serialize_doc())
        self._redo_stack.clear()
        while len(self._undo_stack) > self.settings.undo_depth + 1:
            self._undo_stack.pop(0)

    @staticmethod
    def _atomic_write_dxf(doc: Any, target: Path) -> None:
        fd, temp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
                doc.write(stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_name, target)
        except Exception:
            try:
                os.unlink(temp_name)
            except OSError:
                pass
            raise

    async def _run(
        self,
        func: Callable[[], _T],
        *,
        integrity_sensitive: bool = False,
        record_history: bool = False,
        quarantine_exit: bool = False,
        may_mutate_document: bool | None = None,
        timeout_seconds: float | None = None,
    ) -> _T:
        if may_mutate_document is not None:
            integrity_sensitive = may_mutate_document
        async with self._lock:
            if self._quarantined and not quarantine_exit:
                raise BackendQuarantinedError(
                    "Document is quarantined after a timed-out mutation; rebind with "
                    "document_new or document_open before continuing"
                )

            generation = self._generation
            document = self._doc

            def _wrapped() -> _T:
                result = func()
                if (
                    record_history
                    and document is not None
                    and self._generation == generation
                    and self._doc is document
                ):
                    self._record_mutation()
                return result

            task = asyncio.create_task(asyncio.to_thread(_wrapped))
            deadline = timeout_seconds or self.settings.call_timeout_seconds
            try:
                return await asyncio.wait_for(asyncio.shield(task), timeout=deadline)
            except TimeoutError as exc:
                if integrity_sensitive:
                    self._quarantined = True
                raise BackendTimeoutError(f"ezdxf operation exceeded {deadline:g}s") from exc

    async def document_new(self) -> dict[str, Any]:
        doc = await self._run(lambda: ezdxf.new(dxfversion="R2010"), quarantine_exit=True)
        self._doc = doc
        self._generation += 1
        self._doc_path = None
        self._dirty = False
        self._current_layer = "0"
        self._current_space = "Model"
        self._quarantined = False
        self._reset_history_baseline()
        return {"ok": True, "name": "untitled.dxf", "backend": self.name}

    async def document_open(self, path: str) -> dict[str, Any]:
        if Path(path).suffix.lower() == ".dwg":
            raise UnsupportedCapabilityError(
                "autocad.dwg.read",
                "Native DWG reading requires the live AutoCAD backend or an explicit converter",
            )
        resolved = resolve_dxf_path(path, self.settings, must_exist=True)
        doc = await self._run(lambda: ezdxf.readfile(resolved), quarantine_exit=True)
        self._doc = doc
        self._generation += 1
        self._doc_path = resolved
        self._dirty = False
        self._quarantined = False
        self._sync_document_context()
        self._reset_history_baseline()
        return {"ok": True, "name": resolved.name, "path": str(resolved), "backend": self.name}

    async def document_info(self) -> dict[str, Any]:
        def _sync() -> dict[str, Any]:
            doc = self._require_doc()
            space = self._space()
            units_code = int(doc.header.get("$INSUNITS", 0))
            return {
                "name": self._doc_path.name if self._doc_path else "untitled.dxf",
                "path": str(self._doc_path) if self._doc_path else None,
                "saved": not self._dirty,
                "entity_count": len(space),
                "model_entity_count": len(doc.modelspace()),
                "layer_count": len(doc.layers),
                "block_count": len([b for b in doc.blocks if not b.name.startswith("*")]),
                "layout_count": len(doc.layouts),
                "units": _UNIT_NAMES.get(units_code, f"unknown:{units_code}"),
                "dxf_version": str(doc.dxfversion),
                "backend": self.name,
                "current_space": self._current_space,
                "default_coordinate_frame": "wcs",
            }

        return await self._run(_sync)

    async def document_save(self, path: str | None = None) -> dict[str, Any]:
        requested = path or (str(self._doc_path) if self._doc_path else "")
        if Path(requested).suffix.lower() == ".dwg":
            raise UnsupportedCapabilityError(
                "autocad.dwg.write",
                "Native DWG writing requires the live AutoCAD backend; use a .dxf target",
            )
        target = resolve_dxf_path(
            requested,
            self.settings,
            must_exist=False,
            for_write=True,
        )

        def _sync() -> None:
            self._atomic_write_dxf(self._require_doc(), target)

        await self._run(_sync, integrity_sensitive=True)
        self._doc_path = target
        self._dirty = False
        return {"ok": True, "path": str(target), "format": "dxf"}

    async def document_save_as(self, path: str) -> dict[str, Any]:
        return await self.document_save(path)

    async def document_export_pdf(self, path: str, layout: str | None = None) -> dict[str, Any]:
        if find_spec("matplotlib") is None:
            raise UnsupportedCapabilityError(
                "autocad.pdf.export",
                "PDF export requires the optional matplotlib dependency",
            )
        target = resolve_pdf_path(path, self.settings)

        def _sync() -> dict[str, Any]:
            from ezdxf.addons.drawing import Frontend, RenderContext
            from ezdxf.addons.drawing.matplotlib import MatplotlibBackend
            from matplotlib.backends.backend_agg import FigureCanvasAgg
            from matplotlib.figure import Figure

            doc = self._require_doc()
            resolved_layout = self._find_layout(layout) if layout else None
            if layout and resolved_layout is None:
                raise ValueError(f"layout not found: {layout}")
            drawing_layout = (
                doc.modelspace()
                if resolved_layout in (None, "Model")
                else doc.layouts.get(resolved_layout)
            )

            fd, temp_name = tempfile.mkstemp(
                prefix=f".{target.name}.", suffix=".tmp.pdf", dir=target.parent
            )
            os.close(fd)
            try:
                figure = Figure()
                FigureCanvasAgg(figure)
                axis = figure.add_axes([0, 0, 1, 1])
                context = RenderContext(doc)
                output = MatplotlibBackend(axis)
                Frontend(context, output).draw_layout(drawing_layout, finalize=True)
                figure.savefig(temp_name, dpi=150, format="pdf")
                os.replace(temp_name, target)
            except Exception:
                try:
                    os.unlink(temp_name)
                except OSError:
                    pass
                raise
            return {
                "ok": True,
                "path": str(target),
                "layout": resolved_layout or self._current_space,
            }

        return await self._run(_sync, timeout_seconds=self.settings.render_timeout_seconds)

    def _validate_layer(self, layer: str | None) -> str:
        name = layer or self._current_layer
        doc = self._require_doc()
        if name not in doc.layers:
            raise ValueError(f"layer does not exist: {name}")
        return name

    @staticmethod
    def _validate_color(color: int | None) -> int | None:
        if color is None:
            return None
        if not 0 <= int(color) <= 256:
            raise ValueError("color must be AutoCAD ACI value 0..256")
        return int(color)

    def _prepare_attrs(self, layer: str | None, color: int | None) -> tuple[str, int | None]:
        return self._validate_layer(layer), self._validate_color(color)

    @staticmethod
    def _apply_attrs(entity: Any, layer: str, color: int | None) -> None:
        entity.dxf.layer = layer
        if color is not None:
            entity.dxf.color = color

    async def entity_create_line(
        self,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        z1: float = 0.0,
        z2: float = 0.0,
        layer: str | None = None,
        color: int | None = None,
    ) -> EntityInfo:
        def _sync() -> EntityInfo:
            layer_name, normalized_color = self._prepare_attrs(layer, color)
            entity = self._space().add_line((x1, y1, z1), (x2, y2, z2))
            self._apply_attrs(entity, layer_name, normalized_color)
            return _entity_info(entity)

        return await self._run(_sync, integrity_sensitive=True, record_history=True)

    async def entity_create_circle(
        self,
        cx: float,
        cy: float,
        radius: float,
        layer: str | None = None,
        color: int | None = None,
    ) -> EntityInfo:
        if radius <= 0:
            raise ValueError("radius must be > 0")

        def _sync() -> EntityInfo:
            layer_name, normalized_color = self._prepare_attrs(layer, color)
            entity = self._space().add_circle((cx, cy), radius)
            self._apply_attrs(entity, layer_name, normalized_color)
            return _entity_info(entity)

        return await self._run(_sync, integrity_sensitive=True, record_history=True)

    async def entity_create_arc(
        self,
        cx: float,
        cy: float,
        radius: float,
        start_angle: float,
        end_angle: float,
        layer: str | None = None,
        color: int | None = None,
    ) -> EntityInfo:
        if radius <= 0:
            raise ValueError("radius must be > 0")

        def _sync() -> EntityInfo:
            layer_name, normalized_color = self._prepare_attrs(layer, color)
            entity = self._space().add_arc(
                (cx, cy), radius, start_angle=start_angle, end_angle=end_angle
            )
            self._apply_attrs(entity, layer_name, normalized_color)
            return _entity_info(entity)

        return await self._run(_sync, integrity_sensitive=True, record_history=True)

    async def entity_create_polyline(
        self,
        points: list[list[float]],
        closed: bool = False,
        layer: str | None = None,
        color: int | None = None,
    ) -> EntityInfo:
        if len(points) < 2 or any(len(point) < 2 for point in points):
            raise ValueError("polyline requires at least two [x, y] points")

        def _sync() -> EntityInfo:
            layer_name, normalized_color = self._prepare_attrs(layer, color)
            entity = self._space().add_lwpolyline(
                [(float(point[0]), float(point[1])) for point in points], close=closed
            )
            self._apply_attrs(entity, layer_name, normalized_color)
            return _entity_info(entity)

        return await self._run(_sync, integrity_sensitive=True, record_history=True)

    async def entity_create_text(
        self,
        text: str,
        x: float,
        y: float,
        height: float = 2.5,
        rotation: float = 0.0,
        layer: str | None = None,
        color: int | None = None,
    ) -> EntityInfo:
        if height <= 0:
            raise ValueError("height must be > 0")

        def _sync() -> EntityInfo:
            layer_name, normalized_color = self._prepare_attrs(layer, color)
            entity = self._space().add_text(
                text,
                dxfattribs={"insert": (x, y), "height": height, "rotation": rotation},
            )
            self._apply_attrs(entity, layer_name, normalized_color)
            return _entity_info(entity)

        return await self._run(_sync, integrity_sensitive=True, record_history=True)

    async def hatch_create(
        self,
        boundary_points: list[list[float]],
        pattern: str = "SOLID",
        scale: float = 1.0,
        angle: float = 0.0,
        layer: str | None = None,
        color: int | None = None,
    ) -> EntityInfo:
        if len(boundary_points) < 3 or any(len(point) < 2 for point in boundary_points):
            raise ValueError("hatch boundary requires at least three [x, y] points")
        if scale <= 0:
            raise ValueError("hatch scale must be > 0")

        def _sync() -> EntityInfo:
            layer_name, normalized_color = self._prepare_attrs(layer, color)
            hatch = self._space().add_hatch()
            if pattern.strip().upper() == "SOLID":
                hatch.set_solid_fill(color=normalized_color or 7)
            else:
                hatch.set_pattern_fill(pattern.strip(), scale=scale, angle=angle)
            points = [(float(point[0]), float(point[1])) for point in boundary_points]
            hatch.paths.add_polyline_path(points, is_closed=True)
            self._apply_attrs(hatch, layer_name, normalized_color)
            return _entity_info(hatch)

        return await self._run(_sync, integrity_sensitive=True, record_history=True)

    async def dimension_linear(
        self,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        dim_x: float,
        dim_y: float,
        rotation: float = 0.0,
        layer: str | None = None,
    ) -> EntityInfo:
        def _sync() -> EntityInfo:
            layer_name = self._validate_layer(layer)
            dim = self._space().add_linear_dim(
                base=(dim_x, dim_y),
                p1=(x1, y1),
                p2=(x2, y2),
                angle=rotation,
                dimstyle="Standard",
            )
            dim.render()
            entity = dim.dimension
            entity.dxf.layer = layer_name
            return _entity_info(entity)

        return await self._run(_sync, integrity_sensitive=True, record_history=True)

    async def dimension_aligned(
        self,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        dim_x: float,
        dim_y: float,
        layer: str | None = None,
    ) -> EntityInfo:
        dx = x2 - x1
        dy = y2 - y1
        length = math.hypot(dx, dy)
        if length <= 0:
            raise ValueError("aligned dimension requires two distinct points")
        signed_distance = ((dim_x - x1) * (-dy) + (dim_y - y1) * dx) / length

        def _sync() -> EntityInfo:
            layer_name = self._validate_layer(layer)
            dim = self._space().add_aligned_dim(
                p1=(x1, y1),
                p2=(x2, y2),
                distance=signed_distance,
                dimstyle="Standard",
            )
            dim.render()
            entity = dim.dimension
            entity.dxf.layer = layer_name
            return _entity_info(entity)

        return await self._run(_sync, integrity_sensitive=True, record_history=True)

    async def dimension_angular(
        self,
        vertex_x: float,
        vertex_y: float,
        first_x: float,
        first_y: float,
        second_x: float,
        second_y: float,
        text_x: float,
        text_y: float,
        layer: str | None = None,
    ) -> EntityInfo:
        self._validate_angular_dimension(
            vertex_x, vertex_y, first_x, first_y, second_x, second_y, text_x, text_y
        )

        def _sync() -> EntityInfo:
            layer_name = self._validate_layer(layer)
            dim = self._space().add_angular_dim_3p(
                base=(text_x, text_y),
                center=(vertex_x, vertex_y),
                p1=(first_x, first_y),
                p2=(second_x, second_y),
                location=(text_x, text_y),
                dimstyle="Standard",
            )
            dim.render()
            entity = dim.dimension
            entity.dxf.layer = layer_name
            return _entity_info(entity)

        return await self._run(_sync, integrity_sensitive=True, record_history=True)

    async def dimension_radial(
        self,
        center_x: float,
        center_y: float,
        chord_x: float,
        chord_y: float,
        leader_length: float,
        layer: str | None = None,
    ) -> EntityInfo:
        radius = self._validate_radial_dimension(
            center_x, center_y, chord_x, chord_y, leader_length
        )
        unit_x = (float(chord_x) - float(center_x)) / radius
        unit_y = (float(chord_y) - float(center_y)) / radius
        location = (
            float(center_x) + unit_x * (radius + float(leader_length)),
            float(center_y) + unit_y * (radius + float(leader_length)),
        )

        def _sync() -> EntityInfo:
            layer_name = self._validate_layer(layer)
            dim = self._space().add_radius_dim(
                center=(center_x, center_y),
                radius=radius,
                location=location,
                dimstyle="Standard",
            )
            dim.render()
            entity = dim.dimension
            entity.dxf.layer = layer_name
            return _entity_info(entity)

        return await self._run(_sync, integrity_sensitive=True, record_history=True)

    async def dimension_diametric(
        self,
        center_x: float,
        center_y: float,
        chord_x: float,
        chord_y: float,
        leader_length: float,
        layer: str | None = None,
    ) -> EntityInfo:
        radius = self._validate_diametric_dimension(
            center_x, center_y, chord_x, chord_y, leader_length
        )
        unit_x = (float(chord_x) - float(center_x)) / radius
        unit_y = (float(chord_y) - float(center_y)) / radius
        location = (
            float(center_x) + unit_x * (radius + float(leader_length)),
            float(center_y) + unit_y * (radius + float(leader_length)),
        )

        def _sync() -> EntityInfo:
            layer_name = self._validate_layer(layer)
            dim = self._space().add_diameter_dim(
                center=(center_x, center_y),
                radius=radius,
                location=location,
                dimstyle="Standard",
            )
            dim.render()
            entity = dim.dimension
            entity.dxf.layer = layer_name
            return _entity_info(entity)

        return await self._run(_sync, integrity_sensitive=True, record_history=True)

    async def dimension_ordinate(
        self,
        definition_x: float,
        definition_y: float,
        leader_x: float,
        leader_y: float,
        axis: str = "x",
        layer: str | None = None,
    ) -> EntityInfo:
        normalized_axis = self._normalize_ordinate_axis(
            definition_x, definition_y, leader_x, leader_y, axis
        )
        offset = (
            float(leader_x) - float(definition_x),
            float(leader_y) - float(definition_y),
        )

        def _sync() -> EntityInfo:
            layer_name = self._validate_layer(layer)
            dim = self._space().add_ordinate_dim(
                feature_location=(definition_x, definition_y),
                offset=offset,
                dtype=1 if normalized_axis == "x" else 0,
                dimstyle="Standard",
            )
            dim.render()
            entity = dim.dimension
            entity.dxf.layer = layer_name
            return _entity_info(entity)

        return await self._run(_sync, integrity_sensitive=True, record_history=True)

    async def object_get(self, object_id: str) -> EntityInfo:
        return await self._run(lambda: _entity_info(self._get_entity(object_id)))

    async def object_list(
        self,
        type_filter: str | None = None,
        layer_filter: str | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> list[EntityInfo]:
        if not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        if offset < 0:
            raise ValueError("offset must be >= 0")

        def _sync() -> list[EntityInfo]:
            result: list[EntityInfo] = []
            skipped = 0
            for entity in self._space():
                if type_filter and entity.dxftype().lower() != type_filter.lower():
                    continue
                if layer_filter and str(entity.dxf.get("layer", "0")).lower() != layer_filter.lower():
                    continue
                if skipped < offset:
                    skipped += 1
                    continue
                result.append(_entity_info(entity))
                if len(result) >= limit:
                    break
            return result

        return await self._run(_sync)

    async def object_count(
        self,
        type_filter: str | None = None,
        layer_filter: str | None = None,
    ) -> int:
        def _sync() -> int:
            count = 0
            for entity in self._space():
                if type_filter and entity.dxftype().lower() != type_filter.lower():
                    continue
                if layer_filter and str(entity.dxf.get("layer", "0")).lower() != layer_filter.lower():
                    continue
                count += 1
            return count

        return await self._run(_sync)

    async def object_set_properties(
        self,
        object_id: str,
        layer: str | None = None,
        color: int | None = None,
        linetype: str | None = None,
        visible: bool | None = None,
    ) -> EntityInfo:
        def _sync() -> EntityInfo:
            entity = self._get_entity(object_id)
            target_layer = self._validate_layer(layer) if layer is not None else None
            target_color = self._validate_color(color)
            if linetype is not None:
                known = {str(item.dxf.name).lower(): str(item.dxf.name) for item in self._require_doc().linetypes}
                canonical = known.get(linetype.lower())
                if canonical is None:
                    raise ValueError(f"linetype does not exist: {linetype}")
            else:
                canonical = None
            if target_layer is not None:
                entity.dxf.layer = target_layer
            if target_color is not None:
                entity.dxf.color = target_color
            if canonical is not None:
                entity.dxf.linetype = canonical
            if visible is not None:
                entity.dxf.invisible = not bool(visible)
            return _entity_info(entity)

        return await self._run(_sync, integrity_sensitive=True, record_history=True)

    async def object_delete(self, object_id: str) -> dict[str, Any]:
        def _sync() -> dict[str, Any]:
            entity = self._get_entity(object_id)
            try:
                self._space().delete_entity(entity)
            except Exception as exc:
                raise StateConflictError(
                    "object_delete currently requires the object to be in the active layout/space"
                ) from exc
            return {"ok": True, "deleted_id": object_id}

        return await self._run(_sync, integrity_sensitive=True, record_history=True)

    async def object_move(
        self, object_id: str, dx: float, dy: float, dz: float = 0.0
    ) -> EntityInfo:
        def _sync() -> EntityInfo:
            entity = self._get_entity(object_id)
            entity.translate(dx, dy, dz)
            return _entity_info(entity)

        return await self._run(_sync, integrity_sensitive=True, record_history=True)

    async def object_copy(
        self, object_id: str, dx: float, dy: float, dz: float = 0.0
    ) -> EntityInfo:
        def _sync() -> EntityInfo:
            entity = self._get_entity(object_id)
            duplicate = entity.copy()
            self._space().add_entity(duplicate)
            duplicate.translate(dx, dy, dz)
            return _entity_info(duplicate)

        return await self._run(_sync, integrity_sensitive=True, record_history=True)

    async def object_rotate(
        self,
        object_id: str,
        base_x: float,
        base_y: float,
        angle_deg: float,
    ) -> EntityInfo:
        def _sync() -> EntityInfo:
            entity = self._get_entity(object_id)
            matrix = (
                Matrix44.translate(-base_x, -base_y, 0)
                @ Matrix44.z_rotate(math.radians(angle_deg))
                @ Matrix44.translate(base_x, base_y, 0)
            )
            entity.transform(matrix)
            return _entity_info(entity)

        return await self._run(_sync, integrity_sensitive=True, record_history=True)

    async def object_scale(
        self,
        object_id: str,
        base_x: float,
        base_y: float,
        factor: float,
    ) -> EntityInfo:
        if factor <= 0:
            raise ValueError("scale factor must be > 0")

        def _sync() -> EntityInfo:
            entity = self._get_entity(object_id)
            matrix = (
                Matrix44.translate(-base_x, -base_y, 0)
                @ Matrix44.scale(factor, factor, factor)
                @ Matrix44.translate(base_x, base_y, 0)
            )
            entity.transform(matrix)
            return _entity_info(entity)

        return await self._run(_sync, integrity_sensitive=True, record_history=True)

    async def layer_list(self) -> list[LayerInfo]:
        return await self._run(
            lambda: [_layer_info(layer, self._current_layer) for layer in self._require_doc().layers]
        )

    async def layer_create(self, name: str, color: int = 7) -> LayerInfo:
        wanted = name.strip()
        if not wanted:
            raise ValueError("layer name must not be empty")
        normalized_color = self._validate_color(color)
        assert normalized_color is not None

        def _sync() -> LayerInfo:
            doc = self._require_doc()
            if wanted in doc.layers:
                raise ValueError(f"layer already exists: {wanted}")
            layer = doc.layers.add(wanted, color=normalized_color)
            return _layer_info(layer, self._current_layer)

        return await self._run(_sync, integrity_sensitive=True, record_history=True)

    async def layer_set_current(self, name: str) -> dict[str, Any]:
        def _sync() -> dict[str, Any]:
            doc = self._require_doc()
            if name not in doc.layers:
                raise ValueError(f"layer does not exist: {name}")
            doc.header["$CLAYER"] = name
            self._current_layer = name
            return {"ok": True, "current_layer": name}

        return await self._run(_sync, integrity_sensitive=True, record_history=True)

    async def block_list(self) -> list[BlockInfo]:
        def _sync() -> list[BlockInfo]:
            result: list[BlockInfo] = []
            for block in self._require_doc().blocks:
                if block.name.startswith("*"):
                    continue
                base = block.block.dxf.get("base_point", (0.0, 0.0, 0.0))
                result.append(
                    BlockInfo(
                        name=str(block.name),
                        base_point=(float(base[0]), float(base[1]), float(base[2])),
                        entity_count=len(block),
                        attribute_count=sum(1 for entity in block if entity.dxftype() == "ATTDEF"),
                        is_xref=bool(block.block.dxf.get("xref_path", "")),
                    )
                )
            return result

        return await self._run(_sync)

    async def block_create(
        self,
        name: str,
        object_ids: list[str],
        base_x: float = 0.0,
        base_y: float = 0.0,
    ) -> BlockInfo:
        wanted = name.strip()
        if not wanted:
            raise ValueError("block name must not be empty")
        if not object_ids:
            raise ValueError("block_create requires at least one object id")

        def _sync() -> BlockInfo:
            doc = self._require_doc()
            if self._find_block(wanted) is not None:
                raise ValueError(f"block already exists: {wanted}")
            entities = [self._get_entity(object_id) for object_id in object_ids]
            block = doc.blocks.new(name=wanted, base_point=(base_x, base_y, 0.0))
            for entity in entities:
                block.add_entity(entity.copy())
            return BlockInfo(
                name=wanted,
                base_point=(base_x, base_y, 0.0),
                entity_count=len(entities),
                attribute_count=0,
                is_xref=False,
            )

        return await self._run(_sync, integrity_sensitive=True, record_history=True)

    async def block_insert(
        self,
        name: str,
        x: float,
        y: float,
        scale_x: float = 1.0,
        scale_y: float = 1.0,
        rotation: float = 0.0,
        layer: str | None = None,
    ) -> EntityInfo:
        if scale_x == 0 or scale_y == 0:
            raise ValueError("block scale must be non-zero")

        def _sync() -> EntityInfo:
            canonical = self._find_block(name)
            if canonical is None:
                raise ValueError(f"block does not exist: {name}")
            layer_name = self._validate_layer(layer)
            entity = self._space().add_blockref(
                canonical,
                (x, y),
                dxfattribs={
                    "xscale": scale_x,
                    "yscale": scale_y,
                    "rotation": rotation,
                    "layer": layer_name,
                },
            )
            return _entity_info(entity)

        return await self._run(_sync, integrity_sensitive=True, record_history=True)

    async def layout_list(self) -> dict[str, Any]:
        def _sync() -> dict[str, Any]:
            doc = self._require_doc()
            return {
                "ok": True,
                "layouts": list(doc.layouts.names_in_taborder()),
                "current": self._current_space,
            }

        return await self._run(_sync)

    async def layout_create(self, name: str) -> dict[str, Any]:
        wanted = name.strip()
        if not wanted:
            raise ValueError("layout name must not be empty")

        def _sync() -> dict[str, Any]:
            doc = self._require_doc()
            if self._find_layout(wanted) is not None:
                raise ValueError(f"layout already exists: {wanted}")
            doc.layouts.new(wanted)
            return {"ok": True, "layout": wanted}

        return await self._run(_sync, integrity_sensitive=True, record_history=True)

    async def layout_set_current(self, name: str) -> dict[str, Any]:
        def _sync() -> dict[str, Any]:
            doc = self._require_doc()
            resolved = self._find_layout(name)
            if resolved is None:
                raise ValueError(f"layout not found: {name}")
            if resolved == "Model":
                doc.header["$TILEMODE"] = 1
            else:
                doc.layouts.set_active_layout(resolved)
                doc.header["$TILEMODE"] = 0
            self._current_space = resolved
            return {"ok": True, "current": resolved}

        return await self._run(_sync, integrity_sensitive=True, record_history=True)

    async def drawing_audit(self) -> dict[str, Any]:
        def _sync() -> dict[str, Any]:
            auditor = self._require_doc().audit()
            fixes = [_audit_entry(entry) for entry in auditor.fixes]
            errors = [_audit_entry(entry) for entry in auditor.errors]
            return {
                "ok": True,
                "repaired": bool(fixes),
                "fixes": fixes,
                "fix_count": len(fixes),
                "errors": errors,
                "error_count": len(errors),
            }

        result = await self._run(_sync, integrity_sensitive=True, record_history=True)
        if not result["repaired"] and self._undo_stack:
            # Audit is logically read-only when no repair occurred; remove duplicate snapshot.
            if len(self._undo_stack) >= 2 and self._undo_stack[-1] == self._undo_stack[-2]:
                self._undo_stack.pop()
        return result

    async def drawing_purge(self) -> dict[str, Any]:
        def _sync() -> dict[str, Any]:
            doc = self._require_doc()
            purged = {"blocks": [], "layers": [], "linetypes": [], "text_styles": []}
            skipped: list[dict[str, str]] = []

            used_blocks = {
                str(entity.dxf.name)
                for entity in doc.entitydb.values()
                if entity.is_alive and entity.dxftype() == "INSERT"
            }
            for block in list(doc.blocks):
                name = str(block.name)
                if name.startswith("*") or name in used_blocks:
                    continue
                try:
                    doc.blocks.delete_block(name, safe=True)
                    purged["blocks"].append(name)
                except Exception as exc:
                    skipped.append({"kind": "block", "name": name, "reason": str(exc)})

            used_layers = {"0", "Defpoints", self._current_layer}
            for entity in doc.entitydb.values():
                if entity.is_alive and entity.dxf.is_supported("layer"):
                    used_layers.add(str(entity.dxf.get("layer", "0")))
            for layer in list(doc.layers):
                name = str(layer.dxf.name)
                if name in used_layers:
                    continue
                try:
                    doc.layers.remove(name)
                    purged["layers"].append(name)
                except Exception as exc:
                    skipped.append({"kind": "layer", "name": name, "reason": str(exc)})

            used_linetypes = {"BYLAYER", "BYBLOCK", "Continuous"}
            for layer in doc.layers:
                used_linetypes.add(str(layer.dxf.linetype))
            for entity in doc.entitydb.values():
                if entity.is_alive and entity.dxf.is_supported("linetype"):
                    used_linetypes.add(str(entity.dxf.get("linetype", "ByLayer")))
            for item in list(doc.linetypes):
                name = str(item.dxf.name)
                if name in used_linetypes:
                    continue
                try:
                    doc.linetypes.remove(name)
                    purged["linetypes"].append(name)
                except Exception as exc:
                    skipped.append({"kind": "linetype", "name": name, "reason": str(exc)})

            used_styles = {"Standard"}
            for entity in doc.entitydb.values():
                if entity.is_alive and entity.dxf.is_supported("style"):
                    used_styles.add(str(entity.dxf.get("style", "Standard")))
            for item in list(doc.styles):
                name = str(item.dxf.name)
                if name in used_styles:
                    continue
                try:
                    doc.styles.remove(name)
                    purged["text_styles"].append(name)
                except Exception as exc:
                    skipped.append({"kind": "text_style", "name": name, "reason": str(exc)})

            return {
                "ok": True,
                "purged": purged,
                "purged_count": sum(len(items) for items in purged.values()),
                "skipped": skipped,
            }

        result = await self._run(_sync, integrity_sensitive=True, record_history=True)
        if result["purged_count"] == 0 and len(self._undo_stack) >= 2:
            if self._undo_stack[-1] == self._undo_stack[-2]:
                self._undo_stack.pop()
        return result

    async def transaction_begin(self) -> dict[str, Any]:
        def _sync() -> dict[str, Any]:
            self._require_doc()
            if len(self._transaction_stack) >= self.settings.transaction_depth:
                raise StateConflictError(
                    f"transaction depth limit reached: {self.settings.transaction_depth}"
                )
            self._transaction_stack.append(self._serialize_doc())
            return {"ok": True, "transaction_depth": len(self._transaction_stack)}

        return await self._run(_sync)

    async def transaction_commit(self) -> dict[str, Any]:
        def _sync() -> dict[str, Any]:
            if not self._transaction_stack:
                raise StateConflictError("No active transaction")
            self._transaction_stack.pop()
            return {"ok": True, "transaction_depth": len(self._transaction_stack)}

        return await self._run(_sync)

    async def transaction_rollback(self) -> dict[str, Any]:
        def _sync() -> dict[str, Any]:
            if not self._transaction_stack:
                raise StateConflictError("No active transaction to rollback")
            snapshot = self._transaction_stack.pop()
            self._restore_snapshot(snapshot)
            self._record_mutation()
            return {
                "ok": True,
                "transaction_depth": len(self._transaction_stack),
                "rolled_back": True,
            }

        return await self._run(_sync, integrity_sensitive=True)

    async def undo(self) -> dict[str, Any]:
        if self.settings.undo_depth <= 0:
            raise UnsupportedCapabilityError(
                "common.transaction.undo",
                "Undo history is disabled; set CDT_AUTOCAD_UNDO_DEPTH > 0",
            )

        def _sync() -> dict[str, Any]:
            if len(self._undo_stack) < 2:
                raise StateConflictError("Nothing to undo")
            current = self._undo_stack.pop()
            self._redo_stack.append(current)
            self._restore_snapshot(self._undo_stack[-1])
            return {
                "ok": True,
                "undo_depth": len(self._undo_stack) - 1,
                "redo_depth": len(self._redo_stack),
            }

        return await self._run(_sync, integrity_sensitive=True)

    async def redo(self) -> dict[str, Any]:
        if self.settings.undo_depth <= 0:
            raise UnsupportedCapabilityError(
                "common.transaction.undo",
                "Redo history is disabled; set CDT_AUTOCAD_UNDO_DEPTH > 0",
            )

        def _sync() -> dict[str, Any]:
            if not self._redo_stack:
                raise StateConflictError("Nothing to redo")
            snapshot = self._redo_stack.pop()
            self._undo_stack.append(snapshot)
            self._restore_snapshot(snapshot)
            return {
                "ok": True,
                "undo_depth": len(self._undo_stack) - 1,
                "redo_depth": len(self._redo_stack),
            }

        return await self._run(_sync, integrity_sensitive=True)
