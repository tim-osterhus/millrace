"""Built-in graph-loop asset loading for the additive loop-architecture layer."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from millrace_ai.architecture import GraphLoopDefinition
from millrace_ai.assets.architecture import discover_stage_kind_definitions
from millrace_ai.errors import AssetValidationError

ASSETS_ROOT = Path(__file__).resolve().parent
GRAPH_LOOPS_ROOT = Path("graphs")

BUILTIN_GRAPH_LOOP_PATHS: dict[str, Path] = {
    "execution.standard": Path("graphs/execution/standard.json"),
    "execution.skills_pipeline": Path("graphs/execution/skills_pipeline.json"),
    "learning.standard": Path("graphs/learning/standard.json"),
    "planning.standard": Path("graphs/planning/standard.json"),
    "planning.skills_pipeline": Path("graphs/planning/skills_pipeline.json"),
}

SHIPPED_GRAPH_LOOP_IDS: tuple[str, ...] = tuple(BUILTIN_GRAPH_LOOP_PATHS)


class GraphLoopAssetError(AssetValidationError):
    """Raised when built-in graph loop assets cannot be resolved or validated."""


def load_builtin_graph_loop_definition(
    loop_id: str,
    *,
    assets_root: Path | None = None,
) -> GraphLoopDefinition:
    root = _resolve_assets_root(assets_root)
    graph_path = _resolve_graph_loop_path(loop_id, root)
    payload = _load_json_asset(graph_path, asset_kind="graph loop")

    try:
        graph_loop = GraphLoopDefinition.model_validate(payload)
    except ValidationError as exc:
        first_error = exc.errors()[0]["msg"] if exc.errors() else "validation failed"
        raise GraphLoopAssetError(
            f"Invalid graph loop definition in asset: {graph_path} ({first_error})"
        ) from exc

    if graph_loop.loop_id != loop_id:
        raise GraphLoopAssetError(
            f"Graph loop asset id mismatch: expected {loop_id}, found {graph_loop.loop_id}"
        )

    _validate_graph_loop_against_stage_kinds(graph_loop, assets_root=root)
    return graph_loop


def load_builtin_graph_loop_definitions(
    *,
    assets_root: Path | None = None,
) -> tuple[GraphLoopDefinition, ...]:
    return tuple(
        load_builtin_graph_loop_definition(loop_id, assets_root=assets_root)
        for loop_id in SHIPPED_GRAPH_LOOP_IDS
    )


def load_graph_loop_definition(
    loop_id: str,
    *,
    assets_root: Path | None = None,
) -> GraphLoopDefinition:
    root = _resolve_assets_root(assets_root)
    discovered = {graph_loop.loop_id: graph_loop for graph_loop in discover_graph_loop_definitions(assets_root=root)}
    graph_loop = discovered.get(loop_id)
    if graph_loop is None:
        raise GraphLoopAssetError(f"Unknown discovered graph loop id: {loop_id}")
    return graph_loop


def discover_graph_loop_definitions(
    *,
    assets_root: Path | None = None,
) -> tuple[GraphLoopDefinition, ...]:
    root = _resolve_assets_root(assets_root)
    discovered: list[GraphLoopDefinition] = []
    seen_ids: set[str] = set()

    for graph_path in _discover_graph_loop_paths(root):
        graph_loop = _load_graph_loop_definition_at_path(graph_path)
        _validate_graph_loop_against_stage_kinds(graph_loop, assets_root=root)
        if graph_loop.loop_id in seen_ids:
            raise GraphLoopAssetError(f"Duplicate discovered graph loop id: {graph_loop.loop_id}")
        seen_ids.add(graph_loop.loop_id)
        discovered.append(graph_loop)

    return tuple(sorted(discovered, key=lambda graph_loop: graph_loop.loop_id))


def _validate_graph_loop_against_stage_kinds(
    graph_loop: GraphLoopDefinition,
    *,
    assets_root: Path,
) -> None:
    stage_kinds = {
        stage_kind.stage_kind_id: stage_kind
        for stage_kind in discover_stage_kind_definitions(assets_root=assets_root)
    }
    node_map = {node.node_id: node for node in graph_loop.nodes}

    for node in graph_loop.nodes:
        stage_kind = stage_kinds.get(node.stage_kind_id)
        if stage_kind is None:
            raise GraphLoopAssetError(
                f"Graph loop {graph_loop.loop_id} node {node.node_id} references unknown "
                f"stage_kind_id {node.stage_kind_id}"
            )
        if stage_kind.plane is not graph_loop.plane:
            raise GraphLoopAssetError(
                f"Graph loop {graph_loop.loop_id} node {node.node_id} uses stage kind "
                f"{node.stage_kind_id} from plane {stage_kind.plane.value}"
            )
        disallowed_overrides = sorted(node.declared_override_names() - set(stage_kind.allowed_overrides))
        if disallowed_overrides:
            raise GraphLoopAssetError(
                f"Graph loop {graph_loop.loop_id} node {node.node_id} declares unsupported "
                f"overrides for stage kind {node.stage_kind_id}: {', '.join(disallowed_overrides)}"
            )

    for edge in graph_loop.edges:
        source_node = node_map[edge.from_node_id]
        source_stage_kind = stage_kinds[source_node.stage_kind_id]
        for outcome in edge.on_outcomes:
            if outcome not in source_stage_kind.legal_outcomes:
                raise GraphLoopAssetError(
                    f"Graph loop {graph_loop.loop_id} edge {edge.edge_id} declares illegal "
                    f"outcome {outcome} for stage kind {source_stage_kind.stage_kind_id}"
                )

    if graph_loop.dynamic_policies is not None:
        for resume_policy in graph_loop.dynamic_policies.resume_policies:
            source_node = node_map[resume_policy.source_node_id]
            source_stage_kind = stage_kinds[source_node.stage_kind_id]
            if resume_policy.on_outcome not in source_stage_kind.legal_outcomes:
                raise GraphLoopAssetError(
                    f"Graph loop {graph_loop.loop_id} resume policy {resume_policy.policy_id} "
                    f"declares illegal outcome {resume_policy.on_outcome} for stage kind "
                    f"{source_stage_kind.stage_kind_id}"
                )

        for threshold_policy in graph_loop.dynamic_policies.threshold_policies:
            for source_node_id in threshold_policy.source_node_ids:
                source_node = node_map[source_node_id]
                source_stage_kind = stage_kinds[source_node.stage_kind_id]
                if threshold_policy.on_outcome not in source_stage_kind.legal_outcomes:
                    raise GraphLoopAssetError(
                        f"Graph loop {graph_loop.loop_id} threshold policy "
                        f"{threshold_policy.policy_id} declares illegal outcome "
                        f"{threshold_policy.on_outcome} for stage kind "
                        f"{source_stage_kind.stage_kind_id}"
                    )

    if graph_loop.completion_behavior is not None:
        target_node = node_map[graph_loop.completion_behavior.target_node_id]
        target_stage_kind = stage_kinds[target_node.stage_kind_id]
        if not target_stage_kind.closure_role:
            raise GraphLoopAssetError(
                f"Graph loop {graph_loop.loop_id} completion behavior targets non-closure "
                f"stage kind {target_stage_kind.stage_kind_id}"
            )


def _resolve_assets_root(assets_root: Path | None) -> Path:
    if assets_root is None:
        return ASSETS_ROOT
    return Path(assets_root)


def _discover_graph_loop_paths(assets_root: Path) -> tuple[Path, ...]:
    graph_root = assets_root / GRAPH_LOOPS_ROOT
    if not graph_root.is_dir():
        return ()
    return tuple(sorted(path for path in graph_root.rglob("*.json") if path.is_file()))


def _resolve_graph_loop_path(loop_id: str, assets_root: Path) -> Path:
    relative_path = BUILTIN_GRAPH_LOOP_PATHS.get(loop_id)
    if relative_path is None:
        raise GraphLoopAssetError(f"Unknown built-in graph loop id: {loop_id}")
    return assets_root / relative_path


def _load_json_asset(path: Path, *, asset_kind: str) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise GraphLoopAssetError(f"Cannot read {asset_kind} asset: {path}") from exc

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise GraphLoopAssetError(f"Invalid JSON in {asset_kind} asset: {path}") from exc

    if not isinstance(payload, dict):
        raise GraphLoopAssetError(f"Invalid JSON in {asset_kind} asset: {path}")

    return payload


def _load_graph_loop_definition_at_path(path: Path) -> GraphLoopDefinition:
    payload = _load_json_asset(path, asset_kind="graph loop")

    try:
        return GraphLoopDefinition.model_validate(payload)
    except ValidationError as exc:
        first_error = exc.errors()[0]["msg"] if exc.errors() else "validation failed"
        raise GraphLoopAssetError(
            f"Invalid graph loop definition in asset: {path} ({first_error})"
        ) from exc


__all__ = [
    "ASSETS_ROOT",
    "BUILTIN_GRAPH_LOOP_PATHS",
    "GRAPH_LOOPS_ROOT",
    "GraphLoopAssetError",
    "SHIPPED_GRAPH_LOOP_IDS",
    "discover_graph_loop_definitions",
    "load_graph_loop_definition",
    "load_builtin_graph_loop_definition",
    "load_builtin_graph_loop_definitions",
]
