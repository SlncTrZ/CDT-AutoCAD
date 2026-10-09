"""Backend exports are lazy so the remote control process never imports COM."""
from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = ["ComBackend", "EzdxfBackend"]


def __getattr__(name: str) -> Any:
    modules = {"ComBackend": ".com_backend", "EzdxfBackend": ".ezdxf_backend"}
    if name not in modules:
        raise AttributeError(name)
    value = getattr(import_module(modules[name], __name__), name)
    globals()[name] = value
    return value
