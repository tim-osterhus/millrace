"""Shared GoalSpec helper primitives and source loading."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import re

from ..contracts import ContractModel
from ..paths import RuntimePaths
from .dispatcher import ResearchDispatchError
from .normalization_helpers import _normalize_optional_text, _normalize_required_text
from .parser_helpers import (
    _markdown_section as _shared_markdown_section,
    _split_frontmatter_block as _shared_split_frontmatter_block,
)
from .path_helpers import _normalize_path_token, _relative_path, _resolve_path_token
from .persistence_helpers import (
    _load_json_model as _shared_load_json_model,
    _load_json_object as _shared_load_json_object,
    _sha256_text,
    _write_json_model as _shared_write_json_model,
)
from .specs import GoalSpecDecompositionProfile
from .state import ResearchCheckpoint


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


class GoalSpecExecutionError(ResearchDispatchError):
    """Raised when GoalSpec execution cannot complete safely."""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _isoformat_z(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _slugify(value: str) -> str:
    slug = _TOKEN_RE.sub("-", value.strip().lower()).strip("-")
    return slug or "goal"


def _load_json_object(path: Path) -> dict[str, object]:
    try:
        return _shared_load_json_object(path)
    except ValueError as exc:
        raise GoalSpecExecutionError(f"{path.as_posix()} must contain a JSON object") from exc


def _write_json_model(path: Path, model: ContractModel) -> None:
    _shared_write_json_model(path, model, create_parent=True)


def _load_json_model(path: Path, model_cls: type[ContractModel]) -> ContractModel:
    return _shared_load_json_model(path, model_cls)


def _split_frontmatter(text: str) -> tuple[dict[str, str], str]:
    return _shared_split_frontmatter_block(text, boundary=_FRONTMATTER_BOUNDARY)


def _markdown_section(body: str, heading: str) -> str:
    return _shared_markdown_section(body, heading)


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


def _normalize_decomposition_profile(value: str | None) -> GoalSpecDecompositionProfile:
    normalized = (value or "").strip().lower()
    if normalized not in _SUPPORTED_DECOMPOSITION_PROFILES:
        return "simple"
    return normalized  # type: ignore[return-value]


def _canonical_goal_path_from_frontmatter(paths: RuntimePaths, frontmatter: dict[str, str]) -> Path | None:
    canonical_relative_path = str(frontmatter.get("canonical_source_path") or "").strip()
    if canonical_relative_path:
        candidate = _resolve_path_token(canonical_relative_path, relative_to=paths.root)
        if candidate.exists():
            return candidate

    goal_intake_run_id = str(frontmatter.get("goal_intake_run_id") or "").strip()
    if goal_intake_run_id:
        record_path = paths.goalspec_goal_intake_records_dir / f"{goal_intake_run_id}.json"
        if record_path.exists():
            record_payload = _load_json_object(record_path)
            candidate_relative_path = str(
                record_payload.get("canonical_source_path")
                or record_payload.get("archived_source_path")
                or record_payload.get("source_path")
                or ""
            ).strip()
            if candidate_relative_path:
                candidate = _resolve_path_token(candidate_relative_path, relative_to=paths.root)
                if candidate.exists():
                    return candidate

    source_relative_path = str(frontmatter.get("source_path") or "").strip()
    if source_relative_path:
        candidate = _resolve_path_token(source_relative_path, relative_to=paths.root)
        if candidate.exists():
            return candidate
    return None


def resolve_goal_source(paths: RuntimePaths, checkpoint: ResearchCheckpoint):
    """Resolve the current GoalSpec source artifact from checkpoint state."""

    from .goalspec import GoalSource

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

    current_text = source_path.read_text(encoding="utf-8", errors="replace")
    frontmatter, body = _split_frontmatter(current_text)
    idea_id = (
        frontmatter.get("idea_id")
        or frontmatter.get("goal_id")
        or frontmatter.get("id")
        or source_path.stem.split("__", 1)[0]
        or f"goal-{_slugify(source_path.stem)}"
    )
    title = frontmatter.get("title") or _first_heading(body) or source_path.stem.replace("-", " ").replace("_", " ")
    decomposition_profile = _normalize_decomposition_profile(frontmatter.get("decomposition_profile"))
    canonical_source_path = _canonical_goal_path_from_frontmatter(paths, frontmatter) or source_path
    canonical_text = canonical_source_path.read_text(encoding="utf-8", errors="replace")
    _, canonical_body = _split_frontmatter(canonical_text)
    return GoalSource(
        current_artifact_path=source_path.as_posix(),
        current_artifact_relative_path=_relative_path(source_path, relative_to=paths.root),
        canonical_source_path=canonical_source_path.as_posix(),
        canonical_relative_source_path=_relative_path(canonical_source_path, relative_to=paths.root),
        source_path=source_path.as_posix(),
        relative_source_path=_relative_path(source_path, relative_to=paths.root),
        idea_id=idea_id,
        title=_normalize_required_text(title, field_name="title"),
        decomposition_profile=decomposition_profile,
        frontmatter=frontmatter,
        body=body.strip() or current_text.strip(),
        canonical_body=canonical_body.strip() or canonical_text.strip(),
        checksum_sha256=_sha256_text(current_text),
    )
