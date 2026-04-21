"""Stable public facade for built-in graph-loop helpers."""

from millrace_ai.assets.loop_graphs import (
    ASSETS_ROOT,
    BUILTIN_GRAPH_LOOP_PATHS,
    SHIPPED_GRAPH_LOOP_IDS,
    GraphLoopAssetError,
    discover_graph_loop_definitions,
    load_builtin_graph_loop_definition,
    load_builtin_graph_loop_definitions,
    load_graph_loop_definition,
)

__all__ = [
    "ASSETS_ROOT",
    "BUILTIN_GRAPH_LOOP_PATHS",
    "GraphLoopAssetError",
    "SHIPPED_GRAPH_LOOP_IDS",
    "discover_graph_loop_definitions",
    "load_graph_loop_definition",
    "load_builtin_graph_loop_definition",
    "load_builtin_graph_loop_definitions",
]
