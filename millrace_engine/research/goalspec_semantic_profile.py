"""Semantic profile extraction helpers for GoalSpec objective sync."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal
import json
import re

from pydantic import field_validator

from ..contracts import ContractModel
from ..paths import RuntimePaths
from .normalization_helpers import (
    _normalize_optional_text,
    _normalize_required_text,
    _normalize_text_sequence,
)


DEFAULT_SEMANTIC_SEED_FILENAMES = (
    "semantic_profile_seed.json",
    "semantic_profile_seed.yaml",
    "semantic_profile_seed.yml",
)

_CAPABILITY_HEADING_TOKENS = ("capability", "focus", "feature", "deliverable", "scope")
_PROGRESSION_HEADING_TOKENS = ("progression", "flow", "journey", "ladder")
_ADMIN_LANGUAGE_TOKENS = (
    "goalspec",
    "objective profile",
    "objective-profile",
    "completion manifest",
    "spec review",
    "traceability",
    "task generation",
    "taskmaster",
    "queue spec",
    "phase spec",
    "golden spec",
    "research plane",
)


class SemanticProfileMilestone(ContractModel):
    """One normalized semantic milestone."""

    id: str
    outcome: str
    capability_scope: tuple[str, ...] = ()

    @field_validator("id", "outcome")
    @classmethod
    def validate_required_text(cls, value: str, info: object) -> str:
        field_name = getattr(info, "field_name", "value")
        return _normalize_required_text(value, field_name=field_name)

    @field_validator("capability_scope", mode="before")
    @classmethod
    def normalize_capability_scope(cls, value: tuple[str, ...] | list[str] | str | None) -> tuple[str, ...]:
        if value is None:
            return ()
        if isinstance(value, str):
            return _normalize_text_sequence((value,))
        return _normalize_text_sequence(value)


class GoalSemanticProfile(ContractModel):
    """Deterministic semantic profile derived from goal text or an optional seed."""

    profile_mode: Literal["heuristic", "seeded"]
    objective_summary: str
    capability_domains: tuple[str, ...] = ()
    progression_lines: tuple[str, ...] = ()
    milestones: tuple[SemanticProfileMilestone, ...]
    semantic_seed_path: str = ""

    @field_validator("objective_summary")
    @classmethod
    def validate_objective_summary(cls, value: str) -> str:
        return _normalize_required_text(value, field_name="objective_summary")

    @field_validator("capability_domains", "progression_lines", mode="before")
    @classmethod
    def normalize_text_sequence_fields(
        cls,
        value: tuple[str, ...] | list[str] | str | None,
    ) -> tuple[str, ...]:
        if value is None:
            return ()
        if isinstance(value, str):
            return _normalize_text_sequence((value,))
        return _normalize_text_sequence(value)

    @field_validator("semantic_seed_path", mode="before")
    @classmethod
    def normalize_semantic_seed_path(cls, value: str | Path | None) -> str:
        return _normalize_optional_text(value)


def discover_semantic_seed_path(paths: RuntimePaths) -> Path | None:
    """Return the first default semantic-seed file present in the workspace."""

    for name in DEFAULT_SEMANTIC_SEED_FILENAMES:
        candidate = paths.objective_dir / name
        if candidate.is_file():
            return candidate
    return None


def load_semantic_seed_document(path: Path) -> dict[str, Any]:
    """Parse one semantic seed document from JSON or YAML."""

    text = path.read_text(encoding="utf-8")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        payload = None
    if isinstance(payload, dict):
        return payload

    try:
        import yaml  # type: ignore
    except ImportError:
        payload = _load_simple_yaml_document(text)
    else:
        payload = yaml.safe_load(text)

    if isinstance(payload, dict):
        return payload
    raise ValueError(f"semantic seed {path.as_posix()} must contain a JSON or YAML object")


def _load_simple_yaml_document(text: str) -> dict[str, Any]:
    """Parse the limited YAML subset used by semantic seed documents."""

    lines = _yaml_nonempty_lines(text)
    if not lines:
        return {}
    payload, index = _parse_yaml_block(lines, start=0, indent=lines[0][0])
    if index != len(lines):
        raise ValueError("semantic seed YAML contains trailing content")
    if not isinstance(payload, dict):
        raise ValueError("semantic seed YAML must contain a top-level object")
    return payload


def _yaml_nonempty_lines(text: str) -> list[tuple[int, str]]:
    lines: list[tuple[int, str]] = []
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        if indent % 2 != 0:
            raise ValueError("semantic seed YAML indentation must use two-space multiples")
        lines.append((indent, stripped))
    return lines


def _parse_yaml_block(
    lines: list[tuple[int, str]],
    *,
    start: int,
    indent: int,
) -> tuple[dict[str, Any] | list[Any], int]:
    if start >= len(lines):
        raise ValueError("semantic seed YAML block is incomplete")
    _, content = lines[start]
    if content.startswith("- "):
        return _parse_yaml_list(lines, start=start, indent=indent)
    return _parse_yaml_mapping(lines, start=start, indent=indent)


def _parse_yaml_mapping(
    lines: list[tuple[int, str]],
    *,
    start: int,
    indent: int,
) -> tuple[dict[str, Any], int]:
    payload: dict[str, Any] = {}
    index = start
    while index < len(lines):
        current_indent, content = lines[index]
        if current_indent < indent:
            break
        if current_indent != indent:
            raise ValueError("semantic seed YAML has inconsistent indentation")
        if content.startswith("- "):
            break
        if ":" not in content:
            raise ValueError(f"semantic seed YAML mapping entry is invalid: {content}")
        key, raw_value = content.split(":", 1)
        key = key.strip()
        raw_value = raw_value.strip()
        index += 1
        if raw_value:
            payload[key] = _parse_yaml_scalar(raw_value)
            continue
        if index >= len(lines) or lines[index][0] <= current_indent:
            payload[key] = ""
            continue
        child, index = _parse_yaml_block(lines, start=index, indent=lines[index][0])
        payload[key] = child
    return payload, index


def _parse_yaml_list(
    lines: list[tuple[int, str]],
    *,
    start: int,
    indent: int,
) -> tuple[list[Any], int]:
    payload: list[Any] = []
    index = start
    while index < len(lines):
        current_indent, content = lines[index]
        if current_indent < indent:
            break
        if current_indent != indent:
            raise ValueError("semantic seed YAML has inconsistent indentation")
        if not content.startswith("- "):
            break
        item_content = content[2:].strip()
        index += 1
        if not item_content:
            if index >= len(lines) or lines[index][0] <= current_indent:
                payload.append("")
                continue
            child, index = _parse_yaml_block(lines, start=index, indent=lines[index][0])
            payload.append(child)
            continue
        if ":" not in item_content:
            payload.append(_parse_yaml_scalar(item_content))
            continue
        key, raw_value = item_content.split(":", 1)
        mapping_item: dict[str, Any] = {key.strip(): _parse_yaml_scalar(raw_value.strip()) if raw_value.strip() else ""}
        if index < len(lines) and lines[index][0] > current_indent:
            child, index = _parse_yaml_block(lines, start=index, indent=lines[index][0])
            if raw_value.strip():
                if not isinstance(child, dict):
                    raise ValueError("semantic seed YAML list item continuation must be a mapping")
                mapping_item.update(child)
            else:
                mapping_item[key.strip()] = child
        payload.append(mapping_item)
    return payload, index


def _parse_yaml_scalar(value: str) -> Any:
    lowered = value.lower()
    if lowered == "null":
        return None
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
        return value[1:-1]
    if re.fullmatch(r"-?\d+", value):
        return int(value)
    return value


def build_goal_semantic_profile(
    goal_text: str,
    *,
    semantic_seed_payload: dict[str, Any] | None = None,
    semantic_seed_path: str | Path | None = None,
) -> GoalSemanticProfile:
    """Build a normalized semantic profile from goal text plus an optional seed."""

    heuristic_summary = extract_objective_summary(goal_text)
    heuristic_domains = extract_capability_domains(goal_text)
    heuristic_progression = extract_progression_lines(goal_text)

    if semantic_seed_payload is None:
        return GoalSemanticProfile(
            profile_mode="heuristic",
            objective_summary=heuristic_summary,
            capability_domains=heuristic_domains,
            progression_lines=heuristic_progression,
            milestones=_heuristic_milestones(
                objective_summary=heuristic_summary,
                capability_domains=heuristic_domains,
                progression_lines=heuristic_progression,
            ),
        )

    objective_summary = _normalize_optional_text(
        semantic_seed_payload.get("objective")
        or semantic_seed_payload.get("objective_summary")
    ) or heuristic_summary
    capability_domains = _normalize_raw_string_list(
        semantic_seed_payload.get("capability_domains") or semantic_seed_payload.get("focus_bullets")
    ) or heuristic_domains
    progression_lines = _normalize_raw_string_list(semantic_seed_payload.get("progression_lines")) or heuristic_progression
    milestones = _normalize_seed_milestones(semantic_seed_payload.get("milestones"))
    if not milestones:
        milestones = _heuristic_milestones(
            objective_summary=objective_summary,
            capability_domains=capability_domains,
            progression_lines=progression_lines,
        )

    return GoalSemanticProfile(
        profile_mode="seeded",
        objective_summary=objective_summary,
        capability_domains=capability_domains,
        progression_lines=progression_lines,
        milestones=milestones,
        semantic_seed_path=semantic_seed_path,
    )


def extract_objective_summary(goal_text: str) -> str:
    """Extract the first prose paragraph as the objective summary."""

    in_fence = False
    paragraph: list[str] = []
    for raw in goal_text.splitlines():
        stripped = raw.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            if paragraph:
                break
            continue
        if in_fence or not stripped:
            if paragraph and not stripped:
                break
            continue
        if stripped.startswith("#"):
            if paragraph:
                break
            continue
        if _is_bullet(stripped):
            if paragraph:
                break
            continue
        paragraph.append(_normalize_text_line(stripped))
    summary = _normalize_optional_text(" ".join(paragraph))
    return summary or "Satisfy the synced product objective."


def extract_capability_domains(goal_text: str) -> tuple[str, ...]:
    """Extract product capability domains from the goal text."""

    explicit = _extract_bullets_from_sections(goal_text, heading_tokens=_CAPABILITY_HEADING_TOKENS)
    if explicit:
        return explicit

    bullets: list[str] = []
    for raw in goal_text.splitlines():
        stripped = raw.strip()
        if not _is_bullet(stripped):
            continue
        bullet = _normalize_bullet_line(stripped)
        if not bullet or _looks_administrative(bullet):
            continue
        bullets.append(bullet)
    return _normalize_text_sequence(bullets[:6])


def extract_progression_lines(goal_text: str) -> tuple[str, ...]:
    """Extract goal lines that describe user or capability progression."""

    explicit = _extract_bullets_from_sections(goal_text, heading_tokens=_PROGRESSION_HEADING_TOKENS)
    if explicit:
        return explicit

    matches: list[str] = []
    for raw in goal_text.splitlines():
        line = _normalize_text_line(raw)
        lowered = line.casefold()
        if not line or _looks_administrative(line):
            continue
        if "progression" in lowered or "journey" in lowered or "ladder" in lowered:
            matches.append(line)
            continue
        if re.search(r"\bfrom\b.+\bto\b.+\bto\b", lowered):
            matches.append(line)
            continue
    return _normalize_text_sequence(matches)


def _extract_bullets_from_sections(goal_text: str, *, heading_tokens: tuple[str, ...]) -> tuple[str, ...]:
    collected: list[str] = []
    capture = False
    for raw in goal_text.splitlines():
        stripped = raw.strip()
        if stripped.startswith("#"):
            heading = stripped.lstrip("#").strip().casefold()
            capture = any(token in heading for token in heading_tokens)
            continue
        if not capture:
            continue
        if not stripped:
            if collected:
                break
            continue
        if not _is_bullet(stripped):
            if collected:
                break
            continue
        bullet = _normalize_bullet_line(stripped)
        if not bullet or _looks_administrative(bullet):
            continue
        collected.append(bullet)
    return _normalize_text_sequence(collected)


def _heuristic_milestones(
    *,
    objective_summary: str,
    capability_domains: tuple[str, ...],
    progression_lines: tuple[str, ...],
) -> tuple[SemanticProfileMilestone, ...]:
    milestones: list[SemanticProfileMilestone] = []

    if capability_domains:
        milestones.append(
            SemanticProfileMilestone(
                id="CAPABILITY-FOUNDATION",
                outcome=f"Implement the core product capabilities: {_join_human_list(capability_domains[:4])}.",
                capability_scope=capability_domains[:4],
            )
        )

    if progression_lines:
        progression_fragment = _progression_fragment(progression_lines[0])
        milestones.append(
            SemanticProfileMilestone(
                id="CAPABILITY-PROGRESSION",
                outcome=f"Deliver the intended progression {progression_fragment}.",
                capability_scope=capability_domains[:3],
            )
        )

    if not milestones:
        milestones.append(
            SemanticProfileMilestone(
                id="CAPABILITY-OBJECTIVE",
                outcome=f"Deliver the product objective described by the goal: {objective_summary}",
            )
        )
    elif objective_summary:
        milestones.append(
            SemanticProfileMilestone(
                id="CAPABILITY-ENDSTATE",
                outcome=f"Complete a coherent end-to-end vertical slice for: {objective_summary}",
                capability_scope=capability_domains,
            )
        )

    return tuple(milestones)


def _normalize_seed_milestones(raw: Any) -> tuple[SemanticProfileMilestone, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ValueError("semantic seed milestones must be a list")

    milestones: list[SemanticProfileMilestone] = []
    seen_ids: set[str] = set()
    for index, item in enumerate(raw, start=1):
        if isinstance(item, str):
            milestone_id = f"SEED-{index:03d}"
            outcome = item
            capability_scope: tuple[str, ...] = ()
        elif isinstance(item, dict):
            milestone_id = _normalize_optional_text(item.get("id")) or f"SEED-{index:03d}"
            outcome = _normalize_optional_text(item.get("outcome") or item.get("title") or item.get("summary"))
            capability_scope = _normalize_raw_string_list(
                item.get("capability_scope") or item.get("capability_domains")
            )
        else:
            raise ValueError("semantic seed milestone entries must be strings or objects")
        milestone = SemanticProfileMilestone(
            id=milestone_id,
            outcome=outcome,
            capability_scope=capability_scope,
        )
        if milestone.id in seen_ids:
            raise ValueError(f"semantic seed milestone id `{milestone.id}` is duplicated")
        seen_ids.add(milestone.id)
        milestones.append(milestone)
    return tuple(milestones)


def _normalize_raw_string_list(raw: Any) -> tuple[str, ...]:
    if raw is None:
        return ()
    if isinstance(raw, str):
        return _normalize_text_sequence((raw,))
    if isinstance(raw, list):
        return _normalize_text_sequence([str(item) for item in raw])
    raise ValueError("semantic seed string-list fields must be strings or lists")


def _join_human_list(items: tuple[str, ...]) -> str:
    values = [item for item in items if item]
    if not values:
        return "the extracted product capabilities"
    if len(values) == 1:
        return values[0]
    if len(values) == 2:
        return f"{values[0]} and {values[1]}"
    return f"{', '.join(values[:-1])}, and {values[-1]}"


def _progression_fragment(value: str) -> str:
    fragment = _normalize_optional_text(value).rstrip(".")
    lowered = fragment.casefold()
    if lowered.startswith("progression"):
        remainder = fragment[len("progression") :].lstrip(" :.-")
        if remainder:
            fragment = remainder
    if lowered.startswith("from "):
        return fragment
    return f"for {fragment}"


def _normalize_bullet_line(line: str) -> str:
    if _is_bullet(line):
        return _normalize_text_line(line[2:])
    return _normalize_text_line(line)


def _normalize_text_line(raw: str) -> str:
    return " ".join(raw.strip().split())


def _is_bullet(line: str) -> bool:
    return line.startswith("- ") or line.startswith("* ")


def _looks_administrative(text: str) -> bool:
    lowered = text.casefold()
    return any(token in lowered for token in _ADMIN_LANGUAGE_TOKENS)
