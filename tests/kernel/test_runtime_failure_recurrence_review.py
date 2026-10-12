from __future__ import annotations

from copy import deepcopy
from dataclasses import replace

import pytest

from millrace.compiler.canonical import authority_fingerprint
from millrace.contracts.ids import ActionId, QueueFamilyId
from millrace.contracts.runtime_failures import (
    runtime_failure_applied,
    runtime_failure_input,
    runtime_failure_mapping_refusal,
)
from millrace.contracts.transition import (
    ClaimWork,
    OperatorCloseLineage,
    OperatorResumeLineage,
    RunnerResultObserved,
    TimerDue,
)
from millrace.kernel import apply, decide
from millrace.operator.dispatch import list_ready_dispatch_candidates
from millrace.substrate._sqlite_relations import validate_loaded_runtime_state
from millrace.testing import (
    decide_with_fake_runner_completion,
    deterministic_context,
    fake_runner_observation_payload,
)
from support import generic_admission
from tests.compiler.test_runtime_failure_mappings import _compile, _mapped_source
from tests.kernel.test_runtime_failure_adversarial_review import (
    _apply_failure,
    _claim,
    _timeout_for_run,
)
from tests.substrate.test_persistence_integrity_refusals import (
    _apply_accepted_input,
    _apply_generic_observation,
    _generic_parent_claimed_state,
)

_RECUR_ACTION = "admission.recovery_timeout"
_RECUR_OUTCOME = "admission.recovery.timeout"


def _recurrence_source():
    source = deepcopy(_mapped_source())
    stage = next(
        s
        for s in source["stage_kinds"]
        if s["id"] == generic_admission.RECOVERY_STAGE_ID
    )
    # A recovery route retains parent work even though normal input is child.
    stage["input_queue_family_ids"] = ("child",)
    stage["declared_outcome_ids"] = (*stage["declared_outcome_ids"], _RECUR_OUTCOME)
    source["terminal_outcomes"].append(
        {"id": _RECUR_OUTCOME, "stage_kind_id": stage["id"], "marker": "RECUR_TIMEOUT"}
    )
    action = deepcopy(
        next(
            a
            for a in source["terminal_actions"]
            if a["id"] == generic_admission.RECOVERY_SOURCE_ACTION_ID
        )
    )
    action.update(
        id=_RECUR_ACTION, stage_kind_id=stage["id"], outcome_id=_RECUR_OUTCOME
    )
    source["terminal_actions"].append(action)
    policy = source["recovery_policies"][0]
    policy["source_recovery_action_ids"] = (
        *policy["source_recovery_action_ids"],
        _RECUR_ACTION,
    )
    source["runner_bindings"][0]["runtime_failure_mappings"].append(
        {
            "stage_kind_id": stage["id"],
            "failure_kind": "timeout",
            "action_id": _RECUR_ACTION,
        }
    )
    threshold_outcome = "admission.recovery.domain_exhausted"
    threshold_action = "admission.recovery.domain_close"
    stage["declared_outcome_ids"] = (*stage["declared_outcome_ids"], threshold_outcome)
    source["terminal_outcomes"].append(
        {
            "id": threshold_outcome,
            "stage_kind_id": stage["id"],
            "marker": "DOMAIN_EXHAUSTED",
        }
    )
    source["terminal_actions"].append(
        {
            "id": threshold_action,
            "stage_kind_id": stage["id"],
            "outcome_id": threshold_outcome,
            "kind": "complete_work_item",
        }
    )
    source["counters"].append(
        {
            "id": "admission.recovery.domain_counter",
            "kind": "lineage_terminal_action_counter",
            "scope": "lineage",
            "stage_kind_id": stage["id"],
            "increment_action_id": _RECUR_ACTION,
            "threshold_action_id": threshold_action,
            "threshold_count": 2,
        }
    )
    return source


def _first_recovery_failed(monkeypatch, source=None):
    compiled = _compile(_recurrence_source() if source is None else source)
    assert compiled.plan is not None, compiled.diagnostics
    plan = compiled.plan
    assert runtime_failure_mapping_refusal(plan) is None
    monkeypatch.setattr(
        generic_admission, "compile_plan", lambda: (plan, authority_fingerprint(plan))
    )
    state, _, _ = _generic_parent_claimed_state()
    state, first = _timeout_for_run(state, "run-generic-parent", 2000)
    state, first_command, first_context = _apply_failure(state, first, suffix="origin")
    state = _claim(state, "review-recovery-origin", "review-inherited-recovery")
    state, second = _timeout_for_run(state, "review-inherited-recovery", 3000)
    run = state.runs[second.run_id]
    work = state.work_items[run.work_item_id]
    recovery = next(s for s in plan.stage_kinds if s.id == run.stage_kind_id)
    assert work.queue_family_id not in recovery.input_queue_family_ids
    return state, first, first_command, first_context, second


def _quarantined_recurrence(monkeypatch):
    state, first, first_command, first_context, second = _first_recovery_failed(
        monkeypatch
    )
    state, second_command, second_context = _apply_failure(
        state, second, suffix="recurrence"
    )
    attempt = next(iter(state.recovery_attempts.values()))
    assert (attempt.attempt_count, attempt.phase) == (2, "pending_cooldown")
    wait = next(iter(state.cooldown_waits.values()))
    early = decide(
        state,
        TimerDue(
            "review-recur-early", wait_id=wait.wait_id, observed_at=wait.due_at - 1
        ),
        deterministic_context(transition_id="review-recur-early-transition"),
    )
    assert not early.accepted
    timer = TimerDue("review-recur-due", wait_id=wait.wait_id, observed_at=wait.due_at)
    timer_context = deterministic_context(
        transition_id="review-recur-due-transition",
        activation_id="review-recur-after-timer",
    )
    decision = decide(state, timer, timer_context)
    assert decision.accepted, decision.refusal
    state = apply(state, decision)
    state = _claim(state, "review-recur-after-timer", "review-recur-third-run")
    state, third = _timeout_for_run(state, "review-recur-third-run", wait.due_at + 1)
    state, third_command, third_context = _apply_failure(
        state, third, suffix="exhaustion"
    )
    attempt = next(iter(state.recovery_attempts.values()))
    assert (attempt.attempt_count, attempt.phase) == (3, "quarantine_eligible")
    assert len(state.lineage_quarantines) == 1
    assert not list_ready_dispatch_candidates(state).candidates
    for completion, command, context in (
        (first, first_command, first_context),
        (second, second_command, second_context),
        (third, third_command, third_context),
    ):
        assert runtime_failure_applied(state, completion.run_id)
        replay = decide(state, command, context)
        assert replay.accepted and replay.disposition == "replayed"
        assert apply(state, replay) == state
    assert state.runner_observations == {}
    assert state.artifacts == {}
    # Mechanical failures own policy attempts; agent-domain counters stay intact.
    assert state.counters == {}
    validate_loaded_runtime_state(state)
    return state, (
        (first, first_command, first_context),
        (second, second_command, second_context),
        (third, third_command, third_context),
    )


def test_inherited_recovery_timeout_reaches_cooldown_timer_quarantine_and_replays(
    monkeypatch,
):
    _quarantined_recurrence(monkeypatch)


@pytest.mark.parametrize("kind", ["resume", "close"])
def test_recurrence_intervention_preserves_historical_failure_proof(monkeypatch, kind):
    state, failures = _quarantined_recurrence(monkeypatch)
    quarantine = next(iter(state.lineage_quarantines.values()))
    constructor = OperatorResumeLineage if kind == "resume" else OperatorCloseLineage
    command = constructor(
        f"review-recurrence-{kind}",
        option_id=f"admission.{kind}",
        selected_plan_ref=quarantine.selected_plan_ref,
        quarantine_id=quarantine.quarantine_id,
        lineage_id=quarantine.lineage_id,
        actor_id="local-operator",
        actor_kind="local_operator",
        reason=f"review recurrence {kind}",
        payload={},
    )
    state = _apply_accepted_input(
        state,
        command,
        deterministic_context(
            transition_id=f"review-recurrence-{kind}-transition",
            activation_id=f"review-recurrence-{kind}-activation",
        ),
    )
    for completion, applied_command, context in failures:
        assert runtime_failure_applied(state, completion.run_id)
        replay = decide(state, applied_command, context)
        assert replay.accepted and replay.disposition == "replayed"
        assert apply(state, replay) == state
    validate_loaded_runtime_state(state)
    if kind == "resume":
        # Existing ClaimWork cannot resume an inherited recovery-stage source
        # once the attempt is resolved. This is an explicit separate gate.
        claim = decide(
            state,
            ClaimWork(
                "review-resume-recurrence-claim",
                activation_id="review-recurrence-resume-activation",
            ),
            deterministic_context(
                transition_id="review-resume-recurrence-claim-transition"
            ),
        )
        assert not claim.accepted
        assert claim.refusal.reason == "unsupported_selected_authority"


@pytest.mark.parametrize(
    "field,value",
    [
        ("record_id", "forged-route"),
        ("action_id", ActionId(generic_admission.ALTERNATE_RECOVERY_SOURCE_ACTION_ID)),
        ("source_run_id", "review-inherited-recovery"),
        ("source_work_item_id", "wrong-source-work"),
        ("target_work_item_id", "wrong-target-work"),
        ("created_by_input_id", "generic-claim"),
    ],
)
def test_inherited_timeout_rejects_forged_origin_route(monkeypatch, field, value):
    state, _, _, _, completion = _first_recovery_failed(monkeypatch)
    route = state.activation_routes[0]
    corrupted = replace(state, activation_routes=(replace(route, **{field: value}),))
    decision = decide(
        corrupted,
        runtime_failure_input(corrupted, completion),
        deterministic_context(transition_id="review-corrupt-origin-route"),
    )
    assert not decision.accepted
    assert decision.refusal.reason == "runtime_failure_authority_mismatch"
    assert decision.receipt_ref is None


@pytest.mark.parametrize(
    "part",
    [
        "missing_route",
        "duplicate_route",
        "receipt",
        "transition",
        "event",
        "trace",
        "origin_claim",
    ],
)
def test_inherited_timeout_requires_unique_accepted_origin_and_claim(monkeypatch, part):
    state, first, _, _, completion = _first_recovery_failed(monkeypatch)
    origin = first.application_input_id
    if part == "missing_route":
        corrupted = replace(state, activation_routes=())
    elif part == "duplicate_route":
        corrupted = replace(
            state,
            activation_routes=(*state.activation_routes, state.activation_routes[0]),
        )
    elif part in {"receipt", "origin_claim"}:
        input_id = (
            origin
            if part == "receipt"
            else state.runs[first.run_id].created_by_input_id
        )
        corrupted = replace(
            state, receipts={k: v for k, v in state.receipts.items() if k != input_id}
        )
    elif part == "transition":
        corrupted = replace(
            state,
            transitions=tuple(t for t in state.transitions if t.input_id != origin),
        )
    else:
        field = "governance_events" if part == "event" else "traces"
        corrupted = replace(
            state,
            **{field: tuple(r for r in getattr(state, field) if r.input_id != origin)},
        )
    decision = decide(
        corrupted,
        runtime_failure_input(corrupted, completion),
        deterministic_context(transition_id="review-corrupt-origin-proof"),
    )
    assert not decision.accepted
    assert decision.refusal.reason == "runtime_failure_authority_mismatch"
    assert decision.receipt_ref is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("plan_ref", "wrong_plan"),
        ("lineage_id", "wrong-lineage"),
        ("stage_kind_id", "wrong_stage"),
        ("graph_node_id", "admission.child.start"),
        ("runner_binding_id", "wrong_runner"),
    ],
)
def test_inherited_timeout_rejects_coherent_foreign_source_origin(
    monkeypatch, field, value
):
    state, first, _, _, completion = _first_recovery_failed(monkeypatch)
    source_run = state.runs[first.run_id]
    source_activation = state.activations[source_run.activation_id]
    if field == "plan_ref":
        value = replace(source_activation.plan_ref, plan_id="review-wrong-origin-plan")
        source_run = replace(
            source_run, run_ref=replace(source_run.run_ref, plan_ref=value)
        )
    elif field in {"stage_kind_id", "runner_binding_id"}:
        value = type(getattr(source_activation, field))(value)
        source_run = replace(source_run, **{field: value})
    source_activation = replace(source_activation, **{field: value})
    corrupted = replace(
        state,
        runs={**state.runs, first.run_id: source_run},
        activations={
            **state.activations,
            source_activation.activation_id: source_activation,
        },
    )
    decision = decide(
        corrupted,
        runtime_failure_input(corrupted, completion),
        deterministic_context(transition_id="review-corrupt-origin-selected"),
    )
    assert not decision.accepted
    assert decision.refusal.reason == "runtime_failure_authority_mismatch"


def _timer_recovery_failed(monkeypatch, source=None):
    state, _, _, _, second = _first_recovery_failed(monkeypatch, source)
    state, _, _ = _apply_failure(state, second, suffix="timer-origin")
    wait = next(iter(state.cooldown_waits.values()))
    timer = TimerDue(
        "review-timer-origin", wait_id=wait.wait_id, observed_at=wait.due_at
    )
    context = deterministic_context(
        transition_id="review-timer-origin-transition",
        activation_id="review-timer-origin-activation",
    )
    decision = decide(state, timer, context)
    assert decision.accepted, decision.refusal
    state = apply(state, decision)
    state = _claim(state, context.activation_id, "review-timer-origin-run")
    state, third = _timeout_for_run(state, "review-timer-origin-run", wait.due_at + 1)
    return state, third, state.cooldown_waits[wait.wait_id]


@pytest.mark.parametrize(
    "updates",
    [
        {"consumed_input_id": "generic-claim"},
        {"consumed_at": 0},
        {"resulting_recovery_activation_id": "review-recovery-origin"},
        {"created_input_id": "generic-claim"},
        {"source_run_id": "run-generic-parent"},
        {"source_activation_id": "activation-generic-parent"},
        {"source_work_item_id": "foreign-work"},
        {"recovery_action_id": ActionId(generic_admission.RECOVERY_SOURCE_ACTION_ID)},
        {"target_graph_node_id": generic_admission.CHILD_NODE_ID},
        {"attempt_count": 999},
        {"recovery_attempt_record_id": "foreign-attempt"},
    ],
)
def test_recurrent_timeout_authenticates_consumed_timer_origin(monkeypatch, updates):
    state, completion, wait = _timer_recovery_failed(monkeypatch)
    corrupted = replace(state, cooldown_waits={wait.wait_id: replace(wait, **updates)})
    decision = decide(
        corrupted,
        runtime_failure_input(corrupted, completion),
        deterministic_context(transition_id="review-corrupt-timer-origin"),
    )
    assert not decision.accepted
    assert decision.refusal.reason == "runtime_failure_authority_mismatch"
    assert decision.receipt_ref is None


def test_historical_timer_count_cannot_be_rewritten_to_later_quarantine_count(
    monkeypatch,
):
    state, failures = _quarantined_recurrence(monkeypatch)
    wait = next(iter(state.cooldown_waits.values()))
    assert wait.attempt_count == 2
    attempt = next(iter(state.recovery_attempts.values()))
    assert attempt.attempt_count == 3
    corrupted = replace(
        state, cooldown_waits={wait.wait_id: replace(wait, attempt_count=3)}
    )
    completion, command, context = failures[-1]
    assert not runtime_failure_applied(corrupted, completion.run_id)
    replay = decide(corrupted, command, context)
    assert not replay.accepted


def test_historical_timer_creation_must_precede_accepted_consumption(monkeypatch):
    state, failures = _quarantined_recurrence(monkeypatch)
    wait = next(iter(state.cooldown_waits.values()))
    transitions = list(state.transitions)
    creation = next(
        i for i, t in enumerate(transitions) if t.input_id == wait.created_input_id
    )
    consumption = next(
        i for i, t in enumerate(transitions) if t.input_id == wait.consumed_input_id
    )
    assert creation < consumption
    transitions[creation], transitions[consumption] = (
        transitions[consumption],
        transitions[creation],
    )
    corrupted = replace(state, transitions=tuple(transitions))
    completion, command, context = failures[-1]
    assert not runtime_failure_applied(corrupted, completion.run_id)
    assert not decide(corrupted, command, context).accepted


def test_recovery_pseudo_target_cannot_create_ordinary_source_authority(monkeypatch):
    state, _, _, _, completion = _first_recovery_failed(monkeypatch)
    run = state.runs[completion.run_id]
    activation = state.activations[run.activation_id]
    work = state.work_items[run.work_item_id]
    corrupted = replace(
        state,
        work_items={
            **state.work_items,
            work.ref.work_item_id: replace(
                work, queue_family_id=QueueFamilyId("child")
            ),
        },
        activations={
            **state.activations,
            activation.activation_id: replace(
                activation, queue_family_id=QueueFamilyId("child")
            ),
        },
        activation_routes=(),
    )
    decision = decide(
        corrupted, runtime_failure_input(corrupted, completion), deterministic_context()
    )
    assert not decision.accepted
    assert decision.refusal.reason == "runtime_failure_authority_mismatch"


def test_agent_observations_still_enforce_lower_stage_counter(monkeypatch):
    compiled = _compile(_recurrence_source())
    assert compiled.plan is not None, compiled.diagnostics
    plan = compiled.plan
    fingerprint = authority_fingerprint(plan)
    monkeypatch.setattr(generic_admission, "compile_plan", lambda: (plan, fingerprint))
    state, _, _ = _generic_parent_claimed_state()
    state = _apply_generic_observation(
        state,
        plan,
        fingerprint,
        run_id="run-generic-parent",
        action_id=generic_admission.RECOVERY_SOURCE_ACTION_ID,
        input_id="review-domain-parent",
        observed_at=2000,
        context=deterministic_context(
            transition_id="review-domain-parent-transition",
            activation_id="review-domain-first",
        ),
    )
    state = _claim(state, "review-domain-first", "review-domain-first-run")
    state = _apply_generic_observation(
        state,
        plan,
        fingerprint,
        run_id="review-domain-first-run",
        action_id=_RECUR_ACTION,
        input_id="review-domain-recovery",
        observed_at=3000,
        context=deterministic_context(
            transition_id="review-domain-recovery-transition"
        ),
    )
    counter = next(
        c
        for c in state.counters.values()
        if str(c.counter_id) == "admission.recovery.domain_counter"
    )
    assert counter.value == 1
    wait = next(iter(state.cooldown_waits.values()))
    state = _apply_accepted_input(
        state,
        TimerDue("review-domain-timer", wait_id=wait.wait_id, observed_at=wait.due_at),
        deterministic_context(
            transition_id="review-domain-timer-transition",
            activation_id="review-domain-second",
        ),
    )
    state = _claim(state, "review-domain-second", "review-domain-second-run")
    run = state.runs["review-domain-second-run"]
    observation = RunnerResultObserved(
        "review-domain-threshold-observation",
        run_id=run.run_ref.run_id,
        payload=fake_runner_observation_payload(
            run=run,
            activation=state.activations[run.activation_id],
            plan_fingerprint=fingerprint,
            marker="RECUR_TIMEOUT",
            artifact_payload=None,
        ),
        observed_at=wait.due_at + 1,
    )
    decision = decide_with_fake_runner_completion(
        state, observation, deterministic_context()
    )
    assert not decision.accepted
    assert decision.refusal.reason == "counter_threshold_requires_escalation"
    assert state.counters[counter.record_id] == counter


def test_timer_origin_cannot_substitute_unmapped_selected_recovery_action(monkeypatch):
    source = _recurrence_source()
    alternate_id = "admission.recovery.unmapped_action"
    alternate_outcome = "admission.recovery.unmapped_outcome"
    stage = next(
        s
        for s in source["stage_kinds"]
        if s["id"] == generic_admission.RECOVERY_STAGE_ID
    )
    stage["declared_outcome_ids"] = (*stage["declared_outcome_ids"], alternate_outcome)
    source["terminal_outcomes"].append(
        {
            "id": alternate_outcome,
            "stage_kind_id": stage["id"],
            "marker": "UNMAPPED_AGENT_FAILURE",
        }
    )
    alternate = deepcopy(
        next(a for a in source["terminal_actions"] if a["id"] == _RECUR_ACTION)
    )
    alternate.update(id=alternate_id, outcome_id=alternate_outcome)
    source["terminal_actions"].append(alternate)
    policy = source["recovery_policies"][0]
    policy["source_recovery_action_ids"] = (
        *policy["source_recovery_action_ids"],
        alternate_id,
    )
    state, completion, wait = _timer_recovery_failed(monkeypatch, source)
    alternate_id = ActionId(alternate_id)
    attempt = next(iter(state.recovery_attempts.values()))
    corrupted = replace(
        state,
        recovery_attempts={
            attempt.record_id: replace(attempt, recovery_action_id=alternate_id)
        },
        cooldown_waits={wait.wait_id: replace(wait, recovery_action_id=alternate_id)},
        activation_routes=tuple(
            replace(route, action_id=alternate_id)
            if route.created_by_input_id == wait.consumed_input_id
            else route
            for route in state.activation_routes
        ),
        governance_events=tuple(
            replace(event, action_id=alternate_id)
            if event.input_id in {wait.created_input_id, wait.consumed_input_id}
            else event
            for event in state.governance_events
        ),
        traces=tuple(
            replace(trace, action_id=alternate_id)
            if trace.input_id in {wait.created_input_id, wait.consumed_input_id}
            else trace
            for trace in state.traces
        ),
    )
    decision = decide(
        corrupted, runtime_failure_input(corrupted, completion), deterministic_context()
    )
    assert not decision.accepted
    assert decision.refusal.reason == "runtime_failure_authority_mismatch"
