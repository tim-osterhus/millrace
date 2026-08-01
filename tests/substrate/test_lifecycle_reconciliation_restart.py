from __future__ import annotations

import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from millrace.contracts import OperatorReviseWait, QueueFamilyId
from millrace.contracts.transition import JoinFromArtifact, ReconcileEffect
from millrace.kernel import apply
from millrace.kernel.lifecycle import project_next_lifecycle_transition
from millrace.operator import operator_status
from millrace.operator.dispatch import (
    build_dispatch_envelope_for_run as _build_dispatch_envelope_for_run,
)
from millrace.substrate.cas import ContentAddressedByteStore
from millrace.substrate.codecs import dumps_cas_object, encode_payload
from millrace.substrate.errors import StorageIntegrityError
from millrace.testing import (
    decide_with_fake_runner_completion as decide,
)
from millrace.testing import (
    deterministic_context,
    fake_runner_completion_input_id,
    fake_runner_session_state,
)
from substrate._runtime_store_support import (
    load_runtime_state,
    persist_and_load_runtime_state,
    persist_runtime_state,
    runtime_store_paths,
)
from support import generic_effect, generic_lifecycle, generic_operator_wait

_ARTIFACT_PAYLOAD_AUTHORITY_ERROR = (
    "artifacts runner-observation provenance invalid: artifact_payload_authority"
)
_ARTIFACT_SOURCE_AUTHORITY_ERROR = (
    "artifacts runner-observation provenance invalid: artifact_source_authority"
)
_RECONCILIATION_TRANSITION_AUTHORITY_ERROR = (
    "effect_reconciliations.created_transition_id must reference accepted effect "
    "reconciliation transition"
)


def _build_dispatch_envelope(*, state, run_id):
    return _build_dispatch_envelope_for_run(
        state=fake_runner_session_state(state=state, run_id=run_id),
        run_id=run_id,
    )


@pytest.mark.parametrize("resolution_kind", ("active", "resume", "close", "revise"))
def test_restart_preserves_generic_operator_wait_records(
    tmp_path: Path,
    resolution_kind: str,
) -> None:
    state, wait = generic_operator_wait.wait_state(resolution_kind)

    loaded = persist_and_load_runtime_state(tmp_path, state)

    assert loaded == state
    loaded_wait = loaded.operator_waits[wait.wait_id]
    assert loaded_wait.operator_wait_id == wait.operator_wait_id
    assert loaded_wait.source_action_id == wait.source_action_id
    assert loaded_wait.source_work_item_id == wait.source_work_item_id
    assert loaded_wait.source_activation_id == wait.source_activation_id
    assert loaded_wait.source_run_id == wait.source_run_id
    assert loaded_wait.source_graph_node_id == wait.source_graph_node_id
    assert loaded_wait.source_stage_kind_id == wait.source_stage_kind_id
    assert loaded_wait.source_runner_binding_id == wait.source_runner_binding_id
    assert loaded_wait.source_queue_family_id == wait.source_queue_family_id
    assert loaded_wait.source_artifact_id == wait.source_artifact_id
    assert (
        loaded_wait.selected_plan_fingerprint
        == wait.selected_plan_ref.authority_fingerprint
    )


def test_restart_preserves_refused_generic_operator_wait_decision(
    tmp_path: Path,
) -> None:
    state, wait = generic_operator_wait.wait_state("active")
    transition_input = OperatorReviseWait(
        "operator-revise-generic-refused-restart",
        selected_plan_ref=wait.selected_plan_ref,
        wait_id=wait.wait_id,
        lineage_id=wait.lineage_id,
        actor_id="local_operator",
        actor_kind="local_operator",
        payload={"task_id": "missing-title"},
    )
    decision = decide(
        state,
        transition_input,
        deterministic_context(
            transition_id=f"transition-{transition_input.input_id}",
            work_item_id="work-refused-generic-revision",
            activation_id="activation-refused-generic-revision",
        ),
    )
    refused = apply(state, decision)

    loaded = persist_and_load_runtime_state(tmp_path, refused)

    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == "invalid_operator_wait_payload_schema"
    assert loaded == refused
    assert loaded.receipts[transition_input.input_id].accepted is False
    assert loaded.refusals[-1].input_id == transition_input.input_id
    assert loaded.refusals[-1].reason == "invalid_operator_wait_payload_schema"
    assert loaded.operator_waits[wait.wait_id].status == "active"
    assert "work-refused-generic-revision" not in loaded.work_items
    assert "activation-refused-generic-revision" not in loaded.activations


@pytest.mark.parametrize(
    ("column", "value", "match"),
    (
        ("status", "stale", "operator_waits.status is unsupported"),
        ("actor_kind", "runtime", "operator_waits.actor_kind must match selected"),
        (
            "resolution_kind",
            "delegate_to_fixture",
            "operator_waits.resolution_kind must be selected",
        ),
    ),
)
def test_restart_refuses_generic_operator_wait_status_drift(
    tmp_path: Path,
    column: str,
    value: str,
    match: str,
) -> None:
    state, wait = generic_operator_wait.wait_state("resume")
    db_path, cas_root = runtime_store_paths(tmp_path)
    persist_runtime_state(db_path, cas_root, state)
    with sqlite3.connect(db_path) as connection:
        if column == "status":
            connection.execute("PRAGMA ignore_check_constraints = ON")
        connection.execute(
            f"UPDATE operator_waits SET {column} = ? WHERE wait_id = ?",
            (value, wait.wait_id),
        )
    with pytest.raises(StorageIntegrityError, match=match):
        load_runtime_state(db_path, cas_root)


@pytest.mark.parametrize(
    ("column", "value", "match"),
    (
        (
            "source_work_item_id",
            "missing-work",
            "operator_waits.source_work_item_id must reference work_items",
        ),
        (
            "source_activation_id",
            "missing-activation",
            "operator_waits.source_activation_id must reference activations",
        ),
        (
            "source_run_id",
            "missing-run",
            "operator_waits.source_run_id must reference runs",
        ),
        (
            "source_graph_node_id",
            "kernel_ping.worker.start",
            "operator_waits source context fields must match recorded source",
        ),
        (
            "source_stage_kind_id",
            "kernel_ping.worker",
            "operator_waits source context fields must match recorded source",
        ),
        (
            "source_queue_family_id",
            "worker_task",
            "operator_waits source context fields must match recorded source",
        ),
        (
            "source_runner_binding_id",
            "missing.runner",
            "operator_waits source context fields must match recorded source",
        ),
        (
            "source_action_id",
            generic_operator_wait.CLOSE_ACTION_ID,
            "operator_waits.source_action_id must reference wait source actions",
        ),
    ),
)
def test_restart_refuses_generic_operator_wait_source_context_drift(
    tmp_path: Path,
    column: str,
    value: str,
    match: str,
) -> None:
    state, wait = generic_operator_wait.wait_state("active")
    db_path, cas_root = runtime_store_paths(tmp_path)
    persist_runtime_state(db_path, cas_root, state)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            f"UPDATE operator_waits SET {column} = ? WHERE wait_id = ?",
            (value, wait.wait_id),
        )
    with pytest.raises(StorageIntegrityError, match=match):
        load_runtime_state(db_path, cas_root)


def test_restart_preserves_generic_effect_proposal_and_reconciliation(
    tmp_path: Path,
) -> None:
    state = generic_effect.runtime_state(reconciliation_status="applied")

    loaded = persist_and_load_runtime_state(tmp_path, state)

    assert loaded == state
    assert len(loaded.effect_proposals) == 1
    assert len(loaded.effect_reconciliations) == 1


def test_restart_reconciles_generic_effect_after_loading_pending_proposal(
    tmp_path: Path,
) -> None:
    loaded = persist_and_load_runtime_state(tmp_path, generic_effect.runtime_state())
    effect_id = next(iter(loaded.effect_proposals))
    transition_input = ReconcileEffect(
        "reconcile-loaded-generic-effect",
        effect_id=effect_id,
        provider_ref="provider.fake_local.workspace",
        status="applied",
        result={
            "provider_result_id": "result-loaded",
            "summary": "Recorded after restart.",
        },
    )
    decision = decide(
        loaded, transition_input, generic_effect.context(transition_input.input_id)
    )
    assert decision.accepted is True
    assert len(apply(loaded, decision).effect_reconciliations) == 1


def test_restart_replays_generic_effect_reconciliation_after_load(
    tmp_path: Path,
) -> None:
    loaded = persist_and_load_runtime_state(
        tmp_path,
        generic_effect.runtime_state(reconciliation_status="applied"),
    )
    effect = next(iter(loaded.effect_proposals.values()))
    transition_input = ReconcileEffect(
        "reconcile-effect-applied",
        effect_id=effect.effect_id,
        provider_ref=effect.provider_ref,
        status="applied",
        result={
            "provider_result_id": "result-applied",
            "summary": "Recorded as local test evidence.",
        },
    )
    decision = decide(
        loaded, transition_input, generic_effect.context(transition_input.input_id)
    )
    assert decision.accepted is True
    assert (
        apply(loaded, decision).effect_reconciliations == loaded.effect_reconciliations
    )


def test_restart_preserves_generic_effect_status_source_rows(tmp_path: Path) -> None:
    state = generic_effect.runtime_state(reconciliation_status="applied")
    loaded = persist_and_load_runtime_state(tmp_path, state)
    status = operator_status(loaded)
    effect = next(iter(status.effects))
    proposal = next(iter(state.effect_proposals.values()))
    assert effect.selected_plan_fingerprint == proposal.selected_plan_fingerprint
    assert effect.status == "applied"
    assert effect.source_input_id == proposal.source_input_id
    assert effect.source_action_id == generic_effect.EFFECT_ACTION_ID
    assert effect.terminal_action_id == generic_effect.EFFECT_ACTION_ID
    assert effect.source_work_item_id == proposal.source_work_item_id
    assert effect.source_activation_id == proposal.source_activation_id
    assert effect.source_stage_kind_id == str(proposal.source_stage_kind_id)
    assert effect.source_queue_family_id == str(proposal.source_queue_family_id)
    assert (
        effect.reconciliation_id == "transition-reconcile-effect-applied:reconciliation"
    )
    assert any(
        artifact.artifact_id == effect.artifact_id
        and artifact.source_input_id == effect.source_input_id
        and artifact.terminal_action_id == effect.terminal_action_id
        for artifact in status.artifacts
    )


def test_restart_preserves_generic_effect_after_source_close(tmp_path: Path) -> None:
    state = generic_effect.runtime_state(reconciliation_status="applied")
    loaded = persist_and_load_runtime_state(tmp_path, state)
    effect = next(iter(loaded.effect_proposals.values()))
    reconciliation = next(iter(loaded.effect_reconciliations.values()))
    source_close = loaded.closed_work_items[effect.source_work_item_id]
    assert source_close.source_run_id == effect.source_run_id
    assert source_close.action_id == effect.source_action_id
    assert source_close.work_item_id == effect.lineage_id
    assert reconciliation.effect_id == effect.effect_id


@pytest.mark.parametrize(
    ("column", "value", "match"),
    (
        (
            "artifact_schema_id",
            "kernel_ping.task_incident",
            _ARTIFACT_PAYLOAD_AUTHORITY_ERROR,
        ),
        (
            "source_action_id",
            "kernel_ping.route_taskmaster_success",
            _ARTIFACT_PAYLOAD_AUTHORITY_ERROR,
        ),
        (
            "source_action_id",
            "kernel_ping.pause_taskmaster_blocked",
            _ARTIFACT_PAYLOAD_AUTHORITY_ERROR,
        ),
        (
            "source_stage_kind_id",
            "kernel_ping.worker",
            _ARTIFACT_SOURCE_AUTHORITY_ERROR,
        ),
        (
            "source_graph_node_id",
            "kernel_ping.worker.start",
            _ARTIFACT_SOURCE_AUTHORITY_ERROR,
        ),
        (
            "artifact_payload_digest",
            f"sha256:{'0' * 64}",
            _ARTIFACT_PAYLOAD_AUTHORITY_ERROR,
        ),
        (
            "created_by_input_id",
            "claim-taskmaster",
            _ARTIFACT_SOURCE_AUTHORITY_ERROR,
        ),
        (
            "transition_id",
            "transition-claim-taskmaster",
            _ARTIFACT_SOURCE_AUTHORITY_ERROR,
        ),
        (
            "source_run_id",
            "missing-run",
            _ARTIFACT_SOURCE_AUTHORITY_ERROR,
        ),
    ),
)
def test_restart_refuses_generic_artifact_provenance_drift(
    tmp_path: Path,
    column: str,
    value: str,
    match: str,
) -> None:
    state = generic_effect.runtime_state(reconciliation_status="applied")
    artifact_id = next(iter(state.artifacts))
    db_path, cas_root = runtime_store_paths(tmp_path)
    persist_runtime_state(db_path, cas_root, state)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            f"UPDATE artifacts SET {column} = ? WHERE artifact_id = ?",
            (value, artifact_id),
        )
    with pytest.raises(StorageIntegrityError, match=match):
        load_runtime_state(db_path, cas_root)


@pytest.mark.parametrize(
    ("column", "value", "match"),
    (
        (
            "artifact_schema_id",
            "kernel_ping.task_incident",
            "effect_proposals.artifact_schema_id must match artifact",
        ),
        (
            "selected_plan_fingerprint",
            f"sha256:{'0' * 64}",
            "effect_proposals.selected_plan_fingerprint must match PlanRef",
        ),
        (
            "source_run_id",
            "missing-run",
            "effect_proposals.source_run_id must reference runs",
        ),
        (
            "provider_ref",
            "provider.real.workspace",
            "effect_proposals.effect_declaration_id must reference selected effect",
        ),
        (
            "source_input_id",
            "claim-taskmaster",
            "effect_proposals.source_input_id must match created_input_id",
        ),
        (
            "target_skill_id",
            "wrong.skill",
            "effect_proposals.target_skill_id must match artifact payload",
        ),
    ),
)
def test_restart_refuses_generic_effect_record_drift(
    tmp_path: Path,
    column: str,
    value: str,
    match: str,
) -> None:
    state = generic_effect.runtime_state(reconciliation_status="applied")
    effect_id = next(iter(state.effect_proposals))
    db_path, cas_root = runtime_store_paths(tmp_path)
    persist_runtime_state(db_path, cas_root, state)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            f"UPDATE effect_proposals SET {column} = ? WHERE effect_id = ?",
            (value, effect_id),
        )
    with pytest.raises(StorageIntegrityError, match=match):
        load_runtime_state(db_path, cas_root)


def test_restart_refuses_generic_effect_correlated_source_action_drift(
    tmp_path: Path,
) -> None:
    state = generic_effect.runtime_state(reconciliation_status="applied")
    effect = next(iter(state.effect_proposals.values()))
    db_path, cas_root = runtime_store_paths(tmp_path)
    persist_runtime_state(db_path, cas_root, state)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE artifacts SET source_action_id = ? WHERE artifact_id = ?",
            ("kernel_ping.route_taskmaster_success", effect.artifact_id),
        )
        connection.execute(
            "UPDATE effect_proposals SET source_action_id = ? WHERE effect_id = ?",
            ("kernel_ping.route_taskmaster_success", effect.effect_id),
        )
    with pytest.raises(
        StorageIntegrityError,
        match=_ARTIFACT_PAYLOAD_AUTHORITY_ERROR,
    ):
        load_runtime_state(db_path, cas_root)


def test_restart_refuses_generic_effect_target_path_ref_drift(tmp_path: Path) -> None:
    state = generic_effect.runtime_state(reconciliation_status="applied")
    effect_id = next(iter(state.effect_proposals))
    db_path, cas_root = runtime_store_paths(tmp_path)
    persist_runtime_state(db_path, cas_root, state)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE effect_proposals SET target_path_ref = ? WHERE effect_id = ?",
            ("records/wrong.json", effect_id),
        )
    with pytest.raises(
        StorageIntegrityError,
        match="effect_proposals.target_path_ref must match artifact payload",
    ):
        load_runtime_state(db_path, cas_root)


def test_restart_refuses_generic_effect_reconciliation_status_drift(
    tmp_path: Path,
) -> None:
    state = generic_effect.runtime_state(reconciliation_status="applied")
    reconciliation_id = next(iter(state.effect_reconciliations))
    db_path, cas_root = runtime_store_paths(tmp_path)
    persist_runtime_state(db_path, cas_root, state)
    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA ignore_check_constraints = ON")
        connection.execute(
            "UPDATE effect_reconciliations SET status = ? WHERE reconciliation_id = ?",
            ("stale", reconciliation_id),
        )
    with pytest.raises(StorageIntegrityError, match="effect_reconciliations.status"):
        load_runtime_state(db_path, cas_root)


@pytest.mark.parametrize(
    ("column", "value", "match"),
    (
        (
            "created_input_id",
            "observe-effect-ready",
            "effect_reconciliations.created_input_id must match transition",
        ),
        (
            "created_transition_id",
            "transition-observe-effect-ready",
            _RECONCILIATION_TRANSITION_AUTHORITY_ERROR,
        ),
        (
            "fake_local_result_digest",
            "not-a-digest",
            "effect_reconciliations.fake_local_result_digest must be sha256 digest",
        ),
    ),
)
def test_restart_refuses_generic_effect_reconciliation_authority_drift(
    tmp_path: Path,
    column: str,
    value: str,
    match: str,
) -> None:
    state = generic_effect.runtime_state(reconciliation_status="applied")
    reconciliation_id = next(iter(state.effect_reconciliations))
    db_path, cas_root = runtime_store_paths(tmp_path)
    persist_runtime_state(db_path, cas_root, state)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            f"UPDATE effect_reconciliations SET {column} = ? "
            "WHERE reconciliation_id = ?",
            (value, reconciliation_id),
        )
        if column == "created_transition_id":
            proposal = next(iter(state.effect_proposals.values()))
            connection.execute(
                "UPDATE effect_reconciliations SET created_input_id = ? "
                "WHERE reconciliation_id = ?",
                (proposal.source_input_id, reconciliation_id),
            )
    with pytest.raises(StorageIntegrityError, match=match):
        load_runtime_state(db_path, cas_root)


def test_restart_refuses_generic_effect_missing_proposal_link(tmp_path: Path) -> None:
    state = generic_effect.runtime_state(reconciliation_status="applied")
    reconciliation_id = next(iter(state.effect_reconciliations))
    db_path, cas_root = runtime_store_paths(tmp_path)
    persist_runtime_state(db_path, cas_root, state)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "UPDATE effect_reconciliations SET effect_id = ? "
            "WHERE reconciliation_id = ?",
            ("missing-effect", reconciliation_id),
        )
    with pytest.raises(
        StorageIntegrityError,
        match="effect_reconciliations.effect_id must reference effect_proposals",
    ):
        load_runtime_state(db_path, cas_root)


def _with_source_context_drift(state, field: str):
    artifact_id = generic_lifecycle.source_artifact_id()
    artifact = state.artifacts[artifact_id]
    run = state.runs[artifact.source_run_id]
    activation = state.activations[run.activation_id]
    if field in {"artifact_stage", "artifact_graph"}:
        drifted_artifact = replace(
            artifact,
            source_stage_kind_id=(
                "wrong.stage"
                if field == "artifact_stage"
                else artifact.source_stage_kind_id
            ),
            source_graph_node_id=(
                "wrong.node"
                if field == "artifact_graph"
                else artifact.source_graph_node_id
            ),
        )
        return replace(
            state,
            artifacts={**state.artifacts, artifact_id: drifted_artifact},
        )
    if field in {"run_ref_work", "run_ref_generation"}:
        drifted_run = replace(
            run,
            run_ref=replace(
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
            ),
        )
        return replace(
            state,
            runs={**state.runs, run.run_ref.run_id: drifted_run},
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
def test_restart_refuses_source_context_authority_drift(
    tmp_path: Path,
    field: str,
) -> None:
    state, _plan, _fingerprint = generic_lifecycle.origin_closed_state()
    db_path, cas_root = runtime_store_paths(tmp_path)

    with pytest.raises(StorageIntegrityError):
        persist_runtime_state(
            db_path,
            cas_root,
            _with_source_context_drift(state, field),
        )


def test_restart_resumes_between_two_selected_fanouts_without_duplicates(
    tmp_path: Path,
) -> None:
    state, _plan, _fingerprint = generic_lifecycle.origin_closed_state()
    first = project_next_lifecycle_transition(state).candidate
    assert first is not None
    after_first = generic_lifecycle.apply_candidate(state, first)
    first_path = tmp_path / "first"
    first_path.mkdir()
    loaded_first = persist_and_load_runtime_state(first_path, after_first)

    second = project_next_lifecycle_transition(loaded_first).candidate
    assert second is not None
    after_second = generic_lifecycle.apply_candidate(loaded_first, second)
    second_path = tmp_path / "second"
    second_path.mkdir()
    loaded_second = persist_and_load_runtime_state(second_path, after_second)

    assert second.declaration_id == generic_lifecycle.FANOUT_BETA_ID
    assert project_next_lifecycle_transition(loaded_second).candidate is None
    assert len(loaded_second.fanout_records) == 4
    assert len(loaded_second.work_dependencies) == 4


def test_restart_preserves_optional_omission_fanout_payload(tmp_path: Path) -> None:
    state, _plan, _fingerprint = (
        generic_lifecycle.accepted_terminal_optional_omission_fanout_state()
    )

    loaded = persist_and_load_runtime_state(tmp_path, state)
    generated_payloads = tuple(
        item.payload
        for item in loaded.work_items.values()
        if item.created_by_input_id == fake_runner_completion_input_id("observe-origin")
    )
    projection = project_next_lifecycle_transition(loaded)

    assert len(generated_payloads) == 4
    assert all("note" not in payload for payload in generated_payloads)
    assert projection.candidate is None
    assert projection.diagnostics == ()


def test_restart_preserves_absent_optional_fanout_collection_noop(
    tmp_path: Path,
) -> None:
    state, _plan, _fingerprint = (
        generic_lifecycle.accepted_terminal_optional_collection_omission_state()
    )

    loaded = persist_and_load_runtime_state(tmp_path, state)
    projection = project_next_lifecycle_transition(loaded)

    assert loaded.fanout_records == {}
    assert projection.candidate is None
    assert projection.diagnostics == ()


def test_restart_does_not_duplicate_selected_join_target(tmp_path: Path) -> None:
    state, _plan, _fingerprint = generic_lifecycle.two_report_state()
    candidate = project_next_lifecycle_transition(state).candidate
    assert candidate is not None
    joined = generic_lifecycle.apply_candidate(state, candidate)

    loaded = persist_and_load_runtime_state(tmp_path, joined)
    projection = project_next_lifecycle_transition(loaded)

    assert projection.candidate is None
    assert projection.diagnostics == ()
    assert candidate.transition_context.work_item_id in loaded.work_items
    assert candidate.transition_context.activation_id in loaded.activations
    assert [
        route
        for route in loaded.activation_routes
        if route.created_by_input_id == candidate.transition_input.input_id
    ]


def test_restart_refuses_cross_plan_or_partial_lifecycle_aftermath(
    tmp_path: Path,
) -> None:
    state, _plan, _fingerprint = generic_lifecycle.origin_closed_state()
    first = project_next_lifecycle_transition(state).candidate
    assert first is not None
    after_first = generic_lifecycle.apply_candidate(state, first)
    partial = replace(after_first, work_dependencies={})
    partial_path = tmp_path / "partial"
    partial_path.mkdir()
    db_path, cas_root = runtime_store_paths(partial_path)

    with pytest.raises(StorageIntegrityError):
        persist_runtime_state(db_path, cas_root, partial)


def test_restart_refuses_duplicate_logical_join_aftermath(tmp_path: Path) -> None:
    state, _plan, _fingerprint = generic_lifecycle.two_report_state()
    first_decision = decide(
        state,
        JoinFromArtifact(
            "join-reports",
            join_id=generic_lifecycle.JOIN_ID,
            source_artifact_id="transition-observe-beta:artifact",
        ),
        generic_lifecycle.context(
            "join-reports",
            work_item_id="work-review",
            activation_id="activation-review",
        ),
    )
    assert first_decision.accepted is True
    joined = apply(state, first_decision)
    stale_duplicate_decision = decide(
        state,
        JoinFromArtifact(
            "join-reports-fresh",
            join_id=generic_lifecycle.JOIN_ID,
            source_artifact_id="transition-observe-beta:artifact",
        ),
        generic_lifecycle.context(
            "join-reports-fresh",
            work_item_id="work-review-fresh",
            activation_id="activation-review-fresh",
        ),
    )
    assert stale_duplicate_decision.accepted is True
    duplicated = apply(joined, stale_duplicate_decision)
    db_path, cas_root = runtime_store_paths(tmp_path)

    with pytest.raises(StorageIntegrityError, match="duplicate logical join"):
        persist_runtime_state(db_path, cas_root, duplicated)


def test_restart_accepts_two_distinct_logical_join_completions(
    tmp_path: Path,
) -> None:
    state, _plan, _fingerprint = generic_lifecycle.two_group_report_state()
    first_input, first_context = generic_lifecycle.join_transition_for_group("a")
    second_input, second_context = generic_lifecycle.join_transition_for_group("b")
    first_decision = decide(state, first_input, first_context)
    second_decision = decide(state, second_input, second_context)
    assert first_decision.accepted is True
    assert second_decision.accepted is True
    completed = apply(apply(state, first_decision), second_decision)

    loaded = persist_and_load_runtime_state(tmp_path, completed)
    projection = project_next_lifecycle_transition(loaded)

    assert {
        route.created_by_input_id
        for route in loaded.activation_routes
        if route.created_by_input_id in {first_input.input_id, second_input.input_id}
    } == {first_input.input_id, second_input.input_id}
    assert projection.candidate is None
    assert projection.diagnostics == ()


def test_restart_refuses_join_completion_authored_by_non_join_input(
    tmp_path: Path,
) -> None:
    state, _plan, _fingerprint = generic_lifecycle.two_report_state()
    candidate = project_next_lifecycle_transition(state).candidate
    assert candidate is not None
    completed = generic_lifecycle.apply_candidate(state, candidate)
    forged = generic_lifecycle.with_non_join_join_completion_authorship(completed)
    db_path, cas_root = runtime_store_paths(tmp_path)

    with pytest.raises(
        StorageIntegrityError,
        match="join transition",
    ):
        persist_runtime_state(db_path, cas_root, forged)


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
def test_restart_refuses_join_transition_route_bijection_breaks(
    tmp_path: Path,
    case: str,
) -> None:
    state = generic_lifecycle.join_bijection_state(case)
    db_path, cas_root = runtime_store_paths(tmp_path)

    with pytest.raises(StorageIntegrityError):
        persist_runtime_state(db_path, cas_root, state)


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
def test_restart_refuses_fanout_creator_or_aftermath_breaks(
    tmp_path: Path,
    case: str,
) -> None:
    state = generic_lifecycle.fanout_integrity_state(case)
    db_path, cas_root = runtime_store_paths(tmp_path)

    with pytest.raises(StorageIntegrityError):
        persist_runtime_state(db_path, cas_root, state)


def test_restart_preserves_complete_multi_item_join_cardinality(
    tmp_path: Path,
) -> None:
    state, _plan, _fingerprint = generic_lifecycle.complete_multi_item_report_state()
    candidate = project_next_lifecycle_transition(state).candidate
    assert candidate is not None
    joined = generic_lifecycle.apply_candidate(state, candidate)

    loaded = persist_and_load_runtime_state(tmp_path, joined)
    projection = project_next_lifecycle_transition(loaded)

    assert projection.candidate is None
    assert projection.diagnostics == ()


def test_restart_preserves_alternative_action_target_cardinality(
    tmp_path: Path,
) -> None:
    state, _plan, _fingerprint = generic_lifecycle.alternative_action_report_state(
        alpha_uses_alternative=False
    )
    candidate = project_next_lifecycle_transition(state).candidate
    assert candidate is not None
    joined = generic_lifecycle.apply_candidate(state, candidate)

    loaded = persist_and_load_runtime_state(tmp_path, joined)
    projection = project_next_lifecycle_transition(loaded)

    assert projection.candidate is None
    assert projection.diagnostics == ()


def test_restart_preserves_computed_join_readiness(tmp_path: Path) -> None:
    one_report, _plan, _fingerprint = generic_lifecycle.one_report_state()
    one_path = tmp_path / "one"
    one_path.mkdir()
    loaded_one = persist_and_load_runtime_state(one_path, one_report)
    missing_input = JoinFromArtifact(
        "join-one-report",
        join_id=generic_lifecycle.JOIN_ID,
        source_artifact_id="transition-observe-alpha:artifact",
    )

    missing = decide(
        loaded_one,
        missing_input,
        generic_lifecycle.context(missing_input.input_id),
    )

    assert missing.accepted is False
    assert missing.refusal is not None
    assert missing.refusal.reason == "join_evidence_missing"

    two_report, _plan, _fingerprint = generic_lifecycle.two_report_state()
    two_path = tmp_path / "two"
    two_path.mkdir()
    loaded_two = persist_and_load_runtime_state(two_path, two_report)
    ready_input = JoinFromArtifact(
        "join-ready",
        join_id=generic_lifecycle.JOIN_ID,
        source_artifact_id="transition-observe-beta:artifact",
    )
    ready = decide(
        loaded_two,
        ready_input,
        generic_lifecycle.context(
            ready_input.input_id,
            work_item_id="work-review-ready",
            activation_id="activation-review-ready",
        ),
    )
    after_ready = apply(loaded_two, ready)

    assert ready.accepted is True
    assert after_ready.work_items["work-review-ready"].queue_family_id == QueueFamilyId(
        "joined_bundle"
    )
    assert str(after_ready.activations["activation-review-ready"].stage_kind_id) == (
        "review_stage"
    )


def test_restart_preserves_multi_item_join_cardinality(tmp_path: Path) -> None:
    state, _plan, _fingerprint = generic_lifecycle.complete_multi_item_report_state()
    loaded = persist_and_load_runtime_state(tmp_path, state)
    transition_input = JoinFromArtifact(
        "join-multi-restart",
        join_id=generic_lifecycle.JOIN_ID,
        source_artifact_id="transition-observe-beta:artifact",
    )
    decision = decide(
        loaded,
        transition_input,
        generic_lifecycle.context(
            transition_input.input_id,
            work_item_id="work-review-multi-restart",
            activation_id="activation-review-multi-restart",
        ),
    )

    assert decision.accepted is True
    joined = apply(loaded, decision)
    joined_path = tmp_path / "joined"
    joined_path.mkdir()
    reloaded = persist_and_load_runtime_state(joined_path, joined)
    projection = project_next_lifecycle_transition(reloaded)
    status = operator_status(reloaded)

    assert projection.candidate is None
    assert projection.diagnostics == ()
    assert len(status.joins) == 1
    assert status.joins[0].observed_artifact_schema_ids == (
        generic_lifecycle.ALPHA_REPORT_SCHEMA_ID,
        generic_lifecycle.BETA_REPORT_SCHEMA_ID,
    )
    assert status.joins[0].missing_artifact_schema_ids == ()
    assert status.joins[0].ready is True


def test_restart_preserves_selected_join_dispatch_evidence(tmp_path: Path) -> None:
    state, _plan, _fingerprint = generic_lifecycle.complete_multi_item_report_state()
    joined = generic_lifecycle.apply_join(state)
    claimed = generic_lifecycle.claim_activation(
        joined,
        activation_id="activation-review",
        suffix="review",
    )

    before = _build_dispatch_envelope(state=claimed, run_id="run-review").payload()
    loaded = persist_and_load_runtime_state(tmp_path, claimed)
    after = _build_dispatch_envelope(state=loaded, run_id="run-review").payload()

    assert before["selected_join_evidence"] is not None
    assert after == before


def test_restart_refuses_full_slots_without_required_schema_coverage(
    tmp_path: Path,
) -> None:
    state, _plan, _fingerprint = generic_lifecycle.alternative_action_report_state(
        alpha_uses_alternative=True
    )
    db_path, cas_root = runtime_store_paths(tmp_path)

    with pytest.raises(StorageIntegrityError):
        persist_runtime_state(db_path, cas_root, state)


def test_restart_refuses_full_plan_ref_namespace_drift(tmp_path: Path) -> None:
    state, _plan, _fingerprint = (
        generic_lifecycle.origin_closed_with_admitted_plan_ref_drift()
    )
    db_path, cas_root = runtime_store_paths(tmp_path)

    with pytest.raises(StorageIntegrityError):
        persist_runtime_state(db_path, cas_root, state)


def test_restart_keeps_other_plan_join_completion_nonparticipating(
    tmp_path: Path,
) -> None:
    state, lower_fingerprint, _higher_fingerprint = (
        generic_lifecycle.lower_plan_ready_with_higher_plan_joined_state()
    )

    loaded = persist_and_load_runtime_state(tmp_path, state)
    projection = project_next_lifecycle_transition(loaded)

    assert projection.diagnostics == ()
    assert projection.candidate is not None
    assert projection.candidate.plan_fingerprint == lower_fingerprint
    assert projection.candidate.kind == "join"


def test_restart_preserves_selected_fanout_branches(tmp_path: Path) -> None:
    state, _plan, _fingerprint = generic_lifecycle.two_complete_fanouts_state()
    loaded = persist_and_load_runtime_state(tmp_path, state)
    assert loaded.fanout_records == state.fanout_records
    assert loaded.activation_routes == state.activation_routes


@pytest.mark.parametrize(
    "case",
    ("missing_aftermath", "partial_item_aftermath", "split_item_creators"),
)
def test_restart_refuses_selected_fanout_target_aftermath_drift(
    tmp_path: Path,
    case: str,
) -> None:
    state = generic_lifecycle.fanout_integrity_state(case)
    db_path, cas_root = runtime_store_paths(tmp_path)
    with pytest.raises(StorageIntegrityError):
        persist_runtime_state(db_path, cas_root, state)


def test_restart_refuses_non_join_route_to_selected_join_target(
    tmp_path: Path,
) -> None:
    state = generic_lifecycle.join_bijection_state("unscopable_non_join_route")
    db_path, cas_root = runtime_store_paths(tmp_path)
    with pytest.raises(StorageIntegrityError):
        persist_runtime_state(db_path, cas_root, state)


def test_restart_preserves_observation_time_and_join_dispatch(
    tmp_path: Path,
) -> None:
    state, _plan, _fingerprint = generic_lifecycle.complete_multi_item_report_state()
    joined = generic_lifecycle.apply_join(state)
    claimed = generic_lifecycle.claim_activation(
        joined,
        activation_id="activation-review",
        suffix="review",
    )
    before = _build_dispatch_envelope(state=claimed, run_id="run-review").payload()
    loaded = persist_and_load_runtime_state(tmp_path, claimed)
    after = _build_dispatch_envelope(state=loaded, run_id="run-review").payload()
    assert after == before


@pytest.mark.parametrize(
    ("field", "evidence_kind"),
    (
        ("payload", "bundle"),
        ("payload", "evidence"),
        ("observed_at", "bundle"),
        ("observed_at", "evidence"),
    ),
)
def test_restart_refuses_selected_observation_payload_or_time_drift(
    tmp_path: Path,
    field: str,
    evidence_kind: str,
) -> None:
    state, _plan, _fingerprint = generic_lifecycle.two_report_state()
    observation = next(
        item
        for item in state.runner_observations.values()
        if (
            (evidence_kind == "bundle" and item.run_id == "run-origin")
            or (evidence_kind == "evidence" and item.run_id == "run-beta")
        )
    )
    db_path, cas_root = runtime_store_paths(tmp_path)
    persist_runtime_state(db_path, cas_root, state)
    with sqlite3.connect(db_path) as connection:
        if field == "payload":
            payload_digest = ContentAddressedByteStore(cas_root).put_bytes(
                dumps_cas_object(
                    encode_payload({**observation.payload, "marker": "CORRUPT"})
                )
            )
            connection.execute(
                "UPDATE runner_observations SET payload_digest = ? "
                "WHERE observation_id = ?",
                (payload_digest, observation.observation_id),
            )
        else:
            connection.execute(
                "UPDATE runner_observations SET observed_at = 1 "
                "WHERE observation_id = ?",
                (observation.observation_id,),
            )
    with pytest.raises(StorageIntegrityError):
        load_runtime_state(db_path, cas_root)


def test_restart_refuses_corrupt_selected_join_dispatch_authority(
    tmp_path: Path,
) -> None:
    state, _plan, _fingerprint = generic_lifecycle.two_report_state()
    drifted = _with_source_context_drift(state, "run_runner")
    db_path, cas_root = runtime_store_paths(tmp_path)
    with pytest.raises(StorageIntegrityError):
        persist_runtime_state(db_path, cas_root, drifted)


@pytest.mark.parametrize(
    "case",
    ("missing_route", "route_record_id", "non_join_creator", "unscopable_source"),
)
def test_restart_refuses_corrupt_selected_join_target_route(
    tmp_path: Path,
    case: str,
) -> None:
    state = generic_lifecycle.join_bijection_state(case)
    db_path, cas_root = runtime_store_paths(tmp_path)
    with pytest.raises(StorageIntegrityError):
        persist_runtime_state(db_path, cas_root, state)
