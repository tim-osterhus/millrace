"""Transition input, decision, and mutation contract records."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field, fields, is_dataclass
from hashlib import sha256
from types import MappingProxyType
from typing import Any, ClassVar, TypeAlias, TypeVar, cast

from millrace.contracts.compiled_plan import (
    AuthorityValue,
    SelectedCompiledPlan,
    freeze_authority_mapping,
)
from millrace.contracts.fingerprints import AuthorityFingerprint
from millrace.contracts.ids import QueueFamilyId
from millrace.contracts.state import (
    DURABLE_INT64_MAX,
    RUNNER_SESSION_TEXT_MAX_BYTES,
    Activation,
    ActivationRouteRecord,
    ArtifactRecord,
    ClosedWorkItemRecord,
    ClosureBlockedRecord,
    ClosureEvaluationRecord,
    ClosureTargetRecord,
    ClosureTerminalRecord,
    CooldownWaitRecord,
    CounterRecord,
    DispatchSuspensionRecord,
    EffectProposalRecord,
    EffectReconciliationRecord,
    FanoutRecord,
    GovernanceEventRecord,
    InputReceipt,
    InputReceiptRef,
    LineageQuarantineRecord,
    OperatorInterventionRecord,
    OperatorWaitRecord,
    PauseRecord,
    PlanRef,
    QuarantineRecord,
    QueueClosureRecord,
    RecoveryAttemptRecord,
    RemediationWorkRecord,
    RunnerObservationRecord,
    RunnerSessionCancellationAttemptRecord,
    RunnerSessionCancellationRecord,
    RunnerSessionCompletionRecord,
    RunnerSessionRecord,
    RunRecord,
    RunRef,
    TraceRecord,
    TransitionRecord,
    TransitionRefusal,
    WorkDependencyRecord,
    WorkItem,
)

INPUT_PAYLOAD_DIGEST_DOMAIN_PREFIX = b"millrace-transition-input-v1\0"
OPERATOR_PAYLOAD_DIGEST_DOMAIN_PREFIX = b"millrace-operator-payload-v1\0"
ARTIFACT_PAYLOAD_DIGEST_DOMAIN_PREFIX = b"millrace-artifact-payload-v1\0"

CanonicalInputValue: TypeAlias = (
    str
    | int
    | bool
    | None
    | list["CanonicalInputValue"]
    | dict[str, "CanonicalInputValue"]
)

T = TypeVar("T")


def _freeze_expectation_mapping(value: Mapping[str, T]) -> Mapping[str, T]:
    return MappingProxyType(dict(value))


def _require_non_blank_protocol_id(field_name: str, value: object) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be non-blank")


def _require_runner_session_text(field_name: str, value: object) -> None:
    _require_non_blank_protocol_id(field_name, value)
    assert isinstance(value, str)
    if len(value.encode("utf-8")) > RUNNER_SESSION_TEXT_MAX_BYTES:
        raise ValueError(
            f"{field_name} must be at most "
            f"{RUNNER_SESSION_TEXT_MAX_BYTES} UTF-8 bytes"
        )


def _require_runner_session_digest(field_name: str, digest: str) -> None:
    if (
        not digest.startswith("sha256:")
        or len(digest.encode("ascii", errors="ignore")) != 71
        or len(digest.removeprefix("sha256:")) != 64
        or any(
            character not in "0123456789abcdef"
            for character in digest.removeprefix("sha256:")
        )
    ):
        raise ValueError(f"{field_name} must be a sha256 digest")


def _require_durable_timestamp(field_name: str, value: object) -> None:
    if type(value) is not int or value < 0 or value > DURABLE_INT64_MAX:
        raise ValueError(f"{field_name} must be a durable non-negative integer")


def _require_durable_positive_integer(
    field_name: str,
    value: object,
) -> None:
    if type(value) is not int or value < 1 or value > DURABLE_INT64_MAX:
        raise ValueError(f"{field_name} must be a durable positive integer")


def _require_boolean(field_name: str, value: object) -> None:
    if type(value) is not bool:
        raise ValueError(f"{field_name} must be a boolean")


_RUNNER_SESSION_SIGNAL_KINDS = frozenset(
    {
        "runner_request",
        "runner_dispatch_echo",
        "runner_start_outcome",
        "runner_session_locator",
        "runner_completion_poll",
        "runner_completion_outcome",
        "runner_result_evidence",
        "runner_session_deadline",
        "runner_start_diagnostic",
        "runner_reconciliation",
    }
)
_RUNNER_SESSION_SIGNAL_REFUSAL_REASONS = frozenset(
    {
        "runner_session_authority_mismatch",
        "runner_session_reconciliation_contradiction",
    }
)


@dataclass(frozen=True, slots=True)
class TransitionInput:
    input_kind: ClassVar[str] = "transition_input"
    input_schema_version: ClassVar[int] = 1

    input_id: str

    def __post_init__(self) -> None:
        if not self.input_id:
            raise ValueError("input_id must be non-empty")


@dataclass(frozen=True, slots=True)
class ControlInput(TransitionInput):
    input_kind: ClassVar[str] = "control"

    pass


@dataclass(frozen=True, slots=True)
class WorkflowInput(TransitionInput):
    input_kind: ClassVar[str] = "workflow"

    pass


@dataclass(frozen=True, slots=True)
class OperatorCommand(WorkflowInput):
    input_kind: ClassVar[str] = "workflow.operator_command"

    pass


@dataclass(frozen=True, slots=True)
class KernelCommand(WorkflowInput):
    input_kind: ClassVar[str] = "workflow.kernel_command"

    pass


@dataclass(frozen=True, slots=True)
class Observation(WorkflowInput):
    input_kind: ClassVar[str] = "workflow.observation"

    pass


@dataclass(frozen=True, slots=True)
class InitializeWorkspace(ControlInput):
    input_kind: ClassVar[str] = "control.initialize_workspace"

    pass


@dataclass(frozen=True, slots=True)
class AdmitPlan(ControlInput):
    input_kind: ClassVar[str] = "control.admit_plan"

    selected_plan: SelectedCompiledPlan
    authority_fingerprint: AuthorityFingerprint


@dataclass(frozen=True, slots=True)
class SelectDefaultPlan(ControlInput):
    input_kind: ClassVar[str] = "control.select_default_plan"

    authority_fingerprint: AuthorityFingerprint


@dataclass(frozen=True, slots=True)
class EnqueueWork(OperatorCommand):
    input_kind: ClassVar[str] = "workflow.enqueue_work"

    queue_family_id: QueueFamilyId
    payload: Mapping[str, AuthorityValue]

    def __post_init__(self) -> None:
        TransitionInput.__post_init__(self)
        object.__setattr__(
            self,
            "payload",
            freeze_authority_mapping(cast(Mapping[str, object], self.payload)),
        )


@dataclass(frozen=True, slots=True)
class SuspendDispatch(OperatorCommand):
    input_kind: ClassVar[str] = "workflow.suspend_dispatch"

    plan_fingerprint: AuthorityFingerprint
    actor_id: str
    reason: str

    def __post_init__(self) -> None:
        TransitionInput.__post_init__(self)
        for field_name in ("plan_fingerprint", "actor_id", "reason"):
            _require_non_blank_protocol_id(field_name, getattr(self, field_name))


@dataclass(frozen=True, slots=True)
class ResumeDispatch(OperatorCommand):
    input_kind: ClassVar[str] = "workflow.resume_dispatch"

    plan_fingerprint: AuthorityFingerprint
    suspension_id: str
    actor_id: str
    reason: str

    def __post_init__(self) -> None:
        TransitionInput.__post_init__(self)
        for field_name in (
            "plan_fingerprint",
            "suspension_id",
            "actor_id",
            "reason",
        ):
            _require_non_blank_protocol_id(field_name, getattr(self, field_name))


@dataclass(frozen=True, slots=True)
class CancelQueuedWork(OperatorCommand):
    input_kind: ClassVar[str] = "workflow.cancel_queued_work"

    work_item_id: str
    plan_fingerprint: AuthorityFingerprint
    actor_id: str
    reason: str

    def __post_init__(self) -> None:
        TransitionInput.__post_init__(self)
        for field_name in (
            "work_item_id",
            "plan_fingerprint",
            "actor_id",
            "reason",
        ):
            _require_non_blank_protocol_id(field_name, getattr(self, field_name))


@dataclass(frozen=True, slots=True)
class CancelQueuedLineage(OperatorCommand):
    input_kind: ClassVar[str] = "workflow.cancel_queued_lineage"

    lineage_id: str
    plan_fingerprint: AuthorityFingerprint
    actor_id: str
    reason: str

    def __post_init__(self) -> None:
        TransitionInput.__post_init__(self)
        for field_name in (
            "lineage_id",
            "plan_fingerprint",
            "actor_id",
            "reason",
        ):
            _require_non_blank_protocol_id(field_name, getattr(self, field_name))


@dataclass(frozen=True, slots=True)
class ClaimWork(KernelCommand):
    input_kind: ClassVar[str] = "workflow.claim_work"

    activation_id: str


@dataclass(frozen=True, slots=True)
class CreateRunnerSession(KernelCommand):
    input_kind: ClassVar[str] = "workflow.create_runner_session"

    run_ref: RunRef
    session_id: str
    session_fencing_token: str
    created_at: int
    explicit_retry_intent: bool

    def __post_init__(self) -> None:
        TransitionInput.__post_init__(self)
        for field_name in ("session_id", "session_fencing_token"):
            _require_runner_session_text(field_name, getattr(self, field_name))
        _require_durable_timestamp("created_at", self.created_at)
        _require_boolean("explicit_retry_intent", self.explicit_retry_intent)


@dataclass(frozen=True, slots=True)
class AdvanceRunnerSession(KernelCommand):
    input_kind: ClassVar[str] = "workflow.advance_runner_session"

    run_ref: RunRef
    session_id: str
    dispatch_generation: int
    session_fencing_token: str
    expected_state: str
    next_state: str
    occurred_at: int
    durable_locator_digest: str | None = None

    def __post_init__(self) -> None:
        TransitionInput.__post_init__(self)
        for field_name in (
            "session_id",
            "session_fencing_token",
            "expected_state",
            "next_state",
        ):
            _require_runner_session_text(field_name, getattr(self, field_name))
        _require_durable_positive_integer(
            "dispatch_generation",
            self.dispatch_generation,
        )
        _require_durable_timestamp("occurred_at", self.occurred_at)
        if self.durable_locator_digest is not None:
            _require_runner_session_digest(
                "durable_locator_digest",
                self.durable_locator_digest,
            )


@dataclass(frozen=True, slots=True)
class RefuseRunnerSessionSignal(KernelCommand):
    input_kind: ClassVar[str] = "workflow.refuse_runner_session_signal"

    run_ref: RunRef
    session_id: str
    dispatch_generation: int
    session_fencing_token: str
    expected_state: str
    signal_kind: str
    reason: str
    signal_digest: str

    def __post_init__(self) -> None:
        TransitionInput.__post_init__(self)
        for field_name in (
            "session_id",
            "session_fencing_token",
            "expected_state",
        ):
            _require_runner_session_text(field_name, getattr(self, field_name))
        _require_durable_positive_integer(
            "dispatch_generation",
            self.dispatch_generation,
        )
        if self.signal_kind not in _RUNNER_SESSION_SIGNAL_KINDS:
            raise ValueError("unsupported runner session signal kind")
        if self.reason not in _RUNNER_SESSION_SIGNAL_REFUSAL_REASONS:
            raise ValueError("unsupported runner session signal refusal reason")
        _require_runner_session_digest("signal_digest", self.signal_digest)


@dataclass(frozen=True, slots=True)
class RequestRunnerSessionCancellation(KernelCommand):
    input_kind: ClassVar[str] = "workflow.request_runner_session_cancellation"

    run_ref: RunRef
    session_id: str
    dispatch_generation: int
    session_fencing_token: str
    expected_state: str
    request_id: str
    reason: str
    source_kind: str
    actor_id: str
    requested_at: int
    request_order: int
    primary: bool

    def __post_init__(self) -> None:
        TransitionInput.__post_init__(self)
        for field_name in (
            "session_id",
            "session_fencing_token",
            "expected_state",
            "request_id",
            "reason",
            "source_kind",
            "actor_id",
        ):
            _require_runner_session_text(field_name, getattr(self, field_name))
        if self.reason not in {
            "operator_cancel_work",
            "daemon_shutdown",
            "runner_timeout",
            "runtime_failure",
        }:
            raise ValueError("unsupported runner session cancellation reason")
        if self.source_kind not in {"operator", "daemon", "runtime"}:
            raise ValueError("unsupported runner session cancellation source")
        _require_durable_positive_integer(
            "dispatch_generation",
            self.dispatch_generation,
        )
        _require_durable_timestamp("requested_at", self.requested_at)
        _require_durable_positive_integer("request_order", self.request_order)
        _require_boolean("primary", self.primary)


@dataclass(frozen=True, slots=True)
class RecordRunnerSessionCancellationAttempt(KernelCommand):
    input_kind: ClassVar[str] = "workflow.record_runner_session_cancellation_attempt"

    run_ref: RunRef
    session_id: str
    dispatch_generation: int
    session_fencing_token: str
    expected_state: str
    attempt_id: str
    request_id: str
    sequence: int
    operation: str
    result: str
    started_at: int
    completed_at: int
    bounded_diagnostic_digest: str

    def __post_init__(self) -> None:
        TransitionInput.__post_init__(self)
        for field_name in (
            "session_id",
            "session_fencing_token",
            "expected_state",
            "attempt_id",
            "request_id",
            "operation",
            "result",
        ):
            _require_runner_session_text(field_name, getattr(self, field_name))
        _require_runner_session_digest(
            "bounded_diagnostic_digest",
            self.bounded_diagnostic_digest,
        )
        _require_durable_positive_integer(
            "dispatch_generation",
            self.dispatch_generation,
        )
        _require_durable_positive_integer("sequence", self.sequence)
        _require_durable_timestamp("started_at", self.started_at)
        _require_durable_timestamp("completed_at", self.completed_at)
        if self.completed_at < self.started_at:
            raise ValueError(
                "runner session cancellation attempt timestamps must be monotonic"
            )
        if self.operation not in {
            "cooperative_cancel",
            "terminate",
            "kill",
            "transport_cleanup",
        }:
            raise ValueError("unsupported runner session cancellation operation")
        if self.result not in {
            "succeeded",
            "failed",
            "timed_out",
            "unsupported",
        }:
            raise ValueError("unsupported runner session cancellation result")


@dataclass(frozen=True, slots=True)
class RecordRunnerSessionCompletion(KernelCommand):
    input_kind: ClassVar[str] = "workflow.record_runner_session_completion"

    run_ref: RunRef
    expected_state: str
    completion: RunnerSessionCompletionRecord

    def __post_init__(self) -> None:
        TransitionInput.__post_init__(self)
        _require_runner_session_text("expected_state", self.expected_state)


@dataclass(frozen=True, slots=True)
class FanoutFromArtifact(KernelCommand):
    input_kind: ClassVar[str] = "workflow.fanout_from_artifact"

    fanout_id: str
    source_artifact_id: str

    def __post_init__(self) -> None:
        TransitionInput.__post_init__(self)
        _require_non_blank_protocol_id("fanout_id", self.fanout_id)
        _require_non_blank_protocol_id("source_artifact_id", self.source_artifact_id)


@dataclass(frozen=True, slots=True)
class JoinFromArtifact(KernelCommand):
    input_kind: ClassVar[str] = "workflow.join_from_artifact"

    join_id: str
    source_artifact_id: str

    def __post_init__(self) -> None:
        TransitionInput.__post_init__(self)
        _require_non_blank_protocol_id("join_id", self.join_id)
        _require_non_blank_protocol_id("source_artifact_id", self.source_artifact_id)


@dataclass(frozen=True, slots=True)
class TimerDue(KernelCommand):
    input_kind: ClassVar[str] = "workflow.timer_due"

    wait_id: str
    observed_at: int

    def __post_init__(self) -> None:
        TransitionInput.__post_init__(self)
        _require_non_blank_protocol_id("wait_id", self.wait_id)
        if type(self.observed_at) is not int:
            raise ValueError("observed_at must be an integer")
        if self.observed_at < 0:
            raise ValueError("observed_at must be non-negative")
        if self.observed_at > DURABLE_INT64_MAX:
            raise ValueError("observed_at exceeds durable integer range")


@dataclass(frozen=True, slots=True)
class ReconcileEffect(KernelCommand):
    input_kind: ClassVar[str] = "workflow.reconcile_effect"

    effect_id: str
    provider_ref: str
    status: str
    result: Mapping[str, AuthorityValue]

    def __post_init__(self) -> None:
        TransitionInput.__post_init__(self)
        for field_name in ("effect_id", "provider_ref", "status"):
            _require_non_blank_protocol_id(field_name, getattr(self, field_name))
        object.__setattr__(
            self,
            "result",
            freeze_authority_mapping(cast(Mapping[str, object], self.result)),
        )


@dataclass(frozen=True, slots=True)
class OpenClosureTarget(KernelCommand):
    input_kind: ClassVar[str] = "workflow.open_closure_target"

    selected_plan_ref: PlanRef
    completion_behavior_id: str
    closure_target_id: str
    lineage_id: str
    root_source_kind: str
    root_source_id: str
    closure_root_work_item_id: str | None
    request_kind: str
    target_graph_node_id: str
    evidence_window: Mapping[str, AuthorityValue]

    def __post_init__(self) -> None:
        TransitionInput.__post_init__(self)
        for field_name in (
            "completion_behavior_id",
            "closure_target_id",
            "lineage_id",
            "root_source_kind",
            "root_source_id",
            "request_kind",
            "target_graph_node_id",
        ):
            _require_non_blank_protocol_id(field_name, getattr(self, field_name))
        if self.closure_root_work_item_id is not None:
            _require_non_blank_protocol_id(
                "closure_root_work_item_id",
                self.closure_root_work_item_id,
            )
        object.__setattr__(
            self,
            "evidence_window",
            freeze_authority_mapping(cast(Mapping[str, object], self.evidence_window)),
        )


@dataclass(frozen=True, slots=True)
class EvaluateCompletionBehavior(KernelCommand):
    input_kind: ClassVar[str] = "workflow.evaluate_completion_behavior"

    selected_plan_ref: PlanRef
    completion_behavior_id: str
    closure_target_id: str

    def __post_init__(self) -> None:
        TransitionInput.__post_init__(self)
        _require_non_blank_protocol_id(
            "completion_behavior_id",
            self.completion_behavior_id,
        )
        _require_non_blank_protocol_id("closure_target_id", self.closure_target_id)


@dataclass(frozen=True, slots=True)
class RunnerResultObserved(Observation):
    input_kind: ClassVar[str] = "workflow.runner_result_observed"

    run_id: str
    payload: Mapping[str, AuthorityValue]
    observed_at: int | None

    def __post_init__(self) -> None:
        TransitionInput.__post_init__(self)
        if self.observed_at is not None and type(self.observed_at) is not int:
            raise ValueError("observed_at must be an integer")
        if self.observed_at is not None and self.observed_at < 0:
            raise ValueError("observed_at must be non-negative")
        if self.observed_at is not None and self.observed_at > DURABLE_INT64_MAX:
            raise ValueError("observed_at exceeds durable integer range")
        object.__setattr__(
            self,
            "payload",
            freeze_authority_mapping(cast(Mapping[str, object], self.payload)),
        )


@dataclass(frozen=True, slots=True)
class OperatorResumeLineage(OperatorCommand):
    input_kind: ClassVar[str] = "workflow.operator_resume_lineage"

    option_id: str
    selected_plan_ref: PlanRef
    quarantine_id: str
    lineage_id: str
    actor_id: str
    actor_kind: str
    reason: str
    payload: Mapping[str, AuthorityValue]

    def __post_init__(self) -> None:
        TransitionInput.__post_init__(self)
        for field_name in (
            "option_id",
            "quarantine_id",
            "lineage_id",
            "actor_id",
            "actor_kind",
            "reason",
        ):
            _require_non_blank_protocol_id(field_name, getattr(self, field_name))
        object.__setattr__(
            self,
            "payload",
            freeze_authority_mapping(cast(Mapping[str, object], self.payload)),
        )


@dataclass(frozen=True, slots=True)
class OperatorCloseLineage(OperatorCommand):
    input_kind: ClassVar[str] = "workflow.operator_close_lineage"

    option_id: str
    selected_plan_ref: PlanRef
    quarantine_id: str
    lineage_id: str
    actor_id: str
    actor_kind: str
    reason: str
    payload: Mapping[str, AuthorityValue]

    def __post_init__(self) -> None:
        TransitionInput.__post_init__(self)
        for field_name in (
            "option_id",
            "quarantine_id",
            "lineage_id",
            "actor_id",
            "actor_kind",
            "reason",
        ):
            _require_non_blank_protocol_id(field_name, getattr(self, field_name))
        object.__setattr__(
            self,
            "payload",
            freeze_authority_mapping(cast(Mapping[str, object], self.payload)),
        )


@dataclass(frozen=True, slots=True)
class OperatorReviseLineage(OperatorCommand):
    input_kind: ClassVar[str] = "workflow.operator_revise_lineage"

    option_id: str
    selected_plan_ref: PlanRef
    quarantine_id: str
    lineage_id: str
    actor_id: str
    actor_kind: str
    reason: str
    payload: Mapping[str, AuthorityValue]

    def __post_init__(self) -> None:
        TransitionInput.__post_init__(self)
        for field_name in (
            "option_id",
            "quarantine_id",
            "lineage_id",
            "actor_id",
            "actor_kind",
            "reason",
        ):
            _require_non_blank_protocol_id(field_name, getattr(self, field_name))
        object.__setattr__(
            self,
            "payload",
            freeze_authority_mapping(cast(Mapping[str, object], self.payload)),
        )


@dataclass(frozen=True, slots=True)
class OperatorResumeWait(OperatorCommand):
    input_kind: ClassVar[str] = "workflow.operator_resume_wait"

    selected_plan_ref: PlanRef
    wait_id: str
    lineage_id: str
    actor_id: str
    actor_kind: str
    payload: Mapping[str, AuthorityValue]

    def __post_init__(self) -> None:
        TransitionInput.__post_init__(self)
        for field_name in ("wait_id", "lineage_id", "actor_id", "actor_kind"):
            _require_non_blank_protocol_id(field_name, getattr(self, field_name))
        object.__setattr__(
            self,
            "payload",
            freeze_authority_mapping(cast(Mapping[str, object], self.payload)),
        )


@dataclass(frozen=True, slots=True)
class OperatorCloseWait(OperatorCommand):
    input_kind: ClassVar[str] = "workflow.operator_close_wait"

    selected_plan_ref: PlanRef
    wait_id: str
    lineage_id: str
    actor_id: str
    actor_kind: str
    payload: Mapping[str, AuthorityValue]

    def __post_init__(self) -> None:
        TransitionInput.__post_init__(self)
        for field_name in ("wait_id", "lineage_id", "actor_id", "actor_kind"):
            _require_non_blank_protocol_id(field_name, getattr(self, field_name))
        object.__setattr__(
            self,
            "payload",
            freeze_authority_mapping(cast(Mapping[str, object], self.payload)),
        )


@dataclass(frozen=True, slots=True)
class OperatorReviseWait(OperatorCommand):
    input_kind: ClassVar[str] = "workflow.operator_revise_wait"

    selected_plan_ref: PlanRef
    wait_id: str
    lineage_id: str
    actor_id: str
    actor_kind: str
    payload: Mapping[str, AuthorityValue]

    def __post_init__(self) -> None:
        TransitionInput.__post_init__(self)
        for field_name in ("wait_id", "lineage_id", "actor_id", "actor_kind"):
            _require_non_blank_protocol_id(field_name, getattr(self, field_name))
        object.__setattr__(
            self,
            "payload",
            freeze_authority_mapping(cast(Mapping[str, object], self.payload)),
        )


@dataclass(frozen=True, slots=True)
class TransitionContext:
    transition_id: str
    work_item_id: str
    activation_id: str
    run_id: str
    claim_id: str
    fencing_token: str

    def __post_init__(self) -> None:
        for field_name in (
            "transition_id",
            "work_item_id",
            "activation_id",
            "run_id",
            "claim_id",
            "fencing_token",
        ):
            _require_non_blank_protocol_id(field_name, getattr(self, field_name))


@dataclass(frozen=True, slots=True)
class TransitionDecision:
    input_id: str
    input_kind: str
    input_family: str
    input_payload_digest: str
    accepted: bool
    receipt_ref: InputReceiptRef | None
    refusal: TransitionRefusal | None
    expected_plan_fingerprint: AuthorityFingerprint | None
    expected_work_item_generations: Mapping[str, int]
    expected_activation_generations: Mapping[str, int]
    expected_activation_unclaimed: tuple[str, ...]
    expected_run_generations: Mapping[str, int]
    expected_run_fencing_tokens: Mapping[str, str]
    expected_run_unobserved: tuple[str, ...]
    expected_pause_absent: bool
    expected_dispatch_suspension_absent: bool
    expected_dispatch_suspension_generation: int | None
    expected_lineage_quarantine_absent: tuple[str, ...]
    mutations: tuple[TransitionMutation, ...]
    governance_events: tuple[GovernanceEventRecord, ...] = ()
    trace_records: tuple[TraceRecord, ...] = ()
    expected_work_item_plan_refs: Mapping[str, PlanRef] = field(default_factory=dict)
    expected_activation_plan_refs: Mapping[str, PlanRef] = field(default_factory=dict)
    expected_work_item_open: tuple[str, ...] = ()
    expected_operator_wait_absent: tuple[str, ...] = ()
    expected_activation_claims: Mapping[str, str | None] = field(
        default_factory=dict
    )
    expected_run_current_session_ids: Mapping[str, str | None] = field(
        default_factory=dict
    )
    expected_runner_session_snapshots: Mapping[str, tuple[str, str]] = field(
        default_factory=dict
    )
    expected_lineage_work_item_ids: Mapping[str, tuple[str, ...]] = field(
        default_factory=dict
    )

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "expected_work_item_generations",
            _freeze_expectation_mapping(self.expected_work_item_generations),
        )
        object.__setattr__(
            self,
            "expected_activation_generations",
            _freeze_expectation_mapping(self.expected_activation_generations),
        )
        object.__setattr__(
            self,
            "expected_activation_unclaimed",
            tuple(self.expected_activation_unclaimed),
        )
        object.__setattr__(
            self,
            "expected_run_generations",
            _freeze_expectation_mapping(self.expected_run_generations),
        )
        object.__setattr__(
            self,
            "expected_run_fencing_tokens",
            _freeze_expectation_mapping(self.expected_run_fencing_tokens),
        )
        object.__setattr__(
            self,
            "expected_run_unobserved",
            tuple(self.expected_run_unobserved),
        )
        object.__setattr__(
            self,
            "expected_lineage_quarantine_absent",
            tuple(self.expected_lineage_quarantine_absent),
        )
        object.__setattr__(
            self,
            "expected_work_item_open",
            tuple(self.expected_work_item_open),
        )
        object.__setattr__(
            self,
            "expected_operator_wait_absent",
            tuple(self.expected_operator_wait_absent),
        )
        object.__setattr__(
            self,
            "expected_work_item_plan_refs",
            _freeze_expectation_mapping(self.expected_work_item_plan_refs),
        )
        object.__setattr__(
            self,
            "expected_activation_plan_refs",
            _freeze_expectation_mapping(self.expected_activation_plan_refs),
        )
        object.__setattr__(
            self,
            "expected_activation_claims",
            _freeze_expectation_mapping(self.expected_activation_claims),
        )
        object.__setattr__(
            self,
            "expected_run_current_session_ids",
            _freeze_expectation_mapping(self.expected_run_current_session_ids),
        )
        object.__setattr__(
            self,
            "expected_runner_session_snapshots",
            _freeze_expectation_mapping(self.expected_runner_session_snapshots),
        )
        object.__setattr__(
            self,
            "expected_lineage_work_item_ids",
            _freeze_expectation_mapping(self.expected_lineage_work_item_ids),
        )
        object.__setattr__(self, "mutations", tuple(self.mutations))
        object.__setattr__(self, "governance_events", tuple(self.governance_events))
        object.__setattr__(self, "trace_records", tuple(self.trace_records))

    @property
    def disposition(self) -> str:
        if not self.accepted:
            return "refused"
        if self.receipt_ref is not None and not self.mutations:
            return "replayed"
        return "accepted"


@dataclass(frozen=True, slots=True)
class RecordInputReceipt:
    receipt: InputReceipt
    mutation_kind: ClassVar[str] = "mutation.record_input_receipt"
    mutation_schema_version: ClassVar[int] = 1


@dataclass(frozen=True, slots=True)
class AdmitPlanRef:
    plan_ref: PlanRef
    selected_plan: SelectedCompiledPlan
    mutation_kind: ClassVar[str] = "mutation.admit_plan_ref"
    mutation_schema_version: ClassVar[int] = 1


@dataclass(frozen=True, slots=True)
class SelectDefaultPlanRef:
    plan_ref: PlanRef
    mutation_kind: ClassVar[str] = "mutation.select_default_plan_ref"
    mutation_schema_version: ClassVar[int] = 1


@dataclass(frozen=True, slots=True)
class CreateWorkItem:
    work_item: WorkItem
    mutation_kind: ClassVar[str] = "mutation.create_work_item"
    mutation_schema_version: ClassVar[int] = 1


@dataclass(frozen=True, slots=True)
class CreateActivation:
    activation: Activation
    mutation_kind: ClassVar[str] = "mutation.create_activation"
    mutation_schema_version: ClassVar[int] = 1


@dataclass(frozen=True, slots=True)
class CreateRun:
    run: RunRecord
    mutation_kind: ClassVar[str] = "mutation.create_run"
    mutation_schema_version: ClassVar[int] = 1


@dataclass(frozen=True, slots=True)
class CreateRunnerSessionRecord:
    session: RunnerSessionRecord
    expected_run_ref: RunRef
    expected_current_session_id: str | None
    mutation_kind: ClassVar[str] = "mutation.create_runner_session"
    mutation_schema_version: ClassVar[int] = 1


@dataclass(frozen=True, slots=True)
class AdvanceRunnerSessionRecord:
    session: RunnerSessionRecord
    expected_run_ref: RunRef
    expected_session_state: str
    mutation_kind: ClassVar[str] = "mutation.advance_runner_session"
    mutation_schema_version: ClassVar[int] = 1


@dataclass(frozen=True, slots=True)
class RecordRunnerSessionCancellation:
    record: RunnerSessionCancellationRecord
    expected_run_ref: RunRef
    expected_session_state: str
    mutation_kind: ClassVar[str] = "mutation.record_runner_session_cancellation"
    mutation_schema_version: ClassVar[int] = 1


@dataclass(frozen=True, slots=True)
class RecordRunnerSessionCancellationAttemptRecord:
    record: RunnerSessionCancellationAttemptRecord
    expected_run_ref: RunRef
    expected_session_state: str
    mutation_kind: ClassVar[str] = (
        "mutation.record_runner_session_cancellation_attempt"
    )
    mutation_schema_version: ClassVar[int] = 1


@dataclass(frozen=True, slots=True)
class RecordRunnerSessionCompletionRecord:
    record: RunnerSessionCompletionRecord
    expected_run_ref: RunRef
    expected_session_state: str
    mutation_kind: ClassVar[str] = "mutation.record_runner_session_completion"
    mutation_schema_version: ClassVar[int] = 1


@dataclass(frozen=True, slots=True)
class RecordTransition:
    transition_record: TransitionRecord
    mutation_kind: ClassVar[str] = "mutation.record_transition"
    mutation_schema_version: ClassVar[int] = 1


@dataclass(frozen=True, slots=True)
class RecordRefusal:
    refusal: TransitionRefusal
    mutation_kind: ClassVar[str] = "mutation.record_refusal"
    mutation_schema_version: ClassVar[int] = 1


@dataclass(frozen=True, slots=True)
class RecordRunnerObservation:
    record_id: str
    observation: RunnerObservationRecord | None = None
    mutation_kind: ClassVar[str] = "mutation.record_runner_observation"
    mutation_schema_version: ClassVar[int] = 1


@dataclass(frozen=True, slots=True)
class RecordArtifact:
    record_id: str
    artifact: ArtifactRecord | None = None
    mutation_kind: ClassVar[str] = "mutation.record_artifact"
    mutation_schema_version: ClassVar[int] = 1


@dataclass(frozen=True, slots=True)
class RecordEffectProposal:
    record_id: str
    record: EffectProposalRecord | None = None
    mutation_kind: ClassVar[str] = "mutation.record_effect_proposal"
    mutation_schema_version: ClassVar[int] = 1


@dataclass(frozen=True, slots=True)
class RecordEffectReconciliation:
    record_id: str
    record: EffectReconciliationRecord | None = None
    mutation_kind: ClassVar[str] = "mutation.record_effect_reconciliation"
    mutation_schema_version: ClassVar[int] = 1


@dataclass(frozen=True, slots=True)
class RouteActivation:
    record_id: str
    route: ActivationRouteRecord | None = None
    mutation_kind: ClassVar[str] = "mutation.route_activation"
    mutation_schema_version: ClassVar[int] = 1


@dataclass(frozen=True, slots=True)
class RecordFanout:
    record_id: str
    record: FanoutRecord | None = None
    mutation_kind: ClassVar[str] = "mutation.record_fanout"
    mutation_schema_version: ClassVar[int] = 1


@dataclass(frozen=True, slots=True)
class RecordWorkDependency:
    record_id: str
    record: WorkDependencyRecord | None = None
    mutation_kind: ClassVar[str] = "mutation.record_work_dependency"
    mutation_schema_version: ClassVar[int] = 1


@dataclass(frozen=True, slots=True)
class RecordClosureTarget:
    record_id: str
    record: ClosureTargetRecord | None = None
    mutation_kind: ClassVar[str] = "mutation.record_closure_target"
    mutation_schema_version: ClassVar[int] = 1


@dataclass(frozen=True, slots=True)
class CloseClosureTarget:
    record_id: str
    closure_target_id: str
    closed_by_record_id: str
    mutation_kind: ClassVar[str] = "mutation.close_closure_target"
    mutation_schema_version: ClassVar[int] = 1


@dataclass(frozen=True, slots=True)
class RecordClosureEvaluation:
    record_id: str
    record: ClosureEvaluationRecord | None = None
    mutation_kind: ClassVar[str] = "mutation.record_closure_evaluation"
    mutation_schema_version: ClassVar[int] = 1


@dataclass(frozen=True, slots=True)
class RecordClosureTerminal:
    record_id: str
    record: ClosureTerminalRecord | None = None
    mutation_kind: ClassVar[str] = "mutation.record_closure_terminal"
    mutation_schema_version: ClassVar[int] = 1


@dataclass(frozen=True, slots=True)
class RecordRemediationWork:
    record_id: str
    record: RemediationWorkRecord | None = None
    mutation_kind: ClassVar[str] = "mutation.record_remediation_work"
    mutation_schema_version: ClassVar[int] = 1


@dataclass(frozen=True, slots=True)
class RecordClosureBlocked:
    record_id: str
    record: ClosureBlockedRecord | None = None
    mutation_kind: ClassVar[str] = "mutation.record_closure_blocked"
    mutation_schema_version: ClassVar[int] = 1


@dataclass(frozen=True, slots=True)
class CloseWorkItem:
    record_id: str
    record: ClosedWorkItemRecord | None = None
    mutation_kind: ClassVar[str] = "mutation.close_work_item"
    mutation_schema_version: ClassVar[int] = 1


@dataclass(frozen=True, slots=True)
class RecordQueueClosure:
    record_id: str
    record: QueueClosureRecord | None = None
    mutation_kind: ClassVar[str] = "mutation.record_queue_closure"
    mutation_schema_version: ClassVar[int] = 1


@dataclass(frozen=True, slots=True)
class SetPause:
    record_id: str
    record: PauseRecord | None = None
    mutation_kind: ClassVar[str] = "mutation.set_pause"
    mutation_schema_version: ClassVar[int] = 1


@dataclass(frozen=True, slots=True)
class SetDispatchSuspension:
    record: DispatchSuspensionRecord
    expected_record: DispatchSuspensionRecord | None
    expected_dispatch_generation: int
    expected_default_plan_ref: PlanRef | None
    mutation_kind: ClassVar[str] = "mutation.set_dispatch_suspension"
    mutation_schema_version: ClassVar[int] = 1


@dataclass(frozen=True, slots=True)
class SetQuarantine:
    record_id: str
    record: QuarantineRecord | None = None
    mutation_kind: ClassVar[str] = "mutation.set_quarantine"
    mutation_schema_version: ClassVar[int] = 1


@dataclass(frozen=True, slots=True)
class RecordLineageQuarantine:
    record_id: str
    record: LineageQuarantineRecord | None = None
    mutation_kind: ClassVar[str] = "mutation.record_lineage_quarantine"
    mutation_schema_version: ClassVar[int] = 1


@dataclass(frozen=True, slots=True)
class SupersedeLineageQuarantine:
    record_id: str
    lineage_id: str
    quarantine_id: str
    superseded_input_id: str
    mutation_kind: ClassVar[str] = "mutation.supersede_lineage_quarantine"
    mutation_schema_version: ClassVar[int] = 1


@dataclass(frozen=True, slots=True)
class RecordRecoveryAttempt:
    record_id: str
    attempt: RecoveryAttemptRecord | None = None
    mutation_kind: ClassVar[str] = "mutation.record_recovery_attempt"
    mutation_schema_version: ClassVar[int] = 1


@dataclass(frozen=True, slots=True)
class RecordOperatorIntervention:
    record_id: str
    record: OperatorInterventionRecord | None = None
    mutation_kind: ClassVar[str] = "mutation.record_operator_intervention"
    mutation_schema_version: ClassVar[int] = 1


@dataclass(frozen=True, slots=True)
class RecordOperatorWait:
    record_id: str
    record: OperatorWaitRecord | None = None
    mutation_kind: ClassVar[str] = "mutation.record_operator_wait"
    mutation_schema_version: ClassVar[int] = 1


@dataclass(frozen=True, slots=True)
class RecordCooldownWait:
    record_id: str
    wait: CooldownWaitRecord | None = None
    mutation_kind: ClassVar[str] = "mutation.record_cooldown_wait"
    mutation_schema_version: ClassVar[int] = 1


@dataclass(frozen=True, slots=True)
class RecordCounter:
    record_id: str
    record: CounterRecord | None = None
    mutation_kind: ClassVar[str] = "mutation.record_counter"
    mutation_schema_version: ClassVar[int] = 1


@dataclass(frozen=True, slots=True)
class EmitGovernanceEvent:
    record_id: str
    event: GovernanceEventRecord | None = None
    mutation_kind: ClassVar[str] = "mutation.emit_governance_event"
    mutation_schema_version: ClassVar[int] = 1


@dataclass(frozen=True, slots=True)
class EmitTrace:
    record_id: str
    trace: TraceRecord | None = None
    mutation_kind: ClassVar[str] = "mutation.emit_trace"
    mutation_schema_version: ClassVar[int] = 1


TransitionMutation: TypeAlias = (
    RecordInputReceipt
    | AdmitPlanRef
    | SelectDefaultPlanRef
    | CreateWorkItem
    | CreateActivation
    | CreateRun
    | CreateRunnerSessionRecord
    | AdvanceRunnerSessionRecord
    | RecordRunnerSessionCancellation
    | RecordRunnerSessionCancellationAttemptRecord
    | RecordRunnerSessionCompletionRecord
    | RecordTransition
    | RecordRefusal
    | RecordRunnerObservation
    | RecordArtifact
    | RecordEffectProposal
    | RecordEffectReconciliation
    | RouteActivation
    | RecordFanout
    | RecordWorkDependency
    | RecordClosureTarget
    | CloseClosureTarget
    | RecordClosureEvaluation
    | RecordClosureTerminal
    | RecordRemediationWork
    | RecordClosureBlocked
    | CloseWorkItem
    | RecordQueueClosure
    | SetPause
    | SetDispatchSuspension
    | SetQuarantine
    | RecordLineageQuarantine
    | SupersedeLineageQuarantine
    | RecordRecoveryAttempt
    | RecordOperatorIntervention
    | RecordOperatorWait
    | RecordCooldownWait
    | RecordCounter
    | EmitGovernanceEvent
    | EmitTrace
)


def input_payload_digest(transition_input: TransitionInput) -> str:
    payload = _transition_input_payload(transition_input)
    serialized = json.dumps(
        _canonical_input_value(payload),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    digest = sha256(INPUT_PAYLOAD_DIGEST_DOMAIN_PREFIX + serialized).hexdigest()
    return f"sha256:{digest}"


def operator_payload_digest(payload: Mapping[str, object]) -> str:
    serialized = json.dumps(
        _canonical_input_value(payload),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    digest = sha256(OPERATOR_PAYLOAD_DIGEST_DOMAIN_PREFIX + serialized).hexdigest()
    return f"sha256:{digest}"


def artifact_payload_digest(payload: Mapping[str, object]) -> str:
    serialized = canonical_authority_mapping_bytes(payload)
    digest = sha256(ARTIFACT_PAYLOAD_DIGEST_DOMAIN_PREFIX + serialized).hexdigest()
    return f"sha256:{digest}"


def canonical_authority_mapping_bytes(payload: Mapping[str, object]) -> bytes:
    """Return canonical UTF-8 JSON bytes for an authority mapping."""

    return json.dumps(
        _canonical_input_value(payload),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def input_family(transition_input: TransitionInput) -> str:
    if isinstance(transition_input, ControlInput):
        return "control"
    if isinstance(transition_input, OperatorCommand):
        return "workflow_operator_command"
    if isinstance(transition_input, KernelCommand):
        return "workflow_kernel_command"
    if isinstance(transition_input, Observation):
        return "workflow_observation"
    return "unknown"


def input_kind(transition_input: TransitionInput) -> str:
    return type(transition_input).input_kind


def _transition_input_payload(
    transition_input: TransitionInput,
) -> Mapping[str, object]:
    if isinstance(transition_input, InitializeWorkspace):
        return {"input_kind": input_kind(transition_input)}
    if isinstance(transition_input, AdmitPlan):
        return {
            "input_kind": input_kind(transition_input),
            "selected_plan": transition_input.selected_plan,
            "authority_fingerprint": transition_input.authority_fingerprint,
        }
    if isinstance(transition_input, SelectDefaultPlan):
        return {
            "input_kind": input_kind(transition_input),
            "authority_fingerprint": transition_input.authority_fingerprint,
        }
    if isinstance(transition_input, EnqueueWork):
        return {
            "input_kind": input_kind(transition_input),
            "queue_family_id": transition_input.queue_family_id,
            "payload": transition_input.payload,
        }
    if isinstance(transition_input, SuspendDispatch):
        return {
            "input_kind": input_kind(transition_input),
            "plan_fingerprint": transition_input.plan_fingerprint,
            "actor_id": transition_input.actor_id,
            "reason": transition_input.reason,
        }
    if isinstance(transition_input, ResumeDispatch):
        return {
            "input_kind": input_kind(transition_input),
            "plan_fingerprint": transition_input.plan_fingerprint,
            "suspension_id": transition_input.suspension_id,
            "actor_id": transition_input.actor_id,
            "reason": transition_input.reason,
        }
    if isinstance(transition_input, CancelQueuedWork):
        return {
            "input_kind": input_kind(transition_input),
            "work_item_id": transition_input.work_item_id,
            "plan_fingerprint": transition_input.plan_fingerprint,
            "actor_id": transition_input.actor_id,
            "reason": transition_input.reason,
        }
    if isinstance(transition_input, CancelQueuedLineage):
        return {
            "input_kind": input_kind(transition_input),
            "lineage_id": transition_input.lineage_id,
            "plan_fingerprint": transition_input.plan_fingerprint,
            "actor_id": transition_input.actor_id,
            "reason": transition_input.reason,
        }
    if isinstance(transition_input, ClaimWork):
        return {
            "input_kind": input_kind(transition_input),
            "activation_id": transition_input.activation_id,
        }
    if isinstance(transition_input, CreateRunnerSession):
        return {
            "input_kind": input_kind(transition_input),
            "run_ref": transition_input.run_ref,
            "session_id": transition_input.session_id,
            "session_fencing_token": transition_input.session_fencing_token,
            "created_at": transition_input.created_at,
            "explicit_retry_intent": transition_input.explicit_retry_intent,
        }
    if isinstance(transition_input, AdvanceRunnerSession):
        return {
            "input_kind": input_kind(transition_input),
            "run_ref": transition_input.run_ref,
            "session_id": transition_input.session_id,
            "dispatch_generation": transition_input.dispatch_generation,
            "session_fencing_token": transition_input.session_fencing_token,
            "expected_state": transition_input.expected_state,
            "next_state": transition_input.next_state,
            "occurred_at": transition_input.occurred_at,
            "durable_locator_digest": transition_input.durable_locator_digest,
        }
    if isinstance(transition_input, RefuseRunnerSessionSignal):
        return {
            "input_kind": input_kind(transition_input),
            "run_ref": transition_input.run_ref,
            "session_id": transition_input.session_id,
            "dispatch_generation": transition_input.dispatch_generation,
            "session_fencing_token": transition_input.session_fencing_token,
            "expected_state": transition_input.expected_state,
            "signal_kind": transition_input.signal_kind,
            "reason": transition_input.reason,
            "signal_digest": transition_input.signal_digest,
        }
    if isinstance(transition_input, RequestRunnerSessionCancellation):
        return {
            "input_kind": input_kind(transition_input),
            "run_ref": transition_input.run_ref,
            "session_id": transition_input.session_id,
            "dispatch_generation": transition_input.dispatch_generation,
            "session_fencing_token": transition_input.session_fencing_token,
            "expected_state": transition_input.expected_state,
            "request_id": transition_input.request_id,
            "reason": transition_input.reason,
            "source_kind": transition_input.source_kind,
            "actor_id": transition_input.actor_id,
            "requested_at": transition_input.requested_at,
            "request_order": transition_input.request_order,
            "primary": transition_input.primary,
        }
    if isinstance(transition_input, RecordRunnerSessionCancellationAttempt):
        return {
            "input_kind": input_kind(transition_input),
            "run_ref": transition_input.run_ref,
            "session_id": transition_input.session_id,
            "dispatch_generation": transition_input.dispatch_generation,
            "session_fencing_token": transition_input.session_fencing_token,
            "expected_state": transition_input.expected_state,
            "attempt_id": transition_input.attempt_id,
            "request_id": transition_input.request_id,
            "sequence": transition_input.sequence,
            "operation": transition_input.operation,
            "result": transition_input.result,
            "started_at": transition_input.started_at,
            "completed_at": transition_input.completed_at,
            "bounded_diagnostic_digest": (
                transition_input.bounded_diagnostic_digest
            ),
        }
    if isinstance(transition_input, RecordRunnerSessionCompletion):
        return {
            "input_kind": input_kind(transition_input),
            "run_ref": transition_input.run_ref,
            "expected_state": transition_input.expected_state,
            "completion": transition_input.completion,
        }
    if isinstance(transition_input, FanoutFromArtifact):
        return {
            "input_kind": input_kind(transition_input),
            "fanout_id": transition_input.fanout_id,
            "source_artifact_id": transition_input.source_artifact_id,
        }
    if isinstance(transition_input, JoinFromArtifact):
        return {
            "input_kind": input_kind(transition_input),
            "join_id": transition_input.join_id,
            "source_artifact_id": transition_input.source_artifact_id,
        }
    if isinstance(transition_input, TimerDue):
        return {
            "input_kind": input_kind(transition_input),
            "wait_id": transition_input.wait_id,
            "observed_at": transition_input.observed_at,
        }
    if isinstance(transition_input, ReconcileEffect):
        return {
            "input_kind": input_kind(transition_input),
            "effect_id": transition_input.effect_id,
            "provider_ref": transition_input.provider_ref,
            "status": transition_input.status,
            "result": transition_input.result,
        }
    if isinstance(transition_input, OpenClosureTarget):
        return {
            "input_kind": input_kind(transition_input),
            "selected_plan_ref": transition_input.selected_plan_ref,
            "completion_behavior_id": transition_input.completion_behavior_id,
            "closure_target_id": transition_input.closure_target_id,
            "lineage_id": transition_input.lineage_id,
            "root_source_kind": transition_input.root_source_kind,
            "root_source_id": transition_input.root_source_id,
            "closure_root_work_item_id": transition_input.closure_root_work_item_id,
            "request_kind": transition_input.request_kind,
            "target_graph_node_id": transition_input.target_graph_node_id,
            "evidence_window": transition_input.evidence_window,
        }
    if isinstance(transition_input, EvaluateCompletionBehavior):
        return {
            "input_kind": input_kind(transition_input),
            "selected_plan_ref": transition_input.selected_plan_ref,
            "completion_behavior_id": transition_input.completion_behavior_id,
            "closure_target_id": transition_input.closure_target_id,
        }
    if isinstance(transition_input, RunnerResultObserved):
        return {
            "input_kind": input_kind(transition_input),
            "run_id": transition_input.run_id,
            "payload": transition_input.payload,
            "observed_at": transition_input.observed_at,
        }
    if isinstance(transition_input, OperatorResumeLineage):
        return _operator_lineage_payload(transition_input)
    if isinstance(transition_input, OperatorCloseLineage):
        return _operator_lineage_payload(transition_input)
    if isinstance(transition_input, OperatorReviseLineage):
        return _operator_lineage_payload(transition_input)
    if isinstance(transition_input, OperatorResumeWait):
        return _operator_wait_payload(transition_input)
    if isinstance(transition_input, OperatorCloseWait):
        return _operator_wait_payload(transition_input)
    if isinstance(transition_input, OperatorReviseWait):
        return _operator_wait_payload(transition_input)
    return {"input_kind": input_kind(transition_input)}


def _operator_lineage_payload(
    transition_input: (
        OperatorResumeLineage | OperatorCloseLineage | OperatorReviseLineage
    ),
) -> Mapping[str, object]:
    return {
        "input_kind": input_kind(transition_input),
        "option_id": transition_input.option_id,
        "selected_plan_ref": transition_input.selected_plan_ref,
        "quarantine_id": transition_input.quarantine_id,
        "lineage_id": transition_input.lineage_id,
        "actor_id": transition_input.actor_id,
        "actor_kind": transition_input.actor_kind,
        "payload": transition_input.payload,
    }


def _operator_wait_payload(
    transition_input: OperatorResumeWait | OperatorCloseWait | OperatorReviseWait,
) -> Mapping[str, object]:
    return {
        "input_kind": input_kind(transition_input),
        "selected_plan_ref": transition_input.selected_plan_ref,
        "wait_id": transition_input.wait_id,
        "lineage_id": transition_input.lineage_id,
        "actor_id": transition_input.actor_id,
        "actor_kind": transition_input.actor_kind,
        "payload": transition_input.payload,
    }


def _canonical_input_value(value: object) -> CanonicalInputValue:
    if value is None or isinstance(value, (str, bool)):
        return value
    if type(value) is int:
        return value
    if _is_string_backed_id(value):
        return str(value)
    if isinstance(value, (list, tuple)):
        return [_canonical_input_value(item) for item in value]
    if isinstance(value, Mapping):
        return _canonical_input_mapping(value)
    if is_dataclass(value) and not isinstance(value, type):
        return _canonical_input_record(value)
    raise TypeError(
        f"unsupported transition input payload type: {type(value).__name__}"
    )


def _canonical_input_mapping(
    value: Mapping[Any, Any],
) -> dict[str, CanonicalInputValue]:
    canonical: dict[str, CanonicalInputValue] = {}
    for key, nested_value in value.items():
        if not isinstance(key, str):
            raise TypeError("transition input payload map keys must be strings")
        canonical[key] = _canonical_input_value(nested_value)
    return canonical


def _canonical_input_record(value: object) -> dict[str, CanonicalInputValue]:
    record: dict[str, object] = {}
    record_kind = getattr(value, "record_kind", None)
    schema_version = getattr(value, "schema_version", None)
    if isinstance(record_kind, str):
        record["record_kind"] = record_kind
    if type(schema_version) is int:
        record["schema_version"] = schema_version
    for record_field in fields(cast(Any, value)):
        record[record_field.name] = getattr(value, record_field.name)
    return _canonical_input_mapping(record)


def _is_string_backed_id(value: object) -> bool:
    return value.__class__.__module__ == "millrace.contracts.ids" and isinstance(
        getattr(value, "value", None), str
    )


__all__ = (
    "AdmitPlan",
    "AdmitPlanRef",
    "AdvanceRunnerSession",
    "AdvanceRunnerSessionRecord",
    "CancelQueuedLineage",
    "CancelQueuedWork",
    "ClaimWork",
    "CloseWorkItem",
    "ControlInput",
    "CreateActivation",
    "CreateRunnerSession",
    "CreateRunnerSessionRecord",
    "CreateRun",
    "CreateWorkItem",
    "EmitGovernanceEvent",
    "EmitTrace",
    "EnqueueWork",
    "EvaluateCompletionBehavior",
    "FanoutFromArtifact",
    "InitializeWorkspace",
    "InputReceiptRef",
    "JoinFromArtifact",
    "KernelCommand",
    "Observation",
    "OperatorCommand",
    "OperatorCloseLineage",
    "OperatorCloseWait",
    "OperatorResumeLineage",
    "OperatorResumeWait",
    "OperatorReviseLineage",
    "OperatorReviseWait",
    "OpenClosureTarget",
    "RecordArtifact",
    "CloseClosureTarget",
    "RecordClosureEvaluation",
    "RecordClosureBlocked",
    "RecordClosureTarget",
    "RecordClosureTerminal",
    "RecordCooldownWait",
    "RecordFanout",
    "RecordEffectProposal",
    "RecordEffectReconciliation",
    "RecordInputReceipt",
    "RecordLineageQuarantine",
    "RecordOperatorIntervention",
    "RecordOperatorWait",
    "RecordQueueClosure",
    "RecordRefusal",
    "RecordRecoveryAttempt",
    "RecordRemediationWork",
    "RecordRunnerObservation",
    "RecordRunnerSessionCancellation",
    "RecordRunnerSessionCancellationAttempt",
    "RecordRunnerSessionCancellationAttemptRecord",
    "RecordRunnerSessionCompletion",
    "RecordRunnerSessionCompletionRecord",
    "RefuseRunnerSessionSignal",
    "RecordTransition",
    "RecordWorkDependency",
    "RouteActivation",
    "ReconcileEffect",
    "ResumeDispatch",
    "RunnerResultObserved",
    "RequestRunnerSessionCancellation",
    "SelectDefaultPlan",
    "SelectDefaultPlanRef",
    "SetPause",
    "SetDispatchSuspension",
    "SetQuarantine",
    "SupersedeLineageQuarantine",
    "SuspendDispatch",
    "TimerDue",
    "TransitionContext",
    "TransitionDecision",
    "TransitionInput",
    "TransitionMutation",
    "TransitionRecord",
    "TransitionRefusal",
    "WorkflowInput",
    "artifact_payload_digest",
    "canonical_authority_mapping_bytes",
    "input_family",
    "input_kind",
    "input_payload_digest",
    "operator_payload_digest",
)
