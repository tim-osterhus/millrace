from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from millrace.contracts.state import (
    PlanRef,
    RunnerSessionCompletionRecord,
    RuntimeState,
)
from millrace.contracts.transition import (
    AdmitPlan,
    CancelQueuedLineage,
    CancelQueuedWork,
    ClaimWork,
    RecordRunnerSessionCompletion,
    SelectDefaultPlan,
)
from millrace.kernel import StateConcurrencyError, apply
from millrace.testing import decide_with_fake_runner_completion as decide
from millrace.testing import fake_runner_session_state
from substrate._runtime_store_support import persist_and_load_runtime_state
from support.generic_lifecycle import (
    apply_accepted_input,
    compile_lifecycle,
    context,
    origin_queued_state,
    source_with_version,
)


def _close_work_item(state: RuntimeState, work_item_id: str) -> RuntimeState:
    assert state.default_plan_ref is not None
    transition_input = CancelQueuedWork(
        f"cancel-{work_item_id}",
        work_item_id=work_item_id,
        plan_fingerprint=state.default_plan_ref.authority_fingerprint,
        actor_id="local_operator",
        reason="test fixture closes selected queued work",
    )
    decision = decide(
        state,
        transition_input,
        context(transition_input.input_id),
    )
    assert decision.accepted is True
    return apply(state, decision)


def _replace_work_item_plan_ref(
    state: RuntimeState,
    work_item_id: str,
) -> RuntimeState:
    work_item = state.work_items[work_item_id]
    drifted_ref = PlanRef(
        plan_id=work_item.ref.plan_ref.plan_id,
        authority_fingerprint=f"drifted-{work_item.ref.plan_ref.authority_fingerprint}",
        plan_format_version=work_item.ref.plan_ref.plan_format_version,
    )
    return replace(
        state,
        work_items={
            **state.work_items,
            work_item_id: replace(
                work_item,
                ref=replace(work_item.ref, plan_ref=drifted_ref),
            ),
        },
    )


def _two_item_lineage_state() -> tuple[RuntimeState, str]:
    state, _plan, fingerprint = origin_queued_state()
    root = state.work_items["work-origin"]
    root_activation = state.activations["activation-origin"]
    child = replace(
        root,
        ref=replace(root.ref, work_item_id="work-origin-child"),
        created_by_input_id="create-origin-child",
    )
    child_activation = replace(
        root_activation,
        activation_id="activation-origin-child",
        work_item_id=child.ref.work_item_id,
        created_by_input_id="create-origin-child",
    )
    return (
        replace(
            state,
            work_items={**state.work_items, child.ref.work_item_id: child},
            activations={
                **state.activations,
                child_activation.activation_id: child_activation,
            },
        ),
        fingerprint,
    )


def test_queue_lineage_closure_preflights_and_closes_complete_membership() -> None:
    state, fingerprint = _two_item_lineage_state()
    transition_input = CancelQueuedLineage(
        "cancel-complete-lineage",
        lineage_id="work-origin",
        plan_fingerprint=fingerprint,
        actor_id="local_operator",
        reason="queued input was superseded",
    )

    decision = decide(state, transition_input, context("cancel-complete-lineage"))

    assert decision.accepted is True
    assert (
        sum(
            mutation.mutation_kind == "mutation.close_work_item"
            for mutation in decision.mutations
        )
        == 2
    )
    next_state = apply(state, decision)
    audit = next(iter(next_state.queue_closures.values()))
    assert audit.target_kind == "lineage"
    assert audit.closed_work_item_ids == ("work-origin", "work-origin-child")
    assert audit.closed_activation_ids == (
        "activation-origin",
        "activation-origin-child",
    )
    assert set(next_state.closed_work_items) == {
        "work-origin",
        "work-origin-child",
    }

    child = state.work_items["work-origin-child"]
    late_member = replace(
        child,
        ref=replace(child.ref, work_item_id="work-origin-late"),
        created_by_input_id="create-origin-late",
    )
    changed_membership = replace(
        state,
        work_items={**state.work_items, late_member.ref.work_item_id: late_member},
    )
    with pytest.raises(StateConcurrencyError, match="lineage membership changed"):
        apply(changed_membership, decision)


def test_queue_lineage_closure_refuses_partial_or_wrong_plan_membership() -> None:
    state, fingerprint = _two_item_lineage_state()
    partially_closed = _close_work_item(state, "work-origin-child")
    transition_input = CancelQueuedLineage(
        "cancel-partial-lineage",
        lineage_id="work-origin",
        plan_fingerprint=fingerprint,
        actor_id="local_operator",
        reason="must close the whole lineage or nothing",
    )

    decision = decide(
        partially_closed,
        transition_input,
        context("cancel-partial-lineage"),
    )

    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == "queued_work_not_cancelable"
    refused = apply(partially_closed, decision)
    assert refused.queue_closures == partially_closed.queue_closures
    assert refused.closed_work_items == partially_closed.closed_work_items

    child = state.work_items["work-origin-child"]
    wrong_plan = replace(
        state,
        work_items={
            **state.work_items,
            child.ref.work_item_id: replace(
                child,
                ref=replace(
                    child.ref,
                    plan_ref=PlanRef(
                        plan_id="neutral-wrong-plan",
                        authority_fingerprint=f"sha256:{'f' * 64}",
                        plan_format_version=child.ref.plan_ref.plan_format_version,
                    ),
                ),
            ),
        },
    )
    wrong_plan_decision = decide(
        wrong_plan,
        replace(transition_input, input_id="cancel-wrong-plan-lineage"),
        context("cancel-wrong-plan-lineage"),
    )
    assert wrong_plan_decision.accepted is False
    assert wrong_plan_decision.refusal is not None
    assert wrong_plan_decision.refusal.reason == "queued_lineage_plan_mismatch"


def test_queue_closure_fences_claim_races() -> None:
    state, _plan, fingerprint = origin_queued_state()
    transition_input = CancelQueuedWork(
        "cancel-racing-work",
        work_item_id="work-origin",
        plan_fingerprint=fingerprint,
        actor_id="local_operator",
        reason="cancel only while safely queued",
    )
    stale_decision = decide(state, transition_input, context("cancel-racing-work"))
    claim = decide(
        state,
        ClaimWork("claim-before-cancel-apply", activation_id="activation-origin"),
        context("claim-before-cancel-apply", run_id="run-origin"),
    )
    claimed = apply(state, claim)
    with pytest.raises(StateConcurrencyError, match="activation .* changed"):
        apply(claimed, stale_decision)

    claimed_decision = decide(
        claimed,
        replace(transition_input, input_id="cancel-claimed-work"),
        context("cancel-claimed-work"),
    )
    assert claimed_decision.accepted is False
    assert claimed_decision.refusal is not None
    assert claimed_decision.refusal.reason == "queued_work_claimed"


def test_queue_closure_apply_refuses_a_stale_selected_plan_and_replays() -> None:
    state, _plan_a, fingerprint_a = origin_queued_state()
    transition_input = CancelQueuedWork(
        "cancel-under-plan-a",
        work_item_id="work-origin",
        plan_fingerprint=fingerprint_a,
        actor_id="local_operator",
        reason="selected authority must remain current through apply",
    )
    stale_decision = decide(state, transition_input, context("cancel-under-plan-a"))
    assert stale_decision.accepted is True

    plan_b, fingerprint_b = compile_lifecycle(source_with_version("0.2"))
    changed_default = apply_accepted_input(
        state,
        AdmitPlan(
            "admit-plan-b",
            selected_plan=plan_b,
            authority_fingerprint=fingerprint_b,
        ),
        context("admit-plan-b"),
    )
    changed_default = apply_accepted_input(
        changed_default,
        SelectDefaultPlan("select-plan-b", authority_fingerprint=fingerprint_b),
        context("select-plan-b"),
    )
    before_stale_apply = changed_default

    with pytest.raises(StateConcurrencyError, match="default plan fingerprint changed"):
        apply(changed_default, stale_decision)
    assert changed_default == before_stale_apply
    assert changed_default.queue_closures == {}
    assert changed_default.closed_work_items == {}

    persisted = apply(state, stale_decision)
    replay_state = apply_accepted_input(
        persisted,
        AdmitPlan(
            "admit-plan-b-after-close",
            selected_plan=plan_b,
            authority_fingerprint=fingerprint_b,
        ),
        context("admit-plan-b-after-close"),
    )
    replay_state = apply_accepted_input(
        replay_state,
        SelectDefaultPlan(
            "select-plan-b-after-close",
            authority_fingerprint=fingerprint_b,
        ),
        context("select-plan-b-after-close"),
    )
    replay = decide(replay_state, transition_input, context("cancel-under-plan-a"))
    assert replay.disposition == "replayed"
    assert replay.mutations == ()
    assert apply(replay_state, replay) == replay_state


def test_queue_closure_refusals_preserve_queue_closure_state() -> None:
    state, _plan, fingerprint = origin_queued_state()

    refusal_cases = (
        (
            CancelQueuedWork(
                "cancel-absent-work",
                work_item_id="missing-work",
                plan_fingerprint=fingerprint,
                actor_id="local_operator",
                reason="target is absent",
            ),
            state,
            "queued_work_not_found",
        ),
        (
            CancelQueuedLineage(
                "cancel-absent-lineage",
                lineage_id="missing-lineage",
                plan_fingerprint=fingerprint,
                actor_id="local_operator",
                reason="lineage is absent",
            ),
            state,
            "queued_lineage_not_found",
        ),
        (
            CancelQueuedWork(
                "cancel-selected-plan-mismatch",
                work_item_id="work-origin",
                plan_fingerprint=f"sha256:{'0' * 64}",
                actor_id="local_operator",
                reason="wrong selected plan",
            ),
            state,
            "selected_plan_mismatch",
        ),
        (
            CancelQueuedWork(
                "cancel-work-plan-mismatch",
                work_item_id="work-origin",
                plan_fingerprint=fingerprint,
                actor_id="local_operator",
                reason="work belongs to another plan",
            ),
            _replace_work_item_plan_ref(state, "work-origin"),
            "queued_work_plan_mismatch",
        ),
        (
            CancelQueuedWork(
                "cancel-already-closed",
                work_item_id="work-origin",
                plan_fingerprint=fingerprint,
                actor_id="local_operator",
                reason="work is already closed",
            ),
            _close_work_item(state, "work-origin"),
            "queued_work_not_cancelable",
        ),
    )
    for transition_input, current_state, expected_reason in refusal_cases:
        before = current_state
        decision = decide(
            current_state,
            transition_input,
            context(transition_input.input_id),
        )
        assert decision.accepted is False
        assert decision.refusal is not None
        assert decision.refusal.reason == expected_reason
        after = apply(current_state, decision)
        assert after.queue_closures == before.queue_closures
        assert after.closed_work_items == before.closed_work_items

    state, fingerprint = _two_item_lineage_state()
    original_input = CancelQueuedWork(
        "cancel-conflicting-input",
        work_item_id="work-origin",
        plan_fingerprint=fingerprint,
        actor_id="local_operator",
        reason="original queue closure",
    )
    closed = apply(
        state,
        decide(state, original_input, context("cancel-conflicting-input")),
    )
    for conflicting_input in (
        replace(original_input, work_item_id="work-origin-child"),
        replace(original_input, plan_fingerprint=f"sha256:{'1' * 64}"),
        replace(original_input, actor_id="other-operator"),
        replace(original_input, reason="different reason"),
    ):
        decision = decide(
            closed,
            conflicting_input,
            context("cancel-conflicting-input"),
        )
        assert decision.accepted is False
        assert decision.refusal is not None
        assert decision.refusal.reason == "idempotency_conflict"
        assert closed.queue_closures
        assert set(closed.closed_work_items) == {"work-origin"}


def test_queue_closure_refuses_every_unsafe_session_aftermath(
    tmp_path: Path,
) -> None:
    state, _plan, fingerprint = origin_queued_state()
    claimed = apply(
        state,
        decide(
            state,
            ClaimWork("claim-for-session-aftermath", activation_id="activation-origin"),
            context("claim-for-session-aftermath", run_id="run-origin"),
        ),
    )
    run_id = next(iter(claimed.runs))
    with_session = fake_runner_session_state(state=claimed, run_id=run_id)
    session_id = with_session.runs[run_id].current_session_id
    assert session_id is not None
    session = with_session.runner_sessions[session_id]

    cases = (
        ("created", None, None, None, "pending", "queued_runner_session_live"),
        ("starting", 1, None, None, "not_required", "queued_runner_session_live"),
        ("running", 1, 2, None, "not_required", "queued_runner_session_live"),
        (
            "cancellation_requested",
            1,
            2,
            None,
            "not_required",
            "queued_runner_session_live",
        ),
        ("terminating", 1, 2, None, "not_required", "queued_runner_session_live"),
        (
            "cancellation_requested",
            1,
            2,
            None,
            "orphan_risk",
            "queued_runner_session_live",
        ),
        ("lost", 1, 2, 3, "orphan_risk", "queued_runner_session_lost"),
    )
    for index, (
        session_state,
        start_intent_at,
        started_at,
        ended_at,
        cleanup_disposition,
        expected_reason,
    ) in enumerate(cases):
        current_state = replace(
            with_session,
            runner_sessions={
                session_id: replace(
                    session,
                    state=session_state,
                    start_intent_at=start_intent_at,
                    started_at=started_at,
                    ended_at=ended_at,
                    cleanup_disposition=cleanup_disposition,
                )
            },
        )
        transition_input = CancelQueuedWork(
            f"cancel-unsafe-session-{index}",
            work_item_id="work-origin",
            plan_fingerprint=fingerprint,
            actor_id="local_operator",
            reason="unsafe runner aftermath must block closure",
        )
        decision = decide(
            current_state,
            transition_input,
            context(transition_input.input_id),
        )
        assert decision.accepted is False
        assert decision.refusal is not None
        assert decision.refusal.reason == expected_reason

    completion = RunnerSessionCompletionRecord(
        completion_id="clean-terminal-completion",
        session_id=session_id,
        run_id=run_id,
        dispatch_generation=session.dispatch_generation,
        session_fencing_token=session.session_fencing_token,
        terminal_state="failed",
        exit_kind="error",
        adapter_outcome_kind=None,
        adapter_error_kind="invocation_failed",
        runner_result_evidence_digest=None,
        primary_cancellation_request_id=None,
        cleanup_disposition="complete",
        started_at=None,
        cancel_requested_at=None,
        completed_at=3,
        bounds_summary="clean terminal completion",
        truncation_metadata="none",
        redaction_policy_id="redaction.default",
        diagnostic_digest=f"sha256:{'a' * 64}",
        application_input_id="cli:run.session-completion:clean-terminal-completion",
    )
    completed = apply(
        with_session,
        decide(
            with_session,
            RecordRunnerSessionCompletion(
                "record-clean-terminal-completion",
                run_ref=with_session.runs[run_id].run_ref,
                expected_state="created",
                completion=completion,
            ),
            context("record-clean-terminal-completion"),
        ),
    )
    reloaded = persist_and_load_runtime_state(tmp_path, completed)
    close_after_reload = decide(
        reloaded,
        CancelQueuedWork(
            "cancel-clean-terminal-session",
            work_item_id="work-origin",
            plan_fingerprint=fingerprint,
            actor_id="local_operator",
            reason="explicit closure remains legal after clean completion",
        ),
        context("cancel-clean-terminal-session"),
    )
    assert close_after_reload.accepted is True
    assert "work-origin" in apply(reloaded, close_after_reload).closed_work_items


@pytest.mark.parametrize('marker', ['REVIEW_PASSED', 'REVIEW_GAP', 'REVIEW_BLOCKED'])
def test_pi_capacity_completion_closure_and_reload(tmp_path, monkeypatch, marker):
    from millrace.compiler import compile_workflow
    from millrace.contracts.compiled_plan import authority_fingerprint
    from substrate._runtime_store_support import persist_and_load_runtime_state
    from support import generic_lifecycle
    from tests.compiler.test_completion_capacity import capacity_source
    from tests.operator.test_status_projection import _closure_verdict_payload

    def compile_pi(source):
        result = compile_workflow(source)
        assert result.plan is not None, result.diagnostics
        return result.plan, authority_fingerprint(result.plan)

    monkeypatch.setattr(generic_lifecycle, 'compile_lifecycle', compile_pi)
    state, plan, fingerprint = generic_lifecycle.closure_evaluation_state(
        capacity_source()
    )
    (tmp_path / 'before').mkdir()
    (tmp_path / 'after').mkdir()
    state = persist_and_load_runtime_state(tmp_path / 'before', state)
    evaluation = next(iter(state.closure_evaluations.values()))
    state = generic_lifecycle.claim_activation(
        state, activation_id=evaluation.target_activation_id, suffix="evaluator"
    )
    work = state.work_items[evaluation.target_work_item_id]
    state = generic_lifecycle.apply_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-evaluator",
        input_id="observe-evaluator",
        marker=marker,
        artifact_payload=_closure_verdict_payload(
            work.payload["closure_evidence_snapshot"], marker=marker
        ),
    )
    loaded = persist_and_load_runtime_state(tmp_path / 'after', state)
    assert loaded.closure_targets == state.closure_targets
    assert loaded.admitted_plans[fingerprint].selected_plan == plan


def test_pi_capacity_drift_refuses_closure_without_mutations(monkeypatch):
    from millrace.compiler import compile_workflow
    from millrace.contracts.compiled_plan import authority_fingerprint
    from millrace.kernel.lifecycle import project_next_lifecycle_transition
    from support import generic_lifecycle
    from tests.compiler.test_completion_capacity import capacity_source

    def compile_pi(source):
        plan = compile_workflow(source).plan
        return plan, authority_fingerprint(plan)

    monkeypatch.setattr(generic_lifecycle, 'compile_lifecycle', compile_pi)
    state, plan, fingerprint = generic_lifecycle.closure_opened_state(capacity_source())
    transition = project_next_lifecycle_transition(state).candidate.transition_input
    assert transition is not None
    object.__setattr__(
        plan.runner_bindings[0].payload_capacity_pin, "descriptor_sha256", "0" * 64
    )
    decision = decide(state, transition, context(transition.input_id))
    assert not decision.accepted
    refused = apply(state, decision)
    assert refused.work_items == state.work_items
    assert refused.artifacts == state.artifacts
    assert refused.closure_evaluations == state.closure_evaluations
    assert not state.closure_evaluations


def test_pi_accepted_completion_request_replays_after_reload(tmp_path, monkeypatch):
    from millrace.compiler import compile_workflow
    from millrace.contracts import runner_payload_capacity as capacity
    from millrace.contracts.compiled_plan import authority_fingerprint
    from millrace.kernel.lifecycle import project_next_lifecycle_transition
    from support import generic_lifecycle
    from tests.compiler.test_completion_capacity import capacity_source

    def compile_pi(source):
        plan = compile_workflow(source).plan
        return plan, authority_fingerprint(plan)

    monkeypatch.setattr(generic_lifecycle, 'compile_lifecycle', compile_pi)
    state, _, _ = generic_lifecycle.closure_opened_state(capacity_source())
    candidate = project_next_lifecycle_transition(state).candidate
    transition = candidate.transition_input
    decision = decide(state, transition, candidate.transition_context)
    assert decision.accepted
    state = persist_and_load_runtime_state(tmp_path, apply(state, decision))
    # Old accepted evidence replays without recompiling or running another adapter.
    monkeypatch.setattr(capacity, 'files', lambda _: tmp_path)
    replay = decide(state, transition, candidate.transition_context)
    assert replay.disposition == 'replayed'
    assert replay.mutations == ()
    assert apply(state, replay) == state

    stored = next(iter(state.admitted_plans.values())).selected_plan
    object.__setattr__(stored.runner_bindings[0].payload_capacity_pin,
                       "descriptor_sha256", "0" * 64)
    tampered = decide(state, transition, candidate.transition_context)
    assert not tampered.accepted
    assert tampered.refusal.reason == "closure_replay_relations_invalid"
    assert len(state.closure_evaluations) == 1
