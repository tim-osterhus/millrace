"""Dispatch projection of closure requests that overflow their inline limit.

Every fixture reaches its closure request through real kernel transitions
(see ``tests/support/closure_overflow.py``). Dispatch facts are read from the
projected runner envelope, the materialized page list, and the durable state;
no durable row is inserted by hand.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from millrace.adapters.runner_contract import selected_invocation_authority_digest
from millrace.contracts.closure_request_overflow import (
    OVERFLOW_KEY,
    SNAPSHOT_KEY,
    materialization_component,
)
from millrace.contracts.runtime_failures import (
    runtime_failure_applied,
    runtime_failure_input,
)
from millrace.contracts.transition import RunnerResultObserved
from millrace.kernel import apply, decide
from millrace.operator.dispatch import (
    DispatchProjectionError,
    build_dispatch_envelope_for_run,
    closure_evidence_pages_for_run,
    closure_request_uses_evidence_pages,
    list_ready_dispatch_candidates,
)
from millrace.substrate.errors import StorageIntegrityError
from millrace.testing import (
    deterministic_context,
    fake_completed_runner_observation_state,
    fake_runner_observation_payload,
    fake_runner_session_state,
)
from substrate._runtime_store_support import persist_and_load_runtime_state
from support import closure_overflow as co
from support import generic_admission
from support import generic_lifecycle as gl

RUN_ID = "run-eval"
EVALUATOR_NODE_ID = "lifecycle.review.evaluate"
RECOVER_ACTION_ID = "qa.review.recover"
RETURN_ACTION_ID = "qa.return"
QUARANTINE_ACTION_ID = "qa.quarantine"


def _build(state, run_id: str = RUN_ID):
    """Dispatch envelope for ``run_id`` after its session is materialized in state."""
    return build_dispatch_envelope_for_run(
        state=fake_runner_session_state(state=state, run_id=run_id),
        run_id=run_id,
    )


def _claimed_evaluator(fixture: co.OverflowState, suffix: str = "eval"):
    """State with the evaluator activation claimed and its run session present."""
    activation = co.evaluator_activation(fixture.state)
    state = gl.claim_activation(
        fixture.state, activation_id=activation.activation_id, suffix=suffix
    )
    return fake_runner_session_state(state=state, run_id=f"run-{suffix}")


def _overflow_record(envelope) -> Any:
    return envelope.work_item_payload[OVERFLOW_KEY]


def _page_snapshot(pages) -> list[tuple[str, dict[str, Any], bytes]]:
    """Comparable (path, descriptor, bytes) triples for a materialized page list."""
    return [(page.path, dict(page.descriptor()), bytes(page.data)) for page in pages]


def _projected_bytes(state, payload) -> int:
    """Canonical size of the closure request item carrying ``payload``."""
    return co.work_payload_canonical_bytes(
        replace(co.closure_request_item(state), payload=dict(payload))
    )


def _recovery_overflow_source() -> dict[str, object]:
    """Overflow closure source whose evaluator timeout routes to a recovery stage.

    Mirrors the portable runtime-failure fixture in
    ``tests/kernel/test_closure_evaluator_runtime_failure_authority.py``: the
    evaluator gets its own node with no incoming route, and a timeout maps to a
    recovery route into ``alpha_stage``.
    """
    source = co.overflow_source()
    stages = {row["id"]: row for row in source["stage_kinds"]}
    stages["review_stage"]["declared_outcome_ids"] += ("qa.review.failure",)
    stages["alpha_stage"]["declared_outcome_ids"] += ("qa.recovered", "qa.quarantine")
    source["terminal_outcomes"] += [
        {
            "id": "qa.review.failure",
            "stage_kind_id": "review_stage",
            "marker": "QA_FAILURE",
        },
        {
            "id": "qa.recovered",
            "stage_kind_id": "alpha_stage",
            "marker": "QA_RECOVERED",
        },
        {
            "id": "qa.quarantine",
            "stage_kind_id": "alpha_stage",
            "marker": "QA_QUARANTINE",
        },
    ]
    source["terminal_actions"] += [
        {
            "id": RECOVER_ACTION_ID,
            "stage_kind_id": "review_stage",
            "outcome_id": "qa.review.failure",
            "kind": "recovery_route",
            "target_stage_kind_id": "alpha_stage",
            "target_graph_node_id": "lifecycle.alpha.start",
            "runner_binding_id": "lifecycle.runner",
            "asset_ids": stages["alpha_stage"]["asset_ids"],
        },
        {
            "id": RETURN_ACTION_ID,
            "stage_kind_id": "alpha_stage",
            "outcome_id": "qa.recovered",
            "kind": "return_to_recorded_source",
        },
        {
            "id": QUARANTINE_ACTION_ID,
            "stage_kind_id": "alpha_stage",
            "outcome_id": "qa.quarantine",
            "kind": "quarantine_lineage",
        },
    ]
    policy = deepcopy(generic_admission.source()["recovery_policies"][0])
    policy.update(
        id="qa.policy",
        source_recovery_action_ids=(RECOVER_ACTION_ID,),
        return_action_ids=(RETURN_ACTION_ID,),
        quarantine_action_ids=(QUARANTINE_ACTION_ID,),
        recovery_stage_kind_id="alpha_stage",
        reset_trigger_action_ids=(),
        cooldown_wait_state_id="qa.cooldown",
    )
    source["recovery_policies"] = [policy]
    source["wait_states"] = [
        {
            "id": "qa.cooldown",
            "kind": "timer",
            "policy_id": "qa.policy",
            "starts_at_attempt": 2,
            "duration_seconds": 900,
        }
    ]
    binding = source["runner_bindings"][0]
    binding["runtime_failure_mappings"] = [
        {
            "stage_kind_id": "review_stage",
            "failure_kind": "timeout",
            "action_id": RECOVER_ACTION_ID,
        }
    ]
    binding["component_pin"]["legal_terminal_result_ids"] += (
        "QA_RECOVERED",
        "QA_QUARANTINE",
    )
    binding["terminal_result_mappings"] = [
        {
            "stage_kind_id": "alpha_stage",
            "runner_result_id": result_id,
            "outcome_id": outcome_id,
        }
        for result_id, outcome_id in (
            ("COMPLETE", "lifecycle.alpha.ready"),
            ("QA_RECOVERED", "qa.recovered"),
            ("QA_QUARANTINE", "qa.quarantine"),
        )
    ]
    source["graphs"][0]["node_ids"] = (
        *source["graphs"][0]["node_ids"],
        EVALUATOR_NODE_ID,
    )
    source["completion_behaviors"][0]["target_graph_node_id"] = EVALUATOR_NODE_ID
    return source


def _evaluator_timeout(state, suffix: str):
    """Claimed evaluator run whose session completes with a timeout failure."""
    activation = co.evaluator_activation(state)
    state = gl.claim_activation(
        state, activation_id=activation.activation_id, suffix=suffix
    )
    run_id = f"run-{suffix}"
    run = state.runs[run_id]
    observation = RunnerResultObserved(
        f"timeout-observation-{suffix}",
        run_id=run_id,
        payload=fake_runner_observation_payload(
            run=run,
            activation=state.activations[activation.activation_id],
            plan_fingerprint=run.run_ref.plan_ref.authority_fingerprint,
            marker="DONE",
            artifact_payload=None,
        ),
        observed_at=3,
    )
    state, _ = fake_completed_runner_observation_state(
        state=state, observation=observation
    )
    session = state.runner_sessions[state.runs[run_id].current_session_id]
    completion = replace(
        state.runner_session_completions[session.session_id],
        terminal_state="failed",
        exit_kind="error",
        adapter_outcome_kind="error",
        adapter_error_kind="timeout",
        runner_result_evidence_digest=None,
    )
    return replace(
        state,
        runner_sessions={
            **state.runner_sessions,
            session.session_id: replace(session, state="failed"),
        },
        runner_session_completions={
            **state.runner_session_completions,
            session.session_id: completion,
        },
    ), completion


def _recovery_run_state(state, suffix: str):
    """Time out the evaluator, apply the runtime failure, and claim the recovery run.

    Returns the state with the recovery session present, the recovery run id, and
    the ready candidate that was claimed.
    """
    state, completion = _evaluator_timeout(state, suffix)
    run = state.runs[completion.run_id]
    command = runtime_failure_input(state, completion)
    context = deterministic_context(
        transition_id=f"transition-timeout-{suffix}",
        work_item_id=run.work_item_id,
        activation_id=f"recovery-{suffix}",
        run_id=run.run_ref.run_id,
        claim_id=run.run_ref.claim_id,
        fencing_token=run.run_ref.fencing_token,
    )
    decision = decide(state, command, context)
    assert decision.accepted, decision.refusal
    state = apply(state, decision)
    assert runtime_failure_applied(state, completion.run_id)
    closure_item = co.closure_request_item(state)
    candidates = [
        candidate
        for candidate in list_ready_dispatch_candidates(state).candidates
        if candidate.work_item_id == closure_item.ref.work_item_id
    ]
    assert len(candidates) == 1, [candidate.activation_id for candidate in candidates]
    candidate = candidates[0]
    assert candidate.stage_kind_id == "alpha_stage"
    run_id = f"run-recover-{suffix}"
    state = gl.claim_activation(
        state, activation_id=candidate.activation_id, suffix=f"recover-{suffix}"
    )
    return fake_runner_session_state(state=state, run_id=run_id), run_id, candidate


@pytest.fixture
def overflow_state() -> co.OverflowState:
    return co.evaluated_state()


@pytest.fixture
def claimed(overflow_state: co.OverflowState):
    return _claimed_evaluator(overflow_state)


def test_evaluator_run_projects_overflow_index_without_inline_snapshot(claimed) -> None:
    envelope = _build(claimed)
    payload = envelope.work_item_payload

    assert (
        co.work_payload_canonical_bytes(co.closure_request_item(claimed))
        > co.REQUEST_LIMIT
    )
    assert OVERFLOW_KEY in payload
    assert SNAPSHOT_KEY not in payload
    assert _projected_bytes(claimed, payload) <= co.REQUEST_LIMIT
    assert _overflow_record(envelope)["page_count"] == len(
        closure_evidence_pages_for_run(state=claimed, run_id=RUN_ID)
    )


def test_projection_is_identical_across_rebuilds() -> None:
    first_state = _claimed_evaluator(co.evaluated_state(), suffix="eval")
    second_state = _claimed_evaluator(co.evaluated_state(), suffix="eval")

    first = _build(first_state)
    second = _build(second_state)
    assert first == second
    assert _build(first_state) == first

    first_pages = closure_evidence_pages_for_run(state=first_state, run_id=RUN_ID)
    second_pages = closure_evidence_pages_for_run(state=second_state, run_id=RUN_ID)
    assert first_pages
    assert _page_snapshot(first_pages) == _page_snapshot(second_pages)


def test_selected_invocation_authority_digest_is_stable_across_rebuilds() -> None:
    first = _build(_claimed_evaluator(co.evaluated_state(), suffix="eval"))
    second = _build(_claimed_evaluator(co.evaluated_state(), suffix="eval"))

    digest = selected_invocation_authority_digest(first, "codex")
    assert digest
    assert digest == selected_invocation_authority_digest(second, "codex")


def test_pages_live_in_a_session_derived_directory(claimed) -> None:
    envelope = _build(claimed)
    component = materialization_component(
        envelope.session_id, envelope.dispatch_generation
    )
    directory = f"{co.MATERIALIZATION_ROOT}/{component}"
    pages = closure_evidence_pages_for_run(state=claimed, run_id=RUN_ID)

    assert envelope.session_id == f"test-session:{RUN_ID}"
    assert _overflow_record(envelope)["materialization_directory"] == directory
    assert pages
    assert all(page.path.startswith(f"{directory}/") for page in pages)
    assert materialization_component(
        "test-session:other-run", envelope.dispatch_generation
    ) != (component)
    assert materialization_component(
        envelope.session_id, envelope.dispatch_generation + 1
    ) != (component)


def test_inline_request_is_unchanged_and_projects_no_pages() -> None:
    fixture = co.evaluated_state(count=1, summary_bytes=200)
    state = _claimed_evaluator(fixture, suffix="inline")
    envelope = _build(state, run_id="run-inline")
    work_item = co.closure_request_item(state)
    payload = dict(envelope.work_item_payload)

    assert co.work_payload_canonical_bytes(work_item) <= co.REQUEST_LIMIT
    assert payload == dict(work_item.payload)
    assert SNAPSHOT_KEY in payload
    assert OVERFLOW_KEY not in payload
    assert not closure_evidence_pages_for_run(state=state, run_id="run-inline")
    assert (
        closure_request_uses_evidence_pages(state=state, run_id="run-inline") is False
    )


@pytest.mark.parametrize(
    ("evidence", "expected"),
    [
        pytest.param({"count": 3, "summary_bytes": 4000}, True, id="overflow"),
        pytest.param({"count": 1, "summary_bytes": 200}, False, id="inline"),
    ],
)
def test_uses_evidence_pages_is_true_only_above_the_inline_limit(
    evidence: dict[str, int], expected: bool
) -> None:
    state = _claimed_evaluator(co.evaluated_state(**evidence), suffix="uses")
    durable_bytes = co.work_payload_canonical_bytes(co.closure_request_item(state))

    assert (durable_bytes > co.REQUEST_LIMIT) is expected
    assert (
        closure_request_uses_evidence_pages(state=state, run_id="run-uses") is expected
    )
    assert (
        bool(closure_evidence_pages_for_run(state=state, run_id="run-uses")) is expected
    )


def test_page_digests_match_the_projection_index(claimed) -> None:
    envelope = _build(claimed)
    record = _overflow_record(envelope)
    pages = closure_evidence_pages_for_run(state=claimed, run_id=RUN_ID)
    by_path = {page.path: page.descriptor() for page in pages}

    assert len(by_path) == len(pages) == record["page_count"]
    assert record["page_bytes"] == sum(len(page.data) for page in pages)
    for page in pages:
        descriptor = by_path[page.path]
        assert descriptor["bytes"] == len(page.data)
        assert descriptor["sha256"] == hashlib.sha256(page.data).hexdigest()
        assert descriptor["bytes"] <= co.PAGE_BYTE_LIMIT
    assert record["index_pages"]
    for descriptor in record["index_pages"]:
        assert by_path[descriptor["path"]] == dict(descriptor)


def test_recovery_stage_run_on_the_same_work_item_is_projected() -> None:
    fixture = co.evaluated_state(_recovery_overflow_source())
    state, run_id, candidate = _recovery_run_state(fixture.state, "eval-one")
    envelope = _build(state, run_id=run_id)
    pages = closure_evidence_pages_for_run(state=state, run_id=run_id)
    component = materialization_component(
        envelope.session_id, envelope.dispatch_generation
    )

    assert (
        candidate.activation_id != co.evaluator_activation(fixture.state).activation_id
    )
    assert envelope.work_item_id == candidate.work_item_id
    assert OVERFLOW_KEY in envelope.work_item_payload
    assert SNAPSHOT_KEY not in envelope.work_item_payload
    assert closure_request_uses_evidence_pages(state=state, run_id=run_id) is True
    assert pages
    assert all(
        page.path.startswith(f"{co.MATERIALIZATION_ROOT}/{component}/")
        for page in pages
    )
    assert _build(state, run_id=run_id) == envelope
    by_path = {page.path: page.descriptor() for page in pages}
    for descriptor in _overflow_record(envelope)["index_pages"]:
        assert by_path[descriptor["path"]] == dict(descriptor)


def _duplicate_evaluation(state):
    (record,) = state.closure_evaluations.values()
    duplicate = replace(record, record_id=f"{record.record_id}-duplicate")
    return replace(
        state,
        closure_evaluations={
            **state.closure_evaluations,
            duplicate.record_id: duplicate,
        },
    )


def _foreign_plan(state):
    (record,) = state.closure_evaluations.values()
    foreign = replace(
        record,
        selected_plan_ref=replace(
            record.selected_plan_ref,
            plan_id=f"{record.selected_plan_ref.plan_id}-foreign",
        ),
    )
    return replace(state, closure_evaluations={foreign.record_id: foreign})


def _missing_behavior(state):
    (record,) = state.closure_evaluations.values()
    missing = replace(record, completion_behavior_id="missing.completion-behavior")
    return replace(state, closure_evaluations={missing.record_id: missing})


@pytest.mark.parametrize(
    "mutate",
    [
        pytest.param(_duplicate_evaluation, id="duplicate-evaluation"),
        pytest.param(_foreign_plan, id="foreign-plan"),
        pytest.param(_missing_behavior, id="missing-behavior"),
    ],
)
def test_authority_mismatch_is_refused_at_every_dispatch_entry_point(
    overflow_state: co.OverflowState, mutate: Callable[[Any], Any]
) -> None:
    state = mutate(_claimed_evaluator(overflow_state, suffix="mismatch"))

    with pytest.raises(DispatchProjectionError) as build_error:
        _build(state, run_id="run-mismatch")
    assert build_error.value.reason == "closure_request_authority_mismatch"

    with pytest.raises(DispatchProjectionError) as pages_error:
        closure_evidence_pages_for_run(state=state, run_id="run-mismatch")
    assert pages_error.value.reason == "closure_request_authority_mismatch"

    with pytest.raises(DispatchProjectionError) as uses_error:
        closure_request_uses_evidence_pages(state=state, run_id="run-mismatch")
    assert uses_error.value.reason == "closure_request_authority_mismatch"


def _with_snapshot(state, snapshot: Any, *, extra: dict[str, Any] | None = None):
    """Replace (or drop, when None) the durable snapshot and merge ``extra`` keys."""
    item = co.closure_request_item(state)
    payload = dict(item.payload)
    if snapshot is None:
        assert SNAPSHOT_KEY in payload
        del payload[SNAPSHOT_KEY]
    else:
        payload[SNAPSHOT_KEY] = snapshot
    payload.update(extra or {})
    corrupted = replace(item, payload=payload)
    return replace(
        state, work_items={**state.work_items, item.ref.work_item_id: corrupted}
    )


def _snapshot_with_kind(state, kind: str):
    snapshot = dict(co.durable_snapshot(state))
    snapshot["record_kind"] = kind
    return _with_snapshot(state, snapshot)


def _oversized_text() -> str:
    """Text longer than the inline limit, so the payload must use evidence pages."""
    return "x" * (2 * co.REQUEST_LIMIT)


@pytest.mark.parametrize(
    "corrupt",
    [
        pytest.param(
            lambda state: _snapshot_with_kind(state, "corrupted_kind"),
            id="wrong-kind",
        ),
        pytest.param(
            lambda state: _with_snapshot(state, _oversized_text()),
            id="snapshot-scalar",
        ),
        pytest.param(
            lambda state: _with_snapshot(
                state,
                co.durable_snapshot(state),
                extra={OVERFLOW_KEY: {"stale": True}},
            ),
            id="overflow-key-present",
        ),
        pytest.param(
            lambda state: _with_snapshot(
                state, None, extra={"padding": _oversized_text()}
            ),
            id="snapshot-missing-oversized",
        ),
    ],
)
def test_corrupted_durable_snapshot_yields_an_overflow_reason(
    overflow_state: co.OverflowState, corrupt: Callable[[Any], Any]
) -> None:
    state = corrupt(_claimed_evaluator(overflow_state, suffix="corrupt"))

    with pytest.raises(DispatchProjectionError) as error:
        _build(state, run_id="run-corrupt")
    assert error.value.reason == "closure_request_overflow_snapshot_invalid"

    with pytest.raises(DispatchProjectionError) as pages_error:
        closure_evidence_pages_for_run(state=state, run_id="run-corrupt")
    assert pages_error.value.reason == "closure_request_overflow_snapshot_invalid"


@pytest.mark.parametrize(
    "corrupt",
    [
        pytest.param(lambda state: _with_snapshot(state, None), id="snapshot-removed"),
        pytest.param(
            lambda state: _with_snapshot(state, "not-a-mapping"),
            id="snapshot-scalar",
        ),
    ],
)
def test_inline_sized_snapshot_corruption_is_refused_by_durable_load(
    overflow_state: co.OverflowState, tmp_path: Path, corrupt: Callable[[Any], Any]
) -> None:
    """Inline-sized corrupted snapshots never reach dispatch as overflow requests.

    Dispatch passes inline-sized payloads through unchanged, so for these shapes the
    reachable refusal is the durable closure-evidence anchor check on reload.
    """
    control_dir = tmp_path / "control"
    control_dir.mkdir()
    control = persist_and_load_runtime_state(
        control_dir, _claimed_evaluator(overflow_state, suffix="durable")
    )
    assert (
        closure_request_uses_evidence_pages(state=control, run_id="run-durable") is True
    )

    corrupt_dir = tmp_path / "corrupt"
    corrupt_dir.mkdir()
    state = corrupt(_claimed_evaluator(overflow_state, suffix="durable"))
    with pytest.raises(StorageIntegrityError, match="evidence anchor"):
        persist_and_load_runtime_state(corrupt_dir, state)
