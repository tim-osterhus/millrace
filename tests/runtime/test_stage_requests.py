from __future__ import annotations

import json
from types import SimpleNamespace

from millrace_ai.contracts import Plane
from millrace_ai.paths import bootstrap_workspace, workspace_paths
from millrace_ai.runtime.stage_requests import planning_queue_depth
from millrace_ai.workspace.work_inventory import family_counts, queue_depths_by_plane


def test_planning_queue_depth_uses_shared_inventory_for_blueprint_drafts(tmp_path) -> None:
    paths = bootstrap_workspace(workspace_paths(tmp_path / "workspace"))
    blueprint_queue = paths.runtime_root / "blueprints" / "drafts" / "queue"
    blueprint_queue.mkdir(parents=True, exist_ok=True)
    (blueprint_queue / "draft-001.json").write_text(json.dumps({"draft_id": "draft-001"}), encoding="utf-8")

    engine = SimpleNamespace(paths=paths, compiled_plan=None)

    assert planning_queue_depth(engine) == 1
    assert queue_depths_by_plane(paths)[Plane.PLANNING] == 1
    assert family_counts(paths)["blueprint_draft"]["queue"] == 1
