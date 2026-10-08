"""AutoCAD command presets — load the audited bounded setup-command registry.
Wing: code | Topic: production-findings | Updated: 2026-09-11 17:20
"""

from __future__ import annotations

import json
from importlib import resources
from pathlib import Path
from typing import Any


class CommandPresetError(RuntimeError):
    """Raised when the packaged command-preset registry is missing or malformed."""


def _registry_text() -> str:
    source = Path(__file__).resolve().parents[2] / "config" / "autocad_command_presets.json"
    if source.is_file():
        return source.read_text(encoding="utf-8")
    packaged = resources.files("cdt_autocad").joinpath("config").joinpath(
        "autocad_command_presets.json"
    )
    return packaged.read_text(encoding="utf-8")


def load_command_presets() -> dict[str, Any]:
    """Load and minimally validate the immutable product command-preset registry."""
    raw = json.loads(_registry_text())
    if raw.get("schema_version") != 1:
        raise CommandPresetError("unsupported AutoCAD command-preset schema")
    defaults = raw.get("workflow_defaults")
    views = raw.get("view_presets")
    commands = raw.get("bounded_command_presets")
    if not isinstance(defaults, dict) or not isinstance(views, dict) or not isinstance(commands, dict):
        raise CommandPresetError("AutoCAD command-preset registry is incomplete")
    if defaults.get("step_delay_ms") != 300:
        raise CommandPresetError("3D/2D step pacing preset must remain 300 ms")
    for name, vector in views.items():
        if not isinstance(name, str) or not isinstance(vector, list) or len(vector) != 3:
            raise CommandPresetError("view preset entries must be named XYZ vectors")
    for name, row in commands.items():
        if not isinstance(name, str) or not isinstance(row, dict):
            raise CommandPresetError("bounded command presets must be named objects")
        command = row.get("command")
        if not isinstance(command, str) or not command.endswith("\n"):
            raise CommandPresetError(f"bounded command preset is invalid: {name}")
    return raw


_REGISTRY = load_command_presets()
WORKFLOW_DEFAULTS: dict[str, Any] = dict(_REGISTRY["workflow_defaults"])
VIEW_PRESETS: dict[str, tuple[float, float, float]] = {
    name: (float(vector[0]), float(vector[1]), float(vector[2]))
    for name, vector in _REGISTRY["view_presets"].items()
}
BOUNDED_COMMAND_PRESETS: dict[str, str] = {
    name: str(row["command"]) for name, row in _REGISTRY["bounded_command_presets"].items()
}


def visual_style_command(style: str) -> str:
    """Return one fixed VSCURRENT preset; caller-provided command text is never accepted."""
    key = f"visual.{str(style).strip().lower()}"
    try:
        return BOUNDED_COMMAND_PRESETS[key]
    except KeyError as exc:
        allowed = sorted(name.removeprefix("visual.") for name in BOUNDED_COMMAND_PRESETS if name.startswith("visual."))
        raise ValueError("unsupported visual style; allowed: " + ", ".join(allowed)) from exc
