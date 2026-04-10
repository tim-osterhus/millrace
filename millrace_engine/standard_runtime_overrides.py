"""Config-to-override translation for the standard runtime selection."""

from __future__ import annotations

from pathlib import Path

from .config import ComplexityRouteSelection, EngineConfig, default_stage_configs
from .contracts import ModePolicyToggles, StageType
from .execution_nodes import execution_stage_type_for_node
from .loop_architecture import LoopStageNodeOverrides
from .materialization import ModeMaterializationOverrides, StageInvocationOverride
from .policies.complexity import select_complexity_route

PUBLIC_STANDARD_STAGE_NODES = (
    "builder",
    "integration",
    "qa",
    "hotfix",
    "doublecheck",
    "troubleshoot",
    "consult",
    "update",
)
_BASELINE_STAGE_CONFIGS = default_stage_configs()


def complexity_selection_for_execution_nodes(
    config: EngineConfig,
    node_ids: tuple[str, ...] | list[str],
    *,
    task_complexity: str | None = None,
) -> ComplexityRouteSelection:
    """Resolve the complexity policy selection for one execution loop shape."""

    return select_complexity_route(config, task_complexity=task_complexity, node_ids=node_ids)


def explicit_stage_binding_parameters(
    config: EngineConfig,
    stage_type: StageType,
) -> dict[str, object]:
    """Return only runner/model/effort values that differ from packaged stage defaults."""

    baseline = _BASELINE_STAGE_CONFIGS[stage_type]
    stage_config = config.stages[stage_type]
    updates: dict[str, object] = {}
    if stage_config.runner != baseline.runner:
        updates["runner"] = stage_config.runner
    if stage_config.model != baseline.model:
        updates["model"] = stage_config.model
    if stage_config.effort is not None and stage_config.effort != baseline.effort:
        updates["effort"] = stage_config.effort
    stage_permission_profile = getattr(stage_config, "permission_profile", None)
    baseline_permission_profile = getattr(baseline, "permission_profile", None)
    if (
        stage_permission_profile != baseline_permission_profile
        and (stage_permission_profile is not None or baseline_permission_profile is not None)
    ):
        updates["permission_profile"] = stage_permission_profile
    return updates


def stage_overrides_for_node_ids(
    config: EngineConfig,
    node_ids: tuple[str, ...] | list[str],
) -> tuple[StageInvocationOverride, ...]:
    """Build per-node overrides for the resolved execution loop nodes."""

    stage_overrides: list[StageInvocationOverride] = []
    for node_id in node_ids:
        stage_type = execution_stage_type_for_node(node_id)
        if stage_type is None:
            continue
        stage_config = config.stages[stage_type]
        binding_updates = explicit_stage_binding_parameters(config, stage_type)
        override_kwargs: dict[str, object] = {
            "runner": binding_updates.get("runner"),
            "model": binding_updates.get("model"),
            "effort": binding_updates.get("effort"),
            "allow_search": stage_config.allow_search,
            "prompt_asset_ref": prompt_asset_ref_for_path(config.paths.workspace, stage_config.prompt_file),
            "timeout_seconds": stage_config.timeout_seconds,
        }
        if "permission_profile" in getattr(LoopStageNodeOverrides, "model_fields", {}):
            override_kwargs["permission_profile"] = binding_updates.get("permission_profile")
        stage_overrides.append(
            StageInvocationOverride(
                plane="execution",
                node_id=node_id,
                overrides=LoopStageNodeOverrides(**override_kwargs),
            )
        )
    return tuple(stage_overrides)


def mode_overrides_for_config(config: EngineConfig) -> ModeMaterializationOverrides:
    return ModeMaterializationOverrides(
        policy_toggles=ModePolicyToggles(
            integration_mode=config.execution.integration_mode,
            run_update_on_empty=config.execution.run_update_on_empty,
        ),
        stage_overrides=stage_overrides_for_node_ids(config, PUBLIC_STANDARD_STAGE_NODES),
    )


def mode_overrides_for_execution_nodes(
    config: EngineConfig,
    node_ids: tuple[str, ...] | list[str],
    *,
    task_complexity: str | None = None,
    complexity_selection: ComplexityRouteSelection | None = None,
) -> ModeMaterializationOverrides:
    """Build execution-mode overrides for one resolved loop shape."""

    selection = complexity_selection or complexity_selection_for_execution_nodes(
        config,
        node_ids,
        task_complexity=task_complexity,
    )
    return ModeMaterializationOverrides(
        model_profile_ref=selection.selected_model_profile_ref,
        policy_toggles=ModePolicyToggles(
            integration_mode=config.execution.integration_mode,
            run_update_on_empty=config.execution.run_update_on_empty,
        ),
        stage_overrides=stage_overrides_for_node_ids(config, node_ids),
    )


def prompt_asset_ref_for_path(workspace_root: Path, prompt_path: Path | None) -> str | None:
    if prompt_path is None:
        return None
    resolved_workspace = workspace_root.expanduser().resolve(strict=False)
    resolved_prompt = prompt_path.expanduser().resolve(strict=False)
    try:
        return resolved_prompt.relative_to(resolved_workspace).as_posix()
    except ValueError:
        return resolved_prompt.as_posix()


__all__ = [
    "PUBLIC_STANDARD_STAGE_NODES",
    "complexity_selection_for_execution_nodes",
    "explicit_stage_binding_parameters",
    "mode_overrides_for_config",
    "mode_overrides_for_execution_nodes",
    "prompt_asset_ref_for_path",
    "stage_overrides_for_node_ids",
]
