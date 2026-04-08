"""Deterministic Phase 01B registry resolution and object materialization."""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Any

from .assets.resolver import AssetResolver, AssetSourceKind
from .contracts import (
    ControlPlane,
    LoopConfigDefinition,
    LoopStageNodeOverrides,
    ModeDefinition,
    ModelBinding,
    ModelProfileDefinition,
    OutlinePolicy,
    RegisteredStageKindDefinition,
    RegistryObjectRef,
    ResearchParticipationMode,
    TaskAuthoringProfileDefinition,
)
from .materialization_merge import (
    binding_scope_for as _binding_scope_for,
)
from .materialization_merge import (
    merge_loop_payload as _merge_loop_payload,
)
from .materialization_merge import (
    merge_optional_model as _merge_optional_model,
)
from .materialization_merge import (
    object_ref as _object_ref,
)
from .materialization_merge import (
    overlay_model as _overlay_model,
)
from .materialization_merge import (
    profile_stage_override_for as _profile_stage_override_for,
)
from .materialization_merge import (
    ref_key as _ref_key,
)
from .materialization_merge import (
    ref_string as _ref_string,
)
from .materialization_merge import (
    registry_binding as _registry_binding,
)
from .materialization_merge import (
    stage_override_map as _stage_override_map,
)
from .materialization_models import (
    LoopMaterializationOverrides,
    MaterializationError,
    MaterializedAssetBinding,
    MaterializedLoop,
    MaterializedMode,
    MaterializedStageBinding,
    ModeMaterializationOverrides,
    ProvenanceEntry,
    ProvenanceLane,
    ProvenanceSource,
    ResolvedRegistryBinding,
    StageInvocationOverride,
)
from .materialization_provenance import (
    set_provenance as _set_provenance,
)
from .materialization_provenance import (
    set_registry_binding_provenance as _set_registry_binding_provenance,
)
from .materialization_provenance import (
    sorted_provenance_map as _sorted_provenance_map,
)
from .registry import RegistryDiscovery, RegistryDocument, RegistryLayer, discover_registry_state


class ArchitectureMaterializer:
    """Resolve registry objects and materialize effective 01B loop/mode snapshots."""

    def __init__(
        self,
        workspace_root: Path | str,
        *,
        discovery: RegistryDiscovery | None = None,
        asset_resolver: AssetResolver | None = None,
    ) -> None:
        self.workspace_root = Path(workspace_root).expanduser().resolve(strict=False)
        self.discovery = discovery or discover_registry_state(self.workspace_root)
        self.asset_resolver = asset_resolver or AssetResolver(self.workspace_root)
        self._workspace_documents = {document.key: document for document in self.discovery.workspace}
        self._packaged_documents = {document.key: document for document in self.discovery.packaged}

    def lookup_registry_object(self, ref: RegistryObjectRef) -> tuple[ResolvedRegistryBinding, RegistryDocument]:
        """Resolve one canonical registry reference with workspace shadowing."""

        key = _ref_key(ref)
        document = self._workspace_documents.get(key)
        if document is None:
            document = self._packaged_documents.get(key)
        if document is None:
            raise MaterializationError(f"registry object is missing: {ref.kind.value}:{ref.id}@{ref.version}")
        binding = _registry_binding(ref, document)
        return binding, document

    def materialize_loop(
        self,
        loop_ref: RegistryObjectRef,
        *,
        overrides: LoopMaterializationOverrides | None = None,
        resolve_assets: bool = True,
    ) -> MaterializedLoop:
        """Materialize one loop plus its stage bindings."""

        overrides = overrides or LoopMaterializationOverrides()
        loop_binding, loop_document = self.lookup_registry_object(loop_ref)
        if not isinstance(loop_document.definition, LoopConfigDefinition):
            raise MaterializationError(f"{loop_ref.id} is not a loop_config object")
        materialized_definition, parent_binding, base_provenance = self._materialize_loop_definition(
            loop_document.definition,
        )
        result = self._bind_loop(
            materialized_definition,
            requested_ref=loop_ref,
            loop_binding=loop_binding,
            parent_binding=parent_binding,
            overrides=overrides,
            mode_task_authoring_profile_ref=None,
            mode_definition=None,
            mode_outline_policy=None,
            mode_model_profile_ref=None,
            resolve_assets=resolve_assets,
            base_provenance=base_provenance,
        )
        return result

    def materialize_mode(
        self,
        mode_ref: RegistryObjectRef,
        *,
        overrides: ModeMaterializationOverrides | None = None,
        resolve_assets: bool = True,
    ) -> MaterializedMode:
        """Materialize one composed mode, selected loops, and final stage asset bindings."""

        overrides = overrides or ModeMaterializationOverrides()
        mode_binding, mode_document = self.lookup_registry_object(mode_ref)
        if not isinstance(mode_document.definition, ModeDefinition):
            raise MaterializationError(f"{mode_ref.id} is not a mode object")
        mode_definition = mode_document.definition
        composition_rules = mode_definition.payload.composition_rules

        execution_loop_ref = overrides.execution_loop_ref or mode_definition.payload.execution_loop_ref
        if (
            overrides.execution_loop_ref is not None
            and execution_loop_ref != mode_definition.payload.execution_loop_ref
            and not (composition_rules and composition_rules.allow_ephemeral_execution_loop_override)
        ):
            raise MaterializationError("mode does not allow ad hoc execution loop overrides")

        research_loop_ref = overrides.research_loop_ref or mode_definition.payload.research_loop_ref
        if (
            overrides.research_loop_ref is not None
            and overrides.research_loop_ref != mode_definition.payload.research_loop_ref
            and not (composition_rules and composition_rules.allow_ephemeral_research_loop_override)
        ):
            raise MaterializationError("mode does not allow ad hoc research loop overrides")

        research_participation = overrides.research_participation or mode_definition.payload.research_participation
        if (
            research_participation
            in {
                ResearchParticipationMode.SELECTED_RESEARCH_STAGES,
                ResearchParticipationMode.FULL_RESEARCH_HANDOFF,
            }
            and research_loop_ref is None
        ):
            raise MaterializationError(
                "research participation modes selected_research_stages and full_research_handoff require a research loop"
            )

        outline_policy, outline_policy_source = _merge_optional_model(
            None,
            mode_definition.payload.outline_policy,
            lane=ProvenanceLane.MODE,
            source=ProvenanceSource.MODE,
            detail=f"mode {mode_definition.id}",
            object_ref=_ref_string(_object_ref(mode_definition)),
            path="mode.outline_policy",
        )
        if overrides.outline_policy is not None:
            outline_policy, outline_policy_source = _merge_optional_model(
                outline_policy,
                overrides.outline_policy,
                lane=ProvenanceLane.MODE,
                source=ProvenanceSource.INVOCATION,
                detail="invocation override",
                object_ref=None,
                path="mode.outline_policy",
            )

        policy_toggles, policy_toggle_source = _merge_optional_model(
            None,
            mode_definition.payload.policy_toggles,
            lane=ProvenanceLane.MODE,
            source=ProvenanceSource.MODE,
            detail=f"mode {mode_definition.id}",
            object_ref=_ref_string(_object_ref(mode_definition)),
            path="mode.policy_toggles",
        )
        if overrides.policy_toggles is not None:
            policy_toggles, policy_toggle_source = _merge_optional_model(
                policy_toggles,
                overrides.policy_toggles,
                lane=ProvenanceLane.MODE,
                source=ProvenanceSource.INVOCATION,
                detail="invocation override",
                object_ref=None,
                path="mode.policy_toggles",
            )

        selected_task_profile_ref = (
            overrides.task_authoring_profile_ref
            or mode_definition.payload.task_authoring_profile_ref
        )
        task_profile_binding, task_profile = self._lookup_task_profile(selected_task_profile_ref)

        selected_model_profile_ref = overrides.model_profile_ref or mode_definition.payload.model_profile_ref
        model_profile_binding, model_profile = self._lookup_optional_model_profile(selected_model_profile_ref)

        mode_provenance: dict[str, ProvenanceEntry] = {}
        _set_registry_binding_provenance(mode_provenance, "mode.lookup_ref", mode_binding)
        _set_provenance(
            mode_provenance,
            "mode.execution_loop_ref",
            ProvenanceLane.LOOKUP,
            ProvenanceSource.INVOCATION if overrides.execution_loop_ref is not None else ProvenanceSource.MODE,
            "invocation override" if overrides.execution_loop_ref is not None else f"mode {mode_definition.id}",
            object_ref=None if overrides.execution_loop_ref is not None else _ref_string(_object_ref(mode_definition)),
            value=_ref_string(execution_loop_ref),
        )
        if research_loop_ref is not None:
            _set_provenance(
                mode_provenance,
                "mode.research_loop_ref",
                ProvenanceLane.LOOKUP,
                ProvenanceSource.INVOCATION if overrides.research_loop_ref is not None else ProvenanceSource.MODE,
                "invocation override" if overrides.research_loop_ref is not None else f"mode {mode_definition.id}",
                object_ref=None if overrides.research_loop_ref is not None else _ref_string(_object_ref(mode_definition)),
                value=_ref_string(research_loop_ref),
            )
        _set_provenance(
            mode_provenance,
            "mode.task_authoring_profile_ref",
            ProvenanceLane.TASK_AUTHORING,
            ProvenanceSource.INVOCATION if overrides.task_authoring_profile_ref is not None else ProvenanceSource.MODE,
            "invocation override" if overrides.task_authoring_profile_ref is not None else f"mode {mode_definition.id}",
            object_ref=_ref_string(selected_task_profile_ref),
            value=_ref_string(selected_task_profile_ref),
        )
        if selected_model_profile_ref is not None:
            _set_provenance(
                mode_provenance,
                "mode.model_profile_ref",
                ProvenanceLane.MODEL_PROFILE,
                ProvenanceSource.INVOCATION if overrides.model_profile_ref is not None else ProvenanceSource.MODE,
                "invocation override" if overrides.model_profile_ref is not None else f"mode {mode_definition.id}",
                object_ref=_ref_string(selected_model_profile_ref),
                value=_ref_string(selected_model_profile_ref),
            )
        _set_provenance(
            mode_provenance,
            "mode.research_participation",
            ProvenanceLane.MODE,
            ProvenanceSource.INVOCATION if overrides.research_participation is not None else ProvenanceSource.MODE,
            "invocation override" if overrides.research_participation is not None else f"mode {mode_definition.id}",
            object_ref=None if overrides.research_participation is not None else _ref_string(_object_ref(mode_definition)),
            value=research_participation.value,
        )
        if outline_policy_source is not None:
            mode_provenance[outline_policy_source.path] = outline_policy_source
        if policy_toggle_source is not None:
            mode_provenance[policy_toggle_source.path] = policy_toggle_source
        _set_registry_binding_provenance(
            mode_provenance,
            "mode.task_authoring_profile_lookup_ref",
            task_profile_binding,
        )
        if model_profile_binding is not None:
            _set_registry_binding_provenance(
                mode_provenance,
                "mode.model_profile_lookup_ref",
                model_profile_binding,
            )

        execution_loop_binding, execution_loop_document = self.lookup_registry_object(execution_loop_ref)
        if not isinstance(execution_loop_document.definition, LoopConfigDefinition):
            raise MaterializationError(f"{execution_loop_ref.id} is not a loop_config object")
        if execution_loop_document.definition.payload.plane is not ControlPlane.EXECUTION:
            raise MaterializationError(
                f"mode {mode_definition.id} execution_loop_ref must resolve to an execution loop, "
                f"got {execution_loop_document.definition.payload.plane.value}"
            )
        execution_loop_definition, execution_parent_binding, execution_base_provenance = (
            self._materialize_loop_definition(execution_loop_document.definition)
        )
        execution_stage_overrides = tuple(
            override for override in overrides.stage_overrides if override.plane is ControlPlane.EXECUTION
        )
        self._validate_stage_override_targets(execution_loop_definition, execution_stage_overrides)
        execution_loop = self._bind_loop(
            execution_loop_definition,
            requested_ref=execution_loop_ref,
            loop_binding=execution_loop_binding,
            parent_binding=execution_parent_binding,
            overrides=LoopMaterializationOverrides(
                task_authoring_profile_ref=overrides.task_authoring_profile_ref,
                model_profile_ref=overrides.model_profile_ref,
                outline_policy=overrides.outline_policy,
                stage_overrides=execution_stage_overrides,
            ),
            mode_task_authoring_profile_ref=mode_definition.payload.task_authoring_profile_ref,
            mode_definition=mode_definition,
            mode_outline_policy=mode_definition.payload.outline_policy,
            mode_model_profile_ref=mode_definition.payload.model_profile_ref,
            resolve_assets=resolve_assets,
            base_provenance=execution_base_provenance,
        )
        execution_loop = execution_loop.model_copy(
            update={
                "provenance": _sorted_provenance_map(
                    {**mode_provenance, **execution_loop.provenance}
                )
            }
        )

        research_loop: MaterializedLoop | None = None
        research_stage_overrides = tuple(
            override for override in overrides.stage_overrides if override.plane is ControlPlane.RESEARCH
        )
        if research_loop_ref is not None:
            research_loop_binding, research_loop_document = self.lookup_registry_object(research_loop_ref)
            if not isinstance(research_loop_document.definition, LoopConfigDefinition):
                raise MaterializationError(f"{research_loop_ref.id} is not a loop_config object")
            if research_loop_document.definition.payload.plane is not ControlPlane.RESEARCH:
                raise MaterializationError(
                    f"mode {mode_definition.id} research_loop_ref must resolve to a research loop, "
                    f"got {research_loop_document.definition.payload.plane.value}"
                )
            research_loop_definition, research_parent_binding, research_base_provenance = (
                self._materialize_loop_definition(research_loop_document.definition)
            )
            self._validate_stage_override_targets(research_loop_definition, research_stage_overrides)
            research_loop = self._bind_loop(
                research_loop_definition,
                requested_ref=research_loop_ref,
                loop_binding=research_loop_binding,
                parent_binding=research_parent_binding,
                overrides=LoopMaterializationOverrides(
                    model_profile_ref=overrides.model_profile_ref,
                    outline_policy=overrides.outline_policy,
                    stage_overrides=research_stage_overrides,
                ),
                mode_task_authoring_profile_ref=None,
                mode_definition=mode_definition,
                mode_outline_policy=mode_definition.payload.outline_policy,
                mode_model_profile_ref=mode_definition.payload.model_profile_ref,
                resolve_assets=resolve_assets,
                base_provenance=research_base_provenance,
            )
            research_loop = research_loop.model_copy(
                update={
                    "provenance": _sorted_provenance_map(
                        {**mode_provenance, **research_loop.provenance}
                    )
                }
            )
        elif research_stage_overrides:
            raise MaterializationError(
                f"mode {mode_definition.id} received research stage overrides but no research loop is selected"
            )

        return MaterializedMode(
            requested_ref=mode_ref,
            mode_binding=mode_binding,
            mode_definition=mode_definition,
            execution_loop=execution_loop,
            research_loop=research_loop,
            task_authoring_profile_ref=selected_task_profile_ref,
            task_authoring_profile_binding=task_profile_binding,
            task_authoring_profile=task_profile,
            model_profile_ref=selected_model_profile_ref,
            model_profile_binding=model_profile_binding,
            model_profile=model_profile,
            research_participation=research_participation,
            outline_policy=outline_policy,
            policy_toggles=policy_toggles,
            provenance=_sorted_provenance_map(mode_provenance),
        )

    def _materialize_loop_definition(
        self,
        loop_definition: LoopConfigDefinition,
    ) -> tuple[LoopConfigDefinition, ResolvedRegistryBinding | None, dict[str, ProvenanceEntry]]:
        base_provenance: dict[str, ProvenanceEntry] = {}
        parent_binding: ResolvedRegistryBinding | None = None
        if loop_definition.extends is None:
            return (
                loop_definition.model_copy(update={"extends": None}),
                None,
                base_provenance,
            )

        parent_binding, parent_document = self.lookup_registry_object(loop_definition.extends)
        if not isinstance(parent_document.definition, LoopConfigDefinition):
            raise MaterializationError(
                f"loop parent {loop_definition.extends.id}@{loop_definition.extends.version} is not a loop_config"
            )
        parent_definition = parent_document.definition
        if parent_definition.extends is not None:
            raise MaterializationError(
                "Phase 01B loop inheritance allows only one parent; parent loops may not themselves extend another loop"
            )
        if parent_definition.payload.plane is not loop_definition.payload.plane:
            raise MaterializationError(
                f"loop {loop_definition.id} extends a {parent_definition.payload.plane.value} parent from a "
                f"{loop_definition.payload.plane.value} child"
            )

        merged_payload = _merge_loop_payload(parent_definition.payload, loop_definition.payload)
        materialized_definition = loop_definition.model_copy(
            update={
                "extends": None,
                "payload": merged_payload,
            }
        )
        _set_provenance(
            base_provenance,
            "loop.parent_ref",
            ProvenanceLane.LOOP,
            ProvenanceSource.LOOP_PARENT,
            f"parent loop {parent_definition.id}",
            object_ref=_ref_string(_object_ref(parent_definition)),
            value=_ref_string(_object_ref(parent_definition)),
        )
        return materialized_definition, parent_binding, base_provenance

    def _bind_loop(
        self,
        loop_definition: LoopConfigDefinition,
        *,
        requested_ref: RegistryObjectRef,
        loop_binding: ResolvedRegistryBinding | None,
        parent_binding: ResolvedRegistryBinding | None,
        overrides: LoopMaterializationOverrides,
        mode_task_authoring_profile_ref: RegistryObjectRef | None,
        mode_definition: ModeDefinition | None,
        mode_outline_policy: OutlinePolicy | None,
        mode_model_profile_ref: RegistryObjectRef | None,
        resolve_assets: bool,
        base_provenance: dict[str, ProvenanceEntry],
    ) -> MaterializedLoop:
        self._validate_stage_override_targets(loop_definition, overrides.stage_overrides)
        stage_override_map = _stage_override_map(overrides.stage_overrides)
        base_provenance = dict(base_provenance)
        if loop_binding is not None:
            _set_registry_binding_provenance(base_provenance, "loop.lookup_ref", loop_binding)
        if parent_binding is not None:
            _set_registry_binding_provenance(base_provenance, "loop.parent_lookup_ref", parent_binding)

        selected_task_profile_ref = loop_definition.payload.task_authoring_profile_ref
        task_profile_source = ProvenanceSource.LOOP_CHILD
        task_profile_detail = f"loop {loop_definition.id}"
        if mode_task_authoring_profile_ref is not None:
            selected_task_profile_ref = mode_task_authoring_profile_ref
            task_profile_source = ProvenanceSource.MODE
            task_profile_detail = f"mode {mode_definition.id}" if mode_definition is not None else "mode composition"
        if overrides.task_authoring_profile_ref is not None:
            selected_task_profile_ref = overrides.task_authoring_profile_ref
            task_profile_source = ProvenanceSource.INVOCATION
            task_profile_detail = "invocation override"

        task_profile_binding: ResolvedRegistryBinding | None = None
        task_profile: TaskAuthoringProfileDefinition | None = None
        if selected_task_profile_ref is not None:
            task_profile_binding, task_profile = self._lookup_task_profile(selected_task_profile_ref)
            _set_provenance(
                base_provenance,
                "loop.task_authoring_profile_ref",
                ProvenanceLane.TASK_AUTHORING,
                task_profile_source,
                task_profile_detail,
                object_ref=_ref_string(selected_task_profile_ref),
                value=_ref_string(selected_task_profile_ref),
            )
            _set_registry_binding_provenance(
                base_provenance,
                "loop.task_authoring_profile_lookup_ref",
                task_profile_binding,
            )

        selected_outline_policy = loop_definition.payload.outline_policy
        outline_entry = None
        if selected_outline_policy is not None:
            outline_entry = ProvenanceEntry(
                path="loop.outline_policy",
                lane=ProvenanceLane.LOOP,
                source=ProvenanceSource.LOOP_CHILD,
                detail=f"loop {loop_definition.id}",
                object_ref=_ref_string(_object_ref(loop_definition)),
                value=selected_outline_policy.model_dump(mode="json"),
            )
        if mode_outline_policy is not None:
            selected_outline_policy = _overlay_model(selected_outline_policy, mode_outline_policy)
            outline_entry = ProvenanceEntry(
                path="loop.outline_policy",
                lane=ProvenanceLane.MODE,
                source=ProvenanceSource.MODE,
                detail=f"mode {mode_definition.id}" if mode_definition is not None else "mode composition",
                object_ref=_ref_string(_object_ref(mode_definition)) if mode_definition is not None else None,
                value=selected_outline_policy.model_dump(mode="json"),
            )
        if overrides.outline_policy is not None:
            selected_outline_policy = _overlay_model(selected_outline_policy, overrides.outline_policy)
            outline_entry = ProvenanceEntry(
                path="loop.outline_policy",
                lane=ProvenanceLane.LOOP,
                source=ProvenanceSource.INVOCATION,
                detail="invocation override",
                object_ref=None,
                value=selected_outline_policy.model_dump(mode="json"),
            )
        if outline_entry is not None:
            base_provenance[outline_entry.path] = outline_entry

        selected_model_profile_ref = loop_definition.payload.model_profile_ref
        model_profile_source = ProvenanceSource.LOOP_CHILD
        model_profile_detail = f"loop {loop_definition.id}"
        if mode_model_profile_ref is not None:
            selected_model_profile_ref = mode_model_profile_ref
            model_profile_source = ProvenanceSource.MODE
            model_profile_detail = f"mode {mode_definition.id}" if mode_definition is not None else "mode composition"
        if overrides.model_profile_ref is not None:
            selected_model_profile_ref = overrides.model_profile_ref
            model_profile_source = ProvenanceSource.INVOCATION
            model_profile_detail = "invocation override"

        model_profile_binding, model_profile = self._lookup_optional_model_profile(selected_model_profile_ref)
        if selected_model_profile_ref is not None:
            _set_provenance(
                base_provenance,
                "loop.model_profile_ref",
                ProvenanceLane.MODEL_PROFILE,
                model_profile_source,
                model_profile_detail,
                object_ref=_ref_string(selected_model_profile_ref),
                value=_ref_string(selected_model_profile_ref),
            )
        if model_profile_binding is not None:
            _set_registry_binding_provenance(
                base_provenance,
                "loop.model_profile_lookup_ref",
                model_profile_binding,
            )

        materialized_payload = loop_definition.payload.model_copy(update={"outline_policy": selected_outline_policy})
        materialized_definition = loop_definition.model_copy(update={"payload": materialized_payload})

        stage_bindings: list[MaterializedStageBinding] = []
        for node in materialized_definition.payload.nodes:
            stage_binding, stage_provenance = self._bind_stage(
                loop_definition=materialized_definition,
                node_id=node.node_id,
                selected_model_profile_ref=selected_model_profile_ref,
                selected_model_profile_binding=model_profile_binding,
                selected_model_profile=model_profile,
                stage_override=stage_override_map.get((materialized_definition.payload.plane, node.node_id)),
                mode_definition=mode_definition,
                resolve_assets=resolve_assets,
            )
            stage_bindings.append(stage_binding)
            base_provenance.update(stage_provenance)

        return MaterializedLoop(
            requested_ref=requested_ref,
            loop_binding=loop_binding,
            parent_binding=parent_binding,
            materialized_definition=materialized_definition,
            task_authoring_profile_ref=selected_task_profile_ref,
            task_authoring_profile_binding=task_profile_binding,
            task_authoring_profile=task_profile,
            model_profile_ref=selected_model_profile_ref,
            model_profile_binding=model_profile_binding,
            model_profile=model_profile,
            stage_bindings=tuple(stage_bindings),
            provenance=_sorted_provenance_map(base_provenance),
        )

    def _bind_stage(
        self,
        *,
        loop_definition: LoopConfigDefinition,
        node_id: str,
        selected_model_profile_ref: RegistryObjectRef | None,
        selected_model_profile_binding: ResolvedRegistryBinding | None,
        selected_model_profile: ModelProfileDefinition | None,
        stage_override: StageInvocationOverride | None,
        mode_definition: ModeDefinition | None,
        resolve_assets: bool,
    ) -> tuple[MaterializedStageBinding, dict[str, ProvenanceEntry]]:
        node_map = {node.node_id: node for node in loop_definition.payload.nodes}
        try:
            node = node_map[node_id]
        except KeyError as exc:
            raise MaterializationError(f"loop {loop_definition.id} is missing stage node {node_id}") from exc

        stage_kind_binding, stage_kind = self._lookup_stage_kind(node.kind_id)
        provenance: dict[str, ProvenanceEntry] = {}
        _set_registry_binding_provenance(
            provenance,
            f"stage.{node.node_id}.lookup.stage_kind_ref",
            stage_kind_binding,
        )

        invocation_overrides = stage_override.overrides if stage_override is not None else None
        if stage_override is not None and stage_override.plane is not loop_definition.payload.plane:
            raise MaterializationError(
                f"stage override for {stage_override.node_id} targets {stage_override.plane.value}, "
                f"but loop {loop_definition.id} is {loop_definition.payload.plane.value}"
            )

        self._validate_stage_override_legality(stage_kind, node.overrides, source="loop stage override")
        if invocation_overrides is not None:
            self._validate_stage_override_legality(stage_kind, invocation_overrides, source="invocation stage override")

        effective_model_profile_ref = selected_model_profile_ref
        effective_model_profile_binding = selected_model_profile_binding
        effective_model_profile = selected_model_profile
        profile_source = ProvenanceSource.LOOP_CHILD
        profile_detail = f"loop {loop_definition.id}"
        if node.overrides.model_profile_ref is not None:
            effective_model_profile_ref = node.overrides.model_profile_ref
            effective_model_profile_binding, effective_model_profile = self._lookup_optional_model_profile(
                effective_model_profile_ref
            )
            profile_source = ProvenanceSource.STAGE_OVERRIDE
            profile_detail = f"stage override {node.node_id}"
        if invocation_overrides is not None and invocation_overrides.model_profile_ref is not None:
            effective_model_profile_ref = invocation_overrides.model_profile_ref
            effective_model_profile_binding, effective_model_profile = self._lookup_optional_model_profile(
                effective_model_profile_ref
            )
            profile_source = ProvenanceSource.INVOCATION
            profile_detail = f"invocation override {node.node_id}"
        if effective_model_profile_ref is not None:
            _set_provenance(
                provenance,
                f"stage.{node.node_id}.binding.model_profile_ref",
                ProvenanceLane.MODEL_PROFILE,
                profile_source,
                profile_detail,
                object_ref=_ref_string(effective_model_profile_ref),
                value=_ref_string(effective_model_profile_ref),
            )
        if effective_model_profile_binding is not None:
            _set_registry_binding_provenance(
                provenance,
                f"stage.{node.node_id}.lookup.model_profile_ref",
                effective_model_profile_binding,
            )

        resolved_values: dict[str, Any] = {}
        profile_ref_string = _ref_string(effective_model_profile_ref) if effective_model_profile_ref is not None else None
        if effective_model_profile is not None:
            self._apply_model_binding(
                resolved_values,
                provenance,
                effective_model_profile.payload.default_binding,
                source=ProvenanceSource.MODEL_PROFILE_DEFAULT,
                detail=f"model profile {effective_model_profile.id}",
                object_ref=profile_ref_string,
                path_prefix=f"stage.{node.node_id}.binding",
            )
            loop_scope = _binding_scope_for(effective_model_profile, _object_ref(loop_definition))
            if loop_scope is not None:
                self._apply_model_binding(
                    resolved_values,
                    provenance,
                    loop_scope,
                    source=ProvenanceSource.MODEL_PROFILE_LOOP_SCOPE,
                    detail=f"loop-scoped model profile binding for {loop_definition.id}",
                    object_ref=profile_ref_string,
                    path_prefix=f"stage.{node.node_id}.binding",
                )
            if mode_definition is not None:
                mode_scope = _binding_scope_for(effective_model_profile, _object_ref(mode_definition))
                if mode_scope is not None:
                    self._apply_model_binding(
                        resolved_values,
                        provenance,
                        mode_scope,
                        source=ProvenanceSource.MODEL_PROFILE_MODE_SCOPE,
                        detail=f"mode-scoped model profile binding for {mode_definition.id}",
                        object_ref=profile_ref_string,
                        path_prefix=f"stage.{node.node_id}.binding",
                    )
            profile_stage_override = _profile_stage_override_for(effective_model_profile, node.kind_id)
            if profile_stage_override is not None:
                self._apply_model_binding(
                    resolved_values,
                    provenance,
                    profile_stage_override,
                    source=ProvenanceSource.MODEL_PROFILE_STAGE_OVERRIDE,
                    detail=f"model profile stage override for {node.kind_id}",
                    object_ref=profile_ref_string,
                    path_prefix=f"stage.{node.node_id}.binding",
                )

        self._apply_direct_overrides(
            resolved_values,
            provenance,
            node.overrides,
            source=ProvenanceSource.STAGE_OVERRIDE,
            detail=f"stage override {node.node_id}",
            path_prefix=f"stage.{node.node_id}.binding",
        )
        if invocation_overrides is not None:
            self._apply_direct_overrides(
                resolved_values,
                provenance,
                invocation_overrides,
                source=ProvenanceSource.INVOCATION,
                detail=f"invocation override {node.node_id}",
                path_prefix=f"stage.{node.node_id}.binding",
            )

        prompt_asset: MaterializedAssetBinding | None = None
        prompt_asset_ref = resolved_values.get("prompt_asset_ref")
        if prompt_asset_ref is not None and resolve_assets:
            resolved_asset = self.asset_resolver.resolve_ref(prompt_asset_ref)
            prompt_asset = MaterializedAssetBinding(
                node_id=node.node_id,
                requested_ref=prompt_asset_ref,
                resolved_ref=resolved_asset.resolved_ref,
                source_kind=resolved_asset.source_kind,
                workspace_path=resolved_asset.workspace_path,
                relative_path=(
                    resolved_asset.relative_path.as_posix()
                    if resolved_asset.relative_path is not None
                    else None
                ),
                bundle_version=resolved_asset.bundle_version,
            )
            _set_provenance(
                provenance,
                f"stage.{node.node_id}.asset.prompt_asset_ref",
                ProvenanceLane.ASSET,
                ProvenanceSource.ASSET_WORKSPACE
                if resolved_asset.source_kind is AssetSourceKind.WORKSPACE
                else ProvenanceSource.ASSET_PACKAGE,
                f"final asset materialization for {node.node_id}",
                object_ref=resolved_asset.resolved_ref,
                value=prompt_asset.model_dump(mode="json"),
            )

        binding = MaterializedStageBinding(
            plane=loop_definition.payload.plane,
            node_id=node.node_id,
            kind_id=node.kind_id,
            stage_kind_binding=stage_kind_binding,
            model_profile_ref=effective_model_profile_ref,
            model_profile_binding=effective_model_profile_binding,
            runner=resolved_values.get("runner"),
            model=resolved_values.get("model"),
            effort=resolved_values.get("effort"),
            allow_search=resolved_values.get("allow_search"),
            prompt_asset_ref=prompt_asset_ref,
            timeout_seconds=resolved_values.get("timeout_seconds"),
            prompt_asset=prompt_asset,
        )
        return binding, provenance

    def _lookup_task_profile(
        self,
        ref: RegistryObjectRef,
    ) -> tuple[ResolvedRegistryBinding, TaskAuthoringProfileDefinition]:
        binding, document = self.lookup_registry_object(ref)
        if not isinstance(document.definition, TaskAuthoringProfileDefinition):
            raise MaterializationError(f"{ref.id} is not a task_authoring_profile object")
        return binding, document.definition

    def _lookup_optional_model_profile(
        self,
        ref: RegistryObjectRef | None,
    ) -> tuple[ResolvedRegistryBinding | None, ModelProfileDefinition | None]:
        if ref is None:
            return None, None
        binding, document = self.lookup_registry_object(ref)
        if not isinstance(document.definition, ModelProfileDefinition):
            raise MaterializationError(f"{ref.id} is not a model_profile object")
        return binding, document.definition

    def _lookup_stage_kind(
        self,
        kind_id: str,
    ) -> tuple[ResolvedRegistryBinding, RegisteredStageKindDefinition]:
        candidates: list[RegistryDocument] = []
        for document in self.discovery.effective:
            if not isinstance(document.definition, RegisteredStageKindDefinition):
                continue
            if document.definition.payload.kind_id == kind_id:
                candidates.append(document)
        if not candidates:
            raise MaterializationError(f"registered stage kind is missing: {kind_id}")
        if len(candidates) > 1:
            versions = ", ".join(
                f"{document.definition.id}@{document.definition.version}" for document in sorted(
                    candidates, key=lambda item: item.definition.version
                )
            )
            raise MaterializationError(
                f"registered stage kind {kind_id} resolved to multiple versions: {versions}"
            )
        document = candidates[0]
        return _registry_binding(_object_ref(document.definition), document), document.definition

    @staticmethod
    def _validate_stage_override_legality(
        stage_kind: RegisteredStageKindDefinition,
        overrides: LoopStageNodeOverrides,
        *,
        source: str,
    ) -> None:
        unsupported = sorted(
            field.value for field in overrides.override_fields() if field not in set(stage_kind.payload.allowed_overrides)
        )
        if unsupported:
            formatted = ", ".join(unsupported)
            raise MaterializationError(
                f"{source} for {stage_kind.id} overrides unsupported fields: {formatted}"
            )

    @staticmethod
    def _validate_stage_override_targets(
        loop_definition: LoopConfigDefinition,
        stage_overrides: tuple[StageInvocationOverride, ...],
    ) -> None:
        node_ids = {node.node_id for node in loop_definition.payload.nodes}
        for override in stage_overrides:
            if override.plane is not loop_definition.payload.plane:
                raise MaterializationError(
                    f"stage override for {override.node_id} targets {override.plane.value}, "
                    f"but loop {loop_definition.id} is {loop_definition.payload.plane.value}"
                )
            if override.node_id not in node_ids:
                raise MaterializationError(
                    f"stage override targets unknown node {override.node_id} in loop {loop_definition.id}"
                )

    @staticmethod
    def _apply_model_binding(
        values: dict[str, Any],
        provenance: dict[str, ProvenanceEntry],
        binding: ModelBinding,
        *,
        source: ProvenanceSource,
        detail: str,
        object_ref: str | None,
        path_prefix: str,
    ) -> None:
        payload = binding.model_dump(mode="python")
        for field_name, value in payload.items():
            if value is None:
                continue
            values[field_name] = value
            _set_provenance(
                provenance,
                f"{path_prefix}.{field_name}",
                ProvenanceLane.STAGE_BINDING,
                source,
                detail,
                object_ref=object_ref,
                value=value.value if isinstance(value, Enum) else value,
            )

    @staticmethod
    def _apply_direct_overrides(
        values: dict[str, Any],
        provenance: dict[str, ProvenanceEntry],
        overrides: LoopStageNodeOverrides,
        *,
        source: ProvenanceSource,
        detail: str,
        path_prefix: str,
    ) -> None:
        payload = overrides.model_dump(mode="python", exclude_none=True)
        for field_name in ("runner", "model", "effort", "allow_search", "prompt_asset_ref", "timeout_seconds"):
            if field_name not in payload:
                continue
            value = payload[field_name]
            values[field_name] = value
            _set_provenance(
                provenance,
                f"{path_prefix}.{field_name}",
                ProvenanceLane.STAGE_BINDING,
                source,
                detail,
                object_ref=None,
                value=value.value if isinstance(value, Enum) else value,
            )

__all__ = [
    "ArchitectureMaterializer",
    "LoopMaterializationOverrides",
    "MaterializationError",
    "MaterializedAssetBinding",
    "MaterializedLoop",
    "MaterializedMode",
    "MaterializedStageBinding",
    "ModeMaterializationOverrides",
    "ProvenanceEntry",
    "ProvenanceLane",
    "ProvenanceSource",
    "ResolvedRegistryBinding",
    "StageInvocationOverride",
]
