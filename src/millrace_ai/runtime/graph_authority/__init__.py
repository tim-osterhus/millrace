"""Runtime-owned compiled-graph activation and routing helpers."""

from __future__ import annotations

import importlib as _importlib

from .activation import (
    completion_activation_for_graph,
    learning_stage_activation_for_graph,
    work_item_activation_for_graph,
)
from .models import GraphActivationDecision


def __getattr__(name: str) -> object:
    if name in ("route_generic_stage_result_from_graph", "route_stage_result_from_graph"):
        mod = _importlib.import_module(".routing", __package__)
        attr = getattr(mod, name)
        globals()[name] = attr
        return attr
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "GraphActivationDecision",
    "completion_activation_for_graph",
    "learning_stage_activation_for_graph",
    "route_generic_stage_result_from_graph",
    "route_stage_result_from_graph",
    "work_item_activation_for_graph",
]
