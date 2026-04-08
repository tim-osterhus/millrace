"""Typed blocker queue contracts and markdown ledger validators."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from pydantic import field_validator

from ..contracts import ContractModel, ExecutionStatus, _normalize_datetime, _normalize_path

_HEADING_RE = re.compile(
    r"^##\s*(?P<occurred_at>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} UTC)\s*[—-]\s*(?P<title>.+?)\s*$",
    flags=re.MULTILINE,
)
_FIELD_RE = re.compile(r"^\s*(?:[-*]\s*)?\*\*(?P<name>.+?):\*\*\s*(?P<value>.*)$")
_EVIDENCE_FIELD_RE = re.compile(r"^\s*[-*]\s*(?P<name>[^:]+):\s*(?P<value>.*)$")
_TOKEN_RE = re.compile(r"[^a-z0-9]+")
_WHITESPACE_RE = re.compile(r"\s+")


def _normalize_required_text(value: str, *, field_name: str) -> str:
    normalized = _WHITESPACE_RE.sub(" ", value.strip())
    if not normalized:
        raise ValueError(f"{field_name} may not be empty")
    return normalized


def _normalize_optional_text(value: str | None, *, field_name: str) -> str | None:
    if value is None:
        return None
    normalized = _WHITESPACE_RE.sub(" ", value.strip())
    if not normalized:
        return None
    return normalized


def _slugify(value: str) -> str:
    slug = _TOKEN_RE.sub("-", value.strip().lower()).strip("-")
    return slug or "item"


def _strip_ticks(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if normalized.startswith("`") and normalized.endswith("`") and len(normalized) >= 2:
        return normalized[1:-1].strip()
    return normalized


def _extract_field_value(block: str, field_name: str) -> str | None:
    target = field_name.casefold()
    for raw_line in block.splitlines():
        match = _FIELD_RE.match(raw_line.strip())
        if match is None:
            continue
        if match.group("name").strip().casefold() != target:
            continue
        value = match.group("value").strip()
        return value or None
    return None


def _extract_evidence_value(block: str, field_name: str) -> str | None:
    in_evidence = False
    target = field_name.casefold()
    for raw_line in block.splitlines():
        stripped = raw_line.rstrip()
        field_match = _FIELD_RE.match(stripped.strip())
        if field_match is not None:
            name = field_match.group("name").strip().casefold()
            if name == "evidence":
                in_evidence = True
                continue
            if in_evidence:
                break
        if not in_evidence:
            continue
        evidence_match = _EVIDENCE_FIELD_RE.match(stripped.strip())
        if evidence_match is None:
            if stripped.strip():
                continue
            continue
        if evidence_match.group("name").strip().casefold() != target:
            continue
        value = evidence_match.group("value").strip()
        return value or None
    return None


def _parse_status(value: str | None) -> ExecutionStatus | None:
    normalized = _strip_ticks(value)
    if normalized is None:
        return None
    marker = normalized.strip()
    if marker.startswith("### "):
        marker = marker[4:]
    marker = marker.strip().upper()
    if not marker:
        return None
    return ExecutionStatus(marker)


def _parse_path(value: str | None) -> Path | None:
    normalized = _strip_ticks(value)
    if normalized is None:
        return None
    candidate = normalized.strip()
    if not candidate or candidate.casefold() == "n/a":
        return None
    return Path(candidate)


class BlockerQueueRecord(ContractModel):
    """Validated blocker record parsed from the markdown ledger."""

    ledger_path: Path
    item_key: str
    occurred_at: datetime
    task_title: str
    status: ExecutionStatus | None = None
    stage_blocked: str | None = None
    source_task: str | None = None
    prompt_artifact: Path | None = None
    run_dir: Path | None = None
    diagnostics_dir: Path | None = None
    root_cause_summary: str | None = None
    next_action: str | None = None
    incident_path: Path | None = None
    notes: str | None = None

    @field_validator("ledger_path", "prompt_artifact", "run_dir", "diagnostics_dir", "incident_path", mode="before")
    @classmethod
    def normalize_paths(cls, value: str | Path | None, info: object) -> Path | None:
        normalized = _normalize_path(value)
        field_name = getattr(info, "field_name", "path")
        if field_name == "ledger_path" and normalized is None:
            raise ValueError("ledger_path may not be empty")
        return normalized

    @field_validator("occurred_at", mode="before")
    @classmethod
    def normalize_occurred_at(cls, value: datetime | str) -> datetime:
        return _normalize_datetime(value)

    @field_validator("item_key", "task_title")
    @classmethod
    def normalize_required_text_fields(cls, value: str, info: object) -> str:
        field_name = getattr(info, "field_name", "text")
        return _normalize_required_text(value, field_name=field_name)

    @field_validator("stage_blocked", "source_task", "root_cause_summary", "next_action", "notes")
    @classmethod
    def normalize_optional_text_fields(cls, value: str | None, info: object) -> str | None:
        field_name = getattr(info, "field_name", "text")
        return _normalize_optional_text(value, field_name=field_name)


def parse_blocker_record(block: str, *, ledger_path: Path, contract_path: Path) -> BlockerQueueRecord:
    """Validate one blocker ledger entry block."""

    heading = _HEADING_RE.search(block)
    if heading is None:
        raise ValueError("blocker entry is missing a valid heading")

    occurred_at = datetime.strptime(heading.group("occurred_at"), "%Y-%m-%d %H:%M:%S UTC").replace(
        tzinfo=timezone.utc
    )
    title = _normalize_required_text(heading.group("title"), field_name="task_title")
    item_key = (
        f"{contract_path.as_posix()}#"
        f"{occurred_at.strftime('%Y%m%dT%H%M%SZ')}__{_slugify(title)}"
    )

    return BlockerQueueRecord.model_validate(
        {
            "ledger_path": ledger_path,
            "item_key": item_key,
            "occurred_at": occurred_at,
            "task_title": title,
            "status": _parse_status(_extract_field_value(block, "Status")),
            "stage_blocked": _extract_field_value(block, "Stage blocked"),
            "source_task": _extract_field_value(block, "Source task card")
            or _extract_field_value(block, "Source task"),
            "prompt_artifact": _parse_path(_extract_field_value(block, "Prompt artifact")),
            "run_dir": _parse_path(_extract_evidence_value(block, "Runs")),
            "diagnostics_dir": _parse_path(_extract_evidence_value(block, "Diagnostics")),
            "root_cause_summary": _extract_field_value(block, "Root-cause summary"),
            "next_action": _extract_field_value(block, "Deterministic next action")
            or _extract_field_value(block, "Next action"),
            "incident_path": _parse_path(_extract_field_value(block, "Incident intake")),
            "notes": _extract_field_value(block, "Notes"),
        }
    )


def load_blocker_records(ledger_path: Path, *, contract_path: Path) -> tuple[BlockerQueueRecord, ...]:
    """Read and validate all blocker ledger entries in one file."""

    text = ledger_path.read_text(encoding="utf-8")
    matches = list(_HEADING_RE.finditer(text))
    records: list[BlockerQueueRecord] = []
    for index, match in enumerate(matches):
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        records.append(parse_blocker_record(text[start:end].strip(), ledger_path=ledger_path, contract_path=contract_path))
    return tuple(records)


__all__ = [
    "BlockerQueueRecord",
    "load_blocker_records",
    "parse_blocker_record",
]
