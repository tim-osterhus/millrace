"""Project-rooted exception hierarchy for Millrace."""

from __future__ import annotations


class MillraceError(RuntimeError):
    """Base class for Millrace package errors outside validator semantics."""


class ConfigurationError(MillraceError):
    """Raised for invalid or unusable runtime/compiler configuration state."""


class WorkspaceStateError(MillraceError):
    """Raised for invalid or unrecoverable workspace filesystem state."""


class QueueStateError(MillraceError):
    """Raised for invalid queue ownership or transition state."""


class RuntimeLifecycleError(MillraceError):
    """Raised for runtime startup, reload, or execution lifecycle failures."""


class ControlRoutingError(MillraceError):
    """Raised when control commands cannot be routed or applied safely."""


class AssetValidationError(MillraceError):
    """Raised when shipped or supplied runtime assets are invalid."""


__all__ = [
    "AssetValidationError",
    "ConfigurationError",
    "ControlRoutingError",
    "MillraceError",
    "QueueStateError",
    "RuntimeLifecycleError",
    "WorkspaceStateError",
]
