from __future__ import annotations

from pathlib import Path


WORKFLOW_PATH = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "publish-to-pypi.yml"


def test_publish_workflow_smoke_checks_built_wheel_against_release_core_contract() -> None:
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")

    expected_snippets = (
        "Smoke install built wheel against the release core contract",
        ".publish-smoke/bin/python -m pip install dist/*.whl",
        "rm -rf /tmp/millrace-publish-smoke",
        "printf '#!/bin/sh\\nexit 0\\n' > /tmp/millrace-publish-smoke-bin/codex",
        ".publish-smoke/bin/millrace init /tmp/millrace-publish-smoke --json",
        ".publish-smoke/bin/millrace --config /tmp/millrace-publish-smoke/millrace.toml health --json",
        ".publish-smoke/bin/millrace --config /tmp/millrace-publish-smoke/millrace.toml doctor --json",
        '.publish-smoke/bin/millrace --config /tmp/millrace-publish-smoke/millrace.toml add-task "Release smoke task" --json',
        ".publish-smoke/bin/millrace --config /tmp/millrace-publish-smoke/millrace.toml queue inspect --json",
        'assert doctor_payload["execution_ready"] is True',
        'assert queue_payload["backlog_depth"] == 1',
    )

    for snippet in expected_snippets:
        assert snippet in workflow, snippet
