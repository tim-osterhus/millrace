from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

from kernel.kernel_ping_scenarios import bootstrap_to_taskmaster_claim
from millrace.contracts.compiled_plan import SelectedCompiledPlan
from millrace.contracts.ids import (
    ActionId,
    QueueFamilyId,
    RecoveryPolicyId,
    RunnerBindingId,
    StageKindId,
)
from millrace.contracts.state import (
    ClosedWorkItemRecord,
    CooldownWaitRecord,
    OperatorInterventionRecord,
    PlanRef,
    RecoveryAttemptRecord,
    RuntimeState,
)
from millrace.contracts.transition import (
    AdmitPlan,
    EnqueueWork,
    EvaluateCompletionBehavior,
    OpenClosureTarget,
    ReconcileEffect,
    RunnerResultObserved,
    SelectDefaultPlan,
)
from millrace.kernel import apply, empty_runtime_state
from millrace.operator import operator_status
from millrace.testing import (
    decide_with_fake_runner_completion as decide,
)
from millrace.testing import (
    fake_completed_runner_observation_state,
    fake_runner_completion_input_id,
    fake_runner_observation_payload,
)
from support import generic_admission, generic_effect, generic_lifecycle
from support.kernel_ping import (
    apply_accepted_input,
    kernel_ping_context,
    runner_observation,
    task_artifact_payload,
)


def _queue(status, queue_family_id: str):
    return next(
        family
        for family in status.queue_families
        if family.queue_family_id == queue_family_id
    )


def _stage(status, stage_kind_id: str):
    return next(
        stage for stage in status.stage_kinds if stage.stage_kind_id == stage_kind_id
    )


def _generic_effect_state(*, reconciliation_status: str | None = None):
    plan, fingerprint = generic_effect.compile_effect_plan()
    state = bootstrap_to_taskmaster_claim(plan, fingerprint)
    state = apply_accepted_input(
        state,
        runner_observation(
            state=state,
            plan=plan,
            fingerprint=fingerprint,
            run_id="run-taskmaster",
            action_id=generic_effect.EFFECT_ACTION_ID,
            input_id="observe-effect-ready",
            artifact_payload=task_artifact_payload(),
        ),
        kernel_ping_context("observe-effect-ready"),
    )
    effect = next(iter(state.effect_proposals.values()))
    if reconciliation_status is not None:
        state = apply_accepted_input(
            state,
            ReconcileEffect(
                f"reconcile-{reconciliation_status}",
                effect_id=effect.effect_id,
                provider_ref="provider.fake_local.workspace",
                status=reconciliation_status,
                result={
                    "provider_result_id": f"result-{reconciliation_status}",
                    "summary": "Recorded as local test evidence.",
                },
            ),
            kernel_ping_context(f"reconcile-{reconciliation_status}"),
        )
    return state


def _generic_closure_state(*, manual_root: bool = False):
    source = generic_lifecycle.source()
    source_schema = next(
        row
        for row in source["artifact_schemas"]
        if row["id"] == generic_lifecycle.SOURCE_SCHEMA_ID
    )["schema"]
    source_schema["properties"]["root_source"] = {
        "type": "object",
        "required": ("kind", "source_id"),
        "properties": {
            "kind": {"type": "string", "min_length": 1},
            "source_id": {"type": "string", "min_length": 1},
        },
    }
    source_schema["properties"].update(
        {
            "title": {"type": "string", "min_length": 1},
            "body": {"type": "string", "min_length": 1},
        }
    )
    source_schema["required"] = ()
    beta_schema = next(
        row
        for row in source["artifact_schemas"]
        if row["id"] == generic_lifecycle.BETA_REPORT_SCHEMA_ID
    )
    beta_schema["schema"] = _closure_verdict_schema()
    review = next(row for row in source["stage_kinds"] if row["id"] == "review_stage")
    review["declared_outcome_ids"] = (
        "lifecycle.review.complete",
        "lifecycle.review.gap",
        "lifecycle.review.blocked",
    )
    source["terminal_outcomes"].extend(
        (
            {
                "id": "lifecycle.review.complete",
                "stage_kind_id": "review_stage",
                "marker": "REVIEW_COMPLETE",
            },
            {
                "id": "lifecycle.review.gap",
                "stage_kind_id": "review_stage",
                "marker": "REMEDIATION_NEEDED",
            },
            {
                "id": "lifecycle.review.blocked",
                "stage_kind_id": "review_stage",
                "marker": "BLOCKED",
            },
        )
    )
    source["terminal_actions"].extend(
        (
            {
                "id": "lifecycle.review.close",
                "stage_kind_id": "review_stage",
                "outcome_id": "lifecycle.review.complete",
                "kind": "complete_work_item",
                "artifact_schema_id": generic_lifecycle.BETA_REPORT_SCHEMA_ID,
            },
            {
                "id": "lifecycle.review.gap_action",
                "stage_kind_id": "review_stage",
                "outcome_id": "lifecycle.review.gap",
                "kind": "closure_gap",
                "artifact_schema_id": generic_lifecycle.BETA_REPORT_SCHEMA_ID,
            },
            {
                "id": "lifecycle.review.block_action",
                "stage_kind_id": "review_stage",
                "outcome_id": "lifecycle.review.blocked",
                "kind": "block_work_item",
                "artifact_schema_id": generic_lifecycle.BETA_REPORT_SCHEMA_ID,
            },
        )
    )
    source["completion_behaviors"] = (
        {
            "id": "lifecycle.closure",
            "trigger": "backlog_drained",
            "readiness_rule": "no_open_lineage_work",
            "request_kind": "closure_target",
            "target_selector": "active_closure_target",
            "target_stage_kind_id": "review_stage",
            "target_graph_node_id": "lifecycle.review.start",
            "runner_binding_id": "lifecycle.runner",
            "request_queue_family_id": "joined_bundle",
            "pass_action_id": "lifecycle.review.close",
            "gap_action_id": "lifecycle.review.gap_action",
            "blocked_action_id": "lifecycle.review.block_action",
            "verdict_artifact_schema_id": generic_lifecycle.BETA_REPORT_SCHEMA_ID,
            "evidence_artifact_schema_ids": (generic_lifecycle.BETA_REPORT_SCHEMA_ID,),
            "evidence_item_limit": 64,
            "request_payload_byte_limit": 16_384,
            "remediation_policy_id": "lifecycle.remediation",
            "accepted_root_source_kinds": ("manual", "probe"),
            "root_source_resolution": "runtime_inventory",
            "evidence_window_policy": "lineage",
            "rubric_policy": "reuse_or_create",
            "blocked_work_policy": "suppress",
            "skip_if_closed": True,
        },
    )
    source["remediation_policies"] = (
        {
            "id": "lifecycle.remediation",
            "source_action_id": "lifecycle.review.gap_action",
            "target_queue_family_id": "alpha_branch",
            "target_stage_kind_id": "alpha_stage",
            "target_graph_node_id": "lifecycle.alpha.start",
            "target_runner_binding_id": "lifecycle.runner",
            "payload_schema_id": generic_lifecycle.SOURCE_SCHEMA_ID,
            "guidance_source": "source_artifact",
            "dedupe_key": "closure_target_and_source_artifact",
            "duplicate_policy": "refuse",
            "suppression_policy": "suppress_repeated_same_evidence",
            "root_source_kind": "probe",
        },
    )
    runner = next(
        row for row in source["runner_bindings"] if row["id"] == "lifecycle.runner"
    )
    runner["component_pin"] = {
        "component_kind": "closure.runner",
        "component_id": "closure-evaluator",
        "component_version": "1",
        "provider_distribution": "millrace-test",
        "provider_version": "1",
        "descriptor_media_type": "application/json",
        "descriptor_sha256": "a" * 64,
        "required_capability_ids": (),
        "legal_terminal_result_ids": ("BLOCKED", "COMPLETE"),
        "max_work_item_payload_bytes": 16_384,
    }
    runner["terminal_result_mappings"] = ()
    plan, fingerprint = generic_lifecycle.compile_lifecycle(source)
    state, _plan, _fingerprint = generic_lifecycle.admitted_state(
        plan=plan,
        fingerprint=fingerprint,
    )
    root_source_kind = "manual" if manual_root else "probe"
    payload = {
        **generic_lifecycle.source_payload(),
        "root_source": {"kind": root_source_kind, "source_id": "source-1"},
    }
    state = generic_lifecycle.apply_accepted_input(
        state,
        EnqueueWork(
            "enqueue-origin",
            queue_family_id=QueueFamilyId("origin"),
            payload=payload,
        ),
        generic_lifecycle.context(
            "enqueue-origin",
            work_item_id="work-origin",
            activation_id="activation-origin",
        ),
    )
    lineage_id = "work-origin"
    root_work_item_id = "work-origin"
    transition_input = OpenClosureTarget(
        "open-lifecycle-closure",
        selected_plan_ref=state.default_plan_ref,
        completion_behavior_id="lifecycle.closure",
        closure_target_id="lifecycle-closure",
        lineage_id=lineage_id,
        root_source_kind=root_source_kind,
        root_source_id="source-1",
        closure_root_work_item_id=root_work_item_id,
        request_kind="closure_target",
        target_graph_node_id="lifecycle.review.start",
        evidence_window={"kind": "lineage", "lineage_id": lineage_id},
    )
    state = generic_lifecycle.apply_accepted_input(
        state,
        transition_input,
        generic_lifecycle.context(transition_input.input_id),
    )
    if root_work_item_id is not None:
        state = replace(
            state,
            closed_work_items={
                root_work_item_id: ClosedWorkItemRecord(
                    record_id=root_work_item_id,
                    work_item_id=root_work_item_id,
                    source_run_id=None,
                    action_id=None,
                    created_by_input_id="close-root",
                )
            },
        )
    transition_input = EvaluateCompletionBehavior(
        "evaluate-lifecycle-closure",
        selected_plan_ref=state.default_plan_ref,
        completion_behavior_id="lifecycle.closure",
        closure_target_id="lifecycle-closure",
    )
    state = generic_lifecycle.apply_accepted_input(
        state,
        transition_input,
        generic_lifecycle.context(
            transition_input.input_id,
            work_item_id="work-review-closure",
            activation_id="activation-review-closure",
        ),
    )
    return state, plan, fingerprint


def _closure_verdict_schema() -> dict[str, object]:
    string = {"type": "string", "min_length": 1}
    evidence_ref = {
        "type": "object",
        "required": ("evidence_id", "summary"),
        "properties": {"evidence_id": string, "summary": string},
    }
    criterion = {
        "type": "object",
        "required": ("criterion_id", "requirement", "evidence_rule"),
        "properties": {
            "criterion_id": string,
            "requirement": string,
            "evidence_rule": string,
        },
    }
    result = {
        "type": "object",
        "required": ("criterion_id", "status", "provenance", "evidence_refs"),
        "properties": {
            "criterion_id": string,
            "status": {"enum": ("passed", "failed", "blocked")},
            "provenance": {
                "enum": (
                    "fresh",
                    "revalidated",
                    "historical_only",
                    "missing",
                )
            },
            "evidence_refs": {
                "type": "array",
                "items": evidence_ref,
                "unique_by": "evidence_id",
            },
        },
    }
    guidance = {
        "type": "object",
        "required": ("guidance_id", "summary", "criterion_refs"),
        "properties": {
            "guidance_id": string,
            "summary": string,
            "criterion_refs": {
                "type": "array",
                "min_items": 1,
                "items": {
                    "type": "object",
                    "required": ("criterion_id",),
                    "properties": {"criterion_id": string},
                },
                "unique_by": "criterion_id",
            },
        },
    }
    properties = {
        "bundle_id": string,
        "artifact_kind": string,
        "summary": string,
        "closure_target_id": string,
        "root_contract_digest": string,
        "freshness_anchor_digest": string,
        "rubric": {
            "type": "object",
            "required": ("criteria",),
            "properties": {
                "criteria": {
                    "type": "array",
                    "min_items": 1,
                    "items": criterion,
                    "unique_by": "criterion_id",
                }
            },
        },
        "criterion_results": {
            "type": "array",
            "min_items": 1,
            "items": result,
            "unique_by": "criterion_id",
        },
        "observations": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ("observation_id", "summary"),
                "properties": {"observation_id": string, "summary": string},
            },
            "unique_by": "observation_id",
        },
        "remediation_guidance": {
            "type": "array",
            "items": guidance,
            "unique_by": "guidance_id",
        },
        "confidence": {"enum": ("high", "medium", "low")},
        "residual_uncertainty": string,
    }
    return {
        "type": "object",
        "required": tuple(
            key for key in properties if key != "bundle_id"
        ),
        "properties": properties,
    }


def _closure_verdict_payload(
    snapshot: Mapping[str, object],
    *,
    marker: str,
) -> dict[str, object]:
    gap = marker == "REMEDIATION_NEEDED"
    blocked = marker == "BLOCKED"
    result = {
        "criterion_id": "criterion-1",
        "status": "blocked" if blocked else "failed" if gap else "passed",
        "provenance": "missing" if blocked else "fresh",
        "evidence_refs": ()
        if blocked
        else ({"evidence_id": "evidence-1", "summary": "reviewed"},),
    }
    return {
        "artifact_kind": "closure_verdict",
        "summary": "Review completed.",
        "closure_target_id": snapshot["closure_target_id"],
        "root_contract_digest": snapshot["root_contract"]["payload_digest"],
        "freshness_anchor_digest": snapshot["freshness_anchor_digest"],
        "rubric": {
            "criteria": (
                {
                    "criterion_id": "criterion-1",
                    "requirement": "The closure contract is satisfied.",
                    "evidence_rule": "Use current review evidence.",
                },
            )
        },
        "criterion_results": (result,),
        "observations": (),
        "remediation_guidance": (
            (
                {
                    "guidance_id": "guidance-1",
                    "summary": "Address criterion-1.",
                    "criterion_refs": ({"criterion_id": "criterion-1"},),
                },
            )
            if gap
            else ()
        ),
        "confidence": "high",
        "residual_uncertainty": "none",
    }


def test_status_projects_selected_authority_read_only() -> None:
    state, _plan, fingerprint = generic_lifecycle.admitted_state()

    status = operator_status(state)

    assert status.selected_plan is not None
    assert status.selected_plan.workflow_id == "lifecycle_probe"
    assert status.selected_plan.workflow_version == "0.1"
    assert status.selected_plan.authority_fingerprint == fingerprint
    assert {
        partition.partition_id: partition.partition_kind
        for partition in status.partitions
    } == {"primary": "lane"}
    assert state == generic_lifecycle.admitted_state()[0]


def test_status_projects_selected_queue_and_stage_counts() -> None:
    state, _plan, _fingerprint = generic_lifecycle.one_report_state()

    status = operator_status(state)

    assert {family.queue_family_id for family in status.queue_families} == {
        "origin",
        "alpha_branch",
        "beta_branch",
        "joined_bundle",
    }
    alpha = _queue(status, "alpha_branch")
    assert (alpha.ready_count, alpha.closed_count) == (1, 1)
    assert (_stage(status, "alpha_stage").closed_count) == 1
    assert (_stage(status, "beta_stage").ready_count) == 2


def test_status_projects_fanout_branches_and_computed_join_missing_evidence() -> None:
    state, _plan, _fingerprint = generic_lifecycle.one_report_state()

    status = operator_status(state)

    branches = {row.target_stage_kind_id for row in status.generated_work}
    assert branches == {"alpha_stage", "beta_stage"}
    assert {row.source_artifact_id for row in status.generated_work} == {
        generic_lifecycle.source_artifact_id()
    }
    assert {row.lineage_id for row in status.generated_work} == {"work-origin"}
    assert len(status.joins) == 1
    join = status.joins[0]
    assert join.join_id == generic_lifecycle.JOIN_ID
    assert join.lineage_id == "work-origin"
    assert join.source_artifact_id == generic_lifecycle.source_artifact_id()
    assert (join.correlation_key, join.correlation_value) == (
        "bundle_id",
        "bundle-a",
    )
    assert join.required_artifact_schema_ids == (
        generic_lifecycle.ALPHA_REPORT_SCHEMA_ID,
        generic_lifecycle.BETA_REPORT_SCHEMA_ID,
    )
    assert join.observed_artifact_schema_ids == (
        generic_lifecycle.ALPHA_REPORT_SCHEMA_ID,
    )
    assert join.missing_artifact_schema_ids == (
        generic_lifecycle.BETA_REPORT_SCHEMA_ID,
    )
    assert join.ready is False
    assert join.target_stage_kind_id == "review_stage"


def test_status_projects_computed_join_ready_evidence() -> None:
    state, _plan, _fingerprint = generic_lifecycle.two_report_state()

    join = operator_status(state).joins[0]

    assert join.observed_artifact_schema_ids == (
        generic_lifecycle.ALPHA_REPORT_SCHEMA_ID,
        generic_lifecycle.BETA_REPORT_SCHEMA_ID,
    )
    assert join.missing_artifact_schema_ids == ()
    assert join.ready is True


def test_status_projects_multi_item_join_ready_evidence_after_join() -> None:
    state, _plan, _fingerprint = generic_lifecycle.complete_multi_item_report_state()

    status = operator_status(generic_lifecycle.apply_join(state))

    assert len(status.joins) == 1
    assert status.joins[0].missing_artifact_schema_ids == ()
    assert status.joins[0].ready is True


def test_status_keeps_multi_item_join_not_ready_until_every_slot_reports() -> None:
    state, _plan, _fingerprint = (
        generic_lifecycle.schema_covered_but_incomplete_report_state()
    )

    join = operator_status(state).joins[0]

    assert join.observed_artifact_schema_ids == (
        generic_lifecycle.ALPHA_REPORT_SCHEMA_ID,
        generic_lifecycle.BETA_REPORT_SCHEMA_ID,
    )
    assert join.missing_artifact_schema_ids == ()
    assert join.ready is False


def test_status_refuses_duplicate_evidence_for_one_selected_fanout_slot() -> None:
    state, _plan, _fingerprint = generic_lifecycle.complete_multi_item_report_state()
    alpha = next(
        artifact
        for artifact in state.artifacts.values()
        if str(artifact.schema_id) == generic_lifecycle.ALPHA_REPORT_SCHEMA_ID
    )
    state = replace(
        state,
        artifacts={
            **state.artifacts,
            f"{alpha.artifact_id}:duplicate": replace(
                alpha,
                artifact_id=f"{alpha.artifact_id}:duplicate",
            ),
        },
    )

    assert operator_status(state).joins[0].ready is False


def test_status_projects_mismatched_join_evidence_as_missing_not_ready() -> None:
    state, _plan, _fingerprint = (
        generic_lifecycle.schema_covered_but_incomplete_report_state()
    )
    state = generic_lifecycle.with_mismatched_beta_report(state)

    join = operator_status(state).joins[0]

    assert join.observed_artifact_schema_ids == (
        generic_lifecycle.ALPHA_REPORT_SCHEMA_ID,
    )
    assert join.missing_artifact_schema_ids == (
        generic_lifecycle.BETA_REPORT_SCHEMA_ID,
    )
    assert join.ready is False


def test_status_projects_cross_lineage_progress() -> None:
    state, _plan, _fingerprint = generic_lifecycle.two_group_report_state()

    status = operator_status(state)

    assert {join.lineage_id for join in status.joins} == {
        "work-a-origin",
        "work-b-origin",
    }
    assert {join.correlation_value for join in status.joins} == {
        "bundle-a",
        "bundle-b",
    }
    assert all(join.ready for join in status.joins)


def test_status_projection_is_read_only_and_deterministic() -> None:
    state, _plan, _fingerprint = generic_lifecycle.one_report_state()

    first = operator_status(state, max_events=8)
    second = operator_status(state, max_events=8)

    assert first == second
    assert state == generic_lifecycle.one_report_state()[0]


def test_status_projects_operator_interventions() -> None:
    plan_ref = PlanRef(
        plan_id="lifecycle_probe:0.1",
        authority_fingerprint=f"sha256:{'a' * 64}",
        plan_format_version=SelectedCompiledPlan.schema_version,
    )
    record = OperatorInterventionRecord(
        record_id="operator-intervention:revise-lineage",
        created_by_input_id="revise-lineage",
        input_payload_digest=f"sha256:{'b' * 64}",
        option_id="lifecycle.revise_lineage",
        kind="revise_lineage",
        result="revised",
        policy_id=RecoveryPolicyId("lifecycle.recovery"),
        lineage_id="work-origin",
        quarantine_id="lineage-quarantine:1",
        recovery_attempt_record_id="recovery-attempt:1",
        recovery_attempt_count=3,
        attempt_effect="resolve_attempt",
        selected_plan_ref=plan_ref,
        selected_plan_fingerprint=plan_ref.authority_fingerprint,
        actor_kind="local_operator",
        actor_id="operator-a",
        reason="operator supplied revised payload",
        target_work_item_id="work-revised",
        target_activation_id="activation-revised",
        closed_work_item_ids=(),
        closed_activation_ids=(),
        closed_run_ids=(),
        payload_digest=f"sha256:{'c' * 64}",
        payload_reference="work_item:work-revised:payload",
    )

    status = operator_status(
        RuntimeState(operator_interventions={record.record_id: record})
    )

    assert len(status.interventions) == 1
    intervention = status.interventions[0]
    assert intervention.record_id == record.record_id
    assert intervention.created_by_input_id == "revise-lineage"
    assert intervention.input_payload_digest == f"sha256:{'b' * 64}"
    assert intervention.option_id == "lifecycle.revise_lineage"
    assert intervention.kind == "revise_lineage"
    assert intervention.result == "revised"
    assert intervention.policy_id == "lifecycle.recovery"
    assert intervention.lineage_id == "work-origin"
    assert intervention.quarantine_id == "lineage-quarantine:1"
    assert intervention.recovery_attempt_record_id == "recovery-attempt:1"
    assert intervention.recovery_attempt_count == 3
    assert intervention.attempt_effect == "resolve_attempt"
    assert intervention.selected_plan_fingerprint == plan_ref.authority_fingerprint
    assert intervention.actor_kind == "local_operator"
    assert intervention.actor_id == "operator-a"
    assert intervention.reason == "operator supplied revised payload"
    assert intervention.target_work_item_id == "work-revised"
    assert intervention.target_activation_id == "activation-revised"
    assert intervention.closed_work_item_ids == ()
    assert intervention.closed_activation_ids == ()
    assert intervention.closed_run_ids == ()
    assert intervention.payload_digest == f"sha256:{'c' * 64}"
    assert intervention.payload_reference == "work_item:work-revised:payload"


def test_status_sorts_selected_authority_without_cardinality_assumptions() -> None:
    source = generic_lifecycle.source()
    partitions = cast(Sequence[object], source["partitions"])
    stages = cast(list[dict[str, object]], source["stage_kinds"])
    stages[0]["partition_id"] = None
    source["partitions"] = tuple(reversed(partitions))
    source["stage_kinds"] = tuple(reversed(stages))
    plan, fingerprint = generic_lifecycle.compile_lifecycle(source)
    state = empty_runtime_state()
    for transition_input in (
        AdmitPlan("admit", plan, fingerprint),
        SelectDefaultPlan("select", fingerprint),
    ):
        state = generic_lifecycle.apply_accepted_input(
            state,
            transition_input,
            generic_lifecycle.context(transition_input.input_id),
        )

    status = operator_status(state)

    assert [partition.partition_id for partition in status.partitions] == ["primary"]
    assert [stage.stage_kind_id for stage in status.stage_kinds] == [
        "alpha_stage",
        "beta_stage",
        "origin_stage",
        "review_stage",
    ]
    assert len(status.partitions) == 1
    assert len(status.stage_kinds) == 4
    assert any(stage.partition_id is None for stage in status.stage_kinds)


def test_status_projects_partitionless_stage_as_absence() -> None:
    source = generic_lifecycle.source()
    stages = cast(list[dict[str, object]], source["stage_kinds"])
    alpha = next(stage for stage in stages if stage["id"] == "alpha_stage")
    alpha["partition_id"] = None
    plan, fingerprint = generic_lifecycle.compile_lifecycle(source)
    state = empty_runtime_state()
    for transition_input in (
        AdmitPlan("admit-partitionless", plan, fingerprint),
        SelectDefaultPlan("select-partitionless", fingerprint),
    ):
        state = generic_lifecycle.apply_accepted_input(
            state,
            transition_input,
            generic_lifecycle.context(transition_input.input_id),
        )

    status = operator_status(state)

    assert [partition.partition_id for partition in status.partitions] == ["primary"]
    assert {partition.partition_id for partition in status.partitions}.isdisjoint(
        {None, "None", "alpha_stage"}
    )
    assert [stage.stage_kind_id for stage in status.stage_kinds] == [
        "alpha_stage",
        "beta_stage",
        "origin_stage",
        "review_stage",
    ]
    partitions_by_stage = {
        stage.stage_kind_id: stage.partition_id for stage in status.stage_kinds
    }
    assert partitions_by_stage == {
        "alpha_stage": None,
        "beta_stage": "primary",
        "origin_stage": "primary",
        "review_stage": "primary",
    }
    assert [family.queue_family_id for family in status.queue_families] == [
        "alpha_branch",
        "beta_branch",
        "joined_bundle",
        "origin",
    ]
    assert {
        family.queue_family_id: family.external_enqueue
        for family in status.queue_families
    } == {
        "alpha_branch": False,
        "beta_branch": False,
        "joined_bundle": False,
        "origin": True,
    }


def test_status_ignores_legacy_filesystem_indexes_and_aliases(
    tmp_path: Path,
) -> None:
    state, _plan, fingerprint = generic_lifecycle.origin_queued_state()
    legacy_files = {
        tmp_path / "millrace-agents" / "legacy" / "status.json": '{"ready": 99}',
        tmp_path / "millrace-agents" / "queues" / "origin" / "ghost.md": (
            "stale queue item"
        ),
        tmp_path / "millrace-agents" / "indexes" / "remote.md": "# stale index",
        tmp_path / "millrace-agents" / "aliases" / "fixture.json": ('{"alias": true}'),
    }
    for path, body in legacy_files.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")

    status = operator_status(state)

    assert status.selected_plan is not None
    assert status.selected_plan.authority_fingerprint == fingerprint
    family = _queue(status, "origin")
    assert (
        family.ready_count,
        family.active_count,
        family.closed_count,
        family.quarantined_count,
        family.operator_wait_count,
    ) == (1, 0, 0, 0, 0)
    assert status.artifacts == ()
    assert status.effects == ()
    assert status.generated_work == ()


def test_status_projects_ready_and_closed_queue_counts() -> None:
    ready, _plan, _fingerprint = generic_lifecycle.origin_queued_state()
    ready_family = _queue(operator_status(ready), "origin")

    assert (
        ready_family.ready_count,
        ready_family.active_count,
        ready_family.closed_count,
        ready_family.quarantined_count,
    ) == (1, 0, 0, 0)

    closed, _plan, _fingerprint = generic_lifecycle.origin_closed_state()
    closed_status = operator_status(closed)
    closed_family = _queue(closed_status, "origin")

    assert (
        closed_family.ready_count,
        closed_family.active_count,
        closed_family.closed_count,
        closed_family.quarantined_count,
    ) == (0, 0, 1, 0)
    assert closed_status.active_runs == ()


def test_status_derives_metadata_and_active_runs_from_selected_plan() -> None:
    state, _plan, fingerprint = generic_lifecycle.origin_claimed_state()

    status = operator_status(state)

    assert status.selected_plan is not None
    assert status.selected_plan.workflow_id == "lifecycle_probe"
    assert status.selected_plan.workflow_version == "0.1"
    assert status.selected_plan.workflow_name == "Lifecycle Probe"
    assert status.selected_plan.authority_fingerprint == fingerprint
    assert {
        known_plan.authority_fingerprint: known_plan.selected_default
        for known_plan in status.known_plans
    } == {fingerprint: True}
    assert [family.queue_family_id for family in status.queue_families] == [
        "alpha_branch",
        "beta_branch",
        "joined_bundle",
        "origin",
    ]
    assert [partition.partition_id for partition in status.partitions] == ["primary"]
    assert [stage.stage_kind_id for stage in status.stage_kinds] == [
        "alpha_stage",
        "beta_stage",
        "origin_stage",
        "review_stage",
    ]

    assert len(status.active_runs) == 1
    active_run = status.active_runs[0]
    assert active_run.run_id == "run-origin"
    assert active_run.work_item_id == "work-origin"
    assert active_run.activation_id == "activation-origin"
    assert active_run.queue_family_id == "origin"
    assert active_run.graph_node_id == "lifecycle.origin.start"
    assert active_run.stage_kind_id == "origin_stage"
    assert active_run.runner_binding_id == "lifecycle.runner"
    assert active_run.plan_fingerprint == fingerprint


def test_status_recent_events_include_accepted_and_refused_context() -> None:
    state, plan, fingerprint = generic_lifecycle.origin_closed_state()
    run = state.runs["run-origin"]
    activation = state.activations[run.activation_id]
    duplicate = RunnerResultObserved(
        "observe-origin-again",
        run_id=run.run_ref.run_id,
        payload=fake_runner_observation_payload(
            run=run,
            activation=activation,
            plan_fingerprint=fingerprint,
            marker="SOURCE_READY",
            artifact_payload=generic_lifecycle.source_payload(),
        ),
        observed_at=None,
    )
    state, duplicate = fake_completed_runner_observation_state(
        state=state,
        observation=duplicate,
    )
    decision = decide(
        state,
        duplicate,
        generic_lifecycle.context("observe-origin-again"),
    )
    assert decision.accepted is False
    state = apply(state, decision)

    status = operator_status(state, max_events=8)
    accepted_id = fake_runner_completion_input_id("observe-origin")
    refused_id = fake_runner_completion_input_id("observe-origin-again")
    accepted_events = tuple(
        event for event in status.recent_events if event.input_id == accepted_id
    )
    refused_events = tuple(
        event for event in status.recent_events if event.input_id == refused_id
    )

    assert {(event.source, event.disposition) for event in accepted_events} == {
        ("governance_event", "accepted"),
        ("trace", "accepted"),
    }
    assert {
        (
            event.source,
            event.disposition,
            event.action_id,
            event.authority_source,
            event.refusal_reason,
            event.plan_fingerprint,
        )
        for event in refused_events
    } == {
        (
            "governance_event",
            "refused",
            None,
            None,
            "invalid_observation_authority",
            fingerprint,
        ),
        (
            "trace",
            "refused",
            None,
            None,
            "invalid_observation_authority",
            fingerprint,
        ),
    }


def test_status_projects_recovery_attempts() -> None:
    plan, fingerprint = generic_admission.compile_plan()
    state = empty_runtime_state()
    for transition_input in (
        AdmitPlan("admit-recovery-status", plan, fingerprint),
        SelectDefaultPlan("select-recovery-status", fingerprint),
    ):
        state = generic_lifecycle.apply_accepted_input(
            state,
            transition_input,
            generic_lifecycle.context(transition_input.input_id),
        )
    assert state.default_plan_ref is not None
    plan_ref = state.default_plan_ref
    record = RecoveryAttemptRecord(
        record_id="recovery-attempt:1",
        policy_id=RecoveryPolicyId(generic_admission.RECOVERY_POLICY_ID),
        lineage_id="work-origin",
        plan_ref=plan_ref,
        attempt_count=1,
        phase="active_recovery",
        source_run_id="run-origin",
        source_work_item_id="work-origin",
        source_activation_id="activation-origin",
        source_graph_node_id=generic_admission.PARENT_NODE_ID,
        source_stage_kind_id=StageKindId(generic_admission.PARENT_STAGE_ID),
        source_runner_binding_id=RunnerBindingId(generic_admission.RUNNER_ID),
        source_queue_family_id=QueueFamilyId("parent"),
        recovery_action_id=ActionId(generic_admission.RECOVERY_SOURCE_ACTION_ID),
        latest_recovery_activation_id="activation-recovery",
        latest_recovery_run_id=None,
        latest_return_action_id=None,
        created_by_input_id="observe-origin-blocked",
        updated_by_input_id="observe-origin-blocked",
    )

    status = operator_status(
        replace(state, recovery_attempts={record.record_id: record})
    )

    assert len(status.recovery_attempts) == 1
    attempt = status.recovery_attempts[0]
    assert attempt.policy_id == generic_admission.RECOVERY_POLICY_ID
    assert attempt.lineage_id == "work-origin"
    assert attempt.plan_fingerprint == plan_ref.authority_fingerprint
    assert attempt.attempt_count == 1
    assert attempt.phase == "active_recovery"
    assert attempt.source_run_id == "run-origin"
    assert attempt.source_work_item_id == "work-origin"
    assert attempt.source_stage_kind_id == generic_admission.PARENT_STAGE_ID
    assert attempt.source_queue_family_id == "parent"
    assert attempt.latest_recovery_activation_id == "activation-recovery"


def test_status_projects_cooldown_waits() -> None:
    plan, fingerprint = generic_admission.compile_plan()
    state = empty_runtime_state()
    for transition_input in (
        AdmitPlan("admit-cooldown-status", plan, fingerprint),
        SelectDefaultPlan("select-cooldown-status", fingerprint),
    ):
        state = generic_lifecycle.apply_accepted_input(
            state,
            transition_input,
            generic_lifecycle.context(transition_input.input_id),
        )
    assert state.default_plan_ref is not None
    plan_ref = state.default_plan_ref
    record = CooldownWaitRecord(
        wait_id="cooldown-wait:1",
        policy_id=RecoveryPolicyId(generic_admission.RECOVERY_POLICY_ID),
        lineage_id="work-origin",
        recovery_attempt_record_id="recovery-attempt:1",
        attempt_count=2,
        source_run_id="run-origin-retry",
        source_work_item_id="work-origin",
        source_activation_id="activation-origin-returned",
        recovery_action_id=ActionId(generic_admission.RECOVERY_SOURCE_ACTION_ID),
        target_stage_kind_id=StageKindId(generic_admission.RECOVERY_STAGE_ID),
        target_graph_node_id=generic_admission.RECOVERY_NODE_ID,
        target_runner_binding_id=RunnerBindingId(generic_admission.RUNNER_ID),
        plan_ref=plan_ref,
        created_input_id="observe-origin-blocked-2",
        created_at=1000,
        due_at=1900,
        consumed_input_id=None,
        consumed_at=None,
        resulting_recovery_activation_id=None,
    )

    status = operator_status(replace(state, cooldown_waits={record.wait_id: record}))

    assert len(status.cooldown_waits) == 1
    wait = status.cooldown_waits[0]
    assert wait.policy_id == generic_admission.RECOVERY_POLICY_ID
    assert wait.lineage_id == "work-origin"
    assert wait.plan_fingerprint == plan_ref.authority_fingerprint
    assert wait.attempt_count == 2
    assert wait.source_run_id == "run-origin-retry"
    assert wait.source_work_item_id == "work-origin"
    assert wait.source_activation_id == "activation-origin-returned"
    assert wait.recovery_action_id == generic_admission.RECOVERY_SOURCE_ACTION_ID
    assert wait.target_stage_kind_id == generic_admission.RECOVERY_STAGE_ID
    assert wait.target_graph_node_id == generic_admission.RECOVERY_NODE_ID
    assert wait.target_runner_binding_id == generic_admission.RUNNER_ID
    assert wait.created_input_id == "observe-origin-blocked-2"
    assert wait.created_at == 1000
    assert wait.due_at == 1900
    assert wait.consumed_input_id is None
    assert wait.consumed_at is None
    assert wait.resulting_recovery_activation_id is None


@pytest.mark.parametrize(
    "case",
    (
        "ready_count",
        "active_count",
        "closed_count",
        "default_plan_filter",
        "missing_artifact_source",
        "artifact_context",
        "artifact_action_drift",
        "artifact_creator_drift",
        "artifact_digest_drift",
        "artifact_runner_drift",
        "artifact_marker_drift",
        "artifact_graph_drift",
        "generated_context",
        "generated_work_lineage_drift",
        "generated_activation_lineage_drift",
        "generated_runner_drift",
        "generated_graph_drift",
        "generated_marker_drift",
        "effect_pending",
        "effect_applied",
        "effect_no_op",
        "effect_refused",
        "effect_provider_drift",
        "effect_status_drift",
        "effect_target_drift",
        "effect_reconciliation_drift",
        "effect_marker_drift",
    ),
)
def test_status_projects_generic_lifecycle_rows_and_excludes_corruption(
    case: str,
) -> None:
    if case == "ready_count":
        state, _plan, _fingerprint = generic_lifecycle.origin_queued_state()
        assert _queue(operator_status(state), "origin").ready_count == 1
        return
    if case == "active_count":
        state, _plan, _fingerprint = generic_lifecycle.origin_claimed_state()
        status = operator_status(state)
        assert _queue(status, "origin").active_count == 1
        assert {row.run_id for row in status.active_runs} == {"run-origin"}
        return
    if case == "closed_count":
        state, _plan, _fingerprint = generic_lifecycle.origin_closed_state()
        assert _queue(operator_status(state), "origin").closed_count == 1
        return
    if case == "default_plan_filter":
        state, _first, _first_fingerprint, _second, second_fingerprint = (
            generic_lifecycle.two_plan_origin_closed_state()
        )
        status = operator_status(state)
        assert status.selected_plan is not None
        assert status.selected_plan.authority_fingerprint == second_fingerprint
        assert {row.source_run_id for row in status.artifacts} == {"run-second-origin"}
        return

    if case.startswith("artifact_") or case == "missing_artifact_source":
        state, _plan, fingerprint = generic_lifecycle.origin_closed_state()
        artifact = state.artifacts[generic_lifecycle.source_artifact_id()]
        if case == "artifact_context":
            row = operator_status(state).artifacts[0]
            assert row.selected_plan_fingerprint == fingerprint
            assert row.source_action_id == "lifecycle.origin.complete"
            assert row.source_run_id == "run-origin"
            assert row.source_stage_kind_id == "origin_stage"
            assert row.latest_marker == "SOURCE_READY"
            return
        if case == "missing_artifact_source":
            artifact = replace(artifact, source_run_id="missing-run")
        elif case == "artifact_action_drift":
            artifact = replace(artifact, source_action_id=ActionId("wrong.action"))
        elif case == "artifact_creator_drift":
            artifact = replace(artifact, created_by_input_id="claim-origin")
        elif case == "artifact_digest_drift":
            artifact = replace(artifact, payload_digest=f"sha256:{'0' * 64}")
        elif case == "artifact_runner_drift":
            run = state.runs[artifact.source_run_id]
            activation = state.activations[run.activation_id]
            state = replace(
                state,
                runs={
                    **state.runs,
                    run.run_ref.run_id: replace(
                        run,
                        runner_binding_id=RunnerBindingId("wrong.runner"),
                    ),
                },
                activations={
                    **state.activations,
                    activation.activation_id: replace(
                        activation,
                        runner_binding_id=RunnerBindingId("wrong.runner"),
                    ),
                },
            )
        elif case == "artifact_marker_drift":
            observation = next(iter(state.runner_observations.values()))
            changed = replace(
                observation,
                payload={**observation.payload, "marker": "WRONG"},
            )
            state = replace(
                state,
                runner_observations={changed.observation_id: changed},
            )
        else:
            artifact = replace(artifact, source_graph_node_id="wrong.node")
        state = replace(
            state,
            artifacts={**state.artifacts, artifact.artifact_id: artifact},
        )
        assert artifact.artifact_id not in {
            row.artifact_id for row in operator_status(state).artifacts
        }
        return

    if case.startswith("generated_"):
        state, _plan, fingerprint = generic_lifecycle.two_complete_fanouts_state()
        fanout = next(iter(state.fanout_records.values()))
        if case == "generated_context":
            row = next(
                row
                for row in operator_status(state).generated_work
                if row.generated_work_id == fanout.record_id
            )
            assert row.selected_plan_fingerprint == fingerprint
            assert row.source_artifact_id == generic_lifecycle.source_artifact_id()
            assert row.target_stage_kind_id in {"alpha_stage", "beta_stage"}
            return
        if case == "generated_work_lineage_drift":
            work = state.work_items[fanout.target_work_item_id]
            state = replace(
                state,
                work_items={
                    **state.work_items,
                    fanout.target_work_item_id: replace(work, lineage_id="wrong"),
                },
            )
        elif case == "generated_activation_lineage_drift":
            activation = state.activations[fanout.target_activation_id]
            state = replace(
                state,
                activations={
                    **state.activations,
                    fanout.target_activation_id: replace(
                        activation,
                        lineage_id="wrong",
                    ),
                },
            )
        elif case == "generated_runner_drift":
            activation = state.activations[fanout.target_activation_id]
            state = replace(
                state,
                activations={
                    **state.activations,
                    fanout.target_activation_id: replace(
                        activation,
                        runner_binding_id=RunnerBindingId("wrong.runner"),
                    ),
                },
            )
        elif case == "generated_graph_drift":
            state = replace(
                state,
                fanout_records={
                    **state.fanout_records,
                    fanout.record_id: replace(fanout, target_graph_node_id="wrong"),
                },
            )
        else:
            observation = next(
                row
                for row in state.runner_observations.values()
                if row.created_by_input_id
                == state.artifacts[fanout.source_artifact_id].created_by_input_id
            )
            changed = replace(
                observation,
                payload={**observation.payload, "marker": "WRONG"},
            )
            state = replace(
                state,
                runner_observations={
                    **state.runner_observations,
                    changed.observation_id: changed,
                },
            )
        assert fanout.record_id not in {
            row.generated_work_id for row in operator_status(state).generated_work
        }
        return

    reconciliation_status = {
        "effect_applied": "applied",
        "effect_no_op": "no_op",
        "effect_refused": "refused",
        "effect_reconciliation_drift": "applied",
    }.get(case)
    state = _generic_effect_state(reconciliation_status=reconciliation_status)
    effect = next(iter(state.effect_proposals.values()))
    if case in {
        "effect_pending",
        "effect_applied",
        "effect_no_op",
        "effect_refused",
    }:
        expected = reconciliation_status or "pending"
        row = operator_status(state).effects[0]
        assert row.status == expected
        assert row.effect_declaration_id == generic_effect.EFFECT_DECLARATION_ID
        return
    if case == "effect_provider_drift":
        effect = replace(effect, provider_ref="wrong.provider")
    elif case == "effect_status_drift":
        effect = replace(effect, status="applied")
    elif case == "effect_target_drift":
        effect = replace(effect, target_path_ref="wrong.target")
    elif case == "effect_reconciliation_drift":
        reconciliation = next(iter(state.effect_reconciliations.values()))
        state = replace(
            state,
            effect_reconciliations={
                reconciliation.reconciliation_id: replace(
                    reconciliation,
                    status="stale",
                )
            },
        )
    else:
        observation = next(iter(state.runner_observations.values()))
        changed = replace(
            observation,
            payload={**observation.payload, "marker": "WRONG"},
        )
        state = replace(
            state,
            runner_observations={changed.observation_id: changed},
        )
    state = replace(
        state,
        effect_proposals={effect.effect_id: effect},
    )
    assert effect.effect_id not in {
        row.effect_id for row in operator_status(state).effects
    }


@pytest.mark.parametrize(
    "case",
    ("open", "manual", "read_only_corruption", "remediation", "blocked"),
)
def test_status_projects_generic_closure_lifecycle(case: str) -> None:
    state, plan, fingerprint = _generic_closure_state(manual_root=case == "manual")
    if case == "read_only_corruption":
        target = state.closure_targets["lifecycle-closure"]
        drifted = replace(
            state,
            closure_targets={
                target.closure_target_id: replace(target, lineage_id="wrong-lineage")
            },
        )
        before = drifted
        operator_status(drifted)
        assert drifted == before
        return
    if case in {"remediation", "blocked"}:
        state = generic_lifecycle.claim_activation(
            state,
            activation_id="activation-review-closure",
            suffix="review-closure",
        )
        run = state.runs["run-review-closure"]
        activation = state.activations[run.activation_id]
        input_id = f"observe-review-{case}"
        state = generic_lifecycle.apply_accepted_input(
            state,
            RunnerResultObserved(
                input_id,
                run_id=run.run_ref.run_id,
                payload=fake_runner_observation_payload(
                    run=run,
                    activation=activation,
                    plan_fingerprint=fingerprint,
                    marker=(
                        "REMEDIATION_NEEDED" if case == "remediation" else "BLOCKED"
                    ),
                    artifact_payload=_closure_verdict_payload(
                        state.work_items["work-review-closure"].payload[
                            "closure_evidence_snapshot"
                        ],
                        marker=(
                            "REMEDIATION_NEEDED"
                            if case == "remediation"
                            else "BLOCKED"
                        ),
                    ),
                ),
                observed_at=None,
            ),
            generic_lifecycle.context(
                input_id,
                work_item_id=("work-remediation" if case == "remediation" else None),
                activation_id=(
                    "activation-remediation" if case == "remediation" else None
                ),
            ),
        )
    status = operator_status(state)
    target = status.closure_targets[0]
    assert target.closure_target_id == "lifecycle-closure"
    assert target.selected_plan_fingerprint == fingerprint
    if case == "manual":
        assert target.root_source_kind == "manual"
        assert target.closure_root_work_item_id == "work-origin"
    elif case == "remediation":
        assert target.latest_remediation_record_id is not None
        assert status.closure_remediations[0].target_work_item_id == "work-remediation"
    elif case == "blocked":
        assert target.operator_required is True
        assert status.closure_blocks[0].source_action_id == (
            "lifecycle.review.block_action"
        )
    else:
        assert target.status == "open"
        assert status.closure_evaluations[0].status == "ready"
