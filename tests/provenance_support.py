from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from millrace_engine.compiler import FrozenRunCompiler
from millrace_engine.config import build_runtime_paths, load_engine_config
from millrace_engine.contracts import ControlPlane, PersistedObjectKind, RegistryObjectRef
from millrace_engine.provenance import BoundExecutionParameters, TransitionHistoryStore
from millrace_engine.registry import discover_registry_state, persist_workspace_registry_object
from millrace_engine.standard_runtime import compile_standard_runtime_selection

from tests.support import load_workspace_fixture


@dataclass(slots=True)
class ProvenanceFixture:
    workspace: Path
    config_path: Path
    config: Any
    paths: Any


def load_provenance_fixture(tmp_path: Path, name: str = "golden_path") -> ProvenanceFixture:
    workspace, config_path = load_workspace_fixture(tmp_path, name)
    loaded = load_engine_config(config_path)
    config = loaded.config
    config.paths.workspace = workspace
    config.paths.agents_dir = workspace / "agents"
    return ProvenanceFixture(
        workspace=workspace,
        config_path=config_path,
        config=config,
        paths=build_runtime_paths(config),
    )


def compile_standard_provenance(context: ProvenanceFixture, *, run_id: str):
    return compile_standard_runtime_selection(context.config, context.paths, run_id=run_id)


def compile_mode_provenance(
    context: ProvenanceFixture,
    *,
    run_id: str,
    mode_id: str,
    version: str = "1.0.0",
):
    return FrozenRunCompiler(context.paths).compile_mode(
        RegistryObjectRef(
            kind=PersistedObjectKind.MODE,
            id=mode_id,
            version=version,
        ),
        run_id=run_id,
    )


def packaged_definition(
    workspace: Path,
    *,
    kind: str,
    object_id: str,
    version: str = "1.0.0",
):
    return next(
        document.definition
        for document in discover_registry_state(workspace, validate_catalog=False).packaged
        if document.key == (kind, object_id, version)
    )


def persist_packaged_shadow(
    workspace: Path,
    *,
    kind: str,
    object_id: str,
    title: str,
    aliases: Iterable[str] = (),
    version: str = "1.0.0",
):
    packaged = packaged_definition(workspace, kind=kind, object_id=object_id, version=version)
    shadow_payload = packaged.model_dump(mode="json")
    shadow_payload["title"] = title
    shadow_payload["aliases"] = list(aliases)
    shadow_payload["source"] = {"kind": "workspace_defined"}
    persist_workspace_registry_object(
        workspace,
        packaged.__class__.model_validate(shadow_payload),
    )
    return packaged


def prompt_path(workspace: Path) -> Path:
    return workspace / "agents" / "_start.md"


def append_stage_transition_record(
    context: ProvenanceFixture,
    *,
    run_id: str,
    snapshot,
    node_id: str = "builder",
    kind_id: str = "execution.builder",
    outcome: str = "success",
    selected_edge_id: str = "execution.builder.success.qa",
    selected_edge_reason: str = "builder completed in the fixed v1 execution plane",
    status_before: str = "IDLE",
    status_after: str = "BUILDER_COMPLETE",
    model: str = "fixture-model",
    attributes: dict[str, object] | None = None,
):
    return TransitionHistoryStore(
        context.paths.runs_dir / run_id / "transition_history.jsonl",
        run_id=run_id,
        provenance=snapshot.runtime_provenance_context(),
    ).append(
        event_name="execution.stage.transition",
        source="execution_plane",
        plane=ControlPlane.EXECUTION,
        node_id=node_id,
        kind_id=kind_id,
        outcome=outcome,
        selected_edge_id=selected_edge_id,
        selected_edge_reason=selected_edge_reason,
        status_before=status_before,
        status_after=status_after,
        bound_execution_parameters=BoundExecutionParameters(model=model),
        attributes=attributes or {"routing_mode": "frozen_plan"},
    )
