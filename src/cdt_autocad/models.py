"""Normalized contract models shared by AutoCAD backends."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class Capability:
    supported: bool
    mode: str | None = None
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class EntityInfo:
    id: str
    type: str
    layer: str
    color: int
    linetype: str
    visible: bool
    properties: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class LayerInfo:
    name: str
    color: int
    linetype: str
    lineweight: int
    is_on: bool
    is_frozen: bool
    is_locked: bool
    is_current: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class BlockInfo:
    name: str
    base_point: tuple[float, float, float]
    entity_count: int
    attribute_count: int
    is_xref: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
