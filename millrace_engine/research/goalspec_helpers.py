"""Shared GoalSpec helper primitives and source loading."""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import json
import re

from ..contracts import ContractModel
from ..markdown import write_text_atomic
from ..paths import RuntimePaths
from .dispatcher import ResearchDispatchError
from .normalization_helpers import _normalize_optional_text, _normalize_required_text
from .path_helpers import _normalize_path_token, _relative_path, _resolve_path_token
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


def _sha256_text(text: str) -> str:
    return sha256(text.encode("utf-8")).hexdigest()


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
    for raw_line in text[len(_FRONTMATTER_BOUNDARY) + 1 : end].splitlines():
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
