"""Strict Taskmaster shard generation for reviewed GoalSpec specs."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Literal
import re

from pydantic import Field, field_validator

from ..contracts import (
    ACCEPTANCE_ID_RE,
    ContractModel,
    FIELD_LINE_RE,
    REQUIREMENT_ID_RE,
    RegistryObjectRef,
    _normalize_datetime,
)
from ..markdown import parse_task_store, write_text_atomic
from ..materialization import ArchitectureMaterializer, MaterializationError
from ..paths import RuntimePaths
from .dispatcher import CompiledResearchDispatch
from .goalspec import GoalSpecExecutionError
from .normalization_helpers import _normalize_optional_text, _normalize_required_text
from .goalspec_persistence import _load_objective_profile_inputs
from .goalspec_scope_diagnostics import (
    build_goal_anchor_tokens,
    evaluate_scope_divergence,
    infer_goal_scope_kind,
    write_scope_divergence_record,
)
from .parser_helpers import _markdown_section, _split_frontmatter_block
from .path_helpers import _normalize_path_token, _relative_path, _resolve_path_token
from .persistence_helpers import _load_json_model, _write_json_model
from .specs import GoalSpecLineageRecord, load_goal_spec_family_state, write_goal_spec_family_state
from .state import ResearchCheckpoint


TASKMASTER_ARTIFACT_SCHEMA_VERSION = "1.0"
_FRONTMATTER_BOUNDARY = "---"
_NUMBERED_LINE_RE = re.compile(r"^(\d+)\.\s+(.*\S)\s*$")
_BACKTICKED_TOKEN_RE = re.compile(r"`([^`\n]+)`")
_FRAMEWORK_OBJECTIVE_HINTS = (
    "goalspec",
    "goal intake",
    "objective profile",
    "objective sync",
    "completion manifest",
    "taskmaster",
    "taskaudit",
    "queue governor",
    "family governor",
    "family policy",
    "stable spec registry",
    "research dispatcher",
    "research plane",
    "research runtime",
    "task provenance",
    "lineage",
)
_INTERNAL_PIPELINE_RE = re.compile(
    r"(?i)\b(?:_taskmaster\.md|_taskaudit\.md|merge_backlog\.py|queue_governor|"
    r"run\s+taskmaster|run\s+taskaudit|regenerate(?:\s+\w+){0,4}\s+backlog|"
    r"pending\s+backlog|task\s+queue\s+maintenance|task\s+store\s+maintenance|"
    r"task\s+store|agents/tasks(?:backlog|archive|pending|backburner)?\.md)\b"
)
_ROOT_LEVEL_REPO_FILES = {
    "README.md",
    "Makefile",
    "pyproject.toml",
    "package.json",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "Cargo.toml",
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _isoformat_z(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _dedupe(values: list[str]) -> tuple[str, ...]:
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        token = value.strip()
        if not token or token in seen:
            continue
        seen.add(token)
        deduped.append(token)
    return tuple(deduped)


def _field_value(body: str, field_name: str) -> str:
    target = field_name.casefold()
    for raw_line in body.splitlines():
        match = FIELD_LINE_RE.match(raw_line.strip())
        if match is None:
            continue
        if match.group(1).strip().casefold() != target:
            continue
        return match.group(2).strip()
    return ""


def _field_block_lines(body: str, field_name: str) -> tuple[str, ...]:
    target = field_name.casefold()
    collected: list[str] = []
    capture = False
    for raw_line in body.splitlines():
        stripped = raw_line.strip()
        match = FIELD_LINE_RE.match(stripped)
        if match is not None:
            name = match.group(1).strip().casefold()
            if capture and name != target:
                break
            if name == target:
                capture = True
                remainder = match.group(2).strip()
                if remainder:
                    collected.append(remainder)
                continue
        if not capture or not stripped:
            continue
        if FIELD_LINE_RE.match(stripped):
            break
        collected.append(stripped)
    return _dedupe(collected)


def _normalize_goal_token(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    return normalized or "goalspec"


def _goal_title(frontmatter: dict[str, str], spec_id: str) -> str:
    title = _normalize_optional_text(frontmatter.get("title"))
    return title or spec_id


def _acceptance_ids_for(requirement_ids: tuple[str, ...], source_text: str) -> tuple[tuple[str, ...], str]:
    source_acceptance_ids = _dedupe(ACCEPTANCE_ID_RE.findall(source_text))
    if source_acceptance_ids:
        return source_acceptance_ids, "source"
    derived = [f"AC-{requirement_id[4:]}" for requirement_id in requirement_ids]
    return _dedupe(derived), "derived_from_requirements"


def _verification_commands(reviewed_text: str) -> tuple[str, ...]:
    verification_section = _markdown_section(reviewed_text, "Verification")
    commands = [command.strip("`") for command in re.findall(r"`([^`]+)`", verification_section)]
    if commands:
        return _dedupe(commands)
    return (
        "python3 -c \"from pathlib import Path; assert Path('agents/audit/completion_manifest.json').exists(); assert Path('agents/reports/acceptance_profiles').exists()\"",
        "python3 -c \"from pathlib import Path; assert any(Path('agents/specs/stable/golden').glob('*.md')); assert any(Path('agents/specs/stable/phase').glob('*.md'))\"",
    )


def _looks_like_repo_relative_path(value: str) -> bool:
    token = _normalize_path_token(value)
    if not token or " " in token:
        return False
    if token.startswith(("http://", "https://", "file://", "/", "~")):
        return False
    if token.startswith("../"):
        return False
    if token in _ROOT_LEVEL_REPO_FILES:
        return True
    if "/" in token:
        return True
    return bool(Path(token).suffix)


def _extract_repo_relative_paths(text: str) -> tuple[str, ...]:
    matches = [match.group(1) for match in _BACKTICKED_TOKEN_RE.finditer(text)]
    return _dedupe([token for token in matches if _looks_like_repo_relative_path(token)])


def _dependency_paths(reviewed_text: str) -> tuple[str, ...]:
    dependencies = _markdown_section(reviewed_text, "Dependencies")
    references = _markdown_section(reviewed_text, "References")
    return _extract_repo_relative_paths(f"{dependencies}\n{references}")


def _phase_steps(phase_text: str, *, phase_key: str) -> tuple[tuple[str, str], ...]:
    work_plan = _markdown_section(phase_text, "Work Plan")
    steps: list[tuple[str, str]] = []
    for raw_line in work_plan.splitlines():
        match = _NUMBERED_LINE_RE.match(raw_line.strip())
        if match is None:
            continue
        step_index = match.group(1)
        description = " ".join(match.group(2).split())
        steps.append((f"{phase_key}.{step_index}", description))
    return tuple(steps)


def _strict_files_to_touch(
    *,
    reviewed_path: str,
    stable_spec_paths: tuple[str, ...],
    dependency_paths: tuple[str, ...],
) -> tuple[str, ...]:
    ordered = [reviewed_path, *stable_spec_paths, *dependency_paths]
    filtered = [path for path in ordered if path and not path.startswith("agents/tasks")]
    return _dedupe(filtered)


def _task_card_files_to_touch(body: str) -> tuple[str, ...]:
    lines = _field_block_lines(body, "Files to touch")
    return _dedupe([line.strip().strip("-* ").strip("`") for line in lines])


def _objective_scope_summary(
    *,
    reviewed_text: str,
    phase_text: str,
    reviewed_frontmatter: dict[str, str],
    phase_frontmatter: dict[str, str],
    spec_id: str,
) -> str:
    return "\n".join(
        [
            _goal_title(reviewed_frontmatter, spec_id),
            _goal_title(phase_frontmatter, spec_id),
            _markdown_section(reviewed_text, "Summary"),
            _markdown_section(phase_text, "Objective"),
        ]
    ).lower()


def _is_open_product_objective(
    *,
    family_phase: str,
    reviewed_text: str,
    phase_text: str,
    reviewed_frontmatter: dict[str, str],
    phase_frontmatter: dict[str, str],
    spec_id: str,
) -> bool:
    if family_phase == "goal_gap_remediation":
        return False
    scope_summary = _objective_scope_summary(
        reviewed_text=reviewed_text,
        phase_text=phase_text,
        reviewed_frontmatter=reviewed_frontmatter,
        phase_frontmatter=phase_frontmatter,
        spec_id=spec_id,
    )
    return not any(hint in scope_summary for hint in _FRAMEWORK_OBJECTIVE_HINTS)


def _validate_open_product_objective_shard(shard_path: Path, document: object) -> None:
    cards = getattr(document, "cards", ())
    if not cards:
        raise TaskmasterExecutionError(f"Taskmaster shard {shard_path.as_posix()} emitted no cards to validate")

    all_touched_paths: list[str] = []
    for card in cards:
        touched_paths = _task_card_files_to_touch(card.body)
        all_touched_paths.extend(touched_paths)
        if _INTERNAL_PIPELINE_RE.search(f"{card.title}\n{card.body}"):
            raise TaskmasterExecutionError(
                f"Taskmaster card {card.title!r} routes an open product objective into internal pipeline maintenance"
            )

    if not any(path and not path.startswith("agents/") for path in all_touched_paths):
        raise TaskmasterExecutionError(
            f"Taskmaster shard {shard_path.as_posix()} decomposed an open product objective into agents/*-only artifact surfaces"
        )


def _rewrite_emitted_task_paths(
    values: tuple[str, ...],
    *,
    replacements: dict[str, str],
) -> tuple[str, ...]:
    return _dedupe([replacements.get(value, value) for value in values])


def _render_task_card(
    *,
    emitted_at: datetime,
    title: str,
    goal_id: str,
    spec_id: str,
    requirement_ids: tuple[str, ...],
    acceptance_ids: tuple[str, ...],
    phase_step_id: str,
    phase_step_description: str,
    files_to_touch: tuple[str, ...],
    verification_commands: tuple[str, ...],
    dependencies: tuple[str, ...],
) -> str:
    files_block = "\n".join(f"  - `{path}`" for path in files_to_touch)
    steps_block = "\n".join(
        [
            f"  1. Implement `{phase_step_id}` by executing this bounded slice: {phase_step_description}.",
            f"  2. Preserve strict traceability for `{spec_id}` across the reviewed and stable spec artifacts before handoff.",
            "  3. Run the listed verification commands and keep the shard ready for Taskaudit without editing task stores.",
        ]
    )
    verification_block = "\n".join(f"  - `{command}`" for command in verification_commands)
    dependencies_text = ", ".join(f"`{dependency}`" for dependency in dependencies) if dependencies else "none"
    trace_tokens = " ".join(
        [
            f"objective:{_normalize_goal_token(goal_id)}",
            spec_id,
            *requirement_ids,
            *acceptance_ids,
            f"OUTCOME-{phase_step_id.replace('_', '-').replace('.', '-')}",
        ]
    )
    return "\n".join(
        [
            f"## {emitted_at.date().isoformat()} - {title}",
            "",
            f"- **Goal:** Execute `{phase_step_id}` for `{spec_id}` as one strict, reviewable slice.",
            f"- **Context:** Reviewed GoalSpec `{spec_id}` is decomposing phase step `{phase_step_id}` from the stable GoalSpec package.",
            f"- **Spec-ID:** {spec_id}",
            f"- **Requirement IDs:** {' '.join(requirement_ids)}",
            f"- **Acceptance IDs:** {' '.join(acceptance_ids)}",
            f"- **Phase Step IDs:** {phase_step_id}",
            "- **Lane:** OBJECTIVE",
            f"- **Contract Trace:** {trace_tokens}",
            "- **Prompt Source:** `agents/prompts/taskmaster_decompose.md`",
            "- **Files to touch:**",
            files_block,
            "- **Steps:**",
            steps_block,
            "- **Verification commands:**",
            verification_block,
            f"- **Dependencies:** {dependencies_text}",
            "- **Complexity:** INVOLVED",
            "- **Tags:** GOALSPEC TASKMASTER STRICT_CARD",
            "- **Gates:** NONE",
        ]
    ).rstrip()


def _validate_strict_shard(
    shard_path: Path,
    *,
    expected_spec_id: str,
    expected_card_count_min: int,
    expected_card_count_max: int,
    enforce_open_product_lane: bool = False,
) -> tuple[str, ...]:
    document = parse_task_store(shard_path.read_text(encoding="utf-8"), source_file=shard_path)
    card_count = len(document.cards)
    if card_count < expected_card_count_min:
        raise TaskmasterExecutionError(
            f"Taskmaster shard {shard_path.as_posix()} emitted {card_count} cards, below minimum {expected_card_count_min}"
        )
    if card_count > expected_card_count_max:
        raise TaskmasterExecutionError(
            f"Taskmaster shard {shard_path.as_posix()} emitted {card_count} cards, above maximum {expected_card_count_max}"
        )

    titles: list[str] = []
    for card in document.cards:
        titles.append(card.title)
        if card.spec_id != expected_spec_id:
            raise TaskmasterExecutionError(
                f"Taskmaster shard {shard_path.as_posix()} emitted card {card.title!r} with unexpected spec_id {card.spec_id!r}"
            )
        if not card.requirement_ids:
            raise TaskmasterExecutionError(f"Taskmaster card {card.title!r} is missing Requirement IDs")
        if not card.acceptance_ids:
            raise TaskmasterExecutionError(f"Taskmaster card {card.title!r} is missing Acceptance IDs")
        if not _field_value(card.body, "Phase Step IDs"):
            raise TaskmasterExecutionError(f"Taskmaster card {card.title!r} is missing Phase Step IDs")
        lane = _field_value(card.body, "Lane")
        if lane not in {"OBJECTIVE", "RELIABILITY", "INFRA", "DOCUMENTATION", "EXTERNAL_BLOCKED"}:
            raise TaskmasterExecutionError(f"Taskmaster card {card.title!r} has invalid Lane {lane!r}")
        trace = _field_value(card.body, "Contract Trace")
        if expected_spec_id not in trace:
            raise TaskmasterExecutionError(f"Taskmaster card {card.title!r} is missing spec trace in Contract Trace")
        prompt_source = _field_value(card.body, "Prompt Source").strip("`")
        if prompt_source != "agents/prompts/taskmaster_decompose.md":
            raise TaskmasterExecutionError(f"Taskmaster card {card.title!r} has invalid Prompt Source {prompt_source!r}")
        if not _field_block_lines(card.body, "Files to touch"):
            raise TaskmasterExecutionError(f"Taskmaster card {card.title!r} is missing Files to touch")
        steps = _field_block_lines(card.body, "Steps")
        if not steps:
            raise TaskmasterExecutionError(f"Taskmaster card {card.title!r} is missing Steps")
        verification = _field_block_lines(card.body, "Verification commands")
        if not verification or not all("`" in line for line in verification):
            raise TaskmasterExecutionError(f"Taskmaster card {card.title!r} is missing executable Verification commands")
    if enforce_open_product_lane:
        _validate_open_product_objective_shard(shard_path, document)
    return tuple(titles)


def _load_task_authoring_profile(
    paths: RuntimePaths,
    dispatch: CompiledResearchDispatch,
) -> tuple["TaskAuthoringProfileSelection", int, int]:
    content = dispatch.compile_result.plan.content if dispatch.compile_result.plan is not None else None
    if content is None or content.task_authoring_profile_ref is None:
        raise TaskmasterExecutionError("Compiled research dispatch is missing task_authoring_profile_ref for Taskmaster")

    materializer = ArchitectureMaterializer(paths.root)
    try:
        _, document = materializer.lookup_registry_object(content.task_authoring_profile_ref)
    except MaterializationError as exc:
        raise TaskmasterExecutionError(str(exc)) from exc

    profile_definition = document.definition
    payload = getattr(profile_definition, "payload", None)
    if payload is None or not hasattr(payload, "expected_card_count"):
        raise TaskmasterExecutionError(
            f"Taskmaster profile {content.task_authoring_profile_ref.id} does not expose expected_card_count"
        )

    provenance_by_path = {entry.path: entry for entry in dispatch.research_plan.provenance}
    selection_entry = provenance_by_path.get("mode.task_authoring_profile_ref") or provenance_by_path.get(
        "loop.task_authoring_profile_ref"
    )
    lookup_entry = provenance_by_path.get("mode.task_authoring_profile_lookup_ref") or provenance_by_path.get(
        "loop.task_authoring_profile_lookup_ref"
    )
    selection = TaskAuthoringProfileSelection(
        selected_mode_ref=content.selected_mode_ref,
        task_authoring_profile_ref=content.task_authoring_profile_ref,
        selection_path=selection_entry.path if selection_entry is not None else "compile.content.task_authoring_profile_ref",
        lookup_path=lookup_entry.path if lookup_entry is not None else "",
        selection_source=selection_entry.source.value if selection_entry is not None else "compiled_plan",
        selection_detail=selection_entry.detail if selection_entry is not None else "compiled research dispatch",
        required_metadata_fields=getattr(payload, "required_metadata_fields", ()),
        expected_min_cards=payload.expected_card_count.min_cards,
        expected_max_cards=payload.expected_card_count.max_cards,
    )
    return selection, payload.expected_card_count.min_cards, payload.expected_card_count.max_cards


def _finish_source_idea(paths: RuntimePaths, family_state_path: str, *, emitted_at: datetime) -> str:
    if not family_state_path:
        return ""
    source_path = _resolve_path_token(family_state_path, relative_to=paths.root)
    if not source_path.exists():
        return _normalize_path_token(family_state_path)
    finished_dir = paths.ideas_dir / "finished"
    finished_dir.mkdir(parents=True, exist_ok=True)
    finished_path = finished_dir / source_path.name
    text = source_path.read_text(encoding="utf-8")
    frontmatter, body = _split_frontmatter_block(text, boundary=_FRONTMATTER_BOUNDARY)
    if frontmatter:
        frontmatter["status"] = "finished"
        ordered = [f"{key}: {value}" for key, value in frontmatter.items()]
        text = f"---\n" + "\n".join(ordered) + f"\n---\n{body}"
    write_text_atomic(finished_path, text)
    source_path.unlink()
    return _relative_path(finished_path, relative_to=paths.root)


class TaskAuthoringProfileSelection(ContractModel):
    """Explainable task-authoring-profile choice for one Taskmaster run."""

    selected_mode_ref: RegistryObjectRef | None = None
    task_authoring_profile_ref: RegistryObjectRef
    selection_path: str
    lookup_path: str = ""
    selection_source: str
    selection_detail: str
    required_metadata_fields: tuple[str, ...] = ()
    expected_min_cards: int = Field(ge=1)
    expected_max_cards: int = Field(ge=1)

    @field_validator("selection_path", "lookup_path", "selection_source", "selection_detail")
    @classmethod
    def validate_text(cls, value: str, info: object) -> str:
        field_name = getattr(info, "field_name", "value")
        if field_name == "lookup_path":
            return _normalize_optional_text(value)
        return _normalize_required_text(value, field_name=field_name)

    @field_validator("required_metadata_fields", mode="before")
    @classmethod
    def normalize_required_metadata_fields(
        cls,
        value: tuple[str, ...] | list[str] | None,
    ) -> tuple[str, ...]:
        if not value:
            return ()
        return _dedupe([str(item) for item in value])


class TaskmasterRecord(ContractModel):
    """Durable record for one strict Taskmaster shard emission."""

    schema_version: Literal["1.0"] = TASKMASTER_ARTIFACT_SCHEMA_VERSION
    artifact_type: Literal["taskmaster_strict_shard"] = "taskmaster_strict_shard"
    run_id: str
    emitted_at: datetime
    spec_id: str
    goal_id: str = ""
    reviewed_path: str
    archived_path: str
    shard_path: str
    source_idea_path: str = ""
    finished_source_path: str = ""
    stable_spec_paths: tuple[str, ...] = ()
    family_state_path: str
    lineage_path: str
    profile_selection: TaskAuthoringProfileSelection
    acceptance_id_source: Literal["source", "derived_from_requirements"] = "source"
    card_count: int = Field(ge=1)
    task_titles: tuple[str, ...] = ()

    @field_validator("emitted_at", mode="before")
    @classmethod
    def normalize_emitted_at(cls, value: datetime | str) -> datetime:
        return _normalize_datetime(value)

    @field_validator(
        "run_id",
        "spec_id",
        "reviewed_path",
        "archived_path",
        "shard_path",
        "family_state_path",
        "lineage_path",
    )
    @classmethod
    def validate_required_text_fields(cls, value: str, info: object) -> str:
        field_name = getattr(info, "field_name", "value")
        return _normalize_required_text(value, field_name=field_name)

    @field_validator("goal_id", "source_idea_path", "finished_source_path", mode="before")
    @classmethod
    def normalize_optional_text_fields(cls, value: str | None) -> str:
        return _normalize_optional_text(value)

    @field_validator("stable_spec_paths", mode="before")
    @classmethod
    def normalize_stable_spec_paths(
        cls,
        value: tuple[str, ...] | list[str] | None,
    ) -> tuple[str, ...]:
        if not value:
            return ()
        return _dedupe([_normalize_path_token(item) for item in value])

    @field_validator("task_titles", mode="before")
    @classmethod
    def normalize_task_titles(cls, value: tuple[str, ...] | list[str] | None) -> tuple[str, ...]:
        if not value:
            return ()
        return _dedupe([str(item) for item in value])


class TaskmasterExecutionResult(ContractModel):
    """Minimal execution result returned to the research plane."""

    record_path: str
    shard_path: str
    archived_path: str
    lineage_path: str
    family_state_path: str
    finished_source_path: str = ""
    card_count: int = Field(ge=1)

    @field_validator("record_path", "shard_path", "archived_path", "lineage_path", "family_state_path")
    @classmethod
    def validate_required_paths(cls, value: str, info: object) -> str:
        field_name = getattr(info, "field_name", "value")
        return _normalize_required_text(value, field_name=field_name)

    @field_validator("finished_source_path", mode="before")
    @classmethod
    def normalize_finished_source_path(cls, value: str | None) -> str:
        return _normalize_optional_text(value)


class TaskmasterExecutionError(GoalSpecExecutionError):
    """Raised when strict Taskmaster shard generation cannot complete safely."""


def execute_taskmaster(
    paths: RuntimePaths,
    checkpoint: ResearchCheckpoint,
    *,
    dispatch: CompiledResearchDispatch,
    run_id: str,
    emitted_at: datetime | None = None,
) -> TaskmasterExecutionResult:
    """Generate one strict pending shard from one reviewed GoalSpec spec."""

    emitted_at = emitted_at or _utcnow()
    record_path = paths.goalspec_runtime_dir / "taskmaster" / f"{run_id}.json"
    if record_path.exists():
        existing_record = _load_json_model(record_path, TaskmasterRecord)
        archived_path = _resolve_path_token(existing_record.archived_path, relative_to=paths.root)
        shard_path = _resolve_path_token(existing_record.shard_path, relative_to=paths.root)
        lineage_path = _resolve_path_token(existing_record.lineage_path, relative_to=paths.root)
        family_state_path = _resolve_path_token(existing_record.family_state_path, relative_to=paths.root)
        if archived_path.exists() and shard_path.exists() and lineage_path.exists() and family_state_path.exists():
            return TaskmasterExecutionResult(
                record_path=_relative_path(record_path, relative_to=paths.root),
                shard_path=existing_record.shard_path,
                archived_path=existing_record.archived_path,
                lineage_path=existing_record.lineage_path,
                family_state_path=existing_record.family_state_path,
                finished_source_path=existing_record.finished_source_path,
                card_count=existing_record.card_count,
            )

    family_state = load_goal_spec_family_state(paths.goal_spec_family_state_file)
    if checkpoint.owned_queues:
        reviewed_spec_path = checkpoint.owned_queues[0].item_path
    else:
        reviewed_candidates = sorted(paths.ideas_specs_reviewed_dir.glob("*.md"), key=lambda path: path.as_posix())
        if not reviewed_candidates:
            raise TaskmasterExecutionError("Taskmaster requires one reviewed spec")
        reviewed_spec_path = reviewed_candidates[0]
    if not reviewed_spec_path.exists():
        raise TaskmasterExecutionError(f"Reviewed spec is missing: {reviewed_spec_path.as_posix()}")

    reviewed_relative_path = _relative_path(reviewed_spec_path, relative_to=paths.root)
    reviewed_text = reviewed_spec_path.read_text(encoding="utf-8")
    frontmatter, _ = _split_frontmatter_block(reviewed_text, boundary=_FRONTMATTER_BOUNDARY)
    spec_id = frontmatter.get("spec_id", "").strip()
    if not spec_id:
        raise TaskmasterExecutionError(f"Reviewed spec is missing spec_id frontmatter: {reviewed_relative_path}")

    spec_state = family_state.specs.get(spec_id)
    if spec_state is None:
        raise TaskmasterExecutionError(f"GoalSpec family state is missing reviewed spec {spec_id}")

    profile_selection, min_cards, max_cards = _load_task_authoring_profile(paths, dispatch)
    requirement_ids = _dedupe(REQUIREMENT_ID_RE.findall(reviewed_text))
    if not requirement_ids:
        raise TaskmasterExecutionError(f"Reviewed spec {spec_id} is missing REQ-* traceability")
    acceptance_ids, acceptance_id_source = _acceptance_ids_for(requirement_ids, reviewed_text)

    stable_spec_paths = tuple(spec_state.stable_spec_paths)
    phase_relative_paths = tuple(path for path in stable_spec_paths if "/phase/" in path)
    if not phase_relative_paths:
        raise TaskmasterExecutionError(f"GoalSpec family state is missing a stable phase spec for {spec_id}")
    primary_phase_path = _resolve_path_token(phase_relative_paths[0], relative_to=paths.root)
    phase_text = primary_phase_path.read_text(encoding="utf-8")
    phase_frontmatter, _ = _split_frontmatter_block(phase_text, boundary=_FRONTMATTER_BOUNDARY)
    phase_key = phase_frontmatter.get("phase_key", "").strip() or "PHASE_01"
    phase_steps = _phase_steps(phase_text, phase_key=phase_key)
    if not phase_steps:
        raise TaskmasterExecutionError(f"Stable phase spec {primary_phase_path.as_posix()} is missing Work Plan steps")

    if len(phase_steps) < min_cards or len(phase_steps) > max_cards:
        raise TaskmasterExecutionError(
            f"Stable phase spec for {spec_id} yields {len(phase_steps)} steps outside expected card range {min_cards}-{max_cards}"
        )

    dependency_paths = _dependency_paths(reviewed_text)
    verification_commands = _verification_commands(reviewed_text)
    archived_path = paths.ideas_archive_dir / reviewed_spec_path.name
    archived_relative_path = _relative_path(archived_path, relative_to=paths.root)
    path_replacements = {reviewed_relative_path: archived_relative_path}
    if family_state.family_complete and family_state.source_idea_path:
        finished_source_relative_path = _relative_path(
            paths.ideas_dir / "finished" / Path(family_state.source_idea_path).name,
            relative_to=paths.root,
        )
        path_replacements[family_state.source_idea_path] = finished_source_relative_path
    dependency_paths = _rewrite_emitted_task_paths(dependency_paths, replacements=path_replacements)
    files_to_touch = _rewrite_emitted_task_paths(
        _strict_files_to_touch(
            reviewed_path=reviewed_relative_path,
            stable_spec_paths=stable_spec_paths,
            dependency_paths=dependency_paths,
        ),
        replacements=path_replacements,
    )
    if not files_to_touch:
        raise TaskmasterExecutionError(f"Taskmaster could not resolve Files to touch for {spec_id}")
    enforce_open_product_lane = _is_open_product_objective(
        family_phase=family_state.family_phase,
        reviewed_text=reviewed_text,
        phase_text=phase_text,
        reviewed_frontmatter=frontmatter,
        phase_frontmatter=phase_frontmatter,
        spec_id=spec_id,
    )

    pending_dir = paths.agents_dir / "taskspending"
    pending_dir.mkdir(parents=True, exist_ok=True)
    shard_path = pending_dir / f"{spec_id}.md"
    taskspending_before = (
        paths.taskspending_file.read_text(encoding="utf-8")
        if paths.taskspending_file.exists()
        else ""
    )

    shard_text = "\n\n".join(
        _render_task_card(
            emitted_at=emitted_at,
            title=f"{spec_id} {phase_step_id} - {phase_step_description}",
            goal_id=family_state.goal_id or frontmatter.get("idea_id", "").strip(),
            spec_id=spec_id,
            requirement_ids=requirement_ids,
            acceptance_ids=acceptance_ids,
            phase_step_id=phase_step_id,
            phase_step_description=phase_step_description,
            files_to_touch=files_to_touch,
            verification_commands=verification_commands,
            dependencies=dependency_paths or phase_relative_paths,
        )
        for phase_step_id, phase_step_description in phase_steps
    ).rstrip() + "\n"
    _objective_state, profile = _load_objective_profile_inputs(paths)
    scope_record = evaluate_scope_divergence(
        run_id=run_id,
        emitted_at=emitted_at,
        goal_id=family_state.goal_id or frontmatter.get("idea_id", "").strip() or spec_id,
        title=frontmatter.get("title", "").strip() or spec_id,
        stage_name="taskmaster",
        source_path=family_state.source_idea_path or reviewed_relative_path,
        expected_scope=infer_goal_scope_kind(
            title=frontmatter.get("title", "").strip() or spec_id,
            source_body=reviewed_text,
            semantic_summary=profile.semantic_profile.objective_summary,
            capability_domains=tuple(profile.semantic_profile.capability_domains),
        ),
        goal_anchor_tokens=build_goal_anchor_tokens(
            title=frontmatter.get("title", "").strip() or spec_id,
            source_body=reviewed_text,
            semantic_summary=profile.semantic_profile.objective_summary,
            capability_domains=tuple(profile.semantic_profile.capability_domains),
            progression_lines=tuple(profile.semantic_profile.progression_lines),
        ),
        surfaces=(
            ("reviewed_spec", reviewed_text),
            (
                "task_surfaces",
                "\n".join(
                    (
                        shard_text,
                        "Files to touch:",
                        *(f"- {path}" for path in files_to_touch),
                    )
                ),
            ),
        ),
    )
    if scope_record.decision == "blocked":
        record_path = write_scope_divergence_record(paths, scope_record)
        raise TaskmasterExecutionError(
            f"Scope divergence blocked {spec_id} during taskmaster; diagnostic: {record_path}"
        )
    write_text_atomic(shard_path, shard_text)

    task_titles = _validate_strict_shard(
        shard_path,
        expected_spec_id=spec_id,
        expected_card_count_min=min_cards,
        expected_card_count_max=max_cards,
        enforce_open_product_lane=enforce_open_product_lane,
    )
    taskspending_after = (
        paths.taskspending_file.read_text(encoding="utf-8")
        if paths.taskspending_file.exists()
        else ""
    )
    if taskspending_after != taskspending_before:
        raise TaskmasterExecutionError("Taskmaster must not mutate agents/taskspending.md during strict shard generation")

    archived_path.parent.mkdir(parents=True, exist_ok=True)
    reviewed_spec_path.replace(archived_path)

    next_spec_state = spec_state.model_copy(
        update={
            "status": "decomposed",
            "reviewed_path": "",
            "archived_path": archived_relative_path,
            "pending_shard_path": _relative_path(shard_path, relative_to=paths.root),
        }
    )
    next_specs = dict(family_state.specs)
    next_specs[spec_id] = next_spec_state
    next_family_state = family_state.model_copy(
        update={
            "active_spec_id": spec_id,
            "specs": next_specs,
            "updated_at": emitted_at,
        }
    )
    write_goal_spec_family_state(paths.goal_spec_family_state_file, next_family_state, updated_at=emitted_at)

    lineage_path = paths.goalspec_lineage_dir / f"{spec_id}.json"
    lineage_record = GoalSpecLineageRecord(
        spec_id=spec_id,
        goal_id=next_family_state.goal_id,
        source_idea_path=next_family_state.source_idea_path,
        queue_path=next_spec_state.queue_path,
        reviewed_path="",
        archived_path=archived_relative_path,
        stable_spec_paths=next_spec_state.stable_spec_paths,
        pending_shard_path=_relative_path(shard_path, relative_to=paths.root),
    )
    _write_json_model(lineage_path, lineage_record)

    finished_source_path = ""
    if next_family_state.family_complete and next_family_state.source_idea_path:
        finished_source_path = _finish_source_idea(paths, next_family_state.source_idea_path, emitted_at=emitted_at)

    record = TaskmasterRecord(
        run_id=run_id,
        emitted_at=emitted_at,
        spec_id=spec_id,
        goal_id=next_family_state.goal_id or frontmatter.get("idea_id", "").strip(),
        reviewed_path=reviewed_relative_path,
        archived_path=archived_relative_path,
        shard_path=_relative_path(shard_path, relative_to=paths.root),
        source_idea_path=next_family_state.source_idea_path,
        finished_source_path=finished_source_path,
        stable_spec_paths=stable_spec_paths,
        family_state_path=_relative_path(paths.goal_spec_family_state_file, relative_to=paths.root),
        lineage_path=_relative_path(lineage_path, relative_to=paths.root),
        profile_selection=profile_selection,
        acceptance_id_source=acceptance_id_source,  # type: ignore[arg-type]
        card_count=len(task_titles),
        task_titles=task_titles,
    )
    _write_json_model(record_path, record)

    return TaskmasterExecutionResult(
        record_path=_relative_path(record_path, relative_to=paths.root),
        shard_path=_relative_path(shard_path, relative_to=paths.root),
        archived_path=archived_relative_path,
        lineage_path=_relative_path(lineage_path, relative_to=paths.root),
        family_state_path=_relative_path(paths.goal_spec_family_state_file, relative_to=paths.root),
        finished_source_path=finished_source_path,
        card_count=len(task_titles),
    )


__all__ = [
    "TASKMASTER_ARTIFACT_SCHEMA_VERSION",
    "TaskAuthoringProfileSelection",
    "TaskmasterExecutionError",
    "TaskmasterExecutionResult",
    "TaskmasterRecord",
    "execute_taskmaster",
]
