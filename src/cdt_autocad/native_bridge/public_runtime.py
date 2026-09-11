"""Public native-integrity facade — active-document binding for G2/G3 product tools.
Wing: code | Topic: production-native-surface | Updated: 2026-09-11 18:55
"""

from __future__ import annotations

import ctypes
import json
import os
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from cdt_autocad.config import Settings
from cdt_autocad.errors import StateConflictError, UnsupportedCapabilityError

from .client import NativeBridgeClient
from .feature_stream import FeatureStreamExecutor
from .logical_batch import LogicalBatchError, NativeLogicalBatchExecutor
from .transport_windows import NamedPipeTransport, pipe_name_for_session


@dataclass(frozen=True)
class ActiveNativeDocument:
    runtime_document_id: str
    document_pid: str
    name: str
    file_name: str
    document_fp: str
    entity_count: int


class NativePublicFacade:
    """Bind product-facing native operations to the one active AutoCAD document.

    The facade never falls back to COM mutations. If the same-session native bridge is not
    available, the operation fails closed so callers never mistake a weaker route for a
    fingerprint/checkpoint-protected route.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        client_factory: Callable[[], NativeBridgeClient] | None = None,
        journal_root: str | Path | None = None,
    ):
        self.settings = settings
        self._client_factory = client_factory
        if journal_root is None:
            base = os.environ.get("LOCALAPPDATA", "").strip()
            journal_root = (
                Path(base) / "CDT-AutoCAD" / "logical-batch-journals"
                if base
                else Path.home() / ".cdt-autocad" / "logical-batch-journals"
            )
        self.journal_root = Path(journal_root)

    def status(self) -> dict[str, Any]:
        client = self._client()
        health = client.health()
        active = self._active_document(client)
        return {
            "available": True,
            "route": "native-managed-bridge",
            "fallback": False,
            "bridge": health,
            "active_document": self._context_payload(active),
        }

    def feature_execute(
        self,
        *,
        feature_id: str,
        feature_sequence: int,
        correlation_id: str,
        actions: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        client = self._client()
        active = self._active_document(client)
        self.journal_root.mkdir(parents=True, exist_ok=True)
        journal = self.journal_root / f"feature-{uuid4()}.jsonl"
        executor = FeatureStreamExecutor(client, journal)
        result = executor.execute(
            active.runtime_document_id,
            document_pid=active.document_pid,
            expected_parent_fp=active.document_fp,
            feature_id=feature_id,
            feature_sequence=feature_sequence,
            correlation_id=correlation_id,
            actions=actions,
        )
        return {
            "status": result.status,
            "feature_id": result.feature_id,
            "feature_sequence": result.feature_sequence,
            "correlation_id": result.correlation_id,
            "document_pid": result.document_pid,
            "runtime_document_id": result.runtime_document_id,
            "initial_document_fp": result.initial_fp,
            "final_document_fp": result.final_fp,
            "action_count": result.action_count,
            "native_chunk_count": result.native_chunk_count,
            "affected_semantic_pids": list(result.affected_semantic_pids),
            "recommended_next_delay_ms": result.recommended_next_delay_ms,
            "failure": result.failure,
            "route": "native-managed-bridge",
            "fallback": False,
            "journal_path": str(journal),
        }

    def batch_create_entities(self, entities: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        client = self._client()
        active = self._active_document(client)
        executor, journal = self._logical_executor(client)
        result = executor.create_entities(
            active.runtime_document_id,
            document_pid=active.document_pid,
            expected_parent_fp=active.document_fp,
            entities=entities,
        )
        return self._logical_result(result, journal)

    def batch_insert_blocks(self, inserts: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        client = self._client()
        active = self._active_document(client)
        executor, journal = self._logical_executor(client)
        result = executor.insert_blocks(
            active.runtime_document_id,
            document_pid=active.document_pid,
            expected_parent_fp=active.document_fp,
            inserts=inserts,
        )
        return self._logical_result(result, journal)

    def batch_transform_entities(
        self,
        semantic_pids: Sequence[str],
        transform: Mapping[str, Any],
    ) -> dict[str, Any]:
        client = self._client()
        active = self._active_document(client)
        executor, journal = self._logical_executor(client)
        result = executor.transform_entities(
            active.runtime_document_id,
            document_pid=active.document_pid,
            expected_parent_fp=active.document_fp,
            semantic_pids=semantic_pids,
            transform=transform,
        )
        return self._logical_result(result, journal)

    def metadata_get(self, semantic_pid: str, namespace: str) -> dict[str, Any]:
        client = self._client()
        active = self._active_document(client)
        return client.metadata_get(
            active.runtime_document_id,
            document_pid=active.document_pid,
            semantic_pid=semantic_pid,
            namespace=namespace,
        )

    def metadata_query(
        self,
        namespace: str,
        *,
        path: str | None = None,
        equals: Any = None,
        limit: int = 200,
    ) -> dict[str, Any]:
        client = self._client()
        active = self._active_document(client)
        return client.metadata_query(
            active.runtime_document_id,
            document_pid=active.document_pid,
            namespace=namespace,
            path=path,
            equals=equals,
            limit=limit,
        )

    def metadata_set(self, semantic_pid: str, namespace: str, value: Any) -> dict[str, Any]:
        client = self._client()
        active = self._active_document(client)
        try:
            receipt = client.metadata_set(
                active.runtime_document_id,
                document_pid=active.document_pid,
                expected_parent_fp=active.document_fp,
                semantic_pid=semantic_pid,
                namespace=namespace,
                value=value,
            )
        except Exception as exc:
            checkpoint = self._discover_metadata_checkpoint(client, active)
            if checkpoint is None:
                raise StateConflictError(
                    "native metadata completion is unknown and no uniquely bound recovery checkpoint was found"
                ) from exc
            return self._recover_metadata(
                client,
                active,
                {"dispatch_error": f"{type(exc).__name__}: {exc}"},
                checkpoint,
                reason="metadata dispatch completion became unknown",
            )
        outcome = receipt.get("outcome")
        if outcome == "ROLLED_BACK_VERIFIED":
            if receipt.get("post_document_fp") != active.document_fp:
                raise StateConflictError(
                    "native metadata mutation reported rollback but did not restore the predecessor fingerprint"
                )
            return {**receipt, "route": "native-managed-bridge", "fallback": False}

        try:
            checkpoint = self._checkpoint_binding(receipt)
        except StateConflictError:
            checkpoint = self._discover_metadata_checkpoint(client, active)
            if checkpoint is None:
                raise
        if outcome != "COMMITTED_VERIFIED":
            return self._recover_metadata(
                client,
                active,
                receipt,
                checkpoint,
                reason=f"native metadata mutation returned {outcome}",
            )

        try:
            after = client.document_state(
                active.runtime_document_id,
                document_pid=active.document_pid,
            )
            after_fp = after.get("document_fp")
            if (
                not isinstance(after_fp, str)
                or after_fp != receipt.get("post_document_fp")
                or after_fp != receipt.get("provisional_document_fp")
            ):
                raise StateConflictError(
                    "independent metadata read-back fingerprint differs from the native commit receipt"
                )
            metadata = client.metadata_get(
                active.runtime_document_id,
                document_pid=active.document_pid,
                semantic_pid=semantic_pid,
                namespace=namespace,
            )
            if metadata.get("found") is not True:
                raise StateConflictError(
                    "independent semantic read-back does not contain the committed metadata namespace"
                )
            if self._canonical_json(metadata.get("value")) != self._canonical_json(value):
                raise StateConflictError(
                    "independent semantic read-back metadata differs from the requested value"
                )
            finalized = client.finalize_recovery(
                active.runtime_document_id,
                document_pid=active.document_pid,
                checkpoint_id=checkpoint["checkpoint_id"],
                checkpoint_artifact_fp=checkpoint["checkpoint_artifact_fp"],
                accepted_post_fp=after_fp,
            )
            if finalized.get("status") != "FINALIZED":
                raise StateConflictError(
                    "native metadata checkpoint finalization was not confirmed"
                )
            return {
                **receipt,
                "route": "native-managed-bridge",
                "fallback": False,
                "independent_readback_verified": True,
            }
        except Exception as exc:
            return self._recover_metadata(
                client,
                active,
                receipt,
                checkpoint,
                reason=f"{type(exc).__name__}: {exc}",
            )

    def _recover_metadata(
        self,
        client: NativeBridgeClient,
        active: ActiveNativeDocument,
        receipt: Mapping[str, Any],
        checkpoint: Mapping[str, str],
        *,
        reason: str,
    ) -> dict[str, Any]:
        last: Mapping[str, Any] | None = None
        runtime_id = active.runtime_document_id
        for strategy in ("R1_COMPENSATE", "R2_CHECKPOINT_RESTORE"):
            recovery = client.resolve_recovery(
                runtime_id,
                document_pid=active.document_pid,
                checkpoint_id=checkpoint["checkpoint_id"],
                checkpoint_artifact_fp=checkpoint["checkpoint_artifact_fp"],
                expected_restore_fp=active.document_fp,
                strategy=strategy,
            )
            last = recovery
            rollback = recovery.get("rollback")
            if isinstance(rollback, Mapping) and (
                recovery.get("outcome") == "ROLLED_BACK_VERIFIED"
                and rollback.get("status") == "ROLLED_BACK_VERIFIED"
                and rollback.get("expected_restore_fp") == active.document_fp
                and rollback.get("actual_restore_fp") == active.document_fp
            ):
                rebound = recovery.get("runtime_document_id")
                return {
                    "schema_version": 1,
                    "operation": "metadata.set",
                    "outcome": "ROLLED_BACK_VERIFIED",
                    "document_pid": active.document_pid,
                    "runtime_document_id": rebound if isinstance(rebound, str) else runtime_id,
                    "pre_document_fp": active.document_fp,
                    "post_document_fp": active.document_fp,
                    "route": "native-managed-bridge",
                    "fallback": False,
                    "recovery_strategy": strategy,
                    "recovery_reason": reason,
                    "native_receipt": dict(receipt),
                }
            rebound = recovery.get("runtime_document_id")
            if isinstance(rebound, str) and rebound:
                runtime_id = rebound
        raise StateConflictError(
            "native metadata state is uncertain because exact predecessor recovery was not confirmed: "
            + self._canonical_json(last or {"reason": reason})
        )

    @staticmethod
    def _discover_metadata_checkpoint(
        client: NativeBridgeClient,
        active: ActiveNativeDocument,
    ) -> dict[str, str] | None:
        matches = [
            row
            for row in client.recoveries_list()
            if row.get("document_pid") == active.document_pid
            and row.get("operation") == "metadata.set"
            and row.get("expected_restore_fp") == active.document_fp
        ]
        if len(matches) != 1:
            return None
        row = matches[0]
        checkpoint_id = row.get("checkpoint_id")
        artifact_fp = row.get("checkpoint_artifact_fp")
        expected_restore_fp = row.get("expected_restore_fp")
        if not all(isinstance(value, str) and value for value in (checkpoint_id, artifact_fp, expected_restore_fp)):
            return None
        return {
            "checkpoint_id": str(checkpoint_id),
            "checkpoint_artifact_fp": str(artifact_fp),
            "expected_restore_fp": str(expected_restore_fp),
        }

    def _client(self) -> NativeBridgeClient:
        if self.settings.backend != "com":
            raise UnsupportedCapabilityError(
                "autocad.native.integrity",
                "Native integrity tools require the live Windows COM profile; no headless fallback is used.",
            )
        if self._client_factory is not None:
            return self._client_factory()
        if sys.platform != "win32":
            raise UnsupportedCapabilityError(
                "autocad.native.integrity",
                "Native integrity tools require the MCP worker to run in the same Windows session as AutoCAD.",
            )
        session_id = self._current_windows_session_id()
        return NativeBridgeClient(
            NamedPipeTransport(pipe_name_for_session(session_id), connect_timeout_ms=2_000)
        )

    @staticmethod
    def _current_windows_session_id() -> int:
        session_id = ctypes.c_uint32()
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        if not kernel32.ProcessIdToSessionId(os.getpid(), ctypes.byref(session_id)):
            raise StateConflictError("unable to resolve the MCP worker Windows session")
        return int(session_id.value)

    @staticmethod
    def _active_document(client: NativeBridgeClient) -> ActiveNativeDocument:
        documents = client.documents_list()
        active_rows = [row for row in documents if row.get("is_active") is True]
        if len(active_rows) != 1:
            raise StateConflictError(
                "native integrity tools require exactly one bridge-reported active AutoCAD document"
            )
        row = active_rows[0]
        runtime_id = row.get("runtime_document_id")
        document_pid = row.get("document_pid")
        if not isinstance(runtime_id, str) or not runtime_id:
            raise StateConflictError("active native AutoCAD document has no runtime binding")
        if not isinstance(document_pid, str) or not document_pid:
            raise StateConflictError(
                "active AutoCAD document has no persistent PID; native integrity mutation is refused"
            )
        state = client.document_state(runtime_id, document_pid=document_pid)
        document_fp = state.get("document_fp")
        entity_count = state.get("entity_count")
        if not isinstance(document_fp, str) or not document_fp:
            raise StateConflictError("active native compact state has no document fingerprint")
        if not isinstance(entity_count, int) or entity_count < 0:
            raise StateConflictError("active native compact state has no valid entity count")
        return ActiveNativeDocument(
            runtime_document_id=runtime_id,
            document_pid=document_pid,
            name=str(row.get("name") or ""),
            file_name=str(row.get("file_name") or ""),
            document_fp=document_fp,
            entity_count=entity_count,
        )

    def _logical_executor(
        self, client: NativeBridgeClient
    ) -> tuple[NativeLogicalBatchExecutor, Path]:
        self.journal_root.mkdir(parents=True, exist_ok=True)
        journal = self.journal_root / f"logical-{uuid4()}.jsonl"
        return NativeLogicalBatchExecutor(client, journal), journal

    @staticmethod
    def _logical_result(result: Any, journal: Path) -> dict[str, Any]:
        return {
            "status": result.status,
            "operation": result.operation,
            "document_pid": result.document_pid,
            "runtime_document_id": result.runtime_document_id,
            "initial_document_fp": result.initial_fp,
            "final_document_fp": result.final_fp,
            "chunk_count": result.chunk_count,
            "affected_semantic_pids": list(result.affected_semantic_pids),
            "route": "native-managed-bridge",
            "fallback": False,
            "journal_path": str(journal),
        }

    @staticmethod
    def _context_payload(active: ActiveNativeDocument) -> dict[str, Any]:
        return {
            "runtime_document_id": active.runtime_document_id,
            "document_pid": active.document_pid,
            "name": active.name,
            "file_name": active.file_name,
            "document_fp": active.document_fp,
            "entity_count": active.entity_count,
        }

    @staticmethod
    def _checkpoint_binding(receipt: Mapping[str, Any]) -> dict[str, str]:
        raw = receipt.get("recovery_checkpoint")
        if not isinstance(raw, Mapping):
            raise StateConflictError("native mutation receipt is missing its recovery checkpoint")
        result: dict[str, str] = {}
        for name in ("checkpoint_id", "checkpoint_artifact_fp", "expected_restore_fp"):
            value = raw.get(name)
            if not isinstance(value, str) or not value:
                raise StateConflictError(
                    "native mutation receipt contains an invalid recovery checkpoint binding"
                )
            result[name] = value
        return result

    @staticmethod
    def _canonical_json(value: Any) -> str:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
