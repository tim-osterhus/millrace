from __future__ import annotations

import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from millrace.contracts import QueueFamilyId
from millrace.contracts.compiled_plan import canonical_authority_bytes
from millrace.contracts.transition import artifact_payload_digest
from millrace.kernel import apply, decide
from millrace.operator import operator_status
from millrace.operator.dispatch import (
    DispatchProjectionError,
    build_dispatch_envelope_for_run,
)
from millrace.substrate.cas import ContentAddressedByteStore
from millrace.substrate.codecs import dumps_cas_object, encode_payload
from millrace.substrate.errors import StorageIntegrityError
from millrace.substrate.records import ARTIFACT_PAYLOAD_OBJECT_KIND
from substrate._runtime_store_support import (
    load_runtime_state,
    persist_and_load_runtime_state,
    persist_runtime_state,
    runtime_store_paths,
)
from support import vendor_selection


def _with_fully_drifted_fanout_target(state, *, run_id: str):
    run = state.runs[run_id]
    work_item = state.work_items[run.work_item_id]
    activation = state.activations[run.activation_id]
    drifted_work_item_id = "drifted-work"
    drifted_activation_id = "drifted-activation"
    return replace(
        state,
        work_items={
            **{
                work_id: candidate
                for work_id, candidate in state.work_items.items()
                if work_id != work_item.ref.work_item_id
            },
            drifted_work_item_id: replace(
                work_item,
                ref=replace(work_item.ref, work_item_id=drifted_work_item_id),
            ),
        },
        activations={
            **{
                activation_id: candidate
                for activation_id, candidate in state.activations.items()
                if activation_id != activation.activation_id
            },
            drifted_activation_id: replace(
                activation,
                activation_id=drifted_activation_id,
                work_item_id=drifted_work_item_id,
            ),
        },
        runs={
            **state.runs,
            run_id: replace(
                run,
                run_ref=replace(run.run_ref, work_item_id=drifted_work_item_id),
                work_item_id=drifted_work_item_id,
                activation_id=drifted_activation_id,
            ),
        },
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


def _with_extra_fanout_target_route(
    state,
    *,
    run_id: str,
    shared_target: str,
):
    run = state.runs[run_id]
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
            else {
                **state.work_items,
                extra_work_item_id: replace(
                    work_item,
                    ref=replace(
                        work_item.ref,
                        work_item_id=extra_work_item_id,
                    ),
                    created_by_input_id=source_artifact.created_by_input_id,
                ),
            }
        ),
        activations=(
            {
                **state.activations,
                extra_activation_id: replace(
                    activation,
                    activation_id=extra_activation_id,
                    created_by_input_id=source_artifact.created_by_input_id,
                    claimed_by_run_id=None,
                ),
            }
            if shared_target == "work"
            else state.activations
        ),
        activation_routes=(*state.activation_routes, extra_route),
    )


def _with_extra_non_join_target_route(state):
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


def _packager_closed_with_observed_at(observed_at: int):
    state, plan, fingerprint = vendor_selection.packager_claimed_state("a")
    state = vendor_selection.apply_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-packager-a",
        action_id="vendor_selection.candidate_packager.candidates_ready",
        input_id="observe-packager-a",
        artifact_payload=vendor_selection.candidate_bundle_payload(
            "bundle-a",
            request_id="request-a",
        ),
        observed_at=observed_at,
    )
    return state, plan, fingerprint


def _apply_packager_fanouts(state):
    from millrace.contracts.transition import FanoutFromArtifact

    for fanout_id, suffix in (
        ("vendor_selection.candidate_packager.rubric_fanout", "rubric"),
        ("vendor_selection.candidate_packager.conflict_fanout", "conflict"),
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


def _join_dispatch_state_with_observed_at():
    from millrace.contracts.transition import JoinFromArtifact

    state, plan, fingerprint = _packager_closed_with_observed_at(101)
    state = _apply_packager_fanouts(state)
    for kind, stage_kind_id, action_id, artifact_payload, observed_at in (
        (
            "rubric",
            "rubric_evaluator",
            "vendor_selection.rubric_evaluator.rubric_complete",
            vendor_selection.rubric_report_payload("bundle-a"),
            202,
        ),
        (
            "conflict",
            "conflict_checker",
            "vendor_selection.conflict_checker.conflict_complete",
            vendor_selection.conflict_report_payload("bundle-a"),
            303,
        ),
    ):
        activation_id = vendor_selection.report_branch_activation_id(
            state,
            stage_kind_id,
        )
        state = vendor_selection.claim_activation(
            state,
            activation_id=activation_id,
            suffix=f"{kind}-a",
        )
        state = vendor_selection.apply_observation(
            state,
            plan=plan,
            fingerprint=fingerprint,
            run_id=f"run-{kind}-a",
            action_id=action_id,
            input_id=f"observe-{kind}-a",
            artifact_payload=artifact_payload,
            observed_at=observed_at,
        )
    state = vendor_selection.apply_accepted_input(
        state,
        JoinFromArtifact(
            "join-award-a",
            join_id=vendor_selection.JOIN_ID,
            source_artifact_id=vendor_selection.artifact_id_for(
                "observe-conflict-a"
            ),
        ),
        vendor_selection.context(
            "join-award-a",
            work_item_id="work-award-a",
            activation_id="activation-award-a",
        ),
    )
    return vendor_selection.claim_activation(
        state,
        activation_id="activation-award-a",
        suffix="award-a",
    )


def _observation_for_input(state, input_id: str):
    return next(
        observation
        for observation in state.runner_observations.values()
        if observation.created_by_input_id == input_id
    )


def test_restart_preserves_vendor_selection_receipt_and_work_item(
    tmp_path: Path,
) -> None:
    state, _plan, fingerprint = vendor_selection.admit_vendor_selection()
    state = vendor_selection.enqueue_purchase_request(state, suffix="a")

    loaded = persist_and_load_runtime_state(tmp_path, state)

    assert loaded.default_plan_ref == state.default_plan_ref
    assert loaded.receipts["enqueue-a"] == state.receipts["enqueue-a"]
    assert loaded.work_items["work-request-a"] == state.work_items["work-request-a"]
    assert (
        loaded.activations["activation-request-intake-a"]
        == (state.activations["activation-request-intake-a"])
    )
    assert (
        loaded.work_items["work-request-a"].ref.plan_ref.authority_fingerprint
        == fingerprint
    )


def test_restart_preserves_vendor_selection_route_chain_provenance(
    tmp_path: Path,
) -> None:
    state, plan, fingerprint = vendor_selection.admit_vendor_selection()
    state = vendor_selection.enqueue_purchase_request(state, suffix="a")
    state = vendor_selection.progress_to_candidate_packager(
        state,
        plan=plan,
        fingerprint=fingerprint,
        suffix="a",
    )

    loaded = persist_and_load_runtime_state(tmp_path, state)

    assert loaded.work_items == state.work_items
    assert loaded.activations == state.activations
    assert loaded.activation_routes == state.activation_routes
    assert loaded.artifacts == state.artifacts
    assert loaded.transitions == state.transitions


def test_vendor_selection_restart_preserves_fanout_branches(
    tmp_path: Path,
) -> None:
    state, _plan, _fingerprint = vendor_selection.packager_closed_state("a")
    from millrace.contracts.transition import FanoutFromArtifact

    for fanout_id, suffix in (
        ("vendor_selection.candidate_packager.rubric_fanout", "rubric"),
        ("vendor_selection.candidate_packager.conflict_fanout", "conflict"),
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

    loaded = persist_and_load_runtime_state(tmp_path, state)

    assert loaded.fanout_records == state.fanout_records
    assert loaded.work_dependencies == state.work_dependencies
    assert {
        str(activation.stage_kind_id)
        for activation in loaded.activations.values()
        if activation.created_by_input_id in {"fanout-rubric-a", "fanout-conflict-a"}
    } == {"rubric_evaluator", "conflict_checker"}


def test_restart_refuses_fully_drifted_fanout_target_aftermath(
    tmp_path: Path,
) -> None:
    state, _plan, _fingerprint = vendor_selection.one_report_state()
    activation_id = vendor_selection.report_branch_activation_id(
        state,
        "conflict_checker",
    )
    state = vendor_selection.claim_activation(
        state,
        activation_id=activation_id,
        suffix="conflict-a",
    )
    db_path, cas_root = runtime_store_paths(tmp_path)

    with pytest.raises(StorageIntegrityError):
        persist_runtime_state(
            db_path,
            cas_root,
            _with_fully_drifted_fanout_target(
                state,
                run_id="run-conflict-a",
            ),
        )


@pytest.mark.parametrize("shared_target", ("work", "activation"))
def test_restart_refuses_extra_partial_fanout_target_route(
    tmp_path: Path,
    shared_target: str,
) -> None:
    state, _plan, _fingerprint = vendor_selection.one_report_state()
    activation_id = vendor_selection.report_branch_activation_id(
        state,
        "conflict_checker",
    )
    state = vendor_selection.claim_activation(
        state,
        activation_id=activation_id,
        suffix="conflict-a",
    )
    db_path, cas_root = runtime_store_paths(tmp_path)

    with pytest.raises(StorageIntegrityError):
        persist_runtime_state(
            db_path,
            cas_root,
            _with_extra_fanout_target_route(
                state,
                run_id="run-conflict-a",
                shared_target=shared_target,
            ),
        )


def test_restart_preserves_computed_join_readiness(tmp_path: Path) -> None:
    from millrace.contracts.transition import JoinFromArtifact

    one_report, _plan, _fingerprint = vendor_selection.one_report_state()
    one_report_path = tmp_path / "one"
    one_report_path.mkdir()
    loaded_one_report = persist_and_load_runtime_state(one_report_path, one_report)

    one_report_decision = decide(
        loaded_one_report,
        JoinFromArtifact(
            "join-one-report-a",
            join_id=vendor_selection.JOIN_ID,
            source_artifact_id=vendor_selection.artifact_id_for("observe-rubric-a"),
        ),
        vendor_selection.context(
            "join-one-report-a",
            work_item_id="work-award-a",
            activation_id="activation-award-a",
        ),
    )
    assert one_report_decision.accepted is False
    assert one_report_decision.refusal is not None
    assert one_report_decision.refusal.reason == "join_evidence_missing"

    two_report, _plan, _fingerprint = vendor_selection.two_report_state()
    two_report_path = tmp_path / "two"
    two_report_path.mkdir()
    loaded_two_report = persist_and_load_runtime_state(two_report_path, two_report)

    ready_decision = decide(
        loaded_two_report,
        JoinFromArtifact(
            "join-award-a",
            join_id=vendor_selection.JOIN_ID,
            source_artifact_id=vendor_selection.artifact_id_for("observe-conflict-a"),
        ),
        vendor_selection.context(
            "join-award-a",
            work_item_id="work-award-a",
            activation_id="activation-award-a",
        ),
    )
    after_ready = apply(loaded_two_report, ready_decision)

    assert ready_decision.accepted is True
    assert after_ready.work_items["work-award-a"].queue_family_id == QueueFamilyId(
        "candidate_bundle"
    )
    assert str(after_ready.activations["activation-award-a"].stage_kind_id) == (
        "award_decider"
    )


def test_restart_refuses_extra_non_join_route_to_join_target(
    tmp_path: Path,
) -> None:
    state, _plan, _fingerprint = vendor_selection.award_decider_claimed_state()
    db_path, cas_root = runtime_store_paths(tmp_path)

    with pytest.raises(StorageIntegrityError):
        persist_runtime_state(
            db_path,
            cas_root,
            _with_extra_non_join_target_route(state),
        )


def test_restart_preserves_vendor_multi_candidate_join_cardinality(
    tmp_path: Path,
) -> None:
    state, _plan, _fingerprint = (
        vendor_selection.multi_candidate_complete_report_state()
    )
    loaded = persist_and_load_runtime_state(tmp_path, state)
    from millrace.contracts.transition import JoinFromArtifact

    transition_input = JoinFromArtifact(
        "join-vendor-multi-restart",
        join_id=vendor_selection.JOIN_ID,
        source_artifact_id=vendor_selection.artifact_id_for(
            "observe-conflict-multi-1"
        ),
    )
    decision = decide(
        loaded,
        transition_input,
        vendor_selection.context(
            transition_input.input_id,
            work_item_id="work-award-multi-restart",
            activation_id="activation-award-multi-restart",
        ),
    )

    assert decision.accepted is True
    joined = apply(loaded, decision)
    joined_path = tmp_path / "joined"
    joined_path.mkdir()
    reloaded = persist_and_load_runtime_state(joined_path, joined)
    from millrace.kernel.lifecycle import project_next_lifecycle_transition

    projection = project_next_lifecycle_transition(reloaded)
    status = operator_status(reloaded)

    assert projection.candidate is None
    assert projection.diagnostics == ()
    assert len(status.joins) == 1
    assert status.joins[0].observed_artifact_schema_ids == (
        "RubricReport",
        "ConflictReport",
    )
    assert status.joins[0].missing_artifact_schema_ids == ()
    assert status.joins[0].ready is True


def test_restart_preserves_selected_join_dispatch_evidence(tmp_path: Path) -> None:
    state, _plan, _fingerprint = vendor_selection.award_decider_claimed_state()

    before = build_dispatch_envelope_for_run(
        state=state,
        run_id="run-award-a",
    ).payload()
    loaded = persist_and_load_runtime_state(tmp_path, state)
    after = build_dispatch_envelope_for_run(
        state=loaded,
        run_id="run-award-a",
    ).payload()

    assert before["selected_join_evidence"] is not None
    assert after == before


def test_restart_preserves_non_null_observed_at_for_fanout_and_join_dispatch(
    tmp_path: Path,
) -> None:
    fanout_state, _plan, _fingerprint = _packager_closed_with_observed_at(101)
    fanout_state = _apply_packager_fanouts(fanout_state)
    conflict_activation_id = vendor_selection.report_branch_activation_id(
        fanout_state,
        "conflict_checker",
    )
    fanout_state = vendor_selection.claim_activation(
        fanout_state,
        activation_id=conflict_activation_id,
        suffix="conflict-a",
    )
    fanout_before = build_dispatch_envelope_for_run(
        state=fanout_state,
        run_id="run-conflict-a",
    ).payload()
    fanout_path = tmp_path / "fanout"
    fanout_path.mkdir()
    loaded_fanout = persist_and_load_runtime_state(fanout_path, fanout_state)
    fanout_after = build_dispatch_envelope_for_run(
        state=loaded_fanout,
        run_id="run-conflict-a",
    ).payload()

    join_state = _join_dispatch_state_with_observed_at()
    join_before = build_dispatch_envelope_for_run(
        state=join_state,
        run_id="run-award-a",
    ).payload()
    join_path = tmp_path / "join"
    join_path.mkdir()
    loaded_join = persist_and_load_runtime_state(join_path, join_state)
    join_after = build_dispatch_envelope_for_run(
        state=loaded_join,
        run_id="run-award-a",
    ).payload()

    assert (
        _observation_for_input(
            loaded_fanout,
            "observe-packager-a",
        ).observed_at
        == 101
    )
    assert {
        input_id: _observation_for_input(loaded_join, input_id).observed_at
        for input_id in (
            "observe-packager-a",
            "observe-rubric-a",
            "observe-conflict-a",
        )
    } == {
        "observe-packager-a": 101,
        "observe-rubric-a": 202,
        "observe-conflict-a": 303,
    }
    assert canonical_authority_bytes(fanout_after) == canonical_authority_bytes(
        fanout_before
    )
    assert canonical_authority_bytes(join_after) == canonical_authority_bytes(
        join_before
    )


@pytest.mark.parametrize(
    "input_id",
    ("observe-packager-a", "observe-conflict-a"),
    ids=("bundle", "evidence"),
)
@pytest.mark.parametrize("field", ("payload", "observed_at"))
def test_restart_refuses_selected_observation_payload_or_time_drift(
    tmp_path: Path,
    input_id: str,
    field: str,
) -> None:
    state, _plan, _fingerprint = vendor_selection.award_decider_claimed_state()
    observation = _observation_for_input(state, input_id)
    db_path, cas_root = runtime_store_paths(tmp_path)
    persist_runtime_state(db_path, cas_root, state)

    if field == "payload":
        changed_payload = {**observation.payload, "marker": "CORRUPT_MARKER"}
        changed_digest = ContentAddressedByteStore(cas_root).put_bytes(
            dumps_cas_object(encode_payload(changed_payload))
        )
        with sqlite3.connect(db_path) as connection:
            cursor = connection.execute(
                """
                UPDATE runner_observations
                SET payload_digest = ?
                WHERE observation_id = ?
                """,
                (changed_digest, observation.observation_id),
            )
            assert cursor.rowcount == 1
    else:
        with sqlite3.connect(db_path) as connection:
            cursor = connection.execute(
                """
                UPDATE runner_observations
                SET observed_at = 1
                WHERE observation_id = ?
                """,
                (observation.observation_id,),
            )
            assert cursor.rowcount == 1

    with pytest.raises(
        StorageIntegrityError,
        match="runner_observations accepted-input authority invalid: receipt_authority",
    ):
        load_runtime_state(db_path, cas_root)


def test_restart_refuses_corrupt_selected_join_dispatch_authority(
    tmp_path: Path,
) -> None:
    state, _plan, _fingerprint = vendor_selection.award_decider_claimed_state()
    db_path, cas_root = runtime_store_paths(tmp_path)
    persist_runtime_state(db_path, cas_root, state)
    with sqlite3.connect(db_path) as connection:
        cursor = connection.execute(
            """
            UPDATE fanout_records
            SET item_key = 'wrong-item'
            WHERE source_artifact_id = ?
            """,
            (vendor_selection.artifact_id_for("observe-packager-a"),),
        )
        assert cursor.rowcount == 2

    try:
        loaded = load_runtime_state(db_path, cas_root)
    except StorageIntegrityError:
        return
    with pytest.raises(DispatchProjectionError):
        build_dispatch_envelope_for_run(state=loaded, run_id="run-award-a")


def test_restart_refuses_corrupt_join_created_target_route(tmp_path: Path) -> None:
    from millrace.contracts.transition import JoinFromArtifact
    from substrate._runtime_store_support import (
        load_runtime_state,
        persist_runtime_state,
        runtime_store_paths,
    )

    state, _plan, _fingerprint = vendor_selection.two_report_state()
    joined = apply(
        state,
        decide(
            state,
            JoinFromArtifact(
                "join-award-a",
                join_id=vendor_selection.JOIN_ID,
                source_artifact_id=vendor_selection.artifact_id_for(
                    "observe-conflict-a"
                ),
            ),
            vendor_selection.context(
                "join-award-a",
                work_item_id="work-award-a",
                activation_id="activation-award-a",
            ),
        ),
    )
    db_path, cas_root = runtime_store_paths(tmp_path)
    persist_runtime_state(db_path, cas_root, joined)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            UPDATE work_items
            SET queue_family_id = 'decision_pack'
            WHERE work_item_id = 'work-award-a'
            """
        )
        connection.execute(
            """
            UPDATE activations
            SET queue_family_id = 'decision_pack',
                graph_node_id = 'vendor_selection.decision_packager.start',
                stage_kind_id = 'decision_packager'
            WHERE activation_id = 'activation-award-a'
            """
        )

    with pytest.raises(
        StorageIntegrityError,
        match="join-created activation route target must match selected join route",
    ):
        load_runtime_state(db_path, cas_root)


def test_restart_refuses_corrupt_join_route_action_id_and_target(
    tmp_path: Path,
) -> None:
    from millrace.contracts.transition import JoinFromArtifact
    from substrate._runtime_store_support import (
        load_runtime_state,
        persist_runtime_state,
        runtime_store_paths,
    )

    state, _plan, _fingerprint = vendor_selection.two_report_state()
    joined = apply(
        state,
        decide(
            state,
            JoinFromArtifact(
                "join-award-a",
                join_id=vendor_selection.JOIN_ID,
                source_artifact_id=vendor_selection.artifact_id_for(
                    "observe-conflict-a"
                ),
            ),
            vendor_selection.context(
                "join-award-a",
                work_item_id="work-award-a",
                activation_id="activation-award-a",
            ),
        ),
    )
    db_path, cas_root = runtime_store_paths(tmp_path)
    persist_runtime_state(db_path, cas_root, joined)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            UPDATE activation_routes
            SET action_id = 'vendor_selection.request_intake.request_ready'
            WHERE record_id = 'transition-join-award-a:route'
            """
        )
        connection.execute(
            """
            UPDATE work_items
            SET queue_family_id = 'decision_pack'
            WHERE work_item_id = 'work-award-a'
            """
        )
        connection.execute(
            """
            UPDATE activations
            SET queue_family_id = 'decision_pack',
                graph_node_id = 'vendor_selection.decision_packager.start',
                stage_kind_id = 'decision_packager'
            WHERE activation_id = 'activation-award-a'
            """
        )

    with pytest.raises(
        StorageIntegrityError,
        match="join-created activation route action must match selected join",
    ):
        load_runtime_state(db_path, cas_root)


def test_restart_refuses_non_join_route_with_join_action_id(tmp_path: Path) -> None:
    from substrate._runtime_store_support import (
        load_runtime_state,
        persist_runtime_state,
        runtime_store_paths,
    )

    state, _plan, _fingerprint = vendor_selection.full_decision_pack_closed_state()
    db_path, cas_root = runtime_store_paths(tmp_path)
    persist_runtime_state(db_path, cas_root, state)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            UPDATE activation_routes
            SET action_id = ?
            WHERE record_id = 'transition-observe-award-a:route'
            """,
            (vendor_selection.JOIN_ID,),
        )

    with pytest.raises(
        StorageIntegrityError,
        match="join-created activation route must reference join transition",
    ):
        load_runtime_state(db_path, cas_root)


def test_restart_refuses_join_route_coherent_provenance_rewrite(
    tmp_path: Path,
) -> None:
    from millrace.contracts.transition import JoinFromArtifact
    from substrate._runtime_store_support import (
        load_runtime_state,
        persist_runtime_state,
        runtime_store_paths,
    )

    state, _plan, _fingerprint = vendor_selection.two_report_state()
    joined = apply(
        state,
        decide(
            state,
            JoinFromArtifact(
                "join-award-a",
                join_id=vendor_selection.JOIN_ID,
                source_artifact_id=vendor_selection.artifact_id_for(
                    "observe-conflict-a"
                ),
            ),
            vendor_selection.context(
                "join-award-a",
                work_item_id="work-award-a",
                activation_id="activation-award-a",
            ),
        ),
    )
    db_path, cas_root = runtime_store_paths(tmp_path)
    persist_runtime_state(db_path, cas_root, joined)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            UPDATE activation_routes
            SET record_id = 'transition-observe-conflict-a:route',
                action_id = 'vendor_selection.request_intake.request_ready',
                created_by_input_id = 'observe-conflict-a'
            WHERE record_id = 'transition-join-award-a:route'
            """
        )
        connection.execute(
            """
            UPDATE work_items
            SET queue_family_id = 'decision_pack',
                created_by_input_id = 'observe-conflict-a'
            WHERE work_item_id = 'work-award-a'
            """
        )
        connection.execute(
            """
            UPDATE activations
            SET queue_family_id = 'decision_pack',
                graph_node_id = 'vendor_selection.decision_packager.start',
                stage_kind_id = 'decision_packager',
                created_by_input_id = 'observe-conflict-a'
            WHERE activation_id = 'activation-award-a'
            """
        )

    with pytest.raises(
        StorageIntegrityError,
        match="selected join transition or evidence is partial or corrupt",
    ):
        load_runtime_state(db_path, cas_root)


def test_restart_preserves_vendor_selection_decision_pack_close(
    tmp_path: Path,
) -> None:
    state, _plan, fingerprint = vendor_selection.full_decision_pack_closed_state()

    loaded = persist_and_load_runtime_state(tmp_path, state)

    artifact_id = vendor_selection.artifact_id_for("observe-decision-packager-a")
    assert loaded.artifacts[artifact_id] == state.artifacts[artifact_id]
    assert (
        loaded.closed_work_items["work-decision-packager-a"]
        == (state.closed_work_items["work-decision-packager-a"])
    )
    assert loaded.artifacts[artifact_id].payload["selected_plan_fingerprint"] == (
        fingerprint
    )
    assert loaded.governance_events == state.governance_events
    assert loaded.traces == state.traces


def test_restart_preserves_vendor_selection_operator_wait_after_join(
    tmp_path: Path,
) -> None:
    state, plan, fingerprint = vendor_selection.award_decider_claimed_state()
    state = vendor_selection.apply_observation(
        state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-award-a",
        action_id=vendor_selection.OPERATOR_WAIT_ACTION_ID,
        input_id="observe-award-operator-a",
        artifact_payload=vendor_selection.award_decision_payload(
            rubric_ref=vendor_selection.artifact_id_for("observe-rubric-a"),
            conflict_ref=vendor_selection.artifact_id_for("observe-conflict-a"),
            decision_kind="operator_required",
            operator_gate_required=True,
            reason="selected evidence requires local-operator confirmation",
        ),
    )

    loaded = persist_and_load_runtime_state(tmp_path, state)

    assert loaded.operator_waits == state.operator_waits
    wait = next(iter(loaded.operator_waits.values()))
    assert str(wait.operator_wait_id) == vendor_selection.OPERATOR_WAIT_ID
    assert str(wait.source_action_id) == vendor_selection.OPERATOR_WAIT_ACTION_ID
    assert wait.status == "active"
    assert wait.source_artifact_id == vendor_selection.artifact_id_for(
        "observe-award-operator-a"
    )


def test_restart_preserves_open_wait_computed_join_and_close_projection(
    tmp_path: Path,
) -> None:
    one_report, _plan, _fingerprint = vendor_selection.one_report_state()
    one_report_path = tmp_path / "one-report"
    one_report_path.mkdir()
    join_status = operator_status(persist_and_load_runtime_state(
        one_report_path,
        one_report,
    ))
    assert len(join_status.joins) == 1
    assert join_status.joins[0].join_id == vendor_selection.JOIN_ID
    assert join_status.joins[0].missing_artifact_schema_ids == ("ConflictReport",)

    wait_state, _plan, _fingerprint, wait_id = (
        vendor_selection.operator_required_wait_state()
    )
    wait_path = tmp_path / "wait"
    wait_path.mkdir()
    wait_status = operator_status(persist_and_load_runtime_state(
        wait_path,
        wait_state,
    ))
    assert wait_status.operator_waits[0].wait_id == wait_id
    assert wait_status.operator_waits[0].status == "active"
    assert wait_status.operator_waits[0].allowed_resolution_kinds == (
        "resume_recorded_source",
        "revise_recorded_source",
    )

    closed, _plan, fingerprint = vendor_selection.full_decision_pack_closed_state()
    closed_path = tmp_path / "closed"
    closed_path.mkdir()
    close_status = operator_status(persist_and_load_runtime_state(
        closed_path,
        closed,
    ))
    decision_pack = next(
        artifact
        for artifact in close_status.artifacts
        if artifact.source_input_id == "observe-decision-packager-a"
    )
    assert decision_pack.payload["selected_plan_fingerprint"] == fingerprint
    assert decision_pack.payload["close_reason"] == "awarded"


def test_restart_refuses_corrupt_vendor_selection_wait_resolution_or_close_links(
    tmp_path: Path,
) -> None:
    wait_state, _plan, _fingerprint, _wait_id = (
        vendor_selection.operator_required_wait_state()
    )
    wait_root = tmp_path / "wait-corrupt"
    wait_root.mkdir()
    wait_db, wait_cas = runtime_store_paths(wait_root)
    persist_runtime_state(wait_db, wait_cas, wait_state)
    with sqlite3.connect(wait_db) as connection:
        connection.execute(
            """
            UPDATE operator_waits
            SET operator_wait_id = 'vendor_selection.wrong_wait'
            """
        )
    with pytest.raises(
        StorageIntegrityError,
        match="operator_waits.operator_wait_id must reference selected",
    ):
        load_runtime_state(wait_db, wait_cas)

    closed, _plan, _fingerprint = vendor_selection.full_decision_pack_closed_state()
    close_root = tmp_path / "close-corrupt"
    close_root.mkdir()
    close_db, close_cas = runtime_store_paths(close_root)
    persist_runtime_state(close_db, close_cas, closed)
    with sqlite3.connect(close_db) as connection:
        connection.execute(
            """
            UPDATE closed_work_items
            SET source_run_id = 'run-rubric-a'
            WHERE work_item_id = 'work-decision-packager-a'
            """
        )
    with pytest.raises(
        StorageIntegrityError,
        match="closed_work_items.source_run_id must reference run for work_item_id",
    ):
        load_runtime_state(close_db, close_cas)


@pytest.mark.parametrize(
    ("payload_patch", "expected_message"),
    (
        (
            {},
            "artifacts runner-observation provenance invalid: "
            "artifact_payload_authority",
        ),
        (
            {"selected_plan_id": "wrong-plan"},
            "artifacts runner-observation provenance invalid: "
            "artifact_payload_authority",
        ),
        (
            {"selected_plan_fingerprint": f"sha256:{'0' * 64}"},
            "artifacts runner-observation provenance invalid: "
            "artifact_payload_authority",
        ),
    ),
)
def test_restart_refuses_corrupt_vendor_selection_decision_pack_payload(
    tmp_path: Path,
    payload_patch: dict[str, object],
    expected_message: str,
) -> None:
    state, _plan, _fingerprint = vendor_selection.full_decision_pack_closed_state()
    db_path, cas_root = runtime_store_paths(tmp_path)
    persist_runtime_state(db_path, cas_root, state)

    artifact_id = vendor_selection.artifact_id_for("observe-decision-packager-a")
    corrupt_payload = {
        "source_request_id": "request-a",
        "bundle_id": "bundle-a",
        "selected_candidate_id": "vendor_gamma",
        "final_refusal_reason": None,
        "close_reason": "awarded",
    }
    if payload_patch:
        corrupt_payload.update(
            {
                "evidence_refs": {
                    "rubric_report_ref": vendor_selection.artifact_id_for(
                        "observe-rubric-a"
                    ),
                    "conflict_report_ref": vendor_selection.artifact_id_for(
                        "observe-conflict-a"
                    ),
                },
                "selected_plan_id": "vendor_selection:0.1",
                "selected_plan_fingerprint": _fingerprint,
            }
        )
        corrupt_payload.update(payload_patch)
    cas_digest = ContentAddressedByteStore(cas_root).put_bytes(
        dumps_cas_object(
            encode_payload(
                corrupt_payload,
                object_kind=ARTIFACT_PAYLOAD_OBJECT_KIND,
            )
        )
    )
    logical_digest = artifact_payload_digest(corrupt_payload)
    with sqlite3.connect(db_path) as connection:
        cursor = connection.execute(
            """
            UPDATE artifacts
            SET payload_digest = ?, artifact_payload_digest = ?
            WHERE artifact_id = ?
            """,
            (cas_digest, logical_digest, artifact_id),
        )
        assert cursor.rowcount == 1

    with pytest.raises(
        StorageIntegrityError,
        match=expected_message,
    ):
        load_runtime_state(db_path, cas_root)


@pytest.mark.parametrize(
    ("column", "value"),
    (
        ("source_action_id", "vendor_selection.award_decider.award_ready"),
        ("source_stage_kind_id", "decision_packager"),
        ("source_graph_node_id", "vendor_selection.decision_packager.start"),
        ("source_queue_family_id", "decision_pack"),
        ("source_runner_binding_id", "missing_runner"),
    ),
)
def test_restart_refuses_corrupt_vendor_selection_operator_wait_source_links(
    tmp_path: Path,
    column: str,
    value: str,
) -> None:
    state, _plan, _fingerprint, _wait_id = (
        vendor_selection.operator_required_wait_state()
    )
    db_path, cas_root = runtime_store_paths(tmp_path)
    persist_runtime_state(db_path, cas_root, state)
    with sqlite3.connect(db_path) as connection:
        cursor = connection.execute(
            f"UPDATE operator_waits SET {column} = ?",
            (value,),
        )
        assert cursor.rowcount == 1

    with pytest.raises(StorageIntegrityError):
        load_runtime_state(db_path, cas_root)


@pytest.mark.parametrize(
    ("column", "value", "message"),
    (
        (
            "queue_family_id",
            "candidate_bundle",
            "runner_observations accepted-input authority invalid: "
            "selected_source_authority:queue_family",
        ),
        (
            "graph_node_id",
            "vendor_selection.award_decider.start",
            "runner_observations accepted-input authority invalid: "
            "evidence_authority",
        ),
        (
            "stage_kind_id",
            "award_decider",
            "runs.stage_kind_id must match activations.stage_kind_id",
        ),
        (
            "runner_binding_id",
            "missing_runner",
            "runs.runner_binding_id must match activations.runner_binding_id",
        ),
    ),
)
def test_restart_refuses_corrupt_vendor_selection_revise_target_route(
    tmp_path: Path,
    column: str,
    value: str,
    message: str,
) -> None:
    state, _plan, _fingerprint, _wait_id = (
        vendor_selection.operator_revise_decision_pack_closed_state()
    )
    db_path, cas_root = runtime_store_paths(tmp_path)
    persist_runtime_state(db_path, cas_root, state)
    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            "SELECT target_work_item_id, target_activation_id FROM operator_waits"
        ).fetchone()
        assert row is not None
        target_work_item_id, target_activation_id = row
        if column == "queue_family_id":
            connection.execute(
                "UPDATE work_items SET queue_family_id = ? WHERE work_item_id = ?",
                (value, target_work_item_id),
            )
        cursor = connection.execute(
            f"UPDATE activations SET {column} = ? WHERE activation_id = ?",
            (value, target_activation_id),
        )
        assert cursor.rowcount == 1

    with pytest.raises(StorageIntegrityError, match=message):
        load_runtime_state(db_path, cas_root)
