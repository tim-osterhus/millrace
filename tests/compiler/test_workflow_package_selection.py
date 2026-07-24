from __future__ import annotations

import json
from copy import deepcopy
from hashlib import sha256
from typing import cast

import pytest

from millrace.compiler.canonical import authority_fingerprint
from millrace.compiler.compile import CompileResult
from millrace.compiler.package_selection import (
    PackageRegistryView,
    PackageWorkflowSelector,
    compile_workflow_package_selection,
)
from millrace.compiler.runner_bindings import (
    RUNNER_ADAPTER_KIND_DEFAULTED,
    SelectedRunnerAdapterPolicy,
)
from millrace.contracts import Diagnostic
from millrace.contracts.compiled_plan import SelectedCompiledPlan
from millrace.contracts.workflow_package import (
    WorkflowPackageManifestCanonicalizationError,
    asset_digest_for_bytes,
    manifest_digest_for_manifest,
)
from millrace.workflows import simple_loop

Record = dict[str, object]
Source = dict[str, object]

ASSET_BYTES = b"Selected package prompt\n"
ASSET_TEXT = ASSET_BYTES.decode("utf-8")
PACKAGE_ID = "pkg.example.selection"
PACKAGE_VERSION = "1.0.0"
PACKAGE_FORMAT_VERSION = "1"
PACKAGE_DIGEST = "sha256:" + ("1" * 64)
MANIFEST_CAS_DIGEST = "cas:manifest"
ASSET_CAS_DIGEST = "cas:asset.prompt"
SIMPLE_LOOP_PACKAGE_ID = "pkg.example.simple_loop"
SIMPLE_LOOP_PACKAGE_VERSION = "0.1.0"
SIMPLE_LOOP_PACKAGE_DIGEST = "sha256:" + ("2" * 64)
SIMPLE_LOOP_MANIFEST_CAS_DIGEST = "cas:simple-loop-manifest"
SIMPLE_LOOP_TROUBLESHOOTER_PROMPT_ID = "simple_loop.troubleshooter_prompt"
_CODEX_POLICY = SelectedRunnerAdapterPolicy(
    default_adapter_kind="codex",
    supported_adapter_kinds=frozenset({"codex"}),
    component_bound_adapter_kinds=frozenset(),
    default_component_selector=None,
    default_component_required_capability_ids=frozenset(),
    default_component_requires_complete_mappings=False,
)


def _sha256_marker(seed: str) -> str:
    return "sha256:" + sha256(seed.encode("utf-8")).hexdigest()


def _workflow_source(*, asset_body: str = ASSET_TEXT) -> Source:
    return {
        "lineage_policy": "root_from_external_enqueue",
        "workflow": {
            "id": "wf.package",
            "version": "1",
            "name": "Package Workflow",
            "compatibility_profile": None,
            "required_extensions": [],
        },
        "graphs": [{"id": "graph.package", "node_ids": ["node.start"]}],
        "partitions": [{"id": "partition.package", "kind": "workflow"}],
        "queue_families": [{"id": "input", "external_enqueue": True}],
        "external_enqueue_routes": [
            {
                "id": "route.input",
                "queue_family_id": "input",
                "graph_node_id": "node.start",
                "stage_kind_id": "stage.package",
                "runner_binding_id": "runner.package",
            }
        ],
        "artifact_schemas": [],
        "assets": [
            {
                "id": "asset.prompt",
                "kind": "entrypoint_prompt",
                "body": asset_body,
            }
        ],
        "stage_kinds": [
            {
                "id": "stage.package",
                "partition_id": "partition.package",
                "runner_binding_id": "runner.package",
                "input_queue_family_ids": ["input"],
                "output_queue_family_ids": [],
                "artifact_schema_ids": [],
                "asset_ids": ["asset.prompt"],
                "declared_outcome_ids": ["outcome.done"],
            }
        ],
        "terminal_outcomes": [
            {
                "id": "outcome.done",
                "stage_kind_id": "stage.package",
                "marker": "DONE",
            }
        ],
        "terminal_actions": [
            {
                "id": "action.close",
                "stage_kind_id": "stage.package",
                "outcome_id": "outcome.done",
                "kind": "close",
            }
        ],
        "runner_bindings": [
            {
                "id": "runner.package",
                "adapter_kind": "fake_local",
                "stage_kind_ids": ["stage.package"],
            }
        ],
    }


def _selected_authority_source(*, asset_body: str = ASSET_TEXT) -> Source:
    source = _workflow_source(asset_body=asset_body)
    source.pop("assets")
    return source


def _manifest(
    *,
    package_id: str = PACKAGE_ID,
    package_version: str = PACKAGE_VERSION,
    package_format_version: str = PACKAGE_FORMAT_VERSION,
    workflow_id: str = "wf.package",
    workflow_version: str = "1",
    entrypoints: list[str] | None = None,
    selected_authority: Source | None = None,
    asset_bytes: bytes = ASSET_BYTES,
    asset_id: str = "asset.prompt",
    asset_encoding: str = "utf-8",
    asset_content_digest: str | None = None,
    asset_byte_length: int | None = None,
    required_asset_digest: str | None = None,
    dependencies: list[Record] | None = None,
    required_dependencies: list[str] | None = None,
) -> Record:
    content_digest = asset_content_digest or asset_digest_for_bytes(asset_bytes)
    required_digest = required_asset_digest or content_digest
    manifest: Record = {
        "record_kind": "millrace.workflow_package_manifest",
        "manifest_format_version": "1",
        "package": {
            "package_id": package_id,
            "package_version": package_version,
            "package_format_version": package_format_version,
            "package_role": "workflow_package",
            "publisher": "Example",
            "base_millrace_compatibility": ">=0.22,<0.23",
            "source_kind": "archive",
            "publication_scope": "test",
        },
        "workflows": [
            {
                "workflow_id": workflow_id,
                "workflow_version": workflow_version,
                "visibility": "test_only",
                "entrypoints": ["default"] if entrypoints is None else entrypoints,
                "selected_authority": (
                    _selected_authority_source()
                    if selected_authority is None
                    else selected_authority
                ),
                "required_assets": [
                    {"asset_id": asset_id, "content_digest": required_digest}
                ],
                "required_dependencies": []
                if required_dependencies is None
                else required_dependencies,
            }
        ],
        "assets": [
            {
                "asset_id": asset_id,
                "asset_kind": "entrypoint_prompt",
                "media_type": "text/markdown; charset=utf-8",
                "encoding": asset_encoding,
                "content_digest": content_digest,
                "byte_length": len(asset_bytes)
                if asset_byte_length is None
                else asset_byte_length,
                "package_path": "prompts/package.md",
                "selection": "required",
                "selected_authority_participation": "yes",
            }
        ],
        "dependencies": [] if dependencies is None else dependencies,
        "compatibility": {"base_millrace": ">=0.22,<0.23"},
        "canonicalization": {"algorithm": "millrace-json-v1", "hash": "sha256"},
        "manifest_digest": None,
        "non_authoritative_metadata": {
            "source_kind": "archive",
            "status": "non-authoritative",
        },
    }
    manifest["manifest_digest"] = manifest_digest_for_manifest(manifest)
    return manifest


def _manifest_bytes(manifest: Record) -> bytes:
    return json.dumps(manifest, ensure_ascii=False, sort_keys=True).encode("utf-8")


def _registry_record(
    manifest: Record,
    *,
    package_id: str = PACKAGE_ID,
    package_version: str = PACKAGE_VERSION,
    package_format_version: str = PACKAGE_FORMAT_VERSION,
    package_digest: str = PACKAGE_DIGEST,
    manifest_digest: str | None = None,
    manifest_cas_digest: str = MANIFEST_CAS_DIGEST,
    status: str = "enabled",
    is_current: bool = True,
    asset_cas_digest: str = ASSET_CAS_DIGEST,
    asset_content_digest: str | None = None,
    asset_byte_length: int | None = None,
    dependencies: list[Record] | None = None,
) -> Record:
    manifest_assets = cast(list[object], manifest["assets"])
    asset = cast(Record, manifest_assets[0]) if manifest_assets else None
    return {
        "package_id": package_id,
        "package_version": package_version,
        "package_format_version": package_format_version,
        "manifest_digest": manifest_digest or cast(str, manifest["manifest_digest"]),
        "package_digest": package_digest,
        "manifest_cas_digest": manifest_cas_digest,
        "status": status,
        "is_current": is_current,
        "assets": []
        if asset is None
        else [
            {
                "asset_id": asset["asset_id"],
                "content_digest": asset_content_digest or asset["content_digest"],
                "byte_length": (
                    asset["byte_length"]
                    if asset_byte_length is None
                    else asset_byte_length
                ),
                "cas_digest": asset_cas_digest,
                "selected_authority_participation": (
                    asset["selected_authority_participation"]
                ),
            }
        ],
        "dependencies": (
            cast(list[Record], manifest["dependencies"])
            if dependencies is None
            else dependencies
        ),
    }


def _registry_and_cas(
    *,
    manifest: Record | None = None,
    records: list[Record] | None = None,
    asset_bytes: bytes = ASSET_BYTES,
    cas: dict[str, bytes] | None = None,
) -> tuple[PackageRegistryView, dict[str, bytes]]:
    manifest = _manifest() if manifest is None else manifest
    registry_records = [_registry_record(manifest)] if records is None else records
    cas_objects = {
        MANIFEST_CAS_DIGEST: _manifest_bytes(manifest),
        ASSET_CAS_DIGEST: asset_bytes,
    }
    if cas is not None:
        cas_objects = cas
    return PackageRegistryView(records=tuple(registry_records)), cas_objects


def _selector(
    *,
    package_id: str = PACKAGE_ID,
    package_version: str = PACKAGE_VERSION,
    workflow_id: str = "wf.package",
    workflow_version: str = "1",
    entrypoint: str = "default",
    expected_manifest_digest: str | None = None,
    expected_package_digest: str | None = None,
    selected_runner_policy: SelectedRunnerAdapterPolicy = (_CODEX_POLICY),
) -> PackageWorkflowSelector:
    return PackageWorkflowSelector(
        package_id=package_id,
        package_version=package_version,
        workflow_id=workflow_id,
        workflow_version=workflow_version,
        entrypoint=entrypoint,
        expected_manifest_digest=expected_manifest_digest,
        expected_package_digest=expected_package_digest,
        selected_runner_policy=selected_runner_policy,
    )


def _read_cas(cas: dict[str, bytes]):
    def read(cas_digest: str) -> bytes:
        return cas[cas_digest]

    return read


def _compile_from_package(
    *,
    selector: PackageWorkflowSelector | None = None,
    registry: PackageRegistryView | None = None,
    cas: dict[str, bytes] | None = None,
) -> SelectedCompiledPlan:
    if registry is None or cas is None:
        registry, cas = _registry_and_cas()
    result = compile_workflow_package_selection(
        selector or _selector(),
        registry,
        _read_cas(cas),
    )
    errors = [
        diagnostic
        for diagnostic in result.diagnostics
        if diagnostic.severity == "error"
    ]
    assert errors == []
    assert result.plan is not None
    return result.plan


def _error_codes(result) -> set[str]:
    return {
        diagnostic.code
        for diagnostic in result.diagnostics
        if diagnostic.severity == "error"
    }


def _compile_result(
    selector: PackageWorkflowSelector | None = None,
    registry: PackageRegistryView | None = None,
    cas: dict[str, bytes] | None = None,
):
    if registry is None or cas is None:
        registry, cas = _registry_and_cas()
    return compile_workflow_package_selection(
        selector or _selector(),
        registry,
        _read_cas(cas),
    )


def test_package_selection_defaults_runner_with_package_context() -> None:
    result = _compile_result()

    assert result.plan is not None
    warnings = [
        diagnostic
        for diagnostic in result.diagnostics
        if diagnostic.code == RUNNER_ADAPTER_KIND_DEFAULTED
    ]
    assert len(warnings) == 1
    warning = warnings[0]
    assert warning.severity == "warning"
    assert warning.declaration_path == (
        "workflows[0].selected_authority.runner_bindings[0].adapter_kind"
    )
    assert warning.context["runner_binding_id"] == "runner.package"
    assert warning.context["original_adapter_kind"] == "fake_local"
    assert warning.context["default_adapter_kind"] == "codex"
    assert warning.context["workflow_id"] == "wf.package"
    assert warning.context["workflow_version"] == "1"
    assert warning.context["source_kind"] == "workflow_package_selection"
    assert warning.context["package_id"] == PACKAGE_ID
    assert warning.context["package_version"] == PACKAGE_VERSION
    assert warning.context["entrypoint"] == "default"
    assert warning.context["package_manifest_digest"] == cast(
        str,
        _manifest()["manifest_digest"],
    )
    assert warning.context["package_digest"] == PACKAGE_DIGEST
    assert {runner.adapter_kind for runner in result.plan.runner_bindings} == {"codex"}


@pytest.mark.parametrize(
    ("authored_value", "expected_code"),
    (
        (True, "unsupported_authority_value"),
        ("3600", "unsupported_authority_value"),
        (None, "unsupported_authority_value"),
        (0, "invalid_runner_invocation_timeout_seconds"),
        (-1, "invalid_runner_invocation_timeout_seconds"),
    ),
)
def test_package_selection_timeout_diagnostic_preserves_exact_manifest_path(
    authored_value: object,
    expected_code: str,
) -> None:
    selected_authority = _selected_authority_source()
    runner = cast(list[Record], selected_authority["runner_bindings"])[0]
    runner["invocation_timeout_seconds"] = authored_value
    manifest = _manifest(selected_authority=selected_authority)
    registry, cas = _registry_and_cas(manifest=manifest)

    result = _compile_result(registry=registry, cas=cas)

    diagnostic = next(item for item in result.diagnostics if item.code == expected_code)
    assert result.plan is None
    assert diagnostic.declaration_path == (
        "workflows[0].selected_authority.runner_bindings[0].invocation_timeout_seconds"
    )
    if expected_code == "invalid_runner_invocation_timeout_seconds":
        assert diagnostic.context["package_id"] == PACKAGE_ID


@pytest.mark.parametrize(
    "authored_value",
    (3600.0, float("nan"), float("inf")),
)
def test_package_manifest_refuses_noncanonical_float_timeout_before_selection(
    authored_value: float,
) -> None:
    selected_authority = _selected_authority_source()
    runner = cast(list[Record], selected_authority["runner_bindings"])[0]
    runner["invocation_timeout_seconds"] = authored_value

    with pytest.raises(WorkflowPackageManifestCanonicalizationError):
        _manifest(selected_authority=selected_authority)


def test_package_selection_accepts_custom_selected_runner_policy() -> None:
    result = _compile_result(
        selector=_selector(
            selected_runner_policy=SelectedRunnerAdapterPolicy(
                default_adapter_kind="codex.test",
                supported_adapter_kinds=frozenset({"codex.test"}),
                component_bound_adapter_kinds=frozenset(),
                default_component_selector=None,
                default_component_required_capability_ids=frozenset(),
                default_component_requires_complete_mappings=False,
            ),
        ),
    )

    assert result.plan is not None
    warnings = [
        diagnostic
        for diagnostic in result.diagnostics
        if diagnostic.code == RUNNER_ADAPTER_KIND_DEFAULTED
    ]
    assert len(warnings) == 1
    assert warnings[0].context["default_adapter_kind"] == "codex.test"
    assert {runner.adapter_kind for runner in result.plan.runner_bindings} == {
        "codex.test"
    }


def _simple_loop_selector() -> PackageWorkflowSelector:
    return _selector(
        package_id=SIMPLE_LOOP_PACKAGE_ID,
        package_version=SIMPLE_LOOP_PACKAGE_VERSION,
        workflow_id="simple_loop",
        workflow_version="0.1",
    )


def _simple_loop_package_fixture(
    *,
    troubleshooter_asset_kind: str = "entrypoint_prompt",
) -> tuple[Record, PackageRegistryView, dict[str, bytes]]:
    selected_authority = simple_loop.workflow_source()
    source_assets = cast(list[Record], selected_authority.pop("assets"))
    required_assets: list[Record] = []
    manifest_assets: list[Record] = []
    registry_assets: list[Record] = []
    cas: dict[str, bytes] = {}
    for index, asset in enumerate(source_assets):
        asset_id = str(asset["id"])
        body = str(asset["body"])
        asset_bytes = body.encode("utf-8")
        content_digest = asset_digest_for_bytes(asset_bytes)
        asset_kind = (
            troubleshooter_asset_kind
            if asset_id == SIMPLE_LOOP_TROUBLESHOOTER_PROMPT_ID
            else "entrypoint_prompt"
        )
        asset_cas_digest = f"cas:simple-loop-asset-{index}"
        cas[asset_cas_digest] = asset_bytes
        required_assets.append({"asset_id": asset_id, "content_digest": content_digest})
        manifest_assets.append(
            {
                "asset_id": asset_id,
                "asset_kind": asset_kind,
                "media_type": "text/markdown; charset=utf-8",
                "encoding": "utf-8",
                "content_digest": content_digest,
                "byte_length": len(asset_bytes),
                "package_path": f"prompts/{asset_id.replace('.', '_')}.md",
                "selection": "required",
                "selected_authority_participation": "yes",
            }
        )
        registry_assets.append(
            {
                "asset_id": asset_id,
                "content_digest": content_digest,
                "byte_length": len(asset_bytes),
                "cas_digest": asset_cas_digest,
                "selected_authority_participation": "yes",
            }
        )

    workflow = cast(Record, selected_authority["workflow"])
    manifest: Record = {
        "record_kind": "millrace.workflow_package_manifest",
        "manifest_format_version": "1",
        "package": {
            "package_id": SIMPLE_LOOP_PACKAGE_ID,
            "package_version": SIMPLE_LOOP_PACKAGE_VERSION,
            "package_format_version": PACKAGE_FORMAT_VERSION,
            "package_role": "workflow_package",
            "publisher": "Example",
            "base_millrace_compatibility": ">=0.22,<0.23",
            "source_kind": "archive",
            "publication_scope": "test",
        },
        "workflows": [
            {
                "workflow_id": workflow["id"],
                "workflow_version": workflow["version"],
                "visibility": "test_only",
                "entrypoints": ["default"],
                "selected_authority": selected_authority,
                "required_assets": required_assets,
                "required_dependencies": [],
            }
        ],
        "assets": manifest_assets,
        "dependencies": [],
        "compatibility": {"base_millrace": ">=0.22,<0.23"},
        "canonicalization": {"algorithm": "millrace-json-v1", "hash": "sha256"},
        "manifest_digest": None,
        "non_authoritative_metadata": {
            "source_kind": "archive",
            "status": "non-authoritative",
        },
    }
    manifest["manifest_digest"] = manifest_digest_for_manifest(manifest)
    cas[SIMPLE_LOOP_MANIFEST_CAS_DIGEST] = _manifest_bytes(manifest)
    registry_record: Record = {
        "package_id": SIMPLE_LOOP_PACKAGE_ID,
        "package_version": SIMPLE_LOOP_PACKAGE_VERSION,
        "package_format_version": PACKAGE_FORMAT_VERSION,
        "manifest_digest": manifest["manifest_digest"],
        "package_digest": SIMPLE_LOOP_PACKAGE_DIGEST,
        "manifest_cas_digest": SIMPLE_LOOP_MANIFEST_CAS_DIGEST,
        "status": "enabled",
        "is_current": True,
        "assets": registry_assets,
        "dependencies": [],
    }
    return manifest, PackageRegistryView(records=(registry_record,)), cas


def _recovery_route_asset_kind_diagnostics(
    result: CompileResult,
) -> list[Diagnostic]:
    return [
        diagnostic
        for diagnostic in result.diagnostics
        if diagnostic.severity == "error"
        and diagnostic.code == "terminal_recovery_route_asset_kind_mismatch"
    ]


def test_compile_selects_workflow_by_explicit_package_and_workflow_ref() -> None:
    plan = _compile_from_package()

    assert str(plan.workflow.workflow_id) == "wf.package"
    assert str(plan.workflow.workflow_version) == "1"
    assert len(plan.assets) == 1
    assert plan.assets[0].body == ASSET_TEXT
    assert plan.workflow_package_pin is not None
    assert plan.workflow_package_pin.package_id == PACKAGE_ID
    assert plan.workflow_package_pin.package_version == PACKAGE_VERSION
    assert plan.workflow_package_pin.package_format_version == PACKAGE_FORMAT_VERSION
    assert plan.workflow_package_pin.workflow_id == "wf.package"
    assert plan.workflow_package_pin.workflow_version == "1"
    assert plan.workflow_package_pin.entrypoint == "default"
    assert tuple(plan.workflow_package_pin.selected_asset_pins) == (
        plan.workflow_package_pin.selected_asset_pins[0],
    )
    assert plan.workflow_package_pin.selected_asset_pins[0].asset_id == "asset.prompt"
    assert plan.workflow_package_pin.selected_asset_pins[0].content_digest == (
        asset_digest_for_bytes(ASSET_BYTES)
    )
    assert plan.workflow_package_pin.selected_dependency_pins == ()


def test_compile_refuses_missing_package_or_workflow_ref() -> None:
    missing_package = _compile_result(_selector(package_id="pkg.example.missing"))
    missing_workflow = _compile_result(_selector(workflow_id="wf.missing"))

    assert missing_package.plan is None
    assert missing_workflow.plan is None
    assert "package_selection_package_not_found" in _error_codes(missing_package)
    assert "package_selection_workflow_not_found" in _error_codes(missing_workflow)


def test_compile_refuses_unknown_or_missing_package_entrypoint() -> None:
    unknown = _compile_result(_selector(entrypoint="operator"))
    manifest = _manifest(entrypoints=[])
    registry, cas = _registry_and_cas(manifest=manifest)
    missing = _compile_result(registry=registry, cas=cas)

    assert unknown.plan is None
    assert missing.plan is None
    assert "package_selection_entrypoint_not_found" in _error_codes(unknown)
    assert "package_selection_entrypoint_not_found" in _error_codes(missing)


def test_compile_refuses_disabled_removed_or_mismatched_digest_package() -> None:
    for status in ("disabled", "removed"):
        manifest = _manifest()
        registry, cas = _registry_and_cas(
            manifest=manifest,
            records=[_registry_record(manifest, status=status)],
        )
        result = _compile_result(registry=registry, cas=cas)
        assert result.plan is None
        assert "package_selection_package_status_refused" in _error_codes(result)

    manifest = _manifest()
    registry, cas = _registry_and_cas(manifest=manifest)
    manifest_mismatch = _compile_result(
        _selector(expected_manifest_digest=_sha256_marker("wrong-manifest")),
        registry,
        cas,
    )
    package_mismatch = _compile_result(
        _selector(expected_package_digest=_sha256_marker("wrong-package")),
        registry,
        cas,
    )

    assert manifest_mismatch.plan is None
    assert package_mismatch.plan is None
    assert "package_selection_expected_manifest_digest_mismatch" in _error_codes(
        manifest_mismatch
    )
    assert "package_selection_expected_package_digest_mismatch" in _error_codes(
        package_mismatch
    )


def test_compile_refuses_imported_not_enabled_package() -> None:
    manifest = _manifest()
    registry, cas = _registry_and_cas(
        manifest=manifest,
        records=[_registry_record(manifest, status="imported")],
    )

    result = _compile_result(registry=registry, cas=cas)

    assert result.plan is None
    assert "package_selection_package_status_refused" in _error_codes(result)


def test_compile_refuses_unknown_package_status() -> None:
    manifest = _manifest()
    registry, cas = _registry_and_cas(
        manifest=manifest,
        records=[_registry_record(manifest, status="quarantined")],
    )

    result = _compile_result(registry=registry, cas=cas)

    assert result.plan is None
    assert "package_selection_unknown_package_status" in _error_codes(result)


def test_compile_refuses_zero_current_root_package_identity() -> None:
    manifest = _manifest()
    registry, cas = _registry_and_cas(
        manifest=manifest,
        records=[_registry_record(manifest, is_current=False)],
    )

    result = _compile_result(registry=registry, cas=cas)

    assert result.plan is None
    assert "package_selection_zero_current_package" in _error_codes(result)


def test_compile_refuses_duplicate_current_root_package_identity() -> None:
    manifest = _manifest()
    records = [
        _registry_record(manifest, manifest_cas_digest=MANIFEST_CAS_DIGEST),
        _registry_record(manifest, manifest_cas_digest=MANIFEST_CAS_DIGEST),
    ]
    registry, cas = _registry_and_cas(manifest=manifest, records=records)

    result = _compile_result(registry=registry, cas=cas)

    assert result.plan is None
    assert "package_selection_duplicate_current_package" in _error_codes(result)


def test_compile_refuses_package_workflow_with_missing_selected_asset() -> None:
    manifest = _manifest(asset_id="asset.missing")
    record = _registry_record(manifest)
    cast(list[Record], record["assets"])[0]["asset_id"] = "asset.other"
    registry, cas = _registry_and_cas(manifest=manifest, records=[record])

    result = _compile_result(registry=registry, cas=cas)

    assert result.plan is None
    assert "package_selection_asset_not_found" in _error_codes(result)


def test_compile_refuses_missing_manifest_cas_bytes() -> None:
    manifest = _manifest()
    registry, cas = _registry_and_cas(manifest=manifest, cas={})

    result = _compile_result(registry=registry, cas=cas)

    assert result.plan is None
    assert "package_selection_manifest_cas_unreadable" in _error_codes(result)


def test_compile_refuses_wrong_manifest_cas_bytes() -> None:
    manifest = _manifest()
    registry, cas = _registry_and_cas(
        manifest=manifest,
        cas={
            MANIFEST_CAS_DIGEST: b'{"not":"the-manifest"}',
            ASSET_CAS_DIGEST: ASSET_BYTES,
        },
    )

    result = _compile_result(registry=registry, cas=cas)

    assert result.plan is None
    assert "package_selection_manifest_digest_mismatch" in _error_codes(result)


def test_compile_refuses_manifest_package_identity_mismatch() -> None:
    manifest = _manifest(package_id="pkg.example.manifest-other")
    record = _registry_record(manifest, package_id=PACKAGE_ID)
    registry, cas = _registry_and_cas(manifest=manifest, records=[record])

    result = _compile_result(registry=registry, cas=cas)

    assert result.plan is None
    assert _error_codes(result) == {"package_selection_manifest_package_mismatch"}


def test_compile_refuses_selected_workflow_source_identity_mismatch() -> None:
    selected_authority = _selected_authority_source()
    workflow = cast(Record, selected_authority["workflow"])
    workflow["id"] = "wf.selected-other"
    manifest = _manifest(selected_authority=selected_authority)
    registry, cas = _registry_and_cas(manifest=manifest)

    result = _compile_result(registry=registry, cas=cas)

    assert result.plan is None
    assert _error_codes(result) == {"package_selection_workflow_source_mismatch"}


def test_compile_refuses_noncanonical_manifest_cas_bytes_as_diagnostic() -> None:
    manifest = _manifest()
    registry, cas = _registry_and_cas(
        manifest=manifest,
        cas={
            MANIFEST_CAS_DIGEST: b'{"record_kind":1.25}',
            ASSET_CAS_DIGEST: ASSET_BYTES,
        },
    )

    result = _compile_result(registry=registry, cas=cas)

    assert result.plan is None
    assert "package_selection_manifest_digest_mismatch" in _error_codes(result)


def test_compile_refuses_missing_selected_asset_cas_bytes() -> None:
    manifest = _manifest()
    registry, cas = _registry_and_cas(manifest=manifest)
    cas.pop(ASSET_CAS_DIGEST)

    result = _compile_result(registry=registry, cas=cas)

    assert result.plan is None
    assert "package_selection_asset_cas_unreadable" in _error_codes(result)


def test_compile_refuses_wrong_selected_asset_cas_bytes() -> None:
    manifest = _manifest()
    registry, cas = _registry_and_cas(manifest=manifest)
    cas[ASSET_CAS_DIGEST] = b"wrong asset bytes\n"

    result = _compile_result(registry=registry, cas=cas)

    assert result.plan is None
    assert "package_selection_asset_digest_mismatch" in _error_codes(result)


def test_compile_refuses_binary_selected_asset_for_v0_22_text_lowering() -> None:
    manifest = _manifest(asset_bytes=b"\xff\x00binary", asset_encoding="binary")
    registry, cas = _registry_and_cas(manifest=manifest, asset_bytes=b"\xff\x00binary")

    result = _compile_result(registry=registry, cas=cas)

    assert result.plan is None
    assert "package_selection_binary_asset_unsupported" in _error_codes(result)


def test_compile_refuses_package_hidden_defaults_and_undeclared_authority() -> None:
    selected_authority = _selected_authority_source()
    selected_authority["hidden_defaults"] = ["runtime_default"]
    manifest = _manifest(selected_authority=selected_authority)
    registry, cas = _registry_and_cas(manifest=manifest)

    result = _compile_result(registry=registry, cas=cas)

    assert result.plan is None
    assert "hidden_default_authority" in _error_codes(result)


def test_compile_refuses_package_authored_assets_in_selected_authority() -> None:
    selected_authority = _selected_authority_source()
    selected_authority["assets"] = [
        {
            "id": "asset.intruder",
            "kind": "entrypoint_prompt",
            "body": "This direct asset must not be normalized away.",
        }
    ]
    manifest = _manifest(selected_authority=selected_authority)
    registry, cas = _registry_and_cas(manifest=manifest)

    result = _compile_result(registry=registry, cas=cas)

    assert result.plan is None
    assert "package_selection_selected_authority_assets_refused" in _error_codes(result)


def test_compile_raw_and_package_sources_match_except_selected_package_pins() -> None:
    from dataclasses import replace

    from millrace.compiler import compile_workflow

    raw_result = compile_workflow(
        _workflow_source(), selected_runner_policy=_CODEX_POLICY
    )
    package_plan = _compile_from_package()

    assert raw_result.plan is not None
    assert raw_result.plan.workflow_package_pin is None
    assert replace(package_plan, workflow_package_pin=None) == raw_result.plan
    assert authority_fingerprint(package_plan) != authority_fingerprint(raw_result.plan)


def test_compile_does_not_include_unselected_package_workflows_or_assets() -> None:
    manifest = _manifest()
    workflows = cast(list[object], manifest["workflows"])
    workflows.append(
        {
            "workflow_id": "wf.unselected",
            "workflow_version": "1",
            "visibility": "test_only",
            "entrypoints": ["default"],
            "selected_authority": {
                **_selected_authority_source(),
                "workflow": {
                    "id": "wf.unselected",
                    "version": "1",
                    "name": "Unselected",
                },
            },
            "required_assets": [],
        }
    )
    assets = cast(list[object], manifest["assets"])
    assets.append(
        {
            "asset_id": "asset.unselected",
            "asset_kind": "fixture",
            "media_type": "text/plain; charset=utf-8",
            "encoding": "utf-8",
            "content_digest": asset_digest_for_bytes(b"unselected"),
            "byte_length": len(b"unselected"),
            "package_path": "fixtures/unselected.txt",
            "selection": "optional_example",
            "selected_authority_participation": "no",
        }
    )
    manifest["manifest_digest"] = manifest_digest_for_manifest(manifest)
    registry, cas = _registry_and_cas(manifest=manifest)

    plan = _compile_from_package(registry=registry, cas=cas)

    assert str(plan.workflow.workflow_id) == "wf.package"
    assert {str(asset.id) for asset in plan.assets} == {"asset.prompt"}
    assert plan.workflow_package_pin is not None
    assert {pin.asset_id for pin in plan.workflow_package_pin.selected_asset_pins} == {
        "asset.prompt"
    }


def test_compile_selection_api_does_not_mutate_registry_or_cas() -> None:
    manifest = _manifest()
    registry, cas = _registry_and_cas(manifest=manifest)
    registry_before = deepcopy(registry.records)
    cas_before = deepcopy(cas)

    _compile_from_package(registry=registry, cas=cas)

    assert registry.records == registry_before
    assert cas == cas_before


def test_simple_loop_package_selection_allows_recovery_entrypoint_prompt() -> None:
    manifest, registry, cas = _simple_loop_package_fixture()
    workflow = cast(Record, cast(list[object], manifest["workflows"])[0])
    selected_authority = cast(Record, workflow["selected_authority"])

    result = _compile_result(_simple_loop_selector(), registry, cas)

    assert "assets" not in selected_authority
    assert _recovery_route_asset_kind_diagnostics(result) == []
    assert [
        diagnostic
        for diagnostic in result.diagnostics
        if diagnostic.severity == "error"
    ] == []
    assert result.plan is not None
    assert result.plan.workflow_package_pin is not None
    assert {
        pin.asset_id for pin in result.plan.workflow_package_pin.selected_asset_pins
    } == {
        "simple_loop.manager_prompt",
        "simple_loop.worker_prompt",
        "simple_loop.reviewer_prompt",
        SIMPLE_LOOP_TROUBLESHOOTER_PROMPT_ID,
    }
    assets_by_id = {str(asset.id): asset for asset in result.plan.assets}
    assert (
        assets_by_id[SIMPLE_LOOP_TROUBLESHOOTER_PROMPT_ID].asset_kind
        == "entrypoint_prompt"
    )


@pytest.mark.parametrize(
    "asset_kind",
    (
        "stage_skill",
        "shared_skill",
        "template",
        "schema",
        "example",
        "fixture",
        "blob",
    ),
)
def test_compile_simple_loop_package_selection_refuses_non_prompt_like_recovery_asset(
    asset_kind: str,
) -> None:
    _, registry, cas = _simple_loop_package_fixture(
        troubleshooter_asset_kind=asset_kind,
    )

    result = _compile_result(_simple_loop_selector(), registry, cas)

    assert result.plan is None
    mismatches = _recovery_route_asset_kind_diagnostics(result)
    assert {diagnostic.declaration_path for diagnostic in mismatches} == {
        "terminal_actions[3].asset_ids[0]",
        "terminal_actions[7].asset_ids[0]",
        "terminal_actions[8].asset_ids[0]",
        "terminal_actions[12].asset_ids[0]",
    }
    for diagnostic in mismatches:
        assert diagnostic.context["asset_id"] == SIMPLE_LOOP_TROUBLESHOOTER_PROMPT_ID
        assert diagnostic.context["asset_kind"] == asset_kind
