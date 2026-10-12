"""Accepted lifecycle provenance for selected recovery attempt state."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace

from millrace.contracts.compiled_plan import (
    RecoveryPolicyDeclaration,
    SelectedCompiledPlan,
    TerminalActionDeclaration,
)
from millrace.contracts.ids import ActionId, QueueFamilyId
from millrace.contracts.runner import (
    runner_result_evidence_digest,
    runner_result_evidence_from_payload,
)
from millrace.contracts.selected_plan_lookups import (
    stage_kind_for,
    terminal_action_for,
    terminal_outcome_for,
)
from millrace.contracts.state import RecoveryAttemptRecord, RunRecord, RuntimeState
from millrace.contracts.transition import (
    ClaimWork,
    RunnerResultObserved,
    TransitionInput,
    input_family,
    input_payload_digest,
)


def recovery_attempt_history_matches(
    state: RuntimeState,
    attempt: RecoveryAttemptRecord,
    policy: RecoveryPolicyDeclaration,
) -> bool:
    """Authenticate mutable attempt count/phase against its accepted lifecycle."""
    admitted = state.admitted_plans.get(attempt.plan_ref.authority_fingerprint)
    if admitted is None or admitted.plan_ref != attempt.plan_ref:
        return False
    plan = admitted.selected_plan
    if attempt.record_id != (
        f"recovery-attempt:{attempt.plan_ref.authority_fingerprint}:"
        f"{policy.id}:{attempt.lineage_id}:{attempt.created_by_input_id}"
    ):
        return False
    indices = {t.input_id: index for index, t in enumerate(state.transitions)}
    start = indices.get(attempt.created_by_input_id)
    end = indices.get(attempt.updated_by_input_id)
    if start is None or end is None or end < start:
        return False
    count = 0
    phase: str | None = None
    source_run_id: str | None = None
    source_action_id = None
    update_seen = False
    for transition in state.transitions[start : end + 1]:
        if not transition.accepted:
            continue
        events = [
            e for e in state.governance_events if e.input_id == transition.input_id
        ]
        traces = [t for t in state.traces if t.input_id == transition.input_id]
        receipt = state.receipts.get(transition.input_id)
        if (
            len(events) != 1
            or len(traces) != 1
            or receipt is None
            or not receipt.accepted
        ):
            return False
        event, trace = events[0], traces[0]
        if (
            receipt.transition_id != transition.record_id
            or receipt.receipt_ref.input_id != transition.input_id
            or event.record_id != f"{transition.record_id}:governance"
            or trace.record_id != f"{transition.record_id}:trace"
            or (
                event.input_id,
                event.input_kind,
                event.input_family,
                event.disposition,
                event.plan_fingerprint,
                event.work_item_id,
                event.run_id,
                event.action_id,
                event.authority_source,
                event.refusal_reason,
            )
            != (
                trace.input_id,
                trace.input_kind,
                trace.input_family,
                trace.disposition,
                trace.plan_fingerprint,
                trace.work_item_id,
                trace.run_id,
                trace.action_id,
                trace.authority_source,
                trace.refusal_reason,
            )
            or event.disposition != "accepted"
            or event.input_kind != transition.input_kind
            or event.input_family != transition.input_family
        ):
            return False
        work = state.work_items.get(event.work_item_id or "")
        scoped = (
            event.authority_source == "terminal_action"
            and event.plan_fingerprint == attempt.plan_ref.authority_fingerprint
            and work is not None
            and work.lineage_id == attempt.lineage_id
            and work.ref.plan_ref == attempt.plan_ref
        )
        changed = False
        consumed_waits = [
            wait
            for wait in state.cooldown_waits.values()
            if wait.recovery_attempt_record_id == attempt.record_id
            and wait.policy_id == policy.id
            and wait.plan_ref == attempt.plan_ref
            and wait.lineage_id == attempt.lineage_id
            and wait.consumed_input_id == transition.input_id
        ]
        if transition.input_kind == "workflow.timer_due" and len(consumed_waits) == 1:
            phase = "active_recovery"
            changed = True
        elif (
            scoped
            and event.action_id is not None
            and policy in recovery_source_policies(plan, event.action_id)
        ):
            count += 1
            source_run_id = event.run_id
            source_action_id = event.action_id
            phase = (
                "quarantine_eligible"
                if count >= policy.quarantine_threshold_attempt
                else "pending_cooldown"
                if count > policy.immediate_recovery_limit
                else "active_recovery"
            )
            changed = True
        elif scoped and event.action_id in policy.reset_trigger_action_ids:
            # A reset-trigger sibling success cannot resolve a pending cooldown.
            if phase != "pending_cooldown":
                phase = "resolved"
                changed = True
        elif scoped and event.action_id in policy.quarantine_action_ids:
            phase = "quarantine_eligible"
            changed = True
        elif scoped and event.action_id in policy.return_action_ids:
            changed = True
        elif transition.input_kind == ClaimWork.input_kind:
            claimed = state.runs.get(attempt.latest_recovery_run_id or "")
            changed = (
                claimed is not None
                and claimed.created_by_input_id == transition.input_id
            )
        elif any(
            intervention.recovery_attempt_record_id == attempt.record_id
            and intervention.created_by_input_id == transition.input_id
            and intervention.attempt_effect == "resolve_attempt"
            for intervention in state.operator_interventions.values()
        ):
            phase = "resolved"
            changed = True
        if transition.input_id == attempt.updated_by_input_id:
            update_seen = changed
    source_run = state.runs.get(source_run_id or "")
    source_activation = state.activations.get(attempt.source_activation_id)
    source_work = state.work_items.get(attempt.source_work_item_id)
    return (
        update_seen
        and count == attempt.attempt_count
        and phase == attempt.phase
        and source_run is not None
        and source_activation is not None
        and source_work is not None
        and attempt.source_run_id == source_run.run_ref.run_id
        and attempt.source_activation_id
        == source_run.activation_id
        == source_activation.activation_id
        and attempt.source_work_item_id
        == source_run.work_item_id
        == source_activation.work_item_id
        and attempt.recovery_action_id == source_action_id
        and attempt.source_graph_node_id == source_activation.graph_node_id
        and attempt.source_stage_kind_id
        == source_run.stage_kind_id
        == source_activation.stage_kind_id
        and attempt.source_runner_binding_id
        == source_run.runner_binding_id
        == source_activation.runner_binding_id
        and attempt.source_queue_family_id
        == source_work.queue_family_id
        == source_activation.queue_family_id
        and source_run.run_ref.plan_ref
        == source_activation.plan_ref
        == source_work.ref.plan_ref
        == attempt.plan_ref
        and source_work.lineage_id == source_activation.lineage_id == attempt.lineage_id
    )


def selected_activation_targets(
    plan: SelectedCompiledPlan,
) -> set[tuple[object, object, object, object]]:
    targets: set[tuple[object, object, object, object]] = {
        (r.queue_family_id, r.stage_kind_id, r.graph_node_id, r.runner_binding_id)
        for r in plan.external_enqueue_routes
    } | {
        (r.queue_family_id, r.stage_kind_id, r.graph_node_id, r.runner_binding_id)
        for r in plan.generated_work_routes
    }
    for action in plan.terminal_actions:
        queues: tuple[QueueFamilyId | None, ...] = (action.emitted_queue_family_id,)
        if action.action_kind == "recovery_route":
            continue
        targets.update(
            (
                queue,
                action.target_stage_kind_id,
                action.target_graph_node_id,
                action.runner_binding_id,
            )
            for queue in queues
            if queue is not None
        )
        selector = action.dynamic_target_selector
        if isinstance(selector, Mapping):
            dynamic_targets = selector.get("targets", {})
            if isinstance(dynamic_targets, Mapping):
                targets.update(
                    (
                        target.get("emitted_queue_family_id"),
                        target.get("target_stage_kind_id"),
                        target.get("target_graph_node_id"),
                        target.get("runner_binding_id"),
                    )
                    for target in dynamic_targets.values()
                    if isinstance(target, Mapping)
                    and all(
                        isinstance(target.get(field), str)
                        for field in (
                            "emitted_queue_family_id",
                            "target_stage_kind_id",
                            "target_graph_node_id",
                            "runner_binding_id",
                        )
                    )
                )
    return targets


def claimed_activation_matches(state: RuntimeState, activation_id: str) -> bool:
    activation = state.activations[activation_id]
    if activation.claimed_by_run_id is None:
        return not any(
            run.activation_id == activation_id for run in state.runs.values()
        )
    run = state.runs.get(activation.claimed_by_run_id)
    work = state.work_items.get(activation.work_item_id)
    if (
        run is None
        or work is None
        or run.run_ref.generation != work.ref.generation
        or activation.generation != run.run_ref.generation + 1
        or run.run_ref.work_item_id != work.ref.work_item_id
        or run.activation_id != activation_id
        or run.work_item_id != activation.work_item_id
        or run.run_ref.plan_ref != activation.plan_ref
        or run.stage_kind_id != activation.stage_kind_id
        or run.runner_binding_id != activation.runner_binding_id
    ):
        return False
    claim = ClaimWork(run.created_by_input_id, activation_id=activation_id)
    receipt = state.receipts.get(claim.input_id)
    transitions = [t for t in state.transitions if t.input_id == claim.input_id]
    return (
        receipt is not None
        and receipt.accepted
        and receipt.refusal_reason is None
        and receipt.receipt_ref.input_id == claim.input_id
        and receipt.receipt_ref.input_payload_digest == input_payload_digest(claim)
        and len(transitions) == 1
        and transitions[0].accepted
        and transitions[0].record_id == receipt.transition_id
        and transitions[0].input_kind == ClaimWork.input_kind
        and transitions[0].input_family == input_family(claim)
    )


def accepted_recovery_origin_matches(
    state: RuntimeState,
    command: TransitionInput,
    source_run: RunRecord,
    action: TerminalActionDeclaration,
) -> bool:
    """Authenticate a historical selected route command and its paired audit."""
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
        not transition.accepted
        or transition.record_id != receipt.transition_id
        or transition.input_kind != command.input_kind
        or transition.input_family != input_family(command)
    ):
        return False
    expected = (
        command.input_id,
        command.input_kind,
        input_family(command),
        "accepted",
        source_run.run_ref.plan_ref.authority_fingerprint,
        source_run.work_item_id,
        source_run.run_ref.run_id,
        action.id,
        "terminal_action",
        None,
    )
    for records, suffix in (
        (state.governance_events, "governance"),
        (state.traces, "trace"),
    ):
        matching = [record for record in records if record.input_id == command.input_id]
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
    return True


def recorded_source_return_origin(
    state: RuntimeState,
    plan: SelectedCompiledPlan,
    activation_id: str,
    *,
    source_run_id: str | None = None,
) -> RunRecord | None:
    """Authenticate a return against the recorded source at its accepted input.

    Historical returns remain valid after later failures change the attempt's
    source and latest pointers. The caller may require the current source run.
    """
    activation = state.activations.get(activation_id)
    if activation is None:
        return None
    routes = [
        route for route in state.activation_routes
        if route.target_activation_id == activation_id
        or route.created_by_input_id == activation.created_by_input_id
    ]
    if len(routes) != 1 or sum(
        a.created_by_input_id == activation.created_by_input_id
        for a in state.activations.values()
    ) != 1:
        return None
    route = routes[0]
    emitter = state.runs.get(route.source_run_id)
    action = next((a for a in plan.terminal_actions if a.id == route.action_id), None)
    if (
        emitter is None
        or action is None
        or action.action_kind != "return_to_recorded_source"
    ):
        return None
    # A return action can be shared by policies. Its emitting activation's
    # recovery origin selects the applicable policy, including repeated returns.
    origin = emitter
    seen: set[str] = set()
    policies: tuple[RecoveryPolicyDeclaration, ...] = ()
    while origin.activation_id not in seen:
        seen.add(origin.activation_id)
        origins = [
            r for r in state.activation_routes
            if r.target_activation_id == origin.activation_id
        ]
        if len(origins) != 1:
            return None
        origin_route = origins[0]
        origin_action = next(
            (a for a in plan.terminal_actions if a.id == origin_route.action_id),
            None,
        )
        if origin_action is None:
            return None
        if origin_action.action_kind == "recovery_route":
            policies = tuple(
                p for p in recovery_source_policies(plan, origin_action.id)
                if action.id in p.return_action_ids
            )
            break
        if origin_action.action_kind != "return_to_recorded_source":
            return None
        previous = state.runs.get(origin_route.source_run_id)
        if previous is None or previous.run_ref.plan_ref != activation.plan_ref:
            return None
        origin = previous
    if len(policies) != 1:
        return None
    policy = policies[0]
    work = state.work_items.get(activation.work_item_id)
    emitting_stage = stage_kind_for(plan, str(emitter.stage_kind_id))
    if (
        work is None
        or work.lineage_id is None
        or emitting_stage is None
        or emitting_stage.runner_binding_id != emitter.runner_binding_id
        or emitter.stage_kind_id != policy.recovery_stage_kind_id
        or emitter.stage_kind_id != action.stage_kind_id
        or emitter.run_ref.plan_ref != activation.plan_ref
        or work.ref.plan_ref != activation.plan_ref
        or emitter.work_item_id != activation.work_item_id
        or route.source_work_item_id != activation.work_item_id
        or route.target_work_item_id != activation.work_item_id
        or route.created_by_input_id != activation.created_by_input_id
        or activation.generation != work.ref.generation
        + int(activation.claimed_by_run_id is not None)
        or not claimed_activation_matches(state, emitter.activation_id)
        or not claimed_activation_matches(state, activation_id)
    ):
        return None
    indices = {t.input_id: i for i, t in enumerate(state.transitions)}
    end = indices.get(route.created_by_input_id, -1)
    claim = indices.get(emitter.created_by_input_id, -1)
    if not 0 <= claim < end:
        return None
    observations = [
        o for o in state.runner_observations.values()
        if o.run_id == emitter.run_ref.run_id
        or o.created_by_input_id == route.created_by_input_id
    ]
    if len(observations) != 1:
        return None
    observation = observations[0]
    if (
        observation.run_id != emitter.run_ref.run_id
        or observation.created_by_input_id != route.created_by_input_id
    ):
        return None
    command = RunnerResultObserved(
        observation.created_by_input_id,
        run_id=observation.run_id,
        payload=observation.payload,
        observed_at=observation.observed_at,
    )
    if not accepted_recovery_origin_matches(state, command, emitter, action):
        return None
    receipt = state.receipts[command.input_id]
    if (
        route.record_id != f"{receipt.transition_id}:route"
        or observation.observation_id != f"{receipt.transition_id}:observation"
    ):
        return None
    try:
        evidence = runner_result_evidence_from_payload(observation.payload)
    except (TypeError, ValueError):
        return None
    emitting_activation = state.activations[emitter.activation_id]
    session = state.runner_sessions.get(emitter.current_session_id or "")
    completion = state.runner_session_completions.get(emitter.current_session_id or "")
    if (
        session is None
        or completion is None
        or session.state != "completed"
        or completion.terminal_state != "completed"
        or completion.application_input_id != command.input_id
        or completion.runner_result_evidence_digest
        != runner_result_evidence_digest(evidence)
        or (
            evidence.session_id, evidence.dispatch_generation,
            evidence.session_fencing_token,
        ) != (
            session.session_id, session.dispatch_generation,
            session.session_fencing_token,
        )
        or (
            completion.session_id, completion.run_id, completion.dispatch_generation,
            completion.session_fencing_token,
        ) != (
            session.session_id, emitter.run_ref.run_id, session.dispatch_generation,
            session.session_fencing_token,
        )
        or session.run_id != emitter.run_ref.run_id
    ):
        return None
    outcome = terminal_outcome_for(plan, str(emitter.stage_kind_id), evidence.marker)
    if (
        outcome is None
        or terminal_action_for(plan, str(emitter.stage_kind_id), str(outcome.id))
        != action
        or (
            evidence.run_id, evidence.plan_fingerprint, evidence.claim_id,
            evidence.generation, evidence.fencing_token, evidence.stage_kind_id,
            evidence.graph_node_id, evidence.runner_binding_id,
        ) != (
            emitter.run_ref.run_id, activation.plan_ref.authority_fingerprint,
            emitter.run_ref.claim_id, emitter.run_ref.generation,
            emitter.run_ref.fencing_token, str(emitter.stage_kind_id),
            emitting_activation.graph_node_id, str(emitter.runner_binding_id),
        )
    ):
        return None
    attempts = [
        attempt for attempt in state.recovery_attempts.values()
        if attempt.plan_ref == activation.plan_ref
        and attempt.policy_id == policy.id
        and attempt.lineage_id == work.lineage_id
        and 0 <= indices.get(attempt.created_by_input_id, -1) < end
    ]
    if not attempts:
        return None
    attempt = max(attempts, key=lambda a: indices[a.created_by_input_id])
    start = indices[attempt.created_by_input_id]
    sources = [
        event for event in state.governance_events
        if event.disposition == "accepted"
        and event.authority_source == "terminal_action"
        and event.plan_fingerprint == activation.plan_ref.authority_fingerprint
        and event.work_item_id == activation.work_item_id
        and event.action_id is not None
        and policy in recovery_source_policies(plan, event.action_id)
        and start <= indices.get(event.input_id, -1) < end
    ]
    if not sources:
        return None
    source_event = max(sources, key=lambda e: indices[e.input_id])
    if source_event.action_id is None:
        return None
    source = state.runs.get(source_event.run_id or "")
    if source is None or (
        source_run_id is not None and source.run_ref.run_id != source_run_id
    ):
        return None
    recorded = state.activations.get(source.activation_id)
    if recorded is None or not indices[source_event.input_id] < claim:
        return None
    recorded_stage = stage_kind_for(plan, str(recorded.stage_kind_id))
    if (
        recorded_stage is None
        or recorded_stage.runner_binding_id != recorded.runner_binding_id
        or not any(recorded.graph_node_id in graph.node_ids for graph in plan.graphs)
        or not claimed_activation_matches(state, recorded.activation_id)
    ):
        return None
    if (
        activation.work_item_id, activation.plan_ref, activation.lineage_id,
        activation.queue_family_id, activation.stage_kind_id,
        activation.graph_node_id, activation.runner_binding_id,
    ) != (
        recorded.work_item_id, recorded.plan_ref, recorded.lineage_id,
        recorded.queue_family_id, recorded.stage_kind_id,
        recorded.graph_node_id, recorded.runner_binding_id,
    ):
        return None
    count = recovery_attempt_count_at_input(state, attempt, policy, command.input_id)
    if count is None:
        return None
    # Reuse the accepted lifecycle validator with the historical source snapshot
    # rather than duplicating cooldown, reset, quarantine and return phase rules.
    historical = replace(
        attempt,
        attempt_count=count,
        source_run_id=source.run_ref.run_id,
        source_work_item_id=source.work_item_id,
        source_activation_id=source.activation_id,
        source_graph_node_id=recorded.graph_node_id,
        source_stage_kind_id=source.stage_kind_id,
        source_runner_binding_id=source.runner_binding_id,
        source_queue_family_id=recorded.queue_family_id,
        recovery_action_id=source_event.action_id,
        latest_recovery_run_id=emitter.run_ref.run_id,
        updated_by_input_id=command.input_id,
    )
    if not any(
        recovery_attempt_history_matches(
            state, replace(historical, phase=phase), policy
        )
        for phase in policy.return_allowed_phases
    ):
        return None
    return emitter


def current_recovery_return_matches(
    state: RuntimeState,
    plan: SelectedCompiledPlan,
    attempt: RecoveryAttemptRecord,
    policy: RecoveryPolicyDeclaration,
) -> bool:
    """Bind current return tracking to the latest accepted return after its source."""
    indices = {t.input_id: i for i, t in enumerate(state.transitions)}
    start = indices.get(attempt.created_by_input_id, -1)
    end = indices.get(attempt.updated_by_input_id, -1)
    events = [
        event for event in state.governance_events
        if event.disposition == "accepted"
        and event.authority_source == "terminal_action"
        and event.plan_fingerprint == attempt.plan_ref.authority_fingerprint
        and event.work_item_id == attempt.source_work_item_id
        and start <= indices.get(event.input_id, -1) <= end
    ]
    sources = [
        event for event in events
        if event.action_id is not None
        and policy in recovery_source_policies(plan, event.action_id)
    ]
    if not sources:
        return attempt.latest_return_action_id is None
    source_index = max(indices[event.input_id] for event in sources)
    possible_returns = [
        event for event in events
        if event.action_id in policy.return_action_ids
        and indices[event.input_id] > source_index
    ]
    returns = []
    for event in possible_returns:
        targets = [
            activation for activation in state.activations.values()
            if activation.created_by_input_id == event.input_id
        ]
        if len(targets) != 1:
            return False
        if recorded_source_return_origin(
            state, plan, targets[0].activation_id,
            source_run_id=attempt.source_run_id,
        ) is not None:
            returns.append(event)
        elif recorded_source_return_origin(
            state, plan, targets[0].activation_id
        ) is None:
            return False
    if not returns:
        return attempt.latest_return_action_id is None
    latest = max(returns, key=lambda event: indices[event.input_id])
    targets = [
        activation for activation in state.activations.values()
        if activation.created_by_input_id == latest.input_id
    ]
    if len(targets) != 1 or attempt.latest_return_action_id != latest.action_id:
        return False
    target = targets[0]
    if recorded_source_return_origin(
        state, plan, target.activation_id, source_run_id=attempt.source_run_id
    ) is None:
        return False
    return target.stage_kind_id != policy.recovery_stage_kind_id or (
        attempt.latest_recovery_activation_id == target.activation_id
        and attempt.latest_recovery_run_id == target.claimed_by_run_id
    )


def recovery_source_policies(
    plan: SelectedCompiledPlan,
    action_id: ActionId,
) -> tuple[RecoveryPolicyDeclaration, ...]:
    """Use the same explicit or derived source-action ownership as claim admission."""
    return tuple(
        policy
        for policy in plan.recovery_policies
        if action_id in policy.source_recovery_action_ids
        or any(
            counter.threshold_action_id == action_id
            and counter.increment_action_id in policy.source_recovery_action_ids
            for counter in plan.counters
        )
    )


def recovery_attempt_count_at_input(
    state: RuntimeState,
    attempt: RecoveryAttemptRecord,
    policy: RecoveryPolicyDeclaration,
    input_id: str,
) -> int | None:
    """Recover a historical count from the accepted source-action event prefix."""
    admitted = state.admitted_plans.get(attempt.plan_ref.authority_fingerprint)
    if admitted is None or admitted.plan_ref != attempt.plan_ref:
        return None
    plan = admitted.selected_plan
    indices = {transition.input_id: i for i, transition in enumerate(state.transitions)}
    start = indices.get(attempt.created_by_input_id)
    end = indices.get(input_id)
    if start is None or end is None or end < start:
        return None
    inputs = {
        transition.input_id
        for transition in state.transitions[start : end + 1]
        if transition.accepted and transition.input_kind != "workflow.timer_due"
    }
    return sum(
        event.input_id in inputs
        and event.disposition == "accepted"
        and event.authority_source == "terminal_action"
        and event.action_id is not None
        and policy in recovery_source_policies(plan, event.action_id)
        and event.plan_fingerprint == attempt.plan_ref.authority_fingerprint
        and (work := state.work_items.get(event.work_item_id or "")) is not None
        and work.ref.plan_ref == attempt.plan_ref
        and work.lineage_id == attempt.lineage_id
        for event in state.governance_events
    )


__all__ = (
    "recovery_attempt_history_matches",
    "selected_activation_targets",
    "claimed_activation_matches",
    "accepted_recovery_origin_matches",
    "recorded_source_return_origin",
    "current_recovery_return_matches",
    "recovery_source_policies",
    "recovery_attempt_count_at_input",
)
