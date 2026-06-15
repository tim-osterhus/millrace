"""Runtime-owned append and rendering helpers for workspace history logs."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import timezone
from pathlib import Path
from typing import Literal

from pydantic import ValidationError

from millrace_ai.contracts.history_log import CanonicalHistoryEntry, ProposedHistoryEntry
from millrace_ai.contracts.stage_results import StageResultEnvelope
from millrace_ai.contracts.terminal_outcomes import terminal_outcome_value

from .paths import WorkspacePaths

HistoryAppendStatus = Literal["appended", "duplicate_skipped", "conflict_skipped", "invalid_skipped", "missing"]

_RUNTIME_AUTHORED_FIELDS = (
    "history_entry_id",
    "date",
    "occurred_at",
    "run_id",
    "request_id",
    "plane",
    "stage",
    "node_id",
    "work_item_kind",
    "work_item_id",
    "terminal_result",
)
@dataclass(frozen=True, slots=True)
class HistoryAppendResult:
    """Outcome from one history proposal application."""

    status: HistoryAppendStatus
    history_entry_id: str | None = None
    entry_path: Path | None = None
    rendered_paths: tuple[Path, ...] = ()
    diagnostic: str | None = None
    artifact_path: Path | None = None


def append_history_entry_for_stage_result(
    paths: WorkspacePaths,
    *,
    stage_result: StageResultEnvelope,
    stage_result_path: Path,
) -> HistoryAppendResult:
    """Validate, canonicalize, append, and render one run-local history proposal."""

    artifact_path = _find_history_artifact(stage_result=stage_result, stage_result_path=stage_result_path)
    if artifact_path is None:
        return HistoryAppendResult(status="missing")

    try:
        proposed_payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return HistoryAppendResult(status="invalid_skipped", artifact_path=artifact_path, diagnostic=str(exc))
    try:
        proposed = ProposedHistoryEntry.model_validate(proposed_payload)
    except ValidationError as exc:
        return HistoryAppendResult(status="invalid_skipped", artifact_path=artifact_path, diagnostic=str(exc))

    request_id = stage_result_path.stem.strip()
    if not request_id:
        return HistoryAppendResult(
            status="invalid_skipped",
            artifact_path=artifact_path,
            diagnostic="stage result path does not provide request_id",
        )
    try:
        canonical = canonical_history_entry_from_stage_result(
            paths,
            proposed,
            stage_result=stage_result,
            request_id=request_id,
        )
    except ValueError as exc:
        return HistoryAppendResult(status="invalid_skipped", artifact_path=artifact_path, diagnostic=str(exc))

    target_path = paths.history_log_entries_dir / f"{canonical.date}.jsonl"
    canonical_line = _canonical_json(canonical)
    existing_status = _existing_entry_status(target_path, canonical.history_entry_id, canonical_line)
    if existing_status == "duplicate_skipped":
        return HistoryAppendResult(
            status="duplicate_skipped",
            history_entry_id=canonical.history_entry_id,
            entry_path=target_path,
            artifact_path=artifact_path,
        )
    if existing_status == "conflict_skipped":
        return HistoryAppendResult(
            status="conflict_skipped",
            history_entry_id=canonical.history_entry_id,
            entry_path=target_path,
            artifact_path=artifact_path,
            diagnostic="same history_entry_id already exists with different canonical payload",
        )

    target_path.parent.mkdir(parents=True, exist_ok=True)
    existing_text = target_path.read_text(encoding="utf-8") if target_path.exists() else ""
    next_text = existing_text
    if next_text and not next_text.endswith("\n"):
        next_text += "\n"
    next_text += canonical_line + "\n"
    _replace_text(target_path, next_text)
    rendered = render_history_log(paths)
    return HistoryAppendResult(
        status="appended",
        history_entry_id=canonical.history_entry_id,
        entry_path=target_path,
        rendered_paths=rendered,
        artifact_path=artifact_path,
    )


def canonical_history_entry_from_stage_result(
    paths: WorkspacePaths,
    proposed: ProposedHistoryEntry,
    *,
    stage_result: StageResultEnvelope,
    request_id: str,
) -> CanonicalHistoryEntry:
    """Build the runtime-authoritative history payload.

    ``history_entry_id`` is deterministic from runtime identity:
    ``history:<run_id>:<request_id>:<plane>:<node_id>:<terminal_result>``.
    """

    completed_at = stage_result.completed_at
    if completed_at.tzinfo is None or completed_at.utcoffset() is None:
        raise ValueError("stage_result completed_at must be timezone-aware")
    occurred_at = completed_at.astimezone(timezone.utc)
    date = occurred_at.date().isoformat()
    terminal_result = terminal_outcome_value(stage_result.terminal_result)
    runtime_fields = {
        "history_entry_id": (
            f"history:{stage_result.run_id}:{request_id}:{stage_result.plane.value}:"
            f"{stage_result.node_id}:{terminal_result}"
        ),
        "date": date,
        "occurred_at": occurred_at,
        "run_id": stage_result.run_id,
        "request_id": request_id,
        "plane": stage_result.plane.value,
        "stage": stage_result.stage.value,
        "node_id": stage_result.node_id,
        "work_item_kind": stage_result.work_item_kind.value if stage_result.work_item_kind is not None else "",
        "work_item_id": stage_result.work_item_id,
        "terminal_result": terminal_result,
    }
    for field_name in _RUNTIME_AUTHORED_FIELDS:
        supplied = getattr(proposed, field_name)
        if supplied is None:
            continue
        expected = runtime_fields[field_name]
        supplied_value = supplied.astimezone(timezone.utc) if field_name == "occurred_at" else supplied
        if supplied_value != expected:
            raise ValueError(f"history_entry.json conflicts with runtime field {field_name}")

    changed_paths = _validate_relative_paths(paths, proposed.changed_paths, field_name="changed_paths")
    evidence_paths = _validate_relative_paths(paths, proposed.evidence_paths, field_name="evidence_paths")
    summary = proposed.summary.strip()
    if not summary:
        raise ValueError("history_entry.json summary is empty")

    return CanonicalHistoryEntry(
        **runtime_fields,
        summary=summary,
        changed_paths=changed_paths,
        evidence_paths=evidence_paths,
        warnings=tuple(warning.strip() for warning in proposed.warnings if warning.strip()),
    )


def render_history_log(paths: WorkspacePaths) -> tuple[Path, ...]:
    """Render daily, latest, and index markdown from canonical JSONL entries."""

    entries = _load_all_entries(paths)
    entries_by_date: dict[str, list[CanonicalHistoryEntry]] = {}
    for entry in entries:
        entries_by_date.setdefault(entry.date, []).append(entry)

    rendered_paths: list[Path] = []
    paths.history_log_daily_dir.mkdir(parents=True, exist_ok=True)
    for date, day_entries in sorted(entries_by_date.items()):
        day_path = paths.history_log_daily_dir / f"{date}.md"
        _replace_text(day_path, _render_daily(date, day_entries))
        rendered_paths.append(day_path)

    latest_text = _render_latest(entries)
    _replace_text(paths.history_log_latest_file, latest_text)
    rendered_paths.append(paths.history_log_latest_file)

    index_text = _render_index(entries_by_date)
    _replace_text(paths.history_log_index_file, index_text)
    rendered_paths.append(paths.history_log_index_file)
    return tuple(rendered_paths)


def _find_history_artifact(
    stage_result: StageResultEnvelope,
    stage_result_path: Path,
) -> Path | None:
    run_dir = stage_result_path.resolve().parents[1]
    claimed_path = _claimed_history_artifact_path(stage_result_path)
    default_path = run_dir / "history_entry.json"
    if default_path.exists():
        # Claim the root proposal into a request-specific artifact before reading it.
        claimed_path.parent.mkdir(parents=True, exist_ok=True)
        default_path.replace(claimed_path)
        return claimed_path
    if claimed_path.exists():
        return claimed_path
    return None


def _claimed_history_artifact_path(stage_result_path: Path) -> Path:
    return stage_result_path.with_name(f"{stage_result_path.stem}.history_entry.json")


def _validate_relative_paths(paths: WorkspacePaths, values: tuple[str, ...], *, field_name: str) -> tuple[str, ...]:
    normalized: list[str] = []
    for value in values:
        candidate = value.strip()
        if not candidate:
            continue
        candidate_path = Path(candidate)
        if candidate_path.is_absolute():
            try:
                candidate = candidate_path.resolve().relative_to(paths.root).as_posix()
            except ValueError as exc:
                raise ValueError(f"{field_name} path escapes workspace: {value}") from exc
        elif ".." in candidate_path.parts:
            raise ValueError(f"{field_name} path must not contain '..': {value}")
        normalized.append(candidate)
    return tuple(normalized)


def _existing_entry_status(
    target_path: Path,
    history_entry_id: str,
    canonical_line: str,
) -> Literal["new", "duplicate_skipped", "conflict_skipped"]:
    if not target_path.exists():
        return "new"
    with target_path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            payload = json.loads(line)
            if payload.get("history_entry_id") != history_entry_id:
                continue
            return "duplicate_skipped" if _canonical_json(payload) == canonical_line else "conflict_skipped"
    return "new"


def _load_all_entries(paths: WorkspacePaths) -> tuple[CanonicalHistoryEntry, ...]:
    loaded: list[CanonicalHistoryEntry] = []
    if not paths.history_log_entries_dir.exists():
        return ()
    for entry_path in sorted(paths.history_log_entries_dir.glob("*.jsonl")):
        with entry_path.open(encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    loaded.append(CanonicalHistoryEntry.model_validate_json(line))
    return tuple(sorted(loaded, key=lambda entry: (entry.occurred_at, entry.history_entry_id), reverse=True))


def _render_daily(date: str, entries: list[CanonicalHistoryEntry]) -> str:
    lines = [f"# History Log - {date}", ""]
    for entry in sorted(entries, key=lambda item: (item.occurred_at, item.history_entry_id), reverse=True):
        lines.extend(_render_entry(entry))
    return "\n".join(lines).rstrip() + "\n"


def _render_latest(entries: tuple[CanonicalHistoryEntry, ...]) -> str:
    if not entries:
        return "# Latest History\n\nNo rendered history entries yet.\n"
    lines = ["# Latest History", ""]
    for entry in entries[:20]:
        lines.extend(_render_entry(entry))
    return "\n".join(lines).rstrip() + "\n"


def _render_index(entries_by_date: dict[str, list[CanonicalHistoryEntry]]) -> str:
    lines = ["# History Log", "", "Runtime-owned workspace history surfaces.", ""]
    if not entries_by_date:
        lines.append("No rendered history entries yet.")
    else:
        for date in sorted(entries_by_date, reverse=True):
            count = len(entries_by_date[date])
            lines.append(f"- [{date}](daily/{date}.md) - {count} entr{'y' if count == 1 else 'ies'}")
    return "\n".join(lines).rstrip() + "\n"


def _render_entry(entry: CanonicalHistoryEntry) -> list[str]:
    lines = [
        f"## {entry.occurred_at.isoformat()} - {entry.stage} / {entry.terminal_result}",
        "",
        entry.summary,
        "",
        f"- Work item: `{entry.work_item_kind}:{entry.work_item_id}`",
        f"- Run: `{entry.run_id}` request `{entry.request_id}` node `{entry.node_id}`",
    ]
    if entry.changed_paths:
        lines.append(f"- Changed paths: {', '.join(f'`{path}`' for path in entry.changed_paths)}")
    if entry.evidence_paths:
        lines.append(f"- Evidence: {', '.join(f'`{path}`' for path in entry.evidence_paths)}")
    if entry.warnings:
        lines.append(f"- Warnings: {'; '.join(entry.warnings)}")
    lines.append("")
    return lines


def _canonical_json(value: CanonicalHistoryEntry | dict[str, object]) -> str:
    if isinstance(value, CanonicalHistoryEntry):
        payload = value.model_dump(mode="json")
    else:
        payload = value
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _replace_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.tmp")
    tmp_path.write_text(text, encoding="utf-8")
    tmp_path.replace(path)


__all__ = [
    "HistoryAppendResult",
    "append_history_entry_for_stage_result",
    "canonical_history_entry_from_stage_result",
    "render_history_log",
]
