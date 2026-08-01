"""Versioned durable substrate record declarations."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import ClassVar, TypeAlias, cast

from millrace.substrate.errors import InvalidCasObject

CAS_OBJECT_RECORD_KIND = "cas_object"
CAS_OBJECT_SCHEMA_VERSION = 1

SQLITE_STORE_KIND = "millrace_sqlite_runtime_store"
SQLITE_STORE_SCHEMA_VERSION = 8
SQLITE_STORE_CREATED_BY = "millrace-ai"
SQLITE_STORE_INITIALIZATION_MARKER = "fresh_v0.22.0"

SELECTED_COMPILED_PLAN_OBJECT_KIND = "selected_compiled_plan"
PAYLOAD_OBJECT_KIND = "payload"
ARTIFACT_PAYLOAD_OBJECT_KIND = "artifact_payload"
CAS_OBJECT_KINDS = (
    SELECTED_COMPILED_PLAN_OBJECT_KIND,
    PAYLOAD_OBJECT_KIND,
    ARTIFACT_PAYLOAD_OBJECT_KIND,
)

WORKFLOW_PACKAGE_SOURCE_KINDS = ("archive", "path", "installed_python_package")
WORKFLOW_PACKAGE_STATUSES = ("imported", "enabled", "disabled", "removed")
WORKFLOW_PACKAGE_COMMAND_OPERATION_IDS = (
    "package.import_path",
    "package.import_archive",
    "package.import_installed",
    "package.export_path",
    "package.export_archive",
    "package.list",
    "package.inspect",
    "package.select_workflow",
    "package.enable",
    "package.disable",
    "package.remove",
    "package.update",
    "package.verify",
    "package.doctor",
)
WORKFLOW_PACKAGE_MUTATION_COMMAND_OPERATION_IDS = (
    "package.import_path",
    "package.import_archive",
    "package.import_installed",
    "package.enable",
    "package.disable",
    "package.remove",
    "package.update",
)
WORKFLOW_PACKAGE_COMMAND_OUTCOMES = ("succeeded", "failed")
WORKFLOW_PACKAGE_PACKAGE_DIGEST_DOMAIN = "millrace.wpkg.package.v1"
WORKFLOW_PACKAGE_IMPORT_RECORD_DIGEST_DOMAIN = "millrace.wpkg.import_record.v1"

CODEC_ID = "millrace-json-authority-v1"

JsonValue: TypeAlias = (
    str | int | bool | None | tuple["JsonValue", ...] | Mapping[str, "JsonValue"]
)


def freeze_json_value(value: object) -> JsonValue:
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, Mapping):
        return freeze_json_mapping(value)
    if isinstance(value, (list, tuple)):
        return tuple(freeze_json_value(item) for item in value)
    raise InvalidCasObject(
        "CAS JSON values must be strings, integers, booleans, nulls, arrays, "
        "or objects"
    )


def freeze_json_mapping(value: Mapping[object, object]) -> Mapping[str, JsonValue]:
    frozen: dict[str, JsonValue] = {}
    for key, nested_value in value.items():
        if not isinstance(key, str):
            raise InvalidCasObject("CAS JSON object keys must be strings")
        frozen[key] = freeze_json_value(nested_value)
    return MappingProxyType(frozen)


@dataclass(frozen=True, slots=True)
class CasObjectEnvelope:
    record_kind: ClassVar[str] = CAS_OBJECT_RECORD_KIND
    schema_version: ClassVar[int] = CAS_OBJECT_SCHEMA_VERSION

    object_kind: str
    payload: Mapping[str, JsonValue]
    codec: str = CODEC_ID

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "payload",
            freeze_json_mapping(cast(Mapping[object, object], self.payload)),
        )


@dataclass(frozen=True, slots=True)
class WorkflowPackageAssetRecord:
    asset_id: str
    package_path: str
    content_digest: str
    byte_length: int
    cas_digest: str
    selected_authority_participation: str


@dataclass(frozen=True, slots=True)
class WorkflowPackageRegistryRecord:
    record_id: str
    package_id: str
    package_version: str
    package_generation: int
    package_format_version: str
    manifest_digest: str
    manifest_cas_digest: str
    package_digest: str
    source_kind: str
    source_digest: str
    source_provenance_digest: str
    status: str
    status_generation: int
    latest_audit_id: str
    import_record_digest: str
    is_current: bool
    assets: tuple[WorkflowPackageAssetRecord, ...]
    dependencies: tuple[Mapping[str, JsonValue], ...]


@dataclass(frozen=True, slots=True)
class WorkflowPackageStatusHistoryRecord:
    audit_id: str
    package_id: str
    package_version: str
    package_generation: int
    status_generation: int
    status: str
    previous_status: str | None


@dataclass(frozen=True, slots=True)
class WorkflowPackageAuditEventRecord:
    audit_id: str
    actor_id: str
    actor_kind: str
    created_at: str
    operation: str
    source_kind: str
    old_generation: int | None
    new_generation: int
    diagnostics_summary: str
    package_digest: str
    import_record_digest: str


@dataclass(frozen=True, slots=True)
class WorkflowPackageCommandAuditEventRecord:
    command_audit_id: str
    command_id: str
    operation_id: str
    actor_id: str
    actor_kind: str
    created_at: str
    outcome: str
    package_id: str | None
    package_version: str | None
    package_generation: int | None
    status: str | None
    diagnostics_summary: str
    error_code: str | None
    registry_audit_id: str | None
    package_digest: str | None
    import_record_digest: str | None


@dataclass(frozen=True, slots=True)
class WorkflowPackageRegistrySnapshot:
    records: tuple[WorkflowPackageRegistryRecord, ...]
    status_history: tuple[WorkflowPackageStatusHistoryRecord, ...]
    audit_events: tuple[WorkflowPackageAuditEventRecord, ...]

    def current_package(
        self,
        package_id: str,
        package_version: str,
    ) -> WorkflowPackageRegistryRecord:
        matches = [
            record
            for record in self.records
            if record.package_id == package_id
            and record.package_version == package_version
            and record.is_current
        ]
        if len(matches) != 1:
            raise LookupError(f"current workflow package not found: {package_id}")
        return matches[0]


__all__ = (
    "ARTIFACT_PAYLOAD_OBJECT_KIND",
    "CAS_OBJECT_KINDS",
    "CAS_OBJECT_RECORD_KIND",
    "CAS_OBJECT_SCHEMA_VERSION",
    "CODEC_ID",
    "CasObjectEnvelope",
    "JsonValue",
    "PAYLOAD_OBJECT_KIND",
    "SELECTED_COMPILED_PLAN_OBJECT_KIND",
    "SQLITE_STORE_CREATED_BY",
    "SQLITE_STORE_INITIALIZATION_MARKER",
    "SQLITE_STORE_KIND",
    "SQLITE_STORE_SCHEMA_VERSION",
    "WORKFLOW_PACKAGE_IMPORT_RECORD_DIGEST_DOMAIN",
    "WORKFLOW_PACKAGE_COMMAND_OPERATION_IDS",
    "WORKFLOW_PACKAGE_COMMAND_OUTCOMES",
    "WORKFLOW_PACKAGE_MUTATION_COMMAND_OPERATION_IDS",
    "WORKFLOW_PACKAGE_PACKAGE_DIGEST_DOMAIN",
    "WORKFLOW_PACKAGE_SOURCE_KINDS",
    "WORKFLOW_PACKAGE_STATUSES",
    "freeze_json_mapping",
    "freeze_json_value",
    "WorkflowPackageAssetRecord",
    "WorkflowPackageAuditEventRecord",
    "WorkflowPackageCommandAuditEventRecord",
    "WorkflowPackageRegistryRecord",
    "WorkflowPackageRegistrySnapshot",
    "WorkflowPackageStatusHistoryRecord",
)
