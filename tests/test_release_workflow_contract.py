from __future__ import annotations

from pathlib import Path


WORKFLOW_PATH = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "publish-to-pypi.yml"


def test_publish_workflow_smoke_checks_built_wheel_against_release_core_contract() -> None:
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")

    expected_snippets = (
        "Smoke install built wheel against the release core contract",
        ".publish-smoke/bin/python -m pip install dist/*.whl",
        'cat > "$SMOKE_BIN/codex" <<\'PY\'',
        '.publish-smoke/bin/millrace --config "$EXEC_WORKSPACE/millrace.toml" health --json',
        '.publish-smoke/bin/millrace --config "$EXEC_WORKSPACE/millrace.toml" doctor --json',
        '.publish-smoke/bin/millrace --config "$EXEC_WORKSPACE/millrace.toml" add-task "Release smoke task" --json',
        '.publish-smoke/bin/millrace --config "$EXEC_WORKSPACE/millrace.toml" start --once --json',
        '.publish-smoke/bin/millrace --config "$RESEARCH_WORKSPACE/millrace.toml" research --json',
        '.publish-smoke/bin/millrace --config "$RESEARCH_WORKSPACE/millrace.toml" supervisor report --recent-events 20 --json',
        '.publish-smoke/bin/millrace --config "$PUBLISH_WORKSPACE/millrace.toml" publish sync --staging-repo-dir "$STAGING_REPO" --json',
        '.publish-smoke/bin/millrace --config "$PUBLISH_WORKSPACE/millrace.toml" publish preflight --staging-repo-dir "$STAGING_REPO" --message "Release smoke publish preflight" --no-push --json',
        'assert exec_doctor_payload["execution_ready"] is True',
        'assert any(event["type"] == "execution.task.archived" for event in exec_events)',
        'assert research_report_payload["configured_mode"] == "auto"',
        'assert research_queue_payload["backlog_depth"] >= 1',
        'assert research_supervisor_payload["attention_reason"] == "idle_with_pending_work"',
        'assert publish_preflight_payload["status"] == "ready"',
    )

    for snippet in expected_snippets:
        assert snippet in workflow, snippet
