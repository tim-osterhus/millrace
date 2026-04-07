"""GoalSpec spec-synthesis stage executor."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import NamedTuple

from ..markdown import write_text_atomic
from ..paths import RuntimePaths
from .goalspec import (
    AcceptanceProfileRecord,
    CompletionManifestDraftStateRecord,
    GoalSource,
    ObjectiveProfileSyncStateRecord,
    SpecSynthesisExecutionResult,
    SpecSynthesisRecord,
)
from .goalspec_helpers import (
    GoalSpecExecutionError,
    _load_json_model,
    _load_json_object,
    _relative_path,
    _spec_id_for_goal,
    _slugify,
    _utcnow,
    _write_json_model,
    resolve_goal_source,
)
from .goalspec_persistence import (
    _build_goal_spec_family_state,
    _load_objective_profile_inputs,
    _updated_goal_spec_family_state,
)
from .goalspec_scope_diagnostics import (
    build_goal_anchor_tokens,
    evaluate_scope_divergence,
    infer_goal_scope_kind,
    write_scope_divergence_record,
)
from .goalspec_stage_rendering import (
    render_phase_spec,
    render_queue_spec,
    render_synthesis_decision_record,
)
from .governance import (
    build_reused_spec_synthesis_family_state,
    evaluate_spec_synthesis_idempotency,
    resolve_family_governor_state,
)
from .specs import GoalSpecFamilySpecState, GoalSpecFamilyState, load_goal_spec_family_state
from .state import ResearchCheckpoint, ResearchQueueFamily, ResearchQueueOwnership


class PlannedFamilySpec(NamedTuple):
    spec_id: str
    title: str
    depends_on_specs: tuple[str, ...]


def _dedupe_ordered(values: tuple[str, ...]) -> tuple[str, ...]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        token = value.strip()
        if not token or token in seen:
            continue
        seen.add(token)
        ordered.append(token)
    return tuple(ordered)


def _plan_initial_family_specs(
    *,
    spec_id: str,
    source: GoalSource,
    profile: AcceptanceProfileRecord,
    current_family_state: GoalSpecFamilyState,
    family_cap: int,
) -> tuple[PlannedFamilySpec, ...]:
    if (
        current_family_state.goal_id == source.idea_id
        and current_family_state.initial_family_plan is not None
        and current_family_state.initial_family_plan.frozen
    ):
        return tuple(
            PlannedFamilySpec(
                spec_id=planned_spec_id,
                title=current_family_state.initial_family_plan.specs[planned_spec_id].title,
                depends_on_specs=current_family_state.initial_family_plan.specs[planned_spec_id].depends_on_specs,
            )
            for planned_spec_id in current_family_state.initial_family_plan.spec_order
            if planned_spec_id != spec_id
        )

    capability_domains = _dedupe_ordered(tuple(profile.semantic_profile.capability_domains))
    progression_lines = _dedupe_ordered(tuple(profile.semantic_profile.progression_lines))
    # Objective-profile sync already persists the effective family cap in policy.json; synthesis reuses it from family governor.
    # Planned-family derivation stays conservative: only broad goals with both breadth and budget declare later specs.
    broadness_score = 0
    if len(capability_domains) >= 4:
        broadness_score += 1
    if len(progression_lines) >= 2:
        broadness_score += 1
    if source.decomposition_profile in {"involved", "complex", "massive"}:
        broadness_score += 1
    if len(profile.semantic_profile.milestones) >= 4:
        broadness_score += 1
    if broadness_score < 2:
        return ()

    planned_count = min(max(0, family_cap - 1), 2, len(capability_domains) // 2 or 1)
    if planned_count <= 0:
        if family_cap <= 1:
            return ()
        if len(progression_lines) >= 2:
            planned_count = 1
        else:
            return ()

    slice_count = planned_count + 1
    domain_chunks: list[tuple[str, ...]] = []
    if capability_domains:
        chunk_size = max(1, -(-len(capability_domains) // slice_count))
        for start in range(0, len(capability_domains), chunk_size):
            domain_chunks.append(capability_domains[start : start + chunk_size])
    planned_specs: list[PlannedFamilySpec] = []
    for index in range(planned_count):
        suffix = f"{index + 2:02d}"
        planned_spec_id = f"{spec_id}-{suffix}"
        domain_chunk = domain_chunks[index + 1] if index + 1 < len(domain_chunks) else ()
        if domain_chunk:
            title = f"{source.title}: {_join_domains(domain_chunk)}"
        elif index < len(progression_lines):
            title = f"{source.title}: {progression_lines[index]}"
        else:
            title = f"{source.title}: Later Product Slice {index + 2}"
        planned_specs.append(
            PlannedFamilySpec(
                spec_id=planned_spec_id,
                title=title,
                depends_on_specs=(spec_id,),
            )
        )
    return tuple(planned_specs)


def _join_domains(domains: tuple[str, ...]) -> str:
    if not domains:
        return "Later Product Slice"
    if len(domains) == 1:
        return domains[0]
    if len(domains) == 2:
        return f"{domains[0]} and {domains[1]}"
    return f"{', '.join(domains[:-1])}, and {domains[-1]}"


def execute_spec_synthesis(
    paths: RuntimePaths,
    checkpoint: ResearchCheckpoint,
    *,
    run_id: str,
    completion_manifest: CompletionManifestDraftStateRecord | None = None,
    emitted_at: datetime | None = None,
) -> SpecSynthesisExecutionResult:
    """Emit the draft GoalSpec package and update family-state persistence."""

    emitted_at = emitted_at or _utcnow()
    source = resolve_goal_source(paths, checkpoint)
    objective_state, profile = _load_objective_profile_inputs(paths)
    if completion_manifest is None:
        if not paths.audit_completion_manifest_file.exists():
            raise GoalSpecExecutionError(
                "Completion manifest draft is missing; Spec Synthesis cannot proceed"
            )
        completion_manifest = CompletionManifestDraftStateRecord.model_validate(
            _load_json_object(paths.audit_completion_manifest_file)
        )

    spec_id = _spec_id_for_goal(source.idea_id)
    slug = _slugify(source.title)
    queue_spec_path = paths.ideas_specs_dir / f"{spec_id}__{slug}.md"
    golden_spec_path = paths.specs_stable_golden_dir / f"{spec_id}__{slug}.md"
    phase_spec_path = paths.specs_stable_phase_dir / f"{spec_id}__phase-01.md"
    decision_path = paths.specs_decisions_dir / f"{Path(source.source_path).stem}__spec-synthesis.md"
    record_path = paths.goalspec_spec_synthesis_records_dir / f"{run_id}.json"
    completion_manifest_path = _relative_path(paths.audit_completion_manifest_file, relative_to=paths.root)
    current_family_state = load_goal_spec_family_state(paths.goal_spec_family_state_file)
    policy_payload = (
        _load_json_object(paths.objective_family_policy_file)
        if paths.objective_family_policy_file.exists()
        else {}
    )
    family_governor = resolve_family_governor_state(
        paths=paths,
        current_state=current_family_state,
        policy_payload=policy_payload,
    )
    planned_family_specs = _plan_initial_family_specs(
        spec_id=spec_id,
        source=source,
        profile=profile,
        current_family_state=current_family_state,
        family_cap=family_governor.applied_family_max_specs,
    )
    planned_spec_ids = tuple(spec.spec_id for spec in planned_family_specs)
    planned_spec_states = tuple(
        GoalSpecFamilySpecState(
            status="planned",
            title=spec.title,
            decomposition_profile=source.decomposition_profile,
            depends_on_specs=spec.depends_on_specs,
        )
        for spec in planned_family_specs
    )
    family_complete = not planned_family_specs

    queue_spec_text = render_queue_spec(
        emitted_at=emitted_at,
        source=source,
        spec_id=spec_id,
        objective_state=objective_state,
        profile=profile,
        completion_manifest=completion_manifest,
        completion_manifest_path=completion_manifest_path,
    )
    phase_spec_text = render_phase_spec(
        emitted_at=emitted_at,
        source=source,
        spec_id=spec_id,
        profile=profile,
        completion_manifest=completion_manifest,
        completion_manifest_path=completion_manifest_path,
        objective_profile_path=objective_state.profile_path,
        planned_spec_ids=planned_spec_ids,
    )
    scope_record = evaluate_scope_divergence(
        run_id=run_id,
        emitted_at=emitted_at,
        goal_id=source.idea_id,
        title=source.title,
        stage_name="spec_synthesis",
        source_path=source.relative_source_path,
        expected_scope=infer_goal_scope_kind(
            title=source.title,
            source_body=source.body,
            semantic_summary=profile.semantic_profile.objective_summary,
            capability_domains=tuple(profile.semantic_profile.capability_domains),
        ),
        goal_anchor_tokens=build_goal_anchor_tokens(
            title=source.title,
            source_body=source.body,
            semantic_summary=profile.semantic_profile.objective_summary,
            capability_domains=tuple(profile.semantic_profile.capability_domains),
            progression_lines=tuple(profile.semantic_profile.progression_lines),
        ),
        surfaces=(
            (
                "objective_profile",
                "\n".join(
                    (
                        profile.semantic_profile.objective_summary,
                        *profile.semantic_profile.capability_domains,
                        *profile.semantic_profile.progression_lines,
                        *profile.milestones,
                    )
                ),
            ),
            ("queue_spec", queue_spec_text),
            ("phase_spec", phase_spec_text),
        ),
    )
    if scope_record.decision == "blocked":
        record_path = write_scope_divergence_record(paths, scope_record)
        raise GoalSpecExecutionError(
            f"Scope divergence blocked {spec_id} during spec_synthesis; diagnostic: {record_path}"
        )
    expected_family_state = _build_goal_spec_family_state(
        paths=paths,
        source=source,
        spec_id=spec_id,
        title=source.title,
        decomposition_profile=source.decomposition_profile,
        queue_spec_path=queue_spec_path,
        emitted_at=emitted_at,
        planned_specs=planned_spec_states,
        planned_spec_ids=planned_spec_ids,
        family_complete=family_complete,
    )
    decision_text = render_synthesis_decision_record(
        emitted_at=emitted_at,
        run_id=run_id,
        source=source,
        spec_id=spec_id,
        profile=profile,
        completion_manifest=completion_manifest,
        completion_manifest_path=completion_manifest_path,
        objective_profile_path=objective_state.profile_path,
        queue_spec_path=_relative_path(queue_spec_path, relative_to=paths.root),
        family_complete=expected_family_state.family_complete,
        planned_spec_ids=planned_spec_ids,
    )
    if (
        record_path.exists()
        and queue_spec_path.exists()
        and golden_spec_path.exists()
        and phase_spec_path.exists()
        and decision_path.exists()
        and paths.goal_spec_family_state_file.exists()
    ):
        existing_record = _load_json_model(record_path, SpecSynthesisRecord)
        existing_family_state = current_family_state
        reused_emitted_at = existing_record.emitted_at
        expected_record = SpecSynthesisRecord(
            run_id=run_id,
            emitted_at=reused_emitted_at,
            goal_id=source.idea_id,
            spec_id=spec_id,
            title=source.title,
            source_path=source.relative_source_path,
            research_brief_path=source.relative_source_path,
            objective_profile_path=objective_state.profile_path,
            completion_manifest_path=completion_manifest_path,
            queue_spec_path=_relative_path(queue_spec_path, relative_to=paths.root),
            golden_spec_path=_relative_path(golden_spec_path, relative_to=paths.root),
            phase_spec_path=_relative_path(phase_spec_path, relative_to=paths.root),
            decision_path=_relative_path(decision_path, relative_to=paths.root),
            family_state_path=_relative_path(paths.goal_spec_family_state_file, relative_to=paths.root),
        )
        expected_queue_spec = render_queue_spec(
            emitted_at=reused_emitted_at,
            source=source,
            spec_id=spec_id,
            objective_state=objective_state,
            profile=profile,
            completion_manifest=completion_manifest,
            completion_manifest_path=completion_manifest_path,
        )
        expected_phase_spec = render_phase_spec(
            emitted_at=reused_emitted_at,
            source=source,
            spec_id=spec_id,
            profile=profile,
            completion_manifest=completion_manifest,
            completion_manifest_path=completion_manifest_path,
            objective_profile_path=objective_state.profile_path,
            planned_spec_ids=planned_spec_ids,
        )
        expected_decision = render_synthesis_decision_record(
            emitted_at=reused_emitted_at,
            run_id=run_id,
            source=source,
            spec_id=spec_id,
            profile=profile,
            completion_manifest=completion_manifest,
            completion_manifest_path=completion_manifest_path,
            objective_profile_path=objective_state.profile_path,
            queue_spec_path=_relative_path(queue_spec_path, relative_to=paths.root),
            family_complete=expected_family_state.family_complete,
            planned_spec_ids=planned_spec_ids,
        )
        expected_reused_family_state = build_reused_spec_synthesis_family_state(
            expected_family_state=expected_family_state,
            existing_family_state=existing_family_state,
        )
        idempotency_decision = evaluate_spec_synthesis_idempotency(
            existing_record=existing_record,
            expected_record=expected_record,
            existing_family_state=existing_family_state,
            expected_family_state=expected_reused_family_state,
            actual_queue_spec_text=queue_spec_path.read_text(encoding="utf-8"),
            actual_golden_spec_text=golden_spec_path.read_text(encoding="utf-8"),
            actual_phase_spec_text=phase_spec_path.read_text(encoding="utf-8"),
            actual_decision_text=decision_path.read_text(encoding="utf-8"),
            expected_queue_spec_text=expected_queue_spec,
            expected_phase_spec_text=expected_phase_spec,
            expected_decision_text=expected_decision,
        )
        if idempotency_decision.action == "reuse":
            return SpecSynthesisExecutionResult(
                record_path=_relative_path(record_path, relative_to=paths.root),
                queue_spec_path=_relative_path(queue_spec_path, relative_to=paths.root),
                golden_spec_path=_relative_path(golden_spec_path, relative_to=paths.root),
                phase_spec_path=_relative_path(phase_spec_path, relative_to=paths.root),
                decision_path=_relative_path(decision_path, relative_to=paths.root),
                family_state_path=_relative_path(paths.goal_spec_family_state_file, relative_to=paths.root),
                queue_ownership=ResearchQueueOwnership(
                    family=ResearchQueueFamily.GOALSPEC,
                    queue_path=paths.ideas_specs_dir,
                    item_path=queue_spec_path,
                    owner_token=run_id,
                    acquired_at=reused_emitted_at,
                ),
            )

    queue_spec_path.parent.mkdir(parents=True, exist_ok=True)
    golden_spec_path.parent.mkdir(parents=True, exist_ok=True)
    phase_spec_path.parent.mkdir(parents=True, exist_ok=True)
    decision_path.parent.mkdir(parents=True, exist_ok=True)
    write_text_atomic(queue_spec_path, queue_spec_text)
    write_text_atomic(golden_spec_path, queue_spec_text)
    write_text_atomic(phase_spec_path, phase_spec_text)

    _updated_goal_spec_family_state(
        paths=paths,
        source=source,
        spec_id=spec_id,
        title=source.title,
        decomposition_profile=source.decomposition_profile,
        queue_spec_path=queue_spec_path,
        emitted_at=emitted_at,
        planned_specs=planned_spec_states,
        planned_spec_ids=planned_spec_ids,
        family_complete=family_complete,
    )
    write_text_atomic(decision_path, decision_text)

    record = SpecSynthesisRecord(
        run_id=run_id,
        emitted_at=emitted_at,
        goal_id=source.idea_id,
        spec_id=spec_id,
        title=source.title,
        source_path=source.relative_source_path,
        research_brief_path=source.relative_source_path,
        objective_profile_path=objective_state.profile_path,
        completion_manifest_path=completion_manifest_path,
        queue_spec_path=_relative_path(queue_spec_path, relative_to=paths.root),
        golden_spec_path=_relative_path(golden_spec_path, relative_to=paths.root),
        phase_spec_path=_relative_path(phase_spec_path, relative_to=paths.root),
        decision_path=_relative_path(decision_path, relative_to=paths.root),
        family_state_path=_relative_path(paths.goal_spec_family_state_file, relative_to=paths.root),
    )
    _write_json_model(record_path, record)
    return SpecSynthesisExecutionResult(
        record_path=_relative_path(record_path, relative_to=paths.root),
        queue_spec_path=_relative_path(queue_spec_path, relative_to=paths.root),
        golden_spec_path=_relative_path(golden_spec_path, relative_to=paths.root),
        phase_spec_path=_relative_path(phase_spec_path, relative_to=paths.root),
        decision_path=_relative_path(decision_path, relative_to=paths.root),
        family_state_path=_relative_path(paths.goal_spec_family_state_file, relative_to=paths.root),
        queue_ownership=ResearchQueueOwnership(
            family=ResearchQueueFamily.GOALSPEC,
            queue_path=paths.ideas_specs_dir,
            item_path=queue_spec_path,
            owner_token=run_id,
            acquired_at=emitted_at,
        ),
    )
