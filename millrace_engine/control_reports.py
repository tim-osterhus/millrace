"""Control-plane report builders and runtime-state helpers."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Literal

from pydantic import ValidationError

from .assets.resolver import AssetFamilyEntry, AssetResolutionError, AssetResolver, ResolvedAsset
from .baseline_assets import packaged_baseline_asset, packaged_baseline_bundle_version
from .compiler import CompileTimeResolvedSnapshot
from .config import EngineConfig, LoadedConfig, build_runtime_paths
from .contract_compounding import (
    CompoundingFlushCheckpoint,
    ProcedureInjectionBundle,
    ReusableProcedureArtifact,
)
from .contract_context_facts import ContextFactInjectionBundle
from .contracts import (
    AuditGateDecision,
    CompletionDecision,
    ExecutionStatus,
    ObjectiveContract,
    ResearchMode,
    TaskCard,
    load_objective_contract,
)
from .control_common import (
    ControlError,
    expected_error_message,
    normalize_datetime,
    single_line_message,
    validation_error_message,
)
from .control_models import (
    ActiveTaskRemediationResult,
    AssetFamilyEntryView,
    AssetInventoryView,
    AssetResolutionView,
    CompletionStateView,
    DeferredActiveTaskClear,
    PolicyHookSummary,
    QueueItemView,
    ResearchQueueFamilyView,
    RunCompoundingFlushView,
    RunCompoundingReport,
    RunContextFactSelectionView,
    RunCreatedProcedureView,
    RunProcedureSelectionView,
    RunProvenanceReport,
    RuntimeState,
    SelectionExplanationView,
)
from .diagnostics import build_policy_evidence_snapshot
from .events import EventRecord
from .execution_nodes import status_requires_large_route
from .markdown import write_text_atomic
from .paths import RuntimePaths
from .policies import (
    SizeClassificationView,
    execution_integration_context_from_records,
    refresh_size_status,
)
from .provenance import (
    COMPOUNDING_FLUSH_ATTRIBUTE,
    CONTEXT_FACT_INJECTION_ATTRIBUTE,
    PROCEDURE_INJECTION_ATTRIBUTE,
    RuntimeTransitionRecord,
    latest_policy_transition_record,
    policy_evaluation_records_from_transitions,
    read_transition_history,
)
from .queue import TaskQueue
from .research.audit import load_audit_remediation_record, load_audit_summary
from .research.governance import build_research_governance_report
from .research.queues import discover_research_queues
from .research.state import (
    ResearchQueueFamily,
    ResearchQueueOwnership,
    ResearchRuntimeMode,
    ResearchRuntimeState,
    load_research_runtime_state,
)
from .standard_runtime import (
    RuntimeSelectionView,
    preview_execution_runtime_selection,
    runtime_selection_view_from_snapshot,
)
from .status import ControlPlane, StatusError, StatusStore


def _normalize_path_token(value: str | Path) -> Path:
    if isinstance(value, Path):
        return value
    text = value.strip()
    if not text:
        raise ValueError("path token may not be empty")
    return Path(text)


def _resolve_report_path(path_token: str | Path, *, relative_to: Path) -> Path:
    candidate = _normalize_path_token(path_token)
    if candidate.is_absolute():
        return candidate
    return relative_to / candidate


def decision_report_paths(paths: RuntimePaths) -> tuple[Path, Path]:
    gate_decision_path = paths.reports_dir / "audit_gate_decision.json"
    completion_decision_path = paths.reports_dir / "completion_decision.json"
    contract_path = paths.objective_contract_file
    if contract_path.exists():
        raw_text = contract_path.read_text(encoding="utf-8")
    else:
        raw_text = packaged_baseline_asset("agents/objective/contract.yaml").read_text(encoding="utf-8")
    try:
        contract = load_objective_contract(raw_text)
    except (ValidationError, ValueError):
        return gate_decision_path, completion_decision_path
    return (
        _resolve_report_path(contract.completion.fallback_decision_file, relative_to=paths.root),
        _resolve_report_path(contract.completion.authoritative_decision_file, relative_to=paths.root),
    )


def completion_state_view(
    paths: RuntimePaths,
    *,
    latest_completion_decision: CompletionDecision | None,
) -> CompletionStateView:
    marker_path = paths.agents_dir / "AUTONOMY_COMPLETE"
    marker_present = marker_path.exists() and marker_path.is_file()
    completion_allowed = (
        latest_completion_decision is not None and latest_completion_decision.decision == "PASS"
    )
    marker_honored = marker_present and completion_allowed
    if marker_honored:
        reason: Literal["allowed", "marker_missing", "audit_pass_missing", "audit_not_passed"] = "allowed"
    elif not marker_present:
        reason = "marker_missing"
    elif latest_completion_decision is None:
        reason = "audit_pass_missing"
    else:
        reason = "audit_not_passed"
    return CompletionStateView(
        marker_path=marker_path,
        marker_present=marker_present,
        completion_allowed=completion_allowed,
        marker_honored=marker_honored,
        latest_decision=(
            None if latest_completion_decision is None else latest_completion_decision.decision
        ),
        reason=reason,
    )


def _normalized_count_map(values: dict[str, int]) -> dict[str, int]:
    return {key: values[key] for key in sorted(values)}


def _policy_hook_summary(records: tuple[RuntimeTransitionRecord, ...]) -> PolicyHookSummary | None:
    hook_counts: dict[str, int] = {}
    evaluator_counts: dict[str, int] = {}
    decision_counts: dict[str, int] = {}
    record_count = 0
    latest_record = None
    for record in records:
        if not record.has_policy_evaluation:
            continue
        record_count += 1
        latest_record = record.policy_evaluation_record()
        if record.policy_hook is not None:
            hook_counts[record.policy_hook] = hook_counts.get(record.policy_hook, 0) + 1
        if record.policy_evaluator is not None:
            evaluator_counts[record.policy_evaluator] = evaluator_counts.get(record.policy_evaluator, 0) + 1
        if record.policy_decision is not None:
            decision_counts[record.policy_decision] = decision_counts.get(record.policy_decision, 0) + 1
    if record_count == 0:
        return None
    return PolicyHookSummary(
        record_count=record_count,
        hook_counts=_normalized_count_map(hook_counts),
        evaluator_counts=_normalized_count_map(evaluator_counts),
        decision_counts=_normalized_count_map(decision_counts),
        latest_hook=(latest_record.hook.value if latest_record is not None else None),
        latest_evaluator=(latest_record.evaluator if latest_record is not None else None),
        latest_decision=(latest_record.decision.value if latest_record is not None else None),
        latest_notes=(latest_record.notes if latest_record is not None else ()),
        latest_evidence_summaries=(
            tuple(item.summary for item in latest_record.evidence)
            if latest_record is not None
            else ()
        ),
    )


def config_hash(config: EngineConfig) -> str:
    """Return a stable hash of the active config."""

    payload = json.dumps(config.model_dump(mode="json"), sort_keys=True).encode("utf-8")
    return sha256(payload).hexdigest()


def _read_pending_active_task_clear(runtime_dir: Path) -> DeferredActiveTaskClear | None:
    path = runtime_dir / "pending_active_task_clear.json"
    if not path.exists():
        return None
    return DeferredActiveTaskClear.model_validate_json(path.read_text(encoding="utf-8"))


def _read_last_active_task_clear(runtime_dir: Path) -> ActiveTaskRemediationResult | None:
    path = runtime_dir / "last_active_task_clear.json"
    if not path.exists():
        return None
    return ActiveTaskRemediationResult.model_validate_json(path.read_text(encoding="utf-8"))


def read_runtime_state(state_path: Path) -> RuntimeState | None:
    """Read a persisted runtime snapshot if present."""

    if not state_path.exists():
        return None
    state = RuntimeState.model_validate_json(state_path.read_text(encoding="utf-8"))
    runtime_root = state_path.parent
    pending = _read_pending_active_task_clear(runtime_root)
    last = _read_last_active_task_clear(runtime_root)
    if pending is None and last is None:
        return state
    return state.model_copy(
        update={
            "pending_active_task_clear": pending,
            "last_active_task_clear": last,
        }
    )


def read_control_runtime_state(state_path: Path) -> RuntimeState | None:
    try:
        return read_runtime_state(state_path)
    except ValidationError as exc:
        raise ControlError(f"runtime state is invalid: {validation_error_message(exc)}") from exc


def write_runtime_state(state_path: Path, state: RuntimeState) -> None:
    """Persist one runtime snapshot."""

    write_text_atomic(state_path, state.model_dump_json(indent=2) + "\n")


def read_resolved_snapshot(snapshot_path: Path) -> CompileTimeResolvedSnapshot | None:
    """Read one durable compile-time resolved snapshot if present."""

    if not snapshot_path.exists():
        return None
    return CompileTimeResolvedSnapshot.model_validate_json(snapshot_path.read_text(encoding="utf-8"))


def read_runtime_transition_history(history_path: Path) -> tuple[RuntimeTransitionRecord, ...]:
    """Read one durable runtime transition-history file if present."""

    return read_transition_history(history_path)


def read_run_provenance(run_dir: Path) -> RunProvenanceReport | None:
    """Read the compile-time snapshot and runtime history for one run directory."""

    if not run_dir.exists():
        return None
    snapshot_path = run_dir / "resolved_snapshot.json"
    history_path = run_dir / "transition_history.jsonl"
    compile_snapshot = read_resolved_snapshot(snapshot_path)
    runtime_history = read_runtime_transition_history(history_path)
    if compile_snapshot is None and not runtime_history:
        return None
    run_id = (
        compile_snapshot.run_id
        if compile_snapshot is not None
        else runtime_history[0].run_id
    )
    policy_records = tuple(policy_evaluation_records_from_transitions(runtime_history))
    latest_policy_record = latest_policy_transition_record(runtime_history)
    return RunProvenanceReport(
        run_id=run_id,
        policy_hooks=_policy_hook_summary(runtime_history),
        latest_policy_evidence=build_policy_evidence_snapshot(latest_policy_record),
        integration_policy=execution_integration_context_from_records(policy_records),
        compounding=_run_compounding_report(run_dir=run_dir, runtime_history=runtime_history),
        compile_snapshot=compile_snapshot,
        runtime_history=runtime_history,
        snapshot_path=snapshot_path if snapshot_path.exists() else None,
        transition_history_path=history_path if history_path.exists() else None,
    )


def _run_compounding_report(
    *,
    run_dir: Path,
    runtime_history: tuple[RuntimeTransitionRecord, ...],
) -> RunCompoundingReport | None:
    created = _created_procedure_views(run_dir)
    selections = _procedure_selection_views(runtime_history)
    if not created and not selections:
        flushes = _compounding_flush_views(runtime_history)
        if not flushes:
            return None
    else:
        flushes = _compounding_flush_views(runtime_history)
    return RunCompoundingReport(
        created_procedures=created,
        procedure_selections=selections,
        context_fact_selections=_context_fact_selection_views(runtime_history),
        flush_checkpoints=flushes,
    )


def _created_procedure_views(run_dir: Path) -> tuple[RunCreatedProcedureView, ...]:
    candidate_dir = run_dir.parent.parent / "compounding" / "procedures" / run_dir.name
    if not candidate_dir.exists():
        return ()
    created: list[RunCreatedProcedureView] = []
    for path in sorted(candidate_dir.glob("*.json")):
        try:
            artifact = ReusableProcedureArtifact.model_validate_json(path.read_text(encoding="utf-8"))
        except ValidationError:
            continue
        created.append(
            RunCreatedProcedureView(
                procedure_id=artifact.procedure_id,
                scope=artifact.scope,
                source_stage=artifact.source_stage.value,
                title=artifact.title,
                summary=artifact.summary,
                created_at=artifact.created_at,
                artifact_path=path,
                evidence_refs=artifact.evidence_refs,
            )
        )
    return tuple(created)


def _procedure_selection_views(
    runtime_history: tuple[RuntimeTransitionRecord, ...],
) -> tuple[RunProcedureSelectionView, ...]:
    selections: list[RunProcedureSelectionView] = []
    for record in runtime_history:
        raw_bundle = record.attributes.get(PROCEDURE_INJECTION_ATTRIBUTE)
        if not isinstance(raw_bundle, dict):
            continue
        try:
            bundle = ProcedureInjectionBundle.model_validate(raw_bundle)
        except ValidationError:
            continue
        selections.append(
            RunProcedureSelectionView.from_bundle(
                event_id=record.event_id,
                node_id=record.node_id,
                stage=bundle.stage.value,
                bundle=bundle,
            )
        )
    return tuple(selections)


def _context_fact_selection_views(
    runtime_history: tuple[RuntimeTransitionRecord, ...],
) -> tuple[RunContextFactSelectionView, ...]:
    selections: list[RunContextFactSelectionView] = []
    for record in runtime_history:
        raw_bundle = record.attributes.get(CONTEXT_FACT_INJECTION_ATTRIBUTE)
        if not isinstance(raw_bundle, dict):
            continue
        try:
            bundle = ContextFactInjectionBundle.model_validate(raw_bundle)
        except ValidationError:
            continue
        selections.append(
            RunContextFactSelectionView.from_bundle(
                event_id=record.event_id,
                node_id=record.node_id,
                stage=bundle.stage.value,
                bundle=bundle,
            )
        )
    return tuple(selections)


def _compounding_flush_views(
    runtime_history: tuple[RuntimeTransitionRecord, ...],
) -> tuple[RunCompoundingFlushView, ...]:
    flushes: list[RunCompoundingFlushView] = []
    for record in runtime_history:
        raw_checkpoint = record.attributes.get(COMPOUNDING_FLUSH_ATTRIBUTE)
        if not isinstance(raw_checkpoint, dict):
            continue
        try:
            checkpoint = CompoundingFlushCheckpoint.model_validate(raw_checkpoint)
        except ValidationError:
            continue
        flushes.append(
            RunCompoundingFlushView.from_checkpoint(
                event_id=record.event_id,
                node_id=record.node_id,
                checkpoint=checkpoint,
            )
        )
    return tuple(flushes)


def read_control_research_state(paths: RuntimePaths) -> ResearchRuntimeState | None:
    try:
        return load_research_runtime_state(paths.research_state_file, deferred_dir=paths.deferred_dir)
    except ValidationError as exc:
        raise ControlError(f"research state is invalid: {validation_error_message(exc)}") from exc
    except ValueError as exc:
        raise ControlError(f"research state is invalid: {single_line_message(exc)}") from exc


def read_event_log(path: Path) -> list[EventRecord]:
    if not path.exists():
        return []
    records: list[EventRecord] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        records.append(EventRecord.model_validate_json(line))
    return records


def task_view(card: TaskCard | None) -> QueueItemView | None:
    if card is None:
        return None
    return QueueItemView(task_id=card.task_id, title=card.title, spec_id=card.spec_id)


def live_research_runtime_state(
    loaded: LoadedConfig,
    *,
    observed_at: datetime,
) -> ResearchRuntimeState:
    configured_mode = (
        ResearchRuntimeMode.STUB
        if loaded.config.research.mode is ResearchMode.STUB
        else ResearchRuntimeMode.from_value(loaded.config.research.mode)
    )
    return ResearchRuntimeState(
        updated_at=observed_at,
        current_mode=configured_mode,
        last_mode=configured_mode,
        mode_reason="control-live-view",
    )


def count_deferred(paths: RuntimePaths) -> int:
    if not paths.deferred_dir.exists():
        return 0
    return len([path for path in paths.deferred_dir.iterdir() if path.is_file()])


def research_queue_family_view(
    scan,
    *,
    ownerships: tuple[ResearchQueueOwnership, ...],
) -> ResearchQueueFamilyView:
    view = ResearchQueueFamilyView(
        family=scan.family,
        ready=scan.ready,
        item_count=len(scan.items),
        queue_owner=(None if scan.boundary is None else scan.boundary.queue_owner),
        queue_paths=scan.queue_paths,
        contract_paths=scan.contract_paths,
        first_item=scan.first_item,
        ownerships=ownerships,
    )
    return ResearchQueueFamilyView.model_validate(view.model_dump(mode="json"))


def _asset_resolution_view(resolved: ResolvedAsset) -> AssetResolutionView:
    return AssetResolutionView.model_validate(resolved.to_payload())


def _asset_family_entry_view(entry: AssetFamilyEntry) -> AssetFamilyEntryView:
    return AssetFamilyEntryView.model_validate(entry.to_payload())


def asset_inventory_for(loaded: LoadedConfig) -> AssetInventoryView:
    resolver = AssetResolver(loaded.config.paths.workspace)
    stage_prompts: dict[str, AssetResolutionView] = {}
    try:
        for stage in sorted(loaded.config.stages, key=lambda item: item.value):
            prompt_path = loaded.config.stages[stage].prompt_file
            if prompt_path is None:
                continue
            stage_prompts[stage.value] = _asset_resolution_view(resolver.resolve_file(prompt_path))
        return AssetInventoryView(
            bundle_version=resolver.bundle_version,
            stage_prompts=stage_prompts,
            roles=tuple(_asset_family_entry_view(item) for item in resolver.iter_open_family("roles")),
            skills=tuple(_asset_family_entry_view(item) for item in resolver.iter_open_family("skills")),
        )
    except AssetResolutionError as exc:
        raise ControlError(str(exc)) from exc
    except ValidationError as exc:
        raise ControlError(f"asset inventory is invalid: {validation_error_message(exc)}") from exc


def selection_preview_for(
    loaded: LoadedConfig,
    *,
    size: SizeClassificationView,
    current_status: ExecutionStatus,
) -> RuntimeSelectionView:
    try:
        selection = preview_execution_runtime_selection(
            loaded.config,
            build_runtime_paths(loaded.config),
            preview_run_id="status-preview",
            size_latch=size.latched_as.value,
            current_status=current_status,
            task_complexity=size.task.complexity,
            resolve_assets=True,
        )
    except RuntimeError as exc:
        raise ControlError(f"standard runtime selection preview failed: {exc}") from exc
    except KeyError as exc:
        token = exc.args[0] if exc.args else "unknown"
        label = getattr(token, "value", token)
        raise ControlError(f"standard runtime selection preview failed: missing stage config for {label}") from exc
    except ValidationError as exc:
        raise ControlError(
            f"standard runtime selection preview failed: {validation_error_message(exc)}"
        ) from exc
    return selection


def build_live_runtime_state(
    loaded: LoadedConfig,
    *,
    process_running: bool,
    process_id: int | None,
    paused: bool,
    pause_reason: str | None,
    pause_run_id: str | None,
    started_at: datetime | None,
    mode: Literal["once", "daemon"],
    pending_loaded: LoadedConfig | None = None,
    previous_loaded: LoadedConfig | None = None,
    pending_boundary=None,
    pending_fields: tuple[str, ...] = (),
    rollback_armed: bool = False,
) -> RuntimeState:
    """Build a fresh runtime snapshot from visible files."""

    paths = build_runtime_paths(loaded.config)
    queue = TaskQueue(paths)
    runtime = RuntimeState.model_validate(
        {
            "process_running": process_running,
            "process_id": process_id,
            "paused": paused,
            "pause_reason": pause_reason,
            "pause_run_id": pause_run_id,
            "execution_status": StatusStore(paths.status_file, ControlPlane.EXECUTION).read(),
            "research_status": StatusStore(paths.research_status_file, ControlPlane.RESEARCH).read(),
            "active_task_id": queue.active_task().task_id if queue.active_task() is not None else None,
            "backlog_depth": queue.backlog_depth(),
            "deferred_queue_size": count_deferred(paths),
            "uptime_seconds": (
                max((datetime.now(timezone.utc) - started_at).total_seconds(), 0.0)
                if started_at is not None
                else None
            ),
            "config_hash": config_hash(loaded.config),
            "asset_bundle_version": packaged_baseline_bundle_version(),
            "pending_config_hash": (
                config_hash(pending_loaded.config)
                if pending_loaded is not None
                else None
            ),
            "previous_config_hash": (
                config_hash(previous_loaded.config)
                if previous_loaded is not None
                else None
            ),
            "pending_config_boundary": pending_boundary,
            "pending_config_fields": pending_fields,
            "rollback_armed": rollback_armed,
            "pending_active_task_clear": _read_pending_active_task_clear(paths.runtime_dir),
            "last_active_task_clear": _read_last_active_task_clear(paths.runtime_dir),
            "started_at": started_at,
            "updated_at": datetime.now(timezone.utc),
            "mode": mode,
        }
    )
    return runtime


def size_status_view(loaded: LoadedConfig, *, task: TaskCard | None) -> SizeClassificationView:
    paths = build_runtime_paths(loaded.config)
    try:
        return refresh_size_status(
            root=paths.root,
            task=task,
            config=loaded.config.sizing,
            latch_path=paths.size_status_file,
        )
    except ValueError as exc:
        raise ControlError(f"size status could not be read: {expected_error_message(exc)}") from exc


def _selection_uses_large_route(selection: RuntimeSelectionView) -> bool:
    mode_ref = selection.mode.ref if selection.mode is not None else selection.selection.ref
    if mode_ref.id.startswith("mode.large"):
        return True
    execution_loop_ref = selection.execution_loop.ref if selection.execution_loop is not None else None
    return execution_loop_ref is not None and execution_loop_ref.id.startswith("execution.large")


def _large_profile_explanation(selection: RuntimeSelectionView) -> tuple[str, str | None]:
    if not _selection_uses_large_route(selection):
        return "not_applicable", None

    mode_label = selection.mode.ref.id if selection.mode is not None else selection.selection.ref.id
    loop_label = selection.execution_loop.ref.id if selection.execution_loop is not None else "unknown"
    if mode_label == "mode.large" and loop_label == "execution.large":
        return (
            "default_large_profile",
            "Using the default LARGE thorough profile: mode.large with execution.large.",
        )
    if mode_label == "mode.large":
        return (
            "alternate_large_profile",
            f"mode.large currently resolves to {loop_label} instead of the default execution.large loop.",
        )
    return (
        "alternate_large_profile",
        f"Selected alternate LARGE mode {mode_label} with execution loop {loop_label}.",
    )


def selection_explanation(
    *,
    size: SizeClassificationView,
    current_status: ExecutionStatus,
    selection: RuntimeSelectionView,
) -> SelectionExplanationView:
    selected_size = "LARGE" if _selection_uses_large_route(selection) else "SMALL"
    adaptive = size.task.adaptive_upscope
    if adaptive is not None and adaptive.target.value == "LARGE":
        route_decision = "adaptive_upscope_large"
        route_reason = (
            f"Escalated to LARGE via the visible adaptive rule {adaptive.rule} "
            f"at {adaptive.stage}: {adaptive.reason}"
        )
    elif status_requires_large_route(current_status):
        route_decision = "status_forced_large"
        route_reason = (
            f"Stayed on the LARGE route because execution status {current_status.value} "
            "must resume through the LARGE chain."
        )
    elif size.latched_as.value == "SMALL":
        route_decision = "stayed_small"
        route_reason = (
            f"Stayed SMALL because the latch is SMALL and no LARGE trigger sources are active "
            f"(mode={size.mode}, triggered={', '.join(size.triggered_sources) or 'none'})."
        )
    elif size.latch_reason == "retained_large_latch":
        route_decision = "retained_large_latch"
        route_reason = (
            "Stayed LARGE because the durable latch was already LARGE and the task has not been "
            "explicitly reset back to SMALL."
        )
    elif size.latch_reason == "promoted_to_large":
        route_decision = "promoted_to_large"
        route_reason = (
            f"Escalated to LARGE because size policy mode {size.mode} promoted the latch from "
            f"triggered sources: {', '.join(size.triggered_sources) or 'none'}."
        )
    else:
        route_decision = "confirmed_large"
        route_reason = (
            f"Stayed LARGE because the current size evidence still classifies the task as LARGE "
            f"(triggered={', '.join(size.triggered_sources) or 'none'})."
        )

    large_profile_decision, large_profile_reason = _large_profile_explanation(selection)
    return SelectionExplanationView(
        selected_size=selected_size,
        route_decision=route_decision,
        route_reason=route_reason,
        large_profile_decision=large_profile_decision,
        large_profile_reason=large_profile_reason,
    )


def snapshot_selection_explanation(selection: RuntimeSelectionView) -> SelectionExplanationView:
    selected_size = "LARGE" if _selection_uses_large_route(selection) else "SMALL"
    large_profile_decision, large_profile_reason = _large_profile_explanation(selection)
    route_reason = (
        "Frozen run compiled the LARGE route."
        if selected_size == "LARGE"
        else "Frozen run compiled the SMALL route."
    )
    return SelectionExplanationView(
        selected_size=selected_size,
        route_decision="selected_" + selected_size.lower(),
        route_reason=route_reason,
        large_profile_decision=large_profile_decision,
        large_profile_reason=large_profile_reason,
    )
