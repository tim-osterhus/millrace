"""Canonical workspace path model."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Union


@dataclass(frozen=True, slots=True)
class WorkspacePaths:
    """Resolved canonical workspace paths rooted at one workspace directory."""

    root: Path
    runtime_root: Path

    state_dir: Path
    mailbox_dir: Path
    mailbox_incoming_dir: Path
    mailbox_processed_dir: Path
    mailbox_failed_dir: Path

    runs_dir: Path

    tasks_dir: Path
    tasks_queue_dir: Path
    tasks_active_dir: Path
    tasks_done_dir: Path
    tasks_blocked_dir: Path

    specs_dir: Path
    specs_queue_dir: Path
    specs_active_dir: Path
    specs_done_dir: Path
    specs_blocked_dir: Path

    incidents_dir: Path
    incidents_incoming_dir: Path
    incidents_active_dir: Path
    incidents_resolved_dir: Path
    incidents_blocked_dir: Path

    probes_dir: Path
    probes_queue_dir: Path
    probes_active_dir: Path
    probes_done_dir: Path
    probes_blocked_dir: Path

    intake_dir: Path
    intake_sources_dir: Path
    intake_sources_idea_dir: Path
    intake_sources_probe_dir: Path
    intake_sources_manual_dir: Path
    intake_sources_spec_dir: Path
    intake_sources_incident_dir: Path
    intake_ideas_dir: Path
    intake_ideas_inbox_dir: Path
    intake_ideas_normalized_dir: Path
    intake_ideas_archived_dir: Path
    intake_ideas_archived_legacy_dir: Path
    intake_ideas_invalid_dir: Path

    recon_dir: Path
    recon_packets_dir: Path
    recon_reports_dir: Path

    learning_dir: Path
    learning_requests_dir: Path
    learning_requests_queue_dir: Path
    learning_requests_active_dir: Path
    learning_requests_done_dir: Path
    learning_requests_blocked_dir: Path
    learning_research_packets_dir: Path
    learning_skill_candidates_dir: Path
    learning_update_candidates_dir: Path
    learning_events_file: Path

    arbiter_dir: Path
    arbiter_contracts_dir: Path
    arbiter_idea_contracts_dir: Path
    arbiter_root_source_contracts_dir: Path
    arbiter_root_spec_contracts_dir: Path
    arbiter_targets_dir: Path
    arbiter_rubrics_dir: Path
    arbiter_verdicts_dir: Path
    arbiter_reports_dir: Path

    loops_dir: Path
    execution_loops_dir: Path
    planning_loops_dir: Path
    learning_loops_dir: Path
    graphs_dir: Path
    execution_graphs_dir: Path
    planning_graphs_dir: Path
    learning_graphs_dir: Path
    registry_dir: Path
    stage_kind_registry_dir: Path
    execution_stage_kind_registry_dir: Path
    planning_stage_kind_registry_dir: Path
    learning_stage_kind_registry_dir: Path

    modes_dir: Path
    logs_dir: Path
    entrypoints_dir: Path
    skills_dir: Path
    templates_dir: Path

    shared_instruction_file: Path
    shared_instruction_candidate_file: Path

    workspace_map_dir: Path
    workspace_map_index_file: Path
    workspace_map_manifest_file: Path
    workspace_map_generated_dir: Path
    workspace_map_generated_file_tree_file: Path
    workspace_map_generated_repo_map_file: Path
    workspace_map_generated_symbols_file: Path
    workspace_map_generated_imports_file: Path
    workspace_map_generated_reverse_imports_file: Path
    workspace_map_generated_public_api_file: Path
    workspace_map_generated_tests_map_file: Path
    workspace_map_generated_docs_references_file: Path
    workspace_map_generated_freshness_file: Path
    workspace_map_wiki_dir: Path
    workspace_map_wiki_index_file: Path
    workspace_map_wiki_domains_dir: Path
    workspace_map_wiki_runtime_file: Path
    workspace_map_wiki_compiler_file: Path
    workspace_map_wiki_workspace_file: Path
    workspace_map_wiki_runners_file: Path
    workspace_map_wiki_assets_file: Path
    workspace_map_wiki_cli_file: Path
    workspace_map_wiki_contracts_file: Path
    workspace_map_wiki_invariants_file: Path
    workspace_map_wiki_glossary_file: Path
    workspace_map_wiki_maintenance_notes_file: Path
    workspace_map_snapshots_dir: Path

    history_log_dir: Path
    history_log_index_file: Path
    history_log_latest_file: Path
    history_log_daily_dir: Path
    history_log_entries_dir: Path

    outline_file: Path
    historylog_file: Path
    execution_status_file: Path
    planning_status_file: Path
    learning_status_file: Path
    baseline_manifest_file: Path
    runtime_snapshot_file: Path
    recovery_counters_file: Path
    runtime_error_context_file: Path
    usage_governance_state_file: Path
    usage_governance_ledger_file: Path
    runtime_lock_file: Path

    runtime_effect_journal_dir: Path

    def directories(self) -> tuple[Path, ...]:
        """Return all directories that must exist for a canonical workspace."""

        return (
            self.runtime_root,
            self.state_dir,
            self.mailbox_dir,
            self.mailbox_incoming_dir,
            self.mailbox_processed_dir,
            self.mailbox_failed_dir,
            self.runs_dir,
            self.tasks_dir,
            self.tasks_queue_dir,
            self.tasks_active_dir,
            self.tasks_done_dir,
            self.tasks_blocked_dir,
            self.specs_dir,
            self.specs_queue_dir,
            self.specs_active_dir,
            self.specs_done_dir,
            self.specs_blocked_dir,
            self.incidents_dir,
            self.incidents_incoming_dir,
            self.incidents_active_dir,
            self.incidents_resolved_dir,
            self.incidents_blocked_dir,
            self.probes_dir,
            self.probes_queue_dir,
            self.probes_active_dir,
            self.probes_done_dir,
            self.probes_blocked_dir,
            self.intake_dir,
            self.intake_sources_dir,
            self.intake_sources_idea_dir,
            self.intake_sources_probe_dir,
            self.intake_sources_manual_dir,
            self.intake_sources_spec_dir,
            self.intake_sources_incident_dir,
            self.intake_ideas_dir,
            self.intake_ideas_inbox_dir,
            self.intake_ideas_normalized_dir,
            self.intake_ideas_archived_dir,
            self.intake_ideas_archived_legacy_dir,
            self.intake_ideas_invalid_dir,
            self.recon_dir,
            self.recon_packets_dir,
            self.recon_reports_dir,
            self.learning_dir,
            self.learning_requests_dir,
            self.learning_requests_queue_dir,
            self.learning_requests_active_dir,
            self.learning_requests_done_dir,
            self.learning_requests_blocked_dir,
            self.learning_research_packets_dir,
            self.learning_skill_candidates_dir,
            self.learning_update_candidates_dir,
            self.arbiter_dir,
            self.arbiter_contracts_dir,
            self.arbiter_idea_contracts_dir,
            self.arbiter_root_source_contracts_dir,
            self.arbiter_root_spec_contracts_dir,
            self.arbiter_targets_dir,
            self.arbiter_rubrics_dir,
            self.arbiter_verdicts_dir,
            self.arbiter_reports_dir,
            self.loops_dir,
            self.execution_loops_dir,
            self.planning_loops_dir,
            self.learning_loops_dir,
            self.graphs_dir,
            self.execution_graphs_dir,
            self.planning_graphs_dir,
            self.learning_graphs_dir,
            self.registry_dir,
            self.stage_kind_registry_dir,
            self.execution_stage_kind_registry_dir,
            self.planning_stage_kind_registry_dir,
            self.learning_stage_kind_registry_dir,
            self.modes_dir,
            self.logs_dir,
            self.entrypoints_dir,
            self.skills_dir,
            self.templates_dir,
            self.workspace_map_dir,
            self.workspace_map_generated_dir,
            self.workspace_map_wiki_dir,
            self.workspace_map_wiki_domains_dir,
            self.workspace_map_snapshots_dir,
            self.history_log_dir,
            self.history_log_daily_dir,
            self.history_log_entries_dir,
            self.runtime_effect_journal_dir,
        )


def workspace_paths(root: Union[str, Path]) -> WorkspacePaths:
    """Resolve canonical workspace paths from a root workspace directory."""

    resolved_root = Path(root).expanduser().resolve()
    runtime_root = resolved_root / "millrace-agents"
    state_dir = runtime_root / "state"
    mailbox_dir = state_dir / "mailbox"
    tasks_dir = runtime_root / "tasks"
    specs_dir = runtime_root / "specs"
    incidents_dir = runtime_root / "incidents"
    probes_dir = runtime_root / "probes"
    intake_dir = runtime_root / "intake"
    recon_dir = runtime_root / "recon"
    learning_dir = runtime_root / "learning"
    learning_requests_dir = learning_dir / "requests"
    arbiter_dir = runtime_root / "arbiter"
    arbiter_contracts_dir = arbiter_dir / "contracts"
    loops_dir = runtime_root / "loops"
    graphs_dir = runtime_root / "graphs"
    registry_dir = runtime_root / "registry"
    stage_kind_registry_dir = registry_dir / "stage_kinds"

    return WorkspacePaths(
        root=resolved_root,
        runtime_root=runtime_root,
        state_dir=state_dir,
        mailbox_dir=mailbox_dir,
        mailbox_incoming_dir=mailbox_dir / "incoming",
        mailbox_processed_dir=mailbox_dir / "processed",
        mailbox_failed_dir=mailbox_dir / "failed",
        runs_dir=runtime_root / "runs",
        tasks_dir=tasks_dir,
        tasks_queue_dir=tasks_dir / "queue",
        tasks_active_dir=tasks_dir / "active",
        tasks_done_dir=tasks_dir / "done",
        tasks_blocked_dir=tasks_dir / "blocked",
        specs_dir=specs_dir,
        specs_queue_dir=specs_dir / "queue",
        specs_active_dir=specs_dir / "active",
        specs_done_dir=specs_dir / "done",
        specs_blocked_dir=specs_dir / "blocked",
        incidents_dir=incidents_dir,
        incidents_incoming_dir=incidents_dir / "incoming",
        incidents_active_dir=incidents_dir / "active",
        incidents_resolved_dir=incidents_dir / "resolved",
        incidents_blocked_dir=incidents_dir / "blocked",
        probes_dir=probes_dir,
        probes_queue_dir=probes_dir / "queue",
        probes_active_dir=probes_dir / "active",
        probes_done_dir=probes_dir / "done",
        probes_blocked_dir=probes_dir / "blocked",
        intake_dir=intake_dir,
        intake_sources_dir=intake_dir / "sources",
        intake_sources_idea_dir=intake_dir / "sources" / "idea",
        intake_sources_probe_dir=intake_dir / "sources" / "probe",
        intake_sources_manual_dir=intake_dir / "sources" / "manual",
        intake_sources_spec_dir=intake_dir / "sources" / "spec",
        intake_sources_incident_dir=intake_dir / "sources" / "incident",
        intake_ideas_dir=intake_dir / "ideas",
        intake_ideas_inbox_dir=intake_dir / "ideas" / "inbox",
        intake_ideas_normalized_dir=intake_dir / "ideas" / "normalized",
        intake_ideas_archived_dir=intake_dir / "ideas" / "archived",
        intake_ideas_archived_legacy_dir=intake_dir / "ideas" / "archived" / "legacy",
        intake_ideas_invalid_dir=intake_dir / "ideas" / "invalid",
        recon_dir=recon_dir,
        recon_packets_dir=recon_dir / "packets",
        recon_reports_dir=recon_dir / "reports",
        learning_dir=learning_dir,
        learning_requests_dir=learning_requests_dir,
        learning_requests_queue_dir=learning_requests_dir / "queue",
        learning_requests_active_dir=learning_requests_dir / "active",
        learning_requests_done_dir=learning_requests_dir / "done",
        learning_requests_blocked_dir=learning_requests_dir / "blocked",
        learning_research_packets_dir=learning_dir / "research-packets",
        learning_skill_candidates_dir=learning_dir / "skill-candidates",
        learning_update_candidates_dir=learning_dir / "update-candidates",
        learning_events_file=learning_dir / "events.jsonl",
        arbiter_dir=arbiter_dir,
        arbiter_contracts_dir=arbiter_contracts_dir,
        arbiter_idea_contracts_dir=arbiter_contracts_dir / "ideas",
        arbiter_root_source_contracts_dir=arbiter_contracts_dir / "root-sources",
        arbiter_root_spec_contracts_dir=arbiter_contracts_dir / "root-specs",
        arbiter_targets_dir=arbiter_dir / "targets",
        arbiter_rubrics_dir=arbiter_dir / "rubrics",
        arbiter_verdicts_dir=arbiter_dir / "verdicts",
        arbiter_reports_dir=arbiter_dir / "reports",
        loops_dir=loops_dir,
        execution_loops_dir=loops_dir / "execution",
        planning_loops_dir=loops_dir / "planning",
        learning_loops_dir=loops_dir / "learning",
        graphs_dir=graphs_dir,
        execution_graphs_dir=graphs_dir / "execution",
        planning_graphs_dir=graphs_dir / "planning",
        learning_graphs_dir=graphs_dir / "learning",
        registry_dir=registry_dir,
        stage_kind_registry_dir=stage_kind_registry_dir,
        execution_stage_kind_registry_dir=stage_kind_registry_dir / "execution",
        planning_stage_kind_registry_dir=stage_kind_registry_dir / "planning",
        learning_stage_kind_registry_dir=stage_kind_registry_dir / "learning",
        modes_dir=runtime_root / "modes",
        logs_dir=runtime_root / "logs",
        entrypoints_dir=runtime_root / "entrypoints",
        skills_dir=runtime_root / "skills",
        templates_dir=runtime_root / "templates",
        shared_instruction_file=runtime_root / "MILLRACE.md",
        shared_instruction_candidate_file=runtime_root / "templates" / "MILLRACE.md.candidate",
        workspace_map_dir=runtime_root / "workspace-map",
        workspace_map_index_file=runtime_root / "workspace-map" / "index.md",
        workspace_map_manifest_file=runtime_root / "workspace-map" / "manifest.json",
        workspace_map_generated_dir=runtime_root / "workspace-map" / "generated",
        workspace_map_generated_file_tree_file=runtime_root / "workspace-map" / "generated" / "file-tree.md",
        workspace_map_generated_repo_map_file=runtime_root / "workspace-map" / "generated" / "repo-map.compact.md",
        workspace_map_generated_symbols_file=runtime_root / "workspace-map" / "generated" / "symbols.jsonl",
        workspace_map_generated_imports_file=runtime_root / "workspace-map" / "generated" / "imports.json",
        workspace_map_generated_reverse_imports_file=runtime_root / "workspace-map" / "generated" / "reverse-imports.json",
        workspace_map_generated_public_api_file=runtime_root / "workspace-map" / "generated" / "public-api.jsonl",
        workspace_map_generated_tests_map_file=runtime_root / "workspace-map" / "generated" / "tests-map.jsonl",
        workspace_map_generated_docs_references_file=runtime_root / "workspace-map" / "generated" / "docs-references.jsonl",
        workspace_map_generated_freshness_file=runtime_root / "workspace-map" / "generated" / "freshness.json",
        workspace_map_wiki_dir=runtime_root / "workspace-map" / "wiki",
        workspace_map_wiki_index_file=runtime_root / "workspace-map" / "wiki" / "index.md",
        workspace_map_wiki_domains_dir=runtime_root / "workspace-map" / "wiki" / "domains",
        workspace_map_wiki_runtime_file=runtime_root / "workspace-map" / "wiki" / "domains" / "runtime.md",
        workspace_map_wiki_compiler_file=runtime_root / "workspace-map" / "wiki" / "domains" / "compiler.md",
        workspace_map_wiki_workspace_file=runtime_root / "workspace-map" / "wiki" / "domains" / "workspace.md",
        workspace_map_wiki_runners_file=runtime_root / "workspace-map" / "wiki" / "domains" / "runners.md",
        workspace_map_wiki_assets_file=runtime_root / "workspace-map" / "wiki" / "domains" / "assets.md",
        workspace_map_wiki_cli_file=runtime_root / "workspace-map" / "wiki" / "domains" / "cli.md",
        workspace_map_wiki_contracts_file=runtime_root / "workspace-map" / "wiki" / "domains" / "contracts.md",
        workspace_map_wiki_invariants_file=runtime_root / "workspace-map" / "wiki" / "invariants.md",
        workspace_map_wiki_glossary_file=runtime_root / "workspace-map" / "wiki" / "glossary.md",
        workspace_map_wiki_maintenance_notes_file=runtime_root / "workspace-map" / "wiki" / "maintenance-notes.md",
        workspace_map_snapshots_dir=runtime_root / "workspace-map" / "snapshots",
        history_log_dir=runtime_root / "history-log",
        history_log_index_file=runtime_root / "history-log" / "index.md",
        history_log_latest_file=runtime_root / "history-log" / "latest.md",
        history_log_daily_dir=runtime_root / "history-log" / "daily",
        history_log_entries_dir=runtime_root / "history-log" / "entries",
        outline_file=runtime_root / "outline.md",
        historylog_file=runtime_root / "historylog.md",
        execution_status_file=state_dir / "execution_status.md",
        planning_status_file=state_dir / "planning_status.md",
        learning_status_file=state_dir / "learning_status.md",
        baseline_manifest_file=state_dir / "baseline_manifest.json",
        runtime_snapshot_file=state_dir / "runtime_snapshot.json",
        recovery_counters_file=state_dir / "recovery_counters.json",
        runtime_error_context_file=state_dir / "runtime_error_context.json",
        usage_governance_state_file=state_dir / "usage_governance_state.json",
        usage_governance_ledger_file=state_dir / "usage_governance_ledger.jsonl",
        runtime_lock_file=state_dir / "runtime_daemon.lock.json",
        runtime_effect_journal_dir=state_dir / "runtime-effect-journal",
    )


__all__ = ["WorkspacePaths", "workspace_paths"]
