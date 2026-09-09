from __future__ import annotations

from pathlib import Path

import pytest

from cdt_autocad.security import resolve_dxf_path


def test_relative_path_resolves_inside_first_allowed_root(settings, tmp_path: Path):
    resolved = resolve_dxf_path("part.dxf", settings, must_exist=False, for_write=True)
    assert resolved == (tmp_path / "part.dxf").resolve()


def test_outside_allowed_root_is_rejected(settings, tmp_path: Path):
    outside = tmp_path.parent / "outside.dxf"
    with pytest.raises(ValueError, match="outside"):
        resolve_dxf_path(str(outside), settings, must_exist=False, for_write=True)


def test_non_dxf_extension_is_rejected(settings, tmp_path: Path):
    with pytest.raises(ValueError, match="only accepts .dxf"):
        resolve_dxf_path(str(tmp_path / "part.txt"), settings, must_exist=False, for_write=True)


def test_oversized_input_is_rejected(settings, tmp_path: Path):
    target = tmp_path / "huge.dxf"
    target.write_bytes(b"x" * (settings.max_dxf_bytes + 1))
    with pytest.raises(ValueError, match="size limit"):
        resolve_dxf_path(str(target), settings, must_exist=True)
