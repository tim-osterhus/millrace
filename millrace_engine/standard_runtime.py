"""Standard 01B execution-selection helpers and operator-facing views."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from .compiler import CompileResult, CompileStatus, CompileTimeResolvedSnapshot, FrozenRunCompiler, FrozenRunPlan
from .compiler_rebinding import bound_execution_parameters_for_stage
from .config import EngineConfig
from .contracts import ExecutionStatus, PersistedObjectKind, RegistryObjectRef
from .execution_nodes import status_requires_large_route
from .paths import RuntimePaths
from .provenance import BoundExecutionParameters
from .registry import discover_registry_state
from .standard_runtime_models import (
    RegistryObjectSelectionView,
    RuntimeSelectionView,
    StageExecutionBindingView,
)
from .standard_runtime_overrides import (
    complexity_selection_for_execution_nodes,
    mode_overrides_for_execution_nodes,
)
from .standard_runtime_views import runtime_selection_view_from_plan


STANDARD_MODE_REF = RegistryObjectRef(
    kind=PersistedObjectKind.MODE,
    id="mode.standard",
    version="1.0.0",
)
LARGE_MODE_REF = RegistryObjectRef(
    kind=PersistedObjectKind.MODE,
    id="mode.large",
    version="1.0.0",
)


def execution_mode_ref(
    *,
    size_latch: str | None = None,
    current_status: ExecutionStatus | None = None,
) -> RegistryObjectRef:
    """Return the packaged/workspace mode ref that owns the current execution route."""

    normalized_size = (size_latch or "").strip().upper()
    if normalized_size == "LARGE" or status_requires_large_route(current_status):
        return LARGE_MODE_REF
    return STANDARD_MODE_REF


def execution_node_ids_for_mode(paths: RuntimePaths, mode_ref: RegistryObjectRef) -> tuple[str, ...]:
    """Return the resolved execution-node ids for one selected mode ref."""

    discovery = discover_registry_state(paths.root, validate_catalog=False)
    effective_documents = {document.key: document for document in discovery.effective}
    try:
        mode_document = effective_documents[(mode_ref.kind.value, mode_ref.id, mode_ref.version)]
    except KeyError as exc:
        raise RuntimeError(f"runtime selection is missing mode {mode_ref.id}@{mode_ref.version}") from exc
    execution_loop_ref = mode_document.definition.payload.execution_loop_ref
    try:
        loop_document = effective_documents[
            (execution_loop_ref.kind.value, execution_loop_ref.id, execution_loop_ref.version)
        ]
    except KeyError as exc:
        raise RuntimeError(
            f"runtime selection is missing execution loop {execution_loop_ref.id}@{execution_loop_ref.version}"
        ) from exc
    return tuple(node.node_id for node in loop_document.definition.payload.nodes)


def compile_execution_runtime_selection(
    config: EngineConfig,
    paths: RuntimePaths,
    *,
    run_id: str,
    size_latch: str | None = None,
    current_status: ExecutionStatus | None = None,
    task_complexity: str | None = None,
    resolve_assets: bool = True,
) -> CompileResult:
    """Compile the selected SMALL or LARGE execution mode for one run."""

    mode_ref = execution_mode_ref(size_latch=size_latch, current_status=current_status)
    node_ids = execution_node_ids_for_mode(paths, mode_ref)
    complexity_selection = complexity_selection_for_execution_nodes(
        config,
        node_ids,
        task_complexity=task_complexity,
    )
    compiler = FrozenRunCompiler(paths)
    return compiler.compile_mode(
        mode_ref,
        run_id=run_id,
        overrides=mode_overrides_for_execution_nodes(
            config,
            node_ids,
            complexity_selection=complexity_selection,
        ),
        resolve_assets=resolve_assets,
    )


def preview_execution_runtime_selection(
    config: EngineConfig,
    paths: RuntimePaths,
    *,
    preview_run_id: str,
    size_latch: str | None = None,
    current_status: ExecutionStatus | None = None,
    task_complexity: str | None = None,
    resolve_assets: bool = True,
) -> RuntimeSelectionView:
    """Compile and render the selected SMALL or LARGE preview selection."""

    mode_ref = execution_mode_ref(size_latch=size_latch, current_status=current_status)
    node_ids = execution_node_ids_for_mode(paths, mode_ref)
    complexity_selection = complexity_selection_for_execution_nodes(
        config,
        node_ids,
        task_complexity=task_complexity,
    )
    compiler = FrozenRunCompiler(paths)
    plan = compiler.preview_mode(
        mode_ref,
        run_id=preview_run_id,
        overrides=mode_overrides_for_execution_nodes(
            config,
            node_ids,
            complexity_selection=complexity_selection,
        ),
        resolve_assets=resolve_assets,
    )
    return runtime_selection_view_from_plan(
        plan,
        scope="preview",
        workspace_root=paths.root,
    ).model_copy(update={"complexity": complexity_selection})


def rebound_execution_parameters_for_mode(
    config: EngineConfig,
    paths: RuntimePaths,
    *,
    mode_ref: RegistryObjectRef,
    node_ids: tuple[str, ...] | list[str],
    preview_run_id: str,
    task_complexity: str | None = None,
) -> dict[str, BoundExecutionParameters]:
    """Resolve effective stage bindings for runtime rebinds via the compiler path."""

    complexity_selection = complexity_selection_for_execution_nodes(
        config,
        node_ids,
        task_complexity=task_complexity,
    )
    compiler = FrozenRunCompiler(paths)
    plan = compiler.preview_mode(
        mode_ref,
        run_id=preview_run_id,
        overrides=mode_overrides_for_execution_nodes(
            config,
            node_ids,
            complexity_selection=complexity_selection,
        ),
        resolve_assets=False,
    )
    execution_plan = plan.content.execution_plan
    if execution_plan is None:
        return {}
    return {
        stage.node_id: bound_execution_parameters_for_stage(stage)
        for stage in execution_plan.stages
    }


def compile_standard_runtime_selection(
    config: EngineConfig,
    paths: RuntimePaths,
    *,
    run_id: str,
    resolve_assets: bool = True,
) -> CompileResult:
    """Compile the packaged/workspace-standard execution selection for one run."""

    return compile_execution_runtime_selection(
        config,
        paths,
        run_id=run_id,
        size_latch="SMALL",
        current_status=None,
        resolve_assets=resolve_assets,
    )


def preview_standard_runtime_selection(
    config: EngineConfig,
    paths: RuntimePaths,
    *,
    preview_run_id: str,
    resolve_assets: bool = True,
) -> RuntimeSelectionView:
    """Compile and render a preview selection view for status/config reporting."""

    return preview_execution_runtime_selection(
        config,
        paths,
        preview_run_id=preview_run_id,
        size_latch="SMALL",
        current_status=None,
        resolve_assets=resolve_assets,
    )


def runtime_selection_view(
    result: CompileResult,
    *,
    scope: Literal["preview", "frozen_run"],
    workspace_root: Path,
) -> RuntimeSelectionView:
    """Build one operator-facing selection view from a successful compile result."""

    if result.status is not CompileStatus.OK or result.plan is None:
        raise RuntimeError("runtime selection views require a successful compile result")
    return runtime_selection_view_from_plan(result.plan, scope=scope, workspace_root=workspace_root)


def runtime_selection_view_from_snapshot(
    snapshot: CompileTimeResolvedSnapshot,
    *,
    workspace_root: Path,
) -> RuntimeSelectionView:
    """Build one operator-facing selection view from a persisted compile snapshot."""

    return runtime_selection_view_from_plan(
        FrozenRunPlan(
            run_id=snapshot.run_id,
            compiled_at=snapshot.created_at,
            content_hash=snapshot.frozen_plan.content_hash,
            content=snapshot.content,
            compile_diagnostics=snapshot.compile_diagnostics,
        ),
        scope="frozen_run",
        workspace_root=workspace_root,
    )


__all__ = [
    "RegistryObjectSelectionView",
    "RuntimeSelectionView",
    "LARGE_MODE_REF",
    "STANDARD_MODE_REF",
    "StageExecutionBindingView",
    "compile_execution_runtime_selection",
    "compile_standard_runtime_selection",
    "execution_mode_ref",
    "preview_execution_runtime_selection",
    "preview_standard_runtime_selection",
    "rebound_execution_parameters_for_mode",
    "runtime_selection_view",
    "runtime_selection_view_from_snapshot",
]
