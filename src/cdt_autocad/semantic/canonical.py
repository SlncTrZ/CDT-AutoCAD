"""Canonicalization — versioned tolerance-aware deterministic semantic serialization.
Wing: code | Topic: semantic-state-n1 | Updated: 2026-09-10 13:35
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass, fields, is_dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from enum import Enum
from pathlib import PurePath
from typing import Any
from uuid import UUID


class CanonicalizationError(ValueError):
    """Raised when a value cannot be represented safely in canonical semantic form."""


@dataclass(frozen=True)
class ToleranceProfile:
    """Versioned quantization profile used before semantic hashing."""

    profile_id: str = "cad-default-v1"
    linear_quantum: float = 1e-6
    angular_quantum: float = 1e-9
    scalar_quantum: float = 1e-9
    schema_version: int = 1

    def __post_init__(self) -> None:
        if not self.profile_id.strip():
            raise ValueError("profile_id must be non-empty")
        for name in ("linear_quantum", "angular_quantum", "scalar_quantum"):
            value = getattr(self, name)
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be a positive finite number")
        if self.schema_version != 1:
            raise ValueError("unsupported tolerance profile schema_version")

    def quantum(self, kind: str) -> Decimal:
        value = {
            "linear": self.linear_quantum,
            "angular": self.angular_quantum,
            "scalar": self.scalar_quantum,
        }.get(kind)
        if value is None:
            raise ValueError("float_kind must be one of: linear, angular, scalar")
        return Decimal(str(value))

    def signature(self) -> str:
        return "|".join(
            (
                f"v{self.schema_version}",
                self.profile_id,
                f"linear={Decimal(str(self.linear_quantum))}",
                f"angular={Decimal(str(self.angular_quantum))}",
                f"scalar={Decimal(str(self.scalar_quantum))}",
            )
        )


def _canonical_number(value: int | float | Decimal, quantum: Decimal) -> dict[str, str]:
    if isinstance(value, float) and not math.isfinite(value):
        raise CanonicalizationError("semantic numbers must be finite")
    try:
        decimal_value = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise CanonicalizationError(f"invalid semantic number: {value!r}") from exc
    if not decimal_value.is_finite():
        raise CanonicalizationError("semantic numbers must be finite")

    quantized = decimal_value.quantize(quantum, rounding=ROUND_HALF_EVEN)
    if quantized == 0:
        quantized = abs(quantized)

    places = max(0, -quantum.as_tuple().exponent)
    rendered = f"{quantized:.{places}f}" if places else f"{quantized:.0f}"
    return {"$number": rendered}


def _sort_key(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def canonicalize(
    value: Any,
    profile: ToleranceProfile | None = None,
    *,
    float_kind: str = "linear",
) -> Any:
    """Convert semantic data into a deterministic JSON-safe representation."""

    selected = profile or ToleranceProfile()
    quantum = selected.quantum(float_kind)

    def visit(item: Any) -> Any:
        if item is None or isinstance(item, bool | str):
            return item
        if isinstance(item, int | float | Decimal):
            return _canonical_number(item, quantum)
        if isinstance(item, Enum):
            return visit(item.value)
        if isinstance(item, UUID | PurePath):
            return str(item)
        if isinstance(item, datetime):
            if item.tzinfo is None:
                raise CanonicalizationError("datetime values must be timezone-aware")
            return {"$datetime": item.isoformat()}
        if isinstance(item, date):
            return {"$date": item.isoformat()}
        if isinstance(item, bytes):
            return {"$bytes": item.hex()}
        if is_dataclass(item) and not isinstance(item, type):
            return {field.name: visit(getattr(item, field.name)) for field in fields(item)}
        if isinstance(item, Mapping):
            result: dict[str, Any] = {}
            for key, child in item.items():
                if not isinstance(key, str):
                    raise CanonicalizationError("semantic mapping keys must be strings")
                result[key] = visit(child)
            return result
        if isinstance(item, tuple | list):
            return [visit(child) for child in item]
        if isinstance(item, set | frozenset):
            normalized = [visit(child) for child in item]
            return sorted(normalized, key=_sort_key)
        raise CanonicalizationError(f"unsupported semantic value type: {type(item).__name__}")

    return visit(value)


def canonical_json(
    value: Any,
    profile: ToleranceProfile | None = None,
    *,
    float_kind: str = "linear",
) -> str:
    """Return deterministic compact JSON used as the fingerprint payload."""

    normalized = canonicalize(value, profile, float_kind=float_kind)
    return json.dumps(
        normalized,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
