"""Admission regressions from the independent final boundary review."""

import sqlite3
from dataclasses import replace

import pytest
from tests.router.test_execution_boundary import (
    CounterAdapter,
    external_flow,
    generic_flow,
)

from millrace.router import control
from millrace.router.execution import RouterError
from millrace.router.ownership import file_lock, new_id


@pytest.mark.parametrize("actual_only", [False, True])
@pytest.mark.parametrize(
    "names",
    [
        ("Controls/x",),
        ("Gen/x", "gen/x"),
        ("A", "a/x"),
        ("A/x", "a"),
        ("caf\u00e9/x", "cafe\u0301/x"),
        ("\u00c9/x", "e\u0301/X"),
    ],
)
def test_generated_aliases_refuse_before_count(tmp_path, actual_only, names):
    class AliasCounter(CounterAdapter):
        enabled = False

        def plan(self, request):
            plan = super().plan(request)
            if self.enabled and (not actual_only or request.invocation_id != "0" * 32):
                return replace(plan, generated_controls={name: b"x" for name in names})
            return plan

    adapter = AliasCounter()
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
    adapter.enabled = True
    validation = control.validate_revision(
        instance, state_home=home, adapter_resolver=resolver
    )
    assert validation["valid"] is actual_only
    if not actual_only:
        assert validation["diagnostics"] == ["invalid_launch_plan"]
    started = control.start_run(
        instance,
        state_home=home,
        request_id=new_id(),
        expected_state_version=1,
        adapter_resolver=resolver,
    )
    if actual_only:
        result = control.inspect_run(
            instance, str(started["run_id"]), state_home=home, adapter_resolver=resolver
        )
        assert result["pause_reason"] == "invalid_launch_plan" and result["count"] == 0
        assert not result["invocations"]
    else:
        assert (
            started["status"] == "refused" and started["code"] == "invalid_launch_plan"
        )
        assert (
            control.inspect_instance(
                instance, state_home=home, adapter_resolver=resolver
            )["runs"]
            == []
        )
    assert not adapter.calls


def test_generated_unicode_spelling_is_preserved(tmp_path):
    name = "cafe\u0301/X"

    class UnicodeCounter(CounterAdapter):
        def plan(self, request):
            return replace(super().plan(request), generated_controls={name: b"x"})

    file, project, home, resolver = external_flow(tmp_path, UnicodeCounter())
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
    assert result["status"] == "succeeded"
    assert list(result["invocations"][0]["launch"]["generated_controls"]) == [name]


def _command_instance(tmp_path):
    file, project, home = generic_flow(tmp_path)
    created = control.create_instance(
        file, project, state_home=home, request_id=new_id(), expected_state_version=0
    )
    return str(created["instance_id"]), home


def _dump(instance, home):
    with sqlite3.connect(
        (home / "instances" / instance / "state.sqlite3").as_uri() + "?mode=ro",
        uri=True,
    ) as db:
        return list(db.iterdump())


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("contract", ["counter/v1", "counter/v2"])
def test_update_payload_contract_admitted_before_append_or_replay(
    tmp_path, existing, contract
):
    instance, home = _command_instance(tmp_path)
    adapter = CounterAdapter()

    def available(name, version):
        assert (name, version) == ("counter", contract)
        return adapter

    def unavailable(name, version):
        raise RouterError("unsupported_adapter_contract")

    kwargs = dict(
        instance_id=instance,
        state_home=home,
        request_id=new_id(),
        expected_state_version=2 if existing else 1,
        settings={
            "runtime_defaults": {
                "adapter_settings": {"counter": {"contract": contract, "config": {}}}
            }
        },
    )
    if existing:
        expected = control.update_instance_config(**kwargs, adapter_resolver=available)
        assert expected["code"] == "stale_state_version"
    before = _dump(instance, home)
    for resolver in (None, unavailable):
        with pytest.raises(RouterError, match="unsupported_adapter_contract"):
            control.update_instance_config(**kwargs, adapter_resolver=resolver)
        assert _dump(instance, home) == before
    resolved = control.update_instance_config(**kwargs, adapter_resolver=available)
    if existing:
        assert resolved == expected
    else:
        assert resolved["status"] == "applied"
        # Once stored, the existing stored-context guard still applies.
        with pytest.raises(RouterError, match="unsupported_adapter_contract"):
            control.inspect_instance(instance, state_home=home)


def test_invalid_update_settings_still_get_exact_refusal_receipt(tmp_path):
    instance, home = _command_instance(tmp_path)
    kwargs = dict(
        instance_id=instance,
        state_home=home,
        request_id=new_id(),
        expected_state_version=1,
        settings={"runtime_defaults": {"timeout_seconds": 0}},
    )
    receipt = control.update_instance_config(**kwargs)
    assert receipt["status"] == "refused" and receipt["code"] == "invalid_settings"
    assert control.update_instance_config(**kwargs) == receipt
    with sqlite3.connect(home / "instances" / instance / "state.sqlite3") as db:
        assert db.execute("SELECT COUNT(*) FROM controls").fetchone()[0] == 1


@pytest.mark.parametrize("actual_only", [False, True])
def test_plan_exception_becomes_invalid_launch_plan(tmp_path, actual_only):
    class BrokenCounter(CounterAdapter):
        enabled = False

        def plan(self, request):
            if self.enabled and (not actual_only or request.invocation_id != "0" * 32):
                raise ValueError("adapter bug")
            return super().plan(request)

    adapter = BrokenCounter()
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
    adapter.enabled = True
    validation = control.validate_revision(
        instance, state_home=home, adapter_resolver=resolver
    )
    assert validation["valid"] is actual_only
    started = control.start_run(
        instance,
        state_home=home,
        request_id=new_id(),
        expected_state_version=1,
        adapter_resolver=resolver,
    )
    if actual_only:
        result = control.inspect_run(
            instance, str(started["run_id"]), state_home=home, adapter_resolver=resolver
        )
        assert (
            result["status"] == "paused"
            and result["pause_reason"] == "invalid_launch_plan"
        )
        assert result["count"] == 0 and not result["invocations"]
        config = control.load_instance(instance, home, adapter_resolver=resolver)
        with file_lock(config.state_dir / ".controller.lock"):
            pass
    else:
        assert (
            started["status"] == "refused" and started["code"] == "invalid_launch_plan"
        )
    assert not adapter.calls


@pytest.mark.parametrize("exception", [KeyboardInterrupt, SystemExit])
def test_plan_process_control_exceptions_remain_visible(tmp_path, exception):
    class InterruptedCounter(CounterAdapter):
        enabled = False

        def plan(self, request):
            if self.enabled:
                raise exception()
            return super().plan(request)

    adapter = InterruptedCounter()
    file, project, home, resolver = external_flow(tmp_path, adapter)
    created = control.create_instance(
        file,
        project,
        state_home=home,
        request_id=new_id(),
        expected_state_version=0,
        adapter_resolver=resolver,
    )
    adapter.enabled = True
    with pytest.raises(exception):
        control.validate_revision(
            str(created["instance_id"]), state_home=home, adapter_resolver=resolver
        )


def test_invoke_exception_retains_counted_writer_uncertainty(tmp_path):
    class BrokenInvocation(CounterAdapter):
        def invoke(self, plan, **kwargs):
            raise ValueError("invocation failed without closure")

    file, project, home, resolver = external_flow(tmp_path, BrokenInvocation())
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
    assert result["pause_reason"] == "writer_unknown" and result["count"] == 1
    assert result["invocations"][0]["status"] == "uncertain"
    assert result["invocations"][0]["uncertainty_diagnostic"] == "adapter_exception"
    assert result["completions"] == []
    assert (project / ".millrace-router-owner/owner.json").exists()
