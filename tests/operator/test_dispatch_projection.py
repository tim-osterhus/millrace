from __future__ import annotations

from collections.abc import Mapping
from dataclasses import fields, replace

import pytest

from millrace.contracts import ClaimWork
from millrace.contracts.compiled_plan import (
    SelectedWorkflowPackageAssetPin,
    SelectedWorkflowPackagePin,
    canonical_authority_bytes,
)
from millrace.contracts.state import (
    ClosedWorkItemRecord,
    DispatchSuspensionRecord,
    PauseRecord,
    RunnerObservationRecord,
)
from millrace.contracts.transition import JoinFromArtifact
from millrace.contracts.workflow_package import asset_digest_for_bytes
from millrace.kernel import apply, decide
from millrace.operator.dispatch import (
    DispatchProjectionError,
    list_ready_dispatch_candidates,
    ready_diagnostic_from_claim_refusal,
)
from millrace.operator.dispatch import (
    build_dispatch_envelope_for_run as _build_dispatch_envelope_for_run,
)
from millrace.testing import (
    deterministic_context,
    fake_runner_dispatch_envelope_for_run,
    fake_runner_session_state,
)
from support import generic_lifecycle


def build_dispatch_envelope_for_run(*, state, run_id):
    return _build_dispatch_envelope_for_run(
        state=fake_runner_session_state(state=state, run_id=run_id),
        run_id=run_id,
    )


def _claim_origin(state):
    return apply(
        state,
        decide(
            state,
            ClaimWork("claim-origin", activation_id="activation-origin"),
            deterministic_context(
                transition_id="transition-claim-origin",
                run_id="run-origin",
                claim_id="claim-origin",
                fencing_token="fence-origin",
            ),
        ),
    )


def _claimed_fanout_state():
    state, _plan, _fingerprint = generic_lifecycle.two_complete_fanouts_state()
    activation_id = generic_lifecycle.branch_activation_id(state, "beta_stage")
    state = generic_lifecycle.claim_activation(
        state,
        activation_id=activation_id,
        suffix="beta-dispatch",
    )
    return state


def _with_fanout_source_artifact_provenance_drift(state, *, field: str):
    artifact_id = generic_lifecycle.source_artifact_id()
    artifact = state.artifacts[artifact_id]
    changes = (
        {
            "created_by_input_id": "corrupt-observation-input",
        }
        if field == "created_by_input_id"
        else {
            "transition_id": "corrupt-observation-transition",
        }
    )
    return replace(
        state,
        artifacts={
            **state.artifacts,
            artifact_id: replace(artifact, **changes),
        },
    )


def _with_fanout_source_action_authority_drift(state, *, field: str):
    artifact = state.artifacts[generic_lifecycle.source_artifact_id()]
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


def _with_fanout_source_observation_drift(state, *, field: str):
    artifact = state.artifacts[generic_lifecycle.source_artifact_id()]
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


def _without_fanout_record_and_route(state, *, corrupt_route: bool):
    run = state.runs["run-beta-dispatch"]
    target_route = next(
        route
        for route in state.activation_routes
        if route.target_work_item_id == run.work_item_id
        and route.target_activation_id == run.activation_id
    )
    routes = tuple(
        replace(
            route,
            action_id="corrupt-action",
            created_by_input_id="corrupt-input",
        )
        if route == target_route and corrupt_route
        else route
        for route in state.activation_routes
        if route != target_route or corrupt_route
    )
    return replace(
        state,
        activation_routes=routes,
        fanout_records={
            record_id: record
            for record_id, record in state.fanout_records.items()
            if record.target_work_item_id != run.work_item_id
            and record.target_activation_id != run.activation_id
        },
    )


def _with_fully_drifted_fanout_target(state):
    run = state.runs["run-beta-dispatch"]
    work_item = state.work_items[run.work_item_id]
    activation = state.activations[run.activation_id]
    drifted_work_item_id = "drifted-work"
    drifted_activation_id = "drifted-activation"
    drifted_work_item = replace(
        work_item,
        ref=replace(work_item.ref, work_item_id=drifted_work_item_id),
    )
    drifted_activation = replace(
        activation,
        activation_id=drifted_activation_id,
        work_item_id=drifted_work_item_id,
    )
    drifted_run = replace(
        run,
        run_ref=replace(run.run_ref, work_item_id=drifted_work_item_id),
        work_item_id=drifted_work_item_id,
        activation_id=drifted_activation_id,
    )
    return replace(
        state,
        work_items={
            **{
                work_id: candidate
                for work_id, candidate in state.work_items.items()
                if work_id != work_item.ref.work_item_id
            },
            drifted_work_item_id: drifted_work_item,
        },
        activations={
            **{
                activation_id: candidate
                for activation_id, candidate in state.activations.items()
                if activation_id != activation.activation_id
            },
            drifted_activation_id: drifted_activation,
        },
        runs={**state.runs, run.run_ref.run_id: drifted_run},
        activation_routes=tuple(
            route
            for route in state.activation_routes
            if route.target_work_item_id != work_item.ref.work_item_id
            and route.target_activation_id != activation.activation_id
        ),
        fanout_records={
            record_id: record
            for record_id, record in state.fanout_records.items()
            if record.target_work_item_id != work_item.ref.work_item_id
            and record.target_activation_id != activation.activation_id
        },
        work_dependencies={
            dependency_id: dependency
            for dependency_id, dependency in state.work_dependencies.items()
            if dependency.dependent_work_item_id != work_item.ref.work_item_id
        },
    )


def _with_join_source_context_drift(state, field: str):
    artifact = state.artifacts["transition-observe-alpha:artifact"]
    run = state.runs[artifact.source_run_id]
    activation = state.activations[run.activation_id]
    if field == "artifact_stage":
        return replace(
            state,
            artifacts={
                **state.artifacts,
                artifact.artifact_id: replace(
                    artifact,
                    source_stage_kind_id="wrong.stage",
                ),
            },
        )
    if field == "artifact_graph":
        return replace(
            state,
            artifacts={
                **state.artifacts,
                artifact.artifact_id: replace(
                    artifact,
                    source_graph_node_id="wrong.node",
                ),
            },
        )
    if field in {"run_ref_work", "run_ref_generation"}:
        run_ref = replace(
            run.run_ref,
            work_item_id=(
                "wrong-work" if field == "run_ref_work" else run.run_ref.work_item_id
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


def _claimed_join_state(
    *, source_artifact_id: str = "transition-observe-beta:artifact"
):
    state, _plan, _fingerprint = generic_lifecycle.complete_multi_item_report_state()
    transition_input = JoinFromArtifact(
        "join-dispatch",
        join_id=generic_lifecycle.JOIN_ID,
        source_artifact_id=source_artifact_id,
    )
    joined = generic_lifecycle.apply_accepted_input(
        state,
        transition_input,
        generic_lifecycle.context(
            transition_input.input_id,
            work_item_id="work-review-dispatch",
            activation_id="activation-review-dispatch",
        ),
    )
    return generic_lifecycle.claim_activation(
        joined,
        activation_id="activation-review-dispatch",
        suffix="review-dispatch",
    )


def _with_join_artifact_provenance_drift(state, *, artifact_id: str, field: str):
    artifact = state.artifacts[artifact_id]
    changes = (
        {"created_by_input_id": "corrupt-observation-input"}
        if field == "created_by_input_id"
        else {"transition_id": "corrupt-observation-transition"}
    )
    return replace(
        state,
        artifacts={
            **state.artifacts,
            artifact_id: replace(artifact, **changes),
        },
    )


def _with_join_artifact_action_authority_drift(
    state,
    *,
    artifact_id: str,
    field: str,
):
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


def _with_join_artifact_observation_drift(
    state,
    *,
    artifact_id: str,
    field: str,
):
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


def _selected_join_evidence(state):
    evidence = build_dispatch_envelope_for_run(
        state=state,
        run_id="run-review-dispatch",
    ).selected_join_evidence
    assert evidence is not None
    return evidence


def _with_reversed_runtime_order(state):
    reversed_fields: dict[str, object] = {}
    for field in fields(state):
        value = getattr(state, field.name)
        if isinstance(value, Mapping):
            reversed_fields[field.name] = dict(reversed(tuple(value.items())))
        elif isinstance(value, tuple):
            reversed_fields[field.name] = tuple(reversed(value))
    return replace(state, **reversed_fields)


def _with_extra_fanout_target_route(state, *, shared_target: str):
    run = state.runs["run-beta-dispatch"]
    work_item = state.work_items[run.work_item_id]
    activation = state.activations[run.activation_id]
    route = next(
        candidate
        for candidate in state.activation_routes
        if candidate.target_work_item_id == work_item.ref.work_item_id
        and candidate.target_activation_id == activation.activation_id
    )
    admitted = state.admitted_plans[run.run_ref.plan_ref.authority_fingerprint]
    non_fanout_action_id = next(
        action.id
        for action in admitted.selected_plan.terminal_actions
        if action.id != route.action_id
    )
    source_artifact = state.artifacts[
        next(
            record.source_artifact_id
            for record in state.fanout_records.values()
            if record.target_work_item_id == work_item.ref.work_item_id
        )
    ]
    extra_work_item_id = "extra-route-work"
    extra_activation_id = "extra-route-activation"
    extra_work_item = replace(
        work_item,
        ref=replace(work_item.ref, work_item_id=extra_work_item_id),
        created_by_input_id=source_artifact.created_by_input_id,
    )
    extra_activation = replace(
        activation,
        activation_id=extra_activation_id,
        work_item_id=(
            work_item.ref.work_item_id
            if shared_target == "work"
            else extra_work_item_id
        ),
        created_by_input_id=source_artifact.created_by_input_id,
        claimed_by_run_id=None,
    )
    extra_route = replace(
        route,
        record_id=f"extra-{shared_target}-route",
        action_id=non_fanout_action_id,
        target_work_item_id=(
            work_item.ref.work_item_id
            if shared_target == "work"
            else extra_work_item_id
        ),
        target_activation_id=(
            extra_activation_id if shared_target == "work" else activation.activation_id
        ),
        created_by_input_id=source_artifact.created_by_input_id,
    )
    return replace(
        state,
        work_items=(
            state.work_items
            if shared_target == "work"
            else {**state.work_items, extra_work_item_id: extra_work_item}
        ),
        activations=(
            {**state.activations, extra_activation_id: extra_activation}
            if shared_target == "work"
            else state.activations
        ),
        activation_routes=(*state.activation_routes, extra_route),
    )


def test_production_and_fake_dispatch_match_for_generated_fanout_work() -> None:
    state = _claimed_fanout_state()

    production = build_dispatch_envelope_for_run(
        state=state,
        run_id="run-beta-dispatch",
    )
    fake = fake_runner_dispatch_envelope_for_run(
        state=state,
        run_id="run-beta-dispatch",
    )

    assert production.payload() == fake.payload()
    source = production.governance_context["generated_work_source"]
    assert source["item_key"] == "one"
    assert source["source_artifact_id"] == generic_lifecycle.source_artifact_id()
    assert production.selected_join_evidence is None


def test_fanout_dispatch_preserves_omitted_optional_payload_fields() -> None:
    state, _plan, _fingerprint = (
        generic_lifecycle.accepted_terminal_optional_omission_fanout_state()
    )
    activation_id = generic_lifecycle.branch_activation_id(state, "alpha_stage")
    state = generic_lifecycle.claim_activation(
        state,
        activation_id=activation_id,
        suffix="optional-alpha",
    )

    production = build_dispatch_envelope_for_run(
        state=state,
        run_id="run-optional-alpha",
    )
    fake = fake_runner_dispatch_envelope_for_run(
        state=state,
        run_id="run-optional-alpha",
    )

    assert production.payload() == fake.payload()
    assert "note" not in production.work_item_payload


@pytest.mark.parametrize("field", ("created_by_input_id", "transition_id"))
def test_fanout_dispatch_refuses_source_artifact_provenance_drift(
    field: str,
) -> None:
    state = _claimed_fanout_state()

    with pytest.raises(DispatchProjectionError) as exc_info:
        build_dispatch_envelope_for_run(
            state=_with_fanout_source_artifact_provenance_drift(
                state,
                field=field,
            ),
            run_id="run-beta-dispatch",
        )

    assert exc_info.value.reason == "wrong_source_artifact", field
    assert exc_info.value.details["detail"] == "artifact_source_authority", field


@pytest.mark.parametrize(
    "field",
    (
        "observation_action",
        "event_action",
        "trace_action",
        "both_audit_actions",
    ),
)
def test_fanout_dispatch_refuses_source_action_authority_drift(
    field: str,
) -> None:
    state = _claimed_fanout_state()

    with pytest.raises(DispatchProjectionError) as exc_info:
        build_dispatch_envelope_for_run(
            state=_with_fanout_source_action_authority_drift(
                state,
                field=field,
            ),
            run_id="run-beta-dispatch",
        )

    assert exc_info.value.reason == "wrong_source_artifact", field
    assert exc_info.value.details["detail"] == (
        "receipt_authority" if field == "observation_action" else "audit_authority"
    ), field


@pytest.mark.parametrize("field", ("payload", "observed_at"))
def test_fanout_dispatch_refuses_observation_payload_or_observed_at_drift(
    field: str,
) -> None:
    state = _claimed_fanout_state()

    with pytest.raises(DispatchProjectionError) as exc_info:
        build_dispatch_envelope_for_run(
            state=_with_fanout_source_observation_drift(state, field=field),
            run_id="run-beta-dispatch",
        )

    assert exc_info.value.reason == "wrong_source_artifact", field
    assert exc_info.value.details["detail"] == "receipt_authority", field


def test_fanout_dispatch_refuses_missing_record_and_route() -> None:
    state = _claimed_fanout_state()

    try:
        build_dispatch_envelope_for_run(
            state=_without_fanout_record_and_route(state, corrupt_route=False),
            run_id="run-beta-dispatch",
        )
    except DispatchProjectionError as exc:
        assert exc.reason == "fanout_partial_state"
    else:  # pragma: no cover - assertion guard
        raise AssertionError("missing fanout record and route were accepted")


def test_fanout_dispatch_refuses_missing_record_and_obscured_route() -> None:
    state = _claimed_fanout_state()

    try:
        build_dispatch_envelope_for_run(
            state=_without_fanout_record_and_route(state, corrupt_route=True),
            run_id="run-beta-dispatch",
        )
    except DispatchProjectionError as exc:
        assert exc.reason == "fanout_partial_state"
    else:  # pragma: no cover - assertion guard
        raise AssertionError("obscured fanout route was accepted")


def test_fanout_dispatch_refuses_fully_drifted_target_aftermath() -> None:
    state = _claimed_fanout_state()

    try:
        build_dispatch_envelope_for_run(
            state=_with_fully_drifted_fanout_target(state),
            run_id="run-beta-dispatch",
        )
    except DispatchProjectionError as exc:
        assert exc.reason == "fanout_partial_state"
    else:  # pragma: no cover - assertion guard
        raise AssertionError("fully drifted fanout target was accepted")


@pytest.mark.parametrize("shared_target", ("work", "activation"))
def test_fanout_dispatch_refuses_extra_partial_target_route(
    shared_target: str,
) -> None:
    state = _claimed_fanout_state()

    with pytest.raises(DispatchProjectionError) as exc_info:
        build_dispatch_envelope_for_run(
            state=_with_extra_fanout_target_route(
                state,
                shared_target=shared_target,
            ),
            run_id="run-beta-dispatch",
        )

    assert exc_info.value.reason == "fanout_partial_state"


def test_terminal_action_generated_dispatch_has_no_fanout_source() -> None:
    state, _plan, _fingerprint = generic_lifecycle.origin_claimed_state()

    envelope = build_dispatch_envelope_for_run(
        state=state,
        run_id="run-origin",
    )

    assert "generated_work_source" not in envelope.governance_context


def test_dispatch_envelope_for_run_is_production_owned_and_read_only() -> None:
    state, plan, fingerprint = generic_lifecycle.origin_claimed_state()
    before = state

    envelope = build_dispatch_envelope_for_run(state=state, run_id="run-origin")

    assert state == before
    assert envelope.plan_fingerprint == fingerprint
    assert envelope.run_id == "run-origin"
    assert envelope.work_item_id == "work-origin"
    assert envelope.activation_id == "activation-origin"
    assert envelope.claim_id == "claim-origin"
    assert envelope.queue_family_id == "origin"
    assert envelope.graph_node_id == "lifecycle.origin.start"
    assert envelope.stage_kind_id == "origin_stage"
    assert envelope.runner_binding_id == "lifecycle.runner"
    assert envelope.external_enqueue_route_id == "route.origin"
    assert envelope.entrypoint_asset_id == "asset.origin"
    assert envelope.skill_asset_ids == ()
    assert envelope.work_item_payload == generic_lifecycle.source_payload()


def test_dispatch_envelope_for_run_emits_package_entrypoint_prompt_and_stage_skill_refs() -> (  # noqa: E501
    None
):
    state, plan, fingerprint = generic_lifecycle.origin_claimed_state()
    entrypoint_id = "asset.origin"
    skill_id = "asset.alpha"

    package_assets = tuple(
        replace(asset, asset_kind="entrypoint_prompt")
        if str(asset.id) == entrypoint_id
        else replace(asset, asset_kind="stage_skill")
        if str(asset.id) == skill_id
        else asset
        for asset in plan.assets
    )
    assets_by_id = {str(asset.id): asset for asset in package_assets}
    package_plan = replace(
        plan,
        assets=package_assets,
        stage_kinds=tuple(
            replace(
                stage,
                asset_ids=(
                    type(stage.asset_ids[0])(entrypoint_id),
                    type(stage.asset_ids[0])(skill_id),
                ),
            )
            if str(stage.id) == "origin_stage"
            else stage
            for stage in plan.stage_kinds
        ),
        workflow_package_pin=SelectedWorkflowPackagePin(
            package_id="pkg.lifecycle",
            package_version="1.0.0",
            package_format_version="1",
            workflow_id=str(plan.workflow.workflow_id),
            workflow_version=str(plan.workflow.workflow_version),
            entrypoint="default",
            selected_asset_pins=(
                SelectedWorkflowPackageAssetPin(
                    asset_id=entrypoint_id,
                    content_digest=asset_digest_for_bytes(
                        assets_by_id[entrypoint_id].body.encode("utf-8")
                    ),
                ),
                SelectedWorkflowPackageAssetPin(
                    asset_id=skill_id,
                    content_digest=asset_digest_for_bytes(
                        assets_by_id[skill_id].body.encode("utf-8")
                    ),
                ),
            ),
            selected_dependency_pins=(),
        ),
    )
    state = replace(
        state,
        admitted_plans={
            fingerprint: replace(
                state.admitted_plans[fingerprint],
                selected_plan=package_plan,
            )
        },
    )

    envelope = build_dispatch_envelope_for_run(state=state, run_id="run-origin")

    assert envelope.entrypoint_asset_id == entrypoint_id
    assert envelope.skill_asset_ids == (skill_id,)


def test_dispatch_projection_refuses_corrupt_or_inactive_runs() -> None:
    state, plan, fingerprint = generic_lifecycle.origin_claimed_state()
    run = state.runs["run-origin"]
    activation = state.activations["activation-origin"]
    work_item = state.work_items["work-origin"]

    cases = {
        "missing-run": (state, "unknown_run"),
        "missing-activation": (
            replace(state, activations={}),
            "missing_activation",
        ),
        "missing-work-item": (
            replace(state, work_items={}),
            "missing_work_item",
        ),
        "missing-admitted-plan": (
            replace(state, admitted_plans={}),
            "missing_admitted_plan",
        ),
        "wrong-plan-authority": (
            replace(
                state,
                work_items={
                    "work-origin": replace(
                        work_item,
                        ref=replace(
                            work_item.ref,
                            plan_ref=replace(
                                work_item.ref.plan_ref,
                                authority_fingerprint=f"sha256:{'9' * 64}",
                            ),
                        ),
                    )
                },
            ),
            "plan_ref_mismatch",
        ),
        "closed-work": (
            replace(
                state,
                closed_work_items={
                    "work-origin": ClosedWorkItemRecord(
                        record_id="closed-work-origin",
                        work_item_id="work-origin",
                        source_run_id=None,
                        action_id=None,
                        created_by_input_id="close-work-origin",
                    )
                },
            ),
            "work_item_closed",
        ),
        "observed-run": (
            replace(
                state,
                runner_observations={
                    "observation-run-origin": RunnerObservationRecord(
                        observation_id="observation-run-origin",
                        run_id=run.run_ref.run_id,
                        payload={"status": "done"},
                        created_by_input_id="observe-run-origin",
                        observed_at=None,
                    )
                },
            ),
            "run_observed",
        ),
        "run-activation-drift": (
            replace(
                state,
                activations={
                    "activation-origin": replace(
                        activation,
                        claimed_by_run_id="different-run",
                    )
                },
            ),
            "run_activation_drift",
        ),
        "missing-stage-kind": (
            replace(
                state,
                runs={
                    "run-origin": replace(
                        run,
                        stage_kind_id=type(run.stage_kind_id)("missing-stage"),
                    )
                },
                activations={
                    "activation-origin": replace(
                        activation,
                        stage_kind_id=type(activation.stage_kind_id)("missing-stage"),
                    )
                },
            ),
            "missing_stage_kind",
        ),
        "missing-graph-node": (
            replace(
                state,
                activations={
                    "activation-origin": replace(
                        activation,
                        graph_node_id="missing.graph.node",
                    )
                },
            ),
            "graph_node_missing",
        ),
        "route-drift": (
            replace(
                state,
                activations={
                    "activation-origin": replace(
                        activation,
                        graph_node_id="lifecycle.alpha.start",
                    )
                },
            ),
            "graph_stage_runner_drift",
        ),
        "missing-selected-asset": (
            replace(
                state,
                admitted_plans={
                    fingerprint: replace(
                        state.admitted_plans[fingerprint],
                        selected_plan=replace(plan, assets=()),
                    )
                },
            ),
            "missing_selected_asset",
        ),
        "missing-selected-schema": (
            replace(
                state,
                admitted_plans={
                    fingerprint: replace(
                        state.admitted_plans[fingerprint],
                        selected_plan=replace(plan, artifact_schemas=()),
                    )
                },
            ),
            "missing_selected_schema",
        ),
    }

    for run_id, (candidate_state, reason) in cases.items():
        try:
            build_dispatch_envelope_for_run(
                state=candidate_state,
                run_id="run-origin" if run_id != "missing-run" else "missing",
            )
        except DispatchProjectionError as exc:
            assert exc.reason == reason
        else:  # pragma: no cover - assertion guard
            raise AssertionError(f"case did not refuse: {run_id}")


def test_ready_dispatch_candidate_projection_is_public_read_only_and_deterministic() -> (  # noqa: E501
    None
):
    state, plan, fingerprint = generic_lifecycle.origin_queued_state()
    before = state

    first = list_ready_dispatch_candidates(state)
    second = list_ready_dispatch_candidates(state)

    assert state == before
    assert first == second
    assert first.diagnostics == ()
    assert len(first.candidates) == 1
    candidate = first.candidates[0]
    assert candidate.activation_id == "activation-origin"
    assert candidate.work_item_id == "work-origin"
    assert candidate.lineage_id == "work-origin"
    assert candidate.queue_family_id == "origin"
    assert candidate.graph_node_id == "lifecycle.origin.start"
    assert candidate.stage_kind_id == "origin_stage"
    assert candidate.runner_binding_id == "lifecycle.runner"
    assert candidate.plan_fingerprint == fingerprint
    assert candidate.generation == 0
    assert candidate.external_enqueue_route_id == "route.origin"


def test_ready_dispatch_candidate_projection_reports_reason_coded_diagnostics() -> None:
    ready, plan, fingerprint = generic_lifecycle.origin_queued_state()
    activation = ready.activations["activation-origin"]
    work_item = ready.work_items["work-origin"]

    paused = replace(
        ready,
        pause=PauseRecord(
            record_id="pause",
            source_run_id="run-x",
            work_item_id="work-origin",
            action_id=plan.terminal_actions[0].id,
            created_by_input_id="pause-input",
        ),
    )
    suspended = replace(
        ready,
        dispatch_suspension=DispatchSuspensionRecord(
            suspension_id="dispatch-suspension:test",
            selected_plan_ref=ready.default_plan_ref,
            generation=1,
            dispatch_generation=0,
            actor_id="operator-a",
            reason="hold claims",
            suspended_by_input_id="suspend-test",
            status="active",
        ),
    )
    claimed = _claim_origin(ready)
    stale_generation = replace(
        ready,
        activations={
            "activation-origin": replace(
                activation,
                generation=activation.generation + 1,
            )
        },
    )
    wrong_plan = replace(
        ready,
        work_items={
            "work-origin": replace(
                work_item,
                ref=replace(
                    work_item.ref,
                    plan_ref=replace(
                        work_item.ref.plan_ref,
                        authority_fingerprint=f"sha256:{'8' * 64}",
                    ),
                ),
            )
        },
    )
    corrupt_capability = replace(
        ready,
        admitted_plans={
            fingerprint: replace(
                ready.admitted_plans[fingerprint],
                selected_plan=replace(
                    plan,
                    capabilities=(
                        replace(plan.capabilities[0], capability_kind="shell.run"),
                    ),
                ),
            )
        },
    )

    cases = {
        "paused": (paused, "workspace_paused", "policy_refusal"),
        "suspended": (suspended, "dispatch_suspended", "policy_refusal"),
        "claimed": (claimed, "already_claimed", "non_candidate"),
        "wrong-plan": (wrong_plan, "plan_ref_mismatch", "corrupt_authority"),
        "stale-generation": (
            stale_generation,
            "stale_generation",
            "corrupt_authority",
        ),
        "capability": (
            corrupt_capability,
            "unsupported_selected_authority",
            "corrupt_authority",
        ),
    }

    for name, (state, reason, severity) in cases.items():
        projection = list_ready_dispatch_candidates(state)
        assert projection.candidates == (), name
        assert [(row.reason_code, row.severity) for row in projection.diagnostics] == [
            (reason, severity)
        ], name


def test_ready_dispatch_claim_refusal_diagnostic_mapping_is_dispatch_owned() -> None:
    ready, _plan, fingerprint = generic_lifecycle.origin_queued_state()
    activation = ready.activations["activation-origin"]
    work_item = ready.work_items["work-origin"]

    cases = (
        ("dispatch_suspended", None, "dispatch_suspended", "policy_refusal"),
        ("workspace_paused", None, "workspace_paused", "policy_refusal"),
        ("lineage_quarantined", None, "lineage_quarantined", "policy_refusal"),
        ("operator_wait_active", None, "operator_wait_active", "policy_refusal"),
        ("dependency_not_ready", None, "dependency_not_ready", "policy_refusal"),
        ("capability_denied", None, "capability_denied", "policy_refusal"),
        (
            "capability_approval_pending",
            None,
            "capability_approval_pending",
            "policy_refusal",
        ),
        ("capability_unsupported", None, "capability_unsupported", "policy_refusal"),
        ("unknown_plan_ref", None, "missing_admitted_plan", "corrupt_authority"),
        (
            "unsupported_selected_authority",
            "capability_kind: shell.run",
            "unsupported_selected_authority",
            "corrupt_authority",
        ),
    )

    for reason, detail, expected_reason, expected_severity in cases:
        diagnostic = ready_diagnostic_from_claim_refusal(
            activation=activation,
            work_item=work_item,
            reason=reason,
            detail=detail,
        )

        assert diagnostic.activation_id == "activation-origin"
        assert diagnostic.work_item_id == "work-origin"
        assert diagnostic.plan_fingerprint == fingerprint
        assert diagnostic.reason_code == expected_reason
        assert diagnostic.severity == expected_severity


@pytest.mark.parametrize("field", ("created_by_input_id", "transition_id"))
@pytest.mark.parametrize(
    "artifact_id",
    (generic_lifecycle.source_artifact_id(), "transition-observe-alpha:artifact"),
    ids=("bundle", "evidence"),
)
def test_join_dispatch_refuses_artifact_provenance_drift(
    artifact_id: str,
    field: str,
) -> None:
    state = _claimed_join_state()

    with pytest.raises(DispatchProjectionError) as exc_info:
        build_dispatch_envelope_for_run(
            state=_with_join_artifact_provenance_drift(
                state,
                artifact_id=artifact_id,
                field=field,
            ),
            run_id="run-review-dispatch",
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
    "artifact_id",
    (generic_lifecycle.source_artifact_id(), "transition-observe-alpha:artifact"),
    ids=("bundle", "evidence"),
)
def test_join_dispatch_refuses_artifact_action_authority_drift(
    artifact_id: str,
    field: str,
) -> None:
    state = _claimed_join_state()

    with pytest.raises(DispatchProjectionError) as exc_info:
        build_dispatch_envelope_for_run(
            state=_with_join_artifact_action_authority_drift(
                state,
                artifact_id=artifact_id,
                field=field,
            ),
            run_id="run-review-dispatch",
        )

    assert exc_info.value.reason == "join_partial_state", field
    assert exc_info.value.details["detail"] == "wrong_source_artifact", field


@pytest.mark.parametrize("field", ("payload", "observed_at"))
@pytest.mark.parametrize(
    "artifact_id",
    (generic_lifecycle.source_artifact_id(), "transition-observe-alpha:artifact"),
    ids=("bundle", "evidence"),
)
def test_join_dispatch_refuses_artifact_observation_drift(
    artifact_id: str,
    field: str,
) -> None:
    state = _claimed_join_state()

    with pytest.raises(DispatchProjectionError) as exc_info:
        build_dispatch_envelope_for_run(
            state=_with_join_artifact_observation_drift(
                state,
                artifact_id=artifact_id,
                field=field,
            ),
            run_id="run-review-dispatch",
        )

    assert exc_info.value.reason == "join_partial_state", field
    assert exc_info.value.details["detail"] == "wrong_source_artifact", field


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
    ),
)
def test_join_dispatch_refuses_corrupt_completion_bijection(case: str) -> None:
    state = generic_lifecycle.join_bijection_state(case)
    state = generic_lifecycle.claim_activation(
        state,
        activation_id="activation-review",
        suffix="review-bijection",
    )

    with pytest.raises(DispatchProjectionError) as exc_info:
        build_dispatch_envelope_for_run(
            state=state,
            run_id="run-review-bijection",
        )

    assert exc_info.value.reason == "join_partial_state", case


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
        not isinstance(row, Mapping) or row["source_action_id"] != str(alternative.id)
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
            state=_with_join_source_context_drift(claimed, field),
            run_id="run-review-source-context",
        )

    assert exc_info.value.reason == "join_partial_state", field
    assert exc_info.value.details["detail"] == "wrong_source_artifact", field


def test_join_dispatch_preserves_repeated_schema_slots_and_correlation_groups() -> None:
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
        row["artifact_schema_id"] for row in evidence if isinstance(row, Mapping)
    ) == (
        generic_lifecycle.ALPHA_REPORT_SCHEMA_ID,
        generic_lifecycle.ALPHA_REPORT_SCHEMA_ID,
        generic_lifecycle.BETA_REPORT_SCHEMA_ID,
        generic_lifecycle.BETA_REPORT_SCHEMA_ID,
    )
    assert {row["item_key"] for row in evidence if isinstance(row, Mapping)} == {
        "one",
        "two",
    }


def test_join_evidence_is_stable_across_runtime_insertion_order() -> None:
    state = _claimed_join_state()

    expected = _selected_join_evidence(state)
    reordered = _selected_join_evidence(_with_reversed_runtime_order(state))

    assert reordered == expected
    assert canonical_authority_bytes(reordered) == canonical_authority_bytes(expected)


def test_join_evidence_is_stable_across_qualifying_source_artifacts() -> None:
    from_beta = _claimed_join_state()
    from_alpha = _claimed_join_state(
        source_artifact_id="transition-observe-alpha:artifact"
    )

    assert _selected_join_evidence(from_alpha) == _selected_join_evidence(from_beta)


def test_join_dispatch_does_not_merge_evidence_into_work_item_payload() -> None:
    state = _claimed_join_state()
    envelope = build_dispatch_envelope_for_run(
        state=state,
        run_id="run-review-dispatch",
    )

    assert envelope.work_item_payload == generic_lifecycle.source_payload()
    assert "required_evidence_refs" not in envelope.work_item_payload
    assert envelope.selected_join_evidence == _selected_join_evidence(state)


def test_join_dispatch_ignores_unrelated_same_schema_artifacts() -> None:
    state = _claimed_join_state()
    alpha = state.artifacts["transition-observe-alpha:artifact"]
    origin = state.runs["run-origin"]
    unrelated = replace(
        alpha,
        artifact_id="unrelated-alpha-same-schema",
        work_item_id=origin.work_item_id,
        created_by_input_id="unrelated-alpha-input",
        source_run_id=origin.run_ref.run_id,
        transition_id="transition-unrelated-alpha",
    )
    with_unrelated = replace(
        state,
        artifacts={**state.artifacts, unrelated.artifact_id: unrelated},
    )

    assert _selected_join_evidence(with_unrelated) == _selected_join_evidence(state)


def test_non_join_dispatch_has_no_selected_join_evidence() -> None:
    normal, _plan, _fingerprint = generic_lifecycle.origin_claimed_state()
    fanout, _plan, _fingerprint = generic_lifecycle.two_complete_fanouts_state()
    activation_id = generic_lifecycle.branch_activation_id(fanout, "alpha_stage")
    fanout = generic_lifecycle.claim_activation(
        fanout,
        activation_id=activation_id,
        suffix="alpha-non-join",
    )

    assert (
        build_dispatch_envelope_for_run(
            state=normal,
            run_id="run-origin",
        ).selected_join_evidence
        is None
    )
    assert (
        build_dispatch_envelope_for_run(
            state=fanout,
            run_id="run-alpha-non-join",
        ).selected_join_evidence
        is None
    )
