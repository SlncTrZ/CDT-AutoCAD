"""D16 live acceptance — shared COM/native writer authority and quarantine.
Wing: ops | Topic: d16-shared-mutation-ownership | Updated: 2026-09-19 14:38
"""

from __future__ import annotations

import argparse
import asyncio
import ctypes
import json
import os
import subprocess
import threading
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

from cdt_autocad.backends.com_backend import ComBackend, _com_call_with_busy_retry
from cdt_autocad.config import Settings
from cdt_autocad.errors import (
    BackendQuarantinedError,
    BackendTimeoutError,
    StateConflictError,
)
from cdt_autocad.mutation_coordinator import MutationCoordinator
from cdt_autocad.native_bridge.client import BridgeRemoteError
from cdt_autocad.native_bridge.public_runtime import NativePublicFacade
from cdt_autocad.server import _run_native_mutation

EXPECTED_BRIDGE_VERSION = "0.8.5-d15"


def _pid_from_hwnd(hwnd: int) -> int:
    pid = ctypes.c_ulong()
    ctypes.windll.user32.GetWindowThreadProcessId(int(hwnd), ctypes.byref(pid))
    return int(pid.value)


def _retry(action, *, timeout: float = 45.0, delay: float = 0.1):
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            return action()
        except Exception as exc:
            last_error = exc
            time.sleep(delay)
    raise RuntimeError("D16 acceptance operation did not become ready") from last_error


def _line(index: int) -> dict[str, Any]:
    x = float(index * 20)
    return {
        "kind": "line",
        "start": [x, 0.0, 0.0],
        "end": [x + 10.0, 0.0, 0.0],
    }


def _safe_restart_snapshot(app: Any, evidence_root: Path) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    docs = _retry(lambda: app.Documents, timeout=20.0)
    count = _retry(lambda: int(docs.Count), timeout=20.0)
    for index in range(count):
        doc = _retry(lambda index=index: docs.Item(index), timeout=20.0)
        name = _retry(lambda doc=doc: str(doc.Name), timeout=20.0)
        full_name = _retry(
            lambda doc=doc: str(getattr(doc, "FullName", "") or ""),
            timeout=20.0,
        )
        saved = _retry(lambda doc=doc: bool(doc.Saved), timeout=20.0)
        if not saved:
            if not full_name or not Path(full_name).is_absolute():
                target = evidence_root / f"d16-pre-restart-{int(time.time())}-{index}.dwg"
                _retry(
                    lambda doc=doc, target=target: doc.SaveAs(str(target), 64),
                    timeout=20.0,
                )
                full_name = _retry(
                    lambda doc=doc: str(doc.FullName or ""), timeout=20.0
                )
                saved = _retry(lambda doc=doc: bool(doc.Saved), timeout=20.0)
            elif (
                Path(full_name).name.startswith("d16-disposable-")
                and Path(full_name).parent.resolve() == evidence_root.resolve()
            ):
                _retry(lambda doc=doc: doc.Save(), timeout=20.0)
                saved = _retry(lambda doc=doc: bool(doc.Saved), timeout=20.0)
            else:
                raise RuntimeError(
                    f"refusing D16 process-loss fixture: unsaved document {name!r}"
                )
        if not saved:
            raise RuntimeError(
                f"refusing D16 process-loss fixture: document did not become saved {name!r}"
            )
        rows.append({"name": name, "saved": saved, "full_name": full_name})
    return {
        "pid": _pid_from_hwnd(int(_retry(lambda: app.HWND, timeout=20.0))),
        "exe": str(_retry(lambda: app.FullName, timeout=20.0)),
        "documents": rows,
    }


async def _serialization_scenarios(
    settings: Settings,
    facade: NativePublicFacade,
    document_pid: str,
    initial_fp: str,
) -> tuple[dict[str, Any], str]:
    coordinator = MutationCoordinator()
    backend = ComBackend(settings, mutation_coordinator=coordinator)
    com_entered = threading.Event()
    release_com = threading.Event()
    native_done = threading.Event()
    native_entered = threading.Event()
    release_native = threading.Event()
    com_done = threading.Event()
    current_fp = initial_fp

    try:
        def com_hold() -> str:
            doc = backend._doc()
            original = float(
                _com_call_with_busy_retry(lambda: doc.GetVariable("LTSCALE"))
            )
            com_entered.set()
            release_com.wait(5.0)
            _com_call_with_busy_retry(
                lambda: doc.SetVariable("LTSCALE", original + 0.001)
            )
            _com_call_with_busy_retry(lambda: doc.SetVariable("LTSCALE", original))
            return "com-mutated"

        com_task = asyncio.create_task(backend._run(com_hold, may_mutate_document=True))
        if not await asyncio.to_thread(com_entered.wait, 2.0):
            raise RuntimeError("COM writer did not acquire the D16 writer lease")

        native_task = asyncio.create_task(
            _run_native_mutation(
                coordinator,
                facade.batch_create_entities,
                [_line(1)],
                document_pid=document_pid,
                expected_parent_fp=current_fp,
            )
        )
        await asyncio.sleep(0.1)
        if native_task.done():
            raise AssertionError("native writer ran while COM writer still owned the shared lease")
        release_com.set()
        if await com_task != "com-mutated":
            raise AssertionError("COM serialization fixture did not complete")
        native_result = await native_task
        native_done.set()
        if native_result.get("status") != "COMMITTED":
            raise AssertionError("native writer did not commit after COM released the shared lease")
        current_fp = str(native_result["final_document_fp"])

        def native_hold_then_mutate() -> dict[str, Any]:
            native_entered.set()
            release_native.wait(5.0)
            return facade.batch_create_entities(
                [_line(2)],
                document_pid=document_pid,
                expected_parent_fp=current_fp,
            )

        native_task = asyncio.create_task(
            _run_native_mutation(coordinator, native_hold_then_mutate)
        )
        if not await asyncio.to_thread(native_entered.wait, 2.0):
            raise RuntimeError("native writer did not acquire the D16 writer lease")

        def com_after_native() -> str:
            doc = backend._doc()
            original = float(
                _com_call_with_busy_retry(lambda: doc.GetVariable("LTSCALE"))
            )
            _com_call_with_busy_retry(
                lambda: doc.SetVariable("LTSCALE", original + 0.001)
            )
            _com_call_with_busy_retry(lambda: doc.SetVariable("LTSCALE", original))
            com_done.set()
            return "com-after-native"

        com_task = asyncio.create_task(
            backend._run(com_after_native, may_mutate_document=True)
        )
        await asyncio.sleep(0.1)
        if com_done.is_set():
            raise AssertionError("COM writer ran while native writer still owned the shared lease")
        release_native.set()
        native_result = await native_task
        if native_result.get("status") != "COMMITTED":
            raise AssertionError("native writer did not commit in reverse serialization fixture")
        current_fp = str(native_result["final_document_fp"])
        if await com_task != "com-after-native" or not com_done.is_set():
            raise AssertionError("COM writer did not resume after native released the shared lease")

        return (
            {
                "com_to_native_serialized": native_done.is_set(),
                "native_to_com_serialized": com_done.is_set(),
                "coordinator": coordinator.status(),
            },
            current_fp,
        )
    finally:
        release_com.set()
        release_native.set()
        backend.shutdown()


async def _com_timeout_scenario(
    settings: Settings,
    facade: NativePublicFacade,
    document_pid: str,
    current_fp: str,
) -> dict[str, Any]:
    coordinator = MutationCoordinator()
    backend = ComBackend(
        replace(settings, com_call_timeout_seconds=0.05),
        mutation_coordinator=coordinator,
    )
    release = threading.Event()
    entered = threading.Event()
    native_ran = threading.Event()
    try:
        before = facade.status()["active_document"]
        before_count = int(before["entity_count"])

        def timed_out_com_mutation() -> None:
            doc = backend._doc()
            original = float(
                _com_call_with_busy_retry(lambda: doc.GetVariable("LTSCALE"))
            )
            _com_call_with_busy_retry(
                lambda: doc.SetVariable("LTSCALE", original + 0.001)
            )
            _com_call_with_busy_retry(lambda: doc.SetVariable("LTSCALE", original))
            entered.set()
            release.wait(5.0)

        try:
            await backend._run(timed_out_com_mutation, may_mutate_document=True)
        except BackendTimeoutError as exc:
            if exc.completion_unknown is not True:
                raise AssertionError("COM timeout was not classified as unknown completion") from exc
        else:
            raise AssertionError("COM timeout fixture did not time out")

        if not entered.is_set():
            raise AssertionError("COM timeout fixture never dispatched to the COM worker")

        try:
            await _run_native_mutation(
                coordinator,
                lambda: native_ran.set()
                or facade.batch_create_entities(
                    [_line(3)],
                    document_pid=document_pid,
                    expected_parent_fp=current_fp,
                ),
            )
        except BackendQuarantinedError:
            pass
        else:
            raise AssertionError("native writer was admitted after uncertain COM completion")
        if native_ran.is_set():
            raise AssertionError("native writer dispatched despite shared COM quarantine")

        release.set()
        await backend._run(lambda: "late-completion-barrier", may_mutate_document=False)
        if coordinator.status()["quarantined"] is not True:
            raise AssertionError("late COM completion cleared the shared quarantine")

        after = facade.status()["active_document"]
        if int(after["entity_count"]) != before_count:
            raise AssertionError("blocked native writer changed CAD entity state")

        return {
            "com_timeout_quarantined_native": True,
            "late_completion_kept_quarantine": True,
            "native_dispatch_blocked": True,
            "quarantine": coordinator.status(),
        }
    finally:
        release.set()
        backend.shutdown()


async def _native_cancel_scenario(
    settings: Settings,
    facade: NativePublicFacade,
    document_pid: str,
    current_fp: str,
) -> tuple[dict[str, Any], str]:
    coordinator = MutationCoordinator()
    backend = ComBackend(settings, mutation_coordinator=coordinator)
    native_committed = threading.Event()
    release = threading.Event()
    com_ran = threading.Event()
    result_holder: dict[str, Any] = {}

    def native_commit_then_hold_response() -> dict[str, Any]:
        result = facade.batch_create_entities(
            [_line(4)],
            document_pid=document_pid,
            expected_parent_fp=current_fp,
        )
        result_holder.update(result)
        native_committed.set()
        release.wait(5.0)
        return result

    task = asyncio.create_task(
        _run_native_mutation(coordinator, native_commit_then_hold_response)
    )
    try:
        if not await asyncio.to_thread(native_committed.wait, 5.0):
            raise RuntimeError("native cancellation fixture did not commit before cancellation")

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        else:
            raise AssertionError("native cancellation fixture did not propagate cancellation")

        if coordinator.status()["quarantined"] is not True:
            raise AssertionError("native cancellation after dispatch did not quarantine writers")

        try:
            await backend._run(
                lambda: com_ran.set(),
                may_mutate_document=True,
            )
        except BackendQuarantinedError:
            pass
        else:
            raise AssertionError("COM writer was admitted after native cancellation uncertainty")
        if com_ran.is_set():
            raise AssertionError("COM mutation dispatched despite native quarantine")

        release.set()
        await asyncio.sleep(0.1)
        if coordinator.status()["quarantined"] is not True:
            raise AssertionError("late native completion cleared shared quarantine")

        after = facade.status()["active_document"]
        expected_fp = str(result_holder["final_document_fp"])
        if after["document_fp"] != expected_fp:
            raise AssertionError("native committed state was not observable after cancelled response")

        return (
            {
                "native_cancel_quarantined_com": True,
                "late_completion_kept_quarantine": True,
                "com_dispatch_blocked": True,
                "quarantine": coordinator.status(),
            },
            expected_fp,
        )
    finally:
        release.set()
        backend.shutdown()


async def _deterministic_metadata_refusal_scenario(
    facade: NativePublicFacade,
    document_pid: str,
    current_fp: str,
) -> dict[str, Any]:
    coordinator = MutationCoordinator()
    bogus_pid = "pid:00000000-0000-4000-8000-000000000001"

    try:
        await _run_native_mutation(
            coordinator,
            facade.metadata_set,
            bogus_pid,
            "customer.d16.v1",
            {"probe": "deterministic-refusal"},
            document_pid=document_pid,
            expected_parent_fp=current_fp,
        )
    except BridgeRemoteError as exc:
        if exc.code != "ENTITY_NOT_FOUND":
            raise
    else:
        raise AssertionError("metadata deterministic refusal fixture unexpectedly mutated CAD")

    coordinator.ensure_writable()
    return {
        "typed_refusal": "ENTITY_NOT_FOUND",
        "quarantined": coordinator.status()["quarantined"],
    }


async def _process_loss_scenario(
    settings: Settings,
    application: Any,
    evidence_root: Path,
) -> dict[str, Any]:
    coordinator = MutationCoordinator()
    backend = ComBackend(settings, mutation_coordinator=coordinator)
    native_ran = threading.Event()
    before = _safe_restart_snapshot(application, evidence_root)
    old_pid = int(before["pid"])
    exe = str(before["exe"])

    try:
        await backend._run(lambda: backend._app().Name, may_mutate_document=False)
        backend._transaction_depth = 1

        subprocess.run(
            ["taskkill", "/PID", str(old_pid), "/F"],
            check=True,
            capture_output=True,
            text=True,
        )
        deadline = time.monotonic() + 20.0
        while time.monotonic() < deadline:
            rows = subprocess.run(
                ["tasklist", "/FI", f"PID eq {old_pid}", "/FO", "CSV", "/NH"],
                capture_output=True,
                text=True,
            ).stdout.strip()
            if rows.startswith('"INFO:'):
                break
            time.sleep(0.2)

        try:
            await backend._run(lambda: backend._app().Name, may_mutate_document=False)
        except StateConflictError as exc:
            if "tracked transaction" not in str(exc):
                raise
        else:
            raise AssertionError("tracked-transaction process loss did not fail closed")

        try:
            await _run_native_mutation(
                coordinator,
                lambda: native_ran.set(),
            )
        except BackendQuarantinedError:
            pass
        else:
            raise AssertionError("native writer was admitted after COM process-loss quarantine")
        if native_ran.is_set():
            raise AssertionError("native mutation dispatched after COM process loss")

        subprocess.Popen([exe], close_fds=True)
        import win32com.client

        new_pid: int | None = None
        deadline = time.monotonic() + 90.0
        while time.monotonic() < deadline:
            try:
                replacement = win32com.client.GetActiveObject(settings.com_progid)
                _ = str(replacement.Name)
                candidate = _pid_from_hwnd(int(replacement.HWND))
                if candidate and candidate != old_pid:
                    new_pid = candidate
                    break
            except Exception:
                pass
            time.sleep(0.5)
        if new_pid is None:
            raise RuntimeError("replacement AutoCAD did not register after D16 process-loss fixture")

        return {
            "process_loss_quarantined_native": True,
            "native_dispatch_blocked": True,
            "old_pid": old_pid,
            "replacement_pid": new_pid,
            "quarantine": coordinator.status(),
        }
    finally:
        backend._transaction_depth = 0
        backend.shutdown()


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    if os.name != "nt":
        raise RuntimeError("D16 live acceptance requires Windows AutoCAD")

    import pythoncom
    import win32com.client

    pythoncom.CoInitialize()
    created_document = None
    disposable_path: Path | None = None
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    try:
        application = _retry(
            lambda: win32com.client.GetActiveObject(args.com_progid),
            timeout=45.0,
        )
        created_document = _retry(lambda: application.Documents.Add())
        _retry(lambda: created_document.Activate())
        disposable_path = output.parent / f"d16-disposable-{time.time_ns()}.dwg"
        _retry(lambda: created_document.SaveAs(str(disposable_path)), timeout=20.0)

        settings = replace(
            Settings.from_env(),
            backend="com",
            com_progid=args.com_progid,
            com_attach_policy="attach_only",
        )
        facade = NativePublicFacade(settings)
        health = facade._client().health()
        if health.get("bridge_version") != EXPECTED_BRIDGE_VERSION:
            raise AssertionError(
                f"unexpected native bridge version: {health.get('bridge_version')!r}"
            )

        bootstrap_coordinator = MutationCoordinator()
        initialized = await _run_native_mutation(
            bootstrap_coordinator,
            facade.bootstrap_document_identity,
        )
        document_pid = str(initialized["document_pid"])
        current_fp = str(initialized["document_fp"])

        serialization, current_fp = await _serialization_scenarios(
            settings,
            facade,
            document_pid,
            current_fp,
        )
        timeout_result = await _com_timeout_scenario(
            settings,
            facade,
            document_pid,
            current_fp,
        )
        cancel_result, current_fp = await _native_cancel_scenario(
            settings,
            facade,
            document_pid,
            current_fp,
        )
        deterministic_refusal = await _deterministic_metadata_refusal_scenario(
            facade,
            document_pid,
            current_fp,
        )

        final_doc = facade.status()["active_document"]
        final_entity_count = int(final_doc["entity_count"])
        if final_entity_count != 3:
            raise AssertionError(
                f"unexpected D16 disposable entity count before process-loss fixture: {final_entity_count}"
            )

        _retry(lambda: created_document.Save(), timeout=20.0)
        if not _retry(lambda: bool(created_document.Saved), timeout=20.0):
            raise RuntimeError(
                "refusing D16 process-loss fixture: disposable document did not become saved"
            )
        created_document = None

        application = _retry(
            lambda: win32com.client.GetActiveObject(args.com_progid),
            timeout=20.0,
        )
        process_loss = await _process_loss_scenario(
            settings,
            application,
            output.parent,
        )

        summary = {
            "schema_version": 1,
            "finding_id": "D16",
            "status": "PASS",
            "bridge_version": health["bridge_version"],
            "document_pid": document_pid,
            "final_document_fp_before_restart": current_fp,
            "final_entity_count_before_restart": final_entity_count,
            "serialization": serialization,
            "com_timeout": timeout_result,
            "native_cancellation": cancel_result,
            "deterministic_metadata_refusal": deterministic_refusal,
            "process_loss": process_loss,
            "duration_seconds": round(time.perf_counter() - started, 3),
        }
        output.write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return summary
    finally:
        try:
            if created_document is not None:
                created_document.Close(False)
        except Exception:
            pass
        if disposable_path is not None:
            try:
                disposable_path.unlink(missing_ok=True)
            except OSError:
                pass
        pythoncom.CoUninitialize()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run D16 shared COM/native mutation authority acceptance on AutoCAD 2027"
    )
    parser.add_argument("--output", required=True)
    parser.add_argument("--com-progid", default="AutoCAD.Application.26")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(_run(args)), sort_keys=True))


if __name__ == "__main__":
    main()
