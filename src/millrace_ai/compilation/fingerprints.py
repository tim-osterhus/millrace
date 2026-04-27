"""Compiler fingerprint and compiled-plan identity helpers."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from millrace_ai.architecture import CompiledRunPlan, CompileInputFingerprint, FrozenGraphPlanePlan
from millrace_ai.architecture.materialization import ResolvedAssetRef
from millrace_ai.config import RuntimeConfig, fingerprint_runtime_config
from millrace_ai.contracts import LearningTriggerRuleDefinition, Plane
from millrace_ai.paths import WorkspacePaths

from .assets import MISSING_ASSET_TOKEN, sha256_file


def build_compiled_plan_id(
    *,
    mode_id: str,
    loop_ids_by_plane: dict[Plane, str],
    graphs_by_plane: dict[Plane, FrozenGraphPlanePlan],
    concurrency_policy: object,
    learning_trigger_rules: tuple[LearningTriggerRuleDefinition, ...],
) -> str:
    payload = {
        "mode_id": mode_id,
        "loop_ids_by_plane": {
            plane.value: loop_id
            for plane, loop_id in sorted(loop_ids_by_plane.items(), key=lambda item: item[0].value)
        },
        "graphs_by_plane": {
            plane.value: graph.model_dump(mode="json")
            for plane, graph in sorted(graphs_by_plane.items(), key=lambda item: item[0].value)
        },
        "concurrency_policy": (
            concurrency_policy.model_dump(mode="json")
            if hasattr(concurrency_policy, "model_dump")
            else None
        ),
        "learning_trigger_rules": [
            rule.model_dump(mode="json")
            for rule in learning_trigger_rules
        ],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()[:12]
    return f"plan-{mode_id}-{digest}"


def build_compile_input_fingerprint(
    *,
    config: RuntimeConfig,
    mode_id: str,
    resolved_assets: tuple[ResolvedAssetRef, ...],
    paths: WorkspacePaths,
    assets_root: Path,
) -> CompileInputFingerprint:
    return CompileInputFingerprint(
        mode_id=mode_id,
        config_fingerprint=fingerprint_runtime_config(config),
        assets_fingerprint=fingerprint_resolved_assets(
            resolved_assets=resolved_assets,
            paths=paths,
            assets_root=assets_root,
        ),
    )


def build_existing_plan_input_fingerprint(
    *,
    config: RuntimeConfig,
    mode_id: str,
    plan: CompiledRunPlan,
    paths: WorkspacePaths,
    assets_root: Path,
) -> CompileInputFingerprint:
    return build_compile_input_fingerprint(
        config=config,
        mode_id=mode_id,
        resolved_assets=plan.resolved_assets,
        paths=paths,
        assets_root=assets_root,
    )


def fingerprint_resolved_assets(
    *,
    resolved_assets: tuple[ResolvedAssetRef, ...],
    paths: WorkspacePaths,
    assets_root: Path,
) -> str:
    digest = hashlib.sha256()
    for asset_ref in sorted(
        resolved_assets,
        key=lambda ref: (ref.asset_family, ref.logical_id, ref.compile_time_path),
    ):
        file_path = path_for_resolved_asset_ref(asset_ref, paths=paths, assets_root=assets_root)
        digest.update(asset_ref.asset_family.encode("utf-8"))
        digest.update(b"\0")
        digest.update(asset_ref.logical_id.encode("utf-8"))
        digest.update(b"\0")
        digest.update(asset_ref.compile_time_path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(current_asset_content_token(file_path).encode("utf-8"))
        digest.update(b"\0")
    return f"assets-{digest.hexdigest()[:12]}"


def path_for_resolved_asset_ref(
    asset_ref: ResolvedAssetRef,
    *,
    paths: WorkspacePaths,
    assets_root: Path,
) -> Path:
    compile_path = Path(asset_ref.compile_time_path)
    if compile_path.parts[:1] == ("millrace-agents",):
        return paths.root / compile_path
    return assets_root / compile_path


def current_asset_content_token(path: Path) -> str:
    if not path.is_file():
        return MISSING_ASSET_TOKEN
    return sha256_file(path)


__all__ = [
    "build_compile_input_fingerprint",
    "build_compiled_plan_id",
    "build_existing_plan_input_fingerprint",
    "current_asset_content_token",
    "fingerprint_resolved_assets",
    "path_for_resolved_asset_ref",
]
