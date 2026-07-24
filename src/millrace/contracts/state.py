"""Immutable in-memory runtime state records for kernel transitions."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import ClassVar, TypeVar, cast

from millrace.contracts.compiled_plan import (
    AuthorityValue,
    SelectedCompiledPlan,
    freeze_authority_mapping,
)
from millrace.contracts.fingerprints import AuthorityFingerprint
from millrace.contracts.ids import (
    ActionId,
    ArtifactSchemaId,
    CompletionBehaviorId,
    CounterId,
    EffectDeclarationId,
    FanoutId,
    OperatorWaitId,
    QueueFamilyId,
    RecoveryPolicyId,
    RemediationPolicyId,
    RunnerBindingId,
    StageKindId,
)

K = TypeVar("K")
T = TypeVar("T")
DURABLE_INT64_MAX = 2**63 - 1


def _freeze_mapping(value: Mapping[K, T]) -> Mapping[K, T]:
    return MappingProxyType(dict(value))


@dataclass(frozen=True, slots=True)
class PlanRef:
    plan_id: str
    authority_fingerprint: AuthorityFingerprint
    plan_format_version: int


@dataclass(frozen=True, slots=True)
class WorkItemRef:
    work_item_id: str
    plan_ref: PlanRef
    generation: int


@dataclass(frozen=True, slots=True)
class RunRef:
    run_id: str
    work_item_id: str
    claim_id: str
    plan_ref: PlanRef
    generation: int
    fencing_token: str


@dataclass(frozen=True, slots=True)
class InputReceiptRef:
    input_id: str
    input_payload_digest: str


@dataclass(frozen=True, slots=True)
class InputReceipt:
    receipt_ref: InputReceiptRef
    transition_id: str
    accepted: bool = True
    refusal_reason: str | None = None


@dataclass(frozen=True, slots=True)
class ExternalEnqueueRoute:
    queue_family_id: QueueFamilyId
    graph_node_id: str
    stage_kind_id: StageKindId
    runner_binding_id: RunnerBindingId
    payload_schema_id: ArtifactSchemaId | None = None


@dataclass(frozen=True, slots=True)
class AdmittedPlan:
    plan_ref: PlanRef
    selected_plan: SelectedCompiledPlan
    external_enqueue_routes: Mapping[QueueFamilyId, ExternalEnqueueRoute] = field(
        default_factory=dict
    )

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "external_enqueue_routes",
            _freeze_mapping(self.external_enqueue_routes),
        )


@dataclass(frozen=True, slots=True)
class WorkItem:
    ref: WorkItemRef
    queue_family_id: QueueFamilyId
    payload: Mapping[str, AuthorityValue]
    lineage_id: str | None
    created_by_input_id: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "payload",
            freeze_authority_mapping(cast(Mapping[str, object], self.payload)),
        )


@dataclass(frozen=True, slots=True)
class Activation:
    activation_id: str
    work_item_id: str
    lineage_id: str | None
    plan_ref: PlanRef
    queue_family_id: QueueFamilyId
    graph_node_id: str
    stage_kind_id: StageKindId
    runner_binding_id: RunnerBindingId
    generation: int
    created_by_input_id: str
    claimed_by_run_id: str | None = None


@dataclass(frozen=True, slots=True)
class RunRecord:
    run_ref: RunRef
    work_item_id: str
    activation_id: str
    stage_kind_id: StageKindId
    runner_binding_id: RunnerBindingId
    created_by_input_id: str


@dataclass(frozen=True, slots=True)
class RunnerObservationRecord:
    observation_id: str
    run_id: str
    payload: Mapping[str, AuthorityValue]
    created_by_input_id: str
    observed_at: int | None

    def __post_init__(self) -> None:
        if self.observed_at is not None and type(self.observed_at) is not int:
            raise ValueError("observed_at must be an integer")
        if self.observed_at is not None and self.observed_at < 0:
            raise ValueError("observed_at must be non-negative")
        if (
            self.observed_at is not None
            and self.observed_at > DURABLE_INT64_MAX
        ):
            raise ValueError("observed_at exceeds durable integer range")
        object.__setattr__(
            self,
            "payload",
            freeze_authority_mapping(cast(Mapping[str, object], self.payload)),
        )


@dataclass(frozen=True, slots=True)
class ArtifactRecord:
    artifact_id: str
    work_item_id: str
    schema_id: ArtifactSchemaId
    payload: Mapping[str, AuthorityValue]
    created_by_input_id: str
    source_run_id: str
    source_action_id: ActionId
    source_stage_kind_id: StageKindId
    source_graph_node_id: str
    payload_digest: str
    transition_id: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "payload",
            freeze_authority_mapping(cast(Mapping[str, object], self.payload)),
        )


@dataclass(frozen=True, slots=True)
class EffectProposalRecord:
    record_kind: ClassVar[str] = "effect_proposal"
    schema_version: ClassVar[int] = 1

    effect_id: str
    dedupe_key: str
    effect_declaration_id: EffectDeclarationId
    selected_plan_ref: PlanRef
    selected_plan_fingerprint: AuthorityFingerprint
    terminal_action_id: ActionId
    artifact_id: str
    artifact_schema_id: ArtifactSchemaId
    artifact_payload_digest: str
    source_run_id: str
    source_action_id: ActionId
    source_input_id: str
    source_work_item_id: str
    source_activation_id: str
    source_graph_node_id: str
    source_stage_kind_id: StageKindId
    source_runner_binding_id: RunnerBindingId
    source_queue_family_id: QueueFamilyId
    lineage_id: str | None
    provider_ref: str
    capability_policy_ref: str
    target_ref_kind: str
    target_ref_schema: str
    target_skill_id: str | None
    target_path_ref: str | None
    status: str
    created_input_id: str
    created_transition_id: str


@dataclass(frozen=True, slots=True)
class EffectReconciliationRecord:
    record_kind: ClassVar[str] = "effect_reconciliation"
    schema_version: ClassVar[int] = 1

    reconciliation_id: str
    effect_id: str
    selected_plan_ref: PlanRef
    selected_plan_fingerprint: AuthorityFingerprint
    provider_ref: str
    status: str
    fake_local_result_digest: str
    created_input_id: str
    created_transition_id: str


@dataclass(frozen=True, slots=True)
class ActivationRouteRecord:
    record_id: str
    action_id: ActionId
    source_run_id: str
    source_work_item_id: str
    target_work_item_id: str
    target_activation_id: str
    created_by_input_id: str


@dataclass(frozen=True, slots=True)
class FanoutRecord:
    record_id: str
    fanout_id: FanoutId
    source_artifact_id: str
    source_artifact_digest: str
    source_work_item_id: str
    source_run_id: str
    source_action_id: ActionId
    target_work_item_id: str
    target_activation_id: str
    target_queue_family_id: QueueFamilyId
    target_stage_kind_id: StageKindId
    target_graph_node_id: str
    item_key: str
    lineage_id: str | None
    selected_plan_ref: PlanRef
    created_by_input_id: str


@dataclass(frozen=True, slots=True)
class WorkDependencyRecord:
    dependency_id: str
    dependent_work_item_id: str
    dependency_work_item_id: str
    selected_plan_ref: PlanRef
    lineage_id: str | None
    fanout_record_id: str
    created_by_input_id: str


@dataclass(frozen=True, slots=True)
class ClosureTargetRecord:
    closure_target_id: str
    selected_plan_ref: PlanRef
    completion_behavior_id: CompletionBehaviorId
    lineage_id: str
    root_source_kind: str
    root_source_id: str
    closure_root_work_item_id: str | None
    request_kind: str
    target_graph_node_id: str
    evidence_window: Mapping[str, AuthorityValue]
    status: str
    opened_by_input_id: str
    closed_by_record_id: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "evidence_window",
            freeze_authority_mapping(
                cast(Mapping[str, object], self.evidence_window)
            ),
        )


@dataclass(frozen=True, slots=True)
class ClosureEvaluationRecord:
    record_id: str
    closure_target_id: str
    completion_behavior_id: CompletionBehaviorId
    request_kind: str
    target_work_item_id: str
    target_activation_id: str
    selected_plan_ref: PlanRef
    lineage_id: str
    created_by_input_id: str


@dataclass(frozen=True, slots=True)
class ClosureTerminalRecord:
    record_id: str
    closure_target_id: str
    completion_behavior_id: CompletionBehaviorId
    terminal_kind: str
    source_run_id: str
    source_action_id: ActionId
    source_artifact_id: str | None
    selected_plan_ref: PlanRef
    lineage_id: str
    created_by_input_id: str


@dataclass(frozen=True, slots=True)
class RemediationWorkRecord:
    record_id: str
    remediation_policy_id: RemediationPolicyId
    closure_target_id: str
    source_run_id: str
    source_action_id: ActionId
    source_artifact_id: str | None
    target_work_item_id: str
    target_activation_id: str
    selected_plan_ref: PlanRef
    lineage_id: str
    dedupe_key: str
    created_by_input_id: str


@dataclass(frozen=True, slots=True)
class ClosureBlockedRecord:
    record_id: str
    closure_target_id: str
    completion_behavior_id: CompletionBehaviorId
    source_run_id: str
    source_action_id: ActionId
    selected_plan_ref: PlanRef
    lineage_id: str
    operator_required: bool
    created_by_input_id: str


@dataclass(frozen=True, slots=True)
class ClosedWorkItemRecord:
    record_id: str
    work_item_id: str
    source_run_id: str | None
    action_id: ActionId | None
    created_by_input_id: str
    operator_intervention_record_id: str | None = None
    close_kind: str = "terminal_action"


@dataclass(frozen=True, slots=True)
class PauseRecord:
    record_id: str
    source_run_id: str
    work_item_id: str
    action_id: ActionId
    created_by_input_id: str


@dataclass(frozen=True, slots=True)
class QuarantineRecord:
    record_id: str
    work_item_id: str
    source_run_id: str
    action_id: ActionId
    created_by_input_id: str


@dataclass(frozen=True, slots=True)
class LineageQuarantineRecord:
    quarantine_id: str
    policy_id: RecoveryPolicyId
    lineage_id: str
    selected_plan_ref: PlanRef
    selected_plan_fingerprint: AuthorityFingerprint
    recovery_attempt_record_id: str
    original_source_run_id: str
    original_source_work_item_id: str
    original_source_activation_id: str
    emitting_recovery_activation_id: str
    emitting_recovery_run_id: str
    action_id: ActionId
    attempt_count: int
    created_input_id: str
    actor_kind: str
    status: str
    superseded_input_id: str | None


@dataclass(frozen=True, slots=True)
class RecoveryAttemptRecord:
    record_id: str
    policy_id: RecoveryPolicyId
    lineage_id: str
    plan_ref: PlanRef
    attempt_count: int
    phase: str
    source_run_id: str
    source_work_item_id: str
    source_activation_id: str
    source_graph_node_id: str
    source_stage_kind_id: StageKindId
    source_runner_binding_id: RunnerBindingId
    source_queue_family_id: QueueFamilyId
    recovery_action_id: ActionId
    latest_recovery_activation_id: str | None
    latest_recovery_run_id: str | None
    latest_return_action_id: ActionId | None
    created_by_input_id: str
    updated_by_input_id: str


@dataclass(frozen=True, slots=True)
class OperatorInterventionRecord:
    record_kind: ClassVar[str] = "operator_intervention"
    schema_version: ClassVar[int] = 1

    record_id: str
    created_by_input_id: str
    input_payload_digest: str
    option_id: str
    kind: str
    result: str
    policy_id: RecoveryPolicyId
    lineage_id: str
    quarantine_id: str
    recovery_attempt_record_id: str
    recovery_attempt_count: int
    attempt_effect: str
    selected_plan_ref: PlanRef
    selected_plan_fingerprint: AuthorityFingerprint
    actor_kind: str
    actor_id: str
    reason: str
    target_work_item_id: str | None
    target_activation_id: str | None
    closed_work_item_ids: tuple[str, ...]
    closed_activation_ids: tuple[str, ...]
    closed_run_ids: tuple[str, ...]
    payload_digest: str
    payload_reference: str | None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "closed_work_item_ids",
            tuple(self.closed_work_item_ids),
        )
        object.__setattr__(
            self,
            "closed_activation_ids",
            tuple(self.closed_activation_ids),
        )
        object.__setattr__(self, "closed_run_ids", tuple(self.closed_run_ids))


@dataclass(frozen=True, slots=True)
class OperatorWaitRecord:
    record_kind: ClassVar[str] = "operator_wait"
    schema_version: ClassVar[int] = 1

    wait_id: str
    operator_wait_id: OperatorWaitId
    source_action_id: ActionId
    lineage_id: str
    selected_plan_ref: PlanRef
    selected_plan_fingerprint: AuthorityFingerprint
    source_work_item_id: str
    source_activation_id: str
    source_run_id: str
    source_stage_kind_id: StageKindId
    source_graph_node_id: str
    source_queue_family_id: QueueFamilyId
    source_runner_binding_id: RunnerBindingId
    source_artifact_id: str | None
    status: str
    created_input_id: str
    created_input_payload_digest: str
    resolved_input_id: str | None
    resolved_input_payload_digest: str | None
    actor_id: str | None
    actor_kind: str | None
    resolution_kind: str | None
    target_work_item_id: str | None = None
    target_activation_id: str | None = None
    closed_work_item_ids: tuple[str, ...] = ()
    payload_digest: str | None = None
    payload_reference: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "closed_work_item_ids",
            tuple(self.closed_work_item_ids),
        )


@dataclass(frozen=True, slots=True)
class CooldownWaitRecord:
    wait_id: str
    policy_id: RecoveryPolicyId
    lineage_id: str
    recovery_attempt_record_id: str
    attempt_count: int
    source_run_id: str
    source_work_item_id: str
    source_activation_id: str
    recovery_action_id: ActionId
    target_stage_kind_id: StageKindId
    target_graph_node_id: str
    target_runner_binding_id: RunnerBindingId
    plan_ref: PlanRef
    created_input_id: str
    created_at: int
    due_at: int
    consumed_input_id: str | None
    consumed_at: int | None
    resulting_recovery_activation_id: str | None


@dataclass(frozen=True, slots=True)
class CounterRecord:
    record_id: str
    counter_id: CounterId
    selected_plan_ref: PlanRef
    lineage_id: str
    value: int
    updated_by_input_id: str


@dataclass(frozen=True, slots=True)
class TransitionRecord:
    record_kind: ClassVar[str] = "transition_record"
    schema_version: ClassVar[int] = 1

    record_id: str
    input_id: str
    input_kind: str
    input_family: str
    accepted: bool


@dataclass(frozen=True, slots=True)
class TransitionRefusal:
    record_kind: ClassVar[str] = "transition_refusal"
    schema_version: ClassVar[int] = 1

    record_id: str
    input_id: str
    input_kind: str
    input_family: str
    reason: str
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class GovernanceEventRecord:
    record_kind: ClassVar[str] = "governance_event"
    schema_version: ClassVar[int] = 1

    record_id: str
    input_id: str
    input_kind: str
    input_family: str
    disposition: str
    plan_fingerprint: AuthorityFingerprint | None
    work_item_id: str | None
    run_id: str | None
    action_id: ActionId | None
    authority_source: str | None
    refusal_reason: str | None = None


@dataclass(frozen=True, slots=True)
class TraceRecord:
    record_kind: ClassVar[str] = "trace"
    schema_version: ClassVar[int] = 1

    record_id: str
    input_id: str
    input_kind: str
    input_family: str
    disposition: str
    plan_fingerprint: AuthorityFingerprint | None
    work_item_id: str | None
    run_id: str | None
    action_id: ActionId | None
    authority_source: str | None
    refusal_reason: str | None = None


@dataclass(frozen=True, slots=True)
class RuntimeState:
    admitted_plans: Mapping[AuthorityFingerprint, AdmittedPlan] = field(
        default_factory=dict
    )
    default_plan_ref: PlanRef | None = None
    receipts: Mapping[str, InputReceipt] = field(default_factory=dict)
    work_items: Mapping[str, WorkItem] = field(default_factory=dict)
    activations: Mapping[str, Activation] = field(default_factory=dict)
    runs: Mapping[str, RunRecord] = field(default_factory=dict)
    runner_observations: Mapping[str, RunnerObservationRecord] = field(
        default_factory=dict
    )
    artifacts: Mapping[str, ArtifactRecord] = field(default_factory=dict)
    effect_proposals: Mapping[str, EffectProposalRecord] = field(
        default_factory=dict
    )
    effect_reconciliations: Mapping[str, EffectReconciliationRecord] = field(
        default_factory=dict
    )
    activation_routes: tuple[ActivationRouteRecord, ...] = ()
    fanout_records: Mapping[str, FanoutRecord] = field(default_factory=dict)
    work_dependencies: Mapping[str, WorkDependencyRecord] = field(default_factory=dict)
    closure_targets: Mapping[str, ClosureTargetRecord] = field(default_factory=dict)
    closure_evaluations: Mapping[str, ClosureEvaluationRecord] = (
        field(default_factory=dict)
    )
    closure_terminal_records: Mapping[str, ClosureTerminalRecord] = field(
        default_factory=dict
    )
    remediation_work_records: Mapping[str, RemediationWorkRecord] = field(
        default_factory=dict
    )
    closure_blocked_records: Mapping[str, ClosureBlockedRecord] = field(
        default_factory=dict
    )
    closed_work_items: Mapping[str, ClosedWorkItemRecord] = field(default_factory=dict)
    pause: PauseRecord | None = None
    quarantines: Mapping[str, QuarantineRecord] = field(default_factory=dict)
    lineage_quarantines: Mapping[str, LineageQuarantineRecord] = field(
        default_factory=dict
    )
    recovery_attempts: Mapping[str, RecoveryAttemptRecord] = field(
        default_factory=dict
    )
    operator_interventions: Mapping[str, OperatorInterventionRecord] = field(
        default_factory=dict
    )
    operator_waits: Mapping[str, OperatorWaitRecord] = field(default_factory=dict)
    cooldown_waits: Mapping[str, CooldownWaitRecord] = field(default_factory=dict)
    counters: Mapping[str, CounterRecord] = field(default_factory=dict)
    governance_events: tuple[GovernanceEventRecord, ...] = ()
    traces: tuple[TraceRecord, ...] = ()
    transitions: tuple[TransitionRecord, ...] = ()
    refusals: tuple[TransitionRefusal, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "admitted_plans", _freeze_mapping(self.admitted_plans))
        object.__setattr__(self, "receipts", _freeze_mapping(self.receipts))
        object.__setattr__(self, "work_items", _freeze_mapping(self.work_items))
        object.__setattr__(self, "activations", _freeze_mapping(self.activations))
        object.__setattr__(self, "runs", _freeze_mapping(self.runs))
        object.__setattr__(
            self,
            "runner_observations",
            _freeze_mapping(self.runner_observations),
        )
        object.__setattr__(self, "artifacts", _freeze_mapping(self.artifacts))
        object.__setattr__(
            self,
            "effect_proposals",
            _freeze_mapping(self.effect_proposals),
        )
        object.__setattr__(
            self,
            "effect_reconciliations",
            _freeze_mapping(self.effect_reconciliations),
        )
        object.__setattr__(self, "activation_routes", tuple(self.activation_routes))
        object.__setattr__(
            self,
            "fanout_records",
            _freeze_mapping(self.fanout_records),
        )
        object.__setattr__(
            self,
            "work_dependencies",
            _freeze_mapping(self.work_dependencies),
        )
        object.__setattr__(
            self,
            "closure_targets",
            _freeze_mapping(self.closure_targets),
        )
        object.__setattr__(
            self,
            "closure_evaluations",
            _freeze_mapping(self.closure_evaluations),
        )
        object.__setattr__(
            self,
            "closure_terminal_records",
            _freeze_mapping(self.closure_terminal_records),
        )
        object.__setattr__(
            self,
            "remediation_work_records",
            _freeze_mapping(self.remediation_work_records),
        )
        object.__setattr__(
            self,
            "closure_blocked_records",
            _freeze_mapping(self.closure_blocked_records),
        )
        object.__setattr__(
            self,
            "closed_work_items",
            _freeze_mapping(self.closed_work_items),
        )
        object.__setattr__(self, "quarantines", _freeze_mapping(self.quarantines))
        object.__setattr__(
            self,
            "lineage_quarantines",
            _freeze_mapping(self.lineage_quarantines),
        )
        object.__setattr__(
            self,
            "recovery_attempts",
            _freeze_mapping(self.recovery_attempts),
        )
        object.__setattr__(
            self,
            "operator_interventions",
            _freeze_mapping(self.operator_interventions),
        )
        object.__setattr__(
            self,
            "operator_waits",
            _freeze_mapping(self.operator_waits),
        )
        object.__setattr__(
            self,
            "cooldown_waits",
            _freeze_mapping(self.cooldown_waits),
        )
        object.__setattr__(self, "counters", _freeze_mapping(self.counters))
        object.__setattr__(
            self,
            "governance_events",
            tuple(self.governance_events),
        )
        object.__setattr__(self, "traces", tuple(self.traces))
        object.__setattr__(self, "transitions", tuple(self.transitions))
        object.__setattr__(self, "refusals", tuple(self.refusals))


__all__ = (
    "Activation",
    "ActivationRouteRecord",
    "AdmittedPlan",
    "ArtifactRecord",
    "ClosureEvaluationRecord",
    "ClosureBlockedRecord",
    "ClosureTargetRecord",
    "ClosureTerminalRecord",
    "ClosedWorkItemRecord",
    "CooldownWaitRecord",
    "DURABLE_INT64_MAX",
    "ExternalEnqueueRoute",
    "EffectProposalRecord",
    "EffectReconciliationRecord",
    "FanoutRecord",
    "GovernanceEventRecord",
    "InputReceipt",
    "InputReceiptRef",
    "LineageQuarantineRecord",
    "OperatorInterventionRecord",
    "OperatorWaitRecord",
    "PlanRef",
    "PauseRecord",
    "QuarantineRecord",
    "RecoveryAttemptRecord",
    "RemediationWorkRecord",
    "RunRecord",
    "RunRef",
    "RunnerObservationRecord",
    "RuntimeState",
    "TraceRecord",
    "TransitionRecord",
    "TransitionRefusal",
    "WorkItem",
    "WorkItemRef",
    "WorkDependencyRecord",
)
