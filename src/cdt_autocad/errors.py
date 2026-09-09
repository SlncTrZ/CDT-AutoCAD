"""Provider-local error vocabulary."""

from __future__ import annotations


class AutoCADProviderError(RuntimeError):
    """Base class for expected provider failures."""


class UnsupportedCapabilityError(AutoCADProviderError):
    def __init__(self, capability: str, message: str):
        super().__init__(message)
        self.capability = capability


class StateConflictError(AutoCADProviderError):
    """Raised when a valid operation cannot run in the current document state."""


class BackendQuarantinedError(StateConflictError):
    """Raised when a timed-out mutation left document integrity uncertain."""


class BackendTimeoutError(AutoCADProviderError):
    """Raised when a backend operation exceeds its configured deadline."""
