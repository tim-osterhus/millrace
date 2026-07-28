from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from millrace.contracts.transition import JoinFromArtifact
from millrace.kernel import apply
from millrace.kernel.lifecycle import project_next_lifecycle_transition
from millrace.substrate.errors import StorageIntegrityError
from millrace.testing import (
    decide_with_fake_runner_completion as decide,
)
from millrace.testing import (
    fake_runner_completion_input_id,
)
from substrate._runtime_store_support import (
    persist_and_load_runtime_state,
    persist_runtime_state,
    runtime_store_paths,
)
from support import generic_lifecycle


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
        if item.created_by_input_id
        == fake_runner_completion_input_id("observe-origin")
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
