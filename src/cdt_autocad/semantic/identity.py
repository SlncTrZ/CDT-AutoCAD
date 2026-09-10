"""Semantic identity — PID uniqueness and duplicate geometry detection helpers.
Wing: code | Topic: semantic-state-n1 | Updated: 2026-09-10 13:50
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable

from cdt_autocad.errors import StateConflictError

from .models import EntitySemanticState


def duplicate_pids(entities: Iterable[EntitySemanticState]) -> tuple[str, ...]:
    """Return duplicate semantic PIDs in deterministic order."""

    counts = Counter(entity.semantic_pid for entity in entities)
    return tuple(sorted(pid for pid, count in counts.items() if count > 1))


def assert_unique_pids(entities: Iterable[EntitySemanticState]) -> None:
    """Fail closed when one semantic PID resolves to more than one entity instance."""

    duplicates = duplicate_pids(tuple(entities))
    if duplicates:
        joined = ", ".join(duplicates)
        raise StateConflictError(f"DUPLICATE_PID: duplicate semantic PID(s): {joined}")


def duplicate_geometry_groups(
    entities: Iterable[EntitySemanticState],
) -> dict[str, tuple[str, ...]]:
    """Group distinct semantic PIDs that currently share an identical geometry fingerprint."""

    grouped: dict[str, list[str]] = defaultdict(list)
    for entity in entities:
        geometry_fp = entity.fingerprints.geometry_fp
        if geometry_fp is None:
            continue
        grouped[geometry_fp].append(entity.semantic_pid)

    return {
        geometry_fp: tuple(sorted(pids))
        for geometry_fp, pids in sorted(grouped.items())
        if len(pids) > 1
    }
