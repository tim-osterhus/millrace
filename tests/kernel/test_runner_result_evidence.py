from __future__ import annotations

from collections.abc import Mapping
from types import SimpleNamespace
from typing import cast

import pytest

from kernel.kernel_ping_scenarios import bootstrap_to_taskmaster_claim
from millrace.contracts.compiled_plan import AuthorityValue, TerminalActionDeclaration
from millrace.contracts.transition import RunnerResultObserved
from millrace.kernel import decision as decision_module
from millrace.kernel import observation_policy
from millrace.kernel.terminal_actions import (
    TerminalActionRefusal,
    _route_target_fields_or_refusal,
)
from millrace.testing import decide_with_fake_runner_completion as decide
from support import generic_admission
from support.kernel_ping import (
    compile_kernel_ping,
    kernel_ping_context,
    runner_observation,
    task_artifact_payload,
)


@pytest.mark.parametrize(
    "artifact_candidate",
    (None, {}),
    ids=("absent", "present-empty"),
)
def test_kernel_refuses_absent_and_empty_artifact_candidates_without_crashing(
    artifact_candidate: Mapping[str, AuthorityValue] | None,
) -> None:
    plan, fingerprint = compile_kernel_ping()
    state = bootstrap_to_taskmaster_claim(plan, fingerprint)
    valid_observation = runner_observation(
        state=state,
        plan=plan,
        fingerprint=fingerprint,
        run_id="run-taskmaster",
        action_id="kernel_ping.route_taskmaster_success",
        input_id="observe-candidate-shape",
        artifact_payload=task_artifact_payload(),
    )
    payload = dict(valid_observation.payload)
    payload["artifact_payload"] = artifact_candidate
    observation = RunnerResultObserved(
        "observe-candidate-shape",
        run_id="run-taskmaster",
        payload=payload,
        observed_at=None,
    )

    decision = decide(
        state,
        observation,
        kernel_ping_context("observe-candidate-shape"),
    )

    assert decision.accepted is False
    assert decision.refusal is not None
    assert decision.refusal.reason == "invalid_artifact_payload"


def test_kernel_preserves_observation_candidate_at_terminal_action_seam(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[tuple[object, object]] = []

    def refuse_terminal_action(**kwargs: object) -> TerminalActionRefusal:
        observed.append(
            (
                kwargs["observation_payload"],
                cast(RunnerResultObserved, kwargs["transition_input"])
                .payload["artifact_payload"],
            )
        )
        return TerminalActionRefusal(
            reason="test_terminal_action_refusal",
            action=cast(TerminalActionDeclaration, kwargs["action"]),
        )

    monkeypatch.setattr(
        decision_module,
        "resolve_terminal_action",
        refuse_terminal_action,
    )

    plan, fingerprint = compile_kernel_ping()
    state = bootstrap_to_taskmaster_claim(plan, fingerprint)
    artifact_payload = task_artifact_payload()
    candidates: tuple[Mapping[str, AuthorityValue] | None, ...] = (None, {})
    for index, observation_candidate in enumerate(candidates):
        valid_observation = runner_observation(
            state=state,
            plan=plan,
            fingerprint=fingerprint,
            run_id="run-taskmaster",
            action_id="kernel_ping.route_taskmaster_success",
            input_id=f"observe-candidate-shape-{index}",
            artifact_payload=artifact_payload,
        )
        payload = dict(valid_observation.payload)
        payload["observation_payload"] = observation_candidate
        observation = RunnerResultObserved(
            f"observe-candidate-shape-{index}",
            run_id="run-taskmaster",
            payload=payload,
            observed_at=None,
        )

        decision = decide(
            state,
            observation,
            kernel_ping_context(f"observe-candidate-shape-{index}"),
        )

        assert decision.accepted is False
        assert decision.refusal is not None
        assert decision.refusal.reason == "test_terminal_action_refusal"

    assert observed[0][0] is None
    assert observed[1][0] == {}
    assert observed[1][0] is not None
    assert observed[0][1] == artifact_payload
    assert observed[1][1] == artifact_payload


@pytest.mark.parametrize(
    "observation_candidate",
    (None, {}),
    ids=("absent", "present-empty"),
)
def test_dynamic_route_projection_handles_absent_observation_candidate(
    observation_candidate: Mapping[str, AuthorityValue] | None,
) -> None:
    plan, _fingerprint = generic_admission.compile_plan()
    action = next(
        action
        for action in plan.terminal_actions
        if str(action.id) == generic_admission.DYNAMIC_ROUTE_ACTION_ID
    )

    result = _route_target_fields_or_refusal(
        action=action,
        observation_payload=observation_candidate,
    )

    if observation_candidate is None:
        assert isinstance(result, TerminalActionRefusal)
        assert result.reason == "invalid_dynamic_route_target"
    else:
        assert result == (
            action.target_stage_kind_id,
            action.target_graph_node_id,
            action.emitted_queue_family_id,
            action.runner_binding_id,
        )


@pytest.mark.parametrize(
    ("observation_payload", "artifact_payload"),
    ((None, {"artifact": "present"}), ({}, {}), ({}, None)),
    ids=("observation-absent", "present-empty", "artifact-absent"),
)
def test_create_incident_projection_preserves_absent_and_empty_candidates(
    monkeypatch: pytest.MonkeyPatch,
    observation_payload: Mapping[str, AuthorityValue] | None,
    artifact_payload: Mapping[str, AuthorityValue] | None,
) -> None:
    contexts: list[tuple[object, object]] = []

    def projection_context(**kwargs: object) -> object:
        contexts.append((kwargs["observation_payload"], kwargs["artifact_payload"]))
        return SimpleNamespace()

    monkeypatch.setattr(
        observation_policy,
        "projection_context_for_run",
        projection_context,
    )
    monkeypatch.setattr(
        observation_policy,
        "evaluate_projection",
        lambda _projection, _context: SimpleNamespace(
            accepted=True,
            value={},
        ),
    )

    authenticated = cast(
        observation_policy.AuthenticatedRunnerObservation,
        SimpleNamespace(
            action=SimpleNamespace(
                action_kind="create_incident_route",
                payload_projection={"kind": "object"},
            ),
            evidence=SimpleNamespace(
                observation_payload=observation_payload,
                artifact_payload=artifact_payload,
            ),
            work_item=SimpleNamespace(),
            run=SimpleNamespace(),
        ),
    )

    result = observation_policy._expected_artifact_payload(authenticated)

    if artifact_payload is None:
        assert result is None
        assert contexts == []
    else:
        assert result == {}
        assert contexts == [(
            {} if observation_payload is None else observation_payload,
            artifact_payload,
        )]
