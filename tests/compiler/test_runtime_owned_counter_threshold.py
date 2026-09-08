from __future__ import annotations

from collections.abc import Iterable
from typing import cast

from millrace.compiler import SelectedRunnerAdapterPolicy, compile_workflow
from millrace.contracts import Diagnostic
from support import generic_admission, generic_fanout

Source = dict[str, object]
Record = dict[str, object]

_CODEX_POLICY = SelectedRunnerAdapterPolicy(
    default_adapter_kind="codex",
    supported_adapter_kinds=frozenset({"codex"}),
    component_bound_adapter_kinds=frozenset(),
    default_component_selector=None,
    default_component_required_capability_ids=frozenset(),
    default_component_requires_complete_mappings=False,
)


def _runtime_admission_source() -> Source:
    source = generic_admission.source()
    runner = cast(list[Record], source["runner_bindings"])[0]
    runner.update(
        {
            "adapter_kind": "codex",
            "component_pin": {
                "component_kind": "runner",
                "component_id": "bounded-runner",
                "component_version": "1",
                "provider_distribution": "bounded-provider",
                "provider_version": "1",
                "descriptor_media_type": "application/json",
                "descriptor_sha256": "a" * 64,
                "required_capability_ids": (),
                "legal_terminal_result_ids": ("RUNTIME_FAILURE",),
            },
            "terminal_result_mappings": (
                {
                    "stage_kind_id": generic_admission.PARENT_STAGE_ID,
                    "runner_result_id": "RUNTIME_FAILURE",
                    "outcome_id": "admission.retry_ready",
                },
            ),
        }
    )
    return source


def _generic_counter_source() -> Source:
    source = generic_fanout.source()
    stage = cast(list[Record], source["stage_kinds"])[0]
    stage["declared_outcome_ids"] = (
        "fanout.parent.done",
        "generic.retry_ready",
        "generic.retry_exhausted",
    )
    cast(list[Record], source["terminal_outcomes"]).extend(
        (
            {
                "id": "generic.retry_ready",
                "stage_kind_id": "parent_stage",
                "marker": "GENERIC_RETRY_READY",
            },
            {
                "id": "generic.retry_exhausted",
                "stage_kind_id": "parent_stage",
                "marker": "GENERIC_RETRY_EXHAUSTED",
            },
        )
    )
    cast(list[Record], source["terminal_actions"]).extend(
        (
            {
                "id": "generic.retry",
                "stage_kind_id": "parent_stage",
                "outcome_id": "generic.retry_ready",
                "kind": "close",
                "artifact_schema_id": generic_fanout.PACKET_SCHEMA_ID,
            },
            {
                "id": "generic.retry_exhausted",
                "stage_kind_id": "parent_stage",
                "outcome_id": "generic.retry_exhausted",
                "kind": "close",
                "artifact_schema_id": generic_fanout.PACKET_SCHEMA_ID,
            },
        )
    )
    source["counters"] = [
        {
            "id": "generic.counter",
            "kind": "lineage_terminal_action_counter",
            "scope": "lineage",
            "stage_kind_id": "parent_stage",
            "increment_action_id": "generic.retry",
            "threshold_action_id": "generic.retry_exhausted",
            "threshold_count": 2,
        }
    ]
    runner = cast(list[Record], source["runner_bindings"])[0]
    runner.update(
        {
            "adapter_kind": "codex",
            "component_pin": {
                "component_kind": "runner",
                "component_id": "generic-runner",
                "component_version": "1",
                "provider_distribution": "generic-provider",
                "provider_version": "1",
                "descriptor_media_type": "application/json",
                "descriptor_sha256": "b" * 64,
                "required_capability_ids": (),
                "legal_terminal_result_ids": ("GENERIC_FAILURE",),
            },
            "terminal_result_mappings": (
                {
                    "stage_kind_id": "parent_stage",
                    "runner_result_id": "GENERIC_FAILURE",
                    "outcome_id": "generic.retry_ready",
                },
            ),
        }
    )
    return source


def _action(source: Source, action_id: str) -> Record:
    return next(
        action
        for action in cast(list[Record], source["terminal_actions"])
        if action.get("id") == action_id
    )


def _parent_stage(source: Source) -> Record:
    return next(
        stage
        for stage in cast(list[Record], source["stage_kinds"])
        if stage.get("id") == generic_admission.PARENT_STAGE_ID
    )


def _add_parent_schema(source: Source, schema_id: str) -> None:
    stage = _parent_stage(source)
    stage["artifact_schema_ids"] = (
        *cast(tuple[str, ...], stage["artifact_schema_ids"]),
        schema_id,
    )


def _set_counter_contract(
    source: Source,
    *,
    schema_id: str,
    increment_conditions: object | None = None,
    threshold_conditions: object | None = None,
) -> None:
    _add_parent_schema(source, schema_id)
    increment = _action(source, generic_admission.COUNTER_INCREMENT_ACTION_ID)
    threshold = _action(source, generic_admission.COUNTER_THRESHOLD_ACTION_ID)
    increment["artifact_schema_id"] = schema_id
    threshold["artifact_schema_id"] = schema_id
    if increment_conditions is not None:
        increment["artifact_field_conditions"] = increment_conditions
    if threshold_conditions is not None:
        threshold["artifact_field_conditions"] = threshold_conditions


def _errors(source: Source) -> tuple[Diagnostic, ...]:
    result = compile_workflow(source, selected_runner_policy=_CODEX_POLICY)
    return tuple(
        diagnostic
        for diagnostic in result.diagnostics
        if diagnostic.severity == "error"
    )


def _find_error(errors: Iterable[Diagnostic], code: str) -> Diagnostic:
    matches = [diagnostic for diagnostic in errors if diagnostic.code == code]
    assert matches, f"missing diagnostic code {code!r} in {tuple(errors)!r}"
    return matches[0]


def test_compiler_diagnoses_runtime_owned_threshold_schema_mismatch() -> None:
    source = _runtime_admission_source()
    _add_parent_schema(source, generic_admission.OTHER_SCHEMA_ID)
    _action(source, generic_admission.COUNTER_THRESHOLD_ACTION_ID)[
        "artifact_schema_id"
    ] = generic_admission.OTHER_SCHEMA_ID

    error = _find_error(
        _errors(source),
        "counter_runtime_threshold_artifact_schema_mismatch",
    )

    assert error.declaration_path.endswith(".artifact_schema_id")
    assert error.related_declaration_path is not None
    assert error.related_declaration_path.endswith(".artifact_schema_id")
    assert error.context["counter_id"] == generic_admission.COUNTER_ID


def test_compiler_diagnoses_runtime_owned_threshold_condition_mismatch() -> None:
    source = _runtime_admission_source()
    _set_counter_contract(
        source,
        schema_id=generic_fanout.CHILD_SCHEMA_ID,
        increment_conditions={"child_id": "increment"},
        threshold_conditions={"child_id": "threshold"},
    )

    error = _find_error(
        _errors(source),
        "counter_runtime_threshold_artifact_conditions_mismatch",
    )

    assert error.declaration_path.endswith(".artifact_field_conditions")
    assert error.related_declaration_path is not None
    assert error.related_declaration_path.endswith(".artifact_field_conditions")
    assert error.context["counter_id"] == generic_admission.COUNTER_ID


def test_compiler_accepts_equal_runtime_owned_threshold_conditions() -> None:
    source = _runtime_admission_source()
    _set_counter_contract(
        source,
        schema_id=generic_fanout.CHILD_SCHEMA_ID,
        increment_conditions={"child_id": "same"},
        threshold_conditions={"child_id": "same"},
    )

    result = compile_workflow(source, selected_runner_policy=_CODEX_POLICY)

    assert result.plan is not None, result.diagnostics
    assert not [
        diagnostic
        for diagnostic in result.diagnostics
        if diagnostic.severity == "error"
    ]


def test_compiler_accepts_threshold_conditions_subset_of_increment_conditions() -> None:
    source = _runtime_admission_source()
    _set_counter_contract(
        source,
        schema_id=generic_fanout.CHILD_SCHEMA_ID,
        increment_conditions={"child_id": "same", "body": "increment"},
        threshold_conditions={"child_id": "same"},
    )

    result = compile_workflow(source, selected_runner_policy=_CODEX_POLICY)

    assert result.plan is not None, result.diagnostics
    assert not [
        diagnostic
        for diagnostic in result.diagnostics
        if diagnostic.severity == "error"
    ]


def test_neutral_result_mapping_negative_has_no_lad_vocabulary() -> None:
    source = _runtime_admission_source()
    assert "lad" not in repr(source).lower()
    mappings = cast(list[Record], source["runner_bindings"])[0][
        "terminal_result_mappings"
    ]
    assert generic_admission.COUNTER_THRESHOLD_ACTION_ID not in {
        mapping["outcome_id"]
        for mapping in cast(tuple[Record, ...], mappings)
    }

    _add_parent_schema(source, generic_admission.OTHER_SCHEMA_ID)
    _action(source, generic_admission.COUNTER_THRESHOLD_ACTION_ID)[
        "artifact_schema_id"
    ] = generic_admission.OTHER_SCHEMA_ID

    error = _find_error(
        _errors(source),
        "counter_runtime_threshold_artifact_schema_mismatch",
    )
    assert error.context["threshold_action_id"] == (
        generic_admission.COUNTER_THRESHOLD_ACTION_ID
    )


def test_non_lad_generic_fixture_uses_result_mapping_for_runtime_ownership() -> None:
    source = _generic_counter_source()
    assert source["workflow"] == generic_fanout.source()["workflow"]
    assert "lad" not in repr(source).lower()

    stage = cast(list[Record], source["stage_kinds"])[0]
    stage["artifact_schema_ids"] = (
        generic_fanout.PACKET_SCHEMA_ID,
        generic_fanout.CHILD_SCHEMA_ID,
    )
    threshold = _action(source, "generic.retry_exhausted")
    threshold["artifact_schema_id"] = generic_fanout.CHILD_SCHEMA_ID

    result = compile_workflow(source, selected_runner_policy=_CODEX_POLICY)

    assert result.plan is None
    error = _find_error(
        (
            diagnostic
            for diagnostic in result.diagnostics
            if diagnostic.severity == "error"
        ),
        "counter_runtime_threshold_artifact_schema_mismatch",
    )
    assert error.context["counter_id"] == "generic.counter"
