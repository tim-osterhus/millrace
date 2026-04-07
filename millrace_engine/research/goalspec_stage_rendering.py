"""Shared GoalSpec stage rendering helpers."""

from __future__ import annotations

from datetime import datetime

from .goalspec import (
    AcceptanceProfileRecord,
    CompletionManifestDraftStateRecord,
    GoalSource,
    ObjectiveProfileSyncStateRecord,
)
from .goalspec_helpers import (
    _FRONTMATTER_BOUNDARY,
    _first_paragraph,
    _isoformat_z,
)


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


def _required_output_paths(completion_manifest: CompletionManifestDraftStateRecord) -> tuple[str, ...]:
    return _dedupe_ordered([artifact.path for artifact in completion_manifest.required_outputs])


def _format_domain_sentence(domains: tuple[str, ...]) -> str:
    if not domains:
        return "the profiled product objective"
    if len(domains) == 1:
        return domains[0]
    if len(domains) == 2:
        return f"{domains[0]} and {domains[1]}"
    return f"{', '.join(domains[:-1])}, and {domains[-1]}"


def render_queue_spec(
    *,
    emitted_at: datetime,
    source: GoalSource,
    spec_id: str,
    objective_state: ObjectiveProfileSyncStateRecord,
    profile: AcceptanceProfileRecord,
    completion_manifest: CompletionManifestDraftStateRecord,
    completion_manifest_path: str,
) -> str:
    summary = _product_summary(source=source, profile=profile)
    capability_domains = _capability_domains(profile)
    progression_lines = _progression_lines(profile)
    acceptance_focus = _acceptance_focus(completion_manifest)
    required_output_paths = _required_output_paths(completion_manifest)
    milestone_lines = [f"- {item}" for item in profile.milestones] or [f"- Deliver {summary}."]
    hard_blocker_lines = [f"- {item}" for item in profile.hard_blockers] or ["- No explicit blockers were recorded."]
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
            f"- Product-facing implementation and verification work for {_format_domain_sentence(capability_domains)}.",
            (
                f"- Acceptance and progression coverage for {progression_lines[0]}."
                if progression_lines
                else "- Acceptance and verification coverage for the profiled product objective."
            ),
            (
                f"- Bounded repo deltas needed to satisfy these outputs: {', '.join(f'`{path}`' for path in required_output_paths)}."
                if required_output_paths
                else "- Bounded repo deltas required to satisfy the profiled product objective."
            ),
            "",
            "### Out of Scope",
            "- Approval, merge, or backlog handoff.",
            "- Additional capability families beyond this bounded synthesized slice.",
            "",
            "## Capability Domains",
            *(f"- {item}" for item in capability_domains),
            *(["- No explicit capability domains were detected."] if not capability_domains else []),
            "",
            "## Decomposition Readiness",
            (
                f"- The first bounded product slice centers on {progression_lines[0]}."
                if progression_lines
                else "- The first bounded product slice preserves the profiled objective summary and milestone ladder."
            ),
            f"- Declared decomposition profile: `{source.decomposition_profile}`.",
            "- Later review/task-generation stages remain downstream of this synthesized product slice.",
            "",
            "## Constraints",
            "- Preserve the product nouns, capability scope, and validation duties from the staged goal and synced profile.",
            "- Keep research-plane runtime behavior restart-safe and deterministic.",
            "- Avoid pulling Spec Review or task generation into this run.",
            "",
            "## Implementation Plan",
            "1. Implement the bounded product capability slice carried by the staged goal and semantic milestones.",
            "2. Add or adjust verification coverage for the profiled acceptance focus and progression path.",
            "3. Persist the bounded synthesized package and decision record for downstream review.",
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
            "- `python3 -c \"from pathlib import Path; assert Path('agents/audit/completion_manifest.json').exists(); assert Path('agents/reports/acceptance_profiles').exists()\"`",
            "- `python3 -c \"from pathlib import Path; assert any(Path('agents/specs/stable/golden').glob('*.md')); assert any(Path('agents/specs/stable/phase').glob('*.md'))\"`",
            "",
            "## Dependencies",
            f"- Objective profile: `{objective_state.profile_path}`",
            f"- Completion manifest draft: `{completion_manifest_path}`",
            "",
            "## Risks and Mitigations",
            "- Risk: later stages may require additional capability decomposition. Mitigation: family state remains explicit and bounded for later runs.",
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
    spec_id: str,
    title: str,
    completion_manifest_path: str,
    objective_profile_path: str,
) -> str:
    timestamp = _isoformat_z(emitted_at)
    return "\n".join(
        [
            _FRONTMATTER_BOUNDARY,
            f"phase_id: PHASE-{spec_id}-01",
            "phase_key: PHASE_01",
            "phase_priority: P1",
            f"parent_spec_id: {spec_id}",
            f"title: {title} Implementation Foundation",
            "status: planned",
            "owner: research",
            f"created_at: {timestamp}",
            f"updated_at: {timestamp}",
            _FRONTMATTER_BOUNDARY,
            "",
            "## Objective",
            "- Carry the drafted GoalSpec package into a reviewable runtime implementation slice.",
            "",
            "## Entry Criteria",
            f"- Completion manifest draft exists at `{completion_manifest_path}`.",
            f"- Objective profile exists at `{objective_profile_path}`.",
            "",
            "## Scope",
            "### In Scope",
            "- Finalize the bounded GoalSpec runtime surfaces declared by the draft spec.",
            "- Preserve traceability between the staged goal, completion manifest, and emitted spec artifacts.",
            "",
            "### Out of Scope",
            "- Spec Review approval.",
            "- Task generation.",
            "",
            "## Work Plan",
            "1. Validate the completion-manifest draft and objective profile against the emitted queue spec.",
            "2. Implement the bounded GoalSpec runtime deliverables declared by the draft package.",
            "3. Run targeted verification and hand the package to Spec Review.",
            "",
            "## Requirements Traceability (Req-ID)",
            f"- `Req-ID: REQ-001` traced through `{completion_manifest_path}`.",
            f"- `Req-ID: REQ-002` traced through `{objective_profile_path}`.",
            "",
            "## Assumptions Ledger",
            "- The emitted draft package remains a single-spec family through review (confidence: inferred).",
            "",
            "## Structured Decision Log",
            "| decision_id | phase_key | phase_priority | status | owner | rationale | timestamp |",
            "| --- | --- | --- | --- | --- | --- | --- |",
            f"| DEC-PHASE-001 | PHASE_01 | P1 | proposed | research | Preserve bounded Run 03 scope for the first draft spec family | {timestamp} |",
            "",
            "## Interrogation Notes",
            "- This phase exists to keep implementation work bounded and reviewable after draft synthesis.",
            "",
            "## Verification",
            "- Draft artifacts and family state remain mutually traceable.",
            "",
            "## Exit Criteria",
            "- The package is ready for Spec Review without inventing new scope.",
            "",
            "## Handoff",
            "- Feed the queue spec and this phase note into the next research stage.",
            "",
            "## Risks",
            "- Review may discover a need for additional later specs; if so, record them explicitly in a later run.",
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
            "- Emit one bounded product-grounded spec family for this goal in Run 03.",
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
            "- Initial-family declaration: one emitted spec.",
            f"- Family complete after this run: `{family_complete_text}`",
            f"- Emitted spec: `{spec_id}` at `{queue_spec_path}`",
            "- Planned later specs: none",
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
) -> str:
    stable_lines = [f"- `{path}`" for path in stable_spec_paths] or ["- No stable spec copies were discovered."]
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
            "- No material delta was required to make this package decomposition-ready.",
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
) -> str:
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
            "- Approved for downstream decomposition without material spec edits in this run.",
            "",
        ]
    )
