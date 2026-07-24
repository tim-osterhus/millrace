from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path

import pytest

from millrace.contracts import ActionId, QueueFamilyId
from millrace.contracts.compiled_plan import (
    AuthorityValue,
    CompletionBehaviorDeclaration,
    RemediationPolicyDeclaration,
    SelectedCompiledPlan,
    TerminalActionDeclaration,
)
from millrace.contracts.state import (
    Activation,
    AdmittedPlan,
    ArtifactRecord,
    ClosureBlockedRecord,
    ClosureEvaluationRecord,
    ClosureTargetRecord,
    ClosureTerminalRecord,
    GovernanceEventRecord,
    InputReceipt,
    InputReceiptRef,
    PlanRef,
    RemediationWorkRecord,
    RunnerObservationRecord,
    RunRecord,
    RunRef,
    RuntimeState,
    TraceRecord,
    TransitionRecord,
    WorkItem,
    WorkItemRef,
)
from millrace.contracts.transition import (
    RunnerResultObserved,
    artifact_payload_digest,
    input_payload_digest,
)
from millrace.substrate.cas import ContentAddressedByteStore
from millrace.substrate.codecs import dumps_cas_object, encode_payload
from millrace.substrate.errors import StorageIntegrityError
from millrace.testing.fakes import fake_runner_observation_payload
from substrate._runtime_store_support import (
    load_runtime_state,
    persist_runtime_state,
    runtime_store_paths,
)
from support.lad_planning import artifact_payload, compile_lad_planning

COMPLETION_BEHAVIOR_ID = "planning.closure.completion"


def _plan_ref(plan: SelectedCompiledPlan, fingerprint: str) -> PlanRef:
    return PlanRef(
        plan_id=f"{plan.workflow.workflow_id.value}:{plan.workflow.workflow_version.value}",
        authority_fingerprint=fingerprint,
        plan_format_version=plan.schema_version,
    )


def _selected_authority(
    plan: SelectedCompiledPlan,
) -> tuple[
    CompletionBehaviorDeclaration,
    RemediationPolicyDeclaration,
    dict[str, TerminalActionDeclaration],
]:
    behavior = next(
        item
        for item in plan.completion_behaviors
        if str(item.id) == COMPLETION_BEHAVIOR_ID
    )
    policy = next(
        item
        for item in plan.remediation_policies
        if item.id == behavior.remediation_policy_id
    )
    actions = {str(action.id): action for action in plan.terminal_actions}
    return behavior, policy, actions


def _closure_target(
    *,
    target_id: str,
    plan_ref: PlanRef,
    behavior: CompletionBehaviorDeclaration,
    lineage_id: str,
    status: str = "open",
    closed_by_record_id: str | None = None,
) -> ClosureTargetRecord:
    return ClosureTargetRecord(
        closure_target_id=target_id,
        selected_plan_ref=plan_ref,
        completion_behavior_id=behavior.id,
        lineage_id=lineage_id,
        root_source_kind="spec",
        root_source_id=f"root-source-{target_id}",
        closure_root_work_item_id=f"root-spec-{target_id}",
        request_kind=behavior.request_kind,
        target_graph_node_id=behavior.target_graph_node_id,
        evidence_window={"kind": "lineage", "lineage_id": lineage_id},
        status=status,
        opened_by_input_id=f"open-{target_id}",
        closed_by_record_id=closed_by_record_id,
    )


def _root_inventory_work_item(
    target: ClosureTargetRecord,
) -> WorkItem:
    assert target.closure_root_work_item_id is not None
    return WorkItem(
        ref=WorkItemRef(
            work_item_id=target.closure_root_work_item_id,
            plan_ref=target.selected_plan_ref,
            generation=0,
        ),
        queue_family_id=QueueFamilyId(target.root_source_kind),
        payload={
            "title": f"Inventory for {target.closure_target_id}",
            "body": "Root source inventory record.",
            "root_source": {
                "kind": target.root_source_kind,
                "source_id": target.root_source_id,
            },
        },
        lineage_id=target.lineage_id,
        created_by_input_id=f"enqueue-{target.closure_root_work_item_id}",
    )


def _store_payload(cas_root: Path, payload: Mapping[str, AuthorityValue]) -> str:
    return ContentAddressedByteStore(cas_root).put_bytes(
        dumps_cas_object(encode_payload(payload))
    )


def _arbiter_source_records(
    *,
    plan: SelectedCompiledPlan,
    plan_ref: PlanRef,
    behavior: CompletionBehaviorDeclaration,
    closure_target_id: str,
    suffix: str,
    source_action_id: ActionId,
    artifact: tuple[str, Mapping[str, AuthorityValue]] | None,
) -> tuple[
    WorkItem,
    Activation,
    RunRecord,
    ClosureEvaluationRecord,
    RunnerObservationRecord,
    ArtifactRecord | None,
    TransitionRecord,
]:
    lineage_id = f"lineage-{suffix}"
    work_item_id = f"work-arbiter-{suffix}"
    activation_id = f"activation-arbiter-{suffix}"
    run_id = f"run-arbiter-{suffix}"
    input_id = f"observe-arbiter-{suffix}"
    work_item = WorkItem(
        ref=WorkItemRef(work_item_id=work_item_id, plan_ref=plan_ref, generation=0),
        queue_family_id=behavior.request_queue_family_id,
        payload={
            "request_kind": behavior.request_kind,
            "closure_target_id": closure_target_id,
            "graph_node_id": behavior.target_graph_node_id,
        },
        lineage_id=lineage_id,
        created_by_input_id=f"evaluate-{suffix}",
    )
    activation = Activation(
        activation_id=activation_id,
        work_item_id=work_item_id,
        lineage_id=lineage_id,
        plan_ref=plan_ref,
        queue_family_id=behavior.request_queue_family_id,
        graph_node_id=behavior.target_graph_node_id,
        stage_kind_id=behavior.target_stage_kind_id,
        runner_binding_id=behavior.runner_binding_id,
        generation=1,
        created_by_input_id=f"evaluate-{suffix}",
        claimed_by_run_id=run_id,
    )
    run = RunRecord(
        run_ref=RunRef(
            run_id=run_id,
            work_item_id=work_item_id,
            claim_id=f"claim-{suffix}",
            plan_ref=plan_ref,
            generation=0,
            fencing_token=f"fence-{suffix}",
        ),
        work_item_id=work_item_id,
        activation_id=activation_id,
        stage_kind_id=behavior.target_stage_kind_id,
        runner_binding_id=behavior.runner_binding_id,
        created_by_input_id=f"claim-{suffix}",
    )
    arbiter = ClosureEvaluationRecord(
        record_id=f"closure-evaluator:{activation_id}",
        closure_target_id=closure_target_id,
        completion_behavior_id=behavior.id,
        request_kind=behavior.request_kind,
        target_work_item_id=work_item_id,
        target_activation_id=activation_id,
        selected_plan_ref=plan_ref,
        lineage_id=lineage_id,
        created_by_input_id=f"evaluate-{suffix}",
    )
    source_action = next(
        action for action in actions_by_id if action.id == source_action_id
    )
    if artifact is not None and source_action.artifact_schema_id is None:
        raise AssertionError("closure source action must declare an artifact schema")
    artifact_body = artifact[1] if artifact is not None else {}
    marker = next(
        outcome.marker
        for outcome in plan.terminal_outcomes
        if outcome.id == source_action.outcome_id
    )
    observation_payload = fake_runner_observation_payload(
        run=run,
        activation=activation,
        plan_fingerprint=plan_ref.authority_fingerprint,
        marker=marker,
        artifact_payload=artifact_body,
    )
    transition_id = f"transition-{input_id}"
    observation = RunnerObservationRecord(
        observation_id=f"{transition_id}:observation",
        run_id=run_id,
        payload=observation_payload,
        created_by_input_id=input_id,
        observed_at=None,
    )
    artifact_record = (
        ArtifactRecord(
            artifact_id=artifact[0],
            work_item_id=work_item_id,
            schema_id=source_action.artifact_schema_id,
            payload=artifact_body,
            created_by_input_id=input_id,
            source_run_id=run_id,
            source_action_id=source_action_id,
            source_stage_kind_id=behavior.target_stage_kind_id,
            source_graph_node_id=behavior.target_graph_node_id,
            payload_digest=artifact_payload_digest(artifact_body),
            transition_id=transition_id,
        )
        if artifact is not None and source_action.artifact_schema_id is not None
        else None
    )
    transition = TransitionRecord(
        record_id=transition_id,
        input_id=input_id,
        input_kind=RunnerResultObserved.input_kind,
        input_family="workflow_observation",
        accepted=True,
    )
    return work_item, activation, run, arbiter, observation, artifact_record, transition


actions_by_id: tuple[TerminalActionDeclaration, ...]


def _closure_state() -> RuntimeState:
    global actions_by_id
    plan, fingerprint = compile_lad_planning()
    plan_ref = _plan_ref(plan, fingerprint)
    behavior, policy, actions = _selected_authority(plan)
    actions_by_id = tuple(actions.values())
    complete_terminal_id = "closure-terminal:transition-observe-arbiter-complete"
    complete_target = _closure_target(
        target_id="closure-target-complete",
        plan_ref=plan_ref,
        behavior=behavior,
        lineage_id="lineage-complete",
        status="closed",
        closed_by_record_id=complete_terminal_id,
    )
    incident_target = _closure_target(
        target_id="closure-target-incident",
        plan_ref=plan_ref,
        behavior=behavior,
        lineage_id="lineage-incident",
    )
    blocked_target = _closure_target(
        target_id="closure-target-blocked",
        plan_ref=plan_ref,
        behavior=behavior,
        lineage_id="lineage-blocked",
    )
    complete = _arbiter_source_records(
        plan=plan,
        plan_ref=plan_ref,
        behavior=behavior,
        closure_target_id=complete_target.closure_target_id,
        suffix="complete",
        source_action_id=behavior.pass_action_id,
        artifact=(
            "transition-observe-arbiter-complete:artifact",
            artifact_payload(
                "planning.artifacts.verdict",
                summary="closure pass verdict",
            ),
        ),
    )
    incident = _arbiter_source_records(
        plan=plan,
        plan_ref=plan_ref,
        behavior=behavior,
        closure_target_id=incident_target.closure_target_id,
        suffix="incident",
        source_action_id=behavior.gap_action_id,
        artifact=(
            "transition-observe-arbiter-incident:artifact",
            artifact_payload("planning.artifacts.incident_report"),
        ),
    )
    blocked = _arbiter_source_records(
        plan=plan,
        plan_ref=plan_ref,
        behavior=behavior,
        closure_target_id=blocked_target.closure_target_id,
        suffix="blocked",
        source_action_id=behavior.blocked_action_id,
        artifact=None,
    )
    incident_record_id = "remediation-record:transition-observe-arbiter-incident"
    remediation_work = WorkItem(
        ref=WorkItemRef(
            work_item_id="work-remediation-target",
            plan_ref=plan_ref,
            generation=0,
        ),
        queue_family_id=policy.target_queue_family_id,
        payload={
            "root_source": {
                "kind": policy.root_source_kind,
                "source_id": incident_record_id,
            }
        },
        lineage_id=incident_target.lineage_id,
        created_by_input_id="observe-arbiter-incident",
    )
    remediation_activation = Activation(
        activation_id="activation-remediation-target",
        work_item_id=remediation_work.ref.work_item_id,
        lineage_id=incident_target.lineage_id,
        plan_ref=plan_ref,
        queue_family_id=policy.target_queue_family_id,
        graph_node_id=policy.target_graph_node_id,
        stage_kind_id=policy.target_stage_kind_id,
        runner_binding_id=policy.target_runner_binding_id,
        generation=0,
        created_by_input_id="observe-arbiter-incident",
    )
    terminal = ClosureTerminalRecord(
        record_id=complete_terminal_id,
        closure_target_id=complete_target.closure_target_id,
        completion_behavior_id=behavior.id,
        terminal_kind="passed",
        source_run_id=complete[2].run_ref.run_id,
        source_action_id=behavior.pass_action_id,
        source_artifact_id="transition-observe-arbiter-complete:artifact",
        selected_plan_ref=plan_ref,
        lineage_id=complete_target.lineage_id,
        created_by_input_id="observe-arbiter-complete",
    )
    remediation = RemediationWorkRecord(
        record_id=incident_record_id,
        remediation_policy_id=policy.id,
        closure_target_id=incident_target.closure_target_id,
        source_run_id=incident[2].run_ref.run_id,
        source_action_id=behavior.gap_action_id,
        source_artifact_id="transition-observe-arbiter-incident:artifact",
        target_work_item_id=remediation_work.ref.work_item_id,
        target_activation_id=remediation_activation.activation_id,
        selected_plan_ref=plan_ref,
        lineage_id=incident_target.lineage_id,
        dedupe_key=f"{incident_target.closure_target_id}:transition-observe-arbiter-incident:artifact",
        created_by_input_id="observe-arbiter-incident",
    )
    blocked_record = ClosureBlockedRecord(
        record_id="closure-blocked:transition-observe-arbiter-blocked",
        closure_target_id=blocked_target.closure_target_id,
        completion_behavior_id=behavior.id,
        source_run_id=blocked[2].run_ref.run_id,
        source_action_id=behavior.blocked_action_id,
        selected_plan_ref=plan_ref,
        lineage_id=blocked_target.lineage_id,
        operator_required=True,
        created_by_input_id="observe-arbiter-blocked",
    )
    root_work_items = tuple(
        _root_inventory_work_item(target)
        for target in (complete_target, incident_target, blocked_target)
    )
    source_work_items = (
        *root_work_items,
        complete[0],
        incident[0],
        blocked[0],
        remediation_work,
    )
    source_activations = (
        complete[1],
        incident[1],
        blocked[1],
        remediation_activation,
    )
    source_runs = (complete[2], incident[2], blocked[2])
    observations = (complete[4], incident[4], blocked[4])
    artifacts = tuple(
        item for item in (complete[5], incident[5], blocked[5]) if item is not None
    )
    transitions = (complete[6], incident[6], blocked[6])
    observation_sources = (
        (complete, behavior.pass_action_id),
        (incident, behavior.gap_action_id),
        (blocked, behavior.blocked_action_id),
    )
    receipts: dict[str, InputReceipt] = {}
    governance_events: list[GovernanceEventRecord] = []
    traces: list[TraceRecord] = []
    for source, action_id in observation_sources:
        run = source[2]
        observation = source[4]
        transition = source[6]
        accepted_input = RunnerResultObserved(
            observation.created_by_input_id,
            run_id=observation.run_id,
            payload=observation.payload,
            observed_at=observation.observed_at,
        )
        receipts[observation.created_by_input_id] = InputReceipt(
            receipt_ref=InputReceiptRef(
                input_id=observation.created_by_input_id,
                input_payload_digest=input_payload_digest(accepted_input),
            ),
            transition_id=transition.record_id,
        )
        audit_fields = {
            "record_id": f"{transition.record_id}:governance",
            "input_id": observation.created_by_input_id,
            "input_kind": RunnerResultObserved.input_kind,
            "input_family": "workflow_observation",
            "disposition": "accepted",
            "plan_fingerprint": plan_ref.authority_fingerprint,
            "work_item_id": run.work_item_id,
            "run_id": run.run_ref.run_id,
            "action_id": action_id,
            "authority_source": "terminal_action",
        }
        governance_events.append(GovernanceEventRecord(**audit_fields))
        traces.append(
            TraceRecord(
                **{
                    **audit_fields,
                    "record_id": f"{transition.record_id}:trace",
                }
            )
        )
    return RuntimeState(
        admitted_plans={
            fingerprint: AdmittedPlan(plan_ref=plan_ref, selected_plan=plan)
        },
        default_plan_ref=plan_ref,
        receipts=receipts,
        work_items={item.ref.work_item_id: item for item in source_work_items},
        activations={item.activation_id: item for item in source_activations},
        runs={item.run_ref.run_id: item for item in source_runs},
        runner_observations={item.observation_id: item for item in observations},
        artifacts={item.artifact_id: item for item in artifacts},
        closure_targets={
            complete_target.closure_target_id: complete_target,
            incident_target.closure_target_id: incident_target,
            blocked_target.closure_target_id: blocked_target,
        },
        closure_evaluations={
            item.record_id: item for item in (complete[3], incident[3], blocked[3])
        },
        closure_terminal_records={terminal.record_id: terminal},
        remediation_work_records={remediation.record_id: remediation},
        closure_blocked_records={blocked_record.record_id: blocked_record},
        governance_events=tuple(governance_events),
        traces=tuple(traces),
        transitions=transitions,
    )


def test_closure_records_survive_restart(tmp_path: Path) -> None:
    state = _closure_state()
    db_path, cas_root = runtime_store_paths(tmp_path)

    persist_runtime_state(db_path, cas_root, state)
    loaded = load_runtime_state(db_path, cas_root)

    assert loaded.closure_targets == state.closure_targets
    assert loaded.closure_evaluations == state.closure_evaluations
    assert loaded.closure_terminal_records == state.closure_terminal_records
    assert loaded.remediation_work_records == state.remediation_work_records
    assert loaded.closure_blocked_records == state.closure_blocked_records


def test_restart_refuses_corrupt_closure_target_authority_link(
    tmp_path: Path,
) -> None:
    state = _closure_state()
    db_path, cas_root = runtime_store_paths(tmp_path)
    persist_runtime_state(db_path, cas_root, state)

    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            UPDATE closure_targets
            SET target_graph_node_id = ?
            WHERE closure_target_id = ?
            """,
            ("wrong-node", "closure-target-complete"),
        )

    with pytest.raises(StorageIntegrityError, match="target_graph_node_id"):
        load_runtime_state(db_path, cas_root)


def test_restart_refuses_missing_closure_root_inventory(tmp_path: Path) -> None:
    state = _closure_state()
    db_path, cas_root = runtime_store_paths(tmp_path)
    persist_runtime_state(db_path, cas_root, state)

    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            UPDATE closure_targets
            SET root_source_id = ?
            WHERE closure_target_id = ?
            """,
            ("missing-root-source", "closure-target-complete"),
        )

    with pytest.raises(StorageIntegrityError, match="root source"):
        load_runtime_state(db_path, cas_root)


def test_restart_refuses_closure_root_lineage_drift(tmp_path: Path) -> None:
    state = _closure_state()
    db_path, cas_root = runtime_store_paths(tmp_path)
    persist_runtime_state(db_path, cas_root, state)

    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            UPDATE work_items
            SET lineage_id = ?
            WHERE work_item_id = ?
            """,
            ("different-lineage", "root-spec-closure-target-complete"),
        )

    with pytest.raises(StorageIntegrityError, match="lineage"):
        load_runtime_state(db_path, cas_root)


def test_restart_refuses_closure_root_work_item_id_drift(tmp_path: Path) -> None:
    state = _closure_state()
    db_path, cas_root = runtime_store_paths(tmp_path)
    persist_runtime_state(db_path, cas_root, state)

    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            UPDATE closure_targets
            SET closure_root_work_item_id = ?
            WHERE closure_target_id = ?
            """,
            ("wrong-root-work-item", "closure-target-complete"),
        )

    with pytest.raises(StorageIntegrityError, match="closure_root_work_item_id"):
        load_runtime_state(db_path, cas_root)


def test_restart_refuses_non_manual_missing_closure_root_work_item_id(
    tmp_path: Path,
) -> None:
    state = _closure_state()
    db_path, cas_root = runtime_store_paths(tmp_path)
    persist_runtime_state(db_path, cas_root, state)

    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            UPDATE closure_targets
            SET closure_root_work_item_id = NULL
            WHERE closure_target_id = ?
            """,
            ("closure-target-complete",),
        )

    with pytest.raises(StorageIntegrityError, match="closure_root_work_item_id"):
        load_runtime_state(db_path, cas_root)


def test_restart_refuses_closure_root_queue_family_drift(tmp_path: Path) -> None:
    state = _closure_state()
    db_path, cas_root = runtime_store_paths(tmp_path)
    persist_runtime_state(db_path, cas_root, state)

    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            UPDATE work_items
            SET queue_family_id = ?
            WHERE work_item_id = ?
            """,
            ("stage_result", "root-spec-closure-target-complete"),
        )
        connection.execute(
            """
            UPDATE activations
            SET queue_family_id = ?
            WHERE work_item_id = ?
            """,
            ("stage_result", "root-spec-closure-target-complete"),
        )

    with pytest.raises(StorageIntegrityError, match="root source"):
        load_runtime_state(db_path, cas_root)


def test_restart_refuses_closure_root_plan_ref_drift(tmp_path: Path) -> None:
    state = _closure_state()
    db_path, cas_root = runtime_store_paths(tmp_path)
    persist_runtime_state(db_path, cas_root, state)

    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            UPDATE work_items
            SET plan_authority_fingerprint = ?
            WHERE work_item_id = ?
            """,
            ("sha256:drifted-root-plan", "root-spec-closure-target-complete"),
        )

    with pytest.raises(StorageIntegrityError, match="work_items"):
        load_runtime_state(db_path, cas_root)


def test_restart_refuses_closure_root_payload_source_kind_drift(
    tmp_path: Path,
) -> None:
    state = _closure_state()
    db_path, cas_root = runtime_store_paths(tmp_path)
    persist_runtime_state(db_path, cas_root, state)
    drifted_payload_digest = _store_payload(
        cas_root,
        {
            "title": "Drifted inventory",
            "body": "Root source inventory with wrong kind.",
            "root_source": {
                "kind": "idea",
                "source_id": "root-source-closure-target-complete",
            },
        },
    )

    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            UPDATE work_items
            SET payload_digest = ?
            WHERE work_item_id = ?
            """,
            (drifted_payload_digest, "root-spec-closure-target-complete"),
        )

    with pytest.raises(StorageIntegrityError, match="root source"):
        load_runtime_state(db_path, cas_root)


def test_restart_refuses_manual_closure_root_work_item_id(tmp_path: Path) -> None:
    state = _closure_state()
    base_target = next(iter(state.closure_targets.values()))
    manual_target = replace(
        base_target,
        closure_target_id="closure-target-manual",
        lineage_id="manual-lineage-1",
        root_source_kind="manual",
        root_source_id="manual-source-1",
        closure_root_work_item_id=None,
        evidence_window={"kind": "lineage", "lineage_id": "manual-lineage-1"},
        status="open",
        opened_by_input_id="open-manual-root",
        closed_by_record_id=None,
    )
    legal = replace(
        state,
        closure_targets={
            **state.closure_targets,
            manual_target.closure_target_id: manual_target,
        },
    )
    db_path, cas_root = runtime_store_paths(tmp_path)
    persist_runtime_state(db_path, cas_root, legal)

    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            UPDATE closure_targets
            SET closure_root_work_item_id = ?
            WHERE closure_target_id = ?
            """,
            ("fake-root-work-item", "closure-target-manual"),
        )

    with pytest.raises(StorageIntegrityError, match="manual root source"):
        load_runtime_state(db_path, cas_root)


def test_restart_refuses_closure_terminal_without_matching_evaluator(
    tmp_path: Path,
) -> None:
    state = _closure_state()
    db_path, cas_root = runtime_store_paths(tmp_path)
    persist_runtime_state(db_path, cas_root, state)

    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            UPDATE closure_terminal_records
            SET source_run_id = ?, source_artifact_id = NULL
            WHERE record_id = ?
            """,
            (
                "run-arbiter-incident",
                "closure-terminal:transition-observe-arbiter-complete",
            ),
        )

    with pytest.raises(StorageIntegrityError, match="closure evaluator activation"):
        load_runtime_state(db_path, cas_root)


def test_restart_refuses_remediation_missing_required_source_artifact(
    tmp_path: Path,
) -> None:
    state = _closure_state()
    db_path, cas_root = runtime_store_paths(tmp_path)
    persist_runtime_state(db_path, cas_root, state)

    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            UPDATE remediation_work_records
            SET source_artifact_id = NULL
            WHERE record_id = ?
            """,
            ("remediation-record:transition-observe-arbiter-incident",),
        )

    with pytest.raises(StorageIntegrityError, match="source_artifact_id"):
        load_runtime_state(db_path, cas_root)


def test_restart_refuses_remediation_wrong_dedupe_key(tmp_path: Path) -> None:
    state = _closure_state()
    db_path, cas_root = runtime_store_paths(tmp_path)
    persist_runtime_state(db_path, cas_root, state)

    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            UPDATE remediation_work_records
            SET dedupe_key = ?
            WHERE record_id = ?
            """,
            (
                "wrong-dedupe-key",
                "remediation-record:transition-observe-arbiter-incident",
            ),
        )

    with pytest.raises(StorageIntegrityError, match="dedupe_key"):
        load_runtime_state(db_path, cas_root)


def test_persist_refuses_duplicate_remediation_dedupe_key(tmp_path: Path) -> None:
    state = _closure_state()
    remediation = next(iter(state.remediation_work_records.values()))
    duplicate = replace(
        remediation,
        record_id="remediation-record:duplicate",
        created_by_input_id="observe-arbiter-incident-duplicate",
    )
    duplicated = replace(
        state,
        remediation_work_records={
            remediation.record_id: remediation,
            duplicate.record_id: duplicate,
        },
    )
    db_path, cas_root = runtime_store_paths(tmp_path)

    with pytest.raises(StorageIntegrityError, match="dedupe_key"):
        persist_runtime_state(db_path, cas_root, duplicated)
