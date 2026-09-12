"""MP-G07 visual-style acceptance — live round-trip and exact restoration on AutoCAD.
Wing: ops | Topic: visual-style-acceptance | Updated: 2026-09-12 20:10
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

from cdt_autocad import __version__
from cdt_autocad.backends.com_backend import ComBackend
from cdt_autocad.config import Settings
from cdt_autocad.contract_identity import CONTRACT_VERSION, PUBLIC_TOOL_COUNT, contract_hash
from cdt_autocad.native_bridge.public_runtime import NativePublicFacade

_STYLE_KEYS = {
    "2dwireframe": "2d_wireframe",
    "wireframe": "wireframe",
    "hidden": "hidden",
    "realistic": "realistic",
    "conceptual": "conceptual",
    "shadesofgray": "shades_of_gray",
    "shaded": "shaded",
    "shadedwithedges": "shaded_with_edges",
}


def _style_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value).casefold())


async def _read_state(
    backend: ComBackend,
    facade: NativePublicFacade,
) -> dict[str, Any]:
    visual_style = await asyncio.to_thread(facade.visual_style_get)

    def _sync() -> dict[str, Any]:
        doc = backend._doc()
        return {
            "visual_style": str(visual_style["visual_style_name"]),
            "visual_style_handle": str(visual_style["visual_style_handle"]),
            "cmdnames": str(doc.GetVariable("CMDNAMES") or "").strip(),
            "cmdactive": int(doc.GetVariable("CMDACTIVE")),
            "dbmod": int(doc.GetVariable("DBMOD")),
            "saved": bool(doc.Saved),
        }

    return await backend._run(_sync, may_mutate_document=False)


async def _close_document(backend: ComBackend, name: str) -> None:
    await backend._run(lambda: backend._app().Documents.Item(name).Close(False))


async def _run(args: argparse.Namespace) -> dict[str, Any]:
    if sys.platform != "win32":
        raise RuntimeError("MP-G07 visual-style acceptance requires Windows + live AutoCAD")
    settings = replace(
        Settings.from_env(),
        backend="com",
        com_attach_policy="attach_only",
        com_progid=args.com_progid,
        com_call_timeout_seconds=60.0,
    )
    backend = ComBackend(settings)
    facade = NativePublicFacade(settings)
    started = time.perf_counter()
    created_name: str | None = None
    try:
        created = await backend.document_new()
        created_name = str(created["name"])
        initial = await _read_state(backend, facade)
        initial_key = _STYLE_KEYS.get(_style_key(initial["visual_style"]))
        if initial_key is None:
            raise AssertionError(
                f"initial VSCURRENT is outside the audited visual-style allowlist: {initial['visual_style']!r}"
            )
        target_key = "shades_of_gray" if initial_key != "shades_of_gray" else "2d_wireframe"
        changed = await backend.view_set_visual_style(target_key)
        after_change = await _read_state(backend, facade)
        if changed.get("state_verified") is not True:
            raise AssertionError("provider did not mark target visual-style read-back verified")
        if _style_key(after_change["visual_style"]) != _style_key(target_key):
            raise AssertionError(
                f"target visual-style read-back mismatch: requested={target_key!r}; actual={after_change['visual_style']!r}"
            )
        if _style_key(changed.get("previous_visual_style")) != _style_key(initial["visual_style"]):
            raise AssertionError("provider did not capture the exact predecessor visual style")
        if after_change["cmdnames"] or after_change["cmdactive"] != 0:
            raise AssertionError("AutoCAD did not return idle after visual-style mutation")

        restored = await backend.view_set_visual_style(initial_key)
        final = await _read_state(backend, facade)
        if restored.get("state_verified") is not True:
            raise AssertionError("provider did not verify visual-style restoration")
        if _style_key(final["visual_style"]) != _style_key(initial["visual_style"]):
            raise AssertionError(
                f"visual-style restoration mismatch: expected={initial['visual_style']!r}; actual={final['visual_style']!r}"
            )
        if final["cmdnames"] or final["cmdactive"] != 0:
            raise AssertionError("AutoCAD did not return idle after visual-style restoration")
        if backend.status().get("integrity_uncertain"):
            raise AssertionError(
                f"backend integrity became uncertain: {backend.status().get('integrity_uncertain_reason')}"
            )

        summary = {
            "schema_version": 1,
            "status": "PASS",
            "finding_id": "MP-G07",
            "provider_version": __version__,
            "contract_version": CONTRACT_VERSION,
            "contract_hash": contract_hash(),
            "public_tool_count": PUBLIC_TOOL_COUNT,
            "com_progid": args.com_progid,
            "initial": initial,
            "target_key": target_key,
            "change_receipt": changed,
            "after_change": after_change,
            "restore_key": initial_key,
            "restore_receipt": restored,
            "final": final,
            "predecessor_restored": True,
            "command_idle_verified": True,
            "duration_seconds": round(time.perf_counter() - started, 3),
        }
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return summary
    finally:
        if created_name is not None:
            try:
                await _close_document(backend, created_name)
            except Exception:
                pass
        backend.shutdown()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run live MP-G07 visual-style round-trip acceptance")
    parser.add_argument("--output", required=True)
    parser.add_argument("--com-progid", default="AutoCAD.Application.26")
    args = parser.parse_args()
    summary = asyncio.run(_run(args))
    print(json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    main()
