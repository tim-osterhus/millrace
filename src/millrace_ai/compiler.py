"""Reduced compiler for mode selection, frozen plans, and compile diagnostics."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from pydantic import ValidationError

from millrace_ai.config import RuntimeConfig
from millrace_ai.contracts import (
    CompileDiagnostics,
    ExecutionStageName,
    FrozenRunPlan,
    FrozenStagePlan,
    LoopConfigDefinition,
    ModeDefinition,
    PlanningStageName,
    StageName,
)
from millrace_ai.modes import ModeAssetError, load_builtin_mode_bundle
from millrace_ai.paths import WorkspacePaths, workspace_paths

_DEFAULT_MODE_ID = "standard_plain"
_DEFAULT_STAGE_TIMEOUT_SECONDS = 300
_REQUIRED_SKILLS_BY_STAGE: dict[StageName, tuple[str, ...]] = {
    ExecutionStageName.BUILDER: ("skills/stage/execution/builder-core/SKILL.md",),
    ExecutionStageName.CHECKER: ("skills/stage/execution/checker-core/SKILL.md",),
    ExecutionStageName.FIXER: ("skills/stage/execution/fixer-core/SKILL.md",),
    ExecutionStageName.DOUBLECHECKER: ("skills/stage/execution/doublechecker-core/SKILL.md",),
    ExecutionStageName.UPDATER: ("skills/stage/execution/updater-core/SKILL.md",),
    ExecutionStageName.TROUBLESHOOTER: ("skills/stage/execution/troubleshooter-core/SKILL.md",),
    ExecutionStageName.CONSULTANT: ("skills/stage/execution/consultant-core/SKILL.md",),
    PlanningStageName.PLANNER: ("skills/stage/planning/planner-core/SKILL.md",),
    PlanningStageName.MANAGER: ("skills/stage/planning/manager-core/SKILL.md",),
    PlanningStageName.MECHANIC: ("skills/stage/planning/mechanic-core/SKILL.md",),
    PlanningStageName.AUDITOR: ("skills/stage/planning/auditor-core/SKILL.md",),
}

class CompilerValidationError(ValueError):
    """Raised when a mode bundle fails reduced-compiler validation rules."""


@dataclass(frozen=True, slots=True)
class CompileOutcome:
    """Result of one compile attempt including fallback state."""

    active_plan: FrozenRunPlan | None
    diagnostics: CompileDiagnostics
    used_last_known_good: bool


def compile_and_persist_workspace_plan(
    target: WorkspacePaths | Path | str,
    *,
    config: RuntimeConfig,
    requested_mode_id: str | None = None,
    assets_root: Path | None = None,
    now: datetime | None = None,
) -> CompileOutcome:
    """Compile one mode into a frozen plan and persist canonical artifacts.

    Failure policy:
    - Always writes fresh diagnostics.
    - Keeps the existing compiled plan untouched on compile failure.
    - Returns the last known-good plan when one exists.
    """

    paths = _resolve_paths(target)
    compile_time = _utc_now(now)
    mode_id = _resolve_mode_id(requested_mode_id, config)
    compiled_plan_path = paths.state_dir / "compiled_plan.json"
    diagnostics_path = paths.state_dir / "compile_diagnostics.json"

    last_known_good = _load_existing_plan(compiled_plan_path)

    try:
        plan = _compile_frozen_run_plan(
            config=config,
            mode_id=mode_id,
            assets_root=assets_root,
            compile_time=compile_time,
        )
        diagnostics = CompileDiagnostics(
            ok=True,
            mode_id=mode_id,
            warnings=(),
            emitted_at=compile_time,
        )
        _atomic_write_json(compiled_plan_path, plan.model_dump(mode="json"))
        _atomic_write_json(diagnostics_path, diagnostics.model_dump(mode="json"))
        return CompileOutcome(active_plan=plan, diagnostics=diagnostics, used_last_known_good=False)

    except (ModeAssetError, CompilerValidationError, ValidationError, ValueError) as exc:
        diagnostics = CompileDiagnostics(
            ok=False,
            mode_id=mode_id,
            errors=(str(exc),),
            warnings=(),
            emitted_at=compile_time,
        )
        _atomic_write_json(diagnostics_path, diagnostics.model_dump(mode="json"))
        return CompileOutcome(
            active_plan=last_known_good,
            diagnostics=diagnostics,
            used_last_known_good=last_known_good is not None,
        )


def _compile_frozen_run_plan(
    *,
    config: RuntimeConfig,
    mode_id: str,
    assets_root: Path | None,
    compile_time: datetime,
) -> FrozenRunPlan:
    # Phase 1 + 2: resolve mode and loop definitions.
    bundle = load_builtin_mode_bundle(mode_id, assets_root=assets_root)

    # Phase 3 + 4 + 5 + 6: validate map scope, resolve bundles, enforce boundaries.
    stage_plans = _freeze_stage_plans(
        config=config,
        mode=bundle.mode,
        execution_loop=bundle.execution_loop,
        planning_loop=bundle.planning_loop,
    )

    # Phase 7: freeze run plan.
    return FrozenRunPlan(
        compiled_plan_id=_build_compiled_plan_id(
            mode=bundle.mode,
            execution_loop_id=bundle.execution_loop.loop_id,
            planning_loop_id=bundle.planning_loop.loop_id,
            stage_plans=stage_plans,
        ),
        mode_id=bundle.mode.mode_id,
        execution_loop_id=bundle.execution_loop.loop_id,
        planning_loop_id=bundle.planning_loop.loop_id,
        stage_plans=stage_plans,
        compiled_at=compile_time,
        source_refs=(
            f"mode:{bundle.mode.mode_id}",
            f"loop:{bundle.execution_loop.loop_id}",
            f"loop:{bundle.planning_loop.loop_id}",
        ),
    )


def _freeze_stage_plans(
    *,
    config: RuntimeConfig,
    mode: ModeDefinition,
    execution_loop: LoopConfigDefinition,
    planning_loop: LoopConfigDefinition,
) -> tuple[FrozenStagePlan, ...]:
    selected_stages = {stage for stage in execution_loop.stages} | {
        stage for stage in planning_loop.stages
    }
    _validate_mode_stage_maps(mode, selected_stages)

    stage_plans: list[FrozenStagePlan] = []
    for loop in (execution_loop, planning_loop):
        for stage in loop.stages:
            stage_name = stage.value
            entrypoint_override = mode.stage_entrypoint_overrides.get(stage)
            if entrypoint_override is not None:
                entrypoint_path = _validate_entrypoint_override(stage_name, entrypoint_override)
            else:
                entrypoint_path = f"entrypoints/{loop.plane.value}/{stage_name}.md"

            stage_config = config.stages.get(stage_name)
            runner_name = mode.stage_runner_bindings.get(stage)
            if runner_name is None and stage_config is not None:
                runner_name = stage_config.runner

            model_name = mode.stage_model_bindings.get(stage)
            if model_name is None and stage_config is not None:
                model_name = stage_config.model

            timeout_seconds = (
                stage_config.timeout_seconds
                if stage_config is not None
                else _DEFAULT_STAGE_TIMEOUT_SECONDS
            )

            stage_plans.append(
                FrozenStagePlan(
                    stage=stage,
                    plane=loop.plane,
                    entrypoint_path=entrypoint_path,
                    entrypoint_contract_id=f"{stage_name}.contract.v1",
                    required_skills=_required_skills_for_stage(stage),
                    attached_skill_additions=tuple(mode.stage_skill_additions.get(stage, ())),
                    runner_name=runner_name,
                    model_name=model_name,
                    timeout_seconds=timeout_seconds,
                )
            )

    return tuple(stage_plans)


def _required_skills_for_stage(stage: StageName) -> tuple[str, ...]:
    return _REQUIRED_SKILLS_BY_STAGE.get(stage, ())


def _validate_mode_stage_maps(mode: ModeDefinition, selected_stages: set[StageName]) -> None:
    for map_name, mapping in (
        ("stage_entrypoint_overrides", mode.stage_entrypoint_overrides),
        ("stage_skill_additions", mode.stage_skill_additions),
        ("stage_model_bindings", mode.stage_model_bindings),
        ("stage_runner_bindings", mode.stage_runner_bindings),
    ):
        for stage in sorted(mapping, key=lambda stage_name: stage_name.value):
            if stage not in selected_stages:
                raise CompilerValidationError(
                    f"Mode map `{map_name}` references stage outside selected loops: {stage.value}"
                )


def _validate_entrypoint_override(stage_name: str, raw_path: str) -> str:
    normalized = _normalize_relative_asset_path(raw_path)
    if (
        normalized is None
        or not normalized.startswith("entrypoints/")
        or not normalized.endswith(".md")
    ):
        raise CompilerValidationError(
            f"Invalid entrypoint override for stage `{stage_name}`: {raw_path}"
        )
    return normalized


def _normalize_relative_asset_path(raw_path: str) -> str | None:
    text = raw_path.strip()
    if not text:
        return None

    path = Path(text)
    if path.is_absolute():
        return None

    normalized = path.as_posix()
    while normalized.startswith("./"):
        normalized = normalized[2:]
    if not normalized:
        return None
    if normalized == ".." or normalized.startswith("../") or "/../" in normalized:
        return None

    return normalized


def _build_compiled_plan_id(
    *,
    mode: ModeDefinition,
    execution_loop_id: str,
    planning_loop_id: str,
    stage_plans: tuple[FrozenStagePlan, ...],
) -> str:
    payload = {
        "mode_id": mode.mode_id,
        "execution_loop_id": execution_loop_id,
        "planning_loop_id": planning_loop_id,
        "stage_plans": [stage_plan.model_dump(mode="json") for stage_plan in stage_plans],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()[:12]
    return f"plan-{mode.mode_id}-{digest}"


def _resolve_mode_id(requested_mode_id: str | None, config: RuntimeConfig) -> str:
    if requested_mode_id and requested_mode_id.strip():
        return requested_mode_id.strip()

    default_mode = config.runtime.default_mode.strip()
    if default_mode:
        return default_mode

    return _DEFAULT_MODE_ID


def _resolve_paths(target: WorkspacePaths | Path | str) -> WorkspacePaths:
    return target if isinstance(target, WorkspacePaths) else workspace_paths(target)


def _utc_now(now: datetime | None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    if now.tzinfo is None:
        return now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc)


def _load_existing_plan(path: Path) -> FrozenRunPlan | None:
    if not path.is_file():
        return None

    try:
        payload = path.read_text(encoding="utf-8")
    except OSError:
        return None

    try:
        return FrozenRunPlan.model_validate_json(payload)
    except ValidationError:
        return None


def _atomic_write_json(path: Path, payload: dict[str, object]) -> None:
    serialized = json.dumps(payload, indent=2) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)

    temp_path = path.with_name(f".{path.name}.tmp-{uuid4().hex}")
    try:
        with temp_path.open("w", encoding="utf-8") as handle:
            handle.write(serialized)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path.exists():
            temp_path.unlink()


__all__ = [
    "CompileOutcome",
    "CompilerValidationError",
    "compile_and_persist_workspace_plan",
]
