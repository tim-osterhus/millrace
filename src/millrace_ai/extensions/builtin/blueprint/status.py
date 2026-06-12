"""Blueprint-owned CLI status projection implementation."""

from __future__ import annotations

import json
from pathlib import Path

from millrace_ai.paths import WorkspacePaths

_EMPTY_BLUEPRINT_PAYLOAD: dict[str, object] = {
    "draft_counts": {
        "queue": 0,
        "active": 0,
        "blocked": 0,
        "approved": 0,
        "canceled": 0,
        "superseded": 0,
    },
    "packet_counts": {
        "candidates": 0,
        "approved": 0,
        "rejected": 0,
        "superseded": 0,
    },
    "critique_counts": {
        "open": 0,
        "resolved": 0,
    },
    "evaluation_count": 0,
    "promotion_count": 0,
    "drafts": [],
    "packets": [],
    "critiques": [],
    "evaluations": [],
    "promotions": [],
}

_EMPTY_BLUEPRINT_LINES = (
    "blueprint_draft_queue_depth: 0",
    "blueprint_draft_active_count: 0",
    "blueprint_draft_blocked_count: 0",
    "blueprint_draft_approved_count: 0",
    "blueprint_packet_candidate_count: 0",
    "blueprint_packet_approved_count: 0",
    "blueprint_packet_rejected_count: 0",
    "blueprint_critique_open_count: 0",
    "blueprint_evaluation_count: 0",
    "blueprint_promotion_count: 0",
)


def default_status_projection_payload() -> dict[str, object]:
    """Return the empty Blueprint status payload for inactive projections."""

    return _EMPTY_BLUEPRINT_PAYLOAD


def default_status_projection_lines() -> tuple[str, ...]:
    """Return the empty Blueprint status lines for inactive projections."""

    return _EMPTY_BLUEPRINT_LINES


def collect_status_projection(
    paths: WorkspacePaths,
    *,
    active_mode_id: str | None,
    persisted_mode_id: str | None,
) -> dict[str, object]:
    return collect_blueprint_status(
        paths,
        active_mode_id=active_mode_id,
        persisted_mode_id=persisted_mode_id,
    )


def render_status_projection_lines(status: dict[str, object]) -> tuple[str, ...]:
    return render_blueprint_status_lines(status)


def collect_blueprint_status(
    paths: WorkspacePaths,
    *,
    active_mode_id: str | None,
    persisted_mode_id: str | None,
) -> dict[str, object]:
    root = paths.runtime_root / "blueprints"
    draft_counts: dict[str, int] = {}
    drafts: list[dict[str, object]] = []
    for state in ("queue", "active", "blocked", "approved", "canceled", "superseded"):
        directory = root / "drafts" / state
        draft_counts[state] = 0
        for path in _json_files(directory):
            draft_counts[state] += 1
            draft = _read_json_object(path)
            if draft is None:
                continue
            drafts.append(
                {
                    "state": state,
                    "draft_id": _string_payload_value(draft, "draft_id"),
                    "root_spec_id": _string_payload_value(draft, "root_spec_id"),
                    "draft_index": _payload_value(draft, "draft_index"),
                    "current_revision": _payload_value(draft, "current_revision"),
                    "latest_blueprint_id": _string_payload_value(
                        draft,
                        "latest_blueprint_id",
                    ),
                    "latest_critique_id": _string_payload_value(
                        draft,
                        "latest_critique_id",
                    ),
                    "path": _workspace_relative(paths, path),
                }
            )

    packets: list[dict[str, object]] = []
    packet_counts: dict[str, int] = {}
    for state in ("candidates", "approved", "rejected", "superseded"):
        directory = root / "packets" / state
        packet_counts[state] = 0
        for path in _json_files(directory):
            packet_counts[state] += 1
            packet = _read_json_object(path)
            if packet is None:
                continue
            packets.append(
                {
                    "state": state,
                    "blueprint_id": _string_payload_value(packet, "blueprint_id"),
                    "draft_id": _string_payload_value(packet, "draft_id"),
                    "root_spec_id": _string_payload_value(packet, "root_spec_id"),
                    "revision": _payload_value(packet, "revision"),
                    "path": _workspace_relative(paths, path),
                }
            )

    critiques: list[dict[str, object]] = []
    critique_counts: dict[str, int] = {}
    for state in ("open", "resolved"):
        directory = root / "critiques" / state
        critique_counts[state] = 0
        for path in _json_files(directory):
            critique_counts[state] += 1
            critique = _read_json_object(path)
            if critique is None:
                continue
            critiques.append(
                {
                    "state": state,
                    "critique_id": _string_payload_value(critique, "critique_id"),
                    "blueprint_id": _string_payload_value(critique, "blueprint_id"),
                    "draft_id": _string_payload_value(critique, "draft_id"),
                    "root_spec_id": _string_payload_value(critique, "root_spec_id"),
                    "path": _workspace_relative(paths, path),
                }
            )

    evaluations: list[dict[str, object]] = []
    for path in _json_files(root / "evaluations"):
        evaluation = _read_json_object(path)
        if evaluation is None:
            continue
        evaluations.append(
            {
                "evaluation_id": _string_payload_value(evaluation, "evaluation_id"),
                "decision": _string_payload_value(evaluation, "decision"),
                "blueprint_id": _string_payload_value(evaluation, "blueprint_id"),
                "draft_id": _string_payload_value(evaluation, "draft_id"),
                "root_spec_id": _string_payload_value(evaluation, "root_spec_id"),
                "critique_id": _string_payload_value(evaluation, "critique_id"),
                "path": _workspace_relative(paths, path),
            }
        )

    promotions: list[dict[str, object]] = []
    for path in _json_files(root / "promotions"):
        promotion = _read_json_object(path)
        if promotion is None:
            continue
        promotions.append(
            {
                "promotion_id": _string_payload_value(promotion, "promotion_id"),
                "blueprint_id": _string_payload_value(promotion, "blueprint_id"),
                "evaluation_id": _string_payload_value(promotion, "evaluation_id"),
                "draft_id": _string_payload_value(promotion, "draft_id"),
                "root_spec_id": _string_payload_value(promotion, "root_spec_id"),
                "generated_task_id": _string_payload_value(
                    promotion,
                    "generated_task_id",
                ),
                "generated_task_path": _string_payload_value(
                    promotion,
                    "generated_task_path",
                ),
                "path": _workspace_relative(paths, path),
            }
        )

    return {
        "draft_counts": draft_counts,
        "packet_counts": packet_counts,
        "critique_counts": critique_counts,
        "evaluation_count": len(evaluations),
        "promotion_count": len(promotions),
        "drafts": sorted(
            drafts,
            key=lambda item: (str(item["state"]), str(item["draft_id"])),
        ),
        "packets": sorted(
            packets,
            key=lambda item: (str(item["state"]), str(item["blueprint_id"])),
        ),
        "critiques": sorted(
            critiques,
            key=lambda item: (str(item["state"]), str(item["critique_id"])),
        ),
        "evaluations": sorted(evaluations, key=lambda item: str(item["evaluation_id"])),
        "promotions": sorted(promotions, key=lambda item: str(item["promotion_id"])),
    }


def render_blueprint_status_lines(status: dict[str, object]) -> tuple[str, ...]:
    draft_counts = _dict_value(status, "draft_counts")
    packet_counts = _dict_value(status, "packet_counts")
    critique_counts = _dict_value(status, "critique_counts")
    lines = [
        f"blueprint_draft_queue_depth: {_count_value(draft_counts, 'queue')}",
        f"blueprint_draft_active_count: {_count_value(draft_counts, 'active')}",
        f"blueprint_draft_blocked_count: {_count_value(draft_counts, 'blocked')}",
        f"blueprint_draft_approved_count: {_count_value(draft_counts, 'approved')}",
        f"blueprint_packet_candidate_count: {_count_value(packet_counts, 'candidates')}",
        f"blueprint_packet_approved_count: {_count_value(packet_counts, 'approved')}",
        f"blueprint_packet_rejected_count: {_count_value(packet_counts, 'rejected')}",
        f"blueprint_critique_open_count: {_count_value(critique_counts, 'open')}",
        f"blueprint_evaluation_count: {_status_value(status.get('evaluation_count'))}",
        f"blueprint_promotion_count: {_status_value(status.get('promotion_count'))}",
    ]
    for draft in _dict_items(status, "drafts"):
        lines.append(
            "blueprint_draft: "
            f"state={_status_value(draft.get('state'))} "
            f"draft={_status_value(draft.get('draft_id'))} "
            f"root_spec={_status_value(draft.get('root_spec_id'))} "
            f"revision={_status_value(draft.get('current_revision'))} "
            f"latest_blueprint={_status_value(draft.get('latest_blueprint_id'))} "
            f"latest_critique={_status_value(draft.get('latest_critique_id'))} "
            f"path={_status_value(draft.get('path'))}"
        )
    for packet in _dict_items(status, "packets"):
        lines.append(
            "blueprint_packet: "
            f"state={_status_value(packet.get('state'))} "
            f"blueprint={_status_value(packet.get('blueprint_id'))} "
            f"draft={_status_value(packet.get('draft_id'))} "
            f"root_spec={_status_value(packet.get('root_spec_id'))} "
            f"revision={_status_value(packet.get('revision'))} "
            f"path={_status_value(packet.get('path'))}"
        )
    for critique in _dict_items(status, "critiques"):
        lines.append(
            "blueprint_critique: "
            f"state={_status_value(critique.get('state'))} "
            f"critique={_status_value(critique.get('critique_id'))} "
            f"blueprint={_status_value(critique.get('blueprint_id'))} "
            f"draft={_status_value(critique.get('draft_id'))} "
            f"path={_status_value(critique.get('path'))}"
        )
    for evaluation in _dict_items(status, "evaluations"):
        lines.append(
            "blueprint_evaluation: "
            f"evaluation={_status_value(evaluation.get('evaluation_id'))} "
            f"decision={_status_value(evaluation.get('decision'))} "
            f"blueprint={_status_value(evaluation.get('blueprint_id'))} "
            f"draft={_status_value(evaluation.get('draft_id'))} "
            f"critique={_status_value(evaluation.get('critique_id'))} "
            f"path={_status_value(evaluation.get('path'))}"
        )
    for promotion in _dict_items(status, "promotions"):
        lines.append(
            "blueprint_promotion: "
            f"promotion={_status_value(promotion.get('promotion_id'))} "
            f"blueprint={_status_value(promotion.get('blueprint_id'))} "
            f"evaluation={_status_value(promotion.get('evaluation_id'))} "
            f"generated_task={_status_value(promotion.get('generated_task_id'))} "
            f"generated_task_path={_status_value(promotion.get('generated_task_path'))} "
            f"path={_status_value(promotion.get('path'))}"
    )
    return tuple(lines)


def _json_files(directory: Path) -> tuple[Path, ...]:
    if not directory.exists():
        return ()
    return tuple(sorted(path for path in directory.iterdir() if path.suffix == ".json"))


def _read_json_object(path: Path) -> dict[str, object] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if isinstance(data, dict):
        return data
    return None


def _workspace_relative(paths: WorkspacePaths, path: Path) -> str:
    return str(path.relative_to(paths.runtime_root))


def _payload_value(payload: dict[str, object], key: str) -> object:
    value = payload.get(key)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return json.dumps(value, sort_keys=True)


def _string_payload_value(payload: dict[str, object], key: str) -> str:
    value = payload.get(key)
    if value is None:
        return ""
    return str(value)


def _dict_value(status: dict[str, object], key: str) -> dict[str, object]:
    value = status.get(key)
    return value if isinstance(value, dict) else {}


def _dict_items(status: dict[str, object], key: str) -> tuple[dict[str, object], ...]:
    value = status.get(key)
    if isinstance(value, list):
        return tuple(item for item in value if isinstance(item, dict))
    return ()


def _count_value(payload: dict[str, object], key: str) -> str:
    value = payload.get(key)
    return _status_value(value)


def _status_value(value: object) -> str:
    if value is None:
        return "none"
    return str(value)
