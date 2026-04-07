"""Reusable GoalSpec scope-divergence diagnostics."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Literal
import re

from pydantic import Field, field_validator

from ..contracts import ContractModel, _normalize_datetime
from ..paths import RuntimePaths
from .goalspec_helpers import _normalize_path_token, _normalize_required_text
from .normalization_helpers import _normalize_optional_text, _normalize_text_sequence
from .persistence_helpers import _write_json_model


GOALSPEC_SCOPE_DIAGNOSTIC_SCHEMA_VERSION = "1.0"
GoalScopeKind = Literal["product", "framework_internal"]
ScopeDecision = Literal["aligned", "warning", "blocked"]

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_STOPWORDS = frozenset(
    {
        "about",
        "after",
        "again",
        "agent",
        "along",
        "also",
        "against",
        "build",
        "bounded",
        "carry",
        "check",
        "core",
        "deliver",
        "first",
        "focus",
        "from",
        "goal",
        "goals",
        "into",
        "keep",
        "lane",
        "later",
        "make",
        "need",
        "open",
        "phase",
        "plan",
        "proof",
        "review",
        "scope",
        "slice",
        "spec",
        "stage",
        "task",
        "through",
        "user",
        "validation",
        "verify",
        "with",
        "without",
        "work",
    }
)
_META_SCOPE_HINTS = (
    "goalspec",
    "objective profile",
    "objective sync",
    "completion manifest",
    "spec review",
    "task generation",
    "taskmaster",
    "taskaudit",
    "queue spec",
    "golden spec",
    "phase spec",
    "traceability",
    "research plane",
    "research runtime",
    "family policy",
    "queue governor",
    "stable spec",
    "runtime implementation slice",
)
_FRAMEWORK_SCOPE_HINTS = _META_SCOPE_HINTS + (
    "millrace",
    "dispatcher",
    "runtime",
    "engine",
    "control plane",
    "task store",
    "backlog",
)


class ScopeSurfaceDiagnostic(ContractModel):
    """Coverage and drift signals for one emitted surface."""

    surface_id: str
    coverage_ratio: float = Field(ge=0.0, le=1.0)
    matched_goal_tokens: tuple[str, ...] = ()
    missing_goal_tokens: tuple[str, ...] = ()
    meta_scope_hits: tuple[str, ...] = ()
    severe: bool = False
    excerpt: str = ""

    @field_validator("surface_id")
    @classmethod
    def validate_surface_id(cls, value: str) -> str:
        return _normalize_required_text(value, field_name="surface_id")

    @field_validator("matched_goal_tokens", "missing_goal_tokens", "meta_scope_hits", mode="before")
    @classmethod
    def normalize_text_sequence_fields(cls, value: tuple[str, ...] | list[str] | None) -> tuple[str, ...]:
        if not value:
            return ()
        return _normalize_text_sequence(value)

    @field_validator("excerpt", mode="before")
    @classmethod
    def normalize_excerpt(cls, value: str | None) -> str:
        return _normalize_optional_text(value)


class ScopeDivergenceRecord(ContractModel):
    """Persisted cross-stage scope-divergence decision."""

    schema_version: Literal["1.0"] = GOALSPEC_SCOPE_DIAGNOSTIC_SCHEMA_VERSION
    artifact_type: Literal["scope_divergence"] = "scope_divergence"
    run_id: str
    emitted_at: datetime
    goal_id: str
    title: str
    stage_name: str
    source_path: str
    expected_scope: GoalScopeKind
    decision: ScopeDecision
    reason: str
    summary: str
    surfaces: tuple[ScopeSurfaceDiagnostic, ...]

    @field_validator("emitted_at", mode="before")
    @classmethod
    def normalize_emitted_at(cls, value: datetime | str) -> datetime:
        return _normalize_datetime(value)

    @field_validator("run_id", "goal_id", "title", "stage_name", "source_path", "reason", "summary")
    @classmethod
    def validate_required_text(cls, value: str, info: object) -> str:
        field_name = getattr(info, "field_name", "value")
        return _normalize_required_text(value, field_name=field_name)

    @field_validator("source_path", mode="before")
    @classmethod
    def normalize_source_path(cls, value: str | Path) -> str:
        return _normalize_path_token(value)


def infer_goal_scope_kind(*, title: str, source_body: str, semantic_summary: str, capability_domains: tuple[str, ...]) -> GoalScopeKind:
    """Classify the expected scope lane from the staged goal and semantic profile."""

    combined = "\n".join((title, source_body, semantic_summary, *capability_domains)).casefold()
    framework_hits = sum(1 for hint in _FRAMEWORK_SCOPE_HINTS if hint in combined)
    if framework_hits >= 2:
        return "framework_internal"
    return "product"


def build_goal_anchor_tokens(
    *,
    title: str,
    source_body: str,
    semantic_summary: str,
    capability_domains: tuple[str, ...],
    progression_lines: tuple[str, ...],
) -> tuple[str, ...]:
    """Extract stable goal tokens used to compare later stage outputs."""

    ordered: list[str] = []
    seen: set[str] = set()
    for text in (title, semantic_summary, *capability_domains, *progression_lines, source_body):
        for token in _TOKEN_RE.findall(text.casefold()):
            if len(token) < 4 or token in _STOPWORDS:
                continue
            if any(token in hint for hint in _META_SCOPE_HINTS):
                continue
            if token in seen:
                continue
            seen.add(token)
            ordered.append(token)
    return tuple(ordered[:24])


def evaluate_scope_divergence(
    *,
    run_id: str,
    emitted_at: datetime,
    goal_id: str,
    title: str,
    stage_name: str,
    source_path: str,
    expected_scope: GoalScopeKind,
    goal_anchor_tokens: tuple[str, ...],
    surfaces: tuple[tuple[str, str], ...],
) -> ScopeDivergenceRecord:
    """Build one deterministic divergence decision from the provided stage surfaces."""

    surface_records: list[ScopeSurfaceDiagnostic] = []
    severe_surfaces: list[str] = []
    for surface_id, text in surfaces:
        normalized_text = _normalize_surface_text(text)
        matched = tuple(token for token in goal_anchor_tokens if f" {token} " in normalized_text)
        missing = tuple(token for token in goal_anchor_tokens if token not in matched)
        meta_hits = tuple(hint for hint in _META_SCOPE_HINTS if hint in normalized_text)
        coverage_ratio = (len(matched) / len(goal_anchor_tokens)) if goal_anchor_tokens else 1.0
        severe = _surface_is_severe(
            expected_scope=expected_scope,
            coverage_ratio=coverage_ratio,
            meta_hits=meta_hits,
            matched_count=len(matched),
        )
        if severe:
            severe_surfaces.append(surface_id)
        surface_records.append(
            ScopeSurfaceDiagnostic(
                surface_id=surface_id,
                coverage_ratio=coverage_ratio,
                matched_goal_tokens=matched,
                missing_goal_tokens=missing,
                meta_scope_hits=meta_hits,
                severe=severe,
                excerpt=_excerpt(text),
            )
        )

    if severe_surfaces:
        reason = "severe_product_scope_divergence"
        summary = (
            f"{stage_name} drifted off the staged product scope on {', '.join(severe_surfaces)} "
            f"and now emphasizes meta GoalSpec administration language instead of the goal anchors."
        )
        decision: ScopeDecision = "blocked"
    elif expected_scope == "framework_internal":
        reason = "framework_internal_scope_aligned"
        summary = f"{stage_name} remains aligned with an explicitly internal/framework objective."
        decision = "aligned"
    else:
        reason = "goal_scope_aligned"
        summary = f"{stage_name} preserves the staged product scope anchors across the checked surfaces."
        decision = "aligned"

    return ScopeDivergenceRecord(
        run_id=run_id,
        emitted_at=emitted_at,
        goal_id=goal_id,
        title=title,
        stage_name=stage_name,
        source_path=source_path,
        expected_scope=expected_scope,
        decision=decision,
        reason=reason,
        summary=summary,
        surfaces=tuple(surface_records),
    )


def write_scope_divergence_record(paths: RuntimePaths, record: ScopeDivergenceRecord) -> str:
    """Persist one scope-divergence diagnostic under the GoalSpec runtime tree."""

    output_path = paths.goalspec_runtime_dir / "scope_divergence" / f"{record.run_id}__{record.stage_name}.json"
    _write_json_model(output_path, record, create_parent=True)
    return output_path.relative_to(paths.root).as_posix()


def _normalize_surface_text(text: str) -> str:
    return f" {' '.join(_TOKEN_RE.findall(text.casefold()))} "


def _surface_is_severe(
    *,
    expected_scope: GoalScopeKind,
    coverage_ratio: float,
    meta_hits: tuple[str, ...],
    matched_count: int,
) -> bool:
    if expected_scope != "product":
        return False
    if matched_count == 0 and len(meta_hits) >= 2:
        return True
    if matched_count <= 1 and len(meta_hits) >= 2:
        return True
    return coverage_ratio < 0.5 and len(meta_hits) >= 2


def _excerpt(text: str, *, max_length: int = 180) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= max_length:
        return normalized
    return normalized[: max_length - 3].rstrip() + "..."


__all__ = [
    "GOALSPEC_SCOPE_DIAGNOSTIC_SCHEMA_VERSION",
    "GoalScopeKind",
    "ScopeDecision",
    "ScopeDivergenceRecord",
    "ScopeSurfaceDiagnostic",
    "build_goal_anchor_tokens",
    "evaluate_scope_divergence",
    "infer_goal_scope_kind",
    "write_scope_divergence_record",
]
