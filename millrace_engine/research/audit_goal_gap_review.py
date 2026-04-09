"""Goal-gap review helpers for marathon-style completion audits."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any

from ..contracts import ObjectiveContract
from ..markdown import write_text_atomic
from ..paths import RuntimePaths
from .audit_models import (
    AuditGoalGapMatch,
    AuditGoalGapMilestoneReview,
    AuditGoalGapReviewRecord,
    AuditQueueRecord,
)
from .audit_storage_helpers import _relative_path, _write_json_model

_STOPWORDS = {
    "and",
    "for",
    "from",
    "gap",
    "into",
    "milestone",
    "not",
    "that",
    "the",
    "then",
    "this",
    "with",
}


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip()).casefold()


def _tokenize(value: str) -> tuple[str, ...]:
    tokens: list[str] = []
    seen: set[str] = set()
    for token in re.findall(r"[A-Za-z0-9]+", value.casefold()):
        if token.isdigit() or len(token) < 3 or token in _STOPWORDS:
            continue
        if token in seen:
            continue
        seen.add(token)
        tokens.append(token)
    return tuple(tokens)


def _goal_gap_review_json_path(paths: RuntimePaths) -> Path:
    return paths.reports_dir / "goal_gap_review.json"


def _goal_gap_review_markdown_path(paths: RuntimePaths) -> Path:
    return paths.reports_dir / "goal_gap_review.md"


def _open_gap_rows(gaps_path: Path) -> tuple[dict[str, str], ...]:
    if not gaps_path.exists():
        return ()

    rows: list[dict[str, str]] = []
    in_open_section = False
    for raw_line in gaps_path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = raw_line.strip()
        if stripped.startswith("## Open Gaps"):
            in_open_section = True
            continue
        if in_open_section and stripped.startswith("## "):
            break
        if not in_open_section or not stripped.startswith("|"):
            continue
        cells = [cell.strip() for cell in stripped.split("|")[1:-1]]
        if len(cells) < 7:
            continue
        gap_id = cells[0]
        if not gap_id or gap_id in {"Gap ID", "---"}:
            continue
        if cells[5].casefold() != "open":
            continue
        rows.append(
            {
                "gap_id": gap_id,
                "title": cells[1],
                "area": cells[2],
                "owner": cells[3],
                "severity": cells[4],
                "notes": cells[6],
            }
        )
    return tuple(rows)


def _milestone_payloads(objective_contract: ObjectiveContract) -> tuple[dict[str, Any], ...]:
    profile = objective_contract.objective_profile if isinstance(objective_contract.objective_profile, dict) else {}
    semantic_profile = profile.get("semantic_profile")
    payloads: list[dict[str, Any]] = []

    if isinstance(semantic_profile, dict):
        raw_semantic_milestones = semantic_profile.get("milestones")
        if isinstance(raw_semantic_milestones, list):
            for index, item in enumerate(raw_semantic_milestones, start=1):
                if isinstance(item, dict):
                    payloads.append(dict(item))
                    continue
                text = str(item).strip()
                if text:
                    payloads.append({"id": f"MILESTONE-{index:03d}", "outcome": text})
    if payloads:
        return tuple(payloads)

    raw_profile_milestones = profile.get("milestones")
    if isinstance(raw_profile_milestones, list):
        for index, item in enumerate(raw_profile_milestones, start=1):
            if isinstance(item, dict):
                payloads.append(dict(item))
                continue
            text = str(item).strip()
            if text:
                payloads.append({"id": f"MILESTONE-{index:03d}", "outcome": text})
    return tuple(payloads)


def _capability_scope(milestone: dict[str, Any]) -> tuple[str, ...]:
    raw_scope = milestone.get("capability_scope")
    if not isinstance(raw_scope, list):
        return ()
    normalized: list[str] = []
    for item in raw_scope:
        text = str(item).strip()
        if text:
            normalized.append(text)
    return tuple(normalized)


def _matched_terms(milestone: dict[str, Any], gap: dict[str, str]) -> tuple[str, ...]:
    milestone_id = str(milestone.get("id", "")).strip()
    outcome = str(milestone.get("outcome", "")).strip()
    capability_scope = _capability_scope(milestone)
    gap_text = " ".join(
        value
        for value in (
            gap.get("gap_id", ""),
            gap.get("title", ""),
            gap.get("area", ""),
            gap.get("owner", ""),
            gap.get("severity", ""),
            gap.get("notes", ""),
        )
        if value
    )
    normalized_gap_text = _normalize_text(gap_text)

    matched: list[str] = []
    if milestone_id and milestone_id.casefold() in normalized_gap_text:
        matched.append(milestone_id)
    for phrase in (outcome, *capability_scope):
        normalized_phrase = _normalize_text(phrase)
        if len(normalized_phrase) >= 8 and normalized_phrase in normalized_gap_text:
            matched.append(phrase)

    milestone_tokens = _tokenize(" ".join((milestone_id, outcome, *capability_scope)))
    gap_tokens = set(_tokenize(gap_text))
    overlap = [token for token in milestone_tokens if token in gap_tokens]
    if len(overlap) >= 2:
        matched.extend(overlap[:5])

    deduped: list[str] = []
    seen: set[str] = set()
    for item in matched:
        normalized = item.strip()
        if not normalized:
            continue
        key = normalized.casefold()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(normalized)
    return tuple(deduped)


def execute_audit_goal_gap_review(
    paths: RuntimePaths,
    record: AuditQueueRecord,
    *,
    run_id: str,
    emitted_at: datetime,
    objective_contract: ObjectiveContract,
    gate_decision_path: str,
    completion_decision_path: str,
) -> AuditGoalGapReviewRecord | None:
    """Write a durable milestone-to-gap review for queue-empty completion audits."""

    milestones = _milestone_payloads(objective_contract)
    if not milestones:
        return None

    review_path = _goal_gap_review_json_path(paths)
    markdown_path = _goal_gap_review_markdown_path(paths)
    gap_rows = _open_gap_rows(paths.agents_dir / "gaps.md")
    milestone_reviews: list[AuditGoalGapMilestoneReview] = []
    unresolved_ids: list[str] = []

    for index, milestone in enumerate(milestones, start=1):
        milestone_id = str(milestone.get("id", "")).strip() or f"MILESTONE-{index:03d}"
        outcome = str(milestone.get("outcome", "")).strip() or f"Milestone {index}"
        capability_scope = _capability_scope(milestone)

        matched_gaps: list[AuditGoalGapMatch] = []
        for gap in gap_rows:
            matched_terms = _matched_terms(milestone, gap)
            if not matched_terms:
                continue
            matched_gaps.append(
                AuditGoalGapMatch(
                    gap_id=gap["gap_id"],
                    title=gap["title"],
                    area=gap.get("area"),
                    owner=gap.get("owner"),
                    severity=gap.get("severity"),
                    notes=gap.get("notes"),
                    matched_terms=matched_terms,
                )
            )

        if matched_gaps:
            unresolved_ids.append(milestone_id)
            milestone_reviews.append(
                AuditGoalGapMilestoneReview(
                    milestone_id=milestone_id,
                    outcome=outcome,
                    capability_scope=capability_scope,
                    status="goal_gap",
                    matched_gap_count=len(matched_gaps),
                    matched_gaps=tuple(matched_gaps),
                )
            )
            continue

        milestone_reviews.append(
            AuditGoalGapMilestoneReview(
                milestone_id=milestone_id,
                outcome=outcome,
                capability_scope=capability_scope,
                status="satisfied",
                matched_gap_count=0,
                matched_gaps=(),
            )
        )

    overall_status = "goal_gaps" if unresolved_ids else "satisfied"
    if not unresolved_ids and gap_rows:
        overall_status = "audit_gaps_only"

    review = AuditGoalGapReviewRecord(
        run_id=run_id,
        emitted_at=emitted_at,
        audit_id=record.audit_id,
        title=record.title,
        trigger=record.trigger,
        source_path=_relative_path(record.source_path, relative_to=paths.root),
        objective_contract_path=_relative_path(paths.objective_contract_file, relative_to=paths.root),
        profile_id=str(objective_contract.objective_profile.get("profile_id", "")).strip() or None,
        goal_path=str(objective_contract.objective_profile.get("source_path", "")).strip() or None,
        deterministic_decision="PASS",
        gate_decision_path=gate_decision_path,
        completion_decision_path=completion_decision_path,
        review_path=_relative_path(review_path, relative_to=paths.root),
        markdown_path=_relative_path(markdown_path, relative_to=paths.root),
        overall_status=overall_status,
        open_gap_count=len(gap_rows),
        goal_gap_count=len(unresolved_ids),
        unresolved_milestone_ids=tuple(unresolved_ids),
        milestones=tuple(milestone_reviews),
    )
    _write_json_model(review_path, review)

    lines = [
        "# Goal Gap Review",
        "",
        f"- Audit: `{record.audit_id}` :: {record.title}",
        f"- Trigger: `{record.trigger.value}`",
        f"- Deterministic decision: `{review.deterministic_decision}`",
        f"- Overall status: `{review.overall_status}`",
        f"- Open gap rows: `{review.open_gap_count}`",
        f"- Unresolved milestones: `{review.goal_gap_count}`",
        "",
        "## Milestones",
    ]
    for milestone in review.milestones:
        lines.append(f"- `{milestone.milestone_id}` `{milestone.status}`: {milestone.outcome}")
        for gap in milestone.matched_gaps:
            terms = ", ".join(gap.matched_terms) if gap.matched_terms else "matched text"
            lines.append(f"  - `{gap.gap_id}` {gap.title} :: {terms}")
    write_text_atomic(markdown_path, "\n".join(lines).rstrip() + "\n")
    return review


__all__ = ["execute_audit_goal_gap_review"]
