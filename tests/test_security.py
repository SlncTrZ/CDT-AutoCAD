from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from cdt_autocad.security import revalidate_side_effect_path, resolve_dxf_path


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


def test_unc_path_is_rejected_before_resolution(settings):
    with pytest.raises(ValueError, match="UNC"):
        resolve_dxf_path(r"\\server\share\part.dxf", settings, must_exist=False, for_write=True)


@pytest.mark.skipif(os.name == "nt", reason="Linux symlink negative fixture")
def test_symlink_component_is_rejected_even_when_target_stays_inside_root(settings, tmp_path: Path):
    real_dir = tmp_path / "real"
    real_dir.mkdir()
    link_dir = tmp_path / "link"
    link_dir.symlink_to(real_dir, target_is_directory=True)

    with pytest.raises(ValueError, match="symlink|reparse"):
        resolve_dxf_path(str(link_dir / "part.dxf"), settings, must_exist=False, for_write=True)


@pytest.mark.skipif(os.name == "nt", reason="Linux path-swap negative fixture")
def test_side_effect_revalidation_rejects_parent_symlink_swap(settings, tmp_path: Path):
    slot = tmp_path / "slot"
    slot.mkdir()
    expected = resolve_dxf_path(str(slot / "part.dxf"), settings, must_exist=False, for_write=True)
    slot.rename(tmp_path / "original-slot")
    outside = tmp_path.parent / f"{tmp_path.name}-outside-swap"
    outside.mkdir(exist_ok=True)
    slot.symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="changed|outside|symlink|reparse"):
        revalidate_side_effect_path(expected, settings, must_exist=False, for_write=True)


@pytest.mark.skipif(os.name != "nt", reason="Windows junction negative fixture")
def test_windows_junction_component_is_rejected(settings, tmp_path: Path):
    real_dir = tmp_path / "real"
    real_dir.mkdir()
    junction = tmp_path / "junction"
    created = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(junction), str(real_dir)],
        capture_output=True,
        text=True,
        check=False,
    )
    if created.returncode != 0:
        pytest.skip(f"junction creation unavailable: {created.stderr or created.stdout}")

    with pytest.raises(ValueError, match="symlink|reparse|junction"):
        resolve_dxf_path(str(junction / "part.dxf"), settings, must_exist=False, for_write=True)


@pytest.mark.skipif(os.name != "nt", reason="Windows junction path-swap negative fixture")
def test_windows_side_effect_revalidation_rejects_junction_swap(settings, tmp_path: Path):
    slot = tmp_path / "slot"
    slot.mkdir()
    expected = resolve_dxf_path(str(slot / "part.dxf"), settings, must_exist=False, for_write=True)
    slot.rename(tmp_path / "original-slot")
    outside = tmp_path.parent / f"{tmp_path.name}-outside-junction"
    outside.mkdir(exist_ok=True)
    created = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(slot), str(outside)],
        capture_output=True,
        text=True,
        check=False,
    )
    if created.returncode != 0:
        pytest.skip(f"junction creation unavailable: {created.stderr or created.stdout}")

    with pytest.raises(ValueError, match="changed|outside|symlink|reparse|junction"):
        revalidate_side_effect_path(expected, settings, must_exist=False, for_write=True)
