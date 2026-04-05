from __future__ import annotations

from pathlib import Path
from threading import Thread
import json
import os
import pytest
import subprocess
import sys
import time

from typer.testing import CliRunner

from millrace_engine.cli import app
from millrace_engine.config import build_runtime_paths, load_engine_config
from millrace_engine.control import EngineControl
from millrace_engine.contracts import (
    AuditGateDecision,
    AuditGateDecisionCounts,
    CompletionDecision,
    CrossPlaneParentRun,
    ExecutionStatus,
    ExecutionResearchHandoff,
    ModelProfileDefinition,
    PersistedObjectKind,
    ResearchStatus,
    ResearchRecoveryDecision,
    RegistryObjectRef,
    StageType,
)
from millrace_engine.events import EventRecord, EventSource, EventType
from millrace_engine.engine import MillraceEngine
from millrace_engine.control_models import RuntimeState
from millrace_engine.markdown import parse_task_cards
from millrace_engine.planes.research import ResearchPlane
from millrace_engine.policies.outage import OutageProbeResult, StaticOutageProbe
from millrace_engine.policies.transport import TransportProbeResult, TransportReadiness
from millrace_engine.provenance import read_transition_history
from millrace_engine.queue import TaskQueue, load_research_recovery_latch
from millrace_engine.research.specs import GoalSpecFamilyState, build_initial_family_plan_snapshot
from millrace_engine.registry import discover_registry_state, persist_workspace_registry_object
from millrace_engine.standard_runtime import compile_standard_runtime_selection
from tests.support import load_workspace_fixture, read_state, set_engine_idle_mode, wait_for


RUNNER = CliRunner()


def cli_subprocess_env() -> dict[str, str]:
    env = os.environ.copy()
    existing_pythonpath = env.get("PYTHONPATH")
    repo_pythonpath = str(Path(__file__).resolve().parents[1])
    env["PYTHONPATH"] = (
        repo_pythonpath if not existing_pythonpath else f"{repo_pythonpath}{os.pathsep}{existing_pythonpath}"
    )
    return env


def run_cli_subprocess(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "millrace_engine", *args],
        cwd=cwd,
        env=cli_subprocess_env(),
        capture_output=True,
        text=True,
        check=False,
    )


def fake_runner_env(tmp_path: Path, *, executables: tuple[str, ...]) -> dict[str, str]:
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir(exist_ok=True)
    for executable in executables:
        path = fake_bin / executable
        path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        path.chmod(0o755)
    return {"PATH": str(fake_bin)}


def git_cli(repo_dir: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", repo_dir.as_posix(), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def read_event_types(workspace: Path) -> list[str]:
    return [
        json.loads(line)["type"]
        for line in (workspace / "agents/engine_events.log").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def write_audit_queue_file(
    workspace: Path,
    *,
    audit_id: str,
    command: str,
    scope: str = "cli-audit",
    summaries: list[str] | None = None,
) -> Path:
    path = workspace / "agents" / "ideas" / "audit" / "incoming" / f"{audit_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    summary_lines = summaries or ["Completion gate coverage for CLI reporting."]
    path.write_text(
        "\n".join(
            [
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
                f"- {command}",
                "",
                "## Summary",
                *[f"- {summary}" for summary in summary_lines],
                "",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def write_completion_manifest(workspace: Path, *, command: str) -> None:
    manifest_path = workspace / "agents" / "audit" / "completion_manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "profile_id": "cli-completion-manifest",
                "configured": True,
                "notes": ["CLI reporting coverage manifest."],
                "required_completion_commands": [
                    {
                        "id": "cli-command-1",
                        "required": True,
                        "category": "quality",
                        "timeout_secs": 300,
                        "command": command,
                    }
                ],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def write_staging_manifest(workspace: Path, *, payload: str) -> None:
    manifest_path = workspace / "agents" / "staging_manifest.yml"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(payload, encoding="utf-8")


def write_typed_objective_contract(
    workspace: Path,
    *,
    profile_id: str = "cli-legacy-profile",
    goal_id: str = "IDEA-CLI-001",
    title: str = "CLI legacy objective",
    source_path: str = "agents/ideas/raw/goal.md",
    updated_at: str = "2026-03-21T12:05:00Z",
) -> None:
    contract_path = workspace / "agents" / "objective" / "contract.yaml"
    contract_path.parent.mkdir(parents=True, exist_ok=True)
    contract_path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "objective_id": goal_id,
                "objective_root": ".",
                "completion": {
                    "authoritative_decision_file": "agents/reports/completion_decision.json",
                    "fallback_decision_file": "agents/reports/audit_gate_decision.json",
                    "require_task_store_cards_zero": True,
                    "require_open_gaps_zero": True,
                },
                "objective_profile": {
                    "profile_id": profile_id,
                    "title": title,
                    "source_path": source_path,
                    "updated_at": updated_at,
                },
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def write_malformed_typed_objective_contract(
    workspace: Path,
    *,
    objective_id: str = "OBJ-CLI-BROKEN-001",
    fallback_decision_file: str = "agents/custom/broken_gate.json",
    authoritative_decision_file: str = "agents/custom/broken_completion.json",
) -> None:
    contract_path = workspace / "agents" / "objective" / "contract.yaml"
    contract_path.parent.mkdir(parents=True, exist_ok=True)
    contract_path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "objective_id": objective_id,
                "completion": {
                    "authoritative_decision_file": authoritative_decision_file,
                    "fallback_decision_file": fallback_decision_file,
                    "require_task_store_cards_zero": True,
                    "require_open_gaps_zero": True,
                },
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def write_empty_gaps_file(workspace: Path) -> None:
    (workspace / "agents" / "gaps.md").write_text(
        "\n".join(
            [
                "# Gaps",
                "",
                "## Open Gaps",
                "",
                "| Gap ID | Title | Area | Owner | Severity | Status | Notes |",
                "| --- | --- | --- | --- | --- | --- | --- |",
                "",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def write_decision_reports(
    workspace: Path,
    *,
    gate_rel_path: str,
    completion_rel_path: str,
) -> None:
    counts = AuditGateDecisionCounts(
        required_total=1,
        required_pass=1,
        required_fail=0,
        required_blocked=0,
        completion_required=1,
        completion_pass=1,
        open_gaps=0,
        task_store_cards=0,
        active_task_cards=0,
        backlog_cards=0,
        pending_task_cards=0,
    )
    gate_path = workspace / gate_rel_path
    completion_path = workspace / completion_rel_path
    gate_path.parent.mkdir(parents=True, exist_ok=True)
    completion_path.parent.mkdir(parents=True, exist_ok=True)
    gate_path.write_text(
        AuditGateDecision(
            run_id="cli-broken-contract-run",
            audit_id="AUD-CLI-BROKEN",
            generated_at="2026-03-21T12:10:00Z",
            decision="PASS",
            counts=counts,
            gate_decision_path=gate_rel_path,
            objective_contract_path="agents/objective/contract.yaml",
            completion_manifest_path="agents/audit/completion_manifest.json",
            execution_report_path="agents/.research_runtime/audit/execution/cli-broken-contract-run.json",
            validate_record_path="agents/.research_runtime/audit/validate/cli-broken-contract-run.json",
        ).model_dump_json(indent=2)
        + "\n",
        encoding="utf-8",
    )
    completion_path.write_text(
        CompletionDecision(
            run_id="cli-broken-contract-run",
            audit_id="AUD-CLI-BROKEN",
            generated_at="2026-03-21T12:10:00Z",
            decision="PASS",
            counts=counts,
            completion_decision_path=completion_rel_path,
            gate_decision_path=gate_rel_path,
            objective_contract_path="agents/objective/contract.yaml",
        ).model_dump_json(indent=2)
        + "\n",
        encoding="utf-8",
    )


def _configure_complexity_routing_profiles(workspace: Path, config_path: Path) -> None:
    config_path.write_text(
        config_path.read_text(encoding="utf-8")
        + "\n".join(
            [
                "",
                "[policies.complexity]",
                "enabled = true",
                'default_band = "moderate"',
                "",
                "[policies.complexity.profiles.moderate]",
                'kind = "model_profile"',
                'id = "model.workspace.moderate"',
                'version = "1.0.0"',
                "",
                "[policies.complexity.profiles.involved]",
                'kind = "model_profile"',
                'id = "model.workspace.involved"',
                'version = "1.0.0"',
                "",
                "[policies.complexity.profiles.complex]",
                'kind = "model_profile"',
                'id = "model.workspace.complex"',
                'version = "1.0.0"',
                "",
            ]
        ),
        encoding="utf-8",
    )

    def _profile(
        object_id: str,
        *,
        builder_model: str,
        qa_model: str,
        hotfix_model: str,
        doublecheck_model: str,
    ) -> ModelProfileDefinition:
        return ModelProfileDefinition.model_validate(
            {
                "id": object_id,
                "version": "1.0.0",
                "tier": "golden",
                "title": f"{object_id} profile",
                "summary": "Workspace complexity-routing profile for CLI selection tests.",
                "source": {"kind": "workspace_defined"},
                "payload": {
                    "default_binding": {
                        "runner": "codex",
                        "model": "default-shared-model",
                        "effort": "medium",
                        "allow_search": False,
                    },
                    "scoped_defaults": [],
                    "stage_overrides": (
                        {
                            "kind_id": "execution.builder",
                            "binding": {"runner": "codex", "model": builder_model, "effort": "high"},
                        },
                        {
                            "kind_id": "execution.qa",
                            "binding": {"runner": "codex", "model": qa_model, "effort": "xhigh"},
                        },
                        {
                            "kind_id": "execution.hotfix",
                            "binding": {"runner": "codex", "model": hotfix_model, "effort": "medium"},
                        },
                        {
                            "kind_id": "execution.doublecheck",
                            "binding": {"runner": "codex", "model": doublecheck_model, "effort": "high"},
                        },
                    ),
                },
            }
        )

    persist_workspace_registry_object(
        workspace,
        _profile(
            "model.workspace.moderate",
            builder_model="builder-moderate-model",
            qa_model="qa-moderate-model",
            hotfix_model="hotfix-moderate-model",
            doublecheck_model="doublecheck-moderate-model",
        ),
    )
    persist_workspace_registry_object(
        workspace,
        _profile(
            "model.workspace.involved",
            builder_model="builder-involved-model",
            qa_model="qa-involved-model",
            hotfix_model="hotfix-involved-model",
            doublecheck_model="doublecheck-involved-model",
        ),
    )
    persist_workspace_registry_object(
        workspace,
        _profile(
            "model.workspace.complex",
            builder_model="builder-complex-model",
            qa_model="qa-complex-model",
            hotfix_model="hotfix-complex-model",
            doublecheck_model="doublecheck-complex-model",
        ),
    )


def read_events(workspace: Path) -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in (workspace / "agents/engine_events.log").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def append_subprocess_stage_config(config_path: Path, *, qa_model: str = "before-model") -> None:
    config_path.write_text(
        config_path.read_text(encoding="utf-8")
        + "\n".join(
            [
                "",
                "[stages.builder]",
                'runner = "subprocess"',
                'model = "builder-model"',
                "timeout_seconds = 30",
                "",
                "[stages.integration]",
                'runner = "subprocess"',
                'model = "integration-model"',
                "timeout_seconds = 30",
                "",
                "[stages.qa]",
                'runner = "subprocess"',
                f'model = "{qa_model}"',
                "timeout_seconds = 30",
                "",
                "[stages.hotfix]",
                'runner = "subprocess"',
                'model = "hotfix-model"',
                "timeout_seconds = 30",
                "",
                "[stages.doublecheck]",
                'runner = "subprocess"',
                'model = "doublecheck-model"',
                "timeout_seconds = 30",
                "",
                "[stages.troubleshoot]",
                'runner = "subprocess"',
                'model = "troubleshoot-model"',
                "timeout_seconds = 30",
                "",
                "[stages.consult]",
                'runner = "subprocess"',
                'model = "consult-model"',
                "timeout_seconds = 30",
                "",
                "[stages.update]",
                'runner = "subprocess"',
                'model = "update-model"',
                "timeout_seconds = 30",
                "",
            ]
        ),
        encoding="utf-8",
    )


def append_outage_policy_config(
    config_path: Path,
    *,
    wait_initial_seconds: int = 0,
    wait_max_seconds: int = 0,
    max_probes: int = 1,
    policy: str = "pause_resume",
    route_to_blocker: bool = False,
    route_to_incident: bool = False,
) -> None:
    config_path.write_text(
        config_path.read_text(encoding="utf-8")
        + "\n".join(
            [
                "",
                "[policies.outage]",
                "enabled = true",
                f"wait_initial_seconds = {wait_initial_seconds}",
                f"wait_max_seconds = {wait_max_seconds}",
                f"max_probes = {max_probes}",
                "probe_timeout_seconds = 1",
                'probe_host = "api.openai.com"',
                "probe_port = 443",
                f'policy = "{policy}"',
                f"route_to_blocker = {'true' if route_to_blocker else 'false'}",
                f"route_to_incident = {'true' if route_to_incident else 'false'}",
                "",
            ]
        ),
        encoding="utf-8",
    )


def append_usage_policy_config(
    config_path: Path,
    *,
    provider: str = "env",
    remaining_threshold: int | None = None,
    consumed_threshold: int | None = None,
) -> None:
    lines = [
        "",
        "[policies.usage]",
        "enabled = true",
        f'provider = "{provider}"',
        "cache_max_age_secs = 0",
        "",
        "[policies.usage.execution]",
    ]
    if remaining_threshold is not None:
        lines.append(f"remaining_threshold = {remaining_threshold}")
    if consumed_threshold is not None:
        lines.append(f"consumed_threshold = {consumed_threshold}")
    lines.extend(['refresh_utc = "MON 00:00"', ""])
    config_path.write_text(
        config_path.read_text(encoding="utf-8") + "\n".join(lines),
        encoding="utf-8",
    )


class SequencedTransportProbe:
    def __init__(self, results: list[TransportProbeResult]) -> None:
        self._results = list(results)
        self._index = 0

    def check(self, context) -> TransportProbeResult:
        if self._index >= len(self._results):
            result = self._results[-1]
        else:
            result = self._results[self._index]
            self._index += 1
        details = dict(result.details)
        details.setdefault("runner", context.runner.value)
        if context.command:
            details.setdefault("command", list(context.command))
        return result.model_copy(update={"command": context.command, "details": details})


def write_hot_swap_stage_driver(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    script = tmp_path / "hot_swap_stage_driver.py"
    gate_path = tmp_path / "builder-release"
    builder_started_path = tmp_path / "builder-started"
    qa_observed_path = tmp_path / "qa-observed"
    script.write_text(
        "\n".join(
            [
                "from __future__ import annotations",
                "",
                "from pathlib import Path",
                "import os",
                "import sys",
                "import time",
                "",
                f"GATE_PATH = Path({str(gate_path)!r})",
                f"BUILDER_STARTED_PATH = Path({str(builder_started_path)!r})",
                f"QA_OBSERVED_PATH = Path({str(qa_observed_path)!r})",
                "",
                "mode = sys.argv[1]",
                "last_path = Path(os.environ['MILLRACE_LAST_RESPONSE_PATH'])",
                "model = os.environ['MILLRACE_MODEL']",
                "",
                "def emit(marker: str | None = None, *, message: str = '') -> None:",
                "    lines: list[str] = []",
                "    if message:",
                "        print(message)",
                "        lines.append(message)",
                "    if marker is not None:",
                "        print(f'### {marker}')",
                "        lines.append(f'### {marker}')",
                "    if lines:",
                "        last_path.write_text('\\n'.join(lines) + '\\n', encoding='utf-8')",
                "    raise SystemExit(0)",
                "",
                "if mode == 'builder-block':",
                "    BUILDER_STARTED_PATH.write_text(model, encoding='utf-8')",
                "    while not GATE_PATH.exists():",
                "        time.sleep(0.05)",
                "    emit('BUILDER_COMPLETE', message=f'builder={model}')",
                "",
                "if mode == 'qa-observe':",
                "    QA_OBSERVED_PATH.write_text(model, encoding='utf-8')",
                "    if model == 'bad-model':",
                "        print('qa rejected bad-model')",
                "        raise SystemExit(9)",
                "    emit('QA_COMPLETE', message=f'qa={model}')",
                "",
                "if mode == 'update-idle':",
                "    print('update complete')",
                "    raise SystemExit(0)",
                "",
                "raise SystemExit(9)",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return script, gate_path, builder_started_path, qa_observed_path


def write_outage_stage_driver(tmp_path: Path) -> Path:
    script = tmp_path / "outage_stage_driver.py"
    script.write_text(
        "\n".join(
            [
                "from __future__ import annotations",
                "",
                "from pathlib import Path",
                "import os",
                "import sys",
                "",
                "mode = sys.argv[1]",
                "last_path = Path(os.environ['MILLRACE_LAST_RESPONSE_PATH'])",
                "",
                "def emit(marker: str | None = None, *, message: str = '') -> None:",
                "    lines: list[str] = []",
                "    if message:",
                "        print(message)",
                "        lines.append(message)",
                "    if marker is not None:",
                "        print(f'### {marker}')",
                "        lines.append(f'### {marker}')",
                "    if lines:",
                "        last_path.write_text('\\n'.join(lines) + '\\n', encoding='utf-8')",
                "    raise SystemExit(0)",
                "",
                "if mode == 'builder':",
                "    emit('BUILDER_COMPLETE', message='builder complete')",
                "if mode == 'integration':",
                "    emit('INTEGRATION_COMPLETE', message='integration complete')",
                "if mode == 'qa':",
                "    emit('QA_COMPLETE', message='qa complete')",
                "if mode == 'update':",
                "    emit('UPDATE_COMPLETE', message='update complete')",
                "raise SystemExit(9)",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return script


def write_needs_research_stage_driver(tmp_path: Path) -> Path:
    script = tmp_path / "needs_research_stage_driver.py"
    script.write_text(
        "\n".join(
            [
                "from __future__ import annotations",
                "",
                "from pathlib import Path",
                "import os",
                "import sys",
                "",
                "mode = sys.argv[1]",
                "last_path = Path(os.environ['MILLRACE_LAST_RESPONSE_PATH'])",
                "",
                "def emit(marker: str | None = None, *, message: str = '', last: str | None = None, code: int = 0) -> None:",
                "    lines: list[str] = []",
                "    if message:",
                "        print(message)",
                "        lines.append(message)",
                "    if marker is not None:",
                "        print(f'### {marker}')",
                "        lines.append(f'### {marker}')",
                "    if last is None and lines:",
                "        last = '\\n'.join(lines) + '\\n'",
                "    if last is not None:",
                "        last_path.write_text(last, encoding='utf-8')",
                "    raise SystemExit(code)",
                "",
                "if mode == 'builder':",
                "    emit('BUILDER_COMPLETE', message='builder complete')",
                "if mode == 'qa-blocked':",
                "    emit('BLOCKED', message='qa blocked')",
                "if mode == 'troubleshoot-blocked':",
                "    emit('BLOCKED', message='troubleshoot blocked')",
                "if mode == 'consult-needs-research':",
                "    emit(",
                "        'NEEDS_RESEARCH',",
                "        message='consult requires research',",
                "        last='Incident: agents/ideas/incidents/incoming/INC-CLI-001.md\\n### NEEDS_RESEARCH\\n',",
                "    )",
                "raise SystemExit(9)",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return script


def write_incident_file(
    workspace: Path,
    *,
    incident_rel_path: Path,
    incident_id: str,
    title: str,
    summary: str,
) -> None:
    incident_path = workspace / incident_rel_path
    incident_path.parent.mkdir(parents=True, exist_ok=True)
    incident_path.write_text(
        "\n".join(
            [
                "---",
                f"incident_id: {incident_id}",
                "status: incoming",
                "severity: S2",
                "opened_at: 2026-03-21T12:00:00Z",
                "updated_at: 2026-03-21T12:05:00Z",
                "---",
                "",
                f"# {title}",
                "",
                "## Summary",
                f"- {summary}",
                "",
            ]
        ),
        encoding="utf-8",
    )


def test_cli_status_json_and_add_task(tmp_path: Path) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "control_mailbox")

    status_result = RUNNER.invoke(app, ["--config", str(config_path), "status", "--json"])
    assert status_result.exit_code == 0
    status_payload = json.loads(status_result.stdout)
    assert status_payload["runtime"]["process_running"] is False
    assert status_payload["runtime"]["execution_status"] == "IDLE"
    assert status_payload["size"]["mode"] == "hybrid"
    assert status_payload["size"]["classified_as"] == "SMALL"
    assert status_payload["size"]["latched_as"] == "SMALL"
    assert status_payload["size"]["task"]["file_count"] == 0
    assert status_payload["size"]["task"]["qualifying_signal_count"] == 0
    assert (workspace / "agents/size_status.md").read_text(encoding="utf-8") == "### SMALL\n"
    assert status_payload["selection"]["scope"] == "preview"
    assert status_payload["selection"]["mode"]["ref"]["id"] == "mode.standard"
    assert status_payload["selection"]["execution_loop"]["ref"]["id"] == "execution.standard"

    add_result = RUNNER.invoke(
        app,
        [
            "--config",
            str(config_path),
            "add-task",
            "Add CLI coverage",
            "--body",
            "- **Goal:** Exercise the control CLI.",
            "--spec-id",
            "SPEC-CLI-001",
        ],
    )
    assert add_result.exit_code == 0
    backlog_cards = parse_task_cards((workspace / "agents/tasksbacklog.md").read_text(encoding="utf-8"))
    assert [card.title for card in backlog_cards] == ["Add CLI coverage"]
    assert backlog_cards[0].spec_id == "SPEC-CLI-001"


def test_cli_status_reports_sticky_size_latch_evidence_when_repo_thresholds_change(
    tmp_path: Path,
) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "control_mailbox")
    config_path.write_text(
        config_path.read_text(encoding="utf-8")
        + "\n".join(
            [
                "",
                "[sizing]",
                'mode = "repo"',
                "",
                "[sizing.repo]",
                "file_count_threshold = 1",
                "nonempty_line_count_threshold = 999999999",
                "",
            ]
        ),
        encoding="utf-8",
    )

    first = RUNNER.invoke(app, ["--config", str(config_path), "status", "--json"])

    assert first.exit_code == 0
    first_payload = json.loads(first.stdout)
    assert first_payload["size"]["classified_as"] == "LARGE"
    assert first_payload["size"]["latched_as"] == "LARGE"
    assert first_payload["size"]["latch_reason"] == "promoted_to_large"
    assert first_payload["selection"]["mode"]["ref"]["id"] == "mode.large"
    assert first_payload["selection"]["execution_loop"]["ref"]["id"] == "execution.large"
    assert first_payload["selection_explanation"]["route_decision"] == "promoted_to_large"
    assert (workspace / "agents/size_status.md").read_text(encoding="utf-8") == "### LARGE\n"

    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            "file_count_threshold = 1",
            "file_count_threshold = 999999999",
        ),
        encoding="utf-8",
    )

    second = RUNNER.invoke(app, ["--config", str(config_path), "status", "--json"])

    assert second.exit_code == 0
    second_payload = json.loads(second.stdout)
    assert second_payload["size"]["classified_as"] == "SMALL"
    assert second_payload["size"]["latched_as"] == "LARGE"
    assert second_payload["size"]["latch_reason"] == "retained_large_latch"
    assert second_payload["selection"]["mode"]["ref"]["id"] == "mode.large"
    assert second_payload["selection"]["execution_loop"]["ref"]["id"] == "execution.large"
    assert second_payload["selection_explanation"]["route_decision"] == "retained_large_latch"
    assert (workspace / "agents/size_status.md").read_text(encoding="utf-8") == "### LARGE\n"


def test_cli_status_reports_route_and_large_profile_explanations(
    tmp_path: Path,
) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "control_mailbox")

    small = RUNNER.invoke(app, ["--config", str(config_path), "status", "--json"])

    assert small.exit_code == 0
    small_payload = json.loads(small.stdout)
    assert small_payload["selection_explanation"]["selected_size"] == "SMALL"
    assert small_payload["selection_explanation"]["route_decision"] == "stayed_small"
    assert small_payload["selection_explanation"]["large_profile_decision"] == "not_applicable"

    packaged_mode = next(
        document.definition
        for document in discover_registry_state(workspace, validate_catalog=False).packaged
        if document.key == ("mode", "mode.large", "1.0.0")
    )
    shadow_payload = packaged_mode.model_dump(mode="json")
    shadow_payload["title"] = "Workspace Large Direct Update Profile"
    shadow_payload["source"] = {"kind": "workspace_defined"}
    shadow_payload["payload"]["execution_loop_ref"] = RegistryObjectRef(
        kind=PersistedObjectKind.LOOP_CONFIG,
        id="execution.large_direct_update",
        version="1.0.0",
    ).model_dump(mode="json")
    persist_workspace_registry_object(
        workspace,
        packaged_mode.__class__.model_validate(shadow_payload),
    )

    config_path.write_text(
        config_path.read_text(encoding="utf-8")
        + "\n".join(
            [
                "",
                "[sizing]",
                'mode = "repo"',
                "",
                "[sizing.repo]",
                "file_count_threshold = 1",
                "nonempty_line_count_threshold = 999999999",
                "",
            ]
        ),
        encoding="utf-8",
    )

    large_json = RUNNER.invoke(app, ["--config", str(config_path), "status", "--json"])
    large_text = RUNNER.invoke(app, ["--config", str(config_path), "status"])

    assert large_json.exit_code == 0
    large_payload = json.loads(large_json.stdout)
    assert large_payload["selection_explanation"]["selected_size"] == "LARGE"
    assert large_payload["selection_explanation"]["route_decision"] == "promoted_to_large"
    assert large_payload["selection_explanation"]["large_profile_decision"] == "alternate_large_profile"
    assert "execution.large_direct_update" in large_payload["selection_explanation"]["large_profile_reason"]

    assert large_text.exit_code == 0
    assert "Selection route: LARGE (" in large_text.stdout
    assert "Large profile: alternate_large_profile" in large_text.stdout
    assert "execution.large_direct_update" in large_text.stdout


def test_cli_status_text_reports_preview_selection_provenance(tmp_path: Path) -> None:
    _, config_path = load_workspace_fixture(tmp_path, "control_mailbox")

    result = RUNNER.invoke(app, ["--config", str(config_path), "status"])

    assert result.exit_code == 0
    assert "Selection scope: preview" in result.stdout
    assert "Preview plan id:" in result.stdout
    assert "Preview plan hash:" in result.stdout
    assert "Frozen plan id:" not in result.stdout
    assert (
        "Mode: mode.standard@1.0.0 [kind=mode, aliases=standard, default-autonomous, "
        "layer=packaged, source=packaged_default, source_ref=registry/modes/mode.standard__1.0.0.json]"
    ) in result.stdout
    assert "Execution loop: execution.standard@1.0.0" in result.stdout
    assert "Bound execution parameters:" in result.stdout
    assert "prompt_source=" in result.stdout


def test_cli_status_reports_complexity_routing_selection_and_stage_bindings(tmp_path: Path) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "control_mailbox")
    _configure_complexity_routing_profiles(workspace, config_path)
    (workspace / "agents" / "tasks.md").write_text(
        "\n".join(
            [
                "# Active Task",
                "",
                "## 2026-03-19 - Complexity routed task",
                "",
                "- **Task-ID:** complexity-routed-task",
                "- **Spec-ID:** SPEC-COMPLEXITY-001",
                "- **Complexity:** INVOLVED",
                "- **Goal:** Exercise the complexity-routing preview report.",
                "",
            ]
        ),
        encoding="utf-8",
    )

    json_result = RUNNER.invoke(app, ["--config", str(config_path), "status", "--json"])

    assert json_result.exit_code == 0
    payload = json.loads(json_result.stdout)
    assert payload["selection"]["complexity"]["enabled"] is True
    assert payload["selection"]["complexity"]["band"] == "involved"
    assert payload["selection"]["complexity"]["reason"] == "task_complexity"
    assert payload["selection"]["complexity"]["selected_model_profile_ref"]["id"] == "model.workspace.involved"
    assert payload["selection"]["complexity"]["routed_node_ids"] == ["builder", "qa", "hotfix", "doublecheck"]
    bound_models = {binding["node_id"]: binding["model"] for binding in payload["selection"]["stage_bindings"]}
    assert bound_models["builder"] == "builder-involved-model"
    assert bound_models["qa"] == "qa-involved-model"
    assert bound_models["hotfix"] == "hotfix-involved-model"
    assert bound_models["doublecheck"] == "doublecheck-involved-model"

    text_result = RUNNER.invoke(app, ["--config", str(config_path), "status"])

    assert text_result.exit_code == 0
    assert (
        "Complexity routing: enabled=yes band=involved reason=task_complexity "
        "task=INVOLVED selected_model_profile=model.workspace.involved@1.0.0 "
        "routed_nodes=builder, qa, hotfix, doublecheck"
    ) in text_result.stdout


def test_cli_status_invalid_toml_fails_on_stderr_without_traceback(tmp_path: Path) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "control_mailbox")
    config_path.write_text("[engine\nmode = 'once'\n", encoding="utf-8")

    result = run_cli_subprocess(workspace, "--config", "millrace.toml", "status")

    assert result.returncode == 1
    assert result.stdout == ""
    assert "config TOML is invalid:" in result.stderr
    assert "Traceback" not in result.stderr


def test_cli_config_show_preview_failure_uses_text_stderr_only(tmp_path: Path) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "control_mailbox")
    EngineControl(config_path).config_set("stages.builder.prompt_file", "agents/missing_builder_prompt.md")

    result = run_cli_subprocess(workspace, "--config", "millrace.toml", "config", "show")

    assert result.returncode == 1
    assert result.stdout == ""
    assert "standard runtime selection preview failed:" in result.stderr
    assert "Traceback" not in result.stderr


def test_cli_config_show_text_uses_preview_plan_labels(tmp_path: Path) -> None:
    _, config_path = load_workspace_fixture(tmp_path, "control_mailbox")

    result = RUNNER.invoke(app, ["--config", str(config_path), "config", "show"])

    assert result.exit_code == 0
    assert "Selection scope: preview" in result.stdout
    assert "Preview plan id:" in result.stdout
    assert "Preview plan hash:" in result.stdout
    assert "Frozen plan id:" not in result.stdout


def test_cli_config_set_unknown_key_json_error_stderr_only(tmp_path: Path) -> None:
    workspace, _ = load_workspace_fixture(tmp_path, "control_mailbox")

    result = run_cli_subprocess(
        workspace,
        "--config",
        "millrace.toml",
        "config",
        "set",
        "nope.bad",
        "1",
        "--json",
    )

    assert result.returncode == 1
    assert result.stdout == ""
    assert "Traceback" not in result.stderr
    payload = json.loads(result.stderr)
    assert payload == {"error": "unknown config key segment: nope"}


def test_cli_queue_reorder_unknown_id_fails_on_stderr_without_traceback(tmp_path: Path) -> None:
    workspace, _ = load_workspace_fixture(tmp_path, "golden_path")
    backlog_ids = [card.task_id for card in parse_task_cards((workspace / "agents/tasksbacklog.md").read_text(encoding="utf-8"))]
    assert backlog_ids
    requested_ids = [*backlog_ids[:-1], "task-2099-12-31-missing"]

    result = run_cli_subprocess(
        workspace,
        "--config",
        "millrace.toml",
        "queue",
        "reorder",
        *requested_ids,
    )

    assert result.returncode == 1
    assert result.stdout == ""
    assert "queue reorder id mismatch:" in result.stderr
    assert "Traceback" not in result.stderr


def test_cli_run_provenance_corrupt_snapshot_json_error_stderr_only(tmp_path: Path) -> None:
    workspace, _ = load_workspace_fixture(tmp_path, "control_mailbox")
    run_dir = workspace / "agents" / "runs" / "broken-run"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "resolved_snapshot.json").write_text("{not valid json\n", encoding="utf-8")

    result = run_cli_subprocess(
        workspace,
        "--config",
        "millrace.toml",
        "run-provenance",
        "broken-run",
        "--json",
    )

    assert result.returncode == 1
    assert result.stdout == ""
    assert "Traceback" not in result.stderr
    payload = json.loads(result.stderr)
    assert payload["error"].startswith("run provenance is invalid:")


def test_cli_run_provenance_inconsistent_snapshot_contract_error_stderr_only(tmp_path: Path) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "golden_path")
    append_subprocess_stage_config(config_path)
    script = write_needs_research_stage_driver(tmp_path)
    engine = MillraceEngine(
        config_path,
        stage_commands={
            StageType.BUILDER: [sys.executable, str(script), "builder"],
            StageType.QA: [sys.executable, str(script), "qa-blocked"],
            StageType.TROUBLESHOOT: [sys.executable, str(script), "troubleshoot-blocked"],
            StageType.CONSULT: [sys.executable, str(script), "consult-needs-research"],
        },
    )

    engine.start(once=True)

    run_dirs = sorted((workspace / "agents" / "runs").iterdir())
    assert run_dirs
    run_dir = run_dirs[-1]
    snapshot_path = run_dir / "resolved_snapshot.json"
    snapshot_payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    snapshot_payload["selection_ref"] = {
        "kind": "loop_config",
        "id": "execution.standard",
        "version": "1.0.0",
    }
    snapshot_path.write_text(json.dumps(snapshot_payload), encoding="utf-8")

    result = run_cli_subprocess(
        workspace,
        "--config",
        "millrace.toml",
        "run-provenance",
        run_dir.name,
        "--json",
    )

    assert result.returncode == 1
    assert result.stdout == ""
    assert "Traceback" not in result.stderr
    payload = json.loads(result.stderr)
    assert payload["error"].startswith("run provenance is invalid:")


def test_cli_subgroup_help_does_not_require_config_file(tmp_path: Path) -> None:
    missing_config = tmp_path / "missing.toml"

    for args in (
        ["--config", str(missing_config), "init", "--help"],
        ["--config", str(missing_config), "health", "--help"],
        ["--config", str(missing_config), "config", "--help"],
        ["--config", str(missing_config), "queue", "--help"],
        ["--config", str(missing_config), "queue", "reorder", "--help"],
        ["--config", str(missing_config), "research", "--help"],
        ["--config", str(missing_config), "research", "history", "--help"],
        ["--config", str(missing_config), "logs", "--help"],
    ):
        result = RUNNER.invoke(app, args)
        assert result.exit_code == 0
        assert "Usage:" in result.stdout


def test_cli_start_once_help_and_docs_describe_research_split_phase_contract(tmp_path: Path) -> None:
    missing_config = tmp_path / "missing.toml"
    result = RUNNER.invoke(app, ["--config", str(missing_config), "start", "--help"])

    assert result.exit_code == 0
    assert "startup research sync creates" in result.stdout
    assert "new execution backlog from an empty execution queue" in result.stdout
    assert "run --once again to execute" in result.stdout

    readme = (Path(__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8")
    operator_guide = (Path(__file__).resolve().parents[1] / "OPERATOR_GUIDE.md").read_text(encoding="utf-8")

    assert "that invocation stops after the research pass" in readme
    assert "run `start --once` a second time" in readme
    assert "leaves the new task in backlog for the next `start --once`" in operator_guide


def test_cli_init_scaffolds_new_workspace(tmp_path: Path) -> None:
    destination = tmp_path / "fresh-workspace"

    result = RUNNER.invoke(app, ["init", str(destination), "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["mode"] == "direct"
    assert payload["message"] == "workspace initialized"
    assert payload["payload"]["workspace_root"] == destination.resolve().as_posix()
    assert payload["payload"]["bundle_version"] == "baseline-bundle-v1"
    assert payload["payload"]["created_file_count"] > 0
    assert payload["payload"]["overwritten_file_count"] == 0
    assert (destination / "millrace.toml").exists()
    assert (destination / "agents/status.md").read_text(encoding="utf-8") == "### IDLE\n"
    assert (destination / "agents/.runtime/commands/incoming").is_dir()
    assert (destination / "agents/registry/stages").is_dir()
    assert (destination / "agents/registry/loops/execution").is_dir()
    assert (destination / "agents/registry/loops/research").is_dir()
    assert (destination / "agents/registry/modes").is_dir()
    assert (destination / "agents/registry/task_authoring").is_dir()
    assert (destination / "agents/registry/model_profiles").is_dir()
    assert not any((destination / "agents/registry").rglob("*.json"))
    assert not any((destination / "agents/registry").rglob("*.md"))


def test_cli_init_rejects_non_empty_destination_without_force(tmp_path: Path) -> None:
    destination = tmp_path / "existing-workspace"
    destination.mkdir()
    (destination / "notes.md").write_text("keep\n", encoding="utf-8")

    result = RUNNER.invoke(app, ["init", str(destination)])

    assert result.exit_code != 0
    assert "destination exists and is not empty" in result.output
    assert "--force" in result.output
    assert not (destination / "millrace.toml").exists()


def test_cli_init_force_overwrites_manifest_files_and_preserves_unmanaged_files(tmp_path: Path) -> None:
    destination = tmp_path / "existing-workspace"
    destination.mkdir()
    (destination / "custom-notes.md").write_text("keep me\n", encoding="utf-8")
    (destination / "agents").mkdir()
    (destination / "agents/status.md").write_text("### BROKEN\n", encoding="utf-8")

    result = RUNNER.invoke(app, ["init", str(destination), "--force", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["payload"]["overwritten_file_count"] >= 1
    assert (destination / "custom-notes.md").read_text(encoding="utf-8") == "keep me\n"
    assert (destination / "agents/status.md").read_text(encoding="utf-8") == "### IDLE\n"
    assert (destination / "millrace.toml").exists()


def test_cli_init_scaffolded_workspace_acts_as_real_workspace_root(tmp_path: Path) -> None:
    destination = tmp_path / "scaffolded-workspace"
    repo_root = Path(__file__).resolve().parents[1]

    init_result = RUNNER.invoke(app, ["init", str(destination)])

    assert init_result.exit_code == 0
    env = os.environ.copy()
    existing_pythonpath = env.get("PYTHONPATH")
    repo_pythonpath = str(repo_root)
    env["PYTHONPATH"] = (
        repo_pythonpath if not existing_pythonpath else f"{repo_pythonpath}{os.pathsep}{existing_pythonpath}"
    )

    status_result = subprocess.run(
        [sys.executable, "-m", "millrace_engine", "--config", "millrace.toml", "status", "--json"],
        cwd=destination,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    start_result = subprocess.run(
        [sys.executable, "-m", "millrace_engine", "--config", "millrace.toml", "start", "--once"],
        cwd=destination,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert status_result.returncode == 0, status_result.stderr
    assert json.loads(status_result.stdout)["config_path"] == (destination / "millrace.toml").as_posix()
    assert start_result.returncode == 0, start_result.stderr
    assert "Execution status: IDLE" in start_result.stdout

    stale_markers = (
        "workspace-local override resolution and precedence are not yet implemented",
        "missing workspace files are not yet scaffolded from the packaged bundle",
        "this run does not yet scaffold missing files or resolve workspace-local overrides over packaged defaults",
    )
    for relative_path in ("README.md", "ADVISOR.md", "OPERATOR_GUIDE.md", "docs/RUNTIME_DEEP_DIVE.md"):
        contents = (destination / relative_path).read_text(encoding="utf-8")
        assert "/Users/timinator/Desktop/Millrace-2.0" not in contents
        for marker in stale_markers:
            assert marker not in contents

    assert "Workspace-first assets" in (destination / "README.md").read_text(encoding="utf-8")
    assert "## Asset Resolution" in (destination / "OPERATOR_GUIDE.md").read_text(encoding="utf-8")
    assert "real default model ids" in (destination / "README.md").read_text(encoding="utf-8")
    assert "default model ids are real packaged defaults" in (
        destination / "OPERATOR_GUIDE.md"
    ).read_text(encoding="utf-8")
    assert "Use `init` to scaffold workspaces instead of copying baseline files by hand." in (
        destination / "ADVISOR.md"
    ).read_text(encoding="utf-8")
    assert "packaged assets are the fallback when the workspace copy is absent" in (
        destination / "docs" / "RUNTIME_DEEP_DIVE.md"
    ).read_text(encoding="utf-8")
    workspace_model_config = destination / "agents" / "options" / "model_config.md"
    packaged_model_config = repo_root / "millrace_engine" / "assets" / "agents" / "options" / "model_config.md"
    if packaged_model_config.exists():
        assert workspace_model_config.exists()
        assert "real packaged defaults for Codex/OpenAI execution" in workspace_model_config.read_text(
            encoding="utf-8"
        )
    else:
        assert not workspace_model_config.exists()


def test_cli_health_reports_clean_initialized_workspace(tmp_path: Path) -> None:
    destination = tmp_path / "health-workspace"
    workspace_result = EngineControl.init_workspace(destination)

    assert workspace_result.applied is True
    result = RUNNER.invoke(
        app,
        ["--config", str(destination / "millrace.toml"), "health", "--json"],
        env=fake_runner_env(tmp_path, executables=("codex",)),
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "pass"
    assert payload["bootstrap_ready"] is True
    assert payload["execution_ready"] is True
    assert payload["summary"]["failed_checks"] == 0
    assert payload["config_source_kind"] == "native_toml"
    assert any(check["check_id"] == "execution.runners" for check in payload["checks"])


def test_cli_health_exits_nonzero_for_broken_config(tmp_path: Path) -> None:
    destination = tmp_path / "health-broken-config-workspace"
    workspace_result = EngineControl.init_workspace(destination)

    assert workspace_result.applied is True
    (destination / "millrace.toml").write_text("[engine\n", encoding="utf-8")

    result = RUNNER.invoke(
        app,
        ["--config", str(destination / "millrace.toml"), "health", "--json"],
    )

    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["status"] == "fail"
    assert payload["summary"]["failed_checks"] >= 1
    config_check = next(check for check in payload["checks"] if check["check_id"] == "config.load")
    assert config_check["status"] == "fail"
    assert any("config TOML is invalid" in detail for detail in config_check["details"])


def test_cli_doctor_reports_missing_runner_prerequisite_before_start(tmp_path: Path) -> None:
    destination = tmp_path / "doctor-workspace"
    workspace_result = EngineControl.init_workspace(destination)

    assert workspace_result.applied is True
    empty_bin = tmp_path / "empty-bin"
    empty_bin.mkdir()
    result = RUNNER.invoke(
        app,
        ["--config", str(destination / "millrace.toml"), "doctor"],
        env={"PATH": str(empty_bin)},
    )

    assert result.exit_code == 1
    assert "Bootstrap ready: yes" in result.stdout
    assert "Execution ready: no" in result.stdout
    assert "codex" in result.stdout
    assert "start --once" in result.stdout


def test_cli_status_detail_and_config_show_report_asset_inventory(tmp_path: Path) -> None:
    destination = tmp_path / "reporting-workspace"
    workspace_result = EngineControl.init_workspace(destination)
    assert workspace_result.applied is True

    (destination / "agents" / "_start.md").unlink()
    custom_role = destination / "agents" / "roles" / "custom-role.md"
    custom_role.parent.mkdir(parents=True, exist_ok=True)
    custom_role.write_text("custom role\n", encoding="utf-8")

    status_result = RUNNER.invoke(
        app,
        ["--config", str(destination / "millrace.toml"), "status", "--detail", "--json"],
    )
    config_result = RUNNER.invoke(
        app,
        ["--config", str(destination / "millrace.toml"), "config", "show", "--json"],
    )

    assert status_result.exit_code == 0
    assert config_result.exit_code == 0

    status_payload = json.loads(status_result.stdout)
    config_payload = json.loads(config_result.stdout)

    assert status_payload["runtime"]["asset_bundle_version"] == "baseline-bundle-v1"
    assert status_payload["selection"]["mode"]["ref"]["id"] == "mode.standard"
    assert status_payload["selection"]["execution_loop"]["ref"]["id"] == "execution.standard"
    assert status_payload["assets"]["bundle_version"] == "baseline-bundle-v1"
    assert status_payload["assets"]["stage_prompts"]["builder"]["source_kind"] == "package"
    assert status_payload["assets"]["stage_prompts"]["builder"]["resolved_ref"] == "package:agents/_start.md"
    assert any(
        entry["relative_path"] == "agents/roles/custom-role.md" and entry["source_kind"] == "workspace"
        for entry in status_payload["assets"]["roles"]
    )

    assert config_payload["assets"]["bundle_version"] == "baseline-bundle-v1"
    assert config_payload["selection"]["mode"]["ref"]["id"] == "mode.standard"
    assert config_payload["selection"]["execution_loop"]["ref"]["id"] == "execution.standard"
    assert config_payload["assets"]["stage_prompts"]["builder"]["source_kind"] == "package"
    assert any(
        entry["relative_path"] == "agents/roles/custom-role.md" and entry["source_kind"] == "workspace"
        for entry in config_payload["assets"]["roles"]
    )


def test_cli_research_report_tolerates_initialized_workspace_without_snapshot_file(tmp_path: Path) -> None:
    destination = tmp_path / "reporting-workspace"
    workspace_result = EngineControl.init_workspace(destination)
    assert workspace_result.applied is True

    result = RUNNER.invoke(
        app,
        ["--config", str(destination / "millrace.toml"), "research", "--json"],
    )

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["source_kind"] == "live"
    assert payload["runtime"]["mode_reason"] == "control-live-view"
    assert payload["runtime"]["updated_at"] is not None


def test_cli_research_report_exposes_mode_queue_retry_and_lock_state(tmp_path: Path) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "control_mailbox")
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace('mode = "stub"', 'mode = "auto"', 1),
        encoding="utf-8",
    )
    incident_path = workspace / "agents" / "ideas" / "incidents" / "incoming" / "incident.md"
    incident_path.parent.mkdir(parents=True, exist_ok=True)
    incident_path.write_text("# incident\n", encoding="utf-8")

    loaded = load_engine_config(config_path)
    paths = build_runtime_paths(loaded.config)
    plane = ResearchPlane(loaded.config, paths)
    try:
        plane.dispatch_ready_work(run_id="research-auto-run", resolve_assets=False)

        json_result = RUNNER.invoke(app, ["--config", str(config_path), "research", "--json"])
        text_result = RUNNER.invoke(app, ["--config", str(config_path), "research"])
        status_result = RUNNER.invoke(app, ["--config", str(config_path), "status", "--detail", "--json"])
    finally:
        plane.shutdown()

    assert json_result.exit_code == 0
    payload = json.loads(json_result.stdout)
    assert payload["configured_mode"] == "auto"
    assert payload["configured_idle_mode"] == loaded.config.research.idle_mode
    assert payload["status"] == "INCIDENT_INTAKE_RUNNING"
    assert payload["runtime"]["current_mode"] == "INCIDENT"
    assert payload["runtime"]["mode_reason"] == "incident-queue-ready"
    assert payload["runtime"]["checkpoint"]["checkpoint_id"] == "research-auto-run"
    assert payload["runtime"]["lock_state"]["lock_key"] == "research-loop"
    incident_family = next(entry for entry in payload["queue_families"] if entry["family"] == "incident")
    assert incident_family["ready"] is True
    assert incident_family["item_count"] == 1
    assert incident_family["ownerships"][0]["owner_token"] == "research-auto-run"

    assert text_result.exit_code == 0
    assert "Research configured mode: auto" in text_result.stdout
    assert "Research status: INCIDENT_INTAKE_RUNNING" in text_result.stdout
    assert "Research runtime mode: INCIDENT" in text_result.stdout
    assert "Research lock: owner=" in text_result.stdout
    assert "Research queues:" in text_result.stdout
    assert "- incident: ready=yes items=1" in text_result.stdout

    assert status_result.exit_code == 0
    status_payload = json.loads(status_result.stdout)
    assert status_payload["research"]["runtime"]["checkpoint"]["checkpoint_id"] == "research-auto-run"


def test_cli_research_report_exposes_gate_and_completion_decisions(tmp_path: Path) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "control_mailbox")
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace('mode = "stub"', 'mode = "audit"', 1),
        encoding="utf-8",
    )
    required_command = "pytest -q tests/test_cli.py"
    write_audit_queue_file(workspace, audit_id="AUD-CLI-001", command=required_command)
    write_completion_manifest(workspace, command=required_command)
    write_typed_objective_contract(workspace)
    write_empty_gaps_file(workspace)

    loaded = load_engine_config(config_path)
    paths = build_runtime_paths(loaded.config)
    plane = ResearchPlane(loaded.config, paths)
    try:
        plane.sync_runtime(trigger="cli-research-report", run_id="audit-cli-run", resolve_assets=False)

        json_result = RUNNER.invoke(app, ["--config", str(config_path), "research", "--json"])
        text_result = RUNNER.invoke(app, ["--config", str(config_path), "research"])
        status_result = RUNNER.invoke(app, ["--config", str(config_path), "status", "--detail", "--json"])
    finally:
        plane.shutdown()

    assert json_result.exit_code == 0
    payload = json.loads(json_result.stdout)
    assert payload["status"] == "AUDIT_PASS"
    assert payload["latest_gate_decision"]["decision"] == "PASS"
    assert payload["latest_gate_decision"]["counts"]["completion_required"] == 1
    assert payload["latest_gate_decision"]["objective_contract_path"] == "agents/objective/contract.yaml"
    assert payload["latest_completion_decision"]["decision"] == "PASS"
    assert payload["latest_completion_decision"]["completion_decision_path"] == "agents/reports/completion_decision.json"
    assert payload["latest_completion_decision"]["objective_contract_path"] == "agents/objective/contract.yaml"
    assert payload["completion_state"]["marker_present"] is False
    assert payload["completion_state"]["completion_allowed"] is True
    assert payload["completion_state"]["marker_honored"] is False
    assert payload["completion_state"]["reason"] == "marker_missing"

    assert text_result.exit_code == 0
    assert "Research gate decision: PASS" in text_result.stdout
    assert "Research completion decision: PASS" in text_result.stdout
    assert "Research completion state: marker_present=no completion_allowed=yes marker_honored=no reason=marker_missing" in text_result.stdout

    assert status_result.exit_code == 0
    status_payload = json.loads(status_result.stdout)
    assert status_payload["research"]["latest_gate_decision"]["decision"] == "PASS"
    assert status_payload["research"]["latest_completion_decision"]["decision"] == "PASS"
    assert status_payload["research"]["completion_state"]["completion_allowed"] is True
    assert status_payload["research"]["completion_state"]["marker_honored"] is False
    assert status_payload["research"]["queue_families"][1]["family"] == "incident"


def test_cli_research_report_exposes_audit_failure_story_and_remediation(tmp_path: Path) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "control_mailbox")
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace('mode = "stub"', 'mode = "audit"', 1),
        encoding="utf-8",
    )
    required_command = "pytest -q tests/test_cli.py --fast"
    write_audit_queue_file(
        workspace,
        audit_id="AUD-CLI-FAIL-001",
        command=required_command,
        scope="cli-audit-failure",
        summaries=["Open issues detected: 1"],
    )
    write_completion_manifest(workspace, command=required_command)
    write_typed_objective_contract(workspace)
    write_empty_gaps_file(workspace)
    (workspace / "agents" / "audit").mkdir(parents=True, exist_ok=True)
    (workspace / "agents" / "audit" / "strict_contract.json").write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "contract_id": "cli-strict-audit",
                "enabled": True,
                "description": "Fail closed when CLI audit output is missing the required clean summary.",
                "required_command_substrings": ["pytest -q tests/test_cli.py"],
                "forbidden_command_markers": ["--fast"],
                "required_summaries": ["Open issues detected: 0"],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    loaded = load_engine_config(config_path)
    paths = build_runtime_paths(loaded.config)
    plane = ResearchPlane(loaded.config, paths)
    try:
        plane.sync_runtime(trigger="cli-research-report-fail", run_id="audit-cli-fail-run", resolve_assets=False)

        json_result = RUNNER.invoke(app, ["--config", str(config_path), "research", "--json"])
        text_result = RUNNER.invoke(app, ["--config", str(config_path), "research"])
        status_result = RUNNER.invoke(app, ["--config", str(config_path), "status", "--detail", "--json"])
    finally:
        plane.shutdown()

    assert json_result.exit_code == 0
    payload = json.loads(json_result.stdout)
    assert payload["status"] == "AUDIT_FAIL"
    assert payload["audit_summary"]["counts"] == {"total": 1, "pass": 0, "fail": 1}
    assert payload["audit_summary"]["last_outcome"]["audit_id"] == "AUD-CLI-FAIL-001"
    assert payload["audit_summary"]["last_outcome"]["decision"] == "FAIL"
    assert payload["latest_audit_remediation"]["selected_action"] == "enqueue_backlog_task"
    assert payload["latest_audit_remediation"]["remediation_spec_id"] == "SPEC-AUD-CLI-FAIL-001-REMEDIATION"
    assert payload["latest_audit_remediation"]["remediation_task_title"] == "Remediate failed audit AUD-CLI-FAIL-001"
    assert payload["latest_completion_decision"]["decision"] == "FAIL"

    assert text_result.exit_code == 0
    assert "Research audit outcome: AUDIT_FAIL audit=AUD-CLI-FAIL-001" in text_result.stdout
    assert "Research audit details: Forbidden command marker `--fast` found in observed commands." in text_result.stdout
    assert "Research audit remediation: enqueue_backlog_task spec=SPEC-AUD-CLI-FAIL-001-REMEDIATION" in text_result.stdout
    assert "audited=agents/ideas/audit/incoming/AUD-CLI-FAIL-001.md" in text_result.stdout
    assert "terminal=agents/ideas/audit/failed/AUD-CLI-FAIL-001.md" in text_result.stdout

    assert status_result.exit_code == 0
    status_payload = json.loads(status_result.stdout)
    assert status_payload["research"]["audit_summary"]["last_outcome"]["audit_id"] == "AUD-CLI-FAIL-001"
    assert status_payload["research"]["latest_audit_remediation"]["remediation_spec_id"] == (
        "SPEC-AUD-CLI-FAIL-001-REMEDIATION"
    )
    assert status_payload["research"]["latest_audit_remediation"]["reasons"][0].startswith(
        "Forbidden command marker `--fast`"
    )


def test_cli_research_report_exposes_governance_canary_drift_and_queue_governor(tmp_path: Path) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "control_mailbox")
    goal_file = workspace / "agents" / "ideas" / "raw" / "goal.md"
    family_policy_file = workspace / "agents" / "objective" / "family_policy.json"
    queue_governor_path = workspace / "agents" / "reports" / "queue_governor.json"
    drift_policy_path = workspace / "agents" / "policies" / "drift_control_policy.json"
    baseline_policy_path = workspace / "agents" / "policies" / "drift_control_policy.baseline.json"
    goal_file.parent.mkdir(parents=True, exist_ok=True)
    family_policy_file.parent.mkdir(parents=True, exist_ok=True)
    drift_policy_path.parent.mkdir(parents=True, exist_ok=True)
    goal_file.write_text("# Goal\n", encoding="utf-8")
    family_policy_file.write_text(
        json.dumps({"family_cap_mode": "adaptive", "initial_family_max_specs": 3}) + "\n",
        encoding="utf-8",
    )
    state = GoalSpecFamilyState.model_validate(
        {
            "goal_id": "IDEA-CLI-301",
            "source_idea_path": "agents/ideas/raw/goal.md",
            "family_phase": "initial_family",
            "family_complete": False,
            "active_spec_id": "SPEC-CLI-301",
            "spec_order": ["SPEC-CLI-301"],
            "specs": {
                "SPEC-CLI-301": {
                    "status": "emitted",
                    "title": "CLI governance visibility",
                    "decomposition_profile": "simple",
                }
            },
            "family_governor": {
                "policy_path": "agents/objective/family_policy.json",
                "initial_family_max_specs": 3,
                "applied_family_max_specs": 3,
            },
        }
    )
    frozen_state = state.model_copy(
        update={
            "initial_family_plan": build_initial_family_plan_snapshot(
                state,
                repo_root=workspace,
                goal_file=goal_file,
                policy_path=family_policy_file,
                frozen_at="2026-03-21T12:00:00Z",
            )
        }
    )
    family_policy_file.write_text(
        json.dumps({"family_cap_mode": "adaptive", "initial_family_max_specs": 4}) + "\n",
        encoding="utf-8",
    )
    (workspace / "agents" / ".research_runtime").mkdir(parents=True, exist_ok=True)
    (workspace / "agents" / ".research_runtime" / "spec_family_state.json").write_text(
        frozen_state.model_dump_json(indent=2, exclude_none=True) + "\n",
        encoding="utf-8",
    )
    queue_governor_path.parent.mkdir(parents=True, exist_ok=True)
    queue_governor_path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "updated_at": "2026-03-21T12:05:00Z",
                "goal_id": "IDEA-CLI-301",
                "report_path": "agents/reports/queue_governor.json",
                "status": "pinned",
                "reason": "frozen-initial-family-policy-preserved",
                "initial_family_policy_pin": {
                    "active": True,
                    "action": "pin",
                    "reason": "frozen-initial-family-policy-preserved",
                    "pinned_fields": ["family_cap_mode", "initial_family_max_specs"],
                    "family_policy_path": "agents/objective/family_policy.json",
                    "spec_family_state_path": "agents/.research_runtime/spec_family_state.json",
                },
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    baseline_policy_path.write_text(
        json.dumps({"watched_family_policy_fields": ["family_cap_mode", "initial_family_max_specs"]}) + "\n",
        encoding="utf-8",
    )
    drift_policy_path.write_text(
        json.dumps(
            {
                "watched_family_policy_fields": ["family_cap_mode", "initial_family_max_specs"],
                "hard_latch_on_policy_drift": True,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    json_result = RUNNER.invoke(app, ["--config", str(config_path), "research", "--json"])
    text_result = RUNNER.invoke(app, ["--config", str(config_path), "research"])
    status_result = RUNNER.invoke(app, ["--config", str(config_path), "status", "--detail", "--json"])

    assert json_result.exit_code == 0
    payload = json.loads(json_result.stdout)
    assert payload["governance"]["queue_governor"]["status"] == "pinned"
    assert payload["governance"]["queue_governor"]["initial_family_policy_pin"]["active"] is True
    assert payload["governance"]["governance_canary"]["status"] == "drifted"
    assert payload["governance"]["governance_canary"]["changed_fields"] == ["hard_latch_on_policy_drift"]
    assert payload["governance"]["drift"]["status"] == "hard_latch"
    assert payload["governance"]["drift"]["drift_fields"] == ["initial_family_max_specs"]
    assert payload["governance"]["progress_watchdog"]["status"] == "not_active"
    assert payload["governance"]["progress_watchdog"]["reason"] == "no-research-recovery-latch"

    assert text_result.exit_code == 0
    assert "Research queue governor: pinned reason=frozen-initial-family-policy-preserved" in text_result.stdout
    assert "Research initial-family policy pin: active=yes action=pin reason=frozen-initial-family-policy-preserved" in text_result.stdout
    assert "Research governance canary: drifted reason=governance-canary-policy-drift changed_fields=hard_latch_on_policy_drift" in text_result.stdout
    assert "Research drift status: hard_latch reason=frozen-family-policy-drift-detected fields=initial_family_max_specs warning=yes hard_latch=yes" in text_result.stdout
    assert (
        "Research progress watchdog: not_active reason=no-research-recovery-latch spec=none visible_tasks=0 escalation=none"
        in text_result.stdout
    )

    assert status_result.exit_code == 0
    status_payload = json.loads(status_result.stdout)
    assert status_payload["research"]["governance"]["queue_governor"]["status"] == "pinned"
    assert status_payload["research"]["governance"]["drift"]["status"] == "hard_latch"
    assert status_payload["research"]["governance"]["progress_watchdog"]["status"] == "not_active"


def test_control_research_report_ignores_custom_paths_from_malformed_typed_objective_contract(
    tmp_path: Path,
) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "control_mailbox")
    default_gate_path = workspace / "agents" / "reports" / "audit_gate_decision.json"
    default_completion_path = workspace / "agents" / "reports" / "completion_decision.json"
    if default_gate_path.exists():
        default_gate_path.unlink()
    if default_completion_path.exists():
        default_completion_path.unlink()

    write_malformed_typed_objective_contract(
        workspace,
        fallback_decision_file="agents/custom/broken_gate.json",
        authoritative_decision_file="agents/custom/broken_completion.json",
    )
    write_decision_reports(
        workspace,
        gate_rel_path="agents/custom/broken_gate.json",
        completion_rel_path="agents/custom/broken_completion.json",
    )

    report = EngineControl(config_path).research_report()

    assert report.latest_gate_decision is None
    assert report.latest_completion_decision is None


def test_cli_research_history_filters_to_research_related_events(tmp_path: Path) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "control_mailbox")
    log_path = workspace / "agents" / "engine_events.log"
    events = [
        EventRecord.model_validate(
            {
                "type": EventType.ENGINE_STARTED,
                "timestamp": "2026-03-19T12:00:00Z",
                "source": EventSource.ENGINE,
                "payload": {},
            }
        ),
        EventRecord.model_validate(
            {
                "type": EventType.NEEDS_RESEARCH,
                "timestamp": "2026-03-19T12:00:01Z",
                "source": EventSource.EXECUTION,
                "payload": {"task_id": "task-1"},
            }
        ),
        EventRecord.model_validate(
            {
                "type": EventType.RESEARCH_SCAN_COMPLETED,
                "timestamp": "2026-03-19T12:00:02Z",
                "source": EventSource.RESEARCH,
                "payload": {"ready_families": ["incident"]},
            }
        ),
        EventRecord.model_validate(
            {
                "type": EventType.RESEARCH_DISPATCH_COMPILED,
                "timestamp": "2026-03-19T12:00:03Z",
                "source": EventSource.RESEARCH,
                "payload": {"run_id": "research-auto-run"},
            }
        ),
        EventRecord.model_validate(
            {
                "type": EventType.BACKLOG_REPOPULATED,
                "timestamp": "2026-03-19T12:00:04Z",
                "source": EventSource.ENGINE,
                "payload": {"batch_id": "research-batch-1", "thawed_cards": 2},
            }
        ),
        EventRecord.model_validate(
            {
                "type": EventType.ENGINE_STOPPED,
                "timestamp": "2026-03-19T12:00:05Z",
                "source": EventSource.ENGINE,
                "payload": {},
            }
        ),
    ]
    log_path.write_text("".join(event.model_dump_json() + "\n" for event in events), encoding="utf-8")

    json_result = RUNNER.invoke(app, ["--config", str(config_path), "research", "history", "--limit", "3", "--json"])
    text_result = RUNNER.invoke(app, ["--config", str(config_path), "research", "history", "--limit", "5"])

    assert json_result.exit_code == 0
    payload = json.loads(json_result.stdout)
    assert [event["type"] for event in payload] == [
        EventType.RESEARCH_SCAN_COMPLETED.value,
        EventType.RESEARCH_DISPATCH_COMPILED.value,
        EventType.BACKLOG_REPOPULATED.value,
    ]

    assert text_result.exit_code == 0
    assert EventType.RESEARCH_SCAN_COMPLETED.value in text_result.stdout
    assert EventType.RESEARCH_DISPATCH_COMPILED.value in text_result.stdout
    assert EventType.BACKLOG_REPOPULATED.value in text_result.stdout
    assert EventType.ENGINE_STARTED.value not in text_result.stdout


def test_cli_mailbox_commands_update_runtime_state_and_event_log(tmp_path: Path) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "control_mailbox")
    controller = EngineControl(config_path)
    state_path = workspace / "agents/.runtime/state.json"
    thread = Thread(target=lambda: controller.start(daemon=True), daemon=True)
    thread.start()

    wait_for(lambda: state_path.exists() and read_state(state_path)["process_running"] is True)

    status_result = RUNNER.invoke(app, ["--config", str(config_path), "status", "--json"])
    assert status_result.exit_code == 0
    assert json.loads(status_result.stdout)["runtime"]["process_running"] is True

    pause_result = RUNNER.invoke(app, ["--config", str(config_path), "pause"])
    assert pause_result.exit_code == 0
    wait_for(lambda: read_state(state_path)["paused"] is True)

    resume_result = RUNNER.invoke(app, ["--config", str(config_path), "resume"])
    assert resume_result.exit_code == 0
    wait_for(lambda: read_state(state_path)["paused"] is False)

    stop_result = RUNNER.invoke(app, ["--config", str(config_path), "stop"])
    assert stop_result.exit_code == 0
    wait_for(lambda: read_state(state_path)["process_running"] is False)
    thread.join(timeout=5.0)
    assert not thread.is_alive()

    incoming = sorted((workspace / "agents/.runtime/commands/incoming").glob("*.json"))
    processed = sorted((workspace / "agents/.runtime/commands/processed").glob("*.json"))
    failed = sorted((workspace / "agents/.runtime/commands/failed").glob("*.json"))
    assert incoming == []
    assert len(processed) == 3
    assert failed == []

    event_types = read_event_types(workspace)
    assert EventType.ENGINE_STARTED.value in event_types
    assert EventType.CONTROL_COMMAND_RECEIVED.value in event_types
    assert EventType.CONTROL_COMMAND_APPLIED.value in event_types
    assert EventType.ENGINE_PAUSED.value in event_types
    assert EventType.ENGINE_RESUMED.value in event_types
    assert EventType.ENGINE_STOPPED.value in event_types


def test_cli_config_hotswap_applies_runtime_safe_change_and_rejects_startup_only(tmp_path: Path) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "config_hotswap")
    controller = EngineControl(config_path)
    state_path = workspace / "agents/.runtime/state.json"
    processed_dir = workspace / "agents/.runtime/commands/processed"
    failed_dir = workspace / "agents/.runtime/commands/failed"
    thread = Thread(target=lambda: controller.start(daemon=True), daemon=True)
    thread.start()

    wait_for(lambda: state_path.exists() and read_state(state_path)["process_running"] is True)
    initial_hash = str(read_state(state_path)["config_hash"])

    set_result = RUNNER.invoke(
        app,
        [
            "--config",
            str(config_path),
            "config",
            "set",
            "execution.quickfix_max_attempts",
            "5",
            "--json",
        ],
    )
    assert set_result.exit_code == 0
    set_payload = json.loads(set_result.stdout)
    assert set_payload["mode"] == "mailbox"
    wait_for(lambda: EngineControl(config_path).config_show().config.execution.quickfix_max_attempts == 5)
    wait_for(lambda: str(read_state(state_path)["config_hash"]) != initial_hash)
    wait_for(lambda: len(list(processed_dir.glob("*.json"))) == 1)

    startup_only_result = RUNNER.invoke(
        app,
        [
            "--config",
            str(config_path),
            "config",
            "set",
            "paths.agents_dir",
            "other-agents",
            "--json",
        ],
    )
    assert startup_only_result.exit_code == 0
    wait_for(lambda: len(list(failed_dir.glob("*.json"))) == 1)
    failed_payload = json.loads(next(failed_dir.glob("*.json")).read_text(encoding="utf-8"))
    assert failed_payload["result"]["message"] == "cannot change startup-only field at runtime: paths.agents_dir"

    stop_result = RUNNER.invoke(app, ["--config", str(config_path), "stop"])
    assert stop_result.exit_code == 0
    wait_for(lambda: read_state(state_path)["process_running"] is False)
    thread.join(timeout=5.0)
    assert not thread.is_alive()


def test_cli_daemon_reloads_after_watched_config_file_change(tmp_path: Path) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "config_hotswap")
    controller = EngineControl(config_path)
    state_path = workspace / "agents/.runtime/state.json"
    thread = Thread(target=lambda: controller.start(daemon=True), daemon=True)
    thread.start()

    wait_for(lambda: state_path.exists() and read_state(state_path)["process_running"] is True)
    initial_hash = str(read_state(state_path)["config_hash"])

    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace("poll_interval_seconds = 1", "poll_interval_seconds = 2", 1),
        encoding="utf-8",
    )

    wait_for(lambda: str(read_state(state_path)["config_hash"]) != initial_hash)

    applied_events = [
        event
        for event in read_events(workspace)
        if event["type"] == EventType.CONFIG_APPLIED.value
    ]
    assert any(event["payload"]["boundary"] == "live_immediate" for event in applied_events)
    assert EventType.CONFIG_CHANGED.value in read_event_types(workspace)

    stop_result = RUNNER.invoke(app, ["--config", str(config_path), "stop"])
    assert stop_result.exit_code == 0
    wait_for(lambda: read_state(state_path)["process_running"] is False)
    thread.join(timeout=5.0)
    assert not thread.is_alive()


def test_cli_watcher_autonomy_complete_marker_stays_fail_closed_without_audit_pass(tmp_path: Path) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "watcher_stop_completion")
    controller = EngineControl(config_path)
    state_path = workspace / "agents/.runtime/state.json"
    thread = Thread(target=lambda: controller.start(daemon=True), daemon=True)
    thread.start()

    wait_for(lambda: state_path.exists() and read_state(state_path)["process_running"] is True)
    (workspace / "agents/AUTONOMY_COMPLETE").write_text("done\n", encoding="utf-8")

    time.sleep(0.3)
    assert read_state(state_path)["process_running"] is True

    status_result = RUNNER.invoke(app, ["--config", str(config_path), "status", "--detail", "--json"])
    assert status_result.exit_code == 0
    status_payload = json.loads(status_result.stdout)
    assert status_payload["research"]["completion_state"]["marker_present"] is True
    assert status_payload["research"]["completion_state"]["completion_allowed"] is False
    assert status_payload["research"]["completion_state"]["marker_honored"] is False
    assert status_payload["research"]["completion_state"]["reason"] == "audit_pass_missing"

    stop_result = RUNNER.invoke(app, ["--config", str(config_path), "stop"])
    assert stop_result.exit_code == 0
    wait_for(lambda: read_state(state_path)["process_running"] is False)
    thread.join(timeout=5.0)
    assert not thread.is_alive()

    event_types = read_event_types(workspace)
    assert EventType.ENGINE_STARTED.value in event_types
    assert EventType.ENGINE_STOPPED.value in event_types


def test_cli_watcher_autonomy_complete_marker_stops_daemon_after_audit_pass(tmp_path: Path) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "watcher_stop_completion")
    write_decision_reports(
        workspace,
        gate_rel_path="agents/reports/audit_gate_decision.json",
        completion_rel_path="agents/reports/completion_decision.json",
    )
    controller = EngineControl(config_path)
    state_path = workspace / "agents/.runtime/state.json"
    thread = Thread(target=lambda: controller.start(daemon=True), daemon=True)
    thread.start()

    wait_for(lambda: state_path.exists() and read_state(state_path)["process_running"] is True)
    (workspace / "agents/AUTONOMY_COMPLETE").write_text("done\n", encoding="utf-8")

    wait_for(lambda: read_state(state_path)["process_running"] is False)
    thread.join(timeout=5.0)
    assert not thread.is_alive()

    event_types = read_event_types(workspace)
    assert EventType.ENGINE_STARTED.value in event_types
    assert EventType.ENGINE_STOPPED.value in event_types


def test_cli_status_detail_honors_autonomy_complete_marker_after_audit_pass_without_daemon(tmp_path: Path) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "control_mailbox")
    write_decision_reports(
        workspace,
        gate_rel_path="agents/reports/audit_gate_decision.json",
        completion_rel_path="agents/reports/completion_decision.json",
    )
    (workspace / "agents/AUTONOMY_COMPLETE").write_text("done\n", encoding="utf-8")

    research_result = RUNNER.invoke(app, ["--config", str(config_path), "research", "--json"])
    status_result = RUNNER.invoke(app, ["--config", str(config_path), "status", "--detail", "--json"])

    assert research_result.exit_code == 0
    research_payload = json.loads(research_result.stdout)
    assert research_payload["completion_state"]["marker_present"] is True
    assert research_payload["completion_state"]["completion_allowed"] is True
    assert research_payload["completion_state"]["marker_honored"] is True
    assert research_payload["completion_state"]["reason"] == "allowed"

    assert status_result.exit_code == 0
    status_payload = json.loads(status_result.stdout)
    assert status_payload["research"]["completion_state"]["marker_present"] is True
    assert status_payload["research"]["completion_state"]["completion_allowed"] is True
    assert status_payload["research"]["completion_state"]["marker_honored"] is True
    assert status_payload["research"]["completion_state"]["reason"] == "allowed"


def test_cli_add_task_thaws_research_frozen_batch_once(tmp_path: Path) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "needs_research")
    config_path.write_text(
        config_path.read_text(encoding="utf-8")
        .replace('integration_mode = "large_only"', 'integration_mode = "never"', 1)
        .replace("run_update_on_empty = true", "run_update_on_empty = false", 1),
        encoding="utf-8",
    )
    append_subprocess_stage_config(config_path)
    script = write_needs_research_stage_driver(tmp_path)

    latch_path = workspace / "agents/.runtime/research_recovery_latch.json"
    engine = MillraceEngine(
        config_path,
        stage_commands={
            StageType.BUILDER: [sys.executable, str(script), "builder"],
            StageType.QA: [sys.executable, str(script), "qa-blocked"],
            StageType.TROUBLESHOOT: [sys.executable, str(script), "troubleshoot-blocked"],
            StageType.CONSULT: [sys.executable, str(script), "consult-needs-research"],
        },
    )
    engine.start(once=True)

    assert latch_path.exists()
    assert parse_task_cards((workspace / "agents/tasksbacklog.md").read_text(encoding="utf-8")) == []
    latch = load_research_recovery_latch(latch_path)
    assert latch is not None
    latch_path.write_text(
        latch.model_copy(
            update={
                "remediation_decision": ResearchRecoveryDecision.model_validate(
                    {
                        "decision_type": "regenerated_backlog_work",
                        "decided_at": "2026-03-21T12:00:00Z",
                        "remediation_spec_id": "SPEC-CLI-THAW",
                        "remediation_record_path": "agents/.research_runtime/incidents/remediation/cli-thaw.json",
                        "taskaudit_record_path": "agents/.research_runtime/goalspec/taskaudit/cli-thaw.json",
                        "task_provenance_path": "agents/task_provenance.json",
                        "lineage_path": "agents/.research_runtime/incidents/lineage/inc-cli-001.json",
                        "pending_card_count": 1,
                        "backlog_card_count": 1,
                    }
                )
            }
        ).model_dump_json(indent=2, exclude_none=True)
        + "\n",
        encoding="utf-8",
    )

    add_result = RUNNER.invoke(
        app,
        [
            "--config",
            str(config_path),
            "add-task",
            "Research regenerated task",
            "--spec-id",
            "SPEC-CLI-THAW",
            "--json",
        ],
    )
    assert add_result.exit_code == 0
    payload = json.loads(add_result.stdout)
    assert payload["mode"] == "direct"

    assert engine._consume_research_recovery_latch(trigger="add_task") == 2
    assert not latch_path.exists()
    assert [
        card.title for card in parse_task_cards((workspace / "agents/tasksbacklog.md").read_text(encoding="utf-8"))
    ] == ["Research regenerated task", "Ship the happy path", "Research follow-up task"]
    watchdog_state = json.loads(engine.paths.progress_watchdog_state_file.read_text(encoding="utf-8"))
    watchdog_report = json.loads(engine.paths.progress_watchdog_report_file.read_text(encoding="utf-8"))
    assert watchdog_state["status"] == "not_active"
    assert watchdog_state["reason"] == "no-research-recovery-latch"
    assert watchdog_report["status"] == "not_active"
    assert watchdog_report["reason"] == "no-research-recovery-latch"

    repopulated_events = [
        event
        for event in read_events(workspace)
        if event["type"] == EventType.BACKLOG_REPOPULATED.value
    ]
    assert len(repopulated_events) == 1
    assert repopulated_events[0]["payload"]["trigger"] == "add_task"
    assert repopulated_events[0]["payload"]["thawed_cards"] == 2
    assert repopulated_events[0]["payload"]["decision_type"] == "regenerated_backlog_work"
    assert repopulated_events[0]["payload"]["remediation_spec_id"] == "SPEC-CLI-THAW"
    assert "research_recovery:freeze:start" not in (
        workspace / "agents/tasksbackburner.md"
    ).read_text(encoding="utf-8")


def test_cli_add_task_does_not_thaw_research_frozen_batch_without_remediation_decision(tmp_path: Path) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "needs_research")
    config_path.write_text(
        config_path.read_text(encoding="utf-8")
        .replace('integration_mode = "large_only"', 'integration_mode = "never"', 1)
        .replace("run_update_on_empty = true", "run_update_on_empty = false", 1),
        encoding="utf-8",
    )
    append_subprocess_stage_config(config_path)
    script = write_needs_research_stage_driver(tmp_path)

    latch_path = workspace / "agents/.runtime/research_recovery_latch.json"
    engine = MillraceEngine(
        config_path,
        stage_commands={
            StageType.BUILDER: [sys.executable, str(script), "builder"],
            StageType.QA: [sys.executable, str(script), "qa-blocked"],
            StageType.TROUBLESHOOT: [sys.executable, str(script), "troubleshoot-blocked"],
            StageType.CONSULT: [sys.executable, str(script), "consult-needs-research"],
        },
    )
    engine.start(once=True)

    assert latch_path.exists()
    assert parse_task_cards((workspace / "agents/tasksbacklog.md").read_text(encoding="utf-8")) == []

    add_result = RUNNER.invoke(
        app,
        ["--config", str(config_path), "add-task", "Unrelated backlog task", "--json"],
    )
    assert add_result.exit_code == 0

    wait_for(
        lambda: [
            card.title
            for card in parse_task_cards((workspace / "agents/tasksbacklog.md").read_text(encoding="utf-8"))
        ]
        == ["Unrelated backlog task"]
    )
    assert latch_path.exists()
    assert "research_recovery:freeze:start" in (
        workspace / "agents/tasksbackburner.md"
    ).read_text(encoding="utf-8")
    assert [
        event
        for event in read_events(workspace)
        if event["type"] == EventType.BACKLOG_REPOPULATED.value
    ] == []
    assert read_state(engine.paths.runtime_dir / "state.json")["process_running"] is False


def test_cli_add_task_does_not_thaw_research_frozen_batch_when_visible_work_has_the_wrong_spec(
    tmp_path: Path,
) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "needs_research")
    config_path.write_text(
        config_path.read_text(encoding="utf-8")
        .replace('integration_mode = "large_only"', 'integration_mode = "never"', 1)
        .replace("run_update_on_empty = true", "run_update_on_empty = false", 1),
        encoding="utf-8",
    )
    append_subprocess_stage_config(config_path)
    script = write_needs_research_stage_driver(tmp_path)

    latch_path = workspace / "agents/.runtime/research_recovery_latch.json"
    engine = MillraceEngine(
        config_path,
        stage_commands={
            StageType.BUILDER: [sys.executable, str(script), "builder"],
            StageType.QA: [sys.executable, str(script), "qa-blocked"],
            StageType.TROUBLESHOOT: [sys.executable, str(script), "troubleshoot-blocked"],
            StageType.CONSULT: [sys.executable, str(script), "consult-needs-research"],
        },
    )
    engine.start(once=True)

    assert latch_path.exists()
    assert parse_task_cards((workspace / "agents/tasksbacklog.md").read_text(encoding="utf-8")) == []
    latch = load_research_recovery_latch(latch_path)
    assert latch is not None
    latch_path.write_text(
        latch.model_copy(
            update={
                "remediation_decision": ResearchRecoveryDecision.model_validate(
                    {
                        "decision_type": "regenerated_backlog_work",
                        "decided_at": "2026-03-21T12:00:00Z",
                        "remediation_spec_id": "SPEC-CLI-THAW",
                        "remediation_record_path": "agents/.research_runtime/incidents/remediation/cli-thaw.json",
                        "taskaudit_record_path": "agents/.research_runtime/goalspec/taskaudit/cli-thaw.json",
                        "task_provenance_path": "agents/task_provenance.json",
                        "lineage_path": "agents/.research_runtime/incidents/lineage/inc-cli-001.json",
                        "pending_card_count": 1,
                        "backlog_card_count": 1,
                    }
                )
            }
        ).model_dump_json(indent=2, exclude_none=True)
        + "\n",
        encoding="utf-8",
    )

    add_result = RUNNER.invoke(
        app,
        ["--config", str(config_path), "add-task", "Unrelated recovery task", "--spec-id", "SPEC-OTHER", "--json"],
    )
    assert add_result.exit_code == 0
    assert engine._consume_research_recovery_latch(trigger="add_task") == 0

    assert [
        card.spec_id for card in parse_task_cards((workspace / "agents/tasksbacklog.md").read_text(encoding="utf-8"))
    ] == ["SPEC-OTHER"]
    assert latch_path.exists()
    report = EngineControl(config_path).research_report()
    assert report.governance is not None
    assert report.governance.progress_watchdog.status == "stalled"
    assert report.governance.progress_watchdog.remediation_spec_id == "SPEC-CLI-THAW"
    assert report.governance.progress_watchdog.recovery_regeneration is not None
    assert report.governance.progress_watchdog.recovery_regeneration.status == "manual_only"
    assert [
        event
        for event in read_events(workspace)
        if event["type"] == EventType.BACKLOG_REPOPULATED.value
    ] == []


def test_cli_daemon_auto_roundtrip_thaws_only_after_research_remediation_output(tmp_path: Path) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "needs_research")
    config_path.write_text(
        config_path.read_text(encoding="utf-8")
        .replace('mode = "stub"', 'mode = "auto"\nstage_retry_backoff_seconds = 0\nstage_retry_max = 2', 1)
        .replace('integration_mode = "large_only"', 'integration_mode = "never"', 1)
        .replace("run_update_on_empty = true", "run_update_on_empty = false", 1),
        encoding="utf-8",
    )
    append_subprocess_stage_config(config_path)
    script = write_needs_research_stage_driver(tmp_path)

    latch_path = workspace / "agents/.runtime/research_recovery_latch.json"
    engine = MillraceEngine(
        config_path,
        stage_commands={
            StageType.BUILDER: [sys.executable, str(script), "builder"],
            StageType.QA: [sys.executable, str(script), "qa-blocked"],
            StageType.TROUBLESHOOT: [sys.executable, str(script), "troubleshoot-blocked"],
            StageType.CONSULT: [sys.executable, str(script), "consult-needs-research"],
        },
    )
    engine.start(once=True)

    assert latch_path.exists()
    write_incident_file(
        workspace,
        incident_rel_path=Path("agents/ideas/incidents/incoming/INC-CLI-001.md"),
        incident_id="INC-CLI-001",
        title="Live consult incident probe",
        summary="Generate remediation work and thaw only after the research roundtrip completes.",
    )

    engine._sync_ready_research_dispatch(trigger="test-roundtrip")

    assert not latch_path.exists()
    assert {
        card.title
        for card in parse_task_cards((workspace / "agents/tasksbacklog.md").read_text(encoding="utf-8"))
    } >= {"Ship the happy path", "Research follow-up task"}

    repopulated_events = [
        event
        for event in read_events(workspace)
        if event["type"] == EventType.BACKLOG_REPOPULATED.value
    ]
    assert repopulated_events
    payload = repopulated_events[-1]["payload"]
    assert payload["trigger"] == "backlog_changed" or payload["trigger"].startswith("research_sync:")
    assert payload["decision_type"] == "regenerated_backlog_work"
    assert payload["remediation_spec_id"] == "SPEC-INC-CLI-001"
    assert payload["handoff_id"] is not None
    assert payload["parent_run_id"] is not None
    assert payload["remediation_record_path"].startswith("agents/.research_runtime/incidents/remediation/")
    assert payload["taskaudit_record_path"].startswith("agents/.research_runtime/goalspec/taskaudit/")
    assert payload["task_provenance_path"] == "agents/task_provenance.json"
    assert payload["lineage_path"] == "agents/.research_runtime/incidents/lineage/inc-cli-001.json"
    assert "research_recovery:freeze:start" not in (
        workspace / "agents/tasksbackburner.md"
    ).read_text(encoding="utf-8")


def test_cli_add_idea_mailbox_routes_once_through_research_stub(tmp_path: Path) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "control_mailbox")
    controller = EngineControl(config_path)
    state_path = workspace / "agents/.runtime/state.json"
    thread = Thread(target=lambda: controller.start(daemon=True), daemon=True)
    thread.start()

    wait_for(lambda: state_path.exists() and read_state(state_path)["process_running"] is True)
    idea_source = tmp_path / "idea.md"
    idea_source.write_text("# idea\n", encoding="utf-8")

    add_result = RUNNER.invoke(app, ["--config", str(config_path), "add-idea", str(idea_source), "--json"])
    assert add_result.exit_code == 0
    wait_for(lambda: len(list((workspace / "agents/ideas/raw").glob("*.md"))) == 1)
    wait_for(
        lambda: read_event_types(workspace).count(EventType.IDEA_SUBMITTED.value) == 1
        and read_event_types(workspace).count(EventType.RESEARCH_RECEIVED.value) == 1
        and read_event_types(workspace).count(EventType.RESEARCH_DEFERRED.value) == 1
    )

    stop_result = RUNNER.invoke(app, ["--config", str(config_path), "stop"])
    assert stop_result.exit_code == 0
    wait_for(lambda: read_state(state_path)["process_running"] is False)
    thread.join(timeout=5.0)
    assert not thread.is_alive()


def test_cli_start_once_research_sync_requires_second_invocation_for_new_execution_work(tmp_path: Path) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "control_mailbox")
    append_subprocess_stage_config(config_path)
    config_path.write_text(
        config_path.read_text(encoding="utf-8")
        .replace('mode = "stub"', 'mode = "auto"', 1)
        .replace('integration_mode = "large_only"', 'integration_mode = "never"', 1),
        encoding="utf-8",
    )
    (workspace / "agents" / "ideas" / "raw" / "goal.md").write_text("# goal\n", encoding="utf-8")
    script = write_outage_stage_driver(tmp_path)
    engine = MillraceEngine(
        config_path,
        stage_commands={
            StageType.BUILDER: [sys.executable, str(script), "builder"],
            StageType.QA: [sys.executable, str(script), "qa"],
            StageType.UPDATE: [sys.executable, str(script), "update"],
        },
    )

    engine.start(once=True)

    backlog_after_first = parse_task_cards((workspace / "agents/tasksbacklog.md").read_text(encoding="utf-8"))
    assert backlog_after_first
    assert parse_task_cards((workspace / "agents/tasks.md").read_text(encoding="utf-8")) == []
    assert parse_task_cards((workspace / "agents/tasksarchive.md").read_text(encoding="utf-8")) == []
    assert not any(
        event["type"] == EventType.STAGE_STARTED.value and event["source"] == "execution"
        for event in read_events(workspace)
    )

    engine.start(once=True)

    backlog_after_second = parse_task_cards((workspace / "agents/tasksbacklog.md").read_text(encoding="utf-8"))
    assert len(backlog_after_second) == len(backlog_after_first) - 1
    archived_cards = parse_task_cards((workspace / "agents/tasksarchive.md").read_text(encoding="utf-8"))
    assert len(archived_cards) == 1
    assert any(
        event["type"] == EventType.STAGE_STARTED.value
        and event["source"] == "execution"
        and event["payload"].get("stage") == StageType.BUILDER.value
        for event in read_events(workspace)
    )


def test_cli_queue_reorder_rewrites_backlog_directly(tmp_path: Path) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "control_mailbox")

    RUNNER.invoke(app, ["--config", str(config_path), "add-task", "First queued task"])
    RUNNER.invoke(app, ["--config", str(config_path), "add-task", "Second queued task"])
    backlog = parse_task_cards((workspace / "agents/tasksbacklog.md").read_text(encoding="utf-8"))
    assert [card.title for card in backlog] == ["First queued task", "Second queued task"]

    reorder_result = RUNNER.invoke(
        app,
        [
            "--config",
            str(config_path),
            "queue",
            "reorder",
            backlog[1].task_id,
            backlog[0].task_id,
            "--json",
        ],
    )
    assert reorder_result.exit_code == 0
    payload = json.loads(reorder_result.stdout)
    assert payload["mode"] == "direct"
    assert payload["message"] == "queue reordered"
    assert payload["payload"]["task_ids"] == [backlog[1].task_id, backlog[0].task_id]

    reordered = parse_task_cards((workspace / "agents/tasksbacklog.md").read_text(encoding="utf-8"))
    assert [card.title for card in reordered] == ["Second queued task", "First queued task"]


def test_cli_queue_reorder_uses_mailbox_when_daemon_is_running(tmp_path: Path) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "control_mailbox")
    set_engine_idle_mode(config_path, "poll", poll_interval_seconds=1)
    controller = EngineControl(config_path)
    state_path = workspace / "agents/.runtime/state.json"
    thread = Thread(target=lambda: controller.start(daemon=True), daemon=True)
    thread.start()

    wait_for(lambda: state_path.exists() and read_state(state_path)["process_running"] is True)
    pause_result = RUNNER.invoke(app, ["--config", str(config_path), "pause"])
    assert pause_result.exit_code == 0
    wait_for(lambda: read_state(state_path)["paused"] is True)

    first_add = RUNNER.invoke(app, ["--config", str(config_path), "add-task", "Mailbox first"])
    second_add = RUNNER.invoke(app, ["--config", str(config_path), "add-task", "Mailbox second"])
    assert first_add.exit_code == 0
    assert second_add.exit_code == 0
    wait_for(
        lambda: len(parse_task_cards((workspace / "agents/tasksbacklog.md").read_text(encoding="utf-8"))) == 2
    )
    backlog = parse_task_cards((workspace / "agents/tasksbacklog.md").read_text(encoding="utf-8"))

    reorder_result = RUNNER.invoke(
        app,
        [
            "--config",
            str(config_path),
            "queue",
            "reorder",
            backlog[1].task_id,
            backlog[0].task_id,
            "--json",
        ],
    )
    assert reorder_result.exit_code == 0
    payload = json.loads(reorder_result.stdout)
    assert payload["mode"] == "mailbox"
    assert payload["command_id"] is not None

    wait_for(
        lambda: [
            card.title
            for card in parse_task_cards((workspace / "agents/tasksbacklog.md").read_text(encoding="utf-8"))
        ]
        == ["Mailbox second", "Mailbox first"]
    )

    stop_result = RUNNER.invoke(app, ["--config", str(config_path), "stop"])
    assert stop_result.exit_code == 0
    wait_for(lambda: read_state(state_path)["process_running"] is False)
    thread.join(timeout=5.0)
    assert not thread.is_alive()


def test_cli_logs_reads_recent_structured_events(tmp_path: Path) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "control_mailbox")
    controller = EngineControl(config_path)

    controller.start(once=True)

    logs_result = RUNNER.invoke(
        app,
        ["--config", str(config_path), "logs", "--tail", "3", "--json"],
    )
    assert logs_result.exit_code == 0
    payload = json.loads(logs_result.stdout)
    assert [event["type"] for event in payload] == [
        EventType.ENGINE_STARTED.value,
        EventType.BACKLOG_EMPTY.value,
        EventType.ENGINE_STOPPED.value,
    ]


def test_cli_logs_invalid_event_log_json_error_stderr_only(tmp_path: Path) -> None:
    workspace, _ = load_workspace_fixture(tmp_path, "control_mailbox")
    (workspace / "agents/engine_events.log").write_text("{bad json\n", encoding="utf-8")

    result = run_cli_subprocess(
        workspace,
        "--config",
        "millrace.toml",
        "logs",
        "--json",
    )

    assert result.returncode == 1
    assert result.stdout == ""
    assert "Traceback" not in result.stderr
    payload = json.loads(result.stderr)
    assert payload["error"].startswith("event log is invalid:")


def test_cli_logs_follow_invalid_event_stream_json_error_stderr_only(tmp_path: Path) -> None:
    workspace, _ = load_workspace_fixture(tmp_path, "control_mailbox")
    log_path = workspace / "agents/engine_events.log"
    log_path.write_text("", encoding="utf-8")

    def corrupt_log() -> None:
        # Append repeatedly so the follow loop deterministically observes a bad record
        # instead of racing an idle-timeout exit.
        for _ in range(5):
            time.sleep(0.2)
            with log_path.open("a", encoding="utf-8") as handle:
                handle.write("{bad json\n")
                handle.flush()

    thread = Thread(target=corrupt_log, daemon=True)
    thread.start()
    result = run_cli_subprocess(
        workspace,
        "--config",
        "millrace.toml",
        "logs",
        "--follow",
        "--tail",
        "0",
        "--idle-timeout",
        "1",
        "--json",
    )
    thread.join(timeout=2.0)

    assert result.returncode == 1
    assert result.stdout == ""
    assert "Traceback" not in result.stderr
    payload = json.loads(result.stderr)
    assert payload["error"].startswith("event log is invalid:")


def test_cli_run_provenance_reports_frozen_plan_identity(tmp_path: Path) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "golden_path")
    append_subprocess_stage_config(config_path)
    script = write_needs_research_stage_driver(tmp_path)
    engine = MillraceEngine(
        config_path,
        stage_commands={
            StageType.BUILDER: [sys.executable, str(script), "builder"],
            StageType.QA: [sys.executable, str(script), "qa-blocked"],
            StageType.TROUBLESHOOT: [sys.executable, str(script), "troubleshoot-blocked"],
            StageType.CONSULT: [sys.executable, str(script), "consult-needs-research"],
        },
    )

    engine.start(once=True)

    run_dirs = sorted((workspace / "agents" / "runs").iterdir())
    assert run_dirs
    run_id = run_dirs[-1].name

    result = RUNNER.invoke(app, ["--config", str(config_path), "run-provenance", run_id, "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["run_id"] == run_id
    assert payload["selection"]["scope"] == "frozen_run"
    assert payload["routing_modes"] == ["frozen_plan"]
    assert payload["policy_hooks"]["record_count"] == 10
    assert payload["policy_hooks"]["hook_counts"] == {
        "cycle_boundary": 2,
        "post_stage": 4,
        "pre_stage": 4,
    }
    assert payload["policy_hooks"]["evaluator_counts"] == {
        "execution_integration_policy": 1,
        "execution_preflight_policy": 4,
        "execution_usage_budget": 1,
        "policy_hook_scaffold": 4,
    }
    assert payload["policy_hooks"]["decision_counts"] == {"not_evaluated": 4, "pass": 6}
    assert payload["policy_hooks"]["latest_decision"] == "not_evaluated"
    assert payload["policy_hooks"]["latest_hook"] == "post_stage"
    assert payload["policy_hooks"]["latest_evaluator"] == "policy_hook_scaffold"
    assert payload["policy_hooks"]["latest_notes"] == [
        "No concrete policy evaluator is registered for this hook yet."
    ]
    assert "Observed stage result captured for post-stage policy evaluation." in payload["policy_hooks"][
        "latest_evidence_summaries"
    ]
    assert payload["compile_snapshot"]["content"]["selected_mode_ref"]["id"] == "mode.standard"
    assert payload["compile_snapshot"]["content"]["selected_execution_loop_ref"]["id"] == "execution.standard"
    assert payload["compile_snapshot"]["frozen_plan"]["plan_id"].startswith("frozen-plan:")

    text_result = RUNNER.invoke(app, ["--config", str(config_path), "run-provenance", run_id])

    assert text_result.exit_code == 0
    assert "Selection scope: frozen_run" in text_result.stdout
    assert "Routing modes observed: frozen_plan" in text_result.stdout
    assert "Policy hook records: 10" in text_result.stdout
    assert "Policy hooks observed: cycle_boundary=2, post_stage=4, pre_stage=4" in text_result.stdout
    assert (
        "Policy evaluators: execution_integration_policy=1, execution_preflight_policy=4, "
        "execution_usage_budget=1, policy_hook_scaffold=4"
        in text_result.stdout
    )
    assert "Policy decisions observed: not_evaluated=4, pass=6" in text_result.stdout
    assert "Latest policy decision: not_evaluated" in text_result.stdout
    assert "Latest policy record: hook=post_stage evaluator=policy_hook_scaffold" in text_result.stdout
    assert "Latest policy notes:" in text_result.stdout
    assert "Latest policy evidence:" in text_result.stdout


def test_cli_run_provenance_preserves_compile_time_registry_provenance_after_workspace_shadow(
    tmp_path: Path,
) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "golden_path")
    append_subprocess_stage_config(config_path)
    script = write_needs_research_stage_driver(tmp_path)
    engine = MillraceEngine(
        config_path,
        stage_commands={
            StageType.BUILDER: [sys.executable, str(script), "builder"],
            StageType.QA: [sys.executable, str(script), "qa-blocked"],
            StageType.TROUBLESHOOT: [sys.executable, str(script), "troubleshoot-blocked"],
            StageType.CONSULT: [sys.executable, str(script), "consult-needs-research"],
        },
    )

    engine.start(once=True)

    run_dirs = sorted((workspace / "agents" / "runs").iterdir())
    assert run_dirs
    run_id = run_dirs[-1].name

    packaged_mode = next(
        document.definition
        for document in discover_registry_state(workspace, validate_catalog=False).packaged
        if document.key == ("mode", "mode.standard", "1.0.0")
    )
    shadow_payload = packaged_mode.model_dump(mode="json")
    shadow_payload["title"] = "Workspace standard shadow"
    shadow_payload["aliases"] = ["shadow-standard"]
    shadow_payload["source"] = {"kind": "workspace_defined"}
    persist_workspace_registry_object(
        workspace,
        packaged_mode.__class__.model_validate(shadow_payload),
    )

    result = RUNNER.invoke(app, ["--config", str(config_path), "run-provenance", run_id, "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["selection"]["mode"]["title"] != "Workspace standard shadow"
    assert payload["selection"]["mode"]["registry_layer"] == "packaged"
    assert payload["selection"]["mode"]["source_kind"] == "packaged_default"
    assert payload["selection"]["execution_loop"]["registry_layer"] == "packaged"


def test_cli_run_provenance_reports_current_preview_separately_from_frozen_history(tmp_path: Path) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "golden_path")
    loaded = load_engine_config(config_path)
    config = loaded.config
    config.paths.workspace = workspace
    config.paths.agents_dir = workspace / "agents"
    paths = build_runtime_paths(config)
    run_id = "cli-preview-contrast"

    prompt_path = workspace / "agents" / "_start.md"
    prompt_path.unlink()

    compile_result = compile_standard_runtime_selection(config, paths, run_id=run_id)

    assert compile_result.status.value == "ok"
    assert compile_result.snapshot is not None

    prompt_path.write_text("Workspace prompt restored for CLI preview\n", encoding="utf-8")
    packaged_mode = next(
        document.definition
        for document in discover_registry_state(workspace, validate_catalog=False).packaged
        if document.key == ("mode", "mode.standard", "1.0.0")
    )
    shadow_payload = packaged_mode.model_dump(mode="json")
    shadow_payload["title"] = "Workspace CLI preview shadow"
    shadow_payload["aliases"] = ["workspace-cli-preview-shadow"]
    shadow_payload["source"] = {"kind": "workspace_defined"}
    persist_workspace_registry_object(
        workspace,
        packaged_mode.__class__.model_validate(shadow_payload),
    )

    result = RUNNER.invoke(app, ["--config", str(config_path), "run-provenance", run_id, "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["selection"]["scope"] == "frozen_run"
    assert payload["selection"]["mode"]["registry_layer"] == "packaged"
    assert payload["selection"]["stage_bindings"][0]["prompt_source_kind"] == "package"
    assert payload["current_preview"]["scope"] == "preview"
    assert payload["current_preview"]["mode"]["registry_layer"] == "workspace"
    assert payload["current_preview"]["mode"]["title"] == "Workspace CLI preview shadow"
    assert payload["current_preview"]["stage_bindings"][0]["prompt_source_kind"] == "workspace"
    assert payload["current_preview_error"] is None

    text_result = RUNNER.invoke(app, ["--config", str(config_path), "run-provenance", run_id])

    assert text_result.exit_code == 0
    assert "Selection scope: frozen_run" in text_result.stdout
    assert "Frozen plan id:" in text_result.stdout
    assert "Current live preview:" in text_result.stdout
    assert "  Selection scope: preview" in text_result.stdout
    assert "  Preview plan id:" in text_result.stdout


def test_cli_run_provenance_survives_broken_live_registry_with_frozen_history_json(
    tmp_path: Path,
) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "golden_path")
    loaded = load_engine_config(config_path)
    config = loaded.config
    config.paths.workspace = workspace
    config.paths.agents_dir = workspace / "agents"
    paths = build_runtime_paths(config)
    run_id = "cli-broken-live-registry"

    compile_result = compile_standard_runtime_selection(config, paths, run_id=run_id)

    assert compile_result.status.value == "ok"
    assert compile_result.snapshot is not None

    broken_registry_path = workspace / "agents" / "registry" / "modes" / "broken__1.0.0.json"
    broken_registry_path.parent.mkdir(parents=True, exist_ok=True)
    broken_registry_path.write_text("{not valid json\n", encoding="utf-8")

    result = RUNNER.invoke(app, ["--config", str(config_path), "run-provenance", run_id, "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["selection"]["scope"] == "frozen_run"
    assert payload["selection"]["mode"]["ref"]["id"] == "mode.standard"
    assert payload["selection"]["mode"]["registry_layer"] == "packaged"
    assert payload["current_preview"] is None
    assert payload["current_preview_error"] is not None
    assert payload["current_preview_error"].startswith("standard runtime selection preview failed:")


def test_cli_run_provenance_reports_missing_run_cleanly(tmp_path: Path) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "golden_path")
    assert (workspace / "agents" / "runs").is_dir()

    result = RUNNER.invoke(app, ["--config", str(config_path), "run-provenance", "missing-run"])

    assert result.exit_code != 0
    assert "run provenance not found: missing-run" in result.output
    assert "Traceback" not in result.output


def test_cli_logs_follow_streams_new_events_and_exits_cleanly(tmp_path: Path) -> None:
    _, config_path = load_workspace_fixture(tmp_path, "control_mailbox")
    controller = EngineControl(config_path)

    def emit_once() -> None:
        time.sleep(0.2)
        controller.start(once=True)

    thread = Thread(target=emit_once, daemon=True)
    thread.start()
    follow_result = RUNNER.invoke(
        app,
        [
            "--config",
            str(config_path),
            "logs",
            "--follow",
            "--tail",
            "0",
            "--limit",
            "3",
            "--idle-timeout",
            "2",
            "--json",
        ],
    )
    thread.join(timeout=5.0)
    assert follow_result.exit_code == 0
    lines = [line for line in follow_result.stdout.splitlines() if line.strip()]
    assert [json.loads(line)["type"] for line in lines] == [
        EventType.ENGINE_STARTED.value,
        EventType.BACKLOG_EMPTY.value,
        EventType.ENGINE_STOPPED.value,
    ]


def test_python_module_entrypoint_supports_status_and_once(tmp_path: Path) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "control_mailbox")
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])

    status_result = subprocess.run(
        [sys.executable, "-m", "millrace_engine", "--config", str(config_path), "status", "--json"],
        cwd=workspace,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert status_result.returncode == 0
    assert json.loads(status_result.stdout)["runtime"]["process_running"] is False

    once_result = subprocess.run(
        [sys.executable, "-m", "millrace_engine", "--config", str(config_path), "start", "--once"],
        cwd=workspace,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert once_result.returncode == 0
    assert "Process: stopped" in once_result.stdout


def test_cli_status_exposes_pending_cycle_boundary_config_between_cycles(tmp_path: Path) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "config_hotswap")
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace('idle_mode = "watch"', 'idle_mode = "poll"', 1),
        encoding="utf-8",
    )
    append_subprocess_stage_config(config_path)
    script, gate_path, builder_started_path, qa_observed_path = write_hot_swap_stage_driver(tmp_path)
    RUNNER.invoke(app, ["--config", str(config_path), "add-task", "Cycle boundary task"])

    state_path = workspace / "agents/.runtime/state.json"
    engine = MillraceEngine(
        config_path,
        stage_commands={
            StageType.BUILDER: [sys.executable, str(script), "builder-block"],
            StageType.QA: [sys.executable, str(script), "qa-observe"],
            StageType.UPDATE: [sys.executable, str(script), "update-idle"],
        },
    )
    thread = Thread(target=lambda: engine.start(daemon=True), daemon=True)
    thread.start()

    wait_for(lambda: state_path.exists() and read_state(state_path)["process_running"] is True)
    wait_for(lambda: builder_started_path.exists())
    initial_hash = str(read_state(state_path)["config_hash"])

    set_result = RUNNER.invoke(
        app,
        [
            "--config",
            str(config_path),
            "config",
            "set",
            "research.idle_mode",
            "watch",
            "--json",
        ],
    )
    assert set_result.exit_code == 0
    assert json.loads(set_result.stdout)["mode"] == "mailbox"

    gate_path.write_text("release\n", encoding="utf-8")
    wait_for(
        lambda: qa_observed_path.exists() and read_state(state_path)["pending_config_hash"] is not None
    )

    state = read_state(state_path)
    assert str(state["config_hash"]) == initial_hash
    assert state["pending_config_boundary"] == "cycle_boundary"
    assert state["pending_config_hash"] is not None

    stop_result = RUNNER.invoke(app, ["--config", str(config_path), "stop"])
    assert stop_result.exit_code == 0
    wait_for(lambda: read_state(state_path)["process_running"] is False)
    thread.join(timeout=5.0)
    assert not thread.is_alive()


def test_cli_config_hotswap_applies_stage_boundary_change_before_next_stage(tmp_path: Path) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "config_hotswap")
    append_subprocess_stage_config(config_path)
    script, gate_path, builder_started_path, qa_observed_path = write_hot_swap_stage_driver(tmp_path)
    RUNNER.invoke(app, ["--config", str(config_path), "add-task", "Stage boundary task"])

    state_path = workspace / "agents/.runtime/state.json"
    engine = MillraceEngine(
        config_path,
        stage_commands={
            StageType.BUILDER: [sys.executable, str(script), "builder-block"],
            StageType.QA: [sys.executable, str(script), "qa-observe"],
            StageType.UPDATE: [sys.executable, str(script), "update-idle"],
        },
    )
    thread = Thread(target=lambda: engine.start(daemon=True), daemon=True)
    thread.start()

    wait_for(lambda: state_path.exists() and read_state(state_path)["process_running"] is True)
    wait_for(lambda: builder_started_path.exists())
    initial_hash = str(read_state(state_path)["config_hash"])

    set_result = RUNNER.invoke(
        app,
        [
            "--config",
            str(config_path),
            "config",
            "set",
            "stages.qa.model",
            "updated-model",
            "--json",
        ],
    )
    assert set_result.exit_code == 0
    gate_path.write_text("release\n", encoding="utf-8")

    wait_for(
        lambda: qa_observed_path.exists() and str(read_state(state_path)["config_hash"]) != initial_hash
    )

    assert qa_observed_path.read_text(encoding="utf-8") == "updated-model"
    archive_cards = parse_task_cards((workspace / "agents/tasksarchive.md").read_text(encoding="utf-8"))
    assert [card.title for card in archive_cards] == ["Stage boundary task"]
    run_id = next(
        run_dir.name
        for run_dir in sorted((workspace / "agents/runs").iterdir(), reverse=True)
        if any(
            record.event_name == "execution.stage.transition" and record.node_id == "qa"
            for record in read_transition_history(run_dir / "transition_history.jsonl")
        )
    )
    report = EngineControl(config_path).run_provenance(run_id)
    qa_records = [
        record
        for record in report.runtime_history
        if record.event_name == "execution.stage.transition" and record.node_id == "qa"
    ]
    assert len(qa_records) == 1
    assert qa_records[0].bound_execution_parameters.model == "updated-model"

    applied_events = [
        event
        for event in read_events(workspace)
        if event["type"] == EventType.CONFIG_APPLIED.value
    ]
    assert any(event["payload"]["boundary"] == "stage_boundary" for event in applied_events)
    assert EventType.CONFIG_CHANGED.value in read_event_types(workspace)

    stop_result = RUNNER.invoke(app, ["--config", str(config_path), "stop"])
    assert stop_result.exit_code == 0
    wait_for(lambda: read_state(state_path)["process_running"] is False)
    thread.join(timeout=5.0)
    assert not thread.is_alive()


def test_cli_config_hotswap_rebinds_complexity_profile_back_to_mode_default_when_disabled(tmp_path: Path) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "config_hotswap")
    append_subprocess_stage_config(config_path)
    _configure_complexity_routing_profiles(workspace, config_path)
    script, gate_path, builder_started_path, qa_observed_path = write_hot_swap_stage_driver(tmp_path)
    RUNNER.invoke(app, ["--config", str(config_path), "add-task", "Complexity profile fallback task"])

    state_path = workspace / "agents/.runtime/state.json"
    engine = MillraceEngine(
        config_path,
        stage_commands={
            StageType.BUILDER: [sys.executable, str(script), "builder-block"],
            StageType.QA: [sys.executable, str(script), "qa-observe"],
            StageType.UPDATE: [sys.executable, str(script), "update-idle"],
        },
    )
    thread = Thread(target=lambda: engine.start(daemon=True), daemon=True)
    thread.start()

    wait_for(lambda: state_path.exists() and read_state(state_path)["process_running"] is True)
    wait_for(lambda: builder_started_path.exists())
    initial_hash = str(read_state(state_path)["config_hash"])

    set_result = RUNNER.invoke(
        app,
        [
            "--config",
            str(config_path),
            "config",
            "set",
            "policies.complexity.enabled",
            "false",
            "--json",
        ],
    )
    assert set_result.exit_code == 0
    gate_path.write_text("release\n", encoding="utf-8")

    wait_for(
        lambda: qa_observed_path.exists() and str(read_state(state_path)["config_hash"]) != initial_hash
    )

    run_id = next(
        run_dir.name
        for run_dir in sorted((workspace / "agents/runs").iterdir(), reverse=True)
        if run_dir.name != ".gitkeep"
        and any(
            record.event_name == "execution.stage.transition" and record.node_id == "qa"
            for record in read_transition_history(run_dir / "transition_history.jsonl")
        )
    )
    report = EngineControl(config_path).run_provenance(run_id)
    qa_records = [
        record
        for record in report.runtime_history
        if record.event_name == "execution.stage.transition" and record.node_id == "qa"
    ]
    assert len(qa_records) == 1
    assert qa_records[0].bound_execution_parameters.model_profile_ref == RegistryObjectRef(
        kind=PersistedObjectKind.MODEL_PROFILE,
        id="model.default",
        version="1.0.0",
    )

    stop_result = RUNNER.invoke(app, ["--config", str(config_path), "stop"])
    assert stop_result.exit_code == 0
    wait_for(lambda: read_state(state_path)["process_running"] is False)
    thread.join(timeout=5.0)
    assert not thread.is_alive()


def test_cli_config_hotswap_rolls_back_after_immediate_stage_failure(tmp_path: Path) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "config_hotswap")
    append_subprocess_stage_config(config_path)
    script, gate_path, builder_started_path, qa_observed_path = write_hot_swap_stage_driver(tmp_path)
    RUNNER.invoke(app, ["--config", str(config_path), "add-task", "Rollback task"])

    state_path = workspace / "agents/.runtime/state.json"
    engine = MillraceEngine(
        config_path,
        stage_commands={
            StageType.BUILDER: [sys.executable, str(script), "builder-block"],
            StageType.QA: [sys.executable, str(script), "qa-observe"],
            StageType.UPDATE: [sys.executable, str(script), "update-idle"],
        },
    )
    thread = Thread(target=lambda: engine.start(daemon=True), daemon=True)
    thread.start()

    wait_for(lambda: state_path.exists() and read_state(state_path)["process_running"] is True)
    wait_for(lambda: builder_started_path.exists())
    initial_hash = str(read_state(state_path)["config_hash"])

    set_result = RUNNER.invoke(
        app,
        [
            "--config",
            str(config_path),
            "config",
            "set",
            "stages.qa.model",
            "bad-model",
            "--json",
        ],
    )
    assert set_result.exit_code == 0
    gate_path.write_text("release\n", encoding="utf-8")

    wait_for(lambda: read_state(state_path)["process_running"] is False)
    thread.join(timeout=5.0)
    assert not thread.is_alive()

    state = read_state(state_path)
    assert qa_observed_path.read_text(encoding="utf-8") == "bad-model"
    assert str(state["config_hash"]) == initial_hash
    assert state["pending_config_hash"] is None
    assert state["previous_config_hash"] is None
    assert state["rollback_armed"] is False

    applied_events = [
        event
        for event in read_events(workspace)
        if event["type"] == EventType.CONFIG_APPLIED.value
    ]
    assert any(event["payload"].get("rollback") is True for event in applied_events)


def test_cli_daemon_usage_budget_pause_surfaces_in_state_events_and_run_provenance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "golden_path")
    set_engine_idle_mode(config_path, "poll", poll_interval_seconds=1)
    append_usage_policy_config(config_path, remaining_threshold=10)
    state_path = workspace / "agents/.runtime/state.json"
    monkeypatch.setenv("USAGE_SAMPLER_ORCH_CURRENT", "9")

    engine = MillraceEngine(config_path)
    thread = Thread(target=lambda: engine.start(daemon=True), daemon=True)
    thread.start()

    wait_for(lambda: state_path.exists() and read_state(state_path)["process_running"] is True)
    wait_for(
        lambda: read_state(state_path)["paused"] is True
        and read_state(state_path)["pause_reason"] == "usage_budget_threshold"
        and bool(read_state(state_path)["pause_run_id"])
    )

    state = read_state(state_path)
    pause_run_id = str(state["pause_run_id"])

    status_result = RUNNER.invoke(app, ["--config", str(config_path), "status", "--json"])
    assert status_result.exit_code == 0
    status_payload = json.loads(status_result.stdout)
    assert status_payload["runtime"]["paused"] is True
    assert status_payload["runtime"]["pause_reason"] == "usage_budget_threshold"
    assert status_payload["runtime"]["pause_run_id"] == pause_run_id
    text_status_result = RUNNER.invoke(app, ["--config", str(config_path), "status"])
    assert text_status_result.exit_code == 0
    assert "Paused: yes" in text_status_result.stdout
    assert "Pause reason: usage_budget_threshold" in text_status_result.stdout
    assert f"Pause run id: {pause_run_id}" in text_status_result.stdout

    provenance_result = RUNNER.invoke(
        app,
        ["--config", str(config_path), "run-provenance", pause_run_id, "--json"],
    )
    assert provenance_result.exit_code == 0
    provenance_payload = json.loads(provenance_result.stdout)
    assert provenance_payload["policy_hooks"]["evaluator_counts"]["execution_usage_budget"] == 1
    assert provenance_payload["policy_hooks"]["decision_counts"]["policy_blocked"] == 1
    assert provenance_payload["policy_hooks"]["latest_decision"] == "policy_blocked"
    text_provenance_result = RUNNER.invoke(
        app,
        ["--config", str(config_path), "run-provenance", pause_run_id],
    )
    assert text_provenance_result.exit_code == 0
    assert "Policy hook records: 2" in text_provenance_result.stdout
    assert "Policy evaluators: execution_integration_policy=1, execution_usage_budget=1" in text_provenance_result.stdout
    assert "Policy decisions observed: pass=1, policy_blocked=1" in text_provenance_result.stdout
    assert "Latest policy decision: policy_blocked" in text_provenance_result.stdout

    stop_result = RUNNER.invoke(app, ["--config", str(config_path), "stop"])
    assert stop_result.exit_code == 0
    wait_for(lambda: read_state(state_path)["process_running"] is False)
    thread.join(timeout=5.0)
    assert not thread.is_alive()

    paused_events = [
        event
        for event in read_events(workspace)
        if event["type"] == EventType.ENGINE_PAUSED.value
    ]
    assert paused_events
    assert paused_events[-1]["payload"]["reason"] == "usage_budget_threshold"
    assert paused_events[-1]["payload"]["run_id"] == pause_run_id


def test_cli_daemon_inter_task_delay_does_not_starve_stop_command(tmp_path: Path) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "golden_path")
    append_subprocess_stage_config(config_path)
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace(
            "inter_task_delay_seconds = 0",
            "inter_task_delay_seconds = 2",
            1,
        ),
        encoding="utf-8",
    )
    script = write_outage_stage_driver(tmp_path)
    state_path = workspace / "agents/.runtime/state.json"

    engine = MillraceEngine(
        config_path,
        stage_commands={
            StageType.BUILDER: [sys.executable, str(script), "builder"],
            StageType.INTEGRATION: [sys.executable, str(script), "integration"],
            StageType.QA: [sys.executable, str(script), "qa"],
            StageType.UPDATE: [sys.executable, str(script), "update"],
        },
    )
    thread = Thread(target=lambda: engine.start(daemon=True), daemon=True)
    thread.start()

    wait_for(lambda: state_path.exists() and read_state(state_path)["process_running"] is True)
    wait_for(
        lambda: parse_task_cards((workspace / "agents/tasksarchive.md").read_text(encoding="utf-8"))
        and read_state(state_path)["execution_status"] == "IDLE"
    )

    started_at = time.monotonic()
    stop_result = RUNNER.invoke(app, ["--config", str(config_path), "stop"])
    assert stop_result.exit_code == 0
    wait_for(lambda: read_state(state_path)["process_running"] is False)
    elapsed = time.monotonic() - started_at
    thread.join(timeout=5.0)
    assert not thread.is_alive()

    archive_cards = parse_task_cards((workspace / "agents/tasksarchive.md").read_text(encoding="utf-8"))
    assert [card.title for card in archive_cards] == ["Ship the happy path"]
    run_dirs_with_history = [
        run_dir
        for run_dir in (workspace / "agents/runs").iterdir()
        if run_dir.is_dir() and (run_dir / "transition_history.jsonl").exists()
    ]
    assert len(run_dirs_with_history) == 1
    assert elapsed < 1.5


def test_cli_daemon_recovers_from_net_wait_and_resumes_task(tmp_path: Path) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "golden_path")
    append_subprocess_stage_config(config_path)
    append_outage_policy_config(config_path, policy="pause_resume", max_probes=1)
    script = write_outage_stage_driver(tmp_path)
    state_path = workspace / "agents/.runtime/state.json"

    engine = MillraceEngine(
        config_path,
        stage_commands={
            StageType.BUILDER: [sys.executable, str(script), "builder"],
            StageType.INTEGRATION: [sys.executable, str(script), "integration"],
            StageType.QA: [sys.executable, str(script), "qa"],
            StageType.UPDATE: [sys.executable, str(script), "update"],
        },
        transport_probe=SequencedTransportProbe(
            [
                TransportProbeResult(readiness=TransportReadiness.NET_WAIT, summary="transport=net_wait"),
                TransportProbeResult(readiness=TransportReadiness.READY, summary="transport=ready"),
                TransportProbeResult(readiness=TransportReadiness.READY, summary="transport=ready"),
                TransportProbeResult(readiness=TransportReadiness.READY, summary="transport=ready"),
            ]
        ),
        outage_probe=StaticOutageProbe(
            [
                OutageProbeResult(readiness=TransportReadiness.NET_WAIT, summary="probe=net_wait"),
                OutageProbeResult(readiness=TransportReadiness.READY, summary="probe=ready"),
            ]
        ),
    )
    thread = Thread(target=lambda: engine.start(daemon=True), daemon=True)
    thread.start()

    wait_for(lambda: state_path.exists() and read_state(state_path)["process_running"] is True)
    wait_for(
        lambda: parse_task_cards((workspace / "agents/tasksarchive.md").read_text(encoding="utf-8"))
        and read_state(state_path)["execution_status"] == "IDLE"
    )

    stop_result = RUNNER.invoke(app, ["--config", str(config_path), "stop"])
    assert stop_result.exit_code == 0
    wait_for(lambda: read_state(state_path)["process_running"] is False)
    thread.join(timeout=5.0)
    assert not thread.is_alive()

    archive_cards = parse_task_cards((workspace / "agents/tasksarchive.md").read_text(encoding="utf-8"))
    assert [card.title for card in archive_cards] == ["Ship the happy path"]
    run_dirs = sorted(
        run_dir
        for run_dir in (workspace / "agents/runs").iterdir()
        if run_dir.is_dir() and (run_dir / "transition_history.jsonl").exists()
    )
    assert len(run_dirs) >= 2
    reports = {
        run_dir.name: EngineControl(config_path).run_provenance(run_dir.name)
        for run_dir in run_dirs
    }
    blocked_run_id, blocked_report = next(
        (run_id, report)
        for run_id, report in reports.items()
        if report.policy_hooks is not None
        and "execution_outage_policy" in report.policy_hooks.evaluator_counts
    )
    resumed_run_id, resumed_report = next(
        (run_id, report)
        for run_id, report in reports.items()
        if run_id != blocked_run_id
        and any(
            record.event_name == "execution.stage.transition" and record.node_id == "builder"
            for record in report.runtime_history
        )
    )

    assert blocked_report.policy_hooks is not None
    assert blocked_report.policy_hooks.evaluator_counts["execution_outage_policy"] == 2
    assert blocked_report.routing_modes == ("frozen_plan", "outage_recovery")
    assert blocked_report.policy_hooks.decision_counts["net_wait"] == 2
    assert blocked_report.policy_hooks.decision_counts["pass"] == 3
    assert resumed_report.policy_hooks is not None
    assert "execution_outage_policy" not in resumed_report.policy_hooks.evaluator_counts
    assert resumed_report.routing_modes == ("frozen_plan",)

    blocked_cli = RUNNER.invoke(
        app,
        ["--config", str(config_path), "run-provenance", blocked_run_id, "--json"],
    )
    assert blocked_cli.exit_code == 0
    blocked_payload = json.loads(blocked_cli.stdout)
    assert blocked_payload["routing_modes"] == ["frozen_plan", "outage_recovery"]
    assert blocked_payload["policy_hooks"]["evaluator_counts"]["execution_outage_policy"] == 2
    assert blocked_payload["policy_hooks"]["decision_counts"]["net_wait"] == 2

    blocked_records = [
        record
        for record in read_transition_history(workspace / "agents/runs" / blocked_run_id / "transition_history.jsonl")
        if record.source == "execution_outage_policy"
    ]
    resumed_records = [
        record
        for record in read_transition_history(workspace / "agents/runs" / resumed_run_id / "transition_history.jsonl")
        if record.source == "execution_outage_policy"
    ]
    assert [record.policy_decision for record in blocked_records] == ["net_wait", "pass"]
    assert resumed_records == []
    assert EventType.STAGE_FAILED.value in read_event_types(workspace)


def test_cli_daemon_routes_net_wait_to_blocker_when_configured(tmp_path: Path) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "golden_path")
    append_subprocess_stage_config(config_path)
    append_outage_policy_config(config_path, policy="blocker", max_probes=1)
    script = write_outage_stage_driver(tmp_path)
    state_path = workspace / "agents/.runtime/state.json"

    engine = MillraceEngine(
        config_path,
        stage_commands={
            StageType.BUILDER: [sys.executable, str(script), "builder"],
            StageType.INTEGRATION: [sys.executable, str(script), "integration"],
            StageType.QA: [sys.executable, str(script), "qa"],
            StageType.UPDATE: [sys.executable, str(script), "update"],
        },
        transport_probe=SequencedTransportProbe(
            [
                TransportProbeResult(readiness=TransportReadiness.NET_WAIT, summary="transport=net_wait"),
                TransportProbeResult(readiness=TransportReadiness.READY, summary="transport=ready"),
                TransportProbeResult(readiness=TransportReadiness.READY, summary="transport=ready"),
            ]
        ),
        outage_probe=StaticOutageProbe(
            OutageProbeResult(readiness=TransportReadiness.NET_WAIT, summary="probe=net_wait")
        ),
    )
    thread = Thread(target=lambda: engine.start(daemon=True), daemon=True)
    thread.start()

    wait_for(lambda: state_path.exists() and read_state(state_path)["process_running"] is True)
    wait_for(
        lambda: read_state(state_path)["execution_status"] == "BLOCKED"
        and read_state(state_path)["paused"] is True
    )
    time.sleep(0.3)

    stop_result = RUNNER.invoke(app, ["--config", str(config_path), "stop"])
    assert stop_result.exit_code == 0
    wait_for(lambda: read_state(state_path)["process_running"] is False)
    thread.join(timeout=5.0)
    assert not thread.is_alive()

    state = read_state(state_path)
    assert state["execution_status"] == "BLOCKED"
    assert state["paused"] is True
    blocker_text = (workspace / "agents/tasksblocker.md").read_text(encoding="utf-8")
    assert "Ship the happy path" in blocker_text
    assert "### BLOCKED" in blocker_text
    assert "NET_WAIT recovery exhausted" in blocker_text
    assert parse_task_cards((workspace / "agents/tasksarchive.md").read_text(encoding="utf-8")) == []
    assert parse_task_cards((workspace / "agents/tasks.md").read_text(encoding="utf-8"))[0].title == "Ship the happy path"

    run_dirs = sorted((workspace / "agents/runs").iterdir())
    assert run_dirs
    run_dirs_with_history = [run_dir for run_dir in run_dirs if (run_dir / "transition_history.jsonl").exists()]
    assert len(run_dirs_with_history) == 1
    outage_records = [
        record
        for run_dir in run_dirs_with_history
        for record in read_transition_history(run_dir / "transition_history.jsonl")
        if record.source == "execution_outage_policy"
    ]
    assert outage_records[-1].policy_decision == "policy_blocked"


def test_cli_daemon_routes_net_wait_to_incident_when_configured(tmp_path: Path) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "golden_path")
    append_subprocess_stage_config(config_path)
    append_outage_policy_config(config_path, policy="incident", max_probes=1)
    script = write_outage_stage_driver(tmp_path)
    state_path = workspace / "agents/.runtime/state.json"
    latch_path = workspace / "agents/.runtime/research_recovery_latch.json"

    engine = MillraceEngine(
        config_path,
        stage_commands={
            StageType.BUILDER: [sys.executable, str(script), "builder"],
            StageType.INTEGRATION: [sys.executable, str(script), "integration"],
            StageType.QA: [sys.executable, str(script), "qa"],
            StageType.UPDATE: [sys.executable, str(script), "update"],
        },
        transport_probe=SequencedTransportProbe(
            [TransportProbeResult(readiness=TransportReadiness.NET_WAIT, summary="transport=net_wait")]
        ),
        outage_probe=StaticOutageProbe(
            OutageProbeResult(readiness=TransportReadiness.NET_WAIT, summary="probe=net_wait")
        ),
    )
    thread = Thread(target=lambda: engine.start(daemon=True), daemon=True)
    thread.start()

    wait_for(lambda: state_path.exists() and read_state(state_path)["process_running"] is True)
    wait_for(
        lambda: latch_path.exists()
        and read_state(state_path)["execution_status"] == "IDLE"
        and parse_task_cards((workspace / "agents/tasks.md").read_text(encoding="utf-8")) == []
    )

    stop_result = RUNNER.invoke(app, ["--config", str(config_path), "stop"])
    assert stop_result.exit_code == 0
    wait_for(lambda: read_state(state_path)["process_running"] is False)
    thread.join(timeout=5.0)
    assert not thread.is_alive()

    latch = load_research_recovery_latch(latch_path)
    assert latch is not None
    assert latch.incident_path is not None
    blocker_text = (workspace / "agents/tasksblocker.md").read_text(encoding="utf-8")
    assert "### NEEDS_RESEARCH" in blocker_text
    assert "Ship the happy path" in blocker_text
    backburner_text = (workspace / "agents/tasksbackburner.md").read_text(encoding="utf-8")
    assert "Ship the happy path" in backburner_text

    run_dirs = sorted((workspace / "agents/runs").iterdir())
    assert run_dirs
    outage_records = [
        record
        for run_dir in run_dirs
        for record in read_transition_history(run_dir / "transition_history.jsonl")
        if record.source == "execution_outage_policy"
    ]
    assert outage_records[-1].policy_decision == "policy_blocked"


def test_cli_research_report_and_status_detail_show_parent_handoff(tmp_path: Path) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "control_mailbox")
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace('mode = "stub"', 'mode = "auto"', 1),
        encoding="utf-8",
    )
    (workspace / "agents" / "tasks.md").write_text(
        "\n".join(
            [
                "# Active Task",
                "",
                "## 2026-03-19 - Visible parent handoff",
                "",
                "- **Goal:** Surface parent execution linkage in CLI visibility commands.",
                "- **Acceptance:** Research CLI output includes the handoff id and parent run id.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    loaded = load_engine_config(config_path)
    paths = build_runtime_paths(loaded.config)
    queue = TaskQueue(paths)
    active_task = queue.active_task()
    assert active_task is not None

    diagnostics_dir = workspace / "agents" / "diagnostics" / "diag-cli-parent-handoff"
    diagnostics_dir.mkdir(parents=True, exist_ok=True)
    latch = queue.quarantine(
        active_task,
        "Consult exhausted the local path",
        Path("agents/ideas/incidents/incoming/INC-CLI-PARENT-001.md"),
        diagnostics_dir=diagnostics_dir,
    )
    handoff = ExecutionResearchHandoff(
        handoff_id=f"execution-run-cli:needs_research:{latch.batch_id}",
        parent_run=CrossPlaneParentRun(
            plane="execution",
            run_id="execution-run-cli",
            snapshot_id="snapshot-execution-run-cli",
            frozen_plan_id="frozen-plan:cli123",
            frozen_plan_hash="cli123",
            transition_history_path=Path("agents/runs/execution-run-cli/transition_history.jsonl"),
        ),
        task_id=active_task.task_id,
        task_title=active_task.title,
        stage="Consult",
        reason="Consult exhausted the local path",
        incident_path=latch.incident_path,
        diagnostics_dir=diagnostics_dir,
        recovery_batch_id=latch.batch_id,
        failure_signature=latch.failure_signature,
        frozen_backlog_cards=latch.frozen_backlog_cards,
        retained_backlog_cards=latch.retained_backlog_cards,
    )
    latch_path = workspace / "agents/.runtime/research_recovery_latch.json"
    latch_path.write_text(
        latch.model_copy(update={"handoff": handoff}).model_dump_json(indent=2, exclude_none=True) + "\n",
        encoding="utf-8",
    )

    plane = ResearchPlane(loaded.config, paths)
    dispatch = plane.dispatch_ready_work(run_id="research-cli-run", resolve_assets=False)
    assert dispatch is not None
    plane.shutdown()

    research_result = RUNNER.invoke(app, ["--config", str(config_path), "research"])
    assert research_result.exit_code == 0
    assert handoff.handoff_id in research_result.stdout
    assert "parent=execution-run-cli" in research_result.stdout

    status_result = RUNNER.invoke(app, ["--config", str(config_path), "status", "--detail"])
    assert status_result.exit_code == 0
    assert handoff.handoff_id in status_result.stdout
    assert "parent=execution-run-cli" in status_result.stdout


def test_publish_sync_command_reports_manifest_selection_in_json(tmp_path: Path) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "control_mailbox")
    (workspace / "README.md").write_text("workspace readme\n", encoding="utf-8")
    write_staging_manifest(
        workspace,
        payload="\n".join(
            [
                "version: 1",
                "paths:",
                "  - README.md",
                "",
            ]
        ),
    )
    staging_dir = tmp_path / "staging"

    result = RUNNER.invoke(
        app,
        [
            "--config",
            str(config_path),
            "publish",
            "sync",
            "--staging-repo-dir",
            str(staging_dir),
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["selection"]["manifest_source_kind"] == "workspace"
    assert payload["selection"]["required_paths"] == ["README.md"]
    assert payload["selection"]["staging_repo_dir"] == str(staging_dir.resolve())
    assert payload["entries"][0]["action"] == "synced"


def test_publish_preflight_and_commit_commands_render_json(tmp_path: Path) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "control_mailbox")
    (workspace / "README.md").write_text("workspace readme\n", encoding="utf-8")
    write_staging_manifest(
        workspace,
        payload="\n".join(
            [
                "version: 1",
                "paths:",
                "  - README.md",
                "",
            ]
        ),
    )
    staging_dir = tmp_path / "staging"
    staging_dir.mkdir()
    assert git_cli(staging_dir, "init").returncode == 0
    assert git_cli(staging_dir, "config", "user.email", "tests@example.com").returncode == 0
    assert git_cli(staging_dir, "config", "user.name", "Millrace Tests").returncode == 0

    sync_result = RUNNER.invoke(
        app,
        [
            "--config",
            str(config_path),
            "publish",
            "sync",
            "--staging-repo-dir",
            str(staging_dir),
            "--json",
        ],
    )
    assert sync_result.exit_code == 0, sync_result.output

    preflight_result = RUNNER.invoke(
        app,
        [
            "--config",
            str(config_path),
            "publish",
            "preflight",
            "--staging-repo-dir",
            str(staging_dir),
            "--message",
            "CLI publish test",
            "--no-push",
            "--json",
        ],
    )
    assert preflight_result.exit_code == 0, preflight_result.output
    preflight_payload = json.loads(preflight_result.stdout)
    assert preflight_payload["status"] == "ready"
    assert preflight_payload["commit_allowed"] is True
    assert preflight_payload["skip_reason"] == "push_disabled"

    commit_result = RUNNER.invoke(
        app,
        [
            "--config",
            str(config_path),
            "publish",
            "commit",
            "--staging-repo-dir",
            str(staging_dir),
            "--message",
            "CLI publish test",
            "--no-push",
            "--json",
        ],
    )
    assert commit_result.exit_code == 0, commit_result.output
    commit_payload = json.loads(commit_result.stdout)
    assert commit_payload["status"] == "committed"
    assert commit_payload["marker"] == "SKIP_PUBLISH reason=push_disabled"
    assert commit_payload["commit_sha"]


def test_engine_control_start_uses_engine_runtime_helper(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _workspace, config_path = load_workspace_fixture(tmp_path, "golden_path")
    expected = RuntimeState.model_validate(
        {
            "process_running": False,
            "paused": False,
            "execution_status": ExecutionStatus.IDLE,
            "research_status": ResearchStatus.IDLE,
            "backlog_depth": 0,
            "deferred_queue_size": 0,
            "config_hash": "test-config-hash",
            "updated_at": "2026-04-03T00:00:00Z",
            "mode": "once",
        }
    )
    observed: list[tuple[Path, bool, bool]] = []

    def fake_start_engine(
        helper_config_path: Path | str,
        *,
        daemon: bool = False,
        once: bool = False,
    ) -> RuntimeState:
        observed.append((Path(helper_config_path), daemon, once))
        return expected

    monkeypatch.setattr("millrace_engine.control.start_engine", fake_start_engine)

    result = EngineControl(config_path).start(once=True)

    assert result == expected
    assert observed == [(config_path.resolve(), False, True)]
