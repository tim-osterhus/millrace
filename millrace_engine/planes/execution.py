"""Execution-plane routing and escalation state machine."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from ..compiler import FrozenRunPlan, FrozenStagePlan
from ..compiler_rebinding import FrozenExecutionParameterBinder
from ..config import EngineConfig
from ..contracts import (
    CrossPlaneParentRun,
    ExecutionResearchHandoff,
    ExecutionStatus,
    StageResult,
    StageType,
    TaskCard,
)
from ..events import EventType
from ..markdown import write_text_atomic
from ..paths import RuntimePaths
from ..policies import (
    POLICY_CYCLE_NODE_ID,
    DefaultTransportProbe,
    ExecutionIntegrationContext,
    ExecutionIntegrationSnapshot,
    ExecutionPreflightContext,
    ExecutionPreflightEvaluator,
    PolicyEvaluationRecord,
    PolicyHookRuntime,
    SizeClass,
    SizeClassificationView,
    TransportProbe,
    execution_integration_context_from_records,
    execution_pacing_context_from_records,
    execution_preflight_context,
    execution_usage_budget_context_from_records,
    refresh_size_status,
    resolve_execution_integration_context,
)
from ..provenance import (
    BoundExecutionParameters,
    RuntimeProvenanceContext,
    TransitionHistoryStore,
    read_transition_history,
)
from ..run_ids import timestamped_slug_id
from ..stages.base import ExecutionStage, StageExecutionError
from ..status import StatusChange
from .base import PlaneRuntime, StageCommandMap
from .execution_flows import (
    handle_qa_outcome as handle_qa_outcome_flow,
)
from .execution_flows import (
    run_builder_success_sequence as run_builder_success_sequence_flow,
)
from .execution_flows import (
    run_execution_cycle as run_execution_cycle_flow,
)
from .execution_flows import (
    run_full_task_path as run_full_task_path_flow,
)
from .execution_flows import (
    run_quickfix_loop as run_quickfix_loop_flow,
)
from .execution_recovery import (
    _RecoveryResult,
)
from .execution_recovery import (
    create_blocker_bundle as create_blocker_bundle_helper,
)
from .execution_recovery import (
    quarantine_task as quarantine_task_helper,
)
from .execution_recovery import (
    recover_or_quarantine as recover_or_quarantine_helper,
)
from .execution_recovery import (
    resume_after_recovery as resume_after_recovery_helper,
)
from .execution_recovery import (
    write_blocker_entry as write_blocker_entry_helper,
)
from .execution_routing import stage_plan as stage_plan_helper
from .execution_runtime import (
    apply_active_config_rebindings as apply_active_config_rebindings_helper,
)
from .execution_runtime import (
    bound_parameters_for_node as bound_parameters_for_node_helper,
)
from .execution_runtime import (
    handle_status_change as handle_status_change_helper,
)
from .execution_runtime import (
    initialize_parameter_binder as initialize_parameter_binder_helper,
)
from .execution_runtime import (
    rebuild_stages as rebuild_stages_helper,
)
from .execution_runtime import (
    reconfigure as reconfigure_helper,
)
from .execution_runtime import (
    record_stage_transition as record_stage_transition_helper,
)
from .execution_runtime import (
    run_stage as run_stage_helper,
)
from .execution_runtime import (
    stage_context_payload as stage_context_payload_helper,
)
from .execution_runtime import (
    start_transition_history as start_transition_history_helper,
)

MAX_LOCAL_RECOVERY_ROUNDS = 2
ROUTING_MODE_FIXED_V1_BACKLOG_EMPTY = "fixed_v1_backlog_empty"
ROUTING_MODE_FIXED_V1_FALLBACK = "fixed_v1_fallback"
ROUTING_MODE_FROZEN_PLAN = "frozen_plan"
ROUTING_MODE_FROZEN_PLAN_LEGACY_RESUME = "frozen_plan_legacy_resume"
ADAPTIVE_UPSCOPE_RULE = "blocked_small_non_usage_v1"
QUICKFIX_ARTIFACT_SCAFFOLD = "# Quickfix\n"


@dataclass(frozen=True, slots=True)
class ExecutionCycleResult:
    """Visible outcome of one execution-plane cycle."""

    run_id: str | None
    final_status: ExecutionStatus
    stage_results: list[StageResult] = field(default_factory=list)
    promoted_task: TaskCard | None = None
    archived_task: TaskCard | None = None
    quarantined_task: TaskCard | None = None
    diagnostics_dir: Path | None = None
    update_only: bool = False
    quickfix_attempts: int = 0
    transition_history_path: Path | None = None
    research_handoff: ExecutionResearchHandoff | None = None
    pause_requested: bool = False
    pause_reason: str | None = None
    pacing_delay_seconds: int = 0


class ExecutionPlane(PlaneRuntime):
    """Execution-plane routing with quickfix and research-freeze escalation."""

    def __init__(
        self,
        config: EngineConfig,
        paths: RuntimePaths,
        *,
        stage_commands: StageCommandMap | None = None,
        before_stage: Callable[[StageType], None] | None = None,
        emit_event: Callable[[EventType, dict[str, Any]], None] | None = None,
        runtime_provenance: RuntimeProvenanceContext | None = None,
        policy_runtime: PolicyHookRuntime | None = None,
        transport_probe: TransportProbe | None = None,
    ) -> None:
        super().__init__(config, paths)
        self.before_stage = before_stage
        self.emit_event = emit_event
        self.runtime_provenance = runtime_provenance or RuntimeProvenanceContext()
        self._custom_policy_runtime = policy_runtime is not None
        self._transport_probe = transport_probe or DefaultTransportProbe()
        self.policy_runtime = policy_runtime or PolicyHookRuntime()
        self._active_frozen_plan: FrozenRunPlan | None = None
        self._runtime_parameter_binder: FrozenExecutionParameterBinder | None = None
        self._resolved_frozen_stages: dict[str, ExecutionStage] = {}
        self.transition_history: TransitionHistoryStore | None = None
        self._status_event_context: dict[str, object] | None = None
        self._policy_routing_mode: str | None = None
        self._cycle_integration_context: ExecutionIntegrationContext | None = None
        self._last_research_handoff: ExecutionResearchHandoff | None = None
        self._quickfix_artifact_active_for_cycle = False
        self.policy_evaluations: list[PolicyEvaluationRecord] = []
        self._stage_commands = {
            key: tuple(value) if value else ()
            for key, value in (stage_commands or {}).items()
        }
        self.reconfigure(config, paths)

    def _rebuild_stages(self) -> dict[StageType, ExecutionStage]:
        return rebuild_stages_helper(self)

    def reconfigure(self, config: EngineConfig, paths: RuntimePaths) -> None:
        """Refresh in-place dependencies for future stage executions."""

        reconfigure_helper(self, config, paths)

    def _emit_event(self, event_type: EventType, payload: dict[str, Any] | None = None) -> None:
        if self.emit_event is None:
            return
        self.emit_event(event_type, payload or {})

    def _mark_quickfix_artifact_active(self) -> None:
        self._quickfix_artifact_active_for_cycle = True

    def _clear_active_quickfix_artifact(self) -> None:
        if not self._quickfix_artifact_active_for_cycle:
            return
        quickfix_path = self.paths.agents_dir / "quickfix.md"
        if quickfix_path.exists():
            current_text = quickfix_path.read_text(encoding="utf-8")
            if current_text != QUICKFIX_ARTIFACT_SCAFFOLD:
                write_text_atomic(quickfix_path, QUICKFIX_ARTIFACT_SCAFFOLD)
        self._quickfix_artifact_active_for_cycle = False

    def _initialize_parameter_binder(self) -> None:
        initialize_parameter_binder_helper(self)

    def _apply_active_config_rebindings(self) -> None:
        apply_active_config_rebindings_helper(self)

    def _bound_execution_parameters_for_node(self, node_id: str) -> BoundExecutionParameters:
        return bound_parameters_for_node_helper(self, node_id)

    def _stage_context_payload(
        self,
        stage_type: StageType,
        *,
        task: TaskCard | None,
        run_id: str,
    ) -> dict[str, object]:
        return stage_context_payload_helper(self, stage_type, task=task, run_id=run_id)

    def _handle_status_change(self, change: StatusChange) -> None:
        handle_status_change_helper(self, change)

    def _new_run_id(self, task: TaskCard | None, label: str) -> str:
        if task is not None:
            return timestamped_slug_id(task.task_id, fallback="run")
        return timestamped_slug_id(label, fallback="run")

    def _integration_context(self, task: TaskCard | None = None) -> ExecutionIntegrationContext:
        if self._cycle_integration_context is not None:
            return self._cycle_integration_context

        plan_node_ids: tuple[str, ...]
        policy_toggle_integration_mode: str | None = None
        if self._active_frozen_plan is not None and self._active_frozen_plan.content.execution_plan is not None:
            plan_node_ids = tuple(
                stage.node_id for stage in self._active_frozen_plan.content.execution_plan.stages
            )
            if self._active_frozen_plan.content.policy_toggles is not None:
                policy_toggle_integration_mode = self._active_frozen_plan.content.policy_toggles.integration_mode
        else:
            available_nodes = {
                stage.value
                for stage in (
                    self.config.routing.builder_success_sequence
                    + self.config.routing.builder_success_sequence_with_integration
                )
            }
            if StageType.UPDATE in self.stages:
                available_nodes.add(StageType.UPDATE.value)
            if StageType.BUILDER in self.stages:
                available_nodes.add(StageType.BUILDER.value)
            plan_node_ids = tuple(sorted(available_nodes))

        return resolve_execution_integration_context(
            ExecutionIntegrationSnapshot.from_config(self.config),
            task=task,
            policy_toggle_integration_mode=policy_toggle_integration_mode,
            execution_node_ids=plan_node_ids,
        )

    def _should_run_integration(self, task: TaskCard | None = None) -> bool:
        return self._integration_context(task).should_run_integration

    def _selected_builder_sequence(self, task: TaskCard | None = None) -> tuple[StageType, ...]:
        return self._integration_context(task).effective_sequence

    def _builder_success_target(self) -> str:
        return self._integration_context().builder_success_target

    def _refresh_size_status(self, task: TaskCard | None) -> SizeClassificationView:
        return refresh_size_status(
            root=self.paths.root,
            task=task,
            config=self.config.sizing,
            latch_path=self.paths.size_status_file,
        )

    def _latest_preflight_block_context(self, *, task_id: str) -> ExecutionPreflightContext | None:
        runs_dir = self.paths.runs_dir
        if not runs_dir.exists():
            return None

        run_dirs: list[tuple[int, Path]] = []
        for candidate in runs_dir.iterdir():
            if not candidate.is_dir():
                continue
            try:
                modified_at = candidate.stat().st_mtime_ns
            except OSError:
                continue
            run_dirs.append((modified_at, candidate))

        for _, run_dir in sorted(run_dirs, reverse=True):
            history_path = run_dir / "transition_history.jsonl"
            if not history_path.exists():
                continue
            try:
                records = read_transition_history(history_path)
            except (OSError, ValueError):
                continue
            for record in reversed(records):
                if record.policy_evaluator != ExecutionPreflightEvaluator.evaluator_name:
                    continue
                if task_id not in {record.active_task_before, record.active_task_after}:
                    continue
                evaluation = record.policy_evaluation_record()
                context = execution_preflight_context(evaluation)
                if context is not None and context.block_status is not None:
                    return context
        return None

    def _maybe_adaptive_upscope_small_task(
        self,
        *,
        active_task: TaskCard,
        current_status: ExecutionStatus,
        size_view: SizeClassificationView,
    ) -> tuple[TaskCard, SizeClassificationView]:
        if current_status is not ExecutionStatus.BLOCKED:
            return active_task, size_view
        if size_view.latched_as is not SizeClass.SMALL:
            return active_task, size_view
        preflight_context = self._latest_preflight_block_context(task_id=active_task.task_id)
        if preflight_context is not None and preflight_context.block_status is current_status:
            return active_task, size_view
        adaptive = size_view.task.adaptive_upscope
        if adaptive is not None and adaptive.target is SizeClass.LARGE:
            return active_task, size_view

        reason = (
            "Task resumed from BLOCKED while the visible size latch was still SMALL; "
            "apply the one-step adaptive LARGE promotion rule."
        )
        updated_task = self.queue.record_adaptive_upscope(
            active_task,
            target=SizeClass.LARGE,
            rule=ADAPTIVE_UPSCOPE_RULE,
            stage="Resume",
            reason=reason,
        )
        return updated_task, self._refresh_size_status(updated_task)

    def _stage(self, stage_type: StageType) -> ExecutionStage:
        return self.stages[stage_type]

    def _kind_id_for_stage(self, stage_type: StageType) -> str:
        return f"execution.{stage_type.value.replace('_', '-')}"

    def _stage_plan(self, node_id: str) -> FrozenStagePlan:
        return stage_plan_helper(self, node_id)

    def _record_stage_transition(
        self,
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
        record_stage_transition_helper(
            self,
            result,
            task_before=task_before,
            task_after=task_after,
            routing_mode=routing_mode,
            selected_edge_id=selected_edge_id,
            selected_edge_reason=selected_edge_reason,
            selected_terminal_state_id=selected_terminal_state_id,
            condition_inputs=condition_inputs,
            condition_result=condition_result,
            queue_mutations_applied=queue_mutations_applied,
            attributes=attributes,
        )

    def _run_stage(
        self,
        stage_type: StageType,
        task: TaskCard | None,
        run_id: str,
        *,
        node_id: str | None = None,
    ) -> StageResult:
        return run_stage_helper(self, stage_type, task, run_id, node_id=node_id)

    def _record_policy_evaluations(self, records: tuple[PolicyEvaluationRecord, ...]) -> None:
        if not records:
            return
        self.policy_evaluations.extend(records)
        if self.transition_history is None:
            return
        for record in records:
            stage = record.facts.stage
            kind_id = stage.kind_id if stage is not None else "execution.policy_hook"
            node_id = stage.node_id if stage is not None else POLICY_CYCLE_NODE_ID
            bound_parameters = BoundExecutionParameters()
            if stage is not None:
                bound_parameters = BoundExecutionParameters(
                    model_profile_ref=stage.model_profile_ref,
                    runner=stage.runner,
                    model=stage.model,
                    effort=stage.effort,
                    allow_search=stage.allow_search,
                    timeout_seconds=stage.timeout_seconds,
                )
            self.transition_history.append(
                event_name=f"policy.hook.{record.hook.value}",
                source=record.evaluator,
                plane=record.facts.plane,
                node_id=node_id,
                kind_id=kind_id,
                outcome=record.decision.value,
                status_before=record.facts.runtime.execution_status,
                status_after=record.facts.stage_result_status,
                active_task_before=record.facts.task.task_id if record.facts.task is not None else None,
                active_task_after=record.facts.task.task_id if record.facts.task is not None else None,
                bound_execution_parameters=bound_parameters,
                policy_evaluation=record,
                attributes={"policy_hook": record.hook.value},
            )

    def _evaluate_cycle_boundary_policy(
        self,
        *,
        run_id: str,
        current_status: ExecutionStatus,
        active_task: TaskCard | None,
    ) -> tuple[PolicyEvaluationRecord, ...]:
        if self._active_frozen_plan is None or self.transition_history is None:
            return ()
        records = self.policy_runtime.evaluate_cycle_boundary(
            run_id=run_id,
            routing_mode=self._policy_routing_mode,
            execution_status=current_status,
            active_task=active_task,
            backlog_depth=self.queue.backlog_depth(),
            transition_history_count=self.transition_history.record_count,
            frozen_plan=self._active_frozen_plan,
            snapshot_id=self.runtime_provenance.snapshot_id,
        )
        self._record_policy_evaluations(records)
        return records

    def _create_blocker_bundle(
        self,
        run_id: str,
        stage_label: str,
        why: str,
        failing_result: StageResult | None,
    ) -> Path:
        return create_blocker_bundle_helper(self, run_id, stage_label, why, failing_result)

    def _quarantine_task(
        self,
        task: TaskCard,
        *,
        run_id: str,
        stage_label: str,
        why: str,
        diagnostics_dir: Path,
        consult_result: StageResult | None,
    ) -> TaskCard:
        return quarantine_task_helper(
            self,
            task,
            run_id=run_id,
            stage_label=stage_label,
            why=why,
            diagnostics_dir=diagnostics_dir,
            consult_result=consult_result,
        )

    def _build_research_handoff(
        self,
        *,
        run_id: str,
        task: TaskCard,
        stage_label: str,
        reason: str,
        diagnostics_dir: Path | None,
        run_dir: Path | None,
        latch,
    ) -> ExecutionResearchHandoff:
        frozen_plan = self.runtime_provenance.frozen_plan
        parent_run = CrossPlaneParentRun(
            plane="execution",
            run_id=run_id,
            snapshot_id=self.runtime_provenance.snapshot_id,
            frozen_plan_id=None if frozen_plan is None else frozen_plan.plan_id,
            frozen_plan_hash=None if frozen_plan is None else frozen_plan.content_hash,
            transition_history_path=(
                None if self.transition_history is None else self.transition_history.history_path
            ),
        )
        return ExecutionResearchHandoff(
            handoff_id=f"{run_id}:needs_research:{latch.batch_id}",
            parent_run=parent_run,
            task_id=task.task_id,
            task_title=task.title,
            status=ExecutionStatus.NEEDS_RESEARCH,
            stage=stage_label,
            reason=reason,
            incident_path=latch.incident_path,
            diagnostics_dir=diagnostics_dir,
            run_dir=run_dir,
            recovery_batch_id=latch.batch_id,
            failure_signature=latch.failure_signature,
            frozen_backlog_cards=latch.frozen_backlog_cards,
            retained_backlog_cards=latch.retained_backlog_cards,
        )

    def _write_blocker_entry(
        self,
        task: TaskCard | None,
        *,
        stage_label: str,
        reason: str,
        diagnostics_dir: Path,
        status: ExecutionStatus = ExecutionStatus.BLOCKED,
        run_dir: Path | None = None,
        prompt_artifact: Path | None = None,
        incident_path: Path | None = None,
        notes: str | None = None,
    ) -> None:
        write_blocker_entry_helper(
            self,
            task,
            stage_label=stage_label,
            reason=reason,
            diagnostics_dir=diagnostics_dir,
            status=status,
            run_dir=run_dir,
            prompt_artifact=prompt_artifact,
            incident_path=incident_path,
            notes=notes,
        )

    def route_net_wait_to_blocker(
        self,
        task: TaskCard | None,
        *,
        run_id: str,
        stage_label: str,
        reason: str,
        failing_result: StageResult | None,
        diagnostics_dir: Path | None = None,
    ) -> Path:
        """Persist blocker evidence for a NET_WAIT escalation without freezing the task."""

        diagnostics = diagnostics_dir or self._create_blocker_bundle(run_id, stage_label, reason, failing_result)
        self._write_blocker_entry(
            task,
            stage_label=stage_label,
            reason=reason,
            diagnostics_dir=diagnostics,
            run_dir=(failing_result.runner_result.run_dir if failing_result and failing_result.runner_result else None),
            prompt_artifact=(
                failing_result.runner_result.last_response_path
                if failing_result is not None and failing_result.runner_result is not None
                else None
            ),
            notes="NET_WAIT recovery exhausted; task remains active for operator inspection.",
        )
        self.status_store.transition(ExecutionStatus.BLOCKED)
        return diagnostics

    def route_net_wait_to_incident(
        self,
        task: TaskCard,
        *,
        run_id: str,
        stage_label: str,
        reason: str,
        failing_result: StageResult | None,
        diagnostics_dir: Path | None = None,
    ) -> tuple[TaskCard, Path, ExecutionResearchHandoff | None]:
        """Quarantine a NET_WAIT task into the existing incident/research handoff flow."""

        diagnostics = diagnostics_dir or self._create_blocker_bundle(run_id, stage_label, reason, failing_result)
        quarantined = self._quarantine_task(
            task,
            run_id=run_id,
            stage_label=stage_label,
            why=reason,
            diagnostics_dir=diagnostics,
            consult_result=None,
        )
        return quarantined, diagnostics, self._last_research_handoff

    def _recover_or_quarantine(
        self,
        task: TaskCard,
        *,
        run_id: str,
        stage_label: str,
        why: str,
        stage_results: list[StageResult],
        failing_result: StageResult | None,
    ) -> _RecoveryResult:
        return recover_or_quarantine_helper(
            self,
            task,
            run_id=run_id,
            stage_label=stage_label,
            why=why,
            stage_results=stage_results,
            failing_result=failing_result,
            routing_mode_fixed_v1_fallback=ROUTING_MODE_FIXED_V1_FALLBACK,
        )

    def _complete_success_path(self, task: TaskCard, run_id: str, stage_results: list[StageResult]) -> TaskCard:
        for stage_type in self.config.routing.qa_success_sequence:
            if stage_type is not StageType.UPDATE:
                raise StageExecutionError(
                    f"unsupported qa_success_sequence stage: {stage_type.value}"
                )
            update_result = self._run_stage(stage_type, task, run_id)
            stage_results.append(update_result)
            update_status = ExecutionStatus(update_result.status)
            self._record_stage_transition(
                update_result,
                task_before=task,
                task_after=(None if update_status is ExecutionStatus.UPDATE_COMPLETE else task),
                routing_mode=ROUTING_MODE_FIXED_V1_FALLBACK,
                selected_edge_id=(
                    "execution.update.success.archive"
                    if update_status is ExecutionStatus.UPDATE_COMPLETE
                    else "execution.update.failure.blocked"
                ),
                selected_terminal_state_id=(
                    "idle" if update_status is ExecutionStatus.UPDATE_COMPLETE else None
                ),
                selected_edge_reason=(
                    "update completed and the task was archived"
                    if update_status is ExecutionStatus.UPDATE_COMPLETE
                    else f"update ended with {update_status.value}"
                ),
                condition_inputs={"status": update_status.value},
                condition_result=update_status is ExecutionStatus.UPDATE_COMPLETE,
                queue_mutations_applied=(
                    ("archive_task",) if update_status is ExecutionStatus.UPDATE_COMPLETE else ()
                ),
            )
            if update_status is not ExecutionStatus.UPDATE_COMPLETE:
                raise StageExecutionError(f"update stage ended with {update_status.value}")
        self.queue.archive(task)
        self.status_store.transition(ExecutionStatus.IDLE)
        self._clear_active_quickfix_artifact()
        self._apply_inter_task_delay(stage_results)
        return task

    def _apply_inter_task_delay(self, stage_results: list[StageResult]) -> int:
        if not stage_results:
            return 0
        existing = int(stage_results[-1].metadata.get("pacing_delay_seconds_applied") or 0)
        if existing > 0:
            return existing
        applied_delay_seconds = 0
        pacing_context = execution_pacing_context_from_records(self.policy_evaluations)
        if pacing_context is not None:
            applied_delay_seconds = pacing_context.delay_seconds
        stage_results[-1] = stage_results[-1].model_copy(
            update={
                "metadata": {
                    **dict(stage_results[-1].metadata),
                    "pacing_delay_seconds_applied": applied_delay_seconds,
                }
            }
        )
        return applied_delay_seconds

    def _run_empty_backlog_sequence(self, run_id: str, stage_results: list[StageResult]) -> ExecutionStatus:
        for stage_type in self.config.routing.backlog_empty_sequence:
            if stage_type is not StageType.UPDATE:
                raise StageExecutionError(
                    f"unsupported backlog_empty_sequence stage: {stage_type.value}"
                )
            update_result = self._run_stage(stage_type, None, run_id)
            stage_results.append(update_result)
            update_status = ExecutionStatus(update_result.status)
            self._record_stage_transition(
                update_result,
                task_before=None,
                task_after=None,
                routing_mode=ROUTING_MODE_FIXED_V1_BACKLOG_EMPTY,
                selected_edge_id=(
                    "execution.update.success.idle_on_empty"
                    if update_status is ExecutionStatus.UPDATE_COMPLETE
                    else "execution.update.failure.blocked_on_empty"
                ),
                selected_terminal_state_id=(
                    "idle" if update_status is ExecutionStatus.UPDATE_COMPLETE else None
                ),
                selected_edge_reason=(
                    "backlog-empty maintenance completed"
                    if update_status is ExecutionStatus.UPDATE_COMPLETE
                    else f"backlog-empty update ended with {update_status.value}"
                ),
                condition_inputs={"status": update_status.value, "backlog_empty": True},
                condition_result=update_status is ExecutionStatus.UPDATE_COMPLETE,
            )
            if update_status is not ExecutionStatus.UPDATE_COMPLETE:
                return update_status
        self.status_store.transition(ExecutionStatus.IDLE)
        return ExecutionStatus.IDLE

    def _resume_after_recovery(
        self,
        task: TaskCard,
        *,
        run_id: str,
        stage_results: list[StageResult],
        recovery_rounds: int,
        diagnostics_dir: Path | None = None,
    ) -> tuple[ExecutionStatus, TaskCard | None, TaskCard | None, Path | None, int]:
        return resume_after_recovery_helper(
            self,
            task,
            run_id=run_id,
            stage_results=stage_results,
            recovery_rounds=recovery_rounds,
            max_local_recovery_rounds=MAX_LOCAL_RECOVERY_ROUNDS,
            diagnostics_dir=diagnostics_dir,
        )

    def _run_quickfix_loop(
        self,
        task: TaskCard,
        run_id: str,
        stage_results: list[StageResult],
        *,
        recovery_rounds: int,
    ) -> tuple[ExecutionStatus, TaskCard | None, TaskCard | None, Path | None, int]:
        return run_quickfix_loop_flow(
            self,
            task,
            run_id,
            stage_results=stage_results,
            recovery_rounds=recovery_rounds,
            routing_mode=ROUTING_MODE_FIXED_V1_FALLBACK,
        )

    def _handle_qa_outcome(
        self,
        task: TaskCard,
        *,
        run_id: str,
        stage_results: list[StageResult],
        qa_result: StageResult,
        stage_label: str,
        recovery_rounds: int,
    ) -> tuple[ExecutionStatus, TaskCard | None, TaskCard | None, Path | None, int]:
        return handle_qa_outcome_flow(
            self,
            task,
            run_id=run_id,
            stage_results=stage_results,
            qa_result=qa_result,
            stage_label=stage_label,
            recovery_rounds=recovery_rounds,
            routing_mode=ROUTING_MODE_FIXED_V1_FALLBACK,
        )

    def _run_builder_success_sequence(
        self,
        task: TaskCard,
        run_id: str,
        stage_results: list[StageResult],
        *,
        recovery_rounds: int,
    ) -> tuple[ExecutionStatus, TaskCard | None, TaskCard | None, Path | None, int]:
        return run_builder_success_sequence_flow(
            self,
            task,
            run_id,
            stage_results,
            recovery_rounds=recovery_rounds,
            routing_mode=ROUTING_MODE_FIXED_V1_FALLBACK,
        )

    def _run_full_task_path(
        self,
        task: TaskCard,
        run_id: str,
        stage_results: list[StageResult],
        *,
        recovery_rounds: int = 0,
    ) -> tuple[ExecutionStatus, TaskCard | None, TaskCard | None, Path | None, int]:
        return run_full_task_path_flow(
            self,
            task,
            run_id,
            stage_results,
            recovery_rounds=recovery_rounds,
            routing_mode=ROUTING_MODE_FIXED_V1_FALLBACK,
        )

    def run_once(self) -> ExecutionCycleResult:
        return run_execution_cycle_flow(self)
