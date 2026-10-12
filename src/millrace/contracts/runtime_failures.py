"""Selected authority and durable provenance for mechanical runner failures."""

from __future__ import annotations

import json
from hashlib import sha256

from millrace.contracts.compiled_plan import (
    RecoveryPolicyDeclaration,
    RunnerBindingWithRuntimeFailuresAndPayloadCapacityDeclaration,
    RunnerBindingWithRuntimeFailuresDeclaration,
    RunnerRuntimeFailureMapping,
    SelectedCompiledPlan,
    TerminalActionDeclaration,
)
from millrace.contracts.ids import RunnerBindingId, StageKindId
from millrace.contracts.runner import (
    RUNNER_SESSION_COMPLETION_DIAGNOSTIC_MAX_BYTES,
    runner_session_completion_diagnostic_bytes,
    runner_session_completion_diagnostic_from_payload,
)
from millrace.contracts.runtime_failure_history import (
    accepted_recovery_origin_matches,
    recorded_source_return_origin,
    recovery_attempt_count_at_input,
    recovery_attempt_history_matches,
    recovery_source_policies,
    selected_activation_targets,
)
from millrace.contracts.runtime_failure_history import (
    claimed_activation_matches as _target_claim_matches,
)
from millrace.contracts.selected_plan_lookups import (
    runner_selectable_outcome_ids,
    runtime_owned_threshold_for_outcomes,
)
from millrace.contracts.state import (
    ActivationRouteRecord,
    RunnerSessionCompletionRecord,
    RunRecord,
    RuntimeState,
)
from millrace.contracts.transition import (
    ApplyRunnerRuntimeFailure,
    ClaimWork,
    RunnerResultObserved,
    TimerDue,
    TransitionInput,
    input_family,
    input_payload_digest,
)


def runtime_failure_mapping_refusal(plan: SelectedCompiledPlan) -> str | None:
    """Refuse incomplete selected failure mappings without interpreting labels."""
    for binding in plan.runner_bindings:
        binding_id = getattr(binding, "id", "invalid")
        mappings = getattr(binding, "runtime_failure_mappings", ())
        if not mappings:
            if type(binding) in (
                RunnerBindingWithRuntimeFailuresDeclaration,
                RunnerBindingWithRuntimeFailuresAndPayloadCapacityDeclaration,
            ):
                return f"runtime_failure_mapping_shape:{binding_id}"
            continue
        if type(binding) not in (
            RunnerBindingWithRuntimeFailuresDeclaration,
            RunnerBindingWithRuntimeFailuresAndPayloadCapacityDeclaration,
        ) or not isinstance(mappings, tuple):
            return f"runtime_failure_mapping_shape:{binding_id}"
        keys: list[tuple[str, str, str]] = []
        for mapping in mappings:
            if type(mapping) is not RunnerRuntimeFailureMapping:
                return f"runtime_failure_mapping_shape:{binding.id}"
            if mapping.failure_kind != "timeout":
                return f"runtime_failure_mapping_kind:{binding.id}"
            keys.append(
                (
                    str(mapping.stage_kind_id),
                    mapping.failure_kind,
                    str(mapping.action_id),
                )
            )
            stages = [s for s in plan.stage_kinds if s.id == mapping.stage_kind_id]
            actions = [a for a in plan.terminal_actions if a.id == mapping.action_id]
            if (
                len(stages) != 1
                or stages[0].runner_binding_id != binding.id
                or mapping.stage_kind_id not in binding.stage_kind_ids
                or len(actions) != 1
                or actions[0].stage_kind_id != mapping.stage_kind_id
                or actions[0].action_kind != "recovery_route"
            ):
                return f"runtime_failure_mapping_authority:{binding.id}"
            action = actions[0]
            outcomes = [o for o in plan.terminal_outcomes if o.id == action.outcome_id]
            policies = [
                p
                for p in plan.recovery_policies
                if action.id in p.source_recovery_action_ids
            ]
            if (
                len(outcomes) != 1
                or outcomes[0].stage_kind_id != mapping.stage_kind_id
                or action.outcome_id not in stages[0].declared_outcome_ids
                or len(policies) != 1
            ):
                return f"runtime_failure_mapping_authority:{binding.id}"
            for counter in plan.counters:
                if counter.increment_action_id != action.id:
                    continue
                threshold = next(
                    (
                        a
                        for a in plan.terminal_actions
                        if a.id == counter.threshold_action_id
                    ),
                    None,
                )
                if threshold is not None and runtime_owned_threshold_for_outcomes(
                    threshold_action_kind=threshold.action_kind,
                    threshold_outcome_id=str(threshold.outcome_id),
                    runner_selectable_outcome_ids=runner_selectable_outcome_ids(
                        plan, stage_kind_id=str(counter.stage_kind_id)
                    ),
                ):
                    return f"runtime_failure_mapping_counter_redirect:{binding.id}"
            # Mechanical failures cannot manufacture a workflow output artifact.
            if action.artifact_schema_id is not None:
                return f"runtime_failure_mapping_artifact:{binding.id}"
        if keys != sorted(keys) or len({key[:2] for key in keys}) != len(keys):
            return f"runtime_failure_mapping_duplicate:{binding.id}"
    return None


def runtime_failure_action_for(
    plan: SelectedCompiledPlan,
    stage_kind_id: StageKindId,
    runner_binding_id: RunnerBindingId,
    failure_kind: str,
) -> TerminalActionDeclaration | None:
    if runtime_failure_mapping_refusal(plan) is not None:
        return None
    bindings = [b for b in plan.runner_bindings if b.id == runner_binding_id]
    if len(bindings) != 1:
        return None
    mappings = [
        m
        for m in getattr(bindings[0], "runtime_failure_mappings", ())
        if m.stage_kind_id == stage_kind_id and m.failure_kind == failure_kind
    ]
    if len(mappings) != 1:
        return None
    return next(
        (a for a in plan.terminal_actions if a.id == mappings[0].action_id), None
    )


def runtime_failure_eligible(
    state: RuntimeState, completion: RunnerSessionCompletionRecord
) -> bool:
    """A clean adapter-origin timeout is distinct from every cancellation."""
    session = state.runner_sessions.get(completion.session_id)
    run = state.runs.get(completion.run_id)
    return (
        session is not None
        and run is not None
        and run.current_session_id == session.session_id
        and state.runner_session_completions.get(session.session_id) == completion
        and session.run_id == completion.run_id
        and session.state == completion.terminal_state == "failed"
        and session.dispatch_generation
        == completion.dispatch_generation
        == run.last_dispatch_generation
        and session.session_fencing_token == completion.session_fencing_token
        and session.started_at is not None
        and completion.started_at == session.started_at
        and session.ended_at == completion.completed_at
        and completion.exit_kind == "error"
        and completion.adapter_outcome_kind == "error"
        and completion.adapter_error_kind == "timeout"
        and completion.runner_result_evidence_digest is None
        and completion.cleanup_disposition == session.cleanup_disposition
        and completion.cleanup_disposition in {"complete", "not_required"}
        and completion.primary_cancellation_request_id is None
        and not any(
            r.session_id == session.session_id
            for r in state.runner_session_cancellation_requests.values()
        )
    )


def runtime_failure_diagnostic_matches(
    state: RuntimeState, completion: RunnerSessionCompletionRecord, payload: bytes
) -> bool:
    """Authenticate persisted timeout facts before applying mechanical recovery."""
    if (
        not runtime_failure_eligible(state, completion)
        or len(payload) > RUNNER_SESSION_COMPLETION_DIAGNOSTIC_MAX_BYTES
        or completion.diagnostic_digest != f"sha256:{sha256(payload).hexdigest()}"
    ):
        return False
    try:
        raw = json.loads(payload)
        diagnostic = runner_session_completion_diagnostic_from_payload(raw)
        if runner_session_completion_diagnostic_bytes(diagnostic) != payload:
            return False
    except (TypeError, ValueError, UnicodeDecodeError, RecursionError):
        return False
    run = state.runs[completion.run_id]
    activation = state.activations.get(run.activation_id)
    return (
        activation is not None
        and (
            diagnostic.run_id,
            diagnostic.session_id,
            diagnostic.dispatch_generation,
            diagnostic.session_fencing_token,
            diagnostic.plan_fingerprint,
            diagnostic.claim_id,
            diagnostic.generation,
            diagnostic.fencing_token,
            diagnostic.stage_kind_id,
            diagnostic.graph_node_id,
            diagnostic.runner_binding_id,
        )
        == (
            completion.run_id,
            completion.session_id,
            completion.dispatch_generation,
            completion.session_fencing_token,
            str(run.run_ref.plan_ref.authority_fingerprint),
            run.run_ref.claim_id,
            run.run_ref.generation,
            run.run_ref.fencing_token,
            str(run.stage_kind_id),
            activation.graph_node_id,
            str(run.runner_binding_id),
        )
        and set(diagnostic.diagnostic) == {"diagnostics", "error_kind"}
        and diagnostic.diagnostic.get("error_kind") == "timeout"
    )


def runtime_failure_input(
    state: RuntimeState, completion: RunnerSessionCompletionRecord
) -> ApplyRunnerRuntimeFailure:
    if not runtime_failure_eligible(state, completion):
        raise ValueError("ineligible runtime failure completion")
    run = state.runs[completion.run_id]
    return ApplyRunnerRuntimeFailure(
        completion.application_input_id,
        run_ref=run.run_ref,
        session_id=completion.session_id,
        dispatch_generation=completion.dispatch_generation,
        session_fencing_token=completion.session_fencing_token,
        completion_id=completion.completion_id,
        diagnostic_digest=completion.diagnostic_digest,
        failure_kind="timeout",
        observed_at=completion.completed_at // 1_000_000_000,
    )


def runtime_failure_authority_refusal(
    state: RuntimeState, command: ApplyRunnerRuntimeFailure
) -> str | None:
    run = state.runs.get(command.run_id)
    session = state.runner_sessions.get(command.session_id)
    completion = state.runner_session_completions.get(command.session_id)
    if run is None or session is None or completion is None:
        return "runtime_failure_authority_mismatch"
    if not runtime_failure_eligible(state, completion):
        return "runtime_failure_authority_mismatch"
    if command != runtime_failure_input(state, completion):
        return "runtime_failure_authority_mismatch"
    work = state.work_items.get(run.work_item_id)
    activation = state.activations.get(run.activation_id)
    admitted = state.admitted_plans.get(run.run_ref.plan_ref.authority_fingerprint)
    if (
        work is None
        or activation is None
        or admitted is None
        or admitted.plan_ref != run.run_ref.plan_ref
        or work.ref.plan_ref != run.run_ref.plan_ref
        or activation.plan_ref != run.run_ref.plan_ref
        or run.run_ref.work_item_id != work.ref.work_item_id
        or activation.work_item_id != work.ref.work_item_id
        or activation.claimed_by_run_id != run.run_ref.run_id
        or run.run_ref.generation != work.ref.generation
        or activation.generation != run.run_ref.generation + 1
        or activation.stage_kind_id != run.stage_kind_id
        or activation.runner_binding_id != run.runner_binding_id
        or activation.lineage_id != work.lineage_id
        or activation.queue_family_id != work.queue_family_id
    ):
        return "runtime_failure_authority_mismatch"
    plan = admitted.selected_plan
    stages = [s for s in plan.stage_kinds if s.id == run.stage_kind_id]
    targets = selected_activation_targets(plan)
    selected_target = (
        work.queue_family_id,
        run.stage_kind_id,
        activation.graph_node_id,
        run.runner_binding_id,
    )
    if (
        len(stages) != 1
        or stages[0].runner_binding_id != run.runner_binding_id
        or not any(activation.graph_node_id in g.node_ids for g in plan.graphs)
    ):
        return "runtime_failure_authority_mismatch"
    ordinary = (
        work.queue_family_id in stages[0].input_queue_family_ids
        and selected_target in targets
    )
    if not ordinary and not recovery_activation_origin_matches(
        state, plan, activation.activation_id, targets
    ):
        return "runtime_failure_authority_mismatch"
    claim = ClaimWork(run.created_by_input_id, activation_id=activation.activation_id)
    receipt = state.receipts.get(claim.input_id)
    transitions = [t for t in state.transitions if t.input_id == claim.input_id]
    if (
        receipt is None
        or not receipt.accepted
        or receipt.receipt_ref.input_payload_digest != input_payload_digest(claim)
        or len(transitions) != 1
        or not transitions[0].accepted
        or transitions[0].record_id != receipt.transition_id
        or transitions[0].input_kind != ClaimWork.input_kind
        or transitions[0].input_family != input_family(claim)
    ):
        return "runtime_failure_authority_mismatch"
    return None


def recovery_activation_origin_matches(
    state: RuntimeState,
    plan: SelectedCompiledPlan,
    activation_id: str,
    targets: set[tuple[object, object, object, object]],
) -> bool:
    """Follow accepted inherited-work recovery origins to a normal selected root."""
    indices = {t.input_id: i for i, t in enumerate(state.transitions)}
    ceiling = len(state.transitions)
    seen: set[str] = set()
    while activation_id not in seen:
        seen.add(activation_id)
        activation = state.activations.get(activation_id)
        if activation is None or not _target_claim_matches(state, activation_id):
            return False
        work = state.work_items[activation.work_item_id]
        run = state.runs[activation.claimed_by_run_id or ""]
        claim_index = indices.get(run.created_by_input_id, -1)
        stages = [
            stage for stage in plan.stage_kinds if stage.id == activation.stage_kind_id
        ]
        if (
            len(stages) != 1
            or stages[0].runner_binding_id != activation.runner_binding_id
            or activation.plan_ref != work.ref.plan_ref
            or activation.lineage_id != work.lineage_id
            or activation.queue_family_id != work.queue_family_id
            or not 0 <= claim_index < ceiling
        ):
            return False
        target = (
            activation.queue_family_id,
            activation.stage_kind_id,
            activation.graph_node_id,
            activation.runner_binding_id,
        )
        if (
            activation.queue_family_id in stages[0].input_queue_family_ids
            and target in targets
        ):
            return True
        routes = [
            route
            for route in state.activation_routes
            if route.target_activation_id == activation_id
        ]
        if len(routes) != 1:
            return False
        route = routes[0]
        source = state.runs.get(route.source_run_id)
        actions = [
            action for action in plan.terminal_actions if action.id == route.action_id
        ]
        if source is None or len(actions) != 1:
            return False
        action = actions[0]
        if action.action_kind == "return_to_recorded_source":
            if recorded_source_return_origin(state, plan, activation_id) != source:
                return False
            origin_index = indices.get(route.created_by_input_id, -1)
            if not 0 <= origin_index < claim_index:
                return False
            activation_id = source.activation_id
            ceiling = origin_index
            continue
        policies = recovery_source_policies(plan, action.id)
        if (
            len(policies) != 1
            or action.action_kind != "recovery_route"
            or action.stage_kind_id != source.stage_kind_id
            or action.target_stage_kind_id != policies[0].recovery_stage_kind_id
            or (
                action.target_stage_kind_id,
                action.target_graph_node_id,
                action.runner_binding_id,
            )
            != target[1:]
            or route.source_work_item_id != route.target_work_item_id
            or route.source_work_item_id != work.ref.work_item_id
            or source.work_item_id != work.ref.work_item_id
            or source.run_ref.plan_ref != activation.plan_ref
            or route.created_by_input_id != activation.created_by_input_id
        ):
            return False
        origin_index = indices.get(route.created_by_input_id, -1)
        if not 0 <= origin_index < claim_index:
            return False
        command = _recovery_origin_command(state, source, route.created_by_input_id)
        if command is None:
            command = _recovery_timer_command(state, source, route, action, policies[0])
        if command is None or not accepted_recovery_origin_matches(
            state, command, source, action
        ):
            return False
        if (
            isinstance(command, ApplyRunnerRuntimeFailure)
            and runtime_failure_action_for(
                plan,
                source.stage_kind_id,
                source.runner_binding_id,
                command.failure_kind,
            )
            != action
        ):
            return False
        receipt = state.receipts[command.input_id]
        if route.record_id != f"{receipt.transition_id}:route":
            return False
        activation_id = source.activation_id
        ceiling = origin_index
    return False


def _recovery_origin_command(
    state: RuntimeState,
    source: RunRecord,
    input_id: str,
) -> TransitionInput | None:
    transitions = [t for t in state.transitions if t.input_id == input_id]
    if len(transitions) != 1 or not transitions[0].accepted:
        return None
    if transitions[0].input_kind == ApplyRunnerRuntimeFailure.input_kind:
        completion = state.runner_session_completions.get(
            source.current_session_id or ""
        )
        if completion is None or completion.application_input_id != input_id:
            return None
        try:
            return runtime_failure_input(state, completion)
        except ValueError:
            return None
    if transitions[0].input_kind != RunnerResultObserved.input_kind:
        return None
    observations = [
        observation
        for observation in state.runner_observations.values()
        if observation.run_id == source.run_ref.run_id
        and observation.created_by_input_id == input_id
    ]
    if len(observations) != 1:
        return None
    observation = observations[0]
    return RunnerResultObserved(
        input_id,
        run_id=source.run_ref.run_id,
        payload=observation.payload,
        observed_at=observation.observed_at,
    )


def _recovery_timer_command(
    state: RuntimeState,
    source: RunRecord,
    route: ActivationRouteRecord,
    action: TerminalActionDeclaration,
    policy: RecoveryPolicyDeclaration,
) -> TimerDue | None:
    waits = [
        wait
        for wait in state.cooldown_waits.values()
        if wait.consumed_input_id == route.created_by_input_id
        and wait.resulting_recovery_activation_id == route.target_activation_id
    ]
    if len(waits) != 1:
        return None
    wait = waits[0]
    attempt = state.recovery_attempts.get(wait.recovery_attempt_record_id)
    plan = state.admitted_plans[
        source.run_ref.plan_ref.authority_fingerprint
    ].selected_plan
    declaration = next(
        (decl for decl in plan.wait_states if decl.id == policy.cooldown_wait_state_id),
        None,
    )
    duration = (
        policy.default_cooldown_seconds
        if declaration is None
        else declaration.duration_seconds
    )
    original = _recovery_origin_command(state, source, wait.created_input_id)
    if (
        isinstance(original, ApplyRunnerRuntimeFailure)
        and runtime_failure_action_for(
            plan, source.stage_kind_id, source.runner_binding_id, original.failure_kind
        )
        != action
    ):
        return None
    indices = {t.input_id: i for i, t in enumerate(state.transitions)}
    if (
        attempt is None
        or original is None
        or not recovery_attempt_history_matches(state, attempt, policy)
        or not accepted_recovery_origin_matches(state, original, source, action)
        or wait.policy_id != policy.id
        or wait.plan_ref != source.run_ref.plan_ref
        or wait.lineage_id != state.work_items[source.work_item_id].lineage_id
        or attempt.plan_ref != wait.plan_ref
        or attempt.lineage_id != wait.lineage_id
        or attempt.policy_id != policy.id
        or not indices.get(wait.created_input_id, -1)
        < indices.get(route.created_by_input_id, -1)
        or wait.attempt_count
        != recovery_attempt_count_at_input(
            state, attempt, policy, wait.created_input_id
        )
        or wait.wait_id
        != (
            f"cooldown-wait:{wait.plan_ref.authority_fingerprint}:"
            f"{policy.id}:{wait.lineage_id}:{wait.attempt_count}:{wait.created_input_id}"
        )
        or wait.attempt_count >= policy.quarantine_threshold_attempt
        or not policy.cooldown_starts_at_attempt
        <= wait.attempt_count
        <= attempt.attempt_count
        or wait.source_run_id != source.run_ref.run_id
        or wait.source_activation_id != source.activation_id
        or wait.source_work_item_id != source.work_item_id
        or (
            wait.recovery_action_id,
            wait.target_stage_kind_id,
            wait.target_graph_node_id,
            wait.target_runner_binding_id,
        )
        != (
            action.id,
            action.target_stage_kind_id,
            action.target_graph_node_id,
            action.runner_binding_id,
        )
        or wait.created_at != getattr(original, "observed_at", None)
        or wait.due_at != wait.created_at + duration
        or wait.consumed_at is None
        or wait.consumed_at < wait.due_at
    ):
        return None
    return TimerDue(
        route.created_by_input_id, wait_id=wait.wait_id, observed_at=wait.consumed_at
    )


def runtime_failure_applied(state: RuntimeState, run_id: str) -> bool:
    """Authenticate settlement; a bare accepted receipt is never sufficient."""
    run = state.runs.get(run_id)
    completion = state.runner_session_completions.get(
        "" if run is None else run.current_session_id or ""
    )
    if completion is None or not runtime_failure_eligible(state, completion):
        return False
    command = runtime_failure_input(state, completion)
    if runtime_failure_authority_refusal(state, command) is not None:
        return False
    assert run is not None
    plan = state.admitted_plans[
        run.run_ref.plan_ref.authority_fingerprint
    ].selected_plan
    action = runtime_failure_action_for(
        plan, run.stage_kind_id, run.runner_binding_id, command.failure_kind
    )
    if action is None:
        return False
    receipt = state.receipts.get(command.input_id)
    transitions = [t for t in state.transitions if t.input_id == command.input_id]
    if (
        receipt is None
        or not receipt.accepted
        or receipt.refusal_reason is not None
        or receipt.receipt_ref.input_id != command.input_id
        or receipt.receipt_ref.input_payload_digest != input_payload_digest(command)
        or len(transitions) != 1
    ):
        return False
    transition = transitions[0]
    if (
        transition.record_id != receipt.transition_id
        or not transition.accepted
        or transition.input_kind != command.input_kind
        or transition.input_family != input_family(command)
    ):
        return False
    expected = (
        command.input_id,
        command.input_kind,
        input_family(command),
        "accepted",
        run.run_ref.plan_ref.authority_fingerprint,
        run.work_item_id,
        run_id,
        action.id,
        "terminal_action",
        None,
    )
    for records, suffix in (
        (state.governance_events, "governance"),
        (state.traces, "trace"),
    ):
        matching = [r for r in records if r.input_id == command.input_id]
        if len(matching) != 1:
            return False
        record = matching[0]
        if (
            record.record_id != f"{transition.record_id}:{suffix}"
            or (
                record.input_id,
                record.input_kind,
                record.input_family,
                record.disposition,
                record.plan_fingerprint,
                record.work_item_id,
                record.run_id,
                record.action_id,
                record.authority_source,
                record.refusal_reason,
            )
            != expected
        ):
            return False
    return not any(
        o.run_id == run_id for o in state.runner_observations.values()
    ) and _runtime_failure_effects_match(state, command, action)


def _runtime_failure_effects_match(
    state: RuntimeState,
    command: ApplyRunnerRuntimeFailure,
    action: TerminalActionDeclaration,
) -> bool:
    run = state.runs[command.run_id]
    work = state.work_items[run.work_item_id]
    plan = state.admitted_plans[
        run.run_ref.plan_ref.authority_fingerprint
    ].selected_plan
    policies = [
        p for p in plan.recovery_policies if action.id in p.source_recovery_action_ids
    ]
    if len(policies) != 1:
        return False
    policy = policies[0]
    attempts = [
        a
        for a in state.recovery_attempts.values()
        if a.policy_id == policy.id
        and a.plan_ref == run.run_ref.plan_ref
        and a.lineage_id == work.lineage_id
    ]
    if not attempts or any(
        not recovery_attempt_history_matches(state, attempt, policy)
        for attempt in attempts
    ):
        return False
    indices = {
        transition.input_id: index for index, transition in enumerate(state.transitions)
    }
    origin_index = indices.get(command.input_id, -1)
    origin_attempts = [
        attempt
        for attempt in attempts
        if indices.get(attempt.created_by_input_id, -1)
        <= origin_index
        <= indices.get(attempt.updated_by_input_id, -1)
    ]
    if len(origin_attempts) != 1:
        return False
    origin_attempt = origin_attempts[0]
    transition_id = state.receipts[command.input_id].transition_id
    routes = [
        r for r in state.activation_routes if r.created_by_input_id == command.input_id
    ]
    waits = [
        w
        for w in state.cooldown_waits.values()
        if w.created_input_id == command.input_id
    ]
    quarantines = [
        q
        for q in state.lineage_quarantines.values()
        if q.created_input_id == command.input_id
    ]
    if len(routes) + len(waits) + len(quarantines) != 1:
        return False
    if routes:
        route = routes[0]
        activation = state.activations.get(route.target_activation_id)
        return (
            route.record_id == f"{transition_id}:route"
            and route.action_id == action.id
            and route.source_run_id == command.run_id
            and route.source_work_item_id
            == route.target_work_item_id
            == run.work_item_id
            and activation is not None
            and activation.created_by_input_id == command.input_id
            and activation.generation
            == work.ref.generation + (activation.claimed_by_run_id is not None)
            and _target_claim_matches(state, activation.activation_id)
            and activation.work_item_id == run.work_item_id
            and activation.plan_ref == run.run_ref.plan_ref
            and activation.lineage_id == work.lineage_id
            and activation.queue_family_id == work.queue_family_id
            and activation.stage_kind_id == action.target_stage_kind_id
            and activation.graph_node_id == action.target_graph_node_id
            and activation.runner_binding_id == action.runner_binding_id
        )
    if waits:
        wait = waits[0]
        declaration = next(
            (w for w in plan.wait_states if w.id == policy.cooldown_wait_state_id), None
        )
        duration = (
            policy.default_cooldown_seconds
            if declaration is None
            else declaration.duration_seconds
        )
        return (
            wait.recovery_attempt_record_id == origin_attempt.record_id
            and wait.wait_id
            == (
                f"cooldown-wait:{run.run_ref.plan_ref.authority_fingerprint}:"
                f"{policy.id}:{work.lineage_id}:{wait.attempt_count}:{command.input_id}"
            )
            and wait.attempt_count <= origin_attempt.attempt_count
            and wait.attempt_count >= policy.cooldown_starts_at_attempt
            and wait.policy_id == policy.id
            and wait.lineage_id == work.lineage_id
            and wait.plan_ref == run.run_ref.plan_ref
            and wait.source_run_id == command.run_id
            and wait.source_work_item_id == run.work_item_id
            and wait.source_activation_id == run.activation_id
            and wait.recovery_action_id == action.id
            and wait.target_stage_kind_id == action.target_stage_kind_id
            and wait.target_graph_node_id == action.target_graph_node_id
            and wait.target_runner_binding_id == action.runner_binding_id
            and wait.created_at == command.observed_at
            and wait.due_at == command.observed_at + duration
            and wait.recovery_attempt_record_id in state.recovery_attempts
        )
    quarantine = quarantines[0]
    if quarantine.status == "active":
        status_valid = quarantine.superseded_input_id is None
    elif quarantine.status == "superseded":
        status_valid = any(
            intervention.quarantine_id == quarantine.quarantine_id
            and intervention.recovery_attempt_record_id == origin_attempt.record_id
            and intervention.created_by_input_id == quarantine.superseded_input_id
            and intervention.selected_plan_ref == run.run_ref.plan_ref
            and intervention.policy_id == policy.id
            and intervention.lineage_id == work.lineage_id
            and intervention.attempt_effect == "resolve_attempt"
            for intervention in state.operator_interventions.values()
        )
    else:
        status_valid = False
    return (
        status_valid
        and quarantine.recovery_attempt_record_id == origin_attempt.record_id
        and quarantine.quarantine_id
        == (
            f"lineage-quarantine:{run.run_ref.plan_ref.authority_fingerprint}:"
            f"{policy.id}:{work.lineage_id}:{origin_attempt.record_id}"
        )
        and quarantine.attempt_count == origin_attempt.attempt_count
        and quarantine.policy_id == policy.id
        and quarantine.lineage_id == work.lineage_id
        and quarantine.selected_plan_ref == run.run_ref.plan_ref
        and quarantine.selected_plan_fingerprint
        == run.run_ref.plan_ref.authority_fingerprint
        and quarantine.emitting_recovery_run_id == command.run_id
        and quarantine.emitting_recovery_activation_id == run.activation_id
        and quarantine.action_id in policy.quarantine_action_ids
        and quarantine.attempt_count >= policy.quarantine_threshold_attempt
        and quarantine.recovery_attempt_record_id in state.recovery_attempts
    )


__all__ = (
    "recovery_activation_origin_matches",
    "runtime_failure_action_for",
    "runtime_failure_applied",
    "runtime_failure_authority_refusal",
    "runtime_failure_diagnostic_matches",
    "runtime_failure_eligible",
    "runtime_failure_input",
    "runtime_failure_mapping_refusal",
)
