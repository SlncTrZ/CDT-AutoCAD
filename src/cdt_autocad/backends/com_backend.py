"""Live AutoCAD backend using the Windows ActiveX/COM automation API.
Wing: code | Topic: autocad-a3-analysis | Updated: 2026-09-09 22:58

This backend intentionally implements only the existing A0/A1 provider contract. It does not
copy the much larger reference server surface. All COM work is serialized through one STA worker
thread, while attach/start policy and filesystem boundaries stay owned by CDT_Engineer.
"""

from __future__ import annotations

import asyncio
import ctypes
import io
import math
import os
import re
import sys
import threading
import time
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
        import win32com.client.dynamic
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

# Autodesk's version-dependent ActiveX ProgID series. These entries are used only to identify
# the attached application and report certification context; they do not imply that every release
# is certified by CDT-AutoCAD.
_AUTOCAD_RELEASE_BY_COM_VERSION = {
    "23.0": "2019",
    "23.1": "2020",
    "24.0": "2021",
    "24.1": "2022",
    "24.2": "2023",
    "24.3": "2024",
    "25.0": "2025",
    "25.1": "2026",
    "26.0": "2027",
}
_PRIMARY_CERTIFICATION_RELEASE = "2027"
_PRIMARY_CERTIFICATION_COM_VERSION = "26.0"
_PRIMARY_CERTIFICATION_PROGID = "AutoCAD.Application.26"
_RPC_E_CALL_REJECTED = -2147418111
_RPC_E_SERVERCALL_RETRYLATER = -2147417846
_COM_BUSY_HRESULTS = {_RPC_E_CALL_REJECTED, _RPC_E_SERVERCALL_RETRYLATER}

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


def _autocad_release_info(raw_version: str) -> tuple[str | None, str | None]:
    """Map the leading ActiveX COM version to a known AutoCAD release without over-claiming support."""
    match = re.search(r"(\d+\.\d+)", str(raw_version or "").strip())
    if match is None:
        return None, None
    com_version = match.group(1)
    return com_version, _AUTOCAD_RELEASE_BY_COM_VERSION.get(com_version)


def _com_hresult(exc: BaseException) -> int | None:
    try:
        return int(exc.args[0])
    except (IndexError, TypeError, ValueError):
        return None


def _com_call_with_busy_retry(
    func,
    *,
    attempts: int = 20,
    delay_seconds: float = 0.05,
) -> Any:
    if attempts < 1:
        raise ValueError("attempts must be >= 1")
    for attempt in range(attempts):
        try:
            return func()
        except Exception as exc:
            if _com_hresult(exc) not in _COM_BUSY_HRESULTS or attempt + 1 >= attempts:
                raise
            time.sleep(delay_seconds)
    raise RuntimeError("unreachable COM call retry state")


def _com_property_with_busy_retry(
    obj: Any,
    name: str,
    *,
    attempts: int = 20,
    delay_seconds: float = 0.05,
) -> Any:
    return _com_call_with_busy_retry(
        lambda: getattr(obj, name),
        attempts=attempts,
        delay_seconds=delay_seconds,
    )


def _ensure_autocad_type_library() -> bool:
    if not _COM_IMPORTS_OK:
        return False
    common_program_files = os.environ.get("CommonProgramFiles")
    if not common_program_files:
        return False
    shared = Path(common_program_files) / "Autodesk Shared"
    candidates = [shared / "acax26enu.tlb"]
    if shared.exists():
        candidates.extend(sorted(shared.glob("acax26*.tlb")))
    seen: set[Path] = set()
    for candidate in candidates:
        if candidate in seen or not candidate.is_file():
            continue
        seen.add(candidate)
        try:
            attributes = pythoncom.LoadTypeLib(str(candidate)).GetLibAttr()
            win32com.client.gencache.EnsureModule(
                attributes[0], attributes[1], attributes[3], attributes[4]
            )
            return True
        except Exception:
            continue
    return False


def _resolve_com_attr_name(obj: Any, name: str, *, writable: bool = False) -> str:
    mapping_name = "_prop_map_put_" if writable else "_prop_map_get_"
    mapping = getattr(obj, mapping_name, None)
    if isinstance(mapping, dict):
        if name in mapping:
            return name
        wanted = name.casefold()
        for candidate in mapping:
            if str(candidate).casefold() == wanted:
                return str(candidate)
    return name


def _com_get_attr(obj: Any, name: str) -> Any:
    resolved = _resolve_com_attr_name(obj, name)
    return _com_property_with_busy_retry(obj, resolved)


def _com_set_attr(obj: Any, name: str, value: Any) -> None:
    resolved = _resolve_com_attr_name(obj, name, writable=True)
    _com_call_with_busy_retry(lambda: setattr(obj, resolved, value))


def _optional_com_property(obj: Any, name: str) -> Any | None:
    try:
        return _com_property_with_busy_retry(obj, name)
    except Exception:
        return None


def _application_text_property(app: Any, name: str) -> str | None:
    """Read optional COM diagnostics without turning metadata failure into attach failure."""
    try:
        value = _com_property_with_busy_retry(app, name)
    except Exception:
        return None
    text = str(value or "").strip()
    return text or None


def _inspect_application(app: Any) -> dict[str, Any]:
    raw_version = _application_text_property(app, "Version") or ""
    com_version, release = _autocad_release_info(raw_version)
    return {
        "name": _application_text_property(app, "Name"),
        "version": raw_version or None,
        "com_version": com_version,
        "release": release,
        "caption": _application_text_property(app, "Caption"),
        "full_name": _application_text_property(app, "FullName"),
        "primary_target_match": (
            release == _PRIMARY_CERTIFICATION_RELEASE if release is not None else None
        ),
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
        "AcDb3dSolid": "3DSOLID",
        "AcDb3dPolyline": "3DPOLYLINE",
    }
    if name in mapping:
        return mapping[name]
    if name.startswith("AcDb") and name.endswith("Dimension"):
        return "DIMENSION"
    return name.removeprefix("AcDb").upper()


def _com_float_property(entity: Any, name: str) -> float:
    try:
        return float(getattr(entity, name))
    except Exception as exc:
        raise RuntimeError(f"AutoCAD property unavailable: {name}") from exc


def _com_vector_property(entity: Any, name: str) -> list[float]:
    try:
        return _xyz(getattr(entity, name))
    except Exception as exc:
        raise RuntimeError(f"AutoCAD property unavailable: {name}") from exc


def _com_bounding_box(entity: Any) -> dict[str, list[float]]:
    no_arg_error: Exception | None = None
    try:
        result = entity.GetBoundingBox()
        if isinstance(result, (tuple, list)) and len(result) == 2:
            return {"min": _xyz(result[0]), "max": _xyz(result[1])}
    except Exception as exc:
        no_arg_error = exc

    if not _COM_IMPORTS_OK:
        if no_arg_error is not None:
            raise RuntimeError("GetBoundingBox failed") from no_arg_error
        raise RuntimeError("pywin32 COM support is unavailable")
    minimum = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_VARIANT, None)
    maximum = win32com.client.VARIANT(pythoncom.VT_BYREF | pythoncom.VT_VARIANT, None)
    try:
        entity.GetBoundingBox(minimum, maximum)
    except Exception as exc:
        raise RuntimeError("GetBoundingBox failed") from exc
    return {"min": _xyz(minimum.value), "max": _xyz(maximum.value)}


def _entity_property_view(entity: Any) -> Any:
    if not _COM_IMPORTS_OK:
        return entity
    ole = getattr(entity, "_oleobj_", None)
    if ole is None:
        return entity
    try:
        return win32com.client.dynamic.DumbDispatch(ole)
    except Exception:
        return entity


def _entity_info(entity: Any) -> EntityInfo:
    entity = _entity_property_view(entity)
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
        color=int(_com_get_attr(entity, "Color")),
        linetype=linetype,
        visible=visible,
        properties=properties,
    )


def _layer_info(layer: Any, current_layer: str) -> LayerInfo:
    name = str(_com_get_attr(layer, "Name"))
    return LayerInfo(
        name=name,
        color=abs(int(_com_get_attr(layer, "Color"))),
        linetype=str(_com_get_attr(layer, "Linetype")),
        lineweight=int(_com_get_attr(layer, "LineWeight")),
        is_on=bool(_com_get_attr(layer, "LayerOn")),
        is_frozen=bool(_com_get_attr(layer, "Freeze")),
        is_locked=bool(_com_get_attr(layer, "Lock")),
        is_current=name.lower() == current_layer.lower(),
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
        self._application_metadata: dict[str, Any] | None = None
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
        self._application_metadata = None
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
            "autocad.dimensions.advanced": Capability(
                False, reason="A3_2_staged_pending_live_verification"
            ),
            "autocad.analysis.measurement": Capability(
                False, reason="A3_3_staged_pending_live_verification"
            ),
            "autocad.analysis.intersections": Capability(
                False, reason="A3_3_staged_pending_live_verification"
            ),
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
            "autocad.viewport.manage": supported("activex_pviewport"),
            "autocad.view.zoom": supported("activex_live_view"),
            "autocad.viewport.capture": Capability(
                self.runtime_available and _PIL_OK,
                "windows_gdi" if self.runtime_available and _PIL_OK else None,
                (
                    None
                    if self.runtime_available and _PIL_OK
                    else self._runtime_reason() or "optional_dependency_missing:pillow"
                ),
            ),
            "autocad.view.3d": Capability(False, reason="A3_staged_pending_live_verification"),
            "autocad.geometry.3d_polyline": Capability(
                False, reason="A3_staged_pending_live_verification"
            ),
            "autocad.solid.acis": Capability(False, reason="A3_staged_pending_live_verification"),
            "autocad.solid.loft": Capability(False, reason="activex_no_typed_loft_api"),
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
            "a2_implementation_state": "release_candidate",
            "a2_live_verification": "pending_real_autocad",
            "live_certification": {
                "state": "pending_real_autocad",
                "primary_release": _PRIMARY_CERTIFICATION_RELEASE,
                "primary_com_version": _PRIMARY_CERTIFICATION_COM_VERSION,
                "primary_progid": _PRIMARY_CERTIFICATION_PROGID,
                "required_edition": "full",
                "required_platform": "windows_x64",
                "compatibility_policy": "explicit_native_matrix_required",
            },
            "application": (
                dict(self._application_metadata) if self._application_metadata is not None else None
            ),
            "platform": sys.platform,
        }

    def _app(self) -> Any:
        if not self.runtime_available:
            reason = self._runtime_reason() or "unknown"
            raise RuntimeError(f"AutoCAD COM backend unavailable: {reason}")
        generation = int(getattr(_THREAD_STATE, "generation", self._generation))
        app = self._apps.get(generation)
        if app is not None:
            if self._application_metadata is None:
                self._application_metadata = _inspect_application(app)
            return app

        progid = self.settings.com_progid
        _ensure_autocad_type_library()
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
        self._application_metadata = _inspect_application(app)
        self._connected = True
        return app

    def _doc(self) -> Any:
        app = self._app()
        documents = _com_property_with_busy_retry(app, "Documents")
        if int(_com_property_with_busy_retry(documents, "Count")) <= 0:
            raise StateConflictError(
                "No document open in AutoCAD; call document_new or document_open first"
            )
        doc = _com_property_with_busy_retry(app, "ActiveDocument")
        key = (
            str(_com_property_with_busy_retry(doc, "Name")),
            str(_optional_com_property(doc, "FullName") or ""),
        )
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
            active_layout = _com_property_with_busy_retry(doc, "ActiveLayout")
            return _com_property_with_busy_retry(active_layout, "Block")
        except Exception:
            return _com_property_with_busy_retry(doc, "ModelSpace")

    @staticmethod
    def _collection_names(collection: Any) -> list[str]:
        count = int(_com_property_with_busy_retry(collection, "Count"))
        names: list[str] = []
        for index in range(count):
            item = _com_call_with_busy_retry(lambda index=index: collection.Item(index))
            names.append(str(_com_property_with_busy_retry(item, "Name")))
        return names

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
            self._application_metadata = None
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
            self._application_metadata = None
            hr = exc.args[0] if exc.args else 0
            detail = exc.args[1] if len(exc.args) > 1 else str(exc)
            raise RuntimeError(f"AutoCAD COM error ({hr:#010x}): {detail}") from exc

    def _entity_by_id(self, object_id: str) -> Any:
        try:
            return self._doc().HandleToObject(str(object_id).strip())
        except Exception as exc:
            raise KeyError(f"entity not found: {object_id}") from exc

    @staticmethod
    def _normalize_extend_mode(extend_mode: str) -> tuple[str, int]:
        normalized = str(extend_mode).strip().lower()
        values = {"none": 0, "this": 1, "other": 2, "both": 3}
        if normalized not in values:
            raise ValueError("extend_mode must be one of: none, this, other, both")
        return normalized, values[normalized]

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

    def _solid_by_id(self, handle: str) -> Any:
        key = str(handle or "").strip()
        if not key:
            raise ValueError("solid handle must not be empty")
        try:
            solid = self._doc().HandleToObject(key)
        except Exception as exc:
            raise KeyError(f"solid not found: {handle}") from exc
        if str(getattr(solid, "ObjectName", "")) != "AcDb3dSolid":
            raise ValueError(f"handle {key} is {_object_type(solid)}, not 3DSOLID")
        return solid

    @staticmethod
    def _solid_info(solid: Any) -> dict[str, Any]:
        lower, upper = solid.GetBoundingBox()
        centroid = _xyz(solid.Centroid)
        raw_solid_type = _optional_com_property(solid, "SolidType")
        return {
            "ok": True,
            "handle": str(solid.Handle),
            "type": "3DSOLID",
            "layer": str(solid.Layer),
            "visible": bool(solid.Visible),
            "solid_type": str(raw_solid_type) if raw_solid_type is not None else None,
            "volume": float(solid.Volume),
            "centroid": centroid,
            "bounding_box": {"min": _xyz(lower), "max": _xyz(upper)},
        }

    def _region_from_profile(self, doc: Any, profile_handle: str) -> Any:
        key = str(profile_handle or "").strip()
        if not key:
            raise ValueError("profile handle must not be empty")
        try:
            profile = doc.HandleToObject(key)
        except Exception as exc:
            raise KeyError(f"profile not found: {profile_handle}") from exc

        profile_type = str(getattr(profile, "ObjectName", ""))
        if profile_type == "AcDbRegion":
            return profile.Copy()

        allowed_profile_types = {
            "AcDbArc",
            "AcDbCircle",
            "AcDbEllipse",
            "AcDbLine",
            "AcDbPolyline",
            "AcDb2dPolyline",
            "AcDb3dPolyline",
            "AcDbSpline",
        }
        if profile_type not in allowed_profile_types:
            raise ValueError(
                "profile must be a Region or closed coplanar Arc/Circle/Ellipse/Line/Polyline/Spline"
            )

        temporary_profile = profile.Copy()
        try:
            raw_regions = doc.ModelSpace.AddRegion(_dispatch_array([temporary_profile]))
            regions = list(raw_regions or [])
        except Exception:
            try:
                temporary_profile.Delete()
            except Exception:
                pass
            raise
        finally:
            # AddRegion normally consumes the copied source curve. If the COM
            # implementation leaves it alive, delete only the temporary copy.
            try:
                temporary_profile.Delete()
            except Exception:
                pass

        if len(regions) != 1:
            for region in regions:
                try:
                    region.Delete()
                except Exception:
                    pass
            raise ValueError(
                f"profile {profile_handle} must produce exactly one closed planar region; "
                f"AutoCAD produced {len(regions)}"
            )
        return regions[0]

    async def document_new(self) -> dict[str, Any]:
        if self._transaction_depth > 0:
            raise StateConflictError("Cannot create a new document while a transaction is active")

        def _sync() -> dict[str, Any]:
            documents = _com_property_with_busy_retry(self._app(), "Documents")
            doc = documents.Add()
            name = str(_com_property_with_busy_retry(doc, "Name"))
            return {"ok": True, "name": name, "backend": self.name}

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
            documents = _com_property_with_busy_retry(self._app(), "Documents")
            doc = documents.Open(str(target))
            return {
                "ok": True,
                "name": str(_com_property_with_busy_retry(doc, "Name")),
                "path": str(_optional_com_property(doc, "FullName") or target),
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
            active_layout_obj = _com_property_with_busy_retry(doc, "ActiveLayout")
            active_layout = str(_com_property_with_busy_retry(active_layout_obj, "Name"))
            space = _com_property_with_busy_retry(active_layout_obj, "Block")
            full_name = str(_optional_com_property(doc, "FullName") or "")
            try:
                units_code = int(_com_call_with_busy_retry(lambda: doc.GetVariable("INSUNITS")))
            except Exception:
                units_code = 0
            application = _inspect_application(app)
            self._application_metadata = application
            return {
                "name": str(_com_property_with_busy_retry(doc, "Name")),
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
                "autocad_version": application["version"],
                "autocad_com_version": application["com_version"],
                "autocad_release": application["release"],
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
            name = str(_com_property_with_busy_retry(doc, "Name"))
            full_name = str(_optional_com_property(doc, "FullName") or target)
            self._document_scope_key = (name, full_name)
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
            active_layout = _com_property_with_busy_retry(doc, "ActiveLayout")
            previous = str(_com_property_with_busy_retry(active_layout, "Name"))
            selected = previous
            if layout is not None:
                layouts = _com_property_with_busy_retry(doc, "Layouts")
                canonical = self._find_name(layouts, layout)
                if canonical is None:
                    raise ValueError(f"layout not found: {layout}")
                selected = canonical
                if canonical != previous:
                    target_layout = _com_call_with_busy_retry(lambda: layouts.Item(canonical))
                    _com_set_attr(doc, "ActiveLayout", target_layout)
            background_plot = None
            try:
                background_plot = _com_call_with_busy_retry(
                    lambda: doc.GetVariable("BACKGROUNDPLOT")
                )
                _com_call_with_busy_retry(lambda: doc.SetVariable("BACKGROUNDPLOT", 0))
                plot = _com_property_with_busy_retry(doc, "Plot")
                ok = bool(
                    _com_call_with_busy_retry(
                        lambda: plot.PlotToFile(str(target), "DWG To PDF.pc3")
                    )
                )
                if not ok:
                    raise RuntimeError("AutoCAD PlotToFile returned false")
                return {"ok": True, "path": str(target), "layout": selected}
            finally:
                try:
                    if background_plot is not None:
                        _com_call_with_busy_retry(
                            lambda: doc.SetVariable("BACKGROUNDPLOT", background_plot)
                        )
                finally:
                    if selected != previous:
                        layouts = _com_property_with_busy_retry(doc, "Layouts")
                        previous_layout = _com_call_with_busy_retry(
                            lambda: layouts.Item(previous)
                        )
                        _com_set_attr(doc, "ActiveLayout", previous_layout)

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

    async def object_measure(self, object_id: str) -> dict[str, Any]:
        def _sync() -> dict[str, Any]:
            entity = self._entity_by_id(object_id)
            entity_type = _object_type(entity)
            result: dict[str, Any] = {
                "object_id": str(entity.Handle),
                "type": entity_type,
                "coordinate_frame": "wcs",
                "bounding_box": _com_bounding_box(entity),
            }
            if entity_type == "LINE":
                result["length"] = _com_float_property(entity, "Length")
            elif entity_type == "CIRCLE":
                result.update(
                    radius=_com_float_property(entity, "Radius"),
                    circumference=_com_float_property(entity, "Circumference"),
                    area=_com_float_property(entity, "Area"),
                )
            elif entity_type == "ARC":
                result.update(
                    radius=_com_float_property(entity, "Radius"),
                    length=_com_float_property(entity, "ArcLength"),
                )
            elif entity_type in {"LWPOLYLINE", "POLYLINE", "3DPOLYLINE"}:
                result["length"] = _com_float_property(entity, "Length")
                if entity_type != "3DPOLYLINE":
                    result["area"] = _com_float_property(entity, "Area")
            elif entity_type in {"HATCH", "REGION"}:
                result["area"] = _com_float_property(entity, "Area")
            elif entity_type == "3DSOLID":
                result["volume"] = _com_float_property(entity, "Volume")
                result["centroid"] = _com_vector_property(entity, "Centroid")
            return result

        return await self._run(_sync)

    async def drawing_extents(self) -> dict[str, Any]:
        def _sync() -> dict[str, Any]:
            space = self._space()
            minimum: list[float] | None = None
            maximum: list[float] | None = None
            for index in range(int(space.Count)):
                entity = space.Item(index)
                box = _com_bounding_box(entity)
                if minimum is None:
                    minimum = list(box["min"])
                    maximum = list(box["max"])
                    continue
                assert maximum is not None
                minimum = [min(minimum[i], box["min"][i]) for i in range(3)]
                maximum = [max(maximum[i], box["max"][i]) for i in range(3)]
            bounding_box = None if minimum is None else {"min": minimum, "max": maximum}
            return {
                "empty": bounding_box is None,
                "bounding_box": bounding_box,
                "coordinate_frame": "wcs",
            }

        return await self._run(_sync)

    async def object_intersections(
        self, first_id: str, second_id: str, extend_mode: str = "none"
    ) -> dict[str, Any]:
        first_key = str(first_id).strip()
        second_key = str(second_id).strip()
        if not first_key or not second_key:
            raise ValueError("intersection object IDs must not be empty")
        if first_key.upper() == second_key.upper():
            raise ValueError("intersection requires two distinct object IDs")
        normalized_mode, extend_option = self._normalize_extend_mode(extend_mode)

        def _sync() -> dict[str, Any]:
            first = self._entity_by_id(first_key)
            second = self._entity_by_id(second_key)
            raw = first.IntersectWith(second, extend_option)
            if raw is None:
                values: list[float] = []
            else:
                try:
                    values = [float(value) for value in raw]
                except (TypeError, ValueError) as exc:
                    raise RuntimeError("AutoCAD IntersectWith returned a non-numeric sequence payload") from exc
            if len(values) % 3 != 0:
                raise RuntimeError("AutoCAD IntersectWith returned a non-XYZ payload")
            points = [values[index : index + 3] for index in range(0, len(values), 3)]
            return {
                "first_id": str(first.Handle),
                "second_id": str(second.Handle),
                "extend_mode": normalized_mode,
                "coordinate_frame": "wcs",
                "points": points,
                "count": len(points),
            }

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
                _com_set_attr(entity, "Color", normalized_color)
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
                _com_set_attr(entity, "Color", normalized_color)
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
                _com_set_attr(entity, "Color", normalized_color)
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
                _com_set_attr(entity, "Color", normalized_color)
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
                _com_set_attr(entity, "Color", normalized_color)
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
                _com_set_attr(entity, "Color", normalized_color)
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
                    _com_set_attr(hatch, "Color", normalized_color)
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
            canonical_layer = self._validate_layer(layer)
            entity = self._space().AddDimAngular(
                _point(vertex_x, vertex_y),
                _point(first_x, first_y),
                _point(second_x, second_y),
                _point(text_x, text_y),
            )
            if canonical_layer is not None:
                entity.Layer = canonical_layer
            return _entity_info(entity)

        return await self._run(_sync)

    async def dimension_radial(
        self,
        center_x: float,
        center_y: float,
        chord_x: float,
        chord_y: float,
        leader_length: float,
        layer: str | None = None,
    ) -> EntityInfo:
        self._validate_radial_dimension(center_x, center_y, chord_x, chord_y, leader_length)

        def _sync() -> EntityInfo:
            canonical_layer = self._validate_layer(layer)
            entity = self._space().AddDimRadial(
                _point(center_x, center_y),
                _point(chord_x, chord_y),
                float(leader_length),
            )
            if canonical_layer is not None:
                entity.Layer = canonical_layer
            return _entity_info(entity)

        return await self._run(_sync)

    async def dimension_diametric(
        self,
        center_x: float,
        center_y: float,
        chord_x: float,
        chord_y: float,
        leader_length: float,
        layer: str | None = None,
    ) -> EntityInfo:
        self._validate_diametric_dimension(center_x, center_y, chord_x, chord_y, leader_length)
        far_chord_x = 2.0 * float(center_x) - float(chord_x)
        far_chord_y = 2.0 * float(center_y) - float(chord_y)

        def _sync() -> EntityInfo:
            canonical_layer = self._validate_layer(layer)
            entity = self._space().AddDimDiametric(
                _point(chord_x, chord_y),
                _point(far_chord_x, far_chord_y),
                float(leader_length),
            )
            if canonical_layer is not None:
                entity.Layer = canonical_layer
            return _entity_info(entity)

        return await self._run(_sync)

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

        def _sync() -> EntityInfo:
            canonical_layer = self._validate_layer(layer)
            entity = self._space().AddDimOrdinate(
                _point(definition_x, definition_y),
                _point(leader_x, leader_y),
                normalized_axis == "x",
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
            _com_set_attr(layer, "Color", normalized_color)
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
                viewports.append(_entity_property_view(entity))
        return viewports

    def _resolve_viewport(self, doc: Any, handle: str) -> tuple[Any, str | None]:
        key = str(handle or "").strip().upper()
        if not key:
            raise ValueError("viewport handle must not be empty")
        try:
            entity = _entity_property_view(doc.HandleToObject(key))
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

    async def view_set_direction(self, dx: float, dy: float, dz: float) -> dict[str, Any]:
        length = math.sqrt(float(dx) ** 2 + float(dy) ** 2 + float(dz) ** 2)
        if length <= 0:
            raise ValueError("3D view direction must be a non-zero vector")
        direction = [float(dx) / length, float(dy) / length, float(dz) / length]

        def _sync() -> dict[str, Any]:
            doc = self._doc()
            viewport = doc.ActiveViewport
            viewport.Direction = _point(*direction)
            doc.ActiveViewport = viewport
            self._app().ZoomExtents()
            return {"ok": True, "direction": direction, "normalized": True}

        return await self._run(_sync)

    async def entity_create_3d_polyline(
        self, points: list[list[float]], closed: bool = False
    ) -> dict[str, Any]:
        if len(points) < 2 or any(len(point) < 3 for point in points):
            raise ValueError("3D polyline requires at least two [x, y, z] points")
        normalized = [
            [float(point[0]), float(point[1]), float(point[2])] for point in points
        ]
        flat = [value for point in normalized for value in point]

        def _sync() -> dict[str, Any]:
            entity = self._doc().ModelSpace.Add3DPoly(_double_array(flat))
            entity.Closed = bool(closed)
            return {
                "ok": True,
                "handle": str(entity.Handle),
                "type": "3DPOLYLINE",
                "points": normalized,
                "closed": bool(entity.Closed),
                "coordinate_frame": "wcs",
            }

        return await self._run(_sync)

    async def solid_box(
        self,
        cx: float,
        cy: float,
        cz: float,
        length: float,
        width: float,
        height: float,
    ) -> dict[str, Any]:
        if length <= 0:
            raise ValueError("box length must be > 0")
        if width <= 0:
            raise ValueError("box width must be > 0")
        if height <= 0:
            raise ValueError("box height must be > 0")

        def _sync() -> dict[str, Any]:
            solid = self._doc().ModelSpace.AddBox(
                _point(cx, cy, cz), float(length), float(width), float(height)
            )
            return self._solid_info(solid)

        return await self._run(_sync)

    async def solid_cylinder(
        self,
        cx: float,
        cy: float,
        cz: float,
        radius: float,
        height: float,
    ) -> dict[str, Any]:
        if radius <= 0:
            raise ValueError("cylinder radius must be > 0")
        if height <= 0:
            raise ValueError("cylinder height must be > 0")

        def _sync() -> dict[str, Any]:
            solid = self._doc().ModelSpace.AddCylinder(
                _point(cx, cy, cz), float(radius), float(height)
            )
            return self._solid_info(solid)

        return await self._run(_sync)

    async def solid_sphere(
        self,
        cx: float,
        cy: float,
        cz: float,
        radius: float,
    ) -> dict[str, Any]:
        if radius <= 0:
            raise ValueError("sphere radius must be > 0")

        def _sync() -> dict[str, Any]:
            solid = self._doc().ModelSpace.AddSphere(_point(cx, cy, cz), float(radius))
            return self._solid_info(solid)

        return await self._run(_sync)

    async def solid_cone(
        self,
        cx: float,
        cy: float,
        cz: float,
        radius: float,
        height: float,
    ) -> dict[str, Any]:
        if radius <= 0:
            raise ValueError("cone radius must be > 0")
        if height <= 0:
            raise ValueError("cone height must be > 0")

        def _sync() -> dict[str, Any]:
            solid = self._doc().ModelSpace.AddCone(
                _point(cx, cy, cz), float(radius), float(height)
            )
            return self._solid_info(solid)

        return await self._run(_sync)

    async def solid_torus(
        self,
        cx: float,
        cy: float,
        cz: float,
        torus_radius: float,
        tube_radius: float,
    ) -> dict[str, Any]:
        if torus_radius <= 0:
            raise ValueError("torus_radius must be > 0")
        if tube_radius <= 0:
            raise ValueError("tube_radius must be > 0")

        def _sync() -> dict[str, Any]:
            solid = self._doc().ModelSpace.AddTorus(
                _point(cx, cy, cz), float(torus_radius), float(tube_radius)
            )
            return self._solid_info(solid)

        return await self._run(_sync)

    async def solid_wedge(
        self,
        cx: float,
        cy: float,
        cz: float,
        length: float,
        width: float,
        height: float,
    ) -> dict[str, Any]:
        if length <= 0:
            raise ValueError("wedge length must be > 0")
        if width <= 0:
            raise ValueError("wedge width must be > 0")
        if height <= 0:
            raise ValueError("wedge height must be > 0")

        def _sync() -> dict[str, Any]:
            solid = self._doc().ModelSpace.AddWedge(
                _point(cx, cy, cz), float(length), float(width), float(height)
            )
            return self._solid_info(solid)

        return await self._run(_sync)

    async def solid_extrude(
        self,
        profile_handle: str,
        height: float,
        taper_angle: float = 0.0,
    ) -> dict[str, Any]:
        if height == 0:
            raise ValueError("extrude height must be non-zero")
        if not -90.0 < float(taper_angle) < 90.0:
            raise ValueError("taper_angle must be strictly between -90 and 90 degrees")

        def _sync() -> dict[str, Any]:
            doc = self._doc()
            region = self._region_from_profile(doc, profile_handle)
            failed = False
            try:
                solid = doc.ModelSpace.AddExtrudedSolid(
                    region, float(height), math.radians(float(taper_angle))
                )
                return self._solid_info(solid)
            except Exception:
                failed = True
                raise
            finally:
                try:
                    region.Delete()
                except Exception:
                    if not failed:
                        raise

        return await self._run(_sync)

    async def solid_sweep(self, profile_handle: str, path_handle: str) -> dict[str, Any]:
        profile_key = str(profile_handle or "").strip().upper()
        path_key = str(path_handle or "").strip().upper()
        if not profile_key or not path_key:
            raise ValueError("profile_handle and path_handle must not be empty")
        if profile_key == path_key:
            raise ValueError("profile and sweep path must be different objects")

        def _sync() -> dict[str, Any]:
            doc = self._doc()
            region = self._region_from_profile(doc, profile_key)
            failed = False
            try:
                try:
                    path = doc.HandleToObject(path_key)
                except Exception as exc:
                    raise KeyError(f"path not found: {path_handle}") from exc
                path_type = str(getattr(path, "ObjectName", ""))
                allowed_path_types = {
                    "AcDbArc",
                    "AcDbCircle",
                    "AcDbEllipse",
                    "AcDbPolyline",
                    "AcDb2dPolyline",
                    "AcDb3dPolyline",
                    "AcDbSpline",
                }
                if path_type not in allowed_path_types:
                    raise ValueError(
                        "sweep path must be an Arc, Circle, Ellipse, Polyline or Spline"
                    )
                solid = doc.ModelSpace.AddExtrudedSolidAlongPath(region, path)
                return self._solid_info(solid)
            except Exception:
                failed = True
                raise
            finally:
                try:
                    region.Delete()
                except Exception:
                    if not failed:
                        raise

        return await self._run(_sync)

    async def solid_revolve(
        self,
        profile_handle: str,
        axis_x1: float,
        axis_y1: float,
        axis_z1: float,
        axis_x2: float,
        axis_y2: float,
        axis_z2: float,
        angle_deg: float = 360.0,
    ) -> dict[str, Any]:
        axis = (
            float(axis_x2) - float(axis_x1),
            float(axis_y2) - float(axis_y1),
            float(axis_z2) - float(axis_z1),
        )
        if math.sqrt(sum(value * value for value in axis)) <= 0:
            raise ValueError("revolve axis points must be distinct")
        if angle_deg == 0 or abs(float(angle_deg)) > 360.0:
            raise ValueError("revolve angle must be non-zero and within ±360 degrees")

        def _sync() -> dict[str, Any]:
            doc = self._doc()
            region = self._region_from_profile(doc, profile_handle)
            failed = False
            try:
                solid = doc.ModelSpace.AddRevolvedSolid(
                    region,
                    _point(axis_x1, axis_y1, axis_z1),
                    _point(*axis),
                    math.radians(float(angle_deg)),
                )
                return self._solid_info(solid)
            except Exception:
                failed = True
                raise
            finally:
                try:
                    region.Delete()
                except Exception:
                    if not failed:
                        raise

        return await self._run(_sync)

    async def solid_boolean(
        self,
        target_handle: str,
        tool_handle: str,
        operation: str,
    ) -> dict[str, Any]:
        target_key = str(target_handle or "").strip().upper()
        tool_key = str(tool_handle or "").strip().upper()
        if not target_key or not tool_key:
            raise ValueError("target_handle and tool_handle must not be empty")
        if target_key == tool_key:
            raise ValueError("Boolean target and tool must be different solids")
        normalized = str(operation or "").strip().lower()
        operation_codes = {
            "union": 0,
            "intersect": 1,
            "intersection": 1,
            "subtract": 2,
            "subtraction": 2,
        }
        if normalized not in operation_codes:
            raise ValueError("operation must be one of: union, subtract, intersect")
        canonical = {
            "intersection": "intersect",
            "subtraction": "subtract",
        }.get(normalized, normalized)

        def _sync() -> dict[str, Any]:
            doc = self._doc()
            target = self._solid_by_id(target_key)
            tool = self._solid_by_id(tool_key)
            target.Boolean(operation_codes[normalized], tool)
            result = self._solid_info(target)
            try:
                doc.HandleToObject(tool_key)
                tool_exists_after = True
            except Exception:
                tool_exists_after = False
            result.update(
                {
                    "operation": canonical,
                    "tool_handle": tool_key,
                    "tool_exists_after": tool_exists_after,
                }
            )
            return result

        return await self._run(_sync)

    async def solid_move(
        self, handle: str, dx: float, dy: float, dz: float
    ) -> dict[str, Any]:
        def _sync() -> dict[str, Any]:
            solid = self._solid_by_id(handle)
            solid.Move(_point(0, 0, 0), _point(dx, dy, dz))
            return self._solid_info(solid)

        return await self._run(_sync)

    async def solid_rotate3d(
        self,
        handle: str,
        axis_x1: float,
        axis_y1: float,
        axis_z1: float,
        axis_x2: float,
        axis_y2: float,
        axis_z2: float,
        angle_deg: float,
    ) -> dict[str, Any]:
        axis_length = math.sqrt(
            (float(axis_x2) - float(axis_x1)) ** 2
            + (float(axis_y2) - float(axis_y1)) ** 2
            + (float(axis_z2) - float(axis_z1)) ** 2
        )
        if axis_length <= 0:
            raise ValueError("Rotate3D axis points must be distinct")

        def _sync() -> dict[str, Any]:
            solid = self._solid_by_id(handle)
            solid.Rotate3D(
                _point(axis_x1, axis_y1, axis_z1),
                _point(axis_x2, axis_y2, axis_z2),
                math.radians(float(angle_deg)),
            )
            return self._solid_info(solid)

        return await self._run(_sync)

    async def solid_scale3d(
        self,
        handle: str,
        base_x: float,
        base_y: float,
        base_z: float,
        factor: float,
    ) -> dict[str, Any]:
        if factor <= 0:
            raise ValueError("scale factor must be > 0")

        def _sync() -> dict[str, Any]:
            solid = self._solid_by_id(handle)
            solid.ScaleEntity(_point(base_x, base_y, base_z), float(factor))
            return self._solid_info(solid)

        return await self._run(_sync)

    async def solid_mirror3d(
        self,
        handle: str,
        x1: float,
        y1: float,
        z1: float,
        x2: float,
        y2: float,
        z2: float,
        x3: float,
        y3: float,
        z3: float,
    ) -> dict[str, Any]:
        p1 = (float(x1), float(y1), float(z1))
        p2 = (float(x2), float(y2), float(z2))
        p3 = (float(x3), float(y3), float(z3))
        v1 = tuple(p2[index] - p1[index] for index in range(3))
        v2 = tuple(p3[index] - p1[index] for index in range(3))
        cross = (
            v1[1] * v2[2] - v1[2] * v2[1],
            v1[2] * v2[0] - v1[0] * v2[2],
            v1[0] * v2[1] - v1[1] * v2[0],
        )
        norm1 = math.sqrt(sum(value * value for value in v1))
        norm2 = math.sqrt(sum(value * value for value in v2))
        cross_norm = math.sqrt(sum(value * value for value in cross))
        if norm1 <= 0 or norm2 <= 0 or cross_norm <= 1e-12 * norm1 * norm2:
            raise ValueError("Mirror3D plane points must be distinct and non-collinear")

        def _sync() -> dict[str, Any]:
            solid = self._solid_by_id(handle)
            mirrored = solid.Mirror3D(_point(*p1), _point(*p2), _point(*p3))
            if str(getattr(mirrored, "ObjectName", "")) != "AcDb3dSolid":
                raise RuntimeError("Mirror3D did not return a 3DSOLID")
            result = self._solid_info(mirrored)
            result["source_handle"] = str(handle)
            return result

        return await self._run(_sync)

    async def solid_inspect(self, handle: str) -> dict[str, Any]:
        return await self._run(lambda: self._solid_info(self._solid_by_id(handle)))

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
