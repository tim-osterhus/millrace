from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
from typing import cast

from millrace.compiler.workflow_package_manifest import (
    validate_importable_workflow_package_manifest,
    validate_workflow_package_manifest,
)
from millrace.contracts.workflow_package import (
    asset_digest_for_bytes,
    canonical_manifest_bytes,
    manifest_digest_for_manifest,
)

ManifestSource = dict[str, object]
Record = dict[str, object]

DIGEST = "sha256:" + ("1" * 64)


def _minimal_manifest() -> ManifestSource:
    return {
        "record_kind": "millrace.workflow_package_manifest",
        "manifest_format_version": "1",
        "package": {
            "package_id": "pkg.example.diagnostic",
            "package_version": "1.0.0",
            "package_format_version": "1",
            "package_role": "workflow_package",
            "publisher": "Example",
            "base_millrace_compatibility": ">=0.22,<0.23",
        },
        "workflows": [
            {
                "workflow_id": "wf.echo_check",
                "workflow_version": "1",
                "visibility": "test_only",
                "entrypoints": ["default"],
                "selected_authority": {
                    "graphs": ["graph.echo"],
                    "stage_kinds": ["stage.receive", "stage.respond"],
                    "terminal_outcomes": ["outcome.accepted"],
                    "terminal_actions": ["action.close"],
                },
                "required_assets": [
                    {"asset_id": "asset.echo_prompt", "content_digest": DIGEST}
                ],
            }
        ],
        "assets": [
            {
                "asset_id": "asset.echo_prompt",
                "asset_kind": "entrypoint_prompt",
                "media_type": "text/markdown; charset=utf-8",
                "encoding": "utf-8",
                "content_digest": DIGEST,
                "byte_length": 12,
                "package_path": "prompts/echo.md",
                "selection": "required",
                "selected_authority_participation": "yes",
            }
        ],
        "dependencies": [],
        "compatibility": {"base_millrace": ">=0.22,<0.23"},
        "canonicalization": {"algorithm": "millrace-json-v1", "hash": "sha256"},
        "manifest_digest": None,
        "non_authoritative_metadata": {},
    }


def _workflow(source: ManifestSource) -> Record:
    return cast(Record, cast(list[object], source["workflows"])[0])


def _asset(source: ManifestSource) -> Record:
    return cast(Record, cast(list[object], source["assets"])[0])


def _assert_source_and_typed_manifest_authority_match(
    source: ManifestSource,
) -> None:
    result = validate_workflow_package_manifest(source)
    assert result.diagnostics == ()
    assert result.manifest is not None
    assert canonical_manifest_bytes(source) == canonical_manifest_bytes(result.manifest)
    assert manifest_digest_for_manifest(source) == manifest_digest_for_manifest(
        result.manifest
    )


def test_workflow_package_manifest_digest_is_deterministic_for_reordered_maps() -> None:
    first = _minimal_manifest()
    second = OrderedDict(cast(dict[str, object], deepcopy(first)))
    second.move_to_end("package")
    second.move_to_end("record_kind")
    second_workflow = _workflow(cast(ManifestSource, second))
    selected_authority = cast(Record, second_workflow["selected_authority"])
    second_workflow["selected_authority"] = {
        "terminal_actions": selected_authority["terminal_actions"],
        "terminal_outcomes": selected_authority["terminal_outcomes"],
        "stage_kinds": selected_authority["stage_kinds"],
        "graphs": selected_authority["graphs"],
    }

    assert canonical_manifest_bytes(first) == canonical_manifest_bytes(second)
    assert manifest_digest_for_manifest(first) == manifest_digest_for_manifest(second)


def test_workflow_package_manifest_digest_matches_typed_manifest_for_reordered_asset_refs() -> None:  # noqa: E501
    source = _minimal_manifest()
    second_digest = "sha256:" + ("2" * 64)
    second_asset = dict(_asset(source))
    second_asset["asset_id"] = "asset.echo_prompt_extra"
    second_asset["content_digest"] = second_digest
    second_asset["package_path"] = "prompts/extra.md"
    cast(list[object], source["assets"]).append(second_asset)
    _workflow(source)["required_assets"] = [
        {"asset_id": "asset.echo_prompt_extra", "content_digest": second_digest},
        {"asset_id": "asset.echo_prompt", "content_digest": DIGEST},
    ]

    _assert_source_and_typed_manifest_authority_match(source)


def test_workflow_package_manifest_digest_matches_typed_manifest_for_explicit_empty_optional_fields() -> None:  # noqa: E501
    source = _minimal_manifest()
    workflow = _workflow(source)
    workflow["required_dependencies"] = []
    workflow["display"] = {}
    workflow["source_refs"] = []

    _assert_source_and_typed_manifest_authority_match(source)


def test_workflow_package_manifest_digest_matches_typed_manifest_for_null_dependency_digest() -> None:  # noqa: E501
    source = _minimal_manifest()
    source["dependencies"] = [
        {
            "package_id": "pkg.example.dep",
            "version_constraint": ">=1",
            "manifest_digest": None,
        }
    ]

    _assert_source_and_typed_manifest_authority_match(source)


def test_workflow_package_manifest_digest_excludes_manifest_digest_field() -> None:
    source = _minimal_manifest()
    digest = manifest_digest_for_manifest(source)

    source["manifest_digest"] = "sha256:" + ("f" * 64)

    assert manifest_digest_for_manifest(source) == digest


def test_workflow_package_importable_manifest_requires_non_null_digest() -> None:
    source = _minimal_manifest()

    missing_digest = validate_importable_workflow_package_manifest(source)

    assert missing_digest.manifest is None
    assert [diagnostic.code for diagnostic in missing_digest.diagnostics] == [
        "missing_manifest_digest"
    ]

    source["manifest_digest"] = "sha256:" + ("f" * 64)
    mismatched_digest = validate_importable_workflow_package_manifest(source)
    assert mismatched_digest.manifest is None
    assert [diagnostic.code for diagnostic in mismatched_digest.diagnostics] == [
        "manifest_digest_mismatch"
    ]

    source["manifest_digest"] = manifest_digest_for_manifest(source)
    valid = validate_importable_workflow_package_manifest(source)
    assert valid.diagnostics == ()
    assert valid.manifest is not None


def test_workflow_package_manifest_digest_ignores_publication_scope_and_source_kind() -> None:  # noqa: E501
    public_path = _minimal_manifest()
    internal_archive = _minimal_manifest()
    cast(Record, public_path["package"])["source_kind"] = "path"
    cast(Record, public_path["package"])["publication_scope"] = "public"
    cast(Record, internal_archive["package"])["source_kind"] = "archive"
    cast(Record, internal_archive["package"])["publication_scope"] = "internal"

    assert manifest_digest_for_manifest(public_path) == manifest_digest_for_manifest(
        internal_archive
    )


def test_workflow_package_manifest_digest_ignores_workflow_display_and_source_refs() -> None:  # noqa: E501
    base = _minimal_manifest()
    with_metadata = _minimal_manifest()
    _workflow(with_metadata)["display"] = {
        "name": "Echo Check",
        "summary": "Diagnostic workflow",
    }
    _workflow(with_metadata)["source_refs"] = ["workflows/echo.json"]

    assert canonical_manifest_bytes(base) == canonical_manifest_bytes(with_metadata)
    assert manifest_digest_for_manifest(base) == manifest_digest_for_manifest(
        with_metadata
    )

    base_result = validate_importable_workflow_package_manifest(
        {
            **base,
            "manifest_digest": manifest_digest_for_manifest(base),
        }
    )
    metadata_result = validate_importable_workflow_package_manifest(
        {
            **with_metadata,
            "manifest_digest": manifest_digest_for_manifest(with_metadata),
        }
    )
    assert base_result.manifest is not None
    assert metadata_result.manifest is not None
    assert manifest_digest_for_manifest(
        base_result.manifest
    ) == manifest_digest_for_manifest(metadata_result.manifest)


def test_workflow_package_asset_digest_uses_exact_bytes_only() -> None:
    assert asset_digest_for_bytes(b"prompt\n") == asset_digest_for_bytes(b"prompt\n")
    assert asset_digest_for_bytes(b"prompt\n") != asset_digest_for_bytes(b"prompt\r\n")
    assert asset_digest_for_bytes(b"prompt\n") != manifest_digest_for_manifest(
        _minimal_manifest()
    )


def test_equivalent_path_archive_and_installed_bytes_share_manifest_digest() -> None:
    path_manifest = _minimal_manifest()
    archive_manifest = _minimal_manifest()
    installed_manifest = _minimal_manifest()
    cast(Record, path_manifest["package"])["source_kind"] = "path"
    cast(Record, archive_manifest["package"])["source_kind"] = "archive"
    cast(Record, installed_manifest["package"])[
        "source_kind"
    ] = "installed_python_package"
    path_manifest["non_authoritative_metadata"] = {"source_path": "/tmp/pkg"}
    archive_manifest["non_authoritative_metadata"] = {"archive_path": "/tmp/pkg.tar"}
    installed_manifest["non_authoritative_metadata"] = {
        "distribution": "pkg-example-diagnostic"
    }

    digest = manifest_digest_for_manifest(path_manifest)

    assert manifest_digest_for_manifest(archive_manifest) == digest
    assert manifest_digest_for_manifest(installed_manifest) == digest
