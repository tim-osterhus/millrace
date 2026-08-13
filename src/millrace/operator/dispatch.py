"""Read-only local-operator dispatch projections."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from hashlib import sha256

from millrace.contracts.compiled_plan import (
    AuthorityValue,
    ExternalEnqueueRouteDeclaration,
    JoinDeclaration,
    SelectedCompiledPlan,
    StageKindDeclaration,
    context_binding_authority_refusal,
)
from millrace.contracts.fingerprints import AuthorityFingerprint
from millrace.contracts.ids import QueueFamilyId, RunnerBindingId, StageKindId
from millrace.contracts.runner import RunnerDispatchEnvelope
from millrace.contracts.state import Activation, RunRecord, RuntimeState, WorkItem
from millrace.contracts.transition import ClaimWork, TransitionContext
from millrace.kernel import decide
from millrace.kernel.fanout_policy import (
    PolicyAssessment,
    SelectedFanoutSourceProjection,
    project_selected_fanout_source_for_target,
)
from millrace.kernel.join_policy import (
    SelectedJoinEvidenceProjection,
    project_join_evidence_progress,
    project_selected_join_evidence_for_target,
)
from millrace.kernel.operator_waits import (
    SelectedWaitEvidenceProjection,
    project_selected_wait_evidence_for_target,
)

READY_DIAGNOSTIC_SEVERITIES = (
    "non_candidate",
    "policy_refusal",
    "corrupt_authority",
)


def join_evidence_progress_for_status(
    state: RuntimeState,
    *,
    selected_plan: SelectedCompiledPlan,
    plan_fingerprint: AuthorityFingerprint,
    join: JoinDeclaration,
    bundle_artifact_id: str,
) -> tuple[tuple[str, ...], bool] | None:
    """Return selected-slot join progress without exposing kernel policy records."""
    projection = project_join_evidence_progress(
        state,
        selected_plan=selected_plan,
        plan_fingerprint=plan_fingerprint,
        join=join,
        bundle_artifact_id=bundle_artifact_id,
    )
    if isinstance(projection, PolicyAssessment):
        return None
    return projection.observed_schema_ids, projection.ready

_POLICY_REFUSALS = {
    "dispatch_suspended",
    "workspace_paused",
    "lineage_quarantined",
    "operator_wait_active",
    "dependency_not_ready",
    "concurrency_policy_blocked",
    "capability_denied",
    "capability_approval_pending",
    "capability_unsupported",
}
_CORRUPT_REFUSALS = {
    "missing_work_item",
    "unknown_plan_ref",
    "missing_stage_kind",
    "missing_runner_binding",
    "missing_selected_asset",
    "missing_capability",
    "missing_runner_invoke_capability",
    "unsupported_selected_authority",
}
_NON_CANDIDATE_REFUSALS = {
    "stale_activation",
    "work_item_closed",
}
_UNSUPPORTED_CAPABILITY_DETAIL_PREFIXES = (
    "capability_kind:",
    "capability_support_status:",
    "capability_grant_status:",
    "capability_approval_policy:",
)
_ENTRYPOINT_ASSET_KINDS = frozenset(("prompt", "entrypoint_prompt"))
_SKILL_ASSET_KINDS = frozenset(("skill", "stage_skill"))


@dataclass(frozen=True, slots=True)
class DispatchProjectionError(ValueError):
    reason: str
    message: str
    details: Mapping[str, object] = field(default_factory=dict)

    def __str__(self) -> str:
        return self.reason


@dataclass(frozen=True, slots=True)
class ReadyDispatchCandidate:
    activation_id: str
    work_item_id: str
    lineage_id: str | None
    queue_family_id: str
    graph_node_id: str
    stage_kind_id: str
    runner_binding_id: str
    plan_fingerprint: str
    generation: int
    work_item_created_by_input_id: str
    activation_created_by_input_id: str
    external_enqueue_route_id: str | None


@dataclass(frozen=True, slots=True)
class ReadyDispatchDiagnostic:
    activation_id: str | None
    work_item_id: str | None
    reason_code: str
    severity: str
    plan_fingerprint: str | None
    message: str
    detail: str | None = None

    def __post_init__(self) -> None:
        if self.severity not in READY_DIAGNOSTIC_SEVERITIES:
            raise ValueError(f"unsupported ready diagnostic severity: {self.severity}")


@dataclass(frozen=True, slots=True)
class ReadyDispatchProjection:
    candidates: tuple[ReadyDispatchCandidate, ...]
    diagnostics: tuple[ReadyDispatchDiagnostic, ...]


@dataclass(frozen=True, slots=True)
class DispatchSuspensionProjection:
    is_suspended: bool
    suspension_id: str | None = None
    status: str | None = None
    plan_fingerprint: str | None = None
    generation: int = 0
    dispatch_generation: int = 0
    actor_id: str | None = None
    reason: str | None = None
    suspended_by_input_id: str | None = None
    resumed_by_input_id: str | None = None
    resume_actor_id: str | None = None
    resume_reason: str | None = None
    accepted_may_start_count: int = 0
    accepted_run_ids: tuple[str, ...] = ()
    accepted_activation_ids: tuple[str, ...] = ()
    accepted_work_item_ids: tuple[str, ...] = ()
    omitted_identity_count: int = 0


def dispatch_suspension_projection(
    state: RuntimeState,
    *,
    max_identities: int = 20,
) -> DispatchSuspensionProjection:
    if type(max_identities) is not int or max_identities < 0:
        raise ValueError("max_identities must be a non-negative integer")
    record = state.dispatch_suspension
    if record is None:
        return DispatchSuspensionProjection(is_suspended=False)
    may_start = tuple(
        run
        for run in sorted(
            state.runs.values(),
            key=lambda candidate: candidate.run_ref.run_id,
        )
        if run_may_start_while_dispatch_suspended(state, run)
    )
    retained = may_start[:max_identities]
    return DispatchSuspensionProjection(
        is_suspended=record.status == "active",
        suspension_id=record.suspension_id,
        status=record.status,
        plan_fingerprint=record.selected_plan_ref.authority_fingerprint,
        generation=record.generation,
        dispatch_generation=record.dispatch_generation,
        actor_id=record.actor_id,
        reason=record.reason,
        suspended_by_input_id=record.suspended_by_input_id,
        resumed_by_input_id=record.resumed_by_input_id,
        resume_actor_id=record.resume_actor_id,
        resume_reason=record.resume_reason,
        accepted_may_start_count=len(may_start),
        accepted_run_ids=tuple(run.run_ref.run_id for run in retained),
        accepted_activation_ids=tuple(run.activation_id for run in retained),
        accepted_work_item_ids=tuple(run.work_item_id for run in retained),
        omitted_identity_count=len(may_start) - len(retained),
    )


def run_may_start_while_dispatch_suspended(
    state: RuntimeState,
    run: RunRecord,
) -> bool:
    """Return whether an already accepted run may initiate under suspension."""
    record = state.dispatch_suspension
    if record is None or record.status != "active":
        return False
    if run.current_session_id is None:
        return True
    session = state.runner_sessions.get(run.current_session_id)
    return session is not None and session.state == "created"


def build_dispatch_envelope_for_run(
    *,
    state: RuntimeState,
    run_id: str,
) -> RunnerDispatchEnvelope:
    run = state.runs.get(run_id)
    if run is None:
        raise _dispatch_error("unknown_run", run_id=run_id)
    if _run_has_observation(state, run_id):
        raise _dispatch_error("run_observed", run_id=run_id)
    if run.work_item_id in state.closed_work_items:
        raise _dispatch_error(
            "work_item_closed",
            run_id=run_id,
            work_item_id=run.work_item_id,
        )
    if run.current_session_id is None:
        raise _dispatch_error("missing_runner_session", run_id=run_id)
    session = state.runner_sessions.get(run.current_session_id)
    if session is None or session.run_id != run_id:
        raise _dispatch_error("runner_session_authority_mismatch", run_id=run_id)

    activation = state.activations.get(run.activation_id)
    if activation is None:
        raise _dispatch_error(
            "missing_activation",
            run_id=run_id,
            activation_id=run.activation_id,
        )
    work_item = state.work_items.get(run.work_item_id)
    if work_item is None:
        raise _dispatch_error(
            "missing_work_item",
            run_id=run_id,
            work_item_id=run.work_item_id,
        )
    admitted = state.admitted_plans.get(run.run_ref.plan_ref.authority_fingerprint)
    if admitted is None:
        raise _dispatch_error(
            "missing_admitted_plan",
            run_id=run_id,
            plan_fingerprint=run.run_ref.plan_ref.authority_fingerprint,
        )
    selected_plan = admitted.selected_plan
    if (
        admitted.plan_ref != run.run_ref.plan_ref
        or run.run_ref.plan_ref != activation.plan_ref
        or run.run_ref.plan_ref != work_item.ref.plan_ref
    ):
        raise _dispatch_error("plan_ref_mismatch", run_id=run_id)
    if (
        run.run_ref.work_item_id != run.work_item_id
        or run.run_ref.work_item_id != work_item.ref.work_item_id
        or activation.activation_id != run.activation_id
        or activation.work_item_id != work_item.ref.work_item_id
        or activation.claimed_by_run_id != run.run_ref.run_id
        or run.run_ref.generation != work_item.ref.generation
        or activation.generation != run.run_ref.generation + 1
        or activation.lineage_id != work_item.lineage_id
        or activation.queue_family_id != work_item.queue_family_id
        or activation.stage_kind_id != run.stage_kind_id
        or activation.runner_binding_id != run.runner_binding_id
    ):
        raise _dispatch_error("run_activation_drift", run_id=run_id)

    graph_id = _graph_id_for_activation(selected_plan, activation)
    stage = _stage_for_run(selected_plan, run)
    if stage is None:
        raise _dispatch_error(
            "missing_stage_kind",
            run_id=run_id,
            stage_kind_id=str(run.stage_kind_id),
        )
    if stage.runner_binding_id != run.runner_binding_id:
        raise _dispatch_error("graph_stage_runner_drift", run_id=run_id)
    if (
        activation.queue_family_id,
        activation.stage_kind_id,
        activation.graph_node_id,
        activation.runner_binding_id,
    ) not in _selected_route_targets(selected_plan):
        raise _dispatch_error("graph_stage_runner_drift", run_id=run_id)

    context_checkout = _context_checkout_for_dispatch(
        selected_plan=selected_plan,
        run=run,
        session=session,
    )

    assets_by_id = {asset.id: asset for asset in selected_plan.assets}
    if any(asset_id not in assets_by_id for asset_id in stage.asset_ids):
        raise _dispatch_error("missing_selected_asset", run_id=run_id)
    artifact_schema_ids = {schema.id for schema in selected_plan.artifact_schemas}
    if any(
        schema_id not in artifact_schema_ids
        for schema_id in stage.artifact_schema_ids
    ):
        raise _dispatch_error("missing_selected_schema", run_id=run_id)

    stage_assets = tuple(assets_by_id[asset_id] for asset_id in stage.asset_ids)
    external_route = _external_route_for_dispatch(
        selected_plan=selected_plan,
        activation=activation,
        work_item=work_item,
    )
    governance_context = _governance_context_for_dispatch(
        state=state,
        run=run,
        activation=activation,
        work_item=work_item,
        stage=stage,
    )
    selected_join_evidence = _selected_join_evidence_for_dispatch(
        state=state,
        selected_plan=selected_plan,
        run=run,
        activation=activation,
        work_item=work_item,
    )
    selected_wait_evidence = _selected_wait_evidence_for_dispatch(
        state=state,
        selected_plan=selected_plan,
        run=run,
        activation=activation,
        work_item=work_item,
    )
    return RunnerDispatchEnvelope(
        run_id=run.run_ref.run_id,
        session_id=session.session_id,
        dispatch_generation=session.dispatch_generation,
        session_fencing_token=session.session_fencing_token,
        work_item_id=work_item.ref.work_item_id,
        activation_id=activation.activation_id,
        plan_fingerprint=run.run_ref.plan_ref.authority_fingerprint,
        plan_id=run.run_ref.plan_ref.plan_id,
        workflow_id=str(selected_plan.workflow.workflow_id),
        workflow_version=str(selected_plan.workflow.workflow_version),
        graph_id=graph_id,
        claim_id=run.run_ref.claim_id,
        generation=run.run_ref.generation,
        fencing_token=run.run_ref.fencing_token,
        queue_family_id=str(work_item.queue_family_id),
        stage_kind_id=str(run.stage_kind_id),
        graph_node_id=activation.graph_node_id,
        runner_binding_id=str(activation.runner_binding_id),
        external_enqueue_route_id=(
            external_route.id if external_route is not None else None
        ),
        entrypoint_asset_id=next(
            (
                str(asset.id)
                for asset in stage_assets
                if asset.asset_kind in _ENTRYPOINT_ASSET_KINDS
            ),
            None,
        ),
        skill_asset_ids=tuple(
            str(asset.id)
            for asset in stage_assets
            if asset.asset_kind in _SKILL_ASSET_KINDS
        ),
        artifact_schema_ids=tuple(
            str(schema_id) for schema_id in stage.artifact_schema_ids
        ),
        work_item_payload=work_item.payload,
        governance_context=governance_context,
        terminal_options=_terminal_options_for_dispatch(
            state=state,
            run=run,
            work_item=work_item,
            stage=stage,
        ),
        selected_join_evidence=selected_join_evidence,
        selected_wait_evidence=selected_wait_evidence,
        context_checkout=context_checkout,
    )


def _context_checkout_for_dispatch(
    *,
    selected_plan: SelectedCompiledPlan,
    run: RunRecord,
    session: object,
) -> Mapping[str, AuthorityValue] | None:
    try:
        refusal = context_binding_authority_refusal(selected_plan)
    except Exception as exc:
        raise _dispatch_error(
            "context_binding_authority_mismatch",
            run_id=run.run_ref.run_id,
        ) from exc
    if refusal is not None:
        raise _dispatch_error(
            "context_binding_authority_mismatch",
            run_id=run.run_ref.run_id,
            detail=refusal,
        )
    bindings = tuple(
        binding
        for binding in selected_plan.context_bindings
        if binding.stage_kind_id == run.stage_kind_id
    )
    if not bindings:
        return None
    if len(bindings) != 1:
        raise _dispatch_error(
            "context_binding_authority_mismatch",
            run_id=run.run_ref.run_id,
        )
    manifest_digest = getattr(session, "context_manifest_digest", None)
    if not isinstance(manifest_digest, str) or not _is_sha256_digest(manifest_digest):
        raise _dispatch_error(
            "missing_context_manifest",
            run_id=run.run_ref.run_id,
        )
    binding = bindings[0]
    binding_id = getattr(binding, "id", None)
    router_asset_id = getattr(binding, "router_asset_id", None)
    checkout_root = getattr(binding, "checkout_root", None)
    session_id = getattr(session, "session_id", None)
    dispatch_generation = getattr(session, "dispatch_generation", None)
    if (
        not isinstance(binding_id, str)
        or not binding_id.strip()
        or router_asset_id is None
        or not isinstance(session_id, str)
        or type(dispatch_generation) is not int
        or dispatch_generation < 1
        or not isinstance(checkout_root, str)
    ):
        raise _dispatch_error(
            "context_binding_authority_mismatch",
            run_id=run.run_ref.run_id,
        )
    session_id = _safe_dispatch_component(
        session_id,
        run_id=run.run_ref.run_id,
    )
    router_asset_id_text = str(router_asset_id)
    if not router_asset_id_text.strip():
        raise _dispatch_error(
            "context_binding_authority_mismatch",
            run_id=run.run_ref.run_id,
        )
    checkout_relative_path = _safe_dispatch_relative_path(
        f"{checkout_root}/{session_id}/{dispatch_generation}",
        run_id=run.run_ref.run_id,
    )
    router_relative_path = _safe_dispatch_relative_path(
        f"{checkout_relative_path}/CONTEXT.md",
        run_id=run.run_ref.run_id,
    )
    return {
        "manifest_digest": manifest_digest,
        "binding_id": binding_id,
        "router_asset_id": router_asset_id_text,
        "checkout_relative_path": checkout_relative_path,
        "router_relative_path": router_relative_path,
    }


def _safe_dispatch_relative_path(value: str, *, run_id: str) -> str:
    parts = value.split("/")
    if (
        not value
        or value.startswith("/")
        or "\\" in value
        or ":" in parts[0]
        or any(part in {"", ".", "..", ".millrace"} for part in parts)
    ):
        raise _dispatch_error(
            "context_binding_authority_mismatch",
            run_id=run_id,
        )
    return value


def _safe_dispatch_component(value: str, *, run_id: str) -> str:
    if (
        not value.strip()
        or value in {".", ".."}
        or "/" in value
        or "\\" in value
        or "\x00" in value
    ):
        raise _dispatch_error(
            "context_binding_authority_mismatch",
            run_id=run_id,
        )
    return value


def _is_sha256_digest(value: str) -> bool:
    return (
        len(value) == 71
        and value.startswith("sha256:")
        and all(character in "0123456789abcdef" for character in value[7:])
    )


def list_ready_dispatch_candidates(state: RuntimeState) -> ReadyDispatchProjection:
    candidates: list[ReadyDispatchCandidate] = []
    diagnostics: list[ReadyDispatchDiagnostic] = []
    for activation in sorted(state.activations.values(), key=_activation_sort_key):
        candidate, diagnostic = _ready_projection_for_activation(state, activation)
        if candidate is not None:
            candidates.append(candidate)
        if diagnostic is not None:
            diagnostics.append(diagnostic)
    return ReadyDispatchProjection(
        candidates=tuple(candidates),
        diagnostics=tuple(diagnostics),
    )


def _ready_projection_for_activation(
    state: RuntimeState,
    activation: Activation,
) -> tuple[ReadyDispatchCandidate | None, ReadyDispatchDiagnostic | None]:
    plan_fingerprint = activation.plan_ref.authority_fingerprint
    work_item = state.work_items.get(activation.work_item_id)
    if work_item is None:
        return None, _diagnostic(
            activation=activation,
            work_item=None,
            reason_code="missing_work_item",
            severity="corrupt_authority",
            message="Activation references a missing work item.",
        )
    if activation.plan_ref != work_item.ref.plan_ref:
        return None, _diagnostic(
            activation=activation,
            work_item=work_item,
            reason_code="plan_ref_mismatch",
            severity="corrupt_authority",
            message="Activation and work item plan refs do not match.",
        )
    if activation.claimed_by_run_id is not None:
        return None, _diagnostic(
            activation=activation,
            work_item=work_item,
            reason_code="already_claimed",
            severity="non_candidate",
            message="Activation is already claimed.",
        )
    if activation.generation != work_item.ref.generation:
        return None, _diagnostic(
            activation=activation,
            work_item=work_item,
            reason_code="stale_generation",
            severity="corrupt_authority",
            message="Activation generation does not match work item generation.",
        )
    admitted = state.admitted_plans.get(plan_fingerprint)
    if admitted is None or admitted.plan_ref != activation.plan_ref:
        return None, _diagnostic(
            activation=activation,
            work_item=work_item,
            reason_code="missing_admitted_plan",
            severity="corrupt_authority",
            message="Activation plan authority is not admitted.",
        )
    schema_diagnostic = _missing_schema_diagnostic(
        admitted.selected_plan,
        activation,
        work_item,
    )
    if schema_diagnostic is not None:
        return None, schema_diagnostic
    if work_item.ref.work_item_id in state.closed_work_items:
        return None, _diagnostic(
            activation=activation,
            work_item=work_item,
            reason_code="work_item_closed",
            severity="non_candidate",
            message="Work item is closed.",
        )
    transition_input = ClaimWork(
        _candidate_check_input_id(state, activation.activation_id),
        activation_id=activation.activation_id,
    )
    decision = decide(
        state,
        transition_input,
        _candidate_check_context(transition_input.input_id),
    )
    if decision.accepted:
        return _candidate_for_activation(
            admitted.selected_plan,
            activation,
            work_item,
        ), None
    reason = (
        "transition_refused"
        if decision.refusal is None
        else decision.refusal.reason
    )
    detail = None if decision.refusal is None else decision.refusal.detail
    return None, _diagnostic_for_refusal(
        activation=activation,
        work_item=work_item,
        reason=reason,
        detail=detail,
    )


def _candidate_for_activation(
    selected_plan: SelectedCompiledPlan,
    activation: Activation,
    work_item: WorkItem,
) -> ReadyDispatchCandidate:
    external_route = _external_route_for_dispatch(
        selected_plan=selected_plan,
        activation=activation,
        work_item=work_item,
    )
    return ReadyDispatchCandidate(
        activation_id=activation.activation_id,
        work_item_id=work_item.ref.work_item_id,
        lineage_id=work_item.lineage_id,
        queue_family_id=str(work_item.queue_family_id),
        graph_node_id=activation.graph_node_id,
        stage_kind_id=str(activation.stage_kind_id),
        runner_binding_id=str(activation.runner_binding_id),
        plan_fingerprint=activation.plan_ref.authority_fingerprint,
        generation=activation.generation,
        work_item_created_by_input_id=work_item.created_by_input_id,
        activation_created_by_input_id=activation.created_by_input_id,
        external_enqueue_route_id=(
            external_route.id if external_route is not None else None
        ),
    )


def _diagnostic_for_refusal(
    *,
    activation: Activation,
    work_item: WorkItem,
    reason: str,
    detail: str | None,
) -> ReadyDispatchDiagnostic:
    reason_code = reason
    severity = "policy_refusal" if reason in _POLICY_REFUSALS else "non_candidate"
    if reason in _CORRUPT_REFUSALS:
        severity = "corrupt_authority"
    if reason in _NON_CANDIDATE_REFUSALS:
        severity = "non_candidate"
    if reason == "unknown_plan_ref":
        reason_code = "missing_admitted_plan"
        severity = "corrupt_authority"
    elif reason == "stale_activation":
        reason_code = "already_claimed"
    elif reason == "unsupported_selected_authority":
        severity = "corrupt_authority"
        if detail is not None:
            if detail.startswith("activation_graph_node_missing:"):
                reason_code = "graph_node_missing"
            elif detail.startswith("activation_queue_family:"):
                reason_code = "queue_family_drift"
            elif detail.startswith("activation_route_target:"):
                reason_code = "graph_stage_runner_drift"
            elif detail.startswith(_UNSUPPORTED_CAPABILITY_DETAIL_PREFIXES):
                reason_code = "unsupported_selected_authority"
    return _diagnostic(
        activation=activation,
        work_item=work_item,
        reason_code=reason_code,
        severity=severity,
        message=f"Activation is not a ready dispatch candidate: {reason_code}.",
        detail=detail,
    )


def ready_diagnostic_from_claim_refusal(
    *,
    activation: Activation,
    work_item: WorkItem,
    reason: str,
    detail: str | None,
) -> ReadyDispatchDiagnostic:
    """Map a real claim refusal into the dispatch-owned ready diagnostic shape."""

    return _diagnostic_for_refusal(
        activation=activation,
        work_item=work_item,
        reason=reason,
        detail=detail,
    )


def _missing_schema_diagnostic(
    selected_plan: SelectedCompiledPlan,
    activation: Activation,
    work_item: WorkItem,
) -> ReadyDispatchDiagnostic | None:
    stage = _stage_for_activation(selected_plan, activation)
    if stage is None:
        return None
    schema_ids = {schema.id for schema in selected_plan.artifact_schemas}
    if all(schema_id in schema_ids for schema_id in stage.artifact_schema_ids):
        return None
    return _diagnostic(
        activation=activation,
        work_item=work_item,
        reason_code="missing_selected_schema",
        severity="corrupt_authority",
        message="Stage references a missing selected artifact schema.",
    )


def _diagnostic(
    *,
    activation: Activation,
    work_item: WorkItem | None,
    reason_code: str,
    severity: str,
    message: str,
    detail: str | None = None,
) -> ReadyDispatchDiagnostic:
    return ReadyDispatchDiagnostic(
        activation_id=activation.activation_id,
        work_item_id=None if work_item is None else work_item.ref.work_item_id,
        reason_code=reason_code,
        severity=severity,
        plan_fingerprint=activation.plan_ref.authority_fingerprint,
        message=message,
        detail=detail,
    )


def _candidate_check_input_id(state: RuntimeState, activation_id: str) -> str:
    base = f"read-only-dispatch-check:{activation_id}"
    input_id = base
    suffix = 0
    while input_id in state.receipts:
        suffix += 1
        input_id = f"{base}:{suffix}"
    return input_id


def _candidate_check_context(input_id: str) -> TransitionContext:
    prefix = f"cli-dispatch-check:{_stable_suffix(input_id)}"
    return TransitionContext(
        transition_id=f"{prefix}:transition",
        work_item_id=f"{prefix}:work",
        activation_id=f"{prefix}:activation",
        run_id=f"{prefix}:run",
        claim_id=f"{prefix}:claim",
        fencing_token=f"{prefix}:fence",
    )


def _stable_suffix(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()[:32]


def _activation_sort_key(activation: Activation) -> tuple[str, str, int, str]:
    return (
        activation.plan_ref.authority_fingerprint,
        str(activation.queue_family_id),
        activation.generation,
        activation.activation_id,
    )


def _dispatch_error(reason: str, **details: object) -> DispatchProjectionError:
    return DispatchProjectionError(
        reason=reason,
        message=f"Dispatch projection refused: {reason}.",
        details=details,
    )


def _run_has_observation(state: RuntimeState, run_id: str) -> bool:
    return any(
        observation.run_id == run_id
        for observation in state.runner_observations.values()
    )


def _graph_id_for_activation(
    selected_plan: SelectedCompiledPlan,
    activation: Activation,
) -> str:
    for graph in selected_plan.graphs:
        if activation.graph_node_id in graph.node_ids:
            return str(graph.id)
    raise _dispatch_error(
        "graph_node_missing",
        activation_id=activation.activation_id,
        graph_node_id=activation.graph_node_id,
    )


def _stage_for_run(
    selected_plan: SelectedCompiledPlan,
    run: RunRecord,
) -> StageKindDeclaration | None:
    return next(
        (
            candidate
            for candidate in selected_plan.stage_kinds
            if candidate.id == run.stage_kind_id
        ),
        None,
    )


def _stage_for_activation(
    selected_plan: SelectedCompiledPlan,
    activation: Activation,
) -> StageKindDeclaration | None:
    return next(
        (
            candidate
            for candidate in selected_plan.stage_kinds
            if candidate.id == activation.stage_kind_id
        ),
        None,
    )


def _external_route_for_dispatch(
    *,
    selected_plan: SelectedCompiledPlan,
    activation: Activation,
    work_item: WorkItem,
) -> ExternalEnqueueRouteDeclaration | None:
    return next(
        (
            route
            for route in selected_plan.external_enqueue_routes
            if route.queue_family_id == work_item.queue_family_id
            and route.graph_node_id == activation.graph_node_id
            and route.stage_kind_id == activation.stage_kind_id
            and route.runner_binding_id == activation.runner_binding_id
        ),
        None,
    )


def _selected_route_targets(
    selected_plan: SelectedCompiledPlan,
) -> frozenset[tuple[QueueFamilyId, StageKindId, str, RunnerBindingId]]:
    targets: set[tuple[QueueFamilyId, StageKindId, str, RunnerBindingId]] = set()
    stage_by_id = {stage.id: stage for stage in selected_plan.stage_kinds}
    action_by_id = {action.id: action for action in selected_plan.terminal_actions}
    for route in selected_plan.external_enqueue_routes:
        targets.add(
            (
                route.queue_family_id,
                route.stage_kind_id,
                route.graph_node_id,
                route.runner_binding_id,
            )
        )
    for generated_route in selected_plan.generated_work_routes:
        targets.add(
            (
                generated_route.queue_family_id,
                generated_route.stage_kind_id,
                generated_route.graph_node_id,
                generated_route.runner_binding_id,
            )
        )
    targets.update(
        (
            behavior.request_queue_family_id,
            behavior.target_stage_kind_id,
            behavior.target_graph_node_id,
            behavior.runner_binding_id,
        )
        for behavior in selected_plan.completion_behaviors
    )
    targets.update(
        (
            policy.target_queue_family_id,
            policy.target_stage_kind_id,
            policy.target_graph_node_id,
            policy.target_runner_binding_id,
        )
        for policy in selected_plan.remediation_policies
    )
    for policy in selected_plan.recovery_policies:
        for action_id in policy.source_recovery_action_ids:
            action = action_by_id.get(action_id)
            if (
                action is None
                or action.action_kind != "recovery_route"
                or action.target_stage_kind_id != policy.recovery_stage_kind_id
                or action.target_graph_node_id is None
                or action.runner_binding_id is None
            ):
                continue
            source_stage = stage_by_id.get(action.stage_kind_id)
            if source_stage is None:
                continue
            targets.update(
                (
                    queue_family_id,
                    policy.recovery_stage_kind_id,
                    action.target_graph_node_id,
                    action.runner_binding_id,
                )
                for queue_family_id in source_stage.input_queue_family_ids
            )
    for action in selected_plan.terminal_actions:
        if (
            action.emitted_queue_family_id is not None
            and action.target_stage_kind_id is not None
            and action.target_graph_node_id is not None
            and action.runner_binding_id is not None
        ):
            targets.add(
                (
                    action.emitted_queue_family_id,
                    action.target_stage_kind_id,
                    action.target_graph_node_id,
                    action.runner_binding_id,
                )
            )
    return frozenset(targets)


def _governance_context_for_dispatch(
    *,
    state: RuntimeState,
    run: RunRecord,
    activation: Activation,
    work_item: WorkItem,
    stage: StageKindDeclaration,
) -> Mapping[str, AuthorityValue]:
    admitted = state.admitted_plans.get(run.run_ref.plan_ref.authority_fingerprint)
    if admitted is None:
        return {}
    selected_plan = admitted.selected_plan
    context: dict[str, AuthorityValue] = {}
    runner_binding = next(
        (
            candidate
            for candidate in selected_plan.runner_bindings
            if candidate.id == run.runner_binding_id
        ),
        None,
    )
    if runner_binding is not None and runner_binding.required_capability_ids:
        assets_by_id = {asset.id: asset for asset in selected_plan.assets}
        artifact_schemas_by_id = {
            schema.id: schema for schema in selected_plan.artifact_schemas
        }
        capabilities_by_id = {
            capability.id: capability for capability in selected_plan.capabilities
        }
        context.update(
            {
                "workflow": {
                    "id": str(selected_plan.workflow.workflow_id),
                    "version": str(selected_plan.workflow.workflow_version),
                    "name": selected_plan.workflow.workflow_name,
                },
                "queue_family_id": str(work_item.queue_family_id),
                "graph_node_id": activation.graph_node_id,
                "stage_kind_id": str(run.stage_kind_id),
                "runner_binding_id": str(run.runner_binding_id),
                "stage_assets": tuple(
                    {
                        "id": str(asset.id),
                        "kind": asset.asset_kind,
                        "display_name": _authority_display_name(asset.presentation),
                    }
                    for asset in (
                        assets_by_id[asset_id]
                        for asset_id in stage.asset_ids
                        if asset_id in assets_by_id
                    )
                ),
                "artifact_schema_ids": tuple(
                    str(schema_id) for schema_id in stage.artifact_schema_ids
                ),
                "artifact_schemas": tuple(
                    {
                        "id": str(schema.id),
                        "display_name": _authority_display_name(schema.presentation),
                    }
                    for schema in (
                        artifact_schemas_by_id[schema_id]
                        for schema_id in stage.artifact_schema_ids
                        if schema_id in artifact_schemas_by_id
                    )
                ),
                "capabilities": tuple(
                    {
                        "id": str(capability.id),
                        "kind": capability.capability_kind,
                        "support_status": capability.support_status,
                        "grant_status": capability.grant_status,
                        "approval_policy_id": capability.approval_policy_id,
                    }
                    for capability in (
                        capabilities_by_id[capability_id]
                        for capability_id in runner_binding.required_capability_ids
                        if capability_id in capabilities_by_id
                    )
                ),
            }
        )
        external_route = _external_route_for_dispatch(
            selected_plan=selected_plan,
            activation=activation,
            work_item=work_item,
        )
        if external_route is not None:
            context["external_enqueue_route_id"] = external_route.id
        downstream = _downstream_graph_node_ids(
            selected_plan=selected_plan,
            current_stage_kind_id=run.stage_kind_id,
        )
        if downstream:
            context["downstream_graph_node_ids"] = downstream
    generated_work_source = _generated_work_source_for_dispatch(
        state=state,
        selected_plan=selected_plan,
        run=run,
        activation=activation,
        work_item=work_item,
    )
    if generated_work_source is not None:
        context["generated_work_source"] = generated_work_source
    counters = _counter_contexts_for_dispatch(
        state=state,
        selected_plan=selected_plan,
        run=run,
        work_item=work_item,
    )
    if counters:
        context["counters"] = counters
    return context


def _generated_work_source_for_dispatch(
    *,
    state: RuntimeState,
    selected_plan: SelectedCompiledPlan,
    run: RunRecord,
    activation: Activation,
    work_item: WorkItem,
) -> Mapping[str, AuthorityValue] | None:
    projection = project_selected_fanout_source_for_target(
        state,
        selected_plan=selected_plan,
        run=run,
        activation=activation,
        work_item=work_item,
    )
    if isinstance(projection, PolicyAssessment):
        raise _dispatch_error(
            projection.reason_code or "fanout_partial_state",
            detail=projection.detail,
            source_artifact_id=projection.source_artifact_id,
            run_id=run.run_ref.run_id,
        )
    if projection is None:
        return None
    return _generated_work_source_payload(projection)


def _generated_work_source_payload(
    projection: SelectedFanoutSourceProjection,
) -> Mapping[str, AuthorityValue]:
    return {
        "fanout_record_id": projection.fanout_record_id,
        "fanout_id": projection.fanout_id,
        "source_work_item_id": projection.source_work_item_id,
        "source_run_id": projection.source_run_id,
        "source_action_id": projection.source_action_id,
        "source_artifact_id": projection.source_artifact_id,
        "source_artifact_digest": projection.source_artifact_digest,
        "created_by_input_id": projection.created_by_input_id,
        "item_key": projection.item_key,
    }


def _selected_join_evidence_for_dispatch(
    *,
    state: RuntimeState,
    selected_plan: SelectedCompiledPlan,
    run: RunRecord,
    activation: Activation,
    work_item: WorkItem,
) -> Mapping[str, AuthorityValue] | None:
    projection = project_selected_join_evidence_for_target(
        state,
        selected_plan=selected_plan,
        run=run,
        activation=activation,
        work_item=work_item,
    )
    if isinstance(projection, PolicyAssessment):
        raise _dispatch_error(
            projection.reason_code or "join_partial_state",
            detail=projection.detail,
            run_id=run.run_ref.run_id,
        )
    if projection is None:
        return None
    return _selected_join_evidence_payload(projection)


def _selected_join_evidence_payload(
    projection: SelectedJoinEvidenceProjection,
) -> Mapping[str, AuthorityValue]:
    return {
        "record_kind": "selected_join_evidence",
        "schema_version": 1,
        "join_id": projection.join_id,
        "correlation_key": projection.correlation_key,
        "correlation_value": projection.correlation_value,
        "correlation_identity": projection.correlation_identity,
        "lineage_id": projection.lineage_id,
        "bundle_artifact_id": projection.bundle_artifact_id,
        "bundle_artifact_schema_id": projection.bundle_artifact_schema_id,
        "bundle_artifact_digest": projection.bundle_artifact_digest,
        "required_artifact_schema_ids": projection.required_artifact_schema_ids,
        "evidence_artifacts": tuple(
            {
                "artifact_id": artifact.artifact_id,
                "artifact_schema_id": artifact.schema_id,
                "payload_digest": artifact.artifact_digest,
                "payload": artifact.payload,
                "source_action_id": artifact.source_action_id,
                "source_run_id": artifact.source_run_id,
                "source_work_item_id": artifact.source_work_item_id,
                "fanout_id": artifact.fanout_id,
                "fanout_record_id": artifact.fanout_record_id,
                "item_key": artifact.item_key,
            }
            for artifact in projection.evidence_artifacts
        ),
    }


def _selected_wait_evidence_for_dispatch(
    *,
    state: RuntimeState,
    selected_plan: SelectedCompiledPlan,
    run: RunRecord,
    activation: Activation,
    work_item: WorkItem,
) -> Mapping[str, AuthorityValue] | None:
    projection = project_selected_wait_evidence_for_target(
        state,
        selected_plan=selected_plan,
        run=run,
        activation=activation,
        work_item=work_item,
    )
    if isinstance(projection, PolicyAssessment):
        raise _dispatch_error(
            projection.reason_code or "operator_wait_evidence_refused",
            detail=projection.detail,
            run_id=run.run_ref.run_id,
        )
    if projection is None:
        return None
    return _selected_wait_evidence_payload(projection)


def _selected_wait_evidence_payload(
    projection: SelectedWaitEvidenceProjection,
) -> Mapping[str, AuthorityValue]:
    return {
        "record_kind": "selected_wait_evidence",
        "schema_version": 1,
        "wait_id": projection.wait_id,
        "operator_wait_id": projection.operator_wait_id,
        "lineage_id": projection.lineage_id,
        "source_artifact_id": projection.source_artifact_id,
        "source_artifact_schema_id": projection.source_artifact_schema_id,
        "source_artifact_digest": projection.source_artifact_digest,
        "source_artifact_payload": projection.source_artifact_payload,
        "source_action_id": projection.source_action_id,
        "source_run_id": projection.source_run_id,
        "source_work_item_id": projection.source_work_item_id,
    }


def _counter_contexts_for_dispatch(
    *,
    state: RuntimeState,
    selected_plan: SelectedCompiledPlan,
    run: RunRecord,
    work_item: WorkItem,
) -> Mapping[str, AuthorityValue]:
    if work_item.lineage_id is None:
        return {}
    counters: dict[str, AuthorityValue] = {}
    for counter in selected_plan.counters:
        if counter.stage_kind_id != run.stage_kind_id:
            continue
        current_value = next(
            (
                record.value
                for record in state.counters.values()
                if record.selected_plan_ref == run.run_ref.plan_ref
                and record.counter_id == counter.id
                and record.lineage_id == work_item.lineage_id
            ),
            0,
        )
        next_increment_reaches_threshold = (
            current_value + 1 >= counter.threshold_count
        )
        derives_recovery = _counter_increment_derives_recovery_threshold(
            plan=selected_plan,
            increment_action_id=str(counter.increment_action_id),
            threshold_action_id=str(counter.threshold_action_id),
        )
        counter_context: dict[str, AuthorityValue] = {
            "value": current_value,
            "threshold_count": counter.threshold_count,
            "increment_action_id": str(counter.increment_action_id),
            "threshold_action_id": str(counter.threshold_action_id),
            "next_increment_requires_threshold_action": (
                next_increment_reaches_threshold and not derives_recovery
            ),
        }
        if next_increment_reaches_threshold and derives_recovery:
            counter_context["next_increment_derives_threshold_recovery"] = True
        counters[str(counter.id)] = counter_context
    return counters


def _terminal_options_for_dispatch(
    *,
    state: RuntimeState,
    run: RunRecord,
    work_item: WorkItem,
    stage: StageKindDeclaration,
) -> tuple[Mapping[str, AuthorityValue], ...]:
    admitted = state.admitted_plans.get(run.run_ref.plan_ref.authority_fingerprint)
    if admitted is None:
        return ()
    options: list[Mapping[str, AuthorityValue]] = []
    for outcome in admitted.selected_plan.terminal_outcomes:
        if outcome.stage_kind_id != run.stage_kind_id:
            continue
        if not outcome.marker.strip() or outcome.id not in stage.declared_outcome_ids:
            continue
        action = next(
            (
                candidate
                for candidate in admitted.selected_plan.terminal_actions
                if candidate.stage_kind_id == run.stage_kind_id
                and candidate.outcome_id == outcome.id
            ),
            None,
        )
        if action is None:
            continue
        option: dict[str, AuthorityValue] = {
            "outcome_id": str(outcome.id),
            "marker": outcome.marker,
            "action_id": str(action.id),
            "action_kind": action.action_kind,
            "artifact_schema_id": (
                str(action.artifact_schema_id)
                if action.artifact_schema_id is not None
                else None
            ),
        }
        counter_context = _counter_context_for_action(
            state=state,
            run=run,
            work_item=work_item,
            action_id=str(action.id),
        )
        if counter_context:
            option["counter"] = counter_context
        options.append(option)
    return tuple(options)


def _counter_context_for_action(
    *,
    state: RuntimeState,
    run: RunRecord,
    work_item: WorkItem,
    action_id: str,
) -> Mapping[str, AuthorityValue]:
    admitted = state.admitted_plans.get(run.run_ref.plan_ref.authority_fingerprint)
    if admitted is None or work_item.lineage_id is None:
        return {}
    for counter in admitted.selected_plan.counters:
        if str(counter.increment_action_id) != action_id and str(
            counter.threshold_action_id
        ) != action_id:
            continue
        current_value = next(
            (
                record.value
                for record in state.counters.values()
                if record.selected_plan_ref == run.run_ref.plan_ref
                and record.counter_id == counter.id
                and record.lineage_id == work_item.lineage_id
            ),
            0,
        )
        return {
            "counter_id": str(counter.id),
            "value": current_value,
            "threshold_count": counter.threshold_count,
            "increment_action_id": str(counter.increment_action_id),
            "threshold_action_id": str(counter.threshold_action_id),
            "next_increment_requires_threshold_action": (
                current_value + 1 >= counter.threshold_count
            ),
        }
    return {}


def _counter_increment_derives_recovery_threshold(
    *,
    plan: SelectedCompiledPlan,
    increment_action_id: str,
    threshold_action_id: str,
) -> bool:
    threshold_action = next(
        (
            action
            for action in plan.terminal_actions
            if str(action.id) == threshold_action_id
        ),
        None,
    )
    if threshold_action is None or threshold_action.action_kind != "recovery_route":
        return False
    return any(
        increment_action_id
        in {str(action_id) for action_id in policy.source_recovery_action_ids}
        and threshold_action.target_stage_kind_id == policy.recovery_stage_kind_id
        for policy in plan.recovery_policies
    )


def _authority_display_name(presentation: Mapping[str, AuthorityValue]) -> str | None:
    display_name = presentation.get("display_name")
    return display_name if isinstance(display_name, str) else None


def _downstream_graph_node_ids(
    *,
    selected_plan: SelectedCompiledPlan,
    current_stage_kind_id: StageKindId,
) -> tuple[str, ...]:
    order_by_stage_kind_id = {
        stage.id: _graph_order(stage.presentation)
        for stage in selected_plan.stage_kinds
    }
    candidates: dict[str, tuple[int, str]] = {}
    for action in selected_plan.terminal_actions:
        if (
            action.target_stage_kind_id is None
            or action.target_graph_node_id is None
            or action.target_stage_kind_id == current_stage_kind_id
        ):
            continue
        order = order_by_stage_kind_id.get(action.target_stage_kind_id, 1_000_000)
        candidates.setdefault(
            action.target_graph_node_id,
            (order, action.target_graph_node_id),
        )
    return tuple(item[1] for item in sorted(candidates.values()))


def _graph_order(presentation: Mapping[str, AuthorityValue]) -> int:
    details = presentation.get("details")
    if not isinstance(details, Mapping):
        return 1_000_000
    graph_order = details.get("graph_order")
    return graph_order if type(graph_order) is int else 1_000_000


__all__ = (
    "DispatchProjectionError",
    "DispatchSuspensionProjection",
    "ReadyDispatchCandidate",
    "ReadyDispatchDiagnostic",
    "ReadyDispatchProjection",
    "build_dispatch_envelope_for_run",
    "dispatch_suspension_projection",
    "join_evidence_progress_for_status",
    "list_ready_dispatch_candidates",
    "ready_diagnostic_from_claim_refusal",
    "run_may_start_while_dispatch_suspended",
)
