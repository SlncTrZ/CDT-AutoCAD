"""Semantic state chain — parent guards and deterministic verified-step receipts.
Wing: code | Topic: semantic-state-n1 | Updated: 2026-09-10 13:42
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from cdt_autocad.errors import StateConflictError

from .canonical import ToleranceProfile
from .fingerprint import (
    fingerprint_action,
    fingerprint_delta,
    fingerprint_document,
    fingerprint_step,
)
from .models import ActionSpec, MutationOutcome, SemanticDelta, StateChainEntry


def verify_parent_state(expected_parent_fp: str, actual_parent_fp: str) -> None:
    """Fail closed when the live state no longer matches the expected predecessor."""

    if expected_parent_fp != actual_parent_fp:
        raise StateConflictError(
            "STATE_DRIFT: current semantic fingerprint does not match expected parent state"
        )


def _entry_step_payload(entry: StateChainEntry) -> dict[str, Any]:
    return {
        "schema_version": entry.schema_version,
        "step_id": entry.step_id,
        "parent_step_fp": entry.parent_step_fp,
        "parent_state_fp": entry.parent_state_fp,
        "action_fp": entry.action_fp,
        "delta_fp": entry.delta_fp,
        "post_state_fp": entry.post_state_fp,
        "artifact_fp": entry.artifact_fp,
        "profile_signature": entry.profile_signature,
        "status": entry.status.value,
    }


def verify_state_chain(
    entries: Sequence[StateChainEntry], profile: ToleranceProfile | None = None
) -> str | None:
    """Verify chain links and each tamper-evident step digest."""

    if not entries:
        return None

    selected = profile or ToleranceProfile()
    expected_profile_signature = selected.signature()
    previous: StateChainEntry | None = None
    for entry in entries:
        if entry.status is not MutationOutcome.COMMITTED_VERIFIED:
            raise StateConflictError(
                f"STATE_CHAIN_BROKEN: step {entry.step_id} is not COMMITTED_VERIFIED"
            )
        if entry.profile_signature != expected_profile_signature:
            raise StateConflictError(
                f"STATE_CHAIN_BROKEN: step {entry.step_id} fingerprint profile mismatch"
            )
        expected_step_fp = fingerprint_step(_entry_step_payload(entry), selected)
        if entry.step_fp != expected_step_fp:
            raise StateConflictError(
                f"STATE_CHAIN_BROKEN: step {entry.step_id} fingerprint mismatch"
            )
        if previous is None:
            if entry.parent_step_fp is not None:
                raise StateConflictError(
                    f"STATE_CHAIN_BROKEN: first step {entry.step_id} has a parent_step_fp"
                )
        else:
            if entry.parent_step_fp != previous.step_fp:
                raise StateConflictError(
                    f"STATE_CHAIN_BROKEN: step {entry.step_id} parent_step_fp mismatch"
                )
            if entry.parent_state_fp != previous.post_state_fp:
                raise StateConflictError(
                    f"STATE_CHAIN_BROKEN: step {entry.step_id} parent_state_fp mismatch"
                )
        previous = entry

    return entries[-1].post_state_fp


def build_state_chain_entry(
    *,
    step_id: str,
    parent_step_fp: str | None,
    parent_state_fp: str,
    action: ActionSpec,
    delta: SemanticDelta,
    post_state: Any,
    profile: ToleranceProfile | None = None,
    artifact_fp: str | None = None,
) -> StateChainEntry:
    """Build one COMMITTED_VERIFIED chain entry from deterministic semantic evidence."""

    selected = profile or ToleranceProfile()
    verify_parent_state(action.expected_parent_fp, parent_state_fp)

    action_fp = fingerprint_action(action, selected)
    delta_fp = fingerprint_delta(delta, selected)
    post_state_fp = fingerprint_document(post_state, selected)
    profile_signature = selected.signature()
    chain_payload = {
        "schema_version": 1,
        "step_id": step_id,
        "parent_step_fp": parent_step_fp,
        "parent_state_fp": parent_state_fp,
        "action_fp": action_fp,
        "delta_fp": delta_fp,
        "post_state_fp": post_state_fp,
        "artifact_fp": artifact_fp,
        "profile_signature": profile_signature,
        "status": MutationOutcome.COMMITTED_VERIFIED.value,
    }
    step_fp = fingerprint_step(chain_payload, selected)

    return StateChainEntry(
        step_id=step_id,
        parent_step_fp=parent_step_fp,
        parent_state_fp=parent_state_fp,
        action_fp=action_fp,
        delta_fp=delta_fp,
        post_state_fp=post_state_fp,
        artifact_fp=artifact_fp,
        step_fp=step_fp,
        status=MutationOutcome.COMMITTED_VERIFIED,
        profile_signature=profile_signature,
    )
