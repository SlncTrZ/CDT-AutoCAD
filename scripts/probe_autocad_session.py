"""AutoCAD session probe — record whether the current Windows session can see the COM ROT.
Wing: ops | Topic: session1-probe | Updated: 2026-09-12 19:48
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import sys
from pathlib import Path


def _session_id() -> int:
    value = ctypes.c_uint32()
    if not ctypes.windll.kernel32.ProcessIdToSessionId(os.getpid(), ctypes.byref(value)):
        raise RuntimeError("unable to resolve current Windows session")
    return int(value.value)


def main() -> None:
    parser = argparse.ArgumentParser(description="Probe current-session AutoCAD COM visibility")
    parser.add_argument("--output", required=True)
    parser.add_argument("--com-progid", default="AutoCAD.Application.26")
    args = parser.parse_args()
    if sys.platform != "win32":
        raise SystemExit("session probe requires Windows")

    import win32com.client
    import win32process

    result: dict[str, object] = {
        "schema_version": 1,
        "session_id": _session_id(),
        "process_id": os.getpid(),
        "com_progid": args.com_progid,
        "autocad_visible": False,
    }
    try:
        app = win32com.client.GetActiveObject(args.com_progid)
        hwnd = int(app.HWND)
        _thread_id, acad_pid = win32process.GetWindowThreadProcessId(hwnd)
        result.update(
            {
                "autocad_visible": True,
                "autocad_version": str(app.Version),
                "autocad_hwnd": hwnd,
                "autocad_process_id": int(acad_pid),
            }
        )
    except Exception as exc:
        result["error_type"] = type(exc).__name__
        result["error"] = str(exc)[:500]

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
