from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from datetime import UTC, datetime
from hashlib import sha256
from importlib import import_module
from typing import cast
from unicodedata import normalize

import pytest

from millrace.compiler import SelectedRunnerAdapterPolicy, compile_workflow
from millrace.compiler.canonical import (
    AUTHORITY_FINGERPRINT_DOMAIN_PREFIX,
    CanonicalAuthorityError,
    authority_fingerprint,
    canonical_authority_bytes,
)
from millrace.contracts import CompiledPlanEnvelope, SelectedCompiledPlan
from millrace.contracts.diagnostics import Diagnostic
from millrace.workflows import kernel_ping, simple_loop

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

TOP_LEVEL_DECLARATION_COLLECTIONS: tuple[str, ...] = (
    "partitions",
    "queue_families",
    "external_enqueue_routes",
    "artifact_schemas",
    "assets",
    "stage_kinds",
    "terminal_outcomes",
    "terminal_actions",
    "runner_bindings",
)


def _source() -> Source:
    return deepcopy(kernel_ping.WORKFLOW_SOURCE)


def _records(source: Source, key: str) -> list[Record]:
    return cast(list[Record], source[key])


def _compile_plan(source: Mapping[str, object]) -> SelectedCompiledPlan:
    result = compile_workflow(source)
    assert [
        diagnostic
        for diagnostic in result.diagnostics
        if diagnostic.severity == "error"
    ] == []
    assert result.plan is not None
    return result.plan


def _compile_errors(source: Mapping[str, object]) -> tuple[Diagnostic, ...]:
    try:
        result = compile_workflow(source)
    except Exception as exc:  # pragma: no cover - RED guard
        pytest.fail(f"compile_workflow raised instead of returning diagnostics: {exc}")
    assert result.plan is None
    return tuple(
        diagnostic
        for diagnostic in result.diagnostics
        if diagnostic.severity == "error"
    )


def _compile_errors_without_selected_build(
    source: Mapping[str, object],
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[Diagnostic, ...]:
    compiler_compile = import_module("millrace.compiler.compile")

    def unexpected_build(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("selected plan build should not run")

    monkeypatch.setattr(compiler_compile, "build_selected_plan", unexpected_build)
    return _compile_errors(source)


def _source_with_reordered_nonsemantic_maps() -> Source:
    source = _source()
    workflow = cast(Record, source["workflow"])
    source["workflow"] = {
        "required_extensions": workflow["required_extensions"],
        "compatibility_profile": workflow["compatibility_profile"],
        "name": workflow["name"],
        "version": workflow["version"],
        "id": workflow["id"],
    }

    for schema_record in _records(source, "artifact_schemas"):
        schema = cast(Record, schema_record["schema"])
        properties = cast(Record, schema["properties"])
        schema["properties"] = {
            key: properties[key] for key in reversed(tuple(properties.keys()))
        }

    return source


def _source_with_extra_top_level_declarations() -> Source:
    source = _source()
    _records(source, "partitions").append(
        {"id": "z_sandbox", "kind": "plane", "presentation": {}}
    )
    _records(source, "queue_families").append(
        {"id": "z_prompt", "external_enqueue": True, "presentation": {}}
    )
    _records(source, "graphs").append(
        {"id": "z_graph", "node_ids": ("z_stage.start",), "presentation": {}}
    )
    _records(source, "runner_bindings").append(
        {
            "id": "z_runner",
            "adapter_kind": "codex",
            "stage_kind_ids": ("z_stage",),
            "required_capability_ids": ("capability.runner.invoke",),
            "presentation": {},
        }
    )
    _records(source, "stage_kinds").append(
        {
            "id": "z_stage",
            "partition_id": "z_sandbox",
            "runner_binding_id": "z_runner",
            "input_queue_family_ids": ("z_prompt",),
            "output_queue_family_ids": (),
            "artifact_schema_ids": (),
            "asset_ids": (),
            "declared_outcome_ids": (),
            "presentation": {},
        }
    )
    _records(source, "external_enqueue_routes").append(
        {
            "id": "z_external_prompt",
            "queue_family_id": "z_prompt",
            "graph_node_id": "z_stage.start",
            "stage_kind_id": "z_stage",
            "runner_binding_id": "z_runner",
        }
    )
    return source


def _source_with_reordered_top_level_declarations(source: Source) -> Source:
    source = deepcopy(source)
    for collection_key in TOP_LEVEL_DECLARATION_COLLECTIONS:
        source[collection_key] = list(reversed(_records(source, collection_key)))
    return source


def _simple_loop_source() -> Source:
    return simple_loop.workflow_source()


def _add_second_operator_wait_source_action(source: Source) -> None:
    second_outcome_id = "simple_loop.manager.needs_operator_detail_secondary"
    second_action_id = "simple_loop.manager.needs_operator_detail_secondary"
    manager_stage = next(
        record
        for record in _records(source, "stage_kinds")
        if record["id"] == "simple_loop.manager"
    )
    manager_stage["declared_outcome_ids"] = (
        *cast(tuple[str, ...], manager_stage["declared_outcome_ids"]),
        second_outcome_id,
    )
    _records(source, "terminal_outcomes").append(
        {
            "id": second_outcome_id,
            "stage_kind_id": "simple_loop.manager",
            "marker": "NEEDS_OPERATOR_DETAIL_SECONDARY",
            "presentation": {"display_name": second_outcome_id},
        }
    )
    _records(source, "terminal_actions").append(
        {
            "id": second_action_id,
            "stage_kind_id": "simple_loop.manager",
            "outcome_id": second_outcome_id,
            "kind": "operator_wait",
            "artifact_schema_id": "simple_loop.detail_request",
            "presentation": {"display_name": second_action_id},
        }
    )
    operator_wait = next(
        record
        for record in _records(source, "operator_waits")
        if record["id"] == "simple_loop.manager_detail_wait"
    )
    operator_wait["source_action_ids"] = (
        *cast(tuple[str, ...], operator_wait["source_action_ids"]),
        second_action_id,
    )


def test_same_selected_authority_yields_same_bytes_and_fingerprint() -> None:
    first_plan = _compile_plan(_source())
    second_plan = _compile_plan(_source())
    reordered_plan = _compile_plan(_source_with_reordered_nonsemantic_maps())

    assert first_plan == second_plan
    assert first_plan == reordered_plan

    first_bytes = canonical_authority_bytes(first_plan)
    assert first_bytes == canonical_authority_bytes(second_plan)
    assert first_bytes == canonical_authority_bytes(reordered_plan)
    assert authority_fingerprint(first_plan) == authority_fingerprint(second_plan)
    assert authority_fingerprint(first_plan) == authority_fingerprint(reordered_plan)


def test_top_level_declaration_order_does_not_change_selected_authority() -> None:
    base_source = _source_with_extra_top_level_declarations()
    base_plan = _compile_plan(base_source)
    reordered_plan = _compile_plan(
        _source_with_reordered_top_level_declarations(base_source)
    )

    assert reordered_plan == base_plan
    assert canonical_authority_bytes(reordered_plan) == canonical_authority_bytes(
        base_plan
    )
    assert authority_fingerprint(reordered_plan) == authority_fingerprint(base_plan)


def test_operator_wait_resolution_order_does_not_change_selected_authority() -> None:
    base_source = _simple_loop_source()
    reordered_source = _simple_loop_source()
    operator_wait = next(
        record
        for record in _records(reordered_source, "operator_waits")
        if record["id"] == "simple_loop.manager_detail_wait"
    )
    operator_wait["allowed_resolution_kinds"] = (
        "revise_recorded_source",
        "close_recorded_source",
        "resume_recorded_source",
    )

    base_result = compile_workflow(base_source, selected_runner_policy=_CODEX_POLICY)
    reordered_result = compile_workflow(
        reordered_source, selected_runner_policy=_CODEX_POLICY
    )
    assert base_result.plan is not None
    assert reordered_result.plan is not None
    base_plan = base_result.plan
    reordered_plan = reordered_result.plan

    assert reordered_plan == base_plan
    assert canonical_authority_bytes(reordered_plan) == canonical_authority_bytes(
        base_plan
    )
    assert authority_fingerprint(reordered_plan) == authority_fingerprint(base_plan)


def test_operator_wait_source_action_order_does_not_change_selected_authority() -> None:
    base_source = _simple_loop_source()
    reordered_source = _simple_loop_source()
    _add_second_operator_wait_source_action(base_source)
    _add_second_operator_wait_source_action(reordered_source)
    operator_wait = next(
        record
        for record in _records(reordered_source, "operator_waits")
        if record["id"] == "simple_loop.manager_detail_wait"
    )
    operator_wait["source_action_ids"] = tuple(
        reversed(cast(tuple[str, ...], operator_wait["source_action_ids"]))
    )

    base_result = compile_workflow(base_source, selected_runner_policy=_CODEX_POLICY)
    reordered_result = compile_workflow(
        reordered_source, selected_runner_policy=_CODEX_POLICY
    )
    assert base_result.plan is not None
    assert reordered_result.plan is not None
    base_plan = base_result.plan
    reordered_plan = reordered_result.plan

    assert reordered_plan == base_plan
    assert canonical_authority_bytes(reordered_plan) == canonical_authority_bytes(
        base_plan
    )
    assert authority_fingerprint(reordered_plan) == authority_fingerprint(base_plan)


def test_kernel_ping_authority_bytes_and_fingerprint_match_golden() -> None:
    plan = _compile_plan(_source())
    authority_bytes = canonical_authority_bytes(plan)

    assert len(authority_bytes) == 13083
    assert sha256(authority_bytes).hexdigest() == (
        "c08684dbd48ee0ebb1041d5006003c20c8f2928f15efa89d1244e50da74cb1b2"
    )
    assert authority_fingerprint(plan) == (
        "sha256:29d40efa187bef7c2ad2a143f8a685a6f6dbb21dcfdf05258b50c1c1c2586d42"
    )


def test_envelope_metadata_is_excluded_from_authority_bytes_and_fingerprint() -> None:
    result = compile_workflow(_source())
    assert result.plan is not None
    plan = result.plan
    bare_bytes = canonical_authority_bytes(plan)
    bare_fingerprint = authority_fingerprint(plan)

    first_envelope = CompiledPlanEnvelope(
        selected_authority=plan,
        diagnostics=result.diagnostics,
        metadata={
            "source_uri": "/tmp/one/kernel_ping.py",
            "compile_timestamp": datetime(2026, 6, 20, tzinfo=UTC),
            "debug_label": "first compile",
            "source_spans": {"workflow.id": (1, 2)},
        },
    )
    second_envelope = CompiledPlanEnvelope(
        selected_authority=plan,
        diagnostics=(),
        metadata={
            "source_uri": "/tmp/two/kernel_ping.py",
            "compile_timestamp": datetime(2030, 1, 1, tzinfo=UTC),
            "debug_label": "second compile",
            "source_spans": {"workflow.id": (99, 100)},
        },
    )

    assert canonical_authority_bytes(first_envelope) == bare_bytes
    assert canonical_authority_bytes(second_envelope) == bare_bytes
    assert authority_fingerprint(first_envelope) == bare_fingerprint
    assert authority_fingerprint(second_envelope) == bare_fingerprint

    with pytest.raises(AttributeError):
        setattr(first_envelope, "selected_authority", plan)


def test_canonical_map_ordering_has_golden_bytes() -> None:
    value = {
        "z": 1,
        "a": True,
        "aa": None,
        "b": ("x", 2),
    }

    assert canonical_authority_bytes(value) == b'{"a":true,"aa":null,"b":["x",2],"z":1}'


@pytest.mark.parametrize(
    "value",
    [
        1.5,
        datetime(2026, 6, 20, tzinfo=UTC),
        {"items": {"not", "ordered"}},
        b"raw bytes",
        {1: "non-string key"},
        object(),
    ],
)
def test_canonical_authority_rejects_unsupported_values(value: object) -> None:
    with pytest.raises(CanonicalAuthorityError):
        canonical_authority_bytes(value)


def test_authority_fingerprint_is_domain_separated_sha256() -> None:
    value = {"selected": "authority"}
    authority_bytes = canonical_authority_bytes(value)

    assert AUTHORITY_FINGERPRINT_DOMAIN_PREFIX == b"millrace-authority-v1\0"
    assert authority_fingerprint(value) == (
        "sha256:"
        + sha256(AUTHORITY_FINGERPRINT_DOMAIN_PREFIX + authority_bytes).hexdigest()
    )


def test_authority_fingerprint_rejects_raw_bytes_input() -> None:
    with pytest.raises(CanonicalAuthorityError):
        authority_fingerprint(b"not canonical authority bytes")


def test_compile_diagnoses_unsupported_selected_authority_value() -> None:
    source = _source()
    schema_record = _records(source, "artifact_schemas")[0]
    schema_body = cast(Record, schema_record["schema"])
    properties = cast(Record, schema_body["properties"])
    title_schema = cast(Record, properties["title"])
    title_schema["unsupported_float"] = 1.5

    diagnostics = _compile_errors(source)

    unsupported = [
        diagnostic
        for diagnostic in diagnostics
        if diagnostic.code == "unsupported_authority_value"
    ]
    assert len(unsupported) == 1
    assert unsupported[0].phase == "semantic_validation"
    assert unsupported[0].declaration_path == (
        "artifact_schemas[0].schema.properties.title.unsupported_float"
    )
    assert unsupported[0].context["unsupported_type"] == "float"
    assert unsupported[0].context["value_kind"] == "value"
    assert unsupported[0].hint is not None


def test_compile_diagnoses_non_string_selected_authority_map_key() -> None:
    source = _source()
    partition = _records(source, "partitions")[0]
    presentation = cast(dict[object, object], partition["presentation"])
    presentation[1] = "non-string key"

    diagnostics = _compile_errors(source)

    unsupported = [
        diagnostic
        for diagnostic in diagnostics
        if diagnostic.code == "unsupported_authority_value"
    ]
    assert len(unsupported) == 1
    assert unsupported[0].declaration_path == "partitions[0].presentation.<int>"
    assert unsupported[0].context["unsupported_type"] == "int"
    assert unsupported[0].context["value_kind"] == "map_key"


def test_compile_diagnoses_non_nfc_declaration_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _source()
    decomposed_id = "kernel_ping.cafe\u0301_prompt"
    assert normalize("NFC", decomposed_id) != decomposed_id
    _records(source, "assets").append(
        {
            "id": decomposed_id,
            "kind": "prompt",
            "body": "Non-NFC ids must be refused, not normalized.",
            "presentation": {},
        }
    )

    diagnostics = _compile_errors_without_selected_build(source, monkeypatch)

    non_nfc = [
        diagnostic for diagnostic in diagnostics if diagnostic.code == "non_nfc_id"
    ]
    assert len(non_nfc) == 1
    assert non_nfc[0].declaration_path == "assets[4].id"
    assert non_nfc[0].context["namespace"] == "asset"
    assert non_nfc[0].context["identifier_nfc"] == normalize("NFC", decomposed_id)


def test_compile_diagnoses_canonically_equivalent_duplicate_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _source()
    decomposed_id = "kernel_ping.cafe\u0301_prompt"
    precomposed_id = normalize("NFC", decomposed_id)
    assert precomposed_id != decomposed_id
    _records(source, "assets").extend(
        (
            {
                "id": precomposed_id,
                "kind": "prompt",
                "body": "Canonical duplicate anchor.",
                "presentation": {},
            },
            {
                "id": decomposed_id,
                "kind": "prompt",
                "body": "Canonically equivalent duplicate.",
                "presentation": {},
            },
        )
    )

    diagnostics = _compile_errors_without_selected_build(source, monkeypatch)

    equivalent = [
        diagnostic
        for diagnostic in diagnostics
        if diagnostic.code == "canonically_equivalent_id"
    ]
    assert len(equivalent) == 1
    assert equivalent[0].declaration_path == "assets[5].id"
    assert equivalent[0].related_declaration_path == "assets[4].id"
    assert equivalent[0].context["namespace"] == "asset"
    assert equivalent[0].context["canonical_id"] == precomposed_id


def test_compile_diagnoses_non_nfc_selected_authority_map_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _source()
    non_nfc_key = "cafe\u0301"
    assert normalize("NFC", non_nfc_key) != non_nfc_key
    partition = _records(source, "partitions")[0]
    presentation = cast(dict[str, object], partition["presentation"])
    presentation["details"] = {non_nfc_key: "not canonical"}

    diagnostics = _compile_errors_without_selected_build(source, monkeypatch)

    non_nfc_key_diagnostics = [
        diagnostic
        for diagnostic in diagnostics
        if diagnostic.code == "non_nfc_authority_map_key"
    ]
    assert len(non_nfc_key_diagnostics) == 1
    assert (
        non_nfc_key_diagnostics[0].declaration_path
        == "partitions[0].presentation.details.<non_nfc_key>"
    )
    assert non_nfc_key_diagnostics[0].context["map_key"] == non_nfc_key
    assert non_nfc_key_diagnostics[0].context["map_key_nfc"] == normalize(
        "NFC", non_nfc_key
    )


def test_compile_diagnoses_unsupported_workflow_name_scalar() -> None:
    source = _source()
    workflow = cast(Record, source["workflow"])
    workflow["name"] = object()

    diagnostics = _compile_errors(source)

    unsupported = [
        diagnostic
        for diagnostic in diagnostics
        if diagnostic.code == "unsupported_authority_value"
    ]
    assert len(unsupported) == 1
    assert unsupported[0].declaration_path == "workflow.name"
    assert unsupported[0].context["unsupported_type"] == "object"
    assert unsupported[0].context["value_kind"] == "value"


def test_compile_diagnoses_unsupported_terminal_action_kind_scalar() -> None:
    source = _source()
    action = _records(source, "terminal_actions")[0]
    action["kind"] = b"raw_action_kind"

    diagnostics = _compile_errors(source)

    unsupported = [
        diagnostic
        for diagnostic in diagnostics
        if diagnostic.code == "unsupported_authority_value"
    ]
    assert len(unsupported) == 1
    assert unsupported[0].declaration_path == "terminal_actions[0].kind"
    assert unsupported[0].context["unsupported_type"] == "bytes"
    assert unsupported[0].context["value_kind"] == "value"


def test_compile_diagnoses_unsupported_queue_external_enqueue_scalar() -> None:
    source = _source()
    queue_family = _records(source, "queue_families")[0]
    queue_family["external_enqueue"] = 1

    diagnostics = _compile_errors(source)

    unsupported = [
        diagnostic
        for diagnostic in diagnostics
        if diagnostic.code == "unsupported_authority_value"
    ]
    assert len(unsupported) == 1
    assert unsupported[0].declaration_path == "queue_families[0].external_enqueue"
    assert unsupported[0].context["unsupported_type"] == "int"
    assert unsupported[0].context["value_kind"] == "value"


def test_compile_diagnoses_unsupported_selected_reference_sequence_item() -> None:
    source = _source()
    stage = _records(source, "stage_kinds")[0]
    stage["asset_ids"] = ("kernel_ping.taskmaster_prompt", object())

    diagnostics = _compile_errors(source)

    unsupported = [
        diagnostic
        for diagnostic in diagnostics
        if diagnostic.code == "unsupported_authority_value"
    ]
    assert len(unsupported) == 1
    assert unsupported[0].declaration_path == "stage_kinds[0].asset_ids[1]"
    assert unsupported[0].context["unsupported_type"] == "object"
    assert unsupported[0].context["value_kind"] == "value"


def test_compile_diagnoses_empty_selected_reference_sequence_item() -> None:
    source = _source()
    stage = _records(source, "stage_kinds")[0]
    stage["asset_ids"] = [""]

    diagnostics = _compile_errors(source)

    unsupported = [
        diagnostic
        for diagnostic in diagnostics
        if diagnostic.code == "unsupported_authority_value"
    ]
    assert len(unsupported) == 1
    assert unsupported[0].declaration_path == "stage_kinds[0].asset_ids[0]"
    assert unsupported[0].context["unsupported_type"] == "str"
    assert unsupported[0].context["value_kind"] == "empty_string"


def test_compile_diagnoses_malformed_required_extensions_entry() -> None:
    source = _source()
    workflow = cast(Record, source["workflow"])
    workflow["required_extensions"] = [object()]

    diagnostics = _compile_errors(source)

    unsupported = [
        diagnostic
        for diagnostic in diagnostics
        if diagnostic.code == "unsupported_authority_value"
    ]
    assert len(unsupported) == 1
    assert unsupported[0].declaration_path == "workflow.required_extensions[0]"
    assert unsupported[0].context["unsupported_type"] == "object"
    assert unsupported[0].context["value_kind"] == "value"
