"""GoalSpec objective-profile-sync stage executor."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from ..contracts import ObjectiveContract
from ..markdown import write_text_atomic
from ..paths import RuntimePaths
from .goalspec import (
    AcceptanceProfileRecord,
    ObjectiveProfileSyncExecutionResult,
    ObjectiveProfileSyncRecord,
    ObjectiveProfileSyncStateRecord,
)
from .goalspec_family_policy import derive_objective_family_policy
from .goalspec_helpers import (
    _isoformat_z,
    _load_json_object,
    _relative_path,
    _slugify,
    _split_frontmatter,
    _utcnow,
    _write_json_model,
    resolve_goal_source,
)
from .goalspec_semantic_profile import (
    build_goal_semantic_profile,
    discover_semantic_seed_path,
    load_semantic_seed_document,
)
from .goalspec_contractor import execute_contractor
from .governance import apply_initial_family_policy_pin, build_queue_governor_report
from .specs import load_goal_spec_family_state
from .state import ResearchCheckpoint, ResearchQueueFamily, ResearchQueueOwnership


def _join_human_list(items: tuple[str, ...], *, limit: int = 4) -> str:
    selected = tuple(item.strip() for item in items if item.strip())[:limit]
    if not selected:
        return ""
    if len(selected) == 1:
        return selected[0]
    if len(selected) == 2:
        return f"{selected[0]} and {selected[1]}"
    return f"{', '.join(selected[:-1])}, and {selected[-1]}"


def _build_product_hard_blockers(*, title: str, semantic_profile: object) -> tuple[str, ...]:
    capability_domains = tuple(getattr(semantic_profile, "capability_domains", ()) or ())
    progression_lines = tuple(getattr(semantic_profile, "progression_lines", ()) or ())

    if capability_domains:
        implementation_target = _join_human_list(capability_domains)
        implementation_gap = f"Implementation remains open for the profiled product capabilities: {implementation_target}."
    else:
        implementation_gap = f"Implementation remains open for the profiled product objective: {title}."

    if progression_lines:
        verification_gap = (
            f"End-to-end validation remains pending for the product progression: {progression_lines[0]}"
        )
    else:
        verification_gap = "End-to-end product validation remains pending against the profiled acceptance path."

    return (implementation_gap, verification_gap)


def _semantic_hygiene_diagnostic_lines(semantic_profile: object) -> tuple[str, ...]:
    rejected_candidates = tuple(getattr(semantic_profile, "rejected_candidates", ()) or ())
    if not rejected_candidates:
        return ("- No control-plane candidates were rejected during semantic extraction.",)

    lines: list[str] = []
    for item in rejected_candidates:
        candidate = str(getattr(item, "candidate", "")).strip()
        surface = str(getattr(item, "surface", "")).replace("_", " ").strip()
        reason = str(getattr(item, "reason", "")).replace("_", " ").strip()
        if not candidate:
            continue
        descriptor_parts = [part for part in (surface, reason) if part]
        if descriptor_parts:
            lines.append(f"- `{candidate}` ({'; '.join(descriptor_parts)})")
        else:
            lines.append(f"- `{candidate}`")
    return tuple(lines) or ("- No control-plane candidates were rejected during semantic extraction.",)


def execute_objective_profile_sync(
    paths: RuntimePaths,
    checkpoint: ResearchCheckpoint,
    *,
    run_id: str,
    emitted_at: datetime | None = None,
) -> ObjectiveProfileSyncExecutionResult:
    """Materialize the current objective-profile surfaces from one staged research brief."""

    emitted_at = emitted_at or _utcnow()
    source = resolve_goal_source(paths, checkpoint)
    profile_slug = _slugify(source.idea_id or source.title)
    profile_id = f"{profile_slug}-profile"
    research_brief_path = Path(source.current_artifact_path)
    profile_json_path = paths.acceptance_profiles_dir / f"{profile_id}.json"
    profile_markdown_path = paths.acceptance_profiles_dir / f"{profile_id}.md"
    report_path = paths.reports_dir / "objective_profile_sync.md"
    goal_intake_record_path = paths.goalspec_goal_intake_records_dir / f"{run_id}.json"
    contractor_result = execute_contractor(paths, checkpoint, run_id=run_id, emitted_at=emitted_at)

    semantic_goal_text = source.canonical_body
    if goal_intake_record_path.exists():
        goal_intake_payload = _load_json_object(goal_intake_record_path)
        authoritative_goal_rel = str(
            goal_intake_payload.get("canonical_source_path")
            or goal_intake_payload.get("archived_source_path")
            or goal_intake_payload.get("source_path")
            or ""
        ).strip()
        if authoritative_goal_rel:
            authoritative_goal_path = paths.root / authoritative_goal_rel
            if authoritative_goal_path.exists():
                authoritative_goal_text = authoritative_goal_path.read_text(encoding="utf-8", errors="replace")
                _, authoritative_goal_body = _split_frontmatter(authoritative_goal_text)
                semantic_goal_text = authoritative_goal_body.strip() or authoritative_goal_text.strip()

    semantic_seed_path = discover_semantic_seed_path(paths)
    semantic_seed_payload = (
        load_semantic_seed_document(semantic_seed_path) if semantic_seed_path is not None else None
    )
    semantic_profile = build_goal_semantic_profile(
        semantic_goal_text,
        semantic_seed_payload=semantic_seed_payload,
        semantic_seed_path=(
            _relative_path(semantic_seed_path, relative_to=paths.root)
            if semantic_seed_path is not None
            else ""
        ),
    )
    milestones = tuple(item.outcome for item in semantic_profile.milestones)
    hard_blockers = _build_product_hard_blockers(title=source.title, semantic_profile=semantic_profile)

    acceptance_profile = AcceptanceProfileRecord(
        profile_id=profile_id,
        goal_id=source.idea_id,
        title=source.title,
        run_id=run_id,
        updated_at=emitted_at,
        canonical_source_path=source.canonical_relative_source_path,
        current_artifact_path=source.current_artifact_relative_path,
        source_path=source.canonical_relative_source_path,
        research_brief_path=_relative_path(research_brief_path, relative_to=paths.root),
        semantic_profile=semantic_profile,
        milestones=milestones,
        hard_blockers=hard_blockers,
    )
    _write_json_model(profile_json_path, acceptance_profile)

    profile_markdown_path.parent.mkdir(parents=True, exist_ok=True)
    write_text_atomic(
        profile_markdown_path,
        "\n".join(
            [
                f"# Acceptance Profile: {source.title}",
                "",
                f"- **Profile-ID:** {profile_id}",
                f"- **Goal-ID:** {source.idea_id}",
                f"- **Run-ID:** {run_id}",
                f"- **Updated-At:** {_isoformat_z(emitted_at)}",
                f"- **Canonical-Source-Path:** `{source.canonical_relative_source_path}`",
                f"- **Current-Artifact-Path:** `{source.current_artifact_relative_path}`",
                "",
                "## Objective Summary",
                semantic_profile.objective_summary,
                "",
                "## Capability Domains",
                *(f"- {item}" for item in semantic_profile.capability_domains),
                *(["- No explicit capability domains were detected."] if not semantic_profile.capability_domains else []),
                "",
                "## Progression Lines",
                *(f"- {item}" for item in semantic_profile.progression_lines),
                *(["- No explicit progression lines were detected."] if not semantic_profile.progression_lines else []),
                "",
                "## Semantic Hygiene Diagnostics",
                *_semantic_hygiene_diagnostic_lines(semantic_profile),
                "",
                "## Milestones",
                *(f"- {item}" for item in milestones),
                "",
                "## Hard Blockers",
                *(f"- {item}" for item in hard_blockers),
                "",
            ]
        ),
    )

    family_state = (
        load_goal_spec_family_state(paths.goal_spec_family_state_file)
        if paths.goal_spec_family_state_file.exists()
        else None
    )
    family_policy_payload: dict[str, object] = {}
    if paths.objective_family_policy_file.exists():
        family_policy_payload = _load_json_object(paths.objective_family_policy_file)
    family_policy_payload = derive_objective_family_policy(
        current_policy_payload=family_policy_payload,
        semantic_profile=semantic_profile,
        decomposition_profile=source.decomposition_profile,
        source_goal_id=source.idea_id,
        updated_at=emitted_at,
    )
    family_policy_payload, initial_family_policy_pin = apply_initial_family_policy_pin(
        paths=paths,
        current_policy_payload=family_policy_payload,
        current_family_state=family_state,
    )
    write_text_atomic(
        paths.objective_family_policy_file,
        json.dumps(family_policy_payload, indent=2, sort_keys=True) + "\n",
    )
    queue_governor_report = build_queue_governor_report(
        paths=paths,
        goal_id=source.idea_id,
        updated_at=emitted_at,
        pin_decision=initial_family_policy_pin,
    )
    _write_json_model(paths.queue_governor_report_file, queue_governor_report)

    profile_state = ObjectiveProfileSyncStateRecord(
        profile_id=profile_id,
        goal_id=source.idea_id,
        title=source.title,
        run_id=run_id,
        updated_at=emitted_at,
        canonical_source_path=source.canonical_relative_source_path,
        current_artifact_path=source.current_artifact_relative_path,
        source_path=source.canonical_relative_source_path,
        research_brief_path=_relative_path(research_brief_path, relative_to=paths.root),
        profile_path=_relative_path(profile_json_path, relative_to=paths.root),
        profile_markdown_path=_relative_path(profile_markdown_path, relative_to=paths.root),
        report_path=_relative_path(report_path, relative_to=paths.root),
        goal_intake_record_path=_relative_path(goal_intake_record_path, relative_to=paths.root),
        contractor_record_path=contractor_result.record_path,
        contractor_profile_path=contractor_result.profile_path,
        contractor_report_path=contractor_result.report_path,
        contractor_schema_path=contractor_result.schema_path,
        contractor_specificity_level=contractor_result.profile.specificity_level,
        contractor_shape_class=contractor_result.profile.shape_class,
        contractor_fallback_mode=contractor_result.profile.fallback_mode,
        initial_family_policy_pin=initial_family_policy_pin,
    )
    _write_json_model(paths.objective_profile_sync_state_file, profile_state)

    write_text_atomic(
        report_path,
        "\n".join(
            [
                "# Objective Profile Sync",
                "",
                f"- **Run-ID:** {run_id}",
                f"- **Goal-ID:** {source.idea_id}",
                f"- **Profile-ID:** {profile_id}",
                f"- **Updated-At:** {_isoformat_z(emitted_at)}",
                f"- **Canonical-Source-Path:** `{source.canonical_relative_source_path}`",
                f"- **Current-Artifact-Path:** `{source.current_artifact_relative_path}`",
                f"- **Research-Brief:** `{_relative_path(research_brief_path, relative_to=paths.root)}`",
                (
                    f"- **Profile-State:** "
                    f"`{_relative_path(paths.objective_profile_sync_state_file, relative_to=paths.root)}`"
                ),
                f"- **Contractor-Record:** `{contractor_result.record_path}`",
                f"- **Contractor-Profile:** `{contractor_result.profile_path}`",
                f"- **Contractor-Report:** `{contractor_result.report_path}`",
                f"- **Family-Cap-Mode:** `{family_policy_payload.get('family_cap_mode', 'adaptive')}`",
                (
                    f"- **Initial-Family-Max-Specs:** "
                    f"`{int(family_policy_payload.get('initial_family_max_specs', 0) or 0)}`"
                ),
                "",
                "## Contractor Summary",
                f"- **Shape-Class:** `{contractor_result.profile.shape_class}`",
                f"- **Specificity-Level:** `{contractor_result.profile.specificity_level}`",
                f"- **Fallback-Mode:** `{contractor_result.profile.fallback_mode}`",
                (
                    f"- **Resolved-Profile-IDs:** "
                    + ", ".join(f"`{item}`" for item in contractor_result.profile.resolved_profile_ids)
                    if contractor_result.profile.resolved_profile_ids
                    else "- **Resolved-Profile-IDs:** none"
                ),
                (
                    f"- **Abstentions:** "
                    + "; ".join(contractor_result.profile.abstentions)
                    if contractor_result.profile.abstentions
                    else "- **Abstentions:** none"
                ),
                "",
                "## Semantic Hygiene Diagnostics",
                *_semantic_hygiene_diagnostic_lines(semantic_profile),
                "",
                "## Outcome",
                (
                    "Objective Profile Sync refreshed the canonical acceptance-profile and current objective state "
                    "for downstream GoalSpec work after running the inline Contractor classification pass."
                ),
                "",
            ]
        ),
    )

    _write_json_model(
        paths.objective_contract_file,
        ObjectiveContract(
            objective_id=source.idea_id,
            objective_root=".",
            completion={
                "authoritative_decision_file": "agents/reports/completion_decision.json",
                "fallback_decision_file": "agents/reports/audit_gate_decision.json",
                "require_task_store_cards_zero": True,
                "require_open_gaps_zero": True,
            },
            seed_state={
                "mode": "goal_spec_workspace",
                "goal_id": source.idea_id,
                "source_path": source.canonical_relative_source_path,
            },
            artifacts={
                "strict_contract_file": _relative_path(paths.audit_strict_contract_file, relative_to=paths.root),
                "objective_profile_state_file": _relative_path(
                    paths.objective_profile_sync_state_file,
                    relative_to=paths.root,
                ),
                "contractor_profile_file": contractor_result.profile_path,
                "contractor_profile_report_file": contractor_result.report_path,
                "objective_profile_file": _relative_path(profile_json_path, relative_to=paths.root),
                "objective_profile_markdown_file": _relative_path(profile_markdown_path, relative_to=paths.root),
                "completion_manifest_file": _relative_path(
                    paths.audit_completion_manifest_file, relative_to=paths.root
                ),
            },
            objective_profile={
                "profile_id": profile_id,
                "goal_id": source.idea_id,
                "title": source.title,
                "source_path": source.canonical_relative_source_path,
                "canonical_source_path": source.canonical_relative_source_path,
                "current_artifact_path": source.current_artifact_relative_path,
                "updated_at": _isoformat_z(emitted_at),
                "profile_path": _relative_path(profile_json_path, relative_to=paths.root),
                "profile_markdown_path": _relative_path(profile_markdown_path, relative_to=paths.root),
                "research_brief_path": _relative_path(research_brief_path, relative_to=paths.root),
                "report_path": _relative_path(report_path, relative_to=paths.root),
                "goal_intake_record_path": _relative_path(goal_intake_record_path, relative_to=paths.root),
                "contractor_record_path": contractor_result.record_path,
                "contractor_profile_path": contractor_result.profile_path,
                "contractor_report_path": contractor_result.report_path,
                "contractor_schema_path": contractor_result.schema_path,
                "contractor_specificity_level": contractor_result.profile.specificity_level,
                "contractor_shape_class": contractor_result.profile.shape_class,
                "contractor_fallback_mode": contractor_result.profile.fallback_mode,
                "semantic_profile": semantic_profile.model_dump(mode="json"),
                "hard_blockers": list(hard_blockers),
                "family_policy_path": _relative_path(paths.objective_family_policy_file, relative_to=paths.root),
                "family_cap_mode": str(family_policy_payload.get("family_cap_mode", "")).strip() or "adaptive",
                "initial_family_max_specs": int(family_policy_payload.get("initial_family_max_specs", 0) or 0),
                "remediation_family_max_specs": int(
                    family_policy_payload.get("remediation_family_max_specs", 0) or 0
                ),
            },
        ),
    )
    _write_json_model(
        paths.audit_strict_contract_file,
        AcceptanceProfileRecord(
            profile_id=profile_id,
            goal_id=source.idea_id,
            title=source.title,
            run_id=run_id,
            updated_at=emitted_at,
            canonical_source_path=source.canonical_relative_source_path,
            current_artifact_path=source.current_artifact_relative_path,
            source_path=source.canonical_relative_source_path,
            research_brief_path=_relative_path(research_brief_path, relative_to=paths.root),
            semantic_profile=semantic_profile,
            milestones=milestones,
            hard_blockers=hard_blockers,
        ),
    )
    record = ObjectiveProfileSyncRecord(
        run_id=run_id,
        emitted_at=emitted_at,
        goal_id=source.idea_id,
        title=source.title,
        canonical_source_path=source.canonical_relative_source_path,
        current_artifact_path=source.current_artifact_relative_path,
        source_path=source.canonical_relative_source_path,
        research_brief_path=_relative_path(research_brief_path, relative_to=paths.root),
        profile_state_path=_relative_path(paths.objective_profile_sync_state_file, relative_to=paths.root),
        profile_path=_relative_path(profile_json_path, relative_to=paths.root),
        profile_markdown_path=_relative_path(profile_markdown_path, relative_to=paths.root),
        report_path=_relative_path(report_path, relative_to=paths.root),
        contractor_record_path=contractor_result.record_path,
        contractor_profile_path=contractor_result.profile_path,
        contractor_report_path=contractor_result.report_path,
        contractor_schema_path=contractor_result.schema_path,
        contractor_specificity_level=contractor_result.profile.specificity_level,
        contractor_shape_class=contractor_result.profile.shape_class,
        contractor_fallback_mode=contractor_result.profile.fallback_mode,
    )
    record_path = paths.goalspec_objective_profile_sync_records_dir / f"{run_id}.json"
    _write_json_model(record_path, record)

    return ObjectiveProfileSyncExecutionResult(
        record_path=_relative_path(record_path, relative_to=paths.root),
        profile_state_path=_relative_path(paths.objective_profile_sync_state_file, relative_to=paths.root),
        contractor_record_path=contractor_result.record_path,
        queue_ownership=ResearchQueueOwnership(
            family=ResearchQueueFamily.GOALSPEC,
            queue_path=paths.ideas_staging_dir,
            item_path=research_brief_path,
            owner_token=run_id,
            acquired_at=emitted_at,
        ),
    )
