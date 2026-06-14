"""Neutral router decision contracts shared by runtime routing surfaces."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from .enums import Plane, StageName, WorkItemKind
from .work_refs import coerce_family_and_kind


class RouterAction(str, Enum):
    RUN_STAGE = "run_stage"
    HANDOFF = "handoff"
    IDLE = "idle"
    BLOCKED = "blocked"


@dataclass(frozen=True, slots=True)
class RouterDecision:
    action: RouterAction
    next_plane: Plane | None
    next_stage: StageName | None
    reason: str
    next_node_id: str | None = None
    next_stage_kind_id: str | None = None
    terminal_state_id: str | None = None
    terminal_action_id: str | None = None
    terminal_action_router_consequence: str | None = None
    terminal_action_non_mutating: bool = False
    lifecycle_mutation_plan_id: str | None = None
    lifecycle_action_id: str | None = None
    terminal_writes_status: str | None = None
    failure_class: str | None = None
    runtime_operation_id: str | None = None
    counter_mutation_name: str | None = None
    recovery_counter_name: str | None = None
    counter_key: str | None = None
    create_incident: bool = False


def counter_key_for_failure_class(
    *,
    work_item_family_id: str | None = None,
    work_item_kind: WorkItemKind | str | None = None,
    work_item_id: str,
    failure_class: str,
) -> str:
    family_id, _kind = coerce_family_and_kind(
        family_id=work_item_family_id,
        work_item_kind=work_item_kind,
    )
    if family_id is None:
        raise ValueError("work_item_family_id or work_item_kind is required")
    normalized_id = work_item_id.strip()
    if not normalized_id:
        raise ValueError("work_item_id cannot be empty")

    normalized_failure_class = normalize_failure_class(failure_class)
    return f"{family_id}:{normalized_id}:{normalized_failure_class}"


def normalize_failure_class(failure_class: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9]+", "_", failure_class.strip().lower()).strip("_")
    if not normalized:
        raise ValueError("failure_class cannot be empty")
    return normalized


__all__ = [
    "RouterAction",
    "RouterDecision",
    "counter_key_for_failure_class",
    "normalize_failure_class",
]
