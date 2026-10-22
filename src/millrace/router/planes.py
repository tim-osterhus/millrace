"""Pure fixed-topology and retained-result compatibility rules."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from .records import Graph, RouterError, fields, number, text

if TYPE_CHECKING:
    from .graph import Revision


def validate_workspaces(graph: Graph, workspaces: Mapping[str, object]) -> None:
    """Check captured shapes/disjoint links without inspecting the current host."""
    if set(workspaces) != set(graph.planes):
        raise RouterError("revision_integrity", "workspace plane set")
    paths: list[Path] = []
    for value in workspaces.values():
        item = fields(value, {"path", "device", "inode"})
        path = Path(text(item["path"]))
        if not path.is_absolute() or ".." in path.parts or str(path) != item["path"]:
            raise RouterError("revision_integrity", "workspace path")
        number(item["device"], integer=True, zero=True)
        number(item["inode"], integer=True, zero=True)
        if graph.max_parallel > 1 and any(
            path == other or path.is_relative_to(other) or other.is_relative_to(path)
            for other in paths
        ):
            raise RouterError("workspace_collision")
        paths.append(path)


def topology(graph: Graph) -> dict[str, object]:
    """Only structural declarations freeze; local routes/stages remain editable."""
    return {
        "planes": {name: plane.workspace for name, plane in graph.planes.items()},
        "max_parallel": graph.max_parallel,
        "seams": {name: seam.data() for name, seam in graph.seams.items()},
        "dependencies": {
            plane_id: {
                node_id: sorted(
                    (dependency.plane_id, dependency.status)
                    for dependency in node.requires_terminal
                )
                for node_id, node in plane.nodes.items()
                if node.requires_terminal
            }
            for plane_id, plane in graph.planes.items()
        },
    }


def check_run_compatibility(
    startup: Revision,
    candidate: Revision,
    plane_states: Mapping[str, Mapping[str, object]],
    unrouted: Sequence[Mapping[str, object]],
) -> None:
    """One predicate for advisory validation and owner-locked activation."""
    if (
        topology(startup.graph) != topology(candidate.graph)
        or startup.workspaces != candidate.workspaces
    ):
        raise RouterError("incompatible_revision", "startup topology/workspace changed")
    if set(plane_states) != set(startup.graph.planes):
        raise RouterError("run_integrity", "plane set")
    for plane_id, state in plane_states.items():
        nodes = candidate.graph.planes[plane_id].nodes
        for key in ("cursor", "resume_target"):
            target = state.get(key)
            if target is not None and target not in nodes:
                raise RouterError("incompatible_revision", f"{plane_id}.{key}")
    for result in unrouted:
        plane_id, node_id = str(result["plane_id"]), str(result["node_id"])
        if (
            plane_id not in candidate.graph.planes
            or node_id not in candidate.graph.planes[plane_id].nodes
        ):
            raise RouterError("incompatible_revision", "retained producing node")
        if (
            result.get("classification") == "semantic"
            and result.get("outcome")
            not in candidate.graph.planes[plane_id].nodes[node_id].routes
        ):
            raise RouterError("incompatible_revision", "retained semantic outcome")
