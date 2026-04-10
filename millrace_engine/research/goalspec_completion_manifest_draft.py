"""GoalSpec completion-manifest-draft stage executor."""

from __future__ import annotations

from datetime import datetime

from ..markdown import write_text_atomic
from ..paths import RuntimePaths
from .goalspec import (
    CompletionManifestDraftExecutionResult,
    CompletionManifestDraftRecord,
    CompletionManifestDraftStateRecord,
    GoalSource,
)
from .goalspec_helpers import (
    _load_json_model,
    _relative_path,
    _spec_id_for_goal,
    _utcnow,
    _write_json_model,
    resolve_goal_source,
)
from .goalspec_persistence import (
    _build_completion_manifest_draft_state,
    _load_objective_profile_inputs,
)
from .state import ResearchCheckpoint


def _render_completion_manifest_report(
    *,
    run_id: str,
    source: GoalSource,
    draft_state: CompletionManifestDraftStateRecord,
) -> str:
    contractor_lines = []
    if draft_state.contractor_shape_class:
        contractor_lines.extend(
            [
                "## Contractor Grounding",
                f"- **Contractor-Profile:** `{draft_state.contractor_profile_path or 'none'}`",
                f"- **Shape-Class:** `{draft_state.contractor_shape_class}`",
                f"- **Specificity-Level:** `{draft_state.contractor_specificity_level or 'unknown'}`",
                f"- **Fallback-Mode:** `{draft_state.contractor_fallback_mode or 'unknown'}`",
            ]
        )
        if draft_state.contractor_capability_hints:
            contractor_lines.append(
                "- **Capability-Hints:** "
                + ", ".join(f"`{item}`" for item in draft_state.contractor_capability_hints)
            )
        if draft_state.contractor_environment_hints:
            contractor_lines.append(
                "- **Environment-Hints:** "
                + ", ".join(f"`{item}`" for item in draft_state.contractor_environment_hints)
            )
        if draft_state.contractor_unresolved_specializations:
            contractor_lines.append(
                "- **Unresolved-Specializations:** "
                + ", ".join(f"`{item}`" for item in draft_state.contractor_unresolved_specializations)
            )
        if draft_state.contractor_abstentions:
            contractor_lines.extend(
                [
                    "- **Abstentions:**",
                    *(f"  - {item}" for item in draft_state.contractor_abstentions),
                ]
            )
        if draft_state.contractor_contradictions:
            contractor_lines.extend(
                [
                    "- **Contradictions:**",
                    *(f"  - {item}" for item in draft_state.contractor_contradictions),
                ]
            )
        contractor_lines.append("")

    return "\n".join(
        [
            "# Completion Manifest Draft",
            "",
            f"- **Run-ID:** {run_id}",
            f"- **Goal-ID:** {source.idea_id}",
            f"- **Title:** {source.title}",
            f"- **Source-Path:** `{source.relative_source_path}`",
            f"- **Planning-Profile:** `{draft_state.planning_profile}`",
            "",
            "## Acceptance Focus",
            *(f"- {item}" for item in draft_state.acceptance_focus),
            "",
            "## Required Artifacts",
            *(
                f"- `{artifact.artifact_kind}`: `{artifact.path}` ({artifact.purpose})"
                for artifact in draft_state.required_artifacts
            ),
            "",
            "## Implementation Surfaces",
            *(
                f"- `{surface.surface_kind}`: `{surface.path}` ({surface.purpose})"
                for surface in draft_state.implementation_surfaces
            ),
            "",
            "## Verification Surfaces",
            *(
                f"- `{surface.surface_kind}`: `{surface.path}` ({surface.purpose})"
                for surface in draft_state.verification_surfaces
            ),
            "",
            *contractor_lines,
            "## Open Questions",
            *(f"- {item}" for item in draft_state.open_questions),
            "",
        ]
    )


def _render_completion_manifest_record(
    *,
    emitted_at: datetime,
    run_id: str,
    source: GoalSource,
    objective_profile_path: str,
    draft_path: str,
    report_path: str,
) -> CompletionManifestDraftRecord:
    return CompletionManifestDraftRecord(
        run_id=run_id,
        emitted_at=emitted_at,
        goal_id=source.idea_id,
        title=source.title,
        canonical_source_path=source.canonical_relative_source_path,
        current_artifact_path=source.current_artifact_relative_path,
        source_path=source.canonical_relative_source_path,
        research_brief_path=source.current_artifact_relative_path,
        draft_path=draft_path,
        report_path=report_path,
        objective_profile_path=objective_profile_path,
    )


def execute_completion_manifest_draft(
    paths: RuntimePaths,
    checkpoint: ResearchCheckpoint,
    *,
    run_id: str,
    emitted_at: datetime | None = None,
) -> CompletionManifestDraftExecutionResult:
    """Draft the durable completion-manifest state needed before spec synthesis."""

    emitted_at = emitted_at or _utcnow()
    source = resolve_goal_source(paths, checkpoint)
    objective_state, profile = _load_objective_profile_inputs(paths)
    spec_id = _spec_id_for_goal(source.idea_id)
    record_path = paths.goalspec_completion_manifest_records_dir / f"{run_id}.json"
    draft_path = _relative_path(paths.audit_completion_manifest_file, relative_to=paths.root)
    report_path = _relative_path(paths.completion_manifest_plan_file, relative_to=paths.root)
    draft_state = _build_completion_manifest_draft_state(
        emitted_at=emitted_at,
        run_id=run_id,
        source=source,
        objective_state=objective_state,
        profile=profile,
        spec_id=spec_id,
        paths=paths,
    )
    if (
        record_path.exists()
        and paths.audit_completion_manifest_file.exists()
        and paths.completion_manifest_plan_file.exists()
    ):
        existing_record = _load_json_model(record_path, CompletionManifestDraftRecord)
        existing_draft_state = _load_json_model(
            paths.audit_completion_manifest_file,
            CompletionManifestDraftStateRecord,
        )
        expected_draft_state = draft_state.model_copy(update={"updated_at": existing_draft_state.updated_at})
        expected_record = _render_completion_manifest_record(
            emitted_at=existing_record.emitted_at,
            run_id=run_id,
            source=source,
            objective_profile_path=objective_state.profile_path,
            draft_path=draft_path,
            report_path=report_path,
        )
        expected_report = _render_completion_manifest_report(
            run_id=run_id,
            source=source,
            draft_state=expected_draft_state,
        )
        if (
            existing_record == expected_record
            and existing_draft_state == expected_draft_state
            and paths.completion_manifest_plan_file.read_text(encoding="utf-8") == expected_report
        ):
            return CompletionManifestDraftExecutionResult(
                record_path=_relative_path(record_path, relative_to=paths.root),
                draft_path=draft_path,
                report_path=report_path,
                objective_profile_path=objective_state.profile_path,
                draft_state=existing_draft_state,
            )

    _write_json_model(paths.audit_completion_manifest_file, draft_state)
    write_text_atomic(
        paths.completion_manifest_plan_file,
        _render_completion_manifest_report(
            run_id=run_id,
            source=source,
            draft_state=draft_state,
        ),
    )

    record = _render_completion_manifest_record(
        emitted_at=emitted_at,
        run_id=run_id,
        source=source,
        objective_profile_path=objective_state.profile_path,
        draft_path=draft_path,
        report_path=report_path,
    )
    _write_json_model(record_path, record)
    return CompletionManifestDraftExecutionResult(
        record_path=_relative_path(record_path, relative_to=paths.root),
        draft_path=draft_path,
        report_path=report_path,
        objective_profile_path=objective_state.profile_path,
        draft_state=draft_state,
    )
