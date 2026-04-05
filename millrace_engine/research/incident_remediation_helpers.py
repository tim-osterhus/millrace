"""Incident remediation bundle writing helpers."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING
import json

from ..markdown import write_text_atomic
from ..paths import RuntimePaths
from .specs import (
    GoalSpecFamilyGovernorState,
    GoalSpecFamilySpecState,
    GoalSpecFamilyState,
    GoalSpecLineageRecord,
    build_initial_family_plan_snapshot,
    refresh_stable_spec_registry,
    write_goal_spec_family_state,
)

if TYPE_CHECKING:
    from .incidents import IncidentDocument, IncidentFixSpecRecord, IncidentRemediationRecord


def incident_fix_spec_record(
    paths: RuntimePaths,
    *,
    document: "IncidentDocument",
    incident_path: Path,
) -> "IncidentFixSpecRecord":
    """Derive the remediation-spec path family for one resolved incident."""

    from .incidents import (
        IncidentFixSpecRecord,
        _extract_markdown_field,
        _markdown_section,
        _parse_frontmatter,
        _relative_path,
        _resolve_path_token,
        _scope_summary_for_incident,
        _slugify,
        _spec_id_for_incident,
        _strip_ticks,
    )

    incident_text = incident_path.read_text(encoding="utf-8")
    _, remainder = _parse_frontmatter(incident_text)
    section_text = _markdown_section(remainder, "fix_spec")
    spec_id = _spec_id_for_incident(document, section_text)
    queue_field = _strip_ticks(_extract_markdown_field(section_text, "Fix Spec Path"))
    slug = _slugify(f"{document.title} remediation")
    queue_spec_path = (
        _resolve_path_token(queue_field, relative_to=paths.root)
        if queue_field
        else paths.ideas_specs_dir / f"{spec_id}__{slug}.md"
    )
    reviewed_path = paths.ideas_specs_reviewed_dir / queue_spec_path.name
    golden_spec_path = paths.specs_stable_golden_dir / queue_spec_path.name
    phase_spec_path = paths.specs_stable_phase_dir / f"{spec_id}__phase-01.md"
    review_questions_path = paths.specs_questions_dir / f"{queue_spec_path.stem}__spec-review.md"
    review_decision_path = paths.specs_decisions_dir / f"{queue_spec_path.stem}__spec-review.md"
    return IncidentFixSpecRecord(
        spec_id=spec_id,
        title=f"{document.title} remediation",
        scope_summary=_scope_summary_for_incident(document, section_text),
        queue_spec_path=_relative_path(queue_spec_path, relative_to=paths.root),
        reviewed_path=_relative_path(reviewed_path, relative_to=paths.root),
        golden_spec_path=_relative_path(golden_spec_path, relative_to=paths.root),
        phase_spec_path=_relative_path(phase_spec_path, relative_to=paths.root),
        review_questions_path=_relative_path(review_questions_path, relative_to=paths.root),
        review_decision_path=_relative_path(review_decision_path, relative_to=paths.root),
        stable_registry_path=_relative_path(paths.specs_index_file, relative_to=paths.root),
    )


def write_incident_remediation_bundle(
    paths: RuntimePaths,
    *,
    document: "IncidentDocument",
    incident_path: Path,
    lineage_path: Path,
    run_id: str,
    emitted_at: datetime,
) -> "IncidentRemediationRecord":
    """Write the resolved incident's remediation-spec bundle and runtime record."""

    from .incidents import (
        IncidentRemediationRecord,
        _incident_remediation_record_path,
        _relative_path,
        _render_incident_fix_spec,
        _render_incident_phase_spec,
        _render_incident_review_decision,
        _render_incident_review_questions,
        _resolve_path_token,
        _write_json_model,
    )

    fix_spec = incident_fix_spec_record(paths, document=document, incident_path=incident_path)
    queue_spec_path = _resolve_path_token(fix_spec.queue_spec_path, relative_to=paths.root)
    reviewed_path = _resolve_path_token(fix_spec.reviewed_path, relative_to=paths.root)
    golden_spec_path = _resolve_path_token(fix_spec.golden_spec_path, relative_to=paths.root)
    phase_spec_path = _resolve_path_token(fix_spec.phase_spec_path, relative_to=paths.root)
    review_questions_path = _resolve_path_token(fix_spec.review_questions_path, relative_to=paths.root)
    review_decision_path = _resolve_path_token(fix_spec.review_decision_path, relative_to=paths.root)
    remediation_record_path = _incident_remediation_record_path(paths, run_id=run_id)
    goalspec_lineage_path = paths.goalspec_lineage_dir / f"{fix_spec.spec_id}.json"
    resolved_relative_path = _relative_path(incident_path, relative_to=paths.root)
    lineage_relative_path = _relative_path(lineage_path, relative_to=paths.root)

    queue_spec_path.parent.mkdir(parents=True, exist_ok=True)
    reviewed_path.parent.mkdir(parents=True, exist_ok=True)
    golden_spec_path.parent.mkdir(parents=True, exist_ok=True)
    phase_spec_path.parent.mkdir(parents=True, exist_ok=True)
    review_questions_path.parent.mkdir(parents=True, exist_ok=True)
    review_decision_path.parent.mkdir(parents=True, exist_ok=True)
    write_text_atomic(
        queue_spec_path,
        _render_incident_fix_spec(
            emitted_at=emitted_at,
            document=document,
            resolved_path=resolved_relative_path,
            lineage_path=lineage_relative_path,
            spec_id=fix_spec.spec_id,
            scope_summary=fix_spec.scope_summary,
        ),
    )
    write_text_atomic(reviewed_path, queue_spec_path.read_text(encoding="utf-8"))
    write_text_atomic(golden_spec_path, queue_spec_path.read_text(encoding="utf-8"))
    write_text_atomic(
        phase_spec_path,
        _render_incident_phase_spec(
            emitted_at=emitted_at,
            document=document,
            spec_id=fix_spec.spec_id,
            resolved_path=resolved_relative_path,
            lineage_path=lineage_relative_path,
            scope_summary=fix_spec.scope_summary,
        ),
    )
    write_text_atomic(
        review_questions_path,
        _render_incident_review_questions(
            emitted_at=emitted_at,
            run_id=run_id,
            incident_id=document.incident_id or incident_path.stem,
            spec_id=fix_spec.spec_id,
            title=fix_spec.title,
            queue_spec_path=fix_spec.queue_spec_path,
        ),
    )
    write_text_atomic(
        review_decision_path,
        _render_incident_review_decision(
            emitted_at=emitted_at,
            run_id=run_id,
            incident_id=document.incident_id or incident_path.stem,
            spec_id=fix_spec.spec_id,
            title=fix_spec.title,
            reviewed_path=fix_spec.reviewed_path,
            lineage_path=_relative_path(goalspec_lineage_path, relative_to=paths.root),
            stable_registry_path=fix_spec.stable_registry_path,
        ),
    )

    provisional_state = GoalSpecFamilyState(
        goal_id=document.incident_id or incident_path.stem,
        source_idea_path="",
        family_phase="initial_family",
        family_complete=True,
        active_spec_id=fix_spec.spec_id,
        spec_order=(fix_spec.spec_id,),
        specs={
            fix_spec.spec_id: GoalSpecFamilySpecState(
                status="reviewed",
                review_status="no_material_delta",
                title=fix_spec.title,
                decomposition_profile="simple",
                queue_path=fix_spec.queue_spec_path,
                reviewed_path=fix_spec.reviewed_path,
                stable_spec_paths=(fix_spec.golden_spec_path, fix_spec.phase_spec_path),
                review_questions_path=fix_spec.review_questions_path,
                review_decision_path=fix_spec.review_decision_path,
            )
        },
        family_governor=GoalSpecFamilyGovernorState(
            initial_family_max_specs=1,
            applied_family_max_specs=1,
        ),
    )
    initial_plan = build_initial_family_plan_snapshot(
        provisional_state,
        repo_root=paths.root,
        trigger_spec_id=fix_spec.spec_id,
        frozen_at=emitted_at,
    )
    write_goal_spec_family_state(
        paths.goal_spec_family_state_file,
        provisional_state.model_copy(update={"initial_family_plan": initial_plan}),
        updated_at=emitted_at,
    )
    refresh_stable_spec_registry(
        paths.specs_stable_dir,
        paths.specs_stable_dir / ".frozen",
        paths.specs_index_file,
        relative_to=paths.root,
        updated_at=emitted_at,
    )
    _write_json_model(
        goalspec_lineage_path,
        GoalSpecLineageRecord(
            spec_id=fix_spec.spec_id,
            goal_id=document.incident_id or incident_path.stem,
            queue_path=fix_spec.queue_spec_path,
            reviewed_path=fix_spec.reviewed_path,
            archived_path="",
            stable_spec_paths=(fix_spec.golden_spec_path, fix_spec.phase_spec_path),
            pending_shard_path="",
        ),
    )
    remediation_record = IncidentRemediationRecord(
        run_id=run_id,
        emitted_at=emitted_at,
        incident_id=document.incident_id or incident_path.stem,
        incident_title=document.title,
        resolved_path=resolved_relative_path,
        lineage_path=lineage_relative_path,
        family_state_path=_relative_path(paths.goal_spec_family_state_file, relative_to=paths.root),
        goalspec_lineage_path=_relative_path(goalspec_lineage_path, relative_to=paths.root),
        fix_spec=fix_spec,
    )
    _write_json_model(remediation_record_path, remediation_record)
    return remediation_record


def load_incident_remediation_record(path: Path) -> "IncidentRemediationRecord":
    """Load one persisted incident remediation record."""

    from .incidents import IncidentExecutionError, IncidentRemediationRecord

    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise IncidentExecutionError(f"{path.as_posix()} must contain a JSON object")
    return IncidentRemediationRecord.model_validate(payload)
