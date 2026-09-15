"""Filesystem boundary checks for AutoCAD document operations.
Wing: code | Topic: autocad-a2 | Updated: 2026-09-14 22:54
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

from .config import Settings


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _reject_unc_path(raw_path: str | Path) -> None:
    text = str(raw_path).strip()
    if text.startswith("\\\\") or text.startswith("//"):
        raise ValueError("UNC/network paths are not allowed")


def _matching_root(path: Path, settings: Settings) -> Path:
    root = next((root for root in settings.allowed_paths if _inside(path, root)), None)
    if root is None:
        raise ValueError("path is outside CDT_AUTOCAD_ALLOWED_PATHS")
    return root


def _is_redirecting_component(path: Path) -> bool:
    if path.is_symlink():
        return True
    if os.name != "nt":
        return False
    try:
        attributes = path.lstat().st_file_attributes
    except (FileNotFoundError, AttributeError, OSError):
        return False
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return bool(reparse_flag and attributes & reparse_flag)


def _reject_redirecting_components(path: Path, root: Path) -> None:
    relative = path.relative_to(root)
    current = root
    for part in relative.parts:
        current = current / part
        if _is_redirecting_component(current):
            raise ValueError(f"path contains a symlink/reparse/junction component: {current}")
        if not current.exists():
            break


def _lexical_candidate(raw_path: str | Path, settings: Settings) -> tuple[Path, Path]:
    _reject_unc_path(raw_path)
    candidate = Path(raw_path).expanduser()
    if not candidate.is_absolute():
        candidate = settings.allowed_paths[0] / candidate
    lexical = candidate.absolute()
    root = _matching_root(lexical, settings)
    _reject_redirecting_components(lexical, root)
    return lexical, root


def _validate_resolved_target(
    resolved: Path,
    settings: Settings,
    *,
    must_exist: bool,
    for_write: bool,
) -> Path:
    _matching_root(resolved, settings)
    if must_exist:
        if not resolved.is_file():
            raise FileNotFoundError(str(resolved))
        size = resolved.stat().st_size
        if size > settings.max_dxf_bytes:
            raise ValueError(
                f"CAD document exceeds configured size limit ({size} > {settings.max_dxf_bytes})"
            )
    if for_write:
        parent = resolved.parent
        if not parent.exists() or not parent.is_dir():
            raise ValueError("destination parent directory does not exist")
    return resolved


def revalidate_side_effect_path(
    expected: str | Path,
    settings: Settings,
    *,
    must_exist: bool,
    for_write: bool = False,
) -> Path:
    """Revalidate one canonical file target immediately before a side effect."""
    lexical, root = _lexical_candidate(expected, settings)
    resolved = lexical.resolve(strict=False)
    if resolved != lexical or not _inside(resolved, root):
        raise ValueError("path changed before filesystem side effect")
    _reject_redirecting_components(lexical, root)
    return _validate_resolved_target(
        resolved,
        settings,
        must_exist=must_exist,
        for_write=for_write,
    )


def _resolve_path(
    raw_path: str,
    settings: Settings,
    *,
    allowed_suffixes: frozenset[str],
    must_exist: bool,
    for_write: bool,
) -> Path:
    if not raw_path or not raw_path.strip():
        raise ValueError("path must not be empty")

    lexical, root = _lexical_candidate(raw_path, settings)
    resolved = lexical.resolve(strict=False)
    if not _inside(resolved, root):
        raise ValueError("path is outside CDT_AUTOCAD_ALLOWED_PATHS")

    if resolved.suffix.lower() not in allowed_suffixes:
        allowed = ", ".join(sorted(allowed_suffixes))
        raise ValueError(f"path extension must be one of: {allowed}")

    return _validate_resolved_target(
        resolved,
        settings,
        must_exist=must_exist,
        for_write=for_write,
    )


def resolve_dxf_path(
    raw_path: str,
    settings: Settings,
    *,
    must_exist: bool,
    for_write: bool = False,
) -> Path:
    if Path(raw_path).suffix.lower() != ".dxf":
        raise ValueError("headless backend only accepts .dxf paths")
    return _resolve_path(
        raw_path,
        settings,
        allowed_suffixes=frozenset({".dxf"}),
        must_exist=must_exist,
        for_write=for_write,
    )


def resolve_autocad_document_path(
    raw_path: str,
    settings: Settings,
    *,
    must_exist: bool,
    for_write: bool = False,
) -> Path:
    """Resolve a native live-AutoCAD document path without weakening allowed roots."""
    return _resolve_path(
        raw_path,
        settings,
        allowed_suffixes=frozenset({".dwg", ".dxf"}),
        must_exist=must_exist,
        for_write=for_write,
    )


def resolve_pdf_path(raw_path: str, settings: Settings) -> Path:
    return _resolve_path(
        raw_path,
        settings,
        allowed_suffixes=frozenset({".pdf"}),
        must_exist=False,
        for_write=True,
    )


def resolve_allowed_directory(raw_path: str, settings: Settings) -> Path:
    """Resolve an existing destination directory inside the configured CAD roots."""
    if not raw_path or not str(raw_path).strip():
        raise ValueError("directory path must not be empty")
    lexical, root = _lexical_candidate(raw_path, settings)
    resolved = lexical.resolve(strict=False)
    if not _inside(resolved, root):
        raise ValueError("path is outside CDT_AUTOCAD_ALLOWED_PATHS")
    if not resolved.exists() or not resolved.is_dir():
        raise ValueError("destination directory does not exist")
    return resolved


def resolve_autocad_export_path(
    raw_path: str,
    settings: Settings,
    *,
    allowed_suffixes: frozenset[str],
) -> Path:
    """Resolve a bounded export artifact path under the configured roots."""
    return _resolve_path(
        raw_path,
        settings,
        allowed_suffixes=allowed_suffixes,
        must_exist=False,
        for_write=True,
    )
