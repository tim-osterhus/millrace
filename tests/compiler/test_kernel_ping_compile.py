from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol, cast

from millrace.compiler import CompileResult, compile_workflow
from millrace.compiler.runner_bindings import (
    DEFAULT_SELECTED_RUNNER_ADAPTER_POLICY,
    RUNNER_ADAPTER_KIND_DEFAULTED,
    SelectedRunnerAdapterPolicy,
)
from millrace.contracts import (
    ActionId,
    ArtifactSchemaId,
    CapabilityId,
    OutcomeId,
    PartitionId,
    QueueFamilyId,
    RunnerBindingId,
    StageKindId,
    WorkflowId,
    WorkflowVersion,
)
from millrace.workflows import kernel_ping

_CODEX_POLICY = SelectedRunnerAdapterPolicy(
    default_adapter_kind="codex",
    supported_adapter_kinds=frozenset({"codex"}),
    component_bound_adapter_kinds=frozenset(),
    default_component_selector=None,
    default_component_required_capability_ids=frozenset(),
    default_component_requires_complete_mappings=False,
)


class _HasId(Protocol):
    @property
    def id(self) -> object: ...


def _id_values(records: Iterable[_HasId]) -> set[str]:
    return {str(record.id) for record in records}


def _source_records(source: dict[str, object], key: str) -> list[dict[str, object]]:
    return cast(list[dict[str, object]], source[key])


def _source_ids(source: dict[str, object], key: str) -> set[str]:
    return {str(record["id"]) for record in _source_records(source, key)}


def _source_markers(source: dict[str, object]) -> set[str]:
    return {
        str(record["marker"]) for record in _source_records(source, "terminal_outcomes")
    }


def _collapse_to_one_runner(source: dict[str, object]) -> dict[str, object]:
    runner = _source_records(source, "runner_bindings")[0]
    runner_id = str(runner["id"])
    source["runner_bindings"] = [runner]
    for stage in _source_records(source, "stage_kinds"):
        stage["runner_binding_id"] = runner_id
    for route in _source_records(source, "external_enqueue_routes"):
        route["runner_binding_id"] = runner_id
    for action in _source_records(source, "terminal_actions"):
        if action.get("runner_binding_id") is not None:
            action["runner_binding_id"] = runner_id
    runner["stage_kind_ids"] = (
        "kernel_ping.taskmaster",
        "kernel_ping.worker",
    )
    return runner


def _source_with_runner_component() -> dict[str, object]:
    source = kernel_ping.workflow_source()
    source["capabilities"] = [
        {
            "id": "capability.runner.invoke",
            "kind": "runner.invoke",
            "support_status": "supported",
            "grant_status": "granted",
        }
    ]
    runner = _collapse_to_one_runner(source)
    runner.update(
        {
            "adapter_kind": "codex",
            "required_capability_ids": ("capability.runner.invoke",),
            "component_pin": {
                "component_kind": "opaque.runner",
                "component_id": "example.component",
                "component_version": "1.2.3",
                "provider_distribution": "example-provider",
                "provider_version": "4.5.6",
                "descriptor_media_type": "application/vnd.example.runner+json",
                "descriptor_sha256": "a" * 64,
                "required_capability_ids": ("capability.runner.invoke",),
                "legal_terminal_result_ids": ("COMPLETE", "BLOCKED"),
            },
            "terminal_result_mappings": (
                {
                    "stage_kind_id": "kernel_ping.taskmaster",
                    "runner_result_id": "COMPLETE",
                    "outcome_id": "kernel_ping.taskmaster.task_complete",
                },
            ),
        }
    )
    return source


def _component_free_codex_source() -> dict[str, object]:
    source = kernel_ping.workflow_source()
    source["capabilities"] = [
        {
            "id": "capability.runner.invoke",
            "kind": "runner.invoke",
            "support_status": "supported",
            "grant_status": "granted",
        }
    ]
    for runner in _source_records(source, "runner_bindings"):
        runner["adapter_kind"] = "codex"
        runner["required_capability_ids"] = ("capability.runner.invoke",)
        runner.pop("component_pin", None)
        runner.pop("terminal_result_mappings", None)
    return source


def test_generic_runner_component_pin_compiles_into_selected_authority() -> None:
    source = _source_with_runner_component()
    runner = _source_records(source, "runner_bindings")[0]
    pin = cast(dict[str, object], runner["component_pin"])
    pin["max_work_item_payload_bytes"] = 16_384

    result = compile_workflow(source)

    assert result.plan is not None
    assert [item for item in result.diagnostics if item.severity == "error"] == []
    binding = result.plan.runner_bindings[0]
    assert binding.schema_version == 3
    assert binding.component_pin is not None
    assert binding.component_pin.component_kind == "opaque.runner"
    assert binding.component_pin.component_id == "example.component"
    assert binding.component_pin.component_version == "1.2.3"
    assert binding.component_pin.provider_distribution == "example-provider"
    assert binding.component_pin.provider_version == "4.5.6"
    assert (
        binding.component_pin.descriptor_media_type
        == "application/vnd.example.runner+json"
    )
    assert binding.component_pin.descriptor_sha256 == "a" * 64
    assert binding.component_pin.max_work_item_payload_bytes == 16_384
    assert binding.component_pin.required_capability_ids == (
        CapabilityId("capability.runner.invoke"),
    )
    assert binding.component_pin.legal_terminal_result_ids == (
        "BLOCKED",
        "COMPLETE",
    )
    assert len(binding.terminal_result_mappings) == 1
    mapping = binding.terminal_result_mappings[0]
    assert mapping.stage_kind_id == StageKindId("kernel_ping.taskmaster")
    assert mapping.runner_result_id == "COMPLETE"
    assert mapping.outcome_id == OutcomeId("kernel_ping.taskmaster.task_complete")


def test_component_free_codex_binding_compiles_as_format_17() -> None:
    result = compile_workflow(
        _component_free_codex_source(),
        selected_runner_policy=_CODEX_POLICY,
    )

    assert result.plan is not None
    assert result.plan.schema_version == 17
    assert all(binding.component_pin is None for binding in result.plan.runner_bindings)
    assert all(
        binding.terminal_result_mappings == ()
        for binding in result.plan.runner_bindings
    )


def test_custom_runner_adapter_policy_can_omit_millforge() -> None:
    policy = SelectedRunnerAdapterPolicy(
        default_adapter_kind="codex.custom",
        supported_adapter_kinds=frozenset({"codex.custom"}),
        component_bound_adapter_kinds=frozenset(),
        default_component_selector=None,
        default_component_required_capability_ids=frozenset(),
        default_component_requires_complete_mappings=False,
    )

    assert policy.component_bound_adapter_kinds == frozenset()


def test_fresh_runner_adapter_policy_selects_configured_millforge_default() -> None:
    policy = SelectedRunnerAdapterPolicy()

    assert policy.default_adapter_kind == "millforge"
    assert policy.supported_adapter_kinds == frozenset({"codex", "millforge"})
    assert policy.component_bound_adapter_kinds == frozenset({"millforge"})


def test_explicit_runner_selections_are_unchanged_by_millforge_default() -> None:
    codex_source = kernel_ping.workflow_source()
    for runner in _source_records(codex_source, "runner_bindings"):
        runner["adapter_kind"] = "codex"
        runner.pop("component_pin", None)
        runner.pop("terminal_result_mappings", None)

    codex_result = compile_workflow(codex_source)

    assert codex_result.plan is not None
    assert {binding.adapter_kind for binding in codex_result.plan.runner_bindings} == {
        "codex"
    }
    assert all(
        binding.component_pin is None and binding.terminal_result_mappings == ()
        for binding in codex_result.plan.runner_bindings
    )
    assert [
        diagnostic
        for diagnostic in codex_result.diagnostics
        if diagnostic.code == RUNNER_ADAPTER_KIND_DEFAULTED
    ] == []

    millforge_source = _source_with_runner_component()
    _source_records(millforge_source, "runner_bindings")[0]["adapter_kind"] = (
        "millforge"
    )
    millforge_result = compile_workflow(millforge_source)

    assert millforge_result.plan is not None
    assert len(millforge_result.plan.runner_bindings[0].terminal_result_mappings) == 1
    assert DEFAULT_SELECTED_RUNNER_ADAPTER_POLICY.default_adapter_kind == "millforge"


def test_kernel_ping_authors_current_millforge_default_authority() -> None:
    source = kernel_ping.workflow_source()
    selected_capabilities = (
        "capability.runner.invoke",
        "terminal.intent",
        "unrestricted.filesystem.read",
        "unrestricted.filesystem.write",
        "unrestricted.process.execute",
    )
    expected = {
        "kernel_ping.taskmaster_runner": {
            "stages": ("kernel_ping.taskmaster",),
            "results": ("BLOCKED", "TASK_COMPLETE"),
            "digest": (
                "0bace7b27871b03cd7ffe59951953348b3da3214536178d6f"
                "447a21de4403464"
            ),
        },
        "kernel_ping.worker_runner": {
            "stages": ("kernel_ping.worker",),
            "results": ("BLOCKED", "NEEDS_REVIEW", "WORK_COMPLETE"),
            "digest": (
                "d6b5c75f48565b939ee4d6e30b83e3ad203764b7bda0289"
                "0ca515a9bfb3318f0"
            ),
        },
    }

    assert (
        tuple(
            capability["id"] for capability in _source_records(source, "capabilities")
        )
        == selected_capabilities
    )
    assert {
        runner["id"] for runner in _source_records(source, "runner_bindings")
    } == set(expected)
    for runner in _source_records(source, "runner_bindings"):
        authority = expected[str(runner["id"])]
        pin = cast(dict[str, object], runner["component_pin"])
        assert runner["adapter_kind"] == "fake_local"
        assert runner["stage_kind_ids"] == authority["stages"]
        assert runner["required_capability_ids"] == selected_capabilities
        assert pin == {
            "component_kind": "runner",
            "component_id": "millforge-base",
            "component_version": "2",
            "provider_distribution": "millforge",
            "provider_version": "0.1.0",
            "descriptor_media_type": "application/json",
            "descriptor_sha256": authority["digest"],
            "required_capability_ids": selected_capabilities[1:],
            "legal_terminal_result_ids": authority["results"],
        }
        assert {
            (mapping["stage_kind_id"], mapping["runner_result_id"])
            for mapping in cast(
                tuple[dict[str, object], ...], runner["terminal_result_mappings"]
            )
        } == {
            (stage_id, result_id)
            for stage_id in cast(tuple[str, ...], authority["stages"])
            for result_id in cast(tuple[str, ...], authority["results"])
        }

    result = compile_workflow(source)

    assert result.plan is not None
    assert {binding.adapter_kind for binding in result.plan.runner_bindings} == {
        "millforge"
    }
    assert [
        warning.code for warning in result.diagnostics if warning.severity == "warning"
    ] == [RUNNER_ADAPTER_KIND_DEFAULTED, RUNNER_ADAPTER_KIND_DEFAULTED]


def test_kernel_ping_fixture_compiles_into_selected_authority() -> None:
    source = kernel_ping.WORKFLOW_SOURCE

    result = compile_workflow(source)

    assert result.plan is not None
    assert [
        diagnostic
        for diagnostic in result.diagnostics
        if diagnostic.severity == "error"
    ] == []
    warnings = [
        diagnostic
        for diagnostic in result.diagnostics
        if diagnostic.severity == "warning"
    ]
    assert [warning.code for warning in warnings] == [
        RUNNER_ADAPTER_KIND_DEFAULTED,
        RUNNER_ADAPTER_KIND_DEFAULTED,
    ]
    warning = warnings[0]
    assert warning.declaration_path == "runner_bindings[0].adapter_kind"
    assert warning.context["runner_binding_id"] == "kernel_ping.taskmaster_runner"
    assert warning.context["original_adapter_kind"] == "fake_local"
    assert warning.context["default_adapter_kind"] == "millforge"

    plan = result.plan
    assert plan.workflow.workflow_id == WorkflowId("kernel_ping")
    assert plan.workflow.workflow_version == WorkflowVersion("0.1")
    assert plan.workflow.workflow_name == "Kernel Ping"
    assert plan.compatibility_profile is None
    assert plan.required_extensions == ()

    assert _id_values(plan.partitions) == _source_ids(source, "partitions")
    assert PartitionId("craft") in {partition.id for partition in plan.partitions}

    assert _id_values(plan.queue_families) == _source_ids(source, "queue_families")
    assert {
        QueueFamilyId("prompt"),
        QueueFamilyId("task_artifact"),
        QueueFamilyId("task_incident"),
    } <= {queue_family.id for queue_family in plan.queue_families}

    assert _id_values(plan.external_enqueue_routes) == _source_ids(
        source,
        "external_enqueue_routes",
    )
    assert len(plan.external_enqueue_routes) == 1
    external_route = plan.external_enqueue_routes[0]
    assert external_route.queue_family_id == QueueFamilyId("prompt")
    assert external_route.graph_node_id == "kernel_ping.taskmaster.start"
    assert external_route.stage_kind_id == StageKindId("kernel_ping.taskmaster")
    assert external_route.runner_binding_id == RunnerBindingId(
        "kernel_ping.taskmaster_runner"
    )

    assert _id_values(plan.stage_kinds) == _source_ids(source, "stage_kinds")
    assert {
        StageKindId("kernel_ping.taskmaster"),
        StageKindId("kernel_ping.worker"),
    } <= {stage_kind.id for stage_kind in plan.stage_kinds}

    assert _id_values(plan.terminal_outcomes) == _source_ids(
        source,
        "terminal_outcomes",
    )
    assert _source_markers(source) == {
        outcome.marker for outcome in plan.terminal_outcomes
    }

    assert _id_values(plan.terminal_actions) == _source_ids(
        source,
        "terminal_actions",
    )
    assert {
        ActionId("kernel_ping.route_taskmaster_success"),
        ActionId("kernel_ping.pause_taskmaster_blocked"),
        ActionId("kernel_ping.close_worker_success"),
        ActionId("kernel_ping.route_worker_review"),
        ActionId("kernel_ping.pause_worker_blocked"),
    } == {action.id for action in plan.terminal_actions}

    assert {str(runner.id): runner.adapter_kind for runner in plan.runner_bindings} == {
        "kernel_ping.taskmaster_runner": "millforge",
        "kernel_ping.worker_runner": "millforge",
    }

    success_action = next(
        action
        for action in plan.terminal_actions
        if action.id == ActionId("kernel_ping.route_taskmaster_success")
    )
    assert success_action.target_graph_node_id == "kernel_ping.worker.start"
    assert success_action.payload_projection == {
        "kind": "source",
        "path": ("artifact_payload",),
    }

    incident_schema = next(
        schema
        for schema in plan.artifact_schemas
        if schema.id == ArtifactSchemaId("kernel_ping.task_incident")
    )
    incident_required = cast(tuple[str, ...], incident_schema.schema["required"])
    assert set(incident_required) >= {
        "incident_kind",
        "incident_version",
        "source_prompt_id",
        "source_task_artifact_id",
        "worker_run_id",
        "reason",
        "worker_summary",
        "missing_details",
        "requested_taskmaster_action",
    }

    review_action = next(
        action
        for action in plan.terminal_actions
        if action.id == ActionId("kernel_ping.route_worker_review")
    )
    assert review_action.target_stage_kind_id == StageKindId("kernel_ping.taskmaster")
    assert review_action.target_graph_node_id == "kernel_ping.taskmaster.review"
    assert review_action.emitted_queue_family_id == QueueFamilyId("task_incident")
    assert review_action.artifact_schema_id == ArtifactSchemaId(
        "kernel_ping.task_incident"
    )
    assert review_action.runner_binding_id == RunnerBindingId(
        "kernel_ping.taskmaster_runner"
    )
    assert review_action.payload_projection == {
        "kind": "object",
        "fields": {
            "incident_kind": {
                "kind": "literal",
                "value": "kernel_ping.task_incident",
            },
            "incident_version": {"kind": "literal", "value": 1},
            "source_prompt_id": {
                "kind": "source",
                "path": ("work_item_payload", "source_prompt_id"),
            },
            "source_task_artifact_id": {
                "kind": "source",
                "path": ("run_metadata", "work_item_id"),
            },
            "worker_run_id": {
                "kind": "source",
                "path": ("run_metadata", "run_id"),
            },
            "reason": {
                "kind": "literal",
                "value": "insufficient_task_detail",
            },
            "worker_summary": {
                "kind": "source",
                "path": ("artifact_payload", "worker_summary"),
            },
            "missing_details": {
                "kind": "source",
                "path": ("artifact_payload", "missing_details"),
            },
            "requested_taskmaster_action": {
                "kind": "literal",
                "value": "revise_task_artifact",
            },
        },
    }


def test_compiler_package_reexports_public_compile_api() -> None:
    result = compile_workflow(kernel_ping.WORKFLOW_SOURCE)

    assert isinstance(result, CompileResult)
    assert result.plan is not None


def test_stages_can_select_distinct_runner_binding_timeouts() -> None:
    source = _component_free_codex_source()
    stages = _source_records(source, "stage_kinds")
    runners = _source_records(source, "runner_bindings")
    runners[0]["stage_kind_ids"] = ("kernel_ping.taskmaster",)
    runners[0]["invocation_timeout_seconds"] = 3600
    runners[1]["invocation_timeout_seconds"] = 1800
    next(stage for stage in stages if stage["id"] == "kernel_ping.worker")[
        "runner_binding_id"
    ] = "kernel_ping.worker_runner"

    result = compile_workflow(source, selected_runner_policy=_CODEX_POLICY)

    assert result.plan is not None
    assert {
        str(binding.id): binding.invocation_timeout_seconds
        for binding in result.plan.runner_bindings
    } == {
        "kernel_ping.taskmaster_runner": 3600,
        "kernel_ping.worker_runner": 1800,
    }


def test_kernel_ping_compile_accepts_explicit_millforge_component_authority() -> None:
    source = _source_with_runner_component()
    source["capabilities"] = [
        {
            "id": "terminal.intent",
            "kind": "runner.invoke",
            "support_status": "supported",
            "grant_status": "granted",
        }
    ]
    runner = _source_records(source, "runner_bindings")[0]
    runner.update(
        {
            "adapter_kind": "millforge",
            "required_capability_ids": ("terminal.intent",),
            "component_pin": {
                "component_kind": "runner",
                "component_id": "millforge-base",
                "component_version": "1",
                "provider_distribution": "millforge",
                "provider_version": "0.1.0",
                "descriptor_media_type": "application/json",
                "descriptor_sha256": "a" * 64,
                "required_capability_ids": ("terminal.intent",),
                "legal_terminal_result_ids": ("COMPLETE",),
            },
            "terminal_result_mappings": (
                {
                    "stage_kind_id": "kernel_ping.taskmaster",
                    "runner_result_id": "COMPLETE",
                    "outcome_id": "kernel_ping.taskmaster.task_complete",
                },
            ),
        }
    )

    result = compile_workflow(source)

    assert result.plan is not None
    assert result.plan.runner_bindings[0].adapter_kind == "millforge"


def test_selected_authority_contains_no_legacy_defaults() -> None:
    result = compile_workflow(kernel_ping.WORKFLOW_SOURCE)

    assert result.plan is not None
    rendered = repr(result.plan)

    assert "compatibility_profile='" not in rendered
    assert "execution" not in rendered
    assert "planning" not in rendered
    assert "learning" not in rendered
    assert "simple_loop" not in rendered
    assert "lad_codex" not in rendered
