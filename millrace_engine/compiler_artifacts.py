"""Pure helpers for compile ordering, hashing, and artifact emission."""

from __future__ import annotations

import json
from collections import deque
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any

from .assets.resolver import AssetSourceKind
from .baseline_assets import packaged_baseline_asset
from .compiler_models import (
    CompileArtifacts,
    CompileDiagnosticsArtifact,
    CompilerDiagnostic,
    CompileStatus,
    CompileTimeResolvedSnapshot,
    DiagnosticSeverity,
    FrozenPlanSourceKind,
    FrozenPlanSourceRef,
    FrozenRunPlan,
    FrozenRunPlanContent,
)
from .compiler_rendering import (
    render_compile_time_resolved_snapshot_markdown,
    render_frozen_run_plan_markdown,
)
from .contracts import RegistryObjectRef, StageOverrideField
from .diagnostics import ensure_directory
from .markdown import write_text_atomic
from .materialization_models import MaterializedAssetBinding, MaterializedStageBinding
from .paths import RuntimePaths
from .provenance import clear_transition_history


def plan_hash(content: FrozenRunPlanContent) -> str:
    return sha256_text(canonical_json(content.model_dump(mode="json")))


def asset_source_ref(binding: MaterializedAssetBinding) -> FrozenPlanSourceRef:
    if binding.source_kind is AssetSourceKind.PACKAGE:
        if binding.relative_path is None:
            raise RuntimeError(f"package-backed asset binding for {binding.node_id} is missing a relative path")
        sha256_value = sha256(packaged_baseline_asset(binding.relative_path).read_bytes()).hexdigest()
    else:
        sha256_value = sha256_file(binding.workspace_path)
    return FrozenPlanSourceRef(
        kind=FrozenPlanSourceKind.ASSET,
        object_ref=f"asset:{binding.resolved_ref}",
        title=binding.node_id,
        source_ref=binding.relative_path or binding.workspace_path.as_posix(),
        source_layer=binding.source_kind.value,
        sha256=sha256_value,
    )


def current_rebinding_value(
    binding: MaterializedStageBinding,
    field: StageOverrideField,
) -> Any:
    value_map = {
        StageOverrideField.MODEL_PROFILE_REF: (
            binding.model_profile_ref.model_dump(mode="json") if binding.model_profile_ref is not None else None
        ),
        StageOverrideField.RUNNER: binding.runner.value if binding.runner is not None else None,
        StageOverrideField.MODEL: binding.model,
        StageOverrideField.EFFORT: binding.effort.value if binding.effort is not None else None,
        StageOverrideField.PERMISSION_PROFILE: (
            binding.permission_profile.value if binding.permission_profile is not None else None
        ),
        StageOverrideField.ALLOW_SEARCH: binding.allow_search,
        StageOverrideField.PROMPT_ASSET_REF: binding.prompt_asset_ref,
        StageOverrideField.TIMEOUT_SECONDS: binding.timeout_seconds,
    }
    return value_map[field]


def has_error_diagnostics(diagnostics: tuple[CompilerDiagnostic, ...]) -> bool:
    return any(diagnostic.severity is DiagnosticSeverity.ERROR for diagnostic in diagnostics)


def reachable_nodes(
    *,
    entry_node_id: str,
    adjacency: dict[str, set[str]],
) -> set[str]:
    visited: set[str] = set()
    queue: deque[str] = deque([entry_node_id])
    while queue:
        node_id = queue.popleft()
        if node_id in visited:
            continue
        visited.add(node_id)
        for target in sorted(adjacency.get(node_id, ())):
            if target not in visited:
                queue.append(target)
    return visited


def ref_string(ref: RegistryObjectRef | None) -> str:
    if ref is None:
        return "n/a"
    return f"{ref.kind.value}:{ref.id}@{ref.version}"


def canonical_json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def render_json(payload: Any) -> str:
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def sha256_text(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sorted_diagnostics(diagnostics: tuple[CompilerDiagnostic, ...]) -> tuple[CompilerDiagnostic, ...]:
    severity_order = {
        DiagnosticSeverity.ERROR: 0,
        DiagnosticSeverity.WARNING: 1,
    }
    return tuple(
        sorted(
            diagnostics,
            key=lambda item: (
                item.phase.value,
                severity_order[item.severity],
                item.object_ref or "",
                item.path,
                item.code,
            ),
        )
    )


def emit_compile_artifacts(
    paths: RuntimePaths,
    *,
    run_id: str,
    selection_ref: RegistryObjectRef,
    diagnostics: tuple[CompilerDiagnostic, ...],
    result: CompileStatus,
    plan: FrozenRunPlan | None = None,
    snapshot: CompileTimeResolvedSnapshot | None = None,
) -> CompileArtifacts:
    run_dir = ensure_directory(paths.runs_dir / run_id)
    compile_diagnostics_json_path = run_dir / "compile_diagnostics.json"
    transition_history_path = run_dir / "transition_history.jsonl"
    resolved_snapshot_json_path = run_dir / "resolved_snapshot.json"
    resolved_snapshot_markdown_path = run_dir / "resolved_snapshot.md"
    frozen_plan_json_path = run_dir / "frozen_run_plan.json"
    frozen_plan_markdown_path = run_dir / "frozen_run_plan.md"
    clear_transition_history(transition_history_path)
    diagnostics_payload = CompileDiagnosticsArtifact(
        run_id=run_id,
        generated_at=datetime.now(timezone.utc),
        selection_ref=selection_ref,
        result=result,
        content_hash=plan.content_hash if plan is not None else None,
        diagnostics=diagnostics,
    )
    write_text_atomic(
        compile_diagnostics_json_path,
        render_json(diagnostics_payload.model_dump(mode="json")),
    )

    if plan is not None and snapshot is not None:
        write_text_atomic(
            resolved_snapshot_json_path,
            render_json(snapshot.model_dump(mode="json")),
        )
        write_text_atomic(
            resolved_snapshot_markdown_path,
            render_compile_time_resolved_snapshot_markdown(snapshot),
        )
        write_text_atomic(
            frozen_plan_json_path,
            render_json(plan.model_dump(mode="json")),
        )
        write_text_atomic(frozen_plan_markdown_path, render_frozen_run_plan_markdown(plan))
    else:
        for stale_path in (
            resolved_snapshot_json_path,
            resolved_snapshot_markdown_path,
            frozen_plan_json_path,
            frozen_plan_markdown_path,
        ):
            try:
                stale_path.unlink()
            except FileNotFoundError:
                continue
            except IsADirectoryError as exc:
                raise OSError(f"expected compiler artifact path to be a file: {stale_path}") from exc
        return CompileArtifacts(
            run_dir=run_dir,
            compile_diagnostics_json_path=compile_diagnostics_json_path,
        )
    return CompileArtifacts(
        run_dir=run_dir,
        compile_diagnostics_json_path=compile_diagnostics_json_path,
        resolved_snapshot_json_path=resolved_snapshot_json_path,
        resolved_snapshot_markdown_path=resolved_snapshot_markdown_path,
        frozen_plan_json_path=frozen_plan_json_path,
        frozen_plan_markdown_path=frozen_plan_markdown_path,
    )


def emit_failure_compile_artifacts(
    paths: RuntimePaths,
    *,
    selection_ref: RegistryObjectRef,
    run_id: str,
    diagnostics: tuple[CompilerDiagnostic, ...],
) -> CompileArtifacts | None:
    try:
        return emit_compile_artifacts(
            paths,
            run_id=run_id,
            selection_ref=selection_ref,
            diagnostics=diagnostics,
            result=CompileStatus.FAIL,
        )
    except OSError:
        return None


__all__ = [
    "asset_source_ref",
    "canonical_json",
    "current_rebinding_value",
    "emit_compile_artifacts",
    "emit_failure_compile_artifacts",
    "has_error_diagnostics",
    "plan_hash",
    "reachable_nodes",
    "ref_string",
    "render_json",
    "sha256_file",
    "sha256_text",
    "sorted_diagnostics",
]
