"""U1 live acceptance — document bootstrap plus annotation/dimension batch hardening.
Wing: ops | Topic: u1-floorplan-hardening | Updated: 2026-09-17
"""

from __future__ import annotations

import argparse
import ctypes
import json
import math
import os
import time
import traceback
from collections.abc import Callable
from pathlib import Path
from typing import Any

from cdt_autocad.native_bridge.client import NativeBridgeClient
from cdt_autocad.native_bridge.transport_windows import NamedPipeTransport, pipe_name_for_session

EXPECTED_BRIDGE_VERSION = "0.8.3-u1"


def _session_id() -> int:
    value = ctypes.c_ulong()
    if not ctypes.windll.kernel32.ProcessIdToSessionId(os.getpid(), ctypes.byref(value)):
        raise RuntimeError("could not resolve Windows session id")
    return int(value.value)


def _retry(action: Callable[[], Any], *, timeout: float = 20.0, delay: float = 0.1) -> Any:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            return action()
        except Exception as exc:
            last_error = exc
            time.sleep(delay)
    raise RuntimeError("live acceptance operation did not become ready before timeout") from last_error


def _finalize(
    client: NativeBridgeClient,
    runtime_document_id: str,
    document_pid: str,
    receipt: dict[str, Any],
) -> None:
    checkpoint = receipt.get("recovery_checkpoint")
    if not isinstance(checkpoint, dict):
        raise AssertionError("committed U1 batch did not return a recovery checkpoint")
    finalized = client.finalize_recovery(
        runtime_document_id,
        document_pid=document_pid,
        checkpoint_id=str(checkpoint["checkpoint_id"]),
        checkpoint_artifact_fp=str(checkpoint["checkpoint_artifact_fp"]),
        accepted_post_fp=str(receipt["post_document_fp"]),
    )
    if finalized.get("status") != "FINALIZED":
        raise AssertionError("U1 recovery checkpoint did not finalize")


def _run(args: argparse.Namespace) -> dict[str, Any]:
    if os.name != "nt":
        raise RuntimeError("U1 live acceptance requires Windows AutoCAD")

    import pythoncom
    import win32com.client

    pythoncom.CoInitialize()
    document = None
    fixture_path: Path | None = None
    started = time.perf_counter()
    try:
        application = _retry(lambda: win32com.client.GetActiveObject(args.com_progid))
        document = _retry(lambda: application.Documents.Add())
        _retry(lambda: document.Activate())
        fixture_path = Path(os.environ["LOCALAPPDATA"]) / "Temp" / f"cdt-u1-{time.time_ns()}.dwg"
        _retry(lambda: document.SaveAs(str(fixture_path)), timeout=30.0)

        for layer_name in ("U1-GEOM", "U1-ANNOT", "U1-DIM"):
            _retry(lambda name=layer_name: document.Layers.Add(name))

        session_id = _session_id()
        client = NativeBridgeClient(
            NamedPipeTransport(pipe_name_for_session(session_id), connect_timeout_ms=8_000)
        )
        health = _retry(client.health, timeout=30.0)
        if health.get("bridge_version") != EXPECTED_BRIDGE_VERSION:
            raise AssertionError(f"unexpected bridge version: {health.get('bridge_version')!r}")
        mutation_operations = set(health.get("mutation_operations", []))
        if not {"bridge.document.identity.initialize", "entity.batch.create"} <= mutation_operations:
            raise AssertionError("bridge does not advertise U1 bootstrap + batch mutation operations")

        documents = client.documents_list()
        active = [item for item in documents if item.get("is_active") is True]
        if len(active) != 1:
            raise AssertionError("U1 acceptance requires exactly one bridge-reported active document")
        active_row = active[0]
        if active_row.get("document_pid") is not None:
            raise AssertionError("fresh U1 drawing unexpectedly already contains provider document lineage")
        runtime_document_id = str(active_row["runtime_document_id"])

        bootstrap = client.initialize_document_identity(runtime_document_id)
        document_pid = str(bootstrap["document_pid"])
        parent_fp = str(bootstrap["document_fp"])
        if bootstrap.get("readback_verified") is not True or bootstrap.get("entity_count") != 0:
            raise AssertionError("U1 document lineage bootstrap was not independently verified")

        entities: tuple[dict[str, Any], ...] = (
            {
                "kind": "line",
                "start": [0.0, 0.0, 0.0],
                "end": [5000.0, 0.0, 0.0],
                "layer": "U1-GEOM",
                "color_index": 1,
            },
            {
                "kind": "text",
                "text": "U1 TEXT",
                "position": [500.0, 800.0, 0.0],
                "height": 250.0,
                "rotation": 0.0,
                "layer": "U1-ANNOT",
                "color_index": 2,
            },
            {
                "kind": "mtext",
                "text": "U1 MTEXT",
                "position": [500.0, 1400.0, 0.0],
                "height": 250.0,
                "rotation": 0.0,
                "width": 2200.0,
                "layer": "U1-ANNOT",
                "color_index": 3,
            },
            {
                "kind": "aligned_dimension",
                "xline1": [0.0, 0.0, 0.0],
                "xline2": [5000.0, 0.0, 0.0],
                "dim_line_point": [2500.0, -900.0, 0.0],
                "layer": "U1-DIM",
                "color_index": 4,
            },
            {
                "kind": "linear_dimension",
                "xline1": [0.0, 2000.0, 0.0],
                "xline2": [0.0, 6000.0, 0.0],
                "dim_line_point": [900.0, 4000.0, 0.0],
                "rotation": math.pi / 2.0,
                "layer": "U1-DIM",
                "color_index": 5,
            },
        )
        receipts: list[dict[str, Any]] = []
        progress_path = Path(args.output).with_suffix(".progress.json")
        for spec in entities:
            receipt = client.batch_create_chunk(
                runtime_document_id,
                document_pid=document_pid,
                expected_parent_fp=parent_fp,
                entities=(spec,),
            )
            receipts.append(receipt)
            progress_path.parent.mkdir(parents=True, exist_ok=True)
            progress_path.write_text(
                json.dumps(
                    {
                        "status": "RUNNING",
                        "current_kind": spec["kind"],
                        "document_pid": document_pid,
                        "expected_parent_fp": parent_fp,
                        "receipt": receipt,
                        "completed_kinds": [item["kind"] for item in entities[: len(receipts) - 1]],
                    },
                    indent=2,
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            if receipt.get("outcome") != "COMMITTED_VERIFIED":
                raise AssertionError(
                    f"U1 batch kind={spec['kind']} was not COMMITTED_VERIFIED: {receipt!r}"
                )
            if len(receipt.get("affected_semantic_pids", [])) != 1:
                raise AssertionError(f"U1 batch kind={spec['kind']} returned invalid persistent PID receipt")
            _finalize(client, runtime_document_id, document_pid, receipt)
            parent_fp = str(receipt["post_document_fp"])
            if client.recoveries_list():
                raise AssertionError(f"U1 batch kind={spec['kind']} left pending recovery state")

        snapshot = client.document_snapshot(runtime_document_id, document_pid=document_pid)
        by_type: dict[str, list[Any]] = {}
        for entity in snapshot.entities:
            by_type.setdefault(entity.entity_type, []).append(entity)
        expected_counts = {"LINE": 1, "TEXT": 1, "MTEXT": 1, "DIMENSION": 2}
        for entity_type, expected_count in expected_counts.items():
            if len(by_type.get(entity_type, [])) != expected_count:
                raise AssertionError(f"semantic read-back count mismatch for {entity_type}")

        text = by_type["TEXT"][0]
        mtext = by_type["MTEXT"][0]
        dimensions = by_type["DIMENSION"]
        if text.layer != "U1-ANNOT" or int(text.style["color_index"]) != 2:
            raise AssertionError("TEXT layer/color read-back mismatch")
        if text.geometry["text"] != "U1 TEXT" or float(text.geometry["height"]) != 250.0:
            raise AssertionError("TEXT geometry read-back mismatch")
        if mtext.layer != "U1-ANNOT" or int(mtext.style["color_index"]) != 3:
            raise AssertionError("MTEXT layer/color read-back mismatch")
        if mtext.geometry["text"] != "U1 MTEXT" or float(mtext.geometry["width"]) != 2200.0:
            raise AssertionError("MTEXT geometry/width read-back mismatch")
        dimension_types = {str(item.geometry["dimension_type"]): item for item in dimensions}
        if set(dimension_types) != {"AlignedDimension", "RotatedDimension"}:
            raise AssertionError("dimension subtype read-back mismatch")
        aligned = dimension_types["AlignedDimension"]
        rotated = dimension_types["RotatedDimension"]
        if aligned.layer != "U1-DIM" or int(aligned.style["color_index"]) != 4:
            raise AssertionError("aligned dimension layer/color read-back mismatch")
        if rotated.layer != "U1-DIM" or int(rotated.style["color_index"]) != 5:
            raise AssertionError("linear dimension layer/color read-back mismatch")
        if "xline1" not in aligned.geometry or "dim_line_point" not in aligned.geometry:
            raise AssertionError("aligned dimension defining geometry missing from semantic state")
        if abs(float(rotated.geometry["rotation"]) - math.pi / 2.0) > 1e-9:
            raise AssertionError("rotated dimension angle read-back mismatch")

        _retry(lambda: document.Save(), timeout=30.0)
        saved = bool(_retry(lambda: document.Saved))
        dbmod = int(_retry(lambda: document.GetVariable("DBMOD")))
        if not saved or dbmod != 0:
            raise AssertionError(f"document save did not reach persisted-clean state: Saved={saved} DBMOD={dbmod}")

        summary = {
            "schema_version": 1,
            "status": "PASS",
            "bridge_version": health["bridge_version"],
            "session_id": session_id,
            "runtime_document_id": runtime_document_id,
            "document_pid": document_pid,
            "bootstrap_readback_verified": bootstrap["readback_verified"],
            "bootstrap_scope": bootstrap["scope"],
            "created_entity_count": len(entities),
            "entity_types": {key: len(value) for key, value in sorted(by_type.items())},
            "batch_outcomes": [
                {"kind": spec["kind"], "outcome": receipt["outcome"]}
                for spec, receipt in zip(entities, receipts, strict=True)
            ],
            "post_document_fp": parent_fp,
            "pending_recoveries": 0,
            "saved": saved,
            "dbmod": dbmod,
            "persisted_clean": saved and dbmod == 0,
            "duration_seconds": round(time.perf_counter() - started, 3),
        }
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        progress_path.unlink(missing_ok=True)
        return summary
    finally:
        if document is not None:
            try:
                _retry(lambda: document.Close(False), timeout=10.0)
            except Exception:
                pass
        if fixture_path is not None:
            try:
                fixture_path.unlink(missing_ok=True)
            except OSError:
                pass
        pythoncom.CoUninitialize()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run U1 floorplan hardening live acceptance in AutoCAD Session 1")
    parser.add_argument("--output", required=True)
    parser.add_argument("--com-progid", default="AutoCAD.Application.26")
    args = parser.parse_args()
    try:
        summary = _run(args)
    except Exception as exc:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "status": "ERROR",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "traceback": traceback.format_exc(),
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        raise
    print(json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    main()
