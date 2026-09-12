"""Real admitted Core runtime and exact control request fixtures."""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

from cli.test_cli_bounded_execution_unit import (
    _ready_state_with_selected_codex_authority,
    _runtime,
)
from millrace.adapters.cli.context import OpenRuntimeContext, transition_context
from millrace.contracts.controls import ControlRequest
from millrace.contracts.state import DaemonBudgetEpochRecord, RunRecord
from millrace.contracts.transition import ClaimWork, CreateRunnerSession
from millrace.kernel import apply, decide


def runtime_with_run(
    tmp_path: Path, *, session: bool = False, bound: bool = False
) -> tuple[OpenRuntimeContext, RunRecord]:
    if bound:
        from cli.test_cli_bounded_execution_unit import _ready_bound_context_state

        state, _ = _ready_bound_context_state()
    else:
        state, _ = _ready_state_with_selected_codex_authority()
    runtime = _runtime(tmp_path, state)
    activation_id = next(iter(state.activations))
    claim = ClaimWork("claim-a", activation_id=activation_id)
    state = apply(
        state,
        decide(
            state,
            claim,
            transition_context(command="test", input_id_value=claim.input_id),
        ),
    )
    run = next(iter(state.runs.values()))
    if session:
        create = CreateRunnerSession(
            "session-create",
            run_ref=run.run_ref,
            session_id="session-a",
            session_fencing_token="session-fence-a",
            created_at=100,
            explicit_retry_intent=False,
        )
        state = apply(
            state,
            decide(
                state,
                create,
                transition_context(command="test", input_id_value=create.input_id),
            ),
        )
        run = state.runs[run.run_ref.run_id]
    runtime.store.persist_runtime_state(state, runtime.cas_store)
    return runtime, run


def request_for(
    runtime: OpenRuntimeContext, run: RunRecord, **changes: object
) -> ControlRequest:
    identity = runtime.store.control_identity()
    state = runtime.store.load_runtime_state(runtime.cas_store)
    run = state.runs[run.run_ref.run_id]
    session = state.runner_sessions.get(run.current_session_id or "")
    control = state.run_execution_controls.get(run.run_ref.run_id)
    payload = {
        "contract_id": "millrace.core.controls",
        "contract_revision": 1,
        "action": "runs.pause",
        "caller_id": "caller",
        "actor_id": "operator",
        "operation_id": str(uuid4()),
        "reason": "private reason",
        "correlation_id": "test",
        "target": {
            **{
                key: identity[key]
                for key in ("workspace_id", "instance_id", "store_epoch")
            },
            "run_id": run.run_ref.run_id,
            "plan_fingerprint": str(run.run_ref.plan_ref.authority_fingerprint),
            "run_generation": run.run_ref.generation,
            "run_fencing_token": run.run_ref.fencing_token,
            "expected_control_revision": 0
            if control is None
            else control.control_revision,
            "expected_session": None
            if session is None
            else {
                "session_id": session.session_id,
                "dispatch_generation": session.dispatch_generation,
                "session_fencing_token": session.session_fencing_token,
                "state": session.state,
            },
            "last_dispatch_generation": run.last_dispatch_generation,
        },
    }
    payload.update(changes)
    return ControlRequest.parse(json.dumps(payload))


def pause(runtime: OpenRuntimeContext, run: RunRecord) -> dict:
    return runtime.store.execute_run_control(
        request_for(runtime, run),
        runtime.cas_store,
        supported_adapter_kinds=frozenset({"codex", "millforge"}),
    )


def epoch_for(
    runtime: OpenRuntimeContext, run: RunRecord, **changes: object
) -> DaemonBudgetEpochRecord:
    fields = dict(
        budget_id="budget-a",
        workspace_path=str(runtime.paths.workspace_path),
        selected_plan_ref=run.run_ref.plan_ref,
        max_wall_seconds=None,
        max_invocations=2,
        max_total_tokens=None,
        started_at=100,
        wall_deadline=None,
        last_observed_at=100,
    )
    fields.update(changes)
    epoch = DaemonBudgetEpochRecord(**fields)
    return runtime.store.create_or_resume_daemon_budget_epoch(epoch)


def add_run(runtime: OpenRuntimeContext, label: str) -> RunRecord:
    from millrace.contracts.ids import QueueFamilyId
    from millrace.contracts.transition import EnqueueWork

    state = runtime.store.load_runtime_state(runtime.cas_store)
    enqueue = EnqueueWork(
        f"enqueue-{label}",
        queue_family_id=QueueFamilyId("prompt"),
        payload={"prompt_id": label, "body": "Independent work"},
    )
    for transition in (enqueue,):
        state = apply(
            state,
            decide(
                state,
                transition,
                transition_context(command="test", input_id_value=transition.input_id),
            ),
        )
    activation = next(
        item
        for item in state.activations.values()
        if item.created_by_input_id == enqueue.input_id
    )
    claim = ClaimWork(f"claim-{label}", activation_id=activation.activation_id)
    state = apply(
        state,
        decide(
            state,
            claim,
            transition_context(command="test", input_id_value=claim.input_id),
        ),
    )
    run = next(
        item
        for item in state.runs.values()
        if item.created_by_input_id == claim.input_id
    )
    create = CreateRunnerSession(
        f"create-{label}",
        run_ref=run.run_ref,
        session_id=f"session-{label}",
        session_fencing_token=f"fence-{label}",
        created_at=100,
        explicit_retry_intent=False,
    )
    state = apply(
        state,
        decide(
            state,
            create,
            transition_context(command="test", input_id_value=create.input_id),
        ),
    )
    runtime.store.persist_runtime_state(state, runtime.cas_store)
    return state.runs[run.run_ref.run_id]


def start_intent(
    runtime: OpenRuntimeContext, run: RunRecord, *, budget_id: str | None = None
):
    from millrace.adapters.cli.session_completion import _persist_transition
    from millrace.contracts.transition import AdvanceRunnerSession

    session = runtime.store.load_runtime_state(runtime.cas_store).runner_sessions[
        run.current_session_id
    ]
    transition = AdvanceRunnerSession(
        f"start-{session.session_id}",
        run_ref=run.run_ref,
        session_id=session.session_id,
        dispatch_generation=session.dispatch_generation,
        session_fencing_token=session.session_fencing_token,
        expected_state="created",
        next_state="starting",
        occurred_at=101,
    )
    return _persist_transition(
        runtime,
        transition,
        driving_budget_id=budget_id,
        driving_budget_clock=lambda: 101,
    )


def lineage_hold_runtime(tmp_path: Path):
    """Selected fanout siblings: one held; the other may establish quarantine."""
    from copy import deepcopy

    from millrace.contracts.ids import QueueFamilyId
    from millrace.contracts.transition import (
        AdmitPlan,
        EnqueueWork,
        InitializeWorkspace,
        SelectDefaultPlan,
    )
    from millrace.kernel import empty_runtime_state
    from millrace.testing import (
        decide_with_fake_runner_completion,
        deterministic_context,
    )
    from substrate.test_persistence_integrity_refusals import _apply_generic_observation
    from support import generic_admission as admission
    from support import generic_fanout as fanout

    source = admission.source()
    source["fanout_declarations"] = deepcopy(fanout.source()["fanout_declarations"])
    declaration = source["fanout_declarations"][0]
    declaration.update(
        source_action_id="admission.complete",
        target_route_id="admission.child_route",
        source_state_policy="accepted_terminal_observation",
        dependency_policy="none",
    )
    action = next(
        item
        for item in source["terminal_actions"]
        if item["id"] == "admission.complete"
    )
    action.update(
        kind="route",
        target_stage_kind_id=admission.PARENT_STAGE_ID,
        target_graph_node_id=admission.PARENT_NODE_ID,
        runner_binding_id=admission.RUNNER_ID,
        emitted_queue_family_id="parent",
        payload_projection={"kind": "source", "path": ("artifact_payload",)},
    )
    from millrace.compiler import compile_workflow

    result = compile_workflow(source, selected_runner_policy=admission._CODEX_POLICY)
    assert result.plan is not None, result.diagnostics
    plan, fingerprint = admission.compile_plan(source)
    state = empty_runtime_state()
    for transition in (
        InitializeWorkspace("init"),
        AdmitPlan("admit", selected_plan=plan, authority_fingerprint=fingerprint),
        SelectDefaultPlan("select", authority_fingerprint=fingerprint),
        EnqueueWork(
            "parent",
            queue_family_id=QueueFamilyId("parent"),
            payload=fanout.packet_payload(),
        ),
        ClaimWork("claim-parent", activation_id="activation-parent"),
    ):
        context = deterministic_context(
            transition_id=f"transition-{transition.input_id}",
            work_item_id=f"work-{transition.input_id}",
            activation_id=f"activation-{transition.input_id}",
            run_id=f"run-{transition.input_id}",
            claim_id=f"claim-{transition.input_id}",
            fencing_token=f"fence-{transition.input_id}",
        )
        decision = decide_with_fake_runner_completion(state, transition, context)
        assert decision.accepted, decision.refusal
        state = apply(state, decision)
    state = _apply_generic_observation(
        state,
        plan,
        fingerprint,
        run_id="run-claim-parent",
        action_id="admission.complete",
        input_id="fanout",
        context=deterministic_context(
            transition_id="fanout", activation_id="continuation"
        ),
    )
    children = [
        item
        for item in state.activations.values()
        if str(item.stage_kind_id) == admission.CHILD_STAGE_ID
    ]
    assert len(children) == 2
    for label, activation in zip(("held", "other"), children, strict=True):
        claim = ClaimWork(f"claim-{label}", activation_id=activation.activation_id)
        decision = decide(
            state,
            claim,
            deterministic_context(
                transition_id=f"claim-{label}",
                run_id=f"run-{label}",
                claim_id=f"claim-{label}",
                fencing_token=f"fence-{label}",
            ),
        )
        assert decision.accepted, decision.refusal
        state = apply(state, decision)
    runtime = _runtime(tmp_path, state)
    return runtime, state.runs["run-held"], plan, fingerprint
