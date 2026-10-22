"""Production-engine proofs for explicitly injected, data-only execution."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from millrace.router import control
from millrace.router.ownership import new_id


def generic_flow(tmp_path: Path) -> tuple[Path, Path, Path]:
    workflow = tmp_path / "workflow"
    workflow.mkdir()
    project = tmp_path / "project"
    project.mkdir()
    (workflow / "step.py").write_text('print(\'{"outcome":"DONE"}\')\n')
    graph = {
        "format": "millrace-router/v3",
        "limits": {"max_invocations": 3, "max_elapsed_seconds": 60},
        "bindings": {
            "python": {
                "adapter": "command",
                "contract": "millrace-command/v1",
                "executable": sys.executable,
                "config": {},
            }
        },
        "control_files": ["step.py"],
        "planes": {
            "main": {
                "start": "step",
                "workspace": ".",
                "nodes": {
                    "step": {
                        "invoke": {
                            "binding": "python",
                            "timeout_seconds": 5,
                            "config": {"script": "step.py"},
                        },
                        "result": "json-v1",
                        "outcomes": ["DONE"],
                        "on": {"DONE": {"finish": "succeeded"}},
                        "on_error": {
                            "pause": {"reason": "execution_error", "resume": "step"}
                        },
                        "on_unknown": {
                            "pause": {"reason": "unknown", "resume": "step"}
                        },
                    }
                },
            }
        },
    }
    file = workflow / "workflow.json"
    file.write_text(json.dumps(graph))
    return file, project, tmp_path / "state"


def test_validation_and_launch_settings_parity(tmp_path: Path) -> None:
    file, project, home = generic_flow(tmp_path)
    created = control.create_instance(
        file, project, state_home=home, request_id=new_id(), expected_state_version=0
    )
    assert created["status"] == "applied", created
    instance = str(created["instance_id"])
    validation = control.validate_revision(instance, state_home=home)
    assert validation["valid"], validation
    started = control.start_run(
        instance, state_home=home, request_id=new_id(), expected_state_version=1
    )
    assert started["status"] == "applied", started
    result = control.inspect_run(instance, str(started["run_id"]), state_home=home)
    assert result["status"] == "succeeded", result
    assert result["count"] == 1
    launched = result["invocations"][0]["launch_settings"]
    advisory = validation["resolved_nodes"]["main"]["step"]
    assert (
        launched["common"]["producing_revision"]
        == result["invocations"][0]["revision_id"]
    )
    assert advisory["common"]["producing_revision"] == validation["revision_id"]
    assert launched == {
        **advisory,
        "common": {
            **advisory["common"],
            "producing_revision": launched["common"]["producing_revision"],
        },
    }


class CounterAdapter:
    """An external integer command contract with no native event protocol."""

    def __init__(self, bad: str = "") -> None:
        self.bad = bad
        self.calls: list[str] = []

    def validate_configuration(self, config):
        from millrace.router.execution import RouterError

        if any(
            set(value) - {"increment"} for value in (config.runtime, config.workflow)
        ):
            raise RouterError("counter_settings")
        for _, node in config.nodes.values():
            if set(node) - {"number"}:
                raise RouterError("counter_config")

    def plan(self, request):
        from millrace.router.execution import LaunchPlan

        settings = {
            "requested": {"increment": 1},
            "effective": {"increment": 1},
            "provenance": {"increment": {"source": "runtime"}},
            "warnings": [],
        }
        generated = {"number.txt": str(request.node_config.get("number", 6)).encode()}
        if self.bad == "aliases":
            generated = {"a/x": b"1", "a//x": b"2"}
        if self.bad == "actual" and request.invocation_id != "0" * 32:
            generated = {"../escape": b"bad"}
        return LaunchPlan(
            request.invocation_id,
            request.adapter_id,
            request.adapter_contract,
            (request.executable, "-c", "print('DONE')"),
            {},
            generated,
            settings,
            {"number": request.node_config.get("number", 6)},
        )

    def invoke(self, plan, *, cwd, env, deadline, poll, on_started):
        import subprocess

        from millrace.router.execution import AdapterResult

        self.calls.append(plan.invocation_id)
        process = subprocess.Popen(
            plan.argv,
            cwd=cwd,
            env=dict(env),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        on_started(process.pid)
        raw, err = process.communicate(timeout=5)
        assert raw == b"DONE\n"
        evidence = {"counter": plan.details["number"], "id": plan.invocation_id}
        result = AdapterResult(
            b'{"outcome":"DONE"}',
            err,
            "completed",
            "stopped",
            exit_code=process.returncode,
            adapter_evidence=evidence,
        )
        if self.bad in {
            "null_exit",
            "wrong_exit",
            "bool_exit",
            "stdout",
            "stderr",
            "diagnostic",
            "surrogate",
            "interrupted",
            "evidence",
        }:
            from dataclasses import replace

            changes = {
                "null_exit": {"exit_code": None},
                "wrong_exit": {"exit_code": 1},
                "bool_exit": {"exit_code": True},
                "stdout": {"stdout": b"x" * 65537},
                "stderr": {"stderr": b"x" * 65537},
                "diagnostic": {"diagnostic": "x" * 65537},
                "surrogate": {"diagnostic": "\ud800"},
                "interrupted": {"classification": "interrupted"},
                "evidence": {"adapter_evidence": {"id": "wrong"}},
            }
            return replace(result, **changes[self.bad])
        return result

    def validate_completion(self, plan, completion):
        from millrace.router.execution import RouterError

        if completion.evidence != {
            "counter": plan.details["number"],
            "id": plan.invocation_id,
        }:
            raise RouterError("counter_evidence")


def external_flow(tmp_path, adapter):
    file, project, home = generic_flow(tmp_path)
    graph = json.loads(file.read_text())
    graph["bindings"]["python"] = {
        "adapter": "counter",
        "contract": "counter/v1",
        "executable": sys.executable,
        "config": {},
    }
    graph["planes"]["main"]["nodes"]["step"]["invoke"]["config"] = {"number": 7}
    file.write_text(json.dumps(graph))

    def resolver(name, contract):
        from millrace.router.execution import RouterError

        if (name, contract) != ("counter", "counter/v1"):
            raise RouterError("unsupported_adapter_contract")
        return adapter

    return file, project, home, resolver


def test_external_non_pi_adapter_requires_no_core_edit(tmp_path):
    adapter = CounterAdapter()
    file, project, home, resolver = external_flow(tmp_path, adapter)
    created = control.create_instance(
        file,
        project,
        state_home=home,
        request_id=new_id(),
        expected_state_version=0,
        adapter_resolver=resolver,
    )
    instance = str(created["instance_id"])
    validation = control.validate_revision(
        instance, state_home=home, adapter_resolver=resolver
    )
    started = control.start_run(
        instance,
        state_home=home,
        request_id=new_id(),
        expected_state_version=1,
        adapter_resolver=resolver,
    )
    result = control.inspect_run(
        instance, str(started["run_id"]), state_home=home, adapter_resolver=resolver
    )
    assert result["status"] == "succeeded"
    launched = result["invocations"][0]["launch_settings"]
    advisory = validation["resolved_nodes"]["main"]["step"]
    assert (
        launched["common"]["producing_revision"]
        == result["invocations"][0]["revision_id"]
    )
    assert advisory["common"]["producing_revision"] == validation["revision_id"]
    assert launched == {
        **advisory,
        "common": {
            **advisory["common"],
            "producing_revision": launched["common"]["producing_revision"],
        },
    }
    assert len(adapter.calls) == 1
    file.unlink()
    after = control.inspect_run(
        instance, str(started["run_id"]), state_home=home, adapter_resolver=resolver
    )
    assert after["draft_revision"]["diagnostic"] == "missing_control"
    after.pop("draft_revision")
    result.pop("draft_revision")
    assert after == result


def test_missing_or_wrong_contract_refuses(tmp_path):
    import pytest

    from millrace.router.execution import RouterError

    file, project, home, resolver = external_flow(tmp_path, CounterAdapter())
    created = control.create_instance(
        file,
        project,
        state_home=home,
        request_id=new_id(),
        expected_state_version=0,
        adapter_resolver=resolver,
    )
    instance = str(created["instance_id"])
    for operation in (
        lambda: control.load_instance(instance, home),
        lambda: control.list_instances(state_home=home),
        lambda: control.start_run(
            instance, state_home=home, request_id=new_id(), expected_state_version=1
        ),
    ):
        with pytest.raises(RouterError, match="unsupported_adapter_contract"):
            operation()


def test_generated_alias_destinations_refuse_before_count(tmp_path):
    file, project, home, resolver = external_flow(tmp_path, CounterAdapter("aliases"))
    created = control.create_instance(
        file,
        project,
        state_home=home,
        request_id=new_id(),
        expected_state_version=0,
        adapter_resolver=resolver,
    )
    assert created["status"] == "refused" and created["code"] == "invalid_launch_plan"


def test_reservation_plan_refusal_pauses_without_count(tmp_path):
    file, project, home, resolver = external_flow(tmp_path, CounterAdapter("actual"))
    created = control.create_instance(
        file,
        project,
        state_home=home,
        request_id=new_id(),
        expected_state_version=0,
        adapter_resolver=resolver,
    )
    instance = str(created["instance_id"])
    started = control.start_run(
        instance,
        state_home=home,
        request_id=new_id(),
        expected_state_version=1,
        adapter_resolver=resolver,
    )
    result = control.inspect_run(
        instance, str(started["run_id"]), state_home=home, adapter_resolver=resolver
    )
    assert (
        result["status"] == "paused" and result["pause_reason"] == "invalid_launch_plan"
    )
    assert result["count"] == 0 and result["invocations"] == []


@pytest.mark.parametrize(
    "bad",
    [
        "null_exit",
        "wrong_exit",
        "bool_exit",
        "stdout",
        "stderr",
        "diagnostic",
        "surrogate",
        "interrupted",
        "evidence",
    ],
)
def test_invalid_adapter_result_keeps_store_readable(tmp_path, bad):
    file, project, home, resolver = external_flow(tmp_path, CounterAdapter(bad))
    created = control.create_instance(
        file,
        project,
        state_home=home,
        request_id=new_id(),
        expected_state_version=0,
        adapter_resolver=resolver,
    )
    instance = str(created["instance_id"])
    started = control.start_run(
        instance,
        state_home=home,
        request_id=new_id(),
        expected_state_version=1,
        adapter_resolver=resolver,
    )
    result = control.inspect_run(
        instance, str(started["run_id"]), state_home=home, adapter_resolver=resolver
    )
    assert result["status"] == "paused" and result["pause_reason"] == "writer_unknown"
    assert result["count"] == 1 and result["invocations"][0]["status"] == "uncertain"
    assert result["completions"] == []
    assert (project / ".millrace-router-owner/owner.json").exists()


def test_core_operates_without_plus(tmp_path, monkeypatch):
    import importlib.abc

    class BlockOptional(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path=None, target=None):
            if fullname.split(".")[0] in {"millrace_plus", "millrace_workflow_package"}:
                raise ModuleNotFoundError(fullname)
            return None

    assert not any(
        name.split(".")[0] in {"millrace_plus", "millrace_workflow_package"}
        for name in sys.modules
    )
    monkeypatch.setattr(sys, "meta_path", [BlockOptional(), *sys.meta_path])
    test_validation_and_launch_settings_parity(tmp_path)
    assert not any(
        name.split(".")[0] in {"millrace_plus", "millrace_workflow_package"}
        for name in sys.modules
    )


def test_builtin_command_cannot_be_overridden(tmp_path):
    def forbidden(*args):
        raise AssertionError("builtin delegated")

    file, project, home = generic_flow(tmp_path)
    receipt = control.create_instance(
        file,
        project,
        state_home=home,
        request_id=new_id(),
        expected_state_version=0,
        adapter_resolver=forbidden,
    )
    assert receipt["status"] == "applied"
    started = control.start_run(
        str(receipt["instance_id"]),
        state_home=home,
        request_id=new_id(),
        expected_state_version=1,
        adapter_resolver=forbidden,
    )
    assert (
        control.inspect_run(
            str(receipt["instance_id"]),
            str(started["run_id"]),
            state_home=home,
            adapter_resolver=forbidden,
        )["status"]
        == "succeeded"
    )


@pytest.mark.parametrize(
    "field", ["argv", "environment", "details", "settings", "projection", "generated"]
)
@pytest.mark.parametrize("extra", [0, 1])
def test_plan_handoff_exact_bounds(tmp_path, field, extra):
    from dataclasses import replace

    from millrace.router.execution import (
        GENERATED_LIMIT,
        JSON_LIMIT,
        RouterError,
        _admit,
    )
    from millrace.router.graph import build_launch, capture

    file, project, _ = generic_flow(tmp_path)
    revision = capture(file, project)
    context = {
        "state_version": 1,
        "runtime_defaults": {},
        "operator_policy": {},
        "host_paths": {},
        "env_allowlist": [],
    }
    plan, _ = build_launch(
        revision, "main", "step", context, tmp_path / "controls", project_root=project
    )
    # Recreate the boundary request used by the generic graph planner.
    from millrace.router.execution import InvocationRequest

    request = InvocationRequest(
        invocation_id=plan.invocation_id,
        plane_id="main",
        node_id="step",
        producing_revision=revision.revision_id,
        binding_id="python",
        adapter_id="command",
        adapter_contract="millrace-command/v1",
        executable=sys.executable,
        binding_config={},
        node_config={},
        runtime={},
        workflow={},
        plane={},
        common_settings={},
        files={},
        host_paths={},
        environment_names=(),
        outcomes=("DONE",),
        result_contract="json-v1",
        project_root=project,
        invocation_root=tmp_path,
    )
    if field == "argv":
        plan = replace(plan, argv=("x" * (JSON_LIMIT - 4 + extra),))
    elif field in {"environment", "details", "settings"}:
        plan = replace(plan, **{field: {"x": "x" * (JSON_LIMIT - 8 + extra)}})
    elif field == "generated":
        plan = replace(
            plan, generated_controls={"blob": b"x" * (GENERATED_LIMIT + extra)}
        )
    else:
        import hashlib

        from millrace.router.execution import _encoded

        overhead = len(
            _encoded({"": {"size": 0, "sha256": hashlib.sha256(b"").hexdigest()}})
        )
        plan = replace(
            plan, generated_controls={"a" * (JSON_LIMIT - overhead + extra): b""}
        )
    if extra:
        with pytest.raises(RouterError):
            _admit(plan, request)
    else:
        _admit(plan, request)


def test_core_preparation_null_evidence_requires_empty_bytes(tmp_path):
    import sqlite3

    from millrace.router.execution import RouterError

    file, project, home = generic_flow(tmp_path)
    graph = json.loads(file.read_text())
    graph["planes"]["main"]["nodes"]["step"]["invoke"]["inputs"] = ["absent"]
    file.write_text(json.dumps(graph))
    created = control.create_instance(
        file, project, state_home=home, request_id=new_id(), expected_state_version=0
    )
    instance = str(created["instance_id"])
    started = control.start_run(
        instance, state_home=home, request_id=new_id(), expected_state_version=1
    )
    run = str(started["run_id"])
    result = control.inspect_run(instance, run, state_home=home)
    assert result["count"] == 1
    assert result["invocations"][0]["origin"] == "core_preparation"
    assert result["invocations"][0]["adapter_evidence"] is None
    config = control.load_instance(instance, home)
    for field in ("stdout", "stderr"):
        with sqlite3.connect(config.store_path) as db:
            db.execute(f"UPDATE completions SET {field}=?", (b"bad",))
        with pytest.raises(RouterError, match="completion_integrity"):
            control.inspect_run(instance, run, state_home=home)
        with sqlite3.connect(config.store_path) as db:
            db.execute(f"UPDATE completions SET {field}=?", (b"",))


def test_completion_origin_is_core_assigned_and_durable(tmp_path):
    import sqlite3

    from millrace.router.execution import RouterError

    file, project, home, resolver = external_flow(tmp_path, CounterAdapter("evidence"))
    created = control.create_instance(
        file,
        project,
        state_home=home,
        request_id=new_id(),
        expected_state_version=0,
        adapter_resolver=resolver,
    )
    instance = str(created["instance_id"])
    started = control.start_run(
        instance,
        state_home=home,
        request_id=new_id(),
        expected_state_version=1,
        adapter_resolver=resolver,
    )
    run = str(started["run_id"])
    paused = control.inspect_run(
        instance, run, state_home=home, adapter_resolver=resolver
    )
    invocation = paused["invocations"][0]["id"]
    receipt = control.reconcile_invocation(
        instance,
        run,
        invocation_id=invocation,
        reason="owned fake process reaped",
        stopped=True,
        state_home=home,
        request_id=new_id(),
        expected_state_version=paused["state_version"],
        adapter_resolver=resolver,
        plane_id="main",
    )
    assert receipt["status"] == "applied"
    after = control.inspect_run(
        instance, run, state_home=home, adapter_resolver=resolver
    )
    assert after["invocations"][0]["origin"] == "core_interruption"
    assert after["completions"][0]["transport"] == "interrupted"
    assert after == control.inspect_run(
        instance, run, state_home=home, adapter_resolver=resolver
    )
    config = control.load_instance(instance, home, adapter_resolver=resolver)
    with sqlite3.connect(config.store_path) as db:
        db.execute("DELETE FROM writer_facts")
    with pytest.raises(RouterError):
        control.inspect_run(instance, run, state_home=home, adapter_resolver=resolver)


@pytest.mark.parametrize(
    "operation",
    [
        "load_instance",
        "list_instances",
        "inspect_instance",
        "inspect_run",
        "create_instance",
        "update_instance_config",
        "validate_revision",
        "submit_revision",
        "start_run",
        "recover_run",
        "pause_run",
        "resume_run",
        "accept_revision",
        "redirect_run",
        "retry_invocation",
        "cancel_run",
        "reconcile_invocation",
        "execute_request",
        "release_project_hold",
    ],
)
def test_every_public_host_path_propagates_resolver_before_mutation(
    tmp_path, operation
):
    import sqlite3

    from millrace.router.control_requests import envelope
    from millrace.router.execution import RouterError

    adapter = CounterAdapter("evidence")
    file, project, home, base_resolver = external_flow(tmp_path, adapter)
    calls = []

    def resolver(name, contract):
        calls.append((name, contract))
        return base_resolver(name, contract)

    creation_id = new_id()
    created = control.create_instance(
        file,
        project,
        state_home=home,
        request_id=creation_id,
        expected_state_version=0,
        adapter_resolver=resolver,
    )
    instance = str(created["instance_id"])
    started = control.start_run(
        instance,
        state_home=home,
        request_id=new_id(),
        expected_state_version=1,
        adapter_resolver=resolver,
    )
    run = str(started["run_id"])
    before = control.inspect_run(
        instance, run, state_home=home, adapter_resolver=resolver
    )
    config = control.load_instance(instance, home, adapter_resolver=resolver)
    invocation = before["invocations"][0]["id"]

    def invoke(selected):
        extra = {"adapter_resolver": selected} if selected else {}
        common = {"state_home": home, **extra}
        if operation == "load_instance":
            return control.load_instance(instance, home, **extra)
        if operation == "list_instances":
            return control.list_instances(**common)
        if operation == "inspect_instance":
            return control.inspect_instance(instance, **common)
        if operation == "inspect_run":
            return control.inspect_run(instance, run, **common)
        if operation == "create_instance":
            return control.create_instance(
                file,
                project,
                request_id=creation_id,
                expected_state_version=0,
                **common,
            )
        if operation == "validate_revision":
            return control.validate_revision(instance, **common)
        if operation == "start_run":
            return control.start_run(
                instance, request_id=new_id(), expected_state_version=2, **common
            )
        if operation == "update_instance_config":
            return control.update_instance_config(
                instance,
                settings={"runtime_defaults": {}},
                request_id=new_id(),
                expected_state_version=2,
                **common,
            )
        if operation in {"pause_run", "cancel_run"}:
            return getattr(control, operation)(
                instance, run, request_id=new_id(), **common
            )
        if operation == "execute_request":
            return control.execute_request(
                envelope("pause_run", instance, run, new_id(), None, {}, plane_id=None),
                **common,
            )
        if operation == "release_project_hold":
            return control.release_project_hold(
                project_root=project,
                hold_id=before["hold_id"],
                owner_instance_id=instance,
                owner_run_id=run,
                reason="owned fake reaped",
                stopped=True,
                request_id=new_id(),
                **common,
            )
        rest = {
            "request_id": new_id(),
            "expected_state_version": before["state_version"],
        }
        if operation in {"retry_invocation", "reconcile_invocation"}:
            rest.update(invocation_id=invocation, reason="owned fake reaped")
        if operation == "reconcile_invocation":
            rest["stopped"] = True
        if operation == "redirect_run":
            return control.redirect_plane(
                instance,
                run,
                plane_id="main",
                node_id="step",
                reason="test",
                **rest,
                **common,
            )
        if operation in {"retry_invocation", "reconcile_invocation"}:
            rest["plane_id"] = "main"
        if operation == "accept_revision":
            rest["candidate_id"] = "0" * 64
        return getattr(control, operation)(instance, run, **rest, **common)

    with sqlite3.connect(config.store_path) as db:
        original = list(db.iterdump())
    with pytest.raises(RouterError, match="unsupported_adapter_contract"):
        invoke(None)
    with sqlite3.connect(config.store_path) as db:
        assert list(db.iterdump()) == original
    calls.clear()
    invoke(resolver)
    assert ("counter", "counter/v1") in calls


def test_invalid_plan_resume_and_exhaustion_precedence(tmp_path, monkeypatch):
    from millrace.router.store import Store

    adapter = CounterAdapter("actual")
    file, project, home, resolver = external_flow(tmp_path, adapter)
    created = control.create_instance(
        file,
        project,
        state_home=home,
        request_id=new_id(),
        expected_state_version=0,
        adapter_resolver=resolver,
    )
    instance = str(created["instance_id"])
    started = control.start_run(
        instance,
        state_home=home,
        request_id=new_id(),
        expected_state_version=1,
        adapter_resolver=resolver,
    )
    run = str(started["run_id"])
    paused = control.inspect_run(
        instance, run, state_home=home, adapter_resolver=resolver
    )
    assert paused["pause_reason"] == "invalid_launch_plan" and paused["count"] == 0
    adapter.bad = ""
    control.resume_run(
        instance,
        run,
        state_home=home,
        request_id=new_id(),
        expected_state_version=paused["state_version"],
        adapter_resolver=resolver,
    )
    final = control.inspect_run(
        instance, run, state_home=home, adapter_resolver=resolver
    )
    assert final["status"] == "succeeded" and final["count"] == 1
    original = Store.reserve

    def exhausted(store, run_id, revision, plane_id):
        deadline = store.run(run_id)["deadline"]
        with monkeypatch.context() as patch:
            patch.setattr("millrace.router.store.time.time", lambda: deadline)
            return original(store, run_id, revision, plane_id)

    monkeypatch.setattr(Store, "reserve", exhausted)
    again = control.start_run(
        instance,
        state_home=home,
        request_id=new_id(),
        expected_state_version=control.inspect_instance(
            instance, state_home=home, adapter_resolver=resolver
        )["state_version"],
        adapter_resolver=resolver,
    )
    limited = control.inspect_run(
        instance, str(again["run_id"]), state_home=home, adapter_resolver=resolver
    )
    assert limited["pause_reason"] == "limit_exhausted" and limited["count"] == 0


def test_external_completion_raw_first_and_idempotence(tmp_path, monkeypatch):
    from dataclasses import replace

    from millrace.router.execution import AdapterResult, RouterError
    from millrace.router.store import Store

    adapter = CounterAdapter()
    file, project, home, resolver = external_flow(tmp_path, adapter)
    created = control.create_instance(
        file,
        project,
        state_home=home,
        request_id=new_id(),
        expected_state_version=0,
        adapter_resolver=resolver,
    )
    instance = str(created["instance_id"])

    class Crash(BaseException):
        pass

    original = Store.complete

    def committed(store, invocation_id, result, **kwargs):
        original(store, invocation_id, result, **kwargs)
        raise Crash()

    ids = {}
    with monkeypatch.context() as patch:
        patch.setattr(Store, "complete", committed)
        with pytest.raises(Crash):
            control.start_run(
                instance,
                state_home=home,
                request_id=new_id(),
                expected_state_version=1,
                on_started=ids.update,
                adapter_resolver=resolver,
            )
    run = str(ids["run_id"])
    raw = control.inspect_run(instance, run, state_home=home, adapter_resolver=resolver)
    assert raw["completions"][0]["classification"] is None and raw["transitions"] == []
    config = control.load_instance(instance, home, adapter_resolver=resolver)
    store = Store(config, adapter_resolver=resolver)
    try:
        invocation = raw["invocations"][0]
        result = AdapterResult(
            b'{"outcome":"DONE"}',
            b"",
            "completed",
            "stopped",
            exit_code=0,
            adapter_evidence=invocation["adapter_evidence"],
        )
        original(store, invocation["id"], result)
        for changed in (
            replace(result, diagnostic="changed"),
            replace(result, stdout=b'{"outcome":"OTHER"}'),
            replace(result, adapter_evidence={"id": "changed"}),
        ):
            with pytest.raises(RouterError, match="conflicting_completion"):
                original(store, invocation["id"], changed)
        with pytest.raises(RouterError, match="conflicting_completion"):
            original(store, invocation["id"], result, origin="core_preparation")
    finally:
        store.close()
    control.recover_run(
        instance,
        run,
        state_home=home,
        request_id=new_id(),
        expected_state_version=raw["state_version"],
        adapter_resolver=resolver,
    )
    final = control.inspect_run(
        instance, run, state_home=home, adapter_resolver=resolver
    )
    assert (
        final["status"] == "succeeded"
        and len(final["transitions"]) == 1
        and len(adapter.calls) == 1
    )
    assert final == control.inspect_run(
        instance, run, state_home=home, adapter_resolver=resolver
    )


@pytest.mark.parametrize("mode", ["locked", "live"])
def test_adapter_edit_preserves_producing_contract(tmp_path, mode):
    class EditingCounter(CounterAdapter):
        def invoke(self, *args, **kwargs):
            result = super().invoke(*args, **kwargs)
            changed = json.loads(file.read_text())
            changed["bindings"]["python"] = {
                "adapter": "command",
                "contract": "millrace-command/v1",
                "executable": sys.executable,
                "config": {},
            }
            changed["planes"]["main"]["nodes"]["step"]["invoke"]["config"] = {
                "script": "step.py"
            }
            changed["planes"]["main"]["nodes"]["step"]["on"]["DONE"] = {
                "finish": "failed"
            }
            file.write_text(json.dumps(changed))
            return result

    adapter = EditingCounter()
    file, project, home, resolver = external_flow(tmp_path, adapter)
    created = control.create_instance(
        file,
        project,
        state_home=home,
        request_id=new_id(),
        expected_state_version=0,
        adapter_resolver=resolver,
    )
    instance = str(created["instance_id"])
    started = control.start_run(
        instance,
        edit_mode=mode,
        state_home=home,
        request_id=new_id(),
        expected_state_version=1,
        adapter_resolver=resolver,
    )
    run = str(started["run_id"])
    result = control.inspect_run(
        instance, run, state_home=home, adapter_resolver=resolver
    )
    assert result["invocations"][0]["launch"]["adapter_id"] == "counter"
    assert result["invocations"][0]["adapter_evidence"]["counter"] == 7
    if mode == "locked":
        assert (
            result["status"] == "paused"
            and result["pause_reason"] == "locked_edit"
            and result["transitions"] == []
        )
        control.accept_revision(
            instance,
            run,
            candidate_id=result["pending_revision"]["candidate_id"],
            state_home=home,
            request_id=new_id(),
            expected_state_version=result["state_version"],
            adapter_resolver=resolver,
        )
        result = control.inspect_run(
            instance, run, state_home=home, adapter_resolver=resolver
        )
    assert result["status"] == "failed" and result["count"] == 1
    assert result["invocations"][0]["revision_id"] != result["revision_id"]
    assert result == control.inspect_run(
        instance, run, state_home=home, adapter_resolver=resolver
    )


def test_v5_refusal_preserves_historical_bytes(tmp_path):
    import hashlib
    import sqlite3

    from millrace.router.execution import RouterError

    file, project, home = generic_flow(tmp_path)
    created = control.create_instance(
        file, project, state_home=home, request_id=new_id(), expected_state_version=0
    )
    instance = str(created["instance_id"])
    config = control.load_instance(instance, home)
    with sqlite3.connect(config.store_path) as db:
        db.execute(
            "UPDATE meta SET value='millrace-router-store/v5' WHERE key='format'"
        )
    before = hashlib.sha256(config.store_path.read_bytes()).hexdigest()
    for operation in (
        lambda: control.load_instance(instance, home),
        lambda: control.list_instances(state_home=home),
        lambda: control.inspect_instance(instance, state_home=home),
    ):
        with pytest.raises(RouterError, match="unsupported_store_version"):
            operation()
    assert hashlib.sha256(config.store_path.read_bytes()).hexdigest() == before


def test_reconciliation_origin_is_checked_before_atomic_commit(tmp_path, monkeypatch):
    from millrace.router.store import Store

    file, project, home, resolver = external_flow(tmp_path, CounterAdapter("evidence"))
    created = control.create_instance(
        file,
        project,
        state_home=home,
        request_id=new_id(),
        expected_state_version=0,
        adapter_resolver=resolver,
    )
    instance = str(created["instance_id"])
    started = control.start_run(
        instance,
        state_home=home,
        request_id=new_id(),
        expected_state_version=1,
        adapter_resolver=resolver,
    )
    run = str(started["run_id"])
    before = control.inspect_run(
        instance, run, state_home=home, adapter_resolver=resolver
    )
    original = Store.reconcile

    def broken(store, *args):
        original(store, *args)
        store.db.execute("DELETE FROM writer_facts")

    monkeypatch.setattr(Store, "reconcile", broken)
    result = control.reconcile_invocation(
        instance,
        run,
        invocation_id=before["invocations"][0]["id"],
        reason="owned fake",
        stopped=True,
        state_home=home,
        request_id=new_id(),
        expected_state_version=before["state_version"],
        adapter_resolver=resolver,
        plane_id="main",
    )
    assert result["status"] == "refused" and result["code"] == "control_integrity"
    after = control.inspect_run(
        instance, run, state_home=home, adapter_resolver=resolver
    )
    assert (
        after["completions"] == [] and after["invocations"][0]["status"] == "uncertain"
    )


def test_adapter_settings_cannot_depend_on_invocation_identity(tmp_path):
    from dataclasses import replace

    class IdentityCounter(CounterAdapter):
        def plan(self, request):
            plan = super().plan(request)
            return replace(plan, settings={"identity": request.invocation_id})

    adapter = IdentityCounter()
    file, project, home, resolver = external_flow(tmp_path, adapter)
    created = control.create_instance(
        file,
        project,
        state_home=home,
        request_id=new_id(),
        expected_state_version=0,
        adapter_resolver=resolver,
    )
    instance = str(created["instance_id"])
    assert control.validate_revision(
        instance, state_home=home, adapter_resolver=resolver
    )["valid"]
    started = control.start_run(
        instance,
        state_home=home,
        request_id=new_id(),
        expected_state_version=1,
        adapter_resolver=resolver,
    )
    result = control.inspect_run(
        instance, str(started["run_id"]), state_home=home, adapter_resolver=resolver
    )
    assert result["pause_reason"] == "invalid_launch_plan" and result["count"] == 0
    assert not result["invocations"] and not adapter.calls


def test_captured_adapter_plan_and_settings_authority(tmp_path):
    import sqlite3

    from millrace.router.execution import RouterError

    file, project, home, resolver = external_flow(tmp_path, CounterAdapter())
    created = control.create_instance(
        file,
        project,
        state_home=home,
        request_id=new_id(),
        expected_state_version=0,
        adapter_resolver=resolver,
    )
    instance = str(created["instance_id"])
    started = control.start_run(
        instance,
        state_home=home,
        request_id=new_id(),
        expected_state_version=1,
        adapter_resolver=resolver,
    )
    run = str(started["run_id"])
    result = control.inspect_run(
        instance, run, state_home=home, adapter_resolver=resolver
    )

    # A replacement under the same identity cannot reinterpret history.
    class ChangedCounter(CounterAdapter):
        def plan(self, request):
            from dataclasses import replace

            return replace(super().plan(request), details={"number": 8})

    with pytest.raises(RouterError, match="invocation_integrity"):
        control.inspect_run(
            instance,
            run,
            state_home=home,
            adapter_resolver=lambda *args: ChangedCounter(),
        )
    config = control.load_instance(instance, home, adapter_resolver=resolver)
    with sqlite3.connect(config.store_path) as db:
        row = db.execute("SELECT id,body FROM invocations").fetchone()
        body = json.loads(row[1])
        body["launch_settings"]["adapter"]["effective"]["increment"] = 2
        db.execute(
            "UPDATE invocations SET body=? WHERE id=?", (json.dumps(body), row[0])
        )
    with pytest.raises(RouterError, match="invocation_integrity"):
        control.inspect_run(instance, run, state_home=home, adapter_resolver=resolver)
    assert result["status"] == "succeeded"


@pytest.mark.parametrize("location", ["binding", "node"])
def test_builtin_command_unknown_config_is_adapter_settings_error(tmp_path, location):
    file, project, home = generic_flow(tmp_path)
    graph = json.loads(file.read_text())
    if location == "binding":
        graph["bindings"]["python"]["config"]["unrecognized"] = "x"
    else:
        graph["planes"]["main"]["nodes"]["step"]["invoke"]["config"]["unrecognized"] = (
            "x"
        )
    file.write_text(json.dumps(graph))
    created = control.create_instance(
        file, project, state_home=home, request_id=new_id(), expected_state_version=0
    )
    assert (
        created["status"] == "refused" and created["code"] == "invalid_adapter_settings"
    )
    assert not (project / ".millrace-router-owner").exists()


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("missing", ["absent", "wrong_contract"])
def test_release_hold_requires_contract_before_receipt_replay(
    tmp_path, existing, missing
):
    from millrace.router.execution import RouterError

    file, project, home, resolver = external_flow(tmp_path, CounterAdapter())
    graph = json.loads(file.read_text())
    graph["planes"]["main"]["nodes"]["step"]["on"]["DONE"] = {
        "pause": {"reason": "review", "resume": "step"}
    }
    file.write_text(json.dumps(graph))
    created = control.create_instance(
        file,
        project,
        state_home=home,
        request_id=new_id(),
        expected_state_version=0,
        adapter_resolver=resolver,
    )
    instance = str(created["instance_id"])
    started = control.start_run(
        instance,
        state_home=home,
        request_id=new_id(),
        expected_state_version=1,
        adapter_resolver=resolver,
    )
    run = str(started["run_id"])
    owner = json.loads((project / ".millrace-router-owner/owner.json").read_text())
    kwargs = dict(
        project_root=project,
        hold_id=owner["hold_id"],
        owner_instance_id=instance,
        owner_run_id=run,
        reason="owned stopped fake",
        stopped=True,
        state_home=home,
        request_id=new_id(),
    )
    if existing:
        expected = control.release_project_hold(**kwargs, adapter_resolver=resolver)
        assert expected["code"] == "owner_run_nonterminal"

    def snapshot():
        import sqlite3

        database = home / "instances" / instance / "state.sqlite3"
        with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as db:
            state = list(db.iterdump())
        receipts = home / "orphan-receipts"
        return {
            "state": state,
            "owner": (project / ".millrace-router-owner/owner.json").read_bytes(),
            "receipts_exist": receipts.exists(),
            "receipts": {p.name: p.read_bytes() for p in receipts.glob("*.json")},
        }

    before = snapshot()

    def wrong(name, contract):
        raise RouterError("unsupported_adapter_contract")

    with pytest.raises(RouterError, match="unsupported_adapter_contract"):
        control.release_project_hold(
            **kwargs, adapter_resolver=None if missing == "absent" else wrong
        )
    assert snapshot() == before
    if not existing:
        assert not (home / "orphan-receipts").exists()
        expected = control.release_project_hold(**kwargs, adapter_resolver=resolver)
        assert expected["code"] == "owner_run_nonterminal"
    assert control.release_project_hold(**kwargs, adapter_resolver=resolver) == expected


def test_same_adapter_concurrent_started_ack_and_distinct_seam_inputs(
    tmp_path, monkeypatch
):
    import copy
    import os
    import queue
    import threading
    from dataclasses import replace

    from millrace.router.execution import AdapterResult
    from millrace.router.store import Store

    file, project, home = generic_flow(tmp_path)
    graph = json.loads(file.read_text())
    base = graph["planes"].pop("main")
    graph["limits"]["max_parallel_invocations"] = 2
    graph["planes"] = {
        name: {**copy.deepcopy(base), "workspace": name}
        for name in ("source", "alpha", "beta")
    }
    for name in graph["planes"]:
        (project / name).mkdir()
    graph["bindings"]["counter"] = {
        "adapter": "counter",
        "contract": "counter/v1",
        "executable": sys.executable,
        "config": {},
    }
    graph["seams"] = {}
    for name in ("alpha", "beta"):
        graph["planes"][name]["nodes"]["step"]["invoke"].update(
            binding="counter", config={"number": 7}
        )
        graph["seams"][name] = {
            "producer": {"plane": "source", "node": "step", "outcome": "DONE"},
            "consumer": {"plane": name, "node": "step"},
        }
    file.write_text(json.dumps(graph))
    barrier = threading.Barrier(2)
    observed = []
    owner = threading.get_ident()
    committed = set()
    owner_before_commit = threading.Event()
    allow_commit = threading.Event()
    callback_returned = threading.Event()
    blocked_id = {}
    acknowledgement = {}
    ack_wait_entered = threading.Event()
    observer_errors = []
    original_active = Store.active
    original_put = queue.Queue.put
    original_wait = threading.Event.wait

    def observe_put(events, item, *args, **kwargs):
        if (
            isinstance(item, tuple)
            and len(item) == 4
            and item[:2] == (blocked_id.get("id"), "started")
        ):
            acknowledgement["event"] = item[3]
        return original_put(events, item, *args, **kwargs)

    def observe_wait(event, *args, **kwargs):
        if event is acknowledgement.get("event"):
            ack_wait_entered.set()
        return original_wait(event, *args, **kwargs)

    monkeypatch.setattr(queue.Queue, "put", observe_put)
    monkeypatch.setattr(threading.Event, "wait", observe_wait)

    def started(store, invocation_id, pid):
        assert threading.get_ident() == owner
        row = store.db.execute(
            "SELECT plane_id FROM invocations WHERE id=?", (invocation_id,)
        ).fetchone()
        if row[0] == "alpha":
            assert blocked_id["id"] == invocation_id
            owner_before_commit.set()
            assert allow_commit.wait(timeout=3)
        original_active(store, invocation_id, pid)
        committed.add(invocation_id)

    class ConcurrentCounter(CounterAdapter):
        def plan(self, request):
            plan = super().plan(request)
            return replace(plan, details={"number": 7, "plane": request.plane_id})

        def invoke(self, plan, *, cwd, env, deadline, poll, on_started):
            assert threading.get_ident() != owner
            assert "MILLRACE_SEAM_INPUT" in env
            if plan.details["plane"] == "alpha":
                blocked_id["id"] = plan.invocation_id
            on_started(os.getpid())
            if blocked_id.get("id") == plan.invocation_id:
                callback_returned.set()
            assert plan.invocation_id in committed
            import sqlite3

            with sqlite3.connect(
                config.store_path.as_uri() + "?mode=ro", uri=True
            ) as db:
                own = db.execute(
                    "SELECT status,body FROM invocations WHERE id=?",
                    (plan.invocation_id,),
                ).fetchone()
                assert own[0] == "active" and json.loads(own[1])["pid"] == os.getpid()
            item = json.loads(Path(env["MILLRACE_SEAM_INPUT"]).read_text())
            assert item["consumer_plane_id"] == cwd.name == plan.details["plane"]
            assert item["seam_id"] == cwd.name
            assert poll() is False
            observed.append((plan.invocation_id, cwd.name, env["MILLRACE_SEAM_INPUT"]))
            barrier.wait(timeout=3)
            return AdapterResult(
                b'{"outcome":"DONE"}',
                b"",
                "completed",
                "stopped",
                exit_code=0,
                adapter_evidence={"id": plan.invocation_id, "plane": cwd.name},
            )

        def validate_completion(self, plan, completion):
            assert completion.evidence == {
                "id": plan.invocation_id,
                "plane": plan.details["plane"],
            }

    adapter = ConcurrentCounter()

    def resolver(name, contract):
        assert (name, contract) == ("counter", "counter/v1")
        return adapter

    monkeypatch.setattr(Store, "active", started)
    made = control.create_instance(
        file,
        project,
        state_home=home,
        request_id=new_id(),
        expected_state_version=0,
        adapter_resolver=resolver,
    )
    assert made["status"] == "applied", made
    config = control.load_instance(made["instance_id"], home, adapter_resolver=resolver)

    def observe_blocked_commit():
        import sqlite3

        try:
            assert owner_before_commit.wait(timeout=3)
            # The exact queued acknowledgement must enter wait before release.
            # A queue-only callback cannot satisfy this, regardless of scheduling.
            assert ack_wait_entered.wait(timeout=3)
            assert not callback_returned.is_set()
            with sqlite3.connect(
                config.store_path.as_uri() + "?mode=ro", uri=True
            ) as db:
                own = db.execute(
                    "SELECT status,body FROM invocations WHERE id=?",
                    (blocked_id["id"],),
                ).fetchone()
                assert own[0] == "reserved" and json.loads(own[1])["pid"] is None
        except BaseException as exc:
            observer_errors.append(exc)
        finally:
            allow_commit.set()

    observer = threading.Thread(target=observe_blocked_commit)
    observer.start()
    started_run = control.start_run(
        made["instance_id"],
        state_home=home,
        request_id=new_id(),
        expected_state_version=1,
        adapter_resolver=resolver,
    )
    observer.join(timeout=3)
    assert not observer.is_alive() and not observer_errors
    assert callback_returned.is_set()
    result = control.inspect_run(
        made["instance_id"],
        started_run["run_id"],
        state_home=home,
        adapter_resolver=resolver,
    )
    assert result["status"] == "succeeded", result
    assert len(observed) == 2 and {item[1] for item in observed} == {"alpha", "beta"}
    assert (
        len({item[0] for item in observed}) == len({item[2] for item in observed}) == 2
    )
    assert len(result["seam_acknowledgements"]) == 2 and result["quiescent"]
    assert not any(
        thread.name.startswith("millrace-") for thread in threading.enumerate()
    )


def test_ordinary_invocation_drops_inherited_seam_input(tmp_path, monkeypatch):
    file, project, home = generic_flow(tmp_path)
    monkeypatch.setenv("MILLRACE_SEAM_INPUT", "/inert-stale-parent-selection")
    (file.parent / "step.py").write_text(
        "import os\nassert 'MILLRACE_SEAM_INPUT' not in os.environ\n"
        'print(\'{"outcome":"DONE"}\')\n'
    )
    made = control.create_instance(
        file, project, state_home=home, request_id=new_id(), expected_state_version=0
    )
    assert made["status"] == "applied", made
    started = control.start_run(
        made["instance_id"],
        state_home=home,
        request_id=new_id(),
        expected_state_version=1,
    )
    result = control.inspect_run(
        made["instance_id"], started["run_id"], state_home=home
    )
    assert result["status"] == "succeeded" and result["count"] == 1
    assert result["invocations"][0]["launch"]["seam_input"] is None
    assert result["completions"][0]["classification"] == "semantic"


def test_owner_failure_during_started_cleanup_joins_worker_and_keeps_first_error(
    tmp_path, monkeypatch
):
    import threading

    from millrace.router.controller import Controller
    from millrace.router.store import Store

    file, project, home = generic_flow(tmp_path)
    made = control.create_instance(
        file, project, state_home=home, request_id=new_id(), expected_state_version=0
    )
    entered = threading.Event()
    cleaned = threading.Event()
    adapter_continued = threading.Event()
    threads = []
    original_poll = Controller.poll

    def invoke(*args):
        threads.append(threading.current_thread())
        entered.set()
        try:
            args[5](12345)
            adapter_continued.set()
            pytest.fail("a failed owner commit must not authorize adapter continuation")
        finally:
            cleaned.set()

    def poll(owner, active):
        if active:
            assert entered.wait(timeout=3)
            raise RuntimeError("owner_poll_failure")
        return original_poll(owner, active)

    def active(*args):
        raise RuntimeError("started_commit_failure")

    monkeypatch.setattr("millrace.router.engine.invoke", invoke)
    monkeypatch.setattr(Controller, "poll", poll)
    monkeypatch.setattr(Store, "active", active)
    error = None
    try:
        control.start_run(
            made["instance_id"],
            state_home=home,
            request_id=new_id(),
            expected_state_version=1,
        )
    except RuntimeError as exc:
        error = exc
    finally:
        alive_on_return = [thread.is_alive() for thread in threads]
        # Preserve finite cleanup even against the broken RED implementation.
        for thread in threads:
            thread.join(timeout=6)
    assert str(error) == "owner_poll_failure"
    assert not adapter_continued.is_set()
    assert threads and alive_on_return == [False]
    assert cleaned.is_set() and all(not thread.is_alive() for thread in threads)
