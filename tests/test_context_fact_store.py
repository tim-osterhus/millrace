from __future__ import annotations

from pathlib import Path

from millrace_engine.config import build_runtime_paths, load_engine_config
from millrace_engine.context_facts import (
    context_fact_for_id,
    discover_context_facts,
    load_retrievable_context_facts,
    persist_context_fact,
)
from millrace_engine.contracts import ContextFactArtifact, ContextFactLifecycleState, ContextFactScope, StageType
from tests.support import runtime_workspace


def _workspace_fact(*, fact_id: str, lifecycle_state: ContextFactLifecycleState) -> ContextFactArtifact:
    stale_reason = "Superseded by fresher evidence." if lifecycle_state is ContextFactLifecycleState.STALE else None
    return ContextFactArtifact(
        fact_id=fact_id,
        scope=ContextFactScope.WORKSPACE,
        lifecycle_state=lifecycle_state,
        source_run_id="run-201",
        source_stage=StageType.BUILDER,
        title=f"Title for {fact_id}",
        statement=f"Statement for {fact_id}",
        summary=f"Summary for {fact_id}",
        created_at="2026-04-07T18:00:00Z",
        stale_reason=stale_reason,
    )


def test_context_fact_store_persists_workspace_and_run_scoped_artifacts(tmp_path: Path) -> None:
    workspace_root, config_path = runtime_workspace(tmp_path)
    loaded = load_engine_config(config_path)
    paths = build_runtime_paths(loaded.config)

    workspace_fact = _workspace_fact(
        fact_id="fact.workspace.builder.001",
        lifecycle_state=ContextFactLifecycleState.PROMOTED,
    )
    run_fact = ContextFactArtifact(
        fact_id="fact.run.builder.001",
        scope=ContextFactScope.RUN,
        lifecycle_state=ContextFactLifecycleState.CANDIDATE,
        source_run_id="run-202",
        source_stage=StageType.BUILDER,
        title="Run fact",
        statement="Run-scoped fact statement",
        summary="Run-scoped fact summary",
        created_at="2026-04-07T19:00:00Z",
    )

    workspace_path = persist_context_fact(paths, workspace_fact)
    run_path = persist_context_fact(paths, run_fact)

    assert workspace_path == (
        workspace_root / "agents/compounding/context_facts/fact.workspace.builder.001.json"
    ).resolve()
    assert run_path == (
        workspace_root / "agents/compounding/context_facts/run-202/fact.run.builder.001.json"
    ).resolve()
    assert workspace_path.read_text(encoding="utf-8").strip().startswith("{")
    assert run_path.read_text(encoding="utf-8").strip().startswith("{")


def test_context_fact_store_discovers_and_classifies_retrieval_status(tmp_path: Path) -> None:
    _, config_path = runtime_workspace(tmp_path)
    loaded = load_engine_config(config_path)
    paths = build_runtime_paths(loaded.config)

    promoted = _workspace_fact(
        fact_id="fact.workspace.promoted.001",
        lifecycle_state=ContextFactLifecycleState.PROMOTED,
    )
    stale = _workspace_fact(
        fact_id="fact.workspace.stale.001",
        lifecycle_state=ContextFactLifecycleState.STALE,
    )
    deprecated = _workspace_fact(
        fact_id="fact.workspace.deprecated.001",
        lifecycle_state=ContextFactLifecycleState.DEPRECATED,
    )
    run_candidate = ContextFactArtifact(
        fact_id="fact.run.qa.001",
        scope=ContextFactScope.RUN,
        lifecycle_state=ContextFactLifecycleState.CANDIDATE,
        source_run_id="run-203",
        source_stage=StageType.QA,
        title="Run candidate fact",
        statement="Run candidate statement",
        summary="Run candidate summary",
        created_at="2026-04-07T20:00:00Z",
    )

    for artifact in (promoted, stale, deprecated, run_candidate):
        persist_context_fact(paths, artifact)

    discovered = {item.artifact.fact_id: item for item in discover_context_facts(paths)}

    assert discovered["fact.workspace.promoted.001"].retrieval_status == "eligible"
    assert discovered["fact.workspace.promoted.001"].eligible_for_retrieval is True
    assert discovered["fact.workspace.stale.001"].retrieval_status == "stale"
    assert discovered["fact.workspace.deprecated.001"].retrieval_status == "deprecated"
    assert discovered["fact.run.qa.001"].retrieval_status == "run_candidate"
    assert [artifact.fact_id for artifact in load_retrievable_context_facts(paths)] == [
        "fact.workspace.promoted.001"
    ]


def test_context_fact_store_resolves_fact_by_id(tmp_path: Path) -> None:
    _, config_path = runtime_workspace(tmp_path)
    loaded = load_engine_config(config_path)
    paths = build_runtime_paths(loaded.config)

    artifact = _workspace_fact(
        fact_id="fact.workspace.resolve.001",
        lifecycle_state=ContextFactLifecycleState.PROMOTED,
    )
    persist_context_fact(paths, artifact)

    stored = context_fact_for_id(paths, "fact.workspace.resolve.001", include_run_candidates=False)

    assert stored.artifact == artifact
    assert stored.retrieval_status == "eligible"
