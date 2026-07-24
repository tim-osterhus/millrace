from __future__ import annotations

from typing import cast

from millrace.compiler import SelectedRunnerAdapterPolicy
from millrace.compiler import compile_workflow as _raw_compile_workflow
from millrace.contracts import QueueFamilyId, StageKindId
from support import generic_fanout

Source = dict[str, object]
_CODEX_POLICY = SelectedRunnerAdapterPolicy(
    default_adapter_kind="codex",
    supported_adapter_kinds=frozenset({"codex"}),
    component_bound_adapter_kinds=frozenset(),
    default_component_selector=None,
    default_component_required_capability_ids=frozenset(),
    default_component_requires_complete_mappings=False,
)


def _compile_codex(source: Source):
    return _raw_compile_workflow(source, selected_runner_policy=_CODEX_POLICY)


Record = dict[str, object]


def _records(source: Source, key: str) -> list[Record]:
    return cast(list[Record], source[key])


def _fanout_error(source: Source, declaration_path_suffix: str, reason: str):
    result = _compile_codex(source)

    assert result.plan is None
    error = next(
        diagnostic
        for diagnostic in result.diagnostics
        if diagnostic.declaration_path.endswith(declaration_path_suffix)
    )
    assert error.code == "invalid_fanout_declaration"
    assert error.context["reason"] == reason
    return error


def test_selected_fanout_declaration_targets_selected_route_and_schema() -> None:
    plan, _fingerprint = generic_fanout.compile_fanout()

    fanout = plan.fanout_declarations[0]
    assert str(fanout.id) == "fanout.packet.children"
    assert str(fanout.source_action_id) == "fanout.parent.close"
    assert str(fanout.source_artifact_schema_id) == generic_fanout.PACKET_SCHEMA_ID
    assert fanout.item_source_path == ("items",)
    assert fanout.item_id_key == "item_id"
    assert fanout.target_route_id == "child"
    assert fanout.target_queue_family_id == QueueFamilyId("child")
    assert fanout.target_stage_kind_id == StageKindId("child_stage")
    assert str(fanout.target_payload_schema_id) == generic_fanout.CHILD_SCHEMA_ID
    assert fanout.duplicate_policy == "refuse"
    assert fanout.root_lineage_policy == "inherit_source_lineage"
    assert fanout.dependency_policy == "depends_on_source_work_item"


def test_fanout_rejects_missing_target_route() -> None:
    source = generic_fanout.source()
    fanout = _records(source, "fanout_declarations")[0]
    fanout["target_route_id"] = "missing"

    result = _compile_codex(source)

    assert result.plan is None
    error = next(
        diagnostic
        for diagnostic in result.diagnostics
        if diagnostic.declaration_path.endswith(".target_route_id")
    )
    assert error.code == "missing_reference"
    assert error.context["reference_kind"] == "external_enqueue_route"
    assert error.context["referenced_id"] == "missing"


def test_fanout_rejects_missing_source_artifact_schema() -> None:
    source = generic_fanout.source()
    fanout = _records(source, "fanout_declarations")[0]
    fanout["source_artifact_schema_id"] = "fanout.missing"

    result = _compile_codex(source)

    assert result.plan is None
    error = next(
        diagnostic
        for diagnostic in result.diagnostics
        if diagnostic.declaration_path.endswith(".source_artifact_schema_id")
    )
    assert error.code == "missing_reference"
    assert error.context["reference_kind"] == "artifact_schema"
    assert error.context["referenced_id"] == "fanout.missing"


def test_fanout_rejects_source_action_schema_mismatch() -> None:
    source = generic_fanout.source()
    action = _records(source, "terminal_actions")[0]
    action["artifact_schema_id"] = generic_fanout.CHILD_SCHEMA_ID

    _fanout_error(
        source,
        ".source_artifact_schema_id",
        "source_action_schema_mismatch",
    )


def test_fanout_rejects_missing_target_payload_schema() -> None:
    source = generic_fanout.source()
    fanout = _records(source, "fanout_declarations")[0]
    fanout["target_payload_schema_id"] = "fanout.missing"

    result = _compile_codex(source)

    assert result.plan is None
    error = next(
        diagnostic
        for diagnostic in result.diagnostics
        if diagnostic.declaration_path.endswith(".target_payload_schema_id")
    )
    assert error.code == "missing_reference"
    assert error.context["reference_kind"] == "artifact_schema"
    assert error.context["referenced_id"] == "fanout.missing"


def test_fanout_rejects_target_route_payload_schema_mismatch() -> None:
    source = generic_fanout.source()
    route = _records(source, "external_enqueue_routes")[1]
    route["payload_schema_id"] = generic_fanout.PACKET_SCHEMA_ID

    _fanout_error(source, ".target_route_id", "target_route_contract_mismatch")


def test_fanout_rejects_missing_root_lineage_policy() -> None:
    source = generic_fanout.source()
    fanout = _records(source, "fanout_declarations")[0]
    fanout.pop("root_lineage_policy")

    result = _compile_codex(source)

    assert result.plan is None
    error = next(
        diagnostic
        for diagnostic in result.diagnostics
        if diagnostic.declaration_path.endswith(".root_lineage_policy")
    )
    assert error.code == "invalid_fanout_declaration"
    assert error.context["reason"] == "unsupported_root_lineage_policy"


def test_fanout_rejects_unsupported_duplicate_policy() -> None:
    source = generic_fanout.source()
    fanout = _records(source, "fanout_declarations")[0]
    fanout["duplicate_policy"] = "merge"

    result = _compile_codex(source)

    assert result.plan is None
    error = next(
        diagnostic
        for diagnostic in result.diagnostics
        if diagnostic.declaration_path.endswith(".duplicate_policy")
    )
    assert error.code == "invalid_fanout_declaration"
    assert error.context["reason"] == "unsupported_duplicate_policy"


def test_fanout_rejects_unsupported_dependency_policy() -> None:
    source = generic_fanout.source()
    fanout = _records(source, "fanout_declarations")[0]
    fanout["dependency_policy"] = "none"

    _fanout_error(source, ".dependency_policy", "unsupported_dependency_policy")


def test_fanout_rejects_bad_item_source_path() -> None:
    source = generic_fanout.source()
    fanout = _records(source, "fanout_declarations")[0]
    fanout["item_source_path"] = ()

    _fanout_error(source, ".item_source_path", "unsupported_item_source_path")


def test_fanout_rejects_missing_item_id_key() -> None:
    source = generic_fanout.source()
    fanout = _records(source, "fanout_declarations")[0]
    fanout.pop("item_id_key")

    _fanout_error(source, ".item_id_key", "unsupported_item_id_key")


def test_fanout_rejects_blank_item_id_key() -> None:
    source = generic_fanout.source()
    fanout = _records(source, "fanout_declarations")[0]
    fanout["item_id_key"] = ""

    _fanout_error(source, ".item_id_key", "unsupported_item_id_key")


def test_fanout_rejects_bad_target_payload_mapping() -> None:
    source = generic_fanout.source()
    fanout = _records(source, "fanout_declarations")[0]
    fanout["target_payload_mapping"] = {"child_id": ()}

    _fanout_error(
        source,
        ".target_payload_mapping",
        "unsupported_target_payload_mapping",
    )


def test_fanout_rejects_missing_target_payload_mapping() -> None:
    source = generic_fanout.source()
    fanout = _records(source, "fanout_declarations")[0]
    fanout.pop("target_payload_mapping")

    _fanout_error(
        source,
        ".target_payload_mapping",
        "unsupported_target_payload_mapping",
    )


def test_fanout_rejects_empty_target_payload_mapping() -> None:
    source = generic_fanout.source()
    fanout = _records(source, "fanout_declarations")[0]
    fanout["target_payload_mapping"] = {}

    _fanout_error(
        source,
        ".target_payload_mapping",
        "unsupported_target_payload_mapping",
    )


def test_fanout_rejects_target_route_outside_target_stage_authority() -> None:
    source = generic_fanout.source()
    route = _records(source, "external_enqueue_routes")[1]
    route["stage_kind_id"] = "parent_stage"

    result = _compile_codex(source)

    assert result.plan is None
    error = next(
        diagnostic
        for diagnostic in result.diagnostics
        if diagnostic.declaration_path.endswith(".target_route_id")
    )
    assert error.code == "invalid_fanout_declaration"
    assert error.context["reason"] == "target_route_contract_mismatch"


def test_fanout_rejects_non_closing_source_action() -> None:
    source = generic_fanout.source()
    action = _records(source, "terminal_actions")[0]
    action.update(
        {
            "kind": "route",
            "target_stage_kind_id": "parent_stage",
            "target_graph_node_id": "fanout.parent.start",
            "emitted_queue_family_id": "parent",
            "runner_binding_id": "fanout.runner",
        }
    )

    _fanout_error(source, ".source_action_id", "unsupported_source_action_kind")
