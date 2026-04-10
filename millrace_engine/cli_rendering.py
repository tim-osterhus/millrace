"""Deterministic human/JSON renderers for the CLI surface."""

from __future__ import annotations

import json
from typing import Any

import typer

from .config_compat import LegacyPolicyCompatReport, LegacyPolicyCompatStatus
from .control import (
    AssetInventoryView,
    CompoundingContextFactListReport,
    CompoundingContextFactReport,
    CompoundingGovernanceSummaryView,
    CompoundingHarnessBenchmarkListReport,
    CompoundingHarnessBenchmarkReport,
    CompoundingHarnessCandidateListReport,
    CompoundingHarnessCandidateReport,
    CompoundingHarnessRecommendationListReport,
    CompoundingHarnessRecommendationReport,
    CompoundingIntegrityReport,
    CompoundingOrientationReport,
    CompoundingProcedureListReport,
    CompoundingProcedureReport,
    OperationResult,
    QueueSnapshot,
    ResearchReport,
    RunProvenanceReport,
    SelectionExplanationView,
    StatusReport,
    SupervisorReport,
    WorkspaceHealthReport,
)
from .events import EventRecord, render_event_record_line
from .health import HealthCheckStatus
from .publishing import PublishCommitReport, PublishPreflightReport, StagingSyncReport
from .standard_runtime import (
    RegistryObjectSelectionView,
    RuntimeSelectionView,
    StageExecutionBindingView,
)


def _json_output(payload: Any, *, err: bool = False) -> None:
    typer.echo(json.dumps(payload, indent=2, sort_keys=True), err=err)


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


def render_status(report: StatusReport, *, json_mode: bool) -> None:
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
    if runtime.execution_status is ExecutionStatus.IDLE:
        lines.append(
            "Execution status detail: IDLE is the execution plane's neutral state "
            "(no execution stage active); it does not mean the daemon is stopped."
        )
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


def render_supervisor_report(report: SupervisorReport, *, json_mode: bool) -> None:
    if json_mode:
        _json_output(report.model_dump(mode="json"))
        return
    lines = [
        f"Schema version: {report.schema_version}",
        f"Workspace root: {report.workspace_root.as_posix()}",
        f"Config path: {report.config_path.as_posix()}",
        f"Generated at: {report.generated_at.isoformat().replace('+00:00', 'Z')}",
        f"Health status: {report.health_status.value.upper()}",
        f"Bootstrap ready: {'yes' if report.bootstrap_ready else 'no'}",
        f"Execution ready: {'yes' if report.execution_ready else 'no'}",
        f"Process: {'running' if report.process_running else 'stopped'}",
        f"Paused: {'yes' if report.paused else 'no'}",
        f"Execution status: {report.execution_status.value}",
        f"Research status: {report.research_status.value}",
        f"Status source: {report.status_source_kind}",
        f"Research source: {report.research_source_kind}",
        f"Backlog depth: {report.backlog_depth}",
        f"Deferred queue size: {report.deferred_queue_size}",
        f"Active task: {report.active_task.task_id if report.active_task is not None else 'none'}",
        (
            f"Next task: {report.next_task.task_id} :: {report.next_task.title}"
            if report.next_task is not None
            else "Next task: none"
        ),
        f"Current run id: {report.current_run_id or 'none'}",
        f"Current stage: {report.current_stage or 'none'}",
        (
            f"Time in current status seconds: {report.time_in_current_status_seconds:.1f}"
            if report.time_in_current_status_seconds is not None
            else "Time in current status seconds: n/a"
        ),
        f"Attention reason: {report.attention_reason.value}",
        f"Attention summary: {report.attention_summary}",
        (
            "Allowed actions: "
            + (", ".join(action.value for action in report.allowed_actions) if report.allowed_actions else "none")
        ),
        (
            "Health summary: "
            f"total={report.health_summary.total_checks} "
            f"pass={report.health_summary.passed_checks} "
            f"warn={report.health_summary.warning_checks} "
            f"fail={report.health_summary.failed_checks}"
        ),
    ]
    if report.execution_status is ExecutionStatus.IDLE:
        lines.append(
            "Execution status detail: IDLE is the execution plane's neutral state "
            "(no execution stage active); it does not mean the daemon is stopped."
        )
    if report.recent_events:
        lines.append("Recent events:")
        lines.extend(f"- {render_event_record_line(event)}" for event in report.recent_events)
    else:
        lines.append("Recent events: none")
    typer.echo("\n".join(lines))


def render_health(report: WorkspaceHealthReport, *, json_mode: bool) -> None:
    if json_mode:
        _json_output(report.model_dump(mode="json"))
        return
    lines = [
        f"Workspace root: {report.workspace_root.as_posix()}",
        f"Config path: {report.config_path.as_posix()}",
        f"Workspace root source: {report.workspace_root_source}",
        f"Config source kind: {report.config_source_kind}",
        f"Asset bundle version: {report.bundle_version}",
        (
            "Research bootstrap: "
            f"{report.research_bootstrap.contract_state} "
            f"(mode={report.research_bootstrap.mode.value if report.research_bootstrap.mode is not None else 'unknown'}, "
            f"interview_policy="
            f"{report.research_bootstrap.interview_policy.value if report.research_bootstrap.interview_policy is not None else 'unknown'})"
        ),
        f"Research summary: {report.research_bootstrap.summary}",
        f"Bootstrap ready: {'yes' if report.bootstrap_ready else 'no'}",
        f"Execution ready: {'yes' if report.execution_ready else 'no'}",
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


def render_doctor(report: WorkspaceHealthReport, *, json_mode: bool) -> None:
    if json_mode:
        _json_output(report.model_dump(mode="json"))
        return
    lines = [
        f"Workspace root: {report.workspace_root.as_posix()}",
        f"Config path: {report.config_path.as_posix()}",
        (
            "Research bootstrap: "
            f"{report.research_bootstrap.contract_state} "
            f"(mode={report.research_bootstrap.mode.value if report.research_bootstrap.mode is not None else 'unknown'}, "
            f"interview_policy="
            f"{report.research_bootstrap.interview_policy.value if report.research_bootstrap.interview_policy is not None else 'unknown'})"
        ),
        f"Research summary: {report.research_bootstrap.summary}",
        f"Bootstrap ready: {'yes' if report.bootstrap_ready else 'no'}",
        f"Execution ready: {'yes' if report.execution_ready else 'no'}",
    ]
    if report.bootstrap_ready and report.execution_ready:
        lines.append("PASS: workspace bootstrap and configured runner prerequisites are ready.")
    elif not report.bootstrap_ready:
        lines.append("FAIL: workspace bootstrap is incomplete; fix the failing health checks before starting the runtime.")
    else:
        lines.append("FAIL: workspace bootstrap passed, but configured execution stages are missing runner prerequisites.")
    lines.append(f"Research next step: {report.research_bootstrap.next_step}")
    if report.runner_prerequisites:
        lines.append("Runner prerequisites:")
        for prerequisite in report.runner_prerequisites:
            status = "ready" if prerequisite.available else "missing"
            lines.append(
                f"- {prerequisite.executable}: {status}; "
                f"stages={', '.join(stage.value for stage in prerequisite.affected_stages) or 'unknown'}; "
                f"nodes={', '.join(prerequisite.affected_stage_nodes) or 'unknown'}"
            )
            if prerequisite.resolved_path is not None:
                lines.append(f"  resolved_path={prerequisite.resolved_path.as_posix()}")
            if not prerequisite.available:
                lines.append(
                    f"  action=install `{prerequisite.executable}` and ensure it is on PATH, or reconfigure the affected stages before `start --once`."
                )
    failing_checks = [check for check in report.checks if check.status is HealthCheckStatus.FAIL]
    if failing_checks:
        lines.append("Failing checks:")
        for check in failing_checks:
            lines.append(f"- {check.check_id}: {check.message}")
            lines.extend(f"  {detail}" for detail in check.details)
    typer.echo("\n".join(lines))


def render_operation(result: OperationResult, *, json_mode: bool = False) -> None:
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


def render_upgrade_preview(result: OperationResult, *, json_mode: bool = False) -> None:
    if json_mode:
        _json_output(result.model_dump(mode="json"))
        return

    payload = result.payload
    lines = [
        "Upgrade preview: baseline refresh plus persisted-state migration inspection",
        f"Workspace root: {payload['workspace_root']}",
        f"Bundle version: {payload['bundle_version']}",
        f"Manifest files: {payload['manifest_file_count']}",
        f"Manifest directories: {payload['manifest_directory_count']}",
        f"Would create: {len(payload['would_create'])}",
        f"Would update: {len(payload['would_update'])}",
        f"Unchanged: {len(payload['unchanged'])}",
        f"Conflicts: {len(payload['conflicting_paths'])}",
        f"Would materialize runtime-owned paths: {len(payload['would_materialize_runtime_owned'])}",
        f"Preserved runtime-owned paths: {len(payload['preserved_runtime_owned'])}",
        f"Preserved operator-owned files: {len(payload['preserved_operator_owned'])}",
        f"Persisted-state migrations: {len(payload['persisted_state_migrations'])}",
        "Preview only: yes",
    ]
    section_order = (
        ("Would create", payload["would_create"]),
        ("Would update", payload["would_update"]),
        ("Unchanged", payload["unchanged"]),
        ("Conflicting paths", payload["conflicting_paths"]),
        ("Would materialize runtime-owned paths", payload["would_materialize_runtime_owned"]),
        ("Preserved runtime-owned paths", payload["preserved_runtime_owned"]),
        ("Preserved operator-owned files", payload["preserved_operator_owned"]),
    )
    for label, paths in section_order:
        if not paths:
            continue
        lines.append(f"{label}:")
        lines.extend(f"- {path}" for path in paths)
    for migration in payload["persisted_state_migrations"]:
        lines.extend(
            [
                "Persisted-state migration:",
                f"- family={migration['state_family']} action={migration['action']} "
                f"would_write_state_file={_bool_label(migration['would_write_state_file'])} "
                f"breadcrumbs={migration['breadcrumb_file_count']}",
                f"  state_path={migration['state_path']}",
                f"  deferred_dir={migration['deferred_dir']}",
                f"  summary={migration['summary']}",
            ]
        )
    typer.echo("\n".join(lines))


def render_upgrade_apply(result: OperationResult, *, json_mode: bool = False) -> None:
    if json_mode:
        _json_output(result.model_dump(mode="json"))
        return

    payload = result.payload
    lines = [
        "Upgrade apply: baseline refresh plus persisted-state migration",
        f"Workspace root: {payload['workspace_root']}",
        f"Bundle version: {payload['bundle_version']}",
        f"Manifest files: {payload['manifest_file_count']}",
        f"Manifest directories: {payload['manifest_directory_count']}",
        f"Created manifest directories: {payload['created_directory_count']}",
        f"Created manifest files: {payload['created_file_count']}",
        f"Updated manifest files: {payload['updated_file_count']}",
        f"Unchanged: {len(payload['unchanged'])}",
        f"Conflicts: {len(payload['conflicting_paths'])}",
        f"Materialized runtime-owned paths: {len(payload['materialized_runtime_owned'])}",
        f"Preserved runtime-owned paths: {len(payload['preserved_runtime_owned'])}",
        f"Preserved operator-owned files: {len(payload['preserved_operator_owned'])}",
        f"Persisted-state migrations: {len(payload['persisted_state_migrations'])}",
        "Applied: yes",
    ]
    section_order = (
        ("Created files", payload["created_files"]),
        ("Updated files", payload["updated_files"]),
        ("Unchanged", payload["unchanged"]),
        ("Conflicting paths", payload["conflicting_paths"]),
        ("Materialized runtime-owned paths", payload["materialized_runtime_owned"]),
        ("Preserved runtime-owned paths", payload["preserved_runtime_owned"]),
        ("Preserved operator-owned files", payload["preserved_operator_owned"]),
    )
    for label, paths in section_order:
        if not paths:
            continue
        lines.append(f"{label}:")
        lines.extend(f"- {path}" for path in paths)
    for migration in payload["persisted_state_migrations"]:
        lines.extend(
            [
                "Persisted-state migration:",
                f"- family={migration['state_family']} action={migration['action']} "
                f"wrote_state_file={_bool_label(migration['wrote_state_file'])} "
                f"breadcrumbs={migration['breadcrumb_file_count']}",
                f"  state_path={migration['state_path']}",
                f"  deferred_dir={migration['deferred_dir']}",
                f"  summary={migration['summary']}",
            ]
        )
    typer.echo("\n".join(lines))


def render_queue(snapshot: QueueSnapshot, *, json_mode: bool, detail: bool) -> None:
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


def render_staging_sync(report: StagingSyncReport, *, json_mode: bool) -> None:
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


def render_publish_preflight(report: PublishPreflightReport, *, json_mode: bool) -> None:
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


def render_publish_commit(report: PublishCommitReport, *, json_mode: bool) -> None:
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


def render_run_provenance(report: RunProvenanceReport, *, json_mode: bool) -> None:
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
    if report.compounding is not None:
        flush_checkpoints = tuple(getattr(report.compounding, "flush_checkpoints", ()))
        flush_count = int(getattr(report.compounding, "flush_count", len(flush_checkpoints)))
        lines.append(
            "Compounding summary: "
            f"created={report.compounding.created_count} "
            f"procedure_selections={report.compounding.selection_count} "
            f"context_fact_selections={report.compounding.fact_selection_count} "
            f"flush_checkpoints={flush_count}"
        )
        if report.compounding.created_procedures:
            lines.append("Created procedures:")
            for procedure in report.compounding.created_procedures:
                lines.append(
                    f"- {procedure.procedure_id} [{procedure.scope.value}] "
                    f"stage={procedure.source_stage} path={procedure.artifact_path}"
                )
        if report.compounding.procedure_selections:
            lines.append("Procedure selections:")
            for selection in report.compounding.procedure_selections:
                lines.append(
                    f"- {selection.stage} ({selection.node_id}): "
                    f"considered={selection.considered_count} injected={selection.injected_count}"
                )
                if selection.injected_procedures:
                    lines.append(
                        "  Injected: "
                        + ", ".join(procedure.procedure_id for procedure in selection.injected_procedures)
                    )
                elif selection.considered_procedures:
                    lines.append("  Injected: none")
                if selection.considered_procedures:
                    lines.append(
                        "  Considered: "
                        + ", ".join(procedure.procedure_id for procedure in selection.considered_procedures)
                    )
        if report.compounding.context_fact_selections:
            lines.append("Context fact selections:")
            for selection in report.compounding.context_fact_selections:
                lines.append(
                    f"- {selection.stage} ({selection.node_id}): "
                    f"considered={selection.considered_count} injected={selection.injected_count}"
                )
                if selection.injected_facts:
                    lines.append("  Injected: " + ", ".join(fact.fact_id for fact in selection.injected_facts))
                elif selection.considered_facts:
                    lines.append("  Injected: none")
                if selection.considered_facts:
                    lines.append("  Considered: " + ", ".join(fact.fact_id for fact in selection.considered_facts))
        if flush_checkpoints:
            lines.append("Compounding flush checkpoints:")
            for checkpoint in flush_checkpoints:
                lines.append(
                    f"- {checkpoint.trigger_stage} ({checkpoint.node_id}): "
                    f"milestone={checkpoint.milestone.value} "
                    f"procedures={len(checkpoint.finalized_procedure_ids)} "
                    f"context_facts={len(checkpoint.finalized_context_fact_ids)}"
                )
    lines.append(f"Runtime history records: {len(report.runtime_history)}")
    typer.echo("\n".join(lines))


def render_log_events(events: list[EventRecord], *, json_mode: bool) -> None:
    if json_mode:
        _json_output([event.model_dump(mode="json") for event in events])
        return
    if not events:
        typer.echo("No events.")
        return
    typer.echo("\n".join(render_event_record_line(event) for event in events))


def render_compounding_procedures(report: CompoundingProcedureListReport, *, json_mode: bool) -> None:
    if json_mode:
        _json_output(report.model_dump(mode="json"))
        return
    if not report.procedures:
        typer.echo("No compounding procedures.")
        return
    lines: list[str] = []
    for procedure in report.procedures:
        lines.append(
            f"{procedure.procedure_id} [{procedure.scope.value}] "
            f"status={procedure.retrieval_status} eligible={'yes' if procedure.eligible_for_retrieval else 'no'}"
        )
        lines.append(f"  Title: {procedure.title}")
        lines.append(f"  Source stage: {procedure.source_stage}")
        lines.append(f"  Artifact: {procedure.artifact_path}")
        if procedure.latest_lifecycle_record is not None:
            lines.append(
                "  Latest review: "
                f"{procedure.latest_lifecycle_record.state.value} by {procedure.latest_lifecycle_record.changed_by}"
            )
    typer.echo("\n".join(lines))


def render_compounding_procedure(report: CompoundingProcedureReport, *, json_mode: bool) -> None:
    if json_mode:
        _json_output(report.model_dump(mode="json"))
        return
    procedure = report.procedure
    lines = [
        f"Procedure ID: {procedure.procedure_id}",
        f"Scope: {procedure.scope.value}",
        f"Status: {procedure.retrieval_status}",
        f"Eligible for retrieval: {'yes' if procedure.eligible_for_retrieval else 'no'}",
        f"Title: {procedure.title}",
        f"Source run: {procedure.source_run_id}",
        f"Source stage: {procedure.source_stage}",
        f"Artifact: {procedure.artifact_path}",
        f"Summary: {procedure.summary}",
    ]
    if procedure.evidence_refs:
        lines.append("Evidence refs:")
        lines.extend(f"- {item}" for item in procedure.evidence_refs)
    if report.lifecycle_records:
        lines.append("Lifecycle records:")
        for record in report.lifecycle_records:
            line = (
                f"- {record.state.value} at {record.changed_at.isoformat().replace('+00:00', 'Z')} "
                f"by {record.changed_by}: {record.reason}"
            )
            if record.replacement_procedure_id is not None:
                line += f" replacement={record.replacement_procedure_id}"
            lines.append(line)
    typer.echo("\n".join(lines))


def render_compounding_context_facts(report: CompoundingContextFactListReport, *, json_mode: bool) -> None:
    if json_mode:
        _json_output(report.model_dump(mode="json"))
        return
    if not report.facts:
        typer.echo("No compounding context facts.")
        return
    lines: list[str] = []
    for fact in report.facts:
        lines.append(
            f"{fact.fact_id} [{fact.scope.value}] status={fact.retrieval_status} "
            f"eligible={'yes' if fact.eligible_for_retrieval else 'no'}"
        )
        lines.append(f"  Title: {fact.title}")
        lines.append(f"  Source stage: {fact.source_stage}")
        lines.append(f"  Artifact: {fact.artifact_path}")
    typer.echo("\n".join(lines))


def render_compounding_context_fact(report: CompoundingContextFactReport, *, json_mode: bool) -> None:
    if json_mode:
        _json_output(report.model_dump(mode="json"))
        return
    fact = report.fact
    lines = [
        f"Fact ID: {fact.fact_id}",
        f"Scope: {fact.scope.value}",
        f"Lifecycle state: {fact.lifecycle_state.value}",
        f"Status: {fact.retrieval_status}",
        f"Eligible for retrieval: {'yes' if fact.eligible_for_retrieval else 'no'}",
        f"Title: {fact.title}",
        f"Source run: {fact.source_run_id}",
        f"Source stage: {fact.source_stage}",
        f"Artifact: {fact.artifact_path}",
        f"Summary: {fact.summary}",
        f"Statement: {fact.statement}",
    ]
    if fact.observed_at is not None:
        lines.append(f"Observed at: {fact.observed_at.isoformat().replace('+00:00', 'Z')}")
    if fact.tags:
        lines.append("Tags:")
        lines.extend(f"- {item}" for item in fact.tags)
    if fact.evidence_refs:
        lines.append("Evidence refs:")
        lines.extend(f"- {item}" for item in fact.evidence_refs)
    if fact.stale_reason is not None:
        lines.append(f"Stale reason: {fact.stale_reason}")
    if fact.supersedes_fact_id is not None:
        lines.append(f"Supersedes fact: {fact.supersedes_fact_id}")
    typer.echo("\n".join(lines))


def render_compounding_governance_summary(report: CompoundingGovernanceSummaryView, *, json_mode: bool) -> None:
    if json_mode:
        _json_output(report.model_dump(mode="json"))
        return
    lines = [
        f"Pending governance items: {report.pending_governance_items}",
        (
            "Procedures: "
            f"total={report.procedure_total} eligible={report.procedure_eligible} "
            f"pending_review={report.procedure_pending_review} deprecated={report.procedure_deprecated}"
        ),
        (
            "Context facts: "
            f"total={report.context_fact_total} eligible={report.context_fact_eligible} "
            f"pending_review={report.context_fact_pending_review} deprecated={report.context_fact_deprecated}"
        ),
        (
            "Harness candidates: "
            f"total={report.harness_candidate_total} pending_review={report.harness_candidate_pending_review} "
            f"accepted={report.harness_candidate_accepted} rejected={report.harness_candidate_rejected}"
        ),
        (
            "Recommendations: "
            f"total={report.recommendation_total} recommend={report.recommendation_pending} "
            f"no_change={report.recommendation_no_change}"
        ),
        f"Benchmarks: total={report.benchmark_total}",
    ]
    if report.recent_usage_run_id is not None:
        lines.append(
            "Recent knowledge usage: "
            f"run={report.recent_usage_run_id} procedures={report.recent_usage_procedure_count} "
            f"context_facts={report.recent_usage_context_fact_count}"
        )
    else:
        lines.append("Recent knowledge usage: none")
    if report.latest_recommendation_id is not None and report.latest_recommendation_summary is not None:
        lines.append(
            "Latest recommendation: "
            f"{report.latest_recommendation_id} :: {report.latest_recommendation_summary}"
        )
    typer.echo("\n".join(lines))


def render_compounding_orientation(report: CompoundingOrientationReport, *, json_mode: bool) -> None:
    if json_mode:
        _json_output(report.model_dump(mode="json"))
        return
    lines = [
        report.secondary_surface_note,
        (
            "Index artifact: "
            f"{report.index_artifact.path} "
            f"generated={report.index_artifact.generated_at.isoformat().replace('+00:00', 'Z')} "
            f"entries={report.index_artifact.item_count}"
        ),
        (
            "Relationship artifact: "
            f"{report.relationship_artifact.path} "
            f"generated={report.relationship_artifact.generated_at.isoformat().replace('+00:00', 'Z')} "
            f"clusters={report.relationship_artifact.item_count}"
        ),
        "Family counts: "
        + (", ".join(f"{key}={report.family_counts[key]}" for key in sorted(report.family_counts)) or "none"),
        "Relationship counts: "
        + (", ".join(f"{key}={report.cluster_counts[key]}" for key in sorted(report.cluster_counts)) or "none"),
    ]
    if report.query is not None:
        lines.append(f"Query: {report.query}")
    if report.entries:
        lines.append("Matching entries:")
        for entry in report.entries:
            source_bits = []
            if entry.source_run_id is not None:
                source_bits.append(f"run={entry.source_run_id}")
            if entry.source_stage is not None:
                source_bits.append(f"stage={entry.source_stage}")
            source_suffix = f" {' '.join(source_bits)}" if source_bits else ""
            lines.append(
                f"- {entry.entry_id} [{entry.family.value} {entry.status}] {entry.label}{source_suffix}"
            )
            lines.append(f"  Artifact: {entry.artifact_path}")
            if entry.related_ids:
                lines.append(f"  Related: {', '.join(entry.related_ids)}")
    else:
        lines.append("Matching entries: none")
    if report.relationship_clusters:
        lines.append("Relationship summaries:")
        for cluster in report.relationship_clusters:
            lines.append(f"- {cluster.cluster_id} [{cluster.kind.value}] {cluster.label}")
            lines.append(f"  Summary: {cluster.summary}")
            lines.append(f"  Members: {', '.join(cluster.member_ids) or 'none'}")
            if cluster.shared_terms:
                lines.append(f"  Shared terms: {', '.join(cluster.shared_terms)}")
    else:
        lines.append("Relationship summaries: none")
    typer.echo("\n".join(lines))


def render_compounding_lint(report: CompoundingIntegrityReport, *, json_mode: bool) -> None:
    if json_mode:
        _json_output(report.model_dump(mode="json"))
        return
    lines = [
        f"Status: {report.status.value.upper()}",
        f"Summary: {report.summary}",
        (
            "Checked counts: "
            + (", ".join(f"{key}={report.checked_counts[key]}" for key in sorted(report.checked_counts)) or "none")
        ),
        f"Stored orientation index present: {_bool_label(report.orientation_index_present)}",
        f"Stored relationship summary present: {_bool_label(report.relationship_summary_present)}",
        f"Issues: total={report.issue_count} warn={report.warning_count} fail={report.failure_count}",
    ]
    if report.issues:
        lines.append("Findings:")
        for issue in report.issues:
            lines.append(f"- {issue.severity.value.upper()}: {issue.issue_id} [{issue.family.value}] {issue.message}")
            if issue.artifact_ref is not None:
                lines.append(f"  Artifact: {issue.artifact_ref}")
            if issue.related_refs:
                lines.append(f"  Related: {', '.join(issue.related_refs)}")
    else:
        lines.append("Findings: none")
    typer.echo("\n".join(lines))


def render_compounding_harness_candidates(report: CompoundingHarnessCandidateListReport, *, json_mode: bool) -> None:
    if json_mode:
        _json_output(report.model_dump(mode="json"))
        return
    if not report.candidates:
        typer.echo("No compounding harness candidates.")
        return
    lines: list[str] = []
    for candidate in report.candidates:
        lines.append(
            f"{candidate.candidate_id} [{candidate.state.value}] baseline={candidate.baseline_ref} "
            f"suite={candidate.benchmark_suite_ref}"
        )
        lines.append(f"  Name: {candidate.name}")
        lines.append(f"  Artifact: {candidate.artifact_path}")
        lines.append(
            "  Changed surfaces: "
            + ", ".join(f"{item.kind.value}:{item.target}" for item in candidate.changed_surfaces)
        )
    typer.echo("\n".join(lines))


def render_compounding_harness_candidate(report: CompoundingHarnessCandidateReport, *, json_mode: bool) -> None:
    if json_mode:
        _json_output(report.model_dump(mode="json"))
        return
    candidate = report.candidate
    lines = [
        f"Candidate ID: {candidate.candidate_id}",
        f"State: {candidate.state.value}",
        f"Name: {candidate.name}",
        f"Baseline ref: {candidate.baseline_ref}",
        f"Benchmark suite ref: {candidate.benchmark_suite_ref}",
        f"Compounding override: {'yes' if candidate.has_compounding_policy_override else 'no'}",
        f"Created by: {candidate.created_by}",
        f"Artifact: {candidate.artifact_path}",
    ]
    if candidate.reviewer_note is not None:
        lines.append(f"Reviewer note: {candidate.reviewer_note}")
    if candidate.changed_surfaces:
        lines.append("Changed surfaces:")
        lines.extend(f"- {item.kind.value}:{item.target} :: {item.summary}" for item in candidate.changed_surfaces)
    if report.recent_benchmarks:
        lines.append("Recent benchmarks:")
        for benchmark in report.recent_benchmarks:
            lines.append(
                f"- {benchmark.result_id} [{benchmark.status.value}] outcome={benchmark.outcome.value} "
                f"selection_changed={'yes' if benchmark.outcome_summary.selection_changed else 'no'}"
            )
    typer.echo("\n".join(lines))


def render_compounding_harness_benchmarks(report: CompoundingHarnessBenchmarkListReport, *, json_mode: bool) -> None:
    if json_mode:
        _json_output(report.model_dump(mode="json"))
        return
    if not report.benchmarks:
        typer.echo("No compounding harness benchmarks.")
        return
    lines: list[str] = []
    for benchmark in report.benchmarks:
        lines.append(
            f"{benchmark.result_id} [{benchmark.status.value}] candidate={benchmark.candidate_id} "
            f"outcome={benchmark.outcome.value}"
        )
        lines.append(
            f"  Selection changed: {'yes' if benchmark.outcome_summary.selection_changed else 'no'} "
            f"budget_delta={benchmark.cost_summary.budget_delta_characters}"
        )
        lines.append(f"  Result: {benchmark.result_path}")
    typer.echo("\n".join(lines))


def render_compounding_harness_benchmark(report: CompoundingHarnessBenchmarkReport, *, json_mode: bool) -> None:
    if json_mode:
        _json_output(report.model_dump(mode="json"))
        return
    benchmark = report.benchmark
    lines = [
        f"Benchmark ID: {benchmark.result_id}",
        f"Candidate ID: {benchmark.candidate_id}",
        f"Status: {benchmark.status.value}",
        f"Outcome: {benchmark.outcome.value}",
        f"Benchmark suite ref: {benchmark.benchmark_suite_ref}",
        f"Completed at: {benchmark.completed_at.isoformat().replace('+00:00', 'Z')}",
        f"Result: {benchmark.result_path}",
        f"Selection changed: {'yes' if benchmark.outcome_summary.selection_changed else 'no'}",
        f"Changed config fields: {', '.join(benchmark.outcome_summary.changed_config_fields) or 'none'}",
        f"Changed stage bindings: {', '.join(benchmark.outcome_summary.changed_stage_bindings) or 'none'}",
        f"Baseline mode: {benchmark.outcome_summary.baseline_mode_ref}",
        f"Candidate mode: {benchmark.outcome_summary.candidate_mode_ref}",
        f"Summary: {benchmark.outcome_summary.message}",
        (
            "Cost summary: "
            f"baseline={benchmark.cost_summary.baseline_governed_plus_budget_characters} "
            f"candidate={benchmark.cost_summary.candidate_governed_plus_budget_characters} "
            f"delta={benchmark.cost_summary.budget_delta_characters}"
        ),
    ]
    if benchmark.artifact_refs:
        lines.append("Artifacts:")
        lines.extend(f"- {item}" for item in benchmark.artifact_refs)
    typer.echo("\n".join(lines))


def render_compounding_harness_recommendations(
    report: CompoundingHarnessRecommendationListReport,
    *,
    json_mode: bool,
) -> None:
    if json_mode:
        _json_output(report.model_dump(mode="json"))
        return
    if not report.recommendations:
        typer.echo("No compounding harness recommendations.")
        return
    lines: list[str] = []
    for recommendation in report.recommendations:
        lines.append(
            f"{recommendation.recommendation_id} [{recommendation.disposition.value}] "
            f"search={recommendation.search_id}"
        )
        lines.append(f"  Summary: {recommendation.summary}")
        lines.append(f"  Artifact: {recommendation.artifact_path}")
    typer.echo("\n".join(lines))


def render_compounding_harness_recommendation(
    report: CompoundingHarnessRecommendationReport,
    *,
    json_mode: bool,
) -> None:
    if json_mode:
        _json_output(report.model_dump(mode="json"))
        return
    recommendation = report.recommendation
    lines = [
        f"Recommendation ID: {recommendation.recommendation_id}",
        f"Search ID: {recommendation.search_id}",
        f"Disposition: {recommendation.disposition.value}",
        f"Summary: {recommendation.summary}",
        f"Created by: {recommendation.created_by}",
        f"Artifact: {recommendation.artifact_path}",
    ]
    if recommendation.recommended_candidate_id is not None:
        lines.append(f"Recommended candidate: {recommendation.recommended_candidate_id}")
    if recommendation.recommended_result_id is not None:
        lines.append(f"Recommended benchmark: {recommendation.recommended_result_id}")
    if recommendation.candidate_ids:
        lines.append("Candidate ids:")
        lines.extend(f"- {item}" for item in recommendation.candidate_ids)
    if recommendation.benchmark_result_ids:
        lines.append("Benchmark result ids:")
        lines.extend(f"- {item}" for item in recommendation.benchmark_result_ids)
    typer.echo("\n".join(lines))


def render_follow_event(event: EventRecord, *, json_mode: bool) -> None:
    if json_mode:
        typer.echo(event.model_dump_json())
        return
    typer.echo(render_event_record_line(event))


def render_research_report(report: ResearchReport, *, json_mode: bool) -> None:
    if json_mode:
        _json_output(report.model_dump(mode="json"))
        return
    typer.echo("\n".join(_research_report_lines(report)))
