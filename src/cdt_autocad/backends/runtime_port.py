"""AutoCAD runtime port — provider/execution seam for migration R1.
Wing: code | Topic: migration-r1-seam | Updated: 2026-10-07 14:30
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from .base import AutoCADBackend

if TYPE_CHECKING:
    from ..mutation_coordinator import MutationCoordinator


@runtime_checkable
class AutoCADRuntimePort(Protocol):
    """Seam between the MCP provider layer and AutoCAD execution.

    Structural port only: it declares the seam extras plus the two read-only
    identity methods (``capabilities``/``status``) with the SAME signatures and
    semantics as AutoCADBackend (base.py composition surface, lines 410-425).
    Nothing is redefined — implementers satisfy the FULL AutoCADBackend contract
    by 1:1 delegation (see LocalAutoCADRuntimeAdapter), which keeps PID,
    fingerprint, recovery, timeout and quarantine semantics untouched.

    - ``capabilities``/``status`` mirror IdentityContract (read-only).
    - ``runtime_status`` reports the same backend status semantics as ``status``.
    - ``health`` is read-only liveness without CAD mutation.
    - ``backend`` is the controlled escape hatch to the wrapped AutoCADBackend.
    - ``mutation_coordinator`` is the ONE shared provider-local writer authority.
    """

    @property
    def backend(self) -> AutoCADBackend:
        """Return the wrapped execution backend (controlled escape hatch)."""
        ...

    @property
    def mutation_coordinator(self) -> MutationCoordinator:
        """Return the single shared mutation coordinator (no duplicates)."""
        ...

    def capabilities(self) -> dict[str, dict[str, Any]]:
        """Mirror AutoCADBackend capabilities (IdentityContract, read-only)."""
        ...

    def status(self) -> dict[str, Any]:
        """Mirror AutoCADBackend status (IdentityContract, read-only)."""
        ...

    def runtime_status(self) -> dict[str, Any]:
        """Return backend status for this runtime (same semantics as status)."""
        ...

    def health(self) -> dict[str, Any]:
        """Return read-only liveness without CAD mutation."""
        ...
