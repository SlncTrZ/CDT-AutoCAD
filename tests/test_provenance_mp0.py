"""MP0-T00 provenance tests — lock parsing and reproducible runtime identity.
Wing: code | Topic: mp0-t00-reproducibility | Updated: 2026-09-10 23:20
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

from cdt_autocad.dependency_lock import (
    LOCK_PIP_VERSION,
    collect_lock_requirements,
    normalize_lock_newlines,
)
from cdt_autocad.provenance import (
    build_acceptance_record,
    build_runtime_manifest,
    canonical_lock_path,
    hash_source_tree,
    read_lock_packages,
    register_accepted_artifact,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
WINDOWS_LOCK = REPO_ROOT / "pylock.windows.toml"
LINUX_LOCK = REPO_ROOT / "pylock.linux.toml"
CURRENT_LOCK = WINDOWS_LOCK if sys.platform == "win32" else LINUX_LOCK


def test_lock_inputs_are_derived_from_pyproject_runtime_extras_dev_and_build_requirements():
    requirements = collect_lock_requirements(REPO_ROOT / "pyproject.toml")

    assert LOCK_PIP_VERSION == "26.1.2"
    assert "fastmcp>=3.4.5,<4" in requirements
    assert "ezdxf>=1.4,<2" in requirements
    assert "pydantic>=2,<3" in requirements
    assert "pywin32>=306; sys_platform == 'win32'" in requirements
    assert "pillow>=10,<12; sys_platform == 'win32'" in requirements
    assert "matplotlib>=3.8,<4" in requirements
    assert "pytest>=8,<9" in requirements
    assert "pytest-asyncio>=0.23,<1" in requirements
    assert "ruff>=0.16,<0.17" in requirements
    assert "hatchling" in requirements


def test_lock_newline_normalization_is_platform_stable(tmp_path):
    lock = tmp_path / "pylock.windows.toml"
    lock.write_bytes(b'lock-version = "1.0"\r\ncreated-by = "pip"\r\n')

    assert normalize_lock_newlines(lock) == lock
    assert lock.read_bytes() == b'lock-version = "1.0"\ncreated-by = "pip"\n'


def test_primary_windows_lock_pins_required_runtime_and_dev_dependencies():
    packages = read_lock_packages(WINDOWS_LOCK)

    assert packages["fastmcp"] == "3.4.7"
    assert packages["ezdxf"] == "1.4.4"
    assert packages["pydantic"] == "2.13.5"
    assert packages["pytest"] == "8.4.2"
    assert packages["pytest-asyncio"] == "0.26.0"
    assert packages["ruff"] == "0.16.7"
    assert packages["pywin32"] == "312"
    assert packages["pillow"] == "11.3.0"


def test_linux_lock_pins_ci_dependencies_without_windows_only_pywin32():
    packages = read_lock_packages(LINUX_LOCK)

    assert packages["fastmcp"] == "3.4.7"
    assert packages["ezdxf"] == "1.4.4"
    assert packages["pydantic"] == "2.13.5"
    assert packages["pytest"] == "8.4.2"
    assert packages["pytest-asyncio"] == "0.26.0"
    assert packages["ruff"] == "0.16.7"
    assert "pywin32" not in packages


def test_canonical_lock_matches_current_platform():
    assert canonical_lock_path(REPO_ROOT) == CURRENT_LOCK


def test_source_tree_hash_is_content_and_path_sensitive(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("alpha\n", encoding="utf-8")
    first = hash_source_tree(tmp_path, (Path("src"),))

    (tmp_path / "src" / "a.py").write_text("beta\n", encoding="utf-8")
    second = hash_source_tree(tmp_path, (Path("src"),))
    assert first != second

    (tmp_path / "src" / "nested").mkdir()
    (tmp_path / "src" / "nested" / "a.py").write_text("beta\n", encoding="utf-8")
    third = hash_source_tree(tmp_path, (Path("src"),))
    assert second != third


def _acceptance_runtime_manifest(*, bridge_sha256: str | None = "b" * 64):
    return {
        "schema_version": 1,
        "generated_at_utc": "2026-09-12T05:00:00+00:00",
        "git": {
            "head": "abc123",
            "dirty": True,
            "tracked_diff_sha256": "d" * 64,
        },
        "source": {"tree_sha256": "s" * 64},
        "provider": {"version": "0.4.0rc1"},
        "bridge_artifact": {
            "present": bridge_sha256 is not None,
            "path": "native/CDT.AutoCAD.Bridge/bin/x64/Release/CDT.AutoCAD.Bridge.dll",
            "sha256": bridge_sha256,
        },
        "autocad": {
            "target": "AutoCAD 2027 / Windows x64",
            "process_present": True,
            "pid": 4321,
            "session": "1",
            "observation": "tasklist",
        },
        "runtime": {
            "python_version": "3.12.3",
            "python_executable": "python.exe",
            "platform": "Windows-11",
            "machine": "AMD64",
            "hostname": "workstation",
        },
    }


def _loaded_bridge_observation(sha256: str = "b" * 64):
    return {
        "present": True,
        "path": r"C:\Users\truon\AppData\Roaming\Autodesk\ApplicationPlugins\CDT.AutoCAD.Bridge.bundle\Contents\Windows\CDT.AutoCAD.Bridge.dll",
        "sha256": sha256,
        "observation": "acad_process_module",
    }


def test_acceptance_record_binds_run_contract_binary_process_fixture_and_exit_code(tmp_path):
    fixture = tmp_path / "fixture.dwg"
    fixture.write_bytes(b"fixture")

    record = build_acceptance_record(
        _acceptance_runtime_manifest(),
        run_id="b5a-live-001",
        finding_ids=("BS-G09", "MP-G08"),
        contract_version="autocad-generic-v1-rc1",
        contract_hash="c" * 64,
        loaded_bridge_observation=_loaded_bridge_observation(),
        fixture_path=fixture,
        exit_code=0,
        result="PASS",
        checks=[{"name": "typed_dispatch", "result": "PASS"}],
        remaining_uncertainty=("independent review pending",),
    )

    assert record["run_id"] == "b5a-live-001"
    assert record["source_commit"] == "abc123"
    assert record["source_diff_sha256"] == "d" * 64
    assert record["provider_version"] == "0.4.0rc1"
    assert record["contract_version"] == "autocad-generic-v1-rc1"
    assert record["contract_hash"] == "c" * 64
    assert record["environment"]["runtime_observed_at_utc"] == "2026-09-12T05:00:00+00:00"
    assert record["environment"]["loaded_bridge"]["sha256"] == "b" * 64
    assert record["environment"]["loaded_bridge"]["observation"] == "acad_process_module"
    assert record["environment"]["candidate_bridge_artifact"]["sha256"] == "b" * 64
    assert record["environment"]["autocad_process"]["pid"] == 4321
    assert record["environment"]["autocad_process"]["session"] == "1"
    assert record["fixture"]["sha256"] == hashlib.sha256(b"fixture").hexdigest()
    assert record["exit_code"] == 0
    assert record["result"] == "PASS"
    assert record["remaining_uncertainty"] == ["independent review pending"]


def test_accepted_artifact_registry_uses_runtime_observed_binary_and_rejects_retroactive_binding(tmp_path):
    fixture = tmp_path / "fixture.dwg"
    fixture.write_bytes(b"fixture")
    record = build_acceptance_record(
        _acceptance_runtime_manifest(),
        run_id="accepted-001",
        finding_ids=("BS-G09",),
        contract_version="autocad-generic-v1-rc1",
        contract_hash="c" * 64,
        loaded_bridge_observation=_loaded_bridge_observation(),
        fixture_path=fixture,
        exit_code=0,
        result="PASS",
        checks=[],
        remaining_uncertainty=(),
    )
    evidence = tmp_path / "accepted-001.json"
    evidence.write_text(__import__("json").dumps(record), encoding="utf-8")
    registry_path = tmp_path / "accepted-artifacts.json"

    registry = register_accepted_artifact(registry_path, evidence)
    entry = registry["entries"]["accepted-001"]
    assert entry["loaded_bridge_sha256"] == "b" * 64
    assert entry["runtime_observed_at_utc"] == "2026-09-12T05:00:00+00:00"
    assert len(entry["evidence_sha256"]) == 64

    no_binary = build_acceptance_record(
        _acceptance_runtime_manifest(),
        run_id="historical-without-binary",
        finding_ids=("A-10",),
        contract_version="autocad-generic-v1-rc1",
        contract_hash="c" * 64,
        loaded_bridge_observation=None,
        fixture_path=None,
        exit_code=0,
        result="PASS",
        checks=[],
        remaining_uncertainty=("binary identity absent at run time",),
    )
    old_evidence = tmp_path / "historical.json"
    old_evidence.write_text(__import__("json").dumps(no_binary), encoding="utf-8")
    with __import__("pytest").raises(ValueError, match="runtime-observed bridge hash"):
        register_accepted_artifact(registry_path, old_evidence)


def test_accepted_artifact_registry_rejects_conflicting_reuse_of_run_id(tmp_path):
    record = build_acceptance_record(
        _acceptance_runtime_manifest(),
        run_id="accepted-duplicate",
        finding_ids=("A-10",),
        contract_version="autocad-generic-v1-rc1",
        contract_hash="c" * 64,
        loaded_bridge_observation=_loaded_bridge_observation(),
        fixture_path=None,
        exit_code=0,
        result="PASS",
        checks=[],
        remaining_uncertainty=(),
    )
    first = tmp_path / "first.json"
    first.write_text(__import__("json").dumps(record), encoding="utf-8")
    registry_path = tmp_path / "accepted-artifacts.json"
    register_accepted_artifact(registry_path, first)

    record["environment"]["loaded_bridge"]["sha256"] = "e" * 64
    second = tmp_path / "second.json"
    second.write_text(__import__("json").dumps(record), encoding="utf-8")
    with __import__("pytest").raises(ValueError, match="conflicting accepted-artifact entry"):
        register_accepted_artifact(registry_path, second)


def test_runtime_manifest_binds_git_lock_source_and_optional_dll(tmp_path):
    dll = tmp_path / "CDT.AutoCAD.Bridge.dll"
    dll.write_bytes(b"candidate-dll")

    manifest = build_runtime_manifest(
        REPO_ROOT,
        lock_path=CURRENT_LOCK,
        dll_path=dll,
        autocad_target="AutoCAD 2027 / COM 26.0",
    )

    assert manifest["schema_version"] == 1
    assert manifest["git"]["head"]
    assert isinstance(manifest["git"]["dirty"], bool)
    assert len(manifest["git"]["tracked_diff_sha256"]) == 64
    assert len(manifest["source"]["tree_sha256"]) == 64
    assert manifest["dependency_lock"]["path"] == CURRENT_LOCK.name
    assert len(manifest["dependency_lock"]["sha256"]) == 64
    assert manifest["dependency_lock"]["package_count"] > 20
    assert manifest["bridge_artifact"]["present"] is True
    assert manifest["bridge_artifact"]["sha256"] == hashlib.sha256(b"candidate-dll").hexdigest()
    assert manifest["autocad"]["target"] == "AutoCAD 2027 / COM 26.0"
    assert "process_present" in manifest["autocad"]
    assert manifest["runtime"]["python_version"]
    assert manifest["runtime"]["platform"]
