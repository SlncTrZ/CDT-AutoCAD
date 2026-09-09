"""Live AutoCAD backend using the Windows ActiveX/COM automation API.
Wing: code | Topic: autocad-a2 | Updated: 2026-09-09 14:14

This backend intentionally implements only the existing A0/A1 provider contract. It does not
copy the much larger reference server surface. All COM work is serialized through one STA worker
thread, while attach/start policy and filesystem boundaries stay owned by CDT_Engineer.
"""

from __future__ import annotations

import asyncio
import ctypes
import io
import math
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, TypeVar

from ..config import Settings
from ..errors import BackendTimeoutError, StateConflictError, UnsupportedCapabilityError
from ..models import BlockInfo, Capability, EntityInfo, LayerInfo
from ..security import resolve_autocad_document_path, resolve_pdf_path
from .base import AutoCADBackend

_T = TypeVar("_T")
_WIN32 = sys.platform == "win32"

if _WIN32:
    try:
        import pythoncom
        import pywintypes
        import win32com.client
        import win32gui
        import win32ui

        _COM_IMPORTS_OK = True
    except ImportError:
        _COM_IMPORTS_OK = False
else:
    _COM_IMPORTS_OK = False

try:
    from PIL import Image as PILImage

    _PIL_OK = True
except ImportError:
    _PIL_OK = False

_COM_ERROR: tuple[type[BaseException], ...] = (
    (pywintypes.com_error,) if _COM_IMPORTS_OK else ()
)
_THREAD_STATE = threading.local()

# AutoCAD 2018 is the native DWG generation used by AutoCAD 2018-2027.
_AC2018_DWG = 64
_AC2018_DXF = 65

_UNIT_NAMES = {
    0: "unitless",
    1: "inches",
    2: "feet",
    3: "miles",
    4: "mm",
    5: "cm",
    6: "m",
    7: "km",
}


def _com_initialize(generation: int) -> None:
    _THREAD_STATE.generation = generation
    if _COM_IMPORTS_OK:
        pythoncom.CoInitialize()


def _point(x: float, y: float, z: float = 0.0):
    if not _COM_IMPORTS_OK:
        raise RuntimeError("pywin32 COM support is unavailable")
    return win32com.client.VARIANT(
        pythoncom.VT_ARRAY | pythoncom.VT_R8,
        [float(x), float(y), float(z)],
    )


def _double_array(values: list[float]):
    if not _COM_IMPORTS_OK:
        raise RuntimeError("pywin32 COM support is unavailable")
    return win32com.client.VARIANT(
        pythoncom.VT_ARRAY | pythoncom.VT_R8,
        [float(value) for value in values],
    )


def _dispatch_array(values: list[Any]):
    if not _COM_IMPORTS_OK:
        raise RuntimeError("pywin32 COM support is unavailable")
    return win32com.client.VARIANT(
        pythoncom.VT_ARRAY | pythoncom.VT_DISPATCH,
        values,
    )


def _xyz(value: Any) -> list[float]:
    seq = list(value)
    return [float(seq[0]), float(seq[1]), float(seq[2]) if len(seq) > 2 else 0.0]


def _object_type(entity: Any) -> str:
    name = str(getattr(entity, "ObjectName", "UNKNOWN"))
    mapping = {
        "AcDbLine": "LINE",
        "AcDbCircle": "CIRCLE",
        "AcDbArc": "ARC",
        "AcDbPolyline": "LWPOLYLINE",
        "AcDb2dPolyline": "POLYLINE",
        "AcDbText": "TEXT",
        "AcDbBlockReference": "INSERT",
        "AcDbRotatedDimension": "DIMENSION",
        "AcDbAlignedDimension": "DIMENSION",
        "AcDbHatch": "HATCH",
    }
    return mapping.get(name, name.removeprefix("AcDb").upper())


def _entity_info(entity: Any) -> EntityInfo:
    entity_type = _object_type(entity)
    properties: dict[str, Any] = {}

    if entity_type == "LINE":
        properties = {
            "start": _xyz(entity.StartPoint),
            "end": _xyz(entity.EndPoint),
            "coordinate_frame": "wcs",
        }
    elif entity_type == "CIRCLE":
        properties = {
            "center": _xyz(entity.Center),
            "radius": float(entity.Radius),
            "coordinate_frame": "wcs",
        }
    elif entity_type == "ARC":
        properties = {
            "center": _xyz(entity.Center),
            "radius": float(entity.Radius),
            "start_angle": math.degrees(float(entity.StartAngle)),
            "end_angle": math.degrees(float(entity.EndAngle)),
            "coordinate_frame": "wcs",
        }
    elif entity_type in {"LWPOLYLINE", "POLYLINE"}:
        coords = list(entity.Coordinates)
        properties = {
            "points": [
                [float(coords[index]), float(coords[index + 1])]
                for index in range(0, len(coords) - 1, 2)
            ],
            "closed": bool(entity.Closed),
            "coordinate_frame": "ocs" if entity_type == "LWPOLYLINE" else "wcs",
        }
    elif entity_type == "TEXT":
        properties = {
            "text": str(entity.TextString),
            "insert": _xyz(entity.InsertionPoint),
            "height": float(entity.Height),
            "rotation": math.degrees(float(entity.Rotation)),
            "coordinate_frame": "wcs",
        }
    elif entity_type == "INSERT":
        properties = {
            "block_name": str(entity.Name),
            "insert": _xyz(entity.InsertionPoint),
            "x_scale": float(entity.XScaleFactor),
            "y_scale": float(entity.YScaleFactor),
            "rotation": math.degrees(float(entity.Rotation)),
        }
    elif entity_type == "HATCH":
        properties = {"pattern_name": str(entity.PatternName)}
    elif entity_type == "DIMENSION":
        try:
            properties["text"] = str(entity.TextOverride or "<>")
        except Exception:
            properties["text"] = "<>"

    try:
        linetype = str(entity.Linetype)
    except Exception:
        linetype = "ByLayer"
    try:
        visible = bool(entity.Visible)
    except Exception:
        visible = True

    return EntityInfo(
        id=str(entity.Handle),
        type=entity_type,
        layer=str(entity.Layer),
        color=int(entity.Color),
        linetype=linetype,
        visible=visible,
        properties=properties,
    )


def _layer_info(layer: Any, current_layer: str) -> LayerInfo:
    return LayerInfo(
        name=str(layer.Name),
        color=abs(int(layer.Color)),
        linetype=str(layer.Linetype),
        lineweight=int(layer.LineWeight),
        is_on=bool(layer.LayerOn),
        is_frozen=bool(layer.Freeze),
        is_locked=bool(layer.Lock),
        is_current=str(layer.Name).lower() == current_layer.lower(),
    )


def _capture_window_png(hwnd: int) -> bytes:
    """Capture one native Windows window as PNG without touching the drawing."""
    if not _WIN32 or not _COM_IMPORTS_OK:
        raise RuntimeError("Windows GDI capture is unavailable on this platform")
    if not _PIL_OK:
        raise UnsupportedCapabilityError(
            "autocad.viewport.capture",
            "Live screenshot capture requires the COM optional dependency Pillow.",
        )

    left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    width = int(right - left)
    height = int(bottom - top)
    if width <= 0 or height <= 0:
        raise StateConflictError("AutoCAD window has no capturable visible area")

    hwnd_dc = None
    source_dc = None
    memory_dc = None
    bitmap = None
    try:
        hwnd_dc = win32gui.GetWindowDC(hwnd)
        source_dc = win32ui.CreateDCFromHandle(hwnd_dc)
        memory_dc = source_dc.CreateCompatibleDC()
        bitmap = win32ui.CreateBitmap()
        bitmap.CreateCompatibleBitmap(source_dc, width, height)
        memory_dc.SelectObject(bitmap)

        rendered = ctypes.windll.user32.PrintWindow(hwnd, memory_dc.GetSafeHdc(), 2)
        if not rendered:
            raise RuntimeError("Windows PrintWindow failed for the AutoCAD main window")

        raw = bitmap.GetBitmapBits(True)
        image = PILImage.frombuffer("RGB", (width, height), raw, "raw", "BGRX", 0, 1)
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()
    finally:
        if bitmap is not None:
            try:
                win32gui.DeleteObject(bitmap.GetHandle())
            except Exception:
                pass
        if memory_dc is not None:
            try:
                memory_dc.DeleteDC()
            except Exception:
                pass
        if source_dc is not None:
            try:
                source_dc.DeleteDC()
            except Exception:
                pass
        if hwnd_dc is not None:
            try:
                win32gui.ReleaseDC(hwnd, hwnd_dc)
            except Exception:
                pass


class ComBackend(AutoCADBackend):
    """A2 live AutoCAD backend with lazy connection and serialized STA execution."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._generation = 0
        self._apps: dict[int, Any] = {}
        self._executor: ThreadPoolExecutor | None = None
        self._connected = False
        self._transaction_depth = 0
        self._timeout_uncertain = False
        self._document_scope_key: tuple[str, str] | None = None
        self._created_viewport_handles: set[str] = set()
        if _COM_IMPORTS_OK:
            self._executor = self._new_executor()

    @property
    def name(self) -> str:
        return "com"

    @property
    def runtime_available(self) -> bool:
        return _WIN32 and _COM_IMPORTS_OK

    def _new_executor(self) -> ThreadPoolExecutor:
        self._generation += 1
        generation = self._generation
        return ThreadPoolExecutor(
            max_workers=1,
            initializer=_com_initialize,
            initargs=(generation,),
            thread_name_prefix="cdt-autocad-com",
        )

    def _teardown_generation(self, generation: int) -> None:
        self._apps.pop(generation, None)
        if _COM_IMPORTS_OK:
            try:
                pythoncom.CoUninitialize()
            except Exception:
                pass

    def shutdown(self) -> None:
        executor = self._executor
        if executor is None:
            return
        generation = self._generation
        try:
            executor.submit(self._teardown_generation, generation)
        except Exception:
            pass
        executor.shutdown(wait=False)
        self._executor = None
        self._connected = False
        self._document_scope_key = None
        self._created_viewport_handles.clear()

    def _runtime_reason(self) -> str | None:
        if not _WIN32:
            return "windows_required"
        if not _COM_IMPORTS_OK:
            return "optional_dependency_missing:pywin32"
        return None

    def _capability(self, mode: str = "native") -> Capability:
        reason = self._runtime_reason()
        if reason is not None:
            return Capability(False, reason=reason)
        return Capability(True, mode)

    def capabilities(self) -> dict[str, dict[str, Any]]:
        supported = self._capability
        capabilities = {
            "common.document.new": supported(),
            "common.document.open": supported(),
            "common.document.save": supported(),
            "common.object.query": supported(),
            "common.object.modify": supported(),
            "common.organization.layers": supported(),
            "common.transaction.rollback": supported("autocad_undo_mark"),
            "common.transaction.undo": supported("autocad_native"),
            "autocad.dxf.read": supported(),
            "autocad.dxf.write": supported(),
            "autocad.dwg.read": supported(),
            "autocad.dwg.write": supported(),
            "autocad.blocks": supported(),
            "autocad.layouts": supported(),
            "autocad.dimensions.linear": supported(),
            "autocad.dimensions.aligned": supported(),
            "autocad.hatch": supported(),
            "autocad.audit": supported("sendcommand"),
            "autocad.audit.detail": Capability(
                False,
                reason=(
                    self._runtime_reason()
                    or "audit_result_not_machine_readable_over_activex"
                ),
            ),
            "autocad.purge": supported(),
            "autocad.pdf.export": supported("native_plot"),
            "autocad.live_ui": supported(),
            "autocad.viewport.capture": Capability(
                False, reason="A2.2_staged_pending_public_contract_and_live_verification"
            ),
            "autocad.solid.acis": Capability(False, reason="A3_not_implemented"),
        }
        return {key: value.to_dict() for key, value in capabilities.items()}

    def status(self) -> dict[str, Any]:
        return {
            "backend": self.name,
            "runtime_available": self.runtime_available,
            "connected": self._connected,
            "cad_progid": self.settings.com_progid,
            "attach_policy": self.settings.com_attach_policy,
            "transaction_depth": self._transaction_depth,
            "timeout_uncertain": self._timeout_uncertain,
            "a2_live_view_staged": True,
            "platform": sys.platform,
        }

    def _app(self) -> Any:
        if not self.runtime_available:
            reason = self._runtime_reason() or "unknown"
            raise RuntimeError(f"AutoCAD COM backend unavailable: {reason}")
        generation = int(getattr(_THREAD_STATE, "generation", self._generation))
        app = self._apps.get(generation)
        if app is not None:
            return app

        progid = self.settings.com_progid
        try:
            app = win32com.client.GetActiveObject(progid)
        except Exception as attach_error:
            if self.settings.com_attach_policy == "attach_only":
                raise RuntimeError(
                    f"No running CAD application for ProgID {progid!r}; "
                    "attach_only policy forbids starting one"
                ) from attach_error
            try:
                app = win32com.client.Dispatch(progid)
                app.Visible = True
            except Exception as start_error:
                raise RuntimeError(
                    f"Cannot attach to or start CAD application {progid!r}"
                ) from start_error

        self._apps[generation] = app
        self._connected = True
        return app

    def _doc(self) -> Any:
        app = self._app()
        if int(app.Documents.Count) <= 0:
            raise StateConflictError(
                "No document open in AutoCAD; call document_new or document_open first"
            )
        doc = app.ActiveDocument
        key = (str(doc.Name), str(getattr(doc, "FullName", "") or ""))
        if key != self._document_scope_key:
            if self._document_scope_key is not None and self._transaction_depth > 0:
                raise StateConflictError(
                    "AutoCAD active document changed while a tracked transaction is open. "
                    "Switch back to the original document and commit/rollback before continuing."
                )
            self._document_scope_key = key
            self._created_viewport_handles.clear()
        return doc

    def _space(self) -> Any:
        doc = self._doc()
        try:
            return doc.ActiveLayout.Block
        except Exception:
            return doc.ModelSpace

    @staticmethod
    def _collection_names(collection: Any) -> list[str]:
        return [str(collection.Item(index).Name) for index in range(int(collection.Count))]

    @classmethod
    def _find_name(cls, collection: Any, raw_name: str) -> str | None:
        wanted = str(raw_name or "").strip().lower()
        if not wanted:
            return None
        return next((name for name in cls._collection_names(collection) if name.lower() == wanted), None)

    async def _run(self, func) -> _T:
        executor = self._executor
        if executor is None:
            reason = self._runtime_reason() or "executor_unavailable"
            raise RuntimeError(f"AutoCAD COM backend unavailable: {reason}")

        loop = asyncio.get_running_loop()
        future = loop.run_in_executor(executor, func)
        deadline = self.settings.com_call_timeout_seconds
        try:
            return await asyncio.wait_for(future, timeout=deadline)
        except TimeoutError as exc:
            if not future.cancelled():
                raise

            self._timeout_uncertain = True
            self._connected = False
            self._created_viewport_handles.clear()
            stuck = self._executor
            old_generation = self._generation
            self._executor = self._new_executor() if self.runtime_available else None
            if stuck is not None:
                try:
                    stuck.submit(self._teardown_generation, old_generation)
                except Exception:
                    pass
                stuck.shutdown(wait=False)
            raise BackendTimeoutError(
                f"AutoCAD COM call exceeded {deadline:g}s. The abandoned call may still "
                "complete inside AutoCAD; verify the live drawing before retrying because "
                "a blind retry can double-apply a mutation."
            ) from exc
        except _COM_ERROR as exc:
            self._connected = False
            hr = exc.args[0] if exc.args else 0
            detail = exc.args[1] if len(exc.args) > 1 else str(exc)
            raise RuntimeError(f"AutoCAD COM error ({hr:#010x}): {detail}") from exc

    def _entity_by_id(self, object_id: str) -> Any:
        try:
            return self._doc().HandleToObject(str(object_id).strip())
        except Exception as exc:
            raise KeyError(f"entity not found: {object_id}") from exc

    def _validate_layer(self, layer: str | None) -> str | None:
        if layer is None:
            return None
        canonical = self._find_name(self._doc().Layers, layer)
        if canonical is None:
            raise ValueError(f"layer does not exist: {layer}")
        return canonical

    def _validate_linetype(self, linetype: str | None) -> str | None:
        if linetype is None:
            return None
        canonical = self._find_name(self._doc().Linetypes, linetype)
        if canonical is None:
            raise ValueError(f"linetype does not exist: {linetype}")
        return canonical

    @staticmethod
    def _validate_color(color: int | None) -> int | None:
        if color is None:
            return None
        normalized = int(color)
        if not 0 <= normalized <= 256:
            raise ValueError("color must be AutoCAD ACI value 0..256")
        return normalized

    async def document_new(self) -> dict[str, Any]:
        if self._transaction_depth > 0:
            raise StateConflictError("Cannot create a new document while a transaction is active")

        def _sync() -> dict[str, Any]:
            doc = self._app().Documents.Add()
            return {"ok": True, "name": str(doc.Name), "backend": self.name}

        result = await self._run(_sync)
        self._transaction_depth = 0
        self._timeout_uncertain = False
        self._document_scope_key = None
        self._created_viewport_handles.clear()
        return result

    async def document_open(self, path: str) -> dict[str, Any]:
        if self._transaction_depth > 0:
            raise StateConflictError("Cannot open another document while a transaction is active")
        target = resolve_autocad_document_path(path, self.settings, must_exist=True)

        def _sync() -> dict[str, Any]:
            doc = self._app().Documents.Open(str(target))
            return {
                "ok": True,
                "name": str(doc.Name),
                "path": str(doc.FullName),
                "backend": self.name,
            }

        result = await self._run(_sync)
        self._transaction_depth = 0
        self._timeout_uncertain = False
        self._document_scope_key = None
        self._created_viewport_handles.clear()
        return result

    async def document_info(self) -> dict[str, Any]:
        def _sync() -> dict[str, Any]:
            doc = self._doc()
            app = self._app()
            active_layout = str(doc.ActiveLayout.Name)
            space = doc.ActiveLayout.Block
            full_name = str(getattr(doc, "FullName", "") or "")
            try:
                units_code = int(app.GetVariable("INSUNITS"))
            except Exception:
                units_code = 0
            return {
                "name": str(doc.Name),
                "path": full_name or None,
                "saved": bool(doc.Saved),
                "entity_count": int(space.Count),
                "model_entity_count": int(doc.ModelSpace.Count),
                "layer_count": int(doc.Layers.Count),
                "block_count": len(
                    [name for name in self._collection_names(doc.Blocks) if not name.startswith("*")]
                ),
                "layout_count": int(doc.Layouts.Count),
                "units": _UNIT_NAMES.get(units_code, f"unknown:{units_code}"),
                "autocad_version": str(app.Version),
                "backend": self.name,
                "current_space": active_layout,
                "default_coordinate_frame": "wcs",
            }

        return await self._run(_sync)

    async def document_save(self, path: str | None = None) -> dict[str, Any]:
        if path is not None:
            return await self.document_save_as(path)

        def _current_path() -> str:
            doc = self._doc()
            return str(getattr(doc, "FullName", "") or "")

        raw_path = await self._run(_current_path)
        if not raw_path or not Path(raw_path).is_absolute():
            raise StateConflictError("Unsaved live AutoCAD document requires an explicit save path")
        target = resolve_autocad_document_path(
            raw_path, self.settings, must_exist=False, for_write=True
        )

        def _sync() -> dict[str, Any]:
            doc = self._doc()
            doc.Save()
            return {
                "ok": True,
                "path": str(target),
                "format": target.suffix.lower().lstrip("."),
            }

        return await self._run(_sync)

    async def document_save_as(self, path: str) -> dict[str, Any]:
        target = resolve_autocad_document_path(path, self.settings, must_exist=False, for_write=True)
        file_type = _AC2018_DWG if target.suffix.lower() == ".dwg" else _AC2018_DXF

        def _sync() -> dict[str, Any]:
            doc = self._doc()
            doc.SaveAs(str(target), file_type)
            self._document_scope_key = (str(doc.Name), str(getattr(doc, "FullName", "") or ""))
            return {
                "ok": True,
                "path": str(target),
                "format": target.suffix.lower().lstrip("."),
            }

        return await self._run(_sync)

    async def document_export_pdf(self, path: str, layout: str | None = None) -> dict[str, Any]:
        target = resolve_pdf_path(path, self.settings)

        def _sync() -> dict[str, Any]:
            doc = self._doc()
            previous = str(doc.ActiveLayout.Name)
            selected = previous
            if layout is not None:
                canonical = self._find_name(doc.Layouts, layout)
                if canonical is None:
                    raise ValueError(f"layout not found: {layout}")
                selected = canonical
                if canonical != previous:
                    doc.ActiveLayout = doc.Layouts.Item(canonical)
            try:
                ok = bool(doc.Plot.PlotToFile(str(target), "DWG To PDF.pc3"))
                if not ok:
                    raise RuntimeError("AutoCAD PlotToFile returned false")
                return {"ok": True, "path": str(target), "layout": selected}
            finally:
                if selected != previous:
                    doc.ActiveLayout = doc.Layouts.Item(previous)

        return await self._run(_sync)

    async def drawing_audit(self) -> dict[str, Any]:
        def _sync() -> dict[str, Any]:
            doc = self._doc()
            doc.SendCommand("_.AUDIT _Y\n")
            return {
                "ok": True,
                "repaired": None,
                "fixes": [],
                "fix_count": None,
                "errors": [],
                "error_count": None,
                "detail": "unavailable",
                "message": "AUDIT was dispatched; ActiveX exposes no machine-readable audit result",
            }

        return await self._run(_sync)

    async def drawing_purge(self) -> dict[str, Any]:
        def _sync() -> dict[str, Any]:
            self._doc().PurgeAll()
            return {"ok": True, "purged_count": None, "detail": "unavailable"}

        return await self._run(_sync)

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
            space = self._space()
            result: list[EntityInfo] = []
            skipped = 0
            for index in range(int(space.Count)):
                entity = space.Item(index)
                entity_type = _object_type(entity)
                if type_filter and entity_type.lower() != type_filter.lower():
                    continue
                if layer_filter and str(entity.Layer).lower() != layer_filter.lower():
                    continue
                if skipped < offset:
                    skipped += 1
                    continue
                result.append(_entity_info(entity))
                if len(result) >= limit:
                    break
            return result

        return await self._run(_sync)

    async def object_get(self, object_id: str) -> EntityInfo:
        return await self._run(lambda: _entity_info(self._entity_by_id(object_id)))

    async def object_count(
        self,
        type_filter: str | None = None,
        layer_filter: str | None = None,
    ) -> int:
        def _sync() -> int:
            space = self._space()
            count = 0
            for index in range(int(space.Count)):
                entity = space.Item(index)
                if type_filter and _object_type(entity).lower() != type_filter.lower():
                    continue
                if layer_filter and str(entity.Layer).lower() != layer_filter.lower():
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
        normalized_color = self._validate_color(color)

        def _sync() -> EntityInfo:
            entity = self._entity_by_id(object_id)
            canonical_layer = self._validate_layer(layer)
            canonical_linetype = self._validate_linetype(linetype)
            if canonical_layer is not None:
                entity.Layer = canonical_layer
            if normalized_color is not None:
                entity.Color = normalized_color
            if canonical_linetype is not None:
                entity.Linetype = canonical_linetype
            if visible is not None:
                entity.Visible = bool(visible)
            return _entity_info(entity)

        return await self._run(_sync)

    async def object_delete(self, object_id: str) -> dict[str, Any]:
        def _sync() -> dict[str, Any]:
            entity = self._entity_by_id(object_id)
            entity.Delete()
            return {"ok": True, "deleted_id": object_id}

        return await self._run(_sync)

    async def object_move(
        self, object_id: str, dx: float, dy: float, dz: float = 0.0
    ) -> EntityInfo:
        def _sync() -> EntityInfo:
            entity = self._entity_by_id(object_id)
            entity.Move(_point(0, 0, 0), _point(dx, dy, dz))
            return _entity_info(entity)

        return await self._run(_sync)

    async def object_copy(
        self, object_id: str, dx: float, dy: float, dz: float = 0.0
    ) -> EntityInfo:
        def _sync() -> EntityInfo:
            duplicate = self._entity_by_id(object_id).Copy()
            duplicate.Move(_point(0, 0, 0), _point(dx, dy, dz))
            return _entity_info(duplicate)

        return await self._run(_sync)

    async def object_rotate(
        self,
        object_id: str,
        base_x: float,
        base_y: float,
        angle_deg: float,
    ) -> EntityInfo:
        def _sync() -> EntityInfo:
            entity = self._entity_by_id(object_id)
            entity.Rotate(_point(base_x, base_y), math.radians(angle_deg))
            return _entity_info(entity)

        return await self._run(_sync)

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
            entity = self._entity_by_id(object_id)
            entity.ScaleEntity(_point(base_x, base_y), float(factor))
            return _entity_info(entity)

        return await self._run(_sync)

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
        normalized_color = self._validate_color(color)

        def _sync() -> EntityInfo:
            canonical_layer = self._validate_layer(layer)
            entity = self._space().AddLine(_point(x1, y1, z1), _point(x2, y2, z2))
            if canonical_layer is not None:
                entity.Layer = canonical_layer
            if normalized_color is not None:
                entity.Color = normalized_color
            return _entity_info(entity)

        return await self._run(_sync)

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
        normalized_color = self._validate_color(color)

        def _sync() -> EntityInfo:
            canonical_layer = self._validate_layer(layer)
            entity = self._space().AddCircle(_point(cx, cy), float(radius))
            if canonical_layer is not None:
                entity.Layer = canonical_layer
            if normalized_color is not None:
                entity.Color = normalized_color
            return _entity_info(entity)

        return await self._run(_sync)

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
        normalized_color = self._validate_color(color)

        def _sync() -> EntityInfo:
            canonical_layer = self._validate_layer(layer)
            entity = self._space().AddArc(
                _point(cx, cy),
                float(radius),
                math.radians(start_angle),
                math.radians(end_angle),
            )
            if canonical_layer is not None:
                entity.Layer = canonical_layer
            if normalized_color is not None:
                entity.Color = normalized_color
            return _entity_info(entity)

        return await self._run(_sync)

    async def entity_create_polyline(
        self,
        points: list[list[float]],
        closed: bool = False,
        layer: str | None = None,
        color: int | None = None,
    ) -> EntityInfo:
        if len(points) < 2 or any(len(point) < 2 for point in points):
            raise ValueError("polyline requires at least two [x, y] points")
        normalized_color = self._validate_color(color)
        flat = [float(value) for point in points for value in point[:2]]

        def _sync() -> EntityInfo:
            canonical_layer = self._validate_layer(layer)
            entity = self._space().AddLightWeightPolyline(_double_array(flat))
            entity.Closed = bool(closed)
            if canonical_layer is not None:
                entity.Layer = canonical_layer
            if normalized_color is not None:
                entity.Color = normalized_color
            return _entity_info(entity)

        return await self._run(_sync)

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
        normalized_color = self._validate_color(color)

        def _sync() -> EntityInfo:
            canonical_layer = self._validate_layer(layer)
            entity = self._space().AddText(str(text), _point(x, y), float(height))
            entity.Rotation = math.radians(rotation)
            if canonical_layer is not None:
                entity.Layer = canonical_layer
            if normalized_color is not None:
                entity.Color = normalized_color
            return _entity_info(entity)

        return await self._run(_sync)

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
        normalized_color = self._validate_color(color)
        flat = [float(value) for point in boundary_points for value in point[:2]]

        def _sync() -> EntityInfo:
            canonical_layer = self._validate_layer(layer)
            space = self._space()
            hatch = None
            boundary = None
            try:
                hatch = space.AddHatch(0, str(pattern), False)
                if str(pattern).upper() != "SOLID":
                    hatch.PatternScale = float(scale)
                    hatch.PatternAngle = math.radians(angle)
                boundary = space.AddLightWeightPolyline(_double_array(flat))
                boundary.Closed = True
                hatch.AppendOuterLoop(_dispatch_array([boundary]))
                hatch.Evaluate()
                if canonical_layer is not None:
                    hatch.Layer = canonical_layer
                if normalized_color is not None:
                    hatch.Color = normalized_color
                return _entity_info(hatch)
            except Exception:
                if hatch is not None:
                    try:
                        hatch.Delete()
                    except Exception:
                        pass
                raise
            finally:
                if boundary is not None:
                    try:
                        boundary.Delete()
                    except Exception:
                        pass

        return await self._run(_sync)

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
            canonical_layer = self._validate_layer(layer)
            entity = self._space().AddDimRotated(
                _point(x1, y1),
                _point(x2, y2),
                _point(dim_x, dim_y),
                math.radians(rotation),
            )
            if canonical_layer is not None:
                entity.Layer = canonical_layer
            return _entity_info(entity)

        return await self._run(_sync)

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
        if math.hypot(x2 - x1, y2 - y1) <= 0:
            raise ValueError("aligned dimension requires two distinct points")

        def _sync() -> EntityInfo:
            canonical_layer = self._validate_layer(layer)
            entity = self._space().AddDimAligned(
                _point(x1, y1),
                _point(x2, y2),
                _point(dim_x, dim_y),
            )
            if canonical_layer is not None:
                entity.Layer = canonical_layer
            return _entity_info(entity)

        return await self._run(_sync)

    async def layer_list(self) -> list[LayerInfo]:
        def _sync() -> list[LayerInfo]:
            doc = self._doc()
            current = str(doc.ActiveLayer.Name)
            return [
                _layer_info(doc.Layers.Item(index), current)
                for index in range(int(doc.Layers.Count))
            ]

        return await self._run(_sync)

    async def layer_create(self, name: str, color: int = 7) -> LayerInfo:
        wanted = str(name).strip()
        if not wanted:
            raise ValueError("layer name must not be empty")
        normalized_color = self._validate_color(color)
        assert normalized_color is not None

        def _sync() -> LayerInfo:
            doc = self._doc()
            if self._find_name(doc.Layers, wanted) is not None:
                raise ValueError(f"layer already exists: {wanted}")
            layer = doc.Layers.Add(wanted)
            layer.Color = normalized_color
            return _layer_info(layer, str(doc.ActiveLayer.Name))

        return await self._run(_sync)

    async def layer_set_current(self, name: str) -> dict[str, Any]:
        def _sync() -> dict[str, Any]:
            doc = self._doc()
            canonical = self._find_name(doc.Layers, name)
            if canonical is None:
                raise ValueError(f"layer does not exist: {name}")
            doc.ActiveLayer = doc.Layers.Item(canonical)
            return {"ok": True, "current_layer": canonical}

        return await self._run(_sync)

    async def block_list(self) -> list[BlockInfo]:
        def _sync() -> list[BlockInfo]:
            doc = self._doc()
            rows: list[BlockInfo] = []
            for index in range(int(doc.Blocks.Count)):
                block = doc.Blocks.Item(index)
                name = str(block.Name)
                if name.startswith("*"):
                    continue
                origin = _xyz(block.Origin)
                attribute_count = 0
                for item_index in range(int(block.Count)):
                    if str(block.Item(item_index).ObjectName) == "AcDbAttributeDefinition":
                        attribute_count += 1
                rows.append(
                    BlockInfo(
                        name=name,
                        base_point=(origin[0], origin[1], origin[2]),
                        entity_count=int(block.Count),
                        attribute_count=attribute_count,
                        is_xref=bool(block.IsXRef),
                    )
                )
            return rows

        return await self._run(_sync)

    async def block_create(
        self,
        name: str,
        object_ids: list[str],
        base_x: float = 0.0,
        base_y: float = 0.0,
    ) -> BlockInfo:
        wanted = str(name).strip()
        if not wanted:
            raise ValueError("block name must not be empty")
        if not object_ids:
            raise ValueError("block_create requires at least one object id")

        def _sync() -> BlockInfo:
            doc = self._doc()
            if self._find_name(doc.Blocks, wanted) is not None:
                raise ValueError(f"block already exists: {wanted}")
            entities = [self._entity_by_id(object_id) for object_id in object_ids]
            block = doc.Blocks.Add(_point(base_x, base_y), wanted)
            try:
                doc.CopyObjects(_dispatch_array(entities), block)
            except Exception:
                try:
                    block.Delete()
                except Exception:
                    pass
                raise
            return BlockInfo(
                name=wanted,
                base_point=(float(base_x), float(base_y), 0.0),
                entity_count=len(entities),
                attribute_count=0,
                is_xref=False,
            )

        return await self._run(_sync)

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
            doc = self._doc()
            canonical = self._find_name(doc.Blocks, name)
            if canonical is None:
                raise ValueError(f"block does not exist: {name}")
            canonical_layer = self._validate_layer(layer)
            entity = self._space().InsertBlock(
                _point(x, y),
                canonical,
                float(scale_x),
                float(scale_y),
                1.0,
                math.radians(rotation),
            )
            if canonical_layer is not None:
                entity.Layer = canonical_layer
            return _entity_info(entity)

        return await self._run(_sync)

    async def layout_list(self) -> dict[str, Any]:
        def _sync() -> dict[str, Any]:
            doc = self._doc()
            return {
                "ok": True,
                "layouts": self._collection_names(doc.Layouts),
                "current": str(doc.ActiveLayout.Name),
            }

        return await self._run(_sync)

    async def layout_create(self, name: str) -> dict[str, Any]:
        wanted = str(name).strip()
        if not wanted:
            raise ValueError("layout name must not be empty")

        def _sync() -> dict[str, Any]:
            doc = self._doc()
            if self._find_name(doc.Layouts, wanted) is not None:
                raise ValueError(f"layout already exists: {wanted}")
            doc.Layouts.Add(wanted)
            return {"ok": True, "layout": wanted}

        return await self._run(_sync)

    async def layout_set_current(self, name: str) -> dict[str, Any]:
        def _sync() -> dict[str, Any]:
            doc = self._doc()
            canonical = self._find_name(doc.Layouts, name)
            if canonical is None:
                raise ValueError(f"layout not found: {name}")
            doc.ActiveLayout = doc.Layouts.Item(canonical)
            return {"ok": True, "current": canonical}

        return await self._run(_sync)

    @staticmethod
    def _paper_viewports(layout: Any) -> list[Any]:
        block = layout.Block
        viewports = []
        for index in range(int(block.Count)):
            entity = block.Item(index)
            if str(entity.ObjectName) == "AcDbViewport":
                viewports.append(entity)
        return viewports

    def _resolve_viewport(self, doc: Any, handle: str) -> tuple[Any, str | None]:
        key = str(handle or "").strip().upper()
        if not key:
            raise ValueError("viewport handle must not be empty")
        try:
            entity = doc.HandleToObject(key)
        except Exception as exc:
            raise KeyError(f"viewport not found: {handle}") from exc
        if str(entity.ObjectName) != "AcDbViewport":
            raise ValueError(f"handle {key} is {_object_type(entity)}, not VIEWPORT")

        layout_name = None
        for index in range(int(doc.Layouts.Count)):
            layout = doc.Layouts.Item(index)
            if any(str(viewport.Handle).upper() == key for viewport in self._paper_viewports(layout)):
                layout_name = str(layout.Name)
                break
        return entity, layout_name

    @staticmethod
    def _viewport_row(viewport: Any, layout_name: str | None, created: bool) -> dict[str, Any]:
        center = _xyz(viewport.Center)
        target = _xyz(viewport.Target)
        height = float(viewport.Height)
        try:
            scale = float(viewport.CustomScale)
        except Exception:
            scale = None
        try:
            locked = bool(viewport.DisplayLocked)
        except Exception:
            locked = None
        return {
            "handle": str(viewport.Handle),
            "layout": layout_name,
            "center": center[:2],
            "width": float(viewport.Width),
            "height": height,
            "view_center": target[:2],
            "view_height": height / scale if scale else None,
            "scale": scale,
            "locked": locked,
            "status": int(bool(viewport.ViewportOn)),
            "is_main": None,
            "created_by_backend": created,
        }

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
        if width <= 0 or height <= 0:
            raise ValueError("viewport width and height must be > 0")
        if scale <= 0:
            raise ValueError("viewport scale must be > 0")

        def _sync() -> dict[str, Any]:
            doc = self._doc()
            canonical = self._find_name(doc.Layouts, layout)
            if canonical is None:
                raise ValueError(f"layout not found: {layout}")
            if canonical.lower() == "model":
                raise ValueError("viewports require a paper-space layout")

            previous = str(doc.ActiveLayout.Name)
            target_layout = doc.Layouts.Item(canonical)
            if previous != canonical:
                doc.ActiveLayout = target_layout
            viewport = None
            try:
                viewport = doc.PaperSpace.AddPViewport(
                    _point(center_x, center_y), float(width), float(height)
                )
                viewport.Display(True)
                viewport.CustomScale = float(scale)
                viewport.Target = _point(view_center_x, view_center_y)
                return {"ok": True, **self._viewport_row(viewport, canonical, True)}
            except Exception:
                if viewport is not None:
                    try:
                        viewport.Delete()
                    except Exception:
                        pass
                raise
            finally:
                if previous != canonical:
                    doc.ActiveLayout = doc.Layouts.Item(previous)

        result = await self._run(_sync)
        self._created_viewport_handles.add(str(result["handle"]).upper())
        return result

    async def viewport_list(self, layout: str | None = None) -> dict[str, Any]:
        def _sync() -> dict[str, Any]:
            doc = self._doc()
            if layout is None:
                names = [
                    name
                    for name in self._collection_names(doc.Layouts)
                    if name.lower() != "model"
                ]
            else:
                canonical = self._find_name(doc.Layouts, layout)
                if canonical is None:
                    raise ValueError(f"layout not found: {layout}")
                if canonical.lower() == "model":
                    raise ValueError("model space has no paper-space viewports")
                names = [canonical]

            rows = []
            for name in names:
                for viewport in self._paper_viewports(doc.Layouts.Item(name)):
                    key = str(viewport.Handle).upper()
                    rows.append(
                        self._viewport_row(
                            viewport,
                            name,
                            key in self._created_viewport_handles,
                        )
                    )
            return {
                "ok": True,
                "viewports": rows,
                "count": len(rows),
                "note": (
                    "ActiveX exposes no reliable main-viewport predicate; is_main is null. "
                    "Delete requires force=true for viewports not created by this backend."
                ),
            }

        return await self._run(_sync)

    async def viewport_set_scale(self, handle: str, scale: float) -> dict[str, Any]:
        if scale <= 0:
            raise ValueError("viewport scale must be > 0")

        def _sync() -> dict[str, Any]:
            viewport, layout_name = self._resolve_viewport(self._doc(), handle)
            viewport.CustomScale = float(scale)
            actual = float(viewport.CustomScale)
            return {
                "ok": True,
                "handle": str(viewport.Handle),
                "layout": layout_name,
                "scale": actual,
                "view_height": float(viewport.Height) / actual,
            }

        return await self._run(_sync)

    async def viewport_lock(self, handle: str, locked: bool = True) -> dict[str, Any]:
        def _sync() -> dict[str, Any]:
            viewport, layout_name = self._resolve_viewport(self._doc(), handle)
            viewport.DisplayLocked = bool(locked)
            return {
                "ok": True,
                "handle": str(viewport.Handle),
                "layout": layout_name,
                "locked": bool(viewport.DisplayLocked),
            }

        return await self._run(_sync)

    async def viewport_delete(self, handle: str, force: bool = False) -> dict[str, Any]:
        key = str(handle or "").strip().upper()
        if not key:
            raise ValueError("viewport handle must not be empty")
        if key not in self._created_viewport_handles and not force:
            raise StateConflictError(
                "Refusing to delete a pre-existing/unknown viewport because ActiveX exposes no "
                "reliable main-viewport predicate. Pass force=true only after verifying the sheet."
            )

        def _sync() -> dict[str, Any]:
            viewport, layout_name = self._resolve_viewport(self._doc(), key)
            actual = str(viewport.Handle).upper()
            viewport.Delete()
            return {
                "ok": True,
                "handle": actual,
                "layout": layout_name,
                "was_main": None,
                "forced": bool(force),
            }

        result = await self._run(_sync)
        self._created_viewport_handles.discard(key)
        return result

    async def view_zoom_extents(self) -> dict[str, Any]:
        def _sync() -> dict[str, Any]:
            app = self._app()
            self._doc()
            app.ZoomExtents()
            return {"ok": True, "mode": "extents"}

        return await self._run(_sync)

    async def view_zoom_window(
        self, x1: float, y1: float, x2: float, y2: float
    ) -> dict[str, Any]:
        low_x, high_x = sorted((float(x1), float(x2)))
        low_y, high_y = sorted((float(y1), float(y2)))
        if low_x == high_x or low_y == high_y:
            raise ValueError("zoom window must have non-zero width and height")

        def _sync() -> dict[str, Any]:
            app = self._app()
            self._doc()
            app.ZoomWindow(_point(low_x, low_y), _point(high_x, high_y))
            return {
                "ok": True,
                "mode": "window",
                "min": [low_x, low_y],
                "max": [high_x, high_y],
            }

        return await self._run(_sync)

    async def view_screenshot(self) -> bytes:
        if not _PIL_OK:
            raise UnsupportedCapabilityError(
                "autocad.viewport.capture",
                "Live screenshot capture requires the COM optional dependency Pillow.",
            )

        def _sync() -> bytes:
            app = self._app()
            self._doc()
            try:
                hwnd = int(app.HWND)
            except Exception as exc:
                raise RuntimeError("AutoCAD did not expose a valid main-window HWND") from exc
            png = _capture_window_png(hwnd)
            if not png.startswith(b"\x89PNG\r\n\x1a\n"):
                raise RuntimeError("AutoCAD screenshot capture did not produce a PNG")
            return png

        return await self._run(_sync)

    async def transaction_begin(self) -> dict[str, Any]:
        if self._transaction_depth >= self.settings.transaction_depth:
            raise StateConflictError(
                f"transaction depth limit reached: {self.settings.transaction_depth}"
            )

        def _sync() -> dict[str, Any]:
            doc = self._doc()
            self._transaction_depth += 1
            try:
                doc.StartUndoMark()
            except Exception:
                self._transaction_depth -= 1
                raise
            return {"ok": True, "transaction_depth": self._transaction_depth}

        return await self._run(_sync)

    async def transaction_commit(self) -> dict[str, Any]:
        if self._transaction_depth <= 0:
            raise StateConflictError("No active transaction")

        def _sync() -> dict[str, Any]:
            self._doc().EndUndoMark()
            return {"ok": True}

        await self._run(_sync)
        self._transaction_depth -= 1
        return {"ok": True, "transaction_depth": self._transaction_depth}

    async def transaction_rollback(self) -> dict[str, Any]:
        if self._transaction_depth <= 0:
            raise StateConflictError("No active transaction to rollback")

        def _sync() -> dict[str, Any]:
            doc = self._doc()
            doc.EndUndoMark()
            doc.SendCommand("_.UNDO _1\n")
            return {"ok": True}

        await self._run(_sync)
        self._transaction_depth -= 1
        return {
            "ok": True,
            "transaction_depth": self._transaction_depth,
            "rolled_back": True,
            "queued": True,
        }

    async def undo(self) -> dict[str, Any]:
        def _sync() -> dict[str, Any]:
            self._doc().SendCommand("_.UNDO _1\n")
            return {"ok": True, "queued": True}

        return await self._run(_sync)

    async def redo(self) -> dict[str, Any]:
        def _sync() -> dict[str, Any]:
            self._doc().SendCommand("_.REDO\n")
            return {"ok": True, "queued": True}

        return await self._run(_sync)
