"""Runner dispatch and result evidence contracts.

These records are part of the runner boundary. They model how test-runner
traffic is represented when it reaches kernel decisions.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from hashlib import sha256
from types import MappingProxyType
from typing import ClassVar, cast

from millrace.contracts.compiled_plan import (
    AuthorityValue,
    freeze_authority_value,
)

RUNNER_DISPATCH_RECORD_KIND = "runner_dispatch_envelope"
RUNNER_DISPATCH_SCHEMA_VERSION = 5
RUNNER_RESULT_RECORD_KIND = "runner_result_evidence"
RUNNER_RESULT_SCHEMA_VERSION = 3
_RUNNER_ADAPTER_PROVENANCE_RECORD_KIND = "runner_adapter_provenance"
_RUNNER_ADAPTER_PROVENANCE_SCHEMA_VERSION = 1
_SELECTED_JOIN_EVIDENCE_RECORD_KIND = "selected_join_evidence"
_SELECTED_JOIN_EVIDENCE_SCHEMA_VERSION = 1

_TERMINAL_OPTION_REQUIRED_KEYS = frozenset(
    {
        "outcome_id",
        "marker",
        "action_id",
        "action_kind",
        "artifact_schema_id",
    }
)
_TERMINAL_OPTION_ALLOWED_KEYS = _TERMINAL_OPTION_REQUIRED_KEYS | {"counter"}
_SELECTED_JOIN_EVIDENCE_REQUIRED_KEYS = frozenset(
    {
        "record_kind",
        "schema_version",
        "join_id",
        "correlation_key",
        "correlation_value",
        "correlation_identity",
        "lineage_id",
        "bundle_artifact_id",
        "bundle_artifact_schema_id",
        "bundle_artifact_digest",
        "required_artifact_schema_ids",
        "evidence_artifacts",
    },
)
_SELECTED_JOIN_EVIDENCE_ARTIFACT_REQUIRED_KEYS = frozenset(
    {
        "artifact_id",
        "artifact_schema_id",
        "payload_digest",
        "payload",
        "source_action_id",
        "source_run_id",
        "source_work_item_id",
        "fanout_id",
        "fanout_record_id",
        "item_key",
    },
)
_RUNNER_ADAPTER_PROVENANCE_REQUIRED_KEYS = frozenset(
    {
        "record_kind",
        "schema_version",
        "adapter_kind",
        "component_descriptor_sha256",
        "invocation_evidence_sha256",
        "correlation_id",
    }
)
_LOWER_HEX = frozenset("0123456789abcdef")


@dataclass(frozen=True, slots=True)
class RunnerDispatchEnvelope:
    record_kind: ClassVar[str] = RUNNER_DISPATCH_RECORD_KIND
    schema_version: ClassVar[int] = RUNNER_DISPATCH_SCHEMA_VERSION

    run_id: str
    session_id: str
    dispatch_generation: int
    session_fencing_token: str
    work_item_id: str
    activation_id: str
    plan_fingerprint: str
    plan_id: str
    workflow_id: str
    workflow_version: str
    graph_id: str
    claim_id: str
    generation: int
    fencing_token: str
    queue_family_id: str
    stage_kind_id: str
    graph_node_id: str
    runner_binding_id: str
    external_enqueue_route_id: str | None
    entrypoint_asset_id: str | None
    skill_asset_ids: tuple[str, ...]
    artifact_schema_ids: tuple[str, ...]
    work_item_payload: Mapping[str, AuthorityValue]
    governance_context: Mapping[str, AuthorityValue] = field(default_factory=dict)
    terminal_options: tuple[Mapping[str, AuthorityValue], ...] = ()
    selected_join_evidence: Mapping[str, AuthorityValue] | None = None

    def __post_init__(self) -> None:
        _require_nonblank_string(self.run_id, "run_id")
        _require_nonblank_string(self.session_id, "session_id")
        _require_int(self.dispatch_generation, "dispatch_generation")
        if self.dispatch_generation < 1:
            raise ValueError("dispatch_generation must be positive")
        _require_nonblank_string(
            self.session_fencing_token,
            "session_fencing_token",
        )
        _require_nonblank_string(self.work_item_id, "work_item_id")
        _require_nonblank_string(self.activation_id, "activation_id")
        _require_nonblank_string(self.plan_fingerprint, "plan_fingerprint")
        _require_nonblank_string(self.plan_id, "plan_id")
        _require_nonblank_string(self.workflow_id, "workflow_id")
        _require_nonblank_string(self.workflow_version, "workflow_version")
        _require_nonblank_string(self.graph_id, "graph_id")
        _require_nonblank_string(self.claim_id, "claim_id")
        _require_int(self.generation, "generation")
        _require_nonblank_string(self.fencing_token, "fencing_token")
        _require_nonblank_string(self.queue_family_id, "queue_family_id")
        _require_nonblank_string(self.stage_kind_id, "stage_kind_id")
        _require_nonblank_string(self.graph_node_id, "graph_node_id")
        _require_nonblank_string(self.runner_binding_id, "runner_binding_id")
        _require_optional_nonblank_string(
            self.external_enqueue_route_id,
            "external_enqueue_route_id",
        )
        _require_optional_nonblank_string(
            self.entrypoint_asset_id,
            "entrypoint_asset_id",
        )
        object.__setattr__(
            self,
            "skill_asset_ids",
            _coerce_string_tuple(self.skill_asset_ids, "skill_asset_ids"),
        )
        object.__setattr__(
            self,
            "artifact_schema_ids",
            _coerce_string_tuple(self.artifact_schema_ids, "artifact_schema_ids"),
        )
        object.__setattr__(
            self,
            "work_item_payload",
            _coerce_payload_mapping(
                self.work_item_payload,
                "work_item_payload",
            ),
        )
        object.__setattr__(
            self,
            "governance_context",
            _coerce_payload_mapping(
                self.governance_context,
                "governance_context",
            ),
        )
        object.__setattr__(
            self,
            "terminal_options",
            _coerce_payload_mapping_tuple(
                self.terminal_options,
                "terminal_options",
            ),
        )
        object.__setattr__(
            self,
            "selected_join_evidence",
            _coerce_selected_join_evidence(
                self.selected_join_evidence,
            ),
        )

    def payload(self) -> Mapping[str, AuthorityValue]:
        return MappingProxyType(
            {
                "record_kind": self.record_kind,
                "schema_version": self.schema_version,
                "run_id": self.run_id,
                "session_id": self.session_id,
                "dispatch_generation": self.dispatch_generation,
                "session_fencing_token": self.session_fencing_token,
                "work_item_id": self.work_item_id,
                "activation_id": self.activation_id,
                "plan_fingerprint": self.plan_fingerprint,
                "plan_id": self.plan_id,
                "workflow_id": self.workflow_id,
                "workflow_version": self.workflow_version,
                "graph_id": self.graph_id,
                "claim_id": self.claim_id,
                "generation": self.generation,
                "fencing_token": self.fencing_token,
                "queue_family_id": self.queue_family_id,
                "stage_kind_id": self.stage_kind_id,
                "graph_node_id": self.graph_node_id,
                "runner_binding_id": self.runner_binding_id,
                "external_enqueue_route_id": self.external_enqueue_route_id,
                "entrypoint_asset_id": self.entrypoint_asset_id,
                "skill_asset_ids": self.skill_asset_ids,
                "artifact_schema_ids": self.artifact_schema_ids,
                "work_item_payload": self.work_item_payload,
                "governance_context": self.governance_context,
                "terminal_options": self.terminal_options,
                "selected_join_evidence": self.selected_join_evidence,
            },
        )


@dataclass(frozen=True, slots=True)
class RunnerAdapterProvenance:
    record_kind: ClassVar[str] = _RUNNER_ADAPTER_PROVENANCE_RECORD_KIND
    schema_version: ClassVar[int] = _RUNNER_ADAPTER_PROVENANCE_SCHEMA_VERSION

    adapter_kind: str
    component_descriptor_sha256: str
    invocation_evidence_sha256: str
    correlation_id: str

    def __post_init__(self) -> None:
        _require_nonblank_string(self.adapter_kind, "adapter_kind")
        _require_raw_sha256_digest(
            self.component_descriptor_sha256,
            "component_descriptor_sha256",
        )
        _require_raw_sha256_digest(
            self.invocation_evidence_sha256,
            "invocation_evidence_sha256",
        )
        _require_nonblank_string(self.correlation_id, "correlation_id")

    def payload(self) -> Mapping[str, AuthorityValue]:
        return MappingProxyType(
            {
                "record_kind": self.record_kind,
                "schema_version": self.schema_version,
                "adapter_kind": self.adapter_kind,
                "component_descriptor_sha256": self.component_descriptor_sha256,
                "invocation_evidence_sha256": self.invocation_evidence_sha256,
                "correlation_id": self.correlation_id,
            }
        )


@dataclass(frozen=True, slots=True)
class RunnerResultEvidence:
    record_kind: ClassVar[str] = RUNNER_RESULT_RECORD_KIND
    schema_version: ClassVar[int] = RUNNER_RESULT_SCHEMA_VERSION

    run_id: str
    session_id: str
    dispatch_generation: int
    session_fencing_token: str
    plan_fingerprint: str
    claim_id: str
    generation: int
    fencing_token: str
    stage_kind_id: str
    graph_node_id: str
    runner_binding_id: str
    marker: str
    adapter_provenance: RunnerAdapterProvenance | None
    observation_payload: Mapping[str, AuthorityValue]
    artifact_payload: Mapping[str, AuthorityValue]

    def __post_init__(self) -> None:
        _require_nonblank_string(self.run_id, "run_id")
        _require_nonblank_string(self.session_id, "session_id")
        _require_int(self.dispatch_generation, "dispatch_generation")
        if self.dispatch_generation < 1:
            raise ValueError("dispatch_generation must be positive")
        _require_nonblank_string(
            self.session_fencing_token,
            "session_fencing_token",
        )
        _require_nonblank_string(self.plan_fingerprint, "plan_fingerprint")
        _require_nonblank_string(self.claim_id, "claim_id")
        _require_int(self.generation, "generation")
        _require_nonblank_string(self.fencing_token, "fencing_token")
        _require_nonblank_string(self.stage_kind_id, "stage_kind_id")
        _require_nonblank_string(self.graph_node_id, "graph_node_id")
        _require_nonblank_string(self.runner_binding_id, "runner_binding_id")
        _require_nonblank_string(self.marker, "marker")
        object.__setattr__(
            self,
            "adapter_provenance",
            _coerce_runner_adapter_provenance(self.adapter_provenance),
        )
        object.__setattr__(
            self,
            "observation_payload",
            _coerce_payload_mapping(
                self.observation_payload,
                "observation_payload",
            ),
        )
        object.__setattr__(
            self,
            "artifact_payload",
            _coerce_payload_mapping(
                self.artifact_payload,
                "artifact_payload",
            ),
        )

    def payload(self) -> Mapping[str, AuthorityValue]:
        return MappingProxyType(
            {
                "record_kind": self.record_kind,
                "schema_version": self.schema_version,
                "run_id": self.run_id,
                "session_id": self.session_id,
                "dispatch_generation": self.dispatch_generation,
                "session_fencing_token": self.session_fencing_token,
                "plan_fingerprint": self.plan_fingerprint,
                "claim_id": self.claim_id,
                "generation": self.generation,
                "fencing_token": self.fencing_token,
                "stage_kind_id": self.stage_kind_id,
                "graph_node_id": self.graph_node_id,
                "runner_binding_id": self.runner_binding_id,
                "marker": self.marker,
                "adapter_provenance": (
                    None
                    if self.adapter_provenance is None
                    else self.adapter_provenance.payload()
                ),
                "observation_payload": self.observation_payload,
                "artifact_payload": self.artifact_payload,
            },
        )


_RESULT_REQUIRED_PAYLOAD_KEYS = {
    "record_kind",
    "schema_version",
    "run_id",
    "session_id",
    "dispatch_generation",
    "session_fencing_token",
    "plan_fingerprint",
    "claim_id",
    "generation",
    "fencing_token",
    "stage_kind_id",
    "graph_node_id",
    "runner_binding_id",
    "marker",
    "adapter_provenance",
    "observation_payload",
    "artifact_payload",
}


def runner_result_payload(
    evidence: RunnerResultEvidence,
) -> Mapping[str, AuthorityValue]:
    return evidence.payload()


def runner_result_evidence_bytes(evidence: RunnerResultEvidence) -> bytes:
    if not isinstance(evidence, RunnerResultEvidence):
        raise TypeError("evidence must be RunnerResultEvidence")
    return json.dumps(
        _plain_authority_value(evidence.payload()),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def runner_result_evidence_digest(evidence: RunnerResultEvidence) -> str:
    return f"sha256:{sha256(runner_result_evidence_bytes(evidence)).hexdigest()}"


def runner_result_evidence_from_payload(
    payload: Mapping[str, object] | MappingProxyType[str, object],
) -> RunnerResultEvidence:
    if not isinstance(payload, Mapping):
        raise TypeError("runner result evidence payload must be a mapping")
    raw_keys = set(payload)
    if raw_keys != _RESULT_REQUIRED_PAYLOAD_KEYS:
        raise ValueError("runner result evidence payload has unexpected top-level keys")

    observation_payload = payload.get("observation_payload")
    artifact_payload = payload.get("artifact_payload")
    adapter_provenance = payload.get("adapter_provenance")
    record_kind = payload.get("record_kind")
    schema_version = payload.get("schema_version")

    record_kind_value = _require_string(record_kind, "record_kind")
    if record_kind_value != RUNNER_RESULT_RECORD_KIND:
        raise ValueError(f"unsupported record kind: {record_kind_value}")
    if type(schema_version) is not int:
        raise ValueError("runner result evidence schema_version must be integer")
    if schema_version != RUNNER_RESULT_SCHEMA_VERSION:
        raise ValueError(f"unsupported schema version: {schema_version}")

    return RunnerResultEvidence(
        run_id=_require_string(payload.get("run_id"), "run_id"),
        session_id=_require_string(payload.get("session_id"), "session_id"),
        dispatch_generation=_require_int(
            payload.get("dispatch_generation"),
            "dispatch_generation",
        ),
        session_fencing_token=_require_string(
            payload.get("session_fencing_token"),
            "session_fencing_token",
        ),
        plan_fingerprint=_require_string(
            payload.get("plan_fingerprint"),
            "plan_fingerprint",
        ),
        claim_id=_require_string(payload.get("claim_id"), "claim_id"),
        generation=_require_int(payload.get("generation"), "generation"),
        fencing_token=_require_string(payload.get("fencing_token"), "fencing_token"),
        stage_kind_id=_require_string(payload.get("stage_kind_id"), "stage_kind_id"),
        graph_node_id=_require_string(payload.get("graph_node_id"), "graph_node_id"),
        runner_binding_id=_require_string(
            payload.get("runner_binding_id"),
            "runner_binding_id",
        ),
        marker=_require_string(payload.get("marker"), "marker"),
        adapter_provenance=_runner_adapter_provenance_from_payload(
            adapter_provenance,
        ),
        observation_payload=_coerce_payload_mapping(
            observation_payload,
            "observation_payload",
        ),
        artifact_payload=_coerce_payload_mapping(artifact_payload, "artifact_payload"),
    )


def _runner_adapter_provenance_from_payload(
    value: object,
) -> RunnerAdapterProvenance | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError("adapter_provenance must be a mapping or None")
    raw = cast(Mapping[object, object], value)
    _require_exact_keys(
        raw,
        _RUNNER_ADAPTER_PROVENANCE_REQUIRED_KEYS,
        "adapter_provenance",
    )
    _require_exact_text(
        raw["record_kind"],
        _RUNNER_ADAPTER_PROVENANCE_RECORD_KIND,
        "adapter_provenance.record_kind",
    )
    _require_exact_int(
        raw["schema_version"],
        _RUNNER_ADAPTER_PROVENANCE_SCHEMA_VERSION,
        "adapter_provenance.schema_version",
    )
    return RunnerAdapterProvenance(
        adapter_kind=_require_string(
            raw["adapter_kind"],
            "adapter_provenance.adapter_kind",
        ),
        component_descriptor_sha256=_require_string(
            raw["component_descriptor_sha256"],
            "adapter_provenance.component_descriptor_sha256",
        ),
        invocation_evidence_sha256=_require_string(
            raw["invocation_evidence_sha256"],
            "adapter_provenance.invocation_evidence_sha256",
        ),
        correlation_id=_require_string(
            raw["correlation_id"],
            "adapter_provenance.correlation_id",
        ),
    )


def _coerce_runner_adapter_provenance(
    value: object,
) -> RunnerAdapterProvenance | None:
    if value is None:
        return None
    if not isinstance(value, RunnerAdapterProvenance):
        raise TypeError("adapter_provenance must be RunnerAdapterProvenance or None")
    return RunnerAdapterProvenance(
        adapter_kind=value.adapter_kind,
        component_descriptor_sha256=value.component_descriptor_sha256,
        invocation_evidence_sha256=value.invocation_evidence_sha256,
        correlation_id=value.correlation_id,
    )


def _coerce_payload_mapping(
    value: object,
    field_name: str,
) -> Mapping[str, AuthorityValue]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be a mapping")
    coerce_items: dict[str, AuthorityValue] = {}
    for key, nested_value in cast(Mapping[object, object], value).items():
        if not isinstance(key, str):
            raise ValueError(f"{field_name} keys must be strings")
        coerce_items[key] = freeze_authority_value(nested_value)

    return MappingProxyType(coerce_items)


def _coerce_payload_mapping_tuple(
    value: object,
    field_name: str,
) -> tuple[Mapping[str, AuthorityValue], ...]:
    if not isinstance(value, tuple):
        raise ValueError(f"{field_name} must be a tuple")
    return tuple(
        _coerce_terminal_option_mapping(item, index)
        for index, item in enumerate(value)
    )


def _coerce_terminal_option_mapping(
    value: object,
    index: int,
) -> Mapping[str, AuthorityValue]:
    field_name = f"terminal_options[{index}]"
    option = _coerce_payload_mapping(value, field_name)
    keys = frozenset(option)
    if not _TERMINAL_OPTION_REQUIRED_KEYS <= keys:
        raise ValueError(f"{field_name} is missing required keys")
    if not keys <= _TERMINAL_OPTION_ALLOWED_KEYS:
        raise ValueError(f"{field_name} has unsupported keys")
    for required_text_key in (
        "outcome_id",
        "marker",
        "action_id",
        "action_kind",
    ):
        _require_nonblank_string(
            option[required_text_key],
            f"{field_name}.{required_text_key}",
        )
    artifact_schema_id = option["artifact_schema_id"]
    if artifact_schema_id is not None:
        _require_nonblank_string(artifact_schema_id, f"{field_name}.artifact_schema_id")
    if "counter" in option and not isinstance(option["counter"], Mapping):
        raise ValueError(f"{field_name}.counter must be a mapping")
    return option


def _coerce_selected_join_evidence(
    value: object,
) -> Mapping[str, AuthorityValue] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError("selected_join_evidence must be a mapping or None")

    raw = cast(Mapping[object, object], value)
    _require_exact_keys(
        raw,
        _SELECTED_JOIN_EVIDENCE_REQUIRED_KEYS,
        "selected_join_evidence",
    )
    _require_exact_text(
        raw["record_kind"],
        _SELECTED_JOIN_EVIDENCE_RECORD_KIND,
        "selected_join_evidence.record_kind",
    )
    _require_exact_int(
        raw["schema_version"],
        _SELECTED_JOIN_EVIDENCE_SCHEMA_VERSION,
        "selected_join_evidence.schema_version",
    )
    for text_field in (
        "join_id",
        "correlation_key",
        "correlation_value",
        "bundle_artifact_id",
        "bundle_artifact_schema_id",
    ):
        _require_nonblank_string(
            raw[text_field],
            f"selected_join_evidence.{text_field}",
        )
    _require_optional_nonblank_string(
        raw["lineage_id"],
        "selected_join_evidence.lineage_id",
    )
    _require_correlation_identity(
        raw["correlation_identity"],
        "selected_join_evidence.correlation_identity",
    )
    _require_sha256_prefixed_digest(
        raw["bundle_artifact_digest"],
        "selected_join_evidence.bundle_artifact_digest",
    )
    required_artifact_schema_ids = _coerce_string_tuple(
        raw["required_artifact_schema_ids"],
        "selected_join_evidence.required_artifact_schema_ids",
    )
    if not required_artifact_schema_ids:
        raise ValueError(
            "selected_join_evidence.required_artifact_schema_ids cannot be empty",
        )
    _validate_selected_join_evidence_artifacts(raw["evidence_artifacts"])
    return _coerce_payload_mapping(value, "selected_join_evidence")


def _validate_selected_join_evidence_artifacts(value: object) -> None:
    field_name = "selected_join_evidence.evidence_artifacts"
    if not isinstance(value, tuple):
        raise ValueError(f"{field_name} must be a tuple")
    if not value:
        raise ValueError(f"{field_name} cannot be empty")
    for index, artifact in enumerate(value):
        artifact_field_name = f"{field_name}[{index}]"
        if not isinstance(artifact, Mapping):
            raise ValueError(f"{artifact_field_name} must be a mapping")
        raw_artifact = cast(Mapping[object, object], artifact)
        _require_exact_keys(
            raw_artifact,
            _SELECTED_JOIN_EVIDENCE_ARTIFACT_REQUIRED_KEYS,
            artifact_field_name,
        )
        for text_field in (
            "artifact_id",
            "artifact_schema_id",
            "source_action_id",
            "source_run_id",
            "source_work_item_id",
            "fanout_id",
            "fanout_record_id",
            "item_key",
        ):
            _require_nonblank_string(
                raw_artifact[text_field],
                f"{artifact_field_name}.{text_field}",
            )
        _require_sha256_prefixed_digest(
            raw_artifact["payload_digest"],
            f"{artifact_field_name}.payload_digest",
        )
        if not isinstance(raw_artifact["payload"], Mapping):
            raise ValueError(f"{artifact_field_name}.payload must be a mapping")


def _require_exact_keys(
    value: Mapping[object, object],
    expected_keys: frozenset[str],
    field_name: str,
) -> None:
    if frozenset(value) != expected_keys:
        raise ValueError(f"{field_name} has unexpected keys")


def _require_exact_text(value: object, expected: str, field_name: str) -> None:
    actual = _require_nonblank_string(value, field_name)
    if actual != expected:
        raise ValueError(f"{field_name} must be {expected!r}")


def _require_exact_int(value: object, expected: int, field_name: str) -> None:
    actual = _require_int(value, field_name)
    if actual != expected:
        raise ValueError(f"{field_name} must be {expected}")


def _require_correlation_identity(value: object, field_name: str) -> str:
    identity = _require_nonblank_string(value, field_name)
    if len(identity) != 64 or any(char not in _LOWER_HEX for char in identity):
        raise ValueError(f"{field_name} must be lowercase 64-hex")
    return identity


def _require_raw_sha256_digest(value: object, field_name: str) -> str:
    digest = _require_nonblank_string(value, field_name)
    if len(digest) != 64 or any(char not in _LOWER_HEX for char in digest):
        raise ValueError(f"{field_name} must be lowercase 64-hex")
    return digest


def _require_sha256_prefixed_digest(value: object, field_name: str) -> str:
    digest = _require_nonblank_string(value, field_name)
    prefix = "sha256:"
    digest_hex = digest.removeprefix(prefix)
    if (
        not digest.startswith(prefix)
        or len(digest_hex) != 64
        or any(char not in _LOWER_HEX for char in digest_hex)
    ):
        raise ValueError(
            f"{field_name} must be 'sha256:' followed by 64 lowercase hex characters",
        )
    return digest


def _coerce_string_tuple(value: object, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, tuple):
        raise ValueError(f"{field_name} must be a tuple")
    return tuple(
        _require_nonblank_string(item, f"{field_name}[{index}]")
        for index, item in enumerate(value)
    )


def _require_optional_nonblank_string(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    return _require_nonblank_string(value, field_name)


def _require_nonblank_string(value: object, field_name: str) -> str:
    _require_string(value, field_name)
    value_as_str = cast(str, value)
    if not value_as_str.strip():
        raise ValueError(f"{field_name} cannot be empty")
    return value_as_str


def _require_string(value: object, field_name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    return value


def _require_int(value: object, field_name: str) -> int:
    if type(value) is not int:
        raise TypeError(f"{field_name} must be an integer")
    return value


def _plain_authority_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {
            str(key): _plain_authority_value(nested)
            for key, nested in value.items()
        }
    if isinstance(value, tuple):
        return [_plain_authority_value(item) for item in value]
    return value


__all__ = (
    "RUNNER_DISPATCH_RECORD_KIND",
    "RUNNER_DISPATCH_SCHEMA_VERSION",
    "RUNNER_RESULT_RECORD_KIND",
    "RUNNER_RESULT_SCHEMA_VERSION",
    "RunnerAdapterProvenance",
    "RunnerDispatchEnvelope",
    "RunnerResultEvidence",
    "runner_result_evidence_bytes",
    "runner_result_evidence_digest",
    "runner_result_evidence_from_payload",
    "runner_result_payload",
)
