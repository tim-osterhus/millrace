"""Extension package manifest data models.

Defines the contracts for extension packages that contribute new runtime
vocabulary items (operation runners, terminal actions, context providers,
document adapters, claim policies, recovery policies, failure policies).

ADRs: ADR-0012 (core-kernel-boundary), ADR-0015 (extension-package-manifests).
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Literal

from millrace_ai.architecture.common import (
    dedupe_preserve_order,
    normalize_canonical_id,
    normalize_nonempty_text,
)


class ExtensionDomain(str, Enum):
    """Workflow domain an extension package targets.

    Domains partition extension vocabulary so that a single extension
    package owns items for one domain family rather than mixing concerns.
    """

    GENERIC = "generic"
    RECON = "recon"
    CLOSURE = "closure"
    BLUEPRINT = "blueprint"
    LEARNING = "learning"


class ExtensionItemKind(str, Enum):
    """Vocabulary item types an extension package may register.

    Each kind maps to one of the workflow primitive or runtime-effect
    contracts that the compiler already validates (ADR-0015).
    """

    OPERATION_RUNNER = "runtime_effect_operation_runner"
    RUNTIME_EFFECT_RUNNER = "runtime_effect_runner"
    RUNTIME_EFFECT_OPERATION = "runtime_effect_operation"
    RUNTIME_EFFECT_HANDLER = "runtime_effect_handler"
    RUNTIME_EFFECT_RULE = "runtime_effect_rule"
    RUNTIME_EFFECT_PRIMITIVE = "runtime_effect_primitive"
    RUNTIME_EFFECT_VALIDATOR = "runtime_effect_validator"
    RUNTIME_EFFECT_STORE = "runtime_effect_store"
    TERMINAL_ACTION = "terminal_action"
    CONTEXT_PROVIDER = "request_context_provider"
    REQUEST_CONTEXT_PROFILE = "request_context_profile"
    REQUEST_CONTEXT_RENDER_PLAN = "request_context_render_plan"
    DOCUMENT_ADAPTER = "work_item_document_adapter"
    WORK_ITEM_FAMILY = "work_item_family"
    STAGE_KIND = "stage_kind"
    RUNTIME_OPERATION = "runtime_operation"
    QUEUE_CLAIM_POLICY = "queue_claim_policy"
    QUEUE_LIFECYCLE_POLICY = "queue_lifecycle_policy"
    RECOVERY_POLICY = "recovery_policy"
    FAILURE_POLICY = "failure_policy"
    RUNTIME_FAILURE_POLICY = "runtime_failure_policy"
    SCHEDULER_POLICY = "scheduler_policy"
    LIFECYCLE_MUTATION_PLAN = "lifecycle_mutation_plan"
    ARTIFACT_CONTRACT = "artifact_contract"
    WORKSPACE_SCHEMA_EPOCH = "workspace_schema_epoch"
    DOCTOR_DIAGNOSTIC = "doctor_diagnostic"
    STATUS_PROJECTION = "status_projection"


_SEMVER_RE = (
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)


def _validate_semver(value: str, *, field_label: str) -> str:
    import re

    normalized = value.strip()
    if not re.fullmatch(_SEMVER_RE, normalized):
        raise ValueError(
            f"{field_label} must be valid semver (e.g. 1.0.0), got {normalized!r}"
        )
    return normalized


def _validate_importable_module_path(value: str, *, field_label: str) -> str:
    """Validate a dotted Python module path without importing it.

    This enforces the boundary that config/graph data must not import
    arbitrary Python at compile time.  The import happens only later,
    when the runtime loader activates the extension item.
    """
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_label} may not be empty")
    parts = normalized.split(".")
    if not all(part.isidentifier() for part in parts):
        raise ValueError(
            f"{field_label} must be a dotted Python module path, got {normalized!r}"
        )
    return normalized


_SCHEMA_REF_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*(?:\.[a-zA-Z_][a-zA-Z0-9_]*)*$")


def _validate_contract_schema_ref(value: str, *, field_label: str) -> str:
    """Validate a schema reference string.

    This is a compiler-validated identifier that references a known
    Pydantic or JSON Schema contract.  It must be a dotted Python
    identifier path (dot-separated valid Python identifiers) rather
    than an arbitrary import that includes non-identifier characters.
    """
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_label} may not be empty")
    if not _SCHEMA_REF_RE.fullmatch(normalized):
        raise ValueError(
            f"{field_label} must be a dotted Python identifier path, "
            f"got {normalized!r}"
        )
    return normalized


class ExtensionItemManifest:
    """A single vocabulary item registered by an extension package.

    Each item declares its kind, a unique identifier for compiler/runtime
    reference, the implementation path that the runtime loader will import
    when the item is activated, and an optional contract schema reference.

    The implementation path is validated for syntactic correctness but is
    **not** imported at compile time — this preserves the boundary that
    config/graph data must not cause arbitrary Python imports.
    """

    def __init__(
        self,
        *,
        item_kind: ExtensionItemKind | str,
        item_id: str,
        implementation_path: str,
        contract_schema_ref: str | None = None,
        dependencies: tuple[str, ...] = (),
        version: str,
    ) -> None:
        self.item_kind = (
            item_kind if isinstance(item_kind, ExtensionItemKind) else ExtensionItemKind(item_kind)
        )
        self.item_id = normalize_canonical_id(str(item_id), field_label="item_id")
        self.implementation_path = _validate_importable_module_path(
            str(implementation_path), field_label="implementation_path"
        )
        self.contract_schema_ref = (
            _validate_contract_schema_ref(contract_schema_ref, field_label="contract_schema_ref")
            if contract_schema_ref is not None
            else None
        )
        self.dependencies = dedupe_preserve_order(
            [
                normalize_canonical_id(str(dep), field_label="item dependency")
                for dep in (dependencies or ())
            ]
        )
        self.version = _validate_semver(str(version), field_label="item version")

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ExtensionItemManifest):
            return NotImplemented
        return (self.item_kind, self.item_id) == (other.item_kind, other.item_id)

    def __hash__(self) -> int:
        return hash((self.item_kind, self.item_id))

    def __repr__(self) -> str:
        return (
            f"ExtensionItemManifest(item_kind={self.item_kind.value!r}, "
            f"item_id={self.item_id!r}, version={self.version!r})"
        )

    def to_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "item_kind": self.item_kind.value,
            "item_id": self.item_id,
            "implementation_path": self.implementation_path,
            "version": self.version,
        }
        if self.contract_schema_ref is not None:
            result["contract_schema_ref"] = self.contract_schema_ref
        if self.dependencies:
            result["dependencies"] = list(self.dependencies)
        return result

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> "ExtensionItemManifest":
        return cls(
            item_kind=str(payload["item_kind"]),
            item_id=str(payload["item_id"]),
            implementation_path=str(payload["implementation_path"]),
            contract_schema_ref=(
                str(payload["contract_schema_ref"])
                if payload.get("contract_schema_ref") is not None
                else None
            ),
            dependencies=tuple(
                str(d) for d in (payload.get("dependencies") or ())
            ),
            version=str(payload["version"]),
        )


class ExtensionPackageManifest:
    """Top-level manifest for one extension package.

    Declares the package identity, the domain it targets, and the
    vocabulary items it registers.  The compiler discovers and validates
    these manifests at compile time; the runtime loader uses them to
    activate extension items when referenced by compiled plan metadata.
    """

    schema_version: Literal["1.0"] = "1.0"
    kind: Literal["extension_package_manifest"] = "extension_package_manifest"

    def __init__(
        self,
        *,
        package_id: str,
        display_name: str,
        domain: ExtensionDomain | str,
        version: str,
        items: tuple[ExtensionItemManifest, ...] = (),
        requires: tuple[str, ...] = (),
    ) -> None:
        self.package_id = normalize_canonical_id(str(package_id), field_label="package_id")
        self.display_name = normalize_nonempty_text(str(display_name), field_label="display_name")
        self.domain = domain if isinstance(domain, ExtensionDomain) else ExtensionDomain(domain)
        self.version = _validate_semver(str(version), field_label="package version")
        self.items = items
        self.requires = dedupe_preserve_order(
            [
                normalize_canonical_id(str(req), field_label="required package")
                for req in (requires or ())
            ]
        )

        # Validate no circular self-requirement
        if self.package_id in self.requires:
            raise ValueError(
                f"extension package {self.package_id!r} may not require itself"
            )

        # Validate ownership keys are unique. Different registry families can
        # legitimately use the same id string, so kind participates in the key.
        item_keys = [(item.item_kind, item.item_id) for item in items]
        if len(item_keys) != len(set(item_keys)):
            seen: set[tuple[ExtensionItemKind, str]] = set()
            duplicates = {
                f"{kind.value}:{item_id}"
                for kind, item_id in item_keys
                if (kind, item_id) in seen or seen.add((kind, item_id))
            }
            raise ValueError(
                f"duplicate item ids in package {self.package_id!r} for item-kind keys: "
                + ", ".join(sorted(duplicates))
            )

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ExtensionPackageManifest):
            return NotImplemented
        return self.package_id == other.package_id

    def __hash__(self) -> int:
        return hash(self.package_id)

    def __repr__(self) -> str:
        return (
            f"ExtensionPackageManifest(package_id={self.package_id!r}, "
            f"domain={self.domain.value!r}, version={self.version!r}, "
            f"items={len(self.items)})"
        )

    @property
    def items_by_id(self) -> dict[str, ExtensionItemManifest]:
        return {item.item_id: item for item in self.items}

    def to_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "schema_version": self.schema_version,
            "kind": self.kind,
            "package_id": self.package_id,
            "display_name": self.display_name,
            "domain": self.domain.value,
            "version": self.version,
            "items": [item.to_dict() for item in self.items],
        }
        if self.requires:
            result["requires"] = list(self.requires)
        return result

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> "ExtensionPackageManifest":
        items_raw = payload.get("items") or []
        if isinstance(items_raw, list):
            items = tuple(
                ExtensionItemManifest.from_dict(item)
                for item in items_raw
            )
        else:
            items = ()
        return cls(
            package_id=str(payload["package_id"]),
            display_name=str(payload["display_name"]),
            domain=str(payload["domain"]),
            version=str(payload["version"]),
            items=items,
            requires=tuple(str(r) for r in (payload.get("requires") or ())),
        )


__all__ = [
    "ExtensionDomain",
    "ExtensionItemKind",
    "ExtensionItemManifest",
    "ExtensionPackageManifest",
]
