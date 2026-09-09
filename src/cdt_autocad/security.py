"""Filesystem boundary checks for AutoCAD document operations.
Wing: code | Topic: autocad-a2 | Updated: 2026-09-09 14:06
"""

from __future__ import annotations

from pathlib import Path

from .config import Settings


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


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

    candidate = Path(raw_path).expanduser()
    if not candidate.is_absolute():
        candidate = settings.allowed_paths[0] / candidate
    resolved = candidate.resolve(strict=False)

    if not any(_inside(resolved, root) for root in settings.allowed_paths):
        raise ValueError("path is outside CDT_AUTOCAD_ALLOWED_PATHS")

    if resolved.suffix.lower() not in allowed_suffixes:
        allowed = ", ".join(sorted(allowed_suffixes))
        raise ValueError(f"path extension must be one of: {allowed}")

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
