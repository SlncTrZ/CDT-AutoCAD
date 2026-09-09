"""Runtime configuration for the AutoCAD provider.
Wing: code | Topic: autocad-a2 | Updated: 2026-09-09 14:06
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

_BACKENDS = {"ezdxf", "com"}
_COM_ATTACH_POLICIES = {"attach_only", "attach_or_start"}


@dataclass(frozen=True)
class Settings:
    allowed_paths: tuple[Path, ...]
    max_dxf_bytes: int = 50 * 1024 * 1024
    call_timeout_seconds: float = 120.0
    render_timeout_seconds: float = 300.0
    undo_depth: int = 10
    transaction_depth: int = 8
    auth_token: str = ""
    allow_remote_http: bool = False
    backend: str = "ezdxf"
    com_progid: str = "AutoCAD.Application"
    com_attach_policy: str = "attach_only"
    com_call_timeout_seconds: float = 60.0

    def __post_init__(self) -> None:
        if not self.allowed_paths:
            raise ValueError("allowed_paths must not be empty")
        if self.max_dxf_bytes <= 0:
            raise ValueError("max_dxf_bytes must be > 0")
        if self.call_timeout_seconds <= 0:
            raise ValueError("call_timeout_seconds must be > 0")
        if self.render_timeout_seconds <= 0:
            raise ValueError("render_timeout_seconds must be > 0")
        if not 0 <= self.undo_depth <= 100:
            raise ValueError("undo_depth must be between 0 and 100")
        if not 1 <= self.transaction_depth <= 100:
            raise ValueError("transaction_depth must be between 1 and 100")
        if self.backend not in _BACKENDS:
            raise ValueError("backend must be one of: com, ezdxf")
        if not self.com_progid.strip():
            raise ValueError("com_progid must not be empty")
        if self.com_attach_policy not in _COM_ATTACH_POLICIES:
            raise ValueError("com_attach_policy must be one of: attach_only, attach_or_start")
        if self.com_call_timeout_seconds <= 0:
            raise ValueError("com_call_timeout_seconds must be > 0")

    @classmethod
    def from_env(cls) -> Settings:
        raw_paths = os.environ.get("CDT_AUTOCAD_ALLOWED_PATHS", "").strip()
        if raw_paths:
            roots = tuple(
                Path(part).expanduser().resolve()
                for part in raw_paths.split(os.pathsep)
                if part.strip()
            )
        else:
            # Development-safe default: provider cannot escape its launch directory.
            roots = (Path.cwd().resolve(),)

        if not roots:
            raise RuntimeError("CDT_AUTOCAD_ALLOWED_PATHS resolved to no usable paths")

        max_bytes = int(os.environ.get("CDT_AUTOCAD_MAX_DXF_BYTES", str(50 * 1024 * 1024)))
        timeout = float(os.environ.get("CDT_AUTOCAD_CALL_TIMEOUT", "120"))
        render_timeout = float(os.environ.get("CDT_AUTOCAD_RENDER_TIMEOUT", "300"))
        undo_depth = int(os.environ.get("CDT_AUTOCAD_UNDO_DEPTH", "10"))
        transaction_depth = int(os.environ.get("CDT_AUTOCAD_TRANSACTION_DEPTH", "8"))
        backend = os.environ.get("CDT_AUTOCAD_BACKEND", "ezdxf").strip().lower()
        com_progid = os.environ.get("CDT_AUTOCAD_COM_PROGID", "AutoCAD.Application").strip()
        com_attach_policy = os.environ.get(
            "CDT_AUTOCAD_COM_ATTACH_POLICY", "attach_only"
        ).strip().lower()
        com_timeout = float(os.environ.get("CDT_AUTOCAD_COM_TIMEOUT", "60"))

        if max_bytes <= 0:
            raise ValueError("CDT_AUTOCAD_MAX_DXF_BYTES must be > 0")
        if timeout <= 0:
            raise ValueError("CDT_AUTOCAD_CALL_TIMEOUT must be > 0")
        if render_timeout <= 0:
            raise ValueError("CDT_AUTOCAD_RENDER_TIMEOUT must be > 0")
        if not 0 <= undo_depth <= 100:
            raise ValueError("CDT_AUTOCAD_UNDO_DEPTH must be between 0 and 100")
        if not 1 <= transaction_depth <= 100:
            raise ValueError("CDT_AUTOCAD_TRANSACTION_DEPTH must be between 1 and 100")
        if backend not in _BACKENDS:
            raise ValueError("CDT_AUTOCAD_BACKEND must be one of: com, ezdxf")
        if not com_progid:
            raise ValueError("CDT_AUTOCAD_COM_PROGID must not be empty")
        if com_attach_policy not in _COM_ATTACH_POLICIES:
            raise ValueError(
                "CDT_AUTOCAD_COM_ATTACH_POLICY must be one of: attach_only, attach_or_start"
            )
        if com_timeout <= 0:
            raise ValueError("CDT_AUTOCAD_COM_TIMEOUT must be > 0")

        return cls(
            allowed_paths=roots,
            max_dxf_bytes=max_bytes,
            call_timeout_seconds=timeout,
            render_timeout_seconds=render_timeout,
            undo_depth=undo_depth,
            transaction_depth=transaction_depth,
            auth_token=os.environ.get("CDT_AUTOCAD_AUTH_TOKEN", "").strip(),
            allow_remote_http=os.environ.get("CDT_AUTOCAD_ALLOW_REMOTE_HTTP", "").lower()
            in {"1", "true", "yes", "on"},
            backend=backend,
            com_progid=com_progid,
            com_attach_policy=com_attach_policy,
            com_call_timeout_seconds=com_timeout,
        )
