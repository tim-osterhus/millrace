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


def test_validation_admits_exact_invocation_plan_for_completion(tmp_path):
    import copy

    from millrace.router.records import RouterError
    from millrace.router.store import Store

    class RecordingCounter(CounterAdapter):
        recording = False

        def __init__(self):
            super().__init__()
            self.plans = {}
            self.completed = []

        def plan(self, request):
            plan = super().plan(request)
            if self.recording and request.invocation_id != "0" * 32:
                assert request.invocation_id not in self.plans
                self.plans[request.invocation_id] = plan
            return plan

        def validate_completion(self, plan, completion):
            if self.recording:
                assert plan is self.plans[completion.invocation_id]
                self.completed.append(completion.invocation_id)
            super().validate_completion(plan, completion)

    adapter = RecordingCounter()
    file, project, home, resolver = external_flow(tmp_path, adapter)
    graph = json.loads(file.read_text())
    nodes = graph["planes"]["main"]["nodes"]
    nodes["next"] = copy.deepcopy(nodes["step"])
    nodes["next"]["invoke"]["config"]["number"] = 9
    nodes["step"]["on"]["DONE"] = {"goto": "next"}
    file.write_text(json.dumps(graph))
    made = control.create_instance(
        file,
        project,
        state_home=home,
        request_id=new_id(),
        expected_state_version=0,
        adapter_resolver=resolver,
    )
    instance = str(made["instance_id"])
    started = control.start_run(
        instance,
        state_home=home,
        request_id=new_id(),
        expected_state_version=1,
        adapter_resolver=resolver,
    )
    view = control.inspect_run(
        instance,
        str(started["run_id"]),
        state_home=home,
        adapter_resolver=resolver,
    )
    assert view["status"] == "succeeded" and view["count"] == 2
    bodies = view["invocations"]
    identifiers = [body["id"] for body in bodies]
    config = control.load_instance(instance, home, adapter_resolver=resolver)
    store = Store(config, adapter_resolver=resolver)
    try:
        durable = list(store.db.iterdump())
        database_bytes = config.store_path.read_bytes()
        adapter.recording = True
        store.validate()
        selected = dict(adapter.plans)
        assert adapter.completed == identifiers
        for body, number in zip(bodies, (7, 9), strict=True):
            plan = selected[body["id"]]
            assert plan.generated_controls == {"number.txt": str(number).encode()}
            assert plan.settings == body["launch_settings"]["adapter"]
            assert list(plan.argv) == body["argv"]
        adapter.plans.clear()
        adapter.completed.clear()
        store.validate()
        assert all(adapter.plans[key] is not selected[key] for key in identifiers)
        assert adapter.completed == identifiers
        assert list(store.db.iterdump()) == durable
        assert config.store_path.read_bytes() == database_bytes

        row = store.db.execute(
            "SELECT id,body FROM invocations ORDER BY attempt"
        ).fetchone()
        raw = json.loads(row[1])
        raw["adapter_evidence"] = {"counter": 8, "id": row[0]}
        store.db.execute(
            "UPDATE invocations SET body=? WHERE id=?", (json.dumps(raw), row[0])
        )
        adapter.plans.clear()
        adapter.completed.clear()
        with pytest.raises(RouterError, match="counter_evidence"):
            store.validate()
    finally:
        store.close()


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


def test_batch_resolution_validates_each_adapter_once_and_matches_single_nodes(
    tmp_path,
):
    import copy

    from millrace.router.execution import RouterError
    from millrace.router.graph import (
        build_launch,
        capture,
        resolve_nodes,
        resolve_settings,
    )

    log: list[tuple[str, str]] = []

    class Logged(CounterAdapter):
        def __init__(self, name):
            super().__init__()
            self.name = name

        def validate_configuration(self, config):
            log.append(("validate", self.name))
            super().validate_configuration(config)

        def plan(self, request):
            log.append(("plan", self.name))
            return super().plan(request)

    adapters = {
        ("counter", "counter/v1"): Logged("counter"),
        ("tally", "tally/v1"): Logged("tally"),
    }

    def resolver(name, contract):
        if (name, contract) not in adapters:
            raise RouterError("unsupported_adapter_contract")
        return adapters[name, contract]

    # Builtin command plus two external adapters across two planes.
    file, project, _ = generic_flow(tmp_path)
    data = json.loads(file.read_text())
    data["limits"]["max_parallel_invocations"] = 1
    for name in ("counter", "tally"):
        data["bindings"][name] = {
            "adapter": name,
            "contract": f"{name}/v1",
            "executable": sys.executable,
            "config": {},
        }
    main = data["planes"]["main"]
    main["defaults"] = {"timeout_seconds": 7}
    base = copy.deepcopy(main["nodes"]["step"])
    main["nodes"]["step"]["on"]["DONE"] = {"goto": "next"}
    main["nodes"]["next"] = copy.deepcopy(base)
    main["nodes"]["next"]["invoke"] = {"binding": "counter", "config": {"number": 7}}
    side = copy.deepcopy(base)
    side["invoke"] = {"binding": "tally", "config": {"number": 9}}
    data["planes"]["side"] = {
        "start": "step",
        "workspace": ".",
        "nodes": {"step": side},
    }
    file.write_text(json.dumps(data))
    revision = capture(file, project)
    context = {
        "state_version": 1,
        "runtime_defaults": {"timeout_seconds": 20},
        "operator_policy": {},
        "host_paths": {},
        "env_allowlist": [],
    }
    validated = [("validate", "counter"), ("validate", "tally")]
    nodes = (("main", "next"), ("main", "step"), ("side", "step"))

    def batch(selected=revision, settings=context):
        log.clear()
        return resolve_nodes(
            selected,
            settings_context=settings,
            adapter_resolver=resolver,
            project_root=project,
        )

    def single(selected, plane_id, node_id, settings=context):
        log.clear()
        return resolve_settings(
            selected,
            plane_id,
            node_id,
            settings_context=settings,
            adapter_resolver=resolver,
            project_root=project,
        )

    resolved = batch()
    assert log == [*validated, ("plan", "counter"), ("plan", "tally")]
    assert {plane: sorted(items) for plane, items in resolved.items()} == {
        "main": ["next", "step"],
        "side": ["step"],
    }
    assert resolved["main"]["next"]["common"]["effective"]["timeout_seconds"] == 7
    assert resolved["side"]["step"]["common"]["effective"]["timeout_seconds"] == 20
    assert resolved["main"]["next"]["common"]["effective"]["binding"] == "counter"
    for plane_id, node_id in nodes:
        assert single(revision, plane_id, node_id) == resolved[plane_id][node_id]
        assert log[:2] == validated
    log.clear()
    build_launch(
        revision,
        "side",
        "step",
        context,
        tmp_path / "controls",
        adapter_resolver=resolver,
        project_root=project,
    )
    assert log == [*validated, ("plan", "tally")]
    assert batch() == resolved
    assert log == [*validated, ("plan", "counter"), ("plan", "tally")]

    changed = {**context, "runtime_defaults": {"timeout_seconds": 30}}
    assert batch(settings=changed)["side"]["step"]["common"]["effective"] == {
        "binding": "tally",
        "timeout_seconds": 30,
    }
    assert log[:2] == validated

    def unused_binding(graph):
        graph["bindings"]["unused"] = {
            **graph["bindings"]["python"],
            "config": {"unrecognized": "x"},
        }

    def later_node(graph):
        graph["planes"]["side"]["nodes"]["step"]["invoke"]["config"]["extra"] = 1

    def plane_namespace(graph):
        graph["planes"]["side"]["defaults"] = {
            "adapter_settings": {
                "command": {
                    "contract": "millrace-command/v1",
                    "config": {"unrecognized": "x"},
                }
            }
        }

    namespace = {"counter": {"contract": "counter/v1", "config": {"unrecognized": "x"}}}
    runtime = {**context, "runtime_defaults": {"adapter_settings": namespace}}
    cases = [(revision, runtime, "counter_settings")]
    for mutate, code in (
        (unused_binding, "invalid_adapter_settings"),
        (later_node, "counter_config"),
        (plane_namespace, "invalid_adapter_settings"),
    ):
        bad = copy.deepcopy(data)
        mutate(bad)
        file.write_text(json.dumps(bad))
        cases.append((capture(file, project), context, code))
    for selected, settings, code in cases:
        with pytest.raises(RouterError) as error:
            batch(selected, settings)
        assert error.value.code == code
        assert not [entry for entry in log if entry[0] == "plan"]
        for plane_id, node_id in nodes:
            with pytest.raises(RouterError) as error:
                single(selected, plane_id, node_id, settings)
            assert error.value.code == code
            assert not [entry for entry in log if entry[0] == "plan"]


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


def test_retained_v7_original_create_request_replay_and_recovery(tmp_path):
    import sqlite3

    from tests.router.support import genuine_v7_fixture

    fixture = genuine_v7_fixture(tmp_path / "retained")
    home = fixture["state_home"]
    instance, run = fixture["instance_id"], fixture["run_id"]
    config = control.load_instance(instance, home)
    with sqlite3.connect(config.store_path) as db:
        before = list(db.iterdump())
        assert dict(db.execute("SELECT key,value FROM meta"))["format"] == (
            "millrace-router-store/v7"
        )
    receipt = control.execute_request(fixture["create_request"], state_home=home)
    assert receipt == fixture["create_receipt"]
    with sqlite3.connect(config.store_path) as db:
        assert list(db.iterdump()) == before
    view = control.inspect_run(instance, run, state_home=home)
    assert view["status"] == "succeeded" and view["count"] == 1
    assert all(
        view[k] is not None
        for k in (
            "startup_cap",
            "startup_elapsed",
            "startup_deadline",
            "cap",
            "max_elapsed",
            "deadline",
        )
    )
    recovered = control.recover_run(
        instance,
        run,
        state_home=home,
        request_id=new_id(),
        expected_state_version=view["state_version"],
    )
    assert recovered["status"] == "applied"
    after = control.inspect_run(instance, run, state_home=home)
    for key in (
        "invocations",
        "completions",
        "transitions",
        "startup_cap",
        "startup_elapsed",
        "startup_deadline",
        "cap",
        "max_elapsed",
        "deadline",
        "count",
        "state_version",
    ):
        assert after[key] == view[key]
    with sqlite3.connect(config.store_path) as db:
        assert dict(db.execute("SELECT key,value FROM meta"))["format"] == (
            "millrace-router-store/v7"
        )


@pytest.mark.parametrize("corruption", ["future", "nullable"])
def test_retained_v7_version_and_nullable_refuse_without_mutation(tmp_path, corruption):
    import sqlite3

    from tests.router.support import genuine_v7_fixture

    from millrace.router.records import RouterError

    fixture = genuine_v7_fixture(tmp_path / "retained")
    instance, home = fixture["instance_id"], fixture["state_home"]
    database = control.load_instance(instance, home).store_path
    with sqlite3.connect(database) as db:
        if corruption == "future":
            db.execute(
                "UPDATE meta SET value='millrace-router-store/v999' WHERE key='format'"
            )
        else:
            body = json.loads(db.execute("SELECT body FROM runs").fetchone()[0])
            body["startup_cap"] = None
            db.execute("UPDATE runs SET body=?", (json.dumps(body),))
    before = database.read_bytes()
    with pytest.raises(RouterError):
        control.inspect_run(instance, fixture["run_id"], state_home=home)
    assert database.read_bytes() == before


def test_v7_v4_candidate_refusal_only_records_targeted_receipt(tmp_path):
    import sqlite3

    from tests.router.support import genuine_v7_fixture

    fixture = genuine_v7_fixture(tmp_path / "retained", "paused")
    instance, run, home = (
        fixture["instance_id"],
        fixture["run_id"],
        fixture["state_home"],
    )
    before = control.inspect_run(instance, run, state_home=home)
    assert before["status"] == "paused" and before["count"] == 1
    database = control.load_instance(instance, home).store_path

    def tables():
        with sqlite3.connect(database) as db:
            names = [
                r[0]
                for r in db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
                )
            ]
            return {
                name: db.execute(f'SELECT * FROM "{name}"').fetchall() for name in names
            }

    original = tables()
    graph = json.loads(fixture["workflow"].read_text())
    graph["format"], graph["limits"] = "millrace-router/v4", {}
    fixture["workflow"].write_text(json.dumps(graph))
    request_id = new_id()
    refused = control.submit_revision(
        instance,
        run,
        state_home=home,
        request_id=request_id,
        expected_state_version=before["state_version"],
    )
    assert refused["status"] == "refused" and refused["code"] == "unsupported_graph"
    after = tables()
    assert set(after) == set(original)
    for name in original:
        if name not in {"controls", "final_receipts", "sqlite_sequence"}:
            assert after[name] == original[name], name
    assert len(after["controls"]) == len(original["controls"]) + 1
    assert len(after["final_receipts"]) == len(original["final_receipts"]) + 1
    assert after["controls"][:-1] == original["controls"]
    assert after["final_receipts"][:-1] == original["final_receipts"]
    sequences = dict(original["sqlite_sequence"])
    sequences["controls"] += 1
    assert dict(after["sqlite_sequence"]) == sequences
    with sqlite3.connect(database) as db:
        receipt = db.execute(
            "SELECT operation,status,receipt FROM controls WHERE request_id=?",
            (request_id,),
        ).fetchone()
    assert receipt[:2] == ("submit_revision", "refused")
    assert json.loads(receipt[2]) == refused
    assert (
        control.submit_revision(
            instance,
            run,
            state_home=home,
            request_id=request_id,
            expected_state_version=before["state_version"],
        )
        == refused
    )
    assert tables() == after


def test_genuine_v7_subsequent_finite_run_does_not_add_anchor(tmp_path):
    import sqlite3

    from tests.router.support import genuine_v7_fixture

    fixture = genuine_v7_fixture(tmp_path / "retained")
    instance, home = fixture["instance_id"], fixture["state_home"]
    database = control.load_instance(instance, home).store_path
    with sqlite3.connect(database) as db:
        columns = list(db.execute("PRAGMA table_info(runs)"))
    assert [row[1] for row in columns] == ["id", "revision_id", "hold_id", "body"]
    old = control.inspect_run(instance, fixture["run_id"], state_home=home)
    started = control.start_run(
        instance,
        state_home=home,
        request_id=new_id(),
        expected_state_version=control.inspect_instance(instance, state_home=home)[
            "state_version"
        ],
    )
    assert started["status"] == "applied"
    new = control.inspect_run(instance, started["run_id"], state_home=home)
    assert new["status"] == "succeeded" and new["count"] == 1
    assert new["id"] != fixture["run_id"]
    assert new["startup_cap"] == new["cap"] == 3
    assert new["startup_elapsed"] == new["max_elapsed"] == 120
    assert new["deadline"] == new["started_at"] + 120
    assert control.inspect_run(instance, fixture["run_id"], state_home=home) == old
    with sqlite3.connect(database) as db:
        assert list(db.execute("PRAGMA table_info(runs)")) == columns
        assert dict(db.execute("SELECT key,value FROM meta"))["format"] == (
            "millrace-router-store/v7"
        )


def test_v7_automatic_v4_observation_preserves_result_and_safety_hold(
    tmp_path, monkeypatch
):
    from tests.router.support import genuine_v7_fixture

    from millrace.router.store import Store

    fixture = genuine_v7_fixture(tmp_path / "retained")
    instance, home = fixture["instance_id"], fixture["state_home"]
    old = control.inspect_run(instance, fixture["run_id"], state_home=home)
    workflow = fixture["workflow"]
    original_bytes = workflow.read_bytes()
    original = Store.complete

    def edit_after_durable_result(store, *args):
        original(store, *args)
        graph = json.loads(original_bytes)
        graph["format"], graph["limits"] = "millrace-router/v4", {}
        workflow.write_text(json.dumps(graph))

    with monkeypatch.context() as patch:
        patch.setattr(Store, "complete", edit_after_durable_result)
        started = control.start_run(
            instance,
            state_home=home,
            request_id=new_id(),
            edit_mode="live",
            expected_state_version=control.inspect_instance(instance, state_home=home)[
                "state_version"
            ],
        )
    assert started["status"] == "applied"
    run = str(started["run_id"])
    view = control.inspect_run(instance, run, state_home=home)
    assert view["pause_reason"] == "invalid_revision" and view["count"] == 1
    assert len(view["completions"]) == 1 and view["transitions"] == []
    assert view["completions"][0]["classification"] == "semantic"
    assert view["completions"][0]["outcome"] == "READY"
    assert view["completions"][0]["effective_writer"] == "stopped"
    assert view["revision_id"] == view["startup_revision_id"]
    assert view["invocations"][0]["revision_id"] == view["revision_id"]
    assert view["candidates"] == [] and view["pending_revision"] is None
    assert len(view["activations"]) == 1
    old_reloaded = control.inspect_run(instance, fixture["run_id"], state_home=home)
    # draft_revision reports today's incompatible authored bytes; durable history
    # of the retained prior run must remain unchanged.
    for key in old:
        if key != "draft_revision":
            assert old_reloaded[key] == old[key], key
    # Restoring valid bytes and explicitly resuming routes the retained result;
    # it cannot replace the saved completion or silently launch another child.
    workflow.write_bytes(original_bytes)

    def forbidden(*args, **kwargs):
        raise AssertionError("held result was discarded and redispatched")

    monkeypatch.setattr("millrace.router.engine.invoke", forbidden)
    resumed = control.resume_run(
        instance,
        run,
        state_home=home,
        request_id=new_id(),
        expected_state_version=view["state_version"],
    )
    assert resumed["status"] == "applied"
    after = control.inspect_run(instance, run, state_home=home)
    assert after["status"] == "succeeded" and after["count"] == 1
    assert len(after["transitions"]) == 1
    assert after["completions"] == view["completions"]
    assert after["invocations"] == view["invocations"]


def test_coherent_old_reader_stale_producer_history_refuses_without_mutation(tmp_path):
    import sqlite3

    from tests.router.support import genuine_v7_fixture

    from millrace.router.records import RouterError

    root = tmp_path / "stale"
    fixture = genuine_v7_fixture(root, "stale_producer")
    assert fixture["old_full_validation"] == "PASS"
    assert fixture["peer_execution"].startswith("NONE ")
    old = json.loads((root / "run.json").read_text())
    invocation = old["invocations"][0]
    r0, r1 = fixture["producer_revision_id"], fixture["active_revision_id"]
    assert r0 != r1
    assert old["revision_id"] == r1 and old["startup_revision_id"] == r0
    assert invocation["revision_id"] == r0
    assert invocation["launch_settings"]["common"]["producing_revision"] == r0
    assert invocation["launch"]["producing_revision"] == r0
    assert invocation["pid"] is None and invocation["writer_state"] == "stopped"
    assert old["count"] == 1 and old["transitions"] == []
    assert old["cap"] == old["startup_cap"] == 3
    assert old["max_elapsed"] == old["startup_elapsed"] == 120
    instance, home, run = (
        fixture["instance_id"],
        fixture["state_home"],
        fixture["run_id"],
    )
    database = home / "instances" / instance / "state.sqlite3"
    with sqlite3.connect(database) as db:
        assert db.execute("SELECT revision_id FROM invocations").fetchone()[0] == r0
    before = database.read_bytes()
    for operation in (
        lambda: control.inspect_run(instance, run, state_home=home),
        lambda: control.recover_run(
            instance,
            run,
            state_home=home,
            request_id=new_id(),
            expected_state_version=old["state_version"],
        ),
    ):
        with pytest.raises(RouterError) as refused:
            operation()
        assert refused.value.code == "invocation_integrity"
        assert database.read_bytes() == before
