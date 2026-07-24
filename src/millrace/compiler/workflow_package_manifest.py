"""Workflow package manifest validation and importable digest checks."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TypeGuard, cast
from unicodedata import normalize

from millrace.compiler.diagnostics import compiler_error
from millrace.contracts import Diagnostic
from millrace.contracts.workflow_package import (
    WORKFLOW_PACKAGE_CANONICALIZATION_ALGORITHM,
    WORKFLOW_PACKAGE_HASH_ALGORITHM,
    WORKFLOW_PACKAGE_MANIFEST_FORMAT_VERSION,
    WORKFLOW_PACKAGE_MANIFEST_RECORD_KIND,
    ManifestValue,
    WorkflowPackageAsset,
    WorkflowPackageAssetRef,
    WorkflowPackageCanonicalization,
    WorkflowPackageCompatibility,
    WorkflowPackageDependency,
    WorkflowPackageIdentity,
    WorkflowPackageManifest,
    WorkflowPackageWorkflow,
    manifest_digest_for_manifest,
)

_ROOT_FIELDS = frozenset(
    (
        "record_kind",
        "manifest_format_version",
        "package",
        "workflows",
        "assets",
        "dependencies",
        "compatibility",
        "canonicalization",
        "manifest_digest",
        "non_authoritative_metadata",
    )
)

_REQUIRED_ROOT_FIELDS = _ROOT_FIELDS
_PACKAGE_FIELDS = frozenset(
    (
        "package_id",
        "package_version",
        "package_format_version",
        "package_role",
        "publisher",
        "base_millrace_compatibility",
        "source_kind",
        "publication_scope",
        "license",
        "repository_url",
        "source_ref",
        "display",
    )
)
_REQUIRED_PACKAGE_FIELDS = frozenset(
    (
        "package_id",
        "package_version",
        "package_format_version",
        "package_role",
        "publisher",
        "base_millrace_compatibility",
    )
)
_WORKFLOW_FIELDS = frozenset(
    (
        "workflow_id",
        "workflow_version",
        "display",
        "visibility",
        "entrypoints",
        "source_refs",
        "selected_authority",
        "required_assets",
        "required_dependencies",
    )
)
_REQUIRED_WORKFLOW_FIELDS = frozenset(
    (
        "workflow_id",
        "workflow_version",
        "visibility",
        "entrypoints",
        "selected_authority",
        "required_assets",
    )
)
_ASSET_FIELDS = frozenset(
    (
        "asset_id",
        "asset_kind",
        "media_type",
        "encoding",
        "content_digest",
        "byte_length",
        "package_path",
        "selection",
        "selected_authority_participation",
    )
)
_REQUIRED_ASSET_FIELDS = _ASSET_FIELDS
_ASSET_REF_FIELDS = frozenset(("asset_id", "content_digest"))
_REQUIRED_ASSET_REF_FIELDS = _ASSET_REF_FIELDS
_DEPENDENCY_FIELDS = frozenset(
    ("package_id", "version_constraint", "manifest_digest")
)
_REQUIRED_DEPENDENCY_FIELDS = frozenset(("package_id", "version_constraint"))
_COMPATIBILITY_FIELDS = frozenset(("base_millrace", "package_contract"))
_REQUIRED_COMPATIBILITY_FIELDS = frozenset(("base_millrace",))
_CANONICALIZATION_FIELDS = frozenset(("algorithm", "hash"))
_REQUIRED_CANONICALIZATION_FIELDS = _CANONICALIZATION_FIELDS
_PACKAGE_ROLE_VALUES = ("workflow_package", "dependency_only")
_DEPENDENCY_ONLY_PACKAGE_ROLE = "dependency_only"

_PACKAGE_SOURCE_KIND_VALUES = ("installed_python_package", "archive", "path")
_PACKAGE_PUBLICATION_SCOPE_VALUES = ("public", "private", "internal", "test")
_WORKFLOW_VISIBILITY_VALUES = ("public", "private", "test_only", "internal")
_ASSET_KIND_VALUES = (
    "entrypoint_prompt",
    "stage_skill",
    "shared_skill",
    "template",
    "schema",
    "example",
    "fixture",
    "blob",
)
_ASSET_ENCODING_VALUES = ("utf-8", "binary")
_ASSET_SELECTION_VALUES = (
    "selectable",
    "required",
    "optional_example",
    "non_authoritative_metadata",
)
_ASSET_AUTHORITY_PARTICIPATION_VALUES = (
    "yes",
    "no",
    "depends_on_workflow_selection",
)

_PACKAGE_ID_PATTERN = re.compile(r"[a-z0-9_-]+(?:\.[a-z0-9_-]+)+")
_SHA256_DIGEST_PATTERN = re.compile(r"sha256:[0-9a-f]{64}")
_RESERVED_PACKAGE_PREFIXES = (
    "millrace.kernel",
    "millrace.internal",
    "millrace.runtime",
)

_HIDDEN_DEFAULT_KEYS = frozenset(
    (
        "hidden_defaults",
        "implicit_defaults",
        "default_authority",
        "package_granted_authority",
    )
)
_PROVIDER_CREDENTIAL_KEY_PARTS = (
    "credential",
    "secret",
    "api_key",
    "access_token",
    "refresh_token",
    "private_key",
)
_NATIVE_RUNNER_KEYS = frozenset(
    (
        "native_runner_implementation",
        "runner_implementation",
        "python_module",
        "entrypoint_module",
    )
)
_PROVIDER_CODE_KEYS = frozenset(
    (
        "provider_code",
        "provider_module",
        "provider_package",
        "provider_code_distribution",
    )
)
_RUNTIME_CODE_KEYS = frozenset(
    (
        "runtime_code_execution",
        "package_code_execution",
        "code_execution",
    )
)
_MARKETPLACE_INSTALL_KEYS = frozenset(
    (
        "marketplace_install",
        "remote_install",
        "remote_package_install",
    )
)
_SUBSTRATE_MUTATION_KEYS = frozenset(
    (
        "undeclared_substrate_mutation",
        "substrate_mutation",
        "registry_mutation",
        "cas_mutation",
        "runtime_state_mutation",
        "workspace_mutation",
    )
)
_PACKAGE_GRANTED_CAPABILITY_KEYS = frozenset(
    (
        "package_granted_capability",
        "package_granted_capabilities",
        "capability_grant",
        "capability_grants",
    )
)
_PACKAGE_GRANTED_APPROVAL_KEYS = frozenset(
    (
        "package_granted_approval",
        "package_granted_approvals",
        "approval_grant",
        "approval_grants",
    )
)


@dataclass(frozen=True, slots=True)
class WorkflowPackageManifestValidationResult:
    manifest: WorkflowPackageManifest | None
    diagnostics: tuple[Diagnostic, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "diagnostics", tuple(self.diagnostics))


def validate_workflow_package_manifest(
    source: Mapping[str, object],
) -> WorkflowPackageManifestValidationResult:
    diagnostics: list[Diagnostic] = []
    _validate_root(source, diagnostics)
    package = _mapping_field(source, "package", diagnostics)
    workflows = _record_sequence_field(source, "workflows", diagnostics)
    assets = _record_sequence_field(source, "assets", diagnostics)
    dependencies = _record_sequence_field(source, "dependencies", diagnostics)
    compatibility = _mapping_field(source, "compatibility", diagnostics)
    canonicalization = _mapping_field(source, "canonicalization", diagnostics)

    _validate_root_field_shapes(source, diagnostics)
    _validate_known_fields(package, _PACKAGE_FIELDS, "package", diagnostics)
    _validate_required_fields(
        package,
        _REQUIRED_PACKAGE_FIELDS,
        "package",
        diagnostics,
    )
    _validate_package_field_shapes(package, diagnostics)
    _validate_manifest_value(package, "package", diagnostics)
    _validate_package_id(package.get("package_id"), diagnostics)
    _validate_supported_package_format(package, diagnostics)
    _validate_known_fields(
        compatibility,
        _COMPATIBILITY_FIELDS,
        "compatibility",
        diagnostics,
    )
    _validate_required_fields(
        compatibility,
        _REQUIRED_COMPATIBILITY_FIELDS,
        "compatibility",
        diagnostics,
    )
    _validate_compatibility_field_shapes(compatibility, diagnostics)
    _validate_known_fields(
        canonicalization,
        _CANONICALIZATION_FIELDS,
        "canonicalization",
        diagnostics,
    )
    _validate_required_fields(
        canonicalization,
        _REQUIRED_CANONICALIZATION_FIELDS,
        "canonicalization",
        diagnostics,
    )
    _validate_canonicalization_field_shapes(canonicalization, diagnostics)
    _validate_supported_canonicalization(canonicalization, diagnostics)
    _validate_manifest_value(compatibility, "compatibility", diagnostics)
    _validate_manifest_value(canonicalization, "canonicalization", diagnostics)
    _validate_manifest_value(
        source.get("non_authoritative_metadata", {}),
        "non_authoritative_metadata",
        diagnostics,
    )
    _validate_workflow_list_role(
        raw_workflows=source.get("workflows"),
        workflows=workflows,
        package=package,
        diagnostics=diagnostics,
    )
    _validate_workflow_records(workflows, diagnostics)
    asset_ids = _validate_asset_records(assets, diagnostics)
    _validate_dependency_records(dependencies, diagnostics)
    _validate_asset_references(workflows, asset_ids, diagnostics)
    _validate_forbidden_authority_claims(workflows, diagnostics)

    if diagnostics:
        return WorkflowPackageManifestValidationResult(
            manifest=None,
            diagnostics=tuple(diagnostics),
        )

    return WorkflowPackageManifestValidationResult(
        manifest=_build_manifest(
            source=source,
            package=package,
            workflows=workflows,
            assets=assets,
            dependencies=dependencies,
            compatibility=compatibility,
            canonicalization=canonicalization,
        ),
        diagnostics=(),
    )


def validate_importable_workflow_package_manifest(
    source: Mapping[str, object],
) -> WorkflowPackageManifestValidationResult:
    result = validate_workflow_package_manifest(source)
    if result.diagnostics:
        return result

    supplied_digest = source.get("manifest_digest")
    if not isinstance(supplied_digest, str) or not supplied_digest:
        return WorkflowPackageManifestValidationResult(
            manifest=None,
            diagnostics=(
                _error(
                    code="missing_manifest_digest",
                    path="manifest_digest",
                    message="Importable workflow package manifests need a digest.",
                    context={},
                    hint="Set manifest_digest to the canonical manifest digest.",
                ),
            ),
        )

    expected_digest = manifest_digest_for_manifest(source)
    if supplied_digest != expected_digest:
        return WorkflowPackageManifestValidationResult(
            manifest=None,
            diagnostics=(
                _error(
                    code="manifest_digest_mismatch",
                    path="manifest_digest",
                    message="Workflow package manifest digest does not match.",
                    context={
                        "expected_digest": expected_digest,
                        "supplied_digest": supplied_digest,
                    },
                    hint="Recompute the digest from canonical manifest authority.",
                ),
            ),
        )

    return result


def _validate_root(
    source: Mapping[str, object],
    diagnostics: list[Diagnostic],
) -> None:
    root_fields = tuple(cast(Mapping[object, object], source))
    for field in sorted(key for key in root_fields if isinstance(key, str)):
        if field not in _ROOT_FIELDS:
            diagnostics.append(
                _unknown_field_error(field=field, path=field, owner="manifest")
            )
    for raw_field in root_fields:
        if isinstance(raw_field, str):
            continue
        diagnostics.append(
            _shape_error(
                path="<non_string_key>",
                expected_type="str_key",
                value=raw_field,
            )
        )

    for field in sorted(_REQUIRED_ROOT_FIELDS):
        if field not in source:
            diagnostics.append(_missing_field_error(field=field, path=field))

    record_kind = source.get("record_kind")
    if record_kind != WORKFLOW_PACKAGE_MANIFEST_RECORD_KIND:
        diagnostics.append(
            _error(
                code="unsupported_manifest_record_kind",
                path="record_kind",
                message="Workflow package manifest has unsupported record kind.",
                context={"record_kind": str(record_kind)},
                hint="Use the workflow package manifest record kind.",
            )
        )

    format_version = source.get("manifest_format_version")
    if format_version != WORKFLOW_PACKAGE_MANIFEST_FORMAT_VERSION:
        diagnostics.append(
            _error(
                code="unsupported_manifest_format_version",
                path="manifest_format_version",
                message="Workflow package manifest format version is unsupported.",
                context={"manifest_format_version": str(format_version)},
                hint="Use manifest_format_version '1'.",
            )
        )


def _validate_root_field_shapes(
    source: Mapping[str, object],
    diagnostics: list[Diagnostic],
) -> None:
    _validate_text_field(source, "record_kind", "record_kind", diagnostics)
    _validate_text_field(
        source,
        "manifest_format_version",
        "manifest_format_version",
        diagnostics,
    )
    _validate_optional_text_field(
        source,
        "manifest_digest",
        "manifest_digest",
        diagnostics,
    )
    _validate_optional_digest_field(
        source,
        "manifest_digest",
        "manifest_digest",
        diagnostics,
    )
    _validate_optional_mapping_field(
        source,
        "non_authoritative_metadata",
        "non_authoritative_metadata",
        diagnostics,
    )


def _validate_known_fields(
    record: Mapping[str, object],
    allowed_fields: frozenset[str],
    path: str,
    diagnostics: list[Diagnostic],
) -> None:
    fields = tuple(cast(Mapping[object, object], record))
    for field in sorted(key for key in fields if isinstance(key, str)):
        if field not in allowed_fields:
            diagnostics.append(
                _unknown_field_error(
                    field=field,
                    path=f"{path}.{field}",
                    owner=path,
                )
            )
    for raw_field in fields:
        if isinstance(raw_field, str):
            continue
        diagnostics.append(
            _shape_error(
                path=f"{path}.<non_string_key>",
                expected_type="str_key",
                value=raw_field,
            )
        )


def _validate_required_fields(
    record: Mapping[str, object],
    required_fields: frozenset[str],
    path: str,
    diagnostics: list[Diagnostic],
) -> None:
    for field in sorted(required_fields):
        if field not in record:
            diagnostics.append(
                _missing_field_error(field=field, path=f"{path}.{field}")
            )


def _validate_package_field_shapes(
    package: Mapping[str, object],
    diagnostics: list[Diagnostic],
) -> None:
    for field in (
        "package_id",
        "package_version",
        "package_format_version",
        "package_role",
        "publisher",
        "base_millrace_compatibility",
        "source_kind",
        "publication_scope",
        "license",
        "repository_url",
        "source_ref",
    ):
        _validate_text_field(package, field, f"package.{field}", diagnostics)
    for field in _REQUIRED_PACKAGE_FIELDS:
        _validate_authority_text_field_whitespace(
            package,
            field,
            f"package.{field}",
            diagnostics,
        )
    _validate_optional_mapping_field(package, "display", "package.display", diagnostics)
    _validate_allowed_text_value(
        package,
        "package_role",
        "package.package_role",
        _PACKAGE_ROLE_VALUES,
        diagnostics,
    )
    _validate_optional_allowed_text_value(
        package,
        "source_kind",
        "package.source_kind",
        _PACKAGE_SOURCE_KIND_VALUES,
        diagnostics,
    )
    _validate_optional_allowed_text_value(
        package,
        "publication_scope",
        "package.publication_scope",
        _PACKAGE_PUBLICATION_SCOPE_VALUES,
        diagnostics,
    )


def _validate_compatibility_field_shapes(
    compatibility: Mapping[str, object],
    diagnostics: list[Diagnostic],
) -> None:
    for field in ("base_millrace", "package_contract"):
        _validate_text_field(
            compatibility,
            field,
            f"compatibility.{field}",
            diagnostics,
        )
        _validate_authority_text_field_whitespace(
            compatibility,
            field,
            f"compatibility.{field}",
            diagnostics,
        )


def _validate_canonicalization_field_shapes(
    canonicalization: Mapping[str, object],
    diagnostics: list[Diagnostic],
) -> None:
    for field in ("algorithm", "hash"):
        _validate_text_field(
            canonicalization,
            field,
            f"canonicalization.{field}",
            diagnostics,
        )
        _validate_authority_text_field_whitespace(
            canonicalization,
            field,
            f"canonicalization.{field}",
            diagnostics,
        )


def _validate_package_id(
    raw_package_id: object,
    diagnostics: list[Diagnostic],
) -> None:
    if not isinstance(raw_package_id, str):
        diagnostics.append(
            _invalid_package_id_error(package_id=str(raw_package_id))
        )
        return

    package_id = raw_package_id
    is_nfc = package_id == normalize("NFC", package_id)
    is_valid = (
        is_nfc
        and package_id.strip() == package_id
        and 3 <= len(package_id.encode("utf-8")) <= 128
        and "/" not in package_id
        and "\\" not in package_id
        and ".." not in package_id
        and "://" not in package_id
        and _PACKAGE_ID_PATTERN.fullmatch(package_id) is not None
    )
    if not is_valid:
        diagnostics.append(_invalid_package_id_error(package_id=package_id))
        return

    if any(
        package_id == prefix or package_id.startswith(f"{prefix}.")
        for prefix in _RESERVED_PACKAGE_PREFIXES
    ):
        diagnostics.append(
            _error(
                code="reserved_package_id",
                path="package.package_id",
                message="Workflow package ID uses a reserved prefix.",
                context={"package_id": package_id},
                hint="Use an external package namespace.",
            )
        )


def _validate_supported_package_format(
    package: Mapping[str, object],
    diagnostics: list[Diagnostic],
) -> None:
    package_format = package.get("package_format_version")
    if package_format == WORKFLOW_PACKAGE_MANIFEST_FORMAT_VERSION:
        return
    diagnostics.append(
        _error(
            code="unsupported_package_format_version",
            path="package.package_format_version",
            message="Workflow package format version is unsupported.",
            context={"package_format_version": str(package_format)},
            hint="Use package_format_version '1'.",
        )
    )


def _validate_supported_canonicalization(
    canonicalization: Mapping[str, object],
    diagnostics: list[Diagnostic],
) -> None:
    algorithm = canonicalization.get("algorithm")
    hash_algorithm = canonicalization.get("hash")
    if (
        algorithm == WORKFLOW_PACKAGE_CANONICALIZATION_ALGORITHM
        and hash_algorithm == WORKFLOW_PACKAGE_HASH_ALGORITHM
    ):
        return
    diagnostics.append(
        _error(
            code="unsupported_manifest_canonicalization",
            path="canonicalization",
            message="Workflow package manifest canonicalization is unsupported.",
            context={
                "algorithm": str(algorithm),
                "hash_algorithm": str(hash_algorithm),
            },
            hint="Use millrace-json-v1 with sha256.",
        )
        )


def _validate_workflow_list_role(
    *,
    raw_workflows: object,
    workflows: tuple[Mapping[str, object], ...],
    package: Mapping[str, object],
    diagnostics: list[Diagnostic],
) -> None:
    if not _is_sequence(raw_workflows) or workflows:
        return
    package_role = package.get("package_role")
    if package_role == _DEPENDENCY_ONLY_PACKAGE_ROLE:
        return
    diagnostics.append(
        _error(
            code="empty_workflow_package",
            path="workflows",
            message="Workflow package manifest has no workflow entries.",
            context={"package_role": str(package_role)},
            hint="Declare workflows or set package_role to dependency_only.",
        )
    )


def _validate_workflow_records(
    workflows: tuple[Mapping[str, object], ...],
    diagnostics: list[Diagnostic],
) -> None:
    paths_by_id: dict[str, str] = {}
    for index, workflow in enumerate(workflows):
        record_path = f"workflows[{index}]"
        _validate_known_fields(workflow, _WORKFLOW_FIELDS, record_path, diagnostics)
        _validate_required_fields(
            workflow,
            _REQUIRED_WORKFLOW_FIELDS,
            record_path,
            diagnostics,
        )
        _validate_workflow_field_shapes(workflow, record_path, diagnostics)
        _validate_manifest_value(workflow, record_path, diagnostics)
        workflow_id = workflow.get("workflow_id")
        if not isinstance(workflow_id, str) or not workflow_id:
            diagnostics.append(
                _missing_field_error(
                    field="workflow_id",
                    path=f"{record_path}.workflow_id",
                )
            )
            continue
        if workflow_id in paths_by_id:
            diagnostics.append(
                _error(
                    code="duplicate_workflow_id",
                    path=f"{record_path}.workflow_id",
                    related_declaration_path=paths_by_id[workflow_id],
                    message="Workflow package manifest repeats a workflow ID.",
                    context={"workflow_id": workflow_id},
                    hint="Use each workflow ID once per package manifest.",
                )
            )
            continue
        paths_by_id[workflow_id] = f"{record_path}.workflow_id"


def _validate_workflow_field_shapes(
    workflow: Mapping[str, object],
    record_path: str,
    diagnostics: list[Diagnostic],
) -> None:
    for field in ("workflow_id", "workflow_version", "visibility"):
        _validate_text_field(workflow, field, f"{record_path}.{field}", diagnostics)
        _validate_authority_text_field_whitespace(
            workflow,
            field,
            f"{record_path}.{field}",
            diagnostics,
        )
    _validate_allowed_text_value(
        workflow,
        "visibility",
        f"{record_path}.visibility",
        _WORKFLOW_VISIBILITY_VALUES,
        diagnostics,
    )
    _validate_optional_mapping_field(
        workflow,
        "display",
        f"{record_path}.display",
        diagnostics,
    )
    _validate_text_sequence_field(
        workflow,
        "entrypoints",
        f"{record_path}.entrypoints",
        diagnostics,
    )
    _validate_authority_text_sequence_whitespace(
        workflow,
        "entrypoints",
        f"{record_path}.entrypoints",
        diagnostics,
    )
    _validate_text_sequence_field(
        workflow,
        "source_refs",
        f"{record_path}.source_refs",
        diagnostics,
    )
    _validate_mapping_field(
        workflow,
        "selected_authority",
        f"{record_path}.selected_authority",
        diagnostics,
    )
    selected_authority = workflow.get("selected_authority")
    if isinstance(selected_authority, Mapping):
        _validate_authority_manifest_string_whitespace(
            selected_authority,
            f"{record_path}.selected_authority",
            diagnostics,
        )
    _validate_text_sequence_field(
        workflow,
        "required_dependencies",
        f"{record_path}.required_dependencies",
        diagnostics,
    )
    _validate_authority_text_sequence_whitespace(
        workflow,
        "required_dependencies",
        f"{record_path}.required_dependencies",
        diagnostics,
    )
    _validate_required_asset_ref_records(
        workflow.get("required_assets"),
        f"{record_path}.required_assets",
        diagnostics,
    )


def _validate_asset_records(
    assets: tuple[Mapping[str, object], ...],
    diagnostics: list[Diagnostic],
) -> frozenset[str]:
    paths_by_id: dict[str, str] = {}
    paths_by_package_path: dict[str, str] = {}
    asset_ids: set[str] = set()
    for index, asset in enumerate(assets):
        record_path = f"assets[{index}]"
        _validate_known_fields(asset, _ASSET_FIELDS, record_path, diagnostics)
        _validate_required_fields(
            asset,
            _REQUIRED_ASSET_FIELDS,
            record_path,
            diagnostics,
        )
        _validate_asset_field_shapes(asset, record_path, diagnostics)
        _validate_manifest_value(asset, record_path, diagnostics)
        _validate_asset_digest_fields(asset, record_path, diagnostics)
        normalized_path = _validate_asset_package_path(
            asset.get("package_path"),
            record_path,
            diagnostics,
        )
        if normalized_path is not None and normalized_path in paths_by_package_path:
            diagnostics.append(
                _error(
                    code="duplicate_asset_package_path",
                    path=f"{record_path}.package_path",
                    related_declaration_path=paths_by_package_path[normalized_path],
                    message="Workflow package manifest repeats an asset path.",
                    context={"package_path": normalized_path},
                    hint="Use one package-relative path per asset.",
                )
            )
        asset_id = asset.get("asset_id")
        if not isinstance(asset_id, str) or not asset_id:
            diagnostics.append(
                _missing_field_error(field="asset_id", path=f"{record_path}.asset_id")
            )
            continue
        if asset_id in paths_by_id:
            diagnostics.append(
                _error(
                    code="duplicate_asset_id",
                    path=f"{record_path}.asset_id",
                    related_declaration_path=paths_by_id[asset_id],
                    message="Workflow package manifest repeats an asset ID.",
                    context={"asset_id": asset_id},
                    hint="Use each asset ID once per package manifest.",
                )
            )
            continue
        paths_by_id[asset_id] = f"{record_path}.asset_id"
        if normalized_path is not None:
            paths_by_package_path[normalized_path] = f"{record_path}.package_path"
        asset_ids.add(asset_id)
    return frozenset(asset_ids)


def _validate_asset_field_shapes(
    asset: Mapping[str, object],
    record_path: str,
    diagnostics: list[Diagnostic],
) -> None:
    for field in (
        "asset_id",
        "asset_kind",
        "media_type",
        "encoding",
        "content_digest",
        "package_path",
        "selection",
        "selected_authority_participation",
    ):
        _validate_text_field(asset, field, f"{record_path}.{field}", diagnostics)
        _validate_authority_text_field_whitespace(
            asset,
            field,
            f"{record_path}.{field}",
            diagnostics,
        )
    _validate_int_field(asset, "byte_length", f"{record_path}.byte_length", diagnostics)
    byte_length = asset.get("byte_length")
    if type(byte_length) is int and byte_length < 0:
        diagnostics.append(
            _error(
                code="invalid_asset_byte_length",
                path=f"{record_path}.byte_length",
                message="Workflow package asset byte length cannot be negative.",
                context={"byte_length": byte_length},
                hint="Use the exact non-negative byte length for the asset.",
            )
        )
    _validate_allowed_text_value(
        asset,
        "asset_kind",
        f"{record_path}.asset_kind",
        _ASSET_KIND_VALUES,
        diagnostics,
    )
    _validate_allowed_text_value(
        asset,
        "encoding",
        f"{record_path}.encoding",
        _ASSET_ENCODING_VALUES,
        diagnostics,
    )
    _validate_allowed_text_value(
        asset,
        "selection",
        f"{record_path}.selection",
        _ASSET_SELECTION_VALUES,
        diagnostics,
    )
    _validate_allowed_text_value(
        asset,
        "selected_authority_participation",
        f"{record_path}.selected_authority_participation",
        _ASSET_AUTHORITY_PARTICIPATION_VALUES,
        diagnostics,
    )


def _validate_asset_digest_fields(
    asset: Mapping[str, object],
    record_path: str,
    diagnostics: list[Diagnostic],
) -> None:
    _validate_digest_field(
        asset,
        "content_digest",
        f"{record_path}.content_digest",
        diagnostics,
    )


def _validate_asset_package_path(
    raw_package_path: object,
    record_path: str,
    diagnostics: list[Diagnostic],
) -> str | None:
    if not isinstance(raw_package_path, str):
        return None
    package_path = raw_package_path
    normalized = _normalized_asset_package_path(package_path)
    if normalized is not None:
        return normalized
    diagnostics.append(
        _error(
            code="invalid_asset_package_path",
            path=f"{record_path}.package_path",
            message="Workflow package asset path is not contained in the package.",
            context={"package_path": package_path},
            hint="Use a relative POSIX package path without empty or parent segments.",
        )
    )
    return None


def _normalized_asset_package_path(package_path: str) -> str | None:
    if not package_path or package_path.startswith("/") or "\\" in package_path:
        return None
    parts = package_path.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        return None
    if ":" in parts[0]:
        return None
    return "/".join(parts)


def _validate_dependency_records(
    dependencies: tuple[Mapping[str, object], ...],
    diagnostics: list[Diagnostic],
) -> None:
    for index, dependency in enumerate(dependencies):
        _validate_known_fields(
            dependency,
            _DEPENDENCY_FIELDS,
            f"dependencies[{index}]",
            diagnostics,
        )
        _validate_required_fields(
            dependency,
            _REQUIRED_DEPENDENCY_FIELDS,
            f"dependencies[{index}]",
            diagnostics,
        )
        _validate_text_field(
            dependency,
            "package_id",
            f"dependencies[{index}].package_id",
            diagnostics,
        )
        _validate_authority_text_field_whitespace(
            dependency,
            "package_id",
            f"dependencies[{index}].package_id",
            diagnostics,
        )
        _validate_text_field(
            dependency,
            "version_constraint",
            f"dependencies[{index}].version_constraint",
            diagnostics,
        )
        _validate_authority_text_field_whitespace(
            dependency,
            "version_constraint",
            f"dependencies[{index}].version_constraint",
            diagnostics,
        )
        _validate_optional_text_field(
            dependency,
            "manifest_digest",
            f"dependencies[{index}].manifest_digest",
            diagnostics,
        )
        _validate_authority_text_field_whitespace(
            dependency,
            "manifest_digest",
            f"dependencies[{index}].manifest_digest",
            diagnostics,
        )
        _validate_optional_digest_field(
            dependency,
            "manifest_digest",
            f"dependencies[{index}].manifest_digest",
            diagnostics,
        )
        _validate_manifest_value(dependency, f"dependencies[{index}]", diagnostics)


def _validate_required_asset_ref_records(
    raw_required_assets: object,
    path: str,
    diagnostics: list[Diagnostic],
) -> None:
    if not _validate_sequence_value(raw_required_assets, path, diagnostics):
        return
    for index, asset_ref in enumerate(cast(Sequence[object], raw_required_assets)):
        asset_ref_path = f"{path}[{index}]"
        if not isinstance(asset_ref, Mapping):
            diagnostics.append(
                _shape_error(
                    path=asset_ref_path,
                    expected_type="mapping",
                    value=asset_ref,
                )
            )
            continue
        asset_ref_mapping = cast(Mapping[str, object], asset_ref)
        _validate_known_fields(
            asset_ref_mapping,
            _ASSET_REF_FIELDS,
            asset_ref_path,
            diagnostics,
        )
        _validate_required_fields(
            asset_ref_mapping,
            _REQUIRED_ASSET_REF_FIELDS,
            asset_ref_path,
            diagnostics,
        )
        _validate_text_field(
            asset_ref_mapping,
            "asset_id",
            f"{asset_ref_path}.asset_id",
            diagnostics,
        )
        _validate_authority_text_field_whitespace(
            asset_ref_mapping,
            "asset_id",
            f"{asset_ref_path}.asset_id",
            diagnostics,
        )
        _validate_text_field(
            asset_ref_mapping,
            "content_digest",
            f"{asset_ref_path}.content_digest",
            diagnostics,
        )
        _validate_authority_text_field_whitespace(
            asset_ref_mapping,
            "content_digest",
            f"{asset_ref_path}.content_digest",
            diagnostics,
        )
        _validate_digest_field(
            asset_ref_mapping,
            "content_digest",
            f"{asset_ref_path}.content_digest",
            diagnostics,
        )


def _validate_asset_references(
    workflows: tuple[Mapping[str, object], ...],
    asset_ids: frozenset[str],
    diagnostics: list[Diagnostic],
) -> None:
    for workflow_index, workflow in enumerate(workflows):
        raw_required_assets = workflow.get("required_assets")
        if not _is_sequence(raw_required_assets):
            continue
        for asset_index, asset_ref in enumerate(raw_required_assets):
            if not isinstance(asset_ref, Mapping):
                continue
            asset_id = _asset_ref_id(asset_ref)
            if asset_id in asset_ids:
                continue
            diagnostics.append(
                _error(
                    code="dangling_asset_reference",
                    path=(
                        f"workflows[{workflow_index}]"
                        f".required_assets[{asset_index}].asset_id"
                    ),
                    message="Workflow package manifest references an undeclared asset.",
                    context={"asset_id": asset_id},
                    hint="Declare the referenced asset or remove the reference.",
                )
            )


def _validate_text_field(
    record: Mapping[str, object],
    field: str,
    path: str,
    diagnostics: list[Diagnostic],
) -> None:
    if field not in record:
        return
    if not isinstance(record[field], str):
        diagnostics.append(
            _shape_error(path=path, expected_type="str", value=record[field])
        )


def _validate_optional_text_field(
    record: Mapping[str, object],
    field: str,
    path: str,
    diagnostics: list[Diagnostic],
) -> None:
    if field not in record or record[field] is None:
        return
    _validate_text_field(record, field, path, diagnostics)


def _validate_int_field(
    record: Mapping[str, object],
    field: str,
    path: str,
    diagnostics: list[Diagnostic],
) -> None:
    if field not in record:
        return
    if type(record[field]) is not int:
        diagnostics.append(
            _shape_error(path=path, expected_type="int", value=record[field])
        )


def _validate_mapping_field(
    record: Mapping[str, object],
    field: str,
    path: str,
    diagnostics: list[Diagnostic],
) -> None:
    if field not in record:
        return
    if not isinstance(record[field], Mapping):
        diagnostics.append(
            _shape_error(path=path, expected_type="mapping", value=record[field])
        )


def _validate_optional_mapping_field(
    record: Mapping[str, object],
    field: str,
    path: str,
    diagnostics: list[Diagnostic],
) -> None:
    if field not in record or record[field] is None:
        return
    _validate_mapping_field(record, field, path, diagnostics)


def _validate_text_sequence_field(
    record: Mapping[str, object],
    field: str,
    path: str,
    diagnostics: list[Diagnostic],
) -> None:
    if field not in record:
        return
    value = record[field]
    if not _validate_sequence_value(value, path, diagnostics):
        return
    for index, item in enumerate(cast(Sequence[object], value)):
        if not isinstance(item, str):
            diagnostics.append(
                _shape_error(
                    path=f"{path}[{index}]",
                    expected_type="str",
                    value=item,
                )
            )


def _validate_sequence_value(
    value: object,
    path: str,
    diagnostics: list[Diagnostic],
) -> bool:
    if _is_sequence(value):
        return True
    diagnostics.append(_shape_error(path=path, expected_type="sequence", value=value))
    return False


def _validate_allowed_text_value(
    record: Mapping[str, object],
    field: str,
    path: str,
    allowed_values: tuple[str, ...],
    diagnostics: list[Diagnostic],
) -> None:
    if field not in record:
        return
    value = record[field]
    if not isinstance(value, str) or value in allowed_values:
        return
    diagnostics.append(
        _invalid_allowed_value_error(
            path=path,
            value=value,
            allowed_values=allowed_values,
        )
    )


def _validate_optional_allowed_text_value(
    record: Mapping[str, object],
    field: str,
    path: str,
    allowed_values: tuple[str, ...],
    diagnostics: list[Diagnostic],
) -> None:
    if field not in record or record[field] is None:
        return
    _validate_allowed_text_value(record, field, path, allowed_values, diagnostics)


def _validate_authority_text_field_whitespace(
    record: Mapping[str, object],
    field: str,
    path: str,
    diagnostics: list[Diagnostic],
) -> None:
    value = record.get(field)
    if isinstance(value, str):
        _validate_authority_string_whitespace(value, path, diagnostics)


def _validate_authority_text_sequence_whitespace(
    record: Mapping[str, object],
    field: str,
    path: str,
    diagnostics: list[Diagnostic],
) -> None:
    value = record.get(field)
    if not _is_sequence(value):
        return
    for index, item in enumerate(value):
        if isinstance(item, str):
            _validate_authority_string_whitespace(
                item,
                f"{path}[{index}]",
                diagnostics,
            )


def _validate_authority_manifest_string_whitespace(
    value: object,
    path: str,
    diagnostics: list[Diagnostic],
) -> None:
    if isinstance(value, str):
        _validate_authority_string_whitespace(value, path, diagnostics)
        return
    if isinstance(value, Mapping):
        for key, nested_value in value.items():
            if not isinstance(key, str):
                continue
            nested_path = f"{path}.{key}"
            _validate_authority_string_whitespace(key, nested_path, diagnostics)
            _validate_authority_manifest_string_whitespace(
                nested_value,
                nested_path,
                diagnostics,
            )
        return
    if _is_sequence(value):
        for index, item in enumerate(value):
            _validate_authority_manifest_string_whitespace(
                item,
                f"{path}[{index}]",
                diagnostics,
            )


def _validate_authority_string_whitespace(
    value: str,
    path: str,
    diagnostics: list[Diagnostic],
) -> None:
    stripped_value = value.strip()
    if value == stripped_value:
        return
    diagnostics.append(
        _error(
            code="invalid_manifest_string_whitespace",
            path=path,
            message="Workflow package manifest authority string has whitespace.",
            context={"value": value, "stripped_value": stripped_value},
            hint="Remove leading or trailing whitespace from authority strings.",
        )
    )


def _validate_manifest_value(
    value: object,
    path: str,
    diagnostics: list[Diagnostic],
) -> None:
    if value is None or isinstance(value, bool) or type(value) is int:
        return
    if isinstance(value, str):
        _validate_nfc_string(value, path, diagnostics)
        return
    if isinstance(value, Mapping):
        for key, nested_value in value.items():
            if not isinstance(key, str):
                diagnostics.append(
                    _invalid_manifest_value_error(
                        path=f"{path}.<non_string_key>",
                        expected_type="str_key",
                        value=key,
                    )
                )
                continue
            _validate_nfc_string(key, f"{path}.{key}", diagnostics)
            _validate_manifest_value(nested_value, f"{path}.{key}", diagnostics)
        return
    if _is_sequence(value):
        for index, item in enumerate(value):
            _validate_manifest_value(item, f"{path}[{index}]", diagnostics)
        return
    diagnostics.append(
        _invalid_manifest_value_error(
            path=path,
            expected_type="manifest_value",
            value=value,
        )
    )


def _validate_nfc_string(
    value: str,
    path: str,
    diagnostics: list[Diagnostic],
) -> None:
    value_nfc = normalize("NFC", value)
    if value == value_nfc:
        return
    diagnostics.append(
        _error(
            code="non_nfc_manifest_string",
            path=path,
            message="Workflow package manifest string is not Unicode NFC.",
            context={"value": value, "value_nfc": value_nfc},
            hint="Use already-NFC strings; validation refuses silent normalization.",
        )
    )


def _validate_digest_field(
    record: Mapping[str, object],
    field: str,
    path: str,
    diagnostics: list[Diagnostic],
) -> None:
    if field not in record:
        return
    value = record[field]
    if isinstance(value, str) and _SHA256_DIGEST_PATTERN.fullmatch(value):
        return
    diagnostics.append(_invalid_digest_error(path=path, value=value))


def _validate_optional_digest_field(
    record: Mapping[str, object],
    field: str,
    path: str,
    diagnostics: list[Diagnostic],
) -> None:
    if field not in record or record[field] is None:
        return
    _validate_digest_field(record, field, path, diagnostics)


def _validate_forbidden_authority_claims(
    workflows: tuple[Mapping[str, object], ...],
    diagnostics: list[Diagnostic],
) -> None:
    for index, workflow in enumerate(workflows):
        selected_authority = workflow.get("selected_authority")
        if not isinstance(selected_authority, Mapping):
            continue
        _scan_forbidden_claims(
            selected_authority,
            path=f"workflows[{index}].selected_authority",
            diagnostics=diagnostics,
        )


def _scan_forbidden_claims(
    value: object,
    *,
    path: str,
    diagnostics: list[Diagnostic],
) -> None:
    if isinstance(value, Mapping):
        for key, nested_value in value.items():
            key_text = str(key)
            nested_path = f"{path}.{key_text}"
            _diagnose_forbidden_key(key_text, nested_path, diagnostics)
            _scan_forbidden_claims(
                nested_value,
                path=nested_path,
                diagnostics=diagnostics,
            )
    elif _is_sequence(value):
        for index, item in enumerate(value):
            _scan_forbidden_claims(
                item,
                path=f"{path}[{index}]",
                diagnostics=diagnostics,
            )


def _diagnose_forbidden_key(
    key: str,
    path: str,
    diagnostics: list[Diagnostic],
) -> None:
    normalized_key = key.lower()
    if normalized_key in _HIDDEN_DEFAULT_KEYS:
        diagnostics.append(
            _error(
                code="hidden_default_authority",
                path=path,
                message="Workflow package manifest declares hidden default authority.",
                context={"field": key},
                hint="Declare selected authority explicitly in workflow data.",
            )
        )
    if any(part in normalized_key for part in _PROVIDER_CREDENTIAL_KEY_PARTS):
        diagnostics.append(
            _error(
                code="provider_credentials",
                path=path,
                message="Workflow package manifest contains provider credentials.",
                context={"field": key},
                hint="Keep credentials outside workflow package manifests.",
            )
        )
    if normalized_key in _NATIVE_RUNNER_KEYS:
        diagnostics.append(
            _error(
                code="native_runner_implementation",
                path=path,
                message="Workflow package manifest distributes native runner code.",
                context={"field": key},
                hint="Declare runner bindings as data without bundled code.",
            )
        )
    if "plugin" in normalized_key:
        diagnostics.append(
            _error(
                code="plugin_execution_claim",
                path=path,
                message="Workflow package manifest claims plugin execution.",
                context={"field": key},
                hint="Keep plugin execution outside workflow package authority.",
            )
        )
    if "mcp" in normalized_key:
        diagnostics.append(
            _error(
                code="mcp_execution_claim",
                path=path,
                message="Workflow package manifest claims MCP execution.",
                context={"field": key},
                hint="Keep MCP execution outside workflow package authority.",
            )
        )
    if normalized_key in _PROVIDER_CODE_KEYS:
        diagnostics.append(
            _error(
                code="provider_code_distribution",
                path=path,
                message="Workflow package manifest distributes provider code.",
                context={"field": key},
                hint="Declare provider refs as data without distributing code.",
            )
        )
    if normalized_key in _RUNTIME_CODE_KEYS:
        diagnostics.append(
            _error(
                code="runtime_code_execution_claim",
                path=path,
                message="Workflow package manifest claims runtime code execution.",
                context={"field": key},
                hint="Keep runtime code execution outside package authority.",
            )
        )
    if normalized_key in _MARKETPLACE_INSTALL_KEYS:
        diagnostics.append(
            _error(
                code="marketplace_install_claim",
                path=path,
                message="Workflow package manifest claims package install behavior.",
                context={"field": key},
                hint=(
                    "Workflow packages cannot declare marketplace or remote "
                    "installation behavior."
                ),
            )
        )
    if normalized_key in _SUBSTRATE_MUTATION_KEYS:
        diagnostics.append(
            _error(
                code="substrate_mutation_claim",
                path=path,
                message="Workflow package manifest claims substrate mutation.",
                context={"field": key},
                hint="Declare substrate mutation only through supported operations.",
            )
        )
    if normalized_key in _PACKAGE_GRANTED_CAPABILITY_KEYS:
        diagnostics.append(
            _error(
                code="package_granted_capability",
                path=path,
                message="Workflow package manifest claims capability grants.",
                context={"field": key},
                hint=(
                    "Packages may reference capability policy data, not grant "
                    "capability."
                ),
            )
        )
    if normalized_key in _PACKAGE_GRANTED_APPROVAL_KEYS:
        diagnostics.append(
            _error(
                code="package_granted_approval",
                path=path,
                message="Workflow package manifest claims approval grants.",
                context={"field": key},
                hint="Packages may reference approval policy data, not grant approval.",
            )
        )


def _build_manifest(
    *,
    source: Mapping[str, object],
    package: Mapping[str, object],
    workflows: tuple[Mapping[str, object], ...],
    assets: tuple[Mapping[str, object], ...],
    dependencies: tuple[Mapping[str, object], ...],
    compatibility: Mapping[str, object],
    canonicalization: Mapping[str, object],
) -> WorkflowPackageManifest:
    return WorkflowPackageManifest(
        package=WorkflowPackageIdentity(
            package_id=_required_text(package, "package_id"),
            package_version=_required_text(package, "package_version"),
            package_format_version=_required_text(package, "package_format_version"),
            package_role=_required_text(package, "package_role"),
            publisher=_required_text(package, "publisher"),
            base_millrace_compatibility=_required_text(
                package,
                "base_millrace_compatibility",
            ),
            source_kind=_optional_text(package.get("source_kind")),
            publication_scope=_optional_text(package.get("publication_scope")),
            license=_optional_text(package.get("license")),
            repository_url=_optional_text(package.get("repository_url")),
            source_ref=_optional_text(package.get("source_ref")),
            display=_manifest_mapping(package.get("display")),
        ),
        workflows=tuple(_build_workflow(record) for record in workflows),
        assets=tuple(_build_asset(record) for record in assets),
        dependencies=tuple(_build_dependency(record) for record in dependencies),
        compatibility=WorkflowPackageCompatibility(
            requirements=cast(Mapping[str, ManifestValue], compatibility)
        ),
        canonicalization=WorkflowPackageCanonicalization(
            algorithm=_required_text(canonicalization, "algorithm"),
            hash_algorithm=_required_text(canonicalization, "hash"),
        ),
        manifest_format_version=_required_text(source, "manifest_format_version"),
        manifest_digest=_optional_text(source.get("manifest_digest")),
        non_authoritative_metadata=_manifest_mapping(
            source.get("non_authoritative_metadata")
        ),
    )


def _build_workflow(record: Mapping[str, object]) -> WorkflowPackageWorkflow:
    return WorkflowPackageWorkflow(
        workflow_id=_required_text(record, "workflow_id"),
        workflow_version=_required_text(record, "workflow_version"),
        visibility=_required_text(record, "visibility"),
        entrypoints=_text_tuple(record.get("entrypoints")),
        selected_authority=_manifest_mapping(record.get("selected_authority")),
        required_assets=tuple(
            _build_asset_ref(asset_ref)
            for asset_ref in _sequence(record.get("required_assets"))
        ),
        display=_manifest_mapping(record.get("display")),
        source_refs=_text_tuple(record.get("source_refs")),
        required_dependencies=_text_tuple(record.get("required_dependencies")),
    )


def _build_asset_ref(value: object) -> WorkflowPackageAssetRef:
    if isinstance(value, Mapping):
        return WorkflowPackageAssetRef(
            asset_id=_required_text(value, "asset_id"),
            content_digest=_optional_text(value.get("content_digest")),
        )
    return WorkflowPackageAssetRef(asset_id=str(value))


def _build_asset(record: Mapping[str, object]) -> WorkflowPackageAsset:
    return WorkflowPackageAsset(
        asset_id=_required_text(record, "asset_id"),
        asset_kind=_required_text(record, "asset_kind"),
        media_type=_required_text(record, "media_type"),
        encoding=_required_text(record, "encoding"),
        content_digest=_required_text(record, "content_digest"),
        byte_length=_required_int(record, "byte_length"),
        package_path=_required_text(record, "package_path"),
        selection=_required_text(record, "selection"),
        selected_authority_participation=_required_text(
            record,
            "selected_authority_participation",
        ),
    )


def _build_dependency(record: Mapping[str, object]) -> WorkflowPackageDependency:
    return WorkflowPackageDependency(
        package_id=_required_text(record, "package_id"),
        version_constraint=_required_text(record, "version_constraint"),
        manifest_digest=_optional_text(record.get("manifest_digest")),
    )


def _asset_ref_id(asset_ref: object) -> str:
    if isinstance(asset_ref, Mapping):
        raw_asset_id = asset_ref.get("asset_id")
        return raw_asset_id if isinstance(raw_asset_id, str) else str(raw_asset_id)
    return str(asset_ref)


def _mapping_field(
    source: Mapping[str, object],
    field: str,
    diagnostics: list[Diagnostic],
) -> Mapping[str, object]:
    if field not in source:
        return {}
    value = source[field]
    if isinstance(value, Mapping):
        return cast(Mapping[str, object], value)
    diagnostics.append(_shape_error(path=field, expected_type="mapping", value=value))
    return {}


def _record_sequence_field(
    source: Mapping[str, object],
    field: str,
    diagnostics: list[Diagnostic],
) -> tuple[Mapping[str, object], ...]:
    if field not in source:
        return ()
    value = source[field]
    if not _is_sequence(value):
        diagnostics.append(
            _shape_error(path=field, expected_type="sequence", value=value)
        )
        return ()

    records: list[Mapping[str, object]] = []
    for index, item in enumerate(value):
        if isinstance(item, Mapping):
            records.append(cast(Mapping[str, object], item))
            continue
        diagnostics.append(
            _shape_error(
                path=f"{field}[{index}]",
                expected_type="mapping",
                value=item,
            )
        )
    return tuple(records)


def _mapping(value: object) -> Mapping[str, object]:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return cast(Mapping[str, object], value)
    raise AssertionError("manifest mapping field should be validated before build")


def _manifest_mapping(value: object) -> Mapping[str, ManifestValue]:
    return cast(Mapping[str, ManifestValue], _mapping(value))


def _sequence(value: object) -> tuple[object, ...]:
    if value is None:
        return ()
    if not _is_sequence(value):
        raise AssertionError("manifest sequence field should be validated before build")
    return tuple(value)


def _is_sequence(value: object) -> TypeGuard[Sequence[object]]:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes))


def _text_tuple(value: object) -> tuple[str, ...]:
    items = _sequence(value)
    if not all(isinstance(item, str) for item in items):
        raise AssertionError("manifest text sequence should be validated before build")
    return tuple(cast(tuple[str, ...], items))


def _required_text(record: Mapping[str, object], field: str) -> str:
    value = record[field]
    if not isinstance(value, str):
        raise AssertionError("manifest text field should be validated before build")
    return value


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise AssertionError("manifest text field should be validated before build")
    return value


def _required_int(record: Mapping[str, object], field: str) -> int:
    value = record[field]
    if type(value) is int:
        return value
    raise AssertionError("manifest int field should be validated before build")


def _invalid_package_id_error(package_id: str) -> Diagnostic:
    return _error(
        code="invalid_package_id",
        path="package.package_id",
        message="Workflow package ID is not a valid opaque package ID.",
        context={"package_id": package_id},
        hint=(
            "Use lowercase dotted package ID segments without whitespace, path "
            "syntax, URL syntax, empty segments, or non-NFC characters."
        ),
    )


def _unknown_field_error(*, field: str, path: str, owner: str) -> Diagnostic:
    return _error(
        code="unknown_manifest_field",
        path=path,
        message="Workflow package manifest contains an unknown field.",
        context={"field": field, "owner": owner},
        hint="Remove the field or place non-authoritative notes under metadata.",
    )


def _missing_field_error(*, field: str, path: str) -> Diagnostic:
    return _error(
        code="missing_manifest_field",
        path=path,
        message="Workflow package manifest is missing a required field.",
        context={"field": field},
        hint="Provide the required workflow package manifest field.",
    )


def _shape_error(*, path: str, expected_type: str, value: object) -> Diagnostic:
    return _error(
        code="invalid_manifest_shape",
        path=path,
        message="Workflow package manifest field has an invalid shape.",
        context={
            "expected_type": expected_type,
            "actual_type": type(value).__name__,
        },
        hint="Use the exact manifest record shape for this field.",
    )


def _invalid_manifest_value_error(
    *,
    path: str,
    expected_type: str,
    value: object,
) -> Diagnostic:
    return _error(
        code="invalid_manifest_value",
        path=path,
        message="Workflow package manifest contains a non-canonical value.",
        context={
            "expected_type": expected_type,
            "actual_type": type(value).__name__,
        },
        hint="Use strings, integers, booleans, null, arrays, or string-keyed maps.",
    )


def _invalid_allowed_value_error(
    *,
    path: str,
    value: str,
    allowed_values: tuple[str, ...],
) -> Diagnostic:
    return _error(
        code="invalid_manifest_value",
        path=path,
        message="Workflow package manifest field has an unsupported value.",
        context={"value": value, "allowed_values": allowed_values},
        hint="Use one of the closed values defined by the workflow package contract.",
    )


def _invalid_digest_error(*, path: str, value: object) -> Diagnostic:
    return _error(
        code="invalid_digest",
        path=path,
        message="Workflow package manifest digest is not a supported sha256 digest.",
        context={"digest": str(value)},
        hint="Use sha256 followed by a colon and 64 lowercase hexadecimal characters.",
    )


def _error(
    *,
    code: str,
    path: str,
    message: str,
    context: Mapping[str, str | int | bool | None | tuple[str, ...]],
    hint: str,
    related_declaration_path: str | None = None,
) -> Diagnostic:
    return compiler_error(
        code=code,
        declaration_path=path,
        message=message,
        context=context,
        hint=hint,
        related_declaration_path=related_declaration_path,
    )


__all__ = (
    "WorkflowPackageManifestValidationResult",
    "validate_importable_workflow_package_manifest",
    "validate_workflow_package_manifest",
)
