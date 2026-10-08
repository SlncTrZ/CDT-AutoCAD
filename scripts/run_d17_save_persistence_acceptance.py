"""D17 live acceptance — Save/SaveAs persisted-clean verification on the bound document.
Wing: ops | Topic: d17-save-persistence | Updated: 2026-09-19 15:36
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

from cdt_autocad.backends.com_backend import (
    ComBackend,
    _com_call_with_busy_retry,
    _com_property_with_busy_retry,
    _point,
)
from cdt_autocad.config import Settings
from cdt_autocad.errors import BackendQuarantinedError, StateConflictError


class _DirtyAfterSaveAsBackend(ComBackend):
    """Inject one real CAD mutation after SaveAs path binding and before clean-state readback."""

    def __init__(self, settings: Settings):
        super().__init__(settings)
        self.injected = False

    def _bound_document_path(self, doc: Any, **kwargs: Any) -> Path:
        resolved = super()._bound_document_path(doc, **kwargs)
        if (
            kwargs.get("operation") == "save as"
            and kwargs.get("expected") is not None
            and not self.injected
        ):
            space = _com_property_with_busy_retry(doc, "ModelSpace")
            _com_call_with_busy_retry(
                lambda: space.AddLine(
                    _point(0.0, 0.0, 0.0),
                    _point(10.0, 0.0, 0.0),
                )
            )
            self.injected = True
        return resolved


def _shutdown_backend(backend: ComBackend) -> None:
    executor = backend._executor
    backend.shutdown()
    if executor is not None:
        executor.shutdown(wait=True)


async def _close_fixture_document(backend: ComBackend) -> None:
    if backend._executor is None:
        return
    try:
        await backend._run(
            lambda: backend._doc().Close(False),
            may_mutate_document=False,
        )
    except Exception:
        pass


async def _positive(settings: Settings, target: Path) -> dict[str, Any]:
    backend = ComBackend(settings)
    try:
        created = await backend.document_new()
        receipt = await backend.document_save_as(str(target))
        info = await backend.document_info()

        if receipt.get("persisted_clean") is not True:
            raise AssertionError("SaveAs did not report persisted_clean=true")
        if receipt.get("saved") is not True or int(receipt.get("dbmod", -1)) != 0:
            raise AssertionError("SaveAs persisted-clean receipt has invalid Saved/DBMOD values")
        if receipt.get("postcondition_verified") is not True:
            raise AssertionError("SaveAs did not report verified postcondition")
        if Path(str(receipt["path"])).resolve() != target.resolve():
            raise AssertionError("SaveAs receipt path does not match requested target")
        if Path(str(info["path"])).resolve() != target.resolve():
            raise AssertionError("active document path drifted after SaveAs")
        if info.get("name") != target.name:
            raise AssertionError("active document identity/name drifted after SaveAs")
        if not target.is_file():
            raise AssertionError("SaveAs target artifact was not created")

        return {
            "created_name": created["name"],
            "path": str(target.resolve()),
            "saved": receipt["saved"],
            "dbmod": receipt["dbmod"],
            "persisted_clean": receipt["persisted_clean"],
            "postcondition_verified": receipt["postcondition_verified"],
            "same_bound_document_verified": True,
        }
    finally:
        await _close_fixture_document(backend)
        _shutdown_backend(backend)


async def _negative(settings: Settings, target: Path) -> dict[str, Any]:
    backend = _DirtyAfterSaveAsBackend(settings)
    try:
        await backend.document_new()
        try:
            await backend.document_save_as(str(target))
        except StateConflictError as exc:
            if "persisted-clean" not in str(exc):
                raise
        else:
            raise AssertionError("dirty SaveAs postcondition incorrectly returned success")

        if backend.injected is not True:
            raise AssertionError("D17 dirty-state fault injection did not execute")
        status = backend.status()
        coordinator = backend._mutation_coordinator.status()
        if status.get("integrity_uncertain") is not True:
            raise AssertionError("dirty SaveAs did not set COM integrity uncertainty")
        if coordinator.get("quarantined") is not True:
            raise AssertionError("dirty SaveAs did not set shared mutation quarantine")
        if coordinator.get("quarantine_lane") != "com":
            raise AssertionError("dirty SaveAs quarantine lane is not COM")

        try:
            await backend.document_save()
        except BackendQuarantinedError:
            pass
        else:
            raise AssertionError("later Save was admitted after dirty SaveAs quarantine")

        return {
            "dirty_state_injected": True,
            "save_as_refused": True,
            "later_mutation_blocked": True,
            "quarantine_lane": coordinator["quarantine_lane"],
            "quarantine_reason": coordinator["quarantine_reason"],
        }
    finally:
        await _close_fixture_document(backend)
        _shutdown_backend(backend)


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    if os.name != "nt":
        raise RuntimeError("D17 live acceptance requires Windows AutoCAD")

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    positive_target = output.parent / f"d17-positive-{time.time_ns()}.dwg"
    negative_target = output.parent / f"d17-negative-{time.time_ns()}.dwg"
    settings = replace(
        Settings.from_env(),
        backend="com",
        allowed_paths=(output.parent.resolve(),),
        com_progid=args.com_progid,
        com_attach_policy="attach_only",
        com_call_timeout_seconds=60.0,
    )
    started = time.perf_counter()

    try:
        positive = await _positive(settings, positive_target)
        negative = await _negative(settings, negative_target)
        summary = {
            "schema_version": 1,
            "finding_id": "D17",
            "status": "PASS",
            "positive_save_as": positive,
            "dirty_postcondition": negative,
            "duration_seconds": round(time.perf_counter() - started, 3),
        }
        output.write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return summary
    finally:
        for target in (positive_target, negative_target):
            try:
                target.unlink(missing_ok=True)
            except OSError:
                pass


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run D17 Save/SaveAs persisted-clean acceptance on AutoCAD 2027"
    )
    parser.add_argument("--output", required=True)
    parser.add_argument("--com-progid", default="AutoCAD.Application.26")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(_run(args)), sort_keys=True))


if __name__ == "__main__":
    main()
