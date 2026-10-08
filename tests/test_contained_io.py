from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from cdt_autocad.contained_io import (
    ensure_contained_directory,
    open_contained_atomic_writer,
    open_contained_reader,
    replace_contained_file,
)


def _assert_no_temp_residue(directory: Path, target_name: str) -> None:
    leftovers = list(directory.glob(f".{target_name}.cdt-*.tmp"))
    assert leftovers == []


def test_contained_atomic_writer_commits_inside_allowed_root(settings, tmp_path: Path):
    target = tmp_path / "nested" / "part.bin"
    target.parent.mkdir()

    with open_contained_atomic_writer(target, settings, binary=True) as stream:
        stream.write(b"payload")

    assert target.read_bytes() == b"payload"
    _assert_no_temp_residue(target.parent, target.name)


def test_contained_atomic_writer_replaces_existing_file_inside_allowed_root(
    settings, tmp_path: Path
):
    target = tmp_path / "part.bin"
    target.write_bytes(b"old")

    with open_contained_atomic_writer(target, settings, binary=True) as stream:
        stream.write(b"new")

    assert target.read_bytes() == b"new"
    _assert_no_temp_residue(target.parent, target.name)


def test_contained_reader_reads_existing_file_inside_allowed_root(settings, tmp_path: Path):
    source = tmp_path / "source.bin"
    source.write_bytes(b"payload")

    with open_contained_reader(source, settings, binary=True) as stream:
        assert stream.read() == b"payload"


def test_ensure_contained_directory_creates_child_inside_allowed_root(settings, tmp_path: Path):
    target = tmp_path / "accepted"

    created = ensure_contained_directory(target, settings)

    assert created == target.resolve()
    assert created.is_dir()


def test_replace_contained_file_moves_bound_entry_inside_allowed_root(settings, tmp_path: Path):
    source = tmp_path / "source.bin"
    source.write_bytes(b"payload")
    target = tmp_path / "target.bin"

    replaced = replace_contained_file(source, target, settings)

    assert replaced == target.resolve()
    assert target.read_bytes() == b"payload"
    assert not source.exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX actual-I/O symlink-swap fixture")
def test_posix_atomic_writer_refuses_parent_swap_at_actual_commit(settings, tmp_path: Path):
    slot = tmp_path / "slot"
    slot.mkdir()
    target = slot / "part.bin"
    outside = tmp_path.parent / f"{tmp_path.name}-outside-atomic-write"
    outside.mkdir(exist_ok=True)
    original_slot = tmp_path / "original-slot"

    with pytest.raises(ValueError, match="namespace|parent|changed|reparse|symlink"):
        with open_contained_atomic_writer(target, settings, binary=True) as stream:
            stream.write(b"payload")
            slot.rename(original_slot)
            slot.symlink_to(outside, target_is_directory=True)

    assert not (outside / target.name).exists()
    assert not (original_slot / target.name).exists()
    _assert_no_temp_residue(original_slot, target.name)


@pytest.mark.skipif(os.name == "nt", reason="POSIX actual-I/O symlink-swap fixture")
def test_posix_reader_refuses_parent_swap_after_bound_read(settings, tmp_path: Path):
    slot = tmp_path / "slot"
    slot.mkdir()
    source = slot / "source.bin"
    source.write_bytes(b"trusted")
    outside = tmp_path.parent / f"{tmp_path.name}-outside-reader"
    outside.mkdir(exist_ok=True)
    (outside / source.name).write_bytes(b"attacker")
    original_slot = tmp_path / "original-slot"

    with pytest.raises(ValueError, match="namespace|parent|changed|reparse|symlink"):
        with open_contained_reader(source, settings, binary=True) as stream:
            assert stream.read() == b"trusted"
            slot.rename(original_slot)
            slot.symlink_to(outside, target_is_directory=True)


@pytest.mark.skipif(os.name != "nt", reason="Windows actual-I/O junction-swap fixture")
def test_windows_atomic_writer_blocks_or_refuses_parent_junction_swap_at_actual_commit(
    settings, tmp_path: Path
):
    slot = tmp_path / "slot"
    slot.mkdir()
    target = slot / "part.bin"
    outside = tmp_path.parent / f"{tmp_path.name}-outside-atomic-write"
    outside.mkdir(exist_ok=True)
    original_slot = tmp_path / "original-slot"
    attack_succeeded = False

    try:
        with open_contained_atomic_writer(target, settings, binary=True) as stream:
            stream.write(b"payload")
            try:
                slot.rename(original_slot)
            except PermissionError:
                # A held Windows directory/file handle may deny the namespace rename outright.
                pass
            else:
                created = subprocess.run(
                    ["cmd", "/c", "mklink", "/J", str(slot), str(outside)],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if created.returncode != 0:
                    raise AssertionError(
                        f"junction creation unavailable after successful rename: "
                        f"{created.stderr or created.stdout}"
                    )
                attack_succeeded = True
    except ValueError as exc:
        assert attack_succeeded
        assert any(token in str(exc).lower() for token in ("namespace", "parent", "changed", "reparse"))
    else:
        assert not attack_succeeded
        assert target.read_bytes() == b"payload"

    assert not (outside / target.name).exists()
    if attack_succeeded:
        assert not (original_slot / target.name).exists()
        _assert_no_temp_residue(original_slot, target.name)


@pytest.mark.skipif(os.name != "nt", reason="Windows actual-I/O junction-swap fixture")
def test_windows_reader_blocks_or_refuses_parent_junction_swap_after_bound_read(
    settings, tmp_path: Path
):
    slot = tmp_path / "slot"
    slot.mkdir()
    source = slot / "source.bin"
    source.write_bytes(b"trusted")
    outside = tmp_path.parent / f"{tmp_path.name}-outside-reader"
    outside.mkdir(exist_ok=True)
    (outside / source.name).write_bytes(b"attacker")
    original_slot = tmp_path / "original-slot"
    attack_succeeded = False

    try:
        with open_contained_reader(source, settings, binary=True) as stream:
            assert stream.read() == b"trusted"
            try:
                slot.rename(original_slot)
            except PermissionError:
                pass
            else:
                created = subprocess.run(
                    ["cmd", "/c", "mklink", "/J", str(slot), str(outside)],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if created.returncode != 0:
                    raise AssertionError(
                        f"junction creation unavailable after successful rename: "
                        f"{created.stderr or created.stdout}"
                    )
                attack_succeeded = True
    except ValueError as exc:
        assert attack_succeeded
        assert any(token in str(exc).lower() for token in ("namespace", "parent", "changed", "reparse"))
    else:
        assert not attack_succeeded
        assert source.read_bytes() == b"trusted"
