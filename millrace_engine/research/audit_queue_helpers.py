"""Queue-document helpers for the research audit loop."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from ..markdown import write_text_atomic
from ..paths import RuntimePaths
from .audit_models import AuditLifecycleStatus, AuditQueueRecord, AuditTrigger, _normalize_required_text
from .parser_helpers import _extract_heading_title, _parse_frontmatter_block


_SCOPE_QUEUE_EMPTY = "orchestration-loop-backlog-empty-handoff"


def parse_audit_queue_record(
    text: str,
    *,
    source_path: Path,
    expected_status: AuditLifecycleStatus | None = None,
) -> AuditQueueRecord:
    """Validate one audit markdown document."""

    frontmatter, body = _parse_frontmatter_block(text)
    status = frontmatter.get("status") or source_path.parent.name
    audit_id = frontmatter.get("audit_id") or source_path.stem
    title = _extract_heading_title(body, normalize=lambda value: _normalize_required_text(value, field_name="title")) or f"Audit {audit_id}"
    record = AuditQueueRecord.model_validate(
        {
            "source_path": source_path,
            "audit_id": audit_id,
            "title": title,
            "scope": frontmatter.get("scope") or "unscoped-audit",
            "trigger": frontmatter.get("trigger") or AuditTrigger.OTHER.value,
            "lifecycle_status": status,
            "owner": frontmatter.get("owner"),
            "created_at": frontmatter.get("created_at"),
            "updated_at": frontmatter.get("updated_at"),
        }
    )
    if expected_status is not None and record.lifecycle_status is not expected_status:
        raise ValueError(
            f"audit record status {record.lifecycle_status.value} does not match queue root {expected_status.value}"
        )
    return record


def load_audit_queue_record(
    path: Path,
    *,
    expected_status: AuditLifecycleStatus | None = None,
) -> AuditQueueRecord:
    """Read and validate one audit queue document from disk."""

    return parse_audit_queue_record(
        path.read_text(encoding="utf-8"),
        source_path=path,
        expected_status=expected_status,
    )


def ensure_backlog_empty_audit_ticket(
    paths: RuntimePaths,
    *,
    observed_at: datetime,
    backlog_depth: int = 0,
) -> AuditQueueRecord:
    """Materialize one actionable audit ticket for backlog-empty handoff."""

    incoming_dir = paths.agents_dir / "ideas" / "audit" / "incoming"
    working_dir = paths.agents_dir / "ideas" / "audit" / "working"
    for queue_dir, status in (
        (incoming_dir, AuditLifecycleStatus.INCOMING),
        (working_dir, AuditLifecycleStatus.WORKING),
    ):
        if not queue_dir.is_dir():
            continue
        for path in sorted(queue_dir.glob("*.md")):
            try:
                record = load_audit_queue_record(path, expected_status=status)
            except ValueError:
                continue
            if record.trigger is AuditTrigger.QUEUE_EMPTY and record.scope == _SCOPE_QUEUE_EMPTY:
                return record

    incoming_dir.mkdir(parents=True, exist_ok=True)
    timestamp = observed_at.strftime("%Y%m%dT%H%M%SZ")
    audit_id = f"AUD-BACKLOG-EMPTY-{timestamp}"
    path = incoming_dir / f"{audit_id}.md"
    suffix = 2
    while path.exists():
        path = incoming_dir / f"{audit_id}__{suffix}.md"
        suffix += 1

    observed_iso = observed_at.isoformat().replace("+00:00", "Z")
    write_text_atomic(
        path,
        "\n".join(
            [
                "---",
                f"audit_id: {path.stem}",
                f"scope: {_SCOPE_QUEUE_EMPTY}",
                f"trigger: {AuditTrigger.QUEUE_EMPTY.value}",
                f"status: {AuditLifecycleStatus.INCOMING.value}",
                "owner: research-plane",
                f"created_at: {observed_iso}",
                f"updated_at: {observed_iso}",
                "---",
                "",
                f"# Audit {path.stem}",
                "",
                "## Objective",
                "- Validate backlog-empty completion conditions through the audit queue.",
                "",
                "## Inputs",
                "- `agents/tasks.md`",
                "- `agents/tasksbacklog.md`",
                "- `agents/taskspending.md`",
                "",
                "## Checks",
                "- Confirm the backlog transition is being treated as audit work, not success.",
                "- Preserve a durable queue item for later audit validation/gate stages.",
                "",
                "## Findings",
                f"- Backlog-empty event observed with backlog_depth={backlog_depth}.",
                "",
                "## Evidence",
                "- Research queue discovery should report this file as actionable audit work.",
                "",
                "## Decision",
                "- Pending",
                "",
                "## Follow-ups",
                "- Run the later audit validation and gatekeeper stages.",
                "",
            ]
        ),
    )
    return load_audit_queue_record(path, expected_status=AuditLifecycleStatus.INCOMING)


__all__ = [
    "ensure_backlog_empty_audit_ticket",
    "load_audit_queue_record",
    "parse_audit_queue_record",
]
