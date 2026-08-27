from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from dataclasses import replace
from typing import cast

import pytest

from millrace.compiler import (
    SelectedRunnerAdapterPolicy,
    authority_fingerprint,
    compile_workflow,
)
from millrace.compiler.export import compiled_plan_export_bytes
from millrace.contracts.ids import ActionId, ArtifactSchemaId
from millrace.workflows import kernel_ping


def _writeback_schema() -> dict[str, object]:
    string_schema = {"type": "string"}
    evidence_refs = {"type": "array", "items": string_schema}
    return {
        "type": "object",
        "required": ("changes", "proposals"),
        "properties": {
            "changes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": (
                        "path",
                        "change_kind",
                        "evidence_refs",
                        "classification",
                    ),
                    "properties": {
                        "path": string_schema,
                        "change_kind": {
                            "enum": ("create", "modify", "delete")
                        },
                        "before_sha256": string_schema,
                        "after_sha256": string_schema,
                        "evidence_refs": evidence_refs,
                        "classification": {"const": "direct_write"},
                    },
                },
            },
            "proposals": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": (
                        "path",
                        "proposed_content",
                        "proposed_content_sha256",
                        "evidence_refs",
                        "classification",
                    ),
                    "properties": {
                        "path": string_schema,
                        "proposed_content": string_schema,
                        "proposed_content_sha256": string_schema,
                        "evidence_refs": evidence_refs,
                        "classification": {"const": "protected_proposal"},
                    },
                },
            },
            "no_op_reason": string_schema,
        },
    }


def _source_with_context_binding(
    *,
    write_enabled: bool = False,
) -> dict[str, object]:
    source = deepcopy(kernel_ping.WORKFLOW_SOURCE)
    runners = cast(list[dict[str, object]], source["runner_bindings"])
    runner = next(
        item
        for item in runners
        if item["id"] == "kernel_ping.taskmaster_runner"
    )
    runner["adapter_kind"] = "codex"
    worker_runner = next(
        item for item in runners if item["id"] == "kernel_ping.worker_runner"
    )
    worker_runner["adapter_kind"] = "codex"
    assets = cast(list[dict[str, object]], source["assets"])
    assets.append(
        {
            "id": "kernel_ping.context_router",
            "kind": "template",
            "body": "Route selected context.",
        }
    )
    binding: dict[str, object] = {
        "id": "kernel_ping.taskmaster_context",
        "stage_kind_id": "kernel_ping.taskmaster",
        "router_asset_id": "kernel_ping.context_router",
        "checkout_root": "checkout",
        "max_hydrated_files": 16,
        "max_hydrated_bytes": 16_384,
        "mutation_policy": (
            "reconcile_selected_writes"
            if write_enabled
            else "forbid_selected_roots"
        ),
        "materialization_retention": "until_session_durable_terminal",
        "required_sources": [
            {
                "source_kind": "dispatch_material",
                "source_ref": "current",
                "max_files": 8,
                "max_bytes": 4096,
            }
        ],
        "discoverable_sources": [
            {
                "source_kind": "workspace_relative_root",
                "source_ref": "docs",
                "max_files": 4,
                "max_bytes": 2048,
            }
        ],
    }
    if write_enabled:
        binding["stage_kind_id"] = "kernel_ping.worker"
        binding["required_sources"] = [
            {
                "source_kind": "workspace_relative_root",
                "source_ref": "src",
                "max_files": 8,
                "max_bytes": 4096,
            }
        ]
        binding["discoverable_sources"] = []
        binding["write_rules"] = [
            {"relative_root": "src", "disposition": "direct_write"}
        ]
        binding["writeback_terminal_action_id"] = "kernel_ping.close_worker_success"
        binding["writeback_artifact_schema_id"] = "kernel_ping.context_writeback"
        schemas = cast(list[dict[str, object]], source["artifact_schemas"])
        schemas.append(
            {
                "id": "kernel_ping.context_writeback",
                "schema": _writeback_schema(),
                "presentation": {},
            }
        )
        stages = cast(list[dict[str, object]], source["stage_kinds"])
        worker = next(item for item in stages if item["id"] == "kernel_ping.worker")
        worker["artifact_schema_ids"] = (
            *cast(tuple[str, ...], worker["artifact_schema_ids"]),
            "kernel_ping.context_writeback",
        )
        actions = cast(list[dict[str, object]], source["terminal_actions"])
        close_action = next(
            item
            for item in actions
            if item["id"] == "kernel_ping.close_worker_success"
        )
        close_action["artifact_schema_id"] = "kernel_ping.context_writeback"
    source["context_bindings"] = [binding]
    return source


SUPPORTED_CONTEXT_SOURCE_PAIRS: tuple[tuple[str, str], ...] = (
    ("dispatch_material", "current"),
    ("workspace_relative_root", "docs"),
    ("selected_artifacts", "direct_predecessors"),
    ("selected_artifacts", "current_lineage"),
    ("selected_attempts", "since_last_accepted_transition"),
    ("selected_attempts", "current_lineage"),
)

_OPAQUE_LOCAL_POLICY = SelectedRunnerAdapterPolicy(
    default_adapter_kind="opaque_local",
    supported_adapter_kinds=frozenset({"opaque_local"}),
    component_bound_adapter_kinds=frozenset(),
    default_component_selector=None,
    default_component_required_capability_ids=frozenset(),
    default_component_requires_complete_mappings=False,
)


def _source_with_opaque_local_context_binding() -> dict[str, object]:
    source = _source_with_context_binding()
    for runner in cast(list[dict[str, object]], source["runner_bindings"]):
        runner["adapter_kind"] = "opaque_local"
        runner.pop("component_pin", None)
        runner.pop("terminal_result_mappings", None)
    return source


def _context_binding(source: dict[str, object]) -> dict[str, object]:
    return cast(list[dict[str, object]], source["context_bindings"])[0]


def _refuses(
    source: dict[str, object],
    expected_code: str,
    case_id: str = "context binding",
) -> None:
    result = compile_workflow(source)
    assert result.plan is None
    assert any(
        diagnostic.severity == "error" and diagnostic.code == expected_code
        for diagnostic in result.diagnostics
    ), (case_id, result.diagnostics)


def test_compiles_generic_context_binding() -> None:
    result = compile_workflow(_source_with_context_binding())

    assert result.plan is not None
    assert result.plan.schema_version == 18
    assert len(result.plan.context_bindings) == 1
    binding = result.plan.context_bindings[0]
    assert binding.schema_version == 2
    assert binding.max_hydrated_files == 16
    assert binding.max_hydrated_bytes == 16_384
    assert binding.required_sources[0].empty_policy == "require_nonempty"
    assert binding.mutation_policy == "forbid_selected_roots"
    assert binding.materialization_retention == "until_session_durable_terminal"


def test_compiles_omit_if_absent_for_required_direct_predecessors() -> None:
    source = _source_with_context_binding()
    _context_binding(source)["required_sources"] = [
        {
            "source_kind": "selected_artifacts",
            "source_ref": "direct_predecessors",
            "empty_policy": "omit_if_absent",
            "max_files": 8,
            "max_bytes": 4096,
        }
    ]
    _context_binding(source)["discoverable_sources"] = []

    result = compile_workflow(source)

    assert result.plan is not None
    assert result.plan.context_bindings[0].required_sources[0].empty_policy == (
        "omit_if_absent"
    )


@pytest.mark.parametrize(
    ("required", "source_kind", "source_ref"),
    (
        (True, "dispatch_material", "current"),
        (True, "selected_artifacts", "current_lineage"),
        (True, "selected_attempts", "current_lineage"),
        (True, "workspace_relative_root", "docs"),
        (False, "selected_artifacts", "direct_predecessors"),
    ),
)
def test_rejects_omit_if_absent_for_unsupported_context_sources(
    required: bool,
    source_kind: str,
    source_ref: str,
) -> None:
    source = _source_with_context_binding()
    declaration = {
        "source_kind": source_kind,
        "source_ref": source_ref,
        "empty_policy": "omit_if_absent",
        "max_files": 8,
        "max_bytes": 4096,
    }
    _context_binding(source)["required_sources"] = [declaration] if required else []
    _context_binding(source)["discoverable_sources"] = (
        [] if required else [declaration]
    )

    _refuses(source, "context_binding_source_empty_policy")


@pytest.mark.parametrize("empty_policy", ("", "invalid"))
def test_rejects_invalid_context_source_empty_policy(empty_policy: str) -> None:
    source = _source_with_context_binding()
    required = cast(
        list[dict[str, object]],
        _context_binding(source)["required_sources"],
    )
    required[0]["empty_policy"] = empty_policy

    _refuses(source, "context_binding_source_empty_policy")


@pytest.mark.parametrize(
    ("source_kind", "source_ref"),
    SUPPORTED_CONTEXT_SOURCE_PAIRS,
)
def test_compiles_each_supported_context_source_pair(
    source_kind: str,
    source_ref: str,
) -> None:
    source = _source_with_context_binding()
    _context_binding(source)["required_sources"] = [
        {
            "source_kind": source_kind,
            "source_ref": source_ref,
            "max_files": 8,
            "max_bytes": 4096,
        }
    ]
    _context_binding(source)["discoverable_sources"] = []

    result = compile_workflow(source)

    assert result.plan is not None
    assert result.plan.context_bindings[0].required_sources[0].source_kind == (
        source_kind
    )
    assert result.plan.context_bindings[0].required_sources[0].source_ref == (
        source_ref
    )


@pytest.mark.parametrize(
    "field_name",
    (
        "max_hydrated_files",
        "max_hydrated_bytes",
        "mutation_policy",
        "materialization_retention",
    ),
)
def test_refuses_omitted_context_binding_v2_field(field_name: str) -> None:
    source = _source_with_context_binding()
    _context_binding(source).pop(field_name)

    _refuses(source, "context_binding_shape")


@pytest.mark.parametrize(
    ("field_name", "value"),
    (
        ("max_hydrated_files", 0),
        ("max_hydrated_files", -1),
        ("max_hydrated_files", 2**63),
        ("max_hydrated_bytes", 0),
        ("max_hydrated_bytes", -1),
        ("max_hydrated_bytes", 2**63),
        ("max_hydrated_files", True),
        ("max_hydrated_bytes", 1.0),
    ),
)
def test_refuses_invalid_context_hydration_bounds(
    field_name: str,
    value: object,
) -> None:
    source = _source_with_context_binding()
    _context_binding(source)[field_name] = value

    _refuses(source, "context_binding_hydration_bounds")


@pytest.mark.parametrize(
    ("field_name", "value", "expected_code"),
    (
        (
            "mutation_policy",
            "allow_selected_roots",
            "context_binding_mutation_policy",
        ),
        (
            "materialization_retention",
            "until_process_exit",
            "context_binding_materialization_retention",
        ),
        ("mutation_policy", None, "context_binding_mutation_policy"),
        (
            "materialization_retention",
            None,
            "context_binding_materialization_retention",
        ),
    ),
)
def test_refuses_invalid_context_mutation_or_retention(
    field_name: str,
    value: object,
    expected_code: str,
) -> None:
    source = _source_with_context_binding()
    _context_binding(source)[field_name] = value

    _refuses(source, expected_code)


@pytest.mark.parametrize(
    ("write_enabled", "mutation_policy"),
    (
        (False, "reconcile_selected_writes"),
        (True, "forbid_selected_roots"),
    ),
)
def test_refuses_illegal_context_mutation_and_write_rule_combination(
    write_enabled: bool,
    mutation_policy: str,
) -> None:
    source = _source_with_context_binding(write_enabled=write_enabled)
    _context_binding(source)["mutation_policy"] = mutation_policy

    _refuses(source, "context_binding_mutation_write_rules")


def test_context_binding_does_not_leak_unselected_catalog() -> None:
    source = _source_with_context_binding()
    source["unselected_catalog"] = [
        {
            "id": "secret.unselected.context",
            "content": "UNSELECTED_CONTEXT_SENTINEL",
        }
    ]

    result = compile_workflow(source)

    assert result.plan is not None
    assert b"unselected_catalog" not in compiled_plan_export_bytes(result.plan)
    assert b"UNSELECTED_CONTEXT_SENTINEL" not in compiled_plan_export_bytes(
        result.plan
    )


def test_context_bound_schema17_authority_is_refused_without_migration() -> None:
    from millrace.contracts.compiled_plan import context_binding_authority_refusal

    legacy_authority = {
        "record_kind": "selected_compiled_plan",
        "schema_version": 17,
        "context_bindings": [{"id": "legacy.context"}],
    }

    assert (
        context_binding_authority_refusal(legacy_authority)
        == "context_binding_schema_version:17"
    )


def test_compiles_synthetic_opaque_local_context_binding_without_special_terms(
) -> None:
    source = _source_with_opaque_local_context_binding()
    result = compile_workflow(source, selected_runner_policy=_OPAQUE_LOCAL_POLICY)

    assert result.plan is not None
    authority = compiled_plan_export_bytes(result.plan).lower()
    assert b"lad" not in authority
    assert b"codex" not in authority
    assert all(
        runner.adapter_kind == "opaque_local"
        for runner in result.plan.runner_bindings
    )


def test_context_declarations_are_public_contracts() -> None:
    import millrace.contracts.compiled_plan as compiled_plan
    from millrace.contracts import (
        ContextSourceDeclaration,
        ContextWriteRule,
        StageContextBindingDeclaration,
    )

    assert ContextSourceDeclaration is compiled_plan.ContextSourceDeclaration
    assert ContextWriteRule is compiled_plan.ContextWriteRule
    assert (
        StageContextBindingDeclaration
        is compiled_plan.StageContextBindingDeclaration
    )


def test_refuses_non_nfc_context_binding_id() -> None:
    source = _source_with_context_binding()
    _context_binding(source)["id"] = "cafe\u0301"

    result = compile_workflow(source)

    errors = [
        diagnostic
        for diagnostic in result.diagnostics
        if diagnostic.severity == "error"
    ]
    assert result.plan is None
    assert [
        (diagnostic.code, diagnostic.declaration_path) for diagnostic in errors
    ] == [
        ("non_nfc_id", "context_bindings[0].id")
    ]
    assert errors[0].context["namespace"] == "context_binding"


def test_refuses_canonically_equivalent_context_binding_ids() -> None:
    source = _source_with_context_binding()
    first = _context_binding(source)
    first["id"] = "caf\u00e9"
    second = deepcopy(first)
    second["id"] = "cafe\u0301"
    second["stage_kind_id"] = "kernel_ping.worker"
    cast(list[object], source["context_bindings"]).append(second)

    result = compile_workflow(source)

    errors = [
        diagnostic
        for diagnostic in result.diagnostics
        if diagnostic.severity == "error"
    ]
    assert result.plan is None
    assert [diagnostic.code for diagnostic in errors] == [
        "canonically_equivalent_id",
        "non_nfc_id",
    ]
    assert errors[0].declaration_path == "context_bindings[1].id"
    assert errors[0].related_declaration_path == "context_bindings[0].id"
    assert errors[0].context["namespace"] == "context_binding"


@pytest.mark.parametrize(
    ("path_kind", "write_enabled", "expected_code"),
    (
        ("checkout_root", False, "context_binding_checkout_root"),
        ("workspace_relative_root", False, "context_binding_source_path"),
        ("write_root", True, "context_binding_write_root"),
    ),
)
def test_refuses_unpaired_surrogate_context_paths(
    path_kind: str,
    write_enabled: bool,
    expected_code: str,
) -> None:
    source = _source_with_context_binding(write_enabled=write_enabled)
    binding = _context_binding(source)
    if path_kind == "checkout_root":
        binding["checkout_root"] = "checkout/\ud800"
    elif path_kind == "workspace_relative_root":
        binding["required_sources"] = [
            {
                "source_kind": "workspace_relative_root",
                "source_ref": "src/\ud800",
                "max_files": 8,
                "max_bytes": 4096,
            }
        ]
    else:
        cast(list[dict[str, object]], binding["write_rules"])[0][
            "relative_root"
        ] = "src/\ud800"

    result = compile_workflow(source)

    assert result.plan is None
    assert {
        diagnostic.code
        for diagnostic in result.diagnostics
        if diagnostic.severity == "error"
    } == {expected_code}


@pytest.mark.parametrize(
    ("path_kind", "write_enabled", "expected_code"),
    (
        ("checkout_root", False, "context_binding_checkout_root"),
        ("workspace_relative_root", False, "context_binding_source_path"),
        ("write_root", True, "context_binding_write_root"),
    ),
)
def test_refuses_nul_context_paths(
    path_kind: str,
    write_enabled: bool,
    expected_code: str,
) -> None:
    source = _source_with_context_binding(write_enabled=write_enabled)
    binding = _context_binding(source)
    if path_kind == "checkout_root":
        binding["checkout_root"] = "checkout/\x00"
    elif path_kind == "workspace_relative_root":
        binding["required_sources"] = [
            {
                "source_kind": "workspace_relative_root",
                "source_ref": "src/\x00",
                "max_files": 8,
                "max_bytes": 4096,
            }
        ]
    else:
        cast(list[dict[str, object]], binding["write_rules"])[0][
            "relative_root"
        ] = "src/\x00"

    result = compile_workflow(source)

    assert result.plan is None
    assert {
        diagnostic.code
        for diagnostic in result.diagnostics
        if diagnostic.severity == "error"
    } == {expected_code}


@pytest.mark.parametrize(
    ("field_name", "value"),
    (
        ("writeback_terminal_action_id", 7),
        ("writeback_terminal_action_id", 8),
        ("writeback_terminal_action_id", ""),
        ("writeback_terminal_action_id", []),
        ("writeback_terminal_action_id", ArtifactSchemaId("wrong.wrapper")),
        ("writeback_artifact_schema_id", 7),
        ("writeback_artifact_schema_id", ""),
        ("writeback_artifact_schema_id", []),
        ("writeback_artifact_schema_id", ActionId("wrong.wrapper")),
    ),
)
def test_refuses_non_null_read_only_writeback_linkage(
    field_name: str,
    value: object,
) -> None:
    source = _source_with_context_binding()
    _context_binding(source)[field_name] = value

    _refuses(source, "context_binding_read_only_linkage")


@pytest.mark.parametrize(
    ("field_name", "value"),
    (
        ("writeback_terminal_action_id", 7),
        ("writeback_terminal_action_id", ""),
        ("writeback_terminal_action_id", []),
        ("writeback_terminal_action_id", ArtifactSchemaId("wrong.wrapper")),
        ("writeback_artifact_schema_id", 7),
        ("writeback_artifact_schema_id", ""),
        ("writeback_artifact_schema_id", []),
        ("writeback_artifact_schema_id", ActionId("wrong.wrapper")),
    ),
)
def test_refuses_malformed_write_enabled_writeback_linkage(
    field_name: str,
    value: object,
) -> None:
    source = _source_with_context_binding(write_enabled=True)
    _context_binding(source)[field_name] = value

    _refuses(source, "context_binding_writeback_linkage")


def test_refuses_context_binding_with_unsupported_source() -> None:
    source = _source_with_context_binding()
    binding = cast(list[dict[str, object]], source["context_bindings"])[0]
    binding["required_sources"] = [
        {
            "source_kind": "unsupported",
            "source_ref": "current",
            "max_files": 1,
            "max_bytes": 1,
        }
    ]

    result = compile_workflow(source)

    assert result.plan is None
    assert {
        diagnostic.code
        for diagnostic in result.diagnostics
        if diagnostic.severity == "error"
    } == {"context_binding_source_kind"}


@pytest.mark.parametrize(
    ("source_kind", "source_ref"),
    (
        ("accepted_lineage_artifacts", "current_lineage"),
        ("lineage_attempt_history", "current_lineage"),
        ("selected_artifacts", "current"),
        ("selected_attempts", "current"),
        ("dispatch_material", "current_lineage"),
    ),
)
def test_refuses_unsupported_context_source_pairs(
    source_kind: str,
    source_ref: str,
) -> None:
    source = _source_with_context_binding()
    binding = _context_binding(source)
    binding["required_sources"] = [
        {
            "source_kind": source_kind,
            "source_ref": source_ref,
            "max_files": 1,
            "max_bytes": 1,
        }
    ]
    binding["discoverable_sources"] = []

    _refuses(source, "context_binding_source_kind")


def test_refuses_context_binding_roots_overlapping_protected_runtime_root() -> None:
    source = _source_with_context_binding(write_enabled=True)
    binding = _context_binding(source)
    cast(list[dict[str, object]], binding["required_sources"])[0][
        "source_ref"
    ] = ".millrace"

    _refuses(source, "context_binding_source_path")


def test_refuses_context_binding_write_root_overlapping_protected_runtime_root(
) -> None:
    source = _source_with_context_binding(write_enabled=True)
    rule = cast(list[dict[str, object]], _context_binding(source)["write_rules"])[0]
    rule["relative_root"] = ".millrace"

    _refuses(source, "context_binding_write_root")


def test_refuses_context_binding_write_root_overlapping_checkout_root() -> None:
    source = _source_with_context_binding(write_enabled=True)
    _context_binding(source)["checkout_root"] = "src"

    _refuses(source, "context_binding_checkout_source_overlap")


def test_refuses_router_asset_kind_alias_that_build_would_not_select() -> None:
    source = _source_with_context_binding()
    assets = cast(list[dict[str, object]], source["assets"])
    router = next(item for item in assets if item["id"] == "kernel_ping.context_router")
    router["kind"] = "prompt"
    router["asset_kind"] = "template"

    result = compile_workflow(source)

    assert result.plan is None
    assert {
        diagnostic.code
        for diagnostic in result.diagnostics
        if diagnostic.severity == "error"
    } == {"context_binding_router_asset_kind"}


def test_compiles_write_enabled_context_binding() -> None:
    result = compile_workflow(_source_with_context_binding(write_enabled=True))

    assert result.plan is not None
    binding = result.plan.context_bindings[0]
    assert binding.mutation_policy == "reconcile_selected_writes"
    assert binding.writeback_terminal_action_id is not None
    assert binding.writeback_artifact_schema_id is not None


def test_context_binding_closure_accepts_non_codex_runner() -> None:
    source = _source_with_context_binding()
    _non_codex_runner(source)

    result = compile_workflow(source)

    assert result.plan is not None
    assert str(result.plan.context_bindings[0].stage_kind_id) == (
        "kernel_ping.taskmaster"
    )


def test_context_source_empty_policy_changes_fingerprint_and_export() -> None:
    default_plan = compile_workflow(_source_with_context_binding()).plan
    omit_source = _source_with_context_binding()
    _context_binding(omit_source)["required_sources"] = [
        {
            "source_kind": "selected_artifacts",
            "source_ref": "direct_predecessors",
            "empty_policy": "omit_if_absent",
            "max_files": 8,
            "max_bytes": 4096,
        }
    ]
    _context_binding(omit_source)["discoverable_sources"] = []
    omit_plan = compile_workflow(omit_source).plan

    assert default_plan is not None
    assert omit_plan is not None
    assert authority_fingerprint(default_plan) != authority_fingerprint(omit_plan)
    assert b'"empty_policy":"omit_if_absent"' in compiled_plan_export_bytes(
        omit_plan
    )


def test_default_context_source_empty_policy_preserves_legacy_export() -> None:
    plan = compile_workflow(_source_with_context_binding()).plan

    assert plan is not None
    assert b'"empty_policy"' not in compiled_plan_export_bytes(plan)


def test_context_policy_changes_fingerprint_but_map_order_does_not() -> None:
    first_source = _source_with_context_binding()
    assets = cast(list[dict[str, object]], first_source["assets"])
    router = next(item for item in assets if item["id"] == "kernel_ping.context_router")
    router["presentation"] = {"z": "last", "a": "first"}
    second_source = deepcopy(first_source)
    second_assets = cast(list[dict[str, object]], second_source["assets"])
    second_router = next(
        item for item in second_assets if item["id"] == "kernel_ping.context_router"
    )
    second_router["presentation"] = {"a": "first", "z": "last"}
    changed_source = deepcopy(first_source)
    _context_binding(changed_source)["checkout_root"] = "other-checkout"

    first = compile_workflow(first_source).plan
    reordered = compile_workflow(second_source).plan
    changed = compile_workflow(changed_source).plan
    assert first is not None
    assert reordered is not None
    assert changed is not None
    assert authority_fingerprint(first) == authority_fingerprint(reordered)
    assert compiled_plan_export_bytes(first) == compiled_plan_export_bytes(reordered)
    assert authority_fingerprint(first) != authority_fingerprint(changed)


def test_context_binding_codec_round_trip_preserves_authority() -> None:
    from millrace.substrate.codecs import (
        decode_selected_compiled_plan,
        encode_selected_compiled_plan,
    )

    plan = compile_workflow(_source_with_context_binding()).plan
    assert plan is not None

    encoded = encode_selected_compiled_plan(plan)
    encoded_binding = cast(
        tuple[dict[str, object], ...],
        encoded.payload["context_bindings"],
    )[0]
    assert {
        field: encoded_binding[field]
        for field in (
            "max_hydrated_files",
            "max_hydrated_bytes",
            "mutation_policy",
            "materialization_retention",
        )
    } == {
        "max_hydrated_files": 16,
        "max_hydrated_bytes": 16_384,
        "mutation_policy": "forbid_selected_roots",
        "materialization_retention": "until_session_durable_terminal",
    }
    encoded_source = cast(
        tuple[dict[str, object], ...],
        encoded_binding["required_sources"],
    )[0]
    assert encoded_source["empty_policy"] == "require_nonempty"

    decoded = decode_selected_compiled_plan(encoded)

    assert decoded == plan
    assert authority_fingerprint(decoded) == authority_fingerprint(plan)


def test_context_binding_codec_defaults_legacy_empty_policy() -> None:
    from millrace.substrate.codecs import (
        decode_selected_compiled_plan,
        encode_selected_compiled_plan,
    )

    plan = compile_workflow(_source_with_context_binding()).plan
    assert plan is not None

    encoded = encode_selected_compiled_plan(plan)
    payload = dict(encoded.payload)
    bindings = [
        dict(binding)
        for binding in cast(tuple[dict[str, object], ...], payload["context_bindings"])
    ]
    required_sources = [
        dict(source)
        for source in cast(
            tuple[dict[str, object], ...],
            bindings[0]["required_sources"],
        )
    ]
    required_sources[0].pop("empty_policy")
    bindings[0]["required_sources"] = tuple(required_sources)
    payload["context_bindings"] = tuple(bindings)

    decoded = decode_selected_compiled_plan(replace(encoded, payload=payload))

    assert decoded == plan
    assert decoded.context_bindings[0].required_sources[0].empty_policy == (
        "require_nonempty"
    )


def _duplicate_stage_binding(source: dict[str, object]) -> None:
    binding = deepcopy(_context_binding(source))
    binding["id"] = "kernel_ping.other_context"
    cast(list[object], source["context_bindings"]).append(binding)


def _missing_stage(source: dict[str, object]) -> None:
    _context_binding(source)["stage_kind_id"] = "missing.stage"


def _missing_router(source: dict[str, object]) -> None:
    _context_binding(source)["router_asset_id"] = "missing.asset"


def _wrong_router_kind(source: dict[str, object]) -> None:
    assets = cast(list[dict[str, object]], source["assets"])
    router = next(item for item in assets if item["id"] == "kernel_ping.context_router")
    router["kind"] = "prompt"


def _unsupported_source(source: dict[str, object]) -> None:
    required = cast(
        list[dict[str, object]],
        _context_binding(source)["required_sources"],
    )
    required[0]["source_kind"] = "unsupported"


def _unsafe_checkout_path(source: dict[str, object]) -> None:
    _context_binding(source)["checkout_root"] = ".millrace"


def _unsafe_workspace_path(source: dict[str, object]) -> None:
    required = cast(
        list[dict[str, object]],
        _context_binding(source)["required_sources"],
    )
    required.append(
        {
            "source_kind": "workspace_relative_root",
            "source_ref": "docs/../src",
            "max_files": 1,
            "max_bytes": 1,
        }
    )


def _non_codex_runner(source: dict[str, object]) -> None:
    binding = _context_binding(source)
    runner_id = (
        "kernel_ping.taskmaster_runner"
        if binding["stage_kind_id"] == "kernel_ping.taskmaster"
        else "kernel_ping.worker_runner"
    )
    runners = cast(list[dict[str, object]], source["runner_bindings"])
    runner = next(item for item in runners if item["id"] == runner_id)
    runner["adapter_kind"] = "millforge"


def _checkout_source_overlap(source: dict[str, object]) -> None:
    _context_binding(source)["checkout_root"] = "docs"


def _duplicate_workspace_source(source: dict[str, object]) -> None:
    binding = _context_binding(source)
    discoverable = cast(list[dict[str, object]], binding["discoverable_sources"])
    discoverable.append(deepcopy(discoverable[0]))


def _overlapping_workspace_source(source: dict[str, object]) -> None:
    binding = _context_binding(source)
    required = cast(list[dict[str, object]], binding["required_sources"])
    required[:] = [
        {
            "source_kind": "workspace_relative_root",
            "source_ref": "docs",
            "max_files": 1,
            "max_bytes": 1,
        }
    ]
    discoverable = cast(list[dict[str, object]], binding["discoverable_sources"])
    discoverable[0]["source_ref"] = "docs/subdir"


def _bad_write_disposition(source: dict[str, object]) -> None:
    binding = _context_binding(source)
    rules = cast(list[dict[str, object]], binding["write_rules"])
    rules[0]["disposition"] = "overwrite"


def _write_root_outside_snapshot(source: dict[str, object]) -> None:
    binding = _context_binding(source)
    rules = cast(list[dict[str, object]], binding["write_rules"])
    rules[0]["relative_root"] = "docs"


def _overlapping_write_rule(source: dict[str, object]) -> None:
    binding = _context_binding(source)
    rules = cast(list[dict[str, object]], binding["write_rules"])
    rules.append({"relative_root": "src/subdir", "disposition": "direct_write"})


def _duplicate_write_rule(source: dict[str, object]) -> None:
    binding = _context_binding(source)
    rules = cast(list[dict[str, object]], binding["write_rules"])
    rules.append({"relative_root": "src", "disposition": "direct_write"})


def _one_sided_linkage(source: dict[str, object]) -> None:
    _context_binding(source)["writeback_terminal_action_id"] = None


def _missing_writeback_linkage(source: dict[str, object]) -> None:
    binding = _context_binding(source)
    binding["writeback_terminal_action_id"] = None
    binding["writeback_artifact_schema_id"] = None


def _read_only_linkage(source: dict[str, object]) -> None:
    _context_binding(source)["writeback_terminal_action_id"] = (
        "kernel_ping.close_worker_success"
    )


def _action_wrong_stage(source: dict[str, object]) -> None:
    _context_binding(source)["writeback_terminal_action_id"] = (
        "kernel_ping.pause_taskmaster_blocked"
    )


def _schema_action_mismatch(source: dict[str, object]) -> None:
    _context_binding(source)["writeback_artifact_schema_id"] = (
        "kernel_ping.task_artifact"
    )


def _non_unique_terminal_result_path(source: dict[str, object]) -> None:
    runners = cast(list[dict[str, object]], source["runner_bindings"])
    runner = next(item for item in runners if item["id"] == "kernel_ping.worker_runner")
    pin = cast(dict[str, object], runner["component_pin"])
    pin["legal_terminal_result_ids"] = (
        *cast(tuple[str, ...], pin["legal_terminal_result_ids"]),
        "WORK_COMPLETE_ALT",
    )
    mappings = list(
        cast(tuple[dict[str, object], ...], runner["terminal_result_mappings"])
    )
    mapping = deepcopy(
        next(item for item in mappings if item["runner_result_id"] == "WORK_COMPLETE")
    )
    mapping["runner_result_id"] = "WORK_COMPLETE_ALT"
    mappings.append(mapping)
    runner["terminal_result_mappings"] = tuple(mappings)


def _non_exact_writeback_schema(source: dict[str, object]) -> None:
    schemas = cast(list[dict[str, object]], source["artifact_schemas"])
    schema = next(
        item for item in schemas if item["id"] == "kernel_ping.context_writeback"
    )
    properties = cast(
        dict[str, object],
        cast(dict[str, object], schema["schema"])["properties"],
    )
    properties["extra"] = {"type": "string"}


@pytest.mark.parametrize(
    ("case_id", "mutator", "write_enabled", "expected_code"),
    (
        pytest.param(
            "duplicate_stage",
            _duplicate_stage_binding,
            False,
            "context_binding_duplicate_stage",
            id="duplicate-stage",
        ),
        pytest.param(
            "missing_stage",
            _missing_stage,
            False,
            "context_binding_stage",
            id="missing-stage",
        ),
        pytest.param(
            "missing_router",
            _missing_router,
            False,
            "context_binding_router_asset",
            id="missing-router",
        ),
        pytest.param(
            "wrong_router_kind",
            _wrong_router_kind,
            False,
            "context_binding_router_asset_kind",
            id="wrong-router-kind",
        ),
        pytest.param(
            "unsupported_source",
            _unsupported_source,
            False,
            "context_binding_source_kind",
            id="unsupported-source",
        ),
        pytest.param(
            "max_files_zero",
            lambda source: _context_binding(source)["required_sources"][0].update(
                {"max_files": 0}
            ),
            False,
            "context_binding_source_bounds",
            id="max-files-zero",
        ),
        pytest.param(
            "max_bytes_bool",
            lambda source: _context_binding(source)["required_sources"][0].update(
                {"max_bytes": True}
            ),
            False,
            "context_binding_source_bounds",
            id="max-bytes-bool",
        ),
        pytest.param(
            "unsafe_checkout",
            _unsafe_checkout_path,
            False,
            "context_binding_checkout_root",
            id="unsafe-checkout",
        ),
        pytest.param(
            "unsafe_workspace",
            _unsafe_workspace_path,
            False,
            "context_binding_source_path",
            id="unsafe-workspace",
        ),
        pytest.param(
            "checkout_overlap",
            _checkout_source_overlap,
            False,
            "context_binding_checkout_source_overlap",
            id="checkout-source-overlap",
        ),
        pytest.param(
            "duplicate_workspace_source",
            _duplicate_workspace_source,
            False,
            "context_binding_source_overlap",
            id="duplicate-workspace-source",
        ),
        pytest.param(
            "overlapping_workspace_source",
            _overlapping_workspace_source,
            False,
            "context_binding_source_overlap",
            id="overlapping-workspace-source",
        ),
        pytest.param(
            "bad_write_disposition",
            _bad_write_disposition,
            True,
            "context_binding_write_disposition",
            id="bad-write-disposition",
        ),
        pytest.param(
            "write_root_outside_snapshot",
            _write_root_outside_snapshot,
            True,
            "context_binding_write_snapshot",
            id="write-root-outside-snapshot",
        ),
        pytest.param(
            "overlapping_write_rule",
            _overlapping_write_rule,
            True,
            "context_binding_write_overlap",
            id="overlapping-write-rule",
        ),
        pytest.param(
            "duplicate_write_rule",
            _duplicate_write_rule,
            True,
            "context_binding_write_overlap",
            id="duplicate-write-rule",
        ),
        pytest.param(
            "one_sided_linkage",
            _one_sided_linkage,
            True,
            "context_binding_writeback_linkage",
            id="one-sided-linkage",
        ),
        pytest.param(
            "missing_writeback_linkage",
            _missing_writeback_linkage,
            True,
            "context_binding_writeback_linkage",
            id="missing-writeback-linkage",
        ),
        pytest.param(
            "read_only_linkage",
            _read_only_linkage,
            False,
            "context_binding_read_only_linkage",
            id="read-only-linkage",
        ),
        pytest.param(
            "action_wrong_stage",
            _action_wrong_stage,
            True,
            "context_binding_writeback_action_stage",
            id="action-wrong-stage",
        ),
        pytest.param(
            "schema_action_mismatch",
            _schema_action_mismatch,
            True,
            "context_binding_writeback_schema_mismatch",
            id="schema-action-mismatch",
        ),
        pytest.param(
            "non_unique_terminal_result_path",
            _non_unique_terminal_result_path,
            True,
            "context_binding_terminal_result_path_count",
            id="non-unique-terminal-result-path",
        ),
        pytest.param(
            "non_exact_writeback_schema",
            _non_exact_writeback_schema,
            True,
            "context_binding_writeback_schema_shape",
            id="non-exact-writeback-schema",
        ),
    ),
)
def test_refuses_malformed_context_binding(
    case_id: str,
    mutator: Callable[[dict[str, object]], None],
    write_enabled: bool,
    expected_code: str,
) -> None:
    source = _source_with_context_binding(write_enabled=write_enabled)
    mutator(source)

    _refuses(source, expected_code, case_id)
