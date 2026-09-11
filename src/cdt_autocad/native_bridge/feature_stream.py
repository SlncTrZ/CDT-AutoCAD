"""Production feature streaming — feature-local atomic orchestration over generic native CAD chunks.
Wing: code | Topic: production-feature-streaming | Updated: 2026-09-11 20:18
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .logical_batch import LogicalBatchError, NativeLogicalBatchExecutor, _is_fingerprint
from .protocol import MAX_BATCH_CHUNK_ENTITIES, MAX_LOGICAL_BATCH_ITEMS

DEFAULT_FEATURE_PACING_MS = 300
MAX_FEATURE_ACTIONS = 128
_IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


class FeatureExecutionError(RuntimeError):
    """Fail-closed feature-streaming error with a stable machine-readable code."""

    def __init__(self, code: str, message: str, *, detail: Any = None):
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True)
class FeatureRequest:
    feature_id: str
    feature_sequence: int
    correlation_id: str
    actions: tuple[dict[str, Any], ...]
    total_items: int
    recommended_next_delay_ms: int = DEFAULT_FEATURE_PACING_MS


@dataclass(frozen=True)
class FeatureExecutionResult:
    status: str
    feature_id: str
    feature_sequence: int
    correlation_id: str
    document_pid: str
    runtime_document_id: str
    initial_fp: str
    final_fp: str
    action_count: int
    native_chunk_count: int
    affected_semantic_pids: tuple[str, ...]
    recommended_next_delay_ms: int
    failure: dict[str, Any] | None
    native_receipts: tuple[dict[str, Any], ...]


def validate_feature_request(
    *,
    feature_id: str,
    feature_sequence: int,
    correlation_id: str,
    actions: Sequence[Mapping[str, Any]],
) -> FeatureRequest:
    """Validate one domain-agnostic feature plan before any native side effect."""

    _require_identifier(feature_id, "feature_id")
    _require_identifier(correlation_id, "correlation_id")
    if isinstance(feature_sequence, bool) or not isinstance(feature_sequence, int) or not 0 <= feature_sequence <= 2_147_483_647:
        raise FeatureExecutionError(
            "INVALID_FEATURE_SEQUENCE",
            "feature_sequence must be an integer between 0 and 2147483647",
        )
    if not isinstance(actions, Sequence) or isinstance(actions, (str, bytes)):
        raise FeatureExecutionError("INVALID_FEATURE_ACTIONS", "actions must be a sequence")
    if not 1 <= len(actions) <= MAX_FEATURE_ACTIONS:
        raise FeatureExecutionError(
            "INVALID_FEATURE_ACTIONS",
            f"feature must contain 1..{MAX_FEATURE_ACTIONS} actions",
        )

    normalized: list[dict[str, Any]] = []
    total_items = 0
    for index, raw_action in enumerate(actions):
        if not isinstance(raw_action, Mapping):
            raise FeatureExecutionError(
                "INVALID_FEATURE_ACTION",
                f"feature action {index} must be an object",
            )
        action = dict(raw_action)
        operation = action.get("operation")
        if operation == "create_entities":
            _require_exact_fields(action, {"operation", "entities"}, index)
            items = _require_item_sequence(action.get("entities"), "entities", index)
        elif operation == "insert_blocks":
            _require_exact_fields(action, {"operation", "inserts"}, index)
            items = _require_item_sequence(action.get("inserts"), "inserts", index)
        elif operation == "transform_entities":
            _require_exact_fields(action, {"operation", "semantic_pids", "transform"}, index)
            items = _require_string_sequence(action.get("semantic_pids"), "semantic_pids", index)
            if not isinstance(action.get("transform"), Mapping):
                raise FeatureExecutionError(
                    "INVALID_FEATURE_ACTION",
                    f"feature action {index} transform must be an object",
                )
        else:
            raise FeatureExecutionError(
                "INVALID_FEATURE_ACTION",
                f"unsupported feature action at index {index}: {operation!r}",
            )
        if not items:
            raise FeatureExecutionError(
                "INVALID_FEATURE_ACTION",
                f"feature action {index} must contain at least one item",
            )
        total_items += len(items)
        normalized.append(action)

    if total_items > MAX_LOGICAL_BATCH_ITEMS:
        raise FeatureExecutionError(
            "FEATURE_TOO_LARGE",
            f"feature contains {total_items} items; maximum is {MAX_LOGICAL_BATCH_ITEMS}",
        )
    return FeatureRequest(
        feature_id=feature_id,
        feature_sequence=feature_sequence,
        correlation_id=correlation_id,
        actions=tuple(normalized),
        total_items=total_items,
    )


class FeatureStreamExecutor(NativeLogicalBatchExecutor):
    """Execute one complete generic feature behind exactly one logical predecessor checkpoint.

    Domain meaning stays outside the provider. The executor only understands generic CAD action
    families and feature correlation metadata. Earlier feature calls remain committed when this
    feature fails because recovery is scoped to this call's predecessor checkpoint.
    """

    def execute(
        self,
        runtime_document_id: str,
        *,
        document_pid: str,
        expected_parent_fp: str,
        feature_id: str,
        feature_sequence: int,
        correlation_id: str,
        actions: Sequence[Mapping[str, Any]],
    ) -> FeatureExecutionResult:
        request = validate_feature_request(
            feature_id=feature_id,
            feature_sequence=feature_sequence,
            correlation_id=correlation_id,
            actions=actions,
        )
        if not _is_fingerprint(expected_parent_fp):
            raise FeatureExecutionError(
                "INVALID_PARENT_FP",
                "expected_parent_fp must be a canonical sha256 fingerprint",
            )

        try:
            begin = self.client.begin_logical_batch(
                runtime_document_id,
                document_pid=document_pid,
                expected_parent_fp=expected_parent_fp,
            )
            binding = self._validate_begin(begin, document_pid, expected_parent_fp)
        except Exception as exc:
            binding = self._discover_logical_binding(document_pid, expected_parent_fp)
            if binding is None:
                self.journal.append(
                    self._event(
                        request,
                        "feature_uncertain",
                        phase="begin",
                        cause=f"{type(exc).__name__}: {exc}",
                    )
                )
                raise FeatureExecutionError(
                    "FEATURE_BEGIN_UNKNOWN",
                    "feature checkpoint completion is unknown and no unique predecessor checkpoint can be recovered",
                ) from exc
            logical = self._rollback(
                runtime_document_id,
                document_pid=document_pid,
                operation="feature.execute",
                expected_parent_fp=expected_parent_fp,
                binding=binding,
                receipts=(),
                affected=(),
                committed_chunks=0,
                cause="feature begin completion became unknown",
            )
            failure = self._failure(
                request,
                action_index=None,
                native_chunk_index=None,
                operation=None,
                cause="feature begin completion became unknown",
            )
            self.journal.append(self._event(request, "feature_rollback", failure=failure))
            return self._from_logical(request, logical, failure=failure)

        binding_payload = binding.to_dict()
        self.journal.append(
            self._event(
                request,
                "feature_begin",
                document_pid=document_pid,
                runtime_document_id=runtime_document_id,
                initial_fp=expected_parent_fp,
                action_count=len(request.actions),
                total_items=request.total_items,
                logical_transaction=binding_payload,
            )
        )

        current_fp = expected_parent_fp
        receipts: list[dict[str, Any]] = []
        affected_ordered: list[str] = []
        affected_seen: set[str] = set()
        created_seen: set[str] = set()
        native_chunk_index = 0
        current_action_index: int | None = None
        current_operation: str | None = None

        try:
            for action_index, action in enumerate(request.actions):
                current_action_index = action_index
                public_operation = str(action["operation"])
                current_operation = public_operation
                items = self._action_items(action)
                for action_chunk_index, offset in enumerate(
                    range(0, len(items), MAX_BATCH_CHUNK_ENTITIES)
                ):
                    chunk = items[offset : offset + MAX_BATCH_CHUNK_ENTITIES]
                    native_operation, receipt = self._dispatch_feature_chunk(
                        runtime_document_id,
                        document_pid=document_pid,
                        parent_fp=current_fp,
                        action=action,
                        chunk=chunk,
                        binding=binding_payload,
                    )
                    self._validate_chunk_receipt(
                        receipt,
                        operation=native_operation,
                        document_pid=document_pid,
                        expected_pre_fp=current_fp,
                        binding=binding,
                    )
                    receipts.append(dict(receipt))
                    outcome = receipt.get("outcome")
                    if outcome != "COMMITTED_VERIFIED":
                        logical = self._rollback(
                            runtime_document_id,
                            document_pid=document_pid,
                            operation="feature.execute",
                            expected_parent_fp=expected_parent_fp,
                            binding=binding,
                            receipts=receipts,
                            affected=affected_ordered,
                            committed_chunks=native_chunk_index,
                            cause=(
                                f"feature action {action_index} native chunk {action_chunk_index} "
                                f"returned {outcome}"
                            ),
                        )
                        failure = self._failure(
                            request,
                            action_index=action_index,
                            native_chunk_index=native_chunk_index,
                            operation=public_operation,
                            cause=f"native chunk returned {outcome}",
                            native_outcome=str(outcome),
                        )
                        self.journal.append(
                            self._event(request, "feature_rollback", failure=failure)
                        )
                        return self._from_logical(request, logical, failure=failure)

                    pids = receipt.get("affected_semantic_pids")
                    self._validate_feature_pids(
                        public_operation,
                        chunk,
                        pids,
                        created_seen=created_seen,
                    )
                    assert isinstance(pids, list)
                    for pid in pids:
                        if pid not in affected_seen:
                            affected_seen.add(pid)
                            affected_ordered.append(pid)
                    post_fp = str(receipt["post_document_fp"])
                    self.journal.append(
                        self._event(
                            request,
                            "feature_native_chunk_committed",
                            action_index=action_index,
                            action_chunk_index=action_chunk_index,
                            native_chunk_index=native_chunk_index,
                            operation=public_operation,
                            native_operation=native_operation,
                            pre_document_fp=current_fp,
                            post_document_fp=post_fp,
                            affected_semantic_pids=pids,
                        )
                    )
                    current_fp = post_fp
                    native_chunk_index += 1

            independent = self.client.document_state(
                runtime_document_id,
                document_pid=document_pid,
            )
            if independent.get("document_fp") != current_fp:
                raise FeatureExecutionError(
                    "FEATURE_READBACK_MISMATCH",
                    "independent compact state differs from the feature chunk fingerprint chain",
                    detail=independent,
                )

            self.journal.append(
                self._event(
                    request,
                    "feature_finalize_pending",
                    final_fp=current_fp,
                    native_chunk_count=native_chunk_index,
                    checkpoint_id=binding.checkpoint_id,
                )
            )
            try:
                finalized = self.client.finalize_recovery(
                    runtime_document_id,
                    document_pid=document_pid,
                    checkpoint_id=binding.checkpoint_id,
                    checkpoint_artifact_fp=binding.checkpoint_artifact_fp,
                    accepted_post_fp=current_fp,
                )
            except Exception as exc:
                if not self._logical_checkpoint_present(binding, document_pid, expected_parent_fp):
                    reconciled = self.client.document_state(
                        runtime_document_id,
                        document_pid=document_pid,
                    )
                    if reconciled.get("document_fp") == current_fp:
                        self.journal.append(
                            self._event(
                                request,
                                "feature_commit_reconciled",
                                final_fp=current_fp,
                                native_chunk_count=native_chunk_index,
                                cause="finalize response lost after checkpoint disappearance",
                            )
                        )
                        return self._committed_feature(
                            request,
                            document_pid=document_pid,
                            runtime_document_id=runtime_document_id,
                            initial_fp=expected_parent_fp,
                            final_fp=current_fp,
                            native_chunk_count=native_chunk_index,
                            affected=affected_ordered,
                            receipts=receipts,
                        )
                raise FeatureExecutionError(
                    "FEATURE_FINALIZE_UNKNOWN",
                    "feature checkpoint finalization could not be reconciled",
                ) from exc
            if finalized.get("status") != "FINALIZED":
                raise FeatureExecutionError(
                    "FEATURE_FINALIZE_FAILED",
                    "bridge did not confirm feature checkpoint finalization",
                    detail=finalized,
                )

            self.journal.append(
                self._event(
                    request,
                    "feature_commit",
                    final_fp=current_fp,
                    native_chunk_count=native_chunk_index,
                    affected_semantic_pids=affected_ordered,
                )
            )
            return self._committed_feature(
                request,
                document_pid=document_pid,
                runtime_document_id=runtime_document_id,
                initial_fp=expected_parent_fp,
                final_fp=current_fp,
                native_chunk_count=native_chunk_index,
                affected=affected_ordered,
                receipts=receipts,
            )
        except (FeatureExecutionError, LogicalBatchError) as exc:
            cause = str(exc)
        except Exception as exc:
            cause = f"{type(exc).__name__}: {exc}"

        logical = self._rollback(
            runtime_document_id,
            document_pid=document_pid,
            operation="feature.execute",
            expected_parent_fp=expected_parent_fp,
            binding=binding,
            receipts=receipts,
            affected=affected_ordered,
            committed_chunks=native_chunk_index,
            cause=cause,
        )
        failure = self._failure(
            request,
            action_index=current_action_index,
            native_chunk_index=native_chunk_index,
            operation=current_operation,
            cause=cause,
        )
        self.journal.append(self._event(request, "feature_rollback", failure=failure))
        return self._from_logical(request, logical, failure=failure)

    @staticmethod
    def _action_items(action: Mapping[str, Any]) -> Sequence[Any]:
        operation = action["operation"]
        if operation == "create_entities":
            return action["entities"]
        if operation == "insert_blocks":
            return action["inserts"]
        return action["semantic_pids"]

    def _dispatch_feature_chunk(
        self,
        runtime_document_id: str,
        *,
        document_pid: str,
        parent_fp: str,
        action: Mapping[str, Any],
        chunk: Sequence[Any],
        binding: Mapping[str, Any],
    ) -> tuple[str, dict[str, Any]]:
        operation = action["operation"]
        if operation == "create_entities":
            return (
                "entity.batch.create",
                self.client.batch_create_chunk(
                    runtime_document_id,
                    document_pid=document_pid,
                    expected_parent_fp=parent_fp,
                    entities=tuple(dict(item) for item in chunk),
                    logical_transaction=binding,
                ),
            )
        if operation == "insert_blocks":
            return (
                "entity.batch.insert_blocks",
                self.client.batch_insert_blocks_chunk(
                    runtime_document_id,
                    document_pid=document_pid,
                    expected_parent_fp=parent_fp,
                    inserts=tuple(dict(item) for item in chunk),
                    logical_transaction=binding,
                ),
            )
        return (
            "entity.batch.transform",
            self.client.batch_transform_chunk(
                runtime_document_id,
                document_pid=document_pid,
                expected_parent_fp=parent_fp,
                semantic_pids=tuple(str(item) for item in chunk),
                transform=dict(action["transform"]),
                logical_transaction=binding,
            ),
        )

    @staticmethod
    def _validate_feature_pids(
        operation: str,
        chunk: Sequence[Any],
        pids: Any,
        *,
        created_seen: set[str],
    ) -> None:
        if not isinstance(pids, list) or not all(isinstance(pid, str) and pid for pid in pids):
            raise FeatureExecutionError(
                "INVALID_FEATURE_RECEIPT",
                "committed feature native chunk must contain affected_semantic_pids",
            )
        if len(set(pids)) != len(pids):
            raise FeatureExecutionError(
                "INVALID_FEATURE_RECEIPT",
                "native feature chunk returned duplicate affected semantic PIDs",
            )
        if operation in {"create_entities", "insert_blocks"}:
            if len(pids) != len(chunk):
                raise FeatureExecutionError(
                    "INVALID_FEATURE_RECEIPT",
                    "created/inserted PID count differs from feature native chunk size",
                )
            if any(pid in created_seen for pid in pids):
                raise FeatureExecutionError(
                    "INVALID_FEATURE_RECEIPT",
                    "feature returned a duplicate created semantic PID across native chunks",
                )
            created_seen.update(pids)
            return
        expected = {str(pid) for pid in chunk}
        if set(pids) != expected:
            raise FeatureExecutionError(
                "INVALID_FEATURE_RECEIPT",
                "transform affected PID set differs from feature native chunk targets",
            )

    @staticmethod
    def _event(request: FeatureRequest, event: str, **fields: Any) -> dict[str, Any]:
        return {
            "event": event,
            "feature_id": request.feature_id,
            "feature_sequence": request.feature_sequence,
            "correlation_id": request.correlation_id,
            **fields,
        }

    @staticmethod
    def _failure(
        request: FeatureRequest,
        *,
        action_index: int | None,
        native_chunk_index: int | None,
        operation: str | None,
        cause: str,
        native_outcome: str | None = None,
    ) -> dict[str, Any]:
        return {
            "feature_id": request.feature_id,
            "feature_sequence": request.feature_sequence,
            "correlation_id": request.correlation_id,
            "action_index": action_index,
            "native_chunk_index": native_chunk_index,
            "operation": operation,
            "native_outcome": native_outcome,
            "cause": cause,
        }

    @staticmethod
    def _committed_feature(
        request: FeatureRequest,
        *,
        document_pid: str,
        runtime_document_id: str,
        initial_fp: str,
        final_fp: str,
        native_chunk_count: int,
        affected: Sequence[str],
        receipts: Sequence[dict[str, Any]],
    ) -> FeatureExecutionResult:
        return FeatureExecutionResult(
            status="COMMITTED",
            feature_id=request.feature_id,
            feature_sequence=request.feature_sequence,
            correlation_id=request.correlation_id,
            document_pid=document_pid,
            runtime_document_id=runtime_document_id,
            initial_fp=initial_fp,
            final_fp=final_fp,
            action_count=len(request.actions),
            native_chunk_count=native_chunk_count,
            affected_semantic_pids=tuple(affected),
            recommended_next_delay_ms=request.recommended_next_delay_ms,
            failure=None,
            native_receipts=tuple(dict(receipt) for receipt in receipts),
        )

    @staticmethod
    def _from_logical(
        request: FeatureRequest,
        logical: Any,
        *,
        failure: dict[str, Any],
    ) -> FeatureExecutionResult:
        return FeatureExecutionResult(
            status=logical.status,
            feature_id=request.feature_id,
            feature_sequence=request.feature_sequence,
            correlation_id=request.correlation_id,
            document_pid=logical.document_pid,
            runtime_document_id=logical.runtime_document_id,
            initial_fp=logical.initial_fp,
            final_fp=logical.final_fp,
            action_count=len(request.actions),
            native_chunk_count=logical.chunk_count,
            affected_semantic_pids=tuple(logical.affected_semantic_pids),
            recommended_next_delay_ms=0,
            failure=failure,
            native_receipts=tuple(logical.native_receipts),
        )


def _require_identifier(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or _IDENTIFIER_RE.fullmatch(value) is None:
        raise FeatureExecutionError(
            "INVALID_FEATURE_IDENTITY",
            f"{field_name} must be 1..128 characters from A-Z, a-z, 0-9, dot, underscore, colon or hyphen",
        )
    return value


def _require_exact_fields(action: Mapping[str, Any], expected: set[str], index: int) -> None:
    if set(action) != expected:
        raise FeatureExecutionError(
            "INVALID_FEATURE_ACTION",
            f"feature action {index} must use the exact typed schema for its operation",
        )


def _require_item_sequence(value: Any, field_name: str, index: int) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise FeatureExecutionError(
            "INVALID_FEATURE_ACTION",
            f"feature action {index} {field_name} must be a sequence",
        )
    result: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, Mapping):
            raise FeatureExecutionError(
                "INVALID_FEATURE_ACTION",
                f"feature action {index} {field_name} items must be objects",
            )
        result.append(dict(item))
    return result


def _require_string_sequence(value: Any, field_name: str, index: int) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise FeatureExecutionError(
            "INVALID_FEATURE_ACTION",
            f"feature action {index} {field_name} must be a sequence",
        )
    result = [str(item) for item in value]
    if any(not isinstance(item, str) or not item for item in value):
        raise FeatureExecutionError(
            "INVALID_FEATURE_ACTION",
            f"feature action {index} {field_name} must contain non-empty strings",
        )
    return result
