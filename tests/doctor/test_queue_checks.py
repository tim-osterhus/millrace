from __future__ import annotations

from pathlib import Path

from millrace_ai.doctor.checks import DoctorContext
from millrace_ai.doctor.queue_checks import check_queue_parseability
from millrace_ai.paths import bootstrap_workspace, workspace_paths


def test_doctor_passes_for_bootstrapped_workspace(tmp_path: Path) -> None:
    paths = workspace_paths(tmp_path / "workspace")
    bootstrap_workspace(paths)
    context = DoctorContext(paths=paths, assets_root=paths.runtime_root)

    check_queue_parseability(context)

    assert context.errors == []


def test_doctor_flags_invalid_blueprint_draft_queue_artifact(tmp_path: Path) -> None:
    paths = workspace_paths(tmp_path / "workspace")
    bootstrap_workspace(paths)
    context = DoctorContext(paths=paths, assets_root=paths.runtime_root)
    blueprint_root = paths.runtime_root / "blueprints" / "drafts" / "queue"
    blueprint_root.mkdir(parents=True, exist_ok=True)
    (blueprint_root / "broken.json").write_text("{ not-json", encoding="utf-8")
    check_queue_parseability(context)

    assert context.errors
    assert any(issue.code == "queue_artifact_invalid" for issue in context.errors)
