"""Recon packet markdown parsing and rendering helpers."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from millrace_ai.contracts import ReconPacketDocument, ReconPathFinding, ReconVerificationPlan

_TITLE_PATTERN = re.compile(r"^#\s+(?P<title>.+?)\s*$")
_FIELD_PATTERN = re.compile(r"^(?P<label>[A-Za-z][A-Za-z0-9-]*):(?:\s*(?P<value>.*))?$")
_LIST_ITEM_PATTERN = re.compile(r"^-\s+(?P<value>.+?)\s*$")

_SCALAR_FIELDS: dict[str, str] = {
    "Recon-Packet-ID": "recon_packet_id",
    "Probe-ID": "probe_id",
    "Decision": "decision",
    "Confidence": "confidence",
    "Risk-Level": "risk_level",
    "Request-Summary": "request_summary",
    "Interpreted-Goal": "interpreted_goal",
    "Handoff-Target": "handoff_target",
    "Emitted-Task-ID": "emitted_task_id",
    "Emitted-Spec-ID": "emitted_spec_id",
    "Created-At": "created_at",
    "Created-By": "created_by",
}
_LIST_FIELDS: dict[str, str] = {
    "Relevant-Paths": "relevant_paths",
    "Relevant-Symbols": "relevant_symbols",
    "Relevant-Tests": "relevant_tests",
    "Semantic-Invariants": "semantic_invariants",
    "Edge-Cases-To-Preserve": "edge_cases_to_preserve",
    "Required-Commands": "required_commands",
    "Focused-Checks": "focused_checks",
    "Fallback-Checks": "fallback_checks",
    "Open-Questions": "open_questions",
}
_ALL_FIELDS = {**_SCALAR_FIELDS, **_LIST_FIELDS}


def render_recon_packet(packet: ReconPacketDocument) -> str:
    """Render a canonical operator-facing recon packet."""

    payload = packet.model_dump(mode="json")
    lines = [f"# Recon Packet {packet.recon_packet_id}", ""]
    for label, field_name in _SCALAR_FIELDS.items():
        value = payload.get(field_name)
        if value is None:
            continue
        lines.append(f"{label}: {value}")

    _append_path_findings(lines, "Relevant-Paths", packet.relevant_paths)
    _append_strings(lines, "Relevant-Symbols", packet.relevant_symbols)
    _append_path_findings(lines, "Relevant-Tests", packet.relevant_tests)
    _append_strings(lines, "Semantic-Invariants", packet.semantic_invariants)
    _append_strings(lines, "Edge-Cases-To-Preserve", packet.edge_cases_to_preserve)
    _append_strings(lines, "Required-Commands", packet.verification_plan.required_commands)
    _append_strings(lines, "Focused-Checks", packet.verification_plan.focused_checks)
    _append_strings(lines, "Fallback-Checks", packet.verification_plan.fallback_checks)
    _append_strings(lines, "Open-Questions", packet.open_questions)
    return "\n".join(lines).rstrip() + "\n"


def parse_recon_packet(raw: str, *, path: Path | None = None) -> ReconPacketDocument:
    """Parse one canonical recon packet markdown artifact."""

    payload = _parse_fields(raw, path=path)
    verification_plan = ReconVerificationPlan(
        required_commands=tuple(payload.pop("required_commands", ())),
        focused_checks=tuple(payload.pop("focused_checks", ())),
        fallback_checks=tuple(payload.pop("fallback_checks", ())),
    )
    payload["verification_plan"] = verification_plan
    return ReconPacketDocument.model_validate(payload)


def read_recon_packet(path: Path) -> ReconPacketDocument:
    """Read and parse one recon packet artifact."""

    return parse_recon_packet(path.read_text(encoding="utf-8"), path=path)


def _append_strings(lines: list[str], label: str, values: tuple[str, ...]) -> None:
    if not values:
        return
    lines.extend(("", f"{label}:"))
    lines.extend(f"- {value}" for value in values)


def _append_path_findings(
    lines: list[str],
    label: str,
    values: tuple[ReconPathFinding, ...],
) -> None:
    if not values:
        return
    lines.extend(("", f"{label}:"))
    lines.extend(f"- {value.path} | {value.reason}" for value in values)


def _parse_fields(raw: str, *, path: Path | None) -> dict[str, Any]:
    source_name = "<memory>" if path is None else path.name
    lines = raw.splitlines()
    if not lines:
        raise ValueError(f"recon packet {source_name} is empty")
    if _TITLE_PATTERN.match(lines[0].strip()) is None:
        raise ValueError(f"recon packet {source_name} must start with a markdown H1 title")

    payload: dict[str, Any] = {}
    index = 1
    while index < len(lines):
        stripped = lines[index].strip()
        index += 1
        if not stripped:
            continue
        field_match = _FIELD_PATTERN.match(stripped)
        if field_match is None:
            continue
        label = field_match.group("label")
        field_name = _ALL_FIELDS.get(label)
        inline_value = (field_match.group("value") or "").strip()
        if field_name is None:
            index = _skip_unknown_field_block(lines, index)
            continue
        if field_name in payload:
            raise ValueError(f"recon packet {source_name} repeats field `{label}`")
        if label in _SCALAR_FIELDS:
            if not inline_value:
                raise ValueError(f"recon packet {source_name} has empty scalar `{label}`")
            payload[field_name] = inline_value
            continue

        items: list[str] = []
        if inline_value:
            items.append(inline_value)
        while index < len(lines):
            next_line = lines[index].strip()
            if not next_line:
                index += 1
                if items:
                    break
                continue
            if _FIELD_PATTERN.match(next_line):
                break
            item_match = _LIST_ITEM_PATTERN.match(next_line)
            if item_match is None:
                raise ValueError(f"recon packet {source_name} has invalid list item under `{label}`")
            items.append(item_match.group("value").strip())
            index += 1
        payload[field_name] = _normalize_list(field_name, items)
    return payload


def _normalize_list(field_name: str, items: list[str]) -> tuple[Any, ...]:
    if field_name in {"relevant_paths", "relevant_tests"}:
        return tuple(_parse_path_finding(item) for item in items)
    return tuple(items)


def _parse_path_finding(value: str) -> ReconPathFinding:
    path, separator, reason = value.partition("|")
    if not separator:
        raise ValueError("recon path findings must use `path | reason`")
    return ReconPathFinding(path=path.strip(), reason=reason.strip())


def _skip_unknown_field_block(lines: list[str], index: int) -> int:
    while index < len(lines):
        candidate = lines[index].strip()
        if not candidate:
            index += 1
            continue
        if _FIELD_PATTERN.match(candidate):
            break
        index += 1
    return index


__all__ = ["parse_recon_packet", "read_recon_packet", "render_recon_packet"]
