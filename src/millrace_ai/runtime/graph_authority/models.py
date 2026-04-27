"""Compiled-graph authority model objects."""

from __future__ import annotations

from dataclasses import dataclass

from millrace_ai.contracts import Plane, StageName


@dataclass(frozen=True, slots=True)
class GraphActivationDecision:
    plane: Plane
    stage: StageName
    node_id: str
    stage_kind_id: str
    entry_key: str


__all__ = ["GraphActivationDecision"]
