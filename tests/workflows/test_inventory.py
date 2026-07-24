from __future__ import annotations

from dataclasses import FrozenInstanceError, fields
from pathlib import Path
from typing import cast

import pytest

from millrace.compiler import compile_workflow
from millrace.compiler.canonical import authority_fingerprint
from millrace.contracts import SelectedCompiledPlan
from millrace.workflows import kernel_ping

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ONLY_DONOR_MODULES = {
    "lad_execution",
    "lad_learning",
    "lad_planning",
    "simple_loop",
    "vendor_selection",
}


def _compile_plan(source: dict[str, object]) -> SelectedCompiledPlan:
    result = compile_workflow(source)
    assert [
        diagnostic
        for diagnostic in result.diagnostics
        if diagnostic.severity == "error"
    ] == []
    assert result.plan is not None
    return result.plan


def test_public_included_workflow_inventory_lists_only_kernel_ping() -> None:
    import millrace.workflows as workflows
    from millrace.workflows.inventory import (
        INCLUDED_WORKFLOW_IDS,
        IncludedWorkflow,
        included_workflow_source,
        included_workflows,
    )

    assert workflows.__all__ == (
        "kernel_ping",
        "IncludedWorkflow",
        "INCLUDED_WORKFLOW_IDS",
        "included_workflows",
        "included_workflow_source",
    )
    assert INCLUDED_WORKFLOW_IDS == ("kernel_ping",)
    assert tuple(field.name for field in fields(IncludedWorkflow)) == (
        "workflow_id",
        "workflow_version",
        "display_name",
        "source_module",
        "provenance",
    )

    entry = IncludedWorkflow(
        workflow_id="kernel_ping",
        workflow_version="0.1",
        display_name="Kernel Ping",
        source_module="millrace.workflows.kernel_ping",
        provenance="base-included-diagnostic",
    )
    assert included_workflows() == (entry,)
    assert tuple(workflow.workflow_id for workflow in included_workflows()) == (
        "kernel_ping",
    )

    with pytest.raises(FrozenInstanceError):
        included_workflows()[0].display_name = "Mutated"

    transitional_fixture_names = {
        "lad_execution",
        "lad_learning",
        "lad_planning",
        "simple_loop",
        "vendor_selection",
    }
    assert transitional_fixture_names.isdisjoint(workflows.__all__)
    assert transitional_fixture_names.isdisjoint(INCLUDED_WORKFLOW_IDS)
    for workflow_id in transitional_fixture_names:
        with pytest.raises(KeyError) as exc_info:
            included_workflow_source(workflow_id)
        assert exc_info.value.args == (workflow_id,)


def test_included_workflow_source_returns_fresh_kernel_ping_source() -> None:
    from millrace.workflows.inventory import included_workflow_source

    first = included_workflow_source("kernel_ping")
    second = included_workflow_source("kernel_ping")

    assert first == kernel_ping.workflow_source()
    assert first == second
    assert first is not second

    first_workflow = cast(dict[str, object], first["workflow"])
    first_workflow["id"] = "mutated"

    third = included_workflow_source("kernel_ping")
    third_workflow = cast(dict[str, object], third["workflow"])
    assert third_workflow["id"] == "kernel_ping"


def test_unknown_included_workflow_source_raises_requested_key() -> None:
    from millrace.workflows.inventory import included_workflow_source

    with pytest.raises(KeyError) as exc_info:
        included_workflow_source("unknown")

    assert exc_info.value.args == ("unknown",)


def test_inventory_kernel_ping_source_compiles_to_direct_authority() -> None:
    from millrace.workflows.inventory import included_workflow_source

    direct_plan = _compile_plan(kernel_ping.workflow_source())
    inventory_plan = _compile_plan(included_workflow_source("kernel_ping"))

    assert inventory_plan == direct_plan
    assert authority_fingerprint(inventory_plan) == authority_fingerprint(
        direct_plan
    )


def test_donor_workflow_modules_are_documented_source_only_fixtures() -> None:
    workflows_readme = PROJECT_ROOT / "src" / "millrace" / "workflows" / "README.md"
    workflows_dir = PROJECT_ROOT / "src" / "millrace" / "workflows"
    readme = workflows_readme.read_text(encoding="utf-8")

    assert "source-only" in readme
    for module_name in SOURCE_ONLY_DONOR_MODULES:
        assert (workflows_dir / f"{module_name}.py").is_file()
        assert module_name in readme
