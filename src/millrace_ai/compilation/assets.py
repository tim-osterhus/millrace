"""Compiler asset reference resolution."""

from __future__ import annotations

import hashlib
from pathlib import Path

from millrace_ai.architecture import GraphLoopDefinition, MaterializedGraphNodePlan
from millrace_ai.architecture.common import dedupe_preserve_order
from millrace_ai.architecture.materialization import ResolvedAssetRef
from millrace_ai.assets import (
    BUILTIN_STAGE_KIND_PATHS,
    graph_loop_asset_relative_path,
    mode_asset_relative_path,
)
from millrace_ai.contracts import ModeDefinition, Plane
from millrace_ai.paths import WorkspacePaths

from .outcomes import CompilerValidationError

MISSING_ASSET_TOKEN = "missing"


def build_resolved_asset_refs(
    *,
    paths: WorkspacePaths,
    mode: ModeDefinition,
    graph_loops: dict[Plane, GraphLoopDefinition],
    node_plans: tuple[MaterializedGraphNodePlan, ...],
    assets_root: Path,
) -> tuple[ResolvedAssetRef, ...]:
    refs: list[ResolvedAssetRef] = [
        resolved_packaged_asset_ref(
            asset_family="mode",
            logical_id=f"mode:{mode.mode_id}",
            relative_path=mode_asset_relative_path(mode.mode_id, assets_root=assets_root),
            assets_root=assets_root,
        ),
        *[
            resolved_packaged_asset_ref(
                asset_family="graph_loop",
                logical_id=f"graph_loop:{graph_loop.loop_id}",
                relative_path=graph_loop_asset_relative_path(
                    graph_loop.loop_id,
                    assets_root=assets_root,
                ),
                assets_root=assets_root,
            )
            for _plane, graph_loop in sorted(graph_loops.items(), key=lambda item: item[0].value)
        ],
    ]

    used_stage_kind_ids = dedupe_preserve_order([node.stage_kind_id for node in node_plans])
    refs.extend(
        resolved_packaged_asset_ref(
            asset_family="stage_kind",
            logical_id=f"stage_kind:{stage_kind_id}",
            relative_path=BUILTIN_STAGE_KIND_PATHS[stage_kind_id],
            assets_root=assets_root,
        )
        for stage_kind_id in used_stage_kind_ids
    )

    entrypoint_paths = dedupe_preserve_order([node.entrypoint_path for node in node_plans])
    refs.extend(
        resolved_workspace_asset_ref(
            asset_family="entrypoint",
            logical_id=f"entrypoint:{entrypoint_path}",
            relative_path=entrypoint_path,
            paths=paths,
        )
        for entrypoint_path in entrypoint_paths
    )

    required_skill_paths = dedupe_preserve_order(
        [
            skill_path
            for node in node_plans
            for skill_path in node.required_skill_paths
        ]
    )
    attached_skill_paths = dedupe_preserve_order(
        [
            skill_path
            for node in node_plans
            for skill_path in node.attached_skill_additions
        ]
    )
    refs.extend(
        resolved_workspace_asset_ref(
            asset_family="skill",
            logical_id=f"skill:{skill_path}",
            relative_path=skill_path,
            paths=paths,
        )
        for skill_path in required_skill_paths
    )
    refs.extend(
        maybe_resolved_workspace_asset_ref(
            asset_family="skill",
            logical_id=f"skill:{skill_path}",
            relative_path=skill_path,
            paths=paths,
        )
        for skill_path in attached_skill_paths
    )

    return tuple(refs)


def resolved_packaged_asset_ref(
    *,
    asset_family: str,
    logical_id: str,
    relative_path: Path,
    assets_root: Path,
) -> ResolvedAssetRef:
    compile_path = assets_root / relative_path
    return ResolvedAssetRef(
        asset_family=asset_family,
        logical_id=logical_id,
        compile_time_path=relative_path.as_posix(),
        content_sha256=sha256_file(compile_path),
    )


def resolved_workspace_asset_ref(
    *,
    asset_family: str,
    logical_id: str,
    relative_path: str,
    paths: WorkspacePaths,
) -> ResolvedAssetRef:
    compile_path = paths.runtime_root / relative_path
    return ResolvedAssetRef(
        asset_family=asset_family,
        logical_id=logical_id,
        compile_time_path=compile_path.relative_to(paths.root).as_posix(),
        content_sha256=sha256_file(compile_path),
    )


def maybe_resolved_workspace_asset_ref(
    *,
    asset_family: str,
    logical_id: str,
    relative_path: str,
    paths: WorkspacePaths,
) -> ResolvedAssetRef:
    compile_path = paths.runtime_root / relative_path
    return ResolvedAssetRef(
        asset_family=asset_family,
        logical_id=logical_id,
        compile_time_path=compile_path.relative_to(paths.root).as_posix(),
        content_sha256=sha256_file(compile_path) if compile_path.is_file() else MISSING_ASSET_TOKEN,
    )


def sha256_file(path: Path) -> str:
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise CompilerValidationError(f"Cannot read compile asset: {path}") from exc
    return hashlib.sha256(payload).hexdigest()


__all__ = [
    "MISSING_ASSET_TOKEN",
    "build_resolved_asset_refs",
    "maybe_resolved_workspace_asset_ref",
    "resolved_packaged_asset_ref",
    "resolved_workspace_asset_ref",
    "sha256_file",
]
