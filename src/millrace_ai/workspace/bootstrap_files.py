"""Default runtime files for newly initialized workspaces."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from millrace_ai.config import render_bootstrap_runtime_config
from millrace_ai.contracts import Plane, RecoveryCounters, RuntimeMode, RuntimeSnapshot, WatcherMode

from .paths import WorkspacePaths

_IDLE_MARKER = "### IDLE\n"
MILLRACE_SHARED_INSTRUCTIONS_TEMPLATE = """# MILLRACE.md

## Purpose

Shared workspace instructions for all Millrace stage agents.

## Authority

- Follow the current runtime request and stage entrypoint first.
- Do not mutate runtime-owned queue, lifecycle, status, snapshot, closure,
  Blueprint, mailbox, approval, or event state directly.
- Write stage artifacts only to the run directory or request-provided artifact
  paths.

## Workspace Map

- Use `millrace-agents/workspace-map/index.md` as the starting repo map.
- Treat `workspace-map/generated/` as generated output. Do not hand-edit it.
- Treat `workspace-map/wiki/` as curated workspace knowledge.
- Updater may edit only wiki pages relevant to completed work.

## History Log

- Do not edit canonical history logs directly unless the request explicitly
  grants that responsibility.
- Prefer writing `history_entry.json` in the current run directory.
- Do not write `history_entry.md` in M1. The runtime history appender accepts
  JSON only so validation and idempotency remain deterministic.
- The runtime may append accepted history entries into
  `millrace-agents/history-log/`.

## Artifacts

- Keep Millrace runtime/control/generated workflow artifacts under
  `millrace-agents/`.
- Keep source-code changes in the project source tree when the task requires
  source edits.
- Do not create unmanaged root-level runtime folders.

## Workspace-Specific Rules

Operator-maintained. Agents must read but not modify this section unless the
operator explicitly instructs them to update shared workspace instructions.
"""
_WORKSPACE_MAP_INDEX = """# Workspace Map

Start here for generated repo structure and curated workspace knowledge.

- Generated map: `generated/`
- Curated wiki: `wiki/`
- Snapshots: `snapshots/`
"""
_WORKSPACE_MAP_WIKI_INDEX = """# Workspace Map Wiki

Curated workspace knowledge owned by operators and Updater.
"""
_WORKSPACE_MAP_WIKI_RUNTIME = """# Runtime

Curated notes about runtime engine behavior, lifecycle, and operational seams.
"""
_WORKSPACE_MAP_WIKI_COMPILER = """# Compiler

Curated notes about plan compilation, graph inputs, and stale-plan handling.
"""
_WORKSPACE_MAP_WIKI_WORKSPACE = """# Workspace

Curated notes about workspace layout, bootstrap, baseline, and upgrade behavior.
"""
_WORKSPACE_MAP_WIKI_RUNNERS = """# Runners

Curated notes about runner adapters, stage invocation, and prompt boundaries.
"""
_WORKSPACE_MAP_WIKI_ASSETS = """# Assets

Curated notes about packaged runtime assets and deployment expectations.
"""
_WORKSPACE_MAP_WIKI_CLI = """# CLI

Curated notes about command-line surfaces and operator workflows.
"""
_WORKSPACE_MAP_WIKI_CONTRACTS = """# Contracts

Curated notes about runtime contracts, schemas, and compatibility rules.
"""
_WORKSPACE_MAP_WIKI_INVARIANTS = """# Invariants

Curated workspace invariants that should remain true across changes.
"""
_WORKSPACE_MAP_WIKI_GLOSSARY = """# Glossary

Curated definitions for workspace-specific terms and abbreviations.
"""
_WORKSPACE_MAP_WIKI_MAINTENANCE_NOTES = """# Maintenance Notes

Curated notes for ongoing workspace-map maintenance.
"""
_HISTORY_LOG_INDEX = """# History Log

Runtime-owned workspace history surfaces.
"""
_HISTORY_LOG_LATEST = """# Latest History

No rendered history entries yet.
"""


def default_file_payloads(paths: WorkspacePaths) -> dict[Path, str]:
    """Return default file payloads for bootstrap-created workspace files."""

    return {
        paths.shared_instruction_file: MILLRACE_SHARED_INSTRUCTIONS_TEMPLATE,
        paths.workspace_map_index_file: _WORKSPACE_MAP_INDEX,
        paths.workspace_map_manifest_file: '{\n  "schema_version": "1.0",\n  "status": "uninitialized"\n}\n',
        paths.workspace_map_wiki_index_file: _WORKSPACE_MAP_WIKI_INDEX,
        paths.workspace_map_wiki_runtime_file: _WORKSPACE_MAP_WIKI_RUNTIME,
        paths.workspace_map_wiki_compiler_file: _WORKSPACE_MAP_WIKI_COMPILER,
        paths.workspace_map_wiki_workspace_file: _WORKSPACE_MAP_WIKI_WORKSPACE,
        paths.workspace_map_wiki_runners_file: _WORKSPACE_MAP_WIKI_RUNNERS,
        paths.workspace_map_wiki_assets_file: _WORKSPACE_MAP_WIKI_ASSETS,
        paths.workspace_map_wiki_cli_file: _WORKSPACE_MAP_WIKI_CLI,
        paths.workspace_map_wiki_contracts_file: _WORKSPACE_MAP_WIKI_CONTRACTS,
        paths.workspace_map_wiki_invariants_file: _WORKSPACE_MAP_WIKI_INVARIANTS,
        paths.workspace_map_wiki_glossary_file: _WORKSPACE_MAP_WIKI_GLOSSARY,
        paths.workspace_map_wiki_maintenance_notes_file: _WORKSPACE_MAP_WIKI_MAINTENANCE_NOTES,
        paths.workspace_map_generated_file_tree_file: "",
        paths.workspace_map_generated_repo_map_file: "",
        paths.workspace_map_generated_symbols_file: "",
        paths.workspace_map_generated_imports_file: "{}\n",
        paths.workspace_map_generated_reverse_imports_file: "{}\n",
        paths.workspace_map_generated_public_api_file: "",
        paths.workspace_map_generated_tests_map_file: "",
        paths.workspace_map_generated_docs_references_file: "",
        paths.workspace_map_generated_freshness_file: '{\n  "status": "uninitialized"\n}\n',
        paths.history_log_index_file: _HISTORY_LOG_INDEX,
        paths.history_log_latest_file: _HISTORY_LOG_LATEST,
        paths.outline_file: "",
        paths.historylog_file: "",
        paths.runtime_root / "millrace.toml": render_bootstrap_runtime_config(),
        paths.execution_status_file: _IDLE_MARKER,
        paths.planning_status_file: _IDLE_MARKER,
        paths.learning_status_file: _IDLE_MARKER,
        paths.learning_events_file: "",
        paths.runtime_snapshot_file: _default_runtime_snapshot_payload(paths),
        paths.recovery_counters_file: _default_recovery_counters_payload(),
    }


def _default_runtime_snapshot_payload(paths: WorkspacePaths) -> str:
    snapshot = RuntimeSnapshot(
        runtime_mode=RuntimeMode.DAEMON,
        process_running=False,
        paused=False,
        active_mode_id="lad_codex",
        execution_loop_id="execution.lad",
        planning_loop_id="planning.lad",
        loop_ids_by_plane={
            Plane.EXECUTION: "execution.lad",
            Plane.PLANNING: "planning.lad",
        },
        compiled_plan_id="bootstrap",
        compiled_plan_fingerprint="bootstrap",
        compiled_plan_path=str((paths.state_dir / "compiled_plan.json").relative_to(paths.root)),
        execution_status_marker=_IDLE_MARKER.strip(),
        planning_status_marker=_IDLE_MARKER.strip(),
        learning_status_marker=_IDLE_MARKER.strip(),
        status_markers_by_plane={
            Plane.EXECUTION: _IDLE_MARKER.strip(),
            Plane.PLANNING: _IDLE_MARKER.strip(),
            Plane.LEARNING: _IDLE_MARKER.strip(),
        },
        queue_depths_by_plane={
            Plane.EXECUTION: 0,
            Plane.PLANNING: 0,
            Plane.LEARNING: 0,
        },
        config_version="bootstrap",
        watcher_mode=WatcherMode.OFF,
        updated_at=datetime.now(timezone.utc),
    )
    return snapshot.model_dump_json(indent=2) + "\n"


def _default_recovery_counters_payload() -> str:
    counters = RecoveryCounters()
    return counters.model_dump_json(indent=2) + "\n"


__all__ = ["MILLRACE_SHARED_INSTRUCTIONS_TEMPLATE", "default_file_payloads"]
