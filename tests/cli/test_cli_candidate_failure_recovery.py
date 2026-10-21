"""Candidate failures retain selected recovery and closure authority."""

from copy import deepcopy

import pytest
from tests.cli.test_cli_bounded_execution_unit import _load, _reopen_runtime, _runtime
from tests.operator.test_status_projection import _closure_verdict_payload
from tests.substrate.test_persistence_integrity_refusals import (
    _generic_parent_claimed_state,
)

from millrace.adapters.cli.run import run_bounded_execution_unit
from millrace.adapters.runner_contract import AdapterLocalConfig, AdapterSuccessResult
from millrace.contracts.runtime_failures import (
    runtime_failure_eligible,
    runtime_failure_input,
)
from millrace.contracts.transition import ClaimWork, RunnerResultObserved, TimerDue
from millrace.kernel import apply, decide
from millrace.operator.dispatch import list_ready_dispatch_candidates
from millrace.testing import (
    deterministic_context,
    fake_completed_runner_observation_state,
    fake_runner_observation_payload,
)
from support import generic_admission, generic_fanout, generic_lifecycle
from support.runner_sessions import (
    _dispatch_echo,
    _ImmediateHandle,
    _RecordingAdapter,
    _started_session,
)


def _success_config(marker, artifact):
    def start(request):
        outcome = AdapterSuccessResult.from_unredacted(
            adapter_id=request.adapter_id,
            dispatch_echo=_dispatch_echo(request),
            redaction_policy=request.redaction_policy,
            marker=marker,
            artifact_payload_candidate=artifact,
        )
        return _started_session(
            request, _ImmediateHandle(outcome, cleanup_disposition="complete")
        )

    return AdapterLocalConfig(adapters={"codex": _RecordingAdapter(start)})


def _recoverable_closure_source(*, bounded_remediation=False):
    source = generic_lifecycle.source_with_completion_behavior()
    stages = {s["id"]: s for s in source["stage_kinds"]}
    stages["review_stage"]["declared_outcome_ids"] += ("qa.review.failure",)
    stages["alpha_stage"]["declared_outcome_ids"] += ("qa.recovered", "qa.quarantine")
    source["terminal_outcomes"] += [
        {
            "id": "qa.review.failure",
            "stage_kind_id": "review_stage",
            "marker": "QA_FAILURE",
        },
        {
            "id": "qa.recovered",
            "stage_kind_id": "alpha_stage",
            "marker": "QA_RECOVERED",
        },
        {
            "id": "qa.quarantine",
            "stage_kind_id": "alpha_stage",
            "marker": "QA_QUARANTINE",
        },
    ]
    source["terminal_actions"] += [
        {
            "id": "qa.review.recover",
            "stage_kind_id": "review_stage",
            "outcome_id": "qa.review.failure",
            "kind": "recovery_route",
            "target_stage_kind_id": "alpha_stage",
            "target_graph_node_id": "lifecycle.alpha.start",
            "runner_binding_id": "lifecycle.runner",
            "asset_ids": stages["alpha_stage"]["asset_ids"],
        },
        {
            "id": "qa.return",
            "stage_kind_id": "alpha_stage",
            "outcome_id": "qa.recovered",
            "kind": "return_to_recorded_source",
        },
        {
            "id": "qa.quarantine",
            "stage_kind_id": "alpha_stage",
            "outcome_id": "qa.quarantine",
            "kind": "quarantine_lineage",
        },
    ]
    policy = deepcopy(generic_admission.source()["recovery_policies"][0])
    policy.update(
        id="qa.policy",
        source_recovery_action_ids=("qa.review.recover",),
        return_action_ids=("qa.return",),
        quarantine_action_ids=("qa.quarantine",),
        recovery_stage_kind_id="alpha_stage",
        reset_trigger_action_ids=(),
        cooldown_wait_state_id="qa.cooldown",
    )
    source["recovery_policies"] = [policy]
    source["wait_states"] = [
        {
            "id": "qa.cooldown",
            "kind": "timer",
            "policy_id": "qa.policy",
            "starts_at_attempt": 2,
            "duration_seconds": 900,
        }
    ]
    source["runner_bindings"][0]["runtime_failure_mappings"] = [
        {
            "stage_kind_id": "review_stage",
            "failure_kind": "result_rejected",
            "action_id": "qa.review.recover",
        }
    ]
    # Alpha has ordinary ingress as well as formal recovery. Its component pin
    # must actually expose an ordinary option, alongside the two formal markers.
    binding = source["runner_bindings"][0]
    binding["component_pin"]["legal_terminal_result_ids"] += (
        "QA_RECOVERED",
        "QA_QUARANTINE",
    )
    binding["terminal_result_mappings"] = [
        {
            "stage_kind_id": "alpha_stage",
            "runner_result_id": result_id,
            "outcome_id": outcome_id,
        }
        for result_id, outcome_id in (
            ("COMPLETE", "lifecycle.alpha.ready"),
            ("QA_RECOVERED", "qa.recovered"),
            ("QA_QUARANTINE", "qa.quarantine"),
        )
    ]
    if bounded_remediation:
        body = next(
            s["schema"]
            for s in source["artifact_schemas"]
            if s["id"] == generic_lifecycle.SOURCE_SCHEMA_ID
        )
        body["properties"]["body"]["enum"] = ("OK",)
    return source


def _rejected_closure(case):
    source = _recoverable_closure_source(
        bounded_remediation=case == "remediation_payload"
    )
    state, plan, fingerprint = generic_lifecycle.closure_evaluation_state(source)
    evaluation = next(iter(state.closure_evaluations.values()))
    if case == "rubric":
        state = generic_lifecycle.claim_activation(
            state, activation_id=evaluation.target_activation_id, suffix="rubric-first"
        )
        work = state.work_items[evaluation.target_work_item_id]
        state = generic_lifecycle.apply_observation(
            state,
            plan=plan,
            fingerprint=fingerprint,
            run_id="run-rubric-first",
            input_id="rubric-accepted-gap",
            marker="REVIEW_GAP",
            artifact_payload=_closure_verdict_payload(
                work.payload["closure_evidence_snapshot"], marker="REVIEW_GAP"
            ),
        )
        remediation = next(iter(state.remediation_work_records.values()))
        state = generic_lifecycle.claim_activation(
            state,
            activation_id=remediation.target_activation_id,
            suffix="rubric-remediation",
        )
        state = generic_lifecycle.apply_observation(
            state,
            plan=plan,
            fingerprint=fingerprint,
            run_id="run-rubric-remediation",
            input_id="rubric-remediation-done",
            marker="ALPHA_READY",
            artifact_payload=generic_lifecycle.report_payload("alpha"),
        )
        candidate = generic_lifecycle.project_next_lifecycle_transition(state).candidate
        assert candidate is not None and candidate.kind == "evaluate"
        state = generic_lifecycle.apply_candidate(state, candidate)
        evaluation = next(
            e
            for e in state.closure_evaluations.values()
            if e.target_activation_id == candidate.transition_context.activation_id
        )
    state = generic_lifecycle.claim_activation(
        state, activation_id=evaluation.target_activation_id, suffix="semantic-qa"
    )
    run = state.runs["run-semantic-qa"]
    work = state.work_items[run.work_item_id]
    marker = "REVIEW_GAP" if case == "remediation_payload" else "REVIEW_PASSED"
    payload = _closure_verdict_payload(
        work.payload["closure_evidence_snapshot"], marker=marker
    )
    if case == "target":
        payload["closure_target_id"] = "foreign.closure"
    elif case == "root_digest":
        payload["root_contract_digest"] = "sha256:" + "f" * 64
    elif case == "freshness":
        payload["freshness_anchor_digest"] = "sha256:" + "f" * 64
    elif case == "criteria":
        payload["criterion_results"][0]["criterion_id"] = "foreign.criterion"
    elif case == "evidence":
        payload["criterion_results"][0]["evidence_refs"] = ()
    elif case == "marker_status":
        payload["criterion_results"][0]["status"] = "failed"
    elif case == "rubric":
        payload["rubric"]["criteria"][0]["requirement"] = (
            "Changed instead of reusing the authenticated rubric."
        )
    pre_observation_state = state
    observation = RunnerResultObserved(
        "semantic-qa-observe",
        run_id=run.run_ref.run_id,
        payload=fake_runner_observation_payload(
            run=run,
            activation=state.activations[run.activation_id],
            plan_fingerprint=fingerprint,
            marker=marker,
            artifact_payload=payload,
        ),
        observed_at=2000,
    )
    state, observation = fake_completed_runner_observation_state(
        state=state, observation=observation
    )
    before = state
    decision = decide(
        state, observation, deterministic_context(transition_id="semantic-qa-refusal")
    )
    assert not decision.accepted, case
    state = apply(state, decision)
    assert (
        state.work_items == before.work_items
        and state.activations == before.activations
    )
    assert (
        state.artifacts == before.artifacts
        and state.runner_observations == before.runner_observations
    )
    assert state.closed_work_items == before.closed_work_items
    completion = state.runner_session_completions[
        state.runs[run.run_ref.run_id].current_session_id
    ]
    return (
        state,
        completion,
        decision.refusal.reason,
        payload,
        marker,
        pre_observation_state,
    )


@pytest.mark.parametrize(
    "case,reason",
    [
        ("target", "closure_verdict_target_mismatch"),
        ("rubric", "closure_rubric_mismatch"),
        ("root_digest", "closure_root_contract_digest_mismatch"),
        ("freshness", "closure_freshness_anchor_mismatch"),
        ("criteria", "closure_criterion_set_mismatch"),
        ("evidence", "closure_verdict_evidence_mismatch"),
        ("marker_status", "closure_marker_status_mismatch"),
        ("remediation_payload", "invalid_remediation_payload"),
    ],
)
def test_authentic_candidate_semantic_failures_route_selected_recovery(
    tmp_path, case, reason
):
    state, completion, actual, payload, marker, pre_observation_state = (
        _rejected_closure(case)
    )
    assert actual == reason
    assert runtime_failure_eligible(state, completion), reason
    command = runtime_failure_input(state, completion)
    decision = decide(
        state,
        command,
        deterministic_context(
            transition_id="semantic-qa-route", activation_id="semantic-qa-recovery"
        ),
    )
    assert decision.accepted, decision.refusal
    routed = apply(state, decision)
    assert routed.runner_session_completions[completion.session_id] == completion
    assert (
        routed.receipts[completion.application_input_id]
        == state.receipts[completion.application_input_id]
    )
    candidates = list_ready_dispatch_candidates(routed).candidates
    assert any(c.activation_id == "semantic-qa-recovery" for c in candidates)
    claim = decide(
        routed,
        ClaimWork("semantic-qa-claim", activation_id="semantic-qa-recovery"),
        deterministic_context(
            transition_id="semantic-qa-claimed", run_id="semantic-qa-recovery-run"
        ),
    )
    assert claim.accepted, claim.refusal
    runtime = _runtime(tmp_path, pre_observation_state)
    source_run = runtime.store.load_runtime_state(runtime.cas_store).runs[
        "run-semantic-qa"
    ]
    initial = run_bounded_execution_unit(
        runtime,
        activation_id=source_run.activation_id,
        local_config=_success_config(marker, payload),
    )
    assert initial.code == "runtime_failure_recovery_applied", initial
    completion = _load(runtime).runner_session_completions[
        _load(runtime).runs[initial.run_id].current_session_id
    ]
    original = _load(runtime).receipts[completion.application_input_id]
    assert not original.accepted and original.refusal_reason == reason
    recovery = list_ready_dispatch_candidates(_load(runtime)).candidates[0]
    recovered = run_bounded_execution_unit(
        runtime,
        activation_id=recovery.activation_id,
        local_config=_success_config("QA_RECOVERED", None),
    )
    assert recovered.accepted, recovered
    runtime = _reopen_runtime(runtime)
    returned = list_ready_dispatch_candidates(_load(runtime)).candidates[0]
    assert returned.stage_kind_id == "review_stage"
    repeated = run_bounded_execution_unit(
        runtime,
        activation_id=returned.activation_id,
        local_config=_success_config(marker, payload),
    )
    after = _load(runtime)
    repeated_completion = after.runner_session_completions[
        after.runs[repeated.run_id].current_session_id
    ]
    repeated_refusal = after.receipts[repeated_completion.application_input_id]
    assert (
        not repeated_refusal.accepted and repeated_refusal.refusal_reason == reason
    ), repeated
    assert repeated.code == "runtime_failure_recovery_applied", repeated
    assert returned.work_item_id not in after.closed_work_items
    wait = next(
        w
        for w in after.cooldown_waits.values()
        if w.created_input_id.endswith(":runtime-failure")
    )
    timer = TimerDue("qa-semantic-timer", wait_id=wait.wait_id, observed_at=wait.due_at)
    decision = decide(
        after,
        timer,
        deterministic_context(
            transition_id="qa-semantic-timer-applied",
            activation_id="qa-semantic-second-recovery",
        ),
    )
    assert decision.accepted, decision.refusal
    runtime.store.persist_runtime_state(apply(after, decision), runtime.cas_store)
    runtime = _reopen_runtime(runtime)
    recovery = list_ready_dispatch_candidates(_load(runtime)).candidates[0]
    recovered_again = run_bounded_execution_unit(
        runtime,
        activation_id=recovery.activation_id,
        local_config=_success_config("QA_RECOVERED", None),
    )
    assert recovered_again.accepted, recovered_again
    returned_again = list_ready_dispatch_candidates(_load(runtime)).candidates[0]
    work = _load(runtime).work_items[returned_again.work_item_id]
    valid_payload = _closure_verdict_payload(
        work.payload["closure_evidence_snapshot"], marker="REVIEW_PASSED"
    )
    closed = run_bounded_execution_unit(
        runtime,
        activation_id=returned_again.activation_id,
        local_config=_success_config("REVIEW_PASSED", valid_payload),
    )
    assert closed.accepted, closed
    final = _load(runtime)
    assert returned.work_item_id in final.closed_work_items
    assert any(
        t.source_run_id == closed.run_id
        for t in final.closure_terminal_records.values()
    )
    assert final.runner_session_completions[completion.session_id] == completion
    assert final.receipts[completion.application_input_id] == original
    assert final.receipts[repeated_completion.application_input_id] == repeated_refusal
    reopened = _reopen_runtime(runtime)
    assert _load(reopened) == final
    reopened.close()


@pytest.mark.parametrize(
    "threshold,action_id,reason",
    [
        (
            2,
            generic_admission.COUNTER_INCREMENT_ACTION_ID,
            "counter_threshold_requires_escalation",
        ),
        (
            2,
            generic_admission.COUNTER_THRESHOLD_ACTION_ID,
            "counter_threshold_not_reached",
        ),
    ],
)
def test_counter_marker_mistakes_preserve_counter_and_route(
    tmp_path, monkeypatch, threshold, action_id, reason
):
    source = generic_admission.source()
    next(c for c in source["counters"] if c["id"] == generic_admission.COUNTER_ID)[
        "threshold_count"
    ] = threshold
    if action_id == generic_admission.COUNTER_INCREMENT_ACTION_ID:
        increment = next(a for a in source["terminal_actions"] if a["id"] == action_id)
        route = deepcopy(
            next(
                a
                for a in source["terminal_actions"]
                if a["id"] == generic_admission.DYNAMIC_ROUTE_ACTION_ID
            )
        )
        route.pop("dynamic_target_selector")
        route.update(
            id=increment["id"],
            outcome_id=increment["outcome_id"],
            target_stage_kind_id=generic_admission.PARENT_STAGE_ID,
            target_graph_node_id=generic_admission.PARENT_NODE_ID,
            emitted_queue_family_id="parent",
        )
        increment.clear()
        increment.update(route)
    source["runner_bindings"][0]["runtime_failure_mappings"] = [
        {
            "stage_kind_id": generic_admission.PARENT_STAGE_ID,
            "failure_kind": "result_rejected",
            "action_id": generic_admission.RECOVERY_SOURCE_ACTION_ID,
        }
    ]
    plan, fingerprint = generic_admission.compile_plan(source)
    monkeypatch.setattr(generic_admission, "compile_plan", lambda: (plan, fingerprint))
    state, _, _ = _generic_parent_claimed_state()
    action = next(a for a in plan.terminal_actions if str(a.id) == action_id)
    marker = next(o.marker for o in plan.terminal_outcomes if o.id == action.outcome_id)
    runtime = _runtime(tmp_path, state)
    activation_id = "activation-generic-parent"
    if action_id == generic_admission.COUNTER_INCREMENT_ACTION_ID:
        first = run_bounded_execution_unit(
            runtime,
            activation_id=activation_id,
            local_config=_success_config(marker, generic_fanout.packet_payload()),
        )
        assert first.accepted
        activation_id = (
            list_ready_dispatch_candidates(_load(runtime)).candidates[0].activation_id
        )
    baseline = _load(runtime)
    original_counters = baseline.counters
    result = run_bounded_execution_unit(
        runtime,
        activation_id=activation_id,
        local_config=_success_config(
            marker,
            generic_fanout.packet_payload()
            if action_id == generic_admission.COUNTER_INCREMENT_ACTION_ID
            else None,
        ),
    )
    after = _load(runtime)
    completion = after.runner_session_completions[
        after.runs[result.run_id].current_session_id
    ]
    original = after.receipts[completion.application_input_id]
    assert not original.accepted and original.refusal_reason == reason
    assert after.counters == original_counters
    assert (
        after.runner_observations == baseline.runner_observations
        and after.artifacts == baseline.artifacts
    )
    assert result.code == "runtime_failure_recovery_applied", result
    candidate = list_ready_dispatch_candidates(after).candidates[0]
    assert candidate.stage_kind_id == generic_admission.RECOVERY_STAGE_ID
    recovered = run_bounded_execution_unit(
        runtime,
        activation_id=candidate.activation_id,
        local_config=_success_config(
            "ADMISSION_RECOVERED", generic_fanout.packet_payload()
        ),
    )
    assert recovered.accepted
    returned = list_ready_dispatch_candidates(_load(runtime)).candidates
    assert (
        len(returned) == 1
        and returned[0].stage_kind_id == generic_admission.PARENT_STAGE_ID
    )
    assert (
        _load(runtime).runner_session_completions[completion.session_id] == completion
    )
    assert _load(runtime).receipts[completion.application_input_id] == original
    assert _load(runtime).counters == original_counters
    final = _load(runtime)
    reopened = _reopen_runtime(runtime)
    assert _load(reopened) == final
    reopened.close()


def test_invalid_generated_fanout_candidate_has_zero_effects_and_routes(
    tmp_path, monkeypatch
):
    source = generic_admission.source()
    schema = next(
        s["schema"]
        for s in source["artifact_schemas"]
        if s["id"] == generic_fanout.CHILD_SCHEMA_ID
    )
    schema["properties"]["body"]["enum"] = ("allowed child body",)
    fanout = deepcopy(generic_fanout.source()["fanout_declarations"][0])
    fanout.update(
        source_action_id="admission.complete",
        target_route_id="admission.child_route",
        target_queue_family_id="child",
        target_stage_kind_id=generic_admission.CHILD_STAGE_ID,
        target_graph_node_id=generic_admission.CHILD_NODE_ID,
        target_runner_binding_id=generic_admission.RUNNER_ID,
        source_state_policy="accepted_terminal_observation",
        dependency_policy="none",
    )
    source["fanout_declarations"] = [fanout]
    source["runner_bindings"][0]["runtime_failure_mappings"] = [
        {
            "stage_kind_id": generic_admission.PARENT_STAGE_ID,
            "failure_kind": "result_rejected",
            "action_id": generic_admission.RECOVERY_SOURCE_ACTION_ID,
        }
    ]
    plan, fingerprint = generic_admission.compile_plan(source)
    monkeypatch.setattr(generic_admission, "compile_plan", lambda: (plan, fingerprint))
    state, _, _ = _generic_parent_claimed_state()
    runtime = _runtime(tmp_path, state)
    result = run_bounded_execution_unit(
        runtime,
        activation_id="activation-generic-parent",
        local_config=_success_config("FANOUT_READY", generic_fanout.packet_payload()),
    )
    after = _load(runtime)
    completion = after.runner_session_completions[
        after.runs[result.run_id].current_session_id
    ]
    refusal = after.receipts[completion.application_input_id]
    assert not refusal.accepted and refusal.refusal_reason == "invalid_fanout_payload"
    assert (
        after.runner_observations == {}
        and after.artifacts == {}
        and after.fanout_records == {}
    )
    assert after.work_items == state.work_items
    assert result.code == "runtime_failure_recovery_applied", result
    candidate = list_ready_dispatch_candidates(after).candidates[0]
    assert candidate.stage_kind_id == generic_admission.RECOVERY_STAGE_ID
    recovered = run_bounded_execution_unit(
        runtime,
        activation_id=candidate.activation_id,
        local_config=_success_config(
            "ADMISSION_RECOVERED", generic_fanout.packet_payload()
        ),
    )
    assert recovered.accepted
    assert (
        _load(runtime).runner_session_completions[completion.session_id] == completion
    )
    assert _load(runtime).receipts[completion.application_input_id] == refusal
    final = _load(runtime)
    reopened = _reopen_runtime(runtime)
    assert _load(reopened) == final
    reopened.close()


def test_missing_selected_remediation_schema_is_authority_refusal():
    from dataclasses import replace
    from importlib import import_module

    from millrace.contracts.transition import input_payload_digest
    from millrace.kernel.terminal_actions import (
        TerminalActionResolution,
        resolve_terminal_action,
    )

    kernel_decision = import_module("millrace.kernel.decision")
    _, _, _, payload, marker, state = _rejected_closure("remediation_payload")
    run = state.runs["run-semantic-qa"]
    work = state.work_items[run.work_item_id]
    admitted = state.admitted_plans[run.run_ref.plan_ref.authority_fingerprint]
    plan = admitted.selected_plan
    observation = RunnerResultObserved(
        "missing-remediation-schema",
        run_id=run.run_ref.run_id,
        payload=fake_runner_observation_payload(
            run=run,
            activation=state.activations[run.activation_id],
            plan_fingerprint=run.run_ref.plan_ref.authority_fingerprint,
            marker=marker,
            artifact_payload=payload,
        ),
        observed_at=2000,
    )
    state, observation = fake_completed_runner_observation_state(
        state=state, observation=observation
    )
    behavior = plan.completion_behaviors[0]
    action = next(a for a in plan.terminal_actions if a.id == behavior.gap_action_id)
    context = deterministic_context(transition_id="missing-schema-refused")
    result = resolve_terminal_action(
        transition_input=observation,
        context=context,
        selected_plan=plan,
        state=state,
        run=state.runs[run.run_ref.run_id],
        activation=state.activations[run.activation_id],
        work_item=work,
        action=action,
        observation_payload=observation.payload,
    )
    assert isinstance(result, TerminalActionResolution)
    policy = next(
        p for p in plan.remediation_policies if p.id == behavior.remediation_policy_id
    )
    # Exercise the defensive selected-schema branch directly. Whole-plan
    # admission/load also rejects this missing declaration before mutation.
    missing_schema_plan = replace(
        plan,
        artifact_schemas=tuple(
            s for s in plan.artifact_schemas if s.id != policy.payload_schema_id
        ),
    )
    evaluation = next(iter(state.closure_evaluations.values()))
    refusal = kernel_decision._closure_gap_mutations_or_refusal(
        state=state,
        transition_input=observation,
        context=context,
        digest=input_payload_digest(observation),
        selected_plan=missing_schema_plan,
        result=result,
        target=state.closure_targets[evaluation.closure_target_id],
        behavior=behavior,
        run=state.runs[run.run_ref.run_id],
        work_item=work,
        action=action,
    )
    assert not isinstance(refusal, tuple)
    assert not refusal.accepted
    assert refusal.refusal.reason == "missing_remediation_payload_schema"
    refused = apply(state, refusal)
    completion = refused.runner_session_completions[
        refused.runs[run.run_ref.run_id].current_session_id
    ]
    assert not runtime_failure_eligible(refused, completion)
    assert refused.artifacts == state.artifacts
    assert refused.runner_observations == state.runner_observations
    assert refused.work_items == state.work_items
    assert refused.remediation_work_records == state.remediation_work_records


@pytest.mark.parametrize("part", ["missing", "stale"])
def test_prior_verdict_authority_failure_never_routes_candidate_recovery(part):
    from dataclasses import replace

    from millrace.substrate._sqlite_relations import validate_loaded_runtime_state
    from millrace.substrate.errors import StorageIntegrityError

    _, _, _, payload, marker, state = _rejected_closure("rubric")
    run = state.runs["run-semantic-qa"]
    snapshot = state.work_items[run.work_item_id].payload["closure_evidence_snapshot"]
    artifact_id = snapshot["prior_verdict"]["artifact_id"]
    prior = state.artifacts[artifact_id]
    artifacts = dict(state.artifacts)
    if part == "missing":
        del artifacts[artifact_id]
    else:
        artifacts[artifact_id] = replace(prior, payload_digest="sha256:" + "f" * 64)
    bad = replace(state, artifacts=artifacts)
    observation = RunnerResultObserved(
        "bad-prior-verdict",
        run_id=run.run_ref.run_id,
        payload=fake_runner_observation_payload(
            run=run,
            activation=state.activations[run.activation_id],
            plan_fingerprint=run.run_ref.plan_ref.authority_fingerprint,
            marker=marker,
            artifact_payload=payload,
        ),
        observed_at=2000,
    )
    bad, observation = fake_completed_runner_observation_state(
        state=bad, observation=observation
    )
    refusal = decide(
        bad, observation, deterministic_context(transition_id="bad-prior-refused")
    )
    assert not refusal.accepted
    assert refusal.refusal.reason == "closure_snapshot_authority_mismatch"
    refused = apply(bad, refusal)
    completion = refused.runner_session_completions[
        refused.runs[run.run_ref.run_id].current_session_id
    ]
    assert not runtime_failure_eligible(refused, completion)
    assert refused.artifacts == bad.artifacts
    assert refused.runner_observations == bad.runner_observations
    assert refused.work_items == bad.work_items
    assert refused.closed_work_items == bad.closed_work_items
    with pytest.raises(StorageIntegrityError):
        validate_loaded_runtime_state(refused)
