"""Codex runner adapter.

The adapter owns Codex wrapper protocol mechanics only. Runner output remains
candidate evidence until the runner contract conversion validates it.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from math import isfinite
from pathlib import Path
from types import MappingProxyType
from typing import TypeAlias, cast

from millrace.adapters.runner_contract import (
    AdapterErrorResult,
    AdapterEvidenceConversionError,
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
from millrace.adapters.subprocess_transport import (
    SubprocessTransport,
    SubprocessTransportError,
    SubprocessTransportHandle,
    SubprocessTransportRequest,
    SubprocessTransportSuccess,
)
from millrace.contracts.compiled_plan import AuthorityValue

CODEX_ADAPTER_KIND = "codex"

_BUNDLE_RECORD_KIND = "codex_adapter_invocation_bundle"
_BUNDLE_SCHEMA_VERSION = 2
_WRAPPER_MODES = frozenset({"offline_fake", "local_argv", "missing"})
_SUCCESS_RESULT_KEYS = frozenset(
    {
        "outcome_kind",
        "adapter_id",
        "dispatch_echo",
        "redaction_policy_id",
        "marker",
        "captured_stdout",
        "captured_stderr",
        "structured_provider_response",
        "artifact_payload_candidate",
        "observation_payload_candidate",
        "evidence_construction_diagnostics",
    },
)
_DISPATCH_ECHO_KEYS = frozenset(
    {
        "run_id",
        "session_id",
        "dispatch_generation",
        "session_fencing_token",
        "claim_id",
        "generation",
        "fencing_token",
        "plan_fingerprint",
        "stage_kind_id",
        "graph_node_id",
        "runner_binding_id",
        "correlation_id",
        "selected_authority_digest",
    },
)

JsonValue: TypeAlias = (
    str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]
)


@dataclass(frozen=True, slots=True, repr=False)
class CodexAdapterConfig:
    adapter_id: str
    wrapper_mode: str
    wrapper_argv: tuple[str, ...] | None
    cwd: Path
    env_allowlist: Mapping[str, str]
    timeout_seconds: float
    max_input_bundle_bytes: int
    max_stdout_bytes: int
    max_stderr_diagnostic_bytes: int
    redaction_policy: RedactionPolicy
    live_test_opt_in_env_flags: tuple[str, ...] = ()
    pre_cancelled: bool = False

    def __post_init__(self) -> None:
        _require_nonblank_string(self.adapter_id, "adapter_id")
        wrapper_mode = _require_nonblank_string(self.wrapper_mode, "wrapper_mode")
        if wrapper_mode not in _WRAPPER_MODES:
            raise ValueError("unsupported wrapper_mode")
        if wrapper_mode == "missing" and self.wrapper_argv is not None:
            raise ValueError("missing wrapper mode cannot include wrapper_argv")
        if wrapper_mode != "missing" and self.wrapper_argv is None:
            raise ValueError("wrapper_argv is required unless wrapper_mode is missing")
        if self.wrapper_argv is not None:
            object.__setattr__(
                self,
                "wrapper_argv",
                _coerce_argv(self.wrapper_argv),
            )
        if not isinstance(self.cwd, Path):
            raise TypeError("cwd must be Path")
        object.__setattr__(
            self,
            "env_allowlist",
            _coerce_env_allowlist(self.env_allowlist),
        )
        _require_positive_number(self.timeout_seconds, "timeout_seconds")
        _require_nonnegative_int(
            self.max_input_bundle_bytes,
            "max_input_bundle_bytes",
        )
        _require_nonnegative_int(self.max_stdout_bytes, "max_stdout_bytes")
        _require_nonnegative_int(
            self.max_stderr_diagnostic_bytes,
            "max_stderr_diagnostic_bytes",
        )
        if not isinstance(self.redaction_policy, RedactionPolicy):
            raise TypeError("redaction_policy must be RedactionPolicy")
        object.__setattr__(
            self,
            "redaction_policy",
            canonicalize_redaction_policy(self.redaction_policy),
        )
        object.__setattr__(
            self,
            "live_test_opt_in_env_flags",
            _coerce_string_tuple(
                self.live_test_opt_in_env_flags,
                "live_test_opt_in_env_flags",
            ),
        )
        if type(self.pre_cancelled) is not bool:
            raise TypeError("pre_cancelled must be a bool")

    def __repr__(self) -> str:
        argv_count = 0 if self.wrapper_argv is None else len(self.wrapper_argv)
        policy_id = _repr_redact(
            self.redaction_policy.policy_id,
            self.redaction_policy,
        )
        return (
            "CodexAdapterConfig("
            f"adapter_id={_repr_redact(self.adapter_id, self.redaction_policy)!r}, "
            f"wrapper_mode={self.wrapper_mode!r}, "
            f"wrapper_argv_count={argv_count}, "
            f"cwd={_repr_redact(str(self.cwd), self.redaction_policy)!r}, "
            f"env_key_count={len(self.env_allowlist)}, "
            f"timeout_seconds={self.timeout_seconds!r}, "
            f"max_input_bundle_bytes={self.max_input_bundle_bytes!r}, "
            f"max_stdout_bytes={self.max_stdout_bytes!r}, "
            f"max_stderr_diagnostic_bytes={self.max_stderr_diagnostic_bytes!r}, "
            f"redaction_policy_id={policy_id!r}, "
            f"redaction_secret_token_count={len(self.redaction_policy.secret_tokens)}, "
            f"live_test_opt_in_flag_count={len(self.live_test_opt_in_env_flags)}, "
            f"pre_cancelled={self.pre_cancelled!r}"
            ")"
        )


def _repr_redact(value: str, policy: RedactionPolicy) -> str:
    return RedactionPolicy.redact_text(policy, value)


class CodexAdapter:
    adapter_kind = CODEX_ADAPTER_KIND

    def __init__(
        self,
        config: CodexAdapterConfig,
        *,
        transport: SubprocessTransport | None = None,
    ) -> None:
        if not isinstance(config, CodexAdapterConfig):
            raise TypeError("config must be CodexAdapterConfig")
        self._config = config
        self._transport = transport or SubprocessTransport()

    def invoke(self, request: AdapterInvocationRequest) -> AdapterInvocationOutcome:
        return self._invoke_bounded(request)

    def start_session(
        self,
        request: AdapterInvocationRequest,
    ) -> RunnerSessionStartOutcome:
        prepared = self._transport_request(request)
        echo = DispatchEcho.from_dispatch_envelope(
            request.dispatch_envelope,
            correlation_id=request.correlation_id,
            selected_adapter_kind=request.selected_adapter_kind,
        )
        if isinstance(prepared, AdapterErrorResult):
            if prepared.error_kind in {
                "missing_opt_in_config",
                "unsupported_adapter_kind",
                "input_too_large",
                "redaction_refused",
                "selected_authority_refused",
            }:
                return StartRefusedBeforeExternalWork(
                    echo,
                    prepared,
                    start_refusal_diagnostic_digest(prepared),
                )
            return StartIndeterminate(
                echo,
                None,
                start_refusal_diagnostic_digest(prepared),
            )
        started = self._transport.start(prepared)
        if isinstance(started, SubprocessTransportError):
            outcome = self._transport_error(
                request,
                started,
                dispatch_echo=echo,
            )
            return StartIndeterminate(
                echo,
                None,
                start_refusal_diagnostic_digest(outcome),
            )
        return StartedSession(
            echo,
            _LiveCodexSessionHandle(
                adapter=self,
                request=request,
                transport_handle=started,
                expected_echo=echo,
            ),
            f"codex:{request.session_id}:{request.dispatch_generation}",
            {
                "wrapper_mode": self._config.wrapper_mode,
                "pid": started.process.pid,
            },
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
                selected_adapter_kind=invocation.selected_adapter_kind,
            )
        )

    def _invoke_bounded(
        self,
        request: AdapterInvocationRequest,
    ) -> AdapterInvocationOutcome:
        if not isinstance(request, AdapterInvocationRequest):
            raise TypeError("request must be AdapterInvocationRequest")
        prepared = self._transport_request(request)
        if isinstance(prepared, AdapterErrorResult):
            return prepared
        dispatch_echo = DispatchEcho.from_dispatch_envelope(
            request.dispatch_envelope,
            correlation_id=request.correlation_id,
            selected_adapter_kind=request.selected_adapter_kind,
        )
        transport_outcome = self._transport.invoke(prepared)
        if isinstance(transport_outcome, SubprocessTransportError):
            return self._transport_error(
                request,
                transport_outcome,
                dispatch_echo=dispatch_echo,
            )
        return self._success_from_transport(
            request,
            transport_outcome,
            expected_echo=dispatch_echo,
        )

    def _transport_request(
        self,
        request: AdapterInvocationRequest,
    ) -> SubprocessTransportRequest | AdapterErrorResult:
        if not isinstance(request, AdapterInvocationRequest):
            raise TypeError("request must be AdapterInvocationRequest")
        dispatch_echo = DispatchEcho.from_dispatch_envelope(
            request.dispatch_envelope,
            correlation_id=request.correlation_id,
            selected_adapter_kind=request.selected_adapter_kind,
        )
        if request.selected_adapter_kind != CODEX_ADAPTER_KIND:
            return self._adapter_error(
                request,
                "unsupported_adapter_kind",
                dispatch_echo=dispatch_echo,
            )
        if request.adapter_id != self._config.adapter_id:
            return self._adapter_error(
                request,
                "missing_opt_in_config",
                dispatch_echo=dispatch_echo,
                diagnostics={"reason": "adapter_id mismatch"},
            )
        if self._config.wrapper_argv is None:
            return self._adapter_error(
                request,
                "missing_opt_in_config",
                dispatch_echo=dispatch_echo,
            )
        if any(
            not os.environ.get(flag) for flag in self._config.live_test_opt_in_env_flags
        ):
            return self._adapter_error(
                request,
                "missing_opt_in_config",
                dispatch_echo=dispatch_echo,
                diagnostics={"missing_live_opt_in": True},
            )
        if not _redaction_policy_matches(
            self._config.redaction_policy,
            request.redaction_policy,
        ):
            return _safe_redaction_refused_error(
                _repr_redact(self._config.adapter_id, self._config.redaction_policy),
                self._config.redaction_policy.policy_id,
                redaction_policy=self._config.redaction_policy,
                dispatch_echo=dispatch_echo,
            )
        if _request_contains_configured_secret(
            request,
            self._config.redaction_policy,
        ):
            return _safe_redaction_refused_error(
                _repr_redact(self._config.adapter_id, self._config.redaction_policy),
                self._config.redaction_policy.policy_id,
                redaction_policy=self._config.redaction_policy,
                dispatch_echo=dispatch_echo,
            )

        stdin_bytes = _bundle_stdin_bytes(
            request,
            config=self._config,
            dispatch_echo=dispatch_echo,
        )
        if len(stdin_bytes) > self._config.max_input_bundle_bytes:
            return self._adapter_error(
                request,
                "input_too_large",
                dispatch_echo=dispatch_echo,
                diagnostics={"input_bytes": len(stdin_bytes)},
            )

        return SubprocessTransportRequest(
            argv=self._config.wrapper_argv,
            stdin_bytes=stdin_bytes,
            cwd=self._config.cwd,
            env_allowlist=self._config.env_allowlist,
            timeout_seconds=min(
                self._config.timeout_seconds,
                request.timeout_seconds,
            ),
            max_stdin_bytes=self._config.max_input_bundle_bytes,
            max_stdout_bytes=self._config.max_stdout_bytes,
            max_stderr_bytes=self._config.max_stderr_diagnostic_bytes,
            redaction_policy=self._config.redaction_policy,
            pre_cancelled=self._config.pre_cancelled,
        )

    def _success_from_transport(
        self,
        request: AdapterInvocationRequest,
        transport_outcome: SubprocessTransportSuccess,
        *,
        expected_echo: DispatchEcho,
    ) -> AdapterInvocationOutcome:
        try:
            wrapper_result = _parse_success_result_object(transport_outcome.stdout)
            dispatch_echo = _dispatch_echo_from_json(wrapper_result["dispatch_echo"])
            dispatch_echo.validate_against(
                request.dispatch_envelope,
                correlation_id=request.correlation_id,
                selected_adapter_kind=request.selected_adapter_kind,
            )
            if wrapper_result["adapter_id"] != self._config.adapter_id:
                raise ValueError("adapter_id mismatch")
            if wrapper_result["redaction_policy_id"] != (
                self._config.redaction_policy.policy_id
            ):
                raise ValueError("redaction_policy_id mismatch")
            diagnostics = _coerce_mapping(
                wrapper_result["evidence_construction_diagnostics"],
                "evidence_construction_diagnostics",
            )
            merged_diagnostics: dict[str, object] = dict(diagnostics)
            if transport_outcome.stderr:
                merged_diagnostics["wrapper_stderr_diagnostic"] = (
                    transport_outcome.stderr
                )
            if transport_outcome.stderr_truncated:
                merged_diagnostics["stderr_truncated"] = True
            marker = _optional_string(wrapper_result["marker"], "marker")
            captured_stdout = _optional_string(
                wrapper_result["captured_stdout"],
                "captured_stdout",
            )
            captured_stderr = _optional_string(
                wrapper_result["captured_stderr"],
                "captured_stderr",
            )
            structured_provider_response = _coerce_mapping(
                wrapper_result["structured_provider_response"],
                "structured_provider_response",
            )
            _validate_authority_mapping(
                structured_provider_response,
                "structured_provider_response",
            )
            artifact_payload_candidate = _optional_mapping(
                wrapper_result["artifact_payload_candidate"],
                "artifact_payload_candidate",
            )
            if artifact_payload_candidate is not None:
                _validate_authority_mapping(
                    artifact_payload_candidate,
                    "artifact_payload_candidate",
                )
            observation_payload_candidate = _optional_mapping(
                wrapper_result["observation_payload_candidate"],
                "observation_payload_candidate",
            )
            if observation_payload_candidate is not None:
                _validate_authority_mapping(
                    observation_payload_candidate,
                    "observation_payload_candidate",
                )
            _validate_authority_mapping(
                merged_diagnostics,
                "evidence_construction_diagnostics",
            )
            try:
                return AdapterSuccessResult.from_unredacted(
                    adapter_id=self._config.adapter_id,
                    dispatch_echo=dispatch_echo,
                    marker=marker,
                    captured_stdout=captured_stdout,
                    captured_stderr=captured_stderr,
                    structured_provider_response=structured_provider_response,
                    artifact_payload_candidate=artifact_payload_candidate,
                    observation_payload_candidate=observation_payload_candidate,
                    evidence_construction_diagnostics=merged_diagnostics,
                    redaction_policy=self._config.redaction_policy,
                )
            except Exception:
                safe_adapter_id = _repr_redact(
                    self._config.adapter_id,
                    self._config.redaction_policy,
                )
                return _safe_redaction_refused_error(
                    safe_adapter_id,
                    self._config.redaction_policy.policy_id,
                    redaction_policy=self._config.redaction_policy,
                    dispatch_echo=expected_echo,
                )
        except AdapterEvidenceConversionError:
            return self._adapter_error(
                request,
                "result_parse_failed",
                dispatch_echo=expected_echo,
                diagnostics={"reason": "dispatch echo mismatch"},
            )
        except Exception as exc:
            return self._adapter_error(
                request,
                "result_parse_failed",
                dispatch_echo=expected_echo,
                diagnostics={
                    "reason": str(exc),
                    "stdout": transport_outcome.stdout,
                    "stderr": transport_outcome.stderr,
                },
            )

    def _transport_error(
        self,
        request: AdapterInvocationRequest,
        transport_error: SubprocessTransportError,
        *,
        dispatch_echo: DispatchEcho,
    ) -> AdapterErrorResult:
        adapter_error_kind = _adapter_error_kind_for_transport(
            transport_error.error_kind,
        )
        return self._adapter_error(
            request,
            adapter_error_kind,
            dispatch_echo=dispatch_echo,
            diagnostics={
                "transport_error_kind": transport_error.error_kind,
                "stdout": transport_error.stdout,
                "stderr": transport_error.stderr,
                "diagnostics": transport_error.diagnostics,
                "exit_code": transport_error.exit_code,
                "stderr_truncated": transport_error.stderr_truncated,
            },
        )

    def _adapter_error(
        self,
        request: AdapterInvocationRequest,
        error_kind: str,
        *,
        dispatch_echo: DispatchEcho | None = None,
        diagnostics: Mapping[str, object] | None = None,
    ) -> AdapterErrorResult:
        try:
            return AdapterErrorResult.from_unredacted(
                adapter_id=self._config.adapter_id,
                error_kind=error_kind,
                redaction_policy=self._config.redaction_policy,
                dispatch_echo=dispatch_echo,
                diagnostics=diagnostics,
            )
        except Exception:
            return _safe_redaction_refused_error(
                _repr_redact(self._config.adapter_id, self._config.redaction_policy),
                self._config.redaction_policy.policy_id,
                redaction_policy=self._config.redaction_policy,
                dispatch_echo=dispatch_echo,
            )


class _LiveCodexSessionHandle:
    def __init__(
        self,
        *,
        adapter: CodexAdapter,
        request: AdapterInvocationRequest,
        transport_handle: SubprocessTransportHandle,
        expected_echo: DispatchEcho,
    ) -> None:
        self._adapter = adapter
        self._request = request
        self._transport_handle = transport_handle
        self._expected_echo = expected_echo

    def poll_completion(self) -> AdapterInvocationOutcome | None:
        outcome = self._transport_handle.poll_completion()
        if outcome is None:
            return None
        if isinstance(outcome, SubprocessTransportError):
            return self._adapter._transport_error(
                self._request,
                outcome,
                dispatch_echo=self._expected_echo,
            )
        return self._adapter._success_from_transport(
            self._request,
            outcome,
            expected_echo=self._expected_echo,
        )

    def request_cancel(self) -> RunnerCancellationOperationResult:
        return self._transport_handle.request_cancel()

    def terminate(self) -> RunnerCancellationOperationResult:
        return self._transport_handle.terminate()

    def kill(self) -> RunnerCancellationOperationResult:
        return self._transport_handle.kill()

    def cleanup(self) -> RunnerCleanupResult:
        return self._transport_handle.cleanup()


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


def _bundle_stdin_bytes(
    request: AdapterInvocationRequest,
    *,
    config: CodexAdapterConfig,
    dispatch_echo: DispatchEcho,
) -> bytes:
    bundle = _invocation_bundle(
        request,
        config=config,
        dispatch_echo=dispatch_echo,
    )
    return json.dumps(
        bundle,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _invocation_bundle(
    request: AdapterInvocationRequest,
    *,
    config: CodexAdapterConfig,
    dispatch_echo: DispatchEcho,
) -> dict[str, JsonValue]:
    dispatch_payload = _to_jsonable(request.dispatch_envelope.payload())
    selected_asset_material = _to_jsonable(request.selected_asset_material)
    return {
        "record_kind": _BUNDLE_RECORD_KIND,
        "schema_version": _BUNDLE_SCHEMA_VERSION,
        "adapter_id": config.adapter_id,
        "selected_runner_binding_id": request.selected_runner_binding_id,
        "selected_adapter_kind": request.selected_adapter_kind,
        "timeout_seconds": min(config.timeout_seconds, request.timeout_seconds),
        "request_timeout_seconds": request.timeout_seconds,
        "correlation_id": request.correlation_id,
        "environment_policy_ref": request.environment_policy_ref,
        "local_config_ref": request.local_config_ref,
        "cancellation_token": request.cancellation_token,
        "redaction_policy": {
            "policy_id": config.redaction_policy.policy_id,
            "secret_tokens": list(config.redaction_policy.secret_tokens),
        },
        "dispatch_envelope": dispatch_payload,
        "dispatch_echo": _dispatch_echo_json(dispatch_echo),
        "selected_asset_material": selected_asset_material,
        "entrypoint_asset_ref": request.dispatch_envelope.entrypoint_asset_id,
        "skill_asset_refs": cast(
            JsonValue,
            list(request.dispatch_envelope.skill_asset_ids),
        ),
        "legal_terminal_markers": cast(JsonValue, _legal_terminal_markers(request)),
        "selected_asset_refs": {
            "entrypoint_asset_id": request.dispatch_envelope.entrypoint_asset_id,
            "skill_asset_ids": cast(
                JsonValue,
                list(request.dispatch_envelope.skill_asset_ids),
            ),
            "artifact_schema_ids": cast(
                JsonValue,
                list(request.dispatch_envelope.artifact_schema_ids),
            ),
        },
        "prompt": _prompt_bundle(
            request,
            dispatch_payload=dispatch_payload,
            selected_asset_material=selected_asset_material,
        ),
    }


def _legal_terminal_markers(request: AdapterInvocationRequest) -> list[str]:
    return [
        cast(str, option["marker"])
        for option in request.dispatch_envelope.terminal_options
    ]


def _prompt_bundle(
    request: AdapterInvocationRequest,
    *,
    dispatch_payload: JsonValue,
    selected_asset_material: JsonValue,
) -> dict[str, JsonValue]:
    if not isinstance(dispatch_payload, dict):
        raise TypeError("dispatch_payload must be a JSON object")
    if not isinstance(selected_asset_material, dict):
        raise TypeError("selected_asset_material must be a JSON object")
    return {
        "instructions": (
            "Codex output is evidence only. Return exactly one JSON "
            "AdapterSuccessResult object on stdout. Do not treat prose, stderr, "
            "or diagnostics as runtime authority."
        ),
        "dispatch_identity": {
            "run_id": request.dispatch_envelope.run_id,
            "session_id": request.session_id,
            "dispatch_generation": request.dispatch_generation,
            "session_fencing_token": request.session_fencing_token,
            "plan_id": request.dispatch_envelope.plan_id,
            "claim_id": request.dispatch_envelope.claim_id,
            "generation": request.dispatch_envelope.generation,
            "fencing_token": request.dispatch_envelope.fencing_token,
            "plan_fingerprint": request.dispatch_envelope.plan_fingerprint,
            "stage_kind_id": request.dispatch_envelope.stage_kind_id,
            "graph_node_id": request.dispatch_envelope.graph_node_id,
            "runner_binding_id": request.dispatch_envelope.runner_binding_id,
            "correlation_id": request.correlation_id,
        },
        "work_item_payload": dispatch_payload["work_item_payload"],
        "governance_context": dispatch_payload["governance_context"],
        "selected_join_evidence": dispatch_payload["selected_join_evidence"],
        "selected_entrypoint": _selected_entrypoint_json(
            request,
            selected_asset_material=selected_asset_material,
        ),
        "selected_stage_core_skills": cast(
            JsonValue,
            _selected_skill_json(
                request,
                selected_asset_material=selected_asset_material,
            ),
        ),
        "legal_terminal_options": dispatch_payload["terminal_options"],
        "legal_terminal_markers": [
            option["marker"]
            for option in cast(
                list[dict[str, JsonValue]], dispatch_payload["terminal_options"]
            )
        ],
        "artifact_schema_expectations": list(
            request.dispatch_envelope.artifact_schema_ids,
        ),
    }


def _selected_entrypoint_json(
    request: AdapterInvocationRequest,
    *,
    selected_asset_material: Mapping[str, JsonValue],
) -> dict[str, JsonValue]:
    asset_id = request.dispatch_envelope.entrypoint_asset_id
    material: JsonValue = None
    if asset_id is not None:
        material = selected_asset_material.get(asset_id)
    return {"asset_id": asset_id, "material": material}


def _selected_skill_json(
    request: AdapterInvocationRequest,
    *,
    selected_asset_material: Mapping[str, JsonValue],
) -> list[dict[str, JsonValue]]:
    return [
        {
            "asset_id": asset_id,
            "material": selected_asset_material.get(asset_id),
        }
        for asset_id in request.dispatch_envelope.skill_asset_ids
    ]


def _dispatch_echo_json(echo: DispatchEcho) -> dict[str, JsonValue]:
    return {
        "run_id": echo.run_id,
        "session_id": echo.session_id,
        "dispatch_generation": echo.dispatch_generation,
        "session_fencing_token": echo.session_fencing_token,
        "claim_id": echo.claim_id,
        "generation": echo.generation,
        "fencing_token": echo.fencing_token,
        "plan_fingerprint": echo.plan_fingerprint,
        "stage_kind_id": echo.stage_kind_id,
        "graph_node_id": echo.graph_node_id,
        "runner_binding_id": echo.runner_binding_id,
        "correlation_id": echo.correlation_id,
        "selected_authority_digest": echo.selected_authority_digest,
    }


def _parse_success_result_object(value: str) -> Mapping[str, object]:
    decoder = json.JSONDecoder()
    parsed, offset = decoder.raw_decode(value)
    if value[offset:].strip():
        raise ValueError("wrapper stdout must contain exactly one JSON object")
    if not isinstance(parsed, Mapping):
        raise ValueError("wrapper stdout must be a JSON object")
    parsed_mapping = cast(Mapping[str, object], parsed)
    if frozenset(parsed_mapping) != _SUCCESS_RESULT_KEYS:
        raise ValueError("wrapper stdout has unsupported top-level keys")
    if parsed_mapping["outcome_kind"] != "success":
        raise ValueError("wrapper outcome_kind must be success")
    return parsed_mapping


def _dispatch_echo_from_json(value: object) -> DispatchEcho:
    mapping = _coerce_mapping(value, "dispatch_echo")
    if frozenset(mapping) != _DISPATCH_ECHO_KEYS:
        raise ValueError("dispatch_echo has unsupported keys")
    return DispatchEcho(
        run_id=_require_nonblank_string(mapping["run_id"], "dispatch_echo.run_id"),
        session_id=_require_nonblank_string(
            mapping["session_id"],
            "dispatch_echo.session_id",
        ),
        dispatch_generation=_require_int(
            mapping["dispatch_generation"],
            "dispatch_echo.dispatch_generation",
        ),
        session_fencing_token=_require_nonblank_string(
            mapping["session_fencing_token"],
            "dispatch_echo.session_fencing_token",
        ),
        claim_id=_require_nonblank_string(
            mapping["claim_id"],
            "dispatch_echo.claim_id",
        ),
        generation=_require_int(mapping["generation"], "dispatch_echo.generation"),
        fencing_token=_require_nonblank_string(
            mapping["fencing_token"],
            "dispatch_echo.fencing_token",
        ),
        plan_fingerprint=_require_nonblank_string(
            mapping["plan_fingerprint"],
            "dispatch_echo.plan_fingerprint",
        ),
        stage_kind_id=_require_nonblank_string(
            mapping["stage_kind_id"],
            "dispatch_echo.stage_kind_id",
        ),
        graph_node_id=_require_nonblank_string(
            mapping["graph_node_id"],
            "dispatch_echo.graph_node_id",
        ),
        runner_binding_id=_require_nonblank_string(
            mapping["runner_binding_id"],
            "dispatch_echo.runner_binding_id",
        ),
        correlation_id=_require_nonblank_string(
            mapping["correlation_id"],
            "dispatch_echo.correlation_id",
        ),
        selected_authority_digest=_require_nonblank_string(
            mapping["selected_authority_digest"],
            "dispatch_echo.selected_authority_digest",
        ),
    )


def _adapter_error_kind_for_transport(error_kind: str) -> str:
    if error_kind == "cancelled":
        return "cancelled"
    if error_kind == "input_too_large":
        return "input_too_large"
    if error_kind == "output_too_large":
        return "output_too_large"
    if error_kind == "redaction_refused":
        return "redaction_refused"
    if error_kind == "timeout":
        return "timeout"
    return "invocation_failed"


def _redaction_policy_matches(
    config_policy: RedactionPolicy,
    request_policy: RedactionPolicy,
) -> bool:
    return (
        config_policy.policy_id == request_policy.policy_id
        and config_policy.secret_tokens == request_policy.secret_tokens
    )


def _request_contains_configured_secret(
    request: AdapterInvocationRequest,
    policy: RedactionPolicy,
) -> bool:
    return _contains_configured_secret(
        request.correlation_id,
        policy,
    ) or _contains_configured_secret(
        request.dispatch_envelope.selected_join_evidence,
        policy,
    )


def _contains_configured_secret(value: object, policy: RedactionPolicy) -> bool:
    if isinstance(value, str):
        return RedactionPolicy.redact_text(policy, value) != value
    if isinstance(value, Mapping):
        return any(
            _contains_configured_secret(key, policy)
            or _contains_configured_secret(nested_value, policy)
            for key, nested_value in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(_contains_configured_secret(item, policy) for item in value)
    return False


def _safe_redaction_refused_error(
    adapter_id: str,
    redaction_policy_id: str,
    *,
    redaction_policy: RedactionPolicy,
    dispatch_echo: DispatchEcho | None,
) -> AdapterErrorResult:
    return AdapterErrorResult(
        adapter_id=adapter_id,
        error_kind="redaction_refused",
        redaction_policy_id=_repr_redact(redaction_policy_id, redaction_policy),
        dispatch_echo=dispatch_echo,
        diagnostics=MappingProxyType({"message": "redaction failed"}),
    )


def _to_jsonable(value: object) -> JsonValue:
    if value is None or isinstance(value, (str, bool)):
        return value
    if type(value) is int:
        return value
    if type(value) is float:
        value_as_float = value
        if not isfinite(value_as_float):
            raise ValueError("JSON float must be finite")
        return value_as_float
    if isinstance(value, Mapping):
        converted: dict[str, JsonValue] = {}
        for key, nested_value in value.items():
            converted[_require_nonblank_string(key, "JSON object key")] = _to_jsonable(
                nested_value
            )
        return converted
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(item) for item in value]
    raise TypeError(f"unsupported JSON value type: {type(value).__name__}")


def _coerce_argv(value: object) -> tuple[str, ...]:
    if not isinstance(value, tuple):
        raise TypeError("wrapper_argv must be an explicit argv tuple")
    if not value:
        raise ValueError("wrapper_argv cannot be empty")
    return _RedactedStringTuple(
        _require_nonblank_string(item, f"wrapper_argv[{index}]")
        for index, item in enumerate(value)
    )


def _coerce_env_allowlist(value: object) -> Mapping[str, str]:
    if not isinstance(value, Mapping):
        raise TypeError("env_allowlist must be a mapping")
    env: dict[str, str] = {}
    for key, nested_value in cast(Mapping[object, object], value).items():
        env[_require_nonblank_string(key, "env_allowlist key")] = _require_string(
            nested_value,
            "env_allowlist value",
        )
    return _RedactedStringMapping(env)


def _coerce_string_tuple(value: object, field_name: str) -> tuple[str, ...]:
    if not isinstance(value, tuple):
        raise TypeError(f"{field_name} must be a tuple")
    return tuple(
        _require_nonblank_string(item, f"{field_name}[{index}]")
        for index, item in enumerate(value)
    )


def _coerce_mapping(value: object, field_name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{field_name} must be a mapping")
    converted: dict[str, object] = {}
    for key, nested_value in cast(Mapping[object, object], value).items():
        converted[_require_nonblank_string(key, f"{field_name} key")] = nested_value
    return MappingProxyType(converted)


def _optional_mapping(
    value: object,
    field_name: str,
) -> Mapping[str, object] | None:
    if value is None:
        return None
    return _coerce_mapping(value, field_name)


def _optional_string(value: object, field_name: str) -> str | None:
    if value is None:
        return None
    return _require_nonblank_string(value, field_name)


def _validate_authority_mapping(
    value: Mapping[str, object],
    field_name: str,
) -> None:
    for key, nested_value in value.items():
        _require_string(key, f"{field_name} key")
        _validate_authority_value(nested_value, f"{field_name}.{key}")


def _validate_authority_value(value: object, field_name: str) -> None:
    if value is None or isinstance(value, (str, bool)):
        return
    if type(value) is int:
        return
    if isinstance(value, Mapping):
        for key, nested_value in cast(Mapping[object, object], value).items():
            _require_string(key, f"{field_name} key")
            _validate_authority_value(nested_value, f"{field_name}.{key}")
        return
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _validate_authority_value(item, f"{field_name}[{index}]")
        return
    raise TypeError(f"{field_name} must be an authority value")


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


def _require_nonnegative_int(value: object, field_name: str) -> int:
    value_as_int = _require_int(value, field_name)
    if value_as_int < 0:
        raise ValueError(f"{field_name} must be non-negative")
    return value_as_int


def _require_positive_number(value: object, field_name: str) -> float:
    if type(value) not in {int, float}:
        raise TypeError(f"{field_name} must be a number")
    value_as_float = float(cast(int | float, value))
    if value_as_float <= 0:
        raise ValueError(f"{field_name} must be positive")
    if not isfinite(value_as_float):
        raise ValueError(f"{field_name} must be finite")
    return value_as_float


class _RedactedStringTuple(tuple[str, ...]):
    def __new__(cls, values: Iterable[str]) -> _RedactedStringTuple:
        return tuple.__new__(cls, tuple(values))

    def __repr__(self) -> str:
        return f"<redacted string tuple: {len(self)} item(s)>"

    __str__ = __repr__


class _RedactedStringMapping(Mapping[str, str]):
    def __init__(self, values: Mapping[str, str]) -> None:
        self._values = MappingProxyType(dict(values))

    def __getitem__(self, key: str) -> str:
        return self._values[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)

    def __repr__(self) -> str:
        return f"<redacted string mapping: {len(self)} item(s)>"

    __str__ = __repr__


__all__ = (
    "CODEX_ADAPTER_KIND",
    "CodexAdapter",
    "CodexAdapterConfig",
)
