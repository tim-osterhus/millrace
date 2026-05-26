"""Doctor checks for shipped assets and resolved runner posture."""

from __future__ import annotations

import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import ValidationError

from millrace_ai.assets import (
    BUILTIN_LOOP_PATHS,
    BUILTIN_MODE_PATHS,
    LintLevel,
    ModeAssetError,
    lint_asset_manifests,
    load_builtin_loop_definition,
    load_builtin_mode_definition,
    validate_shipped_mode_same_graph,
)
from millrace_ai.compilation.mode_resolution import resolve_mode_id
from millrace_ai.compilation.outcomes import CompilerValidationError
from millrace_ai.compilation.workspace_plan import compile_compiled_run_plan
from millrace_ai.config import RuntimeConfig, load_runtime_config
from millrace_ai.contracts import ExecutionStageName, PlanningStageName
from millrace_ai.errors import AssetValidationError

from .models import DoctorIssue

if TYPE_CHECKING:
    from .checks import DoctorContext


def check_mode_and_loop_assets(context: DoctorContext) -> None:
    try:
        validate_shipped_mode_same_graph(assets_root=context.assets_root)
    except ModeAssetError as exc:
        context.errors.append(
            DoctorIssue(
                code="mode_bundle_invalid",
                message=str(exc),
                path=context.assets_root,
            )
        )

    for mode_id in sorted(BUILTIN_MODE_PATHS):
        try:
            load_builtin_mode_definition(mode_id, assets_root=context.assets_root)
        except ModeAssetError as exc:
            context.errors.append(
                DoctorIssue(
                    code="mode_definition_invalid",
                    message=f"{mode_id}: {exc}",
                    path=context.assets_root / BUILTIN_MODE_PATHS[mode_id],
                )
            )

    for loop_id in sorted(BUILTIN_LOOP_PATHS):
        try:
            load_builtin_loop_definition(loop_id, assets_root=context.assets_root)
        except ModeAssetError as exc:
            context.errors.append(
                DoctorIssue(
                    code="loop_definition_invalid",
                    message=f"{loop_id}: {exc}",
                    path=context.assets_root / BUILTIN_LOOP_PATHS[loop_id],
                )
            )


def check_entrypoint_assets(context: DoctorContext) -> None:
    diagnostics = lint_asset_manifests(
        assets_root=context.assets_root,
        canonical_contract_ids_by_stage=_canonical_contract_ids_by_stage(),
    )

    for diagnostic in diagnostics:
        issue = DoctorIssue(
            code=f"asset_lint_{diagnostic.lint_level.value}",
            message=f"{diagnostic.asset_id}: {diagnostic.reason}",
            path=diagnostic.path,
        )
        if diagnostic.lint_level in {LintLevel.STRUCTURAL, LintLevel.COMPATIBILITY}:
            context.errors.append(issue)
        else:
            context.warnings.append(issue)


def check_resolved_runner_posture(context: DoctorContext) -> None:
    config_path = context.paths.runtime_root / "millrace.toml"
    try:
        config = load_runtime_config(config_path)
    except (OSError, ValidationError, ValueError) as exc:
        context.errors.append(
            DoctorIssue(
                code="runtime_config_invalid",
                message=str(exc),
                path=config_path,
            )
        )
        return

    try:
        mode_id = resolve_mode_id(None, config)
        compiled_plan = compile_compiled_run_plan(
            paths=context.paths,
            config=config,
            mode_id=mode_id,
            assets_root=context.assets_root,
            compile_time=datetime.now(timezone.utc),
        )
    except (AssetValidationError, CompilerValidationError, ValidationError, ValueError) as exc:
        context.errors.append(
            DoctorIssue(
                code="resolved_mode_invalid",
                message=str(exc),
                path=config_path,
            )
        )
        return

    resolved_runners = {
        node.runner_name or config.runners.default_runner.strip() or "codex_cli"
        for graph in compiled_plan.graphs_by_plane.values()
        for node in graph.nodes
    }
    for runner_name in sorted(resolved_runners):
        command = _runner_command_for_name(config=config, runner_name=runner_name)
        if command is None:
            context.errors.append(
                DoctorIssue(
                    code="configured_runner_unknown",
                    message=(
                        f"resolved runner `{runner_name}` is not a built-in configured runner "
                        f"for mode `{compiled_plan.mode_id}`"
                    ),
                    path=config_path,
                )
            )
            continue
        if _command_exists(command):
            continue
        context.warnings.append(
            DoctorIssue(
                code="runner_binary_unavailable",
                message=(
                    f"resolved runner `{runner_name}` for mode `{compiled_plan.mode_id}` "
                    f"uses command `{command}`, which is not available"
                ),
                path=config_path,
            )
        )


def _runner_command_for_name(*, config: RuntimeConfig, runner_name: str) -> str | None:
    if runner_name == "codex_cli":
        return config.runners.codex.command
    if runner_name == "pi_rpc":
        return config.runners.pi.command
    return None


def _command_exists(command: str) -> bool:
    candidate = Path(command).expanduser()
    if candidate.is_absolute() or "/" in command:
        return candidate.exists()
    doctor_module = sys.modules.get("millrace_ai.doctor")
    doctor_shutil = getattr(doctor_module, "shutil", shutil)
    return doctor_shutil.which(command) is not None


def _canonical_contract_ids_by_stage() -> dict[str, str]:
    mapping: dict[str, str] = {}
    for stage in ExecutionStageName:
        mapping[stage.value] = f"{stage.value}.v1"
    for planning_stage in PlanningStageName:
        mapping[planning_stage.value] = f"{planning_stage.value}.v1"
    return mapping


__all__ = [
    "check_entrypoint_assets",
    "check_mode_and_loop_assets",
    "check_resolved_runner_posture",
]
