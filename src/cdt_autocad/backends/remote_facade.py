"""Async native facade forwarding; CAD semantics remain on the workstation."""
from __future__ import annotations

from typing import Any


class RemoteNativeFacade:
    def __init__(self, runtime: Any):
        self.runtime = runtime

    async def status(self) -> dict[str, Any]:
        return await self.runtime.native_status()

    async def bootstrap_document_identity(self) -> dict[str, Any]:
        return await self.runtime.native_bootstrap_document_identity()

    async def feature_execute(self, **kwargs: Any) -> dict[str, Any]:
        return await self.runtime._call("native_feature_execute", **kwargs)

    async def batch_create_entities(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return await self.runtime._call("native_batch_create_entities", *args, **kwargs)

    async def batch_insert_blocks(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return await self.runtime._call("native_batch_insert_blocks", *args, **kwargs)

    async def batch_transform_entities(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return await self.runtime._call("native_batch_transform_entities", *args, **kwargs)

    async def metadata_get(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return await self.runtime._call("native_metadata_get", *args, **kwargs)

    async def metadata_set(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return await self.runtime._call("native_metadata_set", *args, **kwargs)

    async def metadata_query(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        return await self.runtime._call("native_metadata_query", *args, **kwargs)
