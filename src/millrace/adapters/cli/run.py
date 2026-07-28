"""CLI adapter for bounded local runner execution."""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from millrace.adapters.cli.context import (
    CliCommandError,
    OpenRuntimeContext,
    transition_context,
)
from millrace.adapters.cli.output import ExitCode
from millrace.adapters.cli.session_coordinator import (
    execute_runner_session,
    session_cancellation_token,
    session_correlation_id,
)
from millrace.adapters.codex import CODEX_ADAPTER_KIND, CodexAdapter, CodexAdapterConfig
from millrace.adapters.millforge import (
    MILLFORGE_ADAPTER_KIND,
    MillforgeAdapter,
    MillforgeAdapterConfig,
)
from millrace.adapters.runner_contract import (
    AdapterInvocationRequest,
    AdapterLocalConfig,
    AdapterResolverError,
    RedactionPolicy,
    resolve_adapter,
)
from millrace.compiler import DEFAULT_SELECTED_RUNNER_ADAPTER_POLICY
from millrace.contracts.compiled_plan import (
    ArtifactSchemaDeclaration,
    RunnerBindingDeclaration,
    RunnerComponentPin,
    RunnerTerminalResultMapping,
    SelectedCompiledPlan,
)
from millrace.contracts.runner import RunnerDispatchEnvelope
from millrace.contracts.state import (
    Activation,
    RunnerSessionRecord,
    RunRecord,
    RuntimeState,
    WorkItem,
)
from millrace.contracts.transition import ClaimWork
from millrace.kernel import apply, decide
from millrace.operator.dispatch import (
    DispatchProjectionError,
    ReadyDispatchCandidate,
    ReadyDispatchDiagnostic,
    build_dispatch_envelope_for_run,
    list_ready_dispatch_candidates,
    ready_diagnostic_from_claim_refusal,
)
from millrace.operator.prompt_material import (
    SelectedAssetMaterializationError,
    build_selected_asset_material,
)

_COMMAND = "run.bounded"
_DEFAULT_REDACTION_POLICY = RedactionPolicy(policy_id="cli-default")
_NON_CORRUPT_DISPATCH_REFUSALS = frozenset({"run_observed", "work_item_closed"})


@dataclass(frozen=True, slots=True)
class BoundedExecutionUnitResult:
    code: str
    accepted: bool = False
    activation_id: str | None = None
    run_id: str | None = None
    claim_id: str | None = None
    fencing_token: str | None = None
    diagnostics: tuple[Mapping[str, object], ...] = field(default_factory=tuple)
    adapter_error_kind: str | None = None
    observation_refusal_reason: str | None = None
    transition_disposition: str | None = None


@dataclass(frozen=True, slots=True)
class _ActiveRun:
    activation: Activation
    run: RunRecord
    selected_plan: SelectedCompiledPlan
    adapter_kind: str


def run_bounded_execution_unit(
    runtime: OpenRuntimeContext,
    *,
    activation_id: str | None = None,
    adapter_kind: str | None = None,
    local_config: AdapterLocalConfig | None = None,
    local_config_path: Path | None = None,
    actor_id: str = "local_operator",
    daemon_stop_requested: Callable[[], bool] | None = None,
) -> BoundedExecutionUnitResult:
    if not isinstance(runtime, OpenRuntimeContext):
        raise TypeError("runtime must be OpenRuntimeContext")
    normalized_activation_id = _optional_cli_nonblank(activation_id, "activation_id")
    normalized_adapter_kind = _optional_cli_nonblank(adapter_kind, "adapter_kind")
    _cli_nonblank(actor_id, "actor_id")
    effective_config = _effective_local_config(local_config, local_config_path)
    state = runtime.store.load_runtime_state(runtime.cas_store)

    if normalized_activation_id is None:
        selected = _select_ready_activation(state)
        if isinstance(selected, BoundedExecutionUnitResult):
            return selected
        try:
            selected_adapter_kind = _adapter_kind_for_activation(
                state,
                selected.activation_id,
            )
        except ValueError:
            return BoundedExecutionUnitResult(code="ready_state_corrupt")
        preflight = _preclaim_adapter_refusal(
            requested_adapter_kind=normalized_adapter_kind,
            selected_adapter_kind=selected_adapter_kind,
            local_config=effective_config,
        )
        if preflight is not None:
            return preflight
        claimed = _claim_activation(
            runtime,
            state,
            activation_id=selected.activation_id,
        )
        if isinstance(claimed, BoundedExecutionUnitResult):
            return claimed
        active = claimed
    else:
        active_result = _active_or_claimed_activation(
            runtime,
            state,
            activation_id=normalized_activation_id,
            requested_adapter_kind=normalized_adapter_kind,
            local_config=effective_config,
        )
        if isinstance(active_result, BoundedExecutionUnitResult):
            return active_result
        active = active_result

    selected_kind = normalized_adapter_kind or active.adapter_kind
    try:
        adapter = resolve_adapter(selected_kind, effective_config)
        session_result = execute_runner_session(
            runtime,
            run_ref=active.run.run_ref,
            adapter=adapter,
            request_factory=lambda session: _session_invocation_request(
                runtime,
                active=active,
                selected_kind=selected_kind,
                effective_config=effective_config,
                session=session,
            ),
            explicit_retry_intent=normalized_activation_id is not None,
            daemon_stop_requested=daemon_stop_requested,
        )
    except SelectedAssetMaterializationError as exc:
        return _with_active_ids(
            BoundedExecutionUnitResult(
                code="asset_material_refused",
                diagnostics=({"reason": str(exc)},),
            ),
            active,
        )
    except DispatchProjectionError:
        return _with_active_ids(
            BoundedExecutionUnitResult(code="ready_state_corrupt"),
            active,
        )
    except (AdapterResolverError, TypeError, ValueError):
        return _with_active_ids(
            BoundedExecutionUnitResult(code="adapter_failure"),
            active,
        )
    return _with_active_ids(
        BoundedExecutionUnitResult(
            code=session_result.code,
            accepted=session_result.accepted,
            adapter_error_kind=session_result.adapter_error_kind,
            observation_refusal_reason=session_result.observation_refusal_reason,
            transition_disposition=session_result.transition_disposition,
        ),
        active,
    )


def load_adapter_local_config(path: Path) -> AdapterLocalConfig:
    config_path = Path(path)
    try:
        raw = config_path.read_text(encoding="utf-8")
        if not raw.strip():
            raise ValueError("adapter config JSON cannot be blank")
        parsed = json.loads(raw)
        if not isinstance(parsed, Mapping):
            raise ValueError("adapter config JSON must be an object")
        adapters: dict[str, object] = {}
        for adapter_kind, adapter_config in parsed.items():
            if adapter_kind == CODEX_ADAPTER_KIND:
                adapters[CODEX_ADAPTER_KIND] = CodexAdapter(
                    _codex_config_from_json(adapter_config),
                )
            elif adapter_kind == MILLFORGE_ADAPTER_KIND:
                adapters[MILLFORGE_ADAPTER_KIND] = MillforgeAdapter(
                    _millforge_config_from_json(adapter_config),
                )
            else:
                raise ValueError("unsupported adapter config kind")
        return AdapterLocalConfig(adapters=cast(Mapping[str, Any], adapters))
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise CliCommandError(
            command=_COMMAND,
            code="invalid_adapter_config",
            message="Adapter config JSON is invalid.",
            exit_code=ExitCode.CLI_USAGE,
            details={"error": type(exc).__name__},
        ) from exc


def _session_invocation_request(
    runtime: OpenRuntimeContext,
    *,
    active: _ActiveRun,
    selected_kind: str,
    effective_config: AdapterLocalConfig,
    session: RunnerSessionRecord,
) -> AdapterInvocationRequest:
    dispatch = build_dispatch_envelope_for_run(
        state=_load(runtime),
        run_id=active.run.run_ref.run_id,
    )
    selected_asset_material = build_selected_asset_material(
        selected_plan=active.selected_plan,
        dispatch_envelope=dispatch,
    )
    selected_component_pin, selected_terminal_mappings, selected_schemas = (
        _selected_runner_authority_for_request(active.selected_plan, dispatch)
    )
    return AdapterInvocationRequest(
        adapter_id=_adapter_id_for_request(selected_kind, effective_config),
        selected_runner_binding_id=dispatch.runner_binding_id,
        selected_adapter_kind=selected_kind,
        dispatch_envelope=dispatch,
        session_id=session.session_id,
        dispatch_generation=session.dispatch_generation,
        session_fencing_token=session.session_fencing_token,
        timeout_seconds=_selected_invocation_timeout_seconds(
            active.selected_plan,
            dispatch.runner_binding_id,
        ),
        correlation_id=session_correlation_id(session),
        redaction_policy=_redaction_policy_for_adapter(
            selected_kind,
            effective_config,
        ),
        selected_asset_material=selected_asset_material,
        local_config_ref="cli-local-adapter-config",
        cancellation_token=session_cancellation_token(session),
        selected_component_pin=selected_component_pin,
        selected_terminal_result_mappings=selected_terminal_mappings,
        selected_artifact_schemas=selected_schemas,
    )


def _select_ready_activation(
    state: RuntimeState,
) -> ReadyDispatchCandidate | BoundedExecutionUnitResult:
    projection = list_ready_dispatch_candidates(state)
    if projection.candidates:
        return projection.candidates[0]
    if not projection.diagnostics or all(
        diagnostic.severity == "non_candidate" for diagnostic in projection.diagnostics
    ):
        return BoundedExecutionUnitResult(code="no_ready_work")
    code = (
        "ready_state_corrupt"
        if any(
            diagnostic.severity == "corrupt_authority"
            for diagnostic in projection.diagnostics
        )
        else "ready_state_refused"
    )
    return BoundedExecutionUnitResult(
        code=code,
        diagnostics=_diagnostics_payload(projection.diagnostics),
    )


def _claim_activation(
    runtime: OpenRuntimeContext,
    state: RuntimeState,
    *,
    activation_id: str,
) -> _ActiveRun | BoundedExecutionUnitResult:
    input_id = _claim_input_id(activation_id)
    transition_input = ClaimWork(input_id, activation_id=activation_id)
    context = transition_context(command=_COMMAND, input_id_value=input_id)
    decision = decide(state, transition_input, context)
    if not decision.accepted:
        activation = state.activations.get(activation_id)
        work_item = (
            None
            if activation is None
            else state.work_items.get(activation.work_item_id)
        )
        diagnostic = (
            None
            if activation is None or work_item is None
            else ready_diagnostic_from_claim_refusal(
                activation=activation,
                work_item=work_item,
                reason=(
                    "transition_refused"
                    if decision.refusal is None
                    else decision.refusal.reason
                ),
                detail=None if decision.refusal is None else decision.refusal.detail,
            )
        )
        return _ready_state_result((), () if diagnostic is None else (diagnostic,))

    next_state = apply(state, decision)
    runtime.store.persist_runtime_state(next_state, runtime.cas_store)
    run_id = _created_run_id(next_state, input_id)
    if run_id is None:
        return BoundedExecutionUnitResult(code="ready_state_corrupt")
    return _active_run_for_run(next_state, run_id)


def _active_or_claimed_activation(
    runtime: OpenRuntimeContext,
    state: RuntimeState,
    *,
    activation_id: str,
    requested_adapter_kind: str | None,
    local_config: AdapterLocalConfig,
) -> _ActiveRun | BoundedExecutionUnitResult:
    activation = state.activations.get(activation_id)
    if activation is None:
        return BoundedExecutionUnitResult(code="ready_state_corrupt")
    if activation.claimed_by_run_id is not None:
        active = _active_run_for_run(state, activation.claimed_by_run_id)
        if isinstance(active, BoundedExecutionUnitResult):
            return active
        if active.activation.activation_id != activation_id:
            return BoundedExecutionUnitResult(code="ready_state_corrupt")
        if any(
            observation.run_id == active.run.run_ref.run_id
            for observation in state.runner_observations.values()
        ):
            return _with_active_ids(
                BoundedExecutionUnitResult(
                    code="no_ready_work",
                    diagnostics=({"reason": "run_observed"},),
                ),
                active,
            )
        refusal = _preclaim_adapter_refusal(
            requested_adapter_kind=requested_adapter_kind,
            selected_adapter_kind=active.adapter_kind,
            local_config=local_config,
        )
        return active if refusal is None else _with_active_ids(refusal, active)

    try:
        selected_adapter_kind = _adapter_kind_for_activation(state, activation_id)
    except ValueError:
        return BoundedExecutionUnitResult(code="ready_state_corrupt")
    refusal = _preclaim_adapter_refusal(
        requested_adapter_kind=requested_adapter_kind,
        selected_adapter_kind=selected_adapter_kind,
        local_config=local_config,
    )
    if refusal is not None:
        return refusal
    return _claim_activation(runtime, state, activation_id=activation_id)


def _active_run_for_run(
    state: RuntimeState,
    run_id: str,
) -> _ActiveRun | BoundedExecutionUnitResult:
    run = state.runs.get(run_id)
    if run is None:
        return BoundedExecutionUnitResult(code="ready_state_corrupt")
    activation = state.activations.get(run.activation_id)
    if activation is None or activation.claimed_by_run_id != run_id:
        return BoundedExecutionUnitResult(code="ready_state_corrupt")
    work_item = state.work_items.get(run.work_item_id)
    if work_item is None:
        return BoundedExecutionUnitResult(code="ready_state_corrupt")
    if _active_run_claim_authority_refusal(
        state,
        run=run,
        activation=activation,
        work_item=work_item,
    ):
        return BoundedExecutionUnitResult(code="ready_state_corrupt")
    admitted = state.admitted_plans.get(run.run_ref.plan_ref.authority_fingerprint)
    if admitted is None or admitted.plan_ref != run.run_ref.plan_ref:
        return BoundedExecutionUnitResult(code="ready_state_corrupt")
    try:
        runner_binding = _selected_runner_binding(
            admitted.selected_plan,
            str(run.runner_binding_id),
        )
    except ValueError:
        return BoundedExecutionUnitResult(code="ready_state_corrupt")
    return _ActiveRun(
        activation=activation,
        run=run,
        selected_plan=admitted.selected_plan,
        adapter_kind=runner_binding.adapter_kind,
    )


def _active_run_claim_authority_refusal(
    state: RuntimeState,
    *,
    run: RunRecord,
    activation: Activation,
    work_item: WorkItem,
) -> bool:
    if run.run_ref.work_item_id != run.work_item_id:
        return True
    if activation.work_item_id != run.work_item_id:
        return True
    if activation.plan_ref != run.run_ref.plan_ref:
        return True
    if work_item.ref.work_item_id != run.work_item_id:
        return True
    if work_item.ref.plan_ref != run.run_ref.plan_ref:
        return True
    receipt = state.receipts.get(run.created_by_input_id)
    if receipt is None or not receipt.accepted:
        return True
    transition = next(
        (
            record
            for record in state.transitions
            if record.record_id == receipt.transition_id
        ),
        None,
    )
    if transition is None:
        return True
    if (
        transition.input_id != run.created_by_input_id
        or transition.input_kind != "workflow.claim_work"
        or transition.input_family != "workflow_kernel_command"
        or not transition.accepted
    ):
        return True
    events = _claim_audit_events(state.governance_events, run.created_by_input_id)
    traces = _claim_audit_events(state.traces, run.created_by_input_id)
    expected = (str(run.run_ref.plan_ref.authority_fingerprint),)
    return events != expected or traces != events


def _claim_audit_events(records: tuple[object, ...], input_id: str) -> tuple[str, ...]:
    return tuple(
        str(getattr(record, "plan_fingerprint", None))
        for record in records
        if getattr(record, "input_id", None) == input_id
        and getattr(record, "input_kind", None) == "workflow.claim_work"
        and getattr(record, "input_family", None) == "workflow_kernel_command"
        and getattr(record, "disposition", None) == "accepted"
    )


def _build_dispatch(
    run_id: str,
    state: RuntimeState,
) -> RunnerDispatchEnvelope | BoundedExecutionUnitResult:
    try:
        return build_dispatch_envelope_for_run(state=state, run_id=run_id)
    except DispatchProjectionError as exc:
        if exc.reason in _NON_CORRUPT_DISPATCH_REFUSALS:
            return BoundedExecutionUnitResult(
                code="no_ready_work",
                diagnostics=({"reason": exc.reason},),
            )
        return BoundedExecutionUnitResult(
            code="ready_state_corrupt",
            diagnostics=({"reason": exc.reason},),
        )


def _preclaim_adapter_refusal(
    *,
    requested_adapter_kind: str | None,
    selected_adapter_kind: str,
    local_config: AdapterLocalConfig,
) -> BoundedExecutionUnitResult | None:
    if requested_adapter_kind is not None:
        requested = _require_nonblank(requested_adapter_kind, "adapter_kind")
        if requested != selected_adapter_kind:
            return BoundedExecutionUnitResult(
                code="adapter_kind_refused",
                diagnostics=(
                    {
                        "selected_adapter_kind": selected_adapter_kind,
                        "requested_adapter_kind": requested,
                    },
                ),
            )
    if (
        selected_adapter_kind
        not in DEFAULT_SELECTED_RUNNER_ADAPTER_POLICY.supported_adapter_kinds
    ):
        return BoundedExecutionUnitResult(code="adapter_kind_refused")
    if selected_adapter_kind not in local_config.adapters:
        return BoundedExecutionUnitResult(code="adapter_failure")
    try:
        adapter = resolve_adapter(selected_adapter_kind, local_config)
    except (AdapterResolverError, TypeError, ValueError):
        return BoundedExecutionUnitResult(code="adapter_failure")
    if selected_adapter_kind == CODEX_ADAPTER_KIND and isinstance(
        adapter,
        CodexAdapter,
    ):
        config = _codex_adapter_config(adapter)
        if config is not None and any(
            not os.environ.get(flag) for flag in config.live_test_opt_in_env_flags
        ):
            return BoundedExecutionUnitResult(
                code="adapter_failure",
                adapter_error_kind="missing_opt_in_config",
            )
    return None


def _adapter_kind_for_activation(state: RuntimeState, activation_id: str) -> str:
    activation = state.activations.get(activation_id)
    if activation is None:
        raise ValueError("activation is missing")
    admitted = state.admitted_plans.get(activation.plan_ref.authority_fingerprint)
    if admitted is None:
        raise ValueError("admitted plan is missing")
    return _selected_runner_binding(
        admitted.selected_plan,
        str(activation.runner_binding_id),
    ).adapter_kind


def _selected_runner_binding(
    selected_plan: SelectedCompiledPlan,
    runner_binding_id: str,
) -> RunnerBindingDeclaration:
    matches = tuple(
        binding
        for binding in selected_plan.runner_bindings
        if str(binding.id) == runner_binding_id
    )
    if len(matches) != 1:
        raise ValueError("runner binding must resolve exactly once")
    return matches[0]


def _selected_runner_authority_for_request(
    selected_plan: SelectedCompiledPlan,
    dispatch: RunnerDispatchEnvelope,
) -> tuple[
    RunnerComponentPin | None,
    tuple[RunnerTerminalResultMapping, ...],
    tuple[ArtifactSchemaDeclaration, ...],
]:
    binding = _selected_runner_binding(selected_plan, dispatch.runner_binding_id)
    if binding.component_pin is None and not binding.terminal_result_mappings:
        return None, (), ()
    mappings = tuple(
        mapping
        for mapping in binding.terminal_result_mappings
        if str(mapping.stage_kind_id) == dispatch.stage_kind_id
    )
    schema_ids = {
        str(option["artifact_schema_id"])
        for option in dispatch.terminal_options
        if option["artifact_schema_id"] is not None
    }
    schemas = tuple(
        schema
        for schema in selected_plan.artifact_schemas
        if str(schema.id) in schema_ids
    )
    return binding.component_pin, mappings, schemas


def _ready_state_result(
    candidates: tuple[object, ...],
    diagnostics: tuple[ReadyDispatchDiagnostic, ...],
) -> BoundedExecutionUnitResult:
    if candidates:
        raise ValueError("ready state result cannot include candidates")
    if not diagnostics or all(
        diagnostic.severity == "non_candidate" for diagnostic in diagnostics
    ):
        return BoundedExecutionUnitResult(code="no_ready_work")
    code = (
        "ready_state_corrupt"
        if any(diagnostic.severity == "corrupt_authority" for diagnostic in diagnostics)
        else "ready_state_refused"
    )
    return BoundedExecutionUnitResult(
        code=code,
        diagnostics=_diagnostics_payload(diagnostics),
    )


def _diagnostics_payload(
    diagnostics: tuple[ReadyDispatchDiagnostic, ...],
) -> tuple[Mapping[str, object], ...]:
    return tuple(
        {
            "activation_id": diagnostic.activation_id,
            "work_item_id": diagnostic.work_item_id,
            "reason_code": diagnostic.reason_code,
            "severity": diagnostic.severity,
            "plan_fingerprint": diagnostic.plan_fingerprint,
            "message": diagnostic.message,
            "detail": diagnostic.detail,
        }
        for diagnostic in diagnostics
    )


def _effective_local_config(
    local_config: AdapterLocalConfig | None,
    local_config_path: Path | None,
) -> AdapterLocalConfig:
    if local_config is not None and local_config_path is not None:
        raise CliCommandError(
            command=_COMMAND,
            code="invalid_adapter_config",
            message="Pass either local_config or local_config_path, not both.",
            exit_code=ExitCode.CLI_USAGE,
            details={},
        )
    if local_config_path is not None:
        return load_adapter_local_config(local_config_path)
    if local_config is None:
        return AdapterLocalConfig()
    if not isinstance(local_config, AdapterLocalConfig):
        raise TypeError("local_config must be AdapterLocalConfig")
    return local_config


def _codex_config_from_json(value: object) -> CodexAdapterConfig:
    if not isinstance(value, Mapping):
        raise ValueError("codex adapter config must be an object")
    redaction_policy = _redaction_policy_from_json(value.get("redaction_policy"))
    return CodexAdapterConfig(
        adapter_id=_json_string(value, "adapter_id"),
        wrapper_mode=_json_string(value, "wrapper_mode"),
        wrapper_argv=_json_optional_string_tuple(value, "wrapper_argv"),
        cwd=Path(_json_string(value, "cwd")),
        env_allowlist=_json_string_mapping(value.get("env_allowlist", {})),
        timeout_seconds=_json_number(value, "timeout_seconds"),
        max_input_bundle_bytes=_json_int(value, "max_input_bundle_bytes"),
        max_stdout_bytes=_json_int(value, "max_stdout_bytes"),
        max_stderr_diagnostic_bytes=_json_int(
            value,
            "max_stderr_diagnostic_bytes",
        ),
        redaction_policy=redaction_policy,
        live_test_opt_in_env_flags=_json_string_tuple(
            value.get("live_test_opt_in_env_flags", ()),
            "live_test_opt_in_env_flags",
        ),
        pre_cancelled=_json_bool(value.get("pre_cancelled", False), "pre_cancelled"),
    )


def _millforge_config_from_json(value: object) -> MillforgeAdapterConfig:
    if not isinstance(value, Mapping):
        raise ValueError("millforge adapter config must be an object")
    expected_keys = {
        "adapter_id",
        "workspace_root",
        "timeout_seconds",
        "model_profile",
        "secret_ref",
        "redaction_policy",
    }
    if set(value) != expected_keys:
        raise ValueError("millforge adapter config has an invalid envelope")
    workspace_root = Path(_json_string(value, "workspace_root"))
    if not workspace_root.is_absolute():
        raise ValueError("workspace_root must be absolute")
    model_profile = value["model_profile"]
    secret_ref = value["secret_ref"]
    if not isinstance(model_profile, Mapping) or not isinstance(secret_ref, Mapping):
        raise ValueError("millforge provider records must be objects")
    if model_profile.get("configured_headers") not in (None, {}):
        raise ValueError("millforge adapter config cannot set provider headers")
    redaction_policy = _redaction_policy_from_json(value["redaction_policy"])
    if redaction_policy.secret_tokens:
        raise ValueError("millforge adapter config cannot contain secret tokens")
    return MillforgeAdapterConfig.for_live(
        adapter_id=_json_string(value, "adapter_id"),
        workspace_root=workspace_root,
        timeout_seconds=_json_number(value, "timeout_seconds"),
        redaction_policy=redaction_policy,
        model_profile=cast(Mapping[str, object], model_profile),
        secret_ref=cast(Mapping[str, object], secret_ref),
    )


def _redaction_policy_from_json(value: object) -> RedactionPolicy:
    if not isinstance(value, Mapping):
        raise ValueError("redaction_policy must be an object")
    return RedactionPolicy(
        policy_id=_json_string(value, "policy_id"),
        secret_tokens=_json_string_tuple(
            value.get("secret_tokens", ()),
            "secret_tokens",
        ),
    )


def _json_string(mapping: Mapping[str, object], key: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a nonblank string")
    return value


def _json_int(mapping: Mapping[str, object], key: str) -> int:
    value = mapping.get(key)
    if type(value) is not int:
        raise ValueError(f"{key} must be an integer")
    return value


def _json_number(mapping: Mapping[str, object], key: str) -> float:
    value = mapping.get(key)
    if type(value) not in {int, float}:
        raise ValueError(f"{key} must be a number")
    return float(cast(int | float, value))


def _json_bool(value: object, key: str) -> bool:
    if type(value) is not bool:
        raise ValueError(f"{key} must be a bool")
    return value


def _json_string_mapping(value: object) -> Mapping[str, str]:
    if not isinstance(value, Mapping):
        raise ValueError("env_allowlist must be an object")
    result: dict[str, str] = {}
    for key, nested_value in value.items():
        if not isinstance(key, str) or not isinstance(nested_value, str):
            raise ValueError("env_allowlist must contain string keys and values")
        result[key] = nested_value
    return result


def _json_optional_string_tuple(
    mapping: Mapping[str, object],
    key: str,
) -> tuple[str, ...] | None:
    value = mapping.get(key)
    if value is None:
        return None
    return _json_string_tuple(value, key)


def _json_string_tuple(value: object, key: str) -> tuple[str, ...]:
    if isinstance(value, list):
        raw = tuple(value)
    elif isinstance(value, tuple):
        raw = value
    else:
        raise ValueError(f"{key} must be a string list")
    if any(not isinstance(item, str) or not item.strip() for item in raw):
        raise ValueError(f"{key} must contain only nonblank strings")
    return cast(tuple[str, ...], raw)


def _adapter_id_for_request(
    adapter_kind: str,
    local_config: AdapterLocalConfig,
) -> str:
    adapter = local_config.adapters.get(adapter_kind)
    config = _adapter_config(adapter)
    adapter_id = getattr(config, "adapter_id", None)
    if isinstance(adapter_id, str) and adapter_id.strip():
        return adapter_id
    return adapter_kind


def _redaction_policy_for_adapter(
    adapter_kind: str,
    local_config: AdapterLocalConfig,
) -> RedactionPolicy:
    adapter = local_config.adapters.get(adapter_kind)
    config = _adapter_config(adapter)
    policy = getattr(config, "redaction_policy", None)
    if isinstance(policy, RedactionPolicy):
        return policy
    return _DEFAULT_REDACTION_POLICY


def _selected_invocation_timeout_seconds(
    selected_plan: SelectedCompiledPlan,
    runner_binding_id: str,
) -> int:
    return _selected_runner_binding(
        selected_plan,
        runner_binding_id,
    ).invocation_timeout_seconds


def _codex_adapter_config(adapter: CodexAdapter) -> CodexAdapterConfig | None:
    config = getattr(adapter, "_config", None)
    return config if isinstance(config, CodexAdapterConfig) else None


def _adapter_config(adapter: object) -> object | None:
    if isinstance(adapter, CodexAdapter):
        return _codex_adapter_config(adapter)
    return getattr(adapter, "config", None)


def _with_active_ids(
    result: BoundedExecutionUnitResult,
    active: _ActiveRun,
) -> BoundedExecutionUnitResult:
    return BoundedExecutionUnitResult(
        code=result.code,
        accepted=result.accepted,
        activation_id=active.activation.activation_id,
        run_id=active.run.run_ref.run_id,
        claim_id=active.run.run_ref.claim_id,
        fencing_token=active.run.run_ref.fencing_token,
        diagnostics=result.diagnostics,
        adapter_error_kind=result.adapter_error_kind,
        observation_refusal_reason=result.observation_refusal_reason,
        transition_disposition=result.transition_disposition,
    )


def _load(runtime: OpenRuntimeContext) -> RuntimeState:
    return runtime.store.load_runtime_state(runtime.cas_store)


def _created_run_id(state: RuntimeState, input_id: str) -> str | None:
    return next(
        (
            run.run_ref.run_id
            for run in sorted(state.runs.values(), key=lambda item: item.run_ref.run_id)
            if run.created_by_input_id == input_id
        ),
        None,
    )


def _claim_input_id(activation_id: str) -> str:
    return f"cli:{_COMMAND}:claim:{activation_id}"


def _observation_input_id(run_id: str) -> str:
    return f"cli:{_COMMAND}:observe:{run_id}"


def _require_nonblank(value: str, field_name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    if not value.strip():
        raise ValueError(f"{field_name} must be nonblank")
    return value


def _optional_cli_nonblank(value: str | None, field_name: str) -> str | None:
    if value is None:
        return None
    return _cli_nonblank(value, field_name)


def _cli_nonblank(value: str, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CliCommandError(
            command=_COMMAND,
            code="invalid_run_option",
            message=f"{field_name} must be a nonblank string.",
            exit_code=ExitCode.CLI_USAGE,
            details={"field": field_name},
        )
    return value


__all__ = (
    "BoundedExecutionUnitResult",
    "load_adapter_local_config",
    "run_bounded_execution_unit",
)
