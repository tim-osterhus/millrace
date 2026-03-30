"""Typer-based runtime control CLI."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any
import json

import typer

from .control import (
    AssetInventoryView,
    ConfigShowReport,
    ControlError,
    EngineControl,
    OperationResult,
    QueueSnapshot,
    ResearchReport,
    RunProvenanceReport,
    SelectionExplanationView,
    StatusReport,
    WorkspaceHealthReport,
)
from .config_compat import LegacyPolicyCompatReport, LegacyPolicyCompatStatus
from .events import EventRecord, render_event_record_line
from .publishing import PublishCommitReport, PublishPreflightReport, StagingSyncReport
from .standard_runtime import RegistryObjectSelectionView, RuntimeSelectionView, StageExecutionBindingView


app = typer.Typer(add_completion=False, help="Control the Millrace runtime.")
config_app = typer.Typer(help="Inspect or mutate runtime config.")
queue_app = typer.Typer(help="Inspect visible execution queues.")
research_app = typer.Typer(help="Inspect research runtime state and history.")
publish_app = typer.Typer(help="Sync and publish the staging surface.")
app.add_typer(config_app, name="config")
app.add_typer(queue_app, name="queue")
app.add_typer(research_app, name="research")
app.add_typer(publish_app, name="publish")


@dataclass(frozen=True, slots=True)
class CLIContext:
    config_path: Path


def _json_output(payload: Any, *, err: bool = False) -> None:
    typer.echo(json.dumps(payload, indent=2, sort_keys=True), err=err)


def _cli_context(ctx: typer.Context) -> CLIContext:
    cli_context = ctx.obj
    if not isinstance(cli_context, CLIContext):
        raise typer.Exit(code=1)
    return cli_context


def _control(ctx: typer.Context) -> EngineControl:
    return EngineControl(_cli_context(ctx).config_path)


def _config_path(ctx: typer.Context) -> Path:
    return _cli_context(ctx).config_path


def _exit_control_error(error: ControlError, *, json_mode: bool) -> None:
    if json_mode:
        _json_output({"error": str(error)}, err=True)
    else:
        typer.echo(str(error), err=True)
    raise typer.Exit(code=1)


def _run_expected(action: Callable[[], Any], *, json_mode: bool) -> Any:
    try:
        return action()
    except ControlError as exc:
        _exit_control_error(exc, json_mode=json_mode)


def _iter_expected(events: Any, *, json_mode: bool) -> Any:
    try:
        yield from events
    except ControlError as exc:
        _exit_control_error(exc, json_mode=json_mode)


def _bool_label(value: bool | None) -> str:
    if value is None:
        return "n/a"
    return "yes" if value else "no"


def _object_selection_line(label: str, selection: RegistryObjectSelectionView | None) -> str | None:
    if selection is None:
        return None
    aliases = ", ".join(selection.aliases) if selection.aliases else "none"
    layer = selection.registry_layer or "unknown"
    source_kind = selection.source_kind.value if selection.source_kind is not None else "unknown"
    source_ref = selection.source_ref or "n/a"
    return (
        f"{label}: {selection.ref.id}@{selection.ref.version} "
        f"[kind={selection.ref.kind.value}, aliases={aliases}, layer={layer}, source={source_kind}, source_ref={source_ref}]"
    )


def _stage_binding_line(binding: StageExecutionBindingView) -> str:
    model_profile = (
        f"{binding.model_profile.ref.id}@{binding.model_profile.ref.version}"
        if binding.model_profile is not None
        else "n/a"
    )
    stage_source = binding.stage_kind.source_kind.value if binding.stage_kind.source_kind is not None else "unknown"
    stage_layer = binding.stage_kind.registry_layer or "unknown"
    prompt_ref = binding.prompt_resolved_ref or binding.prompt_asset_ref or "n/a"
    prompt_source = binding.prompt_source_kind or "n/a"
    return (
        f"- {binding.node_id}: kind={binding.kind_id} stage_kind={binding.stage_kind.ref.id}@{binding.stage_kind.ref.version} "
        f"stage_layer={stage_layer} stage_source={stage_source} model_profile={model_profile} "
        f"runner={binding.runner.value if binding.runner is not None else 'n/a'} model={binding.model or 'n/a'} "
        f"effort={binding.effort.value if binding.effort is not None else 'n/a'} "
        f"allow_search={_bool_label(binding.allow_search)} timeout={binding.timeout_seconds or 'n/a'} "
        f"prompt={prompt_ref} prompt_source={prompt_source}"
    )


def _complexity_selection_line(selection: RuntimeSelectionView) -> str | None:
    if selection.complexity is None:
        return None
    complexity = selection.complexity
    if not complexity.enabled and complexity.task_complexity is None:
        return None
    routed_nodes = ", ".join(complexity.routed_node_ids) if complexity.routed_node_ids else "none"
    selected_profile = (
        f"{complexity.selected_model_profile_ref.id}@{complexity.selected_model_profile_ref.version}"
        if complexity.selected_model_profile_ref is not None
        else "n/a"
    )
    return (
        f"Complexity routing: enabled={_bool_label(complexity.enabled)} "
        f"band={complexity.band.value} reason={complexity.reason.value} "
        f"task={complexity.task_complexity or 'n/a'} "
        f"selected_model_profile={selected_profile} routed_nodes={routed_nodes}"
    )


def _selection_lines(selection: RuntimeSelectionView) -> list[str]:
    plan_label = "Frozen plan" if selection.scope == "frozen_run" else "Preview plan"
    lines = [
        f"Selection scope: {selection.scope}",
        (
            f"Selection ref: {selection.selection.ref.kind.value}:"
            f"{selection.selection.ref.id}@{selection.selection.ref.version}"
        ),
        f"{plan_label} id: {selection.frozen_plan_id}",
        f"{plan_label} hash: {selection.frozen_plan_hash}",
        f"Research participation: {selection.research_participation}",
    ]
    if selection.run_id is not None:
        lines.append(f"Run id: {selection.run_id}")
    complexity_line = _complexity_selection_line(selection)
    if complexity_line is not None:
        lines.append(complexity_line)
    for label, value in (
        ("Selection", selection.selection),
        ("Mode", selection.mode),
        ("Execution loop", selection.execution_loop),
        ("Task authoring profile", selection.task_authoring_profile),
        ("Model profile", selection.model_profile),
    ):
        line = _object_selection_line(label, value)
        if line is not None:
            lines.append(line)
    if selection.policy_toggles is not None:
        lines.append(f"Policy integration mode: {selection.policy_toggles.integration_mode or 'n/a'}")
        lines.append(f"Policy run update on empty: {_bool_label(selection.policy_toggles.run_update_on_empty)}")
        lines.append(f"Policy allow execution search: {_bool_label(selection.policy_toggles.allow_execution_search)}")
        lines.append(f"Policy allow research search: {_bool_label(selection.policy_toggles.allow_research_search)}")
    if selection.stage_bindings:
        lines.append("Bound execution parameters:")
        lines.extend(_stage_binding_line(binding) for binding in selection.stage_bindings)
    return lines


def _selection_explanation_lines(explanation: SelectionExplanationView) -> list[str]:
    lines = [
        f"Selection route: {explanation.selected_size} ({explanation.route_decision})",
        f"Selection reason: {explanation.route_reason}",
    ]
    if explanation.large_profile_decision != "not_applicable":
        lines.append(f"Large profile: {explanation.large_profile_decision}")
        if explanation.large_profile_reason is not None:
            lines.append(f"Large profile reason: {explanation.large_profile_reason}")
    return lines


def _asset_inventory_lines(assets: AssetInventoryView) -> list[str]:
    lines = [
        f"Asset bundle version: {assets.bundle_version}",
        f"Role assets: {len(assets.roles)}",
        f"Skill assets: {len(assets.skills)}",
    ]
    if assets.stage_prompts:
        lines.append("Stage prompt assets:")
        for stage_name, asset in sorted(assets.stage_prompts.items()):
            lines.append(
                f"- {stage_name}: {asset.resolved_ref} "
                f"[source={asset.source_kind}, relative_path={asset.relative_path or 'n/a'}]"
            )
    return lines


def _legacy_policy_summary_line(report: LegacyPolicyCompatReport) -> str:
    counts = report.status_counts(present_only=True)
    return (
        "Legacy policy compatibility: "
        f"mapped={counts['mapped']} "
        f"partially_mapped={counts['partially_mapped']} "
        f"deprecated={counts['deprecated']} "
        f"unsupported={counts['unsupported']}"
    )


def _legacy_policy_entry_line(prefix: str, key: str, suffix: str | None = None) -> str:
    if suffix:
        return f"{prefix}{key}: {suffix}"
    return f"{prefix}{key}"


def _legacy_policy_lines(report: LegacyPolicyCompatReport) -> list[str]:
    lines = [_legacy_policy_summary_line(report)]
    status_order = (
        LegacyPolicyCompatStatus.MAPPED,
        LegacyPolicyCompatStatus.PARTIALLY_MAPPED,
        LegacyPolicyCompatStatus.DEPRECATED,
        LegacyPolicyCompatStatus.UNSUPPORTED,
    )
    for status in status_order:
        entries = report.entries_for_status(status, present_only=True)
        if not entries:
            continue
        lines.append(f"{status.value.replace('_', ' ').title()}:")
        for entry in entries:
            detail_parts: list[str] = []
            if entry.mapped_fields:
                detail_parts.append(f"native={', '.join(entry.mapped_fields)}")
            if entry.replacement_native_fields:
                detail_parts.append(f"replacement={', '.join(entry.replacement_native_fields)}")
            if entry.note:
                detail_parts.append(entry.note)
            lines.append(_legacy_policy_entry_line("- ", entry.key, "; ".join(detail_parts) or None))
    return lines


def _legacy_unmapped_lines(unmapped_keys: tuple[str, ...]) -> list[str]:
    if not unmapped_keys:
        return []
    lines = [f"Legacy unmapped keys: {len(unmapped_keys)}", "Unmapped legacy keys:"]
    lines.extend(f"- {key}" for key in unmapped_keys)
    return lines


def _research_report_lines(report: ResearchReport, *, include_status: bool = True) -> list[str]:
    runtime = report.runtime
    ready_families = [family.family.value for family in report.queue_families if family.ready]
    selected_family = runtime.queue_snapshot.selected_family
    lines = [
        f"Research report source: {report.source_kind}",
        f"Research configured mode: {report.configured_mode.value}",
        f"Research idle mode: {report.configured_idle_mode}",
        f"Research runtime mode: {runtime.current_mode.value}",
        f"Research mode reason: {runtime.mode_reason}",
        f"Research cycles: {runtime.cycle_count}",
        f"Research transitions: {runtime.transition_count}",
        f"Research deferred requests: {len(runtime.deferred_requests)} (breadcrumbs={report.deferred_breadcrumb_count})",
        f"Research ready families: {', '.join(ready_families) if ready_families else 'none'}",
        f"Research selected family: {selected_family.value if selected_family is not None else 'none'}",
    ]
    if include_status:
        lines.insert(3, f"Research status: {report.status.value}")
    checkpoint = runtime.checkpoint
    if checkpoint is None:
        lines.append("Research checkpoint: none")
    else:
        lines.append(
            "Research checkpoint: "
            f"{checkpoint.checkpoint_id} "
            f"(status={checkpoint.status.value}, node={checkpoint.node_id or 'n/a'}, stage={checkpoint.stage_kind_id or 'n/a'})"
        )
        if checkpoint.active_request is not None:
            lines.append(f"Research active request: {checkpoint.active_request.event_type.value}")
        if checkpoint.parent_handoff is None:
            lines.append("Research parent handoff: none")
        else:
            parent_run = checkpoint.parent_handoff.parent_run
            lines.append(
                "Research parent handoff: "
                f"{checkpoint.parent_handoff.handoff_id} "
                f"(parent={parent_run.run_id if parent_run is not None else 'n/a'}, "
                f"task={checkpoint.parent_handoff.task_id}, stage={checkpoint.parent_handoff.stage})"
            )
        lines.append(f"Research checkpoint follow-ons: {len(checkpoint.deferred_follow_ons)}")
    retry_state = runtime.retry_state
    if retry_state is None:
        lines.append("Research retry: none")
    else:
        lines.append(
            "Research retry: "
            f"attempt={retry_state.attempt}/{retry_state.max_attempts} "
            f"backoff={retry_state.backoff_seconds} "
            f"next={retry_state.next_retry_at.isoformat().replace('+00:00', 'Z') if retry_state.next_retry_at is not None else 'none'}"
        )
    lock_state = runtime.lock_state
    if lock_state is None:
        lines.append("Research lock: none")
    else:
        lines.append(
            "Research lock: "
            f"owner={lock_state.owner_id} scope={lock_state.scope.value} "
            f"path={lock_state.lock_path} "
            f"expires={lock_state.expires_at.isoformat().replace('+00:00', 'Z') if lock_state.expires_at is not None else 'none'}"
        )
    if runtime.next_poll_at is not None:
        lines.append(f"Research next poll at: {runtime.next_poll_at.isoformat().replace('+00:00', 'Z')}")
    gate_decision = report.latest_gate_decision
    if gate_decision is None:
        lines.append("Research gate decision: none")
    else:
        lines.append(
            "Research gate decision: "
            f"{gate_decision.decision} "
            f"(completion={gate_decision.counts.completion_pass}/{gate_decision.counts.completion_required}, "
            f"tasks={gate_decision.counts.task_store_cards}, gaps={gate_decision.counts.open_gaps}, "
            f"reasons={len(gate_decision.reasons)})"
        )
    completion_decision = report.latest_completion_decision
    if completion_decision is None:
        lines.append("Research completion decision: none")
    else:
        lines.append(
            "Research completion decision: "
            f"{completion_decision.decision} "
            f"(path={completion_decision.completion_decision_path})"
        )
    completion_state = report.completion_state
    lines.append(
        "Research completion state: "
        f"marker_present={_bool_label(completion_state.marker_present)} "
        f"completion_allowed={_bool_label(completion_state.completion_allowed)} "
        f"marker_honored={_bool_label(completion_state.marker_honored)} "
        f"reason={completion_state.reason} "
        f"(path={completion_state.marker_path.as_posix()})"
    )
    lines.append(
        "Research audit surfaces: "
        f"history={report.audit_history_path.as_posix()} "
        f"summary={report.audit_summary_path.as_posix()}"
    )
    audit_summary = report.audit_summary
    if audit_summary is None:
        lines.append("Research audit summary: none")
    else:
        counts = audit_summary.counts
        last_outcome = audit_summary.last_outcome
        updated_at = (
            audit_summary.updated_at.isoformat().replace("+00:00", "Z")
            if audit_summary.updated_at is not None
            else "none"
        )
        lines.append(
            "Research audit summary: "
            f"total={counts.get('total', 0)} "
            f"pass={counts.get('pass', 0)} "
            f"fail={counts.get('fail', 0)} "
            f"updated_at={updated_at}"
        )
        if last_outcome is None or last_outcome.status == "none":
            lines.append("Research audit outcome: none")
        else:
            recorded_at = (
                last_outcome.at.isoformat().replace("+00:00", "Z")
                if last_outcome.at is not None
                else "none"
            )
            lines.append(
                "Research audit outcome: "
                f"{last_outcome.status} "
                f"audit={last_outcome.audit_id or 'n/a'} "
                f"title={last_outcome.title or 'n/a'} "
                f"scope={last_outcome.scope or 'n/a'} "
                f"trigger={last_outcome.trigger.value if last_outcome.trigger is not None else 'n/a'} "
                f"decision={last_outcome.decision or 'n/a'} "
                f"reasons={last_outcome.reason_count} "
                f"at={recorded_at}"
            )
            lines.append(f"Research audit details: {last_outcome.details}")
    remediation = report.latest_audit_remediation
    if remediation is None:
        lines.append("Research audit remediation: none")
    else:
        lines.append(
            "Research audit remediation: "
            f"{remediation.selected_action} "
            f"spec={remediation.remediation_spec_id} "
            f"task={remediation.remediation_task_id} "
            f"backlog_depth={remediation.backlog_depth_after_enqueue} "
            f"audited={remediation.source_path} "
            f"terminal={remediation.terminal_path}"
        )
    governance = report.governance
    if governance is None:
        lines.append("Research governance: none")
    else:
        queue_governor = governance.queue_governor
        lines.append(
            "Research queue governor: "
            f"{queue_governor.status} "
            f"reason={queue_governor.reason} "
            f"(path={queue_governor.report_path or 'n/a'})"
        )
        pin = queue_governor.initial_family_policy_pin
        if pin is None:
            lines.append("Research initial-family policy pin: none")
        else:
            lines.append(
                "Research initial-family policy pin: "
                f"active={_bool_label(pin.active)} "
                f"action={pin.action} "
                f"reason={pin.reason} "
                f"fields={', '.join(pin.pinned_fields) if pin.pinned_fields else 'none'}"
            )
        canary = governance.governance_canary
        lines.append(
            "Research governance canary: "
            f"{canary.status} "
            f"reason={canary.reason} "
            f"changed_fields={', '.join(canary.changed_fields) if canary.changed_fields else 'none'}"
        )
        drift = governance.drift
        lines.append(
            "Research drift status: "
            f"{drift.status} "
            f"reason={drift.reason} "
            f"fields={', '.join(drift.drift_fields) if drift.drift_fields else 'none'} "
            f"warning={_bool_label(drift.warning_active)} "
            f"hard_latch={_bool_label(drift.hard_latch_active)}"
        )
        watchdog = governance.progress_watchdog
        lines.append(
            "Research progress watchdog: "
            f"{watchdog.status} "
            f"reason={watchdog.reason} "
            f"spec={watchdog.remediation_spec_id or 'none'} "
            f"visible_tasks={watchdog.visible_recovery_task_count} "
            f"escalation={watchdog.escalation_action}"
        )
        regeneration = watchdog.recovery_regeneration
        if regeneration is None:
            lines.append("Research recovery regeneration: none")
        else:
            lines.append(
                "Research recovery regeneration: "
                f"{regeneration.status} "
                f"reason={regeneration.reason} "
                f"spec={regeneration.remediation_spec_id or 'none'} "
                f"visible_before={regeneration.visible_task_count_before} "
                f"visible_after={regeneration.visible_task_count_after} "
                f"regenerated_task={regeneration.regenerated_task_id or 'none'}"
            )
    lines.append("Research queues:")
    for family in report.queue_families:
        first_item = family.first_item
        first_label = "none"
        if first_item is not None:
            first_label = f"{first_item.item_key} :: {first_item.title}"
        lines.append(
            f"- {family.family.value}: ready={_bool_label(family.ready)} "
            f"items={family.item_count} owner={family.queue_owner.value if family.queue_owner is not None else 'n/a'} "
            f"claimed={len(family.ownerships)} first={first_label}"
        )
    return lines


def _render_status(report: StatusReport, *, json_mode: bool) -> None:
    if json_mode:
        _json_output(report.model_dump(mode="json"))
        return
    runtime = report.runtime
    lines = [
        f"Process: {'running' if runtime.process_running else 'stopped'}",
        f"Paused: {'yes' if runtime.paused else 'no'}",
        f"Execution status: {runtime.execution_status.value}",
        f"Research status: {runtime.research_status.value}",
        (
            f"Size policy: {report.size.mode} "
            f"(classified={report.size.classified_as.value}, latched={report.size.latched_as.value}, "
            f"reason={report.size.latch_reason})"
        ),
        (
            f"Size repo evidence: files={report.size.repo.file_count}/{report.size.repo.file_count_threshold} "
            f"nonempty_lines={report.size.repo.nonempty_line_count}/"
            f"{report.size.repo.nonempty_line_count_threshold} "
            f"hits={', '.join(report.size.repo.threshold_hits) or 'none'}"
        ),
        (
            f"Size task evidence: task={report.size.task.active_task_id or 'none'} "
            f"files_to_touch={report.size.task.file_count}/{report.size.task.file_count_threshold} "
            f"nonempty_lines={report.size.task.nonempty_line_count}/"
            f"{report.size.task.nonempty_line_count_threshold} "
            f"complexity={report.size.task.complexity_band} "
            f"signals={report.size.task.qualifying_signal_count}/{report.size.task.minimum_signal_count} "
            f"hits={', '.join(report.size.task.threshold_hits) or 'none'}"
        ),
        (
            f"Size task sources: files={report.size.task.file_count_source} "
            f"loc={report.size.task.nonempty_line_count_source} "
            f"paths={len(report.size.task.files_to_touch)} "
            f"missing_paths={len(report.size.task.missing_files_to_touch)}"
        ),
        f"Active task: {runtime.active_task_id or 'none'}",
        f"Backlog depth: {runtime.backlog_depth}",
        f"Deferred queue size: {runtime.deferred_queue_size}",
        f"Uptime seconds: {runtime.uptime_seconds or 0}",
        f"Config hash: {runtime.config_hash}",
        f"Asset bundle version: {runtime.asset_bundle_version or 'unknown'}",
        f"Config source: {report.config_source_kind}",
        f"Status source: {report.source_kind}",
    ]
    if report.config_source.legacy_policy_compatibility is not None:
        lines.append(_legacy_policy_summary_line(report.config_source.legacy_policy_compatibility))
    if report.config_source.unmapped_keys:
        lines.append(f"Legacy unmapped keys: {len(report.config_source.unmapped_keys)}")
    lines.extend(_selection_explanation_lines(report.selection_explanation))
    lines.extend(_selection_lines(report.selection))
    if report.integration_policy is not None:
        lines.append(
            f"Integration preview: {'run' if report.integration_policy.should_run_integration else 'skip'} "
            f"(target={report.integration_policy.builder_success_target})"
        )
        lines.append(f"Integration reason: {report.integration_policy.reason}")
    if runtime.pending_config_hash is not None:
        lines.append(f"Pending config hash: {runtime.pending_config_hash}")
    if runtime.paused and runtime.pause_reason is not None:
        lines.append(f"Pause reason: {runtime.pause_reason}")
    if runtime.paused and runtime.pause_run_id is not None:
        lines.append(f"Pause run id: {runtime.pause_run_id}")
    if runtime.pending_config_boundary is not None:
        lines.append(f"Pending config boundary: {runtime.pending_config_boundary.value}")
    if runtime.previous_config_hash is not None:
        lines.append(f"Previous config hash: {runtime.previous_config_hash}")
    if runtime.rollback_armed:
        lines.append("Rollback armed: yes")
    if report.active_task is not None:
        lines.append(f"Active task title: {report.active_task.title}")
    if report.next_task is not None:
        lines.append(f"Next task: {report.next_task.task_id} :: {report.next_task.title}")
    if report.assets is not None:
        lines.extend(_asset_inventory_lines(report.assets))
    if report.research is not None:
        lines.extend(_research_report_lines(report.research, include_status=False))
    typer.echo("\n".join(lines))


def _render_health(report: WorkspaceHealthReport, *, json_mode: bool) -> None:
    if json_mode:
        _json_output(report.model_dump(mode="json"))
        return
    lines = [
        f"Workspace root: {report.workspace_root.as_posix()}",
        f"Config path: {report.config_path.as_posix()}",
        f"Workspace root source: {report.workspace_root_source}",
        f"Config source kind: {report.config_source_kind}",
        f"Asset bundle version: {report.bundle_version}",
        f"Status: {report.status.value.upper()}",
        (
            f"Checks: {report.summary.total_checks} "
            f"(pass={report.summary.passed_checks} "
            f"warn={report.summary.warning_checks} "
            f"fail={report.summary.failed_checks})"
        ),
    ]
    for check in report.checks:
        lines.append(
            f"{check.status.value.upper()}: {check.check_id} [{check.category}] {check.message}"
        )
        lines.extend(f"- {detail}" for detail in check.details)
    typer.echo("\n".join(lines))


def _render_operation(result: OperationResult, *, json_mode: bool = False) -> None:
    if json_mode:
        _json_output(result.model_dump(mode="json"))
        return
    lines = [
        f"Mode: {result.mode}",
        f"Applied: {'yes' if result.applied else 'no'}",
        f"Message: {result.message}",
    ]
    if result.command_id is not None:
        lines.append(f"Command id: {result.command_id}")
    for key, value in sorted(result.payload.items()):
        lines.append(f"{key}: {value}")
    typer.echo("\n".join(lines))


def _render_queue(snapshot: QueueSnapshot, *, json_mode: bool, detail: bool) -> None:
    if json_mode:
        _json_output(snapshot.model_dump(mode="json"))
        return
    lines = [
        f"Active task: {snapshot.active_task.task_id if snapshot.active_task else 'none'}",
        f"Backlog depth: {snapshot.backlog_depth}",
        f"Next task: {snapshot.next_task.task_id if snapshot.next_task else 'none'}",
    ]
    if detail and snapshot.backlog:
        lines.append("Backlog:")
        for item in snapshot.backlog:
            lines.append(f"- {item.task_id} :: {item.title}")
    typer.echo("\n".join(lines))


def _render_staging_sync(report: StagingSyncReport, *, json_mode: bool) -> None:
    if json_mode:
        _json_output(report.model_dump(mode="json"))
        return
    selection = report.selection
    lines = [
        f"Staging repo: {selection.staging_repo_dir}",
        f"Workspace root: {selection.workspace_root}",
        f"Manifest source: {selection.manifest_source_kind} :: {selection.manifest_source_ref}",
        f"Manifest version: {selection.manifest_version}",
        f"Required paths: {len(selection.required_paths)}",
        f"Optional paths: {len(selection.optional_paths)}",
        f"Created staging dir: {'yes' if report.created_staging_dir else 'no'}",
    ]
    if report.entries:
        lines.append("Sync entries:")
        for entry in report.entries:
            source_kind = entry.source_kind or "n/a"
            lines.append(
                f"- {entry.path}: action={entry.action} required={'yes' if entry.required else 'no'} source_kind={source_kind}"
            )
    typer.echo("\n".join(lines))


def _render_publish_preflight(report: PublishPreflightReport, *, json_mode: bool) -> None:
    if json_mode:
        _json_output(report.model_dump(mode="json"))
        return
    selection = report.selection
    lines = [
        f"Staging repo: {selection.staging_repo_dir}",
        f"Manifest source: {selection.manifest_source_kind} :: {selection.manifest_source_ref}",
        f"Manifest version: {selection.manifest_version}",
        f"Status: {report.status}",
        f"Commit allowed: {'yes' if report.commit_allowed else 'no'}",
        f"Publish allowed: {'yes' if report.publish_allowed else 'no'}",
        f"Push requested: {'yes' if report.push_requested else 'no'}",
        f"Git worktree present: {'yes' if report.git_worktree_present else 'no'}",
        f"Git worktree valid: {'yes' if report.git_worktree_valid else 'no'}",
        f"Origin configured: {'yes' if report.origin_configured else 'no'}",
        f"Branch: {report.branch or 'none'}",
        f"Has changes: {'yes' if report.has_changes else 'no'}",
        f"Commit message: {report.commit_message}",
    ]
    if report.skip_reason is not None:
        lines.append(f"Skip reason: {report.skip_reason}")
    if report.changed_paths:
        lines.append("Changed paths:")
        lines.extend(f"- {path}" for path in report.changed_paths)
    typer.echo("\n".join(lines))


def _render_publish_commit(report: PublishCommitReport, *, json_mode: bool) -> None:
    if json_mode:
        _json_output(report.model_dump(mode="json"))
        return
    selection = report.selection
    lines = [
        f"Staging repo: {selection.staging_repo_dir}",
        f"Manifest source: {selection.manifest_source_kind} :: {selection.manifest_source_ref}",
        f"Status: {report.status}",
        f"Marker: {report.marker}",
        f"Push requested: {'yes' if report.push_requested else 'no'}",
        f"Push performed: {'yes' if report.push_performed else 'no'}",
        f"Branch: {report.branch or 'none'}",
        f"Commit message: {report.commit_message}",
    ]
    if report.commit_sha is not None:
        lines.append(f"Commit sha: {report.commit_sha}")
    if report.skip_reason is not None:
        lines.append(f"Skip reason: {report.skip_reason}")
    typer.echo("\n".join(lines))


def _detail_value_text(value: Any) -> str:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return str(value)
    return json.dumps(value, sort_keys=True)


def _render_run_provenance(report: RunProvenanceReport, *, json_mode: bool) -> None:
    if json_mode:
        _json_output(report.model_dump(mode="json"))
        return
    lines = [f"Run id: {report.run_id}"]
    if report.snapshot_path is not None:
        lines.append(f"Resolved snapshot: {report.snapshot_path}")
    if report.transition_history_path is not None:
        lines.append(f"Transition history: {report.transition_history_path}")
    if report.compile_snapshot is not None:
        snapshot = report.compile_snapshot
        lines.append(f"Snapshot id: {snapshot.snapshot_id}")
    if report.selection is not None:
        if report.selection_explanation is not None:
            lines.extend(_selection_explanation_lines(report.selection_explanation))
        lines.extend(_selection_lines(report.selection))
    elif report.compile_snapshot is not None:
        lines.extend(
            [
                f"Frozen plan id: {report.compile_snapshot.frozen_plan.plan_id}",
                f"Frozen plan hash: {report.compile_snapshot.frozen_plan.content_hash}",
            ]
        )
    if report.current_preview is not None:
        lines.append("Current live preview:")
        if report.current_preview_explanation is not None:
            lines.extend(f"  {line}" for line in _selection_explanation_lines(report.current_preview_explanation))
        lines.extend(f"  {line}" for line in _selection_lines(report.current_preview))
    elif report.current_preview_error is not None:
        lines.append(f"Current live preview unavailable: {report.current_preview_error}")
    if report.routing_modes:
        lines.append(f"Routing modes observed: {', '.join(report.routing_modes)}")
    if report.integration_policy is not None:
        lines.append(
            f"Integration policy: {'run' if report.integration_policy.should_run_integration else 'skip'} "
            f"(target={report.integration_policy.builder_success_target})"
        )
        lines.append(f"Integration reason: {report.integration_policy.reason}")
    if report.policy_hooks is not None:
        lines.append(f"Policy hook records: {report.policy_hooks.record_count}")
        if report.policy_hooks.hook_counts:
            lines.append(
                "Policy hooks observed: "
                + ", ".join(
                    f"{hook}={count}"
                    for hook, count in report.policy_hooks.hook_counts.items()
                )
            )
        if report.policy_hooks.evaluator_counts:
            lines.append(
                "Policy evaluators: "
                + ", ".join(
                    f"{evaluator}={count}"
                    for evaluator, count in report.policy_hooks.evaluator_counts.items()
                )
            )
        if report.policy_hooks.decision_counts:
            lines.append(
                "Policy decisions observed: "
                + ", ".join(
                    f"{decision}={count}"
                    for decision, count in report.policy_hooks.decision_counts.items()
                )
            )
        if report.policy_hooks.latest_decision is not None:
            lines.append(f"Latest policy decision: {report.policy_hooks.latest_decision}")
        if report.policy_hooks.latest_hook is not None and report.policy_hooks.latest_evaluator is not None:
            lines.append(
                f"Latest policy record: hook={report.policy_hooks.latest_hook} "
                f"evaluator={report.policy_hooks.latest_evaluator}"
            )
        if report.policy_hooks.latest_notes:
            lines.append("Latest policy notes:")
            lines.extend(f"- {note}" for note in report.policy_hooks.latest_notes)
        if report.policy_hooks.latest_evidence_summaries:
            lines.append("Latest policy evidence:")
            lines.extend(f"- {summary}" for summary in report.policy_hooks.latest_evidence_summaries)
    if report.latest_policy_evidence is not None:
        latest = report.latest_policy_evidence
        lines.append("Latest policy evidence detail:")
        lines.append(
            f"Latest policy evidence source: event={latest.event_id} node={latest.node_id} "
            f"hook={latest.hook} evaluator={latest.evaluator} decision={latest.decision}"
        )
        lines.append(
            f"Latest policy evidence timestamp: {latest.timestamp.isoformat().replace('+00:00', 'Z')}"
        )
        if latest.routing_mode is not None:
            lines.append(f"Latest policy evidence routing mode: {latest.routing_mode}")
        lines.append(f"Latest policy evidence redaction: {latest.redaction.summary}")
        if latest.classification is not None:
            lines.append(
                f"Latest policy evidence classification: {latest.classification.label} :: "
                f"{latest.classification.summary}"
            )
        if latest.notes:
            lines.append("Latest policy evidence notes:")
            lines.extend(f"- {note}" for note in latest.notes)
        if latest.evidence:
            lines.append("Latest policy evidence detail map:")
            for item in latest.evidence:
                lines.append(f"- {item.kind.value}: {item.summary}")
                for key, value in sorted(item.details.items()):
                    lines.append(f"  {key}={_detail_value_text(value)}")
    lines.append(f"Runtime history records: {len(report.runtime_history)}")
    typer.echo("\n".join(lines))


def _render_event(event: EventRecord) -> str:
    return render_event_record_line(event)


def _render_log_events(events: list[EventRecord], *, json_mode: bool) -> None:
    if json_mode:
        _json_output([event.model_dump(mode="json") for event in events])
        return
    if not events:
        typer.echo("No events.")
        return
    typer.echo("\n".join(_render_event(event) for event in events))


def _render_follow_event(event: EventRecord, *, json_mode: bool) -> None:
    if json_mode:
        typer.echo(event.model_dump_json())
        return
    typer.echo(_render_event(event))


@app.callback()
def root(
    ctx: typer.Context,
    config_path: Annotated[
        Path,
        typer.Option(
            "--config",
            file_okay=True,
            dir_okay=False,
            resolve_path=True,
            help="Path to millrace.toml.",
        ),
    ] = Path("millrace.toml"),
) -> None:
    """Prepare CLI-local config context."""

    ctx.obj = CLIContext(config_path=config_path)


@app.command("init")
def init_command(
    destination: Annotated[
        Path,
        typer.Argument(
            file_okay=False,
            dir_okay=True,
            resolve_path=True,
            help="Destination workspace directory.",
        ),
    ],
    force: Annotated[
        bool,
        typer.Option(
            "--force",
            help="Allow a non-empty destination and overwrite manifest-tracked files.",
        ),
    ] = False,
    json_mode: Annotated[bool, typer.Option("--json", help="Render JSON output.")] = False,
) -> None:
    """Initialize a workspace from the packaged baseline bundle."""

    try:
        result = EngineControl.init_workspace(destination, force=force)
    except ControlError as exc:
        raise typer.BadParameter(str(exc), param_hint="destination") from exc
    _render_operation(result, json_mode=json_mode)


@app.command("health")
def health_command(
    ctx: typer.Context,
    json_mode: Annotated[bool, typer.Option("--json", help="Render JSON output.")] = False,
) -> None:
    """Run workspace bootstrap and health checks."""

    report = _run_expected(lambda: EngineControl.health_report(_config_path(ctx)), json_mode=json_mode)
    _render_health(report, json_mode=json_mode)
    if report.status.value == "fail":
        raise typer.Exit(code=1)


@app.command("start")
def start_command(
    ctx: typer.Context,
    daemon: Annotated[bool, typer.Option("--daemon", help="Run the foreground daemon loop.")] = False,
    once: Annotated[bool, typer.Option("--once", help="Run one foreground execution cycle.")] = False,
    json_mode: Annotated[bool, typer.Option("--json", help="Render JSON output.")] = False,
) -> None:
    """Start the runtime in foreground once or daemon mode."""

    if daemon and once:
        raise typer.BadParameter("use only one of --daemon or --once")
    report = _run_expected(lambda: _control(ctx).start(daemon=daemon, once=once), json_mode=json_mode)
    if json_mode:
        _json_output(report.model_dump(mode="json"))
        return
    typer.echo(
        "\n".join(
            [
                f"Process: {'running' if report.process_running else 'stopped'}",
                f"Paused: {'yes' if report.paused else 'no'}",
                f"Execution status: {report.execution_status.value}",
                f"Research status: {report.research_status.value}",
            ]
        )
    )


@app.command("stop")
def stop_command(ctx: typer.Context, json_mode: Annotated[bool, typer.Option("--json")] = False) -> None:
    """Request daemon stop."""

    _render_operation(_run_expected(lambda: _control(ctx).stop(), json_mode=json_mode), json_mode=json_mode)


@app.command("pause")
def pause_command(ctx: typer.Context, json_mode: Annotated[bool, typer.Option("--json")] = False) -> None:
    """Request daemon pause."""

    _render_operation(_run_expected(lambda: _control(ctx).pause(), json_mode=json_mode), json_mode=json_mode)


@app.command("resume")
def resume_command(ctx: typer.Context, json_mode: Annotated[bool, typer.Option("--json")] = False) -> None:
    """Request daemon resume."""

    _render_operation(_run_expected(lambda: _control(ctx).resume(), json_mode=json_mode), json_mode=json_mode)


@app.command("status")
def status_command(
    ctx: typer.Context,
    detail: Annotated[bool, typer.Option("--detail", help="Include queue detail.")] = False,
    json_mode: Annotated[bool, typer.Option("--json", help="Render JSON output.")] = False,
) -> None:
    """Show runtime status."""

    _render_status(
        _run_expected(lambda: _control(ctx).status(detail=detail), json_mode=json_mode),
        json_mode=json_mode,
    )


@app.command("run-provenance")
def run_provenance_command(
    ctx: typer.Context,
    run_id: Annotated[str, typer.Argument(help="Run identifier under agents/runs/.")],
    json_mode: Annotated[bool, typer.Option("--json", help="Render JSON output.")] = False,
) -> None:
    """Show compile-time and runtime provenance for one run."""

    report = _run_expected(lambda: _control(ctx).run_provenance(run_id), json_mode=json_mode)
    _render_run_provenance(report, json_mode=json_mode)


@config_app.command("show")
def config_show_command(
    ctx: typer.Context,
    json_mode: Annotated[bool, typer.Option("--json", help="Render JSON output.")] = False,
) -> None:
    """Show the loaded config."""

    report: ConfigShowReport = _run_expected(lambda: _control(ctx).config_show(), json_mode=json_mode)
    if json_mode:
        _json_output(report.model_dump(mode="json"))
        return
    lines = [
        f"Source kind: {report.source.kind}",
        f"Primary path: {report.source.primary_path}",
        f"Config hash: {report.config_hash}",
        f"Execution integration mode: {report.config.execution.integration_mode}",
        f"Quickfix max attempts: {report.config.execution.quickfix_max_attempts}",
        f"Sizing mode: {report.config.sizing.mode}",
        (
            "Repo size thresholds: "
            f"files>={report.config.sizing.repo.file_count_threshold} "
            f"nonempty_lines>={report.config.sizing.repo.nonempty_line_count_threshold}"
        ),
        (
            "Task size thresholds: "
            f"files_to_touch>={report.config.sizing.task.file_count_threshold} "
            f"nonempty_lines>={report.config.sizing.task.nonempty_line_count_threshold} "
            "promotion=2-of-3(files, loc, complexity)"
        ),
        f"Research mode: {report.config.research.mode.value}",
    ]
    if report.source.secondary_paths:
        lines.append("Secondary source paths:")
        lines.extend(f"- {path}" for path in report.source.secondary_paths)
    if report.source.legacy_policy_compatibility is not None:
        lines.extend(_legacy_policy_lines(report.source.legacy_policy_compatibility))
    lines.extend(_legacy_unmapped_lines(report.source.unmapped_keys))
    lines.extend(_selection_explanation_lines(report.selection_explanation))
    lines.extend(_selection_lines(report.selection))
    lines.extend(_asset_inventory_lines(report.assets))
    typer.echo("\n".join(lines))


@config_app.command("set")
def config_set_command(
    ctx: typer.Context,
    key: Annotated[str, typer.Argument(help="Dotted config key.")],
    value: Annotated[str, typer.Argument(help="New value.")],
    json_mode: Annotated[bool, typer.Option("--json", help="Render JSON output.")] = False,
) -> None:
    """Set one dotted config key."""

    _render_operation(
        _run_expected(lambda: _control(ctx).config_set(key, value), json_mode=json_mode),
        json_mode=json_mode,
    )


@config_app.command("reload")
def config_reload_command(
    ctx: typer.Context,
    json_mode: Annotated[bool, typer.Option("--json", help="Render JSON output.")] = False,
) -> None:
    """Reload config from disk."""

    _render_operation(_run_expected(lambda: _control(ctx).config_reload(), json_mode=json_mode), json_mode=json_mode)


@queue_app.command("inspect")
def queue_inspect_command(
    ctx: typer.Context,
    json_mode: Annotated[bool, typer.Option("--json", help="Render JSON output.")] = False,
) -> None:
    """Show active and backlog task detail."""

    _render_queue(
        _run_expected(lambda: _control(ctx).queue_inspect(), json_mode=json_mode),
        json_mode=json_mode,
        detail=True,
    )


@queue_app.command("reorder")
def queue_reorder_command(
    ctx: typer.Context,
    task_ids: Annotated[list[str], typer.Argument(help="Backlog task IDs in final order.")],
    json_mode: Annotated[bool, typer.Option("--json", help="Render JSON output.")] = False,
) -> None:
    """Rewrite the backlog order exactly as provided."""

    if not task_ids:
        raise typer.BadParameter("provide at least one task id to reorder")
    _render_operation(
        _run_expected(lambda: _control(ctx).queue_reorder(task_ids), json_mode=json_mode),
        json_mode=json_mode,
    )


@queue_app.callback(invoke_without_command=True)
def queue_root(
    ctx: typer.Context,
    json_mode: Annotated[bool, typer.Option("--json", help="Render JSON output.")] = False,
) -> None:
    """Show queue summary."""

    if ctx.invoked_subcommand is not None:
        return
    _render_queue(
        _run_expected(lambda: _control(ctx).queue(), json_mode=json_mode),
        json_mode=json_mode,
        detail=False,
    )


@research_app.command("history")
def research_history_command(
    ctx: typer.Context,
    limit: Annotated[int, typer.Option("--limit", min=0, help="Number of recent research events to show.")] = 20,
    json_mode: Annotated[bool, typer.Option("--json", help="Render JSON output.")] = False,
) -> None:
    """Show recent research-related events."""

    _render_log_events(
        _run_expected(lambda: _control(ctx).research_history(limit=limit), json_mode=json_mode),
        json_mode=json_mode,
    )


@research_app.callback(invoke_without_command=True)
def research_root(
    ctx: typer.Context,
    json_mode: Annotated[bool, typer.Option("--json", help="Render JSON output.")] = False,
) -> None:
    """Show research runtime visibility."""

    if ctx.invoked_subcommand is not None:
        return
    report = _run_expected(lambda: _control(ctx).research_report(), json_mode=json_mode)
    if json_mode:
        _json_output(report.model_dump(mode="json"))
        return
    typer.echo("\n".join(_research_report_lines(report)))


@publish_app.command("sync")
def publish_sync_command(
    ctx: typer.Context,
    staging_repo_dir: Annotated[
        Path | None,
        typer.Option(
            "--staging-repo-dir",
            file_okay=False,
            dir_okay=True,
            resolve_path=True,
            help="Override the default staging repo directory.",
        ),
    ] = None,
    json_mode: Annotated[bool, typer.Option("--json", help="Render JSON output.")] = False,
) -> None:
    """Sync manifest-selected files into the staging repo."""

    report = _run_expected(
        lambda: _control(ctx).publish_sync(staging_repo_dir=staging_repo_dir),
        json_mode=json_mode,
    )
    _render_staging_sync(report, json_mode=json_mode)


@publish_app.command("preflight")
def publish_preflight_command(
    ctx: typer.Context,
    staging_repo_dir: Annotated[
        Path | None,
        typer.Option(
            "--staging-repo-dir",
            file_okay=False,
            dir_okay=True,
            resolve_path=True,
            help="Override the default staging repo directory.",
        ),
    ] = None,
    commit_message: Annotated[
        str | None,
        typer.Option("--message", help="Commit message to evaluate."),
    ] = None,
    push: Annotated[
        bool,
        typer.Option("--push/--no-push", help="Check whether a publish push would run."),
    ] = False,
    json_mode: Annotated[bool, typer.Option("--json", help="Render JSON output.")] = False,
) -> None:
    """Show staging commit/publish readiness without mutating git state."""

    report = _run_expected(
        lambda: _control(ctx).publish_preflight(
            staging_repo_dir=staging_repo_dir,
            commit_message=commit_message,
            push=push,
        ),
        json_mode=json_mode,
    )
    _render_publish_preflight(report, json_mode=json_mode)


@publish_app.command("commit")
def publish_commit_command(
    ctx: typer.Context,
    staging_repo_dir: Annotated[
        Path | None,
        typer.Option(
            "--staging-repo-dir",
            file_okay=False,
            dir_okay=True,
            resolve_path=True,
            help="Override the default staging repo directory.",
        ),
    ] = None,
    commit_message: Annotated[
        str | None,
        typer.Option("--message", help="Commit message to use."),
    ] = None,
    push: Annotated[
        bool,
        typer.Option("--push/--no-push", help="Push to origin after commit."),
    ] = False,
    json_mode: Annotated[bool, typer.Option("--json", help="Render JSON output.")] = False,
) -> None:
    """Commit staging changes and optionally push them."""

    report = _run_expected(
        lambda: _control(ctx).publish_commit(
            staging_repo_dir=staging_repo_dir,
            commit_message=commit_message,
            push=push,
        ),
        json_mode=json_mode,
    )
    _render_publish_commit(report, json_mode=json_mode)


@app.command("add-task")
def add_task_command(
    ctx: typer.Context,
    title: Annotated[str, typer.Argument(help="Task title.")],
    body: Annotated[str | None, typer.Option("--body", help="Optional markdown body.")] = None,
    spec_id: Annotated[str | None, typer.Option("--spec-id", help="Optional spec identifier.")] = None,
    json_mode: Annotated[bool, typer.Option("--json", help="Render JSON output.")] = False,
) -> None:
    """Add one task card to the backlog."""

    _render_operation(
        _run_expected(lambda: _control(ctx).add_task(title, body=body, spec_id=spec_id), json_mode=json_mode),
        json_mode=json_mode,
    )


@app.command("add-idea")
def add_idea_command(
    ctx: typer.Context,
    file: Annotated[Path, typer.Argument(exists=True, file_okay=True, dir_okay=False, resolve_path=True)],
    json_mode: Annotated[bool, typer.Option("--json", help="Render JSON output.")] = False,
) -> None:
    """Queue one idea file into `agents/ideas/raw/`."""

    _render_operation(_run_expected(lambda: _control(ctx).add_idea(file), json_mode=json_mode), json_mode=json_mode)


@app.command("logs")
def logs_command(
    ctx: typer.Context,
    tail: Annotated[int, typer.Option("--tail", min=0, help="Number of recent events to show.")] = 50,
    follow: Annotated[bool, typer.Option("--follow", help="Stream new events as they arrive.")] = False,
    limit: Annotated[
        int | None,
        typer.Option("--limit", min=1, help="Maximum number of followed events before exiting."),
    ] = None,
    idle_timeout: Annotated[
        float | None,
        typer.Option("--idle-timeout", min=0.1, help="Stop follow mode after this many idle seconds."),
    ] = None,
    json_mode: Annotated[bool, typer.Option("--json", help="Render JSON output.")] = False,
) -> None:
    """Show recent structured runtime events."""

    control = _run_expected(lambda: _control(ctx), json_mode=json_mode)
    if not follow:
        _render_log_events(_run_expected(lambda: control.logs(n=tail), json_mode=json_mode), json_mode=json_mode)
        return

    if tail > 0:
        for event in _run_expected(lambda: control.logs(n=tail), json_mode=json_mode):
            _render_follow_event(event, json_mode=json_mode)

    followed = 0
    try:
        events = _run_expected(
            lambda: control.events_subscribe(
                start_at_end=True,
                idle_timeout_seconds=idle_timeout,
            ),
            json_mode=json_mode,
        )
        for event in _iter_expected(events, json_mode=json_mode):
            _render_follow_event(event, json_mode=json_mode)
            followed += 1
            if limit is not None and followed >= limit:
                break
    except KeyboardInterrupt as exc:
        raise typer.Exit(code=0) from exc


def main() -> None:
    """Run the Typer app."""

    app()
