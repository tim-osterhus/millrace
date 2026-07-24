from __future__ import annotations

from collections import OrderedDict
from dataclasses import FrozenInstanceError
from typing import Any, cast

import pytest

from millrace.compiler.workflow_package_manifest import (
    validate_workflow_package_manifest,
)
from millrace.contracts.workflow_package import (
    WorkflowPackageAsset,
    WorkflowPackageManifest,
    WorkflowPackageWorkflow,
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


def _find_error(source: ManifestSource, code: str) -> object:
    result = validate_workflow_package_manifest(source)
    assert result.manifest is None
    matches = [
        diagnostic for diagnostic in result.diagnostics if diagnostic.code == code
    ]
    assert matches, f"missing diagnostic code {code!r} in {result.diagnostics!r}"
    return matches[0]


def _workflow(source: ManifestSource) -> Record:
    return cast(Record, cast(list[object], source["workflows"])[0])


def _asset(source: ManifestSource) -> Record:
    return cast(Record, cast(list[object], source["assets"])[0])


def _required_asset_ref(source: ManifestSource) -> Record:
    return cast(Record, cast(list[object], _workflow(source)["required_assets"])[0])


def _add_non_string_dependency_key(source: ManifestSource) -> None:
    dependency: dict[object, object] = {
        "package_id": "pkg.example.dep",
        "version_constraint": ">=1",
    }
    dependency[("dependency",)] = True
    source["dependencies"] = [dependency]


def test_minimal_workflow_package_manifest_records_are_typed_and_immutable() -> None:
    source = _minimal_manifest()
    selected_authority = cast(Record, _workflow(source)["selected_authority"])
    graphs = cast(list[str], selected_authority["graphs"])

    result = validate_workflow_package_manifest(source)

    assert result.diagnostics == ()
    assert isinstance(result.manifest, WorkflowPackageManifest)
    assert isinstance(result.manifest.workflows[0], WorkflowPackageWorkflow)
    assert isinstance(result.manifest.assets[0], WorkflowPackageAsset)
    assert result.manifest.workflows[0].selected_authority["graphs"] == (
        "graph.echo",
    )
    assert isinstance(result.manifest.workflows, tuple)
    assert isinstance(result.manifest.assets, tuple)

    graphs.append("graph.mutated")
    assert result.manifest.workflows[0].selected_authority["graphs"] == (
        "graph.echo",
    )
    with pytest.raises(FrozenInstanceError):
        result.manifest.package.package_id = "pkg.example.mutated"  # type: ignore[misc]
    with pytest.raises(TypeError):
        result.manifest.workflows[0].selected_authority["graphs"] = ()  # type: ignore[index]


def test_workflow_package_manifest_contract_exports_only_current_api() -> None:
    import millrace.contracts.workflow_package as workflow_package

    assert "CanonicalManifestValue" not in workflow_package.__all__
    assert "UnsupportedWorkflowPackageManifestValue" not in workflow_package.__all__
    assert "freeze_manifest_value" not in workflow_package.__all__
    assert "freeze_manifest_mapping" not in workflow_package.__all__


def test_workflow_package_manifest_refuses_unknown_root_fields() -> None:
    source = _minimal_manifest()
    source["extra"] = "unsupported"

    error = _find_error(source, "unknown_manifest_field")

    assert error.declaration_path == "extra"
    assert error.context["field"] == "extra"


@pytest.mark.parametrize(
    ("mutate", "path", "actual_type"),
    [
        (
            lambda source: cast(Any, source).__setitem__(("root",), True),
            "<non_string_key>",
            "tuple",
        ),
        (
            lambda source: cast(Any, source["package"]).__setitem__(
                ("package",),
                True,
            ),
            "package.<non_string_key>",
            "tuple",
        ),
        (
            lambda source: cast(Any, _workflow(source)).__setitem__(
                ("workflow",),
                True,
            ),
            "workflows[0].<non_string_key>",
            "tuple",
        ),
        (
            lambda source: cast(Any, _asset(source)).__setitem__(
                ("asset",),
                True,
            ),
            "assets[0].<non_string_key>",
            "tuple",
        ),
        (
            _add_non_string_dependency_key,
            "dependencies[0].<non_string_key>",
            "tuple",
        ),
        (
            lambda source: cast(Any, _required_asset_ref(source)).__setitem__(
                ("asset_ref",),
                True,
            ),
            "workflows[0].required_assets[0].<non_string_key>",
            "tuple",
        ),
    ],
)
def test_workflow_package_manifest_refuses_non_string_structural_keys(
    mutate: object,
    path: str,
    actual_type: str,
) -> None:
    source = _minimal_manifest()
    cast(Any, mutate)(source)

    error = _find_error(source, "invalid_manifest_shape")

    assert error.declaration_path == path
    assert error.context["expected_type"] == "str_key"
    assert error.context["actual_type"] == actual_type


def test_workflow_package_manifest_refuses_unsupported_record_kind() -> None:
    source = _minimal_manifest()
    source["record_kind"] = "millrace.workflow_package_archive"

    error = _find_error(source, "unsupported_manifest_record_kind")

    assert error.declaration_path == "record_kind"
    assert error.context["record_kind"] == "millrace.workflow_package_archive"


def test_workflow_package_manifest_refuses_unsupported_format_version() -> None:
    source = _minimal_manifest()
    source["manifest_format_version"] = "99"

    error = _find_error(source, "unsupported_manifest_format_version")

    assert error.declaration_path == "manifest_format_version"
    assert error.context["manifest_format_version"] == "99"


def test_workflow_package_manifest_refuses_missing_required_root_fields() -> None:
    source = _minimal_manifest()
    del source["canonicalization"]

    error = _find_error(source, "missing_manifest_field")

    assert error.declaration_path == "canonicalization"
    assert error.context["field"] == "canonicalization"


def test_workflow_package_manifest_refuses_workflows_as_string() -> None:
    source = _minimal_manifest()
    source["workflows"] = "wf.echo_check"

    error = _find_error(source, "invalid_manifest_shape")

    assert error.declaration_path == "workflows"
    assert error.context["expected_type"] == "sequence"
    assert error.context["actual_type"] == "str"


def test_workflow_package_manifest_refuses_package_version_as_int() -> None:
    source = _minimal_manifest()
    cast(Record, source["package"])["package_version"] = 1

    error = _find_error(source, "invalid_manifest_shape")

    assert error.declaration_path == "package.package_version"
    assert error.context["expected_type"] == "str"
    assert error.context["actual_type"] == "int"


def test_workflow_package_manifest_refuses_asset_byte_length_as_string() -> None:
    source = _minimal_manifest()
    _asset(source)["byte_length"] = "12"

    error = _find_error(source, "invalid_manifest_shape")

    assert error.declaration_path == "assets[0].byte_length"
    assert error.context["expected_type"] == "int"
    assert error.context["actual_type"] == "str"


def test_workflow_package_manifest_refuses_negative_asset_byte_length() -> None:
    source = _minimal_manifest()
    _asset(source)["byte_length"] = -1

    error = _find_error(source, "invalid_asset_byte_length")

    assert error.declaration_path == "assets[0].byte_length"
    assert error.context["byte_length"] == -1


def test_workflow_package_manifest_refuses_selected_authority_as_list() -> None:
    source = _minimal_manifest()
    _workflow(source)["selected_authority"] = ["graph.echo"]

    error = _find_error(source, "invalid_manifest_shape")

    assert error.declaration_path == "workflows[0].selected_authority"
    assert error.context["expected_type"] == "mapping"
    assert error.context["actual_type"] == "list"


@pytest.mark.parametrize(
    ("mutate", "path"),
    [
        (
            lambda source: _workflow(source).__setitem__(
                "visibility",
                "workspace_only",
            ),
            "workflows[0].visibility",
        ),
        (
            lambda source: _asset(source).__setitem__(
                "asset_kind",
                "operator_secret",
            ),
            "assets[0].asset_kind",
        ),
        (
            lambda source: _asset(source).__setitem__("encoding", "utf-16"),
            "assets[0].encoding",
        ),
        (
            lambda source: _asset(source).__setitem__("selection", "implicit"),
            "assets[0].selection",
        ),
        (
            lambda source: _asset(source).__setitem__(
                "selected_authority_participation",
                "implicit",
            ),
            "assets[0].selected_authority_participation",
        ),
        (
            lambda source: cast(Record, source["package"]).__setitem__(
                "source_kind",
                "git",
            ),
            "package.source_kind",
        ),
        (
            lambda source: cast(Record, source["package"]).__setitem__(
                "publication_scope",
                "partner",
            ),
            "package.publication_scope",
        ),
        (
            lambda source: cast(Record, source["package"]).__setitem__(
                "package_role",
                "native_runner_package",
            ),
            "package.package_role",
        ),
    ],
)
def test_workflow_package_manifest_refuses_closed_contract_values(
    mutate: object,
    path: str,
) -> None:
    source = _minimal_manifest()
    cast(Any, mutate)(source)

    error = _find_error(source, "invalid_manifest_value")

    assert error.declaration_path == path


def test_workflow_package_manifest_refuses_prompt_asset_kind() -> None:
    source = _minimal_manifest()
    _asset(source)["asset_kind"] = "prompt"

    error = _find_error(source, "invalid_manifest_value")

    assert error.declaration_path == "assets[0].asset_kind"
    assert error.context["value"] == "prompt"


def test_workflow_package_manifest_refuses_non_nfc_package_version() -> None:
    source = _minimal_manifest()
    package_version = "1.0.cafe\u0301"
    cast(Record, source["package"])["package_version"] = package_version

    error = _find_error(source, "non_nfc_manifest_string")

    assert error.declaration_path == "package.package_version"
    assert error.context["value"] == package_version


def test_workflow_package_manifest_refuses_non_nfc_selected_authority_strings() -> None:
    source = _minimal_manifest()
    selected_authority = cast(Record, _workflow(source)["selected_authority"])
    selected_authority["labels"] = ["cafe\u0301"]

    error = _find_error(source, "non_nfc_manifest_string")

    assert error.declaration_path == "workflows[0].selected_authority.labels[0]"


@pytest.mark.parametrize(
    ("mutate", "path", "value"),
    [
        (
            lambda source: cast(Record, source["package"]).__setitem__(
                "package_version",
                " 1.0.0",
            ),
            "package.package_version",
            " 1.0.0",
        ),
        (
            lambda source: _workflow(source).__setitem__(
                "workflow_id",
                "wf.echo_check ",
            ),
            "workflows[0].workflow_id",
            "wf.echo_check ",
        ),
        (
            lambda source: cast(
                list[str],
                cast(Record, _workflow(source)["selected_authority"])["graphs"],
            ).__setitem__(0, " graph.echo"),
            "workflows[0].selected_authority.graphs[0]",
            " graph.echo",
        ),
    ],
)
def test_workflow_package_manifest_refuses_authority_string_whitespace(
    mutate: object,
    path: str,
    value: str,
) -> None:
    source = _minimal_manifest()
    cast(Any, mutate)(source)

    error = _find_error(source, "invalid_manifest_string_whitespace")

    assert error.declaration_path == path
    assert error.context["value"] == value


def test_workflow_package_manifest_refuses_asset_id_whitespace_with_matching_refs(
) -> None:
    source = _minimal_manifest()
    _asset(source)["asset_id"] = " asset.echo_prompt"
    _required_asset_ref(source)["asset_id"] = " asset.echo_prompt"

    result = validate_workflow_package_manifest(source)

    assert result.manifest is None
    error_paths = [
        diagnostic.declaration_path
        for diagnostic in result.diagnostics
        if diagnostic.code == "invalid_manifest_string_whitespace"
    ]
    assert "assets[0].asset_id" in error_paths
    assert "workflows[0].required_assets[0].asset_id" in error_paths


def test_workflow_package_manifest_refuses_empty_workflows_without_dependency_role(
) -> None:
    source = _minimal_manifest()
    source["workflows"] = []

    error = _find_error(source, "empty_workflow_package")

    assert error.declaration_path == "workflows"
    assert error.context["package_role"] == "workflow_package"


def test_workflow_package_manifest_allows_empty_workflows_for_dependency_only_role(
) -> None:
    source = _minimal_manifest()
    source["workflows"] = []
    cast(Record, source["package"])["package_role"] = "dependency_only"

    result = validate_workflow_package_manifest(source)

    assert result.diagnostics == ()
    assert result.manifest is not None
    assert result.manifest.workflows == ()


def test_workflow_package_manifest_refuses_duplicate_workflow_ids() -> None:
    source = _minimal_manifest()
    workflows = cast(list[object], source["workflows"])
    workflows.append(dict(_workflow(source)))

    error = _find_error(source, "duplicate_workflow_id")

    assert error.declaration_path == "workflows[1].workflow_id"
    assert error.related_declaration_path == "workflows[0].workflow_id"


def test_workflow_package_manifest_refuses_duplicate_asset_ids() -> None:
    source = _minimal_manifest()
    assets = cast(list[object], source["assets"])
    assets.append(dict(_asset(source)))

    error = _find_error(source, "duplicate_asset_id")

    assert error.declaration_path == "assets[1].asset_id"
    assert error.related_declaration_path == "assets[0].asset_id"


def test_workflow_package_manifest_refuses_dangling_asset_refs() -> None:
    source = _minimal_manifest()
    _workflow(source)["required_assets"] = [
        {"asset_id": "asset.missing", "content_digest": DIGEST}
    ]

    error = _find_error(source, "dangling_asset_reference")

    assert error.declaration_path == "workflows[0].required_assets[0].asset_id"
    assert error.context["asset_id"] == "asset.missing"


def test_workflow_package_manifest_refuses_unknown_required_asset_ref_fields() -> None:
    source = _minimal_manifest()
    required_assets = cast(list[object], _workflow(source)["required_assets"])
    cast(Record, required_assets[0])["unexpected"] = True

    error = _find_error(source, "unknown_manifest_field")

    assert error.declaration_path == "workflows[0].required_assets[0].unexpected"
    assert error.context["field"] == "unexpected"


@pytest.mark.parametrize(
    "package_path",
    ["../outside.md", "/tmp/outside.md", "prompts\\echo.md", "prompts//echo.md"],
)
def test_workflow_package_manifest_refuses_unsafe_asset_package_paths(
    package_path: str,
) -> None:
    source = _minimal_manifest()
    _asset(source)["package_path"] = package_path

    error = _find_error(source, "invalid_asset_package_path")

    assert error.declaration_path == "assets[0].package_path"
    assert error.context["package_path"] == package_path


def test_workflow_package_manifest_refuses_duplicate_normalized_asset_paths() -> None:
    source = _minimal_manifest()
    duplicate_asset = dict(_asset(source))
    duplicate_asset["asset_id"] = "asset.echo_prompt_copy"
    cast(list[object], source["assets"]).append(duplicate_asset)

    error = _find_error(source, "duplicate_asset_package_path")

    assert error.declaration_path == "assets[1].package_path"
    assert error.related_declaration_path == "assets[0].package_path"


@pytest.mark.parametrize(
    ("mutate", "path"),
    [
        (
            lambda source: _asset(source).__setitem__("content_digest", "sha256:ABC"),
            "assets[0].content_digest",
        ),
        (
            lambda source: cast(Record, cast(list[object], _workflow(source)[
                "required_assets"
            ])[0]).__setitem__("content_digest", "sha256:ABC"),
            "workflows[0].required_assets[0].content_digest",
        ),
        (
            lambda source: source.__setitem__(
                "dependencies",
                [
                    {
                        "package_id": "pkg.example.dep",
                        "version_constraint": ">=1",
                        "manifest_digest": "sha256:ABC",
                    }
                ],
            ),
            "dependencies[0].manifest_digest",
        ),
        (
            lambda source: source.__setitem__("manifest_digest", "sha256:ABC"),
            "manifest_digest",
        ),
    ],
)
def test_workflow_package_manifest_refuses_invalid_digest_shapes(
    mutate: object,
    path: str,
) -> None:
    source = _minimal_manifest()
    cast(Any, mutate)(source)

    error = _find_error(source, "invalid_digest")

    assert error.declaration_path == path
    assert error.context["digest"] == "sha256:ABC"


def test_workflow_package_manifest_refuses_unsupported_package_format_version() -> None:
    source = _minimal_manifest()
    cast(Record, source["package"])["package_format_version"] = "99"

    error = _find_error(source, "unsupported_package_format_version")

    assert error.declaration_path == "package.package_format_version"
    assert error.context["package_format_version"] == "99"


def test_workflow_package_manifest_refuses_unsupported_canonicalization() -> None:
    source = _minimal_manifest()
    source["canonicalization"] = {"algorithm": "other-json", "hash": "sha512"}

    error = _find_error(source, "unsupported_manifest_canonicalization")

    assert error.declaration_path == "canonicalization"
    assert error.context["algorithm"] == "other-json"
    assert error.context["hash_algorithm"] == "sha512"


@pytest.mark.parametrize(
    ("section", "path"),
    [
        ("canonicalization", "canonicalization.unexpected"),
        ("compatibility", "compatibility.unexpected"),
    ],
)
def test_workflow_package_manifest_refuses_unknown_authority_record_fields(
    section: str,
    path: str,
) -> None:
    source = _minimal_manifest()
    cast(Record, source[section])["unexpected"] = "unsupported"

    error = _find_error(source, "unknown_manifest_field")

    assert error.declaration_path == path
    assert error.context["field"] == "unexpected"
    assert error.context["owner"] == section


@pytest.mark.parametrize(
    ("section", "field", "path"),
    [
        ("canonicalization", "algorithm", "canonicalization.algorithm"),
        ("canonicalization", "hash", "canonicalization.hash"),
        ("compatibility", "base_millrace", "compatibility.base_millrace"),
    ],
)
def test_workflow_package_manifest_refuses_missing_authority_record_fields(
    section: str,
    field: str,
    path: str,
) -> None:
    source = _minimal_manifest()
    del cast(Record, source[section])[field]

    error = _find_error(source, "missing_manifest_field")

    assert error.declaration_path == path
    assert error.context["field"] == field


@pytest.mark.parametrize(
    ("section", "path"),
    [
        ("canonicalization", "canonicalization.<non_string_key>"),
        ("compatibility", "compatibility.<non_string_key>"),
    ],
)
def test_workflow_package_manifest_refuses_non_string_authority_record_keys(
    section: str,
    path: str,
) -> None:
    source = _minimal_manifest()
    cast(Any, source[section]).__setitem__(("unexpected",), True)

    error = _find_error(source, "invalid_manifest_shape")

    assert error.declaration_path == path
    assert error.context["expected_type"] == "str_key"
    assert error.context["actual_type"] == "tuple"


def test_workflow_package_manifest_refuses_unknown_package_fields() -> None:
    source = _minimal_manifest()
    cast(Record, source["package"])["unexpected"] = True

    error = _find_error(source, "unknown_manifest_field")

    assert error.declaration_path == "package.unexpected"
    assert error.context["owner"] == "package"


def test_workflow_package_manifest_refuses_unknown_workflow_fields() -> None:
    source = _minimal_manifest()
    _workflow(source)["unexpected"] = True

    error = _find_error(source, "unknown_manifest_field")

    assert error.declaration_path == "workflows[0].unexpected"
    assert error.context["owner"] == "workflows[0]"


def test_workflow_package_manifest_refuses_unknown_asset_fields() -> None:
    source = _minimal_manifest()
    _asset(source)["unexpected"] = True

    error = _find_error(source, "unknown_manifest_field")

    assert error.declaration_path == "assets[0].unexpected"
    assert error.context["owner"] == "assets[0]"


def test_workflow_package_manifest_refuses_missing_package_fields() -> None:
    source = _minimal_manifest()
    del cast(Record, source["package"])["package_version"]

    error = _find_error(source, "missing_manifest_field")

    assert error.declaration_path == "package.package_version"
    assert error.context["field"] == "package_version"


def test_workflow_package_manifest_refuses_missing_workflow_fields() -> None:
    source = _minimal_manifest()
    del _workflow(source)["workflow_version"]

    error = _find_error(source, "missing_manifest_field")

    assert error.declaration_path == "workflows[0].workflow_version"
    assert error.context["field"] == "workflow_version"


def test_workflow_package_manifest_refuses_missing_asset_fields() -> None:
    source = _minimal_manifest()
    del _asset(source)["media_type"]

    error = _find_error(source, "missing_manifest_field")

    assert error.declaration_path == "assets[0].media_type"
    assert error.context["field"] == "media_type"


@pytest.mark.parametrize(
    "package_id",
    [
        "pkg.example.cafe\u0301",
        "pkg/example",
        "../pkg.example",
        "pkg..example",
        "https://pkg.example",
        " Pkg.Example ",
    ],
)
def test_workflow_package_manifest_refuses_non_nfc_or_path_like_package_ids(
    package_id: str,
) -> None:
    source = _minimal_manifest()
    cast(Record, source["package"])["package_id"] = package_id

    error = _find_error(source, "invalid_package_id")

    assert error.declaration_path == "package.package_id"
    assert error.context["package_id"] == package_id


@pytest.mark.parametrize(
    "package_id",
    ["millrace.kernel.extra", "millrace.internal.pkg", "millrace.runtime.tooling"],
)
def test_workflow_package_manifest_refuses_reserved_package_id_prefixes(
    package_id: str,
) -> None:
    source = _minimal_manifest()
    cast(Record, source["package"])["package_id"] = package_id

    error = _find_error(source, "reserved_package_id")

    assert error.declaration_path == "package.package_id"
    assert error.context["package_id"] == package_id


def test_workflow_package_manifest_keeps_source_kind_out_of_manifest_authority() -> None:  # noqa: E501
    path_manifest = _minimal_manifest()
    archive_manifest = _minimal_manifest()
    cast(Record, path_manifest["package"])["source_kind"] = "path"
    cast(Record, archive_manifest["package"])["source_kind"] = "archive"

    assert canonical_manifest_bytes(path_manifest) == canonical_manifest_bytes(
        archive_manifest
    )
    assert manifest_digest_for_manifest(path_manifest) == manifest_digest_for_manifest(
        archive_manifest
    )

    reordered = OrderedDict(cast(dict[str, Any], path_manifest))
    reordered.move_to_end("package")
    assert canonical_manifest_bytes(path_manifest) == canonical_manifest_bytes(
        reordered
    )
