"""Compiler asset reference resolution."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from millrace_ai.architecture import GraphLoopDefinition, MaterializedGraphNodePlan
from millrace_ai.architecture.common import dedupe_preserve_order
from millrace_ai.architecture.materialization import ResolvedAssetRef
from millrace_ai.assets import (
    ARTIFACT_CONTRACT_REGISTRY_ROOT,
    DOCUMENT_ADAPTER_REGISTRY_ROOT,
    EFFECT_STORE_REGISTRY_ROOT,
    EFFECT_VALIDATOR_REGISTRY_ROOT,
    LIFECYCLE_MUTATION_PLAN_REGISTRY_ROOT,
    QUEUE_CLAIM_POLICY_REGISTRY_ROOT,
    RECOVERY_POLICY_REGISTRY_ROOT,
    REQUEST_CONTEXT_PROFILE_REGISTRY_ROOT,
    RUNTIME_EFFECT_HANDLER_REGISTRY_ROOT,
    RUNTIME_EFFECT_OPERATION_REGISTRY_ROOT,
    RUNTIME_EFFECT_RULE_REGISTRY_ROOT,
    RUNTIME_EFFECT_RUNNER_REGISTRY_ROOT,
    RUNTIME_FAILURE_POLICY_REGISTRY_ROOT,
    TERMINAL_ACTION_REGISTRY_ROOT,
    WORK_ITEM_FAMILY_REGISTRY_ROOT,
    WORKSPACE_SCHEMA_EPOCH_REGISTRY_ROOT,
    graph_loop_asset_relative_path,
    mode_asset_relative_path,
    stage_kind_asset_relative_path,
)
from millrace_ai.contracts import ModeDefinition, Plane
from millrace_ai.paths import WorkspacePaths

from .outcomes import CompilerValidationError

MISSING_ASSET_TOKEN = "missing"

_WORKFLOW_PRIMITIVE_REF_SPECS = (
    ("artifact_contract", ARTIFACT_CONTRACT_REGISTRY_ROOT, "artifact_id"),
    ("request_context_profile", REQUEST_CONTEXT_PROFILE_REGISTRY_ROOT, "profile_id"),
    ("work_item_family", WORK_ITEM_FAMILY_REGISTRY_ROOT, "family_id"),
    ("document_adapter", DOCUMENT_ADAPTER_REGISTRY_ROOT, "adapter_id"),
    ("queue_claim_policy", QUEUE_CLAIM_POLICY_REGISTRY_ROOT, "policy_id"),
    ("terminal_action", TERMINAL_ACTION_REGISTRY_ROOT, "terminal_action_id"),
    ("lifecycle_mutation_plan", LIFECYCLE_MUTATION_PLAN_REGISTRY_ROOT, "plan_id"),
    ("runtime_effect_handler", RUNTIME_EFFECT_HANDLER_REGISTRY_ROOT, "handler_id"),
    ("runtime_effect_runner", RUNTIME_EFFECT_RUNNER_REGISTRY_ROOT, "runner_id"),
    ("runtime_effect_rule", RUNTIME_EFFECT_RULE_REGISTRY_ROOT, "rule_id"),
    ("runtime_effect_store", EFFECT_STORE_REGISTRY_ROOT, "store_id"),
    ("runtime_effect_validator", EFFECT_VALIDATOR_REGISTRY_ROOT, "validator_id"),
    ("runtime_effect_operation", RUNTIME_EFFECT_OPERATION_REGISTRY_ROOT, "operation_id"),
    ("workflow_recovery_policy", RECOVERY_POLICY_REGISTRY_ROOT, "policy_id"),
    ("runtime_failure_policy", RUNTIME_FAILURE_POLICY_REGISTRY_ROOT, "policy_id"),
    ("workspace_schema_epoch", WORKSPACE_SCHEMA_EPOCH_REGISTRY_ROOT, "epoch_id"),
)


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
            relative_path=stage_kind_asset_relative_path(
                stage_kind_id,
                assets_root=assets_root,
            ),
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

    refs.extend(build_workflow_primitive_asset_refs(assets_root=assets_root))

    return tuple(refs)


def build_workflow_primitive_asset_refs(*, assets_root: Path) -> tuple[ResolvedAssetRef, ...]:
    refs: list[ResolvedAssetRef] = []
    seen: set[tuple[str, str]] = set()

    for asset_family, registry_root, id_key in _WORKFLOW_PRIMITIVE_REF_SPECS:
        for asset_path in _discover_json_paths(assets_root, registry_root):
            for item in _definition_items(_load_json_asset(asset_path), path=asset_path):
                primitive_id = item.get(id_key)
                if not isinstance(primitive_id, str) or not primitive_id:
                    raise CompilerValidationError(
                        f"Workflow primitive asset missing {id_key}: {asset_path}"
                    )
                identity = (asset_family, primitive_id)
                if identity in seen:
                    raise CompilerValidationError(
                        f"Duplicate workflow primitive asset ref: {asset_family}:{primitive_id}"
                    )
                seen.add(identity)
                refs.append(
                    resolved_packaged_asset_ref(
                        asset_family=asset_family,
                        logical_id=f"{asset_family}:{primitive_id}",
                        relative_path=asset_path.relative_to(assets_root),
                        assets_root=assets_root,
                    )
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


def _discover_json_paths(assets_root: Path, registry_root: Path) -> tuple[Path, ...]:
    root = assets_root / registry_root
    if not root.is_dir():
        return ()
    return tuple(sorted(path for path in root.rglob("*.json") if path.is_file()))


def _load_json_asset(path: Path) -> Any:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise CompilerValidationError(f"Cannot read workflow primitive asset: {path}") from exc
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise CompilerValidationError(f"Invalid JSON in workflow primitive asset: {path}") from exc


def _definition_items(payload: Any, *, path: Path) -> tuple[dict[str, Any], ...]:
    if isinstance(payload, dict) and "definitions" in payload:
        definitions = payload["definitions"]
    else:
        definitions = payload

    if isinstance(definitions, dict):
        items = (definitions,)
    elif isinstance(definitions, list):
        items = tuple(definitions)
    else:
        raise CompilerValidationError(f"Invalid workflow primitive asset shape: {path}")

    if not all(isinstance(item, dict) for item in items):
        raise CompilerValidationError(f"Invalid workflow primitive asset shape: {path}")
    return items


__all__ = [
    "MISSING_ASSET_TOKEN",
    "build_resolved_asset_refs",
    "build_workflow_primitive_asset_refs",
    "maybe_resolved_workspace_asset_ref",
    "resolved_packaged_asset_ref",
    "resolved_workspace_asset_ref",
    "sha256_file",
]
