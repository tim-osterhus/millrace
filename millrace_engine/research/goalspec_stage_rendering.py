"""Shared GoalSpec stage rendering helpers."""

from __future__ import annotations

from datetime import datetime

from .goalspec import (
    AcceptanceProfileRecord,
    CompletionManifestDraftStateRecord,
    ContractorProfileArtifact,
    GoalSource,
    ObjectiveProfileSyncStateRecord,
)
from .goalspec_helpers import (
    _FRONTMATTER_BOUNDARY,
    _first_paragraph,
    _isoformat_z,
)
from .goalspec_product_planning import derive_goal_product_plan, minimum_phase_package_count


def _dedupe_ordered(values: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        token = value.strip()
        if not token or token in seen:
            continue
        seen.add(token)
        deduped.append(token)
    return tuple(deduped)


def _product_summary(*, source: GoalSource, profile: AcceptanceProfileRecord) -> str:
    summary = profile.semantic_profile.objective_summary.strip()
    if summary:
        return summary
    return _first_paragraph(source.body) or source.title


def _capability_domains(profile: AcceptanceProfileRecord) -> tuple[str, ...]:
    semantic_domains = tuple(profile.semantic_profile.capability_domains)
    if semantic_domains:
        return semantic_domains
    milestone_domains: list[str] = []
    for milestone in profile.semantic_profile.milestones:
        milestone_domains.extend(milestone.capability_scope)
    return _dedupe_ordered(milestone_domains)


def _progression_lines(profile: AcceptanceProfileRecord) -> tuple[str, ...]:
    return _dedupe_ordered(tuple(profile.semantic_profile.progression_lines))


def _acceptance_focus(completion_manifest: CompletionManifestDraftStateRecord) -> tuple[str, ...]:
    return _dedupe_ordered(tuple(completion_manifest.acceptance_focus))


def _required_artifact_paths(completion_manifest: CompletionManifestDraftStateRecord) -> tuple[str, ...]:
    return _dedupe_ordered([artifact.path for artifact in completion_manifest.required_artifacts])


def _implementation_surface_paths(completion_manifest: CompletionManifestDraftStateRecord) -> tuple[str, ...]:
    return _dedupe_ordered([surface.path for surface in completion_manifest.implementation_surfaces])


def _verification_surface_paths(completion_manifest: CompletionManifestDraftStateRecord) -> tuple[str, ...]:
    return _dedupe_ordered([surface.path for surface in completion_manifest.verification_surfaces])


def _render_surface_lines(
    values: tuple[object, ...],
    *,
    empty_message: str,
) -> list[str]:
    lines = [
        f"- `{getattr(value, 'path')}` ({getattr(value, 'purpose')})"
        for value in values
    ]
    return lines or [f"- {empty_message}"]


def _format_domain_sentence(domains: tuple[str, ...]) -> str:
    if not domains:
        return "the profiled product objective"
    if len(domains) == 1:
        return domains[0]
    if len(domains) == 2:
        return f"{domains[0]} and {domains[1]}"
    return f"{', '.join(domains[:-1])}, and {domains[-1]}"


def _contractor_shape_phrase(contractor_profile: ContractorProfileArtifact) -> str:
    parts = [f"`{contractor_profile.shape_class}`"]
    if contractor_profile.classification.archetype:
        parts.append(f"archetype `{contractor_profile.classification.archetype}`")
    if contractor_profile.classification.host_platform:
        parts.append(f"host `{contractor_profile.classification.host_platform}`")
    return ", ".join(parts)


def _format_specialization_record(item: object) -> str:
    key = str(getattr(item, "key", "")).strip()
    value = str(getattr(item, "value", "")).strip()
    provenance = str(getattr(item, "provenance", "")).strip() or "unknown"
    support_state = str(getattr(item, "support_state", "")).strip() or "unknown"
    evidence_path = str(getattr(item, "evidence_path", "")).strip()
    path_suffix = f" @ `{evidence_path}`" if evidence_path else ""
    return f"`{key}={value}` ({provenance}, {support_state}{path_suffix})"


def _contractor_grounding_lines(
    *,
    contractor_profile: ContractorProfileArtifact | None,
    completion_manifest: CompletionManifestDraftStateRecord,
) -> list[str]:
    if contractor_profile is None or not completion_manifest.contractor_shape_class:
        return []
    lines = [
        "## Contractor Grounding",
        (
            f"- Contractor resolved {_contractor_shape_phrase(contractor_profile)} at specificity "
            f"`{completion_manifest.contractor_specificity_level or contractor_profile.specificity_level}`."
        ),
        (
            f"- Planning remains bounded by fallback mode "
            f"`{completion_manifest.contractor_fallback_mode or contractor_profile.fallback_mode}`."
        ),
    ]
    if completion_manifest.contractor_capability_hints:
        lines.append(
            "- Carry forward capability hints: "
            + ", ".join(f"`{item}`" for item in completion_manifest.contractor_capability_hints)
            + "."
        )
    if completion_manifest.contractor_environment_hints:
        lines.append(
            "- Environment assumptions remain explicit: "
            + ", ".join(f"`{item}`" for item in completion_manifest.contractor_environment_hints)
            + "."
        )
    if completion_manifest.contractor_specialization_provenance:
        lines.append(
            "- Specialization provenance remains explicit: "
            + "; ".join(_format_specialization_record(item) for item in completion_manifest.contractor_specialization_provenance)
            + "."
        )
    if completion_manifest.contractor_unresolved_specializations:
        lines.append(
            "- Unsupported specialization remains unresolved: "
            + ", ".join(f"`{item}`" for item in completion_manifest.contractor_unresolved_specializations)
            + "."
        )
    if completion_manifest.contractor_abstentions:
        lines.append(
            "- Contractor abstentions remain in force: "
            + "; ".join(completion_manifest.contractor_abstentions[:2])
            + "."
        )
    if completion_manifest.contractor_contradictions:
        lines.append(
            "- Contractor contradictions to preserve: "
            + "; ".join(completion_manifest.contractor_contradictions[:2])
            + "."
        )
    lines.append("")
    return lines


def _contractor_scope_line(contractor_profile: ContractorProfileArtifact | None) -> str | None:
    if contractor_profile is None:
        return None
    return f"- Keep implementation aligned to Contractor-resolved {_contractor_shape_phrase(contractor_profile)}."


def _partition_phase_steps(
    steps: tuple[str, ...],
    *,
    package_count: int,
) -> tuple[tuple[str, ...], ...]:
    if not steps:
        return ()
    bounded_package_count = max(1, min(package_count, len(steps)))
    base_size, remainder = divmod(len(steps), bounded_package_count)
    packages: list[tuple[str, ...]] = []
    cursor = 0
    for index in range(bounded_package_count):
        chunk_size = base_size + (1 if index < remainder else 0)
        next_cursor = cursor + chunk_size
        packages.append(steps[cursor:next_cursor])
        cursor = next_cursor
    return tuple(packages)


def render_queue_spec(
    *,
    emitted_at: datetime,
    source: GoalSource,
    spec_id: str,
    objective_state: ObjectiveProfileSyncStateRecord,
    profile: AcceptanceProfileRecord,
    completion_manifest: CompletionManifestDraftStateRecord,
    completion_manifest_path: str,
    contractor_profile: ContractorProfileArtifact | None = None,
) -> str:
    summary = _product_summary(source=source, profile=profile)
    capability_domains = _capability_domains(profile)
    progression_lines = _progression_lines(profile)
    acceptance_focus = _acceptance_focus(completion_manifest)
    required_artifact_paths = _required_artifact_paths(completion_manifest)
    implementation_surface_paths = _implementation_surface_paths(completion_manifest)
    verification_surface_paths = _verification_surface_paths(completion_manifest)
    product_plan = derive_goal_product_plan(
        source=source,
        profile=profile,
        contractor_profile=contractor_profile,
    )
    milestone_lines = [f"- {item}" for item in profile.milestones] or [f"- Deliver {summary}."]
    hard_blocker_lines = [f"- {item}" for item in profile.hard_blockers] or ["- No explicit blockers were recorded."]
    implementation_surface_lines = _render_surface_lines(
        tuple(completion_manifest.implementation_surfaces),
        empty_message="No implementation surfaces were declared.",
    )
    verification_surface_lines = _render_surface_lines(
        tuple(completion_manifest.verification_surfaces),
        empty_message="No verification surfaces were declared.",
    )
    required_artifact_lines = _render_surface_lines(
        tuple(completion_manifest.required_artifacts),
        empty_message="No governance artifacts were declared.",
    )
    implementation_plan_lines = [f"{index}. {step}" for index, step in enumerate(product_plan.phase_steps[:3], start=1)]
    contractor_scope_line = _contractor_scope_line(contractor_profile)
    contractor_grounding_lines = _contractor_grounding_lines(
        contractor_profile=contractor_profile,
        completion_manifest=completion_manifest,
    )
    timestamp = _isoformat_z(emitted_at)
    return "\n".join(
        [
            _FRONTMATTER_BOUNDARY,
            f"spec_id: {spec_id}",
            f"idea_id: {source.idea_id}",
            f"title: {source.title}",
            "status: proposed",
            "golden_version: 1",
            f"base_goal_sha256: {source.checksum_sha256}",
            "effort: 3",
            f"decomposition_profile: {source.decomposition_profile}",
            "depends_on_specs: []",
            "owner: research",
            f"created_at: {timestamp}",
            f"updated_at: {timestamp}",
            _FRONTMATTER_BOUNDARY,
            "",
            "## Summary",
            summary,
            "",
            "## Goals",
            f"- Deliver the product outcome captured in `{source.idea_id}` without collapsing it into GoalSpec-administration work.",
            f"- Preserve the bounded capability slice for {_format_domain_sentence(capability_domains)}.",
            (
                f"- Keep verification aligned to the profiled acceptance focus: "
                f"{'; '.join(acceptance_focus[:3])}."
                if acceptance_focus
                else "- Keep verification aligned to the profiled product acceptance path."
            ),
            *milestone_lines,
            "",
            "## Non-Goals",
            "- Spec Review approval decisions.",
            "- Task generation and pending shard emission.",
            "- Framework-maintenance work unrelated to the profiled product capabilities.",
            "",
            "## Scope",
            "### In Scope",
            f"- Product-facing implementation for {_format_domain_sentence(capability_domains)}.",
            *([contractor_scope_line] if contractor_scope_line else []),
            (
                f"- Acceptance and progression coverage for {progression_lines[0]}."
                if progression_lines
                else "- Acceptance and verification coverage for the profiled product objective."
            ),
            (
                f"- Implementation surfaces: {', '.join(f'`{path}`' for path in implementation_surface_paths)}."
                if implementation_surface_paths
                else "- Implementation surfaces remain bounded to the profiled product objective."
            ),
            (
                f"- Verification surfaces: {', '.join(f'`{path}`' for path in verification_surface_paths)}."
                if verification_surface_paths
                else "- Verification surfaces remain bounded to the profiled product objective."
            ),
            "",
            "### Out of Scope",
            "- Approval, merge, or backlog handoff.",
            "- Additional capability families beyond this bounded synthesized slice.",
            "- Governance-artifact maintenance outside traceability/reference preservation.",
            "",
            "## Capability Domains",
            *(f"- {item}" for item in capability_domains),
            *(["- No explicit capability domains were detected."] if not capability_domains else []),
            "",
            "## Product Surfaces",
            "### Implementation Surfaces",
            *implementation_surface_lines,
            "",
            "### Verification Surfaces",
            *verification_surface_lines,
            "",
            "## Governance Artifacts",
            *required_artifact_lines,
            "",
            *contractor_grounding_lines,
            "## Decomposition Readiness",
            (
                f"- The first bounded product slice centers on {progression_lines[0]}."
                if progression_lines
                else "- The first bounded product slice preserves the profiled objective summary and milestone ladder."
            ),
            f"- Declared decomposition profile: `{source.decomposition_profile}`.",
            f"- Planning profile: `{completion_manifest.planning_profile}`.",
            "- Later review/task-generation stages remain downstream of this synthesized product slice.",
            "",
            "## Constraints",
            "- Preserve the product nouns, capability scope, and validation duties from the staged goal and synced profile.",
            "- Keep research-plane runtime behavior restart-safe and deterministic.",
            "- Avoid pulling Spec Review or task generation into this run.",
            "",
            "## Implementation Plan",
            *implementation_plan_lines,
            "",
            "## Requirements Traceability (Req-ID Matrix)",
            (
                f"- `Req-ID: REQ-001` | Preserve the staged product objective and capability domains from `{source.idea_id}` | "
                f"`{objective_state.profile_path}`"
            ),
            (
                f"- `Req-ID: REQ-002` | Keep measurable validation and bounded output expectations attached to the synthesized slice | "
                f"`{completion_manifest_path}`"
            ),
            "",
            "## Assumptions Ledger",
            "- The bounded synthesized slice can cover the highest-priority product capability path without expanding the family in this run (source: inferred).",
            "- Synced semantic milestones and completion-manifest acceptance focus are sufficient evidence for first-slice spec authoring (source: confirmed).",
            *(
                [
                    (
                        "- Contractor-specific environment assumptions stay bounded to "
                        + ", ".join(f"`{item}`" for item in completion_manifest.contractor_environment_hints)
                        + " (source: contractor profile)."
                    )
                ]
                if completion_manifest.contractor_environment_hints
                else []
            ),
            "",
            "## Structured Decision Log",
            "| decision_id | phase_key | phase_priority | status | owner | rationale | timestamp |",
            "| --- | --- | --- | --- | --- | --- | --- |",
            f"| DEC-001 | PHASE_01 | P1 | proposed | research | Emit one bounded product-grounded spec slice before Spec Review expands scope | {timestamp} |",
            "",
            "## Interrogation Record",
            f"- Critic question: what is the smallest bounded spec slice that still delivers `{summary}` and its required verification?",
            "- Designer resolution: keep the first emitted slice focused on the profiled capability domains and measurable acceptance evidence.",
            "",
            "## Verification",
            *(
                f"- Confirm the synthesized work preserves this product check: {item}"
                for item in acceptance_focus[:3]
            ),
            *(
                [f"- Confirm the bounded slice advances this progression path: {progression_lines[0]}"]
                if progression_lines
                else []
            ),
            *(f"- `{command}`" for command in product_plan.verification_commands),
            "",
            "## Dependencies",
            f"- Objective profile: `{objective_state.profile_path}`",
            f"- Completion manifest draft: `{completion_manifest_path}`",
            *(
                f"- Governance artifact: `{path}`"
                for path in required_artifact_paths
            ),
            "",
            "## Risks and Mitigations",
            "- Risk: later stages may require additional capability decomposition. Mitigation: family state remains explicit and bounded for later runs.",
            *(
                [
                    (
                        "- Risk: unresolved contractor specialization could be overclaimed. Mitigation: keep "
                        + ", ".join(f"`{item}`" for item in completion_manifest.contractor_unresolved_specializations)
                        + " explicit and unsupported in this slice."
                    )
                ]
                if completion_manifest.contractor_unresolved_specializations
                else []
            ),
            "",
            "## Rollout and Rollback",
            "- Rollout: use this bounded product slice as the input to Spec Review.",
            "- Rollback: discard the emitted draft artifacts and rerun GoalSpec synthesis from the staged brief.",
            "",
            "## Open Questions",
            *hard_blocker_lines,
            "",
            "## References",
            f"- Staged goal: `{source.relative_source_path}`",
            f"- Objective profile JSON: `{objective_state.profile_path}`",
            f"- Completion manifest draft: `{completion_manifest_path}`",
            "",
        ]
    )


def render_phase_spec(
    *,
    emitted_at: datetime,
    source: GoalSource,
    spec_id: str,
    profile: AcceptanceProfileRecord,
    completion_manifest: CompletionManifestDraftStateRecord,
    completion_manifest_path: str,
    objective_profile_path: str,
    planned_spec_ids: tuple[str, ...] = (),
    contractor_profile: ContractorProfileArtifact | None = None,
) -> str:
    timestamp = _isoformat_z(emitted_at)
    summary = _product_summary(source=source, profile=profile)
    capability_domains = _capability_domains(profile)
    progression_lines = _progression_lines(profile)
    acceptance_focus = _acceptance_focus(completion_manifest)
    implementation_surface_paths = _implementation_surface_paths(completion_manifest)
    verification_surface_paths = _verification_surface_paths(completion_manifest)
    required_artifact_paths = _required_artifact_paths(completion_manifest)
    product_plan = derive_goal_product_plan(
        source=source,
        profile=profile,
        contractor_profile=contractor_profile,
    )
    planned_spec_lines = [f"- `{spec_id}`" for spec_id in planned_spec_ids] or ["- None."]
    implementation_surface_lines = _render_surface_lines(
        tuple(completion_manifest.implementation_surfaces),
        empty_message="No implementation surfaces were declared.",
    )
    verification_surface_lines = _render_surface_lines(
        tuple(completion_manifest.verification_surfaces),
        empty_message="No verification surfaces were declared.",
    )
    phase_packages = _partition_phase_steps(
        product_plan.phase_steps,
        package_count=minimum_phase_package_count(source.decomposition_profile),
    ) or (product_plan.phase_steps,)
    phase_package_lines: list[str] = []
    decision_rows = [
        "| decision_id | phase_key | phase_priority | status | owner | rationale | timestamp |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for package_index, package_steps in enumerate(phase_packages, start=1):
        phase_key = f"PHASE_{package_index:02d}"
        phase_priority = f"P{min(package_index, 3)}"
        phase_package_lines.extend(
            [
                f"### Phase Package {package_index:02d}",
                f"- Phase key: `{phase_key}`",
                f"- Phase priority: `{phase_priority}`",
                *[f"{index}. {step}" for index, step in enumerate(package_steps, start=1)],
                "",
            ]
        )
        decision_rows.append(
            (
                f"| DEC-PHASE-{package_index:03d} | {phase_key} | {phase_priority} | proposed | research | "
                f"Keep phase package {package_index} bounded to concrete implementation and verification surfaces before decomposition | {timestamp} |"
            )
        )

    work_plan_lines = [f"{index}. {step}" for index, step in enumerate(product_plan.phase_steps, start=1)]
    phase_package_summary = (
        f"- Render at least {len(phase_packages)} bounded phase package(s) for this `{source.decomposition_profile}` campaign before Taskmaster runs."
        if len(phase_packages) > 1
        else "- One bounded phase package is sufficient for this profile before Taskmaster runs."
    )
    contractor_scope_line = _contractor_scope_line(contractor_profile)
    contractor_grounding_lines = _contractor_grounding_lines(
        contractor_profile=contractor_profile,
        completion_manifest=completion_manifest,
    )
    return "\n".join(
        [
            _FRONTMATTER_BOUNDARY,
            f"phase_id: PHASE-{spec_id}-01",
            "phase_key: PHASE_01",
            "phase_priority: P1",
            f"parent_spec_id: {spec_id}",
            f"title: {source.title} Product Slice 01",
            "status: planned",
            "owner: research",
            f"created_at: {timestamp}",
            f"updated_at: {timestamp}",
            _FRONTMATTER_BOUNDARY,
            "",
            "## Objective",
            f"- Deliver the first bounded product capability slice for `{source.idea_id}` while preserving measurable verification for {summary}.",
            "",
            "## Entry Criteria",
            f"- Completion manifest draft exists at `{completion_manifest_path}`.",
            f"- Objective profile exists at `{objective_profile_path}`.",
            "- The bounded initial-family declaration is frozen for this synthesis pass.",
            "",
            "## Scope",
            "### In Scope",
            f"- Product-facing implementation for {_format_domain_sentence(capability_domains)}.",
            *([contractor_scope_line] if contractor_scope_line else []),
            (
                f"- Verification coverage for {progression_lines[0]}."
                if progression_lines
                else "- Verification coverage for the profiled product objective."
            ),
            (
                f"- Implementation surfaces: {', '.join(f'`{path}`' for path in implementation_surface_paths)}."
                if implementation_surface_paths
                else "- Repo deltas required to satisfy the bounded product slice."
            ),
            (
                f"- Verification surfaces: {', '.join(f'`{path}`' for path in verification_surface_paths)}."
                if verification_surface_paths
                else "- Verification surfaces required to prove the bounded product slice."
            ),
            "",
            "### Out of Scope",
            "- Spec Review decisions and task-card generation.",
            "- Product capability slices reserved for later planned specs in the same bounded family.",
            "- Governance-artifact editing except for trace references.",
            "",
            "## Implementation Surfaces",
            *implementation_surface_lines,
            "",
            "## Verification Surfaces",
            *verification_surface_lines,
            "",
            *contractor_grounding_lines,
            "## Phase Packages",
            phase_package_summary,
            *phase_package_lines,
            "## Work Plan",
            *work_plan_lines,
            "",
            "## Requirements Traceability (Req-ID)",
            (
                f"- `Req-ID: REQ-001` | Deliver the first product slice for {_format_domain_sentence(capability_domains)} | `{objective_profile_path}`"
            ),
            (
                f"- `Req-ID: REQ-002` | Preserve measurable validation for this bounded slice | `{completion_manifest_path}`"
            ),
            "",
            "## Assumptions Ledger",
            "- The first emitted slice can advance the highest-priority capability path without absorbing later planned slices (confidence: inferred).",
            (
                f"- Larger `{source.decomposition_profile}` campaigns keep their decomposition explicit by rendering {len(phase_packages)} bounded phase package(s) in this artifact (confidence: inferred)."
                if len(phase_packages) > 1
                else f"- Planning remains bounded to `{completion_manifest.planning_profile}` surfaces (confidence: inferred)."
            ),
            *(
                [
                    (
                        "- Contractor environment assumptions remain bounded to "
                        + ", ".join(f"`{item}`" for item in completion_manifest.contractor_environment_hints)
                        + " (confidence: contractor profile)."
                    )
                ]
                if completion_manifest.contractor_environment_hints
                else []
            ),
            "",
            "## Structured Decision Log",
            *decision_rows,
            "",
            "## Interrogation Notes",
            (
                "- This phase spec keeps the first emitted spec decomposition-ready by naming concrete repo surfaces before Taskmaster runs."
                if len(phase_packages) == 1
                else f"- This phase spec keeps `{source.decomposition_profile}` work split into bounded phase packages before Taskmaster runs."
            ),
            "",
            "## Verification",
            *(
                f"- Confirm this acceptance check passes: {item}"
                for item in acceptance_focus[:3]
            ),
            *(
                [f"- Confirm the slice advances this progression path: {progression_lines[0]}"]
                if progression_lines
                else []
            ),
            *(f"- `{command}`" for command in product_plan.verification_commands),
            "",
            "## Exit Criteria",
            "- The first bounded product slice is implemented or explicitly specified with measurable proof expectations and no family-scope drift.",
            (
                f"- Larger `{source.decomposition_profile}` campaigns remain split into the declared bounded phase packages above."
                if len(phase_packages) > 1
                else "- The declared phase package remains bounded and decomposition-ready."
            ),
            "",
            "## Handoff",
            "- Feed the queue spec, phase note, and frozen initial-family declaration into downstream review.",
            "- Planned later initial-family specs:",
            *planned_spec_lines,
            "",
            "## Risks",
            "- Broad goals may still require later planned slices; those must stay within the frozen bounded family or route to remediation later.",
            (
                f"- Governance artifacts remain references only: {', '.join(f'`{path}`' for path in required_artifact_paths)}."
                if required_artifact_paths
                else "- Governance artifacts remain references only."
            ),
            *(
                [
                    (
                        "- Unsupported contractor specialization stays unresolved: "
                        + ", ".join(f"`{item}`" for item in completion_manifest.contractor_unresolved_specializations)
                        + "."
                    )
                ]
                if completion_manifest.contractor_unresolved_specializations
                else []
            ),
            "",
        ]
    )


def render_synthesis_decision_record(
    *,
    emitted_at: datetime,
    run_id: str,
    source: GoalSource,
    spec_id: str,
    profile: AcceptanceProfileRecord,
    completion_manifest: CompletionManifestDraftStateRecord,
    completion_manifest_path: str,
    objective_profile_path: str,
    queue_spec_path: str,
    family_complete: bool,
    planned_spec_ids: tuple[str, ...] = (),
) -> str:
    timestamp = _isoformat_z(emitted_at)
    family_complete_text = "yes" if family_complete else "no"
    summary = _product_summary(source=source, profile=profile)
    capability_domains = _capability_domains(profile)
    progression_lines = _progression_lines(profile)
    acceptance_focus = _acceptance_focus(completion_manifest)
    return "\n".join(
        [
            f"# Spec Synthesis: {source.title}",
            "",
            f"- **Run-ID:** {run_id}",
            f"- **Goal-ID:** {source.idea_id}",
            f"- **Spec-ID:** {spec_id}",
            f"- **Updated-At:** {timestamp}",
            "",
            "## Critic Questions",
            f"- What is the smallest bounded spec slice that still preserves the product outcome `{summary}`?",
            (
                f"- Which capability domains must stay in the first synthesized slice for {_format_domain_sentence(capability_domains)}?"
                if capability_domains
                else "- Which product capability commitments must stay in the first synthesized slice?"
            ),
            (
                f"- Which validation obligations remain mandatory before later review/task-generation work can proceed: {'; '.join(acceptance_focus[:3])}?"
                if acceptance_focus
                else "- Which validation obligations remain mandatory before later review/task-generation work can proceed?"
            ),
            "",
            "## Designer Resolutions",
            "- Emit one bounded product-grounded spec family for this goal in the current synthesis pass.",
            (
                f"- Keep the first emitted slice centered on {_format_domain_sentence(capability_domains)}."
                if capability_domains
                else "- Keep the first emitted slice centered on the profiled product objective."
            ),
            (
                f"- Preserve measurable validation for {progression_lines[0]}."
                if progression_lines
                else "- Preserve measurable validation for the profiled product acceptance path."
            ),
            "",
            "## Retained Assumptions",
            "- Additional families are not required before Spec Review for this initial bounded product slice.",
            "",
            "## Contradictions",
            "- None observed during deterministic synthesis.",
            "",
            "## Clarifier Preservation Statement",
            "- The emitted queue/golden/phase artifacts preserve the bounded product decisions above without silently collapsing into GoalSpec-administration work.",
            "",
            "## Family Plan",
            (
                f"- Initial-family declaration: one emitted spec plus {len(planned_spec_ids)} planned later spec(s)."
                if planned_spec_ids
                else "- Initial-family declaration: one emitted spec."
            ),
            f"- Family complete after this run: `{family_complete_text}`",
            f"- Emitted spec: `{spec_id}` at `{queue_spec_path}`",
            (
                f"- Planned later specs: {', '.join(f'`{item}`' for item in planned_spec_ids)}"
                if planned_spec_ids
                else "- Planned later specs: none"
            ),
            "",
            "## References",
            f"- Staged goal: `{source.relative_source_path}`",
            f"- Objective profile: `{objective_profile_path}`",
            f"- Completion manifest: `{completion_manifest_path}`",
            "",
        ]
    )


def render_spec_review_questions(
    *,
    reviewed_at: datetime,
    run_id: str,
    goal_id: str,
    spec_id: str,
    title: str,
    queue_spec_path: str,
    stable_spec_paths: tuple[str, ...],
    findings: tuple[str, ...],
) -> str:
    stable_lines = [f"- `{path}`" for path in stable_spec_paths] or ["- No stable spec copies were discovered."]
    finding_lines = [f"- {finding}" for finding in findings] or [
        "- No blocking findings; the package is decomposition-ready as written."
    ]
    return "\n".join(
        [
            "# Spec Review Questions",
            "",
            f"- **Run-ID:** {run_id}",
            f"- **Goal-ID:** {goal_id}",
            f"- **Spec-ID:** {spec_id}",
            f"- **Title:** {title}",
            f"- **Reviewed-At:** {_isoformat_z(reviewed_at)}",
            f"- **Queue-Spec:** `{queue_spec_path}`",
            "",
            "## Critic Findings",
            *finding_lines,
            "",
            "## Stable Spec Inputs",
            *stable_lines,
            "",
        ]
    )


def render_spec_review_decision(
    *,
    reviewed_at: datetime,
    run_id: str,
    goal_id: str,
    spec_id: str,
    title: str,
    review_status: str,
    reviewed_path: str,
    stable_registry_path: str,
    lineage_path: str,
    findings: tuple[str, ...],
) -> str:
    finding_lines = [f"- {finding}" for finding in findings] or [
        "- No blocking findings were recorded during decomposition review."
    ]
    decision_line = (
        "- Approved for downstream decomposition without material spec edits in this run."
        if review_status != "blocked"
        else "- Blocked before downstream decomposition until the listed review findings are resolved."
    )
    return "\n".join(
        [
            "# Spec Review Decision",
            "",
            f"- **Run-ID:** {run_id}",
            f"- **Goal-ID:** {goal_id}",
            f"- **Spec-ID:** {spec_id}",
            f"- **Title:** {title}",
            f"- **Review-Status:** `{review_status}`",
            f"- **Reviewed-At:** {_isoformat_z(reviewed_at)}",
            f"- **Reviewed-Spec:** `{reviewed_path}`",
            f"- **Stable-Registry:** `{stable_registry_path}`",
            f"- **Lineage-Record:** `{lineage_path}`",
            "",
            "## Decision",
            decision_line,
            "",
            "## Findings",
            *finding_lines,
            "",
        ]
    )
