"""Built-in extension boundary registry.

Maps extension interface IDs to concrete built-in implementations.
The runtime resolves domain-specific behaviour through this registry
by interface ID rather than importing domain modules directly.

Lazy-loading ensures that Blueprint, Recon, closure, and Learning
domain code is not imported until an extension that declares those
domains is active.  Generic fixture configs and minimal modes do not
trigger domain-specific imports.

ADRs: ADR-0012 (core-kernel-boundary), ADR-0015 (extension-package-manifests).
"""

from __future__ import annotations

import importlib
import threading
from dataclasses import dataclass, field
from typing import Any

from millrace_ai.extensions.interfaces import (
    BUILTIN_INTERFACE_IDS,
    ArtifactAdapter,
    BlueprintContextProvider,
    BlueprintValidator,
    ClosureTransitionHandler,
    ContextProvider,
    LearningPromotionHandler,
    LearningTriggerHandler,
    ReconTransitionHandler,
)

# ---------------------------------------------------------------------------
# Built-in implementation module paths
# These are loaded lazily; no import occurs at registry construction time.
# ---------------------------------------------------------------------------

_BUILTIN_IMPLEMENTATION_PATHS: dict[str, tuple[str, str]] = {
    # Generic
    BUILTIN_INTERFACE_IDS["context_provider.generic"]: (
        "millrace_ai.extensions.builtin.generic_context_provider",
        "GenericBuiltInContextProvider",
    ),
    BUILTIN_INTERFACE_IDS["artifact_adapter.generic"]: (
        "millrace_ai.extensions.builtin.generic_artifact_adapter",
        "GenericBuiltInArtifactAdapter",
    ),
    # Recon
    BUILTIN_INTERFACE_IDS["recon_transition_handler"]: (
        "millrace_ai.extensions.builtin.recon_transition_handler",
        "BuiltInReconTransitionHandler",
    ),
    # Closure
    BUILTIN_INTERFACE_IDS["closure_transition_handler"]: (
        "millrace_ai.extensions.builtin.closure_transition_handler",
        "BuiltInClosureTransitionHandler",
    ),
    # Blueprint
    BUILTIN_INTERFACE_IDS["blueprint_validator"]: (
        "millrace_ai.extensions.builtin.blueprint_validator",
        "BuiltInBlueprintValidator",
    ),
    BUILTIN_INTERFACE_IDS["blueprint_context_provider"]: (
        "millrace_ai.extensions.builtin.blueprint_context_provider",
        "BuiltInBlueprintContextProvider",
    ),
    # Learning
    BUILTIN_INTERFACE_IDS["learning_trigger_handler"]: (
        "millrace_ai.extensions.builtin.learning_trigger_handler",
        "BuiltInLearningTriggerHandler",
    ),
    BUILTIN_INTERFACE_IDS["learning_promotion_handler"]: (
        "millrace_ai.extensions.builtin.learning_promotion_handler",
        "BuiltInLearningPromotionHandler",
    ),
}


@dataclass(slots=True)
class BuiltInExtensionBoundaryRegistry:
    """Thread-safe registry of built-in extension boundary implementations.

    Implementations are loaded lazily on first access.  Once loaded, an
    implementation is cached for the lifetime of the registry.  Only
    domain-specific implementations that are actually requested trigger
    imports of their respective domain modules.
    """

    _instances: dict[str, Any] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def get_recon_transition_handler(self) -> ReconTransitionHandler:
        """Return the built-in Recon transition handler."""
        return self._resolve(
            BUILTIN_INTERFACE_IDS["recon_transition_handler"],
            ReconTransitionHandler,
        )

    def get_closure_transition_handler(self) -> ClosureTransitionHandler:
        """Return the built-in closure transition handler."""
        return self._resolve(
            BUILTIN_INTERFACE_IDS["closure_transition_handler"],
            ClosureTransitionHandler,
        )

    def get_learning_trigger_handler(self) -> LearningTriggerHandler:
        """Return the built-in learning trigger handler."""
        return self._resolve(
            BUILTIN_INTERFACE_IDS["learning_trigger_handler"],
            LearningTriggerHandler,
        )

    def get_learning_promotion_handler(self) -> LearningPromotionHandler:
        """Return the built-in learning promotion handler."""
        return self._resolve(
            BUILTIN_INTERFACE_IDS["learning_promotion_handler"],
            LearningPromotionHandler,
        )

    def get_blueprint_validator(self) -> BlueprintValidator:
        """Return the built-in Blueprint validator."""
        return self._resolve(
            BUILTIN_INTERFACE_IDS["blueprint_validator"],
            BlueprintValidator,
        )

    def get_blueprint_context_provider(self) -> BlueprintContextProvider:
        """Return the built-in Blueprint context provider."""
        return self._resolve(
            BUILTIN_INTERFACE_IDS["blueprint_context_provider"],
            BlueprintContextProvider,
        )

    def get_context_provider(self) -> ContextProvider:
        """Return the built-in generic context provider."""
        return self._resolve(
            BUILTIN_INTERFACE_IDS["context_provider.generic"],
            ContextProvider,
        )

    def get_artifact_adapter(self) -> ArtifactAdapter:
        """Return the built-in generic artifact adapter."""
        return self._resolve(
            BUILTIN_INTERFACE_IDS["artifact_adapter.generic"],
            ArtifactAdapter,
        )

    def has_interface(self, interface_id: str) -> bool:
        """Return whether a built-in implementation is registered for the given interface ID."""
        return interface_id in _BUILTIN_IMPLEMENTATION_PATHS

    def _resolve(self, interface_id: str, _protocol_type: type) -> Any:
        with self._lock:
            instance = self._instances.get(interface_id)
            if instance is not None:
                return instance

            spec = _BUILTIN_IMPLEMENTATION_PATHS.get(interface_id)
            if spec is None:
                raise ValueError(
                    f"No built-in implementation registered for extension "
                    f"interface {interface_id!r}"
                )

            module_name, class_name = spec
            module = importlib.import_module(module_name)
            cls = getattr(module, class_name)
            instance = cls()
            self._instances[interface_id] = instance
            return instance


# Process-wide singleton registry.
_builtin_boundary_registry: BuiltInExtensionBoundaryRegistry | None = None
_registry_lock: threading.Lock = threading.Lock()


def builtin_extension_boundary_registry() -> BuiltInExtensionBoundaryRegistry:
    """Return the process-wide built-in extension boundary registry singleton."""
    global _builtin_boundary_registry
    if _builtin_boundary_registry is None:
        with _registry_lock:
            if _builtin_boundary_registry is None:
                _builtin_boundary_registry = BuiltInExtensionBoundaryRegistry()
    return _builtin_boundary_registry


__all__ = [
    "BuiltInExtensionBoundaryRegistry",
    "builtin_extension_boundary_registry",
]
