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
_RUNNER_SESSION_STATES = frozenset(
    {
        "created",
        "starting",
        "running",
        "cancellation_requested",
        "terminating",
        "completed",
        "interrupted",
        "failed",
        "lost",
    }
)
_RUNNER_SESSION_TERMINAL_STATES = frozenset(
    {"completed", "interrupted", "failed", "lost"}
)
_RUNNER_SESSION_CLEANUP_DISPOSITIONS = frozenset(
    {"pending", "not_required", "complete", "orphan_risk"}
)


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
    current_session_id: str | None = None
    last_dispatch_generation: int = 0


@dataclass(frozen=True, slots=True)
class RunnerSessionRecord:
    record_kind: ClassVar[str] = "runner_session"
    schema_version: ClassVar[int] = 1

    session_id: str
    run_id: str
    dispatch_generation: int
    session_fencing_token: str
    state: str
    created_at: int
    start_intent_at: int | None
    started_at: int | None
    ended_at: int | None
    durable_locator_digest: str | None
    cleanup_disposition: str

    def __post_init__(self) -> None:
        for field_name in ("session_id", "run_id", "session_fencing_token"):
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field_name} must be non-blank")
        if type(self.dispatch_generation) is not int or self.dispatch_generation < 1:
            raise ValueError("dispatch_generation must be a positive integer")
        for field_name in (
            "created_at",
            "start_intent_at",
            "started_at",
            "ended_at",
        ):
            value = getattr(self, field_name)
            if value is not None and (
                type(value) is not int
                or value < 0
                or value > DURABLE_INT64_MAX
            ):
                raise ValueError(f"{field_name} must be a durable non-negative integer")
        timestamps = tuple(
            value
            for value in (
                self.created_at,
                self.start_intent_at,
                self.started_at,
                self.ended_at,
            )
            if value is not None
        )
        if timestamps != tuple(sorted(timestamps)):
            raise ValueError("runner session timestamps must be monotonic")
        if self.state not in _RUNNER_SESSION_STATES:
            raise ValueError("unsupported runner session state")
        if self.cleanup_disposition not in _RUNNER_SESSION_CLEANUP_DISPOSITIONS:
            raise ValueError("unsupported runner session cleanup disposition")
        if (
            self.durable_locator_digest is not None
            and not _is_sha256_digest(self.durable_locator_digest)
        ):
            raise ValueError("durable_locator_digest must be a sha256 digest")
        if self.state == "created" and self.durable_locator_digest is not None:
            raise ValueError("created runner session cannot have a durable locator")
        if self.started_at is not None and self.start_intent_at is None:
            raise ValueError("runner session started_at requires start_intent_at")
        if self.state == "created" and (
            self.start_intent_at is not None or self.started_at is not None
        ):
            raise ValueError("created runner session cannot have start timestamps")
        if self.state == "starting" and (
            self.start_intent_at is None or self.started_at is not None
        ):
            raise ValueError("starting runner session timestamps are contradictory")
        if self.state == "running" and (
            self.start_intent_at is None or self.started_at is None
        ):
            raise ValueError("running runner session requires start timestamps")
        if self.state in _RUNNER_SESSION_TERMINAL_STATES:
            if self.ended_at is None:
                raise ValueError("terminal runner session must have ended_at")
            if self.state == "lost":
                if self.cleanup_disposition != "orphan_risk":
                    raise ValueError("lost runner session must retain orphan risk")
            elif self.cleanup_disposition not in {"not_required", "complete"}:
                raise ValueError("terminal runner session cleanup is incomplete")
        elif self.ended_at is not None:
            raise ValueError("nonterminal runner session cannot have ended_at")


@dataclass(frozen=True, slots=True)
class RunnerSessionCancellationRecord:
    record_kind: ClassVar[str] = "runner_session_cancellation"
    schema_version: ClassVar[int] = 1

    request_id: str
    session_id: str
    dispatch_generation: int
    reason: str
    source_kind: str
    actor_id: str
    requested_at: int
    request_order: int
    primary: bool

    def __post_init__(self) -> None:
        _validate_non_blank_fields(
            self,
            ("request_id", "session_id", "actor_id"),
        )
        _validate_positive_integer(
            "dispatch_generation",
            self.dispatch_generation,
        )
        _validate_positive_integer("request_order", self.request_order)
        _validate_durable_timestamp("requested_at", self.requested_at)
        if self.reason not in {
            "operator_cancel_work",
            "daemon_shutdown",
            "runner_timeout",
            "runtime_failure",
        }:
            raise ValueError("unsupported runner session cancellation reason")
        if self.source_kind not in {"operator", "daemon", "runtime"}:
            raise ValueError("unsupported runner session cancellation source")
        if type(self.primary) is not bool:
            raise ValueError("primary must be a boolean")


@dataclass(frozen=True, slots=True)
class RunnerSessionCancellationAttemptRecord:
    record_kind: ClassVar[str] = "runner_session_cancellation_attempt"
    schema_version: ClassVar[int] = 1

    attempt_id: str
    session_id: str
    request_id: str
    sequence: int
    operation: str
    result: str
    started_at: int
    completed_at: int
    bounded_diagnostic_digest: str

    def __post_init__(self) -> None:
        _validate_non_blank_fields(
            self,
            ("attempt_id", "session_id", "request_id"),
        )
        _validate_positive_integer("sequence", self.sequence)
        _validate_durable_timestamp("started_at", self.started_at)
        _validate_durable_timestamp("completed_at", self.completed_at)
        if self.completed_at < self.started_at:
            raise ValueError("cancellation attempt timestamps must be monotonic")
        if self.operation not in {
            "cooperative_cancel",
            "terminate",
            "kill",
            "transport_cleanup",
        }:
            raise ValueError("unsupported runner session cancellation operation")
        if self.result not in {"succeeded", "failed", "timed_out", "unsupported"}:
            raise ValueError("unsupported runner session cancellation result")
        _validate_sha256_digest(
            "bounded_diagnostic_digest",
            self.bounded_diagnostic_digest,
        )


@dataclass(frozen=True, slots=True)
class RunnerSessionCompletionRecord:
    record_kind: ClassVar[str] = "runner_session_completion"
    schema_version: ClassVar[int] = 1

    completion_id: str
    session_id: str
    run_id: str
    dispatch_generation: int
    session_fencing_token: str
    terminal_state: str
    exit_kind: str
    adapter_outcome_kind: str | None
    adapter_error_kind: str | None
    runner_result_evidence_digest: str | None
    primary_cancellation_request_id: str | None
    cleanup_disposition: str
    started_at: int | None
    cancel_requested_at: int | None
    completed_at: int
    bounds_summary: str
    truncation_metadata: str
    redaction_policy_id: str
    diagnostic_digest: str
    application_input_id: str

    def __post_init__(self) -> None:
        _validate_non_blank_fields(
            self,
            (
                "completion_id",
                "session_id",
                "run_id",
                "session_fencing_token",
                "exit_kind",
                "bounds_summary",
                "truncation_metadata",
                "redaction_policy_id",
            ),
        )
        _validate_positive_integer(
            "dispatch_generation",
            self.dispatch_generation,
        )
        for field_name in ("started_at", "cancel_requested_at", "completed_at"):
            value = getattr(self, field_name)
            if value is not None:
                _validate_durable_timestamp(field_name, value)
        earlier_times = tuple(
            value
            for value in (self.started_at, self.cancel_requested_at)
            if value is not None
        )
        if any(value > self.completed_at for value in earlier_times):
            raise ValueError("runner session completion timestamps must be monotonic")
        if self.terminal_state not in _RUNNER_SESSION_TERMINAL_STATES:
            raise ValueError("unsupported runner session terminal state")
        if self.terminal_state == "lost":
            if self.cleanup_disposition != "orphan_risk":
                raise ValueError("lost runner completion must retain orphan risk")
        elif self.cleanup_disposition not in {"not_required", "complete"}:
            raise ValueError("runner session completion cleanup is incomplete")
        if (
            self.runner_result_evidence_digest is not None
            and self.terminal_state != "completed"
        ):
            raise ValueError("only completed sessions may carry runner result evidence")
        if (
            self.terminal_state == "completed"
            and self.runner_result_evidence_digest is None
        ):
            raise ValueError("completed runner session requires result evidence")
        for field_name in (
            "runner_result_evidence_digest",
            "diagnostic_digest",
        ):
            value = getattr(self, field_name)
            if value is not None:
                _validate_sha256_digest(field_name, value)
        expected_application_input_id = (
            f"cli:run.session-completion:{self.completion_id}"
        )
        if self.application_input_id != expected_application_input_id:
            raise ValueError("invalid runner session completion application_input_id")


def _validate_non_blank_fields(record: object, field_names: tuple[str, ...]) -> None:
    for field_name in field_names:
        value = getattr(record, field_name)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field_name} must be non-blank")


def _validate_positive_integer(field_name: str, value: object) -> None:
    if type(value) is not int or value < 1 or value > DURABLE_INT64_MAX:
        raise ValueError(f"{field_name} must be a durable positive integer")


def _validate_durable_timestamp(field_name: str, value: object) -> None:
    if type(value) is not int or value < 0 or value > DURABLE_INT64_MAX:
        raise ValueError(f"{field_name} must be a durable non-negative integer")


def _validate_sha256_digest(field_name: str, value: object) -> None:
    if not _is_sha256_digest(value):
        raise ValueError(f"{field_name} must be a sha256 digest")


def _is_sha256_digest(value: object) -> bool:
    if not isinstance(value, str) or not value.startswith("sha256:"):
        return False
    digest = value.removeprefix("sha256:")
    return len(digest) == 64 and all(
        character in "0123456789abcdef" for character in digest
    )


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
    runner_sessions: Mapping[str, RunnerSessionRecord] = field(default_factory=dict)
    runner_session_cancellation_requests: Mapping[
        str, RunnerSessionCancellationRecord
    ] = field(default_factory=dict)
    runner_session_cancellation_attempts: Mapping[
        str, RunnerSessionCancellationAttemptRecord
    ] = field(default_factory=dict)
    runner_session_completions: Mapping[
        str, RunnerSessionCompletionRecord
    ] = field(default_factory=dict)
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
            "runner_sessions",
            _freeze_mapping(self.runner_sessions),
        )
        object.__setattr__(
            self,
            "runner_session_cancellation_requests",
            _freeze_mapping(self.runner_session_cancellation_requests),
        )
        object.__setattr__(
            self,
            "runner_session_cancellation_attempts",
            _freeze_mapping(self.runner_session_cancellation_attempts),
        )
        object.__setattr__(
            self,
            "runner_session_completions",
            _freeze_mapping(self.runner_session_completions),
        )
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
    "RunnerSessionCancellationAttemptRecord",
    "RunnerSessionCancellationRecord",
    "RunnerSessionCompletionRecord",
    "RunnerSessionRecord",
    "RuntimeState",
    "TraceRecord",
    "TransitionRecord",
    "TransitionRefusal",
    "WorkItem",
    "WorkItemRef",
    "WorkDependencyRecord",
)
