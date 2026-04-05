from __future__ import annotations

from pathlib import Path
import json

from millrace_engine.config import build_runtime_paths, load_engine_config
from millrace_engine.contracts import ResearchMode, ResearchStatus
from millrace_engine.markdown import parse_task_store
from millrace_engine.planes.research import ResearchPlane
from millrace_engine.research.audit import load_audit_remediation_record, load_audit_summary
from tests.support import load_workspace_fixture


def _configured_runtime(
    tmp_path: Path,
    *,
    mode: ResearchMode,
) -> tuple[Path, object, object]:
    workspace, config_path = load_workspace_fixture(tmp_path, "control_mailbox")
    loaded = load_engine_config(config_path)
    loaded.config.research.mode = mode
    return workspace, loaded.config, build_runtime_paths(loaded.config)


def _write_audit_file(
    path: Path,
    *,
    audit_id: str,
    scope: str,
    commands: list[str],
    summaries: list[str],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "---",
        f"audit_id: {audit_id}",
        f"scope: {scope}",
        "trigger: manual",
        "status: incoming",
        "owner: qa",
        "created_at: 2026-03-21T12:00:00Z",
        "updated_at: 2026-03-21T12:05:00Z",
        "---",
        "",
        f"# Audit {audit_id}",
        "",
        "## Commands",
        *[f"- {command}" for command in commands],
        "",
        "## Summary",
        *[f"- {summary}" for summary in summaries],
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def _write_completion_manifest(workspace: Path, *, commands: list[str]) -> None:
    manifest_path = workspace / "agents" / "audit" / "completion_manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "profile_id": "completion.manifest.test",
                "configured": True,
                "notes": ["Configured for remediation-helper regression coverage."],
                "required_completion_commands": [
                    {
                        "id": f"cmd-{index}",
                        "required": True,
                        "category": "quality",
                        "timeout_secs": 300,
                        "command": command,
                    }
                    for index, command in enumerate(commands, start=1)
                ],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def test_load_audit_summary_falls_back_to_default_on_invalid_json(tmp_path: Path) -> None:
    workspace, config, paths = _configured_runtime(tmp_path, mode=ResearchMode.AUDIT)
    summary_path = workspace / "agents" / "audit_summary.json"
    summary_path.write_text("{not-json}\n", encoding="utf-8")

    summary = load_audit_summary(paths)

    assert config.research.mode is ResearchMode.AUDIT
    assert summary.counts == {"total": 0, "pass": 0, "fail": 0}
    assert summary.last_outcome is not None
    assert summary.last_outcome.status == "none"


def test_sync_runtime_audit_fail_reuses_existing_remediation_task(tmp_path: Path) -> None:
    workspace, config, paths = _configured_runtime(tmp_path, mode=ResearchMode.AUDIT)
    incoming_path = workspace / "agents" / "ideas" / "audit" / "incoming" / "AUD-705.md"
    required_command = "pytest -q tests/test_research_dispatcher.py --fast"
    _write_audit_file(
        incoming_path,
        audit_id="AUD-705",
        scope="reuse-existing-remediation-task",
        commands=[required_command],
        summaries=["Open issues detected: 1"],
    )
    _write_completion_manifest(workspace, commands=[required_command])
    (workspace / "agents" / "gaps.md").write_text("# Gaps\n\n## Open Gaps\n\n", encoding="utf-8")
    (workspace / "agents" / "audit").mkdir(parents=True, exist_ok=True)
    (workspace / "agents" / "audit" / "strict_contract.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "contract_id": "strict-command-guard-test",
                "enabled": True,
                "description": "Fail closed when sampled commands or missing summaries are observed.",
                "required_command_substrings": ["pytest -q tests/test_research_dispatcher.py"],
                "forbidden_command_markers": ["--fast"],
                "required_summaries": ["Open issues detected: 0"],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (workspace / "agents" / "tasksbacklog.md").write_text(
        "\n".join(
            [
                "# Backlog",
                "",
                "## 2026-03-21 - Existing remediation work",
                "",
                "- **Spec-ID:** SPEC-AUD-705-REMEDIATION",
                "- **Goal:** Reuse the pre-existing remediation task.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    plane = ResearchPlane(config, paths)

    dispatch = plane.sync_runtime(trigger="engine-start", run_id="audit-sync-705", resolve_assets=False)

    assert dispatch is not None
    assert plane.status_store.read() is ResearchStatus.AUDIT_FAIL

    remediation_record = load_audit_remediation_record(paths, run_id="audit-sync-705")
    backlog_cards = parse_task_store(
        (workspace / "agents" / "tasksbacklog.md").read_text(encoding="utf-8"),
        source_file=workspace / "agents" / "tasksbacklog.md",
    ).cards

    assert remediation_record is not None
    assert remediation_record.selected_action == "reuse_existing_task"
    assert remediation_record.remediation_spec_id == "SPEC-AUD-705-REMEDIATION"
    assert remediation_record.remediation_task_title == "Existing remediation work"
    assert remediation_record.remediation_task_id == backlog_cards[0].task_id
    assert remediation_record.backlog_depth_after_enqueue == 1
    assert len(backlog_cards) == 1
