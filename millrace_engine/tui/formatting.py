"""Shared formatting helpers for shaped TUI event views."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
import re
from typing import Any

from ..events import EventRecord, is_research_event_type, render_structured_event_line
from .models import (
    KeyValueView,
    RunIntegrationSummaryView,
    RunPolicyEvidenceView,
    RunSummaryView,
    RunTransitionView,
    RuntimeEventView,
)

_EVENT_CATEGORY_LABELS = {
    "adapter": "ADP",
    "control": "CTL",
    "engine": "ENG",
    "execution": "EXE",
    "research": "RSH",
}
_EPHEMERAL_TAIL_PARTS = {"T"}
_RUN_ID_KEYS = ("run_id", "pause_run_id", "parent_run_id", "active_run_id")
_SUMMARY_KEYS = (
    "stage",
    "command",
    "title",
    "task_id",
    "active_task_id",
    "reason",
    "decision",
    "status",
    "mode",
    "family",
    "audit_id",
    "item_key",
    "spec_id",
)


def format_timestamp(moment: datetime | None) -> str:
    """Render one UTC timestamp for panel text."""

    if moment is None:
        return "--"
    normalized = moment if moment.tzinfo is not None else moment.replace(tzinfo=timezone.utc)
    return normalized.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def format_short_timestamp(moment: datetime | None) -> str:
    """Render one concise UTC timestamp for dense event lists."""

    if moment is None:
        return "--:--:--"
    normalized = moment if moment.tzinfo is not None else moment.replace(tzinfo=timezone.utc)
    return normalized.astimezone(timezone.utc).strftime("%H:%M:%S")


def stringify_value(value: object) -> str:
    """Render common runtime payload values into compact strings."""

    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, datetime):
        moment = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
        return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(value, Path):
        return value.as_posix()
    if isinstance(value, Enum):
        return str(value.value)
    if isinstance(value, dict):
        parts = [f"{key}={stringify_value(item)}" for key, item in sorted(value.items(), key=lambda item: str(item[0]))]
        return ", ".join(part for part in parts if part)
    if isinstance(value, (list, tuple, set, frozenset)):
        return ", ".join(part for part in (stringify_value(item) for item in value) if part)
    return str(value)


def short_hash(value: str | None, *, prefix: int = 12) -> str:
    """Render one long hash into a stable short label."""

    normalized = " ".join((value or "").split())
    if not normalized:
        return "none"
    if len(normalized) <= prefix:
        return normalized
    return normalized[:prefix]


def _ellipsize_middle(value: str, *, max_length: int) -> str:
    normalized = " ".join(value.split())
    if len(normalized) <= max_length:
        return normalized
    if max_length <= 3:
        return normalized[:max_length]
    head = max(8, (max_length - 1) // 2)
    tail = max(8, max_length - head - 1)
    return f"{normalized[:head]}…{normalized[-tail:]}"


def _strip_leading_date_tokens(slug: str) -> str:
    tokens = [token for token in slug.split("-") if token]
    index = 0
    while index + 2 < len(tokens):
        year, month, day = tokens[index : index + 3]
        if not (len(year) == 4 and year.isdigit() and len(month) == 2 and month.isdigit() and len(day) == 2 and day.isdigit()):
            break
        index += 3
    trimmed = "-".join(tokens[index:])
    return trimmed or slug


def _compact_slug_label(slug: str, *, max_length: int) -> str:
    normalized = " ".join(slug.split())
    if len(normalized) <= max_length:
        return normalized
    tokens = [token for token in normalized.split("-") if token]
    if len(tokens) < 4:
        return _ellipsize_middle(normalized, max_length=max_length)
    left = tokens[:2]
    right = tokens[-2:]
    candidate = "-".join([*left, "…", *right])
    if len(candidate) > max_length:
        return _ellipsize_middle(normalized, max_length=max_length)
    right_start = len(tokens) - 3
    while right_start >= len(left):
        next_candidate = "-".join([*left, "…", *tokens[right_start:]])
        if len(next_candidate) > max_length:
            break
        candidate = next_candidate
        right_start -= 1
    return candidate


def _compact_run_timestamp(run_id_prefix: str) -> str | None:
    normalized = " ".join(run_id_prefix.split())
    if not normalized:
        return None
    matched = re.fullmatch(r"(\d{8})T(\d{2})(\d{2})(\d{2})(?:\d+)?Z", normalized)
    if matched is None:
        return None
    _, hour, minute, second = matched.groups()
    return f"{hour}:{minute}:{second}Z"


def compact_run_label(run_id: str | None, *, max_slug_length: int = 40) -> str:
    """Render one run id into a shorter operator-facing label."""

    normalized = " ".join((run_id or "").split())
    if not normalized:
        return "none"
    prefix, separator, slug = normalized.partition("__")
    if not separator:
        return _ellipsize_middle(normalized, max_length=max(24, max_slug_length))
    compact_slug = _strip_leading_date_tokens(slug)
    compact_slug = _compact_slug_label(compact_slug, max_length=max_slug_length)
    compact_time = _compact_run_timestamp(prefix)
    if compact_time and compact_slug:
        return f"{compact_slug} ({compact_time})"
    if compact_slug:
        return compact_slug
    if compact_time:
        return compact_time
    return _ellipsize_middle(normalized, max_length=max(24, max_slug_length))


def compact_display_path(path: Path, *, max_length: int = 48, minimum_tail_parts: int = 2) -> str:
    """Render one absolute path as a stable operator-facing label."""

    rendered = path.as_posix()
    if len(rendered) <= max_length or not path.is_absolute():
        return rendered
    visible_parts = [part for part in path.parts if part and part != path.anchor]
    if len(visible_parts) <= minimum_tail_parts:
        return rendered
    budget = max_length - len(".../")
    tail_parts: list[str] = []
    for part in reversed(visible_parts):
        candidate_parts = [part, *tail_parts]
        candidate = "/".join(candidate_parts)
        if tail_parts and len(candidate) > budget and len(candidate_parts) > minimum_tail_parts:
            break
        tail_parts = candidate_parts
    while len(tail_parts) > minimum_tail_parts and tail_parts[0] in _EPHEMERAL_TAIL_PARTS:
        tail_parts = tail_parts[1:]
    return f".../{'/'.join(tail_parts)}"


def detail_items(payload: dict[str, Any]) -> tuple[KeyValueView, ...]:
    """Render structured payload detail rows."""

    items: list[KeyValueView] = []
    for key, value in sorted(payload.items(), key=lambda item: str(item[0])):
        rendered = stringify_value(value)
        if not rendered:
            continue
        items.append(KeyValueView(key=str(key), value=rendered))
    return tuple(items)


def event_category_label(source: str) -> str:
    """Return a short stable label for one event source."""

    return _EVENT_CATEGORY_LABELS.get(source, source[:3].upper())


def event_run_id(payload: dict[str, Any]) -> str | None:
    """Extract the most relevant run id from one payload when available."""

    for key in _RUN_ID_KEYS:
        rendered = stringify_value(payload.get(key))
        if rendered:
            return rendered
    return None


def event_summary(event_type: str, payload: dict[str, Any]) -> str:
    """Render a concise human-readable event summary from structured payload data."""

    fragments: list[str] = []
    seen: set[str] = set()
    for key in _SUMMARY_KEYS:
        rendered = stringify_value(payload.get(key))
        if not rendered:
            continue
        fragment = rendered if key in {"title", "reason"} else f"{key}={rendered}"
        if fragment in seen:
            continue
        fragments.append(fragment)
        seen.add(fragment)
        if len(fragments) == 3:
            break
    if not fragments:
        for detail in detail_items(payload)[:2]:
            fragment = f"{detail.key}={detail.value}"
            if fragment in seen:
                continue
            fragments.append(fragment)
            if len(fragments) == 2:
                break
    if not fragments:
        return event_type
    return " | ".join(fragments)


def runtime_event_view(record: EventRecord) -> RuntimeEventView:
    """Convert one runtime event record into the shaped TUI view model."""

    return RuntimeEventView(
        event_type=record.type.value,
        source=record.source.value,
        timestamp=record.timestamp,
        is_research_event=is_research_event_type(record.type),
        payload=detail_items(record.payload),
        category=event_category_label(record.source.value),
        summary=event_summary(record.type.value, record.payload),
        run_id=event_run_id(record.payload),
    )


def render_runtime_event_debug_line(event: RuntimeEventView) -> str:
    """Render one TUI event with the same raw shape as the CLI log feed."""

    payload = {item.key: item.value for item in event.payload}
    return render_structured_event_line(
        timestamp=event.timestamp,
        event_type=event.event_type,
        source=event.source,
        payload=payload,
    )


def run_transition_label(transition: RunTransitionView) -> str:
    """Render one transition into a concise label."""

    fragments = [transition.node_id]
    if transition.kind_id and transition.kind_id != transition.node_id:
        fragments.append(transition.kind_id)
    if transition.outcome:
        fragments.append(transition.outcome)
    elif transition.event_name:
        fragments.append(transition.event_name)
    return " ".join(fragment for fragment in fragments if fragment)


def run_summary_lines(run: RunSummaryView) -> tuple[str, str]:
    """Render the two-line compact runs-panel summary for one run."""

    header_fragments = [run.run_id]
    if run.selection_ref:
        header_fragments.append(run.selection_ref)
    if run.compiled_at is not None:
        header_fragments.append(format_timestamp(run.compiled_at))
    if run.frozen_plan_hash:
        header_fragments.append(f"plan {short_hash(run.frozen_plan_hash)}")
    if run.stage_count is not None:
        header_fragments.append(f"{run.stage_count} stages")

    detail_fragments = [f"transitions {run.transition_count}"]
    if run.latest_transition_label:
        detail_fragments.append(run.latest_transition_label)
    if run.latest_status:
        detail_fragments.append(run.latest_status)
    if run.latest_policy_decision:
        detail_fragments.append(f"policy {run.latest_policy_decision}")
    if run.integration_target:
        integration_state = "on" if run.integration_enabled else "off"
        detail_fragments.append(f"integration {run.integration_target} ({integration_state})")
    if run.routing_modes:
        detail_fragments.append(f"routes {', '.join(run.routing_modes)}")
    if run.issue:
        detail_fragments.append(f"issue {run.issue}")
    elif run.note:
        detail_fragments.append(run.note)
    return " | ".join(header_fragments), " | ".join(detail_fragments)


def run_outcome_label(run: RunSummaryView) -> str:
    """Return one compact outcome tag for operator run rows."""

    if run.issue:
        return "FAIL"
    status_text = " ".join((run.latest_status or "").upper().split())
    if any(token in status_text for token in ("FAIL", "ERROR", "BLOCK", "NEEDED")):
        return "FAIL"
    if any(token in status_text for token in ("ACCEPT", "COMPLETE", "SUCCESS", "PASS")):
        return "OK"
    if run.note:
        return "WARN"
    return "INFO"


def _compact_selection_ref(selection_ref: str | None) -> str | None:
    normalized = " ".join((selection_ref or "").split())
    if not normalized:
        return None
    ref = normalized.split(":", maxsplit=1)[1] if ":" in normalized else normalized
    ref = ref.split("@", maxsplit=1)[0]
    if ref == "mode.standard":
        ref = "mode.std"
    return ref


def run_operator_summary_lines(run: RunSummaryView) -> tuple[str, str]:
    """Render compact operator-mode header/detail lines for one run row."""

    header_fragments = [run_outcome_label(run), compact_run_label(run.run_id)]
    detail_fragments: list[str] = []
    compact_ref = _compact_selection_ref(run.selection_ref)
    if compact_ref:
        detail_fragments.append(f"sel {compact_ref}")
    if run.stage_count is not None:
        detail_fragments.append(f"stg {run.stage_count}")
    if run.latest_status:
        detail_fragments.append(f"status {' '.join(run.latest_status.lower().split())}")
    elif run.latest_transition_label:
        detail_fragments.append(run.latest_transition_label)
    if run.latest_transition_at is not None:
        detail_fragments.append(format_short_timestamp(run.latest_transition_at))
    if run.transition_count:
        detail_fragments.append(f"tr {run.transition_count}")
    return " ".join(header_fragments), " | ".join(detail_fragments) or "no transition detail"


def run_operator_alert(run: RunSummaryView) -> str | None:
    """Return one short inline operator alert for run issues or notable warnings."""

    if run.issue:
        return run.issue
    if run_outcome_label(run) == "FAIL" and run.note:
        return run.note
    if run.note and run_outcome_label(run) == "WARN":
        return run.note
    return None


def run_transition_summary_lines(
    transitions: tuple[RunTransitionView, ...],
    *,
    limit: int = 5,
) -> tuple[str, ...]:
    """Render a concise transition-history excerpt."""

    if not transitions:
        return ("- no runtime transitions recorded",)
    selected = transitions[-limit:]
    lines = []
    for transition in selected:
        fragments = [
            format_short_timestamp(transition.timestamp),
            run_transition_label(transition),
        ]
        if transition.status_after:
            fragments.append(f"status {transition.status_after}")
        elif transition.status_before:
            fragments.append(f"from {transition.status_before}")
        if transition.queue_mutations_applied:
            fragments.append(f"queue {', '.join(transition.queue_mutations_applied)}")
        if transition.artifacts_emitted:
            fragments.append(f"artifacts {len(transition.artifacts_emitted)}")
        lines.append(f"- {' | '.join(fragments)}")
    if len(transitions) > len(selected):
        lines.append(f"- showing latest {len(selected)} of {len(transitions)} transitions")
    return tuple(lines)


def run_policy_summary_lines(
    policy: RunPolicyEvidenceView | None,
    *,
    hook_count: int,
    latest_decision: str | None,
) -> tuple[str, ...]:
    """Render concise run-policy evidence lines."""

    if policy is None:
        decision = latest_decision or "none"
        return (f"POLICY  records {hook_count} | latest {decision}",)
    lines = [
        "POLICY  "
        f"records {hook_count}"
        f" | latest {policy.decision}"
        f" | hook {policy.hook}"
        f" | evaluator {policy.evaluator}"
    ]
    detail_fragments = [
        f"at {format_timestamp(policy.timestamp)}",
        f"event {policy.event_name}",
        f"node {policy.node_id}",
    ]
    if policy.routing_mode:
        detail_fragments.append(f"route {policy.routing_mode}")
    lines.append(f"EVIDENCE {' | '.join(detail_fragments)}")
    for note in policy.notes[:2]:
        lines.append(f"- {note}")
    for summary in policy.evidence_summaries[:3]:
        lines.append(f"- {summary}")
    return tuple(lines)


def run_integration_summary_lines(integration: RunIntegrationSummaryView | None) -> tuple[str, ...]:
    """Render concise execution-integration summary lines."""

    if integration is None:
        return ("INTEGRATION unavailable",)
    lines = [
        "INTEGRATION "
        f"mode {integration.effective_mode}"
        f" | target {integration.builder_success_target}"
        f" | run {'yes' if integration.should_run_integration else 'no'}"
    ]
    detail_fragments = []
    if integration.task_gate_required:
        detail_fragments.append("task gate required")
    if integration.task_integration_preference:
        detail_fragments.append(f"task preference {integration.task_integration_preference}")
    if integration.effective_sequence:
        detail_fragments.append(f"path {', '.join(integration.effective_sequence)}")
    if detail_fragments:
        lines.append(f"DETAIL  {' | '.join(detail_fragments)}")
    lines.append(f"REASON  {integration.reason}")
    return tuple(lines)


__all__ = [
    "compact_display_path",
    "compact_run_label",
    "detail_items",
    "event_category_label",
    "event_run_id",
    "event_summary",
    "format_short_timestamp",
    "format_timestamp",
    "run_integration_summary_lines",
    "run_policy_summary_lines",
    "run_summary_lines",
    "run_transition_label",
    "run_transition_summary_lines",
    "short_hash",
    "runtime_event_view",
    "stringify_value",
]
