"""Read-only local-operator status projection."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from itertools import zip_longest
from typing import TypeVar

from millrace.contracts.compiled_plan import (
    AuthorityValue,
    FanoutDeclaration,
    GeneratedWorkRouteDeclaration,
    JoinDeclaration,
    PartitionDeclaration,
    SelectedCompiledPlan,
    StageKindDeclaration,
    TerminalActionDeclaration,
)
from millrace.contracts.fingerprints import AuthorityFingerprint
from millrace.contracts.state import (
    Activation,
    AdmittedPlan,
    ArtifactRecord,
    ClosureBlockedRecord,
    ClosureEvaluationRecord,
    ClosureTargetRecord,
    ClosureTerminalRecord,
    DaemonBudgetEpochRecord,
    EffectProposalRecord,
    EffectReconciliationRecord,
    FanoutRecord,
    GovernanceEventRecord,
    OperatorWaitRecord,
    QueueClosureRecord,
    RemediationWorkRecord,
    RunnerObservationRecord,
    RunRecord,
    RuntimeState,
    TraceRecord,
    WorkItem,
)
from millrace.contracts.transition import (
    ReconcileEffect,
    RunnerResultObserved,
    artifact_payload_digest,
)
from millrace.operator.dispatch import (
    DispatchSuspensionProjection,
    dispatch_suspension_projection,
    join_evidence_progress_for_status,
)
from millrace.operator.intake import OperatorInputError


def daemon_budget_projection(
    epoch: DaemonBudgetEpochRecord,
) -> dict[str, object]:
    return {
        "budget_id": epoch.budget_id,
        "workspace_path": epoch.workspace_path,
        "plan_id": epoch.selected_plan_ref.plan_id,
        "plan_authority_fingerprint": (
            epoch.selected_plan_ref.authority_fingerprint
        ),
        "plan_format_version": epoch.selected_plan_ref.plan_format_version,
        "max_wall_seconds": epoch.max_wall_seconds,
        "max_invocations": epoch.max_invocations,
        "max_total_tokens": epoch.max_total_tokens,
        "started_at": epoch.started_at,
        "wall_deadline": epoch.wall_deadline,
        "last_observed_at": epoch.last_observed_at,
        "accepted_start_count": epoch.accepted_start_count,
        "cumulative_input_tokens": epoch.cumulative_input_tokens,
        "cumulative_output_tokens": epoch.cumulative_output_tokens,
        "cumulative_total_tokens": epoch.cumulative_total_tokens,
        "status": epoch.status,
        "terminal_reason": epoch.terminal_reason,
        "invocation_overshoot": (
            0
            if epoch.max_invocations is None
            else max(0, epoch.accepted_start_count - epoch.max_invocations)
        ),
        "token_overshoot": (
            0
            if epoch.max_total_tokens is None
            else max(0, epoch.cumulative_total_tokens - epoch.max_total_tokens)
        ),
        "wall_cleanup_grace_overshoot": (
            0
            if epoch.wall_deadline is None
            else max(0, epoch.last_observed_at - epoch.wall_deadline)
        ),
    }

_ClosureLatestT = TypeVar(
    "_ClosureLatestT",
    ClosureTerminalRecord,
    RemediationWorkRecord,
    ClosureBlockedRecord,
)


@dataclass(frozen=True, slots=True)
class SelectedPlanStatus:
    plan_id: str
    workflow_id: str
    workflow_version: str
    workflow_name: str
    authority_fingerprint: AuthorityFingerprint
    plan_format_version: int


@dataclass(frozen=True, slots=True)
class KnownPlanStatus:
    plan_id: str
    workflow_id: str
    workflow_version: str
    workflow_name: str
    authority_fingerprint: AuthorityFingerprint
    plan_format_version: int
    selected_default: bool


@dataclass(frozen=True, slots=True)
class ActivePackagePinStatus:
    authority_fingerprint: AuthorityFingerprint
    package_id: str
    package_version: str
    package_format_version: str
    workflow_id: str
    workflow_version: str
    entrypoint: str
    selected_asset_pins: tuple[tuple[str, str], ...]
    selected_dependency_pins: tuple[tuple[str, str, str], ...]


@dataclass(frozen=True, slots=True)
class QueueFamilyStatus:
    queue_family_id: str
    external_enqueue: bool
    display_name: str | None
    description: str | None
    ready_count: int
    active_count: int
    closed_count: int
    quarantined_count: int
    operator_wait_count: int = 0


@dataclass(frozen=True, slots=True)
class QueueClosureStatus:
    closure_id: str
    plan_fingerprint: AuthorityFingerprint
    target_kind: str
    target_id: str
    actor_id: str
    reason: str
    input_id: str
    closed_work_item_count: int
    closed_work_item_ids: tuple[str, ...]
    closed_activation_count: int
    closed_activation_ids: tuple[str, ...]
    closed_run_count: int
    closed_run_ids: tuple[str, ...]
    omitted_work_item_count: int
    omitted_activation_count: int
    omitted_run_count: int


@dataclass(frozen=True, slots=True)
class QueueClosureProjection:
    count: int
    records: tuple[QueueClosureStatus, ...]
    omitted_record_count: int


@dataclass(frozen=True, slots=True)
class PartitionStatus:
    partition_id: str
    partition_kind: str
    display_name: str | None
    description: str | None


@dataclass(frozen=True, slots=True)
class StageKindStatus:
    stage_kind_id: str
    partition_id: str | None
    runner_binding_id: str
    display_name: str | None
    description: str | None
    ready_count: int = 0
    active_count: int = 0
    closed_count: int = 0
    operator_wait_count: int = 0


@dataclass(frozen=True, slots=True)
class ActiveRunStatus:
    run_id: str
    work_item_id: str
    activation_id: str
    lineage_id: str | None
    queue_family_id: str
    graph_node_id: str
    stage_kind_id: str
    runner_binding_id: str
    claim_id: str
    generation: int
    fencing_token: str
    plan_fingerprint: AuthorityFingerprint


@dataclass(frozen=True, slots=True)
class ArtifactStatus:
    artifact_id: str
    workflow_id: str
    selected_plan_id: str
    selected_plan_fingerprint: AuthorityFingerprint
    work_item_id: str
    queue_family_id: str
    lineage_id: str | None
    schema_id: str
    payload: Mapping[str, AuthorityValue]
    payload_digest: str
    source_run_id: str
    source_activation_id: str
    source_action_id: str
    terminal_action_id: str
    source_input_id: str
    source_stage_kind_id: str
    source_graph_node_id: str
    source_runner_binding_id: str
    latest_marker: str | None
    transition_id: str


@dataclass(frozen=True, slots=True)
class EffectStatus:
    effect_id: str
    workflow_id: str
    selected_plan_fingerprint: AuthorityFingerprint
    status: str
    proposal_status: str
    reconciliation_id: str | None
    reconciliation_status: str | None
    fake_local_result_digest: str | None
    effect_declaration_id: str
    dedupe_key: str
    provider_ref: str
    capability_policy_ref: str
    target_ref_kind: str
    target_ref_schema: str
    target_skill_id: str | None
    target_path_ref: str | None
    terminal_action_id: str
    source_action_id: str
    source_input_id: str
    source_run_id: str
    source_work_item_id: str
    source_activation_id: str
    source_graph_node_id: str
    source_stage_kind_id: str
    source_runner_binding_id: str
    source_queue_family_id: str
    lineage_id: str | None
    artifact_id: str
    artifact_schema_id: str
    artifact_payload_digest: str
    created_input_id: str
    created_transition_id: str


@dataclass(frozen=True, slots=True)
class GeneratedWorkStatus:
    generated_work_id: str
    workflow_id: str
    selected_plan_fingerprint: AuthorityFingerprint
    fanout_id: str
    target_route_id: str
    source_artifact_id: str
    source_artifact_digest: str
    source_work_item_id: str
    source_run_id: str
    source_action_id: str
    source_input_id: str
    target_work_item_id: str
    target_activation_id: str
    target_queue_family_id: str
    target_stage_kind_id: str
    target_graph_node_id: str
    target_runner_binding_id: str
    item_key: str
    lineage_id: str | None
    created_by_input_id: str


@dataclass(frozen=True, slots=True)
class JoinStatus:
    join_id: str
    selected_plan_fingerprint: AuthorityFingerprint
    source_artifact_id: str
    source_work_item_id: str
    lineage_id: str | None
    source_artifact_schema_id: str
    correlation_key: str
    correlation_value: AuthorityValue | None
    required_artifact_schema_ids: tuple[str, ...]
    observed_artifact_schema_ids: tuple[str, ...]
    missing_artifact_schema_ids: tuple[str, ...]
    missing_policy: str
    ready: bool
    target_queue_family_id: str | None
    target_stage_kind_id: str
    target_graph_node_id: str | None
    target_runner_binding_id: str | None


@dataclass(frozen=True, slots=True)
class PauseStatus:
    is_paused: bool
    record_id: str | None = None
    source_run_id: str | None = None
    work_item_id: str | None = None
    action_id: str | None = None
    created_by_input_id: str | None = None


@dataclass(frozen=True, slots=True)
class QuarantineStatus:
    record_id: str
    work_item_id: str
    source_run_id: str
    action_id: str
    created_by_input_id: str
    queue_family_id: str | None
    quarantine_kind: str = "work_item"
    lineage_id: str | None = None
    policy_id: str | None = None
    selected_plan_fingerprint: AuthorityFingerprint | None = None
    recovery_attempt_record_id: str | None = None
    original_source_run_id: str | None = None
    original_source_work_item_id: str | None = None
    original_source_activation_id: str | None = None
    emitting_recovery_activation_id: str | None = None
    emitting_recovery_run_id: str | None = None
    attempt_count: int | None = None
    actor_kind: str | None = None
    status: str | None = None
    superseded_input_id: str | None = None


@dataclass(frozen=True, slots=True)
class RecoveryAttemptStatus:
    record_id: str
    policy_id: str
    lineage_id: str
    plan_fingerprint: AuthorityFingerprint
    attempt_count: int
    phase: str
    source_run_id: str
    source_work_item_id: str
    source_activation_id: str
    source_graph_node_id: str
    source_stage_kind_id: str
    source_runner_binding_id: str
    source_queue_family_id: str
    recovery_action_id: str
    latest_recovery_activation_id: str | None
    latest_recovery_run_id: str | None
    latest_return_action_id: str | None
    created_by_input_id: str
    updated_by_input_id: str


@dataclass(frozen=True, slots=True)
class CooldownWaitStatus:
    wait_id: str
    policy_id: str
    lineage_id: str
    recovery_attempt_record_id: str
    plan_fingerprint: AuthorityFingerprint
    attempt_count: int
    source_run_id: str
    source_work_item_id: str
    source_activation_id: str
    recovery_action_id: str
    target_stage_kind_id: str
    target_graph_node_id: str
    target_runner_binding_id: str
    created_input_id: str
    created_at: int
    due_at: int
    consumed_input_id: str | None
    consumed_at: int | None
    resulting_recovery_activation_id: str | None


@dataclass(frozen=True, slots=True)
class CounterStatus:
    record_id: str
    counter_id: str
    lineage_id: str
    plan_fingerprint: AuthorityFingerprint
    value: int
    updated_by_input_id: str


@dataclass(frozen=True, slots=True)
class OperatorInterventionStatus:
    record_id: str
    created_by_input_id: str
    input_payload_digest: str
    option_id: str
    kind: str
    result: str
    policy_id: str
    lineage_id: str
    quarantine_id: str
    recovery_attempt_record_id: str
    recovery_attempt_count: int
    attempt_effect: str
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


@dataclass(frozen=True, slots=True)
class OperatorWaitStatus:
    wait_id: str
    operator_wait_id: str
    source_action_id: str
    lineage_id: str
    selected_plan_fingerprint: AuthorityFingerprint
    source_work_item_id: str
    source_activation_id: str
    source_run_id: str
    source_stage_kind_id: str
    source_graph_node_id: str
    source_queue_family_id: str
    source_runner_binding_id: str
    source_artifact_id: str | None
    status: str
    created_input_id: str
    resolved_input_id: str | None
    actor_id: str | None
    actor_kind: str | None
    resolution_kind: str | None
    target_work_item_id: str | None
    target_activation_id: str | None
    closed_work_item_ids: tuple[str, ...]
    payload_digest: str | None
    payload_reference: str | None
    allowed_resolution_kinds: tuple[str, ...] = ()
    actor_kind_requirement: str | None = None
    audit_metadata_requirements: tuple[str, ...] = ()
    payload_schema_id: str | None = None
    target_queue_family_id: str | None = None
    target_stage_kind_id: str | None = None
    target_graph_node_id: str | None = None
    target_runner_binding_id: str | None = None
    status_effect: str | None = None


@dataclass(frozen=True, slots=True)
class ClosureTargetStatus:
    closure_target_id: str
    completion_behavior_id: str
    status: str
    lineage_id: str
    root_source_kind: str
    root_source_id: str
    closure_root_work_item_id: str | None
    request_kind: str
    target_graph_node_id: str
    evidence_window: Mapping[str, AuthorityValue]
    selected_plan_fingerprint: AuthorityFingerprint
    opened_by_input_id: str
    closed_by_record_id: str | None
    active_evaluator_record_id: str | None
    active_evaluator_run_id: str | None
    latest_terminal_record_id: str | None
    latest_terminal_kind: str | None
    latest_terminal_artifact_id: str | None
    latest_remediation_record_id: str | None
    latest_remediation_work_item_id: str | None
    latest_blocked_record_id: str | None
    operator_required: bool


@dataclass(frozen=True, slots=True)
class ClosureEvaluationStatus:
    record_id: str
    closure_target_id: str
    completion_behavior_id: str
    request_kind: str
    target_work_item_id: str
    target_activation_id: str
    target_run_id: str | None
    lineage_id: str
    selected_plan_fingerprint: AuthorityFingerprint
    queue_family_id: str | None
    graph_node_id: str | None
    stage_kind_id: str | None
    runner_binding_id: str | None
    status: str


@dataclass(frozen=True, slots=True)
class ClosureRemediationStatus:
    record_id: str
    remediation_policy_id: str
    closure_target_id: str
    source_run_id: str
    source_action_id: str
    source_artifact_id: str | None
    target_work_item_id: str
    target_activation_id: str
    lineage_id: str
    selected_plan_fingerprint: AuthorityFingerprint
    dedupe_key: str
    target_queue_family_id: str | None
    target_graph_node_id: str | None
    target_stage_kind_id: str | None
    target_runner_binding_id: str | None


@dataclass(frozen=True, slots=True)
class ClosureBlockedStatus:
    record_id: str
    closure_target_id: str
    completion_behavior_id: str
    source_run_id: str
    source_action_id: str
    lineage_id: str
    selected_plan_fingerprint: AuthorityFingerprint
    operator_required: bool


@dataclass(frozen=True, slots=True)
class RecentEventStatus:
    record_id: str
    input_id: str
    input_kind: str
    input_family: str
    disposition: str
    plan_fingerprint: AuthorityFingerprint | None
    work_item_id: str | None
    run_id: str | None
    action_id: str | None
    authority_source: str | None
    refusal_reason: str | None
    source: str


@dataclass(frozen=True, slots=True)
class OperatorStatus:
    selected_plan: SelectedPlanStatus | None
    known_plans: tuple[KnownPlanStatus, ...]
    active_package_pins: tuple[ActivePackagePinStatus, ...]
    queue_families: tuple[QueueFamilyStatus, ...]
    partitions: tuple[PartitionStatus, ...]
    stage_kinds: tuple[StageKindStatus, ...]
    active_runs: tuple[ActiveRunStatus, ...]
    artifacts: tuple[ArtifactStatus, ...]
    effects: tuple[EffectStatus, ...]
    generated_work: tuple[GeneratedWorkStatus, ...]
    joins: tuple[JoinStatus, ...]
    pause: PauseStatus
    dispatch_suspension: DispatchSuspensionProjection
    queue_closures: QueueClosureProjection
    quarantines: tuple[QuarantineStatus, ...]
    recovery_attempts: tuple[RecoveryAttemptStatus, ...]
    cooldown_waits: tuple[CooldownWaitStatus, ...]
    counters: tuple[CounterStatus, ...]
    interventions: tuple[OperatorInterventionStatus, ...]
    operator_waits: tuple[OperatorWaitStatus, ...]
    closure_targets: tuple[ClosureTargetStatus, ...]
    closure_evaluations: tuple[ClosureEvaluationStatus, ...]
    closure_remediations: tuple[ClosureRemediationStatus, ...]
    closure_blocks: tuple[ClosureBlockedStatus, ...]
    recent_events: tuple[RecentEventStatus, ...]


@dataclass(frozen=True, slots=True)
class _StatusCoherenceProjection:
    ready_work_item_ids: frozenset[str]
    active_work_item_ids: frozenset[str]
    active_runs: tuple[ActiveRunStatus, ...]


@dataclass(frozen=True, slots=True)
class _SourceContext:
    run: RunRecord
    activation: Activation
    work_item: WorkItem
    stage: StageKindDeclaration


def operator_status(
    state: RuntimeState,
    *,
    plan_fingerprint: AuthorityFingerprint | None = None,
    max_events: int = 20,
) -> OperatorStatus:
    if type(max_events) is not int or max_events < 0:
        raise OperatorInputError("invalid_max_events")
    if plan_fingerprint is not None and not _is_valid_authority_fingerprint(
        plan_fingerprint
    ):
        raise OperatorInputError("invalid_plan_fingerprint")

    selected = _selected_admitted_plan(state, plan_fingerprint)
    selected_fingerprint = (
        selected.plan_ref.authority_fingerprint if selected is not None else None
    )
    selected_plan = (
        _selected_plan_status(selected)
        if selected is not None
        else None
    )
    selected_authority = selected.selected_plan if selected is not None else None
    active_lineage_quarantines = _active_lineage_quarantine_ids(
        state,
        selected_fingerprint,
    )
    active_operator_wait_lineages = _active_operator_wait_lineage_ids(
        state,
        selected_fingerprint,
    )
    coherence_projection = _resolve_status_coherence(
        state,
        selected_fingerprint,
        active_lineage_quarantines | active_operator_wait_lineages,
    )

    return OperatorStatus(
        selected_plan=selected_plan,
        known_plans=_known_plan_statuses(state),
        active_package_pins=_active_package_pin_statuses(state),
        queue_families=_queue_family_statuses(
            state,
            selected_authority,
            selected_fingerprint,
            coherence_projection,
            active_lineage_quarantines,
            active_operator_wait_lineages,
        ),
        partitions=_partition_statuses(selected_authority),
        stage_kinds=_stage_kind_statuses(
            state,
            selected_authority,
            selected_fingerprint,
            coherence_projection,
        ),
        active_runs=coherence_projection.active_runs,
        artifacts=_artifact_statuses(
            state,
            selected_authority,
            selected_fingerprint,
        ),
        effects=_effect_statuses(
            state,
            selected_authority,
            selected_fingerprint,
        ),
        generated_work=_generated_work_statuses(
            state,
            selected_authority,
            selected_fingerprint,
        ),
        joins=_join_statuses(
            state,
            selected_authority,
            selected_fingerprint,
        ),
        pause=_pause_status(state),
        dispatch_suspension=dispatch_suspension_projection(state),
        queue_closures=queue_closure_projection(state),
        quarantines=_quarantine_statuses(state),
        recovery_attempts=_recovery_attempt_statuses(
            state,
            selected_fingerprint,
        ),
        cooldown_waits=_cooldown_wait_statuses(
            state,
            selected_fingerprint,
        ),
        counters=_counter_statuses(
            state,
            selected_fingerprint,
        ),
        interventions=_operator_intervention_statuses(
            state,
            selected_fingerprint,
        ),
        operator_waits=_operator_wait_statuses(
            state,
            selected_authority,
            selected_fingerprint,
        ),
        closure_targets=_closure_target_statuses(
            state,
            selected_fingerprint,
        ),
        closure_evaluations=_closure_evaluation_statuses(
            state,
            selected_fingerprint,
        ),
        closure_remediations=_closure_remediation_statuses(
            state,
            selected_fingerprint,
        ),
        closure_blocks=_closure_block_statuses(
            state,
            selected_fingerprint,
        ),
        recent_events=_recent_event_statuses(state, max_events),
    )


def queue_closure_projection(
    state: RuntimeState,
    *,
    max_records: int = 20,
    max_ids: int = 20,
) -> QueueClosureProjection:
    if (
        type(max_records) is not int
        or max_records < 0
        or type(max_ids) is not int
        or max_ids < 0
    ):
        raise ValueError("queue closure projection bounds must be non-negative")
    records = tuple(state.queue_closures.values())
    retained = records[-max_records:] if max_records else ()
    return QueueClosureProjection(
        count=len(records),
        records=tuple(
            _queue_closure_status(record, max_ids=max_ids)
            for record in retained
        ),
        omitted_record_count=len(records) - len(retained),
    )


def _queue_closure_status(
    record: QueueClosureRecord,
    *,
    max_ids: int,
) -> QueueClosureStatus:
    return QueueClosureStatus(
        closure_id=record.closure_id,
        plan_fingerprint=record.selected_plan_ref.authority_fingerprint,
        target_kind=record.target_kind,
        target_id=record.target_id,
        actor_id=record.actor_id,
        reason=record.reason,
        input_id=record.created_by_input_id,
        closed_work_item_count=len(record.closed_work_item_ids),
        closed_work_item_ids=record.closed_work_item_ids[:max_ids],
        closed_activation_count=len(record.closed_activation_ids),
        closed_activation_ids=record.closed_activation_ids[:max_ids],
        closed_run_count=len(record.closed_run_ids),
        closed_run_ids=record.closed_run_ids[:max_ids],
        omitted_work_item_count=max(
            0,
            len(record.closed_work_item_ids) - max_ids,
        ),
        omitted_activation_count=max(
            0,
            len(record.closed_activation_ids) - max_ids,
        ),
        omitted_run_count=max(0, len(record.closed_run_ids) - max_ids),
    )


def _selected_admitted_plan(
    state: RuntimeState,
    plan_fingerprint: AuthorityFingerprint | None,
) -> AdmittedPlan | None:
    if plan_fingerprint is not None:
        return state.admitted_plans.get(plan_fingerprint)
    if state.default_plan_ref is None:
        return None
    return state.admitted_plans.get(state.default_plan_ref.authority_fingerprint)


def _selected_plan_status(admitted: AdmittedPlan) -> SelectedPlanStatus:
    workflow = admitted.selected_plan.workflow
    return SelectedPlanStatus(
        plan_id=admitted.plan_ref.plan_id,
        workflow_id=str(workflow.workflow_id),
        workflow_version=str(workflow.workflow_version),
        workflow_name=workflow.workflow_name,
        authority_fingerprint=admitted.plan_ref.authority_fingerprint,
        plan_format_version=admitted.plan_ref.plan_format_version,
    )


def _known_plan_statuses(state: RuntimeState) -> tuple[KnownPlanStatus, ...]:
    default_fingerprint = (
        state.default_plan_ref.authority_fingerprint
        if state.default_plan_ref is not None
        else None
    )
    records: list[KnownPlanStatus] = []
    for fingerprint, admitted in sorted(state.admitted_plans.items()):
        workflow = admitted.selected_plan.workflow
        records.append(
            KnownPlanStatus(
                plan_id=admitted.plan_ref.plan_id,
                workflow_id=str(workflow.workflow_id),
                workflow_version=str(workflow.workflow_version),
                workflow_name=workflow.workflow_name,
                authority_fingerprint=fingerprint,
                plan_format_version=admitted.plan_ref.plan_format_version,
                selected_default=fingerprint == default_fingerprint,
            )
        )
    return tuple(records)


def _active_package_pin_statuses(
    state: RuntimeState,
) -> tuple[ActivePackagePinStatus, ...]:
    records: list[ActivePackagePinStatus] = []
    for fingerprint, admitted in sorted(state.admitted_plans.items()):
        pin = admitted.selected_plan.workflow_package_pin
        if pin is None:
            continue
        records.append(
            ActivePackagePinStatus(
                authority_fingerprint=fingerprint,
                package_id=pin.package_id,
                package_version=pin.package_version,
                package_format_version=pin.package_format_version,
                workflow_id=pin.workflow_id,
                workflow_version=pin.workflow_version,
                entrypoint=pin.entrypoint,
                selected_asset_pins=tuple(
                    (item.asset_id, item.content_digest)
                    for item in pin.selected_asset_pins
                ),
                selected_dependency_pins=tuple(
                    (
                        item.package_id,
                        item.package_version,
                        item.package_format_version,
                    )
                    for item in pin.selected_dependency_pins
                ),
            )
        )
    return tuple(records)


def _queue_family_statuses(
    state: RuntimeState,
    selected_plan: SelectedCompiledPlan | None,
    selected_fingerprint: AuthorityFingerprint | None,
    coherence_projection: _StatusCoherenceProjection,
    active_lineage_quarantines: frozenset[str],
    active_operator_wait_lineages: frozenset[str],
) -> tuple[QueueFamilyStatus, ...]:
    if selected_plan is None or selected_fingerprint is None:
        return ()
    closed_work_item_ids = set(state.closed_work_items)
    quarantined_work_item_ids = set(state.quarantines)
    records: list[QueueFamilyStatus] = []
    for queue_family in sorted(
        selected_plan.queue_families,
        key=lambda item: str(item.id),
    ):
        queue_family_id = str(queue_family.id)
        matching_work_items = [
            work_item
            for work_item in state.work_items.values()
            if str(work_item.queue_family_id) == queue_family_id
            and work_item.ref.plan_ref.authority_fingerprint == selected_fingerprint
        ]
        records.append(
            QueueFamilyStatus(
                queue_family_id=queue_family_id,
                external_enqueue=queue_family.external_enqueue,
                display_name=_presentation_text(
                    queue_family.presentation,
                    "display_name",
                ),
                description=_presentation_text(
                    queue_family.presentation,
                    "description",
                ),
                ready_count=sum(
                    1
                    for work_item in matching_work_items
                    if work_item.ref.work_item_id not in closed_work_item_ids
                    and work_item.ref.work_item_id not in quarantined_work_item_ids
                    and work_item.lineage_id not in active_lineage_quarantines
                    and work_item.ref.work_item_id
                    in coherence_projection.ready_work_item_ids
                ),
                active_count=sum(
                    1
                    for work_item in matching_work_items
                    if work_item.ref.work_item_id not in closed_work_item_ids
                    and work_item.lineage_id not in active_lineage_quarantines
                    and work_item.ref.work_item_id
                    in coherence_projection.active_work_item_ids
                ),
                closed_count=sum(
                    1
                    for work_item in matching_work_items
                    if work_item.ref.work_item_id in closed_work_item_ids
                ),
                quarantined_count=sum(
                    1
                    for work_item in matching_work_items
                    if work_item.ref.work_item_id in quarantined_work_item_ids
                    or work_item.lineage_id in active_lineage_quarantines
                ),
                operator_wait_count=sum(
                    1
                    for work_item in matching_work_items
                    if work_item.lineage_id in active_operator_wait_lineages
                    and work_item.ref.work_item_id not in closed_work_item_ids
                ),
            )
        )
    return tuple(records)


def _partition_statuses(
    selected_plan: SelectedCompiledPlan | None,
) -> tuple[PartitionStatus, ...]:
    if selected_plan is None:
        return ()
    return tuple(
        _partition_status(partition)
        for partition in sorted(selected_plan.partitions, key=lambda item: str(item.id))
    )


def _partition_status(partition: PartitionDeclaration) -> PartitionStatus:
    return PartitionStatus(
        partition_id=str(partition.id),
        partition_kind=partition.partition_kind,
        display_name=_presentation_text(partition.presentation, "display_name"),
        description=_presentation_text(partition.presentation, "description"),
    )


def _stage_kind_statuses(
    state: RuntimeState,
    selected_plan: SelectedCompiledPlan | None,
    selected_fingerprint: AuthorityFingerprint | None,
    coherence_projection: _StatusCoherenceProjection,
) -> tuple[StageKindStatus, ...]:
    if selected_plan is None or selected_fingerprint is None:
        return ()
    return tuple(
        _stage_kind_status(
            state,
            stage_kind,
            selected_fingerprint,
            coherence_projection,
        )
        for stage_kind in sorted(
            selected_plan.stage_kinds,
            key=lambda item: str(item.id),
        )
    )


def _stage_kind_status(
    state: RuntimeState,
    stage_kind: StageKindDeclaration,
    selected_fingerprint: AuthorityFingerprint,
    coherence_projection: _StatusCoherenceProjection,
) -> StageKindStatus:
    stage_kind_id = str(stage_kind.id)
    closed_work_item_ids = set(state.closed_work_items)
    return StageKindStatus(
        stage_kind_id=stage_kind_id,
        partition_id=(
            str(stage_kind.partition_id)
            if stage_kind.partition_id is not None
            else None
        ),
        runner_binding_id=str(stage_kind.runner_binding_id),
        display_name=_presentation_text(stage_kind.presentation, "display_name"),
        description=_presentation_text(stage_kind.presentation, "description"),
        ready_count=sum(
            1
            for activation in state.activations.values()
            if str(activation.stage_kind_id) == stage_kind_id
            and activation.plan_ref.authority_fingerprint == selected_fingerprint
            and activation.work_item_id in coherence_projection.ready_work_item_ids
        ),
        active_count=sum(
            1
            for run in coherence_projection.active_runs
            if run.stage_kind_id == stage_kind_id
        ),
        closed_count=len(
            {
                activation.work_item_id
                for activation in state.activations.values()
                if str(activation.stage_kind_id) == stage_kind_id
                and activation.plan_ref.authority_fingerprint == selected_fingerprint
                and activation.work_item_id in closed_work_item_ids
            }
        ),
        operator_wait_count=sum(
            1
            for wait in state.operator_waits.values()
            if str(wait.source_stage_kind_id) == stage_kind_id
            and wait.selected_plan_fingerprint == selected_fingerprint
            and wait.status == "active"
        ),
    )


def _resolve_status_coherence(
    state: RuntimeState,
    selected_fingerprint: AuthorityFingerprint | None,
    active_lineage_quarantines: frozenset[str],
) -> _StatusCoherenceProjection:
    if selected_fingerprint is None:
        return _StatusCoherenceProjection(
            ready_work_item_ids=frozenset(),
            active_work_item_ids=frozenset(),
            active_runs=(),
        )
    selected_work_items = {
        work_item.ref.work_item_id: work_item
        for work_item in state.work_items.values()
        if work_item.ref.plan_ref.authority_fingerprint == selected_fingerprint
    }
    closed_work_item_ids = set(state.closed_work_items)
    observed_run_ids = {
        observation.run_id for observation in state.runner_observations.values()
    }
    active_work_item_ids: set[str] = set()
    active_runs: list[ActiveRunStatus] = []
    for _run_id, run in sorted(state.runs.items()):
        work_item = selected_work_items.get(run.work_item_id)
        if work_item is None:
            continue
        if work_item.ref.work_item_id in closed_work_item_ids:
            continue
        if work_item.lineage_id in active_lineage_quarantines:
            continue
        activation = state.activations.get(run.activation_id)
        if activation is None:
            continue
        if not _is_coherent_live_run(
            run,
            activation,
            work_item,
            observed_run_ids,
        ):
            continue
        active_work_item_ids.add(work_item.ref.work_item_id)
        active_runs.append(_active_run_status(run, activation, work_item))

    ready_work_item_ids: set[str] = set()
    for activation in state.activations.values():
        work_item = selected_work_items.get(activation.work_item_id)
        if work_item is None:
            continue
        if work_item.ref.work_item_id in closed_work_item_ids:
            continue
        if work_item.lineage_id in active_lineage_quarantines:
            continue
        if work_item.ref.work_item_id in active_work_item_ids:
            continue
        if _is_coherent_unclaimed_activation(activation, work_item):
            ready_work_item_ids.add(work_item.ref.work_item_id)

    return _StatusCoherenceProjection(
        ready_work_item_ids=frozenset(ready_work_item_ids),
        active_work_item_ids=frozenset(active_work_item_ids),
        active_runs=tuple(active_runs),
    )


def _is_coherent_unclaimed_activation(
    activation: Activation,
    work_item: WorkItem,
) -> bool:
    return (
        _is_coherent_work_activation(activation, work_item)
        and activation.claimed_by_run_id is None
        and activation.generation == work_item.ref.generation
    )


def _is_coherent_live_run(
    run: RunRecord,
    activation: Activation,
    work_item: WorkItem,
    observed_run_ids: set[str],
) -> bool:
    return (
        run.run_ref.run_id not in observed_run_ids
        and run.run_ref.work_item_id == run.work_item_id
        and run.work_item_id == work_item.ref.work_item_id
        and run.activation_id == activation.activation_id
        and run.run_ref.plan_ref == work_item.ref.plan_ref
        and run.run_ref.plan_ref == activation.plan_ref
        and run.run_ref.generation == work_item.ref.generation
        and activation.generation == run.run_ref.generation + 1
        and activation.claimed_by_run_id == run.run_ref.run_id
        and activation.work_item_id == run.work_item_id
        and activation.stage_kind_id == run.stage_kind_id
        and activation.runner_binding_id == run.runner_binding_id
        and activation.lineage_id == work_item.lineage_id
        and activation.queue_family_id == work_item.queue_family_id
    )


def _is_coherent_work_activation(
    activation: Activation,
    work_item: WorkItem,
) -> bool:
    return (
        activation.work_item_id == work_item.ref.work_item_id
        and activation.lineage_id == work_item.lineage_id
        and activation.plan_ref == work_item.ref.plan_ref
        and activation.queue_family_id == work_item.queue_family_id
    )


def _active_run_status(
    run: RunRecord,
    activation: Activation,
    work_item: WorkItem,
) -> ActiveRunStatus:
    return ActiveRunStatus(
        run_id=run.run_ref.run_id,
        work_item_id=work_item.ref.work_item_id,
        activation_id=activation.activation_id,
        lineage_id=work_item.lineage_id,
        queue_family_id=str(work_item.queue_family_id),
        graph_node_id=activation.graph_node_id,
        stage_kind_id=str(run.stage_kind_id),
        runner_binding_id=str(run.runner_binding_id),
        claim_id=run.run_ref.claim_id,
        generation=run.run_ref.generation,
        fencing_token=run.run_ref.fencing_token,
        plan_fingerprint=run.run_ref.plan_ref.authority_fingerprint,
    )


def _artifact_statuses(
    state: RuntimeState,
    selected_plan: SelectedCompiledPlan | None,
    selected_fingerprint: AuthorityFingerprint | None,
) -> tuple[ArtifactStatus, ...]:
    if selected_plan is None or selected_fingerprint is None:
        return ()
    workflow_id = str(selected_plan.workflow.workflow_id)
    records: list[ArtifactStatus] = []
    for artifact in sorted(
        state.artifacts.values(),
        key=lambda item: item.artifact_id,
    ):
        status = _artifact_status(
            state,
            selected_plan,
            selected_fingerprint,
            workflow_id,
            artifact,
        )
        if status is not None:
            records.append(status)
    return tuple(records)


def _artifact_status(
    state: RuntimeState,
    selected_plan: SelectedCompiledPlan,
    selected_fingerprint: AuthorityFingerprint,
    workflow_id: str,
    artifact: ArtifactRecord,
) -> ArtifactStatus | None:
    source = _source_context(
        state,
        run_id=artifact.source_run_id,
        work_item_id=artifact.work_item_id,
        selected_fingerprint=selected_fingerprint,
    )
    if source is None:
        return None
    action = _terminal_action_by_id(selected_plan, str(artifact.source_action_id))
    if action is None:
        return None
    observation = _runner_observation_for_artifact(state, artifact)
    if observation is None:
        return None
    observed_action = _observed_terminal_action(selected_plan, source, observation)
    if observed_action is None or observed_action.id != action.id:
        return None
    if (
        action.stage_kind_id != artifact.source_stage_kind_id
        or action.artifact_schema_id != artifact.schema_id
        or source.run.stage_kind_id != artifact.source_stage_kind_id
        or source.activation.graph_node_id != artifact.source_graph_node_id
        or source.run.runner_binding_id != source.stage.runner_binding_id
        or artifact.payload_digest != artifact_payload_digest(artifact.payload)
        or not _transition_matches_input(
            state,
            transition_id=artifact.transition_id,
            input_id=artifact.created_by_input_id,
            input_kind=RunnerResultObserved.input_kind,
            input_family="workflow_observation",
        )
    ):
        return None
    return ArtifactStatus(
        artifact_id=artifact.artifact_id,
        workflow_id=workflow_id,
        selected_plan_id=source.run.run_ref.plan_ref.plan_id,
        selected_plan_fingerprint=selected_fingerprint,
        work_item_id=artifact.work_item_id,
        queue_family_id=str(source.work_item.queue_family_id),
        lineage_id=source.work_item.lineage_id,
        schema_id=str(artifact.schema_id),
        payload=artifact.payload,
        payload_digest=artifact.payload_digest,
        source_run_id=artifact.source_run_id,
        source_activation_id=source.run.activation_id,
        source_action_id=str(artifact.source_action_id),
        terminal_action_id=str(action.id),
        source_input_id=artifact.created_by_input_id,
        source_stage_kind_id=str(artifact.source_stage_kind_id),
        source_graph_node_id=artifact.source_graph_node_id,
        source_runner_binding_id=str(source.run.runner_binding_id),
        latest_marker=_observed_marker(observation),
        transition_id=artifact.transition_id,
    )


def _effect_statuses(
    state: RuntimeState,
    selected_plan: SelectedCompiledPlan | None,
    selected_fingerprint: AuthorityFingerprint | None,
) -> tuple[EffectStatus, ...]:
    if selected_plan is None or selected_fingerprint is None:
        return ()
    workflow_id = str(selected_plan.workflow.workflow_id)
    records: list[EffectStatus] = []
    reconciliations_by_effect = _reconciliations_by_effect_id(
        state,
        selected_fingerprint,
    )
    for proposal in sorted(
        state.effect_proposals.values(),
        key=lambda item: item.effect_id,
    ):
        reconciliation = reconciliations_by_effect.get(proposal.effect_id)
        status = _effect_status(
            state,
            selected_plan,
            selected_fingerprint,
            workflow_id,
            proposal,
            reconciliation,
        )
        if status is not None:
            records.append(status)
    return tuple(records)


def _effect_status(
    state: RuntimeState,
    selected_plan: SelectedCompiledPlan,
    selected_fingerprint: AuthorityFingerprint,
    workflow_id: str,
    proposal: EffectProposalRecord,
    reconciliation: EffectReconciliationRecord | None,
) -> EffectStatus | None:
    if (
        proposal.selected_plan_fingerprint != selected_fingerprint
        or proposal.selected_plan_ref.authority_fingerprint != selected_fingerprint
    ):
        return None
    source = _source_context(
        state,
        run_id=proposal.source_run_id,
        work_item_id=proposal.source_work_item_id,
        selected_fingerprint=selected_fingerprint,
    )
    if source is None:
        return None
    artifact = state.artifacts.get(proposal.artifact_id)
    if artifact is None:
        return None
    declaration = next(
        (
            declaration
            for declaration in selected_plan.effect_declarations
            if declaration.effect_declaration_id == proposal.effect_declaration_id
        ),
        None,
    )
    action = _terminal_action_by_id(selected_plan, str(proposal.terminal_action_id))
    if declaration is None or action is None:
        return None
    observation = _runner_observation_for_artifact(state, artifact)
    if observation is None:
        return None
    observed_action = _observed_terminal_action(selected_plan, source, observation)
    if observed_action is None or observed_action.id != proposal.terminal_action_id:
        return None
    if (
        declaration.terminal_action_id != proposal.terminal_action_id
        or declaration.artifact_schema_id != proposal.artifact_schema_id
        or declaration.provider_ref != proposal.provider_ref
        or declaration.capability_policy_ref != proposal.capability_policy_ref
        or declaration.target_ref_kind != proposal.target_ref_kind
        or declaration.target_ref_schema != proposal.target_ref_schema
        or action.id != proposal.terminal_action_id
        or action.stage_kind_id != source.run.stage_kind_id
        or action.artifact_schema_id != proposal.artifact_schema_id
        or proposal.source_action_id != proposal.terminal_action_id
        or proposal.status != "pending"
        or proposal.dedupe_key
        != f"{proposal.effect_declaration_id}:{proposal.artifact_id}"
        or proposal.source_input_id != proposal.created_input_id
        or proposal.source_run_id != source.run.run_ref.run_id
        or proposal.source_activation_id != source.run.activation_id
        or proposal.source_graph_node_id != source.activation.graph_node_id
        or proposal.source_stage_kind_id != source.run.stage_kind_id
        or proposal.source_runner_binding_id != source.run.runner_binding_id
        or proposal.source_queue_family_id != source.activation.queue_family_id
        or proposal.lineage_id != source.work_item.lineage_id
        or proposal.artifact_schema_id != artifact.schema_id
        or proposal.artifact_payload_digest != artifact.payload_digest
        or artifact.source_run_id != proposal.source_run_id
        or artifact.source_action_id != proposal.source_action_id
        or artifact.created_by_input_id != proposal.source_input_id
        or artifact.transition_id != proposal.created_transition_id
        or not _effect_target_refs_match_artifact(proposal, artifact)
        or not _transition_matches_input(
            state,
            transition_id=proposal.created_transition_id,
            input_id=proposal.created_input_id,
            input_kind=RunnerResultObserved.input_kind,
            input_family="workflow_observation",
        )
    ):
        return None
    if reconciliation is not None and (
        reconciliation.selected_plan_fingerprint != selected_fingerprint
        or reconciliation.selected_plan_ref.authority_fingerprint
        != selected_fingerprint
        or reconciliation.selected_plan_ref != proposal.selected_plan_ref
        or reconciliation.provider_ref != proposal.provider_ref
        or reconciliation.status not in declaration.allowed_reconciliation_statuses
        or not _is_sha256_digest(reconciliation.fake_local_result_digest)
        or not _transition_matches_input(
            state,
            transition_id=reconciliation.created_transition_id,
            input_id=reconciliation.created_input_id,
            input_kind=ReconcileEffect.input_kind,
            input_family="workflow_kernel_command",
        )
    ):
        return None
    return EffectStatus(
        effect_id=proposal.effect_id,
        workflow_id=workflow_id,
        selected_plan_fingerprint=selected_fingerprint,
        status=reconciliation.status if reconciliation is not None else proposal.status,
        proposal_status=proposal.status,
        reconciliation_id=(
            reconciliation.reconciliation_id if reconciliation is not None else None
        ),
        reconciliation_status=(
            reconciliation.status if reconciliation is not None else None
        ),
        fake_local_result_digest=(
            reconciliation.fake_local_result_digest
            if reconciliation is not None
            else None
        ),
        effect_declaration_id=str(proposal.effect_declaration_id),
        dedupe_key=proposal.dedupe_key,
        provider_ref=proposal.provider_ref,
        capability_policy_ref=proposal.capability_policy_ref,
        target_ref_kind=proposal.target_ref_kind,
        target_ref_schema=proposal.target_ref_schema,
        target_skill_id=proposal.target_skill_id,
        target_path_ref=proposal.target_path_ref,
        terminal_action_id=str(proposal.terminal_action_id),
        source_action_id=str(proposal.source_action_id),
        source_input_id=proposal.source_input_id,
        source_run_id=proposal.source_run_id,
        source_work_item_id=proposal.source_work_item_id,
        source_activation_id=proposal.source_activation_id,
        source_graph_node_id=proposal.source_graph_node_id,
        source_stage_kind_id=str(proposal.source_stage_kind_id),
        source_runner_binding_id=str(proposal.source_runner_binding_id),
        source_queue_family_id=str(proposal.source_queue_family_id),
        lineage_id=proposal.lineage_id,
        artifact_id=proposal.artifact_id,
        artifact_schema_id=str(proposal.artifact_schema_id),
        artifact_payload_digest=proposal.artifact_payload_digest,
        created_input_id=proposal.created_input_id,
        created_transition_id=proposal.created_transition_id,
    )


def _generated_work_statuses(
    state: RuntimeState,
    selected_plan: SelectedCompiledPlan | None,
    selected_fingerprint: AuthorityFingerprint | None,
) -> tuple[GeneratedWorkStatus, ...]:
    if selected_plan is None or selected_fingerprint is None:
        return ()
    workflow_id = str(selected_plan.workflow.workflow_id)
    records: list[GeneratedWorkStatus] = []
    for fanout in sorted(
        state.fanout_records.values(),
        key=lambda item: item.record_id,
    ):
        status = _generated_work_status(
            state,
            selected_plan,
            selected_fingerprint,
            workflow_id,
            fanout,
        )
        if status is not None:
            records.append(status)
    return tuple(records)


def _generated_work_status(
    state: RuntimeState,
    selected_plan: SelectedCompiledPlan,
    selected_fingerprint: AuthorityFingerprint,
    workflow_id: str,
    fanout: FanoutRecord,
) -> GeneratedWorkStatus | None:
    if fanout.selected_plan_ref.authority_fingerprint != selected_fingerprint:
        return None
    declaration = _fanout_declaration_by_id(selected_plan, str(fanout.fanout_id))
    if declaration is None:
        return None
    route = _generated_work_route_by_id(selected_plan, declaration.target_route_id)
    if route is None:
        return None
    source_artifact = state.artifacts.get(fanout.source_artifact_id)
    source = _source_context(
        state,
        run_id=fanout.source_run_id,
        work_item_id=fanout.source_work_item_id,
        selected_fingerprint=selected_fingerprint,
    )
    target_work_item = state.work_items.get(fanout.target_work_item_id)
    target_activation = state.activations.get(fanout.target_activation_id)
    if (
        source_artifact is None
        or source is None
        or target_work_item is None
        or target_activation is None
    ):
        return None
    if (
        source_artifact.payload_digest != fanout.source_artifact_digest
        or source_artifact.source_run_id != fanout.source_run_id
        or source_artifact.source_action_id != fanout.source_action_id
        or declaration.source_action_id != fanout.source_action_id
        or declaration.target_queue_family_id != fanout.target_queue_family_id
        or declaration.target_stage_kind_id != fanout.target_stage_kind_id
        or declaration.target_graph_node_id != fanout.target_graph_node_id
        or route.queue_family_id != fanout.target_queue_family_id
        or route.stage_kind_id != fanout.target_stage_kind_id
        or route.graph_node_id != fanout.target_graph_node_id
        or target_work_item.queue_family_id != fanout.target_queue_family_id
        or target_work_item.ref.plan_ref.authority_fingerprint
        != selected_fingerprint
        or target_work_item.lineage_id != fanout.lineage_id
        or target_activation.work_item_id != fanout.target_work_item_id
        or target_activation.lineage_id != fanout.lineage_id
        or target_activation.queue_family_id != fanout.target_queue_family_id
        or target_activation.stage_kind_id != fanout.target_stage_kind_id
        or target_activation.graph_node_id != fanout.target_graph_node_id
        or target_activation.runner_binding_id != route.runner_binding_id
        or target_activation.plan_ref.authority_fingerprint != selected_fingerprint
    ):
        return None
    observation = _runner_observation_for_artifact(state, source_artifact)
    if observation is None:
        return None
    observed_action = _observed_terminal_action(selected_plan, source, observation)
    if observed_action is None or observed_action.id != fanout.source_action_id:
        return None
    return GeneratedWorkStatus(
        generated_work_id=fanout.record_id,
        workflow_id=workflow_id,
        selected_plan_fingerprint=selected_fingerprint,
        fanout_id=str(fanout.fanout_id),
        target_route_id=declaration.target_route_id,
        source_artifact_id=fanout.source_artifact_id,
        source_artifact_digest=fanout.source_artifact_digest,
        source_work_item_id=fanout.source_work_item_id,
        source_run_id=fanout.source_run_id,
        source_action_id=str(fanout.source_action_id),
        source_input_id=source_artifact.created_by_input_id,
        target_work_item_id=fanout.target_work_item_id,
        target_activation_id=fanout.target_activation_id,
        target_queue_family_id=str(fanout.target_queue_family_id),
        target_stage_kind_id=str(fanout.target_stage_kind_id),
        target_graph_node_id=fanout.target_graph_node_id,
        target_runner_binding_id=str(route.runner_binding_id),
        item_key=fanout.item_key,
        lineage_id=fanout.lineage_id,
        created_by_input_id=fanout.created_by_input_id,
    )


def _join_statuses(
    state: RuntimeState,
    selected_plan: SelectedCompiledPlan | None,
    selected_fingerprint: AuthorityFingerprint | None,
) -> tuple[JoinStatus, ...]:
    if selected_plan is None or selected_fingerprint is None:
        return ()
    generated_work = _generated_work_statuses(
        state,
        selected_plan,
        selected_fingerprint,
    )
    generated_by_source: dict[str, list[GeneratedWorkStatus]] = {}
    for generated in generated_work:
        generated_by_source.setdefault(generated.source_artifact_id, []).append(
            generated
        )

    records: list[JoinStatus] = []
    for join in selected_plan.join_declarations:
        for source_artifact_id, generated_rows in sorted(generated_by_source.items()):
            status = _join_status(
                state,
                selected_plan,
                selected_fingerprint,
                join,
                source_artifact_id,
                tuple(generated_rows),
            )
            if status is not None:
                records.append(status)
    return tuple(records)


def _join_status(
    state: RuntimeState,
    selected_plan: SelectedCompiledPlan,
    selected_fingerprint: AuthorityFingerprint,
    join: JoinDeclaration,
    source_artifact_id: str,
    generated_rows: tuple[GeneratedWorkStatus, ...],
) -> JoinStatus | None:
    source_artifact = state.artifacts.get(source_artifact_id)
    source_work_item_id = (
        generated_rows[0].source_work_item_id if generated_rows else ""
    )
    source_work_item = state.work_items.get(source_work_item_id)
    if source_artifact is None or source_work_item is None:
        return None
    if source_work_item.ref.plan_ref.authority_fingerprint != selected_fingerprint:
        return None
    target_work_item_ids = {
        generated.target_work_item_id for generated in generated_rows
    }
    required = tuple(str(schema) for schema in join.required_artifact_schema_ids)
    progress = join_evidence_progress_for_status(
        state,
        selected_plan=selected_plan,
        plan_fingerprint=selected_fingerprint,
        join=join,
        bundle_artifact_id=source_artifact.artifact_id,
    )
    if progress is not None:
        observed_schemas = frozenset(progress[0])
        ready = progress[1]
    else:
        observed_schemas = _join_observed_artifact_schemas(
            state,
            selected_plan,
            selected_fingerprint,
            join,
            source_artifact,
            target_work_item_ids,
        )
        ready = False
    observed = tuple(schema for schema in required if schema in observed_schemas)
    missing = tuple(schema for schema in required if schema not in observed_schemas)
    target_route = _join_target_route(selected_plan, join)
    return JoinStatus(
        join_id=str(join.id),
        selected_plan_fingerprint=selected_fingerprint,
        source_artifact_id=source_artifact.artifact_id,
        source_work_item_id=source_work_item.ref.work_item_id,
        lineage_id=source_work_item.lineage_id,
        source_artifact_schema_id=str(source_artifact.schema_id),
        correlation_key=join.correlation_key,
        correlation_value=source_artifact.payload.get(join.correlation_key),
        required_artifact_schema_ids=required,
        observed_artifact_schema_ids=observed,
        missing_artifact_schema_ids=missing,
        missing_policy=join.missing_policy,
        ready=ready,
        target_queue_family_id=(
            str(target_route.queue_family_id) if target_route is not None else None
        ),
        target_stage_kind_id=str(join.target_stage_kind_id),
        target_graph_node_id=(
            target_route.graph_node_id if target_route is not None else None
        ),
        target_runner_binding_id=(
            str(target_route.runner_binding_id) if target_route is not None else None
        ),
    )


def _join_observed_artifact_schemas(
    state: RuntimeState,
    selected_plan: SelectedCompiledPlan,
    selected_fingerprint: AuthorityFingerprint,
    join: JoinDeclaration,
    source_artifact: ArtifactRecord,
    target_work_item_ids: set[str],
) -> frozenset[str]:
    correlation_value = source_artifact.payload.get(join.correlation_key)
    if not isinstance(correlation_value, str) or not correlation_value:
        return frozenset()

    required = {str(schema) for schema in join.required_artifact_schema_ids}
    workflow_id = str(selected_plan.workflow.workflow_id)
    observed: set[str] = set()
    invalid: set[str] = set()
    for artifact in state.artifacts.values():
        schema_id = str(artifact.schema_id)
        if (
            schema_id not in required
            or artifact.work_item_id not in target_work_item_ids
        ):
            continue
        if (
            schema_id in observed
            or artifact.payload.get(join.correlation_key) != correlation_value
        ):
            observed.discard(schema_id)
            invalid.add(schema_id)
            continue
        if _artifact_status(
            state,
            selected_plan,
            selected_fingerprint,
            workflow_id,
            artifact,
        ) is None:
            invalid.add(schema_id)
            continue
        observed.add(schema_id)
    return frozenset(observed - invalid)


def _join_target_route(
    selected_plan: SelectedCompiledPlan,
    join: JoinDeclaration,
) -> GeneratedWorkRouteDeclaration | None:
    routes = tuple(
        route
        for route in selected_plan.generated_work_routes
        if route.stage_kind_id == join.target_stage_kind_id
    )
    return routes[0] if len(routes) == 1 else None


def _source_context(
    state: RuntimeState,
    *,
    run_id: str,
    work_item_id: str,
    selected_fingerprint: AuthorityFingerprint,
) -> _SourceContext | None:
    run = state.runs.get(run_id)
    work_item = state.work_items.get(work_item_id)
    if run is None or work_item is None:
        return None
    activation = state.activations.get(run.activation_id)
    if activation is None:
        return None
    admitted = state.admitted_plans.get(run.run_ref.plan_ref.authority_fingerprint)
    if admitted is None:
        return None
    stage = next(
        (
            stage
            for stage in admitted.selected_plan.stage_kinds
            if stage.id == run.stage_kind_id
        ),
        None,
    )
    if stage is None:
        return None
    if (
        run.run_ref.run_id != run_id
        or run.work_item_id != work_item_id
        or run.run_ref.work_item_id != work_item_id
        or run.run_ref.plan_ref.authority_fingerprint != selected_fingerprint
        or work_item.ref.plan_ref != run.run_ref.plan_ref
        or activation.plan_ref != run.run_ref.plan_ref
        or activation.work_item_id != work_item_id
        or activation.activation_id != run.activation_id
        or activation.stage_kind_id != run.stage_kind_id
        or activation.runner_binding_id != run.runner_binding_id
        or activation.queue_family_id != work_item.queue_family_id
        or activation.lineage_id != work_item.lineage_id
        or stage.runner_binding_id != run.runner_binding_id
    ):
        return None
    return _SourceContext(
        run=run,
        activation=activation,
        work_item=work_item,
        stage=stage,
    )


def _terminal_action_by_id(
    selected_plan: SelectedCompiledPlan,
    action_id: str,
) -> TerminalActionDeclaration | None:
    return next(
        (
            action
            for action in selected_plan.terminal_actions
            if str(action.id) == action_id
        ),
        None,
    )


def _terminal_marker(
    selected_plan: SelectedCompiledPlan,
    action: TerminalActionDeclaration,
) -> str | None:
    outcome = next(
        (
            outcome
            for outcome in selected_plan.terminal_outcomes
            if outcome.id == action.outcome_id
        ),
        None,
    )
    return outcome.marker if outcome is not None else None


def _runner_observation_for_artifact(
    state: RuntimeState,
    artifact: ArtifactRecord,
) -> RunnerObservationRecord | None:
    return next(
        (
            observation
            for observation in state.runner_observations.values()
            if observation.run_id == artifact.source_run_id
            and observation.created_by_input_id == artifact.created_by_input_id
        ),
        None,
    )


def _observed_terminal_action(
    selected_plan: SelectedCompiledPlan,
    source: _SourceContext,
    observation: RunnerObservationRecord,
) -> TerminalActionDeclaration | None:
    action_id = observation.payload.get("action_id")
    if isinstance(action_id, str):
        return next(
            (
                action
                for action in selected_plan.terminal_actions
                if str(action.id) == action_id
                and action.stage_kind_id == source.run.stage_kind_id
            ),
            None,
        )
    marker = _observed_marker(observation)
    if marker is None:
        return None
    outcome = next(
        (
            outcome
            for outcome in selected_plan.terminal_outcomes
            if outcome.marker == marker
            and outcome.stage_kind_id == source.run.stage_kind_id
        ),
        None,
    )
    if outcome is None:
        return None
    return next(
        (
            action
            for action in selected_plan.terminal_actions
            if action.outcome_id == outcome.id
            and action.stage_kind_id == source.run.stage_kind_id
        ),
        None,
    )


def _observed_marker(observation: RunnerObservationRecord | None) -> str | None:
    if observation is None:
        return None
    marker = observation.payload.get("marker")
    return marker if isinstance(marker, str) else None


def _effect_target_refs_match_artifact(
    proposal: EffectProposalRecord,
    artifact: ArtifactRecord,
) -> bool:
    return (
        proposal.target_skill_id
        == _optional_authority_text(artifact.payload, "target_skill_id")
        and proposal.target_path_ref
        == _optional_authority_text(artifact.payload, "installed_path")
    )


def _optional_authority_text(
    payload: Mapping[str, AuthorityValue],
    key: str,
) -> str | None:
    value = payload.get(key)
    return value if isinstance(value, str) and value else None


def _is_sha256_digest(value: object) -> bool:
    if not isinstance(value, str):
        return False
    prefix = "sha256:"
    if not value.startswith(prefix):
        return False
    digest = value.removeprefix(prefix)
    return len(digest) == 64 and all(
        character in "0123456789abcdef" for character in digest
    )


def _transition_matches_input(
    state: RuntimeState,
    *,
    transition_id: str,
    input_id: str,
    input_kind: str,
    input_family: str,
) -> bool:
    transition = next(
        (
            transition
            for transition in state.transitions
            if transition.record_id == transition_id
        ),
        None,
    )
    return (
        transition is not None
        and transition.input_id == input_id
        and transition.accepted
        and transition.input_kind == input_kind
        and transition.input_family == input_family
    )


def _reconciliations_by_effect_id(
    state: RuntimeState,
    selected_fingerprint: AuthorityFingerprint,
) -> Mapping[str, EffectReconciliationRecord]:
    records: dict[str, EffectReconciliationRecord] = {}
    duplicate_effect_ids: set[str] = set()
    for reconciliation in state.effect_reconciliations.values():
        if reconciliation.selected_plan_fingerprint != selected_fingerprint:
            continue
        if reconciliation.effect_id in records:
            duplicate_effect_ids.add(reconciliation.effect_id)
            continue
        records[reconciliation.effect_id] = reconciliation
    for effect_id in duplicate_effect_ids:
        records.pop(effect_id, None)
    return records


def _fanout_declaration_by_id(
    selected_plan: SelectedCompiledPlan,
    fanout_id: str,
) -> FanoutDeclaration | None:
    return next(
        (
            declaration
            for declaration in selected_plan.fanout_declarations
            if str(declaration.id) == fanout_id
        ),
        None,
    )


def _generated_work_route_by_id(
    selected_plan: SelectedCompiledPlan,
    route_id: str,
) -> GeneratedWorkRouteDeclaration | None:
    return next(
        (
            route
            for route in selected_plan.generated_work_routes
            if route.id == route_id
        ),
        None,
    )


def _pause_status(state: RuntimeState) -> PauseStatus:
    if state.pause is None:
        return PauseStatus(is_paused=False)
    return PauseStatus(
        is_paused=True,
        record_id=state.pause.record_id,
        source_run_id=state.pause.source_run_id,
        work_item_id=state.pause.work_item_id,
        action_id=str(state.pause.action_id),
        created_by_input_id=state.pause.created_by_input_id,
    )


def _quarantine_statuses(state: RuntimeState) -> tuple[QuarantineStatus, ...]:
    records: list[QuarantineStatus] = []
    for work_item_id, quarantine in sorted(state.quarantines.items()):
        work_item = state.work_items.get(work_item_id)
        records.append(
            QuarantineStatus(
                record_id=quarantine.record_id,
                work_item_id=quarantine.work_item_id,
                source_run_id=quarantine.source_run_id,
                action_id=str(quarantine.action_id),
                created_by_input_id=quarantine.created_by_input_id,
                queue_family_id=(
                    str(work_item.queue_family_id) if work_item is not None else None
                ),
            )
        )
    for lineage_quarantine in sorted(
        state.lineage_quarantines.values(),
        key=lambda record: record.quarantine_id,
    ):
        if lineage_quarantine.status != "active":
            continue
        work_item = state.work_items.get(
            lineage_quarantine.original_source_work_item_id
        )
        records.append(
            QuarantineStatus(
                record_id=lineage_quarantine.quarantine_id,
                work_item_id=lineage_quarantine.original_source_work_item_id,
                source_run_id=lineage_quarantine.original_source_run_id,
                action_id=str(lineage_quarantine.action_id),
                created_by_input_id=lineage_quarantine.created_input_id,
                queue_family_id=(
                    str(work_item.queue_family_id) if work_item is not None else None
                ),
                quarantine_kind="lineage",
                lineage_id=lineage_quarantine.lineage_id,
                policy_id=str(lineage_quarantine.policy_id),
                selected_plan_fingerprint=(
                    lineage_quarantine.selected_plan_fingerprint
                ),
                recovery_attempt_record_id=(
                    lineage_quarantine.recovery_attempt_record_id
                ),
                original_source_run_id=lineage_quarantine.original_source_run_id,
                original_source_work_item_id=(
                    lineage_quarantine.original_source_work_item_id
                ),
                original_source_activation_id=(
                    lineage_quarantine.original_source_activation_id
                ),
                emitting_recovery_activation_id=(
                    lineage_quarantine.emitting_recovery_activation_id
                ),
                emitting_recovery_run_id=(
                    lineage_quarantine.emitting_recovery_run_id
                ),
                attempt_count=lineage_quarantine.attempt_count,
                actor_kind=lineage_quarantine.actor_kind,
                status=lineage_quarantine.status,
                superseded_input_id=lineage_quarantine.superseded_input_id,
            )
        )
    return tuple(records)


def _active_lineage_quarantine_ids(
    state: RuntimeState,
    selected_fingerprint: AuthorityFingerprint | None,
) -> frozenset[str]:
    if selected_fingerprint is None:
        return frozenset()
    return frozenset(
        quarantine.lineage_id
        for quarantine in state.lineage_quarantines.values()
        if quarantine.status == "active"
        and quarantine.selected_plan_fingerprint == selected_fingerprint
    )


def _active_operator_wait_lineage_ids(
    state: RuntimeState,
    selected_fingerprint: AuthorityFingerprint | None,
) -> frozenset[str]:
    if selected_fingerprint is None:
        return frozenset()
    return frozenset(
        wait.lineage_id
        for wait in state.operator_waits.values()
        if wait.status == "active"
        and wait.selected_plan_fingerprint == selected_fingerprint
    )


def _recovery_attempt_statuses(
    state: RuntimeState,
    selected_fingerprint: AuthorityFingerprint | None,
) -> tuple[RecoveryAttemptStatus, ...]:
    if selected_fingerprint is None:
        return ()
    return tuple(
        RecoveryAttemptStatus(
            record_id=attempt.record_id,
            policy_id=str(attempt.policy_id),
            lineage_id=attempt.lineage_id,
            plan_fingerprint=attempt.plan_ref.authority_fingerprint,
            attempt_count=attempt.attempt_count,
            phase=attempt.phase,
            source_run_id=attempt.source_run_id,
            source_work_item_id=attempt.source_work_item_id,
            source_activation_id=attempt.source_activation_id,
            source_graph_node_id=attempt.source_graph_node_id,
            source_stage_kind_id=str(attempt.source_stage_kind_id),
            source_runner_binding_id=str(attempt.source_runner_binding_id),
            source_queue_family_id=str(attempt.source_queue_family_id),
            recovery_action_id=str(attempt.recovery_action_id),
            latest_recovery_activation_id=attempt.latest_recovery_activation_id,
            latest_recovery_run_id=attempt.latest_recovery_run_id,
            latest_return_action_id=(
                str(attempt.latest_return_action_id)
                if attempt.latest_return_action_id is not None
                else None
            ),
            created_by_input_id=attempt.created_by_input_id,
            updated_by_input_id=attempt.updated_by_input_id,
        )
        for attempt in sorted(
            state.recovery_attempts.values(),
            key=lambda item: (
                item.plan_ref.authority_fingerprint,
                str(item.policy_id),
                item.lineage_id,
            ),
        )
        if attempt.plan_ref.authority_fingerprint == selected_fingerprint
    )


def _cooldown_wait_statuses(
    state: RuntimeState,
    selected_fingerprint: AuthorityFingerprint | None,
) -> tuple[CooldownWaitStatus, ...]:
    if selected_fingerprint is None:
        return ()
    return tuple(
        CooldownWaitStatus(
            wait_id=wait.wait_id,
            policy_id=str(wait.policy_id),
            lineage_id=wait.lineage_id,
            recovery_attempt_record_id=wait.recovery_attempt_record_id,
            plan_fingerprint=wait.plan_ref.authority_fingerprint,
            attempt_count=wait.attempt_count,
            source_run_id=wait.source_run_id,
            source_work_item_id=wait.source_work_item_id,
            source_activation_id=wait.source_activation_id,
            recovery_action_id=str(wait.recovery_action_id),
            target_stage_kind_id=str(wait.target_stage_kind_id),
            target_graph_node_id=wait.target_graph_node_id,
            target_runner_binding_id=str(wait.target_runner_binding_id),
            created_input_id=wait.created_input_id,
            created_at=wait.created_at,
            due_at=wait.due_at,
            consumed_input_id=wait.consumed_input_id,
            consumed_at=wait.consumed_at,
            resulting_recovery_activation_id=wait.resulting_recovery_activation_id,
        )
        for wait in sorted(
            state.cooldown_waits.values(),
            key=lambda item: (
                item.plan_ref.authority_fingerprint,
                str(item.policy_id),
                item.lineage_id,
                item.created_at,
                item.wait_id,
            ),
        )
        if wait.plan_ref.authority_fingerprint == selected_fingerprint
    )


def _operator_intervention_statuses(
    state: RuntimeState,
    selected_fingerprint: AuthorityFingerprint | None,
) -> tuple[OperatorInterventionStatus, ...]:
    return tuple(
        OperatorInterventionStatus(
            record_id=intervention.record_id,
            created_by_input_id=intervention.created_by_input_id,
            input_payload_digest=intervention.input_payload_digest,
            option_id=intervention.option_id,
            kind=intervention.kind,
            result=intervention.result,
            policy_id=str(intervention.policy_id),
            lineage_id=intervention.lineage_id,
            quarantine_id=intervention.quarantine_id,
            recovery_attempt_record_id=intervention.recovery_attempt_record_id,
            recovery_attempt_count=intervention.recovery_attempt_count,
            attempt_effect=intervention.attempt_effect,
            selected_plan_fingerprint=intervention.selected_plan_fingerprint,
            actor_kind=intervention.actor_kind,
            actor_id=intervention.actor_id,
            reason=intervention.reason,
            target_work_item_id=intervention.target_work_item_id,
            target_activation_id=intervention.target_activation_id,
            closed_work_item_ids=intervention.closed_work_item_ids,
            closed_activation_ids=intervention.closed_activation_ids,
            closed_run_ids=intervention.closed_run_ids,
            payload_digest=intervention.payload_digest,
            payload_reference=intervention.payload_reference,
        )
        for intervention in sorted(
            state.operator_interventions.values(),
            key=lambda item: item.record_id,
        )
        if selected_fingerprint is None
        or intervention.selected_plan_fingerprint == selected_fingerprint
    )


def _operator_wait_statuses(
    state: RuntimeState,
    selected_plan: SelectedCompiledPlan | None,
    selected_fingerprint: AuthorityFingerprint | None,
) -> tuple[OperatorWaitStatus, ...]:
    if selected_plan is None or selected_fingerprint is None:
        return ()
    return tuple(
        _operator_wait_status(wait, selected_plan)
        for wait in sorted(
            state.operator_waits.values(),
            key=lambda item: (
                item.selected_plan_fingerprint,
                item.lineage_id,
                item.wait_id,
            ),
        )
        if wait.selected_plan_fingerprint == selected_fingerprint
    )


def _operator_wait_status(
    wait: OperatorWaitRecord,
    selected_plan: SelectedCompiledPlan,
) -> OperatorWaitStatus:
    declaration = next(
        (
            candidate
            for candidate in selected_plan.operator_waits
            if candidate.id == wait.operator_wait_id
        ),
        None,
    )
    return OperatorWaitStatus(
        wait_id=wait.wait_id,
        operator_wait_id=str(wait.operator_wait_id),
        source_action_id=str(wait.source_action_id),
        lineage_id=wait.lineage_id,
        selected_plan_fingerprint=wait.selected_plan_fingerprint,
        source_work_item_id=wait.source_work_item_id,
        source_activation_id=wait.source_activation_id,
        source_run_id=wait.source_run_id,
        source_stage_kind_id=str(wait.source_stage_kind_id),
        source_graph_node_id=wait.source_graph_node_id,
        source_queue_family_id=str(wait.source_queue_family_id),
        source_runner_binding_id=str(wait.source_runner_binding_id),
        source_artifact_id=wait.source_artifact_id,
        status=wait.status,
        created_input_id=wait.created_input_id,
        resolved_input_id=wait.resolved_input_id,
        actor_id=wait.actor_id,
        actor_kind=wait.actor_kind,
        resolution_kind=wait.resolution_kind,
        target_work_item_id=wait.target_work_item_id,
        target_activation_id=wait.target_activation_id,
        closed_work_item_ids=wait.closed_work_item_ids,
        payload_digest=wait.payload_digest,
        payload_reference=wait.payload_reference,
        allowed_resolution_kinds=(
            tuple(str(kind) for kind in declaration.allowed_resolution_kinds)
            if declaration is not None
            else ()
        ),
        actor_kind_requirement=(
            declaration.actor_kind if declaration is not None else None
        ),
        audit_metadata_requirements=(
            tuple(declaration.audit_metadata_requirements)
            if declaration is not None
            else ()
        ),
        payload_schema_id=(
            str(declaration.payload_schema_id)
            if declaration is not None
            and declaration.payload_schema_id is not None
            else None
        ),
        target_queue_family_id=(
            str(declaration.target_queue_family_id)
            if declaration is not None
            and declaration.target_queue_family_id is not None
            else None
        ),
        target_stage_kind_id=(
            str(declaration.target_stage_kind_id)
            if declaration is not None
            and declaration.target_stage_kind_id is not None
            else None
        ),
        target_graph_node_id=(
            declaration.target_graph_node_id if declaration is not None else None
        ),
        target_runner_binding_id=(
            str(declaration.target_runner_binding_id)
            if declaration is not None
            and declaration.target_runner_binding_id is not None
            else None
        ),
        status_effect=declaration.status_effect if declaration is not None else None,
    )


def _counter_statuses(
    state: RuntimeState,
    selected_fingerprint: AuthorityFingerprint | None,
) -> tuple[CounterStatus, ...]:
    if selected_fingerprint is None:
        return ()
    return tuple(
        CounterStatus(
            record_id=counter.record_id,
            counter_id=str(counter.counter_id),
            lineage_id=counter.lineage_id,
            plan_fingerprint=counter.selected_plan_ref.authority_fingerprint,
            value=counter.value,
            updated_by_input_id=counter.updated_by_input_id,
        )
        for counter in sorted(
            state.counters.values(),
            key=lambda item: (
                item.selected_plan_ref.authority_fingerprint,
                str(item.counter_id),
                item.lineage_id,
            ),
        )
        if counter.selected_plan_ref.authority_fingerprint == selected_fingerprint
    )


def _closure_target_statuses(
    state: RuntimeState,
    selected_fingerprint: AuthorityFingerprint | None,
) -> tuple[ClosureTargetStatus, ...]:
    if selected_fingerprint is None:
        return ()
    return tuple(
        _closure_target_status(state, target)
        for target in sorted(
            state.closure_targets.values(),
            key=lambda item: (
                item.selected_plan_ref.authority_fingerprint,
                item.lineage_id,
                item.closure_target_id,
            ),
        )
        if target.selected_plan_ref.authority_fingerprint == selected_fingerprint
    )


def _closure_target_status(
    state: RuntimeState,
    target: ClosureTargetRecord,
) -> ClosureTargetStatus:
    active_evaluator = _active_closure_evaluation_record(
        state,
        target.closure_target_id,
    )
    latest_terminal = _latest_record_for_closure_target(
        state.closure_terminal_records,
        target.closure_target_id,
    )
    latest_remediation = _latest_record_for_closure_target(
        state.remediation_work_records,
        target.closure_target_id,
    )
    latest_block = _latest_record_for_closure_target(
        state.closure_blocked_records,
        target.closure_target_id,
    )
    return ClosureTargetStatus(
        closure_target_id=target.closure_target_id,
        completion_behavior_id=str(target.completion_behavior_id),
        status=target.status,
        lineage_id=target.lineage_id,
        root_source_kind=target.root_source_kind,
        root_source_id=target.root_source_id,
        closure_root_work_item_id=target.closure_root_work_item_id,
        request_kind=target.request_kind,
        target_graph_node_id=target.target_graph_node_id,
        evidence_window=target.evidence_window,
        selected_plan_fingerprint=target.selected_plan_ref.authority_fingerprint,
        opened_by_input_id=target.opened_by_input_id,
        closed_by_record_id=target.closed_by_record_id,
        active_evaluator_record_id=(
            active_evaluator.record_id if active_evaluator is not None else None
        ),
        active_evaluator_run_id=(
            _run_id_for_activation(state, active_evaluator.target_activation_id)
            if active_evaluator is not None
            else None
        ),
        latest_terminal_record_id=(
            latest_terminal.record_id if latest_terminal is not None else None
        ),
        latest_terminal_kind=(
            latest_terminal.terminal_kind if latest_terminal is not None else None
        ),
        latest_terminal_artifact_id=(
            latest_terminal.source_artifact_id
            if latest_terminal is not None
            else None
        ),
        latest_remediation_record_id=(
            latest_remediation.record_id if latest_remediation is not None else None
        ),
        latest_remediation_work_item_id=(
            latest_remediation.target_work_item_id
            if latest_remediation is not None
            else None
        ),
        latest_blocked_record_id=(
            latest_block.record_id if latest_block is not None else None
        ),
        operator_required=(
            latest_block.operator_required if latest_block is not None else False
        ),
    )


def _closure_evaluation_statuses(
    state: RuntimeState,
    selected_fingerprint: AuthorityFingerprint | None,
) -> tuple[ClosureEvaluationStatus, ...]:
    if selected_fingerprint is None:
        return ()
    return tuple(
        _closure_evaluation_status(state, record)
        for record in sorted(
            state.closure_evaluations.values(),
            key=lambda item: (
                item.selected_plan_ref.authority_fingerprint,
                item.lineage_id,
                item.record_id,
            ),
        )
        if record.selected_plan_ref.authority_fingerprint == selected_fingerprint
    )


def _closure_evaluation_status(
    state: RuntimeState,
    record: ClosureEvaluationRecord,
) -> ClosureEvaluationStatus:
    activation = state.activations.get(record.target_activation_id)
    work_item = state.work_items.get(record.target_work_item_id)
    run_id = _run_id_for_activation(state, record.target_activation_id)
    closed = record.target_work_item_id in state.closed_work_items
    return ClosureEvaluationStatus(
        record_id=record.record_id,
        closure_target_id=record.closure_target_id,
        completion_behavior_id=str(record.completion_behavior_id),
        request_kind=record.request_kind,
        target_work_item_id=record.target_work_item_id,
        target_activation_id=record.target_activation_id,
        target_run_id=run_id,
        lineage_id=record.lineage_id,
        selected_plan_fingerprint=record.selected_plan_ref.authority_fingerprint,
        queue_family_id=(
            str(work_item.queue_family_id)
            if work_item is not None
            else (
                str(activation.queue_family_id) if activation is not None else None
            )
        ),
        graph_node_id=activation.graph_node_id if activation is not None else None,
        stage_kind_id=(
            str(activation.stage_kind_id) if activation is not None else None
        ),
        runner_binding_id=(
            str(activation.runner_binding_id) if activation is not None else None
        ),
        status=_closure_evaluation_lifecycle_status(
            activation=activation,
            run_id=run_id,
            closed=closed,
        ),
    )


def _closure_remediation_statuses(
    state: RuntimeState,
    selected_fingerprint: AuthorityFingerprint | None,
) -> tuple[ClosureRemediationStatus, ...]:
    if selected_fingerprint is None:
        return ()
    return tuple(
        _closure_remediation_status(state, record)
        for record in sorted(
            state.remediation_work_records.values(),
            key=lambda item: (
                item.selected_plan_ref.authority_fingerprint,
                item.lineage_id,
                item.record_id,
            ),
        )
        if record.selected_plan_ref.authority_fingerprint == selected_fingerprint
    )


def _closure_remediation_status(
    state: RuntimeState,
    record: RemediationWorkRecord,
) -> ClosureRemediationStatus:
    work_item = state.work_items.get(record.target_work_item_id)
    activation = state.activations.get(record.target_activation_id)
    return ClosureRemediationStatus(
        record_id=record.record_id,
        remediation_policy_id=str(record.remediation_policy_id),
        closure_target_id=record.closure_target_id,
        source_run_id=record.source_run_id,
        source_action_id=str(record.source_action_id),
        source_artifact_id=record.source_artifact_id,
        target_work_item_id=record.target_work_item_id,
        target_activation_id=record.target_activation_id,
        lineage_id=record.lineage_id,
        selected_plan_fingerprint=record.selected_plan_ref.authority_fingerprint,
        dedupe_key=record.dedupe_key,
        target_queue_family_id=(
            str(work_item.queue_family_id)
            if work_item is not None
            else (
                str(activation.queue_family_id) if activation is not None else None
            )
        ),
        target_graph_node_id=(
            activation.graph_node_id if activation is not None else None
        ),
        target_stage_kind_id=(
            str(activation.stage_kind_id) if activation is not None else None
        ),
        target_runner_binding_id=(
            str(activation.runner_binding_id) if activation is not None else None
        ),
    )


def _closure_block_statuses(
    state: RuntimeState,
    selected_fingerprint: AuthorityFingerprint | None,
) -> tuple[ClosureBlockedStatus, ...]:
    if selected_fingerprint is None:
        return ()
    return tuple(
        ClosureBlockedStatus(
            record_id=record.record_id,
            closure_target_id=record.closure_target_id,
            completion_behavior_id=str(record.completion_behavior_id),
            source_run_id=record.source_run_id,
            source_action_id=str(record.source_action_id),
            lineage_id=record.lineage_id,
            selected_plan_fingerprint=record.selected_plan_ref.authority_fingerprint,
            operator_required=record.operator_required,
        )
        for record in sorted(
            state.closure_blocked_records.values(),
            key=lambda item: (
                item.selected_plan_ref.authority_fingerprint,
                item.lineage_id,
                item.record_id,
            ),
        )
        if record.selected_plan_ref.authority_fingerprint == selected_fingerprint
    )


def _active_closure_evaluation_record(
    state: RuntimeState,
    closure_target_id: str,
) -> ClosureEvaluationRecord | None:
    return next(
        (
            record
            for record in state.closure_evaluations.values()
            if record.closure_target_id == closure_target_id
            and record.target_work_item_id in state.work_items
            and record.target_work_item_id not in state.closed_work_items
            and record.target_activation_id in state.activations
        ),
        None,
    )


def _latest_record_for_closure_target(
    records: Mapping[str, _ClosureLatestT],
    closure_target_id: str,
) -> _ClosureLatestT | None:
    return next(
        (
            record
            for record in sorted(
                records.values(),
                key=lambda item: item.record_id,
                reverse=True,
            )
            if record.closure_target_id == closure_target_id
        ),
        None,
    )


def _run_id_for_activation(
    state: RuntimeState,
    activation_id: str,
) -> str | None:
    return next(
        (
            run.run_ref.run_id
            for run in state.runs.values()
            if run.activation_id == activation_id
        ),
        None,
    )


def _closure_evaluation_lifecycle_status(
    *,
    activation: Activation | None,
    run_id: str | None,
    closed: bool,
) -> str:
    if closed:
        return "closed"
    if run_id is not None:
        return "running"
    if activation is None:
        return "unknown"
    if activation.claimed_by_run_id is None:
        return "ready"
    return "active"


def _recent_event_statuses(
    state: RuntimeState,
    max_events: int,
) -> tuple[RecentEventStatus, ...]:
    records: list[RecentEventStatus] = []
    for event, trace in zip_longest(state.governance_events, state.traces):
        if event is not None:
            records.append(_recent_event_status(event, source="governance_event"))
        if trace is not None:
            records.append(_recent_event_status(trace, source="trace"))
    return tuple(records[-max_events:] if max_events else ())


def _recent_event_status(
    record: GovernanceEventRecord | TraceRecord,
    *,
    source: str,
) -> RecentEventStatus:
    return RecentEventStatus(
        record_id=record.record_id,
        input_id=record.input_id,
        input_kind=record.input_kind,
        input_family=record.input_family,
        disposition=record.disposition,
        plan_fingerprint=record.plan_fingerprint,
        work_item_id=record.work_item_id,
        run_id=record.run_id,
        action_id=str(record.action_id) if record.action_id is not None else None,
        authority_source=record.authority_source,
        refusal_reason=record.refusal_reason,
        source=source,
    )


def _is_valid_authority_fingerprint(value: object) -> bool:
    if not isinstance(value, str):
        return False
    prefix = "sha256:"
    if not value.startswith(prefix):
        return False
    digest = value.removeprefix(prefix)
    return len(digest) == 64 and all(
        character in "0123456789abcdef" for character in digest
    )


def _presentation_text(
    presentation: Mapping[str, AuthorityValue],
    key: str,
) -> str | None:
    value = presentation.get(key)
    return value if isinstance(value, str) else None


__all__ = (
    "ActiveRunStatus",
    "ArtifactStatus",
    "ClosureBlockedStatus",
    "ClosureEvaluationStatus",
    "ClosureRemediationStatus",
    "ClosureTargetStatus",
    "CounterStatus",
    "DispatchSuspensionProjection",
    "EffectStatus",
    "GeneratedWorkStatus",
    "JoinStatus",
    "KnownPlanStatus",
    "OperatorInterventionStatus",
    "OperatorWaitStatus",
    "OperatorStatus",
    "PartitionStatus",
    "PauseStatus",
    "QuarantineStatus",
    "QueueFamilyStatus",
    "QueueClosureProjection",
    "QueueClosureStatus",
    "RecentEventStatus",
    "RecoveryAttemptStatus",
    "SelectedPlanStatus",
    "StageKindStatus",
    "daemon_budget_projection",
    "operator_status",
    "queue_closure_projection",
)
