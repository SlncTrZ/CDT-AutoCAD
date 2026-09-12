"""G3 logical batch executor — durable all-or-nothing orchestration across yielded native chunks.
Wing: code | Topic: native-g3-logical-batch | Updated: 2026-09-11 18:35
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .protocol import MAX_BATCH_CHUNK_ENTITIES, MAX_LOGICAL_BATCH_ITEMS, LogicalBatchBinding


class LogicalBatchError(RuntimeError):
    """Fail-closed G3 orchestration error with a stable machine-readable code."""

    def __init__(self, code: str, message: str, *, detail: Any = None):
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True)
class LogicalBatchResult:
    status: str
    operation: str
    document_pid: str
    runtime_document_id: str
    initial_fp: str
    final_fp: str
    chunk_count: int
    affected_semantic_pids: tuple[str, ...]
    native_receipts: tuple[dict[str, Any], ...]


class LogicalBatchJournal:
    """Write-through JSONL evidence for one non-resumable G3 logical transaction."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        if self.path.exists() and self.path.stat().st_size > 0:
            raise ValueError("logical batch journal already exists and is non-empty")
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, event: Mapping[str, Any]) -> None:
        payload = json.dumps(
            dict(event), ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
        )
        with self.path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(payload + "\n")
            stream.flush()
            os.fsync(stream.fileno())


class NativeLogicalBatchExecutor:
    """Execute G1 native chunks behind one G3 recovery checkpoint.

    Each native chunk remains a short AutoCAD transaction and the bridge yields to the next
    AutoCAD Idle event between chunks. The logical transaction owns one immutable predecessor
    checkpoint. Any chunk failure or uncertain completion restores that predecessor with R2.
    """

    def __init__(self, client: Any, journal_path: str | Path):
        self.client = client
        self.journal = LogicalBatchJournal(journal_path)

    def create_entities(
        self,
        runtime_document_id: str,
        *,
        document_pid: str,
        expected_parent_fp: str,
        entities: Sequence[Mapping[str, Any]],
    ) -> LogicalBatchResult:
        items = tuple(dict(item) for item in entities)
        return self._execute(
            runtime_document_id,
            document_pid=document_pid,
            expected_parent_fp=expected_parent_fp,
            operation="entity.batch.create",
            items=items,
            dispatch=lambda runtime_id, parent_fp, chunk, binding: self.client.batch_create_chunk(
                runtime_id,
                document_pid=document_pid,
                expected_parent_fp=parent_fp,
                entities=tuple(chunk),
                logical_transaction=binding,
            ),
        )

    def insert_blocks(
        self,
        runtime_document_id: str,
        *,
        document_pid: str,
        expected_parent_fp: str,
        inserts: Sequence[Mapping[str, Any]],
    ) -> LogicalBatchResult:
        items = tuple(dict(item) for item in inserts)
        return self._execute(
            runtime_document_id,
            document_pid=document_pid,
            expected_parent_fp=expected_parent_fp,
            operation="entity.batch.insert_blocks",
            items=items,
            dispatch=lambda runtime_id, parent_fp, chunk, binding: self.client.batch_insert_blocks_chunk(
                runtime_id,
                document_pid=document_pid,
                expected_parent_fp=parent_fp,
                inserts=tuple(chunk),
                logical_transaction=binding,
            ),
        )

    def transform_entities(
        self,
        runtime_document_id: str,
        *,
        document_pid: str,
        expected_parent_fp: str,
        semantic_pids: Sequence[str],
        transform: Mapping[str, Any],
    ) -> LogicalBatchResult:
        items = tuple(str(item) for item in semantic_pids)
        transform_payload = dict(transform)
        return self._execute(
            runtime_document_id,
            document_pid=document_pid,
            expected_parent_fp=expected_parent_fp,
            operation="entity.batch.transform",
            items=items,
            dispatch=lambda runtime_id, parent_fp, chunk, binding: self.client.batch_transform_chunk(
                runtime_id,
                document_pid=document_pid,
                expected_parent_fp=parent_fp,
                semantic_pids=tuple(chunk),
                transform=transform_payload,
                logical_transaction=binding,
            ),
        )

    def _execute(
        self,
        runtime_document_id: str,
        *,
        document_pid: str,
        expected_parent_fp: str,
        operation: str,
        items: Sequence[Any],
        dispatch: Callable[[str, str, Sequence[Any], Mapping[str, Any]], dict[str, Any]],
    ) -> LogicalBatchResult:
        if not 1 <= len(items) <= MAX_LOGICAL_BATCH_ITEMS:
            raise LogicalBatchError(
                "LOGICAL_BATCH_SIZE_INVALID",
                f"logical batch must contain 1..{MAX_LOGICAL_BATCH_ITEMS} items",
            )
        if not _is_fingerprint(expected_parent_fp):
            raise LogicalBatchError("INVALID_PARENT_FP", "expected_parent_fp must be canonical sha256")

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
                    {
                        "event": "logical_uncertain",
                        "operation": operation,
                        "phase": "begin",
                        "cause": f"{type(exc).__name__}: {exc}",
                    }
                )
                raise LogicalBatchError(
                    "LOGICAL_BEGIN_UNKNOWN",
                    "logical begin completion is unknown and no unique predecessor checkpoint can be recovered",
                ) from exc
            self.journal.append(
                {
                    "event": "logical_begin_unknown",
                    "operation": operation,
                    "document_pid": document_pid,
                    "runtime_document_id": runtime_document_id,
                    "initial_fp": expected_parent_fp,
                    "cause": f"{type(exc).__name__}: {exc}",
                    "logical_transaction": binding.to_dict(),
                }
            )
            return self._rollback(
                runtime_document_id,
                document_pid=document_pid,
                operation=operation,
                expected_parent_fp=expected_parent_fp,
                binding=binding,
                receipts=(),
                affected=(),
                committed_chunks=0,
                cause="logical begin completion became unknown",
            )

        binding_payload = binding.to_dict()
        self.journal.append(
            {
                "event": "logical_begin",
                "operation": operation,
                "document_pid": document_pid,
                "runtime_document_id": runtime_document_id,
                "initial_fp": expected_parent_fp,
                "item_count": len(items),
                "chunk_size": MAX_BATCH_CHUNK_ENTITIES,
                "logical_transaction": binding_payload,
            }
        )

        current_fp = expected_parent_fp
        receipts: list[dict[str, Any]] = []
        affected: list[str] = []
        committed_chunks = 0
        chunks = [
            items[offset : offset + MAX_BATCH_CHUNK_ENTITIES]
            for offset in range(0, len(items), MAX_BATCH_CHUNK_ENTITIES)
        ]
        try:
            for index, chunk in enumerate(chunks):
                receipt = dispatch(runtime_document_id, current_fp, chunk, binding_payload)
                self._validate_chunk_receipt(
                    receipt,
                    operation=operation,
                    document_pid=document_pid,
                    expected_pre_fp=current_fp,
                    binding=binding,
                )
                receipts.append(dict(receipt))
                outcome = receipt.get("outcome")
                if outcome != "COMMITTED_VERIFIED":
                    return self._rollback(
                        runtime_document_id,
                        document_pid=document_pid,
                        operation=operation,
                        expected_parent_fp=expected_parent_fp,
                        binding=binding,
                        receipts=receipts,
                        affected=affected,
                        committed_chunks=committed_chunks,
                        cause=f"chunk {index} returned {outcome}",
                    )
                post_fp = str(receipt["post_document_fp"])
                pids = receipt.get("affected_semantic_pids")
                if not isinstance(pids, list) or not all(isinstance(pid, str) and pid for pid in pids):
                    raise LogicalBatchError(
                        "INVALID_CHUNK_RECEIPT",
                        "committed chunk receipt must contain affected_semantic_pids",
                    )
                if operation in {"entity.batch.create", "entity.batch.insert_blocks"} and len(pids) != len(chunk):
                    raise LogicalBatchError(
                        "INVALID_CHUNK_RECEIPT",
                        "created/inserted PID count differs from logical chunk size",
                    )
                if operation == "entity.batch.transform" and set(pids) != set(chunk):
                    raise LogicalBatchError(
                        "INVALID_CHUNK_RECEIPT",
                        "transform receipt PID set differs from logical chunk targets",
                    )
                if len(set(pids)) != len(pids) or any(pid in affected for pid in pids):
                    raise LogicalBatchError(
                        "INVALID_CHUNK_RECEIPT",
                        "logical batch receipt contains duplicate affected semantic PIDs",
                    )
                affected.extend(pids)
                current_fp = post_fp
                committed_chunks += 1
                self.journal.append(
                    {
                        "event": "chunk_committed",
                        "operation": operation,
                        "chunk_index": index,
                        "chunk_count": len(chunks),
                        "pre_document_fp": receipt["pre_document_fp"],
                        "post_document_fp": post_fp,
                        "affected_semantic_pids": pids,
                    }
                )

            independent = self.client.document_state(
                runtime_document_id,
                document_pid=document_pid,
            )
            if independent.get("document_fp") != current_fp:
                raise LogicalBatchError(
                    "LOGICAL_READBACK_MISMATCH",
                    "independent compact document state differs from the chained final fingerprint",
                    detail=independent,
                )

            self.journal.append(
                {
                    "event": "logical_finalize_pending",
                    "operation": operation,
                    "chunk_count": committed_chunks,
                    "final_fp": current_fp,
                    "checkpoint_id": binding.checkpoint_id,
                }
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
                            {
                                "event": "logical_commit_reconciled",
                                "operation": operation,
                                "chunk_count": committed_chunks,
                                "final_fp": current_fp,
                                "reason": "finalize response lost after checkpoint disappearance",
                            }
                        )
                        return self._committed_result(
                            runtime_document_id,
                            document_pid,
                            operation,
                            expected_parent_fp,
                            current_fp,
                            committed_chunks,
                            affected,
                            receipts,
                        )
                raise LogicalBatchError(
                    "LOGICAL_FINALIZE_UNKNOWN",
                    "logical checkpoint finalization could not be reconciled",
                ) from exc
            if finalized.get("status") != "FINALIZED":
                raise LogicalBatchError(
                    "LOGICAL_FINALIZE_FAILED",
                    "bridge did not confirm logical checkpoint finalization",
                    detail=finalized,
                )
            self.journal.append(
                {
                    "event": "logical_commit",
                    "operation": operation,
                    "chunk_count": committed_chunks,
                    "final_fp": current_fp,
                }
            )
            return self._committed_result(
                runtime_document_id,
                document_pid,
                operation,
                expected_parent_fp,
                current_fp,
                committed_chunks,
                affected,
                receipts,
            )
        except LogicalBatchError as exc:
            return self._rollback(
                runtime_document_id,
                document_pid=document_pid,
                operation=operation,
                expected_parent_fp=expected_parent_fp,
                binding=binding,
                receipts=receipts,
                affected=affected,
                committed_chunks=committed_chunks,
                cause=str(exc),
            )
        except Exception as exc:
            return self._rollback(
                runtime_document_id,
                document_pid=document_pid,
                operation=operation,
                expected_parent_fp=expected_parent_fp,
                binding=binding,
                receipts=receipts,
                affected=affected,
                committed_chunks=committed_chunks,
                cause=f"{type(exc).__name__}: {exc}",
            )

    def _discover_logical_binding(
        self,
        document_pid: str,
        expected_parent_fp: str,
    ) -> LogicalBatchBinding | None:
        try:
            matches = [
                row
                for row in self.client.recoveries_list()
                if row.get("operation") == "logical.batch"
                and row.get("document_pid") == document_pid
                and row.get("expected_restore_fp") == expected_parent_fp
            ]
        except Exception:
            return None
        if len(matches) != 1:
            return None
        row = matches[0]
        try:
            return LogicalBatchBinding.from_dict(
                {
                    "checkpoint_id": row.get("checkpoint_id"),
                    "checkpoint_artifact_fp": row.get("checkpoint_artifact_fp"),
                    "expected_restore_fp": row.get("expected_restore_fp"),
                }
            )
        except Exception:
            return None

    def _logical_checkpoint_present(
        self,
        binding: LogicalBatchBinding,
        document_pid: str,
        expected_parent_fp: str,
    ) -> bool:
        rows = self.client.recoveries_list()
        return any(
            row.get("checkpoint_id") == binding.checkpoint_id
            and row.get("checkpoint_artifact_fp") == binding.checkpoint_artifact_fp
            and row.get("expected_restore_fp") == expected_parent_fp
            and row.get("document_pid") == document_pid
            and row.get("operation") == "logical.batch"
            for row in rows
        )

    @staticmethod
    def _committed_result(
        runtime_document_id: str,
        document_pid: str,
        operation: str,
        expected_parent_fp: str,
        current_fp: str,
        committed_chunks: int,
        affected: Sequence[str],
        receipts: Sequence[dict[str, Any]],
    ) -> LogicalBatchResult:
        return LogicalBatchResult(
            status="COMMITTED",
            operation=operation,
            document_pid=document_pid,
            runtime_document_id=runtime_document_id,
            initial_fp=expected_parent_fp,
            final_fp=current_fp,
            chunk_count=committed_chunks,
            affected_semantic_pids=tuple(affected),
            native_receipts=tuple(dict(receipt) for receipt in receipts),
        )

    def _rollback(
        self,
        runtime_document_id: str,
        *,
        document_pid: str,
        operation: str,
        expected_parent_fp: str,
        binding: LogicalBatchBinding,
        receipts: Sequence[dict[str, Any]],
        affected: Sequence[str],
        committed_chunks: int,
        cause: str,
    ) -> LogicalBatchResult:
        try:
            recovery = self.client.resolve_recovery(
                runtime_document_id,
                document_pid=document_pid,
                checkpoint_id=binding.checkpoint_id,
                checkpoint_artifact_fp=binding.checkpoint_artifact_fp,
                expected_restore_fp=expected_parent_fp,
                strategy="R2_CHECKPOINT_RESTORE",
            )
        except Exception as exc:
            self.journal.append(
                {
                    "event": "logical_uncertain",
                    "operation": operation,
                    "cause": cause,
                    "recovery_error": f"{type(exc).__name__}: {exc}",
                    "checkpoint_id": binding.checkpoint_id,
                }
            )
            raise LogicalBatchError(
                "LOGICAL_STATE_UNCERTAIN",
                "logical batch failed and predecessor checkpoint restore could not be confirmed",
                detail={"cause": cause, "recovery_error": str(exc)},
            ) from exc

        rollback = recovery.get("rollback")
        if not isinstance(rollback, Mapping):
            raise LogicalBatchError(
                "LOGICAL_ROLLBACK_FAILED",
                "R2 recovery response does not contain rollback evidence",
                detail=recovery,
            )
        restored = (
            recovery.get("outcome") == "ROLLED_BACK_VERIFIED"
            and rollback.get("status") == "ROLLED_BACK_VERIFIED"
            and rollback.get("expected_restore_fp") == expected_parent_fp
            and rollback.get("actual_restore_fp") == expected_parent_fp
        )
        if not restored:
            self.journal.append(
                {
                    "event": "logical_rollback_failed",
                    "operation": operation,
                    "cause": cause,
                    "recovery": recovery,
                }
            )
            raise LogicalBatchError(
                "LOGICAL_ROLLBACK_FAILED",
                "R2 did not restore the exact logical predecessor fingerprint",
                detail=recovery,
            )
        restored_runtime = recovery.get("runtime_document_id")
        if not isinstance(restored_runtime, str) or not restored_runtime:
            restored_runtime = runtime_document_id
        self.journal.append(
            {
                "event": "logical_rollback",
                "operation": operation,
                "cause": cause,
                "committed_chunks_before_restore": committed_chunks,
                "expected_restore_fp": expected_parent_fp,
                "actual_restore_fp": expected_parent_fp,
                "runtime_document_id": restored_runtime,
            }
        )
        return LogicalBatchResult(
            status="ROLLED_BACK_VERIFIED",
            operation=operation,
            document_pid=document_pid,
            runtime_document_id=restored_runtime,
            initial_fp=expected_parent_fp,
            final_fp=expected_parent_fp,
            chunk_count=committed_chunks,
            affected_semantic_pids=tuple(affected),
            native_receipts=tuple(dict(receipt) for receipt in receipts),
        )

    @staticmethod
    def _validate_begin(
        receipt: Mapping[str, Any], document_pid: str, expected_parent_fp: str
    ) -> LogicalBatchBinding:
        if (
            receipt.get("status") != "OPEN"
            or receipt.get("document_pid") != document_pid
            or receipt.get("pre_document_fp") != expected_parent_fp
        ):
            raise LogicalBatchError(
                "INVALID_LOGICAL_BEGIN_RECEIPT",
                "bridge logical begin receipt does not match the requested predecessor",
                detail=dict(receipt),
            )
        raw_binding = receipt.get("logical_transaction")
        if not isinstance(raw_binding, Mapping):
            raise LogicalBatchError(
                "INVALID_LOGICAL_BEGIN_RECEIPT",
                "bridge logical begin did not return a checkpoint binding",
            )
        try:
            binding = LogicalBatchBinding.from_dict(raw_binding)
        except Exception as exc:
            raise LogicalBatchError(
                "INVALID_LOGICAL_BEGIN_RECEIPT",
                "bridge logical begin returned an invalid checkpoint binding",
            ) from exc
        if binding.expected_restore_fp != expected_parent_fp:
            raise LogicalBatchError(
                "INVALID_LOGICAL_BEGIN_RECEIPT",
                "logical checkpoint restore fingerprint differs from requested predecessor",
            )
        return binding

    @staticmethod
    def _validate_chunk_receipt(
        receipt: Mapping[str, Any],
        *,
        operation: str,
        document_pid: str,
        expected_pre_fp: str,
        binding: LogicalBatchBinding,
    ) -> None:
        if receipt.get("operation") != operation or receipt.get("document_pid") != document_pid:
            raise LogicalBatchError(
                "INVALID_CHUNK_RECEIPT", "logical chunk receipt identity does not match request"
            )
        if receipt.get("pre_document_fp") != expected_pre_fp:
            raise LogicalBatchError(
                "INVALID_CHUNK_RECEIPT", "logical chunk receipt predecessor fingerprint is discontinuous"
            )
        post_fp = receipt.get("post_document_fp")
        if not isinstance(post_fp, str) or not _is_fingerprint(post_fp):
            raise LogicalBatchError(
                "INVALID_CHUNK_RECEIPT", "logical chunk receipt is missing a canonical post fingerprint"
            )
        raw_checkpoint = receipt.get("recovery_checkpoint")
        if not isinstance(raw_checkpoint, Mapping):
            raise LogicalBatchError(
                "INVALID_CHUNK_RECEIPT", "logical chunk receipt is missing recovery checkpoint binding"
            )
        checkpoint = LogicalBatchBinding.from_dict(raw_checkpoint)
        if checkpoint != binding:
            raise LogicalBatchError(
                "INVALID_CHUNK_RECEIPT", "logical chunk switched recovery checkpoint binding"
            )


def _is_fingerprint(value: str) -> bool:
    return (
        isinstance(value, str)
        and value.startswith("sha256:")
        and len(value) == 71
        and all(char in "0123456789abcdef" for char in value[7:])
    )
