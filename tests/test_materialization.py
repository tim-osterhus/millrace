from __future__ import annotations

from pathlib import Path

import pytest

from millrace_engine.contracts import (
    ControlPlane,
    LoopConfigDefinition,
    ModeDefinition,
    ModelProfileDefinition,
    PersistedObjectKind,
    RegisteredStageKindDefinition,
    RegistryObjectRef,
    TaskAuthoringProfileDefinition,
)
from millrace_engine.materialization import (
    ArchitectureMaterializer,
    LoopMaterializationOverrides,
    MaterializationError,
    ModeMaterializationOverrides,
    ProvenanceSource,
    StageInvocationOverride,
)
from millrace_engine.registry import RegistryLayer, discover_registry_state, persist_workspace_registry_object


def _ref(kind: PersistedObjectKind, object_id: str, version: str = "1.0.0") -> RegistryObjectRef:
    return RegistryObjectRef(kind=kind, id=object_id, version=version)


def _stage_kind_definition(
    *,
    kind_id: str,
    plane: str = "execution",
    allowed_overrides: tuple[str, ...] = ("model_profile_ref", "allow_search"),
) -> RegisteredStageKindDefinition:
    suffix = kind_id.split(".")[-1].upper()
    return RegisteredStageKindDefinition.model_validate(
        {
            "id": kind_id,
            "version": "1.0.0",
            "tier": "golden",
            "title": f"{kind_id} stage",
            "summary": "Workspace stage kind for materialization tests.",
            "source": {"kind": "workspace_defined"},
            "payload": {
                "kind_id": kind_id,
                "contract_version": "1.0.0",
                "plane": plane,
                "handler_ref": f"millrace_engine.stages.{kind_id.split('.')[-1]}:Stage",
                "context_schema_ref": f"{kind_id}.context.v1",
                "result_schema_ref": f"{kind_id}.result.v1",
                "running_status": f"{suffix}_RUNNING",
                "terminal_statuses": (f"{suffix}_COMPLETE", "BLOCKED"),
                "success_statuses": (f"{suffix}_COMPLETE",),
                "input_artifacts": (
                    {
                        "name": "task_card" if plane == "execution" else "spec",
                        "kind": "task_card" if plane == "execution" else "spec",
                        "required": True,
                        "multiplicity": "one",
                    },
                ),
                "output_artifacts": (
                    {
                        "name": "stage_summary",
                        "kind": "stage_summary",
                        "required_on": ("success", f"{suffix}_COMPLETE"),
                        "persistence": "history",
                    },
                ),
                "idempotence_policy": "retry_safe_with_key",
                "retry_policy": {"max_attempts": 1, "backoff_seconds": 0, "exhausted_outcome": "blocked"},
                "queue_mutation_policy": "runtime_only",
                "routing_outcomes": ("success", "blocked"),
                "legal_predecessors": (),
                "legal_successors": (),
                "allowed_overrides": allowed_overrides,
            },
        }
    )


def _task_profile_definition(object_id: str) -> TaskAuthoringProfileDefinition:
    return TaskAuthoringProfileDefinition.model_validate(
        {
            "id": object_id,
            "version": "1.0.0",
            "tier": "golden",
            "title": f"{object_id} task profile",
            "summary": "Workspace task-authoring profile for materialization tests.",
            "source": {"kind": "workspace_defined"},
            "payload": {
                "decomposition_style": "narrow",
                "expected_card_count": {"min_cards": 1, "max_cards": 3},
                "allowed_task_breadth": "focused",
                "required_metadata_fields": ("spec_id",),
                "acceptance_profile": "standard",
                "gate_strictness": "standard",
                "single_card_synthesis_allowed": False,
                "research_assumption": "consult_if_ambiguous",
                "suitable_use_cases": ("Targeted changes",),
            },
        }
    )


def _binding(
    *,
    runner: str = "codex",
    model: str,
    effort: str | None = None,
    allow_search: bool = False,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "runner": runner,
        "model": model,
        "allow_search": allow_search,
    }
    if effort is not None:
        payload["effort"] = effort
    return payload


def _model_profile_definition(
    object_id: str,
    *,
    default_binding: dict[str, object],
    scoped_defaults: tuple[dict[str, object], ...] = (),
    stage_overrides: tuple[dict[str, object], ...] = (),
) -> ModelProfileDefinition:
    return ModelProfileDefinition.model_validate(
        {
            "id": object_id,
            "version": "1.0.0",
            "tier": "golden",
            "title": f"{object_id} model profile",
            "summary": "Workspace model profile for materialization tests.",
            "source": {"kind": "workspace_defined"},
            "payload": {
                "default_binding": default_binding,
                "scoped_defaults": scoped_defaults,
                "stage_overrides": stage_overrides,
            },
        }
    )


def _loop_definition(
    *,
    object_id: str,
    stage_kind_id: str,
    plane: str = "execution",
    extends: RegistryObjectRef | None = None,
    task_profile_ref: RegistryObjectRef | None = None,
    include_task_required: bool | None = None,
    model_profile_ref: RegistryObjectRef | None = None,
    outline_policy: dict[str, object] | None = None,
    node_overrides: dict[str, object] | None = None,
    node_id: str = "build",
) -> LoopConfigDefinition:
    payload: dict[str, object] = {
        "plane": plane,
        "nodes": (
            {
                "node_id": node_id,
                "kind_id": stage_kind_id,
                "overrides": node_overrides or {},
            },
        ),
        "edges": (
            {
                "edge_id": f"{node_id}_to_complete",
                "from_node_id": node_id,
                "terminal_state_id": "complete",
                "on_outcomes": ("success",),
                "kind": "terminal",
            },
        ),
        "entry_node_id": node_id,
        "terminal_states": (
            {
                "terminal_state_id": "complete",
                "terminal_class": "success",
                "writes_status": f"{stage_kind_id.split('.')[-1].upper()}_COMPLETE",
                "emits_artifacts": ("stage_summary",),
                "ends_plane_run": True,
            },
        ),
    }
    if task_profile_ref is not None:
        payload["task_authoring_profile_ref"] = task_profile_ref.model_dump(mode="json")
    if include_task_required is not None:
        payload["task_authoring_required"] = include_task_required
    if model_profile_ref is not None:
        payload["model_profile_ref"] = model_profile_ref.model_dump(mode="json")
    if outline_policy is not None:
        payload["outline_policy"] = outline_policy

    definition: dict[str, object] = {
        "id": object_id,
        "version": "1.0.0",
        "tier": "golden",
        "title": f"{object_id} loop",
        "summary": "Workspace loop for materialization tests.",
        "source": {"kind": "workspace_defined"},
        "payload": payload,
    }
    if extends is not None:
        definition["extends"] = extends.model_dump(mode="json")
    return LoopConfigDefinition.model_validate(definition)


def _mode_definition(
    *,
    object_id: str,
    execution_loop_ref: RegistryObjectRef,
    task_profile_ref: RegistryObjectRef,
    model_profile_ref: RegistryObjectRef | None = None,
    research_loop_ref: RegistryObjectRef | None = None,
    research_participation: str = "none",
    outline_policy: dict[str, object] | None = None,
    composition_rules: dict[str, object] | None = None,
) -> ModeDefinition:
    payload: dict[str, object] = {
        "execution_loop_ref": execution_loop_ref.model_dump(mode="json"),
        "task_authoring_profile_ref": task_profile_ref.model_dump(mode="json"),
        "research_participation": research_participation,
    }
    if model_profile_ref is not None:
        payload["model_profile_ref"] = model_profile_ref.model_dump(mode="json")
    if research_loop_ref is not None:
        payload["research_loop_ref"] = research_loop_ref.model_dump(mode="json")
    if outline_policy is not None:
        payload["outline_policy"] = outline_policy
    if composition_rules is not None:
        payload["composition_rules"] = composition_rules

    return ModeDefinition.model_validate(
        {
            "id": object_id,
            "version": "1.0.0",
            "tier": "golden",
            "title": f"{object_id} mode",
            "summary": "Workspace mode for materialization tests.",
            "source": {"kind": "workspace_defined"},
            "payload": payload,
        }
    )


def _persist(workspace_root: Path, *definitions: object) -> None:
    for definition in definitions:
        persist_workspace_registry_object(workspace_root, definition)


def test_materializer_lookup_prefers_workspace_shadow_for_exact_canonical_match(tmp_path: Path) -> None:
    shadow = _task_profile_definition("task_authoring.narrow").model_copy(
        update={"title": "Workspace narrow shadow"}
    )
    persist_workspace_registry_object(tmp_path, shadow)

    materializer = ArchitectureMaterializer(tmp_path)
    binding, document = materializer.lookup_registry_object(
        _ref(PersistedObjectKind.TASK_AUTHORING_PROFILE, "task_authoring.narrow")
    )

    assert binding.registry_layer is RegistryLayer.WORKSPACE
    assert document.definition.title == "Workspace narrow shadow"


def test_materializer_lookup_does_not_shadow_packaged_object_on_non_canonical_version(tmp_path: Path) -> None:
    packaged_profile = next(
        document.definition
        for document in discover_registry_state(tmp_path, validate_catalog=False).packaged
        if document.key == ("task_authoring_profile", "task_authoring.narrow", "1.0.0")
    )
    shadow_payload = packaged_profile.model_dump(mode="json")
    shadow_payload["version"] = "9.9.9"
    shadow_payload["title"] = "Workspace narrow noncanonical shadow"
    shadow_payload["source"] = {"kind": "workspace_defined"}
    persist_workspace_registry_object(
        tmp_path,
        packaged_profile.__class__.model_validate(shadow_payload),
    )

    materializer = ArchitectureMaterializer(tmp_path)
    binding, document = materializer.lookup_registry_object(
        _ref(PersistedObjectKind.TASK_AUTHORING_PROFILE, "task_authoring.narrow")
    )

    assert binding.registry_layer is RegistryLayer.PACKAGED
    assert document.definition.version == "1.0.0"
    assert document.definition.title != "Workspace narrow noncanonical shadow"


def test_materializer_records_registry_lookup_provenance_for_packaged_defaults(tmp_path: Path) -> None:
    materialized = ArchitectureMaterializer(tmp_path).materialize_mode(
        _ref(PersistedObjectKind.MODE, "mode.default_autonomous")
    )

    assert materialized.provenance["mode.lookup_ref"].source is ProvenanceSource.PACKAGED_REGISTRY
    assert (
        materialized.provenance["mode.task_authoring_profile_lookup_ref"].source
        is ProvenanceSource.PACKAGED_REGISTRY
    )
    assert (
        materialized.provenance["mode.model_profile_lookup_ref"].source
        is ProvenanceSource.PACKAGED_REGISTRY
    )
    assert materialized.execution_loop.provenance["loop.lookup_ref"].source is ProvenanceSource.PACKAGED_REGISTRY
    assert (
        materialized.execution_loop.provenance["loop.task_authoring_profile_lookup_ref"].source
        is ProvenanceSource.PACKAGED_REGISTRY
    )
    assert (
        materialized.execution_loop.provenance["loop.model_profile_lookup_ref"].source
        is ProvenanceSource.PACKAGED_REGISTRY
    )
    assert (
        materialized.execution_loop.provenance["stage.builder.lookup.stage_kind_ref"].source
        is ProvenanceSource.PACKAGED_REGISTRY
    )
    assert (
        materialized.execution_loop.provenance["stage.builder.lookup.model_profile_ref"].source
        is ProvenanceSource.PACKAGED_REGISTRY
    )


def test_materializer_materializes_single_parent_loop_inheritance(tmp_path: Path) -> None:
    stage_kind = _stage_kind_definition(
        kind_id="execution.inherit_builder",
        allowed_overrides=("allow_search",),
    )
    task_profile = _task_profile_definition("task_authoring.inherited")
    parent_loop = _loop_definition(
        object_id="execution.parent_loop",
        stage_kind_id=stage_kind.id,
        task_profile_ref=_ref(PersistedObjectKind.TASK_AUTHORING_PROFILE, task_profile.id),
        include_task_required=True,
        outline_policy={"mode": "hybrid", "shard_glob": "plans/*.md"},
        node_id="parent_build",
    )
    child_loop = _loop_definition(
        object_id="execution.child_loop",
        stage_kind_id=stage_kind.id,
        extends=_ref(PersistedObjectKind.LOOP_CONFIG, parent_loop.id),
        outline_policy={"mode": "index_sharded"},
        node_id="child_build",
    )
    _persist(tmp_path, stage_kind, task_profile, parent_loop, child_loop)

    materialized = ArchitectureMaterializer(tmp_path).materialize_loop(
        _ref(PersistedObjectKind.LOOP_CONFIG, child_loop.id)
    )

    assert materialized.parent_binding is not None
    assert materialized.parent_binding.resolved_ref.id == parent_loop.id
    assert materialized.materialized_definition.extends is None
    assert materialized.materialized_definition.payload.entry_node_id == "child_build"
    assert materialized.task_authoring_profile_ref is not None
    assert materialized.task_authoring_profile_ref.id == task_profile.id
    assert materialized.materialized_definition.payload.outline_policy is not None
    assert materialized.materialized_definition.payload.outline_policy.mode.value == "index_sharded"
    assert materialized.materialized_definition.payload.outline_policy.shard_glob == "plans/*.md"
    assert materialized.provenance["loop.parent_ref"].source is ProvenanceSource.LOOP_PARENT
    assert materialized.provenance["loop.lookup_ref"].source is ProvenanceSource.WORKSPACE_REGISTRY
    assert materialized.provenance["loop.parent_lookup_ref"].source is ProvenanceSource.WORKSPACE_REGISTRY
    assert (
        materialized.provenance["loop.task_authoring_profile_lookup_ref"].source
        is ProvenanceSource.WORKSPACE_REGISTRY
    )
    assert (
        materialized.provenance["stage.child_build.lookup.stage_kind_ref"].source
        is ProvenanceSource.WORKSPACE_REGISTRY
    )


def test_materializer_rejects_grandparent_loop_inheritance(tmp_path: Path) -> None:
    stage_kind = _stage_kind_definition(kind_id="execution.single_parent_only")
    grandparent = _loop_definition(object_id="execution.grandparent", stage_kind_id=stage_kind.id)
    parent = _loop_definition(
        object_id="execution.parent",
        stage_kind_id=stage_kind.id,
        extends=_ref(PersistedObjectKind.LOOP_CONFIG, grandparent.id),
    )
    child = _loop_definition(
        object_id="execution.child",
        stage_kind_id=stage_kind.id,
        extends=_ref(PersistedObjectKind.LOOP_CONFIG, parent.id),
    )
    _persist(tmp_path, stage_kind, grandparent, parent, child)

    with pytest.raises(MaterializationError, match="only one parent"):
        ArchitectureMaterializer(tmp_path).materialize_loop(_ref(PersistedObjectKind.LOOP_CONFIG, child.id))


def test_materializer_composes_mode_and_applies_documented_precedence(tmp_path: Path) -> None:
    execution_stage = _stage_kind_definition(
        kind_id="execution.compose_builder",
        allowed_overrides=("runner", "model", "allow_search", "model_profile_ref"),
    )
    research_stage = _stage_kind_definition(
        kind_id="research.compose_review",
        plane="research",
        allowed_overrides=("allow_search",),
    )
    loop_task_profile = _task_profile_definition("task_authoring.loop_default")
    mode_task_profile = _task_profile_definition("task_authoring.mode_default")
    unused_loop_profile = _model_profile_definition(
        "model.loop_unused",
        default_binding=_binding(model="loop-unused", effort="low"),
    )
    execution_loop_ref = _ref(PersistedObjectKind.LOOP_CONFIG, "execution.composed")
    mode_ref = _ref(PersistedObjectKind.MODE, "mode.composed")
    mode_profile = _model_profile_definition(
        "model.mode_profile",
        default_binding=_binding(model="default-model", effort="low"),
        scoped_defaults=(
            {
                "target_ref": execution_loop_ref.model_dump(mode="json"),
                "binding": _binding(model="loop-scoped", effort="medium"),
            },
            {
                "target_ref": mode_ref.model_dump(mode="json"),
                "binding": _binding(model="mode-scoped", effort="medium", allow_search=True),
            },
        ),
        stage_overrides=(
            {
                "kind_id": execution_stage.id,
                "binding": _binding(model="profile-stage", effort="high", allow_search=True),
            },
        ),
    )
    execution_loop = _loop_definition(
        object_id=execution_loop_ref.id,
        stage_kind_id=execution_stage.id,
        task_profile_ref=_ref(PersistedObjectKind.TASK_AUTHORING_PROFILE, loop_task_profile.id),
        include_task_required=True,
        model_profile_ref=_ref(PersistedObjectKind.MODEL_PROFILE, unused_loop_profile.id),
        node_overrides={"runner": "subprocess"},
    )
    research_loop = _loop_definition(
        object_id="research.composed",
        stage_kind_id=research_stage.id,
        plane="research",
        node_id="research_review",
    )
    mode = _mode_definition(
        object_id=mode_ref.id,
        execution_loop_ref=execution_loop_ref,
        research_loop_ref=_ref(PersistedObjectKind.LOOP_CONFIG, research_loop.id),
        task_profile_ref=_ref(PersistedObjectKind.TASK_AUTHORING_PROFILE, mode_task_profile.id),
        model_profile_ref=_ref(PersistedObjectKind.MODEL_PROFILE, mode_profile.id),
        research_participation="selected_research_stages",
        outline_policy={"mode": "hybrid", "shard_glob": "mode/*.md"},
    )
    _persist(
        tmp_path,
        execution_stage,
        research_stage,
        loop_task_profile,
        mode_task_profile,
        unused_loop_profile,
        mode_profile,
        execution_loop,
        research_loop,
        mode,
    )

    materialized = ArchitectureMaterializer(tmp_path).materialize_mode(
        mode_ref,
        overrides=ModeMaterializationOverrides(
            stage_overrides=(
                StageInvocationOverride.model_validate(
                    {
                        "plane": "execution",
                        "node_id": "build",
                        "overrides": {"model": "invocation-model"},
                    }
                ),
            )
        ),
    )

    assert materialized.execution_loop.requested_ref == execution_loop_ref
    assert materialized.research_loop is not None
    assert materialized.research_loop.requested_ref == _ref(PersistedObjectKind.LOOP_CONFIG, research_loop.id)
    assert materialized.task_authoring_profile_ref.id == mode_task_profile.id
    assert materialized.model_profile_ref is not None
    assert materialized.model_profile_ref.id == mode_profile.id

    stage_binding = materialized.execution_loop.stage_bindings[0]
    assert stage_binding.runner.value == "subprocess"
    assert stage_binding.model == "invocation-model"
    assert stage_binding.effort.value == "high"
    assert stage_binding.allow_search is True
    assert materialized.execution_loop.provenance["stage.build.binding.runner"].source is ProvenanceSource.STAGE_OVERRIDE
    assert materialized.execution_loop.provenance["stage.build.binding.model"].source is ProvenanceSource.INVOCATION
    assert (
        materialized.execution_loop.provenance["stage.build.binding.effort"].source
        is ProvenanceSource.MODEL_PROFILE_STAGE_OVERRIDE
    )
    assert (
        materialized.execution_loop.provenance["stage.build.binding.allow_search"].source
        is ProvenanceSource.MODEL_PROFILE_STAGE_OVERRIDE
    )
    assert materialized.execution_loop.provenance["loop.model_profile_ref"].source is ProvenanceSource.MODE


def test_materializer_rejects_unsupported_stage_invocation_override(tmp_path: Path) -> None:
    stage_kind = _stage_kind_definition(
        kind_id="execution.legality_builder",
        allowed_overrides=("allow_search",),
    )
    loop = _loop_definition(object_id="execution.legality_loop", stage_kind_id=stage_kind.id)
    _persist(tmp_path, stage_kind, loop)

    with pytest.raises(MaterializationError, match="unsupported fields: model"):
        ArchitectureMaterializer(tmp_path).materialize_loop(
            _ref(PersistedObjectKind.LOOP_CONFIG, loop.id),
            overrides=LoopMaterializationOverrides(
                stage_overrides=(
                    StageInvocationOverride.model_validate(
                        {"node_id": "build", "overrides": {"model": "illegal"}}
                    ),
                )
            ),
        )


def test_mode_materialization_overrides_reject_duplicate_stage_invocation_overrides() -> None:
    with pytest.raises(ValueError, match="duplicate stage invocation override for execution:build"):
        ModeMaterializationOverrides.model_validate(
            {
                "stage_overrides": (
                    {
                        "plane": "execution",
                        "node_id": "build",
                        "overrides": {"allow_search": True},
                    },
                    {
                        "plane": "execution",
                        "node_id": "build",
                        "overrides": {"model_profile_ref": _ref(
                            PersistedObjectKind.MODEL_PROFILE,
                            "model.default",
                        ).model_dump(mode="json")},
                    },
                )
            }
        )


def test_materializer_rejects_stage_invocation_override_for_unknown_node(tmp_path: Path) -> None:
    stage_kind = _stage_kind_definition(
        kind_id="execution.unknown_node_builder",
        allowed_overrides=("model",),
    )
    loop = _loop_definition(object_id="execution.unknown_node_loop", stage_kind_id=stage_kind.id)
    _persist(tmp_path, stage_kind, loop)

    with pytest.raises(MaterializationError, match="targets unknown node ghost"):
        ArchitectureMaterializer(tmp_path).materialize_loop(
            _ref(PersistedObjectKind.LOOP_CONFIG, loop.id),
            overrides=LoopMaterializationOverrides(
                stage_overrides=(
                    StageInvocationOverride.model_validate(
                        {"node_id": "ghost", "overrides": {"model": "illegal"}}
                    ),
                )
            ),
        )


def test_materializer_rejects_disallowed_mode_loop_override(tmp_path: Path) -> None:
    stage_kind = _stage_kind_definition(kind_id="execution.mode_override_builder")
    task_profile = _task_profile_definition("task_authoring.mode_override")
    default_loop = _loop_definition(object_id="execution.default_mode_loop", stage_kind_id=stage_kind.id)
    alternate_loop = _loop_definition(object_id="execution.alternate_mode_loop", stage_kind_id=stage_kind.id)
    mode = _mode_definition(
        object_id="mode.no_loop_override",
        execution_loop_ref=_ref(PersistedObjectKind.LOOP_CONFIG, default_loop.id),
        task_profile_ref=_ref(PersistedObjectKind.TASK_AUTHORING_PROFILE, task_profile.id),
    )
    _persist(tmp_path, stage_kind, task_profile, default_loop, alternate_loop, mode)

    with pytest.raises(MaterializationError, match="does not allow ad hoc execution loop overrides"):
        ArchitectureMaterializer(tmp_path).materialize_mode(
            _ref(PersistedObjectKind.MODE, mode.id),
            overrides=ModeMaterializationOverrides(
                execution_loop_ref=_ref(PersistedObjectKind.LOOP_CONFIG, alternate_loop.id)
            ),
        )


def test_materializer_rejects_wrong_plane_mode_execution_loop_override(tmp_path: Path) -> None:
    execution_stage = _stage_kind_definition(kind_id="execution.mode_execution_builder")
    research_stage = _stage_kind_definition(kind_id="research.mode_execution_review", plane="research")
    task_profile = _task_profile_definition("task_authoring.mode_execution")
    execution_loop = _loop_definition(object_id="execution.mode_execution_loop", stage_kind_id=execution_stage.id)
    research_loop = _loop_definition(
        object_id="research.mode_execution_loop",
        stage_kind_id=research_stage.id,
        plane="research",
        node_id="review",
    )
    mode = _mode_definition(
        object_id="mode.execution_plane_guard",
        execution_loop_ref=_ref(PersistedObjectKind.LOOP_CONFIG, execution_loop.id),
        task_profile_ref=_ref(PersistedObjectKind.TASK_AUTHORING_PROFILE, task_profile.id),
        composition_rules={"allow_ephemeral_execution_loop_override": True},
    )
    _persist(tmp_path, execution_stage, research_stage, task_profile, execution_loop, research_loop, mode)

    with pytest.raises(MaterializationError, match="execution_loop_ref must resolve to an execution loop"):
        ArchitectureMaterializer(tmp_path).materialize_mode(
            _ref(PersistedObjectKind.MODE, mode.id),
            overrides=ModeMaterializationOverrides(
                execution_loop_ref=_ref(PersistedObjectKind.LOOP_CONFIG, research_loop.id)
            ),
        )


def test_materializer_rejects_research_stage_overrides_without_selected_research_loop(tmp_path: Path) -> None:
    execution_stage = _stage_kind_definition(kind_id="execution.mode_override_guard")
    task_profile = _task_profile_definition("task_authoring.mode_override_guard")
    execution_loop = _loop_definition(object_id="execution.mode_override_guard_loop", stage_kind_id=execution_stage.id)
    mode = _mode_definition(
        object_id="mode.research_override_guard",
        execution_loop_ref=_ref(PersistedObjectKind.LOOP_CONFIG, execution_loop.id),
        task_profile_ref=_ref(PersistedObjectKind.TASK_AUTHORING_PROFILE, task_profile.id),
    )
    _persist(tmp_path, execution_stage, task_profile, execution_loop, mode)

    with pytest.raises(MaterializationError, match="research stage overrides but no research loop is selected"):
        ArchitectureMaterializer(tmp_path).materialize_mode(
            _ref(PersistedObjectKind.MODE, mode.id),
            overrides=ModeMaterializationOverrides(
                stage_overrides=(
                    StageInvocationOverride.model_validate(
                        {
                            "plane": "research",
                            "node_id": "research_review",
                            "overrides": {"allow_search": True},
                        }
                    ),
                )
            ),
        )


def test_materializer_resolves_prompt_assets_with_workspace_first_provenance(tmp_path: Path) -> None:
    stage_kind = _stage_kind_definition(
        kind_id="execution.prompt_builder",
        allowed_overrides=("prompt_asset_ref",),
    )
    loop = _loop_definition(
        object_id="execution.prompt_loop",
        stage_kind_id=stage_kind.id,
        node_overrides={"prompt_asset_ref": "agents/_start.md"},
    )
    _persist(tmp_path, stage_kind, loop)

    prompt_path = tmp_path / "agents" / "_start.md"
    prompt_path.parent.mkdir(parents=True, exist_ok=True)
    prompt_path.write_text("workspace prompt override\n", encoding="utf-8")

    materializer = ArchitectureMaterializer(tmp_path)
    workspace_bound = materializer.materialize_loop(_ref(PersistedObjectKind.LOOP_CONFIG, loop.id))
    workspace_asset = workspace_bound.stage_bindings[0].prompt_asset
    assert workspace_asset is not None
    assert workspace_asset.source_kind.value == "workspace"
    assert workspace_bound.provenance["stage.build.asset.prompt_asset_ref"].source is ProvenanceSource.ASSET_WORKSPACE

    prompt_path.unlink()
    package_bound = materializer.materialize_loop(_ref(PersistedObjectKind.LOOP_CONFIG, loop.id))
    package_asset = package_bound.stage_bindings[0].prompt_asset
    assert package_asset is not None
    assert package_asset.source_kind.value == "package"
    assert package_bound.provenance["stage.build.asset.prompt_asset_ref"].source is ProvenanceSource.ASSET_PACKAGE
