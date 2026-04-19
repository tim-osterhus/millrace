from __future__ import annotations

from datetime import datetime, timezone

from millrace_ai.contracts import ClosureTargetState
from millrace_ai.paths import bootstrap_workspace, workspace_paths
from millrace_ai.workspace.arbiter_state import (
    list_open_closure_target_states,
    load_closure_target_state,
    save_closure_target_state,
    write_canonical_idea_contract,
    write_canonical_root_spec_contract,
)

NOW = datetime(2026, 4, 19, tzinfo=timezone.utc)


def _closure_target_state() -> ClosureTargetState:
    return ClosureTargetState(
        root_spec_id="spec-root-001",
        root_idea_id="idea-001",
        root_spec_path="millrace-agents/arbiter/contracts/root-specs/spec-root-001.md",
        root_idea_path="millrace-agents/arbiter/contracts/ideas/idea-001.md",
        rubric_path="millrace-agents/arbiter/rubrics/spec-root-001.md",
        latest_verdict_path="millrace-agents/arbiter/verdicts/spec-root-001.json",
        latest_report_path="millrace-agents/arbiter/reports/run-001.md",
        closure_open=True,
        closure_blocked_by_lineage_work=False,
        blocking_work_ids=(),
        opened_at=NOW,
        last_arbiter_run_id="run-001",
    )


def test_closure_target_state_round_trips_via_workspace_helpers(tmp_path) -> None:
    paths = bootstrap_workspace(workspace_paths(tmp_path / "workspace"))
    target = _closure_target_state()

    saved_path = save_closure_target_state(paths, target)
    loaded = load_closure_target_state(paths, root_spec_id=target.root_spec_id)

    assert saved_path == paths.arbiter_targets_dir / "spec-root-001.json"
    assert loaded == target


def test_list_open_closure_target_states_returns_only_open_targets(tmp_path) -> None:
    paths = bootstrap_workspace(workspace_paths(tmp_path / "workspace"))
    open_target = _closure_target_state()
    closed_target = open_target.model_copy(
        update={
            "root_spec_id": "spec-root-closed",
            "closure_open": False,
            "closed_at": NOW,
            "last_arbiter_run_id": "run-002",
        }
    )

    save_closure_target_state(paths, open_target)
    save_closure_target_state(paths, closed_target)

    assert list_open_closure_target_states(paths) == (open_target,)


def test_canonical_contract_copy_helpers_write_expected_markdown_paths(tmp_path) -> None:
    paths = bootstrap_workspace(workspace_paths(tmp_path / "workspace"))

    idea_path = write_canonical_idea_contract(
        paths,
        root_idea_id="idea-001",
        markdown="# Seed Idea\n\nSource of truth.\n",
    )
    spec_path = write_canonical_root_spec_contract(
        paths,
        root_spec_id="spec-root-001",
        markdown="# Root Spec\n\nAuthoritative interpretation.\n",
    )

    assert idea_path == paths.arbiter_idea_contracts_dir / "idea-001.md"
    assert spec_path == paths.arbiter_root_spec_contracts_dir / "spec-root-001.md"
    assert idea_path.read_text(encoding="utf-8") == "# Seed Idea\n\nSource of truth.\n"
    assert spec_path.read_text(encoding="utf-8") == "# Root Spec\n\nAuthoritative interpretation.\n"
