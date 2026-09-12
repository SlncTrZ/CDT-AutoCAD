"""Live AutoCAD backend using the Windows ActiveX/COM automation API.
Wing: code | Topic: autocad-a3-analysis | Updated: 2026-09-09 22:58

This backend intentionally implements only the existing A0/A1 provider contract. It does not
copy the much larger reference server surface. All COM work is serialized through one STA worker
thread, while attach/start policy and filesystem boundaries stay owned by CDT_Engineer.
"""

from __future__ import annotations

import asyncio
import ctypes
import hashlib
import io
import json
import math
import os
import re
import shutil
import sys
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any, TypeVar

from ..command_presets import VIEW_PRESETS as _VIEW_PRESETS
from ..command_presets import visual_style_command
from ..config import Settings
from ..errors import (
    BackendQuarantinedError,
    BackendTimeoutError,
    StateConflictError,
    UnsupportedCapabilityError,
)
from ..models import BlockInfo, Capability, EntityInfo, LayerInfo
from ..security import (
    resolve_allowed_directory,
    resolve_autocad_document_path,
    resolve_autocad_export_path,
    resolve_pdf_path,
)
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


_INSERTION_UNIT_CODES = {
    "unitless": 0,
    "inches": 1,
    "feet": 2,
    "miles": 3,
    "millimeters": 4,
    "millimetres": 4,
    "mm": 4,
    "centimeters": 5,
    "centimetres": 5,
    "cm": 5,
    "meters": 6,
    "metres": 6,
    "m": 6,
    "kilometers": 7,
    "kilometres": 7,
    "km": 7,
}
_INSERTION_UNIT_NAMES = {
    0: "unitless", 1: "inches", 2: "feet", 3: "miles", 4: "millimeters",
    5: "centimeters", 6: "meters", 7: "kilometers",
}
_MEASUREMENT_CODES = {"english": 0, "metric": 1}
_MEASUREMENT_NAMES = {0: "english", 1: "metric"}
_LINEAR_FORMAT_CODES = {
    "scientific": 1, "decimal": 2, "engineering": 3, "architectural": 4, "fractional": 5,
}
_LINEAR_FORMAT_NAMES = {value: key for key, value in _LINEAR_FORMAT_CODES.items()}
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
        return _com_get_attr(obj, name)
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
    name = str(_com_get_attr(entity, "ObjectName"))
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
        return float(_com_get_attr(entity, name))
    except Exception as exc:
        raise RuntimeError(f"AutoCAD property unavailable: {name}") from exc


def _com_vector_property(entity: Any, name: str) -> list[float]:
    try:
        return _xyz(_com_get_attr(entity, name))
    except Exception as exc:
        raise RuntimeError(f"AutoCAD property unavailable: {name}") from exc


def _com_bounding_box(entity: Any) -> dict[str, list[float]]:
    entity = _entity_property_view(entity)
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
            "start": _xyz(_com_get_attr(entity, "StartPoint")),
            "end": _xyz(_com_get_attr(entity, "EndPoint")),
            "coordinate_frame": "wcs",
        }
    elif entity_type == "CIRCLE":
        properties = {
            "center": _xyz(_com_get_attr(entity, "Center")),
            "radius": float(_com_get_attr(entity, "Radius")),
            "coordinate_frame": "wcs",
        }
    elif entity_type == "ARC":
        properties = {
            "center": _xyz(_com_get_attr(entity, "Center")),
            "radius": float(_com_get_attr(entity, "Radius")),
            "start_angle": math.degrees(float(_com_get_attr(entity, "StartAngle"))),
            "end_angle": math.degrees(float(_com_get_attr(entity, "EndAngle"))),
            "coordinate_frame": "wcs",
        }
    elif entity_type in {"LWPOLYLINE", "POLYLINE"}:
        coords = list(_com_get_attr(entity, "Coordinates"))
        properties = {
            "points": [
                [float(coords[index]), float(coords[index + 1])]
                for index in range(0, len(coords) - 1, 2)
            ],
            "closed": bool(_com_get_attr(entity, "Closed")),
            "coordinate_frame": "ocs" if entity_type == "LWPOLYLINE" else "wcs",
        }
    elif entity_type == "TEXT":
        properties = {
            "text": str(_com_get_attr(entity, "TextString")),
            "insert": _xyz(_com_get_attr(entity, "InsertionPoint")),
            "height": float(_com_get_attr(entity, "Height")),
            "rotation": math.degrees(float(_com_get_attr(entity, "Rotation"))),
            "coordinate_frame": "wcs",
        }
    elif entity_type == "INSERT":
        properties = {
            "block_name": str(_com_get_attr(entity, "Name")),
            "insert": _xyz(_com_get_attr(entity, "InsertionPoint")),
            "x_scale": float(_com_get_attr(entity, "XScaleFactor")),
            "y_scale": float(_com_get_attr(entity, "YScaleFactor")),
            "rotation": math.degrees(float(_com_get_attr(entity, "Rotation"))),
        }
    elif entity_type == "HATCH":
        properties = {"pattern_name": str(_com_get_attr(entity, "PatternName"))}
    elif entity_type == "DIMENSION":
        text_override = _optional_com_property(entity, "TextOverride")
        properties["text"] = str(text_override or "<>")

    linetype_value = _optional_com_property(entity, "Linetype")
    visible_value = _optional_com_property(entity, "Visible")
    return EntityInfo(
        id=str(_com_get_attr(entity, "Handle")),
        type=entity_type,
        layer=str(_com_get_attr(entity, "Layer")),
        color=int(_com_get_attr(entity, "Color")),
        linetype=str(linetype_value) if linetype_value is not None else "ByLayer",
        visible=bool(visible_value) if visible_value is not None else True,
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
        self._uncertain_future: Future[Any] | None = None
        self._integrity_uncertain_reason: str | None = None
        self._mutation_gate = asyncio.Lock()
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
            "common.document.units": supported("typed_sysvars"),
            "common.object.query": supported(),
            "common.object.modify": supported(),
            "common.organization.layers": supported(),
            "common.organization.layer_state": supported(),
            "common.transaction.rollback": supported("autocad_undo_mark"),
            "common.transaction.undo": supported("autocad_native"),
            "autocad.dxf.read": supported(),
            "autocad.dxf.write": supported(),
            "autocad.dwg.read": supported(),
            "autocad.dwg.write": supported(),
            "autocad.blocks": supported(),
            "autocad.references.xref": supported("activex_typed"),
            "autocad.artifact.seal": supported("saved_copy_sha256"),
            "autocad.layouts": supported(),
            "autocad.dimensions.linear": supported(),
            "autocad.dimensions.aligned": supported(),
            "autocad.dimensions.advanced": supported("activex_typed"),
            "autocad.analysis.measurement": supported("activex_typed"),
            "autocad.analysis.intersections": supported("activex_typed"),
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
            "autocad.view.3d": supported("activex_live_view"),
            "autocad.view.visual_style": supported("bounded_vscurrent_preset"),
            "autocad.geometry.3d_polyline": supported("activex_typed"),
            "autocad.geometry.lwpolyline.curves": supported("bulge_width_elevation"),
            "autocad.solid.acis": supported("activex_acis"),
            "autocad.solid.export.sat": supported("activex_export"),
            "autocad.solid.edge_fillet": Capability(False, reason="no_deterministic_activex_subentity_api"),
            "autocad.solid.edge_chamfer": Capability(False, reason="no_deterministic_activex_subentity_api"),
            "autocad.solid.shell": Capability(False, reason="no_deterministic_activex_shell_api"),
            "autocad.solid.loft": Capability(False, reason="activex_no_typed_loft_api"),
        }
        return {key: value.to_dict() for key, value in capabilities.items()}

    def status(self) -> dict[str, Any]:
        application = (
            dict(self._application_metadata) if self._application_metadata is not None else None
        )
        current_release_match = (
            None
            if application is None
            else str(application.get("release") or "") == _PRIMARY_CERTIFICATION_RELEASE
        )
        return {
            "backend": self.name,
            "runtime_available": self.runtime_available,
            "connected": self._connected,
            "cad_progid": self.settings.com_progid,
            "attach_policy": self.settings.com_attach_policy,
            "transaction_depth": self._transaction_depth,
            "timeout_uncertain": self._timeout_uncertain,
            "uncertain_call_running": bool(
                self._uncertain_future is not None and not self._uncertain_future.done()
            ),
            "integrity_uncertain": self._integrity_uncertain_reason is not None,
            "integrity_uncertain_reason": self._integrity_uncertain_reason,
            "a2_implementation_state": "release_candidate",
            "a2_live_verification": "historical_primary_target_pass_current_process_unverified",
            "live_certification": {
                "state": "historical_primary_target_pass",
                "current_process_certified": False,
                "current_release_match": current_release_match,
                "evidence_reference": "docs/LIVE_ACCEPTANCE.md",
                "primary_release": _PRIMARY_CERTIFICATION_RELEASE,
                "primary_com_version": _PRIMARY_CERTIFICATION_COM_VERSION,
                "primary_progid": _PRIMARY_CERTIFICATION_PROGID,
                "required_edition": "full",
                "required_platform": "windows_x64",
                "compatibility_policy": "explicit_native_matrix_required",
            },
            "application": application,
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

    def _latch_uncertain_completion(self, future: Future[Any]) -> None:
        self._timeout_uncertain = True
        self._uncertain_future = future
        self._connected = False
        self._application_metadata = None
        self._created_viewport_handles.clear()

    def _quarantine_integrity(self, reason: str) -> None:
        self._integrity_uncertain_reason = str(reason)
        self._connected = False
        self._application_metadata = None
        self._created_viewport_handles.clear()

    async def _run(
        self,
        func,
        *,
        may_mutate_document: bool = True,
    ) -> _T:
        if may_mutate_document:
            async with self._mutation_gate:
                return await self._run_after_mutation_gate(
                    func,
                    may_mutate_document=True,
                )
        return await self._run_after_mutation_gate(
            func,
            may_mutate_document=False,
        )

    async def _run_after_mutation_gate(
        self,
        func,
        *,
        may_mutate_document: bool,
    ) -> _T:
        executor = self._executor
        if executor is None:
            reason = self._runtime_reason() or "executor_unavailable"
            raise RuntimeError(f"AutoCAD COM backend unavailable: {reason}")

        if self._integrity_uncertain_reason is not None and may_mutate_document:
            raise BackendQuarantinedError(
                "AutoCAD COM backend is quarantined after an integrity verification failure: "
                f"{self._integrity_uncertain_reason}. Read-only verification remains available; "
                "restart the provider before any later mutation."
            )
        if self._timeout_uncertain and may_mutate_document:
            raise BackendQuarantinedError(
                "AutoCAD COM backend is quarantined after an unknown mutation completion; "
                "verify the live drawing with read-only calls and restart the provider before "
                "mutating again"
            )

        loop = asyncio.get_running_loop()
        concurrent_future = executor.submit(func)
        future = asyncio.wrap_future(concurrent_future, loop=loop)
        deadline = self.settings.com_call_timeout_seconds
        try:
            done, _ = await asyncio.wait({future}, timeout=deadline)
            if future not in done:
                if may_mutate_document:
                    self._latch_uncertain_completion(concurrent_future)
                    message = (
                        f"AutoCAD COM mutation exceeded {deadline:g}s after dispatch. The call may "
                        "still complete; later mutations are quarantined until provider restart so "
                        "a blind retry cannot double-apply the mutation."
                    )
                else:
                    message = f"AutoCAD COM read exceeded {deadline:g}s"
                raise BackendTimeoutError(
                    message,
                    retryable=not may_mutate_document,
                    completion_unknown=may_mutate_document,
                )
            return future.result()
        except asyncio.CancelledError:
            if may_mutate_document:
                self._latch_uncertain_completion(concurrent_future)
            raise
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
            solid = _entity_property_view(self._doc().HandleToObject(key))
        except Exception as exc:
            raise KeyError(f"solid not found: {handle}") from exc
        if _object_type(solid) != "3DSOLID":
            raise ValueError(f"handle {key} is {_object_type(solid)}, not 3DSOLID")
        return solid

    @staticmethod
    def _solid_info(solid: Any) -> dict[str, Any]:
        solid = _entity_property_view(solid)
        raw_solid_type = _optional_com_property(solid, "SolidType")
        return {
            "ok": True,
            "handle": str(_com_get_attr(solid, "Handle")),
            "type": "3DSOLID",
            "layer": str(_com_get_attr(solid, "Layer")),
            "visible": bool(_com_get_attr(solid, "Visible")),
            "solid_type": str(raw_solid_type) if raw_solid_type is not None else None,
            "volume": _com_float_property(solid, "Volume"),
            "centroid": _com_vector_property(solid, "Centroid"),
            "bounding_box": _com_bounding_box(solid),
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
            doc = _com_call_with_busy_retry(documents.Add)
            name = str(_com_property_with_busy_retry(doc, "Name"))
            return {"ok": True, "name": name, "backend": self.name}

        result = await self._run(_sync)
        self._transaction_depth = 0
        self._timeout_uncertain = False
        self._uncertain_future = None
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
        self._uncertain_future = None
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

        return await self._run(_sync, may_mutate_document=False)

    async def document_save(self, path: str | None = None) -> dict[str, Any]:
        if path is not None:
            return await self.document_save_as(path)

        def _sync() -> dict[str, Any]:
            doc = self._doc()
            raw_path = str(_optional_com_property(doc, "FullName") or "")
            if not raw_path or not Path(raw_path).is_absolute():
                raise StateConflictError(
                    "Unsaved live AutoCAD document requires an explicit save path"
                )
            target = resolve_autocad_document_path(
                raw_path, self.settings, must_exist=False, for_write=True
            )

            doc.Save()

            verified_raw_path = str(_optional_com_property(doc, "FullName") or "")
            if not verified_raw_path or not Path(verified_raw_path).is_absolute():
                raise StateConflictError("AutoCAD document path changed during save")
            verified_target = resolve_autocad_document_path(
                verified_raw_path,
                self.settings,
                must_exist=False,
                for_write=True,
            )
            if verified_target != target:
                raise StateConflictError("AutoCAD document path changed during save")

            name = str(_com_property_with_busy_retry(doc, "Name"))
            self._document_scope_key = (name, str(verified_target))
            return {
                "ok": True,
                "path": str(verified_target),
                "format": verified_target.suffix.lower().lstrip("."),
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

    async def document_configure_units(
        self,
        insertion_units: str | None = None,
        measurement: str | None = None,
        linear_format: str | None = None,
        linear_precision: int | None = None,
    ) -> dict[str, Any]:
        unit_code = None
        if insertion_units is not None:
            normalized = str(insertion_units).strip().lower()
            if normalized not in _INSERTION_UNIT_CODES:
                raise ValueError(f"unsupported insertion_units: {insertion_units}")
            unit_code = _INSERTION_UNIT_CODES[normalized]
        measurement_code = None
        if measurement is not None:
            normalized = str(measurement).strip().lower()
            if normalized not in _MEASUREMENT_CODES:
                raise ValueError("measurement must be 'metric' or 'english'")
            measurement_code = _MEASUREMENT_CODES[normalized]
        format_code = None
        if linear_format is not None:
            normalized = str(linear_format).strip().lower()
            if normalized not in _LINEAR_FORMAT_CODES:
                raise ValueError(f"unsupported linear_format: {linear_format}")
            format_code = _LINEAR_FORMAT_CODES[normalized]
        if linear_precision is not None and not 0 <= int(linear_precision) <= 8:
            raise ValueError("linear_precision must be between 0 and 8")

        desired = {
            name: value
            for name, value in (
                ("INSUNITS", unit_code),
                ("MEASUREMENT", measurement_code),
                ("LUNITS", format_code),
                ("LUPREC", int(linear_precision) if linear_precision is not None else None),
            )
            if value is not None
        }
        unit_variables = ("INSUNITS", "MEASUREMENT", "LUNITS", "LUPREC")

        def _read_units(doc: Any) -> dict[str, int]:
            return {name: int(doc.GetVariable(name)) for name in unit_variables}

        def _sync() -> dict[str, Any]:
            doc = self._doc()
            predecessor = _read_units(doc)
            try:
                for name, value in desired.items():
                    doc.SetVariable(name, value)
                raw = _read_units(doc)
                mismatch = {
                    name: {"expected": value, "actual": raw[name]}
                    for name, value in desired.items()
                    if raw[name] != value
                }
                if mismatch:
                    raise StateConflictError(f"units read-back mismatch: {mismatch}")
            except Exception as mutation_error:
                restore_errors: list[str] = []
                for name, value in predecessor.items():
                    try:
                        doc.SetVariable(name, value)
                    except Exception as restore_error:
                        restore_errors.append(f"{name}: {restore_error}")
                try:
                    restored = _read_units(doc)
                except Exception as restore_read_error:
                    restored = None
                    restore_errors.append(f"read-back: {restore_read_error}")
                if restore_errors or restored != predecessor:
                    reason = (
                        "document_configure_units restore verification failed; "
                        f"errors={restore_errors}; predecessor={predecessor}; restored={restored}"
                    )
                    self._quarantine_integrity(reason)
                    raise StateConflictError(reason) from mutation_error
                raise
            return {
                "insertion_units": _INSERTION_UNIT_NAMES.get(raw["INSUNITS"], f"code_{raw['INSUNITS']}"),
                "measurement": _MEASUREMENT_NAMES.get(raw["MEASUREMENT"], f"code_{raw['MEASUREMENT']}"),
                "linear_format": _LINEAR_FORMAT_NAMES.get(raw["LUNITS"], f"code_{raw['LUNITS']}"),
                "linear_precision": raw["LUPREC"],
                "raw": raw,
            }

        return await self._run(_sync, may_mutate_document=bool(desired))

    async def document_dependencies(self) -> dict[str, Any]:
        rows = await self.xref_list()
        return {
            "ok": True,
            "scope": "dwg_xrefs_only",
            "xrefs": rows,
            "resolved": sum(1 for row in rows if row.get("resolved")),
            "unresolved": sum(1 for row in rows if not row.get("resolved")),
            "complete": all(bool(row.get("resolved")) for row in rows),
        }

    async def artifact_seal(self, destination_dir: str | None = None) -> dict[str, Any]:
        def _save_and_resolve() -> tuple[Path, dict[str, Any]]:
            doc = self._doc()
            raw_path = str(_optional_com_property(doc, "FullName") or "")
            if not raw_path or not Path(raw_path).is_absolute():
                raise StateConflictError("artifact sealing requires a saved AutoCAD document")
            source = resolve_autocad_document_path(raw_path, self.settings, must_exist=True)
            doc.Save()
            saved = bool(_optional_com_property(doc, "Saved"))
            if not saved:
                raise StateConflictError("AutoCAD still reports the document as dirty after Save")
            units_raw: dict[str, int] | None = {}
            for variable in ("INSUNITS", "MEASUREMENT", "LUNITS", "LUPREC"):
                try:
                    units_raw[variable] = int(
                        _com_call_with_busy_retry(lambda variable=variable: doc.GetVariable(variable))
                    )
                except Exception:
                    units_raw = None
                    break
            artifact_state = self._document_artifact_state(doc)
            return source, {
                "saved": artifact_state["saved"],
                "dbmod": artifact_state["dbmod"],
                "units_raw": units_raw,
            }

        source, document_state = await self._run(_save_and_resolve)
        destination = (
            resolve_allowed_directory(destination_dir, self.settings)
            if destination_dir is not None
            else source.parent / ".cdt-accepted"
        )
        if destination_dir is None:
            destination.mkdir(parents=True, exist_ok=True)
            destination = resolve_allowed_directory(str(destination), self.settings)
        source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
        sealed_name = f"{source.stem}.{source_hash[:16]}{source.suffix.lower()}"
        sealed = destination / sealed_name
        if sealed.exists():
            existing_hash = hashlib.sha256(sealed.read_bytes()).hexdigest()
            if existing_hash != source_hash:
                raise StateConflictError("existing sealed artifact has the same name but different bytes")
        else:
            shutil.copy2(source, sealed)
        sealed_hash = hashlib.sha256(sealed.read_bytes()).hexdigest()
        if sealed_hash != source_hash:
            raise StateConflictError("sealed artifact hash does not match saved source")
        post_copy_source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
        if post_copy_source_hash != source_hash:
            raise StateConflictError("source changed while sealing; refusing a stale acceptance manifest")
        manifest = {
            "schema_version": 1,
            "status": "SEALED",
            "source_path": str(source),
            "sealed_path": str(sealed),
            "sha256": sealed_hash,
            "size": sealed.stat().st_size,
            "source_sha256": post_copy_source_hash,
            "source_size": source.stat().st_size,
            "source_mtime_ns": source.stat().st_mtime_ns,
            "document_state": document_state,
        }
        manifest_path = sealed.with_suffix(sealed.suffix + ".manifest.json")
        manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        return {**manifest, "manifest_path": str(manifest_path)}

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

        return await self._run(_sync, may_mutate_document=False)

    async def object_get(self, object_id: str) -> EntityInfo:
        return await self._run(
            lambda: _entity_info(self._entity_by_id(object_id)),
            may_mutate_document=False,
        )

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

        return await self._run(_sync, may_mutate_document=False)

    async def object_measure(self, object_id: str) -> dict[str, Any]:
        def _sync() -> dict[str, Any]:
            entity = _entity_property_view(self._entity_by_id(object_id))
            entity_type = _object_type(entity)
            result: dict[str, Any] = {
                "object_id": str(_com_get_attr(entity, "Handle")),
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

        return await self._run(_sync, may_mutate_document=False)

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

        return await self._run(_sync, may_mutate_document=False)

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

        return await self._run(_sync, may_mutate_document=False)

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
        bulges: list[float] | None = None,
        widths: list[list[float]] | None = None,
        elevation: float = 0.0,
    ) -> EntityInfo:
        if len(points) < 2 or any(len(point) < 2 for point in points):
            raise ValueError("polyline requires at least two [x, y] points")
        if not math.isfinite(float(elevation)):
            raise ValueError("elevation must be finite")
        vertex_count = len(points)
        normalized_bulges = [0.0] * vertex_count if bulges is None else [float(v) for v in bulges]
        if len(normalized_bulges) != vertex_count or not all(math.isfinite(v) for v in normalized_bulges):
            raise ValueError("bulges must contain one finite value per polyline vertex")
        normalized_widths = ([[0.0, 0.0] for _ in range(vertex_count)] if widths is None else widths)
        if len(normalized_widths) != vertex_count or any(len(pair) != 2 for pair in normalized_widths):
            raise ValueError("widths must contain [start_width, end_width] for every vertex")
        width_pairs = [(float(pair[0]), float(pair[1])) for pair in normalized_widths]
        if any((not math.isfinite(a) or not math.isfinite(b) or a < 0 or b < 0) for a, b in width_pairs):
            raise ValueError("polyline widths must be finite values >= 0")
        normalized_color = self._validate_color(color)
        flat = [float(value) for point in points for value in point[:2]]
        if not all(math.isfinite(value) for value in flat):
            raise ValueError("polyline coordinates must be finite")

        def _sync() -> EntityInfo:
            canonical_layer = self._validate_layer(layer)
            entity = self._space().AddLightWeightPolyline(_double_array(flat))
            entity.Closed = bool(closed)
            entity.Elevation = float(elevation)
            for index, bulge in enumerate(normalized_bulges):
                entity.SetBulge(index, bulge)
            for index, (start_width, end_width) in enumerate(width_pairs):
                entity.SetWidth(index, start_width, end_width)
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

        return await self._run(_sync, may_mutate_document=False)

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
        wanted = str(name).strip()
        if not wanted:
            raise ValueError("layer name must not be empty")
        normalized_color = self._validate_color(color) if color is not None else None
        if lineweight is not None and not -3 <= int(lineweight) <= 211:
            raise ValueError("lineweight must be between -3 and 211")
        requested_linetype = None
        if linetype is not None:
            requested_linetype = str(linetype).strip()
            if not requested_linetype:
                raise ValueError("linetype must not be empty")
        may_mutate = any(
            value is not None
            for value in (is_on, is_frozen, is_locked, color, linetype, lineweight)
        )
        layer_fields = ("LayerOn", "Freeze", "Lock", "Color", "Linetype", "LineWeight")

        def _read_layer_state(layer: Any) -> dict[str, Any]:
            return {field: _com_get_attr(layer, field) for field in layer_fields}

        def _sync() -> LayerInfo:
            doc = self._doc()
            layers = _com_get_attr(doc, "Layers")
            canonical = self._find_name(layers, wanted)
            if canonical is None:
                raise ValueError(f"layer does not exist: {wanted}")
            current_layer = _com_get_attr(doc, "ActiveLayer")
            current = str(_com_get_attr(current_layer, "Name"))
            if canonical.lower() == current.lower():
                if is_frozen is True:
                    raise ValueError("current layer cannot be frozen")
                if is_on is False:
                    raise ValueError("current layer cannot be turned off")
            if may_mutate and "|" in canonical:
                raise ValueError("XREF-dependent layer cannot be modified")

            canonical_linetype = None
            if requested_linetype is not None:
                linetypes = _com_get_attr(doc, "Linetypes")
                canonical_linetype = self._find_name(linetypes, requested_linetype)
                if canonical_linetype is None:
                    raise ValueError(f"linetype does not exist: {requested_linetype}")

            layer = layers.Item(canonical)
            desired = {
                field: value
                for field, value in (
                    ("LayerOn", bool(is_on) if is_on is not None else None),
                    ("Freeze", bool(is_frozen) if is_frozen is not None else None),
                    ("Lock", bool(is_locked) if is_locked is not None else None),
                    ("Color", normalized_color),
                    ("Linetype", canonical_linetype),
                    ("LineWeight", int(lineweight) if lineweight is not None else None),
                )
                if value is not None
            }
            predecessor = _read_layer_state(layer)
            try:
                for field, value in desired.items():
                    _com_set_attr(layer, field, value)
                readback = _read_layer_state(layer)
                mismatch = {
                    field: {"expected": value, "actual": readback[field]}
                    for field, value in desired.items()
                    if readback[field] != value
                }
                if mismatch:
                    raise StateConflictError(f"layer state read-back mismatch: {mismatch}")
                return _layer_info(layer, current)
            except Exception as mutation_error:
                restore_errors: list[str] = []
                for field, value in predecessor.items():
                    try:
                        _com_set_attr(layer, field, value)
                    except Exception as restore_error:
                        restore_errors.append(f"{field}: {restore_error}")
                try:
                    restored = _read_layer_state(layer)
                except Exception as restore_read_error:
                    restored = None
                    restore_errors.append(f"read-back: {restore_read_error}")
                if restore_errors or restored != predecessor:
                    reason = (
                        "layer_update_state restore verification failed; "
                        f"errors={restore_errors}; predecessor={predecessor}; restored={restored}"
                    )
                    self._quarantine_integrity(reason)
                    raise StateConflictError(reason) from mutation_error
                raise

        return await self._run(_sync, may_mutate_document=may_mutate)

    async def block_list(
        self,
        include_xref_dependent: bool = False,
        include_xrefs: bool = True,
        name_filter: str | None = None,
        limit: int = 200,
        offset: int = 0,
    ) -> list[BlockInfo]:
        if not 1 <= int(limit) <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        if int(offset) < 0:
            raise ValueError("offset must be >= 0")
        wanted_filter = str(name_filter).strip().lower() if name_filter else None

        def _sync() -> list[BlockInfo]:
            doc = self._doc()
            blocks = _com_get_attr(doc, "Blocks")
            filtered: list[BlockInfo] = []
            for index in range(int(_com_get_attr(blocks, "Count"))):
                block = _com_call_with_busy_retry(lambda index=index: blocks.Item(index))
                name = str(_com_get_attr(block, "Name"))
                if name.startswith("*"):
                    continue
                dependent = "|" in name
                is_xref = bool(_com_get_attr(block, "IsXRef"))
                if dependent and not include_xref_dependent:
                    continue
                if is_xref and not include_xrefs:
                    continue
                if wanted_filter and wanted_filter not in name.lower():
                    continue
                origin = _xyz(_com_get_attr(block, "Origin"))
                block_count = int(_com_get_attr(block, "Count"))
                attribute_count = 0
                for item_index in range(block_count):
                    item = _com_call_with_busy_retry(
                        lambda item_index=item_index: block.Item(item_index)
                    )
                    if str(_com_get_attr(item, "ObjectName")) == "AcDbAttributeDefinition":
                        attribute_count += 1
                filtered.append(
                    BlockInfo(
                        name=name,
                        base_point=(origin[0], origin[1], origin[2]),
                        entity_count=block_count,
                        attribute_count=attribute_count,
                        is_xref=is_xref,
                    )
                )
            return filtered[int(offset): int(offset) + int(limit)]

        return await self._run(_sync, may_mutate_document=False)

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

    async def xref_list(self) -> list[dict[str, Any]]:
        def _sync() -> list[dict[str, Any]]:
            doc = self._doc()
            rows: list[dict[str, Any]] = []
            for index in range(int(doc.Blocks.Count)):
                block = doc.Blocks.Item(index)
                if not bool(_optional_com_property(block, "IsXRef")):
                    continue
                name = str(_com_get_attr(block, "Name"))
                raw_path = str(_optional_com_property(block, "Path") or "")
                contained = False
                resolved = False
                safe_path: str | None = None
                if raw_path:
                    try:
                        candidate = resolve_autocad_document_path(
                            raw_path, self.settings, must_exist=False
                        )
                        contained = True
                        resolved = candidate.is_file()
                        safe_path = str(candidate)
                    except (ValueError, FileNotFoundError):
                        contained = False
                        resolved = False
                loaded: bool | None
                load_state_verified = False
                load_state_reason: str | None = None
                try:
                    loaded = not bool(_com_get_attr(block, "IsUnloaded"))
                    load_state_verified = True
                except AttributeError:
                    loaded = None
                    load_state_reason = "activex_is_unloaded_unavailable"
                source_sha256 = None
                source_size = None
                if resolved and safe_path is not None:
                    source_path = Path(safe_path)
                    source_sha256 = hashlib.sha256(source_path.read_bytes()).hexdigest()
                    source_size = source_path.stat().st_size
                rows.append(
                    {
                        "name": name,
                        "path": safe_path,
                        "contained": contained,
                        "resolved": resolved,
                        "loaded": loaded,
                        "load_state_verified": load_state_verified,
                        "load_state_reason": load_state_reason,
                        "sha256": source_sha256,
                        "size": source_size,
                    }
                )
            return rows

        return await self._run(_sync, may_mutate_document=False)

    async def xref_attach(
        self,
        path: str,
        *,
        name: str | None = None,
        overlay: bool = True,
        x: float = 0.0,
        y: float = 0.0,
        z: float = 0.0,
        scale_x: float = 1.0,
        scale_y: float = 1.0,
        scale_z: float = 1.0,
        rotation: float = 0.0,
        layer: str | None = None,
    ) -> dict[str, Any]:
        source = resolve_autocad_document_path(path, self.settings, must_exist=True)
        wanted = str(name).strip() if name is not None else source.stem
        if not wanted or any(char in wanted for char in ("|", "*", "<", ">", "/", "\\", "\"")):
            raise ValueError("xref name contains unsupported characters")
        if any(not math.isfinite(float(value)) for value in (x, y, z, scale_x, scale_y, scale_z, rotation)):
            raise ValueError("xref transform values must be finite")
        if float(scale_x) == 0 or float(scale_y) == 0 or float(scale_z) == 0:
            raise ValueError("xref scales must be non-zero")
        source_sha256 = hashlib.sha256(source.read_bytes()).hexdigest()
        source_size = source.stat().st_size

        def _sync() -> dict[str, Any]:
            doc = self._doc()
            if self._find_name(doc.Blocks, wanted) is not None:
                raise ValueError(f"block/xref name already exists: {wanted}")
            canonical_layer = self._validate_layer(layer)
            reference = self._space().AttachExternalReference(
                str(source),
                wanted,
                _point(x, y, z),
                float(scale_x),
                float(scale_y),
                float(scale_z),
                math.radians(rotation),
                bool(overlay),
            )
            if canonical_layer is not None:
                reference.Layer = canonical_layer
            return {
                "ok": True,
                "name": wanted,
                "path": str(source),
                "overlay": bool(overlay),
                "handle": str(reference.Handle),
                "layer": str(reference.Layer),
                "source_sha256": source_sha256,
                "source_size": source_size,
            }

        return await self._run(_sync)

    def _xref_block_by_name(self, name: str) -> Any:
        doc = self._doc()
        canonical = self._find_name(doc.Blocks, name)
        if canonical is None:
            raise ValueError(f"xref not found: {name}")
        block = doc.Blocks.Item(canonical)
        if not bool(_optional_com_property(block, "IsXRef")):
            raise ValueError(f"block is not an xref: {name}")
        return block

    @staticmethod
    def _xref_is_unloaded(block: Any) -> bool:
        try:
            return bool(_com_get_attr(block, "IsUnloaded"))
        except AttributeError as exc:
            raise UnsupportedCapabilityError(
                "autocad.references.xref.load_state",
                "AutoCAD ActiveX 2027 does not expose verified XREF load-state read-back; "
                "reload/unload is refused rather than returning an unverified success receipt.",
            ) from exc

    async def xref_reload(self, name: str) -> dict[str, Any]:
        def _sync() -> dict[str, Any]:
            block = self._xref_block_by_name(name)
            raw_path = str(_optional_com_property(block, "Path") or "")
            resolve_autocad_document_path(raw_path, self.settings, must_exist=True)
            before_unloaded = self._xref_is_unloaded(block)
            canonical = str(_com_get_attr(block, "Name"))
            if not before_unloaded:
                return {
                    "ok": True,
                    "name": canonical,
                    "state": "loaded",
                    "state_verified": True,
                    "changed": False,
                }
            block.Reload()
            after_unloaded = self._xref_is_unloaded(block)
            if after_unloaded:
                reason = f"XREF reload read-back mismatch for {canonical}: still unloaded"
                self._quarantine_integrity(reason)
                raise StateConflictError(reason)
            return {
                "ok": True,
                "name": canonical,
                "state": "loaded",
                "state_verified": True,
                "changed": True,
            }

        return await self._run(_sync)

    async def xref_unload(self, name: str) -> dict[str, Any]:
        def _sync() -> dict[str, Any]:
            block = self._xref_block_by_name(name)
            before_unloaded = self._xref_is_unloaded(block)
            canonical = str(_com_get_attr(block, "Name"))
            if before_unloaded:
                return {
                    "ok": True,
                    "name": canonical,
                    "state": "unloaded",
                    "state_verified": True,
                    "changed": False,
                }
            block.Unload()
            after_unloaded = self._xref_is_unloaded(block)
            if not after_unloaded:
                reason = f"XREF unload read-back mismatch for {canonical}: still loaded"
                self._quarantine_integrity(reason)
                raise StateConflictError(reason)
            return {
                "ok": True,
                "name": canonical,
                "state": "unloaded",
                "state_verified": True,
                "changed": True,
            }

        return await self._run(_sync)

    async def xref_detach(self, name: str) -> dict[str, Any]:
        def _sync() -> dict[str, Any]:
            doc = self._doc()
            block = self._xref_block_by_name(name)
            canonical = str(_com_get_attr(block, "Name"))
            block.Detach()
            try:
                remaining = self._find_name(_com_get_attr(doc, "Blocks"), canonical)
            except Exception as exc:
                reason = f"XREF detach read-back failed for {canonical}: {exc}"
                self._quarantine_integrity(reason)
                raise StateConflictError(reason) from exc
            if remaining is not None:
                reason = f"XREF detach read-back mismatch for {canonical}: definition still present"
                self._quarantine_integrity(reason)
                raise StateConflictError(reason)
            return {
                "ok": True,
                "name": canonical,
                "state": "detached",
                "state_verified": True,
            }

        return await self._run(_sync)

    async def layout_list(self) -> dict[str, Any]:
        def _sync() -> dict[str, Any]:
            doc = self._doc()
            return {
                "ok": True,
                "layouts": self._collection_names(doc.Layouts),
                "current": str(doc.ActiveLayout.Name),
            }

        return await self._run(_sync, may_mutate_document=False)

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
        block = _com_get_attr(layout, "Block")
        viewports = []
        for index in range(int(_com_get_attr(block, "Count"))):
            entity = _com_call_with_busy_retry(lambda index=index: block.Item(index))
            if str(_com_get_attr(entity, "ObjectName")) == "AcDbViewport":
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
        if str(_com_get_attr(entity, "ObjectName")) != "AcDbViewport":
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
        center = _xyz(_com_get_attr(viewport, "Center"))
        target = _xyz(_com_get_attr(viewport, "Target"))
        height = float(_com_get_attr(viewport, "Height"))
        try:
            scale = float(_com_get_attr(viewport, "CustomScale"))
        except Exception:
            scale = None
        try:
            locked = bool(_com_get_attr(viewport, "DisplayLocked"))
        except Exception:
            locked = None
        return {
            "handle": str(_com_get_attr(viewport, "Handle")),
            "layout": layout_name,
            "center": center[:2],
            "width": float(_com_get_attr(viewport, "Width")),
            "height": height,
            "view_center": target[:2],
            "view_height": height / scale if scale else None,
            "scale": scale,
            "locked": locked,
            "status": int(bool(_com_get_attr(viewport, "ViewportOn"))),
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

            layouts = _com_get_attr(doc, "Layouts")
            active_layout = _com_get_attr(doc, "ActiveLayout")
            previous = str(_com_get_attr(active_layout, "Name"))
            target_layout = _com_call_with_busy_retry(lambda: layouts.Item(canonical))
            if previous != canonical:
                _com_set_attr(doc, "ActiveLayout", target_layout)
            viewport = None
            try:
                paper_space = _com_get_attr(doc, "PaperSpace")
                viewport = _com_call_with_busy_retry(
                    lambda: paper_space.AddPViewport(
                        _point(center_x, center_y), float(width), float(height)
                    )
                )
                _com_call_with_busy_retry(lambda: viewport.Display(True))
                _com_set_attr(viewport, "CustomScale", float(scale))
                _com_set_attr(viewport, "Target", _point(view_center_x, view_center_y))
                return {"ok": True, **self._viewport_row(viewport, canonical, True)}
            except Exception:
                if viewport is not None:
                    try:
                        _com_call_with_busy_retry(lambda: viewport.Delete())
                    except Exception:
                        pass
                raise
            finally:
                if previous != canonical:
                    previous_layout = _com_call_with_busy_retry(lambda: layouts.Item(previous))
                    _com_set_attr(doc, "ActiveLayout", previous_layout)

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

        return await self._run(_sync, may_mutate_document=False)

    async def viewport_set_scale(self, handle: str, scale: float) -> dict[str, Any]:
        if scale <= 0:
            raise ValueError("viewport scale must be > 0")

        def _sync() -> dict[str, Any]:
            viewport, layout_name = self._resolve_viewport(self._doc(), handle)
            _com_set_attr(viewport, "CustomScale", float(scale))
            actual = float(_com_get_attr(viewport, "CustomScale"))
            return {
                "ok": True,
                "handle": str(_com_get_attr(viewport, "Handle")),
                "layout": layout_name,
                "scale": actual,
                "view_height": float(_com_get_attr(viewport, "Height")) / actual,
            }

        return await self._run(_sync)

    async def viewport_lock(self, handle: str, locked: bool = True) -> dict[str, Any]:
        def _sync() -> dict[str, Any]:
            viewport, layout_name = self._resolve_viewport(self._doc(), handle)
            _com_set_attr(viewport, "DisplayLocked", bool(locked))
            return {
                "ok": True,
                "handle": str(_com_get_attr(viewport, "Handle")),
                "layout": layout_name,
                "locked": bool(_com_get_attr(viewport, "DisplayLocked")),
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
            actual = str(_com_get_attr(viewport, "Handle")).upper()
            _com_call_with_busy_retry(lambda: viewport.Delete())
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

    @staticmethod
    def _document_artifact_state(doc: Any) -> dict[str, Any]:
        saved_value = _optional_com_property(doc, "Saved")
        try:
            dbmod_value = int(_com_call_with_busy_retry(lambda: doc.GetVariable("DBMOD")))
        except Exception:
            dbmod_value = None
        return {
            "saved": bool(saved_value) if saved_value is not None else None,
            "dbmod": dbmod_value,
        }

    @staticmethod
    def _artifact_state_receipt(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
        return {
            "saved_before": before.get("saved"),
            "saved_after": after.get("saved"),
            "dbmod_before": before.get("dbmod"),
            "dbmod_after": after.get("dbmod"),
            "dirty_changed": before != after,
        }

    async def view_zoom_extents(self) -> dict[str, Any]:
        def _sync() -> dict[str, Any]:
            app = self._app()
            doc = self._doc()
            before = self._document_artifact_state(doc)
            app.ZoomExtents()
            after = self._document_artifact_state(doc)
            return {
                "ok": True,
                "mode": "extents",
                "artifact_state": self._artifact_state_receipt(before, after),
            }

        return await self._run(_sync, may_mutate_document=False)

    async def view_zoom_window(
        self, x1: float, y1: float, x2: float, y2: float
    ) -> dict[str, Any]:
        low_x, high_x = sorted((float(x1), float(x2)))
        low_y, high_y = sorted((float(y1), float(y2)))
        if low_x == high_x or low_y == high_y:
            raise ValueError("zoom window must have non-zero width and height")

        def _sync() -> dict[str, Any]:
            app = self._app()
            doc = self._doc()
            before = self._document_artifact_state(doc)
            app.ZoomWindow(_point(low_x, low_y), _point(high_x, high_y))
            after = self._document_artifact_state(doc)
            return {
                "ok": True,
                "mode": "window",
                "min": [low_x, low_y],
                "max": [high_x, high_y],
                "artifact_state": self._artifact_state_receipt(before, after),
            }

        return await self._run(_sync, may_mutate_document=False)

    async def view_screenshot(self) -> bytes:
        if not _PIL_OK:
            raise UnsupportedCapabilityError(
                "autocad.viewport.capture",
                "Live screenshot capture requires the COM optional dependency Pillow.",
            )

        def _sync() -> bytes:
            app = self._app()
            doc = self._doc()
            before = self._document_artifact_state(doc)
            try:
                hwnd = int(app.HWND)
            except Exception as exc:
                raise RuntimeError("AutoCAD did not expose a valid main-window HWND") from exc
            png = _capture_window_png(hwnd)
            if not png.startswith(b"\x89PNG\r\n\x1a\n"):
                raise RuntimeError("AutoCAD screenshot capture did not produce a PNG")
            after = self._document_artifact_state(doc)
            if before != after:
                reason = (
                    "screenshot changed document artifact state; "
                    f"before={before}; after={after}"
                )
                self._quarantine_integrity(reason)
                raise StateConflictError(reason)
            return png

        return await self._run(_sync, may_mutate_document=False)

    async def view_set_direction(self, dx: float, dy: float, dz: float) -> dict[str, Any]:
        length = math.sqrt(float(dx) ** 2 + float(dy) ** 2 + float(dz) ** 2)
        if length <= 0:
            raise ValueError("3D view direction must be a non-zero vector")
        direction = [float(dx) / length, float(dy) / length, float(dz) / length]

        def _sync() -> dict[str, Any]:
            doc = self._doc()
            before = self._document_artifact_state(doc)
            viewport = doc.ActiveViewport
            viewport.Direction = _point(*direction)
            doc.ActiveViewport = viewport
            self._app().ZoomExtents()
            after = self._document_artifact_state(doc)
            return {
                "ok": True,
                "direction": direction,
                "normalized": True,
                "artifact_state": self._artifact_state_receipt(before, after),
            }

        return await self._run(_sync)

    async def view_set_preset(self, preset: str) -> dict[str, Any]:
        normalized = str(preset).strip().lower()
        direction = _VIEW_PRESETS.get(normalized)
        if direction is None:
            raise ValueError(
                "unsupported view preset; allowed: " + ", ".join(sorted(_VIEW_PRESETS))
            )
        result = await self.view_set_direction(*direction)
        return {**result, "preset": normalized}

    def _native_view_facade(self) -> Any:
        from ..native_bridge.public_runtime import NativePublicFacade

        return NativePublicFacade(self.settings)

    async def view_set_visual_style(self, style: str) -> dict[str, Any]:
        normalized = str(style).strip().lower()
        command = visual_style_command(normalized)

        def _style_key(value: Any) -> str:
            return re.sub(r"[^a-z0-9]+", "", str(value).casefold())

        def _sync() -> dict[str, Any]:
            doc = self._doc()
            facade = self._native_view_facade()
            before_style = facade.visual_style_get()
            before_handle = str(before_style.get("visual_style_handle") or "")
            before_name = str(before_style.get("visual_style_name") or "").strip()
            if not before_handle or not before_name:
                raise StateConflictError(
                    "native bridge did not expose a complete predecessor visual-style state"
                )
            before_artifact = self._document_artifact_state(doc)
            try:
                doc.SendCommand(command)
                deadline = time.monotonic() + min(
                    10.0, float(self.settings.com_call_timeout_seconds)
                )
                while time.monotonic() < deadline:
                    if not str(
                        _com_call_with_busy_retry(lambda: doc.GetVariable("CMDNAMES")) or ""
                    ).strip():
                        doc.Regen(1)
                        after_style = facade.visual_style_get()
                        actual_name = str(after_style.get("visual_style_name") or "").strip()
                        actual_handle = str(after_style.get("visual_style_handle") or "")
                        if not actual_handle or _style_key(actual_name) != _style_key(normalized):
                            raise StateConflictError(
                                "visual-style read-back mismatch: "
                                f"expected={normalized!r}; actual={actual_name!r}"
                            )
                        after_artifact = self._document_artifact_state(doc)
                        return {
                            "ok": True,
                            "visual_style": normalized,
                            "visual_style_readback": actual_name,
                            "visual_style_handle": actual_handle,
                            "previous_visual_style": before_name,
                            "previous_visual_style_handle": before_handle,
                            "state_verified": True,
                            "verification_route": "native-managed-bridge",
                            "command_idle_verified": True,
                            "artifact_state": self._artifact_state_receipt(
                                before_artifact, after_artifact
                            ),
                        }
                    time.sleep(0.05)
                raise BackendTimeoutError(
                    "AutoCAD did not return to idle after bounded visual-style command",
                    retryable=False,
                    completion_unknown=True,
                )
            except Exception as mutation_error:
                restore_errors: list[str] = []
                current_style: dict[str, Any] | None = None
                try:
                    current_style = facade.visual_style_get()
                except Exception as read_error:
                    restore_errors.append(f"current-read: {read_error}")
                if current_style is not None:
                    current_handle = str(current_style.get("visual_style_handle") or "")
                    if not current_handle:
                        restore_errors.append("current-read: missing visual_style_handle")
                    elif current_handle != before_handle:
                        try:
                            facade.visual_style_set(
                                before_handle,
                                expected_current_handle=current_handle,
                            )
                        except Exception as restore_error:
                            restore_errors.append(f"set: {restore_error}")
                try:
                    restored_style = facade.visual_style_get()
                    restored_handle = str(restored_style.get("visual_style_handle") or "")
                    if restored_handle != before_handle:
                        restore_errors.append(
                            f"read-back: expected={before_handle!r}; actual={restored_handle!r}"
                        )
                except Exception as restore_read_error:
                    restore_errors.append(f"read-back: {restore_read_error}")
                try:
                    restored_artifact = self._document_artifact_state(doc)
                    if restored_artifact != before_artifact:
                        restore_errors.append(
                            f"artifact-state: expected={before_artifact}; actual={restored_artifact}"
                        )
                except Exception as artifact_error:
                    restore_errors.append(f"artifact-state: {artifact_error}")
                if restore_errors:
                    reason = (
                        "visual-style restore verification failed; "
                        f"previous={before_name!r}; handle={before_handle}; errors={restore_errors}"
                    )
                    self._quarantine_integrity(reason)
                    raise StateConflictError(reason) from mutation_error
                raise

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

    def _finalize_created_solid(
        self,
        doc: Any,
        solid: Any,
        canonical_layer: str | None,
    ) -> dict[str, Any]:
        handle_value = _optional_com_property(solid, "Handle")
        handle = str(handle_value) if handle_value is not None else None
        try:
            if canonical_layer is not None:
                _com_set_attr(solid, "Layer", canonical_layer)
            return self._solid_info(solid)
        except Exception as creation_error:
            cleanup_errors: list[str] = []
            try:
                solid.Delete()
            except Exception as delete_error:
                cleanup_errors.append(f"delete: {delete_error}")
            if handle is not None and not cleanup_errors:
                try:
                    doc.HandleToObject(handle)
                except Exception:
                    pass
                else:
                    cleanup_errors.append("read-back: created solid still resolves after Delete")
            if cleanup_errors:
                reason = (
                    "created solid cleanup verification failed; "
                    f"handle={handle}; errors={cleanup_errors}"
                )
                self._quarantine_integrity(reason)
                raise StateConflictError(reason) from creation_error
            raise

    async def solid_box(
        self,
        cx: float,
        cy: float,
        cz: float,
        length: float,
        width: float,
        height: float,
        layer: str | None = None,
    ) -> dict[str, Any]:
        if length <= 0:
            raise ValueError("box length must be > 0")
        if width <= 0:
            raise ValueError("box width must be > 0")
        if height <= 0:
            raise ValueError("box height must be > 0")

        def _sync() -> dict[str, Any]:
            doc = self._doc()
            canonical_layer = self._validate_layer(layer)
            solid = doc.ModelSpace.AddBox(
                _point(cx, cy, cz), float(length), float(width), float(height)
            )
            return self._finalize_created_solid(doc, solid, canonical_layer)

        return await self._run(_sync)

    async def solid_cylinder(
        self,
        cx: float,
        cy: float,
        cz: float,
        radius: float,
        height: float,
        layer: str | None = None,
    ) -> dict[str, Any]:
        if radius <= 0:
            raise ValueError("cylinder radius must be > 0")
        if height <= 0:
            raise ValueError("cylinder height must be > 0")

        def _sync() -> dict[str, Any]:
            doc = self._doc()
            canonical_layer = self._validate_layer(layer)
            solid = doc.ModelSpace.AddCylinder(
                _point(cx, cy, cz), float(radius), float(height)
            )
            return self._finalize_created_solid(doc, solid, canonical_layer)

        return await self._run(_sync)

    async def solid_sphere(
        self,
        cx: float,
        cy: float,
        cz: float,
        radius: float,
        layer: str | None = None,
    ) -> dict[str, Any]:
        if radius <= 0:
            raise ValueError("sphere radius must be > 0")

        def _sync() -> dict[str, Any]:
            doc = self._doc()
            canonical_layer = self._validate_layer(layer)
            solid = doc.ModelSpace.AddSphere(_point(cx, cy, cz), float(radius))
            return self._finalize_created_solid(doc, solid, canonical_layer)

        return await self._run(_sync)

    async def solid_cone(
        self,
        cx: float,
        cy: float,
        cz: float,
        radius: float,
        height: float,
        layer: str | None = None,
    ) -> dict[str, Any]:
        if radius <= 0:
            raise ValueError("cone radius must be > 0")
        if height <= 0:
            raise ValueError("cone height must be > 0")

        def _sync() -> dict[str, Any]:
            doc = self._doc()
            canonical_layer = self._validate_layer(layer)
            solid = doc.ModelSpace.AddCone(
                _point(cx, cy, cz), float(radius), float(height)
            )
            return self._finalize_created_solid(doc, solid, canonical_layer)

        return await self._run(_sync)

    async def solid_torus(
        self,
        cx: float,
        cy: float,
        cz: float,
        torus_radius: float,
        tube_radius: float,
        layer: str | None = None,
    ) -> dict[str, Any]:
        if torus_radius <= 0:
            raise ValueError("torus_radius must be > 0")
        if tube_radius <= 0:
            raise ValueError("tube_radius must be > 0")

        def _sync() -> dict[str, Any]:
            doc = self._doc()
            canonical_layer = self._validate_layer(layer)
            solid = doc.ModelSpace.AddTorus(
                _point(cx, cy, cz), float(torus_radius), float(tube_radius)
            )
            return self._finalize_created_solid(doc, solid, canonical_layer)

        return await self._run(_sync)

    async def solid_wedge(
        self,
        cx: float,
        cy: float,
        cz: float,
        length: float,
        width: float,
        height: float,
        layer: str | None = None,
    ) -> dict[str, Any]:
        if length <= 0:
            raise ValueError("wedge length must be > 0")
        if width <= 0:
            raise ValueError("wedge width must be > 0")
        if height <= 0:
            raise ValueError("wedge height must be > 0")

        def _sync() -> dict[str, Any]:
            doc = self._doc()
            canonical_layer = self._validate_layer(layer)
            solid = doc.ModelSpace.AddWedge(
                _point(cx, cy, cz), float(length), float(width), float(height)
            )
            return self._finalize_created_solid(doc, solid, canonical_layer)

        return await self._run(_sync)

    async def solid_extrude(
        self,
        profile_handle: str,
        height: float,
        taper_angle: float = 0.0,
        layer: str | None = None,
    ) -> dict[str, Any]:
        if height == 0:
            raise ValueError("extrude height must be non-zero")
        if not -90.0 < float(taper_angle) < 90.0:
            raise ValueError("taper_angle must be strictly between -90 and 90 degrees")

        def _sync() -> dict[str, Any]:
            doc = self._doc()
            canonical_layer = self._validate_layer(layer)
            region = self._region_from_profile(doc, profile_handle)
            failed = False
            try:
                solid = doc.ModelSpace.AddExtrudedSolid(
                    region, float(height), math.radians(float(taper_angle))
                )
                return self._finalize_created_solid(doc, solid, canonical_layer)
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

    async def solid_sweep(
        self,
        profile_handle: str,
        path_handle: str,
        layer: str | None = None,
    ) -> dict[str, Any]:
        profile_key = str(profile_handle or "").strip().upper()
        path_key = str(path_handle or "").strip().upper()
        if not profile_key or not path_key:
            raise ValueError("profile_handle and path_handle must not be empty")
        if profile_key == path_key:
            raise ValueError("profile and sweep path must be different objects")

        def _sync() -> dict[str, Any]:
            doc = self._doc()
            canonical_layer = self._validate_layer(layer)
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
                return self._finalize_created_solid(doc, solid, canonical_layer)
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
        layer: str | None = None,
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
            canonical_layer = self._validate_layer(layer)
            region = self._region_from_profile(doc, profile_handle)
            failed = False
            try:
                solid = doc.ModelSpace.AddRevolvedSolid(
                    region,
                    _point(axis_x1, axis_y1, axis_z1),
                    _point(*axis),
                    math.radians(float(angle_deg)),
                )
                return self._finalize_created_solid(doc, solid, canonical_layer)
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
            try:
                target.Boolean(operation_codes[normalized], tool)
            except Exception as boolean_error:
                reason = (
                    "Boolean completion is uncertain after destructive ACIS failure; "
                    f"target={target_key}; tool={tool_key}; operation={canonical}"
                )
                self._quarantine_integrity(reason)
                raise StateConflictError(reason) from boolean_error
            try:
                result = self._solid_info(target)
            except Exception as inspect_error:
                reason = (
                    "Boolean post-mutation inspection failed; completion is uncertain; "
                    f"target={target_key}; tool={tool_key}; operation={canonical}"
                )
                self._quarantine_integrity(reason)
                raise StateConflictError(reason) from inspect_error
            try:
                _com_call_with_busy_retry(lambda: doc.HandleToObject(tool_key))
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
            if _object_type(_entity_property_view(mirrored)) != "3DSOLID":
                raise RuntimeError("Mirror3D did not return a 3DSOLID")
            result = self._solid_info(mirrored)
            result["source_handle"] = str(handle)
            return result

        return await self._run(_sync)

    async def solid_inspect(self, handle: str) -> dict[str, Any]:
        result = await self._run(
            lambda: self._solid_info(self._solid_by_id(handle)),
            may_mutate_document=False,
        )
        return {
            **result,
            "verification_capabilities": {
                "volume": True,
                "centroid": True,
                "bounding_box": True,
                "face_topology": False,
                "edge_topology": False,
                "reason": "ActiveX exposes no deterministic face/edge topology API",
            },
        }

    async def solid_export(
        self, handles: list[str], path: str, format: str = "sat"
    ) -> dict[str, Any]:
        normalized_format = str(format).strip().lower()
        if normalized_format != "sat":
            raise UnsupportedCapabilityError(
                "autocad.solid.export.sat",
                "The verified ActiveX export path supports SAT only; STEP/STL are not claimed.",
            )
        if not 1 <= len(handles) <= 256:
            raise ValueError("solid_export handles must contain 1..256 solids")
        keys = [str(handle or "").strip().upper() for handle in handles]
        if any(not key for key in keys) or len(set(keys)) != len(keys):
            raise ValueError("solid_export handles must be non-empty and unique")
        target = resolve_autocad_export_path(
            path, self.settings, allowed_suffixes=frozenset({".sat"})
        )

        def _sync() -> None:
            doc = self._doc()
            solids = [self._solid_by_id(key) for key in keys]
            selection_name = f"CDT_EXPORT_{threading.get_ident()}_{time.time_ns()}"
            selection = None
            try:
                selection = doc.SelectionSets.Add(selection_name)
                selection.AddItems(_dispatch_array(solids))
                base_name = str(target.with_suffix(""))
                doc.Export(base_name, "SAT", selection)
            finally:
                if selection is not None:
                    try:
                        selection.Delete()
                    except Exception:
                        pass

        await self._run(_sync, may_mutate_document=False)
        candidates = [target, target.with_suffix(".SAT")]
        actual = next((candidate for candidate in candidates if candidate.is_file()), None)
        if actual is None:
            raise RuntimeError("AutoCAD SAT export completed without producing the requested artifact")
        if actual != target:
            actual.replace(target)
            actual = target
        digest = hashlib.sha256(actual.read_bytes()).hexdigest()
        manifest = {
            "schema_version": 1,
            "status": "EXPORTED",
            "format": "sat",
            "path": str(actual),
            "sha256": digest,
            "size": actual.stat().st_size,
            "solid_handles": keys,
        }
        manifest_path = actual.with_suffix(actual.suffix + ".manifest.json")
        manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return {**manifest, "ok": True, "manifest_path": str(manifest_path)}

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
