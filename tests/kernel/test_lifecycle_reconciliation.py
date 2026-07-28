from __future__ import annotations

from dataclasses import replace

import pytest

from millrace.contracts.state import OperatorWaitRecord
from millrace.contracts.transition import (
    FanoutFromArtifact,
    JoinFromArtifact,
    RunnerResultObserved,
    artifact_payload_digest,
    input_payload_digest,
)
from millrace.kernel.lifecycle import project_next_lifecycle_transition
from millrace.kernel.observation_policy import (
    ObservationPolicyDiagnostic,
    authenticate_runner_observation,
)
from millrace.testing import decide_with_fake_runner_completion as decide
from millrace.testing import (
    fake_runner_completion_input_id,
    fake_runner_observation_payload,
)
from support import generic_lifecycle


def _project(state):
    return project_next_lifecycle_transition(state)


def _assert_diagnostic(state, reason_code: str) -> None:
    projection = _project(state)
    assert projection.candidate is None
    assert projection.diagnostics
    assert projection.diagnostics[0].reason_code == reason_code


def _other_plan_ref(state):
    plan_ref = state.work_items["work-origin"].ref.plan_ref
    return replace(
        plan_ref,
        authority_fingerprint=f"{plan_ref.authority_fingerprint}:other",
    )


def _source_activation_id(state) -> str:
    return state.runs["run-origin"].activation_id


def _first_fanout_record(state):
    return sorted(
        state.fanout_records.values(),
        key=lambda record: (str(record.fanout_id), record.item_key),
    )[0]


def _fanout_route(state, fanout_record):
    return next(
        route
        for route in state.activation_routes
        if route.target_work_item_id == fanout_record.target_work_item_id
    )


def _fanout_dependency(state, fanout_record):
    return next(
        dependency
        for dependency in state.work_dependencies.values()
        if dependency.fanout_record_id == fanout_record.record_id
    )


def _replace_artifact(state, artifact_id: str, **changes):
    artifact = state.artifacts[artifact_id]
    return replace(
        state,
        artifacts={
            **state.artifacts,
            artifact_id: replace(artifact, **changes),
        },
    )


def _replace_work_item(state, work_item_id: str, **changes):
    work_item = state.work_items[work_item_id]
    return replace(
        state,
        work_items={
            **state.work_items,
            work_item_id: replace(work_item, **changes),
        },
    )


def _replace_activation(state, activation_id: str, **changes):
    activation = state.activations[activation_id]
    return replace(
        state,
        activations={
            **state.activations,
            activation_id: replace(activation, **changes),
        },
    )


def _replace_run(state, run_id: str, **changes):
    run = state.runs[run_id]
    return replace(
        state,
        runs={
            **state.runs,
            run_id: replace(run, **changes),
        },
    )


def _source_observation(state):
    artifact = state.artifacts[generic_lifecycle.source_artifact_id()]
    return next(
        observation
        for observation in state.runner_observations.values()
        if observation.created_by_input_id == artifact.created_by_input_id
    )


def _replace_source_observation(state, **changes):
    observation = _source_observation(state)
    changed = replace(observation, **changes)
    return replace(
        state,
        runner_observations={
            **state.runner_observations,
            observation.observation_id: changed,
        },
    )


def _reauthorize_source_observation_payload(state, payload):
    observation = _source_observation(state)
    changed = replace(observation, payload=payload)
    reconstructed = RunnerResultObserved(
        observation.created_by_input_id,
        run_id=observation.run_id,
        payload=payload,
        observed_at=observation.observed_at,
    )
    receipt = state.receipts[observation.created_by_input_id]
    receipt_ref = replace(
        receipt.receipt_ref,
        input_payload_digest=input_payload_digest(reconstructed),
    )
    return replace(
        state,
        runner_observations={
            **state.runner_observations,
            observation.observation_id: changed,
        },
        receipts={
            **state.receipts,
            observation.created_by_input_id: replace(
                receipt,
                receipt_ref=receipt_ref,
            ),
        },
    )


def _observation_diagnostic(state) -> ObservationPolicyDiagnostic:
    result = authenticate_runner_observation(state, _source_observation(state))
    assert isinstance(result, ObservationPolicyDiagnostic)
    return result


def _with_source_context_drift(state, field: str):
    artifact_id = generic_lifecycle.source_artifact_id()
    artifact = state.artifacts[artifact_id]
    run = state.runs[artifact.source_run_id]
    activation = state.activations[run.activation_id]
    if field == "artifact_stage":
        return _replace_artifact(
            state,
            artifact_id,
            source_stage_kind_id="wrong.stage",
        )
    if field == "artifact_graph":
        return _replace_artifact(
            state,
            artifact_id,
            source_graph_node_id="wrong.node",
        )
    if field == "artifact_input":
        return _replace_artifact(
            state,
            artifact_id,
            created_by_input_id="wrong-observation-input",
        )
    if field == "artifact_transition":
        return _replace_artifact(
            state,
            artifact_id,
            transition_id="wrong-observation-transition",
        )
    if field == "observation_action":
        observation = next(
            candidate
            for candidate in state.runner_observations.values()
            if candidate.created_by_input_id == artifact.created_by_input_id
        )
        return replace(
            state,
            runner_observations={
                **state.runner_observations,
                observation.observation_id: replace(
                    observation,
                    payload={**observation.payload, "action_id": "wrong.action"},
                ),
            },
        )
    if field == "event_action":
        return replace(
            state,
            governance_events=tuple(
                replace(event, action_id="wrong.action")
                if event.input_id == artifact.created_by_input_id
                else event
                for event in state.governance_events
            ),
        )
    if field == "trace_action":
        return replace(
            state,
            traces=tuple(
                replace(trace, action_id="wrong.action")
                if trace.input_id == artifact.created_by_input_id
                else trace
                for trace in state.traces
            ),
        )
    if field == "run_ref_work":
        return _replace_run(
            state,
            run.run_ref.run_id,
            run_ref=replace(run.run_ref, work_item_id="wrong-work"),
        )
    if field == "run_ref_generation":
        return _replace_run(
            state,
            run.run_ref.run_id,
            run_ref=replace(run.run_ref, generation=run.run_ref.generation + 1),
        )
    if field == "run_runner":
        return _replace_run(
            state,
            run.run_ref.run_id,
            runner_binding_id="wrong.runner",
        )
    if field == "activation_claim":
        return _replace_activation(
            state,
            activation.activation_id,
            claimed_by_run_id=None,
        )
    return _replace_activation(
        state,
        activation.activation_id,
        generation=activation.generation + 1,
    )


def _replace_closed_work_item(state, work_item_id: str, **changes):
    closed = state.closed_work_items[work_item_id]
    return replace(
        state,
        closed_work_items={
            **state.closed_work_items,
            work_item_id: replace(closed, **changes),
        },
    )


@pytest.mark.parametrize(
    "field",
    (
        "artifact_stage",
        "artifact_graph",
        "artifact_input",
        "artifact_transition",
        "observation_action",
        "event_action",
        "trace_action",
        "run_ref_work",
        "run_ref_generation",
        "run_runner",
        "activation_claim",
        "activation_generation",
    ),
)
def test_source_context_authority_drift_is_diagnostic(field: str) -> None:
    state, _plan, _fingerprint = generic_lifecycle.origin_closed_state()

    projection = _project(_with_source_context_drift(state, field))

    assert projection.candidate is None, field
    assert projection.diagnostics, field
    assert projection.diagnostics[0].reason_code == "wrong_source_artifact", field


def test_observation_receipt_authority_drift_is_diagnostic() -> None:
    state, _plan, _fingerprint = generic_lifecycle.origin_closed_state()
    observation = _source_observation(state)
    receipt = state.receipts[observation.created_by_input_id]
    drifted = replace(
        state,
        receipts={
            **state.receipts,
            observation.created_by_input_id: replace(
                receipt,
                receipt_ref=replace(
                    receipt.receipt_ref,
                    input_payload_digest="sha256:" + "0" * 64,
                ),
            ),
        },
    )

    assert _observation_diagnostic(drifted).reason_code == "receipt_authority"


def test_observation_identity_drift_is_diagnostic() -> None:
    state, _plan, _fingerprint = generic_lifecycle.origin_closed_state()
    drifted = _replace_source_observation(
        state,
        observation_id="wrong-observation-id",
    )

    assert _observation_diagnostic(drifted).reason_code == "observation_identity"


def test_runner_observation_evidence_authority_drift_is_diagnostic() -> None:
    state, _plan, _fingerprint = generic_lifecycle.origin_closed_state()
    observation = _source_observation(state)
    drifted = _reauthorize_source_observation_payload(
        state,
        {**observation.payload, "runner_binding_id": "wrong.runner"},
    )

    assert _observation_diagnostic(drifted).reason_code == "evidence_authority"


def _with_first_fanout_record(state, **changes):
    record = _first_fanout_record(state)
    return replace(
        state,
        fanout_records={
            **state.fanout_records,
            record.record_id: replace(record, **changes),
        },
    )


def _with_first_fanout_work(state, **changes):
    record = _first_fanout_record(state)
    return _replace_work_item(state, record.target_work_item_id, **changes)


def _with_first_fanout_activation(state, **changes):
    record = _first_fanout_record(state)
    return _replace_activation(state, record.target_activation_id, **changes)


def _with_first_fanout_route(state, **changes):
    record = _first_fanout_record(state)
    route = _fanout_route(state, record)
    return replace(
        state,
        activation_routes=tuple(
            replace(candidate, **changes)
            if candidate.record_id == route.record_id
            else candidate
            for candidate in state.activation_routes
        ),
    )


def _with_first_fanout_dependency(state, **changes):
    record = _first_fanout_record(state)
    dependency = _fanout_dependency(state, record)
    return replace(
        state,
        work_dependencies={
            **state.work_dependencies,
            dependency.dependency_id: replace(dependency, **changes),
        },
    )


def _duplicate_first_fanout_dependency(state):
    record = _first_fanout_record(state)
    dependency = _fanout_dependency(state, record)
    duplicate = replace(
        dependency,
        dependency_id=f"{dependency.dependency_id}:duplicate",
    )
    return replace(
        state,
        work_dependencies={
            **state.work_dependencies,
            duplicate.dependency_id: duplicate,
        },
    )


def _without_receipt(state, input_id: str):
    receipts = dict(state.receipts)
    receipts.pop(input_id)
    return replace(state, receipts=receipts)


def _without_transition(state, input_id: str):
    return replace(
        state,
        transitions=tuple(
            transition
            for transition in state.transitions
            if transition.input_id != input_id
        ),
    )


def _with_transition(state, input_id: str, **changes):
    return replace(
        state,
        transitions=tuple(
            replace(transition, **changes)
            if transition.input_id == input_id
            else transition
            for transition in state.transitions
        ),
    )


def _artifact_by_schema(state, schema_id: str):
    return next(
        artifact
        for artifact in state.artifacts.values()
        if str(artifact.schema_id) == schema_id
    )


def _fanout_record_for_work(state, work_item_id: str):
    return next(
        record
        for record in state.fanout_records.values()
        if record.target_work_item_id == work_item_id
    )


def _dependency_for_fanout(state, fanout_record):
    return next(
        dependency
        for dependency in state.work_dependencies.values()
        if dependency.fanout_record_id == fanout_record.record_id
    )


def _replace_fanout_record(state, record_id: str, **changes):
    record = state.fanout_records[record_id]
    return replace(
        state,
        fanout_records={
            **state.fanout_records,
            record_id: replace(record, **changes),
        },
    )


def _replace_dependency(state, dependency_id: str, **changes):
    dependency = state.work_dependencies[dependency_id]
    return replace(
        state,
        work_dependencies={
            **state.work_dependencies,
            dependency_id: replace(dependency, **changes),
        },
    )


def _with_report_artifact(state, current_schema_id: str, **changes):
    artifact = _artifact_by_schema(state, current_schema_id)
    return _replace_artifact(state, artifact.artifact_id, **changes)


def _with_report_payload(state, current_schema_id: str, **changes):
    artifact = _artifact_by_schema(state, current_schema_id)
    payload = {**artifact.payload, **changes}
    return _replace_artifact(
        state,
        artifact.artifact_id,
        payload=payload,
        payload_digest=artifact_payload_digest(payload),
    )


def _with_report_fanout_record(state, schema_id: str, **changes):
    artifact = _artifact_by_schema(state, schema_id)
    record = _fanout_record_for_work(state, artifact.work_item_id)
    return _replace_fanout_record(state, record.record_id, **changes)


def _with_report_dependency(state, schema_id: str, **changes):
    artifact = _artifact_by_schema(state, schema_id)
    record = _fanout_record_for_work(state, artifact.work_item_id)
    dependency = _dependency_for_fanout(state, record)
    return _replace_dependency(state, dependency.dependency_id, **changes)


def _without_report_fanout_record(state, schema_id: str):
    artifact = _artifact_by_schema(state, schema_id)
    record = _fanout_record_for_work(state, artifact.work_item_id)
    fanout_records = dict(state.fanout_records)
    fanout_records.pop(record.record_id)
    return replace(state, fanout_records=fanout_records)


def _without_report_dependency(state, schema_id: str):
    artifact = _artifact_by_schema(state, schema_id)
    record = _fanout_record_for_work(state, artifact.work_item_id)
    dependency = _dependency_for_fanout(state, record)
    work_dependencies = dict(state.work_dependencies)
    work_dependencies.pop(dependency.dependency_id)
    return replace(state, work_dependencies=work_dependencies)


def _duplicate_report_fanout_record(state, schema_id: str):
    artifact = _artifact_by_schema(state, schema_id)
    record = _fanout_record_for_work(state, artifact.work_item_id)
    duplicate = replace(record, record_id=f"{record.record_id}:duplicate")
    return replace(
        state,
        fanout_records={**state.fanout_records, duplicate.record_id: duplicate},
    )


def _duplicate_report_dependency(state, schema_id: str):
    artifact = _artifact_by_schema(state, schema_id)
    record = _fanout_record_for_work(state, artifact.work_item_id)
    dependency = _dependency_for_fanout(state, record)
    duplicate = replace(
        dependency,
        dependency_id=f"{dependency.dependency_id}:duplicate",
    )
    return replace(
        state,
        work_dependencies={
            **state.work_dependencies,
            duplicate.dependency_id: duplicate,
        },
    )


def _with_bundle_artifact_for_report(state, current_schema_id: str, **changes):
    report = _artifact_by_schema(state, current_schema_id)
    record = _fanout_record_for_work(state, report.work_item_id)
    return _replace_artifact(state, record.source_artifact_id, **changes)


def _with_bundle_closed_for_report(state, current_schema_id: str, **changes):
    report = _artifact_by_schema(state, current_schema_id)
    record = _fanout_record_for_work(state, report.work_item_id)
    return _replace_closed_work_item(state, record.source_work_item_id, **changes)


def _join_route(state):
    return next(
        route
        for route in state.activation_routes
        if str(route.action_id) == generic_lifecycle.JOIN_ID
    )


def _with_join_route(state, **changes):
    route = _join_route(state)
    return replace(
        state,
        activation_routes=tuple(
            replace(candidate, **changes)
            if candidate.record_id == route.record_id
            else candidate
            for candidate in state.activation_routes
        ),
    )


def _with_join_target_work(state, **changes):
    return _replace_work_item(state, _join_route(state).target_work_item_id, **changes)


def _with_join_target_activation(state, **changes):
    return _replace_activation(
        state, _join_route(state).target_activation_id, **changes
    )


def _duplicate_join_route(state):
    route = _join_route(state)
    duplicate = replace(route, record_id=f"{route.record_id}:duplicate")
    return replace(state, activation_routes=(*state.activation_routes, duplicate))


def _duplicate_transition(state, input_id: str):
    transition = next(
        transition
        for transition in state.transitions
        if transition.input_id == input_id
    )
    duplicate = replace(transition, record_id=f"{transition.record_id}:duplicate")
    return replace(state, transitions=(*state.transitions, duplicate))


def test_closed_source_fanout_is_projected_from_selected_authority() -> None:
    state, _plan, fingerprint = generic_lifecycle.origin_closed_state()

    projection = _project(state)

    assert projection.candidate is not None
    assert projection.diagnostics == ()
    assert projection.candidate.kind == "fanout"
    assert projection.candidate.plan_fingerprint == fingerprint
    assert projection.candidate.declaration_id == generic_lifecycle.FANOUT_ALPHA_ID
    assert projection.candidate.source_artifact_id == (
        generic_lifecycle.source_artifact_id()
    )
    assert projection.candidate.transition_input.fanout_id == (
        generic_lifecycle.FANOUT_ALPHA_ID
    )


def test_accepted_terminal_fanout_is_not_reprojected() -> None:
    state, _plan, _fingerprint = generic_lifecycle.accepted_terminal_fanout_state()

    projection = _project(state)

    assert projection.candidate is None
    assert projection.diagnostics == ()


def test_accepted_terminal_fanout_accepts_omitted_optional_payload_fields() -> None:
    state, _plan, _fingerprint = (
        generic_lifecycle.accepted_terminal_optional_omission_fanout_state()
    )
    generated_payloads = tuple(
        item.payload
        for item in state.work_items.values()
        if item.created_by_input_id
        == fake_runner_completion_input_id("observe-origin")
    )

    assert len(generated_payloads) == 4
    assert all("note" not in payload for payload in generated_payloads)
    assert len(state.fanout_records) == 4


def test_complete_fanout_with_omitted_optional_payload_is_not_reprojected() -> None:
    state, _plan, _fingerprint = (
        generic_lifecycle.accepted_terminal_optional_omission_fanout_state()
    )

    projection = _project(state)

    assert projection.candidate is None
    assert projection.diagnostics == ()


def test_accepted_terminal_fanout_absent_optional_collection_is_zero_item_noop(
) -> None:
    state, _plan, _fingerprint = (
        generic_lifecycle.accepted_terminal_optional_collection_omission_state()
    )

    projection = _project(state)

    assert len(state.artifacts) == 1
    assert state.fanout_records == {}
    assert not any(
        item.created_by_input_id
        == fake_runner_completion_input_id("observe-origin")
        for item in state.work_items.values()
    )
    assert projection.candidate is None
    assert projection.diagnostics == ()


def test_source_closed_fanout_absent_optional_collection_does_not_reproject(
) -> None:
    state, _plan, _fingerprint = (
        generic_lifecycle.source_closed_optional_collection_omission_state()
    )

    projection = _project(state)

    assert state.fanout_records == {}
    assert projection.candidate is None
    assert projection.diagnostics == ()


@pytest.mark.parametrize(
    "items",
    (
        (),
        "wrong-type",
        ("not-an-object",),
        ({"item_id": "", "body": "blank key"},),
        (
            {"item_id": "duplicate", "body": "first"},
            {"item_id": "duplicate", "body": "second"},
        ),
    ),
)
def test_accepted_terminal_fanout_present_malformed_collection_refuses_atomically(
    items: object,
) -> None:
    state, _plan, fingerprint = (
        generic_lifecycle.accepted_terminal_optional_collection_claimed_state()
    )
    run = state.runs["run-origin"]
    activation = state.activations[run.activation_id]
    observation = RunnerResultObserved(
        "observe-origin",
        run_id=run.run_ref.run_id,
        payload=fake_runner_observation_payload(
            run=run,
            activation=activation,
            plan_fingerprint=fingerprint,
            marker="SOURCE_READY",
            artifact_payload={"bundle_id": "bundle-a", "items": items},
        ),
        observed_at=None,
    )

    decision = decide(
        state,
        observation,
        generic_lifecycle.context("observe-origin"),
    )

    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == "invalid_artifact_payload"
    assert state.artifacts == {}
    assert state.fanout_records == {}


def test_open_source_work_is_not_ready() -> None:
    state, _plan, _fingerprint = generic_lifecycle.origin_claimed_state()

    projection = _project(state)

    assert projection.candidate is None
    assert projection.diagnostics == ()


def test_default_plan_drift_does_not_override_source_run_authority() -> None:
    state, _plan, fingerprint = generic_lifecycle.origin_closed_state()
    drifted = replace(state, default_plan_ref=_other_plan_ref(state))

    projection = _project(drifted)

    assert projection.candidate is not None
    assert projection.candidate.plan_fingerprint == fingerprint
    assert projection.diagnostics == ()


@pytest.mark.parametrize(
    ("label", "mutate", "reason_code"),
    [
        (
            "source_run_plan_ref",
            lambda state: _replace_run(
                state,
                "run-origin",
                run_ref=replace(
                    state.runs["run-origin"].run_ref,
                    plan_ref=_other_plan_ref(state),
                ),
            ),
            "plan_ref_drift",
        ),
        (
            "source_work_plan_ref",
            lambda state: _replace_work_item(
                state,
                "work-origin",
                ref=replace(
                    state.work_items["work-origin"].ref,
                    plan_ref=_other_plan_ref(state),
                ),
            ),
            "plan_ref_drift",
        ),
        (
            "source_activation_plan_ref",
            lambda state: _replace_activation(
                state,
                _source_activation_id(state),
                plan_ref=_other_plan_ref(state),
            ),
            "plan_ref_drift",
        ),
        (
            "missing_admitted_plan",
            lambda state: replace(state, admitted_plans={}),
            "unknown_plan_ref",
        ),
        (
            "wrong_close_run",
            lambda state: _replace_closed_work_item(
                state,
                "work-origin",
                source_run_id="wrong-run",
            ),
            "wrong_source_aftermath",
        ),
        (
            "wrong_close_action",
            lambda state: _replace_closed_work_item(
                state,
                "work-origin",
                action_id="wrong.action",
            ),
            "wrong_source_aftermath",
        ),
        (
            "wrong_artifact_run",
            lambda state: _replace_artifact(
                state,
                generic_lifecycle.source_artifact_id(),
                source_run_id="missing-run",
            ),
            "wrong_source_artifact",
        ),
        (
            "wrong_artifact_digest",
            lambda state: _replace_artifact(
                state,
                generic_lifecycle.source_artifact_id(),
                payload_digest=f"sha256:{'0' * 64}",
            ),
            "wrong_source_artifact",
        ),
    ],
)
def test_closed_source_corruption_is_diagnostic(label, mutate, reason_code) -> None:
    state, _plan, _fingerprint = generic_lifecycle.origin_closed_state()

    _assert_diagnostic(mutate(state), reason_code)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda state: _replace_artifact(
            state,
            generic_lifecycle.source_artifact_id(),
            schema_id=generic_lifecycle.ALPHA_REPORT_SCHEMA_ID,
        ),
        lambda state: _replace_artifact(
            state,
            generic_lifecycle.source_artifact_id(),
            source_action_id="wrong.action",
        ),
    ],
)
def test_fanout_projection_refuses_corrupt_selector_artifacts(mutate) -> None:
    state, _plan, _fingerprint = generic_lifecycle.origin_closed_state()

    projection = _project(mutate(state))

    assert projection.candidate is None
    assert projection.diagnostics[0].reason_code == "wrong_source_artifact"


def test_two_fanout_declarations_project_in_stable_order() -> None:
    state, _plan, _fingerprint = generic_lifecycle.origin_closed_state()

    first = _project(state).candidate
    assert first is not None
    after_first = generic_lifecycle.apply_candidate(state, first)
    second = _project(after_first).candidate

    assert first.declaration_id == generic_lifecycle.FANOUT_ALPHA_ID
    assert second is not None
    assert second.declaration_id == generic_lifecycle.FANOUT_BETA_ID


def test_fanout_declaration_order_does_not_change_candidate_order() -> None:
    state, _plan, _fingerprint = generic_lifecycle.origin_closed_state_from_source(
        generic_lifecycle.source_with_reversed_fanouts()
    )

    projection = _project(state)

    assert projection.candidate is not None
    assert projection.candidate.declaration_id == generic_lifecycle.FANOUT_ALPHA_ID


def test_same_source_values_in_different_plans_have_stable_distinct_candidates() -> (
    None
):
    (
        state,
        _first_plan,
        first_fingerprint,
        _second_plan,
        second_fingerprint,
    ) = generic_lifecycle.two_plan_origin_closed_state()
    reordered = replace(
        state,
        admitted_plans=dict(reversed(tuple(state.admitted_plans.items()))),
        artifacts=dict(reversed(tuple(state.artifacts.items()))),
        runs=dict(reversed(tuple(state.runs.items()))),
        work_items=dict(reversed(tuple(state.work_items.items()))),
    )

    first = _project(state).candidate
    reordered_first = _project(reordered).candidate

    assert first is not None
    assert reordered_first is not None
    assert first.transition_input.input_id == reordered_first.transition_input.input_id
    assert first.plan_fingerprint in {first_fingerprint, second_fingerprint}
    assert first.transition_input.input_id != (
        _project(
            generic_lifecycle.apply_candidate(state, first)
        ).candidate.transition_input.input_id
    )


def test_global_order_prefers_lower_plan_join_over_higher_plan_fanout() -> None:
    (
        state,
        lower_fingerprint,
        higher_fingerprint,
    ) = generic_lifecycle.lower_plan_join_higher_plan_fanout_state()

    projection = _project(state)

    assert lower_fingerprint < higher_fingerprint
    assert projection.diagnostics == ()
    assert projection.candidate is not None
    assert projection.candidate.plan_fingerprint == lower_fingerprint
    assert projection.candidate.kind == "join"


def test_complete_fanout_exact_item_key_coverage_allows_next_declaration() -> None:
    state, _plan, _fingerprint = generic_lifecycle.origin_closed_state()
    first = _project(state).candidate
    assert first is not None
    after_first = generic_lifecycle.apply_candidate(state, first)
    alpha_records = tuple(
        record
        for record in after_first.fanout_records.values()
        if str(record.fanout_id) == generic_lifecycle.FANOUT_ALPHA_ID
    )

    projection = _project(after_first)

    assert {record.item_key for record in alpha_records} == {"one", "two"}
    assert len(alpha_records) == 2
    assert projection.candidate is not None
    assert projection.candidate.declaration_id == generic_lifecycle.FANOUT_BETA_ID


@pytest.mark.parametrize(
    "case",
    (
        "split_item_creators",
        "non_fanout_creator",
        "missing_aftermath",
        "partial_item_aftermath",
        "route_record_id",
        "receipt_transition_id",
        "receipt_payload_digest",
        "missing_event",
        "drifted_event",
        "missing_trace",
        "drifted_trace",
        "extra_creator_output",
        "all_records_declaration_drift",
        "missing_aftermath_transition_kind",
        "accepted_terminal_wrong_creator_kind",
    ),
)
def test_fanout_creator_and_aftermath_integrity_is_shared(case: str) -> None:
    state = generic_lifecycle.fanout_integrity_state(case)
    transition_input = FanoutFromArtifact(
        f"fanout-integrity-{case}",
        fanout_id=generic_lifecycle.FANOUT_ALPHA_ID,
        source_artifact_id=generic_lifecycle.source_artifact_id(),
    )

    projection = _project(state)
    decision = decide(
        state,
        transition_input,
        generic_lifecycle.context(transition_input.input_id),
    )

    expected_reason = (
        "wrong_source_artifact"
        if case == "accepted_terminal_wrong_creator_kind"
        else "fanout_partial_state"
    )
    assert projection.candidate is None
    assert projection.diagnostics[0].reason_code == expected_reason
    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == expected_reason


def test_source_plan_ref_must_equal_the_full_admitted_plan_ref() -> None:
    state, _plan, _fingerprint = (
        generic_lifecycle.origin_closed_with_admitted_plan_ref_drift()
    )
    transition_input = FanoutFromArtifact(
        "fanout-plan-ref-drift",
        fanout_id=generic_lifecycle.FANOUT_ALPHA_ID,
        source_artifact_id=generic_lifecycle.source_artifact_id(),
    )

    projection = _project(state)
    decision = decide(
        state,
        transition_input,
        generic_lifecycle.context(transition_input.input_id),
    )

    assert projection.candidate is None
    assert projection.diagnostics[0].reason_code == "plan_ref_drift"
    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == "plan_ref_drift"


@pytest.mark.parametrize(
    ("label", "mutate"),
    [
        (
            "missing_receipt",
            lambda state: _without_receipt(
                state,
                _first_fanout_record(state).created_by_input_id,
            ),
        ),
        (
            "missing_transition",
            lambda state: _without_transition(
                state,
                _first_fanout_record(state).created_by_input_id,
            ),
        ),
        (
            "refused_transition",
            lambda state: _with_transition(
                state,
                _first_fanout_record(state).created_by_input_id,
                accepted=False,
            ),
        ),
        (
            "duplicate_record",
            lambda state: replace(
                state,
                fanout_records={
                    **state.fanout_records,
                    f"{_first_fanout_record(state).record_id}:duplicate": replace(
                        _first_fanout_record(state),
                        record_id=f"{_first_fanout_record(state).record_id}:duplicate",
                    ),
                },
            ),
        ),
        (
            "cross_plan_record",
            lambda state: _with_first_fanout_record(
                state,
                selected_plan_ref=_other_plan_ref(state),
            ),
        ),
        (
            "target_queue",
            lambda state: _with_first_fanout_record(
                state,
                target_queue_family_id="wrong_queue",
            ),
        ),
        (
            "target_stage",
            lambda state: _with_first_fanout_record(
                state,
                target_stage_kind_id="wrong_stage",
            ),
        ),
        (
            "target_node",
            lambda state: _with_first_fanout_record(
                state,
                target_graph_node_id="wrong.node",
            ),
        ),
        (
            "source_artifact_digest",
            lambda state: _with_first_fanout_record(
                state,
                source_artifact_digest=f"sha256:{'1' * 64}",
            ),
        ),
        (
            "source_work_link",
            lambda state: _with_first_fanout_record(
                state,
                source_work_item_id="wrong-work",
            ),
        ),
        (
            "source_run_link",
            lambda state: _with_first_fanout_record(
                state,
                source_run_id="wrong-run",
            ),
        ),
        (
            "source_action_link",
            lambda state: _with_first_fanout_record(
                state,
                source_action_id="wrong.action",
            ),
        ),
        (
            "lineage_link",
            lambda state: _with_first_fanout_record(state, lineage_id="wrong-lineage"),
        ),
        (
            "target_work_plan",
            lambda state: _with_first_fanout_work(
                state,
                ref=replace(
                    state.work_items[
                        _first_fanout_record(state).target_work_item_id
                    ].ref,
                    plan_ref=_other_plan_ref(state),
                ),
            ),
        ),
        (
            "target_work_queue",
            lambda state: _with_first_fanout_work(
                state,
                queue_family_id="wrong_queue",
            ),
        ),
        (
            "target_work_payload",
            lambda state: _with_first_fanout_work(
                state,
                payload={"bundle_id": "bundle-a", "items": ()},
            ),
        ),
        (
            "target_work_lineage",
            lambda state: _with_first_fanout_work(state, lineage_id="wrong-lineage"),
        ),
        (
            "target_work_created_by",
            lambda state: _with_first_fanout_work(state, created_by_input_id="wrong"),
        ),
        (
            "activation_work_link",
            lambda state: _with_first_fanout_activation(
                state,
                work_item_id="wrong-work",
            ),
        ),
        (
            "activation_stage",
            lambda state: _with_first_fanout_activation(
                state,
                stage_kind_id="wrong_stage",
            ),
        ),
        (
            "activation_runner",
            lambda state: _with_first_fanout_activation(
                state,
                runner_binding_id="wrong.runner",
            ),
        ),
        (
            "route_action",
            lambda state: _with_first_fanout_route(state, action_id="wrong.action"),
        ),
        (
            "route_source",
            lambda state: _with_first_fanout_route(state, source_run_id="wrong-run"),
        ),
        (
            "route_target_work",
            lambda state: _with_first_fanout_route(
                state,
                target_work_item_id="wrong-work",
            ),
        ),
        (
            "route_target_activation",
            lambda state: _with_first_fanout_route(
                state,
                target_activation_id="wrong-activation",
            ),
        ),
        (
            "route_created_by",
            lambda state: _with_first_fanout_route(
                state,
                created_by_input_id="wrong",
            ),
        ),
        (
            "dependency_source",
            lambda state: _with_first_fanout_dependency(
                state,
                dependency_work_item_id="wrong-work",
            ),
        ),
        (
            "dependency_lineage",
            lambda state: _with_first_fanout_dependency(
                state,
                lineage_id="wrong-lineage",
            ),
        ),
        ("duplicate_dependency", _duplicate_first_fanout_dependency),
    ],
)
def test_complete_fanout_aftermath_mutators_are_diagnostic(label, mutate) -> None:
    state, _plan, _fingerprint = generic_lifecycle.origin_closed_state()
    first = _project(state).candidate
    assert first is not None
    after_first = generic_lifecycle.apply_candidate(state, first)

    _assert_diagnostic(mutate(after_first), "fanout_partial_state")


def test_ready_join_is_projected_after_all_selected_evidence() -> None:
    state, _plan, fingerprint = generic_lifecycle.two_report_state()

    projection = _project(state)

    assert projection.candidate is not None
    assert projection.diagnostics == ()
    assert projection.candidate.kind == "join"
    assert projection.candidate.plan_fingerprint == fingerprint
    assert projection.candidate.declaration_id == generic_lifecycle.JOIN_ID
    assert projection.candidate.transition_input.join_id == generic_lifecycle.JOIN_ID
    assert projection.candidate.transition_input.source_artifact_id == min(
        artifact.artifact_id
        for artifact in state.artifacts.values()
        if str(artifact.schema_id) == generic_lifecycle.ALPHA_REPORT_SCHEMA_ID
    )


def test_join_waits_for_every_selected_fanout_target() -> None:
    state, _plan, _fingerprint = (
        generic_lifecycle.schema_covered_but_incomplete_report_state()
    )
    transition_input = JoinFromArtifact(
        "join-before-all-targets",
        join_id=generic_lifecycle.JOIN_ID,
        source_artifact_id="transition-observe-beta:artifact",
    )

    projection = _project(state)
    decision = decide(
        state,
        transition_input,
        generic_lifecycle.context(transition_input.input_id),
    )

    assert projection.candidate is None
    assert projection.diagnostics == ()
    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == "join_evidence_missing"


def test_join_accepts_multiple_artifacts_per_schema_across_distinct_targets() -> None:
    state, _plan, _fingerprint = generic_lifecycle.complete_multi_item_report_state()
    transition_input = JoinFromArtifact(
        "join-all-targets",
        join_id=generic_lifecycle.JOIN_ID,
        source_artifact_id="transition-observe-beta:artifact",
    )

    projection = _project(state)
    decision = decide(
        state,
        transition_input,
        generic_lifecycle.context(transition_input.input_id),
    )

    assert projection.diagnostics == ()
    assert projection.candidate is not None
    assert projection.candidate.kind == "join"
    assert decision.accepted is True
    assert sum(
        str(artifact.schema_id) == generic_lifecycle.ALPHA_REPORT_SCHEMA_ID
        for artifact in state.artifacts.values()
    ) == 2
    assert sum(
        str(artifact.schema_id) == generic_lifecycle.BETA_REPORT_SCHEMA_ID
        for artifact in state.artifacts.values()
    ) == 2


def test_join_counts_each_target_once_across_mutually_exclusive_actions() -> None:
    state, _plan, _fingerprint = generic_lifecycle.alternative_action_report_state(
        alpha_uses_alternative=False
    )
    source_artifact_id = min(
        artifact.artifact_id
        for artifact in state.artifacts.values()
        if str(artifact.schema_id) == generic_lifecycle.ALPHA_REPORT_SCHEMA_ID
    )
    transition_input = JoinFromArtifact(
        "join-alternative-actions",
        join_id=generic_lifecycle.JOIN_ID,
        source_artifact_id=source_artifact_id,
    )

    projection = _project(state)
    decision = decide(
        state,
        transition_input,
        generic_lifecycle.context(transition_input.input_id),
    )

    assert projection.diagnostics == ()
    assert projection.candidate is not None
    assert projection.candidate.kind == "join"
    assert decision.accepted is True


def test_join_requires_group_wide_required_schema_coverage() -> None:
    state, _plan, _fingerprint = generic_lifecycle.alternative_action_report_state(
        alpha_uses_alternative=True
    )
    source_artifact_id = min(
        artifact.artifact_id
        for artifact in state.artifacts.values()
        if str(artifact.schema_id) == generic_lifecycle.BETA_REPORT_SCHEMA_ID
    )
    transition_input = JoinFromArtifact(
        "join-alternative-actions-missing-schema",
        join_id=generic_lifecycle.JOIN_ID,
        source_artifact_id=source_artifact_id,
    )

    projection = _project(state)
    decision = decide(
        state,
        transition_input,
        generic_lifecycle.context(transition_input.input_id),
    )

    assert projection.candidate is None
    assert projection.diagnostics[0].reason_code == "join_evidence_mismatch"
    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == "join_evidence_mismatch"


def test_compiled_unrelated_same_schema_side_stage_is_a_nonparticipant() -> None:
    state, _plan, _fingerprint = generic_lifecycle.unrelated_side_report_state()
    projection = _project(state)

    assert projection.candidate is None
    assert projection.diagnostics == ()


def test_direct_join_refuses_unrelated_side_stage_as_wrong_source() -> None:
    state, _plan, _fingerprint = generic_lifecycle.unrelated_side_report_state()
    transition_input = JoinFromArtifact(
        "join-unrelated-side",
        join_id=generic_lifecycle.JOIN_ID,
        source_artifact_id="transition-observe-side:artifact",
    )

    decision = decide(
        state,
        transition_input,
        generic_lifecycle.context(
            transition_input.input_id,
            work_item_id="work-review-side",
            activation_id="activation-review-side",
        ),
    )

    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == "wrong_source_artifact"


def test_missing_selected_join_evidence_is_not_ready_in_both_paths() -> None:
    state, _plan, _fingerprint = generic_lifecycle.one_report_state()
    transition_input = JoinFromArtifact(
        "join-missing-beta",
        join_id=generic_lifecycle.JOIN_ID,
        source_artifact_id="transition-observe-alpha:artifact",
    )

    projection = _project(state)
    decision = decide(
        state,
        transition_input,
        generic_lifecycle.context(transition_input.input_id),
    )

    assert projection.candidate is None
    assert projection.diagnostics == ()
    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == "join_evidence_missing"


def test_corrupt_selected_join_evidence_has_one_owner_classification() -> None:
    ready, _plan, _fingerprint = generic_lifecycle.two_report_state()
    corrupt = _without_report_fanout_record(
        ready,
        generic_lifecycle.ALPHA_REPORT_SCHEMA_ID,
    )
    transition_input = JoinFromArtifact(
        "join-corrupt-alpha-provenance",
        join_id=generic_lifecycle.JOIN_ID,
        source_artifact_id="transition-observe-beta:artifact",
    )

    projection = _project(corrupt)
    decision = decide(
        corrupt,
        transition_input,
        generic_lifecycle.context(transition_input.input_id),
    )

    assert projection.candidate is None
    assert len(projection.diagnostics) == 1
    assert decision.accepted is False
    assert decision.refusal is not None
    assert projection.diagnostics[0].reason_code == decision.refusal.reason


def test_completed_join_group_does_not_poison_second_group() -> None:
    state, _plan, _fingerprint = generic_lifecycle.two_group_report_state()
    first = _project(state)
    assert first.candidate is not None
    assert "observe-a-" in first.candidate.source_artifact_id
    after_first = generic_lifecycle.apply_candidate(state, first.candidate)

    second = _project(after_first)

    assert second.diagnostics == ()
    assert second.candidate is not None
    assert "observe-b-" in second.candidate.source_artifact_id
    after_second = generic_lifecycle.apply_candidate(after_first, second.candidate)
    final = _project(after_second)
    assert final.candidate is None
    assert final.diagnostics == ()


def test_completed_join_group_keeps_second_group_direct_admissible() -> None:
    state, _plan, _fingerprint = generic_lifecycle.two_group_report_state()
    first_input, first_context = generic_lifecycle.join_transition_for_group("a")
    first_decision = decide(state, first_input, first_context)
    assert first_decision.accepted is True
    after_first = generic_lifecycle.apply_accepted_input(
        state,
        first_input,
        first_context,
    )
    second_input, second_context = generic_lifecycle.join_transition_for_group("b")

    second_decision = decide(after_first, second_input, second_context)

    assert second_decision.accepted is True


def test_non_join_route_from_selected_evidence_group_is_not_completion() -> None:
    state, _plan, _fingerprint = generic_lifecycle.two_report_state_from_source(
        generic_lifecycle.source_with_routed_alpha_report()
    )
    transition_input = JoinFromArtifact(
        "join-after-alpha-route",
        join_id=generic_lifecycle.JOIN_ID,
        source_artifact_id="transition-observe-beta:artifact",
    )

    projection = _project(state)
    decision = decide(
        state,
        transition_input,
        generic_lifecycle.context(transition_input.input_id),
    )

    assert projection.diagnostics == ()
    assert projection.candidate is not None
    assert projection.candidate.kind == "join"
    assert decision.accepted is True


def test_join_shaped_route_from_accepted_non_join_input_is_partial() -> None:
    state, _plan, _fingerprint = generic_lifecycle.two_report_state()
    candidate = _project(state).candidate
    assert candidate is not None
    completed = generic_lifecycle.apply_candidate(state, candidate)
    forged = generic_lifecycle.with_non_join_join_completion_authorship(completed)
    transition_input = JoinFromArtifact(
        "join-after-forged-completion",
        join_id=generic_lifecycle.JOIN_ID,
        source_artifact_id="transition-observe-beta:artifact",
    )

    projection = _project(forged)
    decision = decide(
        forged,
        transition_input,
        generic_lifecycle.context(transition_input.input_id),
    )

    assert projection.candidate is None
    assert projection.diagnostics[0].reason_code == "join_partial_state"
    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == "join_partial_state"


@pytest.mark.parametrize(
    "case",
    (
        "missing_route",
        "duplicate_route",
        "unscopable_source",
        "route_record_id",
        "non_join_creator",
        "receipt_payload_digest",
        "missing_event",
        "drifted_event",
        "missing_trace",
        "drifted_trace",
        "unscopable_non_join_route",
        "missing_route_transition_kind",
    ),
)
def test_join_transition_route_bijection_is_shared(case: str) -> None:
    state = generic_lifecycle.join_bijection_state(case)
    transition_input = JoinFromArtifact(
        f"join-bijection-{case}",
        join_id=generic_lifecycle.JOIN_ID,
        source_artifact_id="transition-observe-beta:artifact",
    )

    projection = _project(state)
    decision = decide(
        state,
        transition_input,
        generic_lifecycle.context(transition_input.input_id),
    )

    assert projection.candidate is None
    assert projection.diagnostics[0].reason_code == "join_partial_state"
    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == "join_partial_state"


def test_other_plan_join_completion_route_is_a_nonparticipant() -> None:
    (
        state,
        lower_fingerprint,
        higher_fingerprint,
    ) = generic_lifecycle.lower_plan_ready_with_higher_plan_joined_state()
    transition_input = JoinFromArtifact(
        "join-lower-plan",
        join_id=generic_lifecycle.JOIN_ID,
        source_artifact_id="transition-observe-lower-plan-beta-1:artifact",
    )

    projection = _project(state)
    decision = decide(
        state,
        transition_input,
        generic_lifecycle.context(
            transition_input.input_id,
            work_item_id="work-review-lower-plan",
            activation_id="activation-review-lower-plan",
        ),
    )

    assert lower_fingerprint < higher_fingerprint
    assert projection.diagnostics == ()
    assert projection.candidate is not None
    assert projection.candidate.plan_fingerprint == lower_fingerprint
    assert projection.candidate.kind == "join"
    assert decision.accepted is True


def test_missing_join_evidence_is_not_ready_without_refusal() -> None:
    state, _plan, _fingerprint = generic_lifecycle.one_report_state()

    projection = _project(state)

    assert projection.candidate is None
    assert projection.diagnostics == ()


def test_duplicate_or_mismatched_join_evidence_is_diagnostic() -> None:
    ready, _plan, _fingerprint = generic_lifecycle.two_report_state()

    duplicate = _project(generic_lifecycle.with_duplicate_alpha_report(ready))
    mismatched = _project(generic_lifecycle.with_mismatched_beta_report(ready))

    assert duplicate.candidate is None
    assert duplicate.diagnostics[0].reason_code == "wrong_source_artifact"
    assert mismatched.candidate is None
    assert mismatched.diagnostics[0].reason_code == "wrong_source_artifact"


@pytest.mark.parametrize(
    ("label", "mutate", "reason_code"),
    [
        (
            "wrong_schema",
            lambda state: _with_report_artifact(
                state,
                generic_lifecycle.ALPHA_REPORT_SCHEMA_ID,
                schema_id=generic_lifecycle.BETA_REPORT_SCHEMA_ID,
            ),
            "wrong_source_artifact",
        ),
        (
            "blank_correlation",
            lambda state: _with_report_payload(
                state,
                generic_lifecycle.ALPHA_REPORT_SCHEMA_ID,
                bundle_id="",
            ),
            "wrong_source_artifact",
        ),
        (
            "mismatched_correlation",
            lambda state: _with_report_payload(
                state,
                generic_lifecycle.BETA_REPORT_SCHEMA_ID,
                bundle_id="bundle-other",
            ),
            "wrong_source_artifact",
        ),
        (
            "artifact_work",
            lambda state: _with_report_artifact(
                state,
                generic_lifecycle.ALPHA_REPORT_SCHEMA_ID,
                work_item_id="wrong-work",
            ),
            "wrong_source_artifact",
        ),
        (
            "artifact_run",
            lambda state: _with_report_artifact(
                state,
                generic_lifecycle.ALPHA_REPORT_SCHEMA_ID,
                source_run_id="wrong-run",
            ),
            "wrong_source_artifact",
        ),
        (
            "artifact_action",
            lambda state: _with_report_artifact(
                state,
                generic_lifecycle.ALPHA_REPORT_SCHEMA_ID,
                source_action_id="wrong.action",
            ),
            "wrong_source_artifact",
        ),
        (
            "missing_fanout",
            lambda state: _without_report_fanout_record(
                state,
                generic_lifecycle.ALPHA_REPORT_SCHEMA_ID,
            ),
            "fanout_partial_state",
        ),
        (
            "foreign_fanout",
            lambda state: _with_report_fanout_record(
                state,
                generic_lifecycle.ALPHA_REPORT_SCHEMA_ID,
                source_artifact_id="foreign-artifact",
            ),
            "fanout_partial_state",
        ),
        (
            "multiple_fanout",
            lambda state: _duplicate_report_fanout_record(
                state,
                generic_lifecycle.ALPHA_REPORT_SCHEMA_ID,
            ),
            "fanout_partial_state",
        ),
        (
            "missing_dependency",
            lambda state: _without_report_dependency(
                state,
                generic_lifecycle.ALPHA_REPORT_SCHEMA_ID,
            ),
            "fanout_partial_state",
        ),
        (
            "foreign_dependency",
            lambda state: _with_report_dependency(
                state,
                generic_lifecycle.ALPHA_REPORT_SCHEMA_ID,
                dependency_work_item_id="foreign-work",
            ),
            "fanout_partial_state",
        ),
        (
            "multiple_dependency",
            lambda state: _duplicate_report_dependency(
                state,
                generic_lifecycle.ALPHA_REPORT_SCHEMA_ID,
            ),
            "fanout_partial_state",
        ),
        (
            "bundle_digest",
            lambda state: _with_report_fanout_record(
                state,
                generic_lifecycle.ALPHA_REPORT_SCHEMA_ID,
                source_artifact_digest=f"sha256:{'2' * 64}",
            ),
            "fanout_partial_state",
        ),
        (
            "bundle_action",
            lambda state: _with_bundle_artifact_for_report(
                state,
                generic_lifecycle.ALPHA_REPORT_SCHEMA_ID,
                source_action_id="wrong.action",
            ),
            "wrong_source_artifact",
        ),
        (
            "bundle_schema",
            lambda state: _with_bundle_artifact_for_report(
                state,
                generic_lifecycle.ALPHA_REPORT_SCHEMA_ID,
                schema_id=generic_lifecycle.ALPHA_REPORT_SCHEMA_ID,
            ),
            "wrong_source_artifact",
        ),
        (
            "bundle_close",
            lambda state: _with_bundle_closed_for_report(
                state,
                generic_lifecycle.ALPHA_REPORT_SCHEMA_ID,
                action_id="wrong.action",
            ),
            "wrong_source_aftermath",
        ),
    ],
)
def test_join_evidence_mutators_are_diagnostic(label, mutate, reason_code) -> None:
    state, _plan, _fingerprint = generic_lifecycle.two_report_state()

    _assert_diagnostic(mutate(state), reason_code)


@pytest.mark.parametrize(
    ("label", "mutate"),
    [
        (
            "missing_receipt",
            lambda state: _without_receipt(
                state,
                _join_route(state).created_by_input_id,
            ),
        ),
        (
            "missing_transition",
            lambda state: _without_transition(
                state,
                _join_route(state).created_by_input_id,
            ),
        ),
        (
            "refused_transition",
            lambda state: _with_transition(
                state,
                _join_route(state).created_by_input_id,
                accepted=False,
            ),
        ),
        (
            "duplicate_transition",
            lambda state: _duplicate_transition(
                state,
                _join_route(state).created_by_input_id,
            ),
        ),
        (
            "route_action",
            lambda state: _with_join_route(state, action_id="wrong.action"),
        ),
        (
            "route_input",
            lambda state: _with_join_route(state, created_by_input_id="wrong-input"),
        ),
        (
            "target_work_plan",
            lambda state: _with_join_target_work(
                state,
                ref=replace(
                    state.work_items[_join_route(state).target_work_item_id].ref,
                    plan_ref=_other_plan_ref(state),
                ),
            ),
        ),
        (
            "target_work_queue",
            lambda state: _with_join_target_work(
                state,
                queue_family_id="wrong_queue",
            ),
        ),
        (
            "target_work_lineage",
            lambda state: _with_join_target_work(state, lineage_id="wrong-lineage"),
        ),
        (
            "target_work_payload",
            lambda state: _with_join_target_work(
                state,
                payload={"bundle_id": "bundle-a", "items": ()},
            ),
        ),
        (
            "activation_work",
            lambda state: _with_join_target_activation(
                state,
                work_item_id="wrong-work",
            ),
        ),
        (
            "activation_stage",
            lambda state: _with_join_target_activation(
                state,
                stage_kind_id="wrong_stage",
            ),
        ),
        (
            "activation_runner",
            lambda state: _with_join_target_activation(
                state,
                runner_binding_id="wrong.runner",
            ),
        ),
        ("duplicate_route", _duplicate_join_route),
    ],
)
def test_completed_join_aftermath_mutators_are_diagnostic(label, mutate) -> None:
    state, _plan, _fingerprint = generic_lifecycle.two_report_state()
    candidate = _project(state).candidate
    assert candidate is not None
    joined = generic_lifecycle.apply_candidate(state, candidate)

    _assert_diagnostic(mutate(joined), "join_partial_state")


def test_operator_wait_records_are_unchanged_by_unrelated_lifecycle_projection() -> (
    None
):
    state, _plan, fingerprint = generic_lifecycle.origin_closed_state()
    work = state.work_items["work-origin"]
    run = state.runs["run-origin"]
    activation = state.activations[run.activation_id]
    wait = OperatorWaitRecord(
        wait_id="wait-unrelated",
        operator_wait_id="operator.wait.unrelated",
        source_action_id="lifecycle.operator_wait",
        lineage_id=work.lineage_id or "lineage",
        selected_plan_ref=work.ref.plan_ref,
        selected_plan_fingerprint=fingerprint,
        source_work_item_id=work.ref.work_item_id,
        source_activation_id=activation.activation_id,
        source_run_id=run.run_ref.run_id,
        source_stage_kind_id=activation.stage_kind_id,
        source_graph_node_id=activation.graph_node_id,
        source_queue_family_id=activation.queue_family_id,
        source_runner_binding_id=activation.runner_binding_id,
        source_artifact_id=None,
        status="active",
        created_input_id="operator-wait-input",
        created_input_payload_digest=f"sha256:{'3' * 64}",
        resolved_input_id=None,
        resolved_input_payload_digest=None,
        actor_id=None,
        actor_kind=None,
        resolution_kind=None,
    )
    with_wait = replace(state, operator_waits={wait.wait_id: wait})
    candidate = _project(with_wait).candidate
    assert candidate is not None

    after = generic_lifecycle.apply_candidate(with_wait, candidate)

    assert after.operator_waits == with_wait.operator_waits


def test_partial_lifecycle_aftermath_is_not_silently_skipped() -> None:
    state, _plan, _fingerprint = generic_lifecycle.origin_closed_state()
    first = _project(state).candidate
    assert first is not None
    after_first = generic_lifecycle.apply_candidate(state, first)
    partial = replace(after_first, work_dependencies={})

    projection = _project(partial)

    assert projection.candidate is None
    assert projection.diagnostics[0].reason_code == "fanout_partial_state"


def test_candidate_identity_is_stable_across_mapping_order() -> None:
    state, _plan, _fingerprint = generic_lifecycle.origin_closed_state()
    reordered = replace(
        state,
        work_items=dict(reversed(tuple(state.work_items.items()))),
        activations=dict(reversed(tuple(state.activations.items()))),
        runs=dict(reversed(tuple(state.runs.items()))),
        artifacts=dict(reversed(tuple(state.artifacts.items()))),
        closed_work_items=dict(reversed(tuple(state.closed_work_items.items()))),
    )

    left = _project(state).candidate
    right = _project(reordered).candidate

    assert left is not None
    assert right is not None
    assert right.transition_input.input_id == left.transition_input.input_id
    assert right.transition_context == left.transition_context
