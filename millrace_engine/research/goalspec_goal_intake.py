"""GoalSpec goal-intake stage executor."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from ..markdown import write_text_atomic
from ..paths import RuntimePaths
from .goalspec import GOALSPEC_ARTIFACT_SCHEMA_VERSION, GoalIntakeExecutionResult, GoalIntakeRecord
from .goalspec_helpers import (
    _FRONTMATTER_BOUNDARY,
    _archive_filename_for_execution,
    _first_paragraph,
    _isoformat_z,
    _markdown_section,
    _relative_path,
    _slugify,
    _utcnow,
    _write_json_model,
    resolve_goal_source,
)
from .state import ResearchCheckpoint, ResearchQueueFamily, ResearchQueueOwnership

_GOAL_INTAKE_STAGE_CONTRACT_PATH = "agents/_goal_intake.md"


def execute_goal_intake(
    paths: RuntimePaths,
    checkpoint: ResearchCheckpoint,
    *,
    run_id: str,
    emitted_at: datetime | None = None,
) -> GoalIntakeExecutionResult:
    """Normalize one queued goal into a durable staged idea plus runtime record."""

    emitted_at = emitted_at or _utcnow()
    source = resolve_goal_source(paths, checkpoint)
    source_path = Path(source.source_path)
    staged_slug = _slugify(source.title)
    research_brief_path = paths.ideas_staging_dir / f"{source.idea_id}__{staged_slug}.md"
    archived_source_path = ""
    canonical_source_path = source.current_artifact_relative_path
    if source_path.parent == paths.ideas_raw_dir and source_path != research_brief_path:
        archive_dir = paths.ideas_archive_dir / source_path.parent.name
        archive_dir.mkdir(parents=True, exist_ok=True)
        archived_path = archive_dir / _archive_filename_for_execution(
            source_path,
            run_id=run_id,
            checksum_sha256=source.checksum_sha256,
        )
        archived_source_path = _relative_path(archived_path, relative_to=paths.root)
        canonical_source_path = archived_source_path

    summary = _first_paragraph(source.body) or source.title
    problem_statement = _markdown_section(source.body, "Problem Statement") or summary
    scope = _markdown_section(
        source.body, "Scope"
    ) or "Preserve the queued goal scope for downstream spec synthesis."
    constraints = _markdown_section(
        source.body, "Constraints"
    ) or "No additional constraints were extracted during deterministic Goal Intake."
    unknowns = _markdown_section(
        source.body, "Unknowns Ledger"
    ) or "Downstream GoalSpec stages still need to refine acceptance details and decomposition boundaries."
    evidence = _markdown_section(source.body, "Evidence") or "No additional product evidence was provided."
    route_decision = (
        "Ready for staging now. "
        "Remaining assumptions are preserved explicitly in the unknowns ledger for Objective Profile Sync and later spec synthesis."
    )

    frontmatter_lines = [
        _FRONTMATTER_BOUNDARY,
        f"idea_id: {source.idea_id}",
        f"title: {source.title}",
        "status: staging",
        f"updated_at: {_isoformat_z(emitted_at)}",
        f"decomposition_profile: {source.decomposition_profile}",
        f"goal_intake_run_id: {run_id}",
        f"source_path: {source.relative_source_path}",
        f"canonical_source_path: {canonical_source_path}",
        f"source_checksum_sha256: {source.checksum_sha256}",
        f"trace_source_artifact_path: {source.relative_source_path}",
        f"trace_stage_contract_path: {_GOAL_INTAKE_STAGE_CONTRACT_PATH}",
        f"artifact_schema_version: {GOALSPEC_ARTIFACT_SCHEMA_VERSION}",
        _FRONTMATTER_BOUNDARY,
        "",
    ]
    body_lines = [
        "## Summary",
        summary,
        "",
        "## Problem Statement",
        problem_statement,
        "",
        "## Scope",
        scope,
        "",
        "## Constraints",
        constraints,
        "",
        "## Unknowns Ledger",
        unknowns,
        "",
        "## Evidence",
        evidence,
        "",
        "## Route Decision",
        route_decision,
        "",
    ]
    research_brief_path.parent.mkdir(parents=True, exist_ok=True)
    write_text_atomic(research_brief_path, "\n".join(frontmatter_lines + body_lines))

    if source_path.parent == paths.ideas_raw_dir and source_path != research_brief_path:
        archived_path = paths.root / archived_source_path
        source_path.replace(archived_path)

    record = GoalIntakeRecord(
        run_id=run_id,
        emitted_at=emitted_at,
        canonical_source_path=canonical_source_path,
        current_artifact_path=_relative_path(research_brief_path, relative_to=paths.root),
        source_path=source.relative_source_path,
        archived_source_path=archived_source_path,
        research_brief_path=_relative_path(research_brief_path, relative_to=paths.root),
        idea_id=source.idea_id,
        title=source.title,
        decomposition_profile=source.decomposition_profile,
        source_checksum_sha256=source.checksum_sha256,
    )
    record_path = paths.goalspec_goal_intake_records_dir / f"{run_id}.json"
    _write_json_model(record_path, record)

    return GoalIntakeExecutionResult(
        record_path=_relative_path(record_path, relative_to=paths.root),
        archived_source_path=archived_source_path,
        research_brief_path=_relative_path(research_brief_path, relative_to=paths.root),
        queue_ownership=ResearchQueueOwnership(
            family=ResearchQueueFamily.GOALSPEC,
            queue_path=paths.ideas_staging_dir,
            item_path=research_brief_path,
            owner_token=run_id,
            acquired_at=emitted_at,
        ),
    )
