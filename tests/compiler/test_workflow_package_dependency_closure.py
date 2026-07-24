from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import replace
from typing import cast

import pytest

from millrace.compiler import compiled_plan_export_record
from millrace.compiler.canonical import authority_fingerprint
from millrace.compiler.package_selection import PackageRegistryView
from millrace.contracts.compiled_plan import (
    SelectedWorkflowPackageDependencyPin,
    SelectedWorkflowPackagePin,
)
from millrace.contracts.workflow_package import manifest_digest_for_manifest
from millrace.substrate.codecs import encode_selected_compiled_plan
from tests.compiler.test_workflow_package_selection import (
    ASSET_BYTES,
    MANIFEST_CAS_DIGEST,
    PACKAGE_ID,
    PACKAGE_VERSION,
    _compile_from_package,
    _compile_result,
    _error_codes,
    _manifest,
    _manifest_bytes,
    _registry_and_cas,
    _registry_record,
)

DEP_ID = "pkg.example.dependency"
DEP_VERSION = "2.0.0"
DEP_CAS_DIGEST = "cas:manifest.dependency"


def _dependency_decl(
    *,
    package_id: str = DEP_ID,
    version_constraint: str = f"=={DEP_VERSION}",
    manifest_digest: str | None = None,
) -> dict[str, object]:
    record: dict[str, object] = {
        "package_id": package_id,
        "version_constraint": version_constraint,
    }
    if manifest_digest is not None:
        record["manifest_digest"] = manifest_digest
    return record


def _dependency_manifest(
    *,
    package_id: str = DEP_ID,
    package_version: str = DEP_VERSION,
    dependencies: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    manifest = _manifest(
        package_id=package_id,
        package_version=package_version,
        workflow_id="wf.dependency",
        dependencies=[] if dependencies is None else dependencies,
        required_dependencies=[],
    )
    package = cast(dict[str, object], manifest["package"])
    package["package_role"] = "dependency_only"
    manifest["workflows"] = []
    manifest["assets"] = []
    manifest["manifest_digest"] = manifest_digest_for_manifest(manifest)
    return manifest


def _registry_with_dependency(
    *,
    root_manifest: dict[str, object] | None = None,
    dependency_manifest: dict[str, object] | None = None,
    dependency_record: dict[str, object] | None = None,
    extra_records: list[dict[str, object]] | None = None,
) -> tuple[PackageRegistryView, dict[str, bytes]]:
    dependency_manifest = (
        _dependency_manifest()
        if dependency_manifest is None
        else dependency_manifest
    )
    root_manifest = (
        _manifest(
            dependencies=[_dependency_decl()],
            required_dependencies=[DEP_ID],
        )
        if root_manifest is None
        else root_manifest
    )
    dep_record = (
        _registry_record(
            dependency_manifest,
            package_id=DEP_ID,
            package_version=DEP_VERSION,
            manifest_cas_digest=DEP_CAS_DIGEST,
            dependencies=cast(
                list[dict[str, object]],
                dependency_manifest["dependencies"],
            ),
        )
        if dependency_record is None
        else dependency_record
    )
    records = [
        _registry_record(root_manifest),
        dep_record,
        *(extra_records or []),
    ]
    return PackageRegistryView(records=tuple(records)), {
        MANIFEST_CAS_DIGEST: _manifest_bytes(root_manifest),
        DEP_CAS_DIGEST: _manifest_bytes(dependency_manifest),
        "cas:asset.prompt": ASSET_BYTES,
    }


def test_package_selection_resolves_declared_dependency_closure() -> None:
    registry, cas = _registry_with_dependency()

    plan = _compile_from_package(registry=registry, cas=cas)

    assert plan.workflow_package_pin is not None
    assert plan.workflow_package_pin.selected_dependency_pins == (
        SelectedWorkflowPackageDependencyPin(
            package_id=DEP_ID,
            package_version=DEP_VERSION,
            package_format_version="1",
        ),
    )


def test_compile_refuses_non_exact_dependency_version_constraint() -> None:
    root_manifest = _manifest(
        dependencies=[_dependency_decl(version_constraint=">=2")],
        required_dependencies=[DEP_ID],
    )
    registry, cas = _registry_with_dependency(root_manifest=root_manifest)

    result = _compile_result(registry=registry, cas=cas)

    assert result.plan is None
    assert "package_selection_non_exact_dependency_constraint" in _error_codes(result)


def test_package_selection_refuses_missing_dependency() -> None:
    root_manifest = _manifest(
        dependencies=[_dependency_decl()],
        required_dependencies=[DEP_ID],
    )
    registry, cas = _registry_and_cas(manifest=root_manifest)

    result = _compile_result(registry=registry, cas=cas)

    assert result.plan is None
    assert "package_selection_dependency_not_found" in _error_codes(result)


def test_package_selection_refuses_malformed_dependency_declaration() -> None:
    registry, cas = _registry_with_dependency()
    root_record = cast(dict[str, object], registry.records[0])
    root_record["dependencies"] = [
        {"package_id": None, "version_constraint": f"=={DEP_VERSION}"}
    ]

    result = _compile_result(registry=registry, cas=cas)

    assert result.plan is None
    assert _error_codes(result) == {"package_selection_dependency_malformed"}


def test_package_selection_refuses_undeclared_required_dependency() -> None:
    root_manifest = _manifest(
        dependencies=[],
        required_dependencies=[DEP_ID],
    )
    registry, cas = _registry_with_dependency(root_manifest=root_manifest)

    result = _compile_result(registry=registry, cas=cas)

    assert result.plan is None
    assert _error_codes(result) == {"package_selection_dependency_not_declared"}


def test_package_selection_refuses_disabled_dependency() -> None:
    dependency_manifest = _dependency_manifest()
    dependency_record = _registry_record(
        dependency_manifest,
        package_id=DEP_ID,
        package_version=DEP_VERSION,
        manifest_cas_digest=DEP_CAS_DIGEST,
        status="disabled",
        dependencies=[],
    )
    registry, cas = _registry_with_dependency(
        dependency_manifest=dependency_manifest,
        dependency_record=dependency_record,
    )

    result = _compile_result(registry=registry, cas=cas)

    assert result.plan is None
    assert _error_codes(result) == {"package_selection_dependency_status_refused"}


def test_package_selection_refuses_dependency_manifest_digest_mismatch() -> None:
    root_manifest = _manifest(
        dependencies=[
            _dependency_decl(manifest_digest="sha256:" + ("9" * 64)),
        ],
        required_dependencies=[DEP_ID],
    )
    registry, cas = _registry_with_dependency(root_manifest=root_manifest)

    result = _compile_result(registry=registry, cas=cas)

    assert result.plan is None
    assert "package_selection_dependency_manifest_digest_mismatch" in _error_codes(
        result
    )


def test_package_selection_refuses_dependency_conflict() -> None:
    root_manifest = _manifest(
        dependencies=[
            _dependency_decl(version_constraint=f"=={DEP_VERSION}"),
            _dependency_decl(version_constraint="==9.9.9"),
        ],
        required_dependencies=[DEP_ID],
    )
    registry, cas = _registry_with_dependency(root_manifest=root_manifest)

    result = _compile_result(registry=registry, cas=cas)

    assert result.plan is None
    assert "package_selection_dependency_conflict" in _error_codes(result)


def test_package_selection_refuses_ambiguous_dependency_resolution() -> None:
    dep_manifest = _dependency_manifest()
    duplicate = _registry_record(
        dep_manifest,
        package_id=DEP_ID,
        package_version=DEP_VERSION,
        manifest_cas_digest=DEP_CAS_DIGEST,
    )
    registry, cas = _registry_with_dependency(extra_records=[duplicate])

    result = _compile_result(registry=registry, cas=cas)

    assert result.plan is None
    assert "package_selection_ambiguous_dependency" in _error_codes(result)


def test_package_selection_refuses_dependency_cycle() -> None:
    dep_manifest = _dependency_manifest(
        dependencies=[
            _dependency_decl(
                package_id=PACKAGE_ID,
                version_constraint=f"=={PACKAGE_VERSION}",
            )
        ],
    )
    registry, cas = _registry_with_dependency(dependency_manifest=dep_manifest)

    result = _compile_result(registry=registry, cas=cas)

    assert result.plan is None
    assert "package_selection_dependency_cycle" in _error_codes(result)


def test_package_selection_refuses_duplicate_dependency_pin_from_diamond() -> None:
    dep_a = "pkg.example.dependency-a"
    dep_b = "pkg.example.dependency-b"
    shared = "pkg.example.dependency-shared"
    root_manifest = _manifest(
        dependencies=[
            _dependency_decl(package_id=dep_a),
            _dependency_decl(package_id=dep_b),
        ],
        required_dependencies=[dep_a, dep_b],
    )
    shared_decl = _dependency_decl(package_id=shared)
    dep_a_manifest = _dependency_manifest(package_id=dep_a)
    dep_b_manifest = _dependency_manifest(package_id=dep_b)
    shared_manifest = _dependency_manifest(package_id=shared)
    records = [
        _registry_record(root_manifest),
        _registry_record(
            dep_a_manifest,
            package_id=dep_a,
            package_version=DEP_VERSION,
            manifest_cas_digest="cas:manifest.dependency-a",
            dependencies=[shared_decl],
        ),
        _registry_record(
            dep_b_manifest,
            package_id=dep_b,
            package_version=DEP_VERSION,
            manifest_cas_digest="cas:manifest.dependency-b",
            dependencies=[shared_decl],
        ),
        _registry_record(
            shared_manifest,
            package_id=shared,
            package_version=DEP_VERSION,
            manifest_cas_digest="cas:manifest.dependency-shared",
            dependencies=[],
        ),
    ]
    registry, cas = _registry_and_cas(manifest=root_manifest, records=records)

    result = _compile_result(registry=registry, cas=cas)

    assert result.plan is None
    assert _error_codes(result) == {"package_selection_duplicate_dependency_pin"}


def test_selected_dependency_pin_change_affects_authority_fingerprint_export_and_codec_payload() -> None:  # noqa: E501
    first_registry, first_cas = _registry_with_dependency()
    second_root = _manifest(
        dependencies=[_dependency_decl(version_constraint="==2.0.1")],
        required_dependencies=[DEP_ID],
    )
    second_dep = _dependency_manifest(package_version="2.0.1")
    second_record = _registry_record(
        second_dep,
        package_id=DEP_ID,
        package_version="2.0.1",
        manifest_cas_digest=DEP_CAS_DIGEST,
    )
    second_registry, second_cas = _registry_with_dependency(
        root_manifest=second_root,
        dependency_manifest=second_dep,
        dependency_record=second_record,
    )

    first_plan = _compile_from_package(registry=first_registry, cas=first_cas)
    second_plan = _compile_from_package(registry=second_registry, cas=second_cas)

    assert authority_fingerprint(first_plan) != authority_fingerprint(second_plan)
    first_export = cast(
        dict[str, object],
        cast(dict[str, object], compiled_plan_export_record(first_plan))[
            "selected_authority"
        ],
    )
    second_export = cast(
        dict[str, object],
        cast(dict[str, object], compiled_plan_export_record(second_plan))[
            "selected_authority"
        ],
    )
    assert json.dumps(first_export, sort_keys=True) != json.dumps(
        second_export,
        sort_keys=True,
    )
    assert (
        encode_selected_compiled_plan(first_plan).payload["workflow_package_pin"]
        != encode_selected_compiled_plan(second_plan).payload["workflow_package_pin"]
    )


def test_missing_or_malformed_selected_dependency_pin_fields_refuse() -> None:
    pin = SelectedWorkflowPackagePin(
        package_id=PACKAGE_ID,
        package_version=PACKAGE_VERSION,
        package_format_version="1",
        workflow_id="wf.package",
        workflow_version="1",
        entrypoint="default",
        selected_asset_pins=(),
        selected_dependency_pins=(
            SelectedWorkflowPackageDependencyPin(
                package_id=DEP_ID,
                package_version=DEP_VERSION,
                package_format_version="1",
            ),
        ),
    )

    with pytest.raises(ValueError, match="package_id must be non-empty text"):
        replace(pin.selected_dependency_pins[0], package_id="")


def test_duplicate_selected_dependency_pin_refuses() -> None:
    duplicate = SelectedWorkflowPackageDependencyPin(
        package_id=DEP_ID,
        package_version=DEP_VERSION,
        package_format_version="1",
    )

    with pytest.raises(ValueError, match="duplicate selected dependency pin"):
        SelectedWorkflowPackagePin(
            package_id=PACKAGE_ID,
            package_version=PACKAGE_VERSION,
            package_format_version="1",
            workflow_id="wf.package",
            workflow_version="1",
            entrypoint="default",
            selected_asset_pins=(),
            selected_dependency_pins=(duplicate, duplicate),
        )


def test_package_selection_does_not_fingerprint_unselected_dependencies() -> None:
    base_manifest = _manifest(
        dependencies=[
            _dependency_decl(package_id=DEP_ID, version_constraint=f"=={DEP_VERSION}")
        ],
        required_dependencies=[],
    )
    changed_manifest = deepcopy(base_manifest)
    cast(list[dict[str, object]], changed_manifest["dependencies"])[0][
        "version_constraint"
    ] = "==9.9.9"
    changed_manifest["manifest_digest"] = manifest_digest_for_manifest(
        changed_manifest
    )
    base_registry, base_cas = _registry_and_cas(manifest=base_manifest)
    changed_registry, changed_cas = _registry_and_cas(manifest=changed_manifest)

    base_plan = _compile_from_package(registry=base_registry, cas=base_cas)
    changed_plan = _compile_from_package(registry=changed_registry, cas=changed_cas)

    assert base_plan.workflow_package_pin is not None
    assert changed_plan.workflow_package_pin is not None
    assert base_plan.workflow_package_pin.selected_dependency_pins == ()
    assert changed_plan.workflow_package_pin.selected_dependency_pins == ()
    assert authority_fingerprint(base_plan) == authority_fingerprint(changed_plan)
