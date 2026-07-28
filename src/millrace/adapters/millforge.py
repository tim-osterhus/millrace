"""Offline Millforge runner adapter with selected-authority translation only."""

from __future__ import annotations

import asyncio
import datetime
import hashlib
import json
import os
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from math import isfinite
from pathlib import Path
from types import MappingProxyType
from typing import Any, Protocol, cast

from millrace.adapters.runner_contract import (
    AdapterErrorResult,
    AdapterInvocationOutcome,
    AdapterInvocationRequest,
    AdapterSuccessResult,
    DispatchEcho,
    RedactionPolicy,
    RunnerCancellationOperationResult,
    RunnerCleanupResult,
    RunnerSessionReconcileRequest,
    RunnerSessionStartOutcome,
    StartedSession,
    StartIndeterminate,
    StartRefusedBeforeExternalWork,
    Unsupported,
    canonicalize_redaction_policy,
    runner_cancellation_diagnostic_digest,
    start_refusal_diagnostic_digest,
)
from millrace.contracts.compiled_plan import (
    ArtifactSchemaDeclaration,
    AuthorityValue,
    RunnerComponentPin,
    RunnerTerminalResultMapping,
)
from millrace.contracts.runner import RunnerAdapterProvenance
from millrace.contracts.schema import validate_schema, validate_schema_declaration

MILLFORGE_ADAPTER_KIND = "millforge"
_PROVIDER_STAGE = ("execution", "millforge-base", "millforge_base")
_MAX_SCHEMA_BYTES = 64 * 1024
_MAX_SCHEMA_DEPTH = 16
_MAX_SCHEMA_PROPERTIES = 64
_MAX_ARRAY_ITEMS = 1024
_MAX_STRING_LENGTH = 64 * 1024
_MAX_TASK_BYTES = 65_536
_SCHEMA_KEYS = frozenset(
    {
        "type",
        "required",
        "properties",
        "items",
        "enum",
        "const",
        "min_items",
        "min_length",
        "unique_by",
    }
)
_SCHEMA_TYPES = frozenset({"object", "array", "string", "integer", "boolean", "null"})


class MillforgeFacade(Protocol):
    """Public facade injected by local configuration."""

    descriptor: object
    components: object

    def invocation_evidence_for(self, request: object) -> object: ...

    async def execute(self, request: object) -> object: ...


@dataclass(frozen=True, slots=True, repr=False, init=False)
class _LiveMillforgeConfig:
    _model_profile_snapshot: bytes
    _secret_ref_snapshot: bytes

    def __init__(
        self,
        *,
        model_profile: Mapping[str, object],
        secret_ref: Mapping[str, object],
    ) -> None:
        object.__setattr__(
            self,
            "_model_profile_snapshot",
            _json_mapping_snapshot(model_profile, "model_profile"),
        )
        object.__setattr__(
            self,
            "_secret_ref_snapshot",
            _json_mapping_snapshot(secret_ref, "secret_ref"),
        )

    @property
    def model_profile(self) -> Mapping[str, object]:
        return _json_mapping_from_snapshot(self._model_profile_snapshot)

    @property
    def secret_ref(self) -> Mapping[str, object]:
        return _json_mapping_from_snapshot(self._secret_ref_snapshot)


@dataclass(frozen=True, slots=True, repr=False)
class MillforgeAdapterConfig:
    adapter_id: str
    workspace_root: Path
    timeout_seconds: float
    redaction_policy: RedactionPolicy
    facade: MillforgeFacade | None = None
    live_config: _LiveMillforgeConfig | None = None

    @classmethod
    def for_live(
        cls,
        *,
        adapter_id: str,
        workspace_root: Path,
        timeout_seconds: float,
        redaction_policy: RedactionPolicy,
        model_profile: Mapping[str, object],
        secret_ref: Mapping[str, object],
    ) -> MillforgeAdapterConfig:
        return cls(
            adapter_id=adapter_id,
            workspace_root=workspace_root,
            timeout_seconds=timeout_seconds,
            redaction_policy=redaction_policy,
            live_config=_LiveMillforgeConfig(
                model_profile=model_profile,
                secret_ref=secret_ref,
            ),
        )

    def __post_init__(self) -> None:
        if not isinstance(self.adapter_id, str) or not self.adapter_id.strip():
            raise ValueError("adapter_id must be nonblank")
        if (self.facade is None) == (self.live_config is None):
            raise ValueError("configure exactly one of facade or live_config")
        if self.facade is not None and (
            not callable(getattr(self.facade, "invocation_evidence_for", None))
            or not callable(getattr(self.facade, "execute", None))
        ):
            raise TypeError(
                "facade must expose public invocation and execution methods"
            )
        if self.live_config is not None and not isinstance(
            self.live_config,
            _LiveMillforgeConfig,
        ):
            raise TypeError("live_config must be _LiveMillforgeConfig")
        if not isinstance(self.workspace_root, Path):
            raise TypeError("workspace_root must be Path")
        if not self.workspace_root.is_absolute():
            raise ValueError("workspace_root must be absolute")
        if (
            type(self.timeout_seconds) not in {int, float}
            or not isfinite(float(self.timeout_seconds))
            or self.timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds must be finite and positive")
        if not isinstance(self.redaction_policy, RedactionPolicy):
            raise TypeError("redaction_policy must be RedactionPolicy")
        object.__setattr__(self, "workspace_root", self.workspace_root.resolve())
        object.__setattr__(
            self,
            "redaction_policy",
            canonicalize_redaction_policy(self.redaction_policy),
        )

    def __repr__(self) -> str:
        return (
            "MillforgeAdapterConfig("
            "adapter_id=<redacted>, "
            f"facade={'<injected>' if self.facade is not None else '<lazy-live>'}, "
            "workspace_root=<redacted>, "
            f"timeout_seconds={self.timeout_seconds!r}, "
            "redaction_policy_id=<redacted>"
            ")"
        )


class MillforgeAdapter:
    """Translate one selected Millrace dispatch through a local facade."""

    adapter_kind = MILLFORGE_ADAPTER_KIND

    def __init__(self, config: MillforgeAdapterConfig) -> None:
        if not isinstance(config, MillforgeAdapterConfig):
            raise TypeError("config must be MillforgeAdapterConfig")
        self._config = config

    @property
    def config(self) -> MillforgeAdapterConfig:
        return self._config

    def invoke(self, request: AdapterInvocationRequest) -> AdapterInvocationOutcome:
        if not isinstance(request, AdapterInvocationRequest):
            raise TypeError("request must be AdapterInvocationRequest")
        if request.selected_adapter_kind != self.adapter_kind:
            return self._authority_error(request, "adapter_kind")
        if request.adapter_id != self._config.adapter_id:
            return self._authority_error(request, "adapter_id")
        if (
            request.selected_runner_binding_id
            != request.dispatch_envelope.runner_binding_id
        ):
            return self._authority_error(request, "runner_binding")
        if request.redaction_policy != self._config.redaction_policy:
            return self._error(request, "redaction_refused", "redaction_policy")
        if _contains_secret(
            _instruction_input(request),
            self._config.redaction_policy,
        ):
            return self._error(request, "redaction_refused", "instruction_input")

        if _has_running_event_loop():
            return self._error(request, "invocation_failed", "active_event_loop")
        provider = _optional_provider()
        if provider is None:
            return self._error(request, "missing_opt_in_config", "provider_unavailable")
        if self._config.facade is not None:
            return self._invoke_injected(request, provider, self._config.facade)
        return self._invoke_live(request, provider)

    def _invoke_injected(
        self,
        request: AdapterInvocationRequest,
        provider: object,
        facade: MillforgeFacade,
    ) -> AdapterInvocationOutcome:
        try:
            prepared = _prepare_invocation(request, self._config, provider, facade)
        except _AuthorityRefusal as exc:
            return self._authority_error(request, exc.reason)
        except _InputTooLarge:
            return self._error(request, "input_too_large", "task_instruction")
        except Exception:
            return self._error(request, "invocation_failed", "request_construction")
        try:
            evidence = facade.invocation_evidence_for(prepared.provider_request)
        except Exception:
            return self._authority_error(request, "invocation_evidence")
        invocation_evidence_sha256 = _verified_invocation_evidence_sha256(
            evidence,
            prepared,
        )
        if invocation_evidence_sha256 is None:
            return self._authority_error(request, "invocation_evidence")
        adapter_provenance = _adapter_provenance(
            request,
            prepared,
            invocation_evidence_sha256,
        )
        try:
            result = asyncio.run(facade.execute(prepared.provider_request))
        except TimeoutError:
            return self._error(request, "timeout", "provider_timeout")
        except asyncio.CancelledError:
            return self._error(request, "cancelled", "provider_cancelled")
        except Exception:
            return self._error(request, "invocation_failed", "provider_execution")
        return _translate_result(request, prepared, result, adapter_provenance)

    def _invoke_live(
        self,
        request: AdapterInvocationRequest,
        provider: object,
    ) -> AdapterInvocationOutcome:
        try:
            return asyncio.run(self._invoke_live_async(request, provider))
        except _LiveConfigError:
            return self._error(request, "invocation_failed", "local_configuration")
        except TimeoutError:
            return self._error(request, "timeout", "provider_timeout")
        except asyncio.CancelledError:
            return self._error(request, "cancelled", "provider_cancelled")
        except Exception:
            return self._error(request, "invocation_failed", "provider_execution")

    async def _invoke_live_async(
        self,
        request: AdapterInvocationRequest,
        provider: object,
    ) -> AdapterInvocationOutcome:
        live_config = self._config.live_config
        if live_config is None:
            raise _LiveConfigError("live configuration is missing")
        pin = request.selected_component_pin
        if pin is None:
            return self._authority_error(request, "component_pin")
        profile, secret_ref = _live_public_records(provider, live_config)
        secret_resolver = _EnvironmentSecretResolver(provider, secret_ref)
        secret_resolver.resolve(secret_ref)
        cancellation_id = request.cancellation_token or request.correlation_id
        facade = await _create_live_facade(
            provider,
            legal_terminal_results=tuple(pin.legal_terminal_result_ids),
            profile=profile,
            secret_ref=secret_ref,
            secret_resolver=secret_resolver,
            workspace_root=self._config.workspace_root,
            timeout_seconds=self._config.timeout_seconds,
            cancellation_id=cancellation_id,
        )
        try:
            try:
                prepared = _prepare_invocation(
                    request,
                    self._config,
                    provider,
                    facade,
                    secret_ref=secret_ref,
                )
            except _AuthorityRefusal as exc:
                return self._authority_error(request, exc.reason)
            except _InputTooLarge:
                return self._error(request, "input_too_large", "task_instruction")
            except Exception:
                return self._error(request, "invocation_failed", "request_construction")
            try:
                evidence = facade.invocation_evidence_for(prepared.provider_request)
            except Exception:
                return self._authority_error(request, "invocation_evidence")
            invocation_evidence_sha256 = _verified_invocation_evidence_sha256(
                evidence,
                prepared,
            )

            if invocation_evidence_sha256 is None:
                return self._authority_error(request, "invocation_evidence")
            adapter_provenance = _adapter_provenance(
                request,
                prepared,
                invocation_evidence_sha256,
            )
            result = await facade.execute(prepared.provider_request)
            return _translate_result(request, prepared, result, adapter_provenance)
        finally:
            await _close_live_facade(facade)

    def start_session(
        self,
        request: AdapterInvocationRequest,
    ) -> RunnerSessionStartOutcome:
        return self._start_session_via_temporary_synchronous_compatibility_shim(
            request
        )

    def _start_session_via_temporary_synchronous_compatibility_shim(
        self,
        request: AdapterInvocationRequest,
    ) -> RunnerSessionStartOutcome:
        """Temporary RS-2 bridge; delete when Millforge gets an RS-5 handle."""

        outcome = self.invoke(request)
        echo = (
            outcome.dispatch_echo
            if outcome.dispatch_echo is not None
            else DispatchEcho.from_dispatch_envelope(
                request.dispatch_envelope,
                correlation_id=request.correlation_id,
            )
        )
        if isinstance(outcome, AdapterErrorResult):
            if outcome.error_kind in {
                "missing_opt_in_config",
                "unsupported_adapter_kind",
                "input_too_large",
                "redaction_refused",
                "selected_authority_refused",
            }:
                return StartRefusedBeforeExternalWork(
                    echo,
                    outcome,
                    start_refusal_diagnostic_digest(outcome),
                )
            return StartIndeterminate(
                echo,
                None,
                start_refusal_diagnostic_digest(outcome),
            )
        return StartedSession(
            echo,
            _CompletedMillforgeCompatibilityHandle(outcome),
            f"millforge:{request.session_id}:{request.dispatch_generation}",
            {},
        )

    def reconcile_session(
        self,
        request: RunnerSessionReconcileRequest,
    ) -> Unsupported:
        invocation = request.invocation_request
        return Unsupported(
            DispatchEcho.from_dispatch_envelope(
                invocation.dispatch_envelope,
                correlation_id=invocation.correlation_id,
            )
        )

    def _authority_error(
        self,
        request: AdapterInvocationRequest,
        reason: str,
    ) -> AdapterErrorResult:
        return self._error(request, "selected_authority_refused", reason)

    def _error(
        self,
        request: AdapterInvocationRequest,
        error_kind: str,
        reason: str,
    ) -> AdapterErrorResult:
        return AdapterErrorResult.from_unredacted(
            adapter_id=self._config.adapter_id,
            error_kind=error_kind,
            redaction_policy=self._config.redaction_policy,
            dispatch_echo=DispatchEcho.from_dispatch_envelope(
                request.dispatch_envelope,
                correlation_id=request.correlation_id,
            ),
            diagnostics={"reason": reason},
        )


class _CompletedMillforgeCompatibilityHandle:
    def __init__(self, outcome: AdapterInvocationOutcome) -> None:
        self._outcome: AdapterInvocationOutcome | None = outcome

    def poll_completion(self) -> AdapterInvocationOutcome | None:
        outcome = self._outcome
        self._outcome = None
        return outcome

    def request_cancel(self) -> RunnerCancellationOperationResult:
        return _unsupported_session_operation("cooperative_cancel")

    def terminate(self) -> RunnerCancellationOperationResult:
        return _unsupported_session_operation("terminate")

    def kill(self) -> RunnerCancellationOperationResult:
        return _unsupported_session_operation("kill")

    def cleanup(self) -> RunnerCleanupResult:
        diagnostic = {"disposition": "not_required"}
        return RunnerCleanupResult(
            "not_required",
            0,
            0,
            diagnostic,
            runner_cancellation_diagnostic_digest(diagnostic),
        )


def _unsupported_session_operation(
    operation: str,
) -> RunnerCancellationOperationResult:
    diagnostic: dict[str, AuthorityValue] = {
        "operation": operation,
        "supported": False,
    }
    return RunnerCancellationOperationResult(
        operation,
        "unsupported",
        0,
        0,
        diagnostic,
        runner_cancellation_diagnostic_digest(diagnostic),
    )


@dataclass(frozen=True, slots=True)
class _PreparedInvocation:
    provider_request: object
    selected_outputs: Mapping[str, _SelectedOutputAuthority]
    selected_output_requirements_sha256: str | None
    mappings: Mapping[str, RunnerTerminalResultMapping]
    options: Mapping[str, Mapping[str, AuthorityValue]]
    descriptor_sha256: str
    selected_output_present_type: type[object]
    selected_output_absent_type: type[object]


@dataclass(frozen=True, slots=True)
class _SelectedOutputAuthority:
    requirement: object
    schema: ArtifactSchemaDeclaration


class _AuthorityRefusal(ValueError):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class _InputTooLarge(ValueError):
    pass


class _LiveConfigError(ValueError):
    pass


class _EnvironmentSecretResolver:
    def __init__(self, provider: object, expected_ref: object) -> None:
        self._provider = provider
        self._expected_ref = expected_ref

    def resolve(self, ref: object) -> object:
        if ref != self._expected_ref:
            raise _LiveConfigError("secret reference mismatch")
        env_var = getattr(ref, "env_var", None)
        if not isinstance(env_var, str) or not env_var.strip():
            raise _LiveConfigError("secret reference is invalid")
        value = os.environ.get(env_var)
        if not value:
            raise _LiveConfigError("environment secret is unavailable")
        secret_type = getattr(self._provider, "ResolvedSecret", None)
        if not callable(secret_type):
            raise _LiveConfigError("provider public secret contract is unavailable")
        try:
            return secret_type(value)
        except Exception as exc:
            raise _LiveConfigError("environment secret is invalid") from exc


class _SystemClock:
    def utc_now(self) -> datetime.datetime:
        return datetime.datetime.now(datetime.UTC)

    def monotonic(self) -> float:
        return time.monotonic()


class _NonCancelledToken:
    def __init__(self, cancellation_id: str) -> None:
        self.cancellation_id = cancellation_id

    def is_cancelled(self) -> bool:
        return False

    async def wait(self) -> None:
        await asyncio.Event().wait()

    @property
    def reason(self) -> None:
        return None


class _CorrelationCancellationResolver:
    def __init__(self, cancellation_id: str) -> None:
        self._cancellation_id = cancellation_id

    def resolve(self, ref: object) -> _NonCancelledToken:
        reference_id = getattr(ref, "cancellation_id", None)
        if reference_id != self._cancellation_id:
            raise _LiveConfigError("cancellation reference mismatch")
        return _NonCancelledToken(self._cancellation_id)


def _optional_provider() -> object | None:
    try:
        import millforge  # type: ignore[import-not-found]
    except ImportError:
        return None
    return cast(object, millforge)


def _live_public_records(
    provider: object,
    config: _LiveMillforgeConfig,
) -> tuple[object, object]:
    profile_type = getattr(provider, "ResolvedModelProfile", None)
    secret_ref_type = getattr(provider, "SecretRef", None)
    if not callable(getattr(profile_type, "model_validate", None)) or not callable(
        getattr(secret_ref_type, "model_validate", None)
    ):
        raise _LiveConfigError("provider public profile contract is unavailable")
    try:
        profile = cast(Any, profile_type).model_validate(dict(config.model_profile))
        secret_ref = cast(Any, secret_ref_type).model_validate(dict(config.secret_ref))
    except Exception as exc:
        raise _LiveConfigError("provider local records are invalid") from exc
    if (
        not isinstance(getattr(profile, "profile_id", None), str)
        or not getattr(profile, "profile_id").strip()
        or getattr(getattr(profile, "authentication", None), "secret_ref", None)
        != secret_ref
    ):
        raise _LiveConfigError("provider local records do not match")
    return profile, secret_ref


async def _create_live_facade(
    provider: object,
    *,
    legal_terminal_results: tuple[str, ...],
    profile: object,
    secret_ref: object,
    secret_resolver: _EnvironmentSecretResolver,
    workspace_root: Path,
    timeout_seconds: float,
    cancellation_id: str,
) -> MillforgeFacade:
    factory = getattr(provider, "create_millforge_base_live_runner", None)
    options_type = getattr(provider, "MillforgeBaseOptions", None)
    timeouts_type = getattr(provider, "OpenAICompatibleTimeouts", None)
    if (
        not callable(factory)
        or not callable(options_type)
        or not callable(getattr(timeouts_type, "uniform", None))
    ):
        raise _LiveConfigError("provider public factory contract is unavailable")
    try:
        facade = await factory(
            legal_terminal_results=legal_terminal_results,
            profile_id=getattr(profile, "profile_id"),
            model_profile=profile,
            secret_ref=secret_ref,
            secret_resolver=secret_resolver,
            cwd=workspace_root,
            clock=_SystemClock(),
            cancellation_resolver=_CorrelationCancellationResolver(cancellation_id),
            timeouts=cast(Any, timeouts_type).uniform(timeout_seconds),
            options=options_type(load_context_files=False),
        )
    except _LiveConfigError:
        raise
    except Exception as exc:
        raise _LiveConfigError("provider factory failed") from exc
    if (
        not callable(getattr(facade, "invocation_evidence_for", None))
        or not callable(getattr(facade, "execute", None))
        or not callable(getattr(facade, "aclose", None))
    ):
        await _close_live_facade(facade)
        raise _LiveConfigError("provider facade is invalid")
    return cast(MillforgeFacade, facade)


async def _close_live_facade(facade: object) -> None:
    close = getattr(facade, "aclose", None)
    if not callable(close):
        return
    await close()


def _prepare_invocation(
    request: AdapterInvocationRequest,
    config: MillforgeAdapterConfig,
    provider: object,
    facade: MillforgeFacade,
    *,
    secret_ref: object | None = None,
) -> _PreparedInvocation:
    pin = request.selected_component_pin
    if pin is None:
        raise _AuthorityRefusal("component_pin")
    _verify_descriptor(pin, facade)
    _verify_components(pin, facade, request)
    selected_output_present_type, selected_output_absent_type = _selected_output_types(
        provider,
    )
    options = _options_by_outcome(request)
    mappings = _current_mappings(request, pin, options)
    selected_outputs, selected_output_requirements = _selected_output_requirements(
        request,
        provider,
        mappings,
        options,
    )
    provider_request = _provider_request(
        request,
        config,
        provider,
        facade,
        selected_output_requirements,
        secret_ref=secret_ref,
    )
    return _PreparedInvocation(
        provider_request=provider_request,
        selected_outputs=MappingProxyType(dict(selected_outputs)),
        selected_output_requirements_sha256=_selected_output_requirements_sha256(
            selected_output_requirements
        ),
        mappings=MappingProxyType(dict(mappings)),
        options=MappingProxyType(dict(options)),
        descriptor_sha256=pin.descriptor_sha256,
        selected_output_present_type=selected_output_present_type,
        selected_output_absent_type=selected_output_absent_type,
    )


def _verify_descriptor(pin: RunnerComponentPin, facade: MillforgeFacade) -> None:
    descriptor = getattr(facade, "descriptor", None)
    expected = (
        ("component_kind", "runner"),
        ("component_id", getattr(descriptor, "runner_id", None)),
        ("component_version", str(getattr(descriptor, "runner_version", ""))),
        ("provider_distribution", getattr(descriptor, "package_name", None)),
        ("provider_version", getattr(descriptor, "package_version", None)),
        ("descriptor_media_type", "application/json"),
        ("descriptor_sha256", getattr(descriptor, "descriptor_sha256", None)),
    )
    if any(getattr(pin, name) != value for name, value in expected):
        raise _AuthorityRefusal("descriptor")
    if tuple(str(value) for value in pin.required_capability_ids) != tuple(
        sorted(
            getattr(descriptor, "required_capability_ids", ()),
            key=lambda value: str(value).encode("utf-8"),
        )
    ):
        raise _AuthorityRefusal("descriptor_capabilities")
    if tuple(pin.legal_terminal_result_ids) != tuple(
        sorted(
            getattr(descriptor, "legal_terminal_result_ids", ()),
            key=lambda value: str(value).encode("utf-8"),
        )
    ):
        raise _AuthorityRefusal("descriptor_results")


def _verify_components(
    pin: RunnerComponentPin,
    facade: MillforgeFacade,
    request: AdapterInvocationRequest,
) -> None:
    components = getattr(facade, "components", None)
    options = getattr(components, "options", None)
    metadata = getattr(components, "metadata", None)
    if getattr(options, "load_context_files", None) is not False:
        raise _AuthorityRefusal("context_discovery")
    if getattr(metadata, "context_file_count", None) != 0:
        raise _AuthorityRefusal("context_files")
    envelope = getattr(components, "capability_envelope", None)
    grants = getattr(envelope, "grants", ())
    component_capabilities = tuple(
        getattr(grant, "capability_id", None) for grant in grants
    )
    required_capabilities = tuple(str(value) for value in pin.required_capability_ids)
    if component_capabilities != required_capabilities:
        raise _AuthorityRefusal("capability_envelope")
    declared = request.dispatch_envelope.governance_context.get("capabilities")
    if not isinstance(declared, Sequence) or isinstance(declared, (str, bytes)):
        raise _AuthorityRefusal("governance_capabilities")
    admitted = {
        item.get("id")
        for item in declared
        if isinstance(item, Mapping)
        and item.get("support_status") == "supported"
        and item.get("grant_status") == "granted"
    }
    if set(required_capabilities) != admitted.intersection(required_capabilities):
        raise _AuthorityRefusal("governance_capabilities")


def _options_by_outcome(
    request: AdapterInvocationRequest,
) -> dict[str, Mapping[str, AuthorityValue]]:
    options: dict[str, Mapping[str, AuthorityValue]] = {}
    declared_schema_ids = set(request.dispatch_envelope.artifact_schema_ids)
    terminal_schema_ids: set[str] = set()
    for option in request.dispatch_envelope.terminal_options:
        outcome_id = option["outcome_id"]
        if not isinstance(outcome_id, str) or outcome_id in options:
            raise _AuthorityRefusal("terminal_options")
        schema_id = option["artifact_schema_id"]
        if schema_id is not None:
            if not isinstance(schema_id, str) or schema_id not in declared_schema_ids:
                raise _AuthorityRefusal("artifact_schema")
            terminal_schema_ids.add(schema_id)
        options[outcome_id] = option
    projected_schema_ids: set[str] = set()
    for schema in request.selected_artifact_schemas:
        schema_id = str(schema.id)
        if (
            schema_id in projected_schema_ids
            or schema_id not in declared_schema_ids
            or schema_id not in terminal_schema_ids
        ):
            raise _AuthorityRefusal("artifact_schema")
        projected_schema_ids.add(schema_id)
    if request.selected_terminal_result_mappings or request.selected_artifact_schemas:
        if projected_schema_ids != terminal_schema_ids:
            raise _AuthorityRefusal("artifact_schema")
    return options


def _current_mappings(
    request: AdapterInvocationRequest,
    pin: RunnerComponentPin,
    options: Mapping[str, Mapping[str, AuthorityValue]],
) -> dict[str, RunnerTerminalResultMapping]:
    mappings: dict[str, RunnerTerminalResultMapping] = {}
    for mapping in request.selected_terminal_result_mappings:
        if str(mapping.stage_kind_id) != request.dispatch_envelope.stage_kind_id:
            raise _AuthorityRefusal("mapping_stage")
        if mapping.runner_result_id not in pin.legal_terminal_result_ids:
            raise _AuthorityRefusal("mapping_result")
        outcome_id = str(mapping.outcome_id)
        if mapping.runner_result_id in mappings or outcome_id not in options:
            raise _AuthorityRefusal("mapping_outcome")
        mappings[mapping.runner_result_id] = mapping
    if not mappings:
        raise _AuthorityRefusal("terminal_mappings")
    return mappings


def _selected_output_requirements(
    request: AdapterInvocationRequest,
    provider: object,
    mappings: Mapping[str, RunnerTerminalResultMapping],
    options: Mapping[str, Mapping[str, AuthorityValue]],
) -> tuple[dict[str, _SelectedOutputAuthority], tuple[object, ...]]:
    requirement_type = getattr(provider, "SelectedOutputRequirement", None)
    terminal_requirement_type = getattr(
        provider,
        "TerminalSelectedOutputRequirement",
        None,
    )
    if not callable(requirement_type) or not callable(terminal_requirement_type):
        raise _AuthorityRefusal("provider_public_contract")
    schemas = {str(schema.id): schema for schema in request.selected_artifact_schemas}
    selected_outputs: dict[str, _SelectedOutputAuthority] = {}
    provider_requirements: list[object] = []
    for result_id in sorted(mappings, key=lambda value: value.encode("utf-8")):
        mapping = mappings[result_id]
        schema_id = options[str(mapping.outcome_id)]["artifact_schema_id"]
        if schema_id is None:
            continue
        if not isinstance(schema_id, str):
            raise _AuthorityRefusal("artifact_schema")
        schema = schemas.get(schema_id)
        if (
            schema is None
            or schema_id not in request.dispatch_envelope.artifact_schema_ids
            or schema.schema.get("type") != "object"
        ):
            raise _AuthorityRefusal("artifact_schema")
        projected = _project_schema(schema.schema)
        try:
            requirement = requirement_type(required=True, json_schema=projected)
            provider_requirement = terminal_requirement_type(
                terminal_result=result_id,
                selected_output=requirement,
            )
        except Exception as exc:
            raise _AuthorityRefusal("selected_output_schema") from exc
        selected_outputs[result_id] = _SelectedOutputAuthority(
            requirement=requirement,
            schema=schema,
        )
        provider_requirements.append(provider_requirement)
    return selected_outputs, tuple(provider_requirements)


def _selected_output_requirements_sha256(
    requirements: tuple[object, ...],
) -> str | None:
    if not requirements:
        return None
    payload: list[dict[str, object]] = []
    for item in requirements:
        terminal_result = getattr(item, "terminal_result", None)
        selected_output = getattr(item, "selected_output", None)
        required = getattr(selected_output, "required", None)
        schema_sha256 = getattr(selected_output, "schema_sha256", None)
        if (
            not isinstance(terminal_result, str)
            or not terminal_result.strip()
            or type(required) is not bool
            or not isinstance(schema_sha256, str)
        ):
            raise _AuthorityRefusal("selected_output_schema")
        payload.append(
            {
                "required": required,
                "schema_sha256": schema_sha256,
                "terminal_result": terminal_result,
            }
        )
    payload.sort(key=lambda item: cast(str, item["terminal_result"]).encode("utf-8"))
    return hashlib.sha256(_canonical_json_bytes(payload)).hexdigest()


def _project_schema(schema: Mapping[str, AuthorityValue]) -> dict[str, object]:
    if (
        not isinstance(schema, Mapping)
        or not validate_schema_declaration(schema).accepted
    ):
        raise _AuthorityRefusal("artifact_schema")
    projected = _project_schema_node(schema, depth=1)
    canonical = _canonical_json_bytes(projected)
    if len(canonical) > _MAX_SCHEMA_BYTES:
        raise _AuthorityRefusal("artifact_schema_ceiling")
    return projected


def _project_schema_node(
    schema: Mapping[str, AuthorityValue],
    *,
    depth: int,
) -> dict[str, object]:
    if depth > _MAX_SCHEMA_DEPTH or set(schema).difference(_SCHEMA_KEYS):
        raise _AuthorityRefusal("artifact_schema")
    schema_type = schema.get("type")
    constraints = {key for key in ("const", "enum") if key in schema}
    if len(constraints) > 1:
        raise _AuthorityRefusal("artifact_schema")
    if schema_type is None:
        if len(constraints) != 1 or set(schema) != constraints:
            raise _AuthorityRefusal("artifact_schema")
        return _project_value_constraint(schema, {})
    if not isinstance(schema_type, str) or schema_type not in _SCHEMA_TYPES:
        raise _AuthorityRefusal("artifact_schema")
    if schema_type == "object":
        if constraints:
            raise _AuthorityRefusal("artifact_schema")
        if "items" in schema or "min_items" in schema or "min_length" in schema:
            raise _AuthorityRefusal("artifact_schema")
        raw_properties = schema.get("properties", {})
        raw_required = schema.get("required", ())
        if not isinstance(raw_properties, Mapping) or not _string_sequence(
            raw_required
        ):
            raise _AuthorityRefusal("artifact_schema")
        if len(raw_properties) > _MAX_SCHEMA_PROPERTIES:
            raise _AuthorityRefusal("artifact_schema_ceiling")
        properties: dict[str, object] = {}
        for name in sorted(raw_properties):
            nested = raw_properties[name]
            if not isinstance(name, str) or not isinstance(nested, Mapping):
                raise _AuthorityRefusal("artifact_schema")
            properties[name] = _project_schema_node(
                nested,
                depth=depth + 1,
            )
        required = tuple(sorted(cast(Sequence[str], raw_required)))
        if len(set(required)) != len(required) or any(
            name not in properties for name in required
        ):
            raise _AuthorityRefusal("artifact_schema")
        return {
            "type": "object",
            "additionalProperties": False,
            "properties": properties,
            "required": list(required),
        }
    if schema_type == "array":
        if constraints:
            raise _AuthorityRefusal("artifact_schema")
        if any(key in schema for key in ("properties", "required", "min_length")):
            raise _AuthorityRefusal("artifact_schema")
        items = schema.get("items")
        minimum = schema.get("min_items", 0)
        if not isinstance(items, Mapping) or type(minimum) is not int or minimum < 0:
            raise _AuthorityRefusal("artifact_schema")
        if minimum > _MAX_ARRAY_ITEMS:
            raise _AuthorityRefusal("artifact_schema_ceiling")
        projected: dict[str, object] = {
            "type": "array",
            "items": _project_schema_node(
                items,
                depth=depth + 1,
            ),
        }
        if "min_items" in schema:
            projected["minItems"] = minimum
        return projected
    if any(key in schema for key in ("properties", "required", "items", "min_items")):
        raise _AuthorityRefusal("artifact_schema")
    if schema_type == "string":
        minimum = schema.get("min_length", 0)
        if type(minimum) is not int or minimum < 0:
            raise _AuthorityRefusal("artifact_schema")
        if minimum > _MAX_STRING_LENGTH:
            raise _AuthorityRefusal("artifact_schema_ceiling")
        string_projection: dict[str, object] = {"type": "string"}
        if "min_length" in schema:
            string_projection["minLength"] = minimum
        return _project_value_constraint(schema, string_projection)
    if "min_length" in schema:
        raise _AuthorityRefusal("artifact_schema")
    return _project_value_constraint(schema, {"type": schema_type})


def _project_value_constraint(
    schema: Mapping[str, AuthorityValue],
    projected: dict[str, object],
) -> dict[str, object]:
    if "const" in schema:
        projected["const"] = schema["const"]
    elif "enum" in schema:
        values = schema["enum"]
        if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
            raise _AuthorityRefusal("artifact_schema")
        projected["enum"] = list(values)
    return projected


def _provider_request(
    request: AdapterInvocationRequest,
    config: MillforgeAdapterConfig,
    provider: object,
    facade: MillforgeFacade,
    selected_output_requirements: tuple[object, ...],
    *,
    secret_ref: object | None,
) -> object:
    components = facade.components
    compiled = getattr(components, "compiled_plan", None)
    profile = getattr(components, "model_profile", None)
    stage = _provider_record(
        provider,
        "StageIdentity",
        plane=_PROVIDER_STAGE[0],
        node_id=_PROVIDER_STAGE[1],
        stage_kind_id=_PROVIDER_STAGE[2],
    )
    instruction = _instruction(request)
    run_directory = _run_directory(
        config.workspace_root, request.dispatch_envelope.run_id
    )
    component_pin = request.selected_component_pin
    if component_pin is None:
        raise _AuthorityRefusal("component_pin")
    capabilities = tuple(str(value) for value in component_pin.required_capability_ids)
    return _provider_record(
        provider,
        "HarnessExecutionRequest",
        request_id=request.correlation_id,
        run_id=request.dispatch_envelope.run_id,
        work_item_id=request.dispatch_envelope.work_item_id,
        task=_provider_record(provider, "HarnessTaskInput", instruction=instruction),
        stage=stage,
        compiled_harness=_provider_record(
            provider,
            "CompiledHarnessRef",
            identity=_provider_record(
                provider,
                "CompiledHarnessIdentity",
                compiled_plan_id=getattr(compiled, "harness_id", None),
                harness_id=getattr(compiled, "harness_id", None),
                harness_version=getattr(compiled, "harness_version", None),
            ),
            path=config.workspace_root / "compiled-harness",
            expected_hash=_provider_record(
                provider,
                "CompiledHarnessHash",
                algorithm="sha256",
                digest=getattr(compiled, "compiled_sha256", None),
            ),
        ),
        capability_envelope=_provider_record(
            provider,
            "CapabilityEnvelope",
            grants=tuple(
                _provider_record(provider, "CapabilityGrant", capability_id=value)
                for value in capabilities
            ),
        ),
        input_artifacts=(),
        run_directory=_provider_record(
            provider,
            "RunDirRef",
            run_id=request.dispatch_envelope.run_id,
            path=run_directory,
        ),
        timeout=_provider_record(
            provider,
            "TimeoutRef",
            timeout_seconds=min(request.timeout_seconds, config.timeout_seconds),
        ),
        cancellation=_provider_record(
            provider,
            "CancellationRef",
            cancellation_id=request.cancellation_token or request.correlation_id,
        ),
        secret_refs=() if secret_ref is None else (secret_ref,),
        model_profile=_provider_record(
            provider,
            "ModelProfileRef",
            profile_id=getattr(profile, "profile_id", None),
        ),
        selected_output_requirements=selected_output_requirements,
    )


def _provider_record(provider: object, name: str, **kwargs: object) -> object:
    factory = getattr(provider, name, None)
    if not callable(factory):
        raise _AuthorityRefusal("provider_public_contract")
    return factory(**kwargs)


def _instruction(request: AdapterInvocationRequest) -> str:
    payload = _instruction_input(request)
    if _contains_nul(payload):
        raise _InputTooLarge
    try:
        instruction = _canonical_json_bytes(_plain_json_value(payload)).decode("utf-8")
        byte_count = len(instruction.encode("utf-8"))
    except (TypeError, UnicodeError, ValueError) as exc:
        raise _InputTooLarge from exc
    if not instruction.strip() or byte_count > _MAX_TASK_BYTES:
        raise _InputTooLarge
    return instruction


def _instruction_input(request: AdapterInvocationRequest) -> dict[str, object]:
    material: dict[str, object] = {}
    for asset_id in sorted(request.selected_asset_material):
        value = request.selected_asset_material[asset_id]
        if isinstance(value, Mapping) and isinstance(value.get("body"), str):
            material[asset_id] = {"body": value["body"]}
    return {
        "entrypoint_asset_id": request.dispatch_envelope.entrypoint_asset_id,
        "skill_asset_ids": request.dispatch_envelope.skill_asset_ids,
        "selected_asset_material": material,
        "work_item_payload": request.dispatch_envelope.work_item_payload,
        "selected_join_evidence": request.dispatch_envelope.selected_join_evidence,
        "terminal_options": request.dispatch_envelope.terminal_options,
    }


def _run_directory(workspace_root: Path, run_id: str) -> Path:
    digest = hashlib.sha256(run_id.encode("utf-8")).hexdigest()
    run_directory = (workspace_root / "millforge-runs" / digest).resolve()
    if not run_directory.is_relative_to(workspace_root):
        raise _AuthorityRefusal("run_directory")
    return run_directory


def _verified_invocation_evidence_sha256(
    evidence: object,
    prepared: _PreparedInvocation,
) -> str | None:
    request = prepared.provider_request
    try:
        snapshot = _canonical_json_bytes(
            {
                "request_id": getattr(evidence, "request_id", None),
                "run_id": getattr(evidence, "run_id", None),
                "descriptor_sha256": getattr(evidence, "descriptor_sha256", None),
                "context_file_count": getattr(evidence, "context_file_count", None),
                "selected_output_requirements_sha256": getattr(
                    evidence,
                    "selected_output_requirements_sha256",
                    None,
                ),
            }
        )
    except (TypeError, ValueError):
        return None
    expected = _canonical_json_bytes(
        {
            "request_id": getattr(request, "request_id", None),
            "run_id": getattr(request, "run_id", None),
            "descriptor_sha256": prepared.descriptor_sha256,
            "context_file_count": 0,
            "selected_output_requirements_sha256": (
                prepared.selected_output_requirements_sha256
            ),
        }
    )
    if snapshot != expected:
        return None
    return hashlib.sha256(snapshot).hexdigest()


def _adapter_provenance(
    request: AdapterInvocationRequest,
    prepared: _PreparedInvocation,
    invocation_evidence_sha256: str,
) -> RunnerAdapterProvenance:
    return RunnerAdapterProvenance(
        adapter_kind=MILLFORGE_ADAPTER_KIND,
        component_descriptor_sha256=prepared.descriptor_sha256,
        invocation_evidence_sha256=invocation_evidence_sha256,
        correlation_id=request.correlation_id,
    )


def _translate_result(
    request: AdapterInvocationRequest,
    prepared: _PreparedInvocation,
    result: object,
    adapter_provenance: RunnerAdapterProvenance,
) -> AdapterInvocationOutcome:
    result_class = _enum_value(getattr(result, "result_class", None))
    if result_class == "timed_out":
        return _result_error(request, "timeout")
    if result_class == "cancelled":
        return _result_error(request, "cancelled")
    if _enum_value(
        getattr(result, "status", None)
    ) != "completed" or result_class not in {"domain_terminal", "domain_rejected"}:
        return _result_error(request, "invocation_failed")
    intent = getattr(result, "terminal_intent", None)
    if not _result_identity_matches(result, intent, prepared.provider_request):
        return _result_error(request, "result_parse_failed")
    result_id = getattr(intent, "terminal_result", None)
    if not isinstance(result_id, str):
        return _result_error(request, "result_parse_failed")
    mapping = prepared.mappings.get(result_id)
    if mapping is None:
        return _result_error(request, "result_parse_failed")
    option = prepared.options.get(str(mapping.outcome_id))
    if option is None:
        return _result_error(request, "result_parse_failed")
    artifact_payload = _artifact_payload(prepared, result, intent, option)
    if artifact_payload is _INVALID_PAYLOAD:
        return _result_error(request, "result_parse_failed")
    return AdapterSuccessResult.from_unredacted(
        adapter_id=request.adapter_id,
        dispatch_echo=DispatchEcho.from_dispatch_envelope(
            request.dispatch_envelope,
            correlation_id=request.correlation_id,
        ),
        redaction_policy=request.redaction_policy,
        marker=cast(str, option["marker"]),
        adapter_provenance=adapter_provenance,
        structured_provider_response={
            "status": _enum_value(getattr(result, "status", None)),
            "result_class": result_class,
        },
        artifact_payload_candidate=cast(Mapping[str, object] | None, artifact_payload),
    )


_INVALID_PAYLOAD = object()


def _result_error(
    request: AdapterInvocationRequest, error_kind: str
) -> AdapterErrorResult:
    return AdapterErrorResult.from_unredacted(
        adapter_id=request.adapter_id,
        error_kind=error_kind,
        redaction_policy=request.redaction_policy,
        dispatch_echo=DispatchEcho.from_dispatch_envelope(
            request.dispatch_envelope,
            correlation_id=request.correlation_id,
        ),
        diagnostics={"reason": "provider_result"},
    )


def _result_identity_matches(result: object, intent: object, request: object) -> bool:
    if intent is None:
        return False
    expected_stage = getattr(request, "stage", None)
    expected_harness = getattr(request, "compiled_harness", None)
    return (
        getattr(result, "request_id", None) == getattr(request, "request_id", None)
        and getattr(result, "run_id", None) == getattr(request, "run_id", None)
        and getattr(result, "stage", None) == expected_stage
        and getattr(intent, "request_id", None) == getattr(request, "request_id", None)
        and getattr(intent, "run_id", None) == getattr(request, "run_id", None)
        and getattr(intent, "stage", None) == expected_stage
        and getattr(result, "compiled_harness", None) == expected_harness
    )


def _artifact_payload(
    prepared: _PreparedInvocation,
    result: object,
    intent: object,
    option: Mapping[str, AuthorityValue],
) -> Mapping[str, object] | None | object:
    result_id = getattr(intent, "terminal_result", None)
    if not isinstance(result_id, str):
        return _INVALID_PAYLOAD
    selected = prepared.selected_outputs.get(result_id)
    result_output = getattr(result, "selected_output", None)
    intent_output = getattr(intent, "selected_output", None)
    result_digest = getattr(result, "selected_output_schema_sha256", None)
    intent_digest = getattr(intent, "selected_output_schema_sha256", None)
    schema_id = option["artifact_schema_id"]
    if selected is None:
        return (
            None
            if schema_id is None
            and result_output is None
            and intent_output is None
            and result_digest is None
            and intent_digest is None
            else _INVALID_PAYLOAD
        )
    if schema_id != str(selected.schema.id):
        return _INVALID_PAYLOAD
    expected_digest = getattr(selected.requirement, "schema_sha256", None)
    if result_digest != expected_digest or intent_digest != expected_digest:
        return _INVALID_PAYLOAD
    if not _selected_outputs_match(result_output, intent_output, prepared):
        return _INVALID_PAYLOAD
    value = getattr(result_output, "value", None)
    if type(
        result_output
    ) is not prepared.selected_output_present_type or not isinstance(
        value,
        Mapping,
    ):
        return _INVALID_PAYLOAD
    if (
        not validate_schema(selected.schema.schema, value).accepted
    ):
        return _INVALID_PAYLOAD
    return cast(Mapping[str, object], value)


def _selected_outputs_match(
    left: object,
    right: object,
    prepared: _PreparedInvocation,
) -> bool:
    if type(left) is not type(right):
        return False
    if type(left) is prepared.selected_output_absent_type:
        return True
    if type(left) is prepared.selected_output_present_type:
        return getattr(left, "value", None) == getattr(right, "value", None)
    return False


def _selected_output_types(provider: object) -> tuple[type[object], type[object]]:
    present_type = getattr(provider, "SelectedOutputPresent", None)
    absent_type = getattr(provider, "SelectedOutputAbsent", None)
    if not isinstance(present_type, type) or not isinstance(absent_type, type):
        raise _AuthorityRefusal("provider_public_contract")
    return cast(type[object], present_type), cast(type[object], absent_type)


def _has_running_event_loop() -> bool:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


def _enum_value(value: object) -> object:
    return getattr(value, "value", value)


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")


def _plain_json_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _plain_json_value(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [_plain_json_value(item) for item in value]
    return value


def _json_mapping_snapshot(value: Mapping[str, object], field_name: str) -> bytes:
    if not isinstance(value, Mapping):
        raise TypeError(f"{field_name} must be a mapping")
    try:
        snapshot = _canonical_json_bytes(_plain_json_value(value))
    except (TypeError, ValueError) as exc:
        raise TypeError(f"{field_name} must contain JSON values") from exc
    if not isinstance(json.loads(snapshot), dict):
        raise TypeError(f"{field_name} must be a JSON object")
    return snapshot


def _json_mapping_from_snapshot(snapshot: bytes) -> Mapping[str, object]:
    value = json.loads(snapshot)
    if not isinstance(value, dict):
        raise RuntimeError("live configuration snapshot must be a JSON object")
    return cast(Mapping[str, object], value)


def _string_sequence(value: object) -> bool:
    return (
        isinstance(value, Sequence)
        and not isinstance(value, (str, bytes))
        and all(isinstance(item, str) for item in value)
    )


def _contains_secret(value: object, policy: RedactionPolicy) -> bool:
    if isinstance(value, str):
        return any(token in value for token in policy.secret_tokens)
    if isinstance(value, Mapping):
        return any(
            _contains_secret(key, policy) or _contains_secret(nested, policy)
            for key, nested in value.items()
        )
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return any(_contains_secret(item, policy) for item in value)
    return False


def _contains_nul(value: object) -> bool:
    if isinstance(value, str):
        return "\x00" in value
    if isinstance(value, Mapping):
        return any(
            _contains_nul(key) or _contains_nul(nested) for key, nested in value.items()
        )
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return any(_contains_nul(item) for item in value)
    return False


__all__ = ("MILLFORGE_ADAPTER_KIND", "MillforgeAdapter", "MillforgeAdapterConfig")
