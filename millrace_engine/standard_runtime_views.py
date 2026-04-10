"""Selection-view assembly helpers for standard runtime previews and snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .compiler_models import (
    CompileResult,
    CompileStatus,
    CompileTimeResolvedSnapshot,
    FrozenPlanSourceKind,
    FrozenRunPlan,
)
from .contracts import RegistryObjectRef, RegistrySourceKind, StageType
from .execution_nodes import execution_stage_type_for_node
from .materialization_models import ProvenanceLane
from .registry import RegistryDocument, discover_registry_state
from .standard_runtime_models import (
    RegistryObjectSelectionView,
    RuntimeSelectionView,
    StageExecutionBindingView,
)


@dataclass(frozen=True, slots=True)
class _ProvenanceSelectionFallback:
    title: str | None
    registry_layer: Literal["packaged", "workspace"] | None
    source_kind: RegistrySourceKind | None
    source_ref: str | None


def runtime_selection_view_from_plan(
    plan: FrozenRunPlan,
    *,
    scope: Literal["preview", "frozen_run"],
    workspace_root: Path,
) -> RuntimeSelectionView:
    documents: dict[tuple[str, str, str], RegistryDocument] | None = None
    provenance_fallbacks = provenance_selection_fallbacks(plan)
    source_refs = {
        source_ref.object_ref: source_ref
        for source_ref in plan.content.source_refs
        if source_ref.kind is FrozenPlanSourceKind.REGISTRY
    }
    content = plan.content

    def _documents() -> dict[tuple[str, str, str], RegistryDocument]:
        nonlocal documents
        if documents is None:
            try:
                discovery = discover_registry_state(workspace_root, validate_catalog=False)
            except RuntimeError:
                documents = {}
            else:
                documents = {document.key: document for document in discovery.effective}
        return documents

    def _fallback_view_for(ref: RegistryObjectRef) -> RegistryObjectSelectionView:
        return RegistryObjectSelectionView(
            ref=ref,
            title=ref.id,
            aliases=(),
            registry_layer=None,
            source_kind=None,
            source_ref=None,
        )

    def _view_for(ref: RegistryObjectRef | None) -> RegistryObjectSelectionView | None:
        if ref is None:
            return None
        frozen_source = source_refs.get(source_object_ref(ref))
        if frozen_source is not None:
            return RegistryObjectSelectionView(
                ref=ref,
                title=frozen_source.title or ref.id,
                aliases=frozen_source.aliases,
                registry_layer=registry_layer_value(frozen_source.source_layer),
                source_kind=frozen_source.registry_source_kind,
                source_ref=frozen_source.source_ref,
            )
        provenance_source = provenance_fallbacks.get(source_object_ref(ref))
        if provenance_source is not None:
            return RegistryObjectSelectionView(
                ref=ref,
                title=provenance_source.title or ref.id,
                aliases=(),
                registry_layer=provenance_source.registry_layer,
                source_kind=provenance_source.source_kind,
                source_ref=provenance_source.source_ref,
            )
        try:
            document = document_for_ref(_documents(), ref)
        except RuntimeError:
            return _fallback_view_for(ref)
        return RegistryObjectSelectionView(
            ref=ref,
            title=document.definition.title,
            aliases=document.definition.aliases,
            registry_layer=document.layer.value,
            source_kind=document.definition.source.kind,
            source_ref=document.definition.source.ref,
        )

    execution_plan = content.execution_plan
    stage_bindings: list[StageExecutionBindingView] = []
    if execution_plan is not None:
        for stage in execution_plan.stages:
            stage_kind_view = _view_for(stage.stage_kind_ref)
            if stage_kind_view is None:
                continue
            stage_bindings.append(
                StageExecutionBindingView(
                    node_id=stage.node_id,
                    stage=stage_type_for_node(stage.node_id),
                    kind_id=stage.kind_id,
                    stage_kind=stage_kind_view,
                    model_profile=_view_for(stage.model_profile_ref),
                    runner=stage.runner,
                    model=stage.model,
                    effort=stage.effort,
                    permission_profile=stage.permission_profile,
                    allow_search=stage.allow_search,
                    timeout_seconds=stage.timeout_seconds,
                    prompt_asset_ref=stage.prompt_asset_ref,
                    prompt_resolved_ref=(stage.prompt_asset.resolved_ref if stage.prompt_asset is not None else None),
                    prompt_source_kind=(
                        stage.prompt_asset.source_kind.value if stage.prompt_asset is not None else None
                    ),
                )
            )

    return RuntimeSelectionView(
        scope=scope,
        selection=_view_for(content.selection_ref) or _fallback_view_for(content.selection_ref),
        mode=_view_for(content.selected_mode_ref),
        execution_loop=_view_for(content.selected_execution_loop_ref),
        task_authoring_profile=_view_for(content.task_authoring_profile_ref),
        model_profile=_view_for(content.model_profile_ref),
        frozen_plan_id=plan.identity.plan_id,
        frozen_plan_hash=plan.identity.content_hash,
        run_id=(plan.run_id if scope == "frozen_run" else None),
        research_participation=content.research_participation.value,
        outline_policy=content.outline_policy,
        policy_toggles=content.policy_toggles,
        stage_bindings=tuple(stage_bindings),
    )


def runtime_selection_view_from_snapshot(
    snapshot: CompileTimeResolvedSnapshot,
    *,
    workspace_root: Path,
) -> RuntimeSelectionView:
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


def compile_error_message(result: CompileResult) -> str:
    if result.status is CompileStatus.OK:
        raise RuntimeError("compile_error_message only applies to failed compile results")
    if result.diagnostics:
        return "; ".join(diagnostic.message for diagnostic in result.diagnostics)
    return "standard runtime selection compile failed without diagnostics"


def document_for_ref(
    documents: dict[tuple[str, str, str], RegistryDocument],
    ref: RegistryObjectRef,
) -> RegistryDocument:
    key = (ref.kind.value, ref.id, ref.version)
    try:
        return documents[key]
    except KeyError as exc:
        raise RuntimeError(f"registry document missing for {ref.kind.value}:{ref.id}@{ref.version}") from exc


def stage_type_for_node(node_id: str) -> StageType | None:
    return execution_stage_type_for_node(node_id)


def source_object_ref(ref: RegistryObjectRef) -> str:
    return f"{ref.kind.value}:{ref.id}@{ref.version}"


def provenance_selection_fallbacks(plan: FrozenRunPlan) -> dict[str, _ProvenanceSelectionFallback]:
    fallbacks: dict[str, _ProvenanceSelectionFallback] = {}
    for frozen_loop in (plan.content.execution_plan, plan.content.research_plan):
        if frozen_loop is None:
            continue
        for entry in frozen_loop.provenance:
            if entry.lane is not ProvenanceLane.LOOKUP or entry.object_ref is None or not isinstance(entry.value, dict):
                continue
            if entry.object_ref in fallbacks:
                continue
            registry_layer = registry_layer_value(entry.value.get("registry_layer"))
            title = normalize_optional_text(entry.value.get("title"))
            source_ref = normalize_optional_text(entry.value.get("source_ref"))
            source_kind = (
                RegistrySourceKind.PACKAGED_DEFAULT
                if registry_layer == "packaged"
                else None
            )
            fallbacks[entry.object_ref] = _ProvenanceSelectionFallback(
                title=title,
                registry_layer=registry_layer,
                source_kind=source_kind,
                source_ref=source_ref,
            )
    return fallbacks


def registry_layer_value(value: str | None) -> Literal["packaged", "workspace"] | None:
    if value in {"packaged", "workspace"}:
        return value
    return None


def normalize_optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


__all__ = [
    "compile_error_message",
    "runtime_selection_view_from_plan",
    "runtime_selection_view_from_snapshot",
]
