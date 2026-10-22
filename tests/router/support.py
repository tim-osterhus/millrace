"""Pilot assertions expressed through the strict request API, without API bypass."""

from collections.abc import Callable
from pathlib import Path
from typing import Any

from millrace.router import control
from millrace.router.control import load_instance as load_instance
from millrace.router.records import RouterError
from millrace.router.store import new_id


def _projection(instance: str, run: str, home: Path) -> dict[str, Any]:
    result = control.inspect_run(instance, run, state_home=home)
    # New receipt rows legitimately accumulate on every explicit recovery.
    # All execution/version/revision/writer history is left intact.
    result["controls"] = []
    return result


def create_instance(
    workflow: Path, project: Path, *, state_home: Path, **kwargs: Any
) -> dict[str, Any]:
    result = control.create_instance(
        workflow,
        project,
        state_home=state_home,
        request_id=new_id(),
        expected_state_version=0,
        **kwargs,
    )
    if result["status"] == "refused":
        raise RouterError(str(result["code"]))
    return result


def start_run(
    instance: str,
    *,
    state_home: Path,
    edit_mode: str = "locked",
    on_started: Callable[[dict[str, object]], None] | None = None,
) -> dict[str, Any]:
    version = control.inspect_instance(instance, state_home=state_home)["state_version"]
    receipt = control.start_run(
        instance,
        state_home=state_home,
        request_id=new_id(),
        expected_state_version=version,
        edit_mode=edit_mode,
        on_started=on_started,
    )
    if receipt["status"] == "refused":
        raise RouterError(str(receipt["code"]))
    return _projection(instance, str(receipt["run_id"]), state_home)


def inspect_run(instance: str, run: str, *, state_home: Path) -> dict[str, Any]:
    return _projection(instance, run, state_home)


def recover_run(instance: str, run: str, *, state_home: Path) -> dict[str, Any]:
    before = control.inspect_run(instance, run, state_home=state_home)
    version = before["state_version"]
    request_id = new_id()
    receipt = control.recover_run(
        instance,
        run,
        state_home=state_home,
        request_id=request_id,
        expected_state_version=version,
    )
    if receipt["status"] == "refused" and receipt["code"] != "stale_state_version":
        raise RouterError(str(receipt["code"]))
    if receipt["status"] == "refused":
        after = control.inspect_run(instance, run, state_home=state_home)
        assert any(
            item["status"] in {"reserved", "active"} for item in before["invocations"]
        )
        assert after["state_version"] > version
        assert after["state_version"] == receipt["state_version"]
        assert after["count"] == before["count"]
        assert after["status"] == "paused" and after["pause_reason"] == "writer_unknown"
        assert (
            control.recover_run(
                instance,
                run,
                state_home=state_home,
                request_id=request_id,
                expected_state_version=version,
            )
            == receipt
        )
    return _projection(instance, run, state_home)


def command_graph_json(value: Any, *args: Any, **kwargs: Any) -> str:
    """Author existing command regression fixtures using the explicit v3 envelope."""
    import copy
    import json

    data = copy.deepcopy(value)
    if isinstance(data, dict) and data.get("format") == "millrace-router/v3":
        controls = data.setdefault("control_files", [])
        for binding in data.get("bindings", {}).values():
            if isinstance(binding, dict):
                binding.setdefault("contract", "millrace-command/v1")
                binding.setdefault("config", {})
        for node in (
            node
            for plane in data.get("planes", {}).values()
            for node in plane.get("nodes", {}).values()
        ):
            invoke = node.get("invoke", {})
            config = invoke.setdefault("config", {})
            for key in ("script", "args"):
                if key in invoke:
                    config[key] = invoke.pop(key)
            if (
                "script" in config
                and isinstance(config["script"], str)
                and config["script"] not in controls
            ):
                controls.append(config["script"])
    return json.dumps(data, *args, **kwargs)
