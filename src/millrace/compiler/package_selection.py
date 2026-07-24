"""Compiler-owned package workflow selection API."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Protocol, cast

from millrace.compiler.compile import CompileResult, compile_workflow
from millrace.compiler.diagnostics import compiler_error
from millrace.compiler.runner_bindings import (
    DEFAULT_SELECTED_RUNNER_ADAPTER_POLICY,
    SelectedRunnerAdapterPolicy,
)
from millrace.compiler.workflow_package_manifest import (
    validate_importable_workflow_package_manifest,
)
from millrace.contracts import Diagnostic
from millrace.contracts.compiled_plan import (
    SelectedWorkflowPackageAssetPin,
    SelectedWorkflowPackageDependencyPin,
    SelectedWorkflowPackagePin,
)
from millrace.contracts.diagnostics import DiagnosticContextValue
from millrace.contracts.workflow_package import (
    WorkflowPackageAsset,
    WorkflowPackageAssetRef,
    WorkflowPackageManifest,
    WorkflowPackageManifestCanonicalizationError,
    WorkflowPackageWorkflow,
    asset_digest_for_bytes,
    manifest_digest_for_manifest,
)

_ENABLED_STATUS = "enabled"
_KNOWN_STATUSES = frozenset(("imported", "enabled", "disabled", "removed"))
_EXACT_CONSTRAINT_RE = re.compile(r"==(.+)")


class CasByteReader(Protocol):
    """Read package CAS bytes by digest without mutating storage."""

    def __call__(self, cas_digest: str) -> bytes: ...


@dataclass(frozen=True, slots=True)
class PackageWorkflowSelector:
    package_id: str
    package_version: str
    workflow_id: str
    workflow_version: str
    entrypoint: str = "default"
    expected_manifest_digest: str | None = None
    expected_package_digest: str | None = None
    selected_runner_policy: SelectedRunnerAdapterPolicy = (
        DEFAULT_SELECTED_RUNNER_ADAPTER_POLICY
    )


@dataclass(frozen=True, slots=True)
class PackageRegistryView:
    records: tuple[object, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "records", tuple(self.records))


def compile_workflow_package_selection(
    selector: PackageWorkflowSelector,
    registry: PackageRegistryView,
    read_cas_bytes: CasByteReader,
) -> CompileResult:
    """Compile one enabled package workflow selection into selected authority."""

    diagnostics: list[Diagnostic] = []
    root_record = _select_current_record(selector, registry, diagnostics)
    if _has_errors(diagnostics) or root_record is None:
        return CompileResult(plan=None, diagnostics=tuple(diagnostics))

    _validate_root_record_preconditions(selector, root_record, diagnostics)
    if _has_errors(diagnostics):
        return CompileResult(plan=None, diagnostics=tuple(diagnostics))

    manifest = _load_manifest(root_record, read_cas_bytes, diagnostics)
    if _has_errors(diagnostics) or manifest is None:
        return CompileResult(plan=None, diagnostics=tuple(diagnostics))

    workflow = _select_workflow(selector, manifest, diagnostics)
    if _has_errors(diagnostics) or workflow is None:
        return CompileResult(plan=None, diagnostics=tuple(diagnostics))

    selected_assets = _lower_selected_assets(
        workflow,
        manifest,
        root_record,
        read_cas_bytes,
        diagnostics,
    )
    dependency_pins = _resolve_dependency_closure(
        root_record,
        workflow,
        registry,
        diagnostics,
    )
    if _has_errors(diagnostics) or selected_assets is None or dependency_pins is None:
        return CompileResult(plan=None, diagnostics=tuple(diagnostics))

    selected_source = _selected_workflow_source(workflow)
    if "assets" in selected_source:
        diagnostics.append(
            _selection_error(
                code="package_selection_selected_authority_assets_refused",
                path="workflows.selected_authority.assets",
                message=(
                    "Package selected authority must not declare assets directly."
                ),
                context={"workflow_id": workflow.workflow_id},
                hint=(
                    "Declare package assets in manifest assets[] and reference "
                    "selected workflow assets through required_assets[]."
                ),
            )
        )
        return CompileResult(plan=None, diagnostics=tuple(diagnostics))
    selected_source["assets"] = selected_assets.source_assets
    workflow_index = _workflow_manifest_index(selector, manifest)
    compile_result = compile_workflow(
        selected_source,
        selected_runner_policy=selector.selected_runner_policy,
        declaration_path_prefix=(
            f"workflows[{workflow_index}].selected_authority."
        ),
        diagnostic_context=_package_selection_warning_context(
            selector,
            root_record,
        ),
    )
    diagnostics.extend(compile_result.diagnostics)
    if _has_errors(diagnostics) or compile_result.plan is None:
        return CompileResult(plan=None, diagnostics=tuple(diagnostics))

    if (
        str(compile_result.plan.workflow.workflow_id) != selector.workflow_id
        or str(compile_result.plan.workflow.workflow_version)
        != selector.workflow_version
    ):
        diagnostics.append(
            _selection_error(
                code="package_selection_workflow_source_mismatch",
                path="workflows.selected_authority.workflow",
                message="Selected package workflow source does not match selector.",
                context={
                    "workflow_id": selector.workflow_id,
                    "workflow_version": selector.workflow_version,
                },
                hint=(
                    "Use selected_authority workflow identity that matches the "
                    "package workflow record."
                ),
            )
        )
        return CompileResult(plan=None, diagnostics=tuple(diagnostics))

    package_pin = SelectedWorkflowPackagePin(
        package_id=selector.package_id,
        package_version=selector.package_version,
        package_format_version=str(
            _record_value(root_record, "package_format_version")
        ),
        workflow_id=selector.workflow_id,
        workflow_version=selector.workflow_version,
        entrypoint=selector.entrypoint,
        selected_asset_pins=selected_assets.asset_pins,
        selected_dependency_pins=dependency_pins,
    )
    return CompileResult(
        plan=replace(compile_result.plan, workflow_package_pin=package_pin),
        diagnostics=tuple(diagnostics),
    )


@dataclass(frozen=True, slots=True)
class _LoweredAssets:
    source_assets: list[dict[str, object]]
    asset_pins: tuple[SelectedWorkflowPackageAssetPin, ...]


def _select_current_record(
    selector: PackageWorkflowSelector,
    registry: PackageRegistryView,
    diagnostics: list[Diagnostic],
) -> object | None:
    identity_matches = [
        record
        for record in registry.records
        if _record_value(record, "package_id") == selector.package_id
        and _record_value(record, "package_version") == selector.package_version
    ]
    if not identity_matches:
        diagnostics.append(
            _selection_error(
                code="package_selection_package_not_found",
                path="package",
                message="Selected workflow package identity was not found.",
                context={
                    "package_id": selector.package_id,
                    "package_version": selector.package_version,
                },
                hint=(
                    "Select an imported package identity present in the "
                    "registry view."
                ),
            )
        )
        return None

    current_matches = [
        record for record in identity_matches if _record_value(record, "is_current")
    ]
    if not current_matches:
        diagnostics.append(
            _selection_error(
                code="package_selection_zero_current_package",
                path="package",
                message="Selected workflow package identity has no current record.",
                context={
                    "package_id": selector.package_id,
                    "package_version": selector.package_version,
                },
                hint="Use a registry snapshot with exactly one current package record.",
            )
        )
        return None
    if len(current_matches) > 1:
        diagnostics.append(
            _selection_error(
                code="package_selection_duplicate_current_package",
                path="package",
                message=(
                    "Selected workflow package identity has multiple current "
                    "records."
                ),
                context={
                    "package_id": selector.package_id,
                    "package_version": selector.package_version,
                },
                hint="Repair the registry view before compiling package selections.",
            )
        )
        return None
    return current_matches[0]


def _validate_root_record_preconditions(
    selector: PackageWorkflowSelector,
    record: object,
    diagnostics: list[Diagnostic],
) -> None:
    _validate_enabled_status(
        record,
        diagnostics,
        status_code="package_selection_package_status_refused",
        path="package.status",
    )
    if (
        selector.expected_manifest_digest is not None
        and selector.expected_manifest_digest
        != _record_value(record, "manifest_digest")
    ):
        diagnostics.append(
            _selection_error(
                code="package_selection_expected_manifest_digest_mismatch",
                path="package.manifest_digest",
                message="Selected package manifest digest precondition does not match.",
                context={
                    "expected_manifest_digest": selector.expected_manifest_digest,
                    "actual_manifest_digest": str(
                        _record_value(record, "manifest_digest")
                    ),
                },
                hint="Refresh the selector or import the expected package record.",
            )
        )
    if (
        selector.expected_package_digest is not None
        and selector.expected_package_digest != _record_value(record, "package_digest")
    ):
        diagnostics.append(
            _selection_error(
                code="package_selection_expected_package_digest_mismatch",
                path="package.package_digest",
                message="Selected package digest precondition does not match.",
                context={
                    "expected_package_digest": selector.expected_package_digest,
                    "actual_package_digest": str(
                        _record_value(record, "package_digest")
                    ),
                },
                hint="Refresh the selector or import the expected package record.",
            )
        )


def _load_manifest(
    record: object,
    read_cas_bytes: CasByteReader,
    diagnostics: list[Diagnostic],
) -> WorkflowPackageManifest | None:
    manifest_cas_digest = _text_record_value(record, "manifest_cas_digest")
    try:
        manifest_bytes = read_cas_bytes(manifest_cas_digest)
    except Exception:  # noqa: BLE001 - reader failures become diagnostics.
        diagnostics.append(
            _selection_error(
                code="package_selection_manifest_cas_unreadable",
                path="package.manifest_cas_digest",
                message="Selected package manifest CAS bytes could not be read.",
                context={"manifest_cas_digest": manifest_cas_digest},
                hint="Provide a CAS byte reader that can read the manifest digest.",
            )
        )
        return None
    if not isinstance(manifest_bytes, bytes):
        diagnostics.append(
            _selection_error(
                code="package_selection_manifest_cas_unreadable",
                path="package.manifest_cas_digest",
                message="Selected package manifest CAS reader returned non-bytes.",
                context={"manifest_cas_digest": manifest_cas_digest},
                hint="Return exact manifest bytes from the CAS byte reader.",
            )
        )
        return None

    manifest_source = _parse_manifest_source(manifest_bytes, diagnostics)
    if manifest_source is None:
        return None
    expected_digest = _text_record_value(record, "manifest_digest")
    try:
        actual_digest = manifest_digest_for_manifest(manifest_source)
    except WorkflowPackageManifestCanonicalizationError:
        diagnostics.append(
            _selection_error(
                code="package_selection_manifest_digest_mismatch",
                path="package.manifest_cas_digest",
                message=(
                    "Selected package manifest CAS bytes are not canonical "
                    "manifest JSON."
                ),
                context={},
                hint="Store canonical importable manifest JSON bytes.",
            )
        )
        return None
    if actual_digest != expected_digest:
        diagnostics.append(
            _selection_error(
                code="package_selection_manifest_digest_mismatch",
                path="package.manifest_digest",
                message=(
                    "Selected package manifest CAS bytes do not match the "
                    "registry digest."
                ),
                context={
                    "expected_manifest_digest": expected_digest,
                    "actual_manifest_digest": actual_digest,
                },
                hint="Use manifest CAS bytes that match the imported package record.",
            )
        )
        return None

    validation = validate_importable_workflow_package_manifest(manifest_source)
    diagnostics.extend(validation.diagnostics)
    if validation.manifest is None:
        return None
    manifest = validation.manifest
    if (
        manifest.package.package_id != _record_value(record, "package_id")
        or manifest.package.package_version != _record_value(record, "package_version")
        or manifest.package.package_format_version
        != _record_value(record, "package_format_version")
    ):
        diagnostics.append(
            _selection_error(
                code="package_selection_manifest_package_mismatch",
                path="package",
                message=(
                    "Selected package manifest identity does not match "
                    "registry record."
                ),
                context={
                    "package_id": manifest.package.package_id,
                    "package_version": manifest.package.package_version,
                },
                hint=(
                    "Use a registry record and manifest CAS object for the "
                    "same package identity."
                ),
            )
        )
        return None
    return manifest


def _parse_manifest_source(
    manifest_bytes: bytes,
    diagnostics: list[Diagnostic],
) -> Mapping[str, object] | None:
    try:
        parsed = json.loads(
            manifest_bytes.decode("utf-8"),
            object_pairs_hook=_object_pairs,
            parse_constant=_reject_json_constant,
            parse_float=_reject_json_float,
        )
    except ValueError:
        diagnostics.append(
            _selection_error(
                code="package_selection_manifest_digest_mismatch",
                path="package.manifest_cas_digest",
                message=(
                    "Selected package manifest CAS bytes are not a valid "
                    "manifest object."
                ),
                context={},
                hint="Store the importable manifest JSON bytes at manifest_cas_digest.",
            )
        )
        return None
    if not isinstance(parsed, Mapping):
        diagnostics.append(
            _selection_error(
                code="package_selection_manifest_digest_mismatch",
                path="package.manifest_cas_digest",
                message="Selected package manifest CAS bytes are not an object.",
                context={},
                hint=(
                    "Store the importable manifest JSON object at "
                    "manifest_cas_digest."
                ),
            )
        )
        return None
    return parsed


def _object_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    parsed: dict[str, object] = {}
    for key, value in pairs:
        if key in parsed:
            raise ValueError(f"duplicate manifest key: {key}")
        parsed[key] = value
    return parsed


def _reject_json_constant(value: str) -> object:
    raise ValueError(f"invalid manifest JSON constant: {value}")


def _reject_json_float(value: str) -> object:
    raise ValueError(f"invalid manifest JSON float: {value}")


def _select_workflow(
    selector: PackageWorkflowSelector,
    manifest: WorkflowPackageManifest,
    diagnostics: list[Diagnostic],
) -> WorkflowPackageWorkflow | None:
    matches = [
        workflow
        for workflow in manifest.workflows
        if workflow.workflow_id == selector.workflow_id
        and workflow.workflow_version == selector.workflow_version
    ]
    if not matches:
        diagnostics.append(
            _selection_error(
                code="package_selection_workflow_not_found",
                path="workflows",
                message="Selected package workflow identity was not found.",
                context={
                    "workflow_id": selector.workflow_id,
                    "workflow_version": selector.workflow_version,
                },
                hint="Select a workflow declared by the imported package manifest.",
            )
        )
        return None
    workflow = matches[0]
    if selector.entrypoint not in workflow.entrypoints:
        diagnostics.append(
            _selection_error(
                code="package_selection_entrypoint_not_found",
                path="workflows.entrypoints",
                message="Selected package workflow entrypoint was not found.",
                context={
                    "workflow_id": selector.workflow_id,
                    "entrypoint": selector.entrypoint,
                },
                hint="Select one of the workflow entrypoints declared by the manifest.",
            )
        )
        return None
    return workflow


def _workflow_manifest_index(
    selector: PackageWorkflowSelector,
    manifest: WorkflowPackageManifest,
) -> int:
    for index, workflow in enumerate(manifest.workflows):
        if (
            workflow.workflow_id == selector.workflow_id
            and workflow.workflow_version == selector.workflow_version
        ):
            return index
    raise ValueError("selected package workflow is missing")


def _package_selection_warning_context(
    selector: PackageWorkflowSelector,
    record: object,
) -> Mapping[str, DiagnosticContextValue]:
    context: dict[str, DiagnosticContextValue] = {
        "source_kind": "workflow_package_selection",
        "package_id": selector.package_id,
        "package_version": selector.package_version,
        "entrypoint": selector.entrypoint,
    }
    for source_key, context_key in (
        ("source_kind", "package_source_kind"),
        ("source_digest", "package_source_digest"),
        ("import_record_digest", "package_import_digest"),
        ("manifest_digest", "package_manifest_digest"),
        ("package_digest", "package_digest"),
    ):
        value = _text_record_value(record, source_key)
        if value:
            context[context_key] = value
    return context


def _lower_selected_assets(
    workflow: WorkflowPackageWorkflow,
    manifest: WorkflowPackageManifest,
    record: object,
    read_cas_bytes: CasByteReader,
    diagnostics: list[Diagnostic],
) -> _LoweredAssets | None:
    manifest_assets = {asset.asset_id: asset for asset in manifest.assets}
    registry_assets = {
        str(_record_value(asset, "asset_id")): asset
        for asset in _record_sequence(record, "assets")
    }
    source_assets: list[dict[str, object]] = []
    asset_pins: list[SelectedWorkflowPackageAssetPin] = []
    for asset_ref in sorted(workflow.required_assets, key=lambda item: item.asset_id):
        manifest_asset = manifest_assets.get(asset_ref.asset_id)
        registry_asset = registry_assets.get(asset_ref.asset_id)
        if manifest_asset is None or registry_asset is None:
            diagnostics.append(
                _asset_error(
                    code="package_selection_asset_not_found",
                    asset_ref=asset_ref,
                    message="Selected package workflow asset was not found.",
                    hint=(
                        "Import a package record that includes every selected "
                        "workflow asset."
                    ),
                )
            )
            continue
        lowered = _lower_asset(
            asset_ref,
            manifest_asset,
            registry_asset,
            read_cas_bytes,
            diagnostics,
        )
        if lowered is None:
            continue
        source_assets.append(lowered)
        asset_pins.append(
            SelectedWorkflowPackageAssetPin(
                asset_id=manifest_asset.asset_id,
                content_digest=manifest_asset.content_digest,
            )
        )
    if _has_errors(diagnostics):
        return None
    return _LoweredAssets(
        source_assets=source_assets,
        asset_pins=tuple(asset_pins),
    )


def _lower_asset(
    asset_ref: WorkflowPackageAssetRef,
    manifest_asset: WorkflowPackageAsset,
    registry_asset: object,
    read_cas_bytes: CasByteReader,
    diagnostics: list[Diagnostic],
) -> dict[str, object] | None:
    if asset_ref.content_digest != manifest_asset.content_digest:
        diagnostics.append(
            _asset_error(
                code="package_selection_asset_digest_mismatch",
                asset_ref=asset_ref,
                message=(
                    "Selected package asset reference digest does not match "
                    "asset declaration."
                ),
                hint="Use asset refs that pin the declared selected asset digest.",
            )
        )
        return None
    if manifest_asset.encoding != "utf-8":
        diagnostics.append(
            _asset_error(
                code="package_selection_binary_asset_unsupported",
                asset_ref=asset_ref,
                message="Selected package asset is not supported by text lowering.",
                hint="Select only UTF-8 text assets.",
            )
        )
        return None
    if _record_value(registry_asset, "content_digest") != manifest_asset.content_digest:
        diagnostics.append(
            _asset_error(
                code="package_selection_asset_digest_mismatch",
                asset_ref=asset_ref,
                message=(
                    "Selected package registry asset digest does not match "
                    "manifest."
                ),
                hint="Use a registry view loaded from a valid imported package.",
            )
        )
        return None

    cas_digest = _text_record_value(registry_asset, "cas_digest")
    try:
        asset_bytes = read_cas_bytes(cas_digest)
    except Exception:  # noqa: BLE001 - reader failures become diagnostics.
        diagnostics.append(
            _asset_error(
                code="package_selection_asset_cas_unreadable",
                asset_ref=asset_ref,
                message="Selected package asset CAS bytes could not be read.",
                hint="Provide a CAS byte reader that can read every selected asset.",
            )
        )
        return None
    if not isinstance(asset_bytes, bytes):
        diagnostics.append(
            _asset_error(
                code="package_selection_asset_cas_unreadable",
                asset_ref=asset_ref,
                message="Selected package asset CAS reader returned non-bytes.",
                hint="Return exact asset bytes from the CAS byte reader.",
            )
        )
        return None

    if (
        asset_digest_for_bytes(asset_bytes) != manifest_asset.content_digest
        or len(asset_bytes) != manifest_asset.byte_length
        or len(asset_bytes) != _record_value(registry_asset, "byte_length")
    ):
        diagnostics.append(
            _asset_error(
                code="package_selection_asset_digest_mismatch",
                asset_ref=asset_ref,
                message="Selected package asset CAS bytes do not match manifest.",
                hint=(
                    "Use CAS bytes matching the selected asset digest and "
                    "byte length."
                ),
            )
        )
        return None
    try:
        body = asset_bytes.decode("utf-8")
    except UnicodeDecodeError:
        diagnostics.append(
            _asset_error(
                code="package_selection_binary_asset_unsupported",
                asset_ref=asset_ref,
                message="Selected package asset bytes are not UTF-8 text.",
                hint="Select only UTF-8 text assets.",
            )
        )
        return None
    return {
        "id": manifest_asset.asset_id,
        "kind": manifest_asset.asset_kind,
        "body": body,
    }


def _selected_workflow_source(workflow: WorkflowPackageWorkflow) -> dict[str, object]:
    return cast(dict[str, object], _plain_value(workflow.selected_authority))


def _plain_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _plain_value(nested) for key, nested in value.items()}
    if isinstance(value, tuple):
        return [_plain_value(item) for item in value]
    return value


def _resolve_dependency_closure(
    root_record: object,
    workflow: WorkflowPackageWorkflow,
    registry: PackageRegistryView,
    diagnostics: list[Diagnostic],
) -> tuple[SelectedWorkflowPackageDependencyPin, ...] | None:
    if not workflow.required_dependencies:
        return ()

    pins: list[SelectedWorkflowPackageDependencyPin] = []
    seen: set[tuple[str, str, str]] = set()

    def resolve_from(
        record: object,
        required_ids: tuple[str, ...],
        stack: tuple[str, ...],
    ) -> None:
        declarations = _dependency_declarations_by_id(record, diagnostics)
        if _has_errors(diagnostics):
            return
        for package_id in required_ids:
            if package_id in stack:
                diagnostics.append(_dependency_cycle_error(package_id))
                return
            matches = declarations.get(package_id, ())
            if not matches:
                diagnostics.append(
                    _dependency_error(
                        code="package_selection_dependency_not_declared",
                        package_id=package_id,
                        message=(
                            "Selected workflow requires an undeclared package "
                            "dependency."
                        ),
                        hint=(
                            "Add the dependency to the package manifest "
                            "dependencies list."
                        ),
                    )
                )
                continue
            if len(matches) != 1:
                diagnostics.append(
                    _dependency_error(
                        code="package_selection_dependency_conflict",
                        package_id=package_id,
                        message=(
                            "Selected workflow dependency has conflicting "
                            "declarations."
                        ),
                        hint="Declare one exact dependency constraint per package ID.",
                    )
                )
                continue
            dependency = matches[0]
            version = _exact_dependency_version(dependency, diagnostics)
            if version is None:
                continue
            resolved = _resolve_dependency_record(
                package_id,
                version,
                registry,
                diagnostics,
            )
            if resolved is None:
                continue
            pinned_digest = _optional_text_dependency(dependency, "manifest_digest")
            if pinned_digest is not None and pinned_digest != _record_value(
                resolved,
                "manifest_digest",
            ):
                diagnostics.append(
                    _dependency_error(
                        code="package_selection_dependency_manifest_digest_mismatch",
                        package_id=package_id,
                        message=(
                            "Selected dependency manifest digest precondition "
                            "does not match."
                        ),
                        hint=(
                            "Refresh the package dependency pin or import the "
                            "expected package."
                        ),
                    )
                )
                continue
            pin = SelectedWorkflowPackageDependencyPin(
                package_id=package_id,
                package_version=version,
                package_format_version=_text_record_value(
                    resolved,
                    "package_format_version",
                ),
            )
            key = (pin.package_id, pin.package_version, pin.package_format_version)
            if key in seen:
                diagnostics.append(
                    _dependency_error(
                        code="package_selection_duplicate_dependency_pin",
                        package_id=package_id,
                        message="Selected dependency closure produced a duplicate pin.",
                        hint=(
                            "Remove duplicate dependency paths for this "
                            "selected dependency closure."
                        ),
                    )
                )
                continue
            seen.add(key)
            pins.append(pin)
            transitive_ids = tuple(
                str(_record_value(item, "package_id"))
                for item in _record_sequence(resolved, "dependencies")
            )
            if transitive_ids:
                resolve_from(resolved, transitive_ids, (*stack, package_id))

    resolve_from(
        root_record,
        tuple(workflow.required_dependencies),
        (_text_record_value(root_record, "package_id"),),
    )
    if _has_errors(diagnostics):
        return None
    return tuple(sorted(pins, key=lambda pin: (pin.package_id, pin.package_version)))


def _dependency_declarations_by_id(
    record: object,
    diagnostics: list[Diagnostic],
) -> Mapping[str, tuple[object, ...]]:
    by_id: dict[str, list[object]] = {}
    for dependency in _record_sequence(record, "dependencies"):
        package_id = _record_value(dependency, "package_id")
        if not isinstance(package_id, str) or not package_id:
            diagnostics.append(
                _dependency_error(
                    code="package_selection_dependency_malformed",
                    package_id=str(package_id),
                    message="Package dependency declaration is malformed.",
                    hint=(
                        "Use dependency records with package_id and "
                        "version_constraint."
                    ),
                )
            )
            continue
        by_id.setdefault(package_id, []).append(dependency)
    return {key: tuple(value) for key, value in by_id.items()}


def _exact_dependency_version(
    dependency: object,
    diagnostics: list[Diagnostic],
) -> str | None:
    constraint = _record_value(dependency, "version_constraint")
    if not isinstance(constraint, str):
        diagnostics.append(
            _dependency_error(
                code="package_selection_non_exact_dependency_constraint",
                package_id=str(_record_value(dependency, "package_id")),
                message="Selected package dependency constraint is not exact.",
                hint="Use exact dependency constraints of the form ==<version>.",
            )
        )
        return None
    match = _EXACT_CONSTRAINT_RE.fullmatch(constraint)
    if match is None or not match.group(1):
        diagnostics.append(
            _dependency_error(
                code="package_selection_non_exact_dependency_constraint",
                package_id=str(_record_value(dependency, "package_id")),
                message="Selected package dependency constraint is not exact.",
                hint="Use exact dependency constraints of the form ==<version>.",
            )
        )
        return None
    return match.group(1)


def _resolve_dependency_record(
    package_id: str,
    package_version: str,
    registry: PackageRegistryView,
    diagnostics: list[Diagnostic],
) -> object | None:
    matches = [
        record
        for record in registry.records
        if _record_value(record, "package_id") == package_id
        and _record_value(record, "package_version") == package_version
        and _record_value(record, "is_current")
    ]
    if not matches:
        diagnostics.append(
            _dependency_error(
                code="package_selection_dependency_not_found",
                package_id=package_id,
                message="Selected package dependency was not found.",
                hint="Import and enable the exact dependency package version.",
            )
        )
        return None
    if len(matches) != 1:
        diagnostics.append(
            _dependency_error(
                code="package_selection_ambiguous_dependency",
                package_id=package_id,
                message="Selected package dependency resolved ambiguously.",
                hint="Use a registry view with exactly one current dependency record.",
            )
        )
        return None
    record = matches[0]
    if not _validate_enabled_status(
        record,
        diagnostics,
        status_code="package_selection_dependency_status_refused",
        path=f"dependencies.{package_id}.status",
    ):
        return None
    return record


def _validate_enabled_status(
    record: object,
    diagnostics: list[Diagnostic],
    *,
    status_code: str,
    path: str,
) -> bool:
    status = _record_value(record, "status")
    if status == _ENABLED_STATUS:
        return True
    if status not in _KNOWN_STATUSES:
        diagnostics.append(
            _selection_error(
                code="package_selection_unknown_package_status",
                path=path,
                message="Selected workflow package has an unknown registry status.",
                context={"status": str(status)},
                hint="Use a registry view loaded by the package registry reader.",
            )
        )
        return False
    diagnostics.append(
        _selection_error(
            code=status_code,
            path=path,
            message="Selected workflow package is not enabled.",
            context={"status": str(status)},
            hint="Enable the package before compiling a new package-backed plan.",
        )
    )
    return False


def _record_value(record: object, field_name: str) -> object:
    if isinstance(record, Mapping):
        return record.get(field_name)
    return getattr(record, field_name, None)


def _text_record_value(record: object, field_name: str) -> str:
    value = _record_value(record, field_name)
    return value if isinstance(value, str) else ""


def _record_sequence(record: object, field_name: str) -> tuple[object, ...]:
    value = _record_value(record, field_name)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return tuple(value)
    return ()


def _optional_text_dependency(record: object, field_name: str) -> str | None:
    value = _record_value(record, field_name)
    return value if isinstance(value, str) and value else None


def _asset_error(
    *,
    code: str,
    asset_ref: WorkflowPackageAssetRef,
    message: str,
    hint: str,
) -> Diagnostic:
    return _selection_error(
        code=code,
        path=f"workflows.required_assets.{asset_ref.asset_id}",
        message=message,
        context={"asset_id": asset_ref.asset_id},
        hint=hint,
    )


def _dependency_error(
    *,
    code: str,
    package_id: str,
    message: str,
    hint: str,
) -> Diagnostic:
    return _selection_error(
        code=code,
        path=f"dependencies.{package_id}",
        message=message,
        context={"package_id": package_id},
        hint=hint,
    )


def _dependency_cycle_error(package_id: str) -> Diagnostic:
    return _dependency_error(
        code="package_selection_dependency_cycle",
        package_id=package_id,
        message="Selected package dependency closure contains a cycle.",
        hint="Remove cyclic selected workflow package dependencies.",
    )


def _selection_error(
    *,
    code: str,
    path: str,
    message: str,
    context: Mapping[str, DiagnosticContextValue],
    hint: str,
) -> Diagnostic:
    return compiler_error(
        code=code,
        declaration_path=path,
        message=message,
        context=context,
        hint=hint,
    )


def _has_errors(diagnostics: Sequence[Diagnostic]) -> bool:
    return any(diagnostic.severity == "error" for diagnostic in diagnostics)


__all__ = (
    "CasByteReader",
    "PackageRegistryView",
    "PackageWorkflowSelector",
    "compile_workflow_package_selection",
)
