from __future__ import annotations

import json
from copy import deepcopy
from typing import cast

from millrace.compiler import compiled_plan_export_record
from millrace.compiler.canonical import authority_fingerprint
from millrace.contracts.workflow_package import (
    asset_digest_for_bytes,
    manifest_digest_for_manifest,
)
from millrace.substrate.codecs import encode_selected_compiled_plan
from tests.compiler.test_workflow_package_dependency_closure import _dependency_decl
from tests.compiler.test_workflow_package_selection import (
    _CODEX_POLICY,
    ASSET_BYTES,
    PACKAGE_ID,
    _compile_from_package,
    _manifest,
    _registry_and_cas,
    _registry_record,
)


def _export_selected_authority(plan) -> object:
    return compiled_plan_export_record(plan)["selected_authority"]


def _codec_package_pin(plan) -> object:
    return encode_selected_compiled_plan(plan).payload["workflow_package_pin"]


def test_selected_package_pins_affect_authority_fingerprint() -> None:
    first_manifest = _manifest(package_id=PACKAGE_ID)
    second_manifest = _manifest(package_id="pkg.example.other")
    first_registry, first_cas = _registry_and_cas(manifest=first_manifest)
    second_registry, second_cas = _registry_and_cas(
        manifest=second_manifest,
        records=[
            _registry_record(
                second_manifest,
                package_id="pkg.example.other",
            )
        ],
    )

    first_plan = _compile_from_package(registry=first_registry, cas=first_cas)
    second_plan = _compile_from_package(
        registry=second_registry,
        cas=second_cas,
        selector=__import__(
            "millrace.compiler.package_selection",
            fromlist=["PackageWorkflowSelector"],
        ).PackageWorkflowSelector(
            package_id="pkg.example.other",
            package_version="1.0.0",
            workflow_id="wf.package",
            workflow_version="1",
            selected_runner_policy=_CODEX_POLICY,
        ),
    )

    assert authority_fingerprint(first_plan) != authority_fingerprint(second_plan)


def test_unselected_package_workflow_change_does_not_affect_fingerprint_export_or_codec() -> None:  # noqa: E501
    base_manifest = _manifest()
    changed_manifest = deepcopy(base_manifest)
    workflows = cast(list[object], changed_manifest["workflows"])
    workflows.append(
        {
            "workflow_id": "wf.unselected",
            "workflow_version": "1",
            "visibility": "test_only",
            "entrypoints": ["default"],
            "selected_authority": {"workflow": {"id": "wf.unselected"}},
            "required_assets": [],
        }
    )
    changed_manifest["manifest_digest"] = manifest_digest_for_manifest(changed_manifest)
    base_registry, base_cas = _registry_and_cas(manifest=base_manifest)
    changed_registry, changed_cas = _registry_and_cas(manifest=changed_manifest)

    base_plan = _compile_from_package(registry=base_registry, cas=base_cas)
    changed_plan = _compile_from_package(registry=changed_registry, cas=changed_cas)

    assert authority_fingerprint(base_plan) == authority_fingerprint(changed_plan)
    assert _export_selected_authority(base_plan) == _export_selected_authority(
        changed_plan
    )
    assert _codec_package_pin(base_plan) == _codec_package_pin(changed_plan)


def test_unselected_package_asset_change_does_not_affect_fingerprint_export_or_codec() -> None:  # noqa: E501
    base_manifest = _manifest()
    changed_manifest = deepcopy(base_manifest)
    cast(list[object], changed_manifest["assets"]).append(
        {
            "asset_id": "asset.unselected",
            "asset_kind": "fixture",
            "media_type": "text/plain; charset=utf-8",
            "encoding": "utf-8",
            "content_digest": asset_digest_for_bytes(b"changed unselected"),
            "byte_length": len(b"changed unselected"),
            "package_path": "fixtures/unselected.txt",
            "selection": "optional_example",
            "selected_authority_participation": "no",
        }
    )
    changed_manifest["manifest_digest"] = manifest_digest_for_manifest(changed_manifest)
    base_registry, base_cas = _registry_and_cas(manifest=base_manifest)
    changed_registry, changed_cas = _registry_and_cas(manifest=changed_manifest)

    base_plan = _compile_from_package(registry=base_registry, cas=base_cas)
    changed_plan = _compile_from_package(registry=changed_registry, cas=changed_cas)

    assert authority_fingerprint(base_plan) == authority_fingerprint(changed_plan)
    assert _export_selected_authority(base_plan) == _export_selected_authority(
        changed_plan
    )
    assert _codec_package_pin(base_plan) == _codec_package_pin(changed_plan)


def test_unselected_dependency_change_does_not_affect_fingerprint_export_or_codec() -> (
    None
):  # noqa: E501
    base_manifest = _manifest(
        dependencies=[_dependency_decl(version_constraint="==1.0.0")],
        required_dependencies=[],
    )
    changed_manifest = _manifest(
        dependencies=[_dependency_decl(version_constraint="==9.9.9")],
        required_dependencies=[],
    )
    base_registry, base_cas = _registry_and_cas(manifest=base_manifest)
    changed_registry, changed_cas = _registry_and_cas(manifest=changed_manifest)

    base_plan = _compile_from_package(registry=base_registry, cas=base_cas)
    changed_plan = _compile_from_package(registry=changed_registry, cas=changed_cas)

    assert authority_fingerprint(base_plan) == authority_fingerprint(changed_plan)
    assert _export_selected_authority(base_plan) == _export_selected_authority(
        changed_plan
    )
    assert _codec_package_pin(base_plan) == _codec_package_pin(changed_plan)


def test_source_provenance_import_record_status_and_cas_refs_do_not_affect_runtime_fingerprint() -> None:  # noqa: E501
    base_manifest = _manifest()
    changed_manifest = deepcopy(base_manifest)
    package = cast(dict[str, object], changed_manifest["package"])
    package["source_kind"] = "path"
    package["publication_scope"] = "internal"
    changed_manifest["non_authoritative_metadata"] = {
        "import_record_digest": "sha256:" + ("a" * 64),
        "status_generation": 42,
        "audit_id": "audit.example",
    }
    changed_manifest["manifest_digest"] = manifest_digest_for_manifest(changed_manifest)
    base_registry, base_cas = _registry_and_cas(manifest=base_manifest)
    changed_record = _registry_record(
        changed_manifest,
        package_digest="sha256:" + ("b" * 64),
        manifest_cas_digest="cas:manifest.changed",
        asset_cas_digest="cas:asset.changed",
    )
    changed_record["source_kind"] = "path"
    changed_record["import_record_digest"] = "sha256:" + ("c" * 64)
    changed_record["status_generation"] = 99
    changed_record["latest_audit_id"] = "audit.changed"
    changed_registry, changed_cas = _registry_and_cas(
        manifest=changed_manifest,
        records=[changed_record],
        cas={
            "cas:manifest.changed": json.dumps(
                changed_manifest,
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8"),
            "cas:asset.changed": ASSET_BYTES,
        },
    )

    base_plan = _compile_from_package(registry=base_registry, cas=base_cas)
    changed_plan = _compile_from_package(registry=changed_registry, cas=changed_cas)

    assert authority_fingerprint(base_plan) == authority_fingerprint(changed_plan)
    assert _export_selected_authority(base_plan) == _export_selected_authority(
        changed_plan
    )
    assert _codec_package_pin(base_plan) == _codec_package_pin(changed_plan)


def test_registry_package_generation_does_not_affect_runtime_fingerprint() -> None:
    manifest = _manifest()
    base_registry, base_cas = _registry_and_cas(manifest=manifest)
    changed_record = _registry_record(manifest)
    changed_record["package_generation"] = 99
    changed_registry, changed_cas = _registry_and_cas(
        manifest=manifest,
        records=[changed_record],
    )

    base_plan = _compile_from_package(registry=base_registry, cas=base_cas)
    changed_plan = _compile_from_package(registry=changed_registry, cas=changed_cas)

    assert authority_fingerprint(base_plan) == authority_fingerprint(changed_plan)
    assert _export_selected_authority(base_plan) == _export_selected_authority(
        changed_plan
    )
    assert _codec_package_pin(base_plan) == _codec_package_pin(changed_plan)


def test_identical_selected_content_from_different_provenance_share_runtime_fingerprint() -> None:  # noqa: E501
    first_manifest = _manifest()
    second_manifest = _manifest()
    cast(dict[str, object], second_manifest["package"])["source_ref"] = "git:example"
    second_manifest["non_authoritative_metadata"] = {"archive_name": "other.tar"}
    second_manifest["manifest_digest"] = manifest_digest_for_manifest(second_manifest)
    first_registry, first_cas = _registry_and_cas(manifest=first_manifest)
    second_record = _registry_record(
        second_manifest,
        package_digest="sha256:" + ("d" * 64),
    )
    second_record["source_provenance_digest"] = "sha256:" + ("e" * 64)
    second_registry, second_cas = _registry_and_cas(
        manifest=second_manifest,
        records=[second_record],
    )

    first_plan = _compile_from_package(registry=first_registry, cas=first_cas)
    second_plan = _compile_from_package(registry=second_registry, cas=second_cas)

    assert authority_fingerprint(first_plan) == authority_fingerprint(second_plan)


def test_selected_asset_pin_change_affects_fingerprint_export_and_codec() -> None:
    changed_bytes = b"Changed selected package prompt\n"
    base_manifest = _manifest(asset_bytes=ASSET_BYTES)
    changed_manifest = _manifest(
        asset_bytes=changed_bytes,
        selected_authority={
            **cast(dict[str, object], _manifest()["workflows"][0])[
                "selected_authority"
            ],
        },
    )
    base_registry, base_cas = _registry_and_cas(
        manifest=base_manifest,
        asset_bytes=ASSET_BYTES,
    )
    changed_registry, changed_cas = _registry_and_cas(
        manifest=changed_manifest,
        asset_bytes=changed_bytes,
    )

    base_plan = _compile_from_package(registry=base_registry, cas=base_cas)
    changed_plan = _compile_from_package(registry=changed_registry, cas=changed_cas)

    assert authority_fingerprint(base_plan) != authority_fingerprint(changed_plan)
    assert _export_selected_authority(base_plan) != _export_selected_authority(
        changed_plan
    )
    assert _codec_package_pin(base_plan) != _codec_package_pin(changed_plan)
