"""Runner adapter contracts.

Adapters prepare candidate evidence from external runner activity. They do not
decide terminal legality and they do not mutate runtime state.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from hashlib import sha256
from math import isfinite
from types import MappingProxyType
from typing import ClassVar, Protocol, TypeAlias, TypeVar, cast

from millrace.contracts.compiled_plan import (
    ArtifactSchemaDeclaration,
    AuthorityValue,
    RunnerComponentPin,
    RunnerTerminalResultMapping,
    freeze_authority_value,
)
from millrace.contracts.runner import (
    RunnerAdapterProvenance,
    RunnerDispatchEnvelope,
    RunnerResultEvidence,
)

_ERROR_KINDS = frozenset(
    {
        "timeout",
        "cancelled",
        "missing_opt_in_config",
        "invocation_failed",
        "result_parse_failed",
        "unsupported_adapter_kind",
        "input_too_large",
        "output_too_large",
        "redaction_refused",
        "selected_authority_refused",
    },
)
_RESERVED_UNSUPPORTED_ADAPTER_KINDS = frozenset({"local_subprocess"})
START_REFUSAL_DIAGNOSTIC_MAX_BYTES = 16 * 1024
RUNNER_CANCELLATION_DIAGNOSTIC_MAX_BYTES = 64 * 1024
T = TypeVar("T")


class AdapterResolverError(ValueError):
    """Raised when selected adapter authority cannot resolve locally."""


class AdapterEvidenceConversionError(ValueError):
    """Raised when adapter output cannot become runner result evidence."""


class RunnerAdapter(Protocol):
    """Protocol implemented by reviewed runner-specific adapters."""

    adapter_kind: str

    def start_session(
        self,
        request: AdapterInvocationRequest,
    ) -> RunnerSessionStartOutcome:
        """Start one externally fenced runner session."""

    def reconcile_session(
        self,
        request: RunnerSessionReconcileRequest,
    ) -> RunnerSessionReconcileOutcome:
        """Reconcile one durable session without guessing its outcome."""


class RunnerSessionHandle(Protocol):
    """Process-local control surface for one runner session."""

    def poll_completion(self) -> AdapterInvocationOutcome | None: ...

    def request_cancel(self) -> RunnerCancellationOperationResult: ...

    def terminate(self) -> RunnerCancellationOperationResult: ...

    def kill(self) -> RunnerCancellationOperationResult: ...

    def cleanup(self) -> RunnerCleanupResult: ...


@dataclass(frozen=True, slots=True, repr=False)
class RedactionPolicy:
    policy_id: str
    secret_tokens: tuple[str, ...] = ()

    def __init_subclass__(cls, **kwargs: object) -> None:
        super(RedactionPolicy, cls).__init_subclass__(**kwargs)
        setattr(cls, "__repr__", RedactionPolicy.__repr__)
        setattr(cls, "__str__", RedactionPolicy.__repr__)

    def __post_init__(self) -> None:
        _require_nonblank_string(
            object.__getattribute__(self, "policy_id"),
            "policy_id",
        )
        secrets = sorted(
            _coerce_string_tuple(
                object.__getattribute__(self, "secret_tokens"),
                "secret_tokens",
            ),
            key=len,
            reverse=True,
        )
        object.__setattr__(
            self,
            "secret_tokens",
            tuple(secrets),
        )

    def redact_text(self, value: str) -> str:
        return _redact_text(value, self)

    def redact_authority_value(self, value: object) -> AuthorityValue:
        return _redact_authority_value(value, self)

    def __repr__(self) -> str:
        return (
            "RedactionPolicy("
            f"policy_id={_redact_text(_policy_id(self), self)!r}, "
            f"secret_token_count={len(_policy_secret_tokens(self))}"
            ")"
        )

    __str__ = __repr__


@dataclass(frozen=True, slots=True)
class AdapterLocalConfig:
    adapters: Mapping[str, RunnerAdapter] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.adapters, Mapping):
            raise TypeError("adapters must be a mapping")
        object.__setattr__(self, "adapters", MappingProxyType(dict(self.adapters)))


def resolve_adapter(
    adapter_kind: str,
    local_config: AdapterLocalConfig,
) -> RunnerAdapter:
    selected_kind = _require_nonblank_string(adapter_kind, "adapter_kind")
    if selected_kind in _RESERVED_UNSUPPORTED_ADAPTER_KINDS:
        raise AdapterResolverError(f"unsupported adapter_kind: {selected_kind}")
    if not isinstance(local_config, AdapterLocalConfig):
        raise TypeError("local_config must be AdapterLocalConfig")

    adapter = local_config.adapters.get(selected_kind)
    if adapter is None:
        raise AdapterResolverError(f"unsupported adapter_kind: {selected_kind}")

    resolved_kind = getattr(adapter, "adapter_kind", None)
    if resolved_kind != selected_kind:
        raise AdapterResolverError(
            "local adapter config cannot remap selected adapter_kind",
        )
    if not callable(getattr(adapter, "start_session", None)):
        raise AdapterResolverError("resolved adapter must implement start_session")
    if not callable(getattr(adapter, "reconcile_session", None)):
        raise AdapterResolverError("resolved adapter must implement reconcile_session")
    return adapter


@dataclass(frozen=True, slots=True, repr=False)
class AdapterInvocationRequest:
    adapter_id: str
    selected_runner_binding_id: str
    selected_adapter_kind: str
    dispatch_envelope: RunnerDispatchEnvelope
    session_id: str
    dispatch_generation: int
    session_fencing_token: str
    timeout_seconds: float
    correlation_id: str
    redaction_policy: RedactionPolicy
    selected_asset_material: Mapping[str, AuthorityValue] = field(default_factory=dict)
    environment_policy_ref: str | None = None
    local_config_ref: str | None = None
    cancellation_token: str | None = None
    selected_component_pin: RunnerComponentPin | None = None
    selected_terminal_result_mappings: tuple[RunnerTerminalResultMapping, ...] = ()
    selected_artifact_schemas: tuple[ArtifactSchemaDeclaration, ...] = ()

    def __post_init__(self) -> None:
        _require_nonblank_string(self.adapter_id, "adapter_id")
        _require_nonblank_string(
            self.selected_runner_binding_id,
            "selected_runner_binding_id",
        )
        _require_nonblank_string(self.selected_adapter_kind, "selected_adapter_kind")
        if not isinstance(self.dispatch_envelope, RunnerDispatchEnvelope):
            raise TypeError("dispatch_envelope must be RunnerDispatchEnvelope")
        if self.selected_runner_binding_id != self.dispatch_envelope.runner_binding_id:
            raise ValueError(
                "selected_runner_binding_id must match dispatch envelope",
            )
        _require_nonblank_string(self.session_id, "session_id")
        _require_int(self.dispatch_generation, "dispatch_generation")
        _require_nonblank_string(
            self.session_fencing_token,
            "session_fencing_token",
        )
        if self.session_id != self.dispatch_envelope.session_id:
            raise ValueError("session_id must match dispatch envelope")
        if self.dispatch_generation != self.dispatch_envelope.dispatch_generation:
            raise ValueError("dispatch_generation must match dispatch envelope")
        if (
            self.session_fencing_token
            != self.dispatch_envelope.session_fencing_token
        ):
            raise ValueError("session_fencing_token must match dispatch envelope")
        _require_positive_number(self.timeout_seconds, "timeout_seconds")
        _require_nonblank_string(self.correlation_id, "correlation_id")
        if not isinstance(self.redaction_policy, RedactionPolicy):
            raise TypeError("redaction_policy must be RedactionPolicy")
        object.__setattr__(
            self,
            "redaction_policy",
            canonicalize_redaction_policy(self.redaction_policy),
        )
        object.__setattr__(
            self,
            "selected_asset_material",
            _coerce_payload_mapping(
                self.selected_asset_material,
                "selected_asset_material",
            ),
        )
        _require_optional_nonblank_string(
            self.environment_policy_ref,
            "environment_policy_ref",
        )
        _require_optional_nonblank_string(self.local_config_ref, "local_config_ref")
        _require_optional_nonblank_string(self.cancellation_token, "cancellation_token")
        if self.selected_component_pin is not None and not isinstance(
            self.selected_component_pin,
            RunnerComponentPin,
        ):
            raise TypeError("selected_component_pin must be RunnerComponentPin or None")
        object.__setattr__(
            self,
            "selected_terminal_result_mappings",
            _coerce_record_tuple(
                self.selected_terminal_result_mappings,
                RunnerTerminalResultMapping,
                "selected_terminal_result_mappings",
                key=lambda item: (
                    str(item.stage_kind_id),
                    item.runner_result_id,
                    str(item.outcome_id),
                ),
            ),
        )
        object.__setattr__(
            self,
            "selected_artifact_schemas",
            _coerce_record_tuple(
                self.selected_artifact_schemas,
                ArtifactSchemaDeclaration,
                "selected_artifact_schemas",
                key=lambda item: (str(item.id),),
            ),
        )
        _validate_selected_projection_coherence(self)

    def __repr__(self) -> str:
        adapter_id = _redact_text(self.adapter_id, self.redaction_policy)
        adapter_kind = _redact_text(
            self.selected_adapter_kind,
            self.redaction_policy,
        )
        policy_id = _redact_text(
            _policy_id(self.redaction_policy),
            self.redaction_policy,
        )
        env_ref_present = self.environment_policy_ref is not None
        local_ref_present = self.local_config_ref is not None
        component_pin_present = self.selected_component_pin is not None
        terminal_mapping_count = len(self.selected_terminal_result_mappings)
        artifact_schema_count = len(self.selected_artifact_schemas)
        return (
            "AdapterInvocationRequest("
            f"adapter_id={adapter_id!r}, "
            f"selected_adapter_kind={adapter_kind!r}, "
            f"selected_runner_binding_present={bool(self.selected_runner_binding_id)}, "
            "dispatch_envelope=<redacted>, "
            f"timeout_seconds={self.timeout_seconds!r}, "
            f"correlation_id_present={bool(self.correlation_id)}, "
            f"redaction_policy_id={policy_id!r}, "
            f"selected_asset_count={len(self.selected_asset_material)}, "
            f"environment_policy_ref_present={env_ref_present}, "
            f"local_config_ref_present={local_ref_present}, "
            f"cancellation_token_present={self.cancellation_token is not None}, "
            f"selected_component_pin_present={component_pin_present}, "
            f"selected_terminal_mapping_count={terminal_mapping_count}, "
            f"selected_artifact_schema_count={artifact_schema_count}"
            ")"
        )

    __str__ = __repr__


@dataclass(frozen=True, slots=True, repr=False)
class DispatchEcho:
    run_id: str
    session_id: str
    dispatch_generation: int
    session_fencing_token: str
    claim_id: str
    generation: int
    fencing_token: str
    plan_fingerprint: str
    stage_kind_id: str
    graph_node_id: str
    runner_binding_id: str
    correlation_id: str

    def __post_init__(self) -> None:
        _require_nonblank_string(self.run_id, "run_id")
        _require_nonblank_string(self.session_id, "session_id")
        _require_int(self.dispatch_generation, "dispatch_generation")
        _require_nonblank_string(
            self.session_fencing_token,
            "session_fencing_token",
        )
        _require_nonblank_string(self.claim_id, "claim_id")
        _require_int(self.generation, "generation")
        _require_nonblank_string(self.fencing_token, "fencing_token")
        _require_nonblank_string(self.plan_fingerprint, "plan_fingerprint")
        _require_nonblank_string(self.stage_kind_id, "stage_kind_id")
        _require_nonblank_string(self.graph_node_id, "graph_node_id")
        _require_nonblank_string(self.runner_binding_id, "runner_binding_id")
        _require_nonblank_string(self.correlation_id, "correlation_id")

    @classmethod
    def from_dispatch_envelope(
        cls,
        envelope: RunnerDispatchEnvelope,
        *,
        correlation_id: str,
    ) -> DispatchEcho:
        return cls(
            run_id=envelope.run_id,
            session_id=envelope.session_id,
            dispatch_generation=envelope.dispatch_generation,
            session_fencing_token=envelope.session_fencing_token,
            claim_id=envelope.claim_id,
            generation=envelope.generation,
            fencing_token=envelope.fencing_token,
            plan_fingerprint=envelope.plan_fingerprint,
            stage_kind_id=envelope.stage_kind_id,
            graph_node_id=envelope.graph_node_id,
            runner_binding_id=envelope.runner_binding_id,
            correlation_id=correlation_id,
        )

    def validate_against(
        self,
        envelope: RunnerDispatchEnvelope,
        *,
        correlation_id: str,
    ) -> None:
        expected = DispatchEcho.from_dispatch_envelope(
            envelope,
            correlation_id=correlation_id,
        )
        if self != expected:
            raise AdapterEvidenceConversionError("dispatch echo mismatch")

    def __repr__(self) -> str:
        return "DispatchEcho(<redacted>)"

    __str__ = __repr__


@dataclass(frozen=True, slots=True, repr=False)
class AdapterSuccessResult:
    outcome_kind: ClassVar[str] = "success"

    adapter_id: str
    dispatch_echo: DispatchEcho
    redaction_policy_id: str
    marker: str | None = None
    adapter_provenance: RunnerAdapterProvenance | None = None
    captured_stdout: str | None = None
    captured_stderr: str | None = None
    structured_provider_response: Mapping[str, AuthorityValue] = field(
        default_factory=dict,
    )
    artifact_payload_candidate: Mapping[str, AuthorityValue] | None = None
    observation_payload_candidate: Mapping[str, AuthorityValue] | None = None
    evidence_construction_diagnostics: Mapping[str, AuthorityValue] = field(
        default_factory=dict,
    )

    def __post_init__(self) -> None:
        _require_nonblank_string(self.adapter_id, "adapter_id")
        if not isinstance(self.dispatch_echo, DispatchEcho):
            raise TypeError("dispatch_echo must be DispatchEcho")
        _require_nonblank_string(self.redaction_policy_id, "redaction_policy_id")
        _require_optional_nonblank_string(self.marker, "marker")
        object.__setattr__(
            self,
            "adapter_provenance",
            _copy_adapter_provenance(self.adapter_provenance),
        )
        _require_optional_string(self.captured_stdout, "captured_stdout")
        _require_optional_string(self.captured_stderr, "captured_stderr")
        object.__setattr__(
            self,
            "structured_provider_response",
            _coerce_payload_mapping(
                self.structured_provider_response,
                "structured_provider_response",
            ),
        )
        if self.artifact_payload_candidate is not None:
            object.__setattr__(
                self,
                "artifact_payload_candidate",
                _coerce_payload_mapping(
                    self.artifact_payload_candidate,
                    "artifact_payload_candidate",
                ),
            )
        if self.observation_payload_candidate is not None:
            object.__setattr__(
                self,
                "observation_payload_candidate",
                _coerce_payload_mapping(
                    self.observation_payload_candidate,
                    "observation_payload_candidate",
                ),
            )
        object.__setattr__(
            self,
            "evidence_construction_diagnostics",
            _coerce_payload_mapping(
                self.evidence_construction_diagnostics,
                "evidence_construction_diagnostics",
            ),
        )

    def __repr__(self) -> str:
        provider_count = len(self.structured_provider_response)
        artifact_present = self.artifact_payload_candidate is not None
        observation_present = self.observation_payload_candidate is not None
        diagnostic_count = len(self.evidence_construction_diagnostics)
        return (
            "AdapterSuccessResult("
            "adapter_id=<redacted>, "
            f"dispatch_echo={self.dispatch_echo!r}, "
            "redaction_policy_id=<redacted>, "
            f"marker_present={self.marker is not None}, "
            f"adapter_provenance_present={self.adapter_provenance is not None}, "
            f"captured_stdout_present={self.captured_stdout is not None}, "
            f"captured_stderr_present={self.captured_stderr is not None}, "
            f"structured_provider_response_count={provider_count}, "
            f"artifact_payload_candidate_present={artifact_present}, "
            f"observation_payload_candidate_present={observation_present}, "
            f"diagnostic_count={diagnostic_count}"
            ")"
        )

    __str__ = __repr__

    @classmethod
    def from_unredacted(
        cls,
        *,
        adapter_id: str,
        dispatch_echo: DispatchEcho,
        redaction_policy: RedactionPolicy,
        marker: str | None = None,
        adapter_provenance: RunnerAdapterProvenance | None = None,
        captured_stdout: str | None = None,
        captured_stderr: str | None = None,
        structured_provider_response: Mapping[str, object] | None = None,
        artifact_payload_candidate: Mapping[str, object] | None = None,
        observation_payload_candidate: Mapping[str, object] | None = None,
        evidence_construction_diagnostics: Mapping[str, object] | None = None,
    ) -> AdapterSuccessResult:
        effective_policy = canonicalize_redaction_policy(redaction_policy)
        return cls(
            adapter_id=_redact_text(adapter_id, effective_policy),
            dispatch_echo=dispatch_echo,
            redaction_policy_id=_redact_text(
                _policy_id(effective_policy),
                effective_policy,
            ),
            marker=(None if marker is None else _redact_text(marker, effective_policy)),
            adapter_provenance=adapter_provenance,
            captured_stdout=(
                None
                if captured_stdout is None
                else _redact_text(captured_stdout, effective_policy)
            ),
            captured_stderr=(
                None
                if captured_stderr is None
                else _redact_text(captured_stderr, effective_policy)
            ),
            structured_provider_response=_redact_mapping(
                structured_provider_response or {},
                effective_policy,
            ),
            artifact_payload_candidate=(
                None
                if artifact_payload_candidate is None
                else _redact_mapping(artifact_payload_candidate, effective_policy)
            ),
            observation_payload_candidate=(
                None
                if observation_payload_candidate is None
                else _redact_mapping(observation_payload_candidate, effective_policy)
            ),
            evidence_construction_diagnostics=_redact_mapping(
                evidence_construction_diagnostics or {},
                effective_policy,
            ),
        )


@dataclass(frozen=True, slots=True, repr=False)
class AdapterErrorResult:
    outcome_kind: ClassVar[str] = "error"

    adapter_id: str
    error_kind: str
    redaction_policy_id: str
    dispatch_echo: DispatchEcho | None = None
    diagnostics: Mapping[str, AuthorityValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_nonblank_string(self.adapter_id, "adapter_id")
        _require_nonblank_string(self.redaction_policy_id, "redaction_policy_id")
        if self.error_kind not in _ERROR_KINDS:
            raise ValueError(f"unsupported adapter error kind: {self.error_kind}")
        if self.dispatch_echo is not None and not isinstance(
            self.dispatch_echo,
            DispatchEcho,
        ):
            raise TypeError("dispatch_echo must be DispatchEcho")
        object.__setattr__(
            self,
            "diagnostics",
            _coerce_payload_mapping(self.diagnostics, "diagnostics"),
        )

    def __repr__(self) -> str:
        return (
            "AdapterErrorResult("
            "adapter_id=<redacted>, "
            f"error_kind={self.error_kind!r}, "
            "redaction_policy_id=<redacted>, "
            f"dispatch_echo_present={self.dispatch_echo is not None}, "
            f"diagnostic_count={len(self.diagnostics)}"
            ")"
        )

    __str__ = __repr__

    @classmethod
    def from_unredacted(
        cls,
        *,
        adapter_id: str,
        error_kind: str,
        redaction_policy: RedactionPolicy,
        dispatch_echo: DispatchEcho | None = None,
        diagnostics: Mapping[str, object] | None = None,
    ) -> AdapterErrorResult:
        effective_policy = canonicalize_redaction_policy(redaction_policy)
        return cls(
            adapter_id=_redact_text(adapter_id, effective_policy),
            error_kind=error_kind,
            dispatch_echo=dispatch_echo,
            diagnostics=_redact_mapping(diagnostics or {}, effective_policy),
            redaction_policy_id=_redact_text(
                _policy_id(effective_policy),
                effective_policy,
            ),
        )


def start_refusal_diagnostic_bytes(outcome: AdapterErrorResult) -> bytes:
    if not isinstance(outcome, AdapterErrorResult):
        raise TypeError("outcome must be AdapterErrorResult")
    payload = _canonical_json_bytes(
        {
            "diagnostics": _plain_authority_value(outcome.diagnostics),
            "error_kind": outcome.error_kind,
        }
    )
    if len(payload) <= START_REFUSAL_DIAGNOSTIC_MAX_BYTES:
        return payload
    return _canonical_json_bytes(
        {
            "diagnostics": {
                "full_diagnostic_digest": (
                    f"sha256:{sha256(payload).hexdigest()}"
                ),
                "observed_bytes": len(payload),
                "truncated": True,
            },
            "error_kind": outcome.error_kind,
        }
    )


def start_refusal_diagnostic_digest(outcome: AdapterErrorResult) -> str:
    return f"sha256:{sha256(start_refusal_diagnostic_bytes(outcome)).hexdigest()}"


AdapterInvocationOutcome: TypeAlias = AdapterSuccessResult | AdapterErrorResult


@dataclass(frozen=True, slots=True)
class StartedSession:
    dispatch_echo: DispatchEcho
    handle: RunnerSessionHandle
    handle_id: str
    durable_locator_metadata: Mapping[str, AuthorityValue]
    outcome_kind: ClassVar[str] = "started"

    def __post_init__(self) -> None:
        _require_dispatch_echo(self.dispatch_echo)
        _require_session_handle(self.handle)
        _require_nonblank_string(self.handle_id, "handle_id")
        object.__setattr__(
            self,
            "durable_locator_metadata",
            _coerce_payload_mapping(
                self.durable_locator_metadata,
                "durable_locator_metadata",
            ),
        )


@dataclass(frozen=True, slots=True)
class StartRefusedBeforeExternalWork:
    dispatch_echo: DispatchEcho
    adapter_error: AdapterErrorResult
    diagnostic_digest: str
    outcome_kind: ClassVar[str] = "refused_before_external_work"

    def __post_init__(self) -> None:
        _require_dispatch_echo(self.dispatch_echo)
        if not isinstance(self.adapter_error, AdapterErrorResult):
            raise TypeError("adapter_error must be AdapterErrorResult")
        _require_sha256_digest(self.diagnostic_digest, "diagnostic_digest")
        if self.diagnostic_digest != start_refusal_diagnostic_digest(
            self.adapter_error
        ):
            raise ValueError(
                "diagnostic_digest must match the bounded adapter error diagnostic"
            )


@dataclass(frozen=True, slots=True)
class StartIndeterminate:
    dispatch_echo: DispatchEcho
    durable_locator_metadata: Mapping[str, AuthorityValue] | None
    diagnostic_digest: str
    outcome_kind: ClassVar[str] = "indeterminate"

    def __post_init__(self) -> None:
        _require_dispatch_echo(self.dispatch_echo)
        if self.durable_locator_metadata is not None:
            object.__setattr__(
                self,
                "durable_locator_metadata",
                _coerce_payload_mapping(
                    self.durable_locator_metadata,
                    "durable_locator_metadata",
                ),
            )
        _require_sha256_digest(self.diagnostic_digest, "diagnostic_digest")


RunnerSessionStartOutcome: TypeAlias = (
    StartedSession | StartRefusedBeforeExternalWork | StartIndeterminate
)


@dataclass(frozen=True, slots=True)
class RunnerSessionReconcileRequest:
    invocation_request: AdapterInvocationRequest
    durable_locator_metadata: Mapping[str, AuthorityValue]

    def __post_init__(self) -> None:
        if not isinstance(self.invocation_request, AdapterInvocationRequest):
            raise TypeError("invocation_request must be AdapterInvocationRequest")
        object.__setattr__(
            self,
            "durable_locator_metadata",
            _coerce_payload_mapping(
                self.durable_locator_metadata,
                "durable_locator_metadata",
            ),
        )


@dataclass(frozen=True, slots=True)
class VerifiedLive:
    dispatch_echo: DispatchEcho
    handle: RunnerSessionHandle
    handle_id: str
    durable_locator_metadata: Mapping[str, AuthorityValue]
    outcome_kind: ClassVar[str] = "verified_live"

    def __post_init__(self) -> None:
        _require_dispatch_echo(self.dispatch_echo)
        _require_session_handle(self.handle)
        _require_nonblank_string(self.handle_id, "handle_id")
        object.__setattr__(
            self,
            "durable_locator_metadata",
            _coerce_payload_mapping(
                self.durable_locator_metadata,
                "durable_locator_metadata",
            ),
        )


@dataclass(frozen=True, slots=True)
class Terminal:
    dispatch_echo: DispatchEcho
    adapter_outcome: AdapterInvocationOutcome
    cleanup_disposition: str
    outcome_kind: ClassVar[str] = "terminal"

    def __post_init__(self) -> None:
        _require_dispatch_echo(self.dispatch_echo)
        if not isinstance(
            self.adapter_outcome,
            (AdapterSuccessResult, AdapterErrorResult),
        ):
            raise TypeError("adapter_outcome must be an adapter invocation outcome")
        if self.cleanup_disposition not in {"not_required", "complete"}:
            raise ValueError("terminal cleanup must be complete or not_required")


@dataclass(frozen=True, slots=True)
class CleanupPending:
    dispatch_echo: DispatchEcho
    handle: RunnerSessionHandle
    handle_id: str
    outcome_kind: ClassVar[str] = "cleanup_pending"

    def __post_init__(self) -> None:
        _require_dispatch_echo(self.dispatch_echo)
        _require_session_handle(self.handle)
        _require_nonblank_string(self.handle_id, "handle_id")


@dataclass(frozen=True, slots=True)
class Unsupported:
    dispatch_echo: DispatchEcho
    outcome_kind: ClassVar[str] = "unsupported"

    def __post_init__(self) -> None:
        _require_dispatch_echo(self.dispatch_echo)


@dataclass(frozen=True, slots=True)
class Contradiction:
    dispatch_echo: DispatchEcho
    diagnostic_digest: str
    outcome_kind: ClassVar[str] = "contradiction"

    def __post_init__(self) -> None:
        _require_dispatch_echo(self.dispatch_echo)
        _require_sha256_digest(self.diagnostic_digest, "diagnostic_digest")


RunnerSessionReconcileOutcome: TypeAlias = (
    VerifiedLive | Terminal | CleanupPending | Unsupported | Contradiction
)


@dataclass(frozen=True, slots=True)
class RunnerCancellationOperationResult:
    operation: str
    result: str
    started_at: int
    completed_at: int
    diagnostic: Mapping[str, AuthorityValue]
    diagnostic_digest: str

    def __post_init__(self) -> None:
        if self.operation not in {
            "cooperative_cancel",
            "terminate",
            "kill",
            "transport_cleanup",
        }:
            raise ValueError("unsupported cancellation operation")
        if self.result not in {"succeeded", "failed", "timed_out", "unsupported"}:
            raise ValueError("unsupported cancellation result")
        _require_timestamps(self.started_at, self.completed_at)
        object.__setattr__(
            self,
            "diagnostic",
            _coerce_payload_mapping(self.diagnostic, "diagnostic"),
        )
        if len(_canonical_json_bytes(_plain_authority_value(self.diagnostic))) > (
            RUNNER_CANCELLATION_DIAGNOSTIC_MAX_BYTES
        ):
            raise ValueError("diagnostic content exceeds cancellation bound")
        _require_sha256_digest(self.diagnostic_digest, "diagnostic_digest")
        if self.diagnostic_digest != runner_cancellation_diagnostic_digest(
            self.diagnostic
        ):
            raise ValueError("diagnostic_digest must match diagnostic content")


@dataclass(frozen=True, slots=True)
class RunnerCleanupResult:
    disposition: str
    started_at: int
    completed_at: int
    diagnostic: Mapping[str, AuthorityValue]
    diagnostic_digest: str

    def __post_init__(self) -> None:
        if self.disposition not in {"not_required", "complete", "orphan_risk"}:
            raise ValueError("unsupported cleanup disposition")
        _require_timestamps(self.started_at, self.completed_at)
        object.__setattr__(
            self,
            "diagnostic",
            _coerce_payload_mapping(self.diagnostic, "diagnostic"),
        )
        if len(_canonical_json_bytes(_plain_authority_value(self.diagnostic))) > (
            RUNNER_CANCELLATION_DIAGNOSTIC_MAX_BYTES
        ):
            raise ValueError("diagnostic content exceeds cancellation bound")
        _require_sha256_digest(self.diagnostic_digest, "diagnostic_digest")
        if self.diagnostic_digest != runner_cancellation_diagnostic_digest(
            self.diagnostic
        ):
            raise ValueError("diagnostic_digest must match diagnostic content")


def runner_cancellation_diagnostic_digest(
    diagnostic: Mapping[str, AuthorityValue],
) -> str:
    payload = _canonical_json_bytes(_plain_authority_value(diagnostic))
    return f"sha256:{sha256(payload).hexdigest()}"


def runner_evidence_from_adapter_outcome(
    outcome: AdapterInvocationOutcome,
    request: AdapterInvocationRequest,
) -> RunnerResultEvidence:
    if not isinstance(outcome, AdapterSuccessResult):
        raise TypeError("only AdapterSuccessResult can convert to runner evidence")
    if not isinstance(request, AdapterInvocationRequest):
        raise TypeError("request must be AdapterInvocationRequest")
    dispatch_envelope = request.dispatch_envelope
    outcome.dispatch_echo.validate_against(
        dispatch_envelope,
        correlation_id=request.correlation_id,
    )
    marker = _require_nonblank_string(outcome.marker, "marker")
    try:
        adapter_provenance = _copy_adapter_provenance(outcome.adapter_provenance)
    except (TypeError, ValueError) as exc:
        raise AdapterEvidenceConversionError("adapter provenance is malformed") from exc
    if adapter_provenance is not None:
        selected_pin = request.selected_component_pin
        if adapter_provenance.adapter_kind != request.selected_adapter_kind:
            raise AdapterEvidenceConversionError("adapter provenance kind mismatch")
        if (
            selected_pin is None
            or adapter_provenance.component_descriptor_sha256
            != selected_pin.descriptor_sha256
        ):
            raise AdapterEvidenceConversionError(
                "adapter provenance component descriptor mismatch"
            )
        if adapter_provenance.correlation_id != request.correlation_id:
            raise AdapterEvidenceConversionError(
                "adapter provenance correlation mismatch"
            )
    return RunnerResultEvidence(
        run_id=dispatch_envelope.run_id,
        session_id=dispatch_envelope.session_id,
        dispatch_generation=dispatch_envelope.dispatch_generation,
        session_fencing_token=dispatch_envelope.session_fencing_token,
        plan_fingerprint=dispatch_envelope.plan_fingerprint,
        claim_id=dispatch_envelope.claim_id,
        generation=dispatch_envelope.generation,
        fencing_token=dispatch_envelope.fencing_token,
        stage_kind_id=dispatch_envelope.stage_kind_id,
        graph_node_id=dispatch_envelope.graph_node_id,
        runner_binding_id=dispatch_envelope.runner_binding_id,
        marker=marker,
        adapter_provenance=adapter_provenance,
        observation_payload=outcome.observation_payload_candidate or {},
        artifact_payload=outcome.artifact_payload_candidate or {},
    )


def _copy_adapter_provenance(
    value: RunnerAdapterProvenance | None,
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


def _redact_mapping(
    value: Mapping[str, object],
    policy: RedactionPolicy,
) -> Mapping[str, AuthorityValue]:
    redacted: dict[str, AuthorityValue] = {}
    for key, nested_value in value.items():
        redacted_key = _redact_text(_require_string(key, "mapping key"), policy)
        if redacted_key in redacted:
            raise ValueError("redacted mapping keys collide")
        redacted[redacted_key] = _redact_authority_value(nested_value, policy)
    return MappingProxyType(redacted)


def _redact_authority_value(value: object, policy: RedactionPolicy) -> AuthorityValue:
    if isinstance(value, str):
        return _redact_text(value, policy)
    if isinstance(value, Mapping):
        return freeze_authority_value(_redact_nested_mapping(value, policy))
    if isinstance(value, (list, tuple)):
        return freeze_authority_value(
            tuple(_redact_authority_value(item, policy) for item in value),
        )
    return freeze_authority_value(value)


def _redact_nested_mapping(
    value: Mapping[object, object],
    policy: RedactionPolicy,
) -> dict[str, AuthorityValue]:
    redacted: dict[str, AuthorityValue] = {}
    for key, nested_value in value.items():
        redacted_key = _redact_text(
            _require_string(key, "authority mapping key"),
            policy,
        )
        if redacted_key in redacted:
            raise ValueError("redacted mapping keys collide")
        redacted[redacted_key] = _redact_authority_value(nested_value, policy)
    return redacted


def _redact_text(value: str, policy: RedactionPolicy) -> str:
    redacted = value
    for secret in _policy_secret_tokens(policy):
        redacted = redacted.replace(secret, "[REDACTED]")
    return redacted


def canonicalize_redaction_policy(policy: RedactionPolicy) -> RedactionPolicy:
    if not isinstance(policy, RedactionPolicy):
        raise TypeError("redaction_policy must be RedactionPolicy")
    return RedactionPolicy(
        policy_id=_policy_id(policy),
        secret_tokens=_policy_secret_tokens(policy),
    )


def _policy_id(policy: RedactionPolicy) -> str:
    return _require_nonblank_string(
        object.__getattribute__(policy, "policy_id"),
        "policy_id",
    )


def _policy_secret_tokens(policy: RedactionPolicy) -> tuple[str, ...]:
    return _coerce_string_tuple(
        object.__getattribute__(policy, "secret_tokens"),
        "secret_tokens",
    )


def _coerce_payload_mapping(
    value: object,
    field_name: str,
) -> Mapping[str, AuthorityValue]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be a mapping")
    coerced: dict[str, AuthorityValue] = {}
    for key, nested_value in cast(Mapping[object, object], value).items():
        if not isinstance(key, str):
            raise ValueError(f"{field_name} keys must be strings")
        coerced[key] = freeze_authority_value(nested_value)
    return MappingProxyType(coerced)


def _coerce_record_tuple(
    value: Iterable[T],
    expected_type: type[T],
    field_name: str,
    *,
    key: Callable[[T], tuple[str, ...]],
) -> tuple[T, ...]:
    if isinstance(value, (str, bytes)):
        raise TypeError(f"{field_name} must be an iterable of records")
    try:
        records = tuple(value)
    except TypeError as exc:
        raise TypeError(f"{field_name} must be an iterable of records") from exc
    if any(not isinstance(item, expected_type) for item in records):
        raise TypeError(f"{field_name} contains an unsupported record")
    return tuple(sorted(records, key=key))


def _validate_selected_projection_coherence(
    request: AdapterInvocationRequest,
) -> None:
    option_outcome_counts: dict[str, int] = {}
    terminal_artifact_schema_ids: set[str] = set()
    declared_schema_ids = set(request.dispatch_envelope.artifact_schema_ids)
    for option in request.dispatch_envelope.terminal_options:
        outcome_id = cast(str, option["outcome_id"])
        option_outcome_counts[outcome_id] = option_outcome_counts.get(outcome_id, 0) + 1
        artifact_schema_id = option["artifact_schema_id"]
        if artifact_schema_id is not None:
            schema_id = cast(str, artifact_schema_id)
            if schema_id not in declared_schema_ids:
                raise ValueError(
                    "terminal option artifact schema must be declared by "
                    "dispatch envelope",
                )
            terminal_artifact_schema_ids.add(schema_id)

    mapping_result_ids: set[str] = set()
    mapping_outcome_ids: set[str] = set()
    for mapping in request.selected_terminal_result_mappings:
        if str(mapping.stage_kind_id) != request.dispatch_envelope.stage_kind_id:
            raise ValueError(
                "selected terminal mapping stage must match dispatch stage"
            )
        outcome_id = str(mapping.outcome_id)
        if (
            mapping.runner_result_id in mapping_result_ids
            or outcome_id in mapping_outcome_ids
        ):
            raise ValueError("duplicate mapping result or outcome")
        if option_outcome_counts.get(outcome_id) != 1:
            raise ValueError(
                "selected terminal mapping outcome must name one terminal option"
            )
        mapping_result_ids.add(mapping.runner_result_id)
        mapping_outcome_ids.add(outcome_id)

    projected_schema_counts: dict[str, int] = {}
    for schema in request.selected_artifact_schemas:
        schema_id = str(schema.id)
        projected_schema_counts[schema_id] = (
            projected_schema_counts.get(schema_id, 0) + 1
        )
        if projected_schema_counts[schema_id] != 1:
            raise ValueError("duplicate projected artifact schema")
        if schema_id not in declared_schema_ids:
            raise ValueError(
                "projected artifact schema must be declared by dispatch envelope"
            )
        if schema_id not in terminal_artifact_schema_ids:
            raise ValueError(
                "projected artifact schema must be named by a terminal option"
            )

    if request.selected_terminal_result_mappings or request.selected_artifact_schemas:
        if any(
            projected_schema_counts.get(schema_id) != 1
            for schema_id in terminal_artifact_schema_ids
        ):
            raise ValueError(
                "terminal option artifact schema must be projected exactly once"
            )


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


def _require_optional_string(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    return _require_string(value, field_name)


def _require_nonblank_string(value: object, field_name: str) -> str:
    value_as_str = _require_string(value, field_name)
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


def _require_dispatch_echo(value: object) -> DispatchEcho:
    if not isinstance(value, DispatchEcho):
        raise TypeError("dispatch_echo must be DispatchEcho")
    return value


def _require_session_handle(value: object) -> None:
    for method_name in (
        "poll_completion",
        "request_cancel",
        "terminate",
        "kill",
        "cleanup",
    ):
        if not callable(getattr(value, method_name, None)):
            raise TypeError(
                f"handle must implement RunnerSessionHandle.{method_name}"
            )


def _require_sha256_digest(value: object, field_name: str) -> str:
    digest = _require_nonblank_string(value, field_name)
    hex_value = digest.removeprefix("sha256:")
    if (
        not digest.startswith("sha256:")
        or len(hex_value) != 64
        or any(character not in "0123456789abcdef" for character in hex_value)
    ):
        raise ValueError(f"{field_name} must be a sha256 digest")
    return digest


def _require_timestamps(started_at: object, completed_at: object) -> None:
    start = _require_int(started_at, "started_at")
    completed = _require_int(completed_at, "completed_at")
    if start < 0 or completed < start:
        raise ValueError("operation timestamps must be durable and monotonic")


def _plain_authority_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {
            str(key): _plain_authority_value(nested)
            for key, nested in value.items()
        }
    if isinstance(value, tuple):
        return [_plain_authority_value(item) for item in value]
    return value


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _require_positive_number(value: object, field_name: str) -> float:
    if type(value) not in {int, float}:
        raise TypeError(f"{field_name} must be a number")
    value_as_float = float(cast(int | float, value))
    if value_as_float <= 0:
        raise ValueError(f"{field_name} must be positive")
    if not isfinite(value_as_float):
        raise ValueError(f"{field_name} must be finite")
    return value_as_float


__all__ = (
    "START_REFUSAL_DIAGNOSTIC_MAX_BYTES",
    "RUNNER_CANCELLATION_DIAGNOSTIC_MAX_BYTES",
    "AdapterEvidenceConversionError",
    "AdapterErrorResult",
    "AdapterInvocationOutcome",
    "AdapterInvocationRequest",
    "AdapterLocalConfig",
    "AdapterResolverError",
    "AdapterSuccessResult",
    "CleanupPending",
    "Contradiction",
    "DispatchEcho",
    "RedactionPolicy",
    "RunnerAdapter",
    "RunnerCancellationOperationResult",
    "RunnerCleanupResult",
    "RunnerSessionHandle",
    "RunnerSessionReconcileOutcome",
    "RunnerSessionReconcileRequest",
    "RunnerSessionStartOutcome",
    "StartIndeterminate",
    "StartRefusedBeforeExternalWork",
    "StartedSession",
    "Terminal",
    "Unsupported",
    "VerifiedLive",
    "canonicalize_redaction_policy",
    "resolve_adapter",
    "runner_evidence_from_adapter_outcome",
    "runner_cancellation_diagnostic_digest",
    "start_refusal_diagnostic_bytes",
    "start_refusal_diagnostic_digest",
)
