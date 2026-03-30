"""Executable GoalSpec stage helpers for Goal Intake through Spec Review."""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Literal
import json
import re

from pydantic import field_validator

from ..compiler_models import FrozenLoopPlan, FrozenStagePlan
from ..contracts import ContractModel, ObjectiveContract, ResearchStatus, _normalize_datetime, _normalize_path
from ..markdown import write_text_atomic
from ..paths import RuntimePaths
from .dispatcher import ResearchDispatchError
from .governance import (
    InitialFamilyPolicyPinDecision,
    apply_initial_family_policy_pin,
    build_queue_governor_report,
    build_reused_spec_synthesis_family_state,
    evaluate_initial_family_plan_guard,
    evaluate_spec_synthesis_idempotency,
    resolve_family_governor_state,
)
from .specs import (
    GoalSpecDecompositionProfile,
    GoalSpecFamilySpecState,
    GoalSpecFamilyState,
    GoalSpecLineageRecord,
    GoalSpecReviewRecord,
    build_initial_family_plan_snapshot,
    load_goal_spec_family_state,
    load_stable_spec_registry,
    refresh_stable_spec_registry,
    write_goal_spec_family_state,
)
from .state import ResearchCheckpoint, ResearchQueueFamily, ResearchQueueOwnership


GOALSPEC_ARTIFACT_SCHEMA_VERSION = "1.0"
_TOKEN_RE = re.compile(r"[^a-z0-9]+")
_FRONTMATTER_BOUNDARY = "---"
_SUPPORTED_DECOMPOSITION_PROFILES = {
    "",
    "trivial",
    "simple",
    "moderate",
    "involved",
    "complex",
    "massive",
}
_TRAILING_NUMBER_RE = re.compile(r"(\d+)$")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _isoformat_z(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _normalize_required_text(value: str, *, field_name: str) -> str:
    normalized = " ".join(value.strip().split())
    if not normalized:
        raise ValueError(f"{field_name} may not be empty")
    return normalized


def _normalize_optional_text(value: str | None) -> str:
    if value is None:
        return ""
    return " ".join(value.strip().split())


def _normalize_path_token(value: str | Path | None) -> str:
    if value is None:
        return ""
    normalized = _normalize_path(value)
    if normalized is None:
        return ""
    return normalized.as_posix()


def _slugify(value: str) -> str:
    slug = _TOKEN_RE.sub("-", value.strip().lower()).strip("-")
    return slug or "goal"


def _sha256_text(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()


def _resolve_path_token(path_token: str | Path, *, relative_to: Path) -> Path:
    candidate = Path(path_token)
    if candidate.is_absolute():
        return candidate
    return relative_to / candidate


def _load_json_object(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise GoalSpecExecutionError(f"{path.as_posix()} must contain a JSON object")
    return payload


def _spec_id_for_goal(goal_id: str) -> str:
    normalized = goal_id.strip()
    if not normalized:
        return "SPEC-GOAL"
    if normalized.upper().startswith("SPEC-"):
        return normalized.upper()
    if normalized.upper().startswith("IDEA-"):
        return f"SPEC-{normalized[5:].upper()}"
    match = _TRAILING_NUMBER_RE.search(normalized)
    if match is not None:
        return f"SPEC-{match.group(1)}"
    return f"SPEC-{_slugify(normalized).upper()}"


def _archive_filename_for_execution(source_path: Path, *, run_id: str, checksum_sha256: str) -> str:
    suffix = "".join(source_path.suffixes)
    stem = source_path.name[: -len(suffix)] if suffix else source_path.name
    run_token = _slugify(run_id)
    checksum_token = checksum_sha256[:12]
    return f"{stem}__{run_token}__{checksum_token}{suffix}"


def _relative_path(path: Path, *, relative_to: Path) -> str:
    try:
        return path.relative_to(relative_to).as_posix()
    except ValueError:
        return path.as_posix()


def _write_json_model(path: Path, model: ContractModel) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.loads(model.model_dump_json(exclude_none=False))
    write_text_atomic(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _load_json_model(path: Path, model_cls: type[ContractModel]) -> ContractModel:
    return model_cls.model_validate(_load_json_object(path))


def _split_frontmatter(text: str) -> tuple[dict[str, str], str]:
    if not text.startswith(f"{_FRONTMATTER_BOUNDARY}\n"):
        return {}, text
    end = text.find(f"\n{_FRONTMATTER_BOUNDARY}\n", len(_FRONTMATTER_BOUNDARY) + 1)
    if end == -1:
        return {}, text

    frontmatter: dict[str, str] = {}
    for raw_line in text[len(_FRONTMATTER_BOUNDARY) + 1:end].splitlines():
        if ":" not in raw_line:
            continue
        key, value = raw_line.split(":", 1)
        frontmatter[key.strip()] = value.strip()
    body = text[end + len(f"\n{_FRONTMATTER_BOUNDARY}\n") :]
    return frontmatter, body


def _first_heading(body: str) -> str:
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip()
    return ""


def _first_paragraph(body: str) -> str:
    paragraph: list[str] = []
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped:
            if paragraph:
                break
            continue
        if stripped.startswith("#"):
            continue
        paragraph.append(stripped)
    return " ".join(paragraph).strip()


def _markdown_section(body: str, heading: str) -> str:
    target = heading.strip().casefold()
    current: list[str] = []
    capture = False
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            if capture:
                break
            capture = stripped[3:].strip().casefold() == target
            continue
        if capture:
            current.append(line.rstrip())
    return "\n".join(current).strip()


def _normalize_decomposition_profile(value: str | None) -> GoalSpecDecompositionProfile:
    normalized = (value or "").strip().lower()
    if normalized not in _SUPPORTED_DECOMPOSITION_PROFILES:
        return "simple"
    return normalized  # type: ignore[return-value]


class GoalSpecExecutionError(ResearchDispatchError):
    """Raised when GoalSpec execution cannot complete safely."""


class GoalSource(ContractModel):
    """Normalized source metadata for one GoalSpec intake artifact."""

    source_path: str
    relative_source_path: str
    queue_family: ResearchQueueFamily = ResearchQueueFamily.GOALSPEC
    idea_id: str
    title: str
    decomposition_profile: GoalSpecDecompositionProfile = "simple"
    frontmatter: dict[str, str] = {}
    body: str
    checksum_sha256: str

    @field_validator("source_path", "relative_source_path", "idea_id", "title", "checksum_sha256")
    @classmethod
    def validate_required_text(cls, value: str, info: object) -> str:
        field_name = getattr(info, "field_name", "value")
        return _normalize_required_text(value, field_name=field_name)

    @field_validator("body")
    @classmethod
    def normalize_body(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("body may not be empty")
        return normalized


class GoalIntakeRecord(ContractModel):
    """Durable runtime record for one Goal Intake execution."""

    schema_version: Literal["1.0"] = GOALSPEC_ARTIFACT_SCHEMA_VERSION
    artifact_type: Literal["goal_intake"] = "goal_intake"
    run_id: str
    emitted_at: datetime
    source_path: str
    archived_source_path: str = ""
    research_brief_path: str
    idea_id: str
    title: str
    decomposition_profile: GoalSpecDecompositionProfile = "simple"
    source_checksum_sha256: str

    @field_validator("emitted_at", mode="before")
    @classmethod
    def normalize_emitted_at(cls, value: datetime | str) -> datetime:
        return _normalize_datetime(value)

    @field_validator(
        "run_id",
        "source_path",
        "research_brief_path",
        "idea_id",
        "title",
        "source_checksum_sha256",
    )
    @classmethod
    def validate_required_text(cls, value: str, info: object) -> str:
        field_name = getattr(info, "field_name", "value")
        return _normalize_required_text(value, field_name=field_name)

    @field_validator("archived_source_path", mode="before")
    @classmethod
    def normalize_archived_source_path(cls, value: str | Path | None) -> str:
        return _normalize_path_token(value)


class AcceptanceProfileRecord(ContractModel):
    """Machine-readable acceptance profile emitted by objective sync."""

    schema_version: Literal["1.0"] = GOALSPEC_ARTIFACT_SCHEMA_VERSION
    profile_id: str
    goal_id: str
    title: str
    run_id: str
    updated_at: datetime
    source_path: str
    research_brief_path: str
    milestones: tuple[str, ...]
    hard_blockers: tuple[str, ...]

    @field_validator("updated_at", mode="before")
    @classmethod
    def normalize_updated_at(cls, value: datetime | str) -> datetime:
        return _normalize_datetime(value)

    @field_validator("profile_id", "goal_id", "title", "run_id", "source_path", "research_brief_path")
    @classmethod
    def validate_required_text(cls, value: str, info: object) -> str:
        field_name = getattr(info, "field_name", "value")
        return _normalize_required_text(value, field_name=field_name)


class ObjectiveProfileSyncStateRecord(ContractModel):
    """Canonical current objective-profile sync state."""

    schema_version: Literal["1.0"] = GOALSPEC_ARTIFACT_SCHEMA_VERSION
    profile_id: str
    goal_id: str
    title: str
    run_id: str
    updated_at: datetime
    source_path: str
    research_brief_path: str
    profile_path: str
    profile_markdown_path: str
    report_path: str
    goal_intake_record_path: str
    initial_family_policy_pin: InitialFamilyPolicyPinDecision | None = None

    @field_validator("updated_at", mode="before")
    @classmethod
    def normalize_updated_at(cls, value: datetime | str) -> datetime:
        return _normalize_datetime(value)

    @field_validator(
        "profile_id",
        "goal_id",
        "title",
        "run_id",
        "source_path",
        "research_brief_path",
        "profile_path",
        "profile_markdown_path",
        "report_path",
        "goal_intake_record_path",
    )
    @classmethod
    def validate_required_text(cls, value: str, info: object) -> str:
        field_name = getattr(info, "field_name", "value")
        return _normalize_required_text(value, field_name=field_name)


class ObjectiveProfileSyncRecord(ContractModel):
    """Durable runtime record for one objective-profile sync execution."""

    schema_version: Literal["1.0"] = GOALSPEC_ARTIFACT_SCHEMA_VERSION
    artifact_type: Literal["objective_profile_sync"] = "objective_profile_sync"
    run_id: str
    emitted_at: datetime
    goal_id: str
    title: str
    source_path: str
    research_brief_path: str
    profile_state_path: str
    profile_path: str
    profile_markdown_path: str
    report_path: str

    @field_validator("emitted_at", mode="before")
    @classmethod
    def normalize_emitted_at(cls, value: datetime | str) -> datetime:
        return _normalize_datetime(value)

    @field_validator(
        "run_id",
        "goal_id",
        "title",
        "source_path",
        "research_brief_path",
        "profile_state_path",
        "profile_path",
        "profile_markdown_path",
        "report_path",
    )
    @classmethod
    def validate_required_text(cls, value: str, info: object) -> str:
        field_name = getattr(info, "field_name", "value")
        return _normalize_required_text(value, field_name=field_name)


class GoalIntakeExecutionResult(ContractModel):
    """Resolved outputs from one Goal Intake execution."""

    record_path: str
    archived_source_path: str = ""
    research_brief_path: str
    queue_ownership: ResearchQueueOwnership


class ObjectiveProfileSyncExecutionResult(ContractModel):
    """Resolved outputs from one Objective Profile Sync execution."""

    record_path: str
    profile_state_path: str
    queue_ownership: ResearchQueueOwnership


class CompletionManifestDraftArtifact(ContractModel):
    """One planned artifact captured in the completion-manifest draft."""

    artifact_kind: str
    path: str
    purpose: str

    @field_validator("artifact_kind", "path", "purpose")
    @classmethod
    def validate_required_text(cls, value: str, info: object) -> str:
        field_name = getattr(info, "field_name", "value")
        return _normalize_required_text(value, field_name=field_name)


class CompletionManifestDraftStateRecord(ContractModel):
    """Canonical completion-manifest draft state for the current GoalSpec source."""

    schema_version: Literal["1.0"] = GOALSPEC_ARTIFACT_SCHEMA_VERSION
    artifact_type: Literal["completion_manifest_draft"] = "completion_manifest_draft"
    draft_id: str
    goal_id: str
    title: str
    run_id: str
    updated_at: datetime
    source_path: str
    research_brief_path: str
    objective_profile_state_path: str
    objective_profile_path: str
    completion_manifest_plan_path: str
    goal_intake_record_path: str
    acceptance_focus: tuple[str, ...]
    open_questions: tuple[str, ...]
    required_outputs: tuple[CompletionManifestDraftArtifact, ...]

    @field_validator("updated_at", mode="before")
    @classmethod
    def normalize_updated_at(cls, value: datetime | str) -> datetime:
        return _normalize_datetime(value)

    @field_validator(
        "draft_id",
        "goal_id",
        "title",
        "run_id",
        "source_path",
        "research_brief_path",
        "objective_profile_state_path",
        "objective_profile_path",
        "completion_manifest_plan_path",
        "goal_intake_record_path",
    )
    @classmethod
    def validate_required_text(cls, value: str, info: object) -> str:
        field_name = getattr(info, "field_name", "value")
        return _normalize_required_text(value, field_name=field_name)


class CompletionManifestDraftRecord(ContractModel):
    """Per-run runtime record for one completion-manifest drafting execution."""

    schema_version: Literal["1.0"] = GOALSPEC_ARTIFACT_SCHEMA_VERSION
    artifact_type: Literal["completion_manifest_draft_record"] = "completion_manifest_draft_record"
    run_id: str
    emitted_at: datetime
    goal_id: str
    title: str
    source_path: str
    research_brief_path: str
    draft_path: str
    report_path: str
    objective_profile_path: str

    @field_validator("emitted_at", mode="before")
    @classmethod
    def normalize_emitted_at(cls, value: datetime | str) -> datetime:
        return _normalize_datetime(value)

    @field_validator(
        "run_id",
        "goal_id",
        "title",
        "source_path",
        "research_brief_path",
        "draft_path",
        "report_path",
        "objective_profile_path",
    )
    @classmethod
    def validate_required_text(cls, value: str, info: object) -> str:
        field_name = getattr(info, "field_name", "value")
        return _normalize_required_text(value, field_name=field_name)


class CompletionManifestDraftExecutionResult(ContractModel):
    """Resolved outputs from one internal completion-manifest drafting execution."""

    record_path: str
    draft_path: str
    report_path: str
    objective_profile_path: str
    draft_state: CompletionManifestDraftStateRecord


class SpecSynthesisRecord(ContractModel):
    """Per-run runtime record for one Spec Synthesis execution."""

    schema_version: Literal["1.0"] = GOALSPEC_ARTIFACT_SCHEMA_VERSION
    artifact_type: Literal["spec_synthesis"] = "spec_synthesis"
    run_id: str
    emitted_at: datetime
    goal_id: str
    spec_id: str
    title: str
    source_path: str
    research_brief_path: str
    objective_profile_path: str
    completion_manifest_path: str
    queue_spec_path: str
    golden_spec_path: str
    phase_spec_path: str
    decision_path: str
    family_state_path: str

    @field_validator("emitted_at", mode="before")
    @classmethod
    def normalize_emitted_at(cls, value: datetime | str) -> datetime:
        return _normalize_datetime(value)

    @field_validator(
        "run_id",
        "goal_id",
        "spec_id",
        "title",
        "source_path",
        "research_brief_path",
        "objective_profile_path",
        "completion_manifest_path",
        "queue_spec_path",
        "golden_spec_path",
        "phase_spec_path",
        "decision_path",
        "family_state_path",
    )
    @classmethod
    def validate_required_text(cls, value: str, info: object) -> str:
        field_name = getattr(info, "field_name", "value")
        return _normalize_required_text(value, field_name=field_name)


class SpecSynthesisExecutionResult(ContractModel):
    """Resolved outputs from one Spec Synthesis execution."""

    record_path: str
    queue_spec_path: str
    golden_spec_path: str
    phase_spec_path: str
    decision_path: str
    family_state_path: str
    queue_ownership: ResearchQueueOwnership


class SpecReviewExecutionResult(ContractModel):
    """Resolved outputs from one Spec Review execution."""

    record_path: str
    questions_path: str
    decision_path: str
    reviewed_path: str
    lineage_path: str
    stable_registry_path: str
    family_state_path: str
    queue_ownership: ResearchQueueOwnership


def resolve_goal_source(paths: RuntimePaths, checkpoint: ResearchCheckpoint) -> GoalSource:
    """Resolve the current GoalSpec source artifact from checkpoint state."""

    candidate_paths: list[Path] = []
    if checkpoint.owned_queues:
        item_path = checkpoint.owned_queues[0].item_path
        if item_path is not None:
            candidate_paths.append(item_path)
    if checkpoint.active_request is not None:
        payload_path = checkpoint.active_request.payload.get("path")
        if payload_path:
            candidate_paths.append(Path(str(payload_path)))

    source_path: Path | None = None
    for candidate in candidate_paths:
        if candidate.exists():
            source_path = candidate
            break
    if source_path is None:
        raise GoalSpecExecutionError("GoalSpec checkpoint has no existing source artifact to execute")

    text = source_path.read_text(encoding="utf-8", errors="replace")
    frontmatter, body = _split_frontmatter(text)
    idea_id = (
        frontmatter.get("idea_id")
        or frontmatter.get("goal_id")
        or frontmatter.get("id")
        or source_path.stem.split("__", 1)[0]
        or f"goal-{_slugify(source_path.stem)}"
    )
    title = frontmatter.get("title") or _first_heading(body) or source_path.stem.replace("-", " ").replace("_", " ")
    decomposition_profile = _normalize_decomposition_profile(frontmatter.get("decomposition_profile"))
    return GoalSource(
        source_path=source_path.as_posix(),
        relative_source_path=_relative_path(source_path, relative_to=paths.root),
        idea_id=idea_id,
        title=_normalize_required_text(title, field_name="title"),
        decomposition_profile=decomposition_profile,
        frontmatter=frontmatter,
        body=body.strip() or text.strip(),
        checksum_sha256=_sha256_text(text),
    )


def _load_objective_profile_inputs(
    paths: RuntimePaths,
) -> tuple[ObjectiveProfileSyncStateRecord, AcceptanceProfileRecord]:
    if not paths.objective_profile_sync_state_file.exists():
        raise GoalSpecExecutionError(
            "Objective Profile Sync state is missing; GoalSpec spec synthesis cannot proceed"
        )
    state = ObjectiveProfileSyncStateRecord.model_validate(
        _load_json_object(paths.objective_profile_sync_state_file)
    )
    profile_path = _resolve_path_token(state.profile_path, relative_to=paths.root)
    if not profile_path.exists():
        raise GoalSpecExecutionError(
            f"Objective profile JSON is missing: {profile_path.as_posix()}"
        )
    profile = AcceptanceProfileRecord.model_validate(_load_json_object(profile_path))
    return state, profile


def _render_completion_manifest_report(
    *,
    run_id: str,
    source: GoalSource,
    draft_state: CompletionManifestDraftStateRecord,
) -> str:
    return "\n".join(
        [
            "# Completion Manifest Draft",
            "",
            f"- **Run-ID:** {run_id}",
            f"- **Goal-ID:** {source.idea_id}",
            f"- **Title:** {source.title}",
            f"- **Source-Path:** `{source.relative_source_path}`",
            "",
            "## Acceptance Focus",
            *(f"- {item}" for item in draft_state.acceptance_focus),
            "",
            "## Planned Outputs",
            *(
                f"- `{artifact.artifact_kind}`: `{artifact.path}` ({artifact.purpose})"
                for artifact in draft_state.required_outputs
            ),
            "",
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
        source_path=source.relative_source_path,
        research_brief_path=source.relative_source_path,
        draft_path=draft_path,
        report_path=report_path,
        objective_profile_path=objective_profile_path,
    )


def _render_spec_review_questions(
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


def _render_spec_review_decision(
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


def _render_queue_spec(
    *,
    emitted_at: datetime,
    source: GoalSource,
    spec_id: str,
    objective_state: ObjectiveProfileSyncStateRecord,
    profile: AcceptanceProfileRecord,
    completion_manifest_path: str,
) -> str:
    summary = _first_paragraph(source.body) or source.title
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
            f"- Convert `{source.idea_id}` into a traceable GoalSpec draft package.",
            "- Preserve objective-profile inputs and the completion-manifest plan in emitted spec artifacts.",
            *(f"- {item}" for item in profile.milestones),
            "",
            "## Non-Goals",
            "- Spec Review decisions.",
            "- Task generation and pending shard emission.",
            "",
            "## Scope",
            "### In Scope",
            "- Completion-manifest draft persistence for this GoalSpec source.",
            "- One queue spec plus stable golden and phase copies for downstream review.",
            "- Family-state initialization for the emitted draft spec.",
            "",
            "### Out of Scope",
            "- Approval, merge, or backlog handoff.",
            "- Additional spec families beyond this initial emitted spec.",
            "",
            "## Capability Domains",
            "- GoalSpec runtime execution",
            "- Durable artifact traceability",
            "",
            "## Decomposition Readiness",
            "- This synthesized scope is intentionally bounded to one emitted spec for Run 03.",
            f"- Declared decomposition profile: `{source.decomposition_profile}`.",
            "- Later review/task-generation stages remain downstream of this draft package.",
            "",
            "## Constraints",
            "- Preserve linkage back to the staged goal and objective profile inputs.",
            "- Keep research-plane runtime behavior restart-safe and deterministic.",
            "- Avoid pulling Spec Review or task generation into this run.",
            "",
            "## Implementation Plan",
            "1. Draft the completion manifest from the staged goal and objective profile.",
            "2. Emit queue, golden, and phase spec artifacts for one spec candidate.",
            "3. Persist spec-family state and a synthesis decision record for downstream review.",
            "",
            "## Requirements Traceability (Req-ID Matrix)",
            f"- `Req-ID: REQ-001` | Preserve linkage to `{source.idea_id}` intake/objective inputs | `{objective_state.profile_path}`",
            f"- `Req-ID: REQ-002` | Persist completion-manifest drafting state before spec output | `{completion_manifest_path}`",
            "",
            "## Assumptions Ledger",
            "- The first initial-family emission for this goal remains a single-spec family in Run 03 (source: inferred).",
            "- Objective-profile milestones are sufficient context for a draft spec package at this stage (source: confirmed).",
            "",
            "## Structured Decision Log",
            "| decision_id | phase_key | phase_priority | status | owner | rationale | timestamp |",
            "| --- | --- | --- | --- | --- | --- | --- |",
            f"| DEC-001 | PHASE_01 | P1 | proposed | research | Emit one bounded draft spec before Spec Review | {timestamp} |",
            "",
            "## Interrogation Record",
            "- Critic question: what is the smallest durable spec package that preserves GoalSpec traceability?",
            "- Designer resolution: emit one bounded spec with explicit links to the objective profile and completion manifest.",
            "",
            "## Verification",
            "- `python3 -c \"from pathlib import Path; assert Path('agents/audit/completion_manifest.json').exists(); assert Path('agents/reports/acceptance_profiles').exists()\"`",
            "- `python3 -c \"from pathlib import Path; assert any(Path('agents/specs/stable/golden').glob('*.md')); assert any(Path('agents/specs/stable/phase').glob('*.md'))\"`",
            "",
            "## Dependencies",
            f"- Objective profile: `{objective_state.profile_path}`",
            f"- Completion manifest draft: `{completion_manifest_path}`",
            "",
            "## Risks and Mitigations",
            "- Risk: later stages may require additional decomposition. Mitigation: family state remains explicit and editable in later runs.",
            "",
            "## Rollout and Rollback",
            "- Rollout: use this draft package as the input to Spec Review.",
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


def _render_phase_spec(
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


def _render_synthesis_decision_record(
    *,
    emitted_at: datetime,
    run_id: str,
    source: GoalSource,
    spec_id: str,
    completion_manifest_path: str,
    objective_profile_path: str,
    queue_spec_path: str,
    family_complete: bool,
) -> str:
    timestamp = _isoformat_z(emitted_at)
    family_complete_text = "yes" if family_complete else "no"
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
            "- What is the smallest durable spec package that preserves GoalSpec traceability?",
            "- Which artifacts must exist before later review/task-generation work can proceed safely?",
            "",
            "## Designer Resolutions",
            "- Emit one bounded draft spec family for this goal in Run 03.",
            "- Preserve explicit links to the staged goal, objective profile, and completion manifest draft.",
            "",
            "## Retained Assumptions",
            "- Additional families are not required before Spec Review for this initial draft package.",
            "",
            "## Contradictions",
            "- None observed during deterministic synthesis.",
            "",
            "## Clarifier Preservation Statement",
            "- The emitted queue/golden/phase artifacts preserve the bounded decisions above without expanding scope.",
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


def _build_completion_manifest_draft_state(
    *,
    emitted_at: datetime,
    run_id: str,
    source: GoalSource,
    objective_state: ObjectiveProfileSyncStateRecord,
    profile: AcceptanceProfileRecord,
    spec_id: str,
    paths: RuntimePaths,
) -> CompletionManifestDraftStateRecord:
    slug = _slugify(source.title)
    queue_spec_path = paths.ideas_specs_dir / f"{spec_id}__{slug}.md"
    golden_spec_path = paths.specs_stable_golden_dir / f"{spec_id}__{slug}.md"
    phase_spec_path = paths.specs_stable_phase_dir / f"{spec_id}__phase-01.md"
    decision_path = paths.specs_decisions_dir / f"{Path(source.source_path).stem}__spec-synthesis.md"
    open_questions = profile.hard_blockers or (
        "Spec Review and task generation remain downstream after this draft synthesis pass.",
    )
    return CompletionManifestDraftStateRecord(
        draft_id=f"{_slugify(source.idea_id)}-completion-manifest",
        goal_id=source.idea_id,
        title=source.title,
        run_id=run_id,
        updated_at=emitted_at,
        source_path=source.relative_source_path,
        research_brief_path=source.relative_source_path,
        objective_profile_state_path=_relative_path(paths.objective_profile_sync_state_file, relative_to=paths.root),
        objective_profile_path=objective_state.profile_path,
        completion_manifest_plan_path=_relative_path(paths.completion_manifest_plan_file, relative_to=paths.root),
        goal_intake_record_path=objective_state.goal_intake_record_path,
        acceptance_focus=profile.milestones,
        open_questions=open_questions,
        required_outputs=(
            CompletionManifestDraftArtifact(
                artifact_kind="queue_spec",
                path=_relative_path(queue_spec_path, relative_to=paths.root),
                purpose="Primary draft spec candidate for downstream review.",
            ),
            CompletionManifestDraftArtifact(
                artifact_kind="stable_golden_spec",
                path=_relative_path(golden_spec_path, relative_to=paths.root),
                purpose="Stable copy of the emitted draft spec.",
            ),
            CompletionManifestDraftArtifact(
                artifact_kind="stable_phase_spec",
                path=_relative_path(phase_spec_path, relative_to=paths.root),
                purpose="Bounded phase plan aligned to the emitted draft spec.",
            ),
            CompletionManifestDraftArtifact(
                artifact_kind="synthesis_record",
                path=_relative_path(decision_path, relative_to=paths.root),
                purpose="Critic/designer/clarifier synthesis summary for traceability.",
            ),
        ),
    )


def _build_goal_spec_family_state(
    *,
    paths: RuntimePaths,
    source: GoalSource,
    spec_id: str,
    title: str,
    decomposition_profile: GoalSpecDecompositionProfile,
    queue_spec_path: Path,
    emitted_at: datetime,
) -> GoalSpecFamilyState:
    current_state = load_goal_spec_family_state(paths.goal_spec_family_state_file)
    policy_payload = (
        _load_json_object(paths.objective_family_policy_file)
        if paths.objective_family_policy_file.exists()
        else {}
    )
    next_state = current_state
    if next_state.goal_id and next_state.goal_id != source.idea_id and not next_state.family_complete:
        raise GoalSpecExecutionError(
            "GoalSpec family state is still active for another goal; refusing to overwrite incomplete family state"
        )
    if not next_state.goal_id or next_state.goal_id != source.idea_id:
        next_state = GoalSpecFamilyState(
            goal_id=source.idea_id,
            source_idea_path=source.relative_source_path,
            family_phase="initial_family",
            family_complete=True,
            active_spec_id="",
            spec_order=(),
            specs={},
            family_governor=resolve_family_governor_state(
                paths=paths,
                current_state=current_state,
                policy_payload=policy_payload,
            ),
        )
    resolved_governor = resolve_family_governor_state(
        paths=paths,
        current_state=next_state,
        policy_payload=policy_payload,
    )
    next_state = next_state.model_copy(update={"family_governor": resolved_governor})

    spec_state = GoalSpecFamilySpecState(
        status="emitted",
        title=title,
        decomposition_profile=decomposition_profile,
        queue_path=_relative_path(queue_spec_path, relative_to=paths.root),
    )
    specs = dict(next_state.specs)
    specs[spec_id] = spec_state
    spec_order = next_state.spec_order or (spec_id,)
    if spec_id not in spec_order:
        spec_order = spec_order + (spec_id,)
    guard_decision = evaluate_initial_family_plan_guard(
        current_state=next_state,
        candidate_spec_id=spec_id,
        proposed_spec_order=spec_order,
        proposed_specs=specs,
    )
    if guard_decision.action == "block":
        raise GoalSpecExecutionError(
            f"GoalSpec family governance blocked {spec_id}: {guard_decision.reason}"
        )

    next_state = next_state.model_copy(
        update={
            "goal_id": source.idea_id,
            "source_idea_path": source.relative_source_path,
            "family_phase": "initial_family",
            "family_complete": True,
            "active_spec_id": spec_id,
            "spec_order": spec_order,
            "specs": specs,
            "family_governor": resolved_governor,
            "updated_at": emitted_at,
        }
    )
    if next_state.initial_family_plan is None and guard_decision.action == "freeze":
        next_state = next_state.model_copy(
            update={
                "initial_family_plan": build_initial_family_plan_snapshot(
                    next_state,
                    repo_root=paths.root,
                    trigger_spec_id=spec_id,
                    goal_file=_resolve_path_token(source.source_path, relative_to=paths.root),
                    policy_path=paths.objective_family_policy_file,
                    policy_payload=policy_payload,
                    frozen_at=emitted_at,
                )
            }
    )
    return next_state


def _updated_goal_spec_family_state(
    *,
    paths: RuntimePaths,
    source: GoalSource,
    spec_id: str,
    title: str,
    decomposition_profile: GoalSpecDecompositionProfile,
    queue_spec_path: Path,
    emitted_at: datetime,
) -> GoalSpecFamilyState:
    next_state = _build_goal_spec_family_state(
        paths=paths,
        source=source,
        spec_id=spec_id,
        title=title,
        decomposition_profile=decomposition_profile,
        queue_spec_path=queue_spec_path,
        emitted_at=emitted_at,
    )
    return write_goal_spec_family_state(
        paths.goal_spec_family_state_file,
        next_state,
        updated_at=emitted_at,
    )


def _stable_spec_paths_for_review(paths: RuntimePaths, *, spec_id: str) -> tuple[Path, ...]:
    candidates = sorted(
        (
            path
            for path in paths.specs_stable_dir.rglob("*.md")
            if path.name.startswith(f"{spec_id}__") and ".frozen" not in path.parts
        ),
        key=lambda path: path.as_posix(),
    )
    if not candidates:
        raise GoalSpecExecutionError(f"Stable spec copies are missing for {spec_id}")
    return tuple(candidates)


def _build_goal_spec_review_state(
    *,
    paths: RuntimePaths,
    spec_id: str,
    goal_id: str,
    queue_spec_path: Path,
    reviewed_path: Path,
    questions_path: Path,
    decision_path: Path,
    stable_spec_paths: tuple[Path, ...],
    review_status: str,
    emitted_at: datetime,
) -> tuple[GoalSpecFamilyState, GoalSpecLineageRecord]:
    current_state = load_goal_spec_family_state(paths.goal_spec_family_state_file)
    spec_state = current_state.specs.get(spec_id)
    if spec_state is None:
        raise GoalSpecExecutionError(f"GoalSpec family state is missing {spec_id} during Spec Review")

    updated_spec_state = spec_state.model_copy(
        update={
            "status": "reviewed",
            "review_status": review_status,
            "queue_path": spec_state.queue_path or _relative_path(queue_spec_path, relative_to=paths.root),
            "reviewed_path": _relative_path(reviewed_path, relative_to=paths.root),
            "stable_spec_paths": tuple(_relative_path(path, relative_to=paths.root) for path in stable_spec_paths),
            "review_questions_path": _relative_path(questions_path, relative_to=paths.root),
            "review_decision_path": _relative_path(decision_path, relative_to=paths.root),
        }
    )
    next_specs = dict(current_state.specs)
    next_specs[spec_id] = updated_spec_state
    next_state = current_state.model_copy(
        update={
            "active_spec_id": spec_id,
            "specs": next_specs,
            "updated_at": emitted_at,
        }
    )
    return next_state, updated_spec_state.lineage(
        spec_id=spec_id,
        goal_id=goal_id,
        source_idea_path=next_state.source_idea_path,
    )


def research_stage_for_node(plan: FrozenLoopPlan, node_id: str) -> FrozenStagePlan:
    """Return one stage plan by node id."""

    for stage in plan.stages:
        if stage.node_id == node_id:
            return stage
    raise GoalSpecExecutionError(f"compiled research plan is missing stage node {node_id}")


def next_stage_for_success(plan: FrozenLoopPlan, node_id: str) -> FrozenStagePlan | None:
    """Return the normal-success successor stage for one node."""

    for transition in sorted(plan.transitions, key=lambda item: (-item.priority, item.edge_id)):
        if transition.from_node_id != node_id:
            continue
        if "success" not in transition.on_outcomes:
            continue
        if transition.to_node_id is None:
            return None
        return research_stage_for_node(plan, transition.to_node_id)
    raise GoalSpecExecutionError(f"compiled research plan has no success transition from {node_id}")


def execute_goal_intake(
    paths: RuntimePaths,
    checkpoint: ResearchCheckpoint,
    *,
    run_id: str,
    emitted_at: datetime | None = None,
) -> GoalIntakeExecutionResult:
    """Normalize one queued goal into a durable staged idea plus runtime record."""

    emitted_at = emitted_at or _utcnow()
    source = resolve_goal_source(paths, checkpoint)
    source_path = Path(source.source_path)
    staged_slug = _slugify(source.title)
    research_brief_path = paths.ideas_staging_dir / f"{source.idea_id}__{staged_slug}.md"

    summary = _first_paragraph(source.body) or source.title
    problem_statement = _markdown_section(source.body, "Problem Statement") or summary
    scope = _markdown_section(source.body, "Scope") or "Preserve the queued goal scope for downstream spec synthesis."
    constraints = _markdown_section(source.body, "Constraints") or "No additional constraints were extracted during deterministic Goal Intake."
    unknowns = _markdown_section(source.body, "Unknowns Ledger") or "Downstream GoalSpec stages still need to refine acceptance details and decomposition boundaries."
    evidence_lines = (
        f"- Source artifact: `{source.relative_source_path}`",
        "- Stage contract: `agents/_goal_intake.md`",
    )
    route_decision = (
        "Ready for staging under the compiled GoalSpec loop. "
        "Remaining assumptions are preserved explicitly for Objective Profile Sync and later spec synthesis. "
        f"Repo evidence anchors: `{source.relative_source_path}`, `agents/_goal_intake.md`."
    )

    frontmatter_lines = [
        _FRONTMATTER_BOUNDARY,
        f"idea_id: {source.idea_id}",
        f"title: {source.title}",
        "status: staging",
        f"updated_at: {_isoformat_z(emitted_at)}",
        f"decomposition_profile: {source.decomposition_profile}",
        f"goal_intake_run_id: {run_id}",
        f"source_path: {source.relative_source_path}",
        f"source_checksum_sha256: {source.checksum_sha256}",
        f"artifact_schema_version: {GOALSPEC_ARTIFACT_SCHEMA_VERSION}",
        _FRONTMATTER_BOUNDARY,
        "",
    ]
    body_lines = [
        "## Summary",
        summary,
        "",
        "## Problem Statement",
        problem_statement,
        "",
        "## Scope",
        scope,
        "",
        "## Constraints",
        constraints,
        "",
        "## Unknowns Ledger",
        unknowns,
        "",
        "## Evidence",
        *evidence_lines,
        "",
        "## Route Decision",
        route_decision,
        "",
    ]
    research_brief_path.parent.mkdir(parents=True, exist_ok=True)
    write_text_atomic(research_brief_path, "\n".join(frontmatter_lines + body_lines))

    archived_source_path = ""
    if source_path.parent == paths.ideas_raw_dir and source_path != research_brief_path:
        archive_dir = paths.ideas_archive_dir / source_path.parent.name
        archive_dir.mkdir(parents=True, exist_ok=True)
        archived_path = archive_dir / _archive_filename_for_execution(
            source_path,
            run_id=run_id,
            checksum_sha256=source.checksum_sha256,
        )
        source_path.replace(archived_path)
        archived_source_path = _relative_path(archived_path, relative_to=paths.root)

    record = GoalIntakeRecord(
        run_id=run_id,
        emitted_at=emitted_at,
        source_path=source.relative_source_path,
        archived_source_path=archived_source_path,
        research_brief_path=_relative_path(research_brief_path, relative_to=paths.root),
        idea_id=source.idea_id,
        title=source.title,
        decomposition_profile=source.decomposition_profile,
        source_checksum_sha256=source.checksum_sha256,
    )
    record_path = paths.goalspec_goal_intake_records_dir / f"{run_id}.json"
    _write_json_model(record_path, record)

    return GoalIntakeExecutionResult(
        record_path=_relative_path(record_path, relative_to=paths.root),
        archived_source_path=archived_source_path,
        research_brief_path=_relative_path(research_brief_path, relative_to=paths.root),
        queue_ownership=ResearchQueueOwnership(
            family=ResearchQueueFamily.GOALSPEC,
            queue_path=paths.ideas_staging_dir,
            item_path=research_brief_path,
            owner_token=run_id,
            acquired_at=emitted_at,
        ),
    )


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

    milestones = (
        f"Normalize queued goal `{source.idea_id}` into a staged GoalSpec brief.",
        "Persist objective-profile state that downstream spec synthesis can reference deterministically.",
    )
    hard_blockers = (
        "Spec Review and task generation remain downstream after this draft synthesis pass.",
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

    goal_intake_record_path = paths.goalspec_goal_intake_records_dir / f"{run_id}.json"
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
            "schema_version": GOALSPEC_ARTIFACT_SCHEMA_VERSION,
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
                f"- **Profile-State:** `{_relative_path(paths.objective_profile_sync_state_file, relative_to=paths.root)}`",
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
                "completion_manifest_file": _relative_path(paths.audit_completion_manifest_file, relative_to=paths.root),
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
    if record_path.exists() and paths.audit_completion_manifest_file.exists() and paths.completion_manifest_plan_file.exists():
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

    queue_spec_text = _render_queue_spec(
        emitted_at=emitted_at,
        source=source,
        spec_id=spec_id,
        objective_state=objective_state,
        profile=profile,
        completion_manifest_path=completion_manifest_path,
    )
    phase_spec_text = _render_phase_spec(
        emitted_at=emitted_at,
        spec_id=spec_id,
        title=source.title,
        completion_manifest_path=completion_manifest_path,
        objective_profile_path=objective_state.profile_path,
    )
    expected_family_state = _build_goal_spec_family_state(
        paths=paths,
        source=source,
        spec_id=spec_id,
        title=source.title,
        decomposition_profile=source.decomposition_profile,
        queue_spec_path=queue_spec_path,
        emitted_at=emitted_at,
    )
    decision_text = _render_synthesis_decision_record(
        emitted_at=emitted_at,
        run_id=run_id,
        source=source,
        spec_id=spec_id,
        completion_manifest_path=completion_manifest_path,
        objective_profile_path=objective_state.profile_path,
        queue_spec_path=_relative_path(queue_spec_path, relative_to=paths.root),
        family_complete=expected_family_state.family_complete,
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
        existing_family_state = load_goal_spec_family_state(paths.goal_spec_family_state_file)
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
        expected_queue_spec = _render_queue_spec(
            emitted_at=reused_emitted_at,
            source=source,
            spec_id=spec_id,
            objective_state=objective_state,
            profile=profile,
            completion_manifest_path=completion_manifest_path,
        )
        expected_phase_spec = _render_phase_spec(
            emitted_at=reused_emitted_at,
            spec_id=spec_id,
            title=source.title,
            completion_manifest_path=completion_manifest_path,
            objective_profile_path=objective_state.profile_path,
        )
        expected_decision = _render_synthesis_decision_record(
            emitted_at=reused_emitted_at,
            run_id=run_id,
            source=source,
            spec_id=spec_id,
            completion_manifest_path=completion_manifest_path,
            objective_profile_path=objective_state.profile_path,
            queue_spec_path=_relative_path(queue_spec_path, relative_to=paths.root),
            family_complete=expected_family_state.family_complete,
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

    family_state = _updated_goal_spec_family_state(
        paths=paths,
        source=source,
        spec_id=spec_id,
        title=source.title,
        decomposition_profile=source.decomposition_profile,
        queue_spec_path=queue_spec_path,
        emitted_at=emitted_at,
    )
    write_text_atomic(
        decision_path,
        decision_text,
    )

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


def execute_spec_review(
    paths: RuntimePaths,
    checkpoint: ResearchCheckpoint,
    *,
    run_id: str,
    emitted_at: datetime | None = None,
) -> SpecReviewExecutionResult:
    """Promote one synthesized queue spec into reviewed state plus lineage/registry artifacts."""

    emitted_at = emitted_at or _utcnow()
    source = resolve_goal_source(paths, checkpoint)
    family_state = load_goal_spec_family_state(paths.goal_spec_family_state_file)
    spec_id = source.frontmatter.get("spec_id", "").strip() or _spec_id_for_goal(source.idea_id)
    spec_state = family_state.specs.get(spec_id)
    if spec_state is None:
        raise GoalSpecExecutionError(f"GoalSpec family state is missing {spec_id} during Spec Review")

    queue_spec_path = _resolve_path_token(spec_state.queue_path or source.relative_source_path, relative_to=paths.root)
    reviewed_path = paths.ideas_specs_reviewed_dir / Path(spec_state.queue_path or source.relative_source_path).name
    source_slug = Path(spec_state.queue_path or source.relative_source_path).stem
    questions_path = paths.specs_questions_dir / f"{source_slug}__spec-review.md"
    decision_path = paths.specs_decisions_dir / f"{source_slug}__spec-review.md"
    record_path = paths.goalspec_spec_review_records_dir / f"{run_id}.json"
    lineage_path = paths.goalspec_lineage_dir / f"{spec_id}.json"
    stable_spec_paths = _stable_spec_paths_for_review(paths, spec_id=spec_id)
    relative_stable_spec_paths = tuple(_relative_path(path, relative_to=paths.root) for path in stable_spec_paths)
    review_status = "no_material_delta"
    review_timestamp = emitted_at
    queue_spec_text = _resolve_path_token(source.source_path, relative_to=paths.root).read_text(
        encoding="utf-8",
        errors="replace",
    )

    if (
        record_path.exists()
        and questions_path.exists()
        and decision_path.exists()
        and reviewed_path.exists()
        and lineage_path.exists()
        and paths.specs_index_file.exists()
    ):
        existing_review_record = _load_json_model(record_path, GoalSpecReviewRecord)
        if existing_review_record.reviewed_at is not None:
            review_timestamp = existing_review_record.reviewed_at
        expected_questions = _render_spec_review_questions(
            reviewed_at=review_timestamp,
            run_id=run_id,
            goal_id=source.idea_id,
            spec_id=spec_id,
            title=source.title,
            queue_spec_path=_relative_path(queue_spec_path, relative_to=paths.root),
            stable_spec_paths=relative_stable_spec_paths,
        )
        expected_decision = _render_spec_review_decision(
            reviewed_at=review_timestamp,
            run_id=run_id,
            goal_id=source.idea_id,
            spec_id=spec_id,
            title=source.title,
            review_status=review_status,
            reviewed_path=_relative_path(reviewed_path, relative_to=paths.root),
            stable_registry_path=_relative_path(paths.specs_index_file, relative_to=paths.root),
            lineage_path=_relative_path(lineage_path, relative_to=paths.root),
        )
        expected_family_state, lineage_record = _build_goal_spec_review_state(
            paths=paths,
            spec_id=spec_id,
            goal_id=source.idea_id,
            queue_spec_path=queue_spec_path,
            reviewed_path=reviewed_path,
            questions_path=questions_path,
            decision_path=decision_path,
            stable_spec_paths=stable_spec_paths,
            review_status=review_status,
            emitted_at=review_timestamp,
        )
        current_family_state = load_goal_spec_family_state(paths.goal_spec_family_state_file)
        stable_registry = load_stable_spec_registry(paths.specs_index_file)
        if (
            existing_review_record
            == GoalSpecReviewRecord(
                spec_id=spec_id,
                review_status=review_status,
                questions_path=_relative_path(questions_path, relative_to=paths.root),
                decision_path=_relative_path(decision_path, relative_to=paths.root),
                reviewed_path=_relative_path(reviewed_path, relative_to=paths.root),
                reviewed_at=review_timestamp,
                findings=(),
            )
            and GoalSpecLineageRecord.model_validate(_load_json_object(lineage_path)) == lineage_record
            and current_family_state == expected_family_state
            and questions_path.read_text(encoding="utf-8") == expected_questions
            and decision_path.read_text(encoding="utf-8") == expected_decision
            and reviewed_path.read_text(encoding="utf-8") == queue_spec_text
            and {entry.spec_path for entry in stable_registry.stable_specs} >= set(relative_stable_spec_paths)
        ):
            return SpecReviewExecutionResult(
                record_path=_relative_path(record_path, relative_to=paths.root),
                questions_path=_relative_path(questions_path, relative_to=paths.root),
                decision_path=_relative_path(decision_path, relative_to=paths.root),
                reviewed_path=_relative_path(reviewed_path, relative_to=paths.root),
                lineage_path=_relative_path(lineage_path, relative_to=paths.root),
                stable_registry_path=_relative_path(paths.specs_index_file, relative_to=paths.root),
                family_state_path=_relative_path(paths.goal_spec_family_state_file, relative_to=paths.root),
                queue_ownership=ResearchQueueOwnership(
                    family=ResearchQueueFamily.GOALSPEC,
                    queue_path=paths.ideas_specs_reviewed_dir,
                    item_path=reviewed_path,
                    owner_token=run_id,
                    acquired_at=review_timestamp,
                ),
            )

    questions_path.parent.mkdir(parents=True, exist_ok=True)
    decision_path.parent.mkdir(parents=True, exist_ok=True)
    record_path.parent.mkdir(parents=True, exist_ok=True)
    lineage_path.parent.mkdir(parents=True, exist_ok=True)
    reviewed_path.parent.mkdir(parents=True, exist_ok=True)

    write_text_atomic(
        questions_path,
        _render_spec_review_questions(
            reviewed_at=review_timestamp,
            run_id=run_id,
            goal_id=source.idea_id,
            spec_id=spec_id,
            title=source.title,
            queue_spec_path=_relative_path(queue_spec_path, relative_to=paths.root),
            stable_spec_paths=relative_stable_spec_paths,
        ),
    )
    write_text_atomic(
        decision_path,
        _render_spec_review_decision(
            reviewed_at=review_timestamp,
            run_id=run_id,
            goal_id=source.idea_id,
            spec_id=spec_id,
            title=source.title,
            review_status=review_status,
            reviewed_path=_relative_path(reviewed_path, relative_to=paths.root),
            stable_registry_path=_relative_path(paths.specs_index_file, relative_to=paths.root),
            lineage_path=_relative_path(lineage_path, relative_to=paths.root),
        ),
    )
    write_text_atomic(reviewed_path, queue_spec_text)
    source_path = _resolve_path_token(source.source_path, relative_to=paths.root)
    if source_path != reviewed_path and source_path.exists():
        source_path.unlink()

    next_family_state, lineage_record = _build_goal_spec_review_state(
        paths=paths,
        spec_id=spec_id,
        goal_id=source.idea_id,
        queue_spec_path=queue_spec_path,
        reviewed_path=reviewed_path,
        questions_path=questions_path,
        decision_path=decision_path,
        stable_spec_paths=stable_spec_paths,
        review_status=review_status,
        emitted_at=review_timestamp,
    )
    current_family_state = write_goal_spec_family_state(
        paths.goal_spec_family_state_file,
        next_family_state,
        updated_at=review_timestamp,
    )
    _write_json_model(
        lineage_path,
        lineage_record,
    )
    _write_json_model(
        record_path,
        GoalSpecReviewRecord(
            spec_id=spec_id,
            review_status=review_status,
            questions_path=_relative_path(questions_path, relative_to=paths.root),
            decision_path=_relative_path(decision_path, relative_to=paths.root),
            reviewed_path=_relative_path(reviewed_path, relative_to=paths.root),
            reviewed_at=review_timestamp,
            findings=(),
        ),
    )
    refresh_stable_spec_registry(
        paths.specs_stable_dir,
        paths.specs_stable_dir / ".frozen",
        paths.specs_index_file,
        relative_to=paths.root,
        updated_at=review_timestamp,
    )

    return SpecReviewExecutionResult(
        record_path=_relative_path(record_path, relative_to=paths.root),
        questions_path=_relative_path(questions_path, relative_to=paths.root),
        decision_path=_relative_path(decision_path, relative_to=paths.root),
        reviewed_path=_relative_path(reviewed_path, relative_to=paths.root),
        lineage_path=_relative_path(lineage_path, relative_to=paths.root),
        stable_registry_path=_relative_path(paths.specs_index_file, relative_to=paths.root),
        family_state_path=_relative_path(paths.goal_spec_family_state_file, relative_to=paths.root),
        queue_ownership=ResearchQueueOwnership(
            family=ResearchQueueFamily.GOALSPEC,
            queue_path=paths.ideas_specs_reviewed_dir,
            item_path=reviewed_path,
            owner_token=run_id,
            acquired_at=review_timestamp,
        ),
    )


__all__ = [
    "AcceptanceProfileRecord",
    "CompletionManifestDraftArtifact",
    "CompletionManifestDraftExecutionResult",
    "CompletionManifestDraftRecord",
    "CompletionManifestDraftStateRecord",
    "GOALSPEC_ARTIFACT_SCHEMA_VERSION",
    "GoalIntakeExecutionResult",
    "GoalIntakeRecord",
    "GoalSource",
    "GoalSpecExecutionError",
    "ObjectiveProfileSyncExecutionResult",
    "ObjectiveProfileSyncRecord",
    "ObjectiveProfileSyncStateRecord",
    "SpecSynthesisExecutionResult",
    "SpecSynthesisRecord",
    "SpecReviewExecutionResult",
    "execute_completion_manifest_draft",
    "execute_goal_intake",
    "execute_objective_profile_sync",
    "execute_spec_review",
    "execute_spec_synthesis",
    "next_stage_for_success",
    "research_stage_for_node",
    "resolve_goal_source",
]
