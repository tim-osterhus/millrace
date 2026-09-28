"""One foreground zero-key journey through the actual Core compiler/runtime."""

from __future__ import annotations

import json
import os
import signal
import sys
import uuid
from importlib import metadata
from typing import Any

from millrace.adapters.cli import run as runmod
from millrace.adapters.cli import session_coordinator as sessions
from millrace.adapters.cli.context import persist_runner_transition
from millrace.adapters.cli.demo_adapter import _DemoAdapter
from millrace.adapters.cli.demo_inventory import (
    current_package,
    import_select,
    installed_identity,
)
from millrace.adapters.cli.demo_owned_runtime import (
    OwnedRuntime,
    initialize_owned_runtime,
)
from millrace.adapters.cli.demo_workspace import CommandHome, demo_root, strict_id
from millrace.adapters.cli.lifecycle import (
    lifecycle_has_pending_work,
    run_lifecycle_transition_once,
)
from millrace.adapters.cli.setup_actions import SetupActionService
from millrace.adapters.cli.setup_interactive import confirm_demo_trust
from millrace.adapters.runner_contract import (
    AdapterInvocationRequest,
    AdapterLocalConfig,
)
from millrace.compiler import authority_fingerprint
from millrace.contracts import QueueFamilyId
from millrace.contracts.demo import ADAPTER_KIND, PLAN_FINGERPRINT, DemoRefusal, serial
from millrace.contracts.setup import SetupRefusal, SetupRequest, digest
from millrace.contracts.transition import (
    AdmitPlan,
    ClaimWork,
    EnqueueWork,
    InitializeWorkspace,
    OperatorCloseWait,
    OperatorReviseWait,
    SelectDefaultPlan,
)
from millrace.kernel import empty_runtime_state
from millrace.operator.dispatch import (
    list_ready_dispatch_candidates,
)

_pending_signal = 0
_waiting_for_input = False


def _signal_requested(number: int, frame: Any) -> None:
    global _pending_signal
    _pending_signal = number
    if _waiting_for_input:
        _checkpoint()


def _checkpoint() -> None:
    if _pending_signal == signal.SIGINT:
        raise KeyboardInterrupt
    if _pending_signal == signal.SIGTERM:
        raise DemoRefusal("operator_cancelled")


def _transition(runtime: OwnedRuntime, transition: Any) -> None:
    if persist_runner_transition(runtime, transition) is None:
        raise DemoRefusal("runtime_transition_refused")


def completion_evidence(runtime: OwnedRuntime) -> dict[str, Any]:
    state = runtime.store.load_runtime_state(runtime.cas_store)
    if (
        lifecycle_has_pending_work(state)
        or list_ready_dispatch_candidates(state).candidates
    ):
        raise DemoRefusal("pending_runtime_work")
    if (
        set(state.admitted_plans) != {PLAN_FINGERPRINT}
        or len(state.operator_waits) != 1
    ):
        raise DemoRefusal("completion_authority_mismatch")
    wait = next(iter(state.operator_waits.values()))
    if wait.status != "resolved" or wait.resolution_kind not in {
        "close_recorded_source",
        "revise_recorded_source",
    }:
        raise DemoRefusal("unresolved_operator_wait")
    stages = [
        "intake",
        "producer_a",
        "producer_b",
        "verify_first",
        "repair",
        "verify_pass",
        "wait",
    ]
    if wait.resolution_kind == "revise_recorded_source":
        stages += ["revise", "close"]
    if sorted(str(r.stage_kind_id) for r in state.runs.values()) != sorted(stages):
        raise DemoRefusal("incomplete_demo_graph")
    action = "demo.close.closed" if "close" in stages else "demo.wait.decide"
    if not any(str(x.action_id) == action for x in state.closed_work_items.values()):
        raise DemoRefusal("legal_closure_missing")
    if len(state.fanout_records) != 2 or len(state.runner_sessions) != len(stages):
        raise DemoRefusal("graph_evidence_mismatch")
    if (
        any(x.state != "completed" for x in state.runner_sessions.values())
        or len(state.runner_session_completions) != len(stages)
        or any(
            x.cleanup_disposition != "complete" or x.terminal_state != "completed"
            for x in state.runner_session_completions.values()
        )
    ):
        raise DemoRefusal("session_cleanup_unestablished")
    if (
        state.quarantines
        or state.lineage_quarantines
        or state.operator_interventions
        or state.refusals
    ):
        raise DemoRefusal("unclean_runtime_aftermath")
    if wait.resolved_input_id not in state.receipts:
        raise DemoRefusal("missing_resolution_receipt")
    from millrace.contracts.setup import digest

    return {
        "state_sha256": digest(serial(state)),
        "plan": PLAN_FINGERPRINT,
        "resolution": wait.resolution_kind,
        "sessions": len(stages),
        "pending_lifecycle": False,
    }


def _adapter(
    runtime: OwnedRuntime,
    home: CommandHome,
    demo: str,
    identity: dict[str, Any],
    package: dict[str, Any],
) -> _DemoAdapter:
    def validate(request: AdapterInvocationRequest) -> None:
        home.validate_lock(demo, identity)
        if installed_identity() != identity:
            raise DemoRefusal("installed_command_changed")
        if current_package(runtime.store, runtime.cas_store) != package:
            raise DemoRefusal("admitted_package_changed")
        state = runtime.store.load_runtime_state(runtime.cas_store)
        session = state.runner_sessions.get(request.session_id)
        if session is None or (
            session.dispatch_generation,
            session.session_fencing_token,
        ) != (request.dispatch_generation, request.session_fencing_token):
            raise DemoRefusal("session_fencing_mismatch")
        run = state.runs.get(session.run_id)
        if run is None or run.current_session_id != session.session_id:
            raise DemoRefusal("current_run_mismatch")
        admitted = state.admitted_plans.get(run.run_ref.plan_ref.authority_fingerprint)
        if (
            admitted is None
            or authority_fingerprint(admitted.selected_plan) != PLAN_FINGERPRINT
        ):
            raise DemoRefusal("admitted_plan_mismatch")
        active = runmod._ActiveRun(
            state.activations[run.activation_id],
            run,
            admitted.selected_plan,
            ADAPTER_KIND,
        )
        expected = runmod._session_invocation_request(
            runtime,
            active=active,
            selected_kind=ADAPTER_KIND,
            effective_config=AdapterLocalConfig(adapters={ADAPTER_KIND: adapter}),
            session=session,
        )
        if request != expected or request.selected_adapter_kind != ADAPTER_KIND:
            raise DemoRefusal("session_request_authority_mismatch")

    def retain(session_id: str, evidence: dict[str, Any]) -> None:
        record = home.verify(demo, identity)
        retained = dict(record["runtime"])
        outcomes = dict(retained.get("outcomes", {}))
        if session_id in outcomes:
            raise DemoRefusal("duplicate_retained_outcome")
        payload = json.dumps(evidence, sort_keys=True, separators=(",", ":")).encode()
        outcomes[session_id] = runtime.cas_store.put_bytes(payload)
        retained.update(outcomes=outcomes, owned_identity=runtime.retained_identity())
        home.update(demo, identity, state="running", runtime=retained)

    def read(session_id: str) -> dict[str, Any] | None:
        record = home.verify(demo, identity)
        key = record["runtime"].get("outcomes", {}).get(session_id)
        if key is None:
            return None
        value: dict[str, Any] = json.loads(runtime.cas_store.get_bytes(key))
        return value

    def before_poll(request: AdapterInvocationRequest) -> bool:
        if _pending_signal == signal.SIGTERM:
            sessions.request_operator_cancellation(
                runtime,
                run_id=request.dispatch_envelope.run_id,
                request_id="demo-signal-cancel:" + request.session_id,
                actor_id=f"operator:uid:{os.getuid()}",
            )
            return False
        _checkpoint()
        return True

    adapter = _DemoAdapter(validate, retain, read, before_poll)
    return adapter


def _execute(runtime: OwnedRuntime, adapter: _DemoAdapter, run: Any) -> Any:
    state = runtime.store.load_runtime_state(runtime.cas_store)
    plan = state.admitted_plans[
        run.run_ref.plan_ref.authority_fingerprint
    ].selected_plan
    active = runmod._ActiveRun(
        state.activations[run.activation_id], run, plan, ADAPTER_KIND
    )
    return sessions.execute_runner_session(
        runtime,
        run_ref=run.run_ref,
        adapter=adapter,
        request_factory=lambda session: runmod._session_invocation_request(
            runtime,
            active=active,
            selected_kind=ADAPTER_KIND,
            effective_config=AdapterLocalConfig(adapters={ADAPTER_KIND: adapter}),
            session=session,
        ),
        explicit_retry_intent=False,
        prepare_created_session=runmod._prepare_created_session_callback(
            runtime, active=active
        ),
    )


def _drive(runtime: OwnedRuntime, adapter: _DemoAdapter) -> None:
    # Resume the admitted run first; never claim again for an existing session.
    state = runtime.store.load_runtime_state(runtime.cas_store)
    for run in state.runs.values():
        session = (
            state.runner_sessions.get(run.current_session_id)
            if run.current_session_id
            else None
        )
        if session is None or session.state not in {
            "completed",
            "interrupted",
            "failed",
        }:
            result = _execute(runtime, adapter, run)
            if not result.accepted:
                raise DemoRefusal("demo_pending_unknown")
    for _ in range(64):
        _checkpoint()
        lifecycle = run_lifecycle_transition_once(runtime)
        if lifecycle.code != "no_ready_work":
            if not lifecycle.accepted:
                raise DemoRefusal("demo_lifecycle_refused")
            continue
        state = runtime.store.load_runtime_state(runtime.cas_store)
        ready = list_ready_dispatch_candidates(state)
        if not ready.candidates:
            return
        candidate = ready.candidates[0]
        _transition(
            runtime,
            ClaimWork(
                "demo-claim-" + uuid.uuid4().hex, activation_id=candidate.activation_id
            ),
        )
        state = runtime.store.load_runtime_state(runtime.cas_store)
        run = next(
            r for r in state.runs.values() if r.activation_id == candidate.activation_id
        )
        print("Demo stage: " + str(run.stage_kind_id), file=sys.stderr, flush=True)
        result = _execute(runtime, adapter, run)
        if not result.accepted:
            state = runtime.store.load_runtime_state(runtime.cas_store)
            if state.runner_session_cancellation_requests:
                raise DemoRefusal("operator_cancelled")
            raise DemoRefusal("demo_session_" + result.code)
    raise DemoRefusal("demo_execution_bound")


def _prepare(home: CommandHome, demo: str, identity: dict[str, Any]) -> OwnedRuntime:
    record = home.verify(demo, identity)
    fd = home.reopen_verified(demo, record)
    try:
        os.mkdir("runtime", 0o700, dir_fd=fd)
        root = home.path / "workspaces" / demo / "runtime"
        for path in [
            ".millrace",
            ".millrace/cas",
            ".millrace/cas/sha256",
            "docs",
            "docs/reviews",
        ]:
            (root / path).mkdir(mode=0o700)
        (root / "docs/context.md").write_text(
            "Synthetic demo context; no user repository content.\n"
        )
        (root / "docs/context.md").chmod(0o600)
    finally:
        os.close(fd)
    runtime = initialize_owned_runtime(
        root, validate_owner=lambda: home.validate_lock(demo, identity)
    )
    runtime.store.persist_runtime_state(empty_runtime_state(), runtime.cas_store)
    plan, package = import_select(runtime.store, runtime.cas_store)
    for transition in (
        InitializeWorkspace("demo-init"),
        AdmitPlan(
            "demo-admit", selected_plan=plan, authority_fingerprint=PLAN_FINGERPRINT
        ),
        SelectDefaultPlan("demo-default", authority_fingerprint=PLAN_FINGERPRINT),
    ):
        _transition(runtime, transition)
    home.update(
        demo,
        identity,
        state="running",
        runtime={
            "owned_identity": runtime.retained_identity(),
            "package": package,
            "outcomes": {},
        },
    )
    return runtime


def run_demo(
    *,
    keep: bool,
    auto_confirm: bool,
    resume: str | None,
    interactive_trust: bool = False,
) -> tuple[int, dict[str, Any]]:
    global _waiting_for_input
    if resume is not None:
        strict_id(resume)
    if not auto_confirm and not sys.stdin.isatty():
        raise DemoRefusal("demo_requires_interactive_stdin_or_auto_confirm")
    identity = installed_identity()
    owner = SetupActionService(SetupRequest())
    accepted = owner.resolve_demo_trust()
    if accepted is None:
        if not interactive_trust or not sys.stdin.isatty():
            raise SetupRefusal("demo_trust_required")
        _checkpoint()
        _waiting_for_input = True
        try:
            if confirm_demo_trust(owner) is None:
                raise SetupRefusal("demo_trust_declined")
        finally:
            _waiting_for_input = False
        _checkpoint()
        accepted = owner.resolve_demo_trust()
        if accepted is None:
            raise SetupRefusal("demo_trust_required")
    root = demo_root()
    selection = owner.service.inspect().selection
    if selection.local_config_digest != digest(
        {"profile": "owned-demo-v1", "root": str(root), "installation": identity}
    ):
        raise SetupRefusal("demo_trust_selection_changed")
    _checkpoint()
    home = CommandHome(root, create=not root.exists())
    demo = resume or home.create(identity)
    runtime: OwnedRuntime | None = None
    result: dict[str, Any] = {
        "command": "demo",
        "demo_id": demo,
        "identity": identity,
        "trust_acceptance": accepted.to_wire(),
        "workspace_path": str(root / "workspaces" / demo / "runtime"),
        "workspace_disposition": "retained",
    }
    exit_code = 1
    try:
        home.acquire(demo, identity)
        record = home.verify(demo, identity)
        _checkpoint()
        runtime = (
            _prepare(home, demo, identity)
            if record["runtime"] is None
            else home.open_runtime(demo, identity, record)
        )
        record = home.verify(demo, identity)
        state = runtime.store.load_runtime_state(runtime.cas_store)
        _checkpoint()
        if not state.work_items:
            _transition(
                runtime,
                EnqueueWork(
                    "demo-enqueue",
                    queue_family_id=QueueFamilyId("intake"),
                    payload={"synthetic": True, "bundle_id": "synthetic-bundle"},
                ),
            )
        adapter = _adapter(runtime, home, demo, identity, record["runtime"]["package"])
        _drive(runtime, adapter)
        state = runtime.store.load_runtime_state(runtime.cas_store)
        waits = [w for w in state.operator_waits.values() if w.status == "active"]
        if waits:
            if len(waits) != 1:
                raise DemoRefusal("unexpected_operator_waits")
            _checkpoint()
            if auto_confirm:
                decision = "confirm"
            else:
                print(
                    "Confirm, revise once, or cancel? [confirm/revise/cancel] ",
                    file=sys.stderr,
                    end="",
                    flush=True,
                )
                _waiting_for_input = True
                try:
                    decision = sys.stdin.readline().strip().lower()
                finally:
                    _waiting_for_input = False
                _checkpoint()
            if decision == "cancel":
                raise DemoRefusal("operator_cancelled_at_wait")
            if decision not in {"confirm", "revise"}:
                raise DemoRefusal("operator_decision_invalid")
            wait = waits[0]
            operation = (
                OperatorCloseWait if decision == "confirm" else OperatorReviseWait
            )
            _transition(
                runtime,
                operation(
                    "demo-decision",
                    selected_plan_ref=wait.selected_plan_ref,
                    wait_id=wait.wait_id,
                    lineage_id=wait.lineage_id,
                    actor_id="synthetic-demo-auto-confirm"
                    if auto_confirm
                    else f"operator:uid:{os.getuid()}",
                    actor_kind="local_operator",
                    payload={}
                    if decision == "confirm"
                    else {"synthetic": True, "bundle_id": "synthetic-bundle"},
                ),
            )
            _drive(runtime, adapter)
        _checkpoint()
        evidence = completion_evidence(runtime)
        state = runtime.store.load_runtime_state(runtime.cas_store)
        from millrace.adapters.cli.doctor import _runner_session_diagnostics
        from millrace.adapters.cli.status import _hydration_totals_by_session

        result.update(
            doctor=_runner_session_diagnostics(
                state, hydration_totals=_hydration_totals_by_session(runtime, state)
            ),
            creation_receipt=home.verify(demo, identity),
            work_items=serial(state.work_items),
            activations=serial(state.activations),
            lineage_ids=sorted({str(r.lineage_id) for r in state.activations.values()}),
            fanout=serial(state.fanout_records),
            outcome="demo_succeeded",
            completion=evidence,
            package_identity=record["runtime"]["package"],
            run_ids=list(state.runs),
            sessions=serial(state.runner_sessions),
            waits=serial(state.operator_waits),
            receipts=serial(state.receipts),
            trace=serial(state.traces),
        )
        exit_code = 0
    except KeyboardInterrupt:
        result.update(outcome="demo_interrupted", reason="handled_interrupt")
        exit_code = 130
    except ValueError as exc:
        result.update(
            outcome="demo_cancelled" if "cancel" in str(exc) else "demo_blocked",
            reason=str(exc),
        )
    except Exception as exc:
        result.update(
            outcome="demo_failed", reason=type(exc).__name__ + ": " + str(exc)
        )
    finally:
        if runtime is not None:
            state = runtime.store.load_runtime_state(runtime.cas_store)
            result.setdefault("sessions", serial(state.runner_sessions))
            result.setdefault("waits", serial(state.operator_waits))
            result.setdefault(
                "cancellations", serial(state.runner_session_cancellation_requests)
            )
            record = home.verify(demo, identity)
            retained = {
                **record["runtime"],
                "owned_identity": runtime.retained_identity(),
            }
            runtime.close()
            home.update(
                demo,
                identity,
                state="cancelled"
                if result.get("outcome") == "demo_cancelled"
                else "interrupted",
                runtime=retained,
            )
        if demo in home.locks:
            if exit_code == 0:
                home.complete(demo, identity)
                if not keep:
                    try:
                        home.cleanup(demo, identity)
                        result["workspace_disposition"] = "removed"
                    except (ValueError, OSError, KeyboardInterrupt) as exc:
                        result.update(
                            outcome="demo_cleanup_incomplete", reason=str(exc)
                        )
                        exit_code = 1
            if demo in home.locks:
                home.release(demo, identity)
        home.close()
    return exit_code, result


def dispatch_demo(namespace: Any) -> int:
    global _pending_signal
    _pending_signal = 0
    previous = {
        sig: signal.signal(sig, _signal_requested)
        for sig in (signal.SIGINT, signal.SIGTERM)
    }
    try:
        code, result = run_demo(
            keep=namespace.keep,
            auto_confirm=namespace.auto_confirm,
            resume=namespace.resume,
            interactive_trust=not namespace.json,
        )
    except KeyboardInterrupt:
        code, result = (
            130,
            {
                "command": "demo",
                "outcome": "demo_interrupted",
                "reason": "handled_interrupt_before_runtime",
            },
        )
    except (ValueError, OSError, metadata.PackageNotFoundError) as exc:
        code, result = (
            2,
            {
                "command": "demo",
                "outcome": "demo_preflight_refused",
                "reason": str(exc),
                "safe_next_action": (
                    "millrace setup --interactive "
                    "(add --wheelhouse PATH for an unpublished candidate)"
                ),
            },
        )
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)
    if namespace.json:
        print(json.dumps(result, sort_keys=True))
    else:
        print(
            result["outcome"] + ": " + result.get("reason", result.get("demo_id", ""))
        )
        if "safe_next_action" in result:
            print("Next: " + result["safe_next_action"])
        if "workspace_path" in result:
            print(
                "Workspace "
                + result["workspace_disposition"]
                + ": "
                + result["workspace_path"]
            )
    return code
