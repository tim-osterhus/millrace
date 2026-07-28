from __future__ import annotations

from dataclasses import replace

import pytest

from millrace.contracts import ClaimWork
from millrace.contracts.compiled_plan import (
    SelectedWorkflowPackageAssetPin,
    SelectedWorkflowPackagePin,
)
from millrace.contracts.state import (
    ClosedWorkItemRecord,
    PauseRecord,
    RunnerObservationRecord,
)
from millrace.contracts.transition import FanoutFromArtifact
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
from support import generic_lifecycle, vendor_selection
from support.lad_execution import (
    bootstrap_builder_claim,
    bootstrap_builder_ready,
    compile_lad,
    task_payload,
)


def build_dispatch_envelope_for_run(*, state, run_id):
    return _build_dispatch_envelope_for_run(
        state=fake_runner_session_state(state=state, run_id=run_id),
        run_id=run_id,
    )


def _claim_builder(state):
    return apply(
        state,
        decide(
            state,
            ClaimWork("claim-builder", activation_id="activation-builder"),
            deterministic_context(
                transition_id="transition-claim-builder",
                run_id="run-builder",
                claim_id="claim-builder",
                fencing_token="fence-builder",
            ),
        ),
    )


def _apply_selected_packager_fanouts(state):
    for fanout_id, suffix in (
        (
            "vendor_selection.candidate_packager.rubric_fanout",
            "rubric",
        ),
        (
            "vendor_selection.candidate_packager.conflict_fanout",
            "conflict",
        ),
    ):
        state = vendor_selection.apply_accepted_input(
            state,
            FanoutFromArtifact(
                f"fanout-{suffix}-a",
                fanout_id=fanout_id,
                source_artifact_id=vendor_selection.artifact_id_for(
                    "observe-packager-a"
                ),
            ),
            vendor_selection.context(f"fanout-{suffix}-a"),
        )
    return state


def _with_fanout_source_artifact_provenance_drift(state, *, field: str):
    artifact_id = vendor_selection.artifact_id_for("observe-packager-a")
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


def _with_fanout_source_action_authority_drift(state, *, field: str):
    artifact = state.artifacts[
        vendor_selection.artifact_id_for("observe-packager-a")
    ]
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
    artifact = state.artifacts[
        vendor_selection.artifact_id_for("observe-packager-a")
    ]
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
    run = state.runs["run-conflict-a"]
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
    run = state.runs["run-conflict-a"]
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


def _with_extra_fanout_target_route(state, *, shared_target: str):
    run = state.runs["run-conflict-a"]
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
            extra_activation_id
            if shared_target == "work"
            else activation.activation_id
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
    state, _plan, _fingerprint = vendor_selection.packager_closed_state("a")
    state = _apply_selected_packager_fanouts(state)
    activation_id = vendor_selection.report_branch_activation_id(
        state,
        "conflict_checker",
    )
    state = vendor_selection.claim_activation(
        state,
        activation_id=activation_id,
        suffix="conflict-a",
    )

    production = build_dispatch_envelope_for_run(
        state=state,
        run_id="run-conflict-a",
    )
    fake = fake_runner_dispatch_envelope_for_run(
        state=state,
        run_id="run-conflict-a",
    )

    assert production.payload() == fake.payload()
    source = production.governance_context["generated_work_source"]
    assert source["item_key"] == "vendor_gamma"
    assert source["source_artifact_id"] == vendor_selection.artifact_id_for(
        "observe-packager-a"
    )
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
    state, _plan, _fingerprint = vendor_selection.packager_closed_state("a")
    state = _apply_selected_packager_fanouts(state)
    activation_id = vendor_selection.report_branch_activation_id(
        state,
        "conflict_checker",
    )
    state = vendor_selection.claim_activation(
        state,
        activation_id=activation_id,
        suffix="conflict-a",
    )

    with pytest.raises(DispatchProjectionError) as exc_info:
        build_dispatch_envelope_for_run(
            state=_with_fanout_source_artifact_provenance_drift(
                state,
                field=field,
            ),
            run_id="run-conflict-a",
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
    state, _plan, _fingerprint = vendor_selection.packager_closed_state("a")
    state = _apply_selected_packager_fanouts(state)
    activation_id = vendor_selection.report_branch_activation_id(
        state,
        "conflict_checker",
    )
    state = vendor_selection.claim_activation(
        state,
        activation_id=activation_id,
        suffix="conflict-a",
    )

    with pytest.raises(DispatchProjectionError) as exc_info:
        build_dispatch_envelope_for_run(
            state=_with_fanout_source_action_authority_drift(
                state,
                field=field,
            ),
            run_id="run-conflict-a",
        )

    assert exc_info.value.reason == "wrong_source_artifact", field
    assert exc_info.value.details["detail"] == (
        "receipt_authority"
        if field == "observation_action"
        else "audit_authority"
    ), field


@pytest.mark.parametrize("field", ("payload", "observed_at"))
def test_fanout_dispatch_refuses_observation_payload_or_observed_at_drift(
    field: str,
) -> None:
    state, _plan, _fingerprint = vendor_selection.packager_closed_state("a")
    state = _apply_selected_packager_fanouts(state)
    activation_id = vendor_selection.report_branch_activation_id(
        state,
        "conflict_checker",
    )
    state = vendor_selection.claim_activation(
        state,
        activation_id=activation_id,
        suffix="conflict-a",
    )

    with pytest.raises(DispatchProjectionError) as exc_info:
        build_dispatch_envelope_for_run(
            state=_with_fanout_source_observation_drift(state, field=field),
            run_id="run-conflict-a",
        )

    assert exc_info.value.reason == "wrong_source_artifact", field
    assert exc_info.value.details["detail"] == "receipt_authority", field


def test_fanout_dispatch_refuses_missing_record_and_route() -> None:
    state, _plan, _fingerprint = vendor_selection.packager_closed_state("a")
    state = _apply_selected_packager_fanouts(state)
    activation_id = vendor_selection.report_branch_activation_id(
        state,
        "conflict_checker",
    )
    state = vendor_selection.claim_activation(
        state,
        activation_id=activation_id,
        suffix="conflict-a",
    )

    try:
        build_dispatch_envelope_for_run(
            state=_without_fanout_record_and_route(state, corrupt_route=False),
            run_id="run-conflict-a",
        )
    except DispatchProjectionError as exc:
        assert exc.reason == "fanout_partial_state"
    else:  # pragma: no cover - assertion guard
        raise AssertionError("missing fanout record and route were accepted")


def test_fanout_dispatch_refuses_missing_record_and_obscured_route() -> None:
    state, _plan, _fingerprint = vendor_selection.packager_closed_state("a")
    state = _apply_selected_packager_fanouts(state)
    activation_id = vendor_selection.report_branch_activation_id(
        state,
        "conflict_checker",
    )
    state = vendor_selection.claim_activation(
        state,
        activation_id=activation_id,
        suffix="conflict-a",
    )

    try:
        build_dispatch_envelope_for_run(
            state=_without_fanout_record_and_route(state, corrupt_route=True),
            run_id="run-conflict-a",
        )
    except DispatchProjectionError as exc:
        assert exc.reason == "fanout_partial_state"
    else:  # pragma: no cover - assertion guard
        raise AssertionError("obscured fanout route was accepted")


def test_fanout_dispatch_refuses_fully_drifted_target_aftermath() -> None:
    state, _plan, _fingerprint = vendor_selection.packager_closed_state("a")
    state = _apply_selected_packager_fanouts(state)
    activation_id = vendor_selection.report_branch_activation_id(
        state,
        "conflict_checker",
    )
    state = vendor_selection.claim_activation(
        state,
        activation_id=activation_id,
        suffix="conflict-a",
    )

    try:
        build_dispatch_envelope_for_run(
            state=_with_fully_drifted_fanout_target(state),
            run_id="run-conflict-a",
        )
    except DispatchProjectionError as exc:
        assert exc.reason == "fanout_partial_state"
    else:  # pragma: no cover - assertion guard
        raise AssertionError("fully drifted fanout target was accepted")


@pytest.mark.parametrize("shared_target", ("work", "activation"))
def test_fanout_dispatch_refuses_extra_partial_target_route(
    shared_target: str,
) -> None:
    state, _plan, _fingerprint = vendor_selection.packager_closed_state("a")
    state = _apply_selected_packager_fanouts(state)
    activation_id = vendor_selection.report_branch_activation_id(
        state,
        "conflict_checker",
    )
    state = vendor_selection.claim_activation(
        state,
        activation_id=activation_id,
        suffix="conflict-a",
    )

    with pytest.raises(DispatchProjectionError) as exc_info:
        build_dispatch_envelope_for_run(
            state=_with_extra_fanout_target_route(
                state,
                shared_target=shared_target,
            ),
            run_id="run-conflict-a",
        )

    assert exc_info.value.reason == "fanout_partial_state"


def test_terminal_action_generated_dispatch_has_no_fanout_source() -> None:
    state, _plan, _fingerprint = vendor_selection.packager_claimed_state("a")

    envelope = build_dispatch_envelope_for_run(
        state=state,
        run_id="run-packager-a",
    )

    assert "generated_work_source" not in envelope.governance_context


def test_dispatch_envelope_for_run_is_production_owned_and_read_only() -> None:
    plan, fingerprint = compile_lad()
    state = bootstrap_builder_claim(plan, fingerprint)
    before = state

    envelope = build_dispatch_envelope_for_run(state=state, run_id="run-builder")

    assert state == before
    assert envelope.plan_fingerprint == fingerprint
    assert envelope.run_id == "run-builder"
    assert envelope.work_item_id == "work-task"
    assert envelope.activation_id == "activation-builder"
    assert envelope.claim_id == "claim-builder"
    assert envelope.queue_family_id == "task"
    assert envelope.graph_node_id == "execution.lad.builder.start"
    assert envelope.stage_kind_id == "lad_builder"
    assert envelope.runner_binding_id == "execution.lad.local_runner"
    assert envelope.external_enqueue_route_id == "execution.lad.task"
    assert envelope.entrypoint_asset_id == "execution.entrypoints.lad_builder"
    assert envelope.skill_asset_ids == ("execution.skills.builder_core",)
    assert envelope.work_item_payload == task_payload()


def test_dispatch_envelope_for_run_emits_package_entrypoint_prompt_and_stage_skill_refs(
) -> None:
    plan, fingerprint = compile_lad()
    state = bootstrap_builder_claim(plan, fingerprint)
    entrypoint_id = "execution.entrypoints.lad_builder"
    skill_id = "execution.skills.builder_core"

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
        workflow_package_pin=SelectedWorkflowPackagePin(
            package_id="pkg.lad.execution",
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

    envelope = build_dispatch_envelope_for_run(state=state, run_id="run-builder")

    assert envelope.entrypoint_asset_id == entrypoint_id
    assert envelope.skill_asset_ids == (skill_id,)


def test_dispatch_projection_refuses_corrupt_or_inactive_runs() -> None:
    plan, fingerprint = compile_lad()
    state = bootstrap_builder_claim(plan, fingerprint)
    run = state.runs["run-builder"]
    activation = state.activations["activation-builder"]
    work_item = state.work_items["work-task"]

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
                    "work-task": replace(
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
                    "work-task": ClosedWorkItemRecord(
                        record_id="closed-work-task",
                        work_item_id="work-task",
                        source_run_id=None,
                        action_id=None,
                        created_by_input_id="close-work-task",
                    )
                },
            ),
            "work_item_closed",
        ),
        "observed-run": (
            replace(
                state,
                runner_observations={
                    "observation-run-builder": RunnerObservationRecord(
                        observation_id="observation-run-builder",
                        run_id=run.run_ref.run_id,
                        payload={"status": "done"},
                        created_by_input_id="observe-run-builder",
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
                    "activation-builder": replace(
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
                    "run-builder": replace(
                        run,
                        stage_kind_id=type(run.stage_kind_id)("missing-stage"),
                    )
                },
                activations={
                    "activation-builder": replace(
                        activation,
                        stage_kind_id=type(activation.stage_kind_id)(
                            "missing-stage"
                        ),
                    )
                },
            ),
            "missing_stage_kind",
        ),
        "missing-graph-node": (
            replace(
                state,
                activations={
                    "activation-builder": replace(
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
                    "activation-builder": replace(
                        activation,
                        graph_node_id="execution.lad.checker.start",
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
                run_id="run-builder" if run_id != "missing-run" else "missing",
            )
        except DispatchProjectionError as exc:
            assert exc.reason == reason
        else:  # pragma: no cover - assertion guard
            raise AssertionError(f"case did not refuse: {run_id}")


def test_ready_dispatch_candidate_projection_is_public_read_only_and_deterministic(
) -> None:
    plan, fingerprint = compile_lad()
    state = bootstrap_builder_ready(plan, fingerprint)
    before = state

    first = list_ready_dispatch_candidates(state)
    second = list_ready_dispatch_candidates(state)

    assert state == before
    assert first == second
    assert first.diagnostics == ()
    assert len(first.candidates) == 1
    candidate = first.candidates[0]
    assert candidate.activation_id == "activation-builder"
    assert candidate.work_item_id == "work-task"
    assert candidate.lineage_id == "work-task"
    assert candidate.queue_family_id == "task"
    assert candidate.graph_node_id == "execution.lad.builder.start"
    assert candidate.stage_kind_id == "lad_builder"
    assert candidate.runner_binding_id == "execution.lad.local_runner"
    assert candidate.plan_fingerprint == fingerprint
    assert candidate.generation == 0
    assert candidate.external_enqueue_route_id == "execution.lad.task"


def test_ready_dispatch_candidate_projection_reports_reason_coded_diagnostics(
) -> None:
    plan, fingerprint = compile_lad()
    ready = bootstrap_builder_ready(plan, fingerprint)
    activation = ready.activations["activation-builder"]
    work_item = ready.work_items["work-task"]

    paused = replace(
        ready,
        pause=PauseRecord(
            record_id="pause",
            source_run_id="run-x",
            work_item_id="work-task",
            action_id=plan.terminal_actions[0].id,
            created_by_input_id="pause-input",
        ),
    )
    claimed = _claim_builder(ready)
    stale_generation = replace(
        ready,
        activations={
            "activation-builder": replace(
                activation,
                generation=activation.generation + 1,
            )
        },
    )
    wrong_plan = replace(
        ready,
        work_items={
            "work-task": replace(
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
    plan, fingerprint = compile_lad()
    ready = bootstrap_builder_ready(plan, fingerprint)
    activation = ready.activations["activation-builder"]
    work_item = ready.work_items["work-task"]

    cases = (
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

        assert diagnostic.activation_id == "activation-builder"
        assert diagnostic.work_item_id == "work-task"
        assert diagnostic.plan_fingerprint == fingerprint
        assert diagnostic.reason_code == expected_reason
        assert diagnostic.severity == expected_severity
