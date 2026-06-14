"""Extension boundary interface protocols.

Defines the contract surface that built-in and third-party extension
implementations must satisfy.  Each interface corresponds to one
ExtensionDomain × ExtensionItemKind pair and serves as the
compiler-validated boundary between the runtime kernel and domain-specific
behavior.

Domain-specific runtime code that previously imported behaviour modules
directly should resolve implementations through the
BuiltInExtensionBoundaryRegistry by interface ID, never by hard-coded
module path.

ADRs: ADR-0012 (core-kernel-boundary), ADR-0015 (extension-package-manifests).
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from millrace_ai.contracts import StageResultEnvelope
from millrace_ai.contracts.router import RouterDecision

if TYPE_CHECKING:
    from millrace_ai.architecture import CompiledRunPlan
    from millrace_ai.runners import StageRunRequest
    from millrace_ai.runtime.engine import RuntimeEngine


# ---------------------------------------------------------------------------
# Generic domain interfaces
# ---------------------------------------------------------------------------


@runtime_checkable
class ContextProvider(Protocol):
    """Extension-owned request-context provider interface.

    Resolves per-request context plan inputs (visible artifacts,
    included/omitted/redacted provider sets, inline sections, output
    contract references) from the active stage request and compiled plan.
    """

    interface_id: str
    domain: str  # ExtensionDomain.GENERIC value

    def build_context_plan(
        self,
        workspace_root: Path,
        request: "StageRunRequest",
        authority: object,
        compiled_plan: "CompiledRunPlan | None",
    ) -> object:
        ...


@runtime_checkable
class ArtifactAdapter(Protocol):
    """Extension-owned artifact adapter interface.

    Loads, validates, and optionally renders a specific artifact contract
    (e.g. Blueprint manifests, Recon packets, generated tasks) so that
    runtime stages receive typed, contract-validated inputs.
    """

    interface_id: str
    domain: str  # ExtensionDomain.GENERIC value
    artifact_contract_id: str

    def load_artifact(
        self,
        artifact_path: Path,
        *,
        workspace_root: Path,
    ) -> object:
        ...

    def validate_artifact(
        self,
        artifact: object,
        *,
        workspace_root: Path,
    ) -> None:
        ...


# ---------------------------------------------------------------------------
# Recon domain interfaces
# ---------------------------------------------------------------------------


@runtime_checkable
class ReconTransitionHandler(Protocol):
    """Extension-owned Recon transition interface.

    Handles Recon probe routing results: persists canonical Recon packets,
    validates handoff contracts, and enqueues generated tasks/specs or
    blocks/no-ops the probe through compiled runtime-operation authority.
    """

    interface_id: str
    domain: str  # ExtensionDomain.RECON value

    def is_recon_stage_result(self, stage_result: StageResultEnvelope) -> bool:
        ...

    def apply_router_decision(
        self,
        engine: "RuntimeEngine",
        decision: RouterDecision,
        stage_result: StageResultEnvelope,
        *,
        stage_result_path: Path | None = None,
        compiled_plan: "CompiledRunPlan | None" = None,
    ) -> tuple[Path, ...]:
        ...


# ---------------------------------------------------------------------------
# Closure domain interfaces
# ---------------------------------------------------------------------------


@runtime_checkable
class ClosureTransitionHandler(Protocol):
    """Extension-owned closure transition interface.

    Handles Arbiter closure-target routing results: closes or keeps-open
    closure targets, persists verdict/report paths, enqueues remediation
    incidents, and manages lineage-drift diagnostics.
    """

    interface_id: str
    domain: str  # ExtensionDomain.CLOSURE value

    def is_closure_target_result(self, stage_result: StageResultEnvelope) -> bool:
        ...

    def apply_router_decision(
        self,
        engine: "RuntimeEngine",
        decision: RouterDecision,
        stage_result: StageResultEnvelope,
    ) -> None:
        ...


# ---------------------------------------------------------------------------
# Blueprint domain interfaces
# ---------------------------------------------------------------------------


@runtime_checkable
class BlueprintValidator(Protocol):
    """Extension-owned Blueprint validation interface.

    Validates Blueprint artifacts (manifests, drafts, packets, evaluations,
    generated tasks) for structural correctness before runtime mutations
    are applied.
    """

    interface_id: str
    domain: str  # ExtensionDomain.BLUEPRINT value

    def validate_manifest(self, manifest: object) -> None:
        ...

    def validate_draft(self, draft: object) -> None:
        ...

    def validate_packet(self, packet: object) -> None:
        ...

    def validate_evaluation(self, evaluation: object) -> None:
        ...

    def validate_generated_task(self, task: object) -> None:
        ...


@runtime_checkable
class BlueprintContextProvider(Protocol):
    """Extension-owned Blueprint context provider interface.

    Resolves Blueprint-specific per-request context (active draft,
    manifest, candidate packets, critique/evaluation history, repair
    failure evidence) for Manager, Contractor, Evaluator, and Mechanic
    Blueprint stages.
    """

    interface_id: str
    domain: str  # ExtensionDomain.BLUEPRINT value

    def build_context_plan(
        self,
        workspace_root: Path,
        request: "StageRunRequest",
        authority: object,
        compiled_plan: "CompiledRunPlan | None",
    ) -> object:
        ...


# ---------------------------------------------------------------------------
# Learning domain interfaces
# ---------------------------------------------------------------------------


@runtime_checkable
class LearningTriggerHandler(Protocol):
    """Extension-owned learning trigger handler interface.

    Evaluates compiler-frozen learning trigger rules after stage result
    application and enqueues matching Learning request documents into
    the Learning queue.
    """

    interface_id: str
    domain: str  # ExtensionDomain.LEARNING value

    def enqueue_learning_requests(
        self,
        engine: "RuntimeEngine",
        *,
        stage_result: StageResultEnvelope,
        stage_result_path: Path,
    ) -> tuple[Path, ...]:
        ...


@runtime_checkable
class LearningPromotionHandler(Protocol):
    """Extension-owned learning promotion handler interface.

    Manages Curator promotion boundaries: defers promotion when foreground
    planes are active and applies deferred promotions when foreground lanes
    drain.
    """

    interface_id: str
    domain: str  # ExtensionDomain.LEARNING value

    def handle_curator_promotion(
        self,
        engine: "RuntimeEngine",
        *,
        stage_result: StageResultEnvelope,
    ) -> None:
        ...

    def apply_deferred_promotions(self, engine: "RuntimeEngine") -> int:
        ...


# ---------------------------------------------------------------------------
# Extension boundary interface ID contract
# ---------------------------------------------------------------------------

# Canonical interface IDs for built-in domain boundaries.
# These are the `interface_id` values that built-in implementations
# register under the BuiltInExtensionBoundaryRegistry.

BUILTIN_INTERFACE_IDS: dict[str, str] = {
    # Generic
    "context_provider.generic": "context_provider.generic",
    "artifact_adapter.generic": "artifact_adapter.generic",
    # Recon
    "recon_transition_handler": "recon_transition_handler",
    # Closure
    "closure_transition_handler": "closure_transition_handler",
    # Blueprint
    "blueprint_validator": "blueprint_validator",
    "blueprint_context_provider": "blueprint_context_provider",
    # Learning
    "learning_trigger_handler": "learning_trigger_handler",
    "learning_promotion_handler": "learning_promotion_handler",
}


__all__ = [
    "ArtifactAdapter",
    "BlueprintContextProvider",
    "BlueprintValidator",
    "BUILTIN_INTERFACE_IDS",
    "ClosureTransitionHandler",
    "ContextProvider",
    "LearningPromotionHandler",
    "LearningTriggerHandler",
    "ReconTransitionHandler",
]
