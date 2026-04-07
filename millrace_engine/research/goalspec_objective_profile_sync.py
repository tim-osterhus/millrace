"""GoalSpec objective-profile-sync stage executor."""

from __future__ import annotations

from datetime import datetime
import json
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
from .governance import apply_initial_family_policy_pin, build_queue_governor_report
from .specs import load_goal_spec_family_state
from .state import ResearchCheckpoint, ResearchQueueFamily, ResearchQueueOwnership


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
    research_brief_path = Path(source.source_path)
    profile_json_path = paths.acceptance_profiles_dir / f"{profile_id}.json"
    profile_markdown_path = paths.acceptance_profiles_dir / f"{profile_id}.md"
    report_path = paths.reports_dir / "objective_profile_sync.md"
    goal_intake_record_path = paths.goalspec_goal_intake_records_dir / f"{run_id}.json"

    semantic_goal_text = source.body
    if goal_intake_record_path.exists():
        goal_intake_payload = _load_json_object(goal_intake_record_path)
        authoritative_goal_rel = str(
            goal_intake_payload.get("archived_source_path") or goal_intake_payload.get("source_path") or ""
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
    hard_blockers = (
        "Completion evidence, spec synthesis, and task generation remain downstream after this profile-sync pass.",
    )

    acceptance_profile = AcceptanceProfileRecord(
        profile_id=profile_id,
        goal_id=source.idea_id,
        title=source.title,
        run_id=run_id,
        updated_at=emitted_at,
        source_path=source.relative_source_path,
        research_brief_path=_relative_path(research_brief_path, relative_to=paths.root),
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
                f"- **Source-Path:** `{source.relative_source_path}`",
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
    family_policy_payload.update(
        {
            "schema_version": "1.0",
            "family_cap_mode": "deterministic",
            "initial_family_max_specs": 1,
            "source_goal_id": source.idea_id,
            "updated_at": _isoformat_z(emitted_at),
        }
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
        source_path=source.relative_source_path,
        research_brief_path=_relative_path(research_brief_path, relative_to=paths.root),
        profile_path=_relative_path(profile_json_path, relative_to=paths.root),
        profile_markdown_path=_relative_path(profile_markdown_path, relative_to=paths.root),
        report_path=_relative_path(report_path, relative_to=paths.root),
        goal_intake_record_path=_relative_path(goal_intake_record_path, relative_to=paths.root),
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
                f"- **Source-Path:** `{source.relative_source_path}`",
                f"- **Research-Brief:** `{_relative_path(research_brief_path, relative_to=paths.root)}`",
                (
                    f"- **Profile-State:** "
                    f"`{_relative_path(paths.objective_profile_sync_state_file, relative_to=paths.root)}`"
                ),
                "",
                "## Outcome",
                "Objective Profile Sync refreshed the canonical acceptance-profile and current objective state for downstream GoalSpec work.",
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
                "source_path": source.relative_source_path,
            },
            artifacts={
                "strict_contract_file": _relative_path(paths.audit_strict_contract_file, relative_to=paths.root),
                "objective_profile_state_file": _relative_path(
                    paths.objective_profile_sync_state_file,
                    relative_to=paths.root,
                ),
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
                "source_path": source.relative_source_path,
                "updated_at": _isoformat_z(emitted_at),
                "profile_path": _relative_path(profile_json_path, relative_to=paths.root),
                "profile_markdown_path": _relative_path(profile_markdown_path, relative_to=paths.root),
                "research_brief_path": _relative_path(research_brief_path, relative_to=paths.root),
                "report_path": _relative_path(report_path, relative_to=paths.root),
                "goal_intake_record_path": _relative_path(goal_intake_record_path, relative_to=paths.root),
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
            source_path=source.relative_source_path,
            research_brief_path=_relative_path(research_brief_path, relative_to=paths.root),
            milestones=milestones,
            hard_blockers=hard_blockers,
        ),
    )
    record = ObjectiveProfileSyncRecord(
        run_id=run_id,
        emitted_at=emitted_at,
        goal_id=source.idea_id,
        title=source.title,
        source_path=source.relative_source_path,
        research_brief_path=_relative_path(research_brief_path, relative_to=paths.root),
        profile_state_path=_relative_path(paths.objective_profile_sync_state_file, relative_to=paths.root),
        profile_path=_relative_path(profile_json_path, relative_to=paths.root),
        profile_markdown_path=_relative_path(profile_markdown_path, relative_to=paths.root),
        report_path=_relative_path(report_path, relative_to=paths.root),
    )
    record_path = paths.goalspec_objective_profile_sync_records_dir / f"{run_id}.json"
    _write_json_model(record_path, record)

    return ObjectiveProfileSyncExecutionResult(
        record_path=_relative_path(record_path, relative_to=paths.root),
        profile_state_path=_relative_path(paths.objective_profile_sync_state_file, relative_to=paths.root),
        queue_ownership=ResearchQueueOwnership(
            family=ResearchQueueFamily.GOALSPEC,
            queue_path=paths.ideas_staging_dir,
            item_path=research_brief_path,
            owner_token=run_id,
            acquired_at=emitted_at,
        ),
    )
