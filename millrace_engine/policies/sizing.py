"""Deterministic repo/task size classification and durable latch helpers."""

from __future__ import annotations

from enum import Enum
from pathlib import Path
import re

from pydantic import ConfigDict, Field, field_validator

from ..config import SizingConfig
from ..contracts import ContractModel, TaskCard
from ..markdown import write_text_atomic


class SizeClass(str, Enum):
    SMALL = "SMALL"
    LARGE = "LARGE"

    @property
    def marker(self) -> str:
        return f"### {self.value}"


class SizeStatusError(ValueError):
    """Raised when the size-status latch does not contain one valid marker line."""


class RepoSizeEvidence(ContractModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    file_count: int = Field(ge=0)
    nonempty_line_count: int = Field(ge=0)
    file_count_threshold: int = Field(ge=1)
    nonempty_line_count_threshold: int = Field(ge=1)
    classified_as: SizeClass
    threshold_hits: tuple[str, ...] = ()


class TaskSizeEvidence(ContractModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    active_task_id: str | None = None
    file_count: int = Field(ge=0)
    nonempty_line_count: int = Field(ge=0)
    file_count_threshold: int = Field(ge=1)
    nonempty_line_count_threshold: int = Field(ge=1)
    complexity: str | None = None
    complexity_band: str = "MODERATE"
    complexity_threshold: str = "INVOLVED|COMPLEX"
    file_count_source: str = "unavailable"
    nonempty_line_count_source: str = "unavailable"
    minimum_signal_count: int = Field(default=2, ge=1, le=3)
    qualifying_signal_count: int = Field(default=0, ge=0, le=3)
    files_to_touch: tuple[str, ...] = ()
    missing_files_to_touch: tuple[str, ...] = ()
    adaptive_upscope: "AdaptiveUpscopeEvidence | None" = None
    classified_as: SizeClass
    threshold_hits: tuple[str, ...] = ()

    @field_validator(
        "active_task_id",
        "complexity",
        "complexity_band",
        "complexity_threshold",
        "file_count_source",
        "nonempty_line_count_source",
    )
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = " ".join(value.strip().split())
        return normalized or None


class AdaptiveUpscopeEvidence(ContractModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    target: SizeClass
    rule: str
    stage: str
    reason: str

    @field_validator("rule", "stage", "reason")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        normalized = " ".join(value.strip().split())
        if not normalized:
            raise ValueError("adaptive upscope fields may not be empty")
        return normalized


class SizeClassificationView(ContractModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    mode: str
    classified_as: SizeClass
    latched_as: SizeClass
    latch_reason: str
    triggered_sources: tuple[str, ...] = ()
    repo: RepoSizeEvidence
    task: TaskSizeEvidence

    @field_validator("mode", "latch_reason")
    @classmethod
    def normalize_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("text fields may not be empty")
        return normalized


_DEFAULT_SIZE_STATUS = SizeClass.SMALL
_DEFAULT_COMPLEXITY_BAND = "MODERATE"
_TASK_SIGNAL_MINIMUM = 2
_FIELD_LINE_RE = re.compile(r"^\s*(?:[-*]\s*)?\*\*(.+?):\*\*\s*(.*)$")
_FILES_HEADING_RE = re.compile(r"^\s*#{2,6}\s*files to touch(?:\s*\(explicit\))?\s*:?\s*$", re.IGNORECASE)
_SKIP_NAMES = frozenset({".git", "staging", "node_modules", ".venv", "venv", "__pycache__"})
_SKIP_PREFIXES = ("agents",)
_FILE_LIST_METADATA_KEYS = (
    "files_to_touch",
    "files_to_touch_explicit",
)
_FILE_COUNT_METADATA_KEYS = (
    "files_to_touch_count",
    "task_large_files",
    "estimated_files_to_touch",
)
_LOC_METADATA_KEYS = (
    "files_to_touch_loc",
    "task_large_loc",
    "loc_to_touch",
    "estimated_loc_to_touch",
)
_FILE_COUNT_BODY_FIELDS = (
    "files to touch count",
    "task large files",
)
_LOC_BODY_FIELDS = (
    "files to touch loc",
    "loc to touch",
    "task large loc",
)
_ADAPTIVE_UPSCOPE_FIELD = "adaptive upscope"
_ADAPTIVE_UPSCOPE_RULE_FIELD = "adaptive upscope rule"
_ADAPTIVE_UPSCOPE_STAGE_FIELD = "adaptive upscope stage"
_ADAPTIVE_UPSCOPE_REASON_FIELD = "adaptive upscope reason"
_ADAPTIVE_UPSCOPE_THRESHOLD_HIT = "adaptive_upscope"
_ADAPTIVE_UPSCOPE_LARGE_RULE = "blocked_small_non_usage_v1"


def format_size_status(value: SizeClass) -> str:
    """Return the canonical size-status payload."""

    return value.marker + "\n"


def parse_size_status(text: str) -> SizeClass:
    """Parse one authoritative size-status marker."""

    lines = [line.strip() for line in text.replace("\r\n", "\n").splitlines() if line.strip()]
    if len(lines) != 1 or not lines[0].startswith("### "):
        raise SizeStatusError("size_status.md must contain exactly one authoritative marker line")
    token = lines[0].removeprefix("### ").strip().upper()
    try:
        return SizeClass(token)
    except ValueError as exc:
        raise SizeStatusError(f"unknown size status marker: {token}") from exc


class SizeStatusStore:
    """Overwrite-only helper for the durable size-status latch."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def ensure(self) -> SizeClass:
        """Create the latch file when missing and return the current value."""

        if not self.path.exists():
            self.write(_DEFAULT_SIZE_STATUS)
            return _DEFAULT_SIZE_STATUS
        return self.read()

    def read(self) -> SizeClass:
        """Read and validate the current latch value."""

        return parse_size_status(self.path.read_text(encoding="utf-8"))

    def write(self, value: SizeClass) -> SizeClass:
        """Persist one authoritative size marker."""

        write_text_atomic(self.path, format_size_status(value))
        return value


def _is_skipped_repo_path(root: Path, path: Path) -> bool:
    rel = path.relative_to(root).as_posix()
    parts = rel.split("/")
    if any(part in _SKIP_NAMES for part in parts):
        return True
    return any(rel == prefix or rel.startswith(f"{prefix}/") for prefix in _SKIP_PREFIXES)


def _read_nonempty_line_count(path: Path) -> int | None:
    try:
        payload = path.read_bytes()
    except OSError:
        return None

    if b"\x00" in payload:
        return 0

    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError:
        try:
            text = payload.decode("latin-1")
        except UnicodeDecodeError:
            return None

    return sum(1 for line in text.splitlines() if line.strip())


def _normalize_metadata_keys(metadata: dict[str, object]) -> dict[str, object]:
    normalized: dict[str, object] = {}
    for key, value in metadata.items():
        token = str(key).strip().casefold().replace("-", "_").replace(" ", "_")
        if token:
            normalized[token] = value
    return normalized


def _normalize_file_entry(raw: str) -> str | None:
    normalized = raw.strip()
    if normalized.startswith(("- ", "* ")):
        normalized = normalized[2:].strip()
    normalized = normalized.strip("`").strip()
    if not normalized or normalized.casefold() == "none":
        return None
    return normalized


def _coerce_file_list(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    raw_items: list[str] = []
    if isinstance(value, str):
        if value.strip().isdigit():
            return ()
        raw_items.extend(part for part in value.splitlines() if part.strip())
    elif isinstance(value, (list, tuple, set, frozenset)):
        raw_items.extend(str(item) for item in value)
    else:
        return ()

    normalized: list[str] = []
    seen: set[str] = set()
    for raw in raw_items:
        item = _normalize_file_entry(raw)
        if item is None:
            continue
        key = item.casefold()
        if key in seen:
            continue
        seen.add(key)
        normalized.append(item)
    return tuple(normalized)


def _coerce_nonnegative_int(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = int(text)
    except ValueError:
        return None
    return parsed if parsed >= 0 else None


def _extract_markdown_field_block_lines(body: str, field_names: tuple[str, ...]) -> tuple[str, ...]:
    targets = {name.casefold() for name in field_names}
    lines = body.splitlines()
    collected: list[str] = []
    capture = False

    for line in lines:
        stripped = line.strip()
        field_match = _FIELD_LINE_RE.match(stripped)
        if field_match:
            name = field_match.group(1).strip().casefold()
            if capture and name not in targets:
                break
            if name in targets:
                capture = True
                remainder = field_match.group(2).strip()
                if remainder:
                    collected.append(remainder)
                continue

        if not capture:
            continue
        if not stripped:
            continue
        if _FIELD_LINE_RE.match(stripped):
            break
        collected.append(stripped)

    return tuple(collected)


def _extract_field_value(body: str, field_name: str) -> str | None:
    target = field_name.casefold()
    for line in body.splitlines():
        match = _FIELD_LINE_RE.match(line.strip())
        if not match:
            continue
        if match.group(1).strip().casefold() != target:
            continue
        value = match.group(2).strip()
        return value or None
    return None


def _extract_files_heading_lines(body: str) -> tuple[str, ...]:
    lines = body.splitlines()
    collected: list[str] = []
    capture = False

    for line in lines:
        stripped = line.strip()
        if _FILES_HEADING_RE.match(stripped):
            capture = True
            continue
        if not capture:
            continue
        if not stripped:
            continue
        if stripped.startswith("#") or _FIELD_LINE_RE.match(stripped):
            break
        collected.append(stripped)

    return tuple(collected)


def _extract_body_file_list(body: str) -> tuple[str, ...]:
    candidates = (
        *_extract_markdown_field_block_lines(body, ("files to touch", "files to touch (explicit)")),
        *_extract_files_heading_lines(body),
    )
    return _coerce_file_list(list(candidates))


def _extract_body_metric(body: str, field_names: tuple[str, ...]) -> int | None:
    for line in _extract_markdown_field_block_lines(body, field_names):
        parsed = _coerce_nonnegative_int(line)
        if parsed is not None:
            return parsed
    return None


def _extract_metadata_metric(metadata: dict[str, object], keys: tuple[str, ...]) -> int | None:
    for key in keys:
        parsed = _coerce_nonnegative_int(metadata.get(key))
        if parsed is not None:
            return parsed
    return None


def _resolve_task_loc_fallback(task: TaskCard, metadata: dict[str, object]) -> tuple[int | None, str]:
    metadata_loc = _extract_metadata_metric(metadata, _LOC_METADATA_KEYS)
    if metadata_loc is not None:
        return metadata_loc, "metadata:nonempty_line_count"
    body_loc = _extract_body_metric(task.body, _LOC_BODY_FIELDS)
    if body_loc is not None:
        return body_loc, "body:nonempty_line_count"
    return None, "unavailable"


def _extract_explicit_files_to_touch(task: TaskCard) -> tuple[tuple[str, ...], str]:
    metadata = _normalize_metadata_keys(task.metadata)
    for key in _FILE_LIST_METADATA_KEYS:
        paths = _coerce_file_list(metadata.get(key))
        if paths:
            return paths, f"metadata:{key}"
    body_paths = _extract_body_file_list(task.body)
    if body_paths:
        return body_paths, "body:files_to_touch"
    return (), "unavailable"


def _normalize_complexity_band(value: str | None) -> str:
    raw = (value or "").strip().upper()
    if raw == "INVOLVED":
        return "INVOLVED"
    if raw == "COMPLEX":
        return "COMPLEX"
    return _DEFAULT_COMPLEXITY_BAND


def _normalize_adaptive_target(value: object) -> SizeClass | None:
    if value is None:
        return None
    normalized = " ".join(str(value).strip().split()).upper()
    if not normalized:
        return None
    try:
        return SizeClass(normalized)
    except ValueError:
        return None


def _extract_adaptive_upscope(task: TaskCard, metadata: dict[str, object]) -> AdaptiveUpscopeEvidence | None:
    target = _normalize_adaptive_target(
        metadata.get("adaptive_upscope") or _extract_field_value(task.body, "Adaptive Upscope")
    )
    if target is None:
        return None
    raw_rule = metadata.get("adaptive_upscope_rule") or _extract_field_value(task.body, "Adaptive Upscope Rule")
    raw_stage = metadata.get("adaptive_upscope_stage") or _extract_field_value(task.body, "Adaptive Upscope Stage")
    raw_reason = metadata.get("adaptive_upscope_reason") or _extract_field_value(
        task.body,
        "Adaptive Upscope Reason",
    )
    return AdaptiveUpscopeEvidence.model_validate(
        {
            "target": target,
            "rule": str(raw_rule or _ADAPTIVE_UPSCOPE_LARGE_RULE),
            "stage": str(raw_stage or "Resume"),
            "reason": str(raw_reason or "explicit adaptive LARGE promotion is recorded on the active card"),
        }
    )


def _upsert_markdown_fields(body: str, fields: tuple[tuple[str, str], ...]) -> str:
    lines = body.splitlines()
    positions: dict[str, int] = {}
    for index, line in enumerate(lines):
        match = _FIELD_LINE_RE.match(line.strip())
        if match:
            positions[match.group(1).strip().casefold()] = index

    updated = list(lines)
    for label, value in fields:
        key = label.casefold()
        rendered = f"- **{label}:** {value}"
        if key in positions:
            updated[positions[key]] = rendered

    missing = [
        f"- **{label}:** {value}"
        for label, value in fields
        if label.casefold() not in positions
    ]
    if missing:
        insert_at = next((index for index, line in enumerate(updated) if line.strip()), len(updated))
        updated[insert_at:insert_at] = missing

    return "\n".join(updated).rstrip("\n")


def adaptive_upscope_task_card(
    task: TaskCard,
    *,
    target: SizeClass,
    rule: str,
    stage: str,
    reason: str,
) -> TaskCard:
    """Return one task card with an explicit adaptive upscope marker block."""

    body = _upsert_markdown_fields(
        task.body,
        (
            (_ADAPTIVE_UPSCOPE_FIELD.title(), target.value),
            (_ADAPTIVE_UPSCOPE_RULE_FIELD.title(), rule),
            (_ADAPTIVE_UPSCOPE_STAGE_FIELD.title(), stage),
            (_ADAPTIVE_UPSCOPE_REASON_FIELD.title(), reason),
        ),
    )
    payload = task.model_dump(mode="python")
    payload["body"] = body
    payload["raw_markdown"] = TaskCard.render_from_parts(task.heading, body)
    return TaskCard.model_validate(payload)


def evaluate_repo_size(root: Path, config: SizingConfig) -> RepoSizeEvidence:
    """Classify repo size from file and non-empty-line counts."""

    file_count = 0
    nonempty_line_count = 0

    for path in root.rglob("*"):
        if not path.is_file() or _is_skipped_repo_path(root, path):
            continue

        file_count += 1
        line_count = _read_nonempty_line_count(path)
        if line_count is None:
            continue
        nonempty_line_count += line_count

    threshold_hits: list[str] = []
    if file_count >= config.repo.file_count_threshold:
        threshold_hits.append("file_count")
    if nonempty_line_count >= config.repo.nonempty_line_count_threshold:
        threshold_hits.append("nonempty_line_count")

    return RepoSizeEvidence.model_validate(
        {
            "file_count": file_count,
            "nonempty_line_count": nonempty_line_count,
            "file_count_threshold": config.repo.file_count_threshold,
            "nonempty_line_count_threshold": config.repo.nonempty_line_count_threshold,
            "classified_as": SizeClass.LARGE if threshold_hits else SizeClass.SMALL,
            "threshold_hits": tuple(threshold_hits),
        }
    )


def evaluate_task_size(root: Path, task: TaskCard | None, config: SizingConfig) -> TaskSizeEvidence:
    """Classify task size from explicit files-to-touch signals and task complexity."""

    if task is None:
        return TaskSizeEvidence.model_validate(
            {
                "file_count": 0,
                "nonempty_line_count": 0,
                "file_count_threshold": config.task.file_count_threshold,
                "nonempty_line_count_threshold": config.task.nonempty_line_count_threshold,
                "complexity_band": _DEFAULT_COMPLEXITY_BAND,
                "file_count_source": "unavailable",
                "nonempty_line_count_source": "unavailable",
                "minimum_signal_count": _TASK_SIGNAL_MINIMUM,
                "qualifying_signal_count": 0,
                "classified_as": SizeClass.SMALL,
            }
        )

    metadata = _normalize_metadata_keys(task.metadata)
    explicit_files, explicit_file_source = _extract_explicit_files_to_touch(task)
    adaptive_upscope = _extract_adaptive_upscope(task, metadata)
    file_count = 0
    nonempty_line_count = 0
    files_to_touch: list[str] = []
    missing_files_to_touch: list[str] = []
    file_count_source = explicit_file_source
    nonempty_line_count_source = "unavailable"

    if explicit_files:
        file_count = len(explicit_files)
        file_count_source = explicit_file_source
        nonempty_line_count_source = "repo:files_to_touch"
        for raw_path in explicit_files:
            candidate = Path(raw_path)
            resolved = candidate if candidate.is_absolute() else (root / candidate)
            try:
                display_path = resolved.resolve().relative_to(root.resolve()).as_posix()
            except ValueError:
                display_path = candidate.as_posix()
                missing_files_to_touch.append(display_path)
                files_to_touch.append(display_path)
                continue
            files_to_touch.append(display_path)
            line_count = _read_nonempty_line_count(resolved)
            if line_count is None:
                missing_files_to_touch.append(display_path)
                continue
            nonempty_line_count += line_count
        if missing_files_to_touch:
            fallback_loc, fallback_source = _resolve_task_loc_fallback(task, metadata)
            if fallback_loc is not None and fallback_loc >= nonempty_line_count:
                nonempty_line_count = fallback_loc
                nonempty_line_count_source = fallback_source
    else:
        metadata_file_count = _extract_metadata_metric(metadata, _FILE_COUNT_METADATA_KEYS)
        if metadata_file_count is not None:
            file_count = metadata_file_count
            file_count_source = "metadata:file_count"
        else:
            body_file_count = _extract_body_metric(task.body, _FILE_COUNT_BODY_FIELDS)
            if body_file_count is not None:
                file_count = body_file_count
                file_count_source = "body:file_count"
            else:
                file_count_source = "unavailable"

        fallback_loc, fallback_source = _resolve_task_loc_fallback(task, metadata)
        if fallback_loc is not None:
            nonempty_line_count = fallback_loc
            nonempty_line_count_source = fallback_source
        else:
            nonempty_line_count_source = fallback_source

    complexity_band = _normalize_complexity_band(task.complexity)
    threshold_hits: list[str] = []
    if file_count >= config.task.file_count_threshold:
        threshold_hits.append("file_count")
    if nonempty_line_count >= config.task.nonempty_line_count_threshold:
        threshold_hits.append("nonempty_line_count")
    if complexity_band in {"INVOLVED", "COMPLEX"}:
        threshold_hits.append("complexity")
    if adaptive_upscope is not None and adaptive_upscope.target is SizeClass.LARGE:
        threshold_hits.append(_ADAPTIVE_UPSCOPE_THRESHOLD_HIT)

    qualifying_signal_count = len(
        [hit for hit in threshold_hits if hit != _ADAPTIVE_UPSCOPE_THRESHOLD_HIT]
    )
    classified_as = (
        SizeClass.LARGE
        if adaptive_upscope is not None and adaptive_upscope.target is SizeClass.LARGE
        else SizeClass.LARGE if qualifying_signal_count >= _TASK_SIGNAL_MINIMUM else SizeClass.SMALL
    )

    return TaskSizeEvidence.model_validate(
        {
            "active_task_id": task.task_id,
            "file_count": file_count,
            "nonempty_line_count": nonempty_line_count,
            "file_count_threshold": config.task.file_count_threshold,
            "nonempty_line_count_threshold": config.task.nonempty_line_count_threshold,
            "complexity": task.complexity,
            "complexity_band": complexity_band,
            "file_count_source": file_count_source,
            "nonempty_line_count_source": nonempty_line_count_source,
            "minimum_signal_count": _TASK_SIGNAL_MINIMUM,
            "qualifying_signal_count": qualifying_signal_count,
            "files_to_touch": tuple(files_to_touch),
            "missing_files_to_touch": tuple(missing_files_to_touch),
            "adaptive_upscope": adaptive_upscope,
            "classified_as": classified_as,
            "threshold_hits": tuple(threshold_hits),
        }
    )


def evaluate_size_policy(
    *,
    root: Path,
    task: TaskCard | None,
    config: SizingConfig,
    current_latch: SizeClass | None = None,
) -> SizeClassificationView:
    """Evaluate repo/task/hybrid size and resolve the durable latch outcome."""

    repo = evaluate_repo_size(root, config)
    task_evidence = evaluate_task_size(root, task, config)

    if config.mode == "repo":
        classified_as = repo.classified_as
        triggered_sources = ("repo",) if repo.classified_as is SizeClass.LARGE else ()
    elif config.mode == "task":
        classified_as = task_evidence.classified_as
        triggered_sources = ("task",) if task_evidence.classified_as is SizeClass.LARGE else ()
    else:
        triggered_sources = tuple(
            source
            for source, evidence in (("repo", repo), ("task", task_evidence))
            if evidence.classified_as is SizeClass.LARGE
        )
        classified_as = SizeClass.LARGE if triggered_sources else SizeClass.SMALL

    if current_latch is SizeClass.LARGE and classified_as is SizeClass.SMALL:
        latched_as = SizeClass.LARGE
        latch_reason = "retained_large_latch"
    elif classified_as is SizeClass.LARGE and current_latch is not SizeClass.LARGE:
        latched_as = SizeClass.LARGE
        latch_reason = "promoted_to_large"
    else:
        latched_as = classified_as
        latch_reason = "confirmed"

    return SizeClassificationView.model_validate(
        {
            "mode": config.mode,
            "classified_as": classified_as,
            "latched_as": latched_as,
            "latch_reason": latch_reason,
            "triggered_sources": triggered_sources,
            "repo": repo,
            "task": task_evidence,
        }
    )


def refresh_size_status(
    *,
    root: Path,
    task: TaskCard | None,
    config: SizingConfig,
    latch_path: Path,
) -> SizeClassificationView:
    """Evaluate the current size policy and persist the durable latch."""

    store = SizeStatusStore(latch_path)
    current = store.ensure()
    view = evaluate_size_policy(root=root, task=task, config=config, current_latch=current)
    if view.latched_as != current:
        store.write(view.latched_as)
    return view
