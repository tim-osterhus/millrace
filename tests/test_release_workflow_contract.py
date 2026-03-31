from __future__ import annotations

from pathlib import Path


WORKFLOW_PATH = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "publish-to-pypi.yml"


def test_publish_workflow_smoke_checks_built_wheel_against_generated_workspace() -> None:
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")

    expected_snippets = (
        "Smoke install built wheel and verify generated workspace",
        ".publish-smoke/bin/python -m pip install dist/*.whl",
        "rm -rf /tmp/millrace-publish-smoke",
        ".publish-smoke/bin/millrace init /tmp/millrace-publish-smoke --json",
        ".publish-smoke/bin/millrace --config /tmp/millrace-publish-smoke/millrace.toml health --json",
    )

    for snippet in expected_snippets:
        assert snippet in workflow, snippet
