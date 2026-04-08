"""Phase 01B compiler for immutable per-run frozen plans."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from .compiler_artifacts import (
    asset_source_ref as _asset_source_ref,
)
from .compiler_artifacts import (
    current_rebinding_value as _current_rebinding_value,
)
from .compiler_artifacts import (
    emit_compile_artifacts as _emit_compile_artifacts,
)
from .compiler_artifacts import (
    emit_failure_compile_artifacts as _emit_failure_artifacts,
)
from .compiler_artifacts import (
    has_error_diagnostics as _has_error_diagnostics,
)
from .compiler_artifacts import (
    plan_hash as _plan_hash,
)
from .compiler_artifacts import (
    reachable_nodes as _reachable_nodes,
)
from .compiler_artifacts import (
    ref_string as _ref_string,
)
from .compiler_artifacts import (
    render_json as _render_json,
)
from .compiler_artifacts import (
    sha256_text as _sha256_text,
)
from .compiler_artifacts import (
    sorted_diagnostics as _sorted_diagnostics,
)
from .compiler_models import (
    COMPILER_VERSION,
    PLAN_SCHEMA_VERSION,
    RESOLVED_SNAPSHOT_SCHEMA_VERSION,
    CompileArtifacts,
    CompileDiagnosticsArtifact,
    CompilePhase,
    CompilerDiagnostic,
    CompileResult,
    CompileStatus,
    CompileTimeResolvedSnapshot,
    DiagnosticSeverity,
    FrozenLoopPlan,
    FrozenParameterRebindingRule,
    FrozenPlanSourceKind,
    FrozenPlanSourceRef,
    FrozenResumeState,
    FrozenRunPlan,
    FrozenRunPlanContent,
    FrozenStagePlan,
    FrozenTransition,
)
from .compiler_rebinding import (
    FrozenExecutionParameterBinder,
    ParameterRebindingError,
    resolved_snapshot_id_for_run,
)
from .compiler_rendering import (
    render_compile_time_resolved_snapshot_markdown,
    render_frozen_run_plan_markdown,
)
from .contracts import (
    ArtifactPersistence,
    ControlPlane,
    LoopEdge,
    LoopEdgeKind,
    LoopTerminalState,
    PersistedObjectKind,
    RegisteredStageKindDefinition,
    RegistryObjectRef,
    StageIdempotencePolicy,
    StageOverrideField,
)
from .materialization import (
    ArchitectureMaterializer,
    LoopMaterializationOverrides,
    MaterializationError,
    MaterializedLoop,
    MaterializedMode,
    MaterializedStageBinding,
    ModeMaterializationOverrides,
)
from .paths import RuntimePaths
from .provenance import runtime_rebindable_stage_fields
from .registry import RegistryDocument, discover_registry_state


class FrozenRunCompiler:
    """Compile one selected loop or mode into an immutable per-run frozen plan."""

    def __init__(
        self,
        paths: RuntimePaths,
        *,
        materializer: ArchitectureMaterializer | None = None,
    ) -> None:
        self.paths = paths
        self.materializer = materializer or ArchitectureMaterializer(
            paths.root,
            discovery=discover_registry_state(paths.root, validate_catalog=False),
        )
        self._documents_by_key = {document.key: document for document in self.materializer.discovery.effective}

    def compile_loop(
        self,
        loop_ref: RegistryObjectRef,
        *,
        run_id: str,
        overrides: LoopMaterializationOverrides | None = None,
        resolve_assets: bool = True,
    ) -> CompileResult:
        if loop_ref.kind is not PersistedObjectKind.LOOP_CONFIG:
            raise ValueError("compile_loop requires a loop_config registry ref")
        return self._compile(
            selection_ref=loop_ref,
            run_id=run_id,
            materialize=lambda: self.materializer.materialize_loop(
                loop_ref,
                overrides=overrides,
                resolve_assets=resolve_assets,
            ),
        )

    def compile_mode(
        self,
        mode_ref: RegistryObjectRef,
        *,
        run_id: str,
        overrides: ModeMaterializationOverrides | None = None,
        resolve_assets: bool = True,
    ) -> CompileResult:
        if mode_ref.kind is not PersistedObjectKind.MODE:
            raise ValueError("compile_mode requires a mode registry ref")
        return self._compile(
            selection_ref=mode_ref,
            run_id=run_id,
            materialize=lambda: self.materializer.materialize_mode(
                mode_ref,
                overrides=overrides,
                resolve_assets=resolve_assets,
            ),
        )

    def preview_loop(
        self,
        loop_ref: RegistryObjectRef,
        *,
        run_id: str,
        overrides: LoopMaterializationOverrides | None = None,
        resolve_assets: bool = True,
    ) -> FrozenRunPlan:
        """Build an in-memory preview frozen plan without emitting run artifacts."""

        if loop_ref.kind is not PersistedObjectKind.LOOP_CONFIG:
            raise ValueError("preview_loop requires a loop_config registry ref")
        return self._preview(
            selection_ref=loop_ref,
            run_id=run_id,
            materialize=lambda: self.materializer.materialize_loop(
                loop_ref,
                overrides=overrides,
                resolve_assets=resolve_assets,
            ),
        )

    def preview_mode(
        self,
        mode_ref: RegistryObjectRef,
        *,
        run_id: str,
        overrides: ModeMaterializationOverrides | None = None,
        resolve_assets: bool = True,
    ) -> FrozenRunPlan:
        """Build an in-memory preview frozen plan without emitting run artifacts."""

        if mode_ref.kind is not PersistedObjectKind.MODE:
            raise ValueError("preview_mode requires a mode registry ref")
        return self._preview(
            selection_ref=mode_ref,
            run_id=run_id,
            materialize=lambda: self.materializer.materialize_mode(
                mode_ref,
                overrides=overrides,
                resolve_assets=resolve_assets,
            ),
        )

    def _compile(
        self,
        *,
        selection_ref: RegistryObjectRef,
        run_id: str,
        materialize: Any,
    ) -> CompileResult:
        try:
            materialized = materialize()
        except (MaterializationError, FileNotFoundError, ValueError) as exc:
            diagnostics = (
                CompilerDiagnostic(
                    code="MATERIALIZATION_ERROR",
                    phase=CompilePhase.MATERIALIZE,
                    path="selection",
                    message=str(exc),
                    object_ref=_ref_string(selection_ref),
                ),
            )
            return self._failure_result(
                selection_ref=selection_ref,
                run_id=run_id,
                diagnostics=diagnostics,
                artifacts=self._emit_failure_artifacts(
                    selection_ref=selection_ref,
                    run_id=run_id,
                    diagnostics=diagnostics,
                ),
            )

        plan_content, diagnostics = self._freeze_content(
            selection_ref=selection_ref,
            materialized=materialized,
        )
        if _has_error_diagnostics(diagnostics):
            return self._failure_result(
                selection_ref=selection_ref,
                run_id=run_id,
                diagnostics=diagnostics,
                artifacts=self._emit_failure_artifacts(
                    selection_ref=selection_ref,
                    run_id=run_id,
                    diagnostics=diagnostics,
                ),
            )

        plan = FrozenRunPlan(
            run_id=run_id,
            compiled_at=datetime.now(timezone.utc),
            content_hash=_plan_hash(plan_content),
            content=plan_content,
            compile_diagnostics=diagnostics,
        )
        snapshot = self._build_resolved_snapshot(plan)

        try:
            artifacts = self._emit_artifacts(
                run_id=run_id,
                selection_ref=selection_ref,
                diagnostics=diagnostics,
                result=CompileStatus.OK,
                plan=plan,
                snapshot=snapshot,
            )
        except OSError as exc:
            return self._failure_result(
                selection_ref=selection_ref,
                run_id=run_id,
                diagnostics=_sorted_diagnostics(
                    diagnostics
                    + (
                        CompilerDiagnostic(
                            code="ARTIFACT_EMIT_ERROR",
                            phase=CompilePhase.EMIT,
                            path="artifacts",
                            message=str(exc),
                            object_ref=_ref_string(selection_ref),
                        ),
                    )
                ),
            )

        return CompileResult(
            status=CompileStatus.OK,
            selection_ref=selection_ref,
            run_id=run_id,
            diagnostics=diagnostics,
            plan=plan,
            snapshot=snapshot,
            artifacts=artifacts,
        )

    def _preview(
        self,
        *,
        selection_ref: RegistryObjectRef,
        run_id: str,
        materialize: Any,
    ) -> FrozenRunPlan:
        try:
            materialized = materialize()
        except (MaterializationError, FileNotFoundError, ValueError) as exc:
            raise RuntimeError(str(exc)) from exc

        plan_content, diagnostics = self._freeze_content(
            selection_ref=selection_ref,
            materialized=materialized,
        )
        if _has_error_diagnostics(diagnostics):
            message = "; ".join(diagnostic.message for diagnostic in diagnostics)
            raise RuntimeError(message or "preview compile failed without diagnostics")
        return FrozenRunPlan(
            run_id=run_id,
            compiled_at=datetime.now(timezone.utc),
            content_hash=_plan_hash(plan_content),
            content=plan_content,
            compile_diagnostics=diagnostics,
        )

    def _freeze_content(
        self,
        *,
        selection_ref: RegistryObjectRef,
        materialized: MaterializedLoop | MaterializedMode,
    ) -> tuple[FrozenRunPlanContent, tuple[CompilerDiagnostic, ...]]:
        diagnostics: list[CompilerDiagnostic] = []
        source_refs: list[FrozenPlanSourceRef] = []
        source_ref_map: dict[tuple[str, str], FrozenPlanSourceRef] = {}
        parameter_rebinding_rules: list[FrozenParameterRebindingRule] = []

        if isinstance(materialized, MaterializedMode):
            mode_source_ref = self._registry_source_ref(materialized.mode_binding.resolved_ref)
            source_ref_map[(mode_source_ref.kind.value, mode_source_ref.object_ref)] = mode_source_ref
            execution_plan, execution_diagnostics = self._freeze_loop_plan(materialized.execution_loop)
            diagnostics.extend(execution_diagnostics)
            source_ref_map.update(self._collect_loop_sources(materialized.execution_loop))
            parameter_rebinding_rules.extend(self._parameter_rebinding_rules(materialized.execution_loop))
            research_plan: FrozenLoopPlan | None = None
            if materialized.research_loop is not None:
                research_plan, research_diagnostics = self._freeze_loop_plan(materialized.research_loop)
                diagnostics.extend(research_diagnostics)
                source_ref_map.update(self._collect_loop_sources(materialized.research_loop))
                parameter_rebinding_rules.extend(self._parameter_rebinding_rules(materialized.research_loop))
            plan_content = FrozenRunPlanContent(
                selection_ref=selection_ref,
                selected_mode_ref=materialized.mode_binding.resolved_ref,
                selected_execution_loop_ref=materialized.execution_loop.requested_ref,
                selected_research_loop_ref=(
                    materialized.research_loop.requested_ref if materialized.research_loop is not None else None
                ),
                task_authoring_profile_ref=materialized.task_authoring_profile_ref,
                model_profile_ref=materialized.model_profile_ref,
                research_participation=materialized.research_participation,
                outline_policy=materialized.outline_policy,
                policy_toggles=materialized.policy_toggles,
                execution_plan=execution_plan,
                research_plan=research_plan,
                parameter_rebinding_rules=(),
                source_refs=(),
            )
        else:
            frozen_loop_plan, loop_diagnostics = self._freeze_loop_plan(materialized)
            diagnostics.extend(loop_diagnostics)
            source_ref_map.update(self._collect_loop_sources(materialized))
            parameter_rebinding_rules.extend(self._parameter_rebinding_rules(materialized))
            if materialized.materialized_definition.payload.plane is ControlPlane.EXECUTION:
                execution_plan = frozen_loop_plan
                research_plan = None
                selected_execution_loop_ref = materialized.requested_ref
                selected_research_loop_ref = None
            else:
                execution_plan = None
                research_plan = frozen_loop_plan
                selected_execution_loop_ref = None
                selected_research_loop_ref = materialized.requested_ref
            plan_content = FrozenRunPlanContent(
                selection_ref=selection_ref,
                selected_execution_loop_ref=selected_execution_loop_ref,
                selected_research_loop_ref=selected_research_loop_ref,
                task_authoring_profile_ref=materialized.task_authoring_profile_ref,
                model_profile_ref=materialized.model_profile_ref,
                outline_policy=materialized.materialized_definition.payload.outline_policy,
                execution_plan=execution_plan,
                research_plan=research_plan,
                parameter_rebinding_rules=(),
                source_refs=(),
            )

        source_refs.extend(source_ref_map.values())
        source_refs.sort(key=lambda item: (item.kind.value, item.object_ref, item.source_layer, item.source_ref or ""))
        content_payload = plan_content.model_dump(mode="python")
        content_payload["parameter_rebinding_rules"] = tuple(
            sorted(
                parameter_rebinding_rules,
                key=lambda item: (item.plane.value, item.node_id, item.field.value),
            )
        )
        content_payload["source_refs"] = tuple(source_refs)
        return (
            FrozenRunPlanContent.model_validate(content_payload),
            _sorted_diagnostics(tuple(diagnostics)),
        )

    def _freeze_loop_plan(
        self,
        loop: MaterializedLoop,
    ) -> tuple[FrozenLoopPlan, tuple[CompilerDiagnostic, ...]]:
        definition = loop.materialized_definition
        loop_ref = _ref_string(loop.requested_ref)
        stage_kind_map: dict[str, RegisteredStageKindDefinition] = {}
        stages: list[FrozenStagePlan] = []
        diagnostics: list[CompilerDiagnostic] = []

        for stage_binding in loop.stage_bindings:
            stage_kind = self._stage_kind_for_binding(stage_binding)
            stage_kind_map[stage_binding.node_id] = stage_kind
            runtime_bundle_outputs = tuple(
                artifact.name
                for artifact in stage_kind.payload.output_artifacts
                if artifact.persistence is ArtifactPersistence.RUNTIME_BUNDLE
            )
            stages.append(
                FrozenStagePlan(
                    plane=stage_binding.plane,
                    node_id=stage_binding.node_id,
                    kind_id=stage_binding.kind_id,
                    stage_kind_ref=stage_binding.stage_kind_binding.resolved_ref,
                    handler_ref=stage_kind.payload.handler_ref,
                    model_profile_ref=stage_binding.model_profile_ref,
                    runner=stage_binding.runner,
                    model=stage_binding.model,
                    effort=stage_binding.effort,
                    allow_search=stage_binding.allow_search,
                    prompt_asset_ref=stage_binding.prompt_asset_ref,
                    timeout_seconds=stage_binding.timeout_seconds,
                    prompt_asset=stage_binding.prompt_asset,
                    running_status=stage_kind.payload.running_status,
                    terminal_statuses=stage_kind.payload.terminal_statuses,
                    success_statuses=stage_kind.payload.success_statuses,
                    routing_outcomes=stage_kind.payload.routing_outcomes,
                    input_artifacts=stage_kind.payload.input_artifacts,
                    output_artifacts=stage_kind.payload.output_artifacts,
                    idempotence_policy=stage_kind.payload.idempotence_policy,
                    retry_max_attempts=stage_kind.payload.retry_policy.max_attempts,
                    retry_exhausted_outcome=stage_kind.payload.retry_policy.exhausted_outcome,
                    runtime_bundle_outputs=runtime_bundle_outputs,
                )
            )
            if stage_kind.payload.plane is not definition.payload.plane:
                diagnostics.append(
                    CompilerDiagnostic(
                        code="STAGE_PLANE_MISMATCH",
                        phase=CompilePhase.VALIDATE,
                        path=f"stage.{stage_binding.node_id}.plane",
                        message=(
                            f"stage {stage_binding.node_id} resolves to {stage_kind.payload.plane.value}, "
                            f"but loop {definition.id} owns {definition.payload.plane.value}"
                        ),
                        object_ref=loop_ref,
                        source_ref=_ref_string(stage_binding.stage_kind_binding.resolved_ref),
                    )
                )

        stage_map = {stage.node_id: stage for stage in stages}
        outgoing_edges: dict[str, list[LoopEdge]] = defaultdict(list)
        incoming_terminal_edges: dict[str, list[LoopEdge]] = defaultdict(list)
        adjacency: dict[str, set[str]] = defaultdict(set)
        terminal_state_map = {
            terminal_state.terminal_state_id: terminal_state for terminal_state in definition.payload.terminal_states
        }
        transitions: list[FrozenTransition] = []

        for edge in definition.payload.edges:
            outgoing_edges[edge.from_node_id].append(edge)
            if edge.to_node_id is not None:
                adjacency[edge.from_node_id].add(edge.to_node_id)
            if edge.terminal_state_id is not None:
                incoming_terminal_edges[edge.terminal_state_id].append(edge)
            terminal_status = None
            if edge.terminal_state_id is not None:
                terminal_status = terminal_state_map[edge.terminal_state_id].writes_status
            transitions.append(
                FrozenTransition(
                    edge_id=edge.edge_id,
                    from_node_id=edge.from_node_id,
                    to_node_id=edge.to_node_id,
                    terminal_state_id=edge.terminal_state_id,
                    terminal_status=terminal_status,
                    on_outcomes=edge.on_outcomes,
                    kind=edge.kind,
                    priority=edge.priority,
                    max_attempts=edge.max_attempts,
                    condition=(
                        edge.condition.model_dump(mode="json") if edge.condition is not None else None
                    ),
                )
            )
            stage_plan = stage_map[edge.from_node_id]
            legal_triggers = set(stage_plan.routing_outcomes) | set(stage_plan.terminal_statuses)
            unsupported = sorted(trigger for trigger in edge.on_outcomes if trigger not in legal_triggers)
            if unsupported:
                diagnostics.append(
                    CompilerDiagnostic(
                        code="EDGE_OUTCOME_UNDECLARED",
                        phase=CompilePhase.VALIDATE,
                        path=f"edge.{edge.edge_id}.on_outcomes",
                        message=(
                            f"edge {edge.edge_id} references undeclared triggers for stage {stage_plan.node_id}: "
                            f"{', '.join(unsupported)}"
                        ),
                        object_ref=loop_ref,
                        source_ref=_ref_string(stage_plan.stage_kind_ref),
                    )
                )
            if edge.kind is LoopEdgeKind.RETRY:
                if edge.to_node_id != stage_plan.node_id or edge.terminal_state_id is not None:
                    diagnostics.append(
                        CompilerDiagnostic(
                            code="RETRY_EDGE_NOT_SELF_LOOP",
                            phase=CompilePhase.VALIDATE,
                            path=f"edge.{edge.edge_id}",
                            message=(
                                f"retry edge {edge.edge_id} must target its originating stage {stage_plan.node_id}"
                            ),
                            object_ref=loop_ref,
                            source_ref=_ref_string(stage_plan.stage_kind_ref),
                        )
                    )
                if stage_plan.idempotence_policy is StageIdempotencePolicy.SINGLE_ATTEMPT_ONLY:
                    diagnostics.append(
                        CompilerDiagnostic(
                            code="RETRY_DISALLOWED_BY_STAGE_KIND",
                            phase=CompilePhase.VALIDATE,
                            path=f"edge.{edge.edge_id}.max_attempts",
                            message=(
                                f"retry edge {edge.edge_id} targets stage {stage_plan.node_id}, "
                                "which is marked single_attempt_only"
                            ),
                            object_ref=loop_ref,
                            source_ref=_ref_string(stage_plan.stage_kind_ref),
                        )
                    )
                if edge.max_attempts is not None and edge.max_attempts > stage_plan.retry_max_attempts:
                    diagnostics.append(
                        CompilerDiagnostic(
                            code="RETRY_ATTEMPTS_EXCEED_STAGE_KIND",
                            phase=CompilePhase.VALIDATE,
                            path=f"edge.{edge.edge_id}.max_attempts",
                            message=(
                                f"retry edge {edge.edge_id} declares {edge.max_attempts} attempts, "
                                f"but stage {stage_plan.node_id} allows at most {stage_plan.retry_max_attempts}"
                            ),
                            object_ref=loop_ref,
                            source_ref=_ref_string(stage_plan.stage_kind_ref),
                        )
                    )
            if edge.terminal_state_id is not None:
                terminal_state = terminal_state_map[edge.terminal_state_id]
                if terminal_state.writes_status not in stage_plan.terminal_statuses:
                    diagnostics.append(
                        CompilerDiagnostic(
                            code="TERMINAL_STATUS_UNSUPPORTED",
                            phase=CompilePhase.VALIDATE,
                            path=f"terminal_state.{terminal_state.terminal_state_id}.writes_status",
                            message=(
                                f"terminal state {terminal_state.terminal_state_id} writes {terminal_state.writes_status}, "
                                f"which stage {stage_plan.node_id} does not declare"
                            ),
                            object_ref=loop_ref,
                            source_ref=_ref_string(stage_plan.stage_kind_ref),
                        )
                    )

        reachable_nodes = _reachable_nodes(
            entry_node_id=definition.payload.entry_node_id,
            adjacency=adjacency,
        )
        for stage_plan in sorted(stages, key=lambda item: item.node_id):
            if stage_plan.node_id not in reachable_nodes:
                diagnostics.append(
                    CompilerDiagnostic(
                        code="GRAPH_UNREACHABLE_STAGE",
                        phase=CompilePhase.VALIDATE,
                        path=f"stage.{stage_plan.node_id}",
                        message=f"stage {stage_plan.node_id} is unreachable from entry node {definition.payload.entry_node_id}",
                        object_ref=loop_ref,
                        source_ref=_ref_string(stage_plan.stage_kind_ref),
                    )
                )
            if not outgoing_edges.get(stage_plan.node_id):
                diagnostics.append(
                    CompilerDiagnostic(
                        code="NODE_MISSING_OUTGOING_EDGE",
                        phase=CompilePhase.VALIDATE,
                        path=f"stage.{stage_plan.node_id}.edges",
                        message=f"stage {stage_plan.node_id} has no outgoing edges",
                        object_ref=loop_ref,
                        source_ref=_ref_string(stage_plan.stage_kind_ref),
                    )
                )

        terminal_status_map: dict[str, LoopTerminalState] = {}
        duplicate_statuses: set[str] = set()
        for terminal_state in definition.payload.terminal_states:
            if terminal_state.writes_status in terminal_status_map:
                duplicate_statuses.add(terminal_state.writes_status)
            else:
                terminal_status_map[terminal_state.writes_status] = terminal_state
        for status in sorted(duplicate_statuses):
            diagnostics.append(
                CompilerDiagnostic(
                    code="DUPLICATE_TERMINAL_STATUS",
                    phase=CompilePhase.VALIDATE,
                    path="terminal_states",
                    message=f"multiple terminal states write status {status}",
                    object_ref=loop_ref,
                )
            )

        resume_states = tuple(
            FrozenResumeState(
                status=terminal_state.writes_status,
                terminal_state_id=terminal_state.terminal_state_id,
                terminal_class=terminal_state.terminal_class,
            )
            for terminal_state in sorted(
                definition.payload.terminal_states,
                key=lambda item: (item.writes_status, item.terminal_state_id),
            )
        )
        frozen_loop = FrozenLoopPlan(
            requested_ref=loop.requested_ref,
            resolved_ref=loop.loop_binding.resolved_ref if loop.loop_binding is not None else None,
            parent_ref=loop.parent_binding.resolved_ref if loop.parent_binding is not None else None,
            plane=definition.payload.plane,
            entry_node_id=definition.payload.entry_node_id,
            task_authoring_profile_ref=loop.task_authoring_profile_ref,
            model_profile_ref=loop.model_profile_ref,
            outline_policy=definition.payload.outline_policy,
            stages=tuple(sorted(stages, key=lambda item: item.node_id)),
            transitions=tuple(sorted(transitions, key=lambda item: item.edge_id)),
            terminal_states=tuple(
                sorted(definition.payload.terminal_states, key=lambda item: item.terminal_state_id)
            ),
            resume_states=resume_states,
            provenance=tuple(
                loop.provenance[key] for key in sorted(loop.provenance)
            ),
        )
        return frozen_loop, _sorted_diagnostics(tuple(diagnostics))

    def _build_resolved_snapshot(self, plan: FrozenRunPlan) -> CompileTimeResolvedSnapshot:
        return CompileTimeResolvedSnapshot(
            snapshot_id=resolved_snapshot_id_for_run(plan.run_id, plan.content_hash),
            created_at=plan.compiled_at,
            run_id=plan.run_id,
            selection_ref=plan.content.selection_ref,
            frozen_plan=plan.identity,
            content=plan.content,
            compile_diagnostics=plan.compile_diagnostics,
        )

    def _emit_artifacts(
        self,
        *,
        run_id: str,
        selection_ref: RegistryObjectRef,
        diagnostics: tuple[CompilerDiagnostic, ...],
        result: CompileStatus,
        plan: FrozenRunPlan | None = None,
        snapshot: CompileTimeResolvedSnapshot | None = None,
    ) -> CompileArtifacts:
        return _emit_compile_artifacts(
            self.paths,
            run_id=run_id,
            selection_ref=selection_ref,
            diagnostics=diagnostics,
            result=result,
            plan=plan,
            snapshot=snapshot,
        )

    def _emit_failure_artifacts(
        self,
        *,
        selection_ref: RegistryObjectRef,
        run_id: str,
        diagnostics: tuple[CompilerDiagnostic, ...],
    ) -> CompileArtifacts | None:
        return _emit_failure_artifacts(
            self.paths,
            selection_ref=selection_ref,
            run_id=run_id,
            diagnostics=diagnostics,
        )

    def _failure_result(
        self,
        *,
        selection_ref: RegistryObjectRef,
        run_id: str,
        diagnostics: tuple[CompilerDiagnostic, ...],
        artifacts: CompileArtifacts | None = None,
    ) -> CompileResult:
        return CompileResult(
            status=CompileStatus.FAIL,
            selection_ref=selection_ref,
            run_id=run_id,
            diagnostics=_sorted_diagnostics(diagnostics),
            artifacts=artifacts,
        )

    def _parameter_rebinding_rules(
        self,
        loop: MaterializedLoop,
    ) -> tuple[FrozenParameterRebindingRule, ...]:
        rules: list[FrozenParameterRebindingRule] = []
        for stage_binding in loop.stage_bindings:
            stage_kind = self._stage_kind_for_binding(stage_binding)
            for field in sorted(runtime_rebindable_stage_fields(stage_kind.payload.allowed_overrides), key=lambda item: item.value):
                rules.append(
                    FrozenParameterRebindingRule(
                        plane=stage_binding.plane,
                        node_id=stage_binding.node_id,
                        kind_id=stage_binding.kind_id,
                        field=field,
                        current_value=_current_rebinding_value(stage_binding, field),
                        stage_kind_ref=stage_binding.stage_kind_binding.resolved_ref,
                    )
                )
        return tuple(rules)

    def _collect_loop_sources(
        self,
        loop: MaterializedLoop,
    ) -> dict[tuple[str, str], FrozenPlanSourceRef]:
        entries: dict[tuple[str, str], FrozenPlanSourceRef] = {}
        if loop.loop_binding is not None:
            source_ref = self._registry_source_ref(loop.loop_binding.resolved_ref)
            entries[(source_ref.kind.value, source_ref.object_ref)] = source_ref
        if loop.parent_binding is not None:
            source_ref = self._registry_source_ref(loop.parent_binding.resolved_ref)
            entries[(source_ref.kind.value, source_ref.object_ref)] = source_ref
        if loop.task_authoring_profile_binding is not None:
            source_ref = self._registry_source_ref(loop.task_authoring_profile_binding.resolved_ref)
            entries[(source_ref.kind.value, source_ref.object_ref)] = source_ref
        if loop.model_profile_binding is not None:
            source_ref = self._registry_source_ref(loop.model_profile_binding.resolved_ref)
            entries[(source_ref.kind.value, source_ref.object_ref)] = source_ref
        for stage_binding in loop.stage_bindings:
            stage_kind_ref = self._registry_source_ref(stage_binding.stage_kind_binding.resolved_ref)
            entries[(stage_kind_ref.kind.value, stage_kind_ref.object_ref)] = stage_kind_ref
            if stage_binding.model_profile_binding is not None:
                model_profile_ref = self._registry_source_ref(stage_binding.model_profile_binding.resolved_ref)
                entries[(model_profile_ref.kind.value, model_profile_ref.object_ref)] = model_profile_ref
            if stage_binding.prompt_asset is not None:
                asset_source_ref = _asset_source_ref(stage_binding.prompt_asset)
                entries[(asset_source_ref.kind.value, asset_source_ref.object_ref)] = asset_source_ref
        return entries

    def _registry_source_ref(self, ref: RegistryObjectRef) -> FrozenPlanSourceRef:
        document = self._document_for_ref(ref)
        payload = document.definition.model_dump(mode="json")
        return FrozenPlanSourceRef(
            kind=FrozenPlanSourceKind.REGISTRY,
            object_ref=_ref_string(ref),
            title=document.definition.title,
            aliases=document.definition.aliases,
            registry_source_kind=document.definition.source.kind,
            source_ref=document.definition.source.ref or document.json_relative_path.as_posix(),
            source_layer=document.layer.value,
            sha256=_sha256_text(_render_json(payload)),
        )

    def _document_for_ref(self, ref: RegistryObjectRef) -> RegistryDocument:
        key = (ref.kind.value, ref.id, ref.version)
        try:
            return self._documents_by_key[key]
        except KeyError as exc:
            raise RuntimeError(f"registry document missing for {ref.kind.value}:{ref.id}@{ref.version}") from exc

    def _stage_kind_for_binding(self, binding: MaterializedStageBinding) -> RegisteredStageKindDefinition:
        document = self._document_for_ref(binding.stage_kind_binding.resolved_ref)
        definition = document.definition
        if not isinstance(definition, RegisteredStageKindDefinition):
            raise RuntimeError(f"{binding.kind_id} did not resolve to a registered stage kind definition")
        return definition


__all__ = [
    "COMPILER_VERSION",
    "PLAN_SCHEMA_VERSION",
    "RESOLVED_SNAPSHOT_SCHEMA_VERSION",
    "CompileArtifacts",
    "CompileDiagnosticsArtifact",
    "CompilePhase",
    "CompileResult",
    "CompileTimeResolvedSnapshot",
    "CompileStatus",
    "CompilerDiagnostic",
    "DiagnosticSeverity",
    "FrozenExecutionParameterBinder",
    "FrozenLoopPlan",
    "FrozenParameterRebindingRule",
    "FrozenPlanSourceKind",
    "FrozenPlanSourceRef",
    "FrozenResumeState",
    "FrozenRunCompiler",
    "FrozenRunPlan",
    "FrozenRunPlanContent",
    "FrozenStagePlan",
    "FrozenTransition",
    "ParameterRebindingError",
    "render_compile_time_resolved_snapshot_markdown",
    "render_frozen_run_plan_markdown",
]
