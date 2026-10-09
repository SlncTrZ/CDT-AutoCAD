"""Durable generation and in-flight mutation marker for the split-host provider."""
from __future__ import annotations

import json
import os
from pathlib import Path
from uuid import uuid4

from .errors import StateConflictError


class RuntimeBinding:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.generation: str | None = None
        self.pending = False
        if self.path.exists():
            try:
                if self.path.stat().st_size > 8192:
                    raise ValueError("oversized binding")
                data = json.loads(self.path.read_text())
                if (data.get("schema_version") != 1 or
                    not isinstance(data.get("generation"), str) or
                    not data["generation"] or type(data.get("pending")) is not bool):
                    raise ValueError("invalid binding")
                self.generation = data["generation"]
                self.pending = data["pending"]
            except (ValueError, KeyError, TypeError) as exc:
                raise StateConflictError("Runtime binding is invalid; operator reconciliation required") from exc

    def save(self, generation: str, *, pending: bool) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_name(self.path.name + "." + uuid4().hex + ".tmp")
        try:
            fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as stream:
                json.dump({"schema_version": 1, "generation": generation, "pending": pending}, stream)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, self.path)
            if os.name != "nt":
                fd = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
        finally:
            temp.unlink(missing_ok=True)
        self.generation, self.pending = generation, pending
