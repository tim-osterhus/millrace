from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import fields, replace

import pytest

from millrace.contracts.compiled_plan import AuthorityValue, canonical_authority_bytes
from millrace.contracts.state import ArtifactRecord, RuntimeState
from millrace.contracts.transition import FanoutFromArtifact, JoinFromArtifact
from millrace.kernel import apply, decide
from millrace.kernel.join_policy import canonical_correlation_identity
from millrace.operator.dispatch import (
    DispatchProjectionError,
    build_dispatch_envelope_for_run,
)
from support import generic_lifecycle, vendor_selection


def _claim_award_decider_for_join(
    state: RuntimeState,
    *,
    input_id: str = "join-award-a",
    source_artifact_id: str | None = None,
    work_item_id: str = "work-award-a",
    activation_id: str = "activation-award-a",
    claim_suffix: str = "award-a",
) -> RuntimeState:
    transition_input = JoinFromArtifact(
        input_id,
        join_id=vendor_selection.JOIN_ID,
        source_artifact_id=source_artifact_id
        or vendor_selection.artifact_id_for("observe-conflict-a"),
    )
    decision = decide(
        state,
        transition_input,
        vendor_selection.context(
            input_id,
            work_item_id=work_item_id,
            activation_id=activation_id,
        ),
    )
    assert decision.accepted is True
    joined = apply(state, decision)
    return vendor_selection.claim_activation(
        joined,
        activation_id=activation_id,
        suffix=claim_suffix,
    )


def _selected_evidence(state: RuntimeState) -> Mapping[str, AuthorityValue]:
    envelope = build_dispatch_envelope_for_run(state=state, run_id="run-award-a")
    evidence = envelope.selected_join_evidence
    assert evidence is not None
    return evidence


def _artifact(
    state: RuntimeState,
    input_id: str,
) -> ArtifactRecord:
    return state.artifacts[vendor_selection.artifact_id_for(input_id)]


def _apply_packager_fanout(
    state: RuntimeState,
    *,
    fanout_id: str,
    suffix: str,
) -> RuntimeState:
    fanout_input = FanoutFromArtifact(
        f"fanout-{fanout_id.rsplit('.', maxsplit=1)[-1]}-{suffix}",
        fanout_id=fanout_id,
        source_artifact_id=vendor_selection.artifact_id_for(
            f"observe-packager-{suffix}"
        ),
    )
    decision = decide(
        state,
        fanout_input,
        vendor_selection.context(fanout_input.input_id),
    )
    assert decision.accepted is True
    return apply(state, decision)


def _evidence_artifact(
    state: RuntimeState,
    *,
    input_id: str,
    schema_id: str,
    item_key: str,
) -> Mapping[str, AuthorityValue]:
    artifact = _artifact(state, input_id)
    fanout_record = next(
        record
        for record in state.fanout_records.values()
        if record.target_work_item_id == artifact.work_item_id
    )
    return {
        "artifact_id": artifact.artifact_id,
        "artifact_schema_id": schema_id,
        "payload_digest": artifact.payload_digest,
        "payload": artifact.payload,
        "source_action_id": str(artifact.source_action_id),
        "source_run_id": artifact.source_run_id,
        "source_work_item_id": artifact.work_item_id,
        "fanout_id": str(fanout_record.fanout_id),
        "fanout_record_id": fanout_record.record_id,
        "item_key": item_key,
    }


def _expected_selected_evidence(
    state: RuntimeState,
    *,
    bundle_input_id: str = "observe-packager-a",
    correlation_value: str = "bundle-a",
    lineage_id: str = "work-request-a",
    artifacts: tuple[Mapping[str, AuthorityValue], ...] | None = None,
) -> Mapping[str, AuthorityValue]:
    bundle = _artifact(state, bundle_input_id)
    return {
        "record_kind": "selected_join_evidence",
        "schema_version": 1,
        "join_id": vendor_selection.JOIN_ID,
        "correlation_key": "bundle_id",
        "correlation_value": correlation_value,
        "correlation_identity": canonical_correlation_identity(correlation_value),
        "lineage_id": lineage_id,
        "bundle_artifact_id": bundle.artifact_id,
        "bundle_artifact_schema_id": "CandidateBundle",
        "bundle_artifact_digest": bundle.payload_digest,
        "required_artifact_schema_ids": ("ConflictReport", "RubricReport"),
        "evidence_artifacts": artifacts
        or (
            _evidence_artifact(
                state,
                input_id="observe-conflict-a",
                schema_id="ConflictReport",
                item_key="vendor_gamma",
            ),
            _evidence_artifact(
                state,
                input_id="observe-rubric-a",
                schema_id="RubricReport",
                item_key="vendor_gamma",
            ),
        ),
    }


def _without_join_target_route(state: RuntimeState) -> RuntimeState:
    run = state.runs["run-award-a"]
    return replace(
        state,
        activation_routes=tuple(
            route
            for route in state.activation_routes
            if route.target_work_item_id != run.work_item_id
            or route.target_activation_id != run.activation_id
        ),
    )


def _with_obscured_join_target_route(state: RuntimeState) -> RuntimeState:
    run = state.runs["run-award-a"]
    return replace(
        state,
        activation_routes=tuple(
            replace(
                route,
                action_id="corrupt-action",
                created_by_input_id="corrupt-input",
            )
            if route.target_work_item_id == run.work_item_id
            and route.target_activation_id == run.activation_id
            else route
            for route in state.activation_routes
        ),
    )


def _with_extra_non_join_target_route(state: RuntimeState) -> RuntimeState:
    run = state.runs["run-award-a"]
    route = next(
        candidate
        for candidate in state.activation_routes
        if candidate.target_work_item_id == run.work_item_id
        and candidate.target_activation_id == run.activation_id
    )
    source_artifact = state.artifacts[
        vendor_selection.artifact_id_for("observe-conflict-a")
    ]
    return replace(
        state,
        activation_routes=(
            *state.activation_routes,
            replace(
                route,
                record_id="extra-non-join-route",
                action_id=source_artifact.source_action_id,
                created_by_input_id=source_artifact.created_by_input_id,
            ),
        ),
    )


def _with_reversed_runtime_order(state: RuntimeState) -> RuntimeState:
    reversed_fields: dict[str, object] = {}
    for field in fields(state):
        value = getattr(state, field.name)
        if isinstance(value, Mapping):
            reversed_fields[field.name] = dict(reversed(tuple(value.items())))
        elif isinstance(value, tuple):
            reversed_fields[field.name] = tuple(reversed(value))
    return replace(state, **reversed_fields)


def _with_artifact_provenance_drift(
    state: RuntimeState,
    *,
    artifact_id: str,
    field: str,
) -> RuntimeState:
    artifact = state.artifacts[artifact_id]
    changes = {
        "created_by_input_id": "corrupt-observation-input",
    } if field == "created_by_input_id" else {
        "transition_id": "corrupt-observation-transition",
    }
    return replace(
        state,
        artifacts={
            **state.artifacts,
            artifact_id: replace(artifact, **changes),
        },
    )


def _with_artifact_action_authority_drift(
    state: RuntimeState,
    *,
    artifact_id: str,
    field: str,
) -> RuntimeState:
    artifact = state.artifacts[artifact_id]
    input_id = artifact.created_by_input_id
    runner_observations = state.runner_observations
    governance_events = state.governance_events
    traces = state.traces
    if field == "observation_action":
        observation = next(
            candidate
            for candidate in state.runner_observations.values()
            if candidate.created_by_input_id == input_id
        )
        runner_observations = {
            **state.runner_observations,
            observation.observation_id: replace(
                observation,
                payload={**observation.payload, "action_id": "wrong.action"},
            ),
        }
    if field in {"event_action", "both_audit_actions"}:
        governance_events = tuple(
            replace(event, action_id="wrong.action")
            if event.input_id == input_id
            else event
            for event in state.governance_events
        )
    if field in {"trace_action", "both_audit_actions"}:
        traces = tuple(
            replace(trace, action_id="wrong.action")
            if trace.input_id == input_id
            else trace
            for trace in state.traces
        )
    return replace(
        state,
        runner_observations=runner_observations,
        governance_events=governance_events,
        traces=traces,
    )


def _with_artifact_observation_drift(
    state: RuntimeState,
    *,
    artifact_id: str,
    field: str,
) -> RuntimeState:
    artifact = state.artifacts[artifact_id]
    observation = next(
        candidate
        for candidate in state.runner_observations.values()
        if candidate.created_by_input_id == artifact.created_by_input_id
    )
    changed = (
        replace(
            observation,
            payload={**observation.payload, "marker": "CORRUPT_MARKER"},
        )
        if field == "payload"
        else replace(observation, observed_at=1)
    )
    return replace(
        state,
        runner_observations={
            **state.runner_observations,
            observation.observation_id: changed,
        },
    )


def _with_generic_source_context_drift(
    state: RuntimeState,
    field: str,
) -> RuntimeState:
    artifact = state.artifacts["transition-observe-alpha:artifact"]
    run = state.runs[artifact.source_run_id]
    activation = state.activations[run.activation_id]
    if field == "artifact_stage":
        artifacts = {
            **state.artifacts,
            artifact.artifact_id: replace(
                artifact,
                source_stage_kind_id="wrong.stage",
            ),
        }
        return replace(state, artifacts=artifacts)
    if field == "artifact_graph":
        artifacts = {
            **state.artifacts,
            artifact.artifact_id: replace(
                artifact,
                source_graph_node_id="wrong.node",
            ),
        }
        return replace(state, artifacts=artifacts)
    if field in {"run_ref_work", "run_ref_generation"}:
        run_ref = replace(
            run.run_ref,
            work_item_id=(
                "wrong-work"
                if field == "run_ref_work"
                else run.run_ref.work_item_id
            ),
            generation=(
                run.run_ref.generation + 1
                if field == "run_ref_generation"
                else run.run_ref.generation
            ),
        )
        return replace(
            state,
            runs={
                **state.runs,
                run.run_ref.run_id: replace(run, run_ref=run_ref),
            },
        )
    if field == "run_runner":
        return replace(
            state,
            runs={
                **state.runs,
                run.run_ref.run_id: replace(
                    run,
                    runner_binding_id="wrong.runner",
                ),
            },
        )
    activation_changes = (
        {"claimed_by_run_id": None}
        if field == "activation_claim"
        else {"generation": activation.generation + 1}
    )
    return replace(
        state,
        activations={
            **state.activations,
            activation.activation_id: replace(activation, **activation_changes),
        },
    )


def test_join_created_dispatch_contains_exact_selected_evidence() -> None:
    state, _plan, _fingerprint = vendor_selection.award_decider_claimed_state()

    assert _selected_evidence(state) == _expected_selected_evidence(state)


def test_fixed_operator_required_path_preserves_dispatch_evidence() -> None:
    state, plan, fingerprint = vendor_selection.admit_vendor_selection()
    state = vendor_selection.enqueue_purchase_request(
        state,
        suffix="fixed",
        payload=vendor_selection.purchase_request_payload(
            "request-fixed",
            approval_policy_hint="operator_required",
        ),
    )
    state = vendor_selection.progress_to_candidate_packager(
        state,
        plan=plan,
        fingerprint=fingerprint,
        suffix="fixed",
    )
    requirement = _artifact(state, "observe-freezer-fixed").payload
    source_bundle = state.work_items["work-packager-fixed"].payload

    assert requirement["approval_policy_hint"] == "operator_required"
    assert requirement["conflict_rules"] == ("no_blocked_conflicts",)
    assert source_bundle["approval_policy_hint"] == "operator_required"
    assert source_bundle["conflict_rules"] == ("no_blocked_conflicts",)
    assert source_bundle["candidate_vendors"] == (
        {
            "candidate_id": "vendor_gamma",
            "vendor_label": "Vendor Gamma",
            "capabilities": ("standard_office_supplies", "net30_invoice"),
            "budget_band": "medium",
            "catalog_ref": "vendor_selection.catalog.vendor_gamma",
            "conflict_status": "clear",
        },
    )
    state = vendor_selection.claim_activation(
        state,
        activation_id="activation-packager-fixed",
        suffix="packager-fixed",
    )
    state = vendor_selection.apply_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-packager-fixed",
        action_id="vendor_selection.candidate_packager.candidates_ready",
        input_id="observe-packager-fixed",
        artifact_payload=source_bundle,
    )
    for fanout_id in (
        "vendor_selection.candidate_packager.rubric_fanout",
        "vendor_selection.candidate_packager.conflict_fanout",
    ):
        state = _apply_packager_fanout(
            state,
            fanout_id=fanout_id,
            suffix="fixed",
        )

    for stage_id, suffix, action_id, payload in (
        (
            "rubric_evaluator",
            "rubric-fixed",
            "vendor_selection.rubric_evaluator.rubric_complete",
            vendor_selection.rubric_report_payload(
                "bundle-fixed",
                candidate_id="vendor_gamma",
            ),
        ),
        (
            "conflict_checker",
            "conflict-fixed",
            "vendor_selection.conflict_checker.conflict_complete",
            vendor_selection.conflict_report_payload("bundle-fixed"),
        ),
    ):
        activation_id = vendor_selection.report_branch_activation_id(state, stage_id)
        state = vendor_selection.claim_activation(
            state,
            activation_id=activation_id,
            suffix=suffix,
        )
        dispatch = build_dispatch_envelope_for_run(
            state=state,
            run_id=f"run-{suffix}",
        )
        generated_source = dispatch.governance_context["generated_work_source"]
        assert dispatch.work_item_payload == source_bundle
        assert generated_source["item_key"] == "vendor_gamma"
        assert generated_source["source_artifact_id"] == (
            vendor_selection.artifact_id_for("observe-packager-fixed")
        )
        state = vendor_selection.apply_observation(
            state,
            plan=plan,
            fingerprint=fingerprint,
            run_id=f"run-{suffix}",
            action_id=action_id,
            input_id=f"observe-{suffix}",
            artifact_payload=payload,
        )

    state = _claim_award_decider_for_join(
        state,
        input_id="join-award-fixed",
        source_artifact_id=vendor_selection.artifact_id_for(
            "observe-conflict-fixed"
        ),
        work_item_id="work-award-fixed",
        activation_id="activation-award-fixed",
        claim_suffix="award-fixed",
    )
    dispatch = build_dispatch_envelope_for_run(state=state, run_id="run-award-fixed")
    joined_evidence = dispatch.selected_join_evidence

    assert dispatch.work_item_payload == source_bundle
    assert joined_evidence is not None
    assert joined_evidence["bundle_artifact_id"] == (
        vendor_selection.artifact_id_for("observe-packager-fixed")
    )
    assert joined_evidence["bundle_artifact_digest"] == (
        _artifact(state, "observe-packager-fixed").payload_digest
    )
    assert joined_evidence["evidence_artifacts"] == (
        _evidence_artifact(
            state,
            input_id="observe-conflict-fixed",
            schema_id="ConflictReport",
            item_key="vendor_gamma",
        ),
        _evidence_artifact(
            state,
            input_id="observe-rubric-fixed",
            schema_id="RubricReport",
            item_key="vendor_gamma",
        ),
    )


@pytest.mark.parametrize("field", ("created_by_input_id", "transition_id"))
@pytest.mark.parametrize(
    "artifact_input_id",
    ("observe-packager-a", "observe-conflict-a"),
    ids=("bundle", "evidence"),
)
def test_join_dispatch_refuses_artifact_provenance_drift(
    artifact_input_id: str,
    field: str,
) -> None:
    state, _plan, _fingerprint = vendor_selection.award_decider_claimed_state()

    with pytest.raises(DispatchProjectionError) as exc_info:
        build_dispatch_envelope_for_run(
            state=_with_artifact_provenance_drift(
                state,
                artifact_id=vendor_selection.artifact_id_for(artifact_input_id),
                field=field,
            ),
            run_id="run-award-a",
        )

    assert exc_info.value.reason == "join_partial_state", field
    assert exc_info.value.details["detail"] == "wrong_source_artifact", field


@pytest.mark.parametrize(
    "field",
    (
        "observation_action",
        "event_action",
        "trace_action",
        "both_audit_actions",
    ),
)
@pytest.mark.parametrize(
    "artifact_input_id",
    ("observe-packager-a", "observe-conflict-a"),
    ids=("bundle", "evidence"),
)
def test_join_dispatch_refuses_artifact_action_authority_drift(
    artifact_input_id: str,
    field: str,
) -> None:
    state, _plan, _fingerprint = vendor_selection.award_decider_claimed_state()

    with pytest.raises(DispatchProjectionError) as exc_info:
        build_dispatch_envelope_for_run(
            state=_with_artifact_action_authority_drift(
                state,
                artifact_id=vendor_selection.artifact_id_for(artifact_input_id),
                field=field,
            ),
            run_id="run-award-a",
        )

    assert exc_info.value.reason == "join_partial_state", field
    assert exc_info.value.details["detail"] == "wrong_source_artifact", field


@pytest.mark.parametrize("field", ("payload", "observed_at"))
@pytest.mark.parametrize(
    "artifact_input_id",
    ("observe-packager-a", "observe-conflict-a"),
    ids=("bundle", "evidence"),
)
def test_join_dispatch_refuses_bundle_and_evidence_observation_payload_or_observed_at_drift(  # noqa: E501
    artifact_input_id: str,
    field: str,
) -> None:
    state, _plan, _fingerprint = vendor_selection.award_decider_claimed_state()

    with pytest.raises(DispatchProjectionError) as exc_info:
        build_dispatch_envelope_for_run(
            state=_with_artifact_observation_drift(
                state,
                artifact_id=vendor_selection.artifact_id_for(artifact_input_id),
                field=field,
            ),
            run_id="run-award-a",
        )

    assert exc_info.value.reason == "join_partial_state", field
    assert exc_info.value.details["detail"] == "wrong_source_artifact", field


def test_join_evidence_is_stable_across_runtime_insertion_order() -> None:
    state, _plan, _fingerprint = vendor_selection.award_decider_claimed_state()

    expected = _selected_evidence(state)
    reordered = _selected_evidence(_with_reversed_runtime_order(state))

    assert reordered == expected
    assert canonical_authority_bytes(reordered) == canonical_authority_bytes(expected)


def test_join_evidence_is_stable_across_qualifying_source_artifacts() -> None:
    state, _plan, _fingerprint = vendor_selection.two_report_state()
    from_conflict = _claim_award_decider_for_join(state)
    from_rubric = _claim_award_decider_for_join(
        state,
        source_artifact_id=vendor_selection.artifact_id_for("observe-rubric-a"),
    )

    assert _selected_evidence(from_rubric) == _selected_evidence(from_conflict)


def test_join_dispatch_includes_only_emitted_alternative_action_artifact() -> None:
    state, plan, _fingerprint = generic_lifecycle.alternative_action_report_state(
        alpha_uses_alternative=False
    )
    emitted = next(
        artifact
        for artifact in state.artifacts.values()
        if str(artifact.source_stage_kind_id) == "alpha_stage"
    )
    alternative = next(
        action
        for action in plan.terminal_actions
        if action.stage_kind_id == emitted.source_stage_kind_id
        and action.id != emitted.source_action_id
    )
    joined = generic_lifecycle.apply_join(
        state,
        input_id="join-alternative-actions-dispatch",
    )
    claimed = generic_lifecycle.claim_activation(
        joined,
        activation_id="activation-review",
        suffix="review",
    )

    selected = build_dispatch_envelope_for_run(
        state=claimed,
        run_id="run-review",
    ).selected_join_evidence
    assert selected is not None
    evidence_artifacts = selected["evidence_artifacts"]
    assert isinstance(evidence_artifacts, tuple)
    emitted_rows = tuple(
        row
        for row in evidence_artifacts
        if isinstance(row, Mapping)
        and row["source_work_item_id"] == emitted.work_item_id
    )

    assert len(emitted_rows) == 1
    assert emitted_rows[0]["artifact_id"] == emitted.artifact_id
    assert emitted_rows[0]["artifact_schema_id"] == str(emitted.schema_id)
    assert emitted_rows[0]["source_action_id"] == str(emitted.source_action_id)
    assert all(
        not isinstance(row, Mapping)
        or row["source_action_id"] != str(alternative.id)
        for row in evidence_artifacts
    )
    assert all(
        not isinstance(row, Mapping)
        or row["source_work_item_id"] != emitted.work_item_id
        or row["artifact_schema_id"] != str(alternative.artifact_schema_id)
        for row in evidence_artifacts
    )


@pytest.mark.parametrize(
    "field",
    (
        "artifact_stage",
        "artifact_graph",
        "run_ref_work",
        "run_ref_generation",
        "run_runner",
        "activation_claim",
        "activation_generation",
    ),
)
def test_join_dispatch_refuses_source_context_authority_drift(field: str) -> None:
    state, _plan, _fingerprint = generic_lifecycle.two_report_state()
    joined = generic_lifecycle.apply_join(
        state,
        input_id="join-source-context-dispatch",
    )
    claimed = generic_lifecycle.claim_activation(
        joined,
        activation_id="activation-review",
        suffix="review-source-context",
    )

    with pytest.raises(DispatchProjectionError) as exc_info:
        build_dispatch_envelope_for_run(
            state=_with_generic_source_context_drift(claimed, field),
            run_id="run-review-source-context",
        )

    assert exc_info.value.reason == "join_partial_state", field
    assert exc_info.value.details["detail"] == "wrong_source_artifact", field


def test_join_dispatch_does_not_merge_evidence_into_work_item_payload() -> None:
    state, _plan, _fingerprint = vendor_selection.award_decider_claimed_state()
    envelope = build_dispatch_envelope_for_run(state=state, run_id="run-award-a")

    assert envelope.work_item_payload == vendor_selection.candidate_bundle_payload(
        "bundle-a",
        request_id="request-a",
    )
    assert "required_evidence_refs" not in envelope.work_item_payload
    assert envelope.selected_join_evidence == _expected_selected_evidence(state)


def test_join_dispatch_preserves_repeated_schema_slots_and_correlation_groups(
) -> None:
    state, _plan, _fingerprint = generic_lifecycle.two_group_report_state()
    join_input, join_context = generic_lifecycle.join_transition_for_group(
        "a",
        input_id="join-dispatch-group-a",
    )
    decision = decide(state, join_input, join_context)
    assert decision.accepted is True
    joined = apply(state, decision)
    claimed = generic_lifecycle.claim_activation(
        joined,
        activation_id=join_context.activation_id,
        suffix="review-group-a",
    )
    envelope = build_dispatch_envelope_for_run(
        state=claimed,
        run_id="run-review-group-a",
    )
    selected = envelope.selected_join_evidence
    assert selected is not None
    evidence = selected["evidence_artifacts"]
    assert isinstance(evidence, tuple)
    report_schema_ids = {
        generic_lifecycle.ALPHA_REPORT_SCHEMA_ID,
        generic_lifecycle.BETA_REPORT_SCHEMA_ID,
    }
    group_a_artifact_ids = {
        artifact.artifact_id
        for artifact in state.artifacts.values()
        if str(artifact.schema_id) in report_schema_ids
        and artifact.payload.get("bundle_id") == "bundle-a"
    }
    group_b_artifact_ids = {
        artifact.artifact_id
        for artifact in state.artifacts.values()
        if str(artifact.schema_id) in report_schema_ids
        and artifact.payload.get("bundle_id") == "bundle-b"
    }
    actual_artifact_ids = {
        row["artifact_id"] for row in evidence if isinstance(row, Mapping)
    }

    assert selected["correlation_value"] == "bundle-a"
    assert len(evidence) == 4
    assert actual_artifact_ids == group_a_artifact_ids
    assert actual_artifact_ids.isdisjoint(group_b_artifact_ids)
    assert tuple(
        row["artifact_schema_id"]
        for row in evidence
        if isinstance(row, Mapping)
    ) == (
        generic_lifecycle.ALPHA_REPORT_SCHEMA_ID,
        generic_lifecycle.ALPHA_REPORT_SCHEMA_ID,
        generic_lifecycle.BETA_REPORT_SCHEMA_ID,
        generic_lifecycle.BETA_REPORT_SCHEMA_ID,
    )
    assert {
        row["item_key"] for row in evidence if isinstance(row, Mapping)
    } == {"one", "two"}


def test_join_dispatch_ignores_unrelated_same_schema_artifacts() -> None:
    state, _plan, _fingerprint = vendor_selection.award_decider_claimed_state()
    rubric = _artifact(state, "observe-rubric-a")
    sourcer = state.runs["run-sourcer-a"]
    unrelated = replace(
        rubric,
        artifact_id="unrelated-rubric-same-schema",
        work_item_id=sourcer.work_item_id,
        created_by_input_id="unrelated-rubric-input",
        source_run_id=sourcer.run_ref.run_id,
        transition_id="transition-unrelated-rubric",
    )
    with_unrelated = replace(
        state,
        artifacts={**state.artifacts, unrelated.artifact_id: unrelated},
    )

    assert _selected_evidence(with_unrelated) == _expected_selected_evidence(
        with_unrelated
    )


@pytest.mark.parametrize(
    ("label", "mutate"),
    (
        (
            "extra_non_join_route",
            _with_extra_non_join_target_route,
        ),
        (
            "missing_route",
            _without_join_target_route,
        ),
        (
            "double_mutated_route",
            _with_obscured_join_target_route,
        ),
        (
            "duplicate_route",
            lambda state: replace(
                state,
                activation_routes=(
                    *state.activation_routes,
                    replace(
                        next(
                            route
                            for route in state.activation_routes
                            if route.created_by_input_id == "join-award-a"
                        ),
                        record_id="duplicate-join-route",
                    ),
                ),
            ),
        ),
        (
            "wrong_route_action",
            lambda state: replace(
                state,
                activation_routes=tuple(
                    replace(route, action_id="wrong-action")
                    if route.created_by_input_id == "join-award-a"
                    else route
                    for route in state.activation_routes
                ),
            ),
        ),
        (
            "wrong_route_creator",
            lambda state: replace(
                state,
                activation_routes=tuple(
                    replace(route, created_by_input_id="wrong-input")
                    if route.created_by_input_id == "join-award-a"
                    else route
                    for route in state.activation_routes
                ),
            ),
        ),
        (
            "wrong_route_source",
            lambda state: replace(
                state,
                activation_routes=tuple(
                    replace(route, source_work_item_id="wrong-work")
                    if route.created_by_input_id == "join-award-a"
                    else route
                    for route in state.activation_routes
                ),
            ),
        ),
        (
            "wrong_route_target",
            lambda state: replace(
                state,
                activation_routes=tuple(
                    replace(route, target_work_item_id="wrong-work")
                    if route.created_by_input_id == "join-award-a"
                    else route
                    for route in state.activation_routes
                ),
            ),
        ),
        (
            "wrong_source_plan",
            lambda state: replace(
                state,
                runs={
                    **state.runs,
                    "run-conflict-a": replace(
                        state.runs["run-conflict-a"],
                        run_ref=replace(
                            state.runs["run-conflict-a"].run_ref,
                            plan_ref=replace(
                                state.runs["run-conflict-a"].run_ref.plan_ref,
                                authority_fingerprint=(
                                    state.runs[
                                        "run-conflict-a"
                                    ].run_ref.plan_ref.authority_fingerprint
                                    + ":other"
                                ),
                            ),
                        ),
                    ),
                },
            ),
        ),
    ),
)
def test_join_dispatch_refuses_corrupt_completion_authority(
    label: str,
    mutate: Callable[[RuntimeState], RuntimeState],
) -> None:
    state, _plan, _fingerprint = vendor_selection.award_decider_claimed_state()

    with pytest.raises(DispatchProjectionError) as exc_info:
        build_dispatch_envelope_for_run(
            state=mutate(state),
            run_id="run-award-a",
        )

    assert exc_info.value.reason == "join_partial_state", label


def test_non_join_dispatch_has_no_selected_join_evidence() -> None:
    normal, _plan, _fingerprint = vendor_selection.packager_claimed_state("a")
    fanout, _plan, _fingerprint = vendor_selection.one_report_state()
    conflict_activation = vendor_selection.report_branch_activation_id(
        fanout,
        "conflict_checker",
    )
    fanout = vendor_selection.claim_activation(
        fanout,
        activation_id=conflict_activation,
        suffix="conflict-a",
    )

    assert (
        build_dispatch_envelope_for_run(
            state=normal,
            run_id="run-packager-a",
        ).selected_join_evidence
        is None
    )
    assert (
        build_dispatch_envelope_for_run(
            state=fanout,
            run_id="run-conflict-a",
        ).selected_join_evidence
        is None
    )
