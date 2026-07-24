from __future__ import annotations

from collections.abc import Iterable
from copy import deepcopy
from typing import cast

import pytest

from millrace.compiler import compile_workflow
from millrace.compiler.runner_bindings import (
    RUNNER_ADAPTER_KIND_DEFAULTED,
    RUNNER_ADAPTER_KIND_UNSUPPORTED,
    SelectedRunnerAdapterPolicy,
)
from millrace.contracts import Diagnostic
from millrace.workflows import kernel_ping
from support import kernel_ping as kernel_ping_support

Source = dict[str, object]
Record = dict[str, object]

_MILLFORGE_COMPONENT_CAPABILITY_IDS = (
    "terminal.intent",
    "unrestricted.filesystem.read",
    "unrestricted.filesystem.write",
    "unrestricted.process.execute",
)

_CODEX_POLICY = SelectedRunnerAdapterPolicy(
    default_adapter_kind="codex",
    supported_adapter_kinds=frozenset({"codex"}),
    component_bound_adapter_kinds=frozenset(),
    default_component_selector=None,
    default_component_required_capability_ids=frozenset(),
    default_component_requires_complete_mappings=False,
)


def _source() -> Source:
    source = deepcopy(kernel_ping.WORKFLOW_SOURCE)
    runner = _collapse_to_one_runner(source)
    previous_runner_id = str(runner["id"])
    runner.update(
        {
            "id": "kernel_ping.fake_local_runner",
            "adapter_kind": "fake_local",
            "stage_kind_ids": ("kernel_ping.taskmaster", "kernel_ping.worker"),
            "required_capability_ids": ("capability.runner.invoke",),
        }
    )
    runner.pop("component_pin", None)
    runner.pop("terminal_result_mappings", None)
    source["capabilities"] = [
        {
            "id": "capability.runner.invoke",
            "kind": "runner.invoke",
            "support_status": "supported",
            "grant_status": "granted",
        }
    ]
    for stage in _records(source, "stage_kinds"):
        if stage["runner_binding_id"] == previous_runner_id:
            stage["runner_binding_id"] = runner["id"]
    for route in _records(source, "external_enqueue_routes"):
        if route["runner_binding_id"] == previous_runner_id:
            route["runner_binding_id"] = runner["id"]
    for action in _records(source, "terminal_actions"):
        if action.get("runner_binding_id") == previous_runner_id:
            action["runner_binding_id"] = runner["id"]
    return source


def _workflow(source: Source) -> Record:
    return cast(Record, source["workflow"])


def _records(source: Source, key: str) -> list[Record]:
    return cast(list[Record], source[key])


def _errors(source: Source) -> tuple[Diagnostic, ...]:
    result = compile_workflow(source, selected_runner_policy=_CODEX_POLICY)
    assert result.plan is None
    return tuple(
        diagnostic
        for diagnostic in result.diagnostics
        if diagnostic.severity == "error"
    )


def _find_error(errors: Iterable[Diagnostic], code: str) -> Diagnostic:
    matches = [diagnostic for diagnostic in errors if diagnostic.code == code]
    assert matches, f"missing diagnostic code {code!r} in {tuple(errors)!r}"
    return matches[0]


def _collapse_to_one_runner(source: Source) -> Record:
    runner = _records(source, "runner_bindings")[0]
    runner_id = str(runner["id"])
    source["runner_bindings"] = [runner]
    for stage in _records(source, "stage_kinds"):
        stage["runner_binding_id"] = runner_id
    for route in _records(source, "external_enqueue_routes"):
        route["runner_binding_id"] = runner_id
    for action in _records(source, "terminal_actions"):
        if action.get("runner_binding_id") is not None:
            action["runner_binding_id"] = runner_id
    runner["stage_kind_ids"] = (
        "kernel_ping.taskmaster",
        "kernel_ping.worker",
    )
    return runner


def _source_with_runner_component() -> Source:
    source = _source()
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


def _source_with_defaultable_millforge_component() -> Source:
    source = _source()
    selected_capability_ids = (
        "capability.runner.invoke",
        *_MILLFORGE_COMPONENT_CAPABILITY_IDS,
    )
    source["capabilities"] = [
        {
            "id": capability_id,
            "kind": "runner.invoke",
            "support_status": "supported",
            "grant_status": "granted",
        }
        for capability_id in selected_capability_ids
    ]
    runner = _collapse_to_one_runner(source)
    runner.update(
        {
            "adapter_kind": "fake_local",
            "required_capability_ids": selected_capability_ids,
            "component_pin": {
                "component_kind": "runner",
                "component_id": "millforge-base",
                "component_version": "2",
                "provider_distribution": "millforge",
                "provider_version": "0.1.0",
                "descriptor_media_type": "application/json",
                "descriptor_sha256": "a" * 64,
                "required_capability_ids": _MILLFORGE_COMPONENT_CAPABILITY_IDS,
                "legal_terminal_result_ids": ("BLOCKED",),
            },
            "terminal_result_mappings": (
                {
                    "stage_kind_id": "kernel_ping.taskmaster",
                    "runner_result_id": "BLOCKED",
                    "outcome_id": "kernel_ping.taskmaster.blocked",
                },
                {
                    "stage_kind_id": "kernel_ping.worker",
                    "runner_result_id": "BLOCKED",
                    "outcome_id": "kernel_ping.worker.blocked",
                },
            ),
        }
    )
    return source


def test_defaulted_component_binding_requires_policy_compatible_authored_authority() -> None:  # noqa: E501
    compatible = compile_workflow(_source_with_defaultable_millforge_component())

    assert compatible.plan is not None
    assert compatible.plan.runner_bindings[0].adapter_kind == "millforge"
    assert [
        diagnostic.code
        for diagnostic in compatible.diagnostics
        if diagnostic.severity == "warning"
    ] == [RUNNER_ADAPTER_KIND_DEFAULTED]

    for case in ("missing_pin", "mismatched_selector"):
        source = _source_with_defaultable_millforge_component()
        runner = _records(source, "runner_bindings")[0]
        if case == "missing_pin":
            runner.pop("component_pin")
            runner.pop("terminal_result_mappings")
        else:
            pin = cast(Record, runner["component_pin"])
            pin["component_version"] = "3"

        result = compile_workflow(source)

        assert result.plan is None
        error = _find_error(
            result.diagnostics,
            "runner_default_component_authority_incompatible",
        )
        assert error.declaration_path.startswith("runner_bindings[0]")


@pytest.mark.parametrize(
    ("case", "expected_code"),
    (
        ("absent_component_capability", "runner_default_component_capability_unusable"),
        (
            "unsupported_component_capability",
            "runner_default_component_capability_unusable",
        ),
        ("denied_component_capability", "runner_default_component_capability_unusable"),
        (
            "approval_pending_component_capability",
            "runner_default_component_capability_unusable",
        ),
        ("no_selected_runner_invoke", "runner_binding_missing_runner_invoke"),
    ),
)
def test_defaulted_component_binding_requires_usable_capability_authority(
    case: str,
    expected_code: str,
) -> None:
    compatible = compile_workflow(_source_with_defaultable_millforge_component())
    assert compatible.plan is not None

    source = _source_with_defaultable_millforge_component()
    capabilities = _records(source, "capabilities")
    runner = _records(source, "runner_bindings")[0]
    pin = cast(Record, runner["component_pin"])
    if case == "absent_component_capability":
        missing_id = _MILLFORGE_COMPONENT_CAPABILITY_IDS[0]
        source["capabilities"] = [
            capability for capability in capabilities if capability["id"] != missing_id
        ]
        runner["required_capability_ids"] = tuple(
            capability_id
            for capability_id in cast(
                tuple[str, ...], runner["required_capability_ids"]
            )
            if capability_id != missing_id
        )
        pin["required_capability_ids"] = tuple(
            capability_id
            for capability_id in cast(tuple[str, ...], pin["required_capability_ids"])
            if capability_id != missing_id
        )
    elif case == "unsupported_component_capability":
        capabilities[1]["support_status"] = "unsupported"
    elif case == "denied_component_capability":
        capabilities[1]["grant_status"] = "denied"
    elif case == "approval_pending_component_capability":
        capabilities[1]["grant_status"] = "approval_pending"
    else:
        source["capabilities"] = []
        runner["required_capability_ids"] = ()
        pin["required_capability_ids"] = ()

    result = compile_workflow(source)

    assert result.plan is None
    _find_error(result.diagnostics, expected_code)


@pytest.mark.parametrize(
    ("case", "expected_code"),
    (
        ("missing", "runner_default_component_mapping_incomplete"),
        ("extra", "duplicate_runner_terminal_result_outcome"),
        ("duplicate", "duplicate_runner_terminal_result_mapping"),
        ("foreign_stage", "runner_terminal_mapping_stage_not_owned"),
        ("wrong_outcome", "runner_terminal_mapping_outcome_stage_mismatch"),
        ("unsupported_result", "unknown_runner_terminal_result"),
    ),
)
def test_defaulted_component_binding_requires_complete_stage_mappings(
    case: str,
    expected_code: str,
) -> None:
    compatible = compile_workflow(_source_with_defaultable_millforge_component())
    assert compatible.plan is not None

    source = _source_with_defaultable_millforge_component()
    runner = _records(source, "runner_bindings")[0]
    mappings = list(cast(tuple[Record, ...], runner["terminal_result_mappings"]))
    if case == "missing":
        mappings.pop()
    elif case == "extra":
        pin = cast(Record, runner["component_pin"])
        pin["legal_terminal_result_ids"] = ("BLOCKED", "EXTRA")
        mappings.append(
            {
                "stage_kind_id": "kernel_ping.taskmaster",
                "runner_result_id": "EXTRA",
                "outcome_id": "kernel_ping.taskmaster.blocked",
            }
        )
    elif case == "duplicate":
        mappings.append(dict(mappings[0]))
    elif case == "foreign_stage":
        runner["stage_kind_ids"] = ("kernel_ping.taskmaster",)
    elif case == "wrong_outcome":
        mappings[0]["outcome_id"] = "kernel_ping.worker.blocked"
    else:
        mappings[0]["runner_result_id"] = "UNKNOWN"
    runner["terminal_result_mappings"] = tuple(mappings)

    result = compile_workflow(source)

    assert result.plan is None
    _find_error(result.diagnostics, expected_code)


def test_kernel_ping_diagnostics_refuse_millforge_without_component_authority() -> None:
    source = _source()
    runner = _records(source, "runner_bindings")[0]
    runner["adapter_kind"] = "millforge"

    result = compile_workflow(source)

    assert result.plan is None
    assert {error.code for error in result.diagnostics} >= {
        "missing_runner_component_authority",
        "missing_runner_terminal_mapping_authority",
    }


@pytest.mark.parametrize(
    "field_name",
    (
        "component_kind",
        "component_id",
        "component_version",
        "provider_distribution",
        "provider_version",
        "descriptor_media_type",
    ),
)
def test_runner_component_pin_blank_text_field_is_diagnosed(field_name: str) -> None:
    source = _source_with_runner_component()
    pin = cast(Record, _records(source, "runner_bindings")[0]["component_pin"])
    pin[field_name] = "  "

    error = _find_error(_errors(source), "invalid_runner_component_pin")

    assert error.declaration_path == f"runner_bindings[0].component_pin.{field_name}"


@pytest.mark.parametrize(
    ("case", "expected_code"),
    (
        ("malformed_digest", "invalid_runner_component_descriptor_digest"),
        ("duplicate_capability", "duplicate_runner_component_capability"),
        ("duplicate_result", "duplicate_runner_terminal_result"),
        ("duplicate_mapping", "duplicate_runner_terminal_result_mapping"),
        ("duplicate_target", "duplicate_runner_terminal_result_outcome"),
        ("mapping_without_pin", "runner_terminal_mapping_without_component"),
        ("unknown_result", "unknown_runner_terminal_result"),
        ("stage_outside_binding", "runner_terminal_mapping_stage_not_owned"),
        ("missing_outcome", "runner_terminal_mapping_outcome_missing"),
        ("wrong_stage_outcome", "runner_terminal_mapping_outcome_stage_mismatch"),
        ("undeclared_outcome", "runner_terminal_mapping_outcome_not_declared"),
        ("component_capability", "runner_component_capability_not_required"),
        ("unsupported_adapter", "runner_component_authority_cannot_default_adapter"),
        ("unknown_pin_key", "invalid_runner_component_pin"),
        ("blank_capability_element", "invalid_runner_component_pin"),
        ("blank_result_element", "invalid_runner_component_pin"),
    ),
)
def test_runner_component_and_mapping_diagnostics(
    case: str,
    expected_code: str,
) -> None:
    source = _source_with_runner_component()
    runner = _records(source, "runner_bindings")[0]
    pin = cast(Record, runner["component_pin"])
    mappings = list(cast(tuple[Record, ...], runner["terminal_result_mappings"]))
    if case == "malformed_digest":
        pin["descriptor_sha256"] = "A" * 64
    elif case == "duplicate_capability":
        pin["required_capability_ids"] = (
            "capability.runner.invoke",
            "capability.runner.invoke",
        )
    elif case == "duplicate_result":
        pin["legal_terminal_result_ids"] = ("COMPLETE", "COMPLETE")
    elif case == "duplicate_mapping":
        mappings.append(dict(mappings[0]))
        runner["terminal_result_mappings"] = tuple(mappings)
    elif case == "duplicate_target":
        mappings.insert(
            0,
            {
                **mappings[0],
                "runner_result_id": "BLOCKED",
            },
        )
        runner["terminal_result_mappings"] = tuple(mappings)
    elif case == "mapping_without_pin":
        runner.pop("component_pin")
    elif case == "unknown_result":
        mappings[0] = {**mappings[0], "runner_result_id": "UNKNOWN"}
        runner["terminal_result_mappings"] = tuple(mappings)
    elif case == "stage_outside_binding":
        runner["stage_kind_ids"] = ("kernel_ping.taskmaster",)
        mappings[0] = {
            **mappings[0],
            "stage_kind_id": "kernel_ping.worker",
            "outcome_id": "kernel_ping.worker.work_complete",
        }
        runner["terminal_result_mappings"] = tuple(mappings)
    elif case == "missing_outcome":
        mappings[0] = {**mappings[0], "outcome_id": "missing.outcome"}
        runner["terminal_result_mappings"] = tuple(mappings)
    elif case == "wrong_stage_outcome":
        mappings[0] = {
            **mappings[0],
            "outcome_id": "kernel_ping.worker.work_complete",
        }
        runner["terminal_result_mappings"] = tuple(mappings)
    elif case == "undeclared_outcome":
        mappings[0] = {
            **mappings[0],
            "outcome_id": "kernel_ping.taskmaster.blocked",
        }
        runner["terminal_result_mappings"] = tuple(mappings)
        stage = next(
            item
            for item in _records(source, "stage_kinds")
            if item["id"] == "kernel_ping.taskmaster"
        )
        stage["declared_outcome_ids"] = ("kernel_ping.taskmaster.task_complete",)
    elif case == "component_capability":
        runner["required_capability_ids"] = ()
    elif case == "unsupported_adapter":
        runner["adapter_kind"] = "fake_local"
    elif case == "unknown_pin_key":
        pin["adapter_options"] = {"unsafe": True}
    elif case == "blank_capability_element":
        pin["required_capability_ids"] = ("capability.runner.invoke", "  ")
    else:
        pin["legal_terminal_result_ids"] = ("COMPLETE", "")

    _find_error(_errors(source), expected_code)


def test_runner_component_mapping_authority_remains_partial() -> None:
    result = compile_workflow(_source_with_runner_component())

    assert result.plan is not None
    binding = result.plan.runner_bindings[0]
    assert binding.component_pin is not None
    assert binding.component_pin.legal_terminal_result_ids == ("BLOCKED", "COMPLETE")
    assert tuple(
        mapping.runner_result_id for mapping in binding.terminal_result_mappings
    ) == ("COMPLETE",)


@pytest.mark.parametrize(
    ("authored_value", "unsupported_type"),
    (
        pytest.param(True, "bool", id="bool"),
        pytest.param(3600.0, "float", id="float"),
        pytest.param("3600", "str", id="string"),
        pytest.param(float("nan"), "float", id="nan"),
        pytest.param(float("inf"), "float", id="infinity"),
    ),
)
def test_runner_invocation_timeout_type_diagnostic_preserves_package_prefix(
    authored_value: object,
    unsupported_type: str,
) -> None:
    source = _source()
    _records(source, "runner_bindings")[0]["invocation_timeout_seconds"] = (
        authored_value
    )

    result = compile_workflow(
        source,
        selected_runner_policy=_CODEX_POLICY,
        declaration_path_prefix="workflows[2].selected_authority.",
    )

    assert result.plan is None
    error = _find_error(result.diagnostics, "unsupported_authority_value")
    assert error.declaration_path == (
        "workflows[2].selected_authority.runner_bindings[0].invocation_timeout_seconds"
    )
    assert error.context["unsupported_type"] == unsupported_type


@pytest.mark.parametrize("authored_value", (0, -1))
def test_nonpositive_runner_invocation_timeout_has_focused_prefixed_diagnostic(
    authored_value: int,
) -> None:
    source = _source()
    _records(source, "runner_bindings")[0]["invocation_timeout_seconds"] = (
        authored_value
    )

    result = compile_workflow(
        source,
        selected_runner_policy=_CODEX_POLICY,
        declaration_path_prefix="workflows[2].selected_authority.",
        diagnostic_context={"package_id": "pkg.timeout"},
    )

    assert result.plan is None
    error = _find_error(
        result.diagnostics,
        "invalid_runner_invocation_timeout_seconds",
    )
    assert error.declaration_path == (
        "workflows[2].selected_authority.runner_bindings[0].invocation_timeout_seconds"
    )
    assert error.context["workflow_id"] == "kernel_ping"
    assert error.context["workflow_version"] == "0.1"
    assert error.context["runner_binding_id"] == "kernel_ping.fake_local_runner"
    assert error.context["authored_value"] == authored_value
    assert error.context["minimum_accepted_value"] == 1
    assert error.context["package_id"] == "pkg.timeout"
    assert error.hint is not None


def test_invalid_selected_runner_adapter_kind_defaults_with_warning() -> None:
    source = _source()

    result = compile_workflow(source, selected_runner_policy=_CODEX_POLICY)

    assert result.plan is not None
    warnings = [
        diagnostic
        for diagnostic in result.diagnostics
        if diagnostic.severity == "warning"
    ]
    assert [warning.code for warning in warnings] == [RUNNER_ADAPTER_KIND_DEFAULTED]
    warning = warnings[0]
    assert warning.phase == "semantic_validation"
    assert warning.declaration_path == "runner_bindings[0].adapter_kind"
    assert warning.context["workflow_id"] == "kernel_ping"
    assert warning.context["workflow_version"] == "0.1"
    assert warning.context["runner_binding_id"] == "kernel_ping.fake_local_runner"
    assert warning.context["original_adapter_kind"] == "fake_local"
    assert warning.context["default_adapter_kind"] == "codex"
    assert warning.context["source_kind"] == "workflow_source"
    assert warning.hint is not None
    assert "daemon will not remap" in warning.hint
    assert {runner.adapter_kind for runner in result.plan.runner_bindings} == {"codex"}


def test_selected_runner_adapter_warning_preserves_diagnostic_context() -> None:
    source = _source()

    result = compile_workflow(
        source,
        selected_runner_policy=_CODEX_POLICY,
        diagnostic_context={"source_path": "workflows/kernel_ping.json"},
    )

    assert result.plan is not None
    warning = next(
        diagnostic
        for diagnostic in result.diagnostics
        if diagnostic.code == RUNNER_ADAPTER_KIND_DEFAULTED
    )
    assert warning.context["source_kind"] == "workflow_source"
    assert warning.context["source_path"] == "workflows/kernel_ping.json"


def test_multiple_invalid_selected_runner_bindings_each_warn() -> None:
    source = _source()
    _records(source, "runner_bindings").append(
        {
            "id": "kernel_ping.second_runner",
            "adapter_kind": "local_subprocess",
            "stage_kind_ids": (),
            "required_capability_ids": ("capability.runner.invoke",),
            "presentation": {},
        }
    )

    result = compile_workflow(source, selected_runner_policy=_CODEX_POLICY)

    assert result.plan is not None
    warnings = [
        diagnostic
        for diagnostic in result.diagnostics
        if diagnostic.code == RUNNER_ADAPTER_KIND_DEFAULTED
    ]
    assert len(warnings) == 2
    assert {
        warning.context["runner_binding_id"]: warning.context["original_adapter_kind"]
        for warning in warnings
    } == {
        "kernel_ping.fake_local_runner": "fake_local",
        "kernel_ping.second_runner": "local_subprocess",
    }
    assert {runner.adapter_kind for runner in result.plan.runner_bindings} == {"codex"}


def test_blank_runner_adapter_kind_remains_error_not_default() -> None:
    source = _source()
    _records(source, "runner_bindings")[0]["adapter_kind"] = ""

    result = compile_workflow(source, selected_runner_policy=_CODEX_POLICY)

    assert result.plan is None
    errors = [
        diagnostic
        for diagnostic in result.diagnostics
        if diagnostic.severity == "error"
    ]
    error = _find_error(errors, "missing_runner_adapter_kind")
    assert error.declaration_path == "runner_bindings[0].adapter_kind"
    assert [
        diagnostic
        for diagnostic in result.diagnostics
        if diagnostic.code == RUNNER_ADAPTER_KIND_DEFAULTED
    ] == []


def test_missing_runner_adapter_kind_remains_error_not_default() -> None:
    source = _source()
    del _records(source, "runner_bindings")[0]["adapter_kind"]

    result = compile_workflow(source, selected_runner_policy=_CODEX_POLICY)

    assert result.plan is None
    errors = [
        diagnostic
        for diagnostic in result.diagnostics
        if diagnostic.severity == "error"
    ]
    error = _find_error(errors, "missing_runner_adapter_kind")
    assert error.declaration_path == "runner_bindings[0].adapter_kind"
    assert [
        diagnostic
        for diagnostic in result.diagnostics
        if diagnostic.code == RUNNER_ADAPTER_KIND_DEFAULTED
    ] == []


def test_whitespace_runner_adapter_kind_remains_error_not_default() -> None:
    source = _source()
    _records(source, "runner_bindings")[0]["adapter_kind"] = "   "

    result = compile_workflow(source, selected_runner_policy=_CODEX_POLICY)

    assert result.plan is None
    errors = [
        diagnostic
        for diagnostic in result.diagnostics
        if diagnostic.severity == "error"
    ]
    error = _find_error(errors, "missing_runner_adapter_kind")
    assert error.declaration_path == "runner_bindings[0].adapter_kind"
    assert [
        diagnostic
        for diagnostic in result.diagnostics
        if diagnostic.code == RUNNER_ADAPTER_KIND_DEFAULTED
    ] == []


def test_non_string_runner_adapter_kind_remains_error_not_default() -> None:
    source = _source()
    _records(source, "runner_bindings")[0]["adapter_kind"] = 42

    result = compile_workflow(source, selected_runner_policy=_CODEX_POLICY)

    assert result.plan is None
    error = _find_error(result.diagnostics, "unsupported_authority_value")
    assert error.declaration_path == "runner_bindings[0].adapter_kind"
    assert error.context["unsupported_type"] == "int"
    assert [
        diagnostic
        for diagnostic in result.diagnostics
        if diagnostic.code == RUNNER_ADAPTER_KIND_DEFAULTED
    ] == []


def test_no_default_policy_rejects_unsupported_runner_adapter_kind() -> None:
    source = _source()

    result = compile_workflow(
        source,
        selected_runner_policy=SelectedRunnerAdapterPolicy(
            default_invalid_adapter_kinds=False,
        ),
    )

    assert result.plan is None
    errors = [
        diagnostic
        for diagnostic in result.diagnostics
        if diagnostic.severity == "error"
    ]
    error = _find_error(errors, RUNNER_ADAPTER_KIND_UNSUPPORTED)
    assert error.declaration_path == "runner_bindings[0].adapter_kind"
    assert error.context["runner_binding_id"] == "kernel_ping.fake_local_runner"
    assert error.context["adapter_kind"] == "fake_local"
    assert [
        diagnostic
        for diagnostic in result.diagnostics
        if diagnostic.code == RUNNER_ADAPTER_KIND_DEFAULTED
    ] == []


def test_missing_runner_binding_id_is_runner_specific() -> None:
    source = _source()
    del _records(source, "runner_bindings")[0]["id"]

    error = _find_error(_errors(source), "missing_id")

    assert error.declaration_path == "runner_bindings[0].id"
    assert error.context["namespace"] == "runner_binding"
    assert error.context["field"] == "id"


def test_blank_runner_binding_id_is_runner_specific() -> None:
    source = _source()
    _records(source, "runner_bindings")[0]["id"] = ""

    error = _find_error(_errors(source), "missing_id")

    assert error.declaration_path == "runner_bindings[0].id"
    assert error.context["namespace"] == "runner_binding"
    assert error.context["field"] == "id"


def test_duplicate_runner_binding_id_is_runner_specific() -> None:
    source = _source()
    _records(source, "runner_bindings").append(
        {
            "id": "kernel_ping.fake_local_runner",
            "adapter_kind": "codex",
            "stage_kind_ids": (),
            "presentation": {},
        }
    )

    error = _find_error(_errors(source), "duplicate_id")

    assert error.declaration_path == "runner_bindings[1].id"
    assert error.related_declaration_path == "runner_bindings[0].id"
    assert error.context["namespace"] == "runner_binding"
    assert error.context["duplicate_id"] == "kernel_ping.fake_local_runner"


def test_missing_workflow_id_returns_structured_diagnostic() -> None:
    source = _source()
    _workflow(source)["id"] = ""

    error = _find_error(_errors(source), "missing_id")

    assert error.severity == "error"
    assert error.phase == "semantic_validation"
    assert error.declaration_path == "workflow.id"
    assert error.context["namespace"] == "workflow"
    assert error.context["field"] == "id"
    assert error.hint is not None


def test_missing_workflow_version_returns_structured_diagnostic() -> None:
    source = _source()
    _workflow(source)["version"] = ""

    error = _find_error(_errors(source), "missing_id")

    assert error.severity == "error"
    assert error.phase == "semantic_validation"
    assert error.declaration_path == "workflow.version"
    assert error.context["namespace"] == "workflow"
    assert error.context["field"] == "version"
    assert error.hint is not None


def test_missing_stage_id_returns_structured_diagnostic() -> None:
    source = _source()
    _records(source, "stage_kinds")[0]["id"] = ""

    error = _find_error(_errors(source), "missing_id")

    assert error.declaration_path == "stage_kinds[0].id"
    assert error.context["namespace"] == "stage_kind"
    assert error.context["field"] == "id"


@pytest.mark.parametrize(
    ("collection_key", "namespace"),
    [
        ("stage_kinds", "stage_kind"),
        ("queue_families", "queue_family"),
        ("assets", "asset"),
        ("terminal_actions", "terminal_action"),
        ("terminal_outcomes", "terminal_outcome"),
    ],
)
def test_duplicate_ids_include_both_declaration_paths(
    collection_key: str,
    namespace: str,
) -> None:
    source = _source()
    records = _records(source, collection_key)
    records[1]["id"] = records[0]["id"]

    error = _find_error(_errors(source), "duplicate_id")

    assert error.declaration_path == f"{collection_key}[1].id"
    assert error.related_declaration_path == f"{collection_key}[0].id"
    assert error.context["namespace"] == namespace
    assert error.context["duplicate_id"] == records[0]["id"]


@pytest.mark.parametrize(
    ("field_name", "reference_kind"),
    [
        ("partition_id", "partition"),
        ("runner_binding_id", "runner_binding"),
    ],
)
def test_stage_kind_required_reference_diagnostics_are_structured(
    field_name: str,
    reference_kind: str,
) -> None:
    source = _source()
    stage = _records(source, "stage_kinds")[0]
    stage[field_name] = ""

    error = _find_error(_errors(source), "missing_reference")

    assert error.declaration_path == f"stage_kinds[0].{field_name}"
    assert error.context["referrer_path"] == "stage_kinds[0]"
    assert error.context["reference_kind"] == reference_kind
    assert error.context["referenced_id"] == ""


def test_stage_kind_artifact_schema_reference_diagnostic_is_structured() -> None:
    source = _source()
    stage = _records(source, "stage_kinds")[0]
    stage["artifact_schema_ids"] = ["missing.schema"]

    error = _find_error(_errors(source), "missing_reference")

    assert error.declaration_path == "stage_kinds[0].artifact_schema_ids[0]"
    assert error.context["referrer_path"] == "stage_kinds[0]"
    assert error.context["reference_kind"] == "artifact_schema"
    assert error.context["referenced_id"] == "missing.schema"


@pytest.mark.parametrize(
    ("field_name", "reference_kind"),
    [
        ("outcome_id", "terminal_outcome"),
        ("stage_kind_id", "stage_kind"),
        ("target_stage_kind_id", "stage_kind"),
        ("emitted_queue_family_id", "queue_family"),
        ("artifact_schema_id", "artifact_schema"),
        ("runner_binding_id", "runner_binding"),
    ],
)
def test_terminal_action_missing_reference_diagnostics_are_structured(
    field_name: str,
    reference_kind: str,
) -> None:
    source = _source()
    action = _records(source, "terminal_actions")[0]
    action[field_name] = f"missing.{reference_kind}"

    error = _find_error(_errors(source), "missing_reference")

    assert error.declaration_path == f"terminal_actions[0].{field_name}"
    assert error.context["referrer_path"] == "terminal_actions[0]"
    assert error.context["reference_kind"] == reference_kind
    assert error.context["referenced_id"] == f"missing.{reference_kind}"
    assert error.hint is not None


def test_terminal_action_missing_asset_reference_is_structured() -> None:
    source = _source()
    action = _records(source, "terminal_actions")[0]
    action["asset_ids"] = ["missing.asset"]

    error = _find_error(_errors(source), "missing_reference")

    assert error.declaration_path == "terminal_actions[0].asset_ids[0]"
    assert error.context["referrer_path"] == "terminal_actions[0]"
    assert error.context["reference_kind"] == "asset"
    assert error.context["referenced_id"] == "missing.asset"


def test_terminal_outcome_required_stage_reference_is_structured() -> None:
    source = _source()
    outcome = _records(source, "terminal_outcomes")[0]
    outcome["stage_kind_id"] = ""

    error = _find_error(_errors(source), "missing_reference")

    assert error.declaration_path == "terminal_outcomes[0].stage_kind_id"
    assert error.context["referrer_path"] == "terminal_outcomes[0]"
    assert error.context["reference_kind"] == "stage_kind"
    assert error.context["referenced_id"] == ""


def test_runner_binding_missing_stage_reference_is_structured() -> None:
    source = _source()
    binding = _records(source, "runner_bindings")[0]
    binding["stage_kind_ids"] = ["missing.stage"]

    error = _find_error(_errors(source), "missing_reference")

    assert error.declaration_path == "runner_bindings[0].stage_kind_ids[0]"
    assert error.context["referrer_path"] == "runner_bindings[0]"
    assert error.context["reference_kind"] == "stage_kind"
    assert error.context["referenced_id"] == "missing.stage"


@pytest.mark.parametrize(
    ("field_name", "reference_kind"),
    [
        ("queue_family_id", "queue_family"),
        ("stage_kind_id", "stage_kind"),
        ("runner_binding_id", "runner_binding"),
    ],
)
def test_external_enqueue_route_missing_reference_diagnostics_are_structured(
    field_name: str,
    reference_kind: str,
) -> None:
    source = _source()
    source["external_enqueue_routes"] = [
        {
            "id": "kernel_ping.external_prompt",
            "queue_family_id": "prompt",
            "graph_node_id": "kernel_ping.taskmaster.start",
            "stage_kind_id": "kernel_ping.taskmaster",
            "runner_binding_id": "kernel_ping.fake_local_runner",
        }
    ]
    route = _records(source, "external_enqueue_routes")[0]
    route[field_name] = f"missing.{reference_kind}"

    error = _find_error(_errors(source), "missing_reference")

    assert error.declaration_path == f"external_enqueue_routes[0].{field_name}"
    assert error.context["referrer_path"] == "external_enqueue_routes[0]"
    assert error.context["reference_kind"] == reference_kind
    assert error.context["referenced_id"] == f"missing.{reference_kind}"
    assert error.hint is not None


def test_external_enqueue_route_requires_graph_node_id() -> None:
    source = _source()
    route = _records(source, "external_enqueue_routes")[0]
    route["graph_node_id"] = ""

    error = _find_error(_errors(source), "missing_id")

    assert error.declaration_path == "external_enqueue_routes[0].graph_node_id"
    assert error.context["namespace"] == "external_enqueue_route"
    assert error.context["field"] == "graph_node_id"


def test_external_enqueue_route_requires_external_queue_family() -> None:
    source = _source()
    route = _records(source, "external_enqueue_routes")[0]
    route["queue_family_id"] = "task_artifact"

    error = _find_error(_errors(source), "external_enqueue_route_internal_queue")

    assert error.declaration_path == "external_enqueue_routes[0].queue_family_id"
    assert error.context["referrer_path"] == "external_enqueue_routes[0]"
    assert error.context["queue_family_id"] == "task_artifact"
    assert error.context["external_enqueue"] is False
    assert error.hint is not None


def test_external_enqueue_routes_reject_duplicate_queue_family_routes() -> None:
    source = _source()
    routes = _records(source, "external_enqueue_routes")
    routes.append(
        {
            "id": "kernel_ping.external_prompt_alternate",
            "queue_family_id": "prompt",
            "graph_node_id": "kernel_ping.taskmaster.alternate_start",
            "stage_kind_id": "kernel_ping.taskmaster",
            "runner_binding_id": "kernel_ping.fake_local_runner",
        }
    )

    error = _find_error(_errors(source), "ambiguous_external_enqueue_route")

    assert error.declaration_path == "external_enqueue_routes[1].queue_family_id"
    assert error.related_declaration_path == (
        "external_enqueue_routes[0].queue_family_id"
    )
    assert error.context["queue_family_id"] == "prompt"
    assert error.context["route_id"] == "kernel_ping.external_prompt_alternate"
    assert error.context["related_route_id"] == "kernel_ping.external_prompt"
    assert error.hint is not None


def test_external_enqueue_route_queue_must_feed_target_stage() -> None:
    source = _source()
    route = _records(source, "external_enqueue_routes")[0]
    route["stage_kind_id"] = "kernel_ping.worker"

    error = _find_error(_errors(source), "external_enqueue_route_stage_input_mismatch")

    assert error.declaration_path == "external_enqueue_routes[0].queue_family_id"
    assert error.context["referrer_path"] == "external_enqueue_routes[0]"
    assert error.context["queue_family_id"] == "prompt"
    assert error.context["stage_kind_id"] == "kernel_ping.worker"
    assert error.hint is not None


def test_external_enqueue_route_runner_must_equal_target_stage_runner() -> None:
    source = _source()
    _records(source, "runner_bindings").append(
        {
            "id": "kernel_ping.alternate_runner",
            "adapter_kind": "fake_local",
            "stage_kind_ids": ("kernel_ping.taskmaster",),
            "presentation": {"display_name": "Alternate runner"},
        }
    )
    route = _records(source, "external_enqueue_routes")[0]
    route["runner_binding_id"] = "kernel_ping.alternate_runner"

    error = _find_error(_errors(source), "external_enqueue_route_stage_runner_mismatch")

    assert error.declaration_path == "external_enqueue_routes[0].runner_binding_id"
    assert error.context["referrer_path"] == "external_enqueue_routes[0]"
    assert error.context["stage_kind_id"] == "kernel_ping.taskmaster"
    assert error.context["route_runner_binding_id"] == "kernel_ping.alternate_runner"
    assert error.context["stage_runner_binding_id"] == "kernel_ping.fake_local_runner"
    assert error.hint is not None


def test_external_enqueue_route_runner_must_list_target_stage() -> None:
    source = _source()
    runner = _records(source, "runner_bindings")[0]
    runner["stage_kind_ids"] = ("kernel_ping.worker",)

    error = _find_error(_errors(source), "external_enqueue_route_runner_stage_mismatch")

    assert error.declaration_path == "external_enqueue_routes[0].runner_binding_id"
    assert error.context["referrer_path"] == "external_enqueue_routes[0]"
    assert error.context["stage_kind_id"] == "kernel_ping.taskmaster"
    assert error.context["runner_binding_id"] == "kernel_ping.fake_local_runner"
    assert error.hint is not None


def test_declared_outcome_must_belong_to_declaring_stage() -> None:
    source = _source()
    stage = _records(source, "stage_kinds")[0]
    stage["declared_outcome_ids"] = ["kernel_ping.worker.work_complete"]

    error = _find_error(_errors(source), "outcome_stage_mismatch")

    assert error.declaration_path == "stage_kinds[0].declared_outcome_ids[0]"
    assert error.context["stage_kind_id"] == "kernel_ping.taskmaster"
    assert error.context["outcome_id"] == "kernel_ping.worker.work_complete"
    assert error.context["outcome_stage_kind_id"] == "kernel_ping.worker"


def test_terminal_action_outcome_must_belong_to_action_stage() -> None:
    source = _source()
    action = _records(source, "terminal_actions")[0]
    action["outcome_id"] = "kernel_ping.worker.work_complete"

    error = _find_error(_errors(source), "outcome_stage_mismatch")

    assert error.declaration_path == "terminal_actions[0].outcome_id"
    assert error.context["stage_kind_id"] == "kernel_ping.taskmaster"
    assert error.context["outcome_id"] == "kernel_ping.worker.work_complete"
    assert error.context["outcome_stage_kind_id"] == "kernel_ping.worker"


def test_terminal_outcome_markers_must_be_unambiguous_within_stage() -> None:
    source = _source()
    outcomes = _records(source, "terminal_outcomes")
    outcomes[1]["marker"] = outcomes[0]["marker"]

    error = _find_error(_errors(source), "ambiguous_terminal_marker")

    assert error.declaration_path == "terminal_outcomes[1].marker"
    assert error.related_declaration_path == "terminal_outcomes[0].marker"
    assert error.context["stage_kind_id"] == "kernel_ping.taskmaster"
    assert error.context["marker"] == "TASK_COMPLETE"
    assert error.hint is not None


def test_terminal_actions_must_be_unambiguous_within_stage_and_outcome() -> None:
    source = _source()
    actions = _records(source, "terminal_actions")
    actions.append({**actions[0], "id": "kernel_ping.duplicate_taskmaster_success"})

    error = _find_error(_errors(source), "ambiguous_terminal_action")

    assert error.declaration_path == "terminal_actions[5].outcome_id"
    assert error.related_declaration_path == "terminal_actions[0].outcome_id"
    assert error.context["stage_kind_id"] == "kernel_ping.taskmaster"
    assert error.context["outcome_id"] == "kernel_ping.taskmaster.task_complete"
    assert error.hint is not None


@pytest.mark.parametrize(
    ("action_id", "remove_kind"),
    (
        ("kernel_ping.route_taskmaster_success", False),
        ("kernel_ping.close_worker_success", False),
        ("kernel_ping.pause_worker_blocked", False),
        ("kernel_ping.route_taskmaster_success", True),
    ),
)
def test_unknown_terminal_action_kind_is_rejected(
    action_id: str,
    remove_kind: bool,
) -> None:
    source = _source()
    action = next(
        action
        for action in _records(source, "terminal_actions")
        if action["id"] == action_id
    )
    if remove_kind:
        del action["kind"]
    else:
        action["kind"] = "unknown_action_kind"
    action_index = _records(source, "terminal_actions").index(action)

    error = _find_error(_errors(source), "unsupported_terminal_action_kind")

    assert error.declaration_path == f"terminal_actions[{action_index}].kind"
    assert error.context["referrer_path"] == f"terminal_actions[{action_index}]"
    assert error.context["action_id"] == action_id
    assert error.context["action_kind"] == (
        "" if remove_kind else "unknown_action_kind"
    )
    assert error.hint is not None


def test_deferred_terminal_action_kind_is_rejected() -> None:
    source = _source()
    action = _records(source, "terminal_actions")[0]
    action["kind"] = "deferred_terminal_action"

    error = _find_error(_errors(source), "unsupported_terminal_action_kind")

    assert error.declaration_path == "terminal_actions[0].kind"
    assert error.context["referrer_path"] == "terminal_actions[0]"
    assert error.context["action_id"] == "kernel_ping.route_taskmaster_success"
    assert error.context["action_kind"] == "deferred_terminal_action"
    assert error.hint is not None


@pytest.mark.parametrize(
    ("field_name", "field_value", "diagnostic_code"),
    (
        (
            "target_stage_kind_id",
            "kernel_ping.taskmaster",
            "terminal_route_stage_input_mismatch",
        ),
        (
            "emitted_queue_family_id",
            "task_incident",
            "terminal_route_stage_output_mismatch",
        ),
    ),
)
def test_route_action_contract_must_match_stage_queue_schema_and_runner(
    field_name: str,
    field_value: str,
    diagnostic_code: str,
) -> None:
    source = _source()
    action = _records(source, "terminal_actions")[0]
    action[field_name] = field_value

    error = _find_error(_errors(source), diagnostic_code)

    assert error.declaration_path == f"terminal_actions[0].{field_name}"
    assert error.context["referrer_path"] == "terminal_actions[0]"
    assert error.context["action_id"] == "kernel_ping.route_taskmaster_success"
    assert error.hint is not None


def test_route_action_artifact_schema_must_be_declared_by_route_stages() -> None:
    source = _source()
    _records(source, "artifact_schemas").append(
        {
            "id": "kernel_ping.unrouted_schema",
            "schema": {"type": "object", "properties": {}},
            "presentation": {"display_name": "Unrouted schema"},
        }
    )
    action = _records(source, "terminal_actions")[0]
    action["artifact_schema_id"] = "kernel_ping.unrouted_schema"

    error = _find_error(_errors(source), "terminal_route_artifact_schema_mismatch")

    assert error.declaration_path == "terminal_actions[0].artifact_schema_id"
    assert error.context["referrer_path"] == "terminal_actions[0]"
    assert error.context["action_id"] == "kernel_ping.route_taskmaster_success"
    assert error.context["artifact_schema_id"] == "kernel_ping.unrouted_schema"
    assert error.hint is not None


def test_route_action_runner_must_match_target_stage_runner() -> None:
    source = _source()
    _records(source, "runner_bindings").append(
        {
            "id": "kernel_ping.alternate_runner",
            "adapter_kind": "fake_local",
            "stage_kind_ids": ("kernel_ping.worker",),
            "presentation": {"display_name": "Alternate runner"},
        }
    )
    action = _records(source, "terminal_actions")[0]
    action["runner_binding_id"] = "kernel_ping.alternate_runner"

    error = _find_error(_errors(source), "terminal_route_stage_runner_mismatch")

    assert error.declaration_path == "terminal_actions[0].runner_binding_id"
    assert error.context["referrer_path"] == "terminal_actions[0]"
    assert error.context["target_stage_kind_id"] == "kernel_ping.worker"
    assert error.context["route_runner_binding_id"] == "kernel_ping.alternate_runner"
    assert error.context["stage_runner_binding_id"] == "kernel_ping.fake_local_runner"
    assert error.hint is not None


def test_route_action_requires_complete_executable_authority() -> None:
    source = _source()
    action = _records(source, "terminal_actions")[0]
    action["target_graph_node_id"] = None

    error = _find_error(_errors(source), "terminal_route_missing_field")

    assert error.declaration_path == "terminal_actions[0].target_graph_node_id"
    assert error.context["referrer_path"] == "terminal_actions[0]"
    assert error.context["action_id"] == "kernel_ping.route_taskmaster_success"
    assert error.context["field_name"] == "target_graph_node_id"
    assert error.hint is not None


def test_route_action_projection_must_be_valid_before_selection() -> None:
    source = _source()
    action = _records(source, "terminal_actions")[0]
    action["payload_projection"] = {
        "kind": "source",
        "path": ("unknown_root",),
    }

    error = _find_error(_errors(source), "invalid_terminal_projection")

    assert error.declaration_path == "terminal_actions[0].payload_projection"
    assert error.context["referrer_path"] == "terminal_actions[0]"
    assert error.context["action_id"] == "kernel_ping.route_taskmaster_success"
    assert error.context["reason"] == "unknown_source_root"
    assert error.context["detail"] == "unknown_root"
    assert error.hint is not None


@pytest.mark.parametrize(
    "projection",
    (
        {"kind": "literal"},
        {"kind": "source", "path": ("artifact_payload",), "ignored": "dead"},
    ),
)
def test_route_action_projection_grammar_must_be_exact(
    projection: Record,
) -> None:
    source = _source()
    action = _records(source, "terminal_actions")[0]
    action["payload_projection"] = projection

    error = _find_error(_errors(source), "invalid_terminal_projection")

    assert error.declaration_path == "terminal_actions[0].payload_projection"
    assert error.context["referrer_path"] == "terminal_actions[0]"
    assert error.context["action_id"] == "kernel_ping.route_taskmaster_success"
    assert error.hint is not None


@pytest.mark.parametrize(
    "field_name",
    (
        "target_stage_kind_id",
        "target_graph_node_id",
        "emitted_queue_family_id",
        "artifact_schema_id",
        "runner_binding_id",
        "payload_projection",
    ),
)
def test_incident_route_requires_complete_executable_authority(
    field_name: str,
) -> None:
    source = _source()
    review_action = next(
        action
        for action in _records(source, "terminal_actions")
        if action["id"] == "kernel_ping.route_worker_review"
    )
    review_action[field_name] = None

    error = _find_error(_errors(source), "terminal_route_missing_field")

    assert error.declaration_path == f"terminal_actions[3].{field_name}"
    assert error.context["referrer_path"] == "terminal_actions[3]"
    assert error.context["action_id"] == "kernel_ping.route_worker_review"
    assert error.context["field_name"] == field_name
    assert error.hint is not None


def test_incident_route_projection_must_be_valid_before_selection() -> None:
    source = _source()
    review_action = next(
        action
        for action in _records(source, "terminal_actions")
        if action["id"] == "kernel_ping.route_worker_review"
    )
    review_action["target_graph_node_id"] = "kernel_ping.taskmaster.review"
    review_action["payload_projection"] = {
        "kind": "object",
        "fields": {
            "source_prompt_id": {
                "kind": "source",
                "path": ("work_item_payload", "source_prompt_id"),
            },
        },
    }
    projection = cast(Record, review_action["payload_projection"])
    fields = cast(Record, projection["fields"])
    fields["source_prompt_id"] = {
        "kind": "source",
        "path": ("artifact_payload", "source_prompt_id"),
        "unexpected": "not authority",
    }

    error = _find_error(_errors(source), "invalid_terminal_projection")

    assert error.declaration_path == "terminal_actions[3].payload_projection"
    assert error.context["referrer_path"] == "terminal_actions[3]"
    assert error.context["action_id"] == "kernel_ping.route_worker_review"
    assert error.context["reason"] == "unsupported_projection_key"
    assert error.hint is not None


def test_artifact_schema_declaration_must_be_valid_before_selection() -> None:
    source = _source()
    task_schema = _records(source, "artifact_schemas")[0]
    schema = cast(Record, task_schema["schema"])
    properties = cast(Record, schema["properties"])
    title_schema = cast(Record, properties["title"])
    title_schema["pattern"] = ".+"

    error = _find_error(_errors(source), "invalid_artifact_schema")

    assert error.declaration_path == "artifact_schemas[0].schema"
    assert error.context["schema_id"] == "kernel_ping.task_artifact"
    assert error.context["reason"] == "unsupported_schema_keyword"
    assert error.hint is not None


def test_artifact_schema_declaration_requires_mapping_schema() -> None:
    source = _source()
    task_schema = _records(source, "artifact_schemas")[0]
    task_schema["schema"] = "not-a-schema-map"

    error = _find_error(_errors(source), "invalid_artifact_schema")

    assert error.declaration_path == "artifact_schemas[0].schema"
    assert error.context["schema_id"] == "kernel_ping.task_artifact"
    assert error.context["reason"] == "unsupported_schema_value"
    assert error.context["detail"] == "schema"
    assert error.hint is not None


def test_declared_outcome_without_terminal_action_is_refused() -> None:
    source = _source()
    actions = _records(source, "terminal_actions")
    actions[:] = [
        action
        for action in actions
        if action["id"] != "kernel_ping.pause_worker_blocked"
    ]

    error = _find_error(_errors(source), "outcome_without_action")

    assert error.declaration_path == "terminal_outcomes[4].id"
    assert error.context["stage_kind_id"] == "kernel_ping.worker"
    assert error.context["outcome_id"] == "kernel_ping.worker.blocked"


def test_non_null_compatibility_profile_is_refused() -> None:
    source = _source()
    _workflow(source)["compatibility_profile"] = "legacy-profile"

    error = _find_error(_errors(source), "unsupported_compatibility_profile")

    assert error.declaration_path == "workflow.compatibility_profile"
    assert error.context["compatibility_profile"] == "legacy-profile"


def test_non_empty_required_extensions_are_refused() -> None:
    source = _source()
    _workflow(source)["required_extensions"] = ["some.extension"]

    error = _find_error(_errors(source), "unsupported_required_extensions")

    assert error.declaration_path == "workflow.required_extensions"
    assert error.context["required_extensions"] == ("some.extension",)


def test_required_extensions_must_be_a_sequence() -> None:
    source = _source()
    _workflow(source)["required_extensions"] = {"some.extension": True}

    error = _find_error(_errors(source), "unsupported_authority_value")

    assert error.declaration_path == "workflow.required_extensions"
    assert error.context["unsupported_type"] == "dict"
    assert error.context["value_kind"] == "value"


def test_unselected_catalog_duplicate_ids_include_both_declaration_paths() -> None:
    source = kernel_ping_support.workflow_source_with_unselected_catalog()
    catalog_entry = cast(tuple[Record, ...], source["unselected_catalog"])[0]
    source["unselected_catalog"] = (
        catalog_entry,
        {**catalog_entry, "kind": "alternate_unselected_entry"},
    )

    error = _find_error(_errors(source), "duplicate_id")

    assert error.declaration_path == "unselected_catalog[1].id"
    assert error.related_declaration_path == "unselected_catalog[0].id"
    assert error.context["namespace"] == "unselected_catalog"
    assert error.context["duplicate_id"] == kernel_ping_support.UNSELECTED_CATALOG_ID
