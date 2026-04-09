"""Goal-gap remediation-family staging helpers for marathon audit parity."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from ..markdown import write_text_atomic
from ..paths import RuntimePaths
from .audit_models import AuditGoalGapRemediationSelectionRecord, AuditGoalGapReviewRecord
from .audit_storage_helpers import _relative_path, _write_json_model
from .governance import resolve_family_governor_state
from .specs import GoalSpecFamilyState, write_goal_spec_family_state

_STOPWORDS = {
    "a",
    "an",
    "and",
    "be",
    "for",
    "from",
    "gap",
    "in",
    "into",
    "milestone",
    "of",
    "on",
    "or",
    "the",
    "to",
    "with",
}

_PROFILE_RANK = {
    "": -1,
    "trivial": 0,
    "simple": 1,
    "moderate": 2,
    "involved": 3,
    "complex": 4,
    "massive": 5,
}


def _tokenize(value: str) -> tuple[str, ...]:
    tokens: list[str] = []
    seen: set[str] = set()
    for token in re.findall(r"[a-z0-9]+", value.lower()):
        if token.isdigit() or len(token) < 2 or token in _STOPWORDS:
            continue
        if token in seen:
            continue
        seen.add(token)
        tokens.append(token)
    return tuple(tokens)


def _normalize_str_list(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    normalized: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = str(item).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        normalized.append(text)
    return tuple(normalized)


def _parse_frontmatter(text: str) -> dict[str, str]:
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---\n", 4)
    if end == -1:
        return {}
    payload: dict[str, str] = {}
    for raw_line in text[4:end].splitlines():
        if ":" not in raw_line:
            continue
        key, value = raw_line.split(":", 1)
        payload[key.strip()] = value.strip()
    return payload


def _derive_goal_identity(goal_path: Path) -> tuple[str, str]:
    text = goal_path.read_text(encoding="utf-8", errors="replace")
    frontmatter = _parse_frontmatter(text)
    for key in ("idea_id", "goal_id", "id"):
        token = frontmatter.get(key, "").strip()
        if token:
            return token, frontmatter.get("title", "").strip() or token
    stem = goal_path.stem.strip() or "goal-gap-remediation"
    return stem, stem.replace("-", " ").replace("_", " ").title()


def _selection_report_path(paths: RuntimePaths) -> Path:
    return paths.reports_dir / "goal_gap_remediation_selection.json"


def _selection_markdown_path(paths: RuntimePaths) -> Path:
    return paths.reports_dir / "goal_gap_remediation_selection.md"


def _family_policy_payload(paths: RuntimePaths) -> dict[str, object]:
    if not paths.objective_family_policy_file.exists():
        return {}
    return json.loads(paths.objective_family_policy_file.read_text(encoding="utf-8"))


def _registry_path(paths: RuntimePaths, policy_payload: dict[str, object]) -> Path:
    raw = str(policy_payload.get("overflow_registry_path", "")).strip() or "agents/.research_runtime/deferred_follow_ons.json"
    return paths.root / raw


def _load_deferred_specs(registry_path: Path) -> list[dict[str, object]]:
    if not registry_path.exists():
        return []
    payload = json.loads(registry_path.read_text(encoding="utf-8"))
    items = payload.get("deferred_specs", [])
    if not isinstance(items, list):
        return []
    return [dict(item) for item in items if isinstance(item, dict)]


def _milestone_tokens(milestone: object) -> tuple[str, ...]:
    capability_scope = getattr(milestone, "capability_scope", ())
    return _tokenize(
        " ".join(
            [
                str(getattr(milestone, "milestone_id", "")).strip(),
                str(getattr(milestone, "outcome", "")).strip(),
                *[str(item).strip() for item in capability_scope],
            ]
        )
    )


def _candidate_tokens(item: dict[str, object]) -> tuple[str, ...]:
    return _tokenize(
        " ".join(
            [
                str(item.get("title", "")).strip(),
                *[str(tag).strip() for tag in _normalize_str_list(item.get("capability_tags"))],
                *[str(tag).strip() for tag in _normalize_str_list(item.get("gap_tags"))],
            ]
        )
    )


def _direct_match(milestone: object, candidate: dict[str, object]) -> bool:
    milestone_id = str(getattr(milestone, "milestone_id", "")).strip().lower()
    capability_scope = {token.lower() for token in _normalize_str_list(candidate.get("capability_tags"))}
    gap_tags = {token.lower() for token in _normalize_str_list(candidate.get("gap_tags"))}
    milestone_scope = {token.lower() for token in getattr(milestone, "capability_scope", ())}
    if milestone_id and milestone_id in gap_tags:
        return True
    return bool(milestone_scope) and milestone_scope.issubset(capability_scope)


def _candidate_summary(milestone: object, candidate: dict[str, object]) -> dict[str, object]:
    milestone_token_set = set(_milestone_tokens(milestone))
    candidate_token_set = set(_candidate_tokens(candidate))
    overlap = sorted(milestone_token_set & candidate_token_set)
    return {
        "spec_id": str(candidate.get("spec_id", "")).strip(),
        "title": str(candidate.get("title", "")).strip(),
        "overlap_tokens": overlap,
        "overlap_score": len(overlap),
        "direct_match": _direct_match(milestone, candidate),
        "decomposition_profile": str(candidate.get("decomposition_profile", "")).strip().lower() or "moderate",
        "depends_on_specs": list(_normalize_str_list(candidate.get("depends_on_specs"))),
    }


def _candidate_sort_key(summary: dict[str, object]) -> tuple[int, int, str]:
    return (
        _PROFILE_RANK.get(str(summary.get("decomposition_profile", "")).strip(), 99),
        len(summary.get("depends_on_specs", [])),
        str(summary.get("spec_id", "")).strip(),
    )


def _synthesized_profile(milestone: object, *, unresolved_count: int) -> str:
    capability_scope = tuple(getattr(milestone, "capability_scope", ()) or ())
    matched_gap_count = len(tuple(getattr(milestone, "matched_gaps", ()) or ()))
    if unresolved_count == 1 and matched_gap_count <= 1 and len(capability_scope) <= 2:
        return "trivial"
    if matched_gap_count <= 1 and len(capability_scope) <= 2:
        return "simple"
    if len(capability_scope) >= 3 or matched_gap_count > 1:
        return "moderate"
    return "simple"


def _strongest_profile(items: list[dict[str, object]], *, default: str = "simple") -> str:
    if not items:
        return default
    best_rank = -1
    best_profile = default
    for item in items:
        profile = str(item.get("decomposition_profile", "")).strip().lower() or default
        rank = _PROFILE_RANK.get(profile, best_rank)
        if rank > best_rank:
            best_rank = rank
            best_profile = profile
    return best_profile


def _synthesized_item(milestone: object, *, unresolved_count: int) -> dict[str, object]:
    milestone_id = str(getattr(milestone, "milestone_id", "")).strip() or "MILESTONE"
    profile = _synthesized_profile(milestone, unresolved_count=unresolved_count)
    return {
        "remediation_id": f"REMED-{milestone_id}",
        "title": f"{milestone_id.replace('-', ' ')} remediation",
        "decomposition_profile": profile,
        "goal_gap_id": milestone_id,
        "capability_scope": list(getattr(milestone, "capability_scope", ()) or ()),
        "matched_gap_ids": [gap.gap_id for gap in getattr(milestone, "matched_gaps", ()) or ()],
        "depends_on_specs": [],
    }


def _build_source_idea_markdown(
    *,
    goal_id: str,
    goal_title: str,
    canonical_goal_path: str,
    goal_gap_review_path: str,
    selection_report_path: str,
    family_policy_path: str,
    deferred_registry_path: str,
    family_decomposition_profile: str,
    selections: list[dict[str, object]],
    selected_deferred: list[dict[str, object]],
    synthesized_specs: list[dict[str, object]],
    deferred_milestone_ids: list[str],
    emitted_at: datetime,
) -> str:
    lines = [
        "---",
        f"idea_id: {goal_id}__goal_gap_remediation",
        f"title: Goal Gap Remediation for {goal_title}",
        "status: staging",
        f"decomposition_profile: {family_decomposition_profile}",
        "family_phase: goal_gap_remediation",
        f"updated_at: {emitted_at.isoformat().replace('+00:00', 'Z')}",
        f"canonical_source_path: {canonical_goal_path}",
        f"source_path: {canonical_goal_path}",
        f"goal_gap_review_path: {goal_gap_review_path}",
        f"goal_gap_remediation_selection_path: {selection_report_path}",
        "---",
        "",
        "## Summary",
        "Queue-empty completion reached a deterministic PASS but still left unresolved semantic goal gaps. Stage only the bounded remediation work described below.",
        "",
        "## Inputs",
        f"- Canonical goal: `{canonical_goal_path}`",
        f"- Goal gap review: `{goal_gap_review_path}`",
        f"- Selection report: `{selection_report_path}`",
        f"- Family policy: `{family_policy_path}`",
        f"- Deferred registry: `{deferred_registry_path or 'none'}`",
        "",
        "## Remediation Guardrails",
        "- Keep the remediation family bounded to the selected unresolved milestones.",
        "- Preserve canonical lineage back to the original goal and goal-gap review artifacts.",
        "- Do not reopen unrestricted initial-family growth.",
        "",
        "## Selections",
    ]
    for selection in selections:
        lines.append(
            f"- `{selection['milestone_id']}` `{selection['selection_type']}`: {selection['selection_reason']}"
        )
        if selection.get("selected_spec_id"):
            lines.append(
                f"  - selected deferred follow-on: `{selection['selected_spec_id']}` `{selection['selected_title']}`"
            )
        synthesized = selection.get("synthesized_spec")
        if isinstance(synthesized, dict) and synthesized:
            lines.append(
                f"  - synthesized remediation: `{synthesized['remediation_id']}` `{synthesized['title']}` (`{synthesized['decomposition_profile']}`)"
            )
    if not selections:
        lines.append("- None.")
    if deferred_milestone_ids:
        lines.extend(
            [
                "",
                "## Deferred Milestones",
                "- Family cap limited this remediation family. These milestones remain deferred for later follow-on work:",
                *[f"  - `{item}`" for item in deferred_milestone_ids],
            ]
        )

    lines.extend(["", "## Selected Deferred Follow-Ons"])
    if not selected_deferred:
        lines.append("- None.")
    for item in selected_deferred:
        lines.append(f"- `{item['spec_id']}` - {item['title']}")

    lines.extend(["", "## Synthesized Remediation Requests"])
    if not synthesized_specs:
        lines.append("- None.")
    for item in synthesized_specs:
        lines.append(
            f"- `{item['remediation_id']}` - {item['title']} (`{item['decomposition_profile']}`)"
        )

    lines.extend(
        [
            "",
            "## Expected Output Contract",
            "- Emit only remediation specs needed to close the selected unresolved milestones.",
            "- Keep decomposition bounded to the selected remediation profile and family cap.",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def stage_goal_gap_remediation_family(
    paths: RuntimePaths,
    goal_gap_review: AuditGoalGapReviewRecord,
    *,
    run_id: str,
    emitted_at: datetime,
) -> AuditGoalGapRemediationSelectionRecord:
    """Stage a bounded goal-gap remediation family and initialize its GoalSpec state."""

    canonical_goal_path = paths.root / goal_gap_review.goal_path if goal_gap_review.goal_path else None
    if canonical_goal_path is None or not canonical_goal_path.exists():
        raise RuntimeError("goal-gap remediation staging requires a canonical goal path")

    goal_id, goal_title = _derive_goal_identity(canonical_goal_path)
    policy_payload = _family_policy_payload(paths)
    registry_path = _registry_path(paths, policy_payload)
    deferred_specs = _load_deferred_specs(registry_path)

    provisional_state = GoalSpecFamilyState(
        goal_id=goal_id,
        source_idea_path="",
        family_phase="goal_gap_remediation",
        family_complete=False,
        active_spec_id="",
        spec_order=(),
        specs={},
    )
    family_governor = resolve_family_governor_state(
        paths=paths,
        current_state=provisional_state,
        policy_payload=policy_payload,
    )
    cap = family_governor.applied_family_max_specs or max(1, len(goal_gap_review.unresolved_milestone_ids))

    unresolved = [milestone for milestone in goal_gap_review.milestones if milestone.status == "goal_gap"]
    selected_milestones = unresolved[:cap]
    deferred_milestones = unresolved[cap:]

    selections: list[dict[str, object]] = []
    selected_deferred_specs: list[dict[str, object]] = []
    synthesized_specs: list[dict[str, object]] = []
    selected_deferred_ids: list[str] = []
    synthesized_ids: list[str] = []

    for milestone in selected_milestones:
        candidate_summaries = [
            _candidate_summary(milestone, candidate)
            for candidate in deferred_specs
            if _candidate_summary(milestone, candidate)["overlap_score"] > 0
            or _candidate_summary(milestone, candidate)["direct_match"]
        ]
        direct_matches = [item for item in candidate_summaries if item["direct_match"]]
        if direct_matches:
            direct_matches.sort(key=_candidate_sort_key)
            selected = direct_matches[0]
            selected_deferred_ids.append(str(selected["spec_id"]))
            selected_deferred_specs.append(
                {
                    "spec_id": selected["spec_id"],
                    "title": selected["title"],
                    "decomposition_profile": selected["decomposition_profile"],
                    "depends_on_specs": selected["depends_on_specs"],
                    "matched_gap_ids": [gap.gap_id for gap in milestone.matched_gaps],
                }
            )
            selections.append(
                {
                    "milestone_id": milestone.milestone_id,
                    "outcome": milestone.outcome,
                    "capability_scope": list(milestone.capability_scope),
                    "selection_type": "deferred-direct",
                    "selected_spec_id": selected["spec_id"],
                    "selected_title": selected["title"],
                    "selection_reason": "selected deferred follow-on with deterministic capability overlap",
                    "candidate_spec_ids": [item["spec_id"] for item in sorted(candidate_summaries, key=_candidate_sort_key)],
                    "synthesized_spec": None,
                }
            )
            continue

        synthesized = _synthesized_item(milestone, unresolved_count=len(unresolved))
        synthesized_specs.append(synthesized)
        synthesized_ids.append(str(synthesized["remediation_id"]))
        selections.append(
            {
                "milestone_id": milestone.milestone_id,
                "outcome": milestone.outcome,
                "capability_scope": list(milestone.capability_scope),
                "selection_type": "synthetic",
                "selected_spec_id": "",
                "selected_title": "",
                "selection_reason": "no deferred follow-on adequately matched the unresolved semantic gap; synthesized a bounded remediation request",
                "candidate_spec_ids": [item["spec_id"] for item in sorted(candidate_summaries, key=_candidate_sort_key)],
                "synthesized_spec": synthesized,
            }
        )

    family_decomposition_profile = _strongest_profile(
        selected_deferred_specs + synthesized_specs,
        default="simple",
    )
    selection_report_path = _selection_report_path(paths)
    selection_markdown_path = _selection_markdown_path(paths)
    output_idea_path = paths.ideas_staging_dir / f"{goal_id}__goal-gap-remediation.md"

    record = AuditGoalGapRemediationSelectionRecord(
        run_id=run_id,
        emitted_at=emitted_at,
        audit_id=goal_gap_review.audit_id,
        title=goal_gap_review.title,
        goal_id=goal_id,
        goal_title=goal_title,
        overall_status=goal_gap_review.overall_status,
        canonical_goal_path=_relative_path(canonical_goal_path, relative_to=paths.root),
        goal_gap_review_path=goal_gap_review.review_path,
        family_policy_path=_relative_path(paths.objective_family_policy_file, relative_to=paths.root),
        deferred_follow_ons_path=(
            _relative_path(registry_path, relative_to=paths.root) if registry_path.exists() else ""
        ),
        selection_report_path=_relative_path(selection_report_path, relative_to=paths.root),
        selection_markdown_path=_relative_path(selection_markdown_path, relative_to=paths.root),
        output_idea_path=_relative_path(output_idea_path, relative_to=paths.root),
        family_decomposition_profile=family_decomposition_profile,
        applied_family_max_specs=cap,
        unresolved_milestone_ids=goal_gap_review.unresolved_milestone_ids,
        deferred_milestone_ids=tuple(item.milestone_id for item in deferred_milestones),
        selected_deferred_spec_ids=tuple(selected_deferred_ids),
        synthesized_remediation_ids=tuple(synthesized_ids),
        total_remediation_items=len(selected_deferred_ids) + len(synthesized_ids),
        selections=tuple(selections),
        selected_deferred_specs=tuple(selected_deferred_specs),
        synthesized_specs=tuple(synthesized_specs),
    )
    _write_json_model(selection_report_path, record)

    md_lines = [
        "# Goal Gap Remediation Selection",
        "",
        f"- Goal ID: `{record.goal_id}`",
        f"- Canonical goal: `{record.canonical_goal_path}`",
        f"- Goal gap review: `{record.goal_gap_review_path}`",
        f"- Total remediation items: `{record.total_remediation_items}`",
        f"- Applied family cap: `{record.applied_family_max_specs}`",
        "",
        "## Selections",
    ]
    for selection in record.selections:
        md_lines.append(
            f"- `{selection['milestone_id']}` `{selection['selection_type']}`: {selection['selection_reason']}"
        )
    if not record.selections:
        md_lines.append("- None.")
    write_text_atomic(selection_markdown_path, "\n".join(md_lines).rstrip() + "\n")

    write_text_atomic(
        output_idea_path,
        _build_source_idea_markdown(
            goal_id=record.goal_id,
            goal_title=record.goal_title,
            canonical_goal_path=record.canonical_goal_path,
            goal_gap_review_path=record.goal_gap_review_path,
            selection_report_path=record.selection_report_path,
            family_policy_path=record.family_policy_path,
            deferred_registry_path=record.deferred_follow_ons_path,
            family_decomposition_profile=record.family_decomposition_profile,
            selections=list(record.selections),
            selected_deferred=list(record.selected_deferred_specs),
            synthesized_specs=list(record.synthesized_specs),
            deferred_milestone_ids=list(record.deferred_milestone_ids),
            emitted_at=emitted_at,
        ),
    )

    family_state = GoalSpecFamilyState(
        goal_id=record.goal_id,
        source_idea_path=record.output_idea_path,
        family_phase="goal_gap_remediation",
        family_complete=False,
        active_spec_id="",
        spec_order=(),
        specs={},
        family_governor=family_governor,
        updated_at=emitted_at,
    )
    write_goal_spec_family_state(
        paths.goal_spec_family_state_file,
        family_state,
        updated_at=emitted_at,
    )
    return record


__all__ = ["stage_goal_gap_remediation_family"]
