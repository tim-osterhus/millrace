"""Status helpers for runtime-effect metadata emitted by stage results."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from millrace_ai.contracts import StageResultEnvelope
from millrace_ai.paths import WorkspacePaths

_RUNTIME_EFFECT_STATUS_KEYS = {
    "latest_runtime_effect_handler_id": "runtime_effect_handler_id",
    "latest_runtime_effect_operation_id": "runtime_effect_operation_id",
    "latest_runtime_effect_legacy_handler_id": "runtime_effect_legacy_handler_id",
    "latest_runtime_effect_decision": "runtime_effect_decision",
    "latest_runtime_effect_failure_class": "runtime_effect_failure_class",
    "latest_runtime_effect_failure_message": "runtime_effect_failure_message",
    "latest_runtime_effect_mutation_phase": "runtime_effect_mutation_phase",
    "latest_runtime_effect_failure_policy_id": "runtime_effect_failure_policy_id",
    "latest_runtime_effect_recovery_action": "runtime_effect_recovery_action",
}


@dataclass(frozen=True, slots=True)
class LatestRuntimeEffectStageResult:
    path: Path
    stage_result: StageResultEnvelope


def latest_runtime_effect_status_metadata(
    paths: WorkspacePaths,
    stage_result_path: str | None,
) -> dict[str, str]:
    latest = latest_runtime_effect_stage_result(paths, stage_result_path)
    if latest is None:
        return {}
    return runtime_effect_status_metadata_from_stage_result(latest.stage_result)


def latest_runtime_effect_stage_result(
    paths: WorkspacePaths,
    stage_result_path: str | None,
) -> LatestRuntimeEffectStageResult | None:
    if stage_result_path is None:
        return None
    path = Path(stage_result_path)
    if not path.is_absolute():
        path = paths.root / path
    stage_result = load_stage_result(path)
    if stage_result is None:
        return None
    latest = _latest_sibling_runtime_effect_stage_result(path, stage_result)
    if latest is not None:
        return latest
    if runtime_effect_status_metadata_from_stage_result(stage_result):
        return LatestRuntimeEffectStageResult(path=path, stage_result=stage_result)
    return None


def load_stage_result(path: Path) -> StageResultEnvelope | None:
    try:
        return StageResultEnvelope.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def runtime_effect_status_metadata_from_stage_result(
    stage_result: StageResultEnvelope,
) -> dict[str, str]:
    metadata = stage_result.metadata
    values = {
        status_key: metadata.get(metadata_key)
        for status_key, metadata_key in _RUNTIME_EFFECT_STATUS_KEYS.items()
    }
    rendered = {
        key: value
        for key, value in values.items()
        if isinstance(value, str) and value
    }
    return rendered


def _latest_sibling_runtime_effect_stage_result(
    path: Path,
    stage_result: StageResultEnvelope,
) -> LatestRuntimeEffectStageResult | None:
    if not path.parent.is_dir():
        return None
    current_sort_key = _stage_result_sort_key(stage_result, path)
    candidates: list[
        tuple[tuple[str, str, str], LatestRuntimeEffectStageResult, dict[str, str]]
    ] = []
    for sibling in path.parent.iterdir():
        if not sibling.is_file() or sibling.suffix != ".json":
            continue
        sibling_stage_result = load_stage_result(sibling)
        if sibling_stage_result is None:
            continue
        sort_key = _stage_result_sort_key(sibling_stage_result, sibling)
        if sort_key > current_sort_key:
            continue
        metadata = runtime_effect_status_metadata_from_stage_result(sibling_stage_result)
        if not metadata:
            continue
        candidates.append(
            (
                sort_key,
                LatestRuntimeEffectStageResult(
                    path=sibling,
                    stage_result=sibling_stage_result,
                ),
                metadata,
            )
        )
    if not candidates:
        return None
    _, latest, latest_metadata = sorted(candidates, key=lambda item: item[0])[-1]
    return latest


def _stage_result_sort_key(
    stage_result: StageResultEnvelope,
    path: Path,
) -> tuple[str, str, str]:
    return (
        stage_result.completed_at.isoformat(),
        stage_result.started_at.isoformat(),
        path.name,
    )
