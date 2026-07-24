from __future__ import annotations

from dataclasses import fields, is_dataclass, replace
from typing import Any

from millrace.contracts.transition import JoinFromArtifact
from millrace.kernel import apply, decide
from millrace.operator import operator_status
from support import vendor_selection


def _queue(status: Any, queue_family_id: str) -> Any:
    return next(
        family
        for family in status.queue_families
        if family.queue_family_id == queue_family_id
    )


def _stage(status: Any, stage_kind_id: str) -> Any:
    return next(
        stage for stage in status.stage_kinds if stage.stage_kind_id == stage_kind_id
    )


def _artifact_by_input(status: Any, input_id: str) -> Any:
    return next(
        artifact
        for artifact in status.artifacts
        if artifact.source_input_id == input_id
    )


def _apply_multi_candidate_join(state: Any) -> Any:
    transition = JoinFromArtifact(
        "join-vendor-multi-status",
        join_id=vendor_selection.JOIN_ID,
        source_artifact_id=vendor_selection.artifact_id_for(
            "observe-conflict-multi-1"
        ),
    )
    decision = decide(
        state,
        transition,
        vendor_selection.context(
            transition.input_id,
            work_item_id="work-award-multi-status",
            activation_id="activation-award-multi-status",
        ),
    )
    assert decision.accepted is True
    return apply(state, decision)


def _leaf_strings(value: object) -> tuple[str, ...]:
    if is_dataclass(value):
        strings: list[str] = []
        for field in fields(value):
            strings.extend(_leaf_strings(getattr(value, field.name)))
        return tuple(strings)
    if isinstance(value, dict):
        mapped: list[str] = []
        for item in value.values():
            mapped.extend(_leaf_strings(item))
        return tuple(mapped)
    if isinstance(value, (tuple, list, set, frozenset)):
        collected: list[str] = []
        for item in value:
            collected.extend(_leaf_strings(item))
        return tuple(collected)
    return (value,) if isinstance(value, str) else ()


def test_status_projects_selected_four_planes_read_only() -> None:
    state, _plan, fingerprint = vendor_selection.admit_vendor_selection()

    status = operator_status(state)

    assert status.selected_plan is not None
    assert status.selected_plan.workflow_id == "vendor_selection"
    assert status.selected_plan.workflow_version == "0.1"
    assert status.selected_plan.authority_fingerprint == fingerprint
    assert {
        partition.partition_id: partition.partition_kind
        for partition in status.partitions
    } == {
        "requirements": "plane",
        "sourcing": "plane",
        "evaluation": "plane",
        "authorization": "plane",
    }
    assert state == vendor_selection.admit_vendor_selection()[0]


def test_status_projects_selected_queue_and_stage_counts() -> None:
    state, _plan, _fingerprint = vendor_selection.one_report_state()

    status = operator_status(state)

    assert {family.queue_family_id for family in status.queue_families} == set(
        vendor_selection.QUEUE_FAMILY_IDS
    )
    candidate_bundle = _queue(status, "candidate_bundle")
    assert candidate_bundle.ready_count == 1
    assert candidate_bundle.closed_count == 2

    rubric = _stage(status, "rubric_evaluator")
    conflict = _stage(status, "conflict_checker")
    assert rubric.closed_count == 1
    assert conflict.ready_count == 1
    assert conflict.active_count == 0


def test_status_projects_fanout_branches_and_computed_join_missing_evidence() -> None:
    state, _plan, _fingerprint = vendor_selection.one_report_state()

    status = operator_status(state)

    branches = {
        generated.target_stage_kind_id: generated
        for generated in status.generated_work
    }
    assert set(branches) == {"rubric_evaluator", "conflict_checker"}
    assert {
        generated.source_artifact_id for generated in branches.values()
    } == {vendor_selection.artifact_id_for("observe-packager-a")}
    assert {
        generated.lineage_id for generated in branches.values()
    } == {"work-request-a"}

    assert len(status.joins) == 1
    join = status.joins[0]
    assert join.join_id == vendor_selection.JOIN_ID
    assert join.lineage_id == "work-request-a"
    assert join.source_artifact_id == vendor_selection.artifact_id_for(
        "observe-packager-a"
    )
    assert join.correlation_key == "bundle_id"
    assert join.correlation_value == "bundle-a"
    assert join.required_artifact_schema_ids == ("RubricReport", "ConflictReport")
    assert join.observed_artifact_schema_ids == ("RubricReport",)
    assert join.missing_artifact_schema_ids == ("ConflictReport",)
    assert join.ready is False
    assert join.target_stage_kind_id == "award_decider"


def test_status_projects_computed_join_ready_evidence() -> None:
    state, _plan, _fingerprint = vendor_selection.two_report_state()

    status = operator_status(state)

    assert len(status.joins) == 1
    join = status.joins[0]
    assert join.observed_artifact_schema_ids == ("RubricReport", "ConflictReport")
    assert join.missing_artifact_schema_ids == ()
    assert join.ready is True


def test_status_projects_multi_candidate_join_ready_evidence_after_join() -> None:
    state, _plan, _fingerprint = (
        vendor_selection.multi_candidate_complete_report_state()
    )

    status = operator_status(_apply_multi_candidate_join(state))

    assert len(status.joins) == 1
    join = status.joins[0]
    assert join.observed_artifact_schema_ids == ("RubricReport", "ConflictReport")
    assert join.missing_artifact_schema_ids == ()
    assert join.ready is True


def test_status_keeps_multi_candidate_join_not_ready_until_every_slot_reports() -> (
    None
):
    state, _plan, _fingerprint = (
        vendor_selection.multi_candidate_schema_covered_state()
    )

    status = operator_status(state)

    assert len(status.joins) == 1
    join = status.joins[0]
    assert join.observed_artifact_schema_ids == ("RubricReport", "ConflictReport")
    assert join.missing_artifact_schema_ids == ()
    assert join.ready is False


def test_status_refuses_duplicate_evidence_for_one_selected_fanout_slot() -> None:
    state, _plan, _fingerprint = (
        vendor_selection.multi_candidate_complete_report_state()
    )
    rubric = next(
        artifact
        for artifact in state.artifacts.values()
        if str(artifact.schema_id) == "RubricReport"
    )
    state = replace(
        state,
        artifacts={
            **state.artifacts,
            f"{rubric.artifact_id}:duplicate": replace(
                rubric,
                artifact_id=f"{rubric.artifact_id}:duplicate",
            ),
        },
    )

    status = operator_status(state)

    assert len(status.joins) == 1
    assert status.joins[0].ready is False


def test_status_projects_mismatched_join_evidence_as_missing_not_ready() -> None:
    state, _plan, _fingerprint = vendor_selection.two_report_state()
    conflict_id = vendor_selection.artifact_id_for("observe-conflict-a")
    conflict = state.artifacts[conflict_id]
    state = replace(
        state,
        artifacts={
            **state.artifacts,
            conflict_id: replace(
                conflict,
                payload={**conflict.payload, "bundle_id": "bundle-other"},
            ),
        },
    )

    status = operator_status(state)

    assert len(status.joins) == 1
    join = status.joins[0]
    assert join.observed_artifact_schema_ids == ("RubricReport",)
    assert join.missing_artifact_schema_ids == ("ConflictReport",)
    assert join.ready is False


def test_status_projects_cross_lineage_progress() -> None:
    state, plan, fingerprint = vendor_selection.one_report_state()
    state = vendor_selection.enqueue_purchase_request(state, suffix="b")
    state = vendor_selection.progress_to_candidate_packager(
        state,
        plan=plan,
        fingerprint=fingerprint,
        suffix="b",
    )

    status = operator_status(state)

    request_ids = {
        artifact.payload.get("source_request_id") or artifact.payload.get("request_id")
        for artifact in status.artifacts
    }
    assert {"request-a", "request-b"} <= request_ids
    assert any(
        join.lineage_id == "work-request-a"
        and join.missing_artifact_schema_ids == ("ConflictReport",)
        for join in status.joins
    )
    assert any(
        artifact.source_input_id == "observe-policy-b"
        and artifact.source_stage_kind_id == "policy_screener"
        for artifact in status.artifacts
    )


def test_status_projects_decision_pack_close_evidence() -> None:
    state, _plan, fingerprint = vendor_selection.full_decision_pack_closed_state()

    status = operator_status(state, max_events=12)
    decision_pack = _artifact_by_input(status, "observe-decision-packager-a")

    assert decision_pack.schema_id == "DecisionPack"
    assert decision_pack.selected_plan_id == "vendor_selection:0.1"
    assert decision_pack.selected_plan_fingerprint == fingerprint
    assert decision_pack.payload["source_request_id"] == "request-a"
    assert decision_pack.payload["selected_candidate_id"] == "vendor_gamma"
    assert decision_pack.payload["final_refusal_reason"] is None
    assert decision_pack.payload["evidence_refs"] == {
        "rubric_report_ref": vendor_selection.artifact_id_for("observe-rubric-a"),
        "conflict_report_ref": vendor_selection.artifact_id_for("observe-conflict-a"),
    }
    assert decision_pack.payload["selected_plan_fingerprint"] == fingerprint
    assert decision_pack.payload["close_reason"] == "awarded"
    assert any(
        event.input_id == "observe-decision-packager-a"
        and event.disposition == "accepted"
        and event.action_id == "vendor_selection.decision_packager.decision_pack_ready"
        for event in status.recent_events
    )


def test_status_projection_is_read_only_and_does_not_mutate_runtime_state() -> None:
    state, _plan, _fingerprint = vendor_selection.one_report_state()

    first = operator_status(state, max_events=8)
    second = operator_status(state, max_events=8)

    assert first == second
    assert state == vendor_selection.one_report_state()[0]


def test_status_output_has_no_lad_simple_loop_provider_or_product_scope() -> None:
    state, _plan, _fingerprint = vendor_selection.full_decision_pack_closed_state()

    status = operator_status(state, max_events=20)
    values = set(_leaf_strings(status))

    assert status.effects == ()
    assert values.isdisjoint(
        {
            "simple_loop",
            "kernel_ping",
            "lad.full",
            "planning",
            "execution",
            "learning",
            "provider.fake_local.workspace",
            "run_card",
            "support_bundle",
            "governance_dashboard",
            "marketplace",
            "native_runner",
        }
    )
