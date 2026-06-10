"""Extension package manifest contracts, boundary interfaces, and registry.

Extension packages contribute new runtime vocabulary items (operation
runners, terminal actions, context providers, document adapters, claim
policies, recovery policies, failure policies) without modifying the
kernel.  The compiler validates extension manifests at compile time;
the runtime loader activates items when referenced by compiled plan
metadata.

Extension boundary interfaces define the contract surface for each
domain (generic, Recon, closure, Blueprint, Learning) that runtime
code resolves through the BuiltInExtensionBoundaryRegistry rather than
through direct domain-module imports.

ADRs: ADR-0012 (core-kernel-boundary), ADR-0015 (extension-package-manifests).
"""

from __future__ import annotations

from millrace_ai.extensions.boundaries import (
    BuiltInExtensionBoundaryRegistry,
    builtin_extension_boundary_registry,
)
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
from millrace_ai.extensions.manifest import (
    ExtensionDomain,
    ExtensionItemKind,
    ExtensionItemManifest,
    ExtensionPackageManifest,
)

__all__ = [
    "ArtifactAdapter",
    "BlueprintContextProvider",
    "BlueprintValidator",
    "BUILTIN_INTERFACE_IDS",
    "BuiltInExtensionBoundaryRegistry",
    "builtin_extension_boundary_registry",
    "ClosureTransitionHandler",
    "ContextProvider",
    "ExtensionDomain",
    "ExtensionItemKind",
    "ExtensionItemManifest",
    "ExtensionPackageManifest",
    "LearningPromotionHandler",
    "LearningTriggerHandler",
    "ReconTransitionHandler",
]
