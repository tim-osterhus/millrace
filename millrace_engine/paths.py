"""Canonical runtime path model."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import re


def _slugify_historylog_part(value: str, *, max_length: int | None = None) -> str:
    text = value.strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    slug = text or "unknown"
    if max_length is None or len(slug) <= max_length:
        return slug
    digest = hashlib.sha1(slug.encode("utf-8")).hexdigest()[:10]
    head = slug[: max_length - len(digest) - 1].rstrip("-")
    return f"{head or 'unknown'}-{digest}"


def format_historylog_entry_name(timestamp: datetime, *, stage: str, task: str) -> str:
    """Return the canonical UTC history-entry filename."""

    moment = timestamp if timestamp.tzinfo is not None else timestamp.replace(tzinfo=timezone.utc)
    timestamp_text = moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
    return (
        f"{timestamp_text}"
        f"__stage-{_slugify_historylog_part(stage, max_length=32)}"
        f"__task-{_slugify_historylog_part(task, max_length=96)}.md"
    )


@dataclass(frozen=True, slots=True)
class RuntimePaths:
    """Resolved runtime paths rooted at the target workspace."""

    root: Path
    agents_dir: Path
    ideas_dir: Path
    ideas_raw_dir: Path
    ideas_staging_dir: Path
    ideas_specs_dir: Path
    ideas_specs_reviewed_dir: Path
    ideas_archive_dir: Path
    reports_dir: Path
    acceptance_profiles_dir: Path
    completion_manifest_plan_file: Path
    staging_manifest_file: Path
    queue_governor_report_file: Path
    governance_canary_report_file: Path
    drift_status_report_file: Path
    tmp_dir: Path
    objective_dir: Path
    objective_contract_file: Path
    objective_profile_sync_state_file: Path
    audit_dir: Path
    audit_strict_contract_file: Path
    audit_completion_manifest_file: Path
    objective_family_policy_file: Path
    policies_dir: Path
    drift_control_policy_file: Path
    governance_canary_baseline_policy_file: Path
    research_runtime_dir: Path
    compounding_dir: Path
    compounding_procedures_dir: Path
    compounding_context_facts_dir: Path
    compounding_usage_records_dir: Path
    compounding_lifecycle_records_dir: Path
    compounding_harness_candidates_dir: Path
    compounding_harness_candidate_assets_dir: Path
    compounding_benchmark_results_dir: Path
    compounding_harness_search_requests_dir: Path
    compounding_harness_recommendations_dir: Path
    lab_dir: Path
    lab_harness_requests_dir: Path
    lab_harness_proposals_dir: Path
    lab_harness_candidate_assets_dir: Path
    lab_harness_comparisons_dir: Path
    goal_spec_family_state_file: Path
    goalspec_runtime_dir: Path
    goalspec_goal_intake_records_dir: Path
    goalspec_objective_profile_sync_records_dir: Path
    goalspec_completion_manifest_records_dir: Path
    goalspec_spec_synthesis_records_dir: Path
    goalspec_spec_interview_records_dir: Path
    goalspec_spec_review_records_dir: Path
    goalspec_lineage_dir: Path
    specs_dir: Path
    specs_index_file: Path
    specs_stable_dir: Path
    specs_stable_golden_dir: Path
    specs_stable_phase_dir: Path
    specs_questions_dir: Path
    specs_decisions_dir: Path
    status_file: Path
    research_status_file: Path
    size_status_file: Path
    tasks_file: Path
    backlog_file: Path
    archive_file: Path
    backburner_file: Path
    blocker_file: Path
    taskspending_file: Path
    staging_repo_dir: Path
    historylog_file: Path
    historylog_dir: Path
    engine_events_log: Path
    research_state_file: Path
    runs_dir: Path
    diagnostics_dir: Path
    runtime_dir: Path
    commands_incoming_dir: Path
    commands_processed_dir: Path
    commands_failed_dir: Path
    queue_lock_file: Path
    deferred_dir: Path
    research_recovery_latch_file: Path
    progress_watchdog_state_file: Path
    progress_watchdog_report_file: Path
    incident_recurrence_ledger_file: Path

    @classmethod
    def from_workspace(cls, workspace_root: Path, agents_dir: Path) -> "RuntimePaths":
        root = workspace_root.expanduser().resolve()
        if agents_dir.is_absolute():
            resolved_agents_dir = agents_dir.resolve()
        else:
            resolved_agents_dir = (root / agents_dir).resolve()
        runtime_dir = resolved_agents_dir / ".runtime"
        commands_dir = runtime_dir / "commands"
        ideas_dir = resolved_agents_dir / "ideas"
        reports_dir = resolved_agents_dir / "reports"
        tmp_dir = resolved_agents_dir / ".tmp"
        objective_dir = resolved_agents_dir / "objective"
        audit_dir = resolved_agents_dir / "audit"
        policies_dir = resolved_agents_dir / "policies"
        research_runtime_dir = resolved_agents_dir / ".research_runtime"
        compounding_dir = resolved_agents_dir / "compounding"
        lab_dir = resolved_agents_dir / "lab"
        goalspec_runtime_dir = research_runtime_dir / "goalspec"
        specs_dir = resolved_agents_dir / "specs"
        specs_stable_dir = specs_dir / "stable"
        return cls(
            root=root,
            agents_dir=resolved_agents_dir,
            ideas_dir=ideas_dir,
            ideas_raw_dir=ideas_dir / "raw",
            ideas_staging_dir=ideas_dir / "staging",
            ideas_specs_dir=ideas_dir / "specs",
            ideas_specs_reviewed_dir=ideas_dir / "specs_reviewed",
            ideas_archive_dir=ideas_dir / "archive",
            reports_dir=reports_dir,
            acceptance_profiles_dir=reports_dir / "acceptance_profiles",
            completion_manifest_plan_file=reports_dir / "completion_manifest_plan.md",
            staging_manifest_file=resolved_agents_dir / "staging_manifest.yml",
            queue_governor_report_file=reports_dir / "queue_governor.json",
            governance_canary_report_file=reports_dir / "governance_canary.json",
            drift_status_report_file=reports_dir / "drift_status.json",
            tmp_dir=tmp_dir,
            objective_dir=objective_dir,
            objective_contract_file=objective_dir / "contract.yaml",
            objective_profile_sync_state_file=objective_dir / "profile_sync_state.json",
            audit_dir=audit_dir,
            audit_strict_contract_file=audit_dir / "strict_contract.json",
            audit_completion_manifest_file=audit_dir / "completion_manifest.json",
            objective_family_policy_file=objective_dir / "family_policy.json",
            policies_dir=policies_dir,
            drift_control_policy_file=policies_dir / "drift_control_policy.json",
            governance_canary_baseline_policy_file=policies_dir / "drift_control_policy.baseline.json",
            research_runtime_dir=research_runtime_dir,
            compounding_dir=compounding_dir,
            compounding_procedures_dir=compounding_dir / "procedures",
            compounding_context_facts_dir=compounding_dir / "context_facts",
            compounding_usage_records_dir=compounding_dir / "usage",
            compounding_lifecycle_records_dir=compounding_dir / "lifecycle",
            compounding_harness_candidates_dir=compounding_dir / "harness_candidates",
            compounding_harness_candidate_assets_dir=compounding_dir / "harness_candidate_assets",
            compounding_benchmark_results_dir=compounding_dir / "benchmark_results",
            compounding_harness_search_requests_dir=compounding_dir / "harness_searches",
            compounding_harness_recommendations_dir=compounding_dir / "harness_recommendations",
            lab_dir=lab_dir,
            lab_harness_requests_dir=lab_dir / "harness_requests",
            lab_harness_proposals_dir=lab_dir / "harness_proposals",
            lab_harness_candidate_assets_dir=lab_dir / "harness_candidate_assets",
            lab_harness_comparisons_dir=lab_dir / "harness_comparisons",
            goal_spec_family_state_file=research_runtime_dir / "spec_family_state.json",
            goalspec_runtime_dir=goalspec_runtime_dir,
            goalspec_goal_intake_records_dir=goalspec_runtime_dir / "goal_intake",
            goalspec_objective_profile_sync_records_dir=goalspec_runtime_dir / "objective_profile_sync",
            goalspec_completion_manifest_records_dir=goalspec_runtime_dir / "completion_manifest",
            goalspec_spec_synthesis_records_dir=goalspec_runtime_dir / "spec_synthesis",
            goalspec_spec_interview_records_dir=goalspec_runtime_dir / "spec_interview",
            goalspec_spec_review_records_dir=goalspec_runtime_dir / "spec_review",
            goalspec_lineage_dir=goalspec_runtime_dir / "lineage",
            specs_dir=specs_dir,
            specs_index_file=specs_dir / "index.json",
            specs_stable_dir=specs_stable_dir,
            specs_stable_golden_dir=specs_stable_dir / "golden",
            specs_stable_phase_dir=specs_stable_dir / "phase",
            specs_questions_dir=specs_dir / "questions",
            specs_decisions_dir=specs_dir / "decisions",
            status_file=resolved_agents_dir / "status.md",
            research_status_file=resolved_agents_dir / "research_status.md",
            size_status_file=resolved_agents_dir / "size_status.md",
            tasks_file=resolved_agents_dir / "tasks.md",
            backlog_file=resolved_agents_dir / "tasksbacklog.md",
            archive_file=resolved_agents_dir / "tasksarchive.md",
            backburner_file=resolved_agents_dir / "tasksbackburner.md",
            blocker_file=resolved_agents_dir / "tasksblocker.md",
            taskspending_file=resolved_agents_dir / "taskspending.md",
            staging_repo_dir=root / "staging",
            historylog_file=resolved_agents_dir / "historylog.md",
            historylog_dir=resolved_agents_dir / "historylog",
            engine_events_log=resolved_agents_dir / "engine_events.log",
            research_state_file=resolved_agents_dir / "research_state.json",
            runs_dir=resolved_agents_dir / "runs",
            diagnostics_dir=resolved_agents_dir / "diagnostics",
            runtime_dir=runtime_dir,
            commands_incoming_dir=commands_dir / "incoming",
            commands_processed_dir=commands_dir / "processed",
            commands_failed_dir=commands_dir / "failed",
            queue_lock_file=resolved_agents_dir / ".locks" / "queue.lock",
            deferred_dir=resolved_agents_dir / ".deferred",
            research_recovery_latch_file=runtime_dir / "research_recovery_latch.json",
            progress_watchdog_state_file=research_runtime_dir / "progress_watchdog_state.json",
            progress_watchdog_report_file=tmp_dir / "progress_watchdog_report.json",
            incident_recurrence_ledger_file=research_runtime_dir / "incidents" / "recurrence_ledger.json",
        )
