"""Execution-plane stage runtime and transition-history helpers."""

from __future__ import annotations

from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Protocol

from ..compiler_rebinding import FrozenExecutionParameterBinder
from ..compounding import (
    clear_run_scoped_procedure_candidates,
    flush_milestone_for_transition,
    flush_run_scoped_compounding_candidates,
    persist_candidate_from_transition,
)
from ..config import StageConfig
from ..contracts import ControlPlane as RuntimeControlPlane
from ..contracts import (
    ExecutionStatus,
    RunnerKind,
    StageOverrideField,
    StageResult,
    StageType,
    TaskCard,
)
from ..events import EventType
from ..execution_nodes import execution_stage_type_for_node
from ..policies import (
    PolicyEvaluationRecord,
    PolicyHookError,
    build_execution_policy_runtime,
    execution_pacing_context_from_records,
    execution_preflight_context_from_records,
    stage_runtime_from_execution_stage,
)
from ..provenance import (
    COMPOUNDING_BUDGET_ATTRIBUTE,
    COMPOUNDING_FLUSH_ATTRIBUTE,
    COMPOUNDING_PROFILE_ATTRIBUTE,
    CONTEXT_FACT_INJECTION_ATTRIBUTE,
    PROCEDURE_INJECTION_ATTRIBUTE,
    ROUTING_MODE_ATTRIBUTE,
    BoundExecutionParameters,
    ExecutionParameterRebindingRequest,
    RuntimeProvenanceContext,
    TransitionHistoryStore,
    clear_transition_history,
    runtime_stage_parameter_key,
)
from ..queue import TaskQueue
from ..runner import ClaudeRunner, CodexRunner, SubprocessRunner
from ..stages.base import ExecutionStage, StageExecutionError
from ..stages.builder import BuilderStage
from ..stages.consult import ConsultStage
from ..stages.doublecheck import DoublecheckStage
from ..stages.hotfix import HotfixStage
from ..stages.integrate import IntegrationStage
from ..stages.qa import QAStage
from ..stages.troubleshoot import TroubleshootStage
from ..stages.update import UpdateStage
from ..standard_runtime import rebound_execution_parameters_for_mode
from ..status import ControlPlane, StatusChange, StatusStore

if TYPE_CHECKING:
    from ..compiler import FrozenRunPlan, FrozenStagePlan
    from ..config import EngineConfig
    from ..paths import RuntimePaths


class ExecutionRuntimePlane(Protocol):
    config: EngineConfig
    paths: RuntimePaths
    queue: TaskQueue
    status_store: StatusStore
    runners: dict[RunnerKind, SubprocessRunner | CodexRunner | ClaudeRunner]
    stages: dict[StageType, ExecutionStage]
    _stage_commands: dict[object, tuple[str, ...]]
    _active_frozen_plan: FrozenRunPlan | None
    _resolved_frozen_stages: dict[str, ExecutionStage]
    _runtime_parameter_binder: FrozenExecutionParameterBinder | None
    runtime_provenance: RuntimeProvenanceContext
    _custom_policy_runtime: bool
    policy_runtime: object
    _transport_probe: object
    before_stage: Callable[[StageType], None] | None
    transition_history: TransitionHistoryStore | None
    _policy_routing_mode: str | None
    _status_event_context: dict[str, object] | None

    def _stage_plan(self, node_id: str) -> FrozenStagePlan: ...

    def _handle_status_change(self, change: StatusChange) -> None: ...

    def _emit_event(self, event_type: EventType, payload: dict[str, Any] | None = None) -> None: ...

    def _record_policy_evaluations(self, records: tuple[PolicyEvaluationRecord, ...]) -> None: ...

    def _bound_execution_parameters_for_node(self, node_id: str) -> BoundExecutionParameters: ...

    def _kind_id_for_stage(self, stage_type: StageType) -> str: ...


def rebuild_stages(plane: ExecutionRuntimePlane) -> dict[StageType, ExecutionStage]:
    """Rebuild the concrete stage objects against the current runtime dependencies."""

    commands = plane._stage_commands
    return {
        StageType.BUILDER: BuilderStage(
            plane.config,
            plane.paths,
            plane.runners,
            plane.status_store,
            commands.get(StageType.BUILDER),
        ),
        StageType.INTEGRATION: IntegrationStage(
            plane.config,
            plane.paths,
            plane.runners,
            plane.status_store,
            commands.get(StageType.INTEGRATION),
        ),
        StageType.QA: QAStage(
            plane.config,
            plane.paths,
            plane.runners,
            plane.status_store,
            commands.get(StageType.QA),
        ),
        StageType.HOTFIX: HotfixStage(
            plane.config,
            plane.paths,
            plane.runners,
            plane.status_store,
            commands.get(StageType.HOTFIX),
        ),
        StageType.DOUBLECHECK: DoublecheckStage(
            plane.config,
            plane.paths,
            plane.runners,
            plane.status_store,
            commands.get(StageType.DOUBLECHECK),
        ),
        StageType.TROUBLESHOOT: TroubleshootStage(
            plane.config,
            plane.paths,
            plane.runners,
            plane.status_store,
            commands.get(StageType.TROUBLESHOOT),
        ),
        StageType.CONSULT: ConsultStage(
            plane.config,
            plane.paths,
            plane.runners,
            plane.status_store,
            commands.get(StageType.CONSULT),
        ),
        StageType.UPDATE: UpdateStage(
            plane.config,
            plane.paths,
            plane.runners,
            plane.status_store,
            commands.get(StageType.UPDATE),
        ),
    }


def _stage_command_for(
    plane: ExecutionRuntimePlane,
    *,
    stage_type: StageType,
    node_id: str,
    kind_id: str | None = None,
) -> tuple[str, ...] | None:
    commands = plane._stage_commands
    for key in (node_id, kind_id, stage_type, stage_type.value):
        if key is None:
            continue
        value = commands.get(key)
        if value:
            return tuple(value)
    return None


def _load_stage_handler(handler_ref: str) -> type[ExecutionStage]:
    try:
        module_name, attr_name = handler_ref.split(":", 1)
    except ValueError as exc:
        raise StageExecutionError(f"registered stage handler ref is invalid: {handler_ref!r}") from exc
    module = import_module(module_name)
    handler = getattr(module, attr_name, None)
    if not isinstance(handler, type) or not issubclass(handler, ExecutionStage):
        raise StageExecutionError(f"registered stage handler {handler_ref!r} is not an ExecutionStage")
    return handler


def resolve_stage(
    plane: ExecutionRuntimePlane,
    stage_type: StageType,
    *,
    node_id: str | None = None,
) -> ExecutionStage:
    """Resolve one concrete execution-stage instance for a public or frozen-plan node."""

    effective_node_id = node_id or stage_type.value
    cached = plane._resolved_frozen_stages.get(effective_node_id)
    if cached is not None:
        return cached
    if plane._active_frozen_plan is None:
        return plane.stages[stage_type]

    try:
        stage_plan = plane._stage_plan(effective_node_id)
    except StageExecutionError:
        return plane.stages[stage_type]

    handler_class = _load_stage_handler(stage_plan.handler_ref)
    stage = handler_class(
        plane.config,
        plane.paths,
        plane.runners,
        plane.status_store,
        _stage_command_for(
            plane,
            stage_type=stage_type,
            node_id=stage_plan.node_id,
            kind_id=stage_plan.kind_id,
        ),
    )
    bound_parameters = bound_parameters_for_node(plane, effective_node_id, stage_plan=stage_plan)
    stage.stage_config = StageConfig(
        runner=bound_parameters.runner or stage.stage_config.runner,
        model=bound_parameters.model or stage.stage_config.model,
        effort=bound_parameters.effort if bound_parameters.effort is not None else stage.stage_config.effort,
        timeout_seconds=bound_parameters.timeout_seconds or stage.stage_config.timeout_seconds,
        prompt_file=(
            Path(stage_plan.prompt_asset_ref)
            if stage_plan.prompt_asset_ref is not None
            else stage.stage_config.prompt_file
        ),
        allow_search=(
            bound_parameters.allow_search
            if bound_parameters.allow_search is not None
            else stage.stage_config.allow_search
        ),
    )
    stage._cached_prompt_resolution = stage._PROMPT_UNSET
    plane._resolved_frozen_stages[effective_node_id] = stage
    return stage


def reconfigure(plane: ExecutionRuntimePlane, config: EngineConfig, paths: RuntimePaths) -> None:
    """Refresh the runtime dependencies for future stage executions."""

    plane.config = config
    plane.paths = paths
    plane.queue = TaskQueue(paths)
    plane.status_store = StatusStore(
        paths.status_file,
        ControlPlane.EXECUTION,
        on_change=plane._handle_status_change,
    )
    plane.runners = {
        RunnerKind.SUBPROCESS: SubprocessRunner(paths),
        RunnerKind.CODEX: CodexRunner(paths),
        RunnerKind.CLAUDE: ClaudeRunner(paths),
    }
    plane.stages = rebuild_stages(plane)
    plane._resolved_frozen_stages = {}
    if plane._active_frozen_plan is not None:
        initialize_parameter_binder(plane)
        apply_active_config_rebindings(plane)
    if not plane._custom_policy_runtime:
        plane.policy_runtime = build_execution_policy_runtime(
            config,
            stage_runtime=lambda stage_type: stage_runtime_from_execution_stage(resolve_stage(plane, stage_type)),
            paths=paths,
            transport_probe=plane._transport_probe,
        )


def initialize_parameter_binder(plane: ExecutionRuntimePlane) -> None:
    """Reset the runtime rebinding view for the currently active frozen plan."""

    if plane._active_frozen_plan is None:
        plane._runtime_parameter_binder = None
        return
    plane._runtime_parameter_binder = FrozenExecutionParameterBinder(plane._active_frozen_plan)


def bound_parameters_for_node(
    plane: ExecutionRuntimePlane,
    node_id: str,
    *,
    stage_plan: object | None = None,
) -> BoundExecutionParameters:
    """Return the current runtime binding for one execution node."""

    binder = plane._runtime_parameter_binder
    if binder is not None:
        return binder.bound_parameters_for(RuntimeControlPlane.EXECUTION, node_id)
    parameters = plane.runtime_provenance.stage_parameters_for(RuntimeControlPlane.EXECUTION, node_id)
    if parameters is not None:
        return parameters
    if stage_plan is None and plane._active_frozen_plan is None:
        stage_type = execution_stage_type_for_node(node_id)
        if stage_type is not None:
            stage = plane.stages.get(stage_type)
            if stage is not None:
                return BoundExecutionParameters(
                    runner=stage.stage_config.runner,
                    model=stage.stage_config.model,
                    effort=stage.stage_config.effort,
                    allow_search=stage.stage_config.allow_search,
                    timeout_seconds=stage.stage_config.timeout_seconds,
                )
    if stage_plan is None:
        stage_plan = plane._stage_plan(node_id)
    return BoundExecutionParameters(
        model_profile_ref=getattr(stage_plan, "model_profile_ref", None),
        runner=getattr(stage_plan, "runner", None),
        model=getattr(stage_plan, "model", None),
        effort=getattr(stage_plan, "effort", None),
        allow_search=getattr(stage_plan, "allow_search", None),
        timeout_seconds=getattr(stage_plan, "timeout_seconds", None),
    )


def apply_active_config_rebindings(plane: ExecutionRuntimePlane) -> None:
    """Apply legal runtime rebinding fields from the current config to future plan nodes."""

    if plane._active_frozen_plan is None or plane._active_frozen_plan.content.execution_plan is None:
        return
    binder = plane._runtime_parameter_binder
    if binder is None:
        return

    allowed_fields_by_node: dict[str, set[StageOverrideField]] = {}
    for rule in plane._active_frozen_plan.content.parameter_rebinding_rules:
        if rule.plane is not RuntimeControlPlane.EXECUTION:
            continue
        allowed_fields_by_node.setdefault(rule.node_id, set()).add(rule.field)

    execution_stages = plane._active_frozen_plan.content.execution_plan.stages
    selected_mode_ref = plane._active_frozen_plan.content.selected_mode_ref
    if selected_mode_ref is None:
        return
    rebound_parameters = rebound_execution_parameters_for_mode(
        plane.config,
        plane.paths,
        mode_ref=selected_mode_ref,
        node_ids=tuple(stage.node_id for stage in execution_stages),
        preview_run_id=f"{plane._active_frozen_plan.run_id}-rebind-preview",
        task_complexity=(plane.queue.active_task().complexity if plane.queue.active_task() is not None else None),
    )

    for stage in execution_stages:
        allowed_fields = allowed_fields_by_node.get(stage.node_id)
        if not allowed_fields:
            continue
        current = binder.bound_parameters_for(RuntimeControlPlane.EXECUTION, stage.node_id)
        target = rebound_parameters.get(stage.node_id)
        if target is None:
            continue
        updates: dict[str, object] = {}
        if (
            StageOverrideField.MODEL_PROFILE_REF in allowed_fields
            and target.model_profile_ref is not None
            and current.model_profile_ref != target.model_profile_ref
        ):
            updates["model_profile_ref"] = target.model_profile_ref
        if (
            StageOverrideField.RUNNER in allowed_fields
            and target.runner is not None
            and current.runner != target.runner
        ):
            updates["runner"] = target.runner
        if (
            StageOverrideField.MODEL in allowed_fields
            and target.model is not None
            and current.model != target.model
        ):
            updates["model"] = target.model
        if (
            StageOverrideField.EFFORT in allowed_fields
            and target.effort is not None
            and current.effort != target.effort
        ):
            updates["effort"] = target.effort
        if StageOverrideField.ALLOW_SEARCH in allowed_fields and current.allow_search != target.allow_search:
            updates["allow_search"] = target.allow_search
        if (
            StageOverrideField.TIMEOUT_SECONDS in allowed_fields
            and target.timeout_seconds is not None
            and current.timeout_seconds != target.timeout_seconds
        ):
            updates["timeout_seconds"] = target.timeout_seconds
        if not updates:
            continue
        binder.apply(
            ExecutionParameterRebindingRequest(
                plane=RuntimeControlPlane.EXECUTION,
                node_id=stage.node_id,
                parameters=BoundExecutionParameters.model_validate(updates),
                reason="runtime config reconfigure",
            )
        )

    updated_stage_parameters = dict(plane.runtime_provenance.stage_bound_execution_parameters)
    for stage in plane._active_frozen_plan.content.execution_plan.stages:
        updated_stage_parameters[runtime_stage_parameter_key(RuntimeControlPlane.EXECUTION, stage.node_id)] = (
            binder.bound_parameters_for(RuntimeControlPlane.EXECUTION, stage.node_id)
        )
    plane.runtime_provenance = plane.runtime_provenance.model_copy(
        update={"stage_bound_execution_parameters": updated_stage_parameters}
    )
    plane._resolved_frozen_stages = {}


def stage_context_payload(
    plane: ExecutionRuntimePlane,
    stage_type: StageType,
    *,
    task: TaskCard | None,
    run_id: str,
    node_id: str | None = None,
) -> dict[str, object]:
    """Build the shared event payload for one stage invocation."""

    payload: dict[str, object] = {"stage": stage_type.value, "run_id": run_id, "node_id": node_id or stage_type.value}
    if task is not None:
        payload["task_id"] = task.task_id
        payload["title"] = task.title
    return payload


def handle_status_change(plane: ExecutionRuntimePlane, change: StatusChange) -> None:
    """Emit the execution-plane status-change event with stage context when available."""

    payload: dict[str, object] = {
        "plane": change.plane.value,
        "status": change.current.value,
        "transition_mode": change.mode,
    }
    if change.previous is not None:
        payload["previous_status"] = change.previous.value
    if plane._status_event_context is not None:
        payload.update(plane._status_event_context)
    plane._emit_event(EventType.STATUS_CHANGED, payload)


def bound_parameters_from_result(plane: ExecutionRuntimePlane, result: StageResult) -> BoundExecutionParameters:
    """Resolve the bound execution parameters recorded for one stage result."""

    node_id = str(result.metadata.get("node_id") or result.stage.value).strip() or result.stage.value
    compile_time_parameters = plane._bound_execution_parameters_for_node(node_id)
    payload = result.metadata.get("bound_execution_parameters")
    if isinstance(payload, BoundExecutionParameters):
        return compile_time_parameters.apply(payload)
    if isinstance(payload, dict):
        return compile_time_parameters.apply(BoundExecutionParameters.model_validate(payload))
    stage_config = resolve_stage(plane, result.stage, node_id=node_id).stage_config
    return compile_time_parameters.apply(
        BoundExecutionParameters(
            runner=stage_config.runner,
            model=stage_config.model,
            effort=stage_config.effort,
            allow_search=stage_config.allow_search,
            timeout_seconds=stage_config.timeout_seconds,
        )
    )


def start_transition_history(plane: ExecutionRuntimePlane, run_id: str) -> TransitionHistoryStore:
    """Reset and open the transition-history store for one run."""

    history_path = plane.paths.runs_dir / run_id / "transition_history.jsonl"
    clear_transition_history(history_path)
    clear_run_scoped_procedure_candidates(plane.paths, run_id)
    plane.transition_history = TransitionHistoryStore(
        history_path,
        run_id=run_id,
        provenance=plane.runtime_provenance,
    )
    return plane.transition_history


def record_stage_transition(
    plane: ExecutionRuntimePlane,
    result: StageResult,
    *,
    task_before: TaskCard | None,
    task_after: TaskCard | None,
    routing_mode: str,
    selected_edge_id: str,
    selected_edge_reason: str,
    selected_terminal_state_id: str | None = None,
    condition_inputs: dict[str, object] | None = None,
    condition_result: bool | None = None,
    queue_mutations_applied: tuple[str, ...] = (),
    attributes: dict[str, object] | None = None,
) -> None:
    """Append one runtime transition-history record when a history store is active."""

    if plane.transition_history is None:
        return
    node_id = str(result.metadata.get("node_id") or result.stage.value).strip() or result.stage.value
    status_before = str(result.metadata.get("status_before") or "").strip() or None
    status_after = result.status
    status = ExecutionStatus(status_after)
    outcome = "success" if resolve_stage(plane, result.stage, node_id=node_id).is_success_status(status) else status.value.lower()
    record_attributes = {"routing_mode": routing_mode}
    injection_payload = result.metadata.get("procedure_injection")
    if isinstance(injection_payload, dict):
        record_attributes[PROCEDURE_INJECTION_ATTRIBUTE] = injection_payload
    fact_injection_payload = result.metadata.get("context_fact_injection")
    if isinstance(fact_injection_payload, dict):
        record_attributes[CONTEXT_FACT_INJECTION_ATTRIBUTE] = fact_injection_payload
    compounding_profile = result.metadata.get("compounding_profile")
    if isinstance(compounding_profile, str):
        profile_value = compounding_profile.strip()
        if profile_value:
            record_attributes[COMPOUNDING_PROFILE_ATTRIBUTE] = profile_value
    compounding_budget_payload = result.metadata.get("compounding_budget")
    if isinstance(compounding_budget_payload, dict):
        record_attributes[COMPOUNDING_BUDGET_ATTRIBUTE] = compounding_budget_payload
    if attributes:
        record_attributes.update(attributes)
    record_attributes.setdefault(
        "compile_time_stage_parameter_key",
        runtime_stage_parameter_key(ControlPlane.EXECUTION, node_id),
    )
    record = plane.transition_history.append(
        event_name="execution.stage.transition",
        source="execution_plane",
        plane=ControlPlane.EXECUTION,
        node_id=node_id,
        kind_id=plane._kind_id_for_stage(result.stage),
        outcome=outcome,
        selected_edge_id=selected_edge_id,
        selected_terminal_state_id=selected_terminal_state_id,
        selected_edge_reason=selected_edge_reason,
        condition_inputs=condition_inputs or {},
        condition_result=condition_result,
        status_before=status_before,
        status_after=status_after,
        active_task_before=task_before.task_id if task_before is not None else None,
        active_task_after=task_after.task_id if task_after is not None else None,
        artifacts_emitted=tuple(path.as_posix() for path in result.artifacts),
        queue_mutations_applied=queue_mutations_applied,
        bound_execution_parameters=bound_parameters_from_result(plane, result),
        attributes=record_attributes,
    )
    candidate_path = persist_candidate_from_transition(plane.paths, record, result)
    milestone = flush_milestone_for_transition(
        stage=result.stage,
        selected_edge_id=selected_edge_id,
        candidate_created=candidate_path is not None,
    )
    if milestone is None:
        return
    checkpoint = flush_run_scoped_compounding_candidates(
        plane.paths,
        run_id=record.run_id,
        trigger_stage=result.stage,
        milestone=milestone,
    )
    plane.transition_history.append(
        event_name="execution.compounding.flush",
        source="execution_plane",
        plane=ControlPlane.EXECUTION,
        node_id=node_id,
        kind_id=plane._kind_id_for_stage(result.stage),
        outcome="success",
        selected_edge_reason=f"compounding flush checkpoint: {milestone.value}",
        active_task_before=record.active_task_before,
        active_task_after=record.active_task_after,
        bound_execution_parameters=record.bound_execution_parameters,
        attributes={
            ROUTING_MODE_ATTRIBUTE: routing_mode,
            COMPOUNDING_FLUSH_ATTRIBUTE: checkpoint.model_dump(mode="json"),
        },
    )


def run_stage(
    plane: ExecutionRuntimePlane,
    stage_type: StageType,
    task: TaskCard | None,
    run_id: str,
    *,
    node_id: str | None = None,
) -> StageResult:
    """Run one stage with event emission and normalized metadata capture."""

    effective_node_id = node_id or stage_type.value
    if plane.before_stage is not None:
        plane.before_stage(stage_type)
    stage = resolve_stage(plane, stage_type, node_id=effective_node_id)
    status_before = plane.status_store.read()
    preflight_context = None
    if plane._active_frozen_plan is not None and plane.transition_history is not None:
        try:
            pre_stage_records = plane.policy_runtime.evaluate_pre_stage(
                run_id=run_id,
                routing_mode=plane._policy_routing_mode,
                execution_status=status_before,
                active_task=task,
                backlog_depth=plane.queue.backlog_depth(),
                transition_history_count=plane.transition_history.record_count,
                frozen_plan=plane._active_frozen_plan,
                snapshot_id=plane.runtime_provenance.snapshot_id,
                stage_type=stage_type,
                node_id=effective_node_id,
            )
            plane._record_policy_evaluations(pre_stage_records)
            preflight_context = execution_preflight_context_from_records(pre_stage_records)
        except PolicyHookError as exc:
            raise StageExecutionError(f"pre-stage policy hook failed: {exc}") from exc
    payload = stage_context_payload(plane, stage_type, task=task, run_id=run_id, node_id=effective_node_id)
    metadata: dict[str, Any] = {
        "node_id": effective_node_id,
        "status_before": status_before.value if isinstance(status_before, ExecutionStatus) else str(status_before),
    }
    if preflight_context is not None:
        metadata["policy_preflight"] = preflight_context.model_dump(mode="json")
    if preflight_context is not None and preflight_context.block_status is not None:
        if status_before is not preflight_context.block_status:
            plane.status_store.transition(preflight_context.block_status)
        compile_time_parameters = plane._bound_execution_parameters_for_node(effective_node_id)
        blocked_bound_parameters = compile_time_parameters.apply(
            BoundExecutionParameters(
                runner=stage.stage_config.runner,
                model=stage.stage_config.model,
                effort=stage.stage_config.effort,
                allow_search=preflight_context.allow_search,
                timeout_seconds=stage.stage_config.timeout_seconds,
            )
        )
        blocked_result = StageResult.model_validate(
            {
                "stage": stage_type,
                "status": preflight_context.block_status.value,
                "exit_code": 0,
                "metadata": {
                    **metadata,
                    "bound_execution_parameters": blocked_bound_parameters.model_dump(mode="json"),
                    "policy_execution_context": {
                        "allow_search": preflight_context.allow_search,
                        "allow_network": preflight_context.allow_network,
                    },
                },
            }
        )
        failure_payload = {
            **payload,
            "status": blocked_result.status,
            "policy_outcome": preflight_context.outcome.value,
            "policy_reason": preflight_context.reason,
        }
        plane._emit_event(EventType.STAGE_FAILED, failure_payload)
        return blocked_result
    plane._emit_event(EventType.STAGE_STARTED, payload)
    previous_context = plane._status_event_context
    plane._status_event_context = dict(payload)
    try:
        result = stage.run(
            task,
            run_id,
            allow_search_override=(preflight_context.allow_search if preflight_context is not None else None),
            allow_network_override=(preflight_context.allow_network if preflight_context is not None else None),
        )
    except Exception as exc:
        failure_payload = dict(payload)
        failure_payload["error"] = str(exc)
        plane._emit_event(EventType.STAGE_FAILED, failure_payload)
        setattr(exc, "_millrace_stage_failed_emitted", True)
        raise
    finally:
        plane._status_event_context = previous_context

    result_status = ExecutionStatus(result.status)
    result_payload: dict[str, Any] = {
        **payload,
        "status": result.status,
        "exit_code": result.exit_code,
        "run_dir": result.runner_result.run_dir if result.runner_result is not None else None,
    }
    if stage.is_success_status(result_status):
        plane._emit_event(EventType.STAGE_COMPLETED, result_payload)
    else:
        plane._emit_event(EventType.STAGE_FAILED, result_payload)
    metadata = {**metadata, **dict(result.metadata)}
    metadata.setdefault(
        "bound_execution_parameters",
        bound_parameters_from_result(plane, result).model_dump(mode="json"),
    )
    normalized_result = result.model_copy(update={"metadata": metadata})
    if plane._active_frozen_plan is not None and plane.transition_history is not None:
        try:
            post_stage_records = plane.policy_runtime.evaluate_post_stage(
                run_id=run_id,
                routing_mode=plane._policy_routing_mode,
                execution_status=plane.status_store.read(),
                active_task=task,
                backlog_depth=plane.queue.backlog_depth(),
                transition_history_count=plane.transition_history.record_count,
                frozen_plan=plane._active_frozen_plan,
                snapshot_id=plane.runtime_provenance.snapshot_id,
                stage_type=stage_type,
                node_id=effective_node_id,
                stage_result_status=normalized_result.status,
                stage_result_exit_code=normalized_result.exit_code,
            )
            plane._record_policy_evaluations(post_stage_records)
            pacing_context = execution_pacing_context_from_records(post_stage_records)
            if pacing_context is not None:
                normalized_result = normalized_result.model_copy(
                    update={
                        "metadata": {
                            **dict(normalized_result.metadata),
                            "policy_pacing": pacing_context.model_dump(mode="json"),
                        }
                    }
                )
        except PolicyHookError as exc:
            raise StageExecutionError(f"post-stage policy hook failed: {exc}") from exc
    return normalized_result


__all__ = [
    "apply_active_config_rebindings",
    "bound_parameters_from_result",
    "bound_parameters_for_node",
    "handle_status_change",
    "initialize_parameter_binder",
    "rebuild_stages",
    "record_stage_transition",
    "reconfigure",
    "resolve_stage",
    "run_stage",
    "stage_context_payload",
    "start_transition_history",
]
