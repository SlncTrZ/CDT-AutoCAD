"""N6 semantic executor — deterministic delta validation and append-only state-chain orchestration.
Wing: code | Topic: semantic-state-n6 | Updated: 2026-09-10 14:45
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn

from cdt_autocad.semantic.canonical import ToleranceProfile
from cdt_autocad.semantic.delta import compute_semantic_delta, validate_action_delta
from cdt_autocad.semantic.models import (
    ActionSpec,
    MutationOutcome,
    SemanticDelta,
    SemanticSnapshot,
    StateChainEntry,
    ValidationResult,
    ValidationStatus,
)
from cdt_autocad.semantic.state_chain import build_state_chain_entry, verify_state_chain

from .client import BridgeRemoteError
from .protocol import BridgeProtocolError, LineCreateParams, LineTargetParams


class SemanticStepError(RuntimeError):
    """Fail-closed semantic step refusal with one stable machine-readable code."""

    def __init__(self, code: str, message: str, *, detail: Any = None):
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True)
class SemanticStepResult:
    """One N6 orchestration result; only committed steps carry a chain entry."""

    outcome: MutationOutcome
    action: ActionSpec
    before: SemanticSnapshot
    after: SemanticSnapshot
    delta: SemanticDelta | None
    validation: ValidationResult | None
    chain_entry: StateChainEntry | None
    native_receipt: dict[str, Any]


class JsonlStateJournal:
    """Append-only per-run evidence journal; N6 intentionally does not infer resume state."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        if self.path.exists() and self.path.stat().st_size > 0:
            raise ValueError("state journal already exists and is non-empty; explicit resume is not supported")
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, event: dict[str, Any]) -> None:
        encoded = json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        with self.path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(encoded + "\n")
            stream.flush()
            os.fsync(stream.fileno())


class NativeSemanticExecutor:
    """Wrap the N5 LINE bridge surface with N6 semantic validation and chain continuity."""

    _OPERATIONS = frozenset(
        {"entity.create.line", "entity.update.line", "entity.delete.line"}
    )
    _VERIFIED_PREWRITE_REMOTE_CODES = frozenset(
        {
            "STATE_DRIFT",
            "SNAPSHOT_CAPACITY_EXCEEDED",
            "ENTITY_NOT_FOUND",
            "ENTITY_TYPE_MISMATCH",
        }
    )

    def __init__(
        self,
        client: Any,
        journal_path: str | Path,
        *,
        profile: ToleranceProfile | None = None,
    ):
        self.client = client
        self.profile = profile or ToleranceProfile()
        self.journal = JsonlStateJournal(journal_path)
        self._entries: list[StateChainEntry] = []
        self.blocked_reason: str | None = None

    @property
    def entries(self) -> tuple[StateChainEntry, ...]:
        return tuple(self._entries)

    def execute_step(self, runtime_document_id: str, action: ActionSpec) -> SemanticStepResult:
        if self.blocked_reason is not None:
            raise SemanticStepError(
                self.blocked_reason,
                "semantic executor is blocked and requires explicit reconciliation",
            )
        self._validate_action_shape(runtime_document_id, action)

        before = self.client.document_snapshot(
            runtime_document_id,
            document_pid=action.document_pid,
        )
        actual_parent_fp = self._document_fp(before)
        expected_chain_parent = self._entries[-1].post_state_fp if self._entries else action.expected_parent_fp
        if actual_parent_fp != expected_chain_parent or actual_parent_fp != action.expected_parent_fp:
            self.blocked_reason = "STATE_DRIFT"
            self._append_or_block(
                {
                    "event": "state_drift",
                    "action": action.to_dict(),
                    "expected_parent_fp": action.expected_parent_fp,
                    "expected_chain_parent_fp": expected_chain_parent,
                    "actual_parent_fp": actual_parent_fp,
                }
            )
            raise SemanticStepError(
                "STATE_DRIFT",
                "current semantic fingerprint does not match the expected predecessor",
            )

        try:
            native_receipt = self._dispatch(runtime_document_id, action)
        except BridgeRemoteError as exc:
            if exc.code in self._VERIFIED_PREWRITE_REMOTE_CODES:
                if exc.code == "STATE_DRIFT":
                    self.blocked_reason = "STATE_DRIFT"
                    event = "state_drift"
                else:
                    event = "native_refusal"
                self._append_or_block(
                    {
                        "event": event,
                        "action": action.to_dict(),
                        "expected_parent_fp": action.expected_parent_fp,
                        "actual_parent_fp": actual_parent_fp,
                        "native_error": {"code": exc.code, "message": exc.remote_message},
                    }
                )
                raise SemanticStepError(exc.code, exc.remote_message) from exc
            self._raise_uncertain_after_dispatch(
                runtime_document_id,
                action,
                before,
                cause_code=exc.code,
                reason=f"native bridge returned {exc.code} after mutation dispatch began",
                native_error={"code": exc.code, "message": exc.remote_message},
            )
        except Exception as exc:
            self._raise_uncertain_after_dispatch(
                runtime_document_id,
                action,
                before,
                cause_code="NATIVE_COMPLETION_UNKNOWN",
                reason="native mutation dispatch failed after completion became unknown",
                native_error={"type": type(exc).__name__, "message": str(exc)},
            )

        try:
            self._validate_receipt_identity(action, native_receipt, actual_parent_fp)
            outcome = self._receipt_outcome(native_receipt)
        except SemanticStepError as exc:
            self._raise_uncertain_after_dispatch(
                runtime_document_id,
                action,
                before,
                cause_code=exc.code,
                reason=str(exc),
                native_receipt=native_receipt,
            )

        try:
            after = self.client.document_snapshot(
                runtime_document_id,
                document_pid=action.document_pid,
            )
        except Exception as exc:
            self._raise_uncertain_after_dispatch(
                runtime_document_id,
                action,
                before,
                cause_code="READBACK_FAILED",
                reason="independent semantic read-back failed after native mutation dispatch",
                native_receipt=native_receipt,
                native_error={"type": type(exc).__name__, "message": str(exc)},
                attempt_readback=False,
            )
        after_fp = self._document_fp(after)

        if outcome is MutationOutcome.ROLLED_BACK_VERIFIED:
            if (
                after_fp != actual_parent_fp
                or native_receipt.get("post_document_fp") != after_fp
                or not self._rollback_receipt_matches(native_receipt, actual_parent_fp)
            ):
                return self._uncertain(
                    action,
                    before,
                    after,
                    native_receipt,
                    "rollback receipt/read-back does not restore the exact predecessor fingerprint",
                )
            self._append_or_block(
                {
                    "event": "rollback",
                    "action": action.to_dict(),
                    "parent_state_fp": actual_parent_fp,
                    "native_receipt": native_receipt,
                }
            )
            return SemanticStepResult(
                outcome=MutationOutcome.ROLLED_BACK_VERIFIED,
                action=action,
                before=before,
                after=after,
                delta=None,
                validation=None,
                chain_entry=None,
                native_receipt=native_receipt,
            )

        if outcome is not MutationOutcome.COMMITTED_VERIFIED:
            return self._uncertain(
                action,
                before,
                after,
                native_receipt,
                f"native mutation returned non-advancing outcome {outcome.value}",
            )
        if native_receipt.get("post_document_fp") != after_fp:
            return self._uncertain(
                action,
                before,
                after,
                native_receipt,
                "native post_document_fp does not match independent semantic read-back",
            )

        try:
            delta = compute_semantic_delta(before, after, self.profile)
            delta, validation = validate_action_delta(action, before, after, delta, self.profile)
            self._validate_committed_affected_pid(action, native_receipt, delta)
        except SemanticStepError as exc:
            self._raise_uncertain_after_dispatch(
                runtime_document_id,
                action,
                before,
                cause_code=exc.code,
                reason=str(exc),
                native_receipt=native_receipt,
                observed_state=after,
                attempt_readback=False,
            )
        except Exception as exc:
            self._raise_uncertain_after_dispatch(
                runtime_document_id,
                action,
                before,
                cause_code="VALIDATION_ENGINE_FAILED",
                reason="deterministic semantic validation failed after native commit",
                native_receipt=native_receipt,
                native_error={"type": type(exc).__name__, "message": str(exc)},
                observed_state=after,
                attempt_readback=False,
            )
        if validation.status is not ValidationStatus.PASS:
            self.blocked_reason = "STATE_UNCERTAIN"
            self._append_or_block(
                {
                    "event": "validation_failed",
                    "action": action.to_dict(),
                    "parent_state_fp": actual_parent_fp,
                    "post_state_fp": after_fp,
                    "native_receipt": native_receipt,
                    "delta": delta.to_dict(),
                    "validation": validation.to_dict(),
                },
                committed_state=True,
            )
            raise SemanticStepError(
                "VALIDATION_FAILED",
                "committed native mutation failed deterministic semantic validation; N7 recovery is not available",
                detail=validation,
            )

        parent_step_fp = self._entries[-1].step_fp if self._entries else None
        try:
            chain_entry = build_state_chain_entry(
                step_id=action.action_id,
                parent_step_fp=parent_step_fp,
                parent_state_fp=actual_parent_fp,
                action=action,
                delta=delta,
                post_state=after,
                profile=self.profile,
            )
            verify_state_chain((*self._entries, chain_entry), self.profile)
        except Exception as exc:
            self._raise_uncertain_after_dispatch(
                runtime_document_id,
                action,
                before,
                cause_code="STATE_CHAIN_FAILED",
                reason="state-chain construction or verification failed after native commit",
                native_receipt=native_receipt,
                native_error={"type": type(exc).__name__, "message": str(exc)},
                observed_state=after,
                attempt_readback=False,
            )
        self._append_or_block(
            {
                "event": "committed_step",
                "action": action.to_dict(),
                "native_receipt": native_receipt,
                "delta": delta.to_dict(),
                "validation": validation.to_dict(),
                "chain_entry": chain_entry.to_dict(),
            },
            committed_state=True,
        )
        self._entries.append(chain_entry)
        return SemanticStepResult(
            outcome=MutationOutcome.COMMITTED_VERIFIED,
            action=action,
            before=before,
            after=after,
            delta=delta,
            validation=validation,
            chain_entry=chain_entry,
            native_receipt=native_receipt,
        )

    def _dispatch(self, runtime_document_id: str, action: ActionSpec) -> dict[str, Any]:
        args = dict(action.args or {})
        common = {
            "document_pid": action.document_pid,
            "expected_parent_fp": action.expected_parent_fp,
        }
        if "fault_stage" in args:
            common["fault_stage"] = args["fault_stage"]

        if action.operation == "entity.create.line":
            return self.client.create_line(
                runtime_document_id,
                **common,
                start=tuple(args["start"]),
                end=tuple(args["end"]),
            )
        if action.operation == "entity.update.line":
            return self.client.update_line(
                runtime_document_id,
                **common,
                semantic_pid=action.semantic_pids[0],
                start=tuple(args["start"]),
                end=tuple(args["end"]),
            )
        return self.client.delete_line(
            runtime_document_id,
            **common,
            semantic_pid=action.semantic_pids[0],
        )

    def _validate_action_shape(self, runtime_document_id: str, action: ActionSpec) -> None:
        if action.operation not in self._OPERATIONS:
            raise SemanticStepError("INVALID_ACTION", "operation is not enabled by the N6 executor")
        args = dict(action.args or {})
        if action.operation == "entity.create.line":
            if action.semantic_pids:
                raise SemanticStepError("INVALID_ACTION", "create.line cannot target existing semantic PIDs")
            payload = {
                "runtime_document_id": runtime_document_id,
                "document_pid": action.document_pid,
                "expected_parent_fp": action.expected_parent_fp,
                **args,
            }
            try:
                LineCreateParams.from_dict(payload)
            except BridgeProtocolError as exc:
                raise SemanticStepError("INVALID_ACTION", exc.safe_message) from exc
            return

        if len(action.semantic_pids) != 1:
            raise SemanticStepError(
                "INVALID_ACTION",
                "update/delete LINE actions require exactly one target semantic PID",
            )
        payload = {
            "runtime_document_id": runtime_document_id,
            "document_pid": action.document_pid,
            "expected_parent_fp": action.expected_parent_fp,
            "semantic_pid": action.semantic_pids[0],
            **args,
        }
        try:
            LineTargetParams.from_dict(
                payload,
                require_geometry=action.operation == "entity.update.line",
            )
        except BridgeProtocolError as exc:
            raise SemanticStepError("INVALID_ACTION", exc.safe_message) from exc

    @staticmethod
    def _document_fp(snapshot: SemanticSnapshot) -> str:
        value = snapshot.fingerprints.document_fp
        if value is None:
            raise SemanticStepError("INVALID_SNAPSHOT", "semantic snapshot is missing document_fp")
        return value

    @staticmethod
    def _receipt_outcome(receipt: dict[str, Any]) -> MutationOutcome:
        try:
            return MutationOutcome(receipt.get("outcome"))
        except (TypeError, ValueError) as exc:
            raise SemanticStepError("INVALID_NATIVE_RECEIPT", "native receipt outcome is invalid") from exc

    @staticmethod
    def _validate_receipt_identity(
        action: ActionSpec,
        receipt: dict[str, Any],
        parent_fp: str,
    ) -> None:
        affected_pid = receipt.get("affected_semantic_pid")
        target_pid = action.semantic_pids[0] if len(action.semantic_pids) == 1 else None
        if (
            receipt.get("schema_version") != 1
            or receipt.get("operation") != action.operation
            or receipt.get("document_pid") != action.document_pid
            or receipt.get("pre_document_fp") != parent_fp
            or not isinstance(affected_pid, str)
            or not affected_pid.strip()
            or (target_pid is not None and affected_pid != target_pid)
        ):
            raise SemanticStepError(
                "INVALID_NATIVE_RECEIPT",
                "native mutation receipt does not match the requested operation/document/parent",
            )

    @staticmethod
    def _validate_committed_affected_pid(
        action: ActionSpec,
        receipt: dict[str, Any],
        delta: SemanticDelta,
    ) -> None:
        affected_pid = receipt.get("affected_semantic_pid")
        if action.operation == "entity.create.line":
            if len(delta.created) != 1 or affected_pid != delta.created[0]:
                raise SemanticStepError(
                    "INVALID_NATIVE_RECEIPT",
                    "created semantic PID in native receipt does not match independent delta",
                )
            return
        if len(action.semantic_pids) != 1 or affected_pid != action.semantic_pids[0]:
            raise SemanticStepError(
                "INVALID_NATIVE_RECEIPT",
                "affected semantic PID in native receipt does not match ActionSpec target",
            )

    @staticmethod
    def _rollback_receipt_matches(receipt: dict[str, Any], parent_fp: str) -> bool:
        rollback = receipt.get("rollback")
        return bool(
            isinstance(rollback, dict)
            and rollback.get("strategy") == "R0_ABORT"
            and rollback.get("status") == "ROLLED_BACK_VERIFIED"
            and rollback.get("expected_restore_fp") == parent_fp
            and rollback.get("actual_restore_fp") == parent_fp
        )

    def _uncertain(
        self,
        action: ActionSpec,
        before: SemanticSnapshot,
        after: SemanticSnapshot,
        native_receipt: dict[str, Any],
        reason: str,
    ) -> NoReturn:
        self.blocked_reason = "STATE_UNCERTAIN"
        self._append_or_block(
            {
                "event": "state_uncertain",
                "action": action.to_dict(),
                "parent_state_fp": self._document_fp(before),
                "actual_state_fp": self._document_fp(after),
                "native_receipt": native_receipt,
                "reason": reason,
            },
            committed_state=True,
        )
        raise SemanticStepError("STATE_UNCERTAIN", reason)

    def _raise_uncertain_after_dispatch(
        self,
        runtime_document_id: str,
        action: ActionSpec,
        before: SemanticSnapshot,
        *,
        cause_code: str,
        reason: str,
        native_receipt: dict[str, Any] | None = None,
        native_error: dict[str, Any] | None = None,
        observed_state: SemanticSnapshot | None = None,
        attempt_readback: bool = True,
    ) -> NoReturn:
        self.blocked_reason = "STATE_UNCERTAIN"
        event: dict[str, Any] = {
            "event": "state_uncertain",
            "action": action.to_dict(),
            "parent_state_fp": self._document_fp(before),
            "cause_code": cause_code,
            "reason": reason,
        }
        if native_receipt is not None:
            event["native_receipt"] = native_receipt
        if native_error is not None:
            event["native_error"] = native_error
        if observed_state is not None:
            event["actual_state_fp"] = self._document_fp(observed_state)
        if attempt_readback and observed_state is None:
            try:
                observed = self.client.document_snapshot(
                    runtime_document_id,
                    document_pid=action.document_pid,
                )
                event["actual_state_fp"] = self._document_fp(observed)
            except Exception as exc:
                event["readback_error"] = {
                    "type": type(exc).__name__,
                    "message": str(exc),
                }
        self._append_or_block(event, committed_state=True)
        raise SemanticStepError("STATE_UNCERTAIN", reason)

    def _append_or_block(self, event: dict[str, Any], *, committed_state: bool = False) -> None:
        try:
            self.journal.append(event)
        except Exception as exc:
            if committed_state:
                self.blocked_reason = "STATE_UNCERTAIN"
            elif self.blocked_reason is None:
                self.blocked_reason = "JOURNAL_FAILED"
            raise SemanticStepError(
                "STATE_UNCERTAIN" if committed_state else "JOURNAL_FAILED",
                "semantic evidence journal append failed",
            ) from exc
