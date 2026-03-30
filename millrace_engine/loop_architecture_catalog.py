"""Catalog and discriminated-union contracts for loop architecture."""

from __future__ import annotations

from typing import Annotated, TypeAlias

from pydantic import Field, model_validator

from .contracts import ContractModel
from .loop_architecture_common import ControlPlane, RegistryObjectRef, _matches_stage_selector_set
from .loop_architecture_loop_contracts import LoopConfigDefinition
from .loop_architecture_profile_contracts import (
    ModeDefinition,
    ModelProfileDefinition,
    TaskAuthoringProfileDefinition,
)
from .loop_architecture_stage_contracts import RegisteredStageKindDefinition


PersistedArchitectureObject: TypeAlias = Annotated[
    RegisteredStageKindDefinition
    | LoopConfigDefinition
    | ModeDefinition
    | TaskAuthoringProfileDefinition
    | ModelProfileDefinition,
    Field(discriminator="kind"),
]


class LoopArchitectureCatalog(ContractModel):
    objects: tuple[PersistedArchitectureObject, ...] = ()

    @model_validator(mode="after")
    def validate_catalog(self) -> "LoopArchitectureCatalog":
        object_map: dict[tuple[str, str, str], PersistedArchitectureObject] = {}
        stage_versions: dict[str, list[RegisteredStageKindDefinition]] = {}
        loop_defs: dict[tuple[str, str], LoopConfigDefinition] = {}

        for obj in self.objects:
            key = (obj.kind, obj.id, obj.version)
            if key in object_map:
                raise ValueError(f"catalog contains duplicate object {obj.kind}:{obj.id}@{obj.version}")
            object_map[key] = obj
            if isinstance(obj, RegisteredStageKindDefinition):
                stage_versions.setdefault(obj.payload.kind_id, []).append(obj)
            if isinstance(obj, LoopConfigDefinition):
                loop_defs[(obj.id, obj.version)] = obj

        for obj in self.objects:
            if isinstance(obj, LoopConfigDefinition):
                self._validate_loop(obj, object_map, stage_versions)
            elif isinstance(obj, ModeDefinition):
                self._validate_mode(obj, object_map, loop_defs)
            elif isinstance(obj, ModelProfileDefinition):
                self._validate_model_profile(obj, object_map, stage_versions)

        return self

    @staticmethod
    def _require_object(
        ref: RegistryObjectRef,
        object_map: dict[tuple[str, str, str], PersistedArchitectureObject],
    ) -> PersistedArchitectureObject:
        key = (ref.kind.value, ref.id, ref.version)
        try:
            return object_map[key]
        except KeyError as exc:
            raise ValueError(f"catalog is missing referenced object {ref.kind.value}:{ref.id}@{ref.version}") from exc

    @classmethod
    def _resolve_stage_kind(
        cls,
        kind_id: str,
        stage_versions: dict[str, list[RegisteredStageKindDefinition]],
    ) -> RegisteredStageKindDefinition:
        candidates = stage_versions.get(kind_id, [])
        if not candidates:
            raise ValueError(f"catalog is missing registered stage kind {kind_id}")
        if len(candidates) > 1:
            raise ValueError(
                f"catalog contains multiple versions of registered stage kind {kind_id}; resolved validation requires one"
            )
        return candidates[0]

    @classmethod
    def _validate_loop(
        cls,
        loop_def: LoopConfigDefinition,
        object_map: dict[tuple[str, str, str], PersistedArchitectureObject],
        stage_versions: dict[str, list[RegisteredStageKindDefinition]],
    ) -> None:
        if loop_def.payload.task_authoring_profile_ref is not None:
            cls._require_object(loop_def.payload.task_authoring_profile_ref, object_map)
        if loop_def.payload.model_profile_ref is not None:
            cls._require_object(loop_def.payload.model_profile_ref, object_map)

        node_stage_kinds: dict[str, RegisteredStageKindDefinition] = {}
        for node in loop_def.payload.nodes:
            stage_kind = cls._resolve_stage_kind(node.kind_id, stage_versions)
            if stage_kind.payload.plane is not loop_def.payload.plane:
                raise ValueError(
                    f"loop {loop_def.id} node {node.node_id} references {stage_kind.payload.plane.value} stage kind {node.kind_id} from a {loop_def.payload.plane.value} loop"
                )
            stage_kind.payload.validate_loop_node(node)
            node_stage_kinds[node.node_id] = stage_kind

        entry_stage_kind = node_stage_kinds[loop_def.payload.entry_node_id]
        if entry_stage_kind.payload.legal_predecessors:
            formatted = ", ".join(entry_stage_kind.payload.legal_predecessors)
            raise ValueError(
                f"loop {loop_def.id} entry node {loop_def.payload.entry_node_id} uses "
                f"{entry_stage_kind.payload.kind_id} which requires legal predecessors: {formatted}"
            )

        for node in loop_def.payload.nodes:
            for binding in node.artifact_bindings:
                source_stage_kind = node_stage_kinds.get(binding.source_node_id)
                if source_stage_kind is None:
                    raise ValueError(
                        f"loop {loop_def.id} node {node.node_id} binds source node {binding.source_node_id} which is not declared in the loop"
                    )
                source_artifacts = {
                    artifact.name for artifact in source_stage_kind.payload.output_artifacts
                }
                if binding.source_artifact not in source_artifacts:
                    raise ValueError(
                        f"loop {loop_def.id} node {node.node_id} binds unknown source artifact {binding.source_artifact} from node {binding.source_node_id}"
                    )

        for edge in loop_def.payload.edges:
            stage_kind = node_stage_kinds[edge.from_node_id]
            allowed_triggers = set(stage_kind.payload.routing_outcomes) | set(
                stage_kind.payload.terminal_statuses
            )
            invalid = sorted(trigger for trigger in edge.on_outcomes if trigger not in allowed_triggers)
            if invalid:
                formatted = ", ".join(invalid)
                raise ValueError(
                    f"loop {loop_def.id} edge {edge.edge_id} uses triggers not declared by {stage_kind.payload.kind_id}: {formatted}"
                )

            success_triggers = {"success", *stage_kind.payload.success_statuses}
            if success_triggers.intersection(edge.on_outcomes):
                transition_trigger = "on a success trigger"
            elif "blocked" in edge.on_outcomes:
                transition_trigger = "on a blocked trigger"
            else:
                formatted_triggers = ", ".join(sorted(edge.on_outcomes))
                transition_trigger = f"on trigger(s): {formatted_triggers}"
            if edge.to_node_id is None:
                if success_triggers.intersection(edge.on_outcomes) and stage_kind.payload.legal_successors:
                    formatted = ", ".join(stage_kind.payload.legal_successors)
                    raise ValueError(
                        f"loop {loop_def.id} edge {edge.edge_id} ends {stage_kind.payload.kind_id} on a success trigger "
                        f"but legal_successors requires: {formatted}"
                    )
                continue

            next_stage_kind = node_stage_kinds[edge.to_node_id]
            if stage_kind.payload.legal_successors and not _matches_stage_selector_set(
                next_stage_kind.payload.kind_id,
                stage_kind.payload.legal_successors,
            ):
                formatted = ", ".join(stage_kind.payload.legal_successors)
                raise ValueError(
                    f"loop {loop_def.id} edge {edge.edge_id} routes {stage_kind.payload.kind_id} to "
                    f"{next_stage_kind.payload.kind_id} {transition_trigger} but legal_successors allows: {formatted}"
                )
            if next_stage_kind.payload.legal_predecessors and not _matches_stage_selector_set(
                stage_kind.payload.kind_id,
                next_stage_kind.payload.legal_predecessors,
            ):
                formatted = ", ".join(next_stage_kind.payload.legal_predecessors)
                raise ValueError(
                    f"loop {loop_def.id} edge {edge.edge_id} routes into {next_stage_kind.payload.kind_id} from "
                    f"{stage_kind.payload.kind_id} {transition_trigger} but legal_predecessors allows: {formatted}"
                )

    @classmethod
    def _validate_mode(
        cls,
        mode_def: ModeDefinition,
        object_map: dict[tuple[str, str, str], PersistedArchitectureObject],
        loop_defs: dict[tuple[str, str], LoopConfigDefinition],
    ) -> None:
        execution_obj = cls._require_object(mode_def.payload.execution_loop_ref, object_map)
        if not isinstance(execution_obj, LoopConfigDefinition):
            raise ValueError("execution_loop_ref must resolve to a loop_config object")
        if execution_obj.payload.plane is not ControlPlane.EXECUTION:
            raise ValueError(
                f"mode {mode_def.id} execution_loop_ref must resolve to an execution loop, got {execution_obj.payload.plane.value}"
            )

        if mode_def.payload.research_loop_ref is not None:
            research_obj = cls._require_object(mode_def.payload.research_loop_ref, object_map)
            if not isinstance(research_obj, LoopConfigDefinition):
                raise ValueError("research_loop_ref must resolve to a loop_config object")
            if research_obj.payload.plane is not ControlPlane.RESEARCH:
                raise ValueError(
                    f"mode {mode_def.id} research_loop_ref must resolve to a research loop, got {research_obj.payload.plane.value}"
                )

        cls._require_object(mode_def.payload.task_authoring_profile_ref, object_map)
        if mode_def.payload.model_profile_ref is not None:
            cls._require_object(mode_def.payload.model_profile_ref, object_map)

    @classmethod
    def _validate_model_profile(
        cls,
        profile_def: ModelProfileDefinition,
        object_map: dict[tuple[str, str, str], PersistedArchitectureObject],
        stage_versions: dict[str, list[RegisteredStageKindDefinition]],
    ) -> None:
        for scoped_default in profile_def.payload.scoped_defaults:
            cls._require_object(scoped_default.target_ref, object_map)
        for stage_override in profile_def.payload.stage_overrides:
            cls._resolve_stage_kind(stage_override.kind_id, stage_versions)


__all__ = [
    "LoopArchitectureCatalog",
    "PersistedArchitectureObject",
]
