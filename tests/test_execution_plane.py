from __future__ import annotations

import json
from pathlib import Path
import sys
import warnings

import pytest

from millrace_engine.compiler import CompileStatus, FrozenRunCompiler
from millrace_engine.config import build_runtime_paths, load_engine_config
from millrace_engine.control import ControlError, EngineControl
from millrace_engine.contracts import (
    ControlPlane,
    ExecutionStatus,
    LoopConfigDefinition,
    ModelProfileDefinition,
    PersistedObjectKind,
    ProcedureScope,
    RegisteredStageKindDefinition,
    RegistryObjectRef,
    ReusableProcedureArtifact,
    RunnerKind,
    StageType,
)
from millrace_engine.events import EventType
from millrace_engine.engine import MillraceEngine
from millrace_engine.markdown import parse_task_cards
from millrace_engine.planes.execution import ExecutionPlane
from millrace_engine.policies import POLICY_CYCLE_NODE_ID, PolicyHook
from millrace_engine.policies.pacing import ExecutionPacingEvaluator
from millrace_engine.policies.outage import (
    OutageAttempt,
    OutagePolicyError,
    OutagePolicySnapshot,
    OutageProbeResult,
    OutageRoute,
    OutageTrigger,
    evaluate_outage_attempt,
    outage_policy_record,
)
from millrace_engine.policies.usage_budget import ExecutionUsageBudgetEvaluator
from millrace_engine.policies.preflight import ExecutionPreflightEvaluator
from millrace_engine.policies.transport import StaticTransportProbe, TransportProbeResult, TransportReadiness
from millrace_engine.provenance import (
    BoundExecutionParameters,
    FrozenPlanIdentity,
    RuntimeProvenanceContext,
    TransitionHistoryStore,
    read_transition_history,
)
from millrace_engine.queue import load_research_recovery_latch
from millrace_engine.registry import discover_registry_state, persist_workspace_registry_object
from millrace_engine.standard_runtime import compile_standard_runtime_selection, preview_execution_runtime_selection
from millrace_engine.stages.base import StageExecutionError
from millrace_engine.standard_runtime_views import runtime_selection_view_from_snapshot

from tests.provenance_support import (
    append_stage_transition_record,
    compile_mode_provenance,
    compile_standard_provenance,
    load_provenance_fixture,
    packaged_definition,
    persist_packaged_shadow,
    prompt_path,
)
from tests.support import load_workspace_fixture


def write_stage_driver(tmp_path: Path) -> Path:
    script = tmp_path / "stage_driver.py"
    state_path = tmp_path / "stage_state.json"
    script.write_text(
        "\n".join(
            [
                "from __future__ import annotations",
                "",
                "from pathlib import Path",
                "import json",
                "import os",
                "import sys",
                "",
                f"STATE_PATH = Path({str(state_path)!r})",
                "",
                "mode = sys.argv[1]",
                "last_path = Path(os.environ['MILLRACE_LAST_RESPONSE_PATH'])",
                "",
                "def bump(label: str) -> int:",
                "    if STATE_PATH.exists():",
                "        state = json.loads(STATE_PATH.read_text(encoding='utf-8'))",
                "    else:",
                "        state = {}",
                "    count = int(state.get(label, 0)) + 1",
                "    state[label] = count",
                "    STATE_PATH.write_text(json.dumps(state, sort_keys=True), encoding='utf-8')",
                "    return count",
                "",
                "def emit(marker: str | None = None, *, message: str = '', last: str | None = None) -> None:",
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
                "    raise SystemExit(0)",
                "",
                "count = bump(mode)",
                "if mode == 'builder':",
                "    emit('BUILDER_COMPLETE', message='Builder finished')",
                "if mode == 'large-plan':",
                "    emit('LARGE_PLAN_COMPLETE', message='Large plan finished')",
                "if mode == 'large-plan-blocked':",
                "    emit('BLOCKED', message='Large plan blocked')",
                "if mode == 'large-execute':",
                "    emit('LARGE_EXECUTE_COMPLETE', message='Large execute finished')",
                "if mode == 'large-execute-blocked':",
                "    emit('BLOCKED', message='Large execute blocked')",
                "if mode == 'reassess':",
                "    emit('LARGE_REASSESS_COMPLETE', message='Reassess finished')",
                "if mode == 'refactor':",
                "    emit('LARGE_REFACTOR_COMPLETE', message='Refactor finished')",
                "if mode == 'refactor-blocked':",
                "    emit('BLOCKED', message='Refactor hit a blocker')",
                "if mode == 'builder-check-prompt':",
                "    prompt = os.environ['MILLRACE_PROMPT']",
                "    if 'Builder prompt fixture text' not in prompt:",
                "        print('missing prompt fixture text')",
                "        raise SystemExit(7)",
                "    if 'Ship the happy path' not in prompt:",
                "        print('missing task title')",
                "        raise SystemExit(8)",
                "    emit('BUILDER_COMPLETE', message='Builder saw prompt asset')",
                "if mode == 'builder-check-compounding-prompt':",
                "    prompt = os.environ['MILLRACE_PROMPT']",
                "    if 'Injected reusable procedures:' not in prompt:",
                "        print('missing injected procedures heading')",
                "        raise SystemExit(10)",
                "    if 'Workspace Builder Procedure' not in prompt:",
                "        print('missing selected workspace procedure')",
                "        raise SystemExit(11)",
                "    if 'proc.workspace.qa.ignored' in prompt:",
                "        print('unexpected qa procedure in builder prompt')",
                "        raise SystemExit(12)",
                "    if 'procedure truncated to fit stage budget' not in prompt:",
                "        print('missing truncation marker')",
                "        raise SystemExit(13)",
                "    emit('BUILDER_COMPLETE', message='Builder saw injected procedures')",
                "if mode == 'integration':",
                "    emit('INTEGRATION_COMPLETE', message='Integration finished')",
                "if mode == 'qa-complete':",
                "    emit('QA_COMPLETE', message='QA finished')",
                "if mode == 'qa-check-compounding-prompt':",
                "    prompt = os.environ['MILLRACE_PROMPT']",
                "    if 'Injected reusable procedures:' not in prompt:",
                "        print('missing injected procedures heading')",
                "        raise SystemExit(14)",
                "    if 'Builder execution candidate' not in prompt:",
                "        print('missing builder candidate title')",
                "        raise SystemExit(15)",
                "    if 'Builder finished' not in prompt:",
                "        print('missing builder procedure body')",
                "        raise SystemExit(16)",
                "    emit('QA_COMPLETE', message='QA saw injected procedures')",
                "if mode == 'qa-missing':",
                "    print('QA finished without a marker')",
                "    raise SystemExit(0)",
                "if mode == 'qa-quickfix':",
                "    emit('QUICKFIX_NEEDED', message='QA found gaps')",
                "if mode == 'qa-quickfix-then-pass':",
                "    if count == 1:",
                "        emit('QUICKFIX_NEEDED', message='QA found gaps')",
                "    emit('QA_COMPLETE', message='QA passed after recovery')",
                "if mode == 'qa-blocked':",
                "    emit('BLOCKED', message='QA is blocked')",
                "if mode == 'qa-blocked-then-pass':",
                "    if count == 1:",
                "        emit('BLOCKED', message='QA is blocked')",
                "    emit('QA_COMPLETE', message='QA passed after troubleshoot')",
                "if mode == 'hotfix-builder':",
                "    emit('BUILDER_COMPLETE', message='Hotfix applied')",
                "if mode == 'doublecheck-qa':",
                "    emit('QA_COMPLETE', message='Doublecheck passed')",
                "if mode == 'doublecheck-always-quickfix':",
                "    emit('QUICKFIX_NEEDED', message='Doublecheck still finds gaps')",
                "if mode == 'troubleshoot-complete':",
                "    emit('TROUBLESHOOT_COMPLETE', message='Troubleshoot restored a local path')",
                "if mode == 'troubleshoot-blocked':",
                "    emit('BLOCKED', message='Troubleshoot could not resolve the blocker')",
                "if mode == 'consult-complete':",
                "    emit('CONSULT_COMPLETE', message='Consult found a local path')",
                "if mode == 'consult-needs-research':",
                "    print('Consult requires research')",
                "    print('### NEEDS_RESEARCH')",
                "    last_path.write_text(",
                "        'Incident: agents/ideas/incidents/incoming/INC-FIXTURE-001.md\\n### NEEDS_RESEARCH\\n',",
                "        encoding='utf-8',",
                "    )",
                "    raise SystemExit(0)",
                "if mode == 'update-idle':",
                "    print('Update performed maintenance only')",
                "    raise SystemExit(0)",
                "raise SystemExit(9)",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return script


def write_policy_stage_driver(tmp_path: Path) -> tuple[Path, Path]:
    script = tmp_path / "policy_stage_driver.py"
    env_path = tmp_path / "policy_env.json"
    script.write_text(
        "\n".join(
            [
                "from __future__ import annotations",
                "",
                "from pathlib import Path",
                "import json",
                "import os",
                "import sys",
                "",
                f"ENV_PATH = Path({str(env_path)!r})",
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
                "if mode == 'builder-record-policy-env':",
                "    ENV_PATH.write_text(",
                "        json.dumps(",
                "            {",
                "                'allow_search': os.environ['MILLRACE_ALLOW_SEARCH'],",
                "                'allow_network': os.environ['MILLRACE_ALLOW_NETWORK'],",
                "            },",
                "            sort_keys=True,",
                "        ),",
                "        encoding='utf-8',",
                "    )",
                "    emit('BUILDER_COMPLETE', message='Builder recorded policy env')",
                "",
                "if mode == 'qa-complete':",
                "    emit('QA_COMPLETE', message='QA finished')",
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
    return script, env_path


def append_subprocess_stage_config(config_path: Path) -> None:
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
                'model = "qa-model"',
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


def seed_active_quickfix_artifact(workspace: Path) -> Path:
    quickfix_path = workspace / "agents" / "quickfix.md"
    quickfix_path.write_text(
        "# Quickfix\n\n- unresolved issue\n- rerun validation after the hotfix\n",
        encoding="utf-8",
    )
    return quickfix_path


def configure_execution_plane(
    workspace: Path,
    config_path: Path,
    stage_commands: dict[StageType, list[str]],
    *,
    integration_mode: str = "always",
    update_on_empty: bool = True,
    quickfix_max_attempts: int = 2,
    emitted: list[tuple[EventType, dict[str, object]]] | None = None,
    runtime_provenance: RuntimeProvenanceContext | None = None,
    mutate_config=None,
    transport_probe=None,
) -> ExecutionPlane:
    loaded = load_engine_config(config_path)
    config = loaded.config
    config.paths.workspace = workspace
    config.paths.agents_dir = workspace / "agents"
    config.execution.integration_mode = integration_mode
    config.execution.run_update_on_empty = update_on_empty
    config.execution.quickfix_max_attempts = quickfix_max_attempts
    if mutate_config is not None:
        mutate_config(config)

    for stage_type in [
        StageType.BUILDER,
        StageType.INTEGRATION,
        StageType.QA,
        StageType.HOTFIX,
        StageType.DOUBLECHECK,
        StageType.TROUBLESHOOT,
        StageType.CONSULT,
        StageType.UPDATE,
        StageType.LARGE_PLAN,
        StageType.LARGE_EXECUTE,
        StageType.REASSESS,
        StageType.REFACTOR,
    ]:
        stage_config = config.stages[stage_type]
        stage_config.runner = RunnerKind.SUBPROCESS
        stage_config.model = "fixture-model"
        stage_config.timeout_seconds = 30

    emit_event = None
    if emitted is not None:
        def _capture_event(event_type: EventType, payload: dict[str, object]) -> None:
            emitted.append((event_type, dict(payload)))

        emit_event = _capture_event

    return ExecutionPlane(
        config,
        build_runtime_paths(config),
        stage_commands=stage_commands,
        emit_event=emit_event,
        runtime_provenance=runtime_provenance,
        transport_probe=transport_probe,
    )


def _builder_binding(selection):
    return next(binding for binding in selection.stage_bindings if binding.node_id == "builder")


def _complexity_profile_definition(
    object_id: str,
    *,
    builder_model: str,
    qa_model: str,
    hotfix_model: str,
    doublecheck_model: str,
    doublecheck_effort: str,
) -> ModelProfileDefinition:
    builder_binding = {
        "runner": "codex",
        "model": builder_model,
        "effort": "high",
    }
    return ModelProfileDefinition.model_validate(
        {
            "id": object_id,
            "version": "1.0.0",
            "tier": "golden",
            "title": f"{object_id} profile",
            "summary": "Workspace complexity-routing profile for execution selection tests.",
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
                    {"kind_id": "execution.builder", "binding": builder_binding},
                    {"kind_id": "execution.large-plan", "binding": builder_binding},
                    {"kind_id": "execution.large-execute", "binding": builder_binding},
                    {"kind_id": "execution.reassess", "binding": builder_binding},
                    {
                        "kind_id": "execution.refactor",
                        "binding": {
                            "runner": "codex",
                            "model": builder_model,
                            "effort": "medium",
                        },
                    },
                    {
                        "kind_id": "execution.qa",
                        "binding": {
                            "runner": "codex",
                            "model": qa_model,
                            "effort": "xhigh",
                        },
                    },
                    {
                        "kind_id": "execution.hotfix",
                        "binding": {
                            "runner": "codex",
                            "model": hotfix_model,
                            "effort": "medium",
                        },
                    },
                    {
                        "kind_id": "execution.doublecheck",
                        "binding": {
                            "runner": "codex",
                            "model": doublecheck_model,
                            "effort": doublecheck_effort,
                        },
                    },
                ),
            },
        }
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
    persist_workspace_registry_object(
        workspace,
        _complexity_profile_definition(
            "model.workspace.moderate",
            builder_model="builder-moderate-model",
            qa_model="qa-moderate-model",
            hotfix_model="hotfix-moderate-model",
            doublecheck_model="doublecheck-moderate-model",
            doublecheck_effort="medium",
        ),
    )
    persist_workspace_registry_object(
        workspace,
        _complexity_profile_definition(
            "model.workspace.involved",
            builder_model="builder-involved-model",
            qa_model="qa-involved-model",
            hotfix_model="hotfix-involved-model",
            doublecheck_model="doublecheck-involved-model",
            doublecheck_effort="high",
        ),
    )
    persist_workspace_registry_object(
        workspace,
        _complexity_profile_definition(
            "model.workspace.complex",
            builder_model="builder-complex-model",
            qa_model="qa-complex-model",
            hotfix_model="hotfix-complex-model",
            doublecheck_model="doublecheck-complex-model",
            doublecheck_effort="xhigh",
        ),
    )


def _stage_transition_records(records):
    return [record for record in records if record.event_name == "execution.stage.transition"]


def _policy_hook_records(records):
    return [record for record in records if record.policy_evaluation is not None]


def _load_compounding_candidates(workspace: Path, run_id: str) -> list[ReusableProcedureArtifact]:
    candidate_dir = workspace / "agents/compounding/procedures" / run_id
    if not candidate_dir.exists():
        return []
    return [
        ReusableProcedureArtifact.model_validate_json(path.read_text(encoding="utf-8"))
        for path in sorted(candidate_dir.glob("*.json"))
    ]


def _write_compounding_procedure(
    workspace: Path,
    *,
    filename: str,
    procedure_id: str,
    scope: ProcedureScope,
    source_stage: StageType,
    title: str,
    summary: str,
    procedure_markdown: str,
) -> Path:
    target_dir = workspace / "agents/compounding/procedures"
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / filename
    artifact = ReusableProcedureArtifact(
        procedure_id=procedure_id,
        scope=scope,
        source_run_id="source-run",
        source_stage=source_stage,
        title=title,
        summary=summary,
        procedure_markdown=procedure_markdown,
        tags=("fixture",),
        evidence_refs=("agents/runs/source-run/transition_history.jsonl",),
        created_at="2026-04-07T18:00:00Z",
    )
    path.write_text(artifact.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return path


def _prepend_task_metadata_lines(workspace: Path, *lines: str) -> None:
    backlog_path = workspace / "agents/tasksbacklog.md"
    original = backlog_path.read_text(encoding="utf-8")
    parts = original.split("\n\n", 2)
    assert len(parts) == 3
    updated = f"{parts[0]}\n\n{parts[1]}\n\n" + "\n".join(lines) + "\n" + parts[2]
    backlog_path.write_text(updated, encoding="utf-8")


@pytest.mark.parametrize(
    ("size_latch", "task_complexity", "expected_profile_id", "expected_builder_model", "expected_doublecheck_model"),
    [
        ("SMALL", "MODERATE", "model.workspace.moderate", "builder-moderate-model", "doublecheck-moderate-model"),
        ("SMALL", "INVOLVED", "model.workspace.involved", "builder-involved-model", "doublecheck-involved-model"),
        ("LARGE", "COMPLEX", "model.workspace.complex", "builder-complex-model", "doublecheck-complex-model"),
    ],
)
def test_execution_runtime_selection_routes_complexity_profile_matrix(
    tmp_path: Path,
    size_latch: str,
    task_complexity: str,
    expected_profile_id: str,
    expected_builder_model: str,
    expected_doublecheck_model: str,
) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "control_mailbox")
    _configure_complexity_routing_profiles(workspace, config_path)

    loaded = load_engine_config(config_path)
    selection = preview_execution_runtime_selection(
        loaded.config,
        build_runtime_paths(loaded.config),
        preview_run_id="complexity-routing-matrix",
        size_latch=size_latch,
        current_status=ExecutionStatus.IDLE,
        task_complexity=task_complexity,
    )

    assert selection.complexity is not None
    assert selection.complexity.enabled is True
    assert selection.complexity.band.value == task_complexity.lower()
    assert selection.complexity.selected_model_profile_ref is not None
    assert selection.complexity.selected_model_profile_ref.id == expected_profile_id
    assert selection.model_profile is not None
    assert selection.model_profile.ref.id == expected_profile_id

    bound_models = {binding.node_id: binding.model for binding in selection.stage_bindings}
    assert bound_models["qa"] == f"qa-{task_complexity.lower()}-model"
    assert bound_models["hotfix"] == f"hotfix-{task_complexity.lower()}-model"
    assert bound_models["doublecheck"] == expected_doublecheck_model
    if size_latch == "LARGE":
        assert bound_models["large_plan"] == expected_builder_model
        assert bound_models["large_execute"] == expected_builder_model
        assert bound_models["reassess"] == expected_builder_model
        assert bound_models["refactor"] == expected_builder_model
        assert "builder" not in bound_models
        assert selection.complexity.routed_node_ids == (
            "large_plan",
            "large_execute",
            "reassess",
            "refactor",
            "qa",
            "hotfix",
            "doublecheck",
        )
    else:
        assert bound_models["builder"] == expected_builder_model
        assert selection.complexity.routed_node_ids == ("builder", "qa", "hotfix", "doublecheck")


def test_execution_runtime_selection_preserves_stage_level_model_override_over_complexity_profile(
    tmp_path: Path,
) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "control_mailbox")
    _configure_complexity_routing_profiles(workspace, config_path)

    loaded = load_engine_config(config_path)
    loaded.config.stages[StageType.QA].model = "qa-explicit-operator-override"
    selection = preview_execution_runtime_selection(
        loaded.config,
        build_runtime_paths(loaded.config),
        preview_run_id="complexity-stage-override",
        size_latch="SMALL",
        current_status=ExecutionStatus.IDLE,
        task_complexity="COMPLEX",
    )

    qa_binding = next(binding for binding in selection.stage_bindings if binding.node_id == "qa")

    assert selection.model_profile is not None
    assert selection.model_profile.ref.id == "model.workspace.complex"
    assert qa_binding.model_profile is not None
    assert qa_binding.model_profile.ref.id == "model.workspace.complex"
    assert qa_binding.model == "qa-explicit-operator-override"


def test_execution_plane_golden_path_runs_builder_integration_qa_update_and_archive(tmp_path: Path) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "golden_path")
    script = write_stage_driver(tmp_path)

    plane = configure_execution_plane(
        workspace,
        config_path,
        {
            StageType.BUILDER: [sys.executable, str(script), "builder"],
            StageType.INTEGRATION: [sys.executable, str(script), "integration"],
            StageType.QA: [sys.executable, str(script), "qa-complete"],
            StageType.UPDATE: [sys.executable, str(script), "update-idle"],
        },
    )

    result = plane.run_once()

    assert result.final_status is ExecutionStatus.IDLE
    assert [stage.stage for stage in result.stage_results] == [
        StageType.BUILDER,
        StageType.INTEGRATION,
        StageType.QA,
        StageType.UPDATE,
    ]
    assert result.stage_results[-1].status == "UPDATE_COMPLETE"
    assert result.archived_task is not None
    assert parse_task_cards((workspace / "agents/tasks.md").read_text(encoding="utf-8")) == []
    archive_cards = parse_task_cards((workspace / "agents/tasksarchive.md").read_text(encoding="utf-8"))
    assert [card.title for card in archive_cards] == ["Ship the happy path"]


def test_execution_plane_refreshes_size_status_latch_and_retains_large_across_new_instance(
    tmp_path: Path,
) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "golden_path")
    script = write_stage_driver(tmp_path)
    _prepend_task_metadata_lines(
        workspace,
        "- **Files to touch:**",
        "  - `millrace.toml`",
        "- **Complexity:** INVOLVED",
    )

    def _task_large(config) -> None:
        config.sizing.mode = "task"
        config.sizing.task.file_count_threshold = 1
        config.sizing.task.nonempty_line_count_threshold = 999_999_999

    plane = configure_execution_plane(
        workspace,
        config_path,
        {
            StageType.LARGE_PLAN: [sys.executable, str(script), "large-plan"],
            StageType.LARGE_EXECUTE: [sys.executable, str(script), "large-execute"],
            StageType.REASSESS: [sys.executable, str(script), "reassess"],
            StageType.REFACTOR: [sys.executable, str(script), "refactor"],
            StageType.QA: [sys.executable, str(script), "qa-complete"],
            StageType.UPDATE: [sys.executable, str(script), "update-idle"],
        },
        mutate_config=_task_large,
    )

    result = plane.run_once()

    assert result.final_status is ExecutionStatus.IDLE
    assert (workspace / "agents/size_status.md").read_text(encoding="utf-8") == "### LARGE\n"

    def _task_small(config) -> None:
        config.execution.run_update_on_empty = False
        config.sizing.mode = "task"
        config.sizing.task.file_count_threshold = 999_999_999
        config.sizing.task.nonempty_line_count_threshold = 999_999_999

    restarted = configure_execution_plane(
        workspace,
        config_path,
        {},
        mutate_config=_task_small,
    )

    restarted_result = restarted.run_once()

    assert restarted_result.final_status is ExecutionStatus.IDLE
    assert (workspace / "agents/size_status.md").read_text(encoding="utf-8") == "### LARGE\n"
    assert (workspace / "agents/status.md").read_text(encoding="utf-8") == "### IDLE\n"


def test_execution_plane_persists_transition_history_with_bound_parameters_and_plan_identity(tmp_path: Path) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "golden_path")
    script = write_stage_driver(tmp_path)

    plane = configure_execution_plane(
        workspace,
        config_path,
        {
            StageType.BUILDER: [sys.executable, str(script), "builder"],
            StageType.INTEGRATION: [sys.executable, str(script), "integration"],
            StageType.QA: [sys.executable, str(script), "qa-complete"],
            StageType.UPDATE: [sys.executable, str(script), "update-idle"],
        },
    )

    result = plane.run_once()

    assert result.transition_history_path is not None
    records = read_transition_history(result.transition_history_path)
    stage_records = _stage_transition_records(records)
    report = EngineControl(config_path).run_provenance(result.run_id or "")

    assert [record.node_id for record in stage_records] == ["builder", "integration", "qa", "update"]
    assert report.compile_snapshot is not None
    assert report.compile_snapshot.content.selected_mode_ref == RegistryObjectRef(
        kind=PersistedObjectKind.MODE,
        id="mode.standard",
        version="1.0.0",
    )
    assert all(record.snapshot_id == report.compile_snapshot.snapshot_id for record in stage_records)
    assert all(record.frozen_plan is not None for record in stage_records)
    assert {
        record.frozen_plan.plan_id for record in stage_records if record.frozen_plan is not None
    } == {report.compile_snapshot.frozen_plan.plan_id}
    assert stage_records[0].selected_edge_id == "execution.builder.success.integration"
    assert stage_records[0].bound_execution_parameters.model == "fixture-model"
    assert stage_records[0].bound_execution_parameters.model_profile_ref == RegistryObjectRef(
        kind=PersistedObjectKind.MODEL_PROFILE,
        id="model.default",
        version="1.0.0",
    )
    assert stage_records[0].bound_execution_parameters.runner is RunnerKind.SUBPROCESS
    assert all(record.attributes["routing_mode"] == "frozen_plan" for record in stage_records)
    assert stage_records[-1].selected_edge_id == "execution.update.success.archive"
    assert stage_records[-1].queue_mutations_applied == ("archive_task",)


def test_execution_plane_emits_run_scoped_compounding_candidates_for_supported_successes(
    tmp_path: Path,
) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "golden_path")
    script = write_stage_driver(tmp_path)

    plane = configure_execution_plane(
        workspace,
        config_path,
        {
            StageType.BUILDER: [sys.executable, str(script), "builder"],
            StageType.QA: [sys.executable, str(script), "qa-complete"],
            StageType.UPDATE: [sys.executable, str(script), "update-idle"],
        },
        integration_mode="never",
    )

    result = plane.run_once()

    assert result.run_id is not None
    candidates = _load_compounding_candidates(workspace, result.run_id)

    assert [candidate.source_stage for candidate in candidates] == [StageType.BUILDER, StageType.QA]
    assert all(candidate.scope.value == "run" for candidate in candidates)
    assert all(candidate.source_run_id == result.run_id for candidate in candidates)
    assert all(
        "agents/runs/" in ref and ref.endswith("transition_history.jsonl")
        for candidate in candidates
        for ref in candidate.evidence_refs[:1]
    )
    assert all("execution.builder.success.qa" not in candidate.summary or candidate.source_stage is StageType.BUILDER for candidate in candidates)


def test_execution_plane_emits_recovery_candidate_with_diagnostics_reference_on_resume(
    tmp_path: Path,
) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "golden_path")
    script = write_stage_driver(tmp_path)

    plane = configure_execution_plane(
        workspace,
        config_path,
        {
            StageType.BUILDER: [sys.executable, str(script), "builder"],
            StageType.QA: [sys.executable, str(script), "qa-blocked-then-pass"],
            StageType.TROUBLESHOOT: [sys.executable, str(script), "troubleshoot-complete"],
            StageType.UPDATE: [sys.executable, str(script), "update-idle"],
        },
        integration_mode="never",
    )

    result = plane.run_once()

    assert result.run_id is not None
    assert result.diagnostics_dir is not None
    candidates = _load_compounding_candidates(workspace, result.run_id)
    troubleshoot_candidates = [candidate for candidate in candidates if candidate.source_stage is StageType.TROUBLESHOOT]

    assert len(troubleshoot_candidates) == 1
    assert result.diagnostics_dir.relative_to(workspace).as_posix() in troubleshoot_candidates[0].evidence_refs
    assert "Recovery Diagnostics:" in troubleshoot_candidates[0].procedure_markdown


def test_execution_plane_skips_compounding_candidates_for_unsupported_intermediate_outcomes(
    tmp_path: Path,
) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "golden_path")
    script = write_stage_driver(tmp_path)

    plane = configure_execution_plane(
        workspace,
        config_path,
        {
            StageType.BUILDER: [sys.executable, str(script), "builder"],
            StageType.QA: [sys.executable, str(script), "qa-quickfix"],
            StageType.HOTFIX: [sys.executable, str(script), "hotfix-builder"],
            StageType.DOUBLECHECK: [sys.executable, str(script), "doublecheck-qa"],
            StageType.UPDATE: [sys.executable, str(script), "update-idle"],
        },
        integration_mode="never",
        quickfix_max_attempts=1,
    )

    result = plane.run_once()

    assert result.run_id is not None
    candidates = _load_compounding_candidates(workspace, result.run_id)

    assert [candidate.source_stage for candidate in candidates] == [
        StageType.BUILDER,
        StageType.DOUBLECHECK,
    ]
    assert all(candidate.source_stage is not StageType.QA for candidate in candidates)
    assert all(candidate.source_stage is not StageType.HOTFIX for candidate in candidates)


def test_execution_plane_records_policy_hooks_with_frozen_plan_facts(tmp_path: Path) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "golden_path")
    script = write_stage_driver(tmp_path)

    plane = configure_execution_plane(
        workspace,
        config_path,
        {
            StageType.BUILDER: [sys.executable, str(script), "builder"],
            StageType.INTEGRATION: [sys.executable, str(script), "integration"],
            StageType.QA: [sys.executable, str(script), "qa-complete"],
            StageType.UPDATE: [sys.executable, str(script), "update-idle"],
        },
    )

    result = plane.run_once()

    assert result.transition_history_path is not None
    records = read_transition_history(result.transition_history_path)
    policy_records = _policy_hook_records(records)
    report = EngineControl(config_path).run_provenance(result.run_id or "")

    assert [record.event_name for record in policy_records] == [
        "policy.hook.cycle_boundary",
        "policy.hook.cycle_boundary",
        "policy.hook.pre_stage",
        "policy.hook.post_stage",
        "policy.hook.pre_stage",
        "policy.hook.post_stage",
        "policy.hook.pre_stage",
        "policy.hook.post_stage",
        "policy.hook.pre_stage",
        "policy.hook.post_stage",
    ]
    first = policy_records[0].policy_evaluation_record()
    assert first is not None
    assert first.hook is PolicyHook.CYCLE_BOUNDARY
    assert first.facts.stage is None
    assert first.facts.plan is not None
    assert policy_records[0].node_id == POLICY_CYCLE_NODE_ID

    usage_cycle = policy_records[1].policy_evaluation_record()
    assert usage_cycle is not None
    assert usage_cycle.hook is PolicyHook.CYCLE_BOUNDARY
    assert usage_cycle.facts.stage is None

    builder_pre = policy_records[2].policy_evaluation_record()
    assert builder_pre is not None
    assert builder_pre.hook is PolicyHook.PRE_STAGE
    assert builder_pre.facts.stage is not None
    assert builder_pre.facts.stage.node_id == "builder"
    assert builder_pre.facts.plan is not None
    assert builder_pre.facts.plan.selected_execution_loop_ref is not None
    assert builder_pre.facts.plan.selected_execution_loop_ref.id == "execution.standard"

    builder_post = policy_records[3].policy_evaluation_record()
    assert builder_post is not None
    assert builder_post.hook is PolicyHook.POST_STAGE
    assert builder_post.facts.stage_result_status == "BUILDER_COMPLETE"
    assert builder_post.facts.stage_result_exit_code == 0
    assert report.policy_hooks is not None
    assert report.policy_hooks.record_count == 10
    assert report.policy_hooks.hook_counts == {
        "cycle_boundary": 2,
        "post_stage": 4,
        "pre_stage": 4,
    }
    assert report.policy_hooks.evaluator_counts == {
        "execution_integration_policy": 1,
        "execution_preflight_policy": 4,
        "execution_pacing_policy": 1,
        "execution_usage_budget": 1,
        "policy_hook_scaffold": 3,
    }
    assert report.policy_hooks.decision_counts == {"not_evaluated": 3, "pass": 7}


def test_execution_plane_integration_gate_card_metadata_can_force_integration(tmp_path: Path) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "golden_path")
    _prepend_task_metadata_lines(workspace, "**Gates:** INTEGRATION")
    script = write_stage_driver(tmp_path)

    plane = configure_execution_plane(
        workspace,
        config_path,
        {
            StageType.BUILDER: [sys.executable, str(script), "builder"],
            StageType.INTEGRATION: [sys.executable, str(script), "integration"],
            StageType.QA: [sys.executable, str(script), "qa-complete"],
            StageType.UPDATE: [sys.executable, str(script), "update-idle"],
        },
        integration_mode="never",
    )

    result = plane.run_once()

    assert result.final_status is ExecutionStatus.IDLE
    assert [stage.stage for stage in result.stage_results] == [
        StageType.BUILDER,
        StageType.INTEGRATION,
        StageType.QA,
        StageType.UPDATE,
    ]
    report = EngineControl(config_path).run_provenance(result.run_id or "")
    assert report.integration_policy is not None
    assert report.integration_policy.should_run_integration is True
    assert report.integration_policy.builder_success_target == "integration"
    assert "Task gate requires integration." in report.integration_policy.reason


def test_execution_plane_task_metadata_can_suppress_integration_without_hardcoding_loop_shape(
    tmp_path: Path,
) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "golden_path")
    _prepend_task_metadata_lines(
        workspace,
        "**Gates:** INTEGRATION",
        "**Integration:** skip",
    )
    script = write_stage_driver(tmp_path)

    plane = configure_execution_plane(
        workspace,
        config_path,
        {
            StageType.BUILDER: [sys.executable, str(script), "builder"],
            StageType.QA: [sys.executable, str(script), "qa-complete"],
            StageType.UPDATE: [sys.executable, str(script), "update-idle"],
        },
        integration_mode="always",
    )
    plane.config.routing.builder_success_sequence_with_integration = (StageType.QA,)

    result = plane.run_once()

    assert result.final_status is ExecutionStatus.IDLE
    assert [stage.stage for stage in result.stage_results] == [
        StageType.BUILDER,
        StageType.QA,
        StageType.UPDATE,
    ]
    report = EngineControl(config_path).run_provenance(result.run_id or "")
    assert report.integration_policy is not None
    assert report.integration_policy.should_run_integration is False
    assert report.integration_policy.builder_success_target == "qa"
    assert report.integration_policy.task_integration_preference == "skip"
    assert "Task integration override suppresses integration." in report.integration_policy.reason


def test_execution_plane_reused_run_id_rewrites_transition_history_instead_of_appending_stale_records(
    tmp_path: Path,
) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "golden_path")
    script = write_stage_driver(tmp_path)

    plane = configure_execution_plane(
        workspace,
        config_path,
        {
            StageType.BUILDER: [sys.executable, str(script), "builder"],
            StageType.INTEGRATION: [sys.executable, str(script), "integration"],
            StageType.QA: [sys.executable, str(script), "qa-complete"],
            StageType.UPDATE: [sys.executable, str(script), "update-idle"],
        },
    )
    plane._new_run_id = lambda task, label: "fixed-run"

    first = plane.run_once()
    second = plane.run_once()

    assert first.transition_history_path == second.transition_history_path
    assert second.transition_history_path is not None
    records = read_transition_history(second.transition_history_path)
    stage_records = _stage_transition_records(records)
    assert [record.node_id for record in stage_records] == ["update"]
    assert [record.event_id for record in stage_records] == ["fixed-run-transition-0001"]


def test_execution_plane_backlog_empty_transition_history_does_not_reuse_prior_frozen_provenance(
    tmp_path: Path,
) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "golden_path")
    script = write_stage_driver(tmp_path)

    plane = configure_execution_plane(
        workspace,
        config_path,
        {
            StageType.BUILDER: [sys.executable, str(script), "builder"],
            StageType.INTEGRATION: [sys.executable, str(script), "integration"],
            StageType.QA: [sys.executable, str(script), "qa-complete"],
            StageType.UPDATE: [sys.executable, str(script), "update-idle"],
        },
    )

    first = plane.run_once()
    second = plane.run_once()

    assert first.archived_task is not None
    assert second.update_only is True
    assert second.transition_history_path is not None
    records = read_transition_history(second.transition_history_path)
    stage_records = _stage_transition_records(records)
    assert [record.node_id for record in stage_records] == ["update"]
    assert stage_records[0].attributes["routing_mode"] == "fixed_v1_backlog_empty"
    assert stage_records[0].snapshot_id is None
    assert stage_records[0].frozen_plan is None


def test_execution_plane_large_latch_selects_mode_surface_and_records_large_route(
    tmp_path: Path,
) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "golden_path")
    script = write_stage_driver(tmp_path)
    _prepend_task_metadata_lines(
        workspace,
        "- **Files to touch:**",
        "  - `millrace.toml`",
        "- **Complexity:** INVOLVED",
    )

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

    def _task_large(config) -> None:
        config.sizing.mode = "task"
        config.sizing.task.file_count_threshold = 1
        config.sizing.task.nonempty_line_count_threshold = 999_999_999

    plane = configure_execution_plane(
        workspace,
        config_path,
        {
            StageType.LARGE_PLAN: [sys.executable, str(script), "large-plan"],
            StageType.LARGE_EXECUTE: [sys.executable, str(script), "large-execute"],
            StageType.REASSESS: [sys.executable, str(script), "reassess"],
            StageType.REFACTOR: [sys.executable, str(script), "refactor"],
            StageType.UPDATE: [sys.executable, str(script), "update-idle"],
        },
        mutate_config=_task_large,
    )

    result = plane.run_once()

    assert result.final_status is ExecutionStatus.IDLE
    assert [stage.metadata["node_id"] for stage in result.stage_results] == [
        "large_plan",
        "large_execute",
        "reassess",
        "refactor",
        "update",
    ]
    assert result.transition_history_path is not None
    records = read_transition_history(result.transition_history_path)
    stage_records = _stage_transition_records(records)
    assert [record.node_id for record in stage_records] == [
        "large_plan",
        "large_execute",
        "reassess",
        "refactor",
        "update",
    ]
    report = EngineControl(config_path).run_provenance(result.run_id or "")
    assert report.compile_snapshot is not None
    assert report.compile_snapshot.content.selected_mode_ref == RegistryObjectRef(
        kind=PersistedObjectKind.MODE,
        id="mode.large",
        version="1.0.0",
    )
    assert report.compile_snapshot.content.selected_execution_loop_ref == RegistryObjectRef(
        kind=PersistedObjectKind.LOOP_CONFIG,
        id="execution.large_direct_update",
        version="1.0.0",
    )
    assert report.selection is not None
    assert report.selection_explanation is not None
    assert report.selection_explanation.selected_size == "LARGE"
    assert report.selection_explanation.large_profile_decision == "alternate_large_profile"
    assert {binding.node_id for binding in report.selection.stage_bindings} >= {
        "large_plan",
        "large_execute",
        "reassess",
        "refactor",
        "update",
    }


def test_execution_plane_blocked_small_task_adaptively_upscopes_once_to_large(
    tmp_path: Path,
) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "golden_path")
    script = write_stage_driver(tmp_path)

    plane = configure_execution_plane(
        workspace,
        config_path,
        {
            StageType.LARGE_PLAN: [sys.executable, str(script), "large-plan"],
            StageType.LARGE_EXECUTE: [sys.executable, str(script), "large-execute"],
            StageType.REASSESS: [sys.executable, str(script), "reassess"],
            StageType.REFACTOR: [sys.executable, str(script), "refactor"],
            StageType.QA: [sys.executable, str(script), "qa-complete"],
            StageType.UPDATE: [sys.executable, str(script), "update-idle"],
        },
    )
    promoted = plane.queue.promote_next()
    assert promoted is not None
    (workspace / "agents/status.md").write_text("### BLOCKED\n", encoding="utf-8")

    result = plane.run_once()

    assert result.final_status is ExecutionStatus.IDLE
    assert result.archived_task is not None
    assert "- **Adaptive Upscope:** LARGE" in result.archived_task.raw_markdown
    assert "- **Adaptive Upscope Rule:** blocked_small_non_usage_v1" in result.archived_task.raw_markdown
    assert [stage.metadata["node_id"] for stage in result.stage_results] == [
        "large_plan",
        "large_execute",
        "reassess",
        "refactor",
        "qa",
        "update",
    ]

    report = EngineControl(config_path).run_provenance(result.run_id or "")
    assert report.compile_snapshot is not None
    assert report.compile_snapshot.content.selected_mode_ref == RegistryObjectRef(
        kind=PersistedObjectKind.MODE,
        id="mode.large",
        version="1.0.0",
    )
    assert report.selection_explanation is not None
    assert report.selection_explanation.selected_size == "LARGE"
    assert report.current_preview_explanation is not None
    assert report.current_preview_explanation.route_decision == "retained_large_latch"


def test_execution_plane_large_blocked_terminal_keeps_active_task_and_legal_status(
    tmp_path: Path,
) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "golden_path")
    script = write_stage_driver(tmp_path)
    _prepend_task_metadata_lines(
        workspace,
        "- **Files to touch:**",
        "  - `millrace.toml`",
        "- **Complexity:** INVOLVED",
    )

    def _task_large(config) -> None:
        config.sizing.mode = "task"
        config.sizing.task.file_count_threshold = 1
        config.sizing.task.nonempty_line_count_threshold = 999_999_999

    plane = configure_execution_plane(
        workspace,
        config_path,
        {
            StageType.LARGE_PLAN: [sys.executable, str(script), "large-plan-blocked"],
        },
        mutate_config=_task_large,
    )

    result = plane.run_once()

    assert result.final_status is ExecutionStatus.BLOCKED
    assert result.archived_task is None
    assert result.quarantined_task is None
    assert parse_task_cards((workspace / "agents/tasks.md").read_text(encoding="utf-8"))[0].title == "Ship the happy path"
    assert result.transition_history_path is not None
    record = _stage_transition_records(read_transition_history(result.transition_history_path))[0]
    assert record.node_id == "large_plan"
    assert record.selected_edge_id == "execution.large.plan.blocked"
    assert record.active_task_after == "2026-03-19__ship-the-happy-path"


def test_execution_plane_large_preflight_net_wait_keeps_active_task_and_records_large_node(
    tmp_path: Path,
) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "golden_path")
    script = write_stage_driver(tmp_path)
    _prepend_task_metadata_lines(
        workspace,
        "- **Files to touch:**",
        "  - `millrace.toml`",
        "- **Complexity:** INVOLVED",
    )

    def _task_large(config) -> None:
        config.sizing.mode = "task"
        config.sizing.task.file_count_threshold = 1
        config.sizing.task.nonempty_line_count_threshold = 999_999_999

    plane = configure_execution_plane(
        workspace,
        config_path,
        {
            StageType.LARGE_PLAN: [sys.executable, str(script), "large-plan"],
        },
        mutate_config=_task_large,
        transport_probe=StaticTransportProbe(
            TransportProbeResult(
                readiness=TransportReadiness.NET_WAIT,
                summary="transport=net_wait",
            )
        ),
    )

    result = plane.run_once()

    assert result.final_status is ExecutionStatus.NET_WAIT
    assert parse_task_cards((workspace / "agents/tasks.md").read_text(encoding="utf-8"))[0].title == "Ship the happy path"
    assert result.transition_history_path is not None
    record = _stage_transition_records(read_transition_history(result.transition_history_path))[0]
    assert record.node_id == "large_plan"
    assert record.selected_edge_id == "execution.policy_preflight.net_wait"
    assert record.active_task_after == "2026-03-19__ship-the-happy-path"


def test_control_run_provenance_reads_compile_snapshot_and_runtime_history(tmp_path: Path) -> None:
    context = load_provenance_fixture(tmp_path)
    run_id = "provenance-control-run"

    compile_result = compile_mode_provenance(context, run_id=run_id, mode_id="mode.default_autonomous")

    assert compile_result.status is CompileStatus.OK
    assert compile_result.snapshot is not None

    append_stage_transition_record(
        context,
        run_id=run_id,
        snapshot=compile_result.snapshot,
        attributes={"routing_mode": "fixed_v1_fallback"},
    )

    report = EngineControl(context.config_path).run_provenance(run_id)

    assert report.run_id == run_id
    assert report.selection is not None
    assert report.selection.scope == "frozen_run"
    assert report.selection.run_id == run_id
    assert report.selection.mode is not None
    assert report.selection.mode.ref.id == "mode.default_autonomous"
    assert report.routing_modes == ("fixed_v1_fallback",)
    assert report.compile_snapshot is not None
    assert report.snapshot_path == context.paths.runs_dir / run_id / "resolved_snapshot.json"
    assert report.transition_history_path == context.paths.runs_dir / run_id / "transition_history.jsonl"
    assert len(report.runtime_history) == 1
    assert report.runtime_history[0].snapshot_id == report.compile_snapshot.snapshot_id
    assert report.runtime_history[0].frozen_plan is not None
    assert report.runtime_history[0].frozen_plan.plan_id == report.compile_snapshot.frozen_plan.plan_id


def test_control_run_provenance_rejects_runtime_history_snapshot_mismatch(tmp_path: Path) -> None:
    context = load_provenance_fixture(tmp_path)
    run_id = "provenance-history-mismatch"

    compile_result = compile_mode_provenance(context, run_id=run_id, mode_id="mode.default_autonomous")

    assert compile_result.status is CompileStatus.OK
    assert compile_result.snapshot is not None

    history_path = context.paths.runs_dir / run_id / "transition_history.jsonl"
    record = append_stage_transition_record(context, run_id=run_id, snapshot=compile_result.snapshot)

    payload = record.model_dump(mode="json")
    payload["snapshot_id"] = "resolved-snapshot:other-run:deadbeef0000"
    history_path.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    with pytest.raises(ControlError, match="runtime history snapshot_id does not match compile snapshot"):
        EngineControl(context.config_path).run_provenance(run_id)


def test_control_run_provenance_rejects_invalid_policy_evaluation_payload(tmp_path: Path) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "golden_path")
    script = write_stage_driver(tmp_path)

    plane = configure_execution_plane(
        workspace,
        config_path,
        {
            StageType.BUILDER: [sys.executable, str(script), "builder"],
            StageType.INTEGRATION: [sys.executable, str(script), "integration"],
            StageType.QA: [sys.executable, str(script), "qa-complete"],
            StageType.UPDATE: [sys.executable, str(script), "update-idle"],
        },
    )

    result = plane.run_once()

    assert result.run_id is not None
    assert result.transition_history_path is not None
    records = read_transition_history(result.transition_history_path)
    payloads = [record.model_dump(mode="json") for record in records]
    policy_index = next(
        index for index, payload in enumerate(payloads) if payload.get("policy_evaluation") is not None
    )
    payloads[policy_index]["policy_evaluation"] = {"hook": "pre_stage"}
    result.transition_history_path.write_text(
        "\n".join(json.dumps(payload) for payload in payloads) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ControlError, match="run provenance is invalid: policy_evaluation"):
        EngineControl(config_path).run_provenance(result.run_id)


def test_bound_execution_parameters_apply_preserves_typed_registry_refs() -> None:
    compile_time = BoundExecutionParameters(
        model_profile_ref=RegistryObjectRef(
            kind=PersistedObjectKind.MODEL_PROFILE,
            id="model.default",
            version="1.0.0",
        ),
        runner=RunnerKind.SUBPROCESS,
        model="fixture-model",
    )
    override = BoundExecutionParameters.model_validate(
        {
            "model_profile_ref": {
                "kind": "model_profile",
                "id": "model.runtime_override",
                "version": "1.0.0",
            },
            "allow_search": False,
        }
    )

    applied = compile_time.apply(override)

    assert isinstance(applied.model_profile_ref, RegistryObjectRef)
    assert applied.model_profile_ref == RegistryObjectRef(
        kind=PersistedObjectKind.MODEL_PROFILE,
        id="model.runtime_override",
        version="1.0.0",
    )
    assert applied.allow_search is False
    assert applied.runner is RunnerKind.SUBPROCESS


def test_control_run_provenance_rejects_selection_view_mismatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    context = load_provenance_fixture(tmp_path)
    run_id = "provenance-selection-mismatch"

    compile_result = compile_mode_provenance(context, run_id=run_id, mode_id="mode.default_autonomous")

    assert compile_result.status is CompileStatus.OK
    assert compile_result.snapshot is not None

    selection = runtime_selection_view_from_snapshot(compile_result.snapshot, workspace_root=context.workspace)
    selection_payload = selection.model_dump(mode="python")
    selection_payload["frozen_plan_id"] = "frozen-plan:wrong"
    mismatched_selection = selection.__class__.model_validate(selection_payload)
    monkeypatch.setattr(
        "millrace_engine.control.runtime_selection_view_from_snapshot",
        lambda snapshot, *, workspace_root: mismatched_selection,
    )

    with pytest.raises(ControlError, match="selection view frozen_plan_id does not match compile snapshot"):
        EngineControl(context.config_path).run_provenance(run_id)


def test_control_run_provenance_rejects_selection_research_participation_mismatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = load_provenance_fixture(tmp_path)
    run_id = "provenance-selection-research-mismatch"

    compile_result = compile_mode_provenance(context, run_id=run_id, mode_id="mode.default_autonomous")

    assert compile_result.status is CompileStatus.OK
    assert compile_result.snapshot is not None

    selection = runtime_selection_view_from_snapshot(compile_result.snapshot, workspace_root=context.workspace)
    selection_payload = selection.model_dump(mode="python")
    selection_payload["research_participation"] = "incorrect-but-nonempty"
    mismatched_selection = selection.__class__.model_validate(selection_payload)
    monkeypatch.setattr(
        "millrace_engine.control.runtime_selection_view_from_snapshot",
        lambda snapshot, *, workspace_root: mismatched_selection,
    )

    with pytest.raises(ControlError, match="selection view research_participation does not match compile snapshot"):
        EngineControl(context.config_path).run_provenance(run_id)


def test_control_run_provenance_reports_latest_snapshot_after_same_run_id_recompile_with_shadow(
    tmp_path: Path,
) -> None:
    context = load_provenance_fixture(tmp_path)
    run_id = "provenance-rerun-shadow"

    first_result = compile_mode_provenance(context, run_id=run_id, mode_id="mode.standard")

    assert first_result.status is CompileStatus.OK
    assert first_result.snapshot is not None

    packaged_mode = persist_packaged_shadow(
        context.workspace,
        kind="mode",
        object_id="mode.standard",
        title="Workspace standard rerun shadow",
        aliases=("workspace-standard-rerun",),
    )

    second_result = compile_mode_provenance(context, run_id=run_id, mode_id="mode.standard")

    assert second_result.status is CompileStatus.OK
    assert second_result.snapshot is not None
    assert second_result.snapshot.snapshot_id != first_result.snapshot.snapshot_id

    report = EngineControl(context.config_path).run_provenance(run_id)

    assert report.compile_snapshot is not None
    assert report.compile_snapshot.snapshot_id == second_result.snapshot.snapshot_id
    assert report.selection is not None
    assert report.selection.scope == "frozen_run"
    assert report.selection.mode is not None
    assert report.selection.mode.title == "Workspace standard rerun shadow"
    assert report.selection.mode.registry_layer == "workspace"
    assert report.selection.mode.source_kind == "workspace_defined"
    assert report.selection.mode.title != packaged_mode.title


def test_control_run_provenance_distinguishes_frozen_history_from_current_preview_under_overlay_churn(
    tmp_path: Path,
) -> None:
    context = load_provenance_fixture(tmp_path)
    run_id = "provenance-preview-contrast"

    prompt_file = prompt_path(context.workspace)
    prompt_file.unlink()

    compile_result = compile_standard_provenance(context, run_id=run_id)

    assert compile_result.status is CompileStatus.OK
    assert compile_result.snapshot is not None

    packaged_mode = packaged_definition(context.workspace, kind="mode", object_id="mode.standard")
    prompt_file.write_text("Restored workspace prompt\n", encoding="utf-8")
    persist_packaged_shadow(
        context.workspace,
        kind="mode",
        object_id="mode.standard",
        title="Workspace preview shadow",
        aliases=("workspace-preview-shadow",),
    )

    report = EngineControl(context.config_path).run_provenance(run_id)

    assert report.selection is not None
    assert report.selection.scope == "frozen_run"
    assert report.selection.mode is not None
    assert report.selection.mode.registry_layer == "packaged"
    assert report.selection.mode.title == packaged_mode.title
    frozen_builder = _builder_binding(report.selection)
    assert frozen_builder.prompt_source_kind == "package"
    assert frozen_builder.prompt_resolved_ref == "package:agents/_start.md"

    assert report.current_preview is not None
    assert report.current_preview.scope == "preview"
    assert report.current_preview.run_id is None
    assert report.current_preview.mode is not None
    assert report.current_preview.mode.registry_layer == "workspace"
    assert report.current_preview.mode.title == "Workspace preview shadow"
    preview_builder = _builder_binding(report.current_preview)
    assert preview_builder.prompt_source_kind == "workspace"
    assert preview_builder.prompt_resolved_ref == "workspace:agents/_start.md"
    assert report.current_preview_error is None


def test_control_run_provenance_reports_latest_prompt_provenance_after_same_run_id_recompile(
    tmp_path: Path,
) -> None:
    context = load_provenance_fixture(tmp_path)
    run_id = "provenance-rerun-prompt"

    prompt_file = prompt_path(context.workspace)
    prompt_file.unlink()

    first_result = compile_standard_provenance(context, run_id=run_id)

    assert first_result.status is CompileStatus.OK
    assert first_result.snapshot is not None

    prompt_file.write_text("Workspace rerun prompt\n", encoding="utf-8")
    second_result = compile_standard_provenance(context, run_id=run_id)

    assert second_result.status is CompileStatus.OK
    assert second_result.snapshot is not None
    assert second_result.snapshot.snapshot_id != first_result.snapshot.snapshot_id

    report = EngineControl(context.config_path).run_provenance(run_id)

    assert report.compile_snapshot is not None
    assert report.compile_snapshot.snapshot_id == second_result.snapshot.snapshot_id
    assert report.selection is not None
    builder = _builder_binding(report.selection)
    assert builder.prompt_source_kind == "workspace"
    assert builder.prompt_resolved_ref == "workspace:agents/_start.md"
    assert builder.prompt_resolved_ref != "package:agents/_start.md"
    assert report.current_preview is not None
    assert _builder_binding(report.current_preview).prompt_resolved_ref == "workspace:agents/_start.md"


def test_control_run_provenance_uses_frozen_aliases_without_live_registry_fallback(tmp_path: Path) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "golden_path")
    loaded = load_engine_config(config_path)
    config = loaded.config
    config.paths.workspace = workspace
    config.paths.agents_dir = workspace / "agents"
    paths = build_runtime_paths(config)
    run_id = "provenance-aliasless-loop"

    stage = RegisteredStageKindDefinition.model_validate(
        {
            "id": "execution.aliasless_builder",
            "version": "1.0.0",
            "tier": "golden",
            "title": "Aliasless builder stage",
            "summary": "Workspace stage kind for provenance fallback coverage.",
            "source": {"kind": "workspace_defined"},
            "payload": {
                "kind_id": "execution.aliasless_builder",
                "contract_version": "1.0.0",
                "plane": "execution",
                "handler_ref": "millrace_engine.stages.aliasless_builder:Stage",
                "context_schema_ref": "execution.aliasless_builder.context.v1",
                "result_schema_ref": "execution.aliasless_builder.result.v1",
                "running_status": "ALIASLESS_BUILDER_RUNNING",
                "terminal_statuses": ("ALIASLESS_BUILDER_COMPLETE", "BLOCKED"),
                "success_statuses": ("ALIASLESS_BUILDER_COMPLETE",),
                "input_artifacts": (
                    {
                        "name": "task_card",
                        "kind": "task_card",
                        "required": True,
                        "multiplicity": "one",
                    },
                ),
                "output_artifacts": (
                    {
                        "name": "stage_summary",
                        "kind": "stage_summary",
                        "required_on": ("success", "ALIASLESS_BUILDER_COMPLETE"),
                        "persistence": "history",
                    },
                ),
                "idempotence_policy": "retry_safe_with_key",
                "retry_policy": {"max_attempts": 1, "backoff_seconds": 0, "exhausted_outcome": "blocked"},
                "queue_mutation_policy": "runtime_only",
                "routing_outcomes": ("success", "blocked"),
                "legal_predecessors": (),
                "legal_successors": (),
                "allowed_overrides": ("allow_search",),
            },
        }
    )
    loop = LoopConfigDefinition.model_validate(
        {
            "id": "execution.aliasless_loop",
            "version": "1.0.0",
            "tier": "golden",
            "title": "Aliasless loop",
            "summary": "Workspace loop for frozen provenance alias coverage.",
            "source": {"kind": "workspace_defined"},
            "payload": {
                "plane": "execution",
                "nodes": (
                    {
                        "node_id": "build",
                        "kind_id": stage.id,
                        "overrides": {},
                    },
                ),
                "edges": (
                    {
                        "edge_id": "build_done",
                        "from_node_id": "build",
                        "terminal_state_id": "done",
                        "on_outcomes": ("success",),
                        "kind": "terminal",
                    },
                ),
                "entry_node_id": "build",
                "terminal_states": (
                    {
                        "terminal_state_id": "done",
                        "terminal_class": "success",
                        "writes_status": "ALIASLESS_BUILDER_COMPLETE",
                        "emits_artifacts": ("stage_summary",),
                        "ends_plane_run": True,
                    },
                ),
            },
        }
    )
    persist_workspace_registry_object(workspace, stage)
    persist_workspace_registry_object(workspace, loop)

    compile_result = FrozenRunCompiler(paths).compile_loop(
        RegistryObjectRef(
            kind=PersistedObjectKind.LOOP_CONFIG,
            id=loop.id,
            version=loop.version,
        ),
        run_id=run_id,
    )

    assert compile_result.status is CompileStatus.OK

    persist_workspace_registry_object(
        workspace,
        loop.model_copy(update={"aliases": ("added-later",)}),
        overwrite=True,
    )

    report = EngineControl(config_path).run_provenance(run_id)

    assert report.selection is not None
    assert report.selection.selection.aliases == ()
    assert report.selection.execution_loop is not None
    assert report.selection.execution_loop.aliases == ()


def test_control_run_provenance_survives_current_preview_failures_without_rewriting_history(
    tmp_path: Path,
) -> None:
    context = load_provenance_fixture(tmp_path)
    run_id = "provenance-preview-failure"

    compile_result = compile_standard_provenance(context, run_id=run_id)

    assert compile_result.status is CompileStatus.OK
    assert compile_result.snapshot is not None

    context.config_path.write_text(
        context.config_path.read_text(encoding="utf-8")
        + '\n[stages.builder]\nprompt_file = "agents/not-a-real-prompt.md"\n',
        encoding="utf-8",
    )

    report = EngineControl(context.config_path).run_provenance(run_id)

    assert report.selection is not None
    assert report.selection.scope == "frozen_run"
    assert report.current_preview is None
    assert report.current_preview_error is not None
    assert "standard runtime selection preview failed:" in report.current_preview_error


def test_control_run_provenance_survives_broken_live_registry_using_frozen_source_metadata(
    tmp_path: Path,
) -> None:
    context = load_provenance_fixture(tmp_path)
    run_id = "provenance-broken-live-registry"

    compile_result = compile_standard_provenance(context, run_id=run_id)

    assert compile_result.status is CompileStatus.OK
    assert compile_result.snapshot is not None

    broken_registry_path = context.workspace / "agents" / "registry" / "modes" / "broken__1.0.0.json"
    broken_registry_path.parent.mkdir(parents=True, exist_ok=True)
    broken_registry_path.write_text("{not valid json\n", encoding="utf-8")

    report = EngineControl(context.config_path).run_provenance(run_id)

    assert report.selection is not None
    assert report.selection.scope == "frozen_run"
    assert report.selection.mode is not None
    assert report.selection.mode.ref.id == "mode.standard"
    assert report.selection.mode.registry_layer == "packaged"
    assert report.current_preview is None
    assert report.current_preview_error is not None
    assert "standard runtime selection preview failed:" in report.current_preview_error


def test_control_run_provenance_uses_compile_provenance_when_source_refs_are_missing(
    tmp_path: Path,
) -> None:
    context = load_provenance_fixture(tmp_path)
    run_id = "provenance-missing-source-refs"

    compile_result = compile_standard_provenance(context, run_id=run_id)

    assert compile_result.status is CompileStatus.OK
    assert compile_result.snapshot is not None

    snapshot_path = context.paths.runs_dir / run_id / "resolved_snapshot.json"
    snapshot_payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    snapshot_payload["content"]["source_refs"] = []
    snapshot_path.write_text(json.dumps(snapshot_payload), encoding="utf-8")

    packaged_mode = persist_packaged_shadow(
        context.workspace,
        kind="mode",
        object_id="mode.standard",
        title="Workspace shadow after old compile",
        aliases=("workspace-shadow-after-old-compile",),
    )

    report = EngineControl(context.config_path).run_provenance(run_id)

    assert report.selection is not None
    assert report.selection.mode is not None
    assert report.selection.mode.title == packaged_mode.title
    assert report.selection.mode.aliases == ()
    assert report.selection.mode.registry_layer == "packaged"
    assert report.selection.mode.source_kind.value == "packaged_default"
    assert report.selection.mode.source_ref == "registry/modes/mode.standard__1.0.0.json"
    assert report.current_preview is not None
    assert report.current_preview.mode is not None
    assert report.current_preview.mode.title == "Workspace shadow after old compile"
    assert report.current_preview.mode.registry_layer == "workspace"


def test_execution_plane_backlog_empty_runs_update_once_when_enabled(tmp_path: Path) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "backlog_empty")
    script = write_stage_driver(tmp_path)
    emitted: list[tuple[EventType, dict[str, object]]] = []

    plane = configure_execution_plane(
        workspace,
        config_path,
        {
            StageType.UPDATE: [sys.executable, str(script), "update-idle"],
        },
        emitted=emitted,
    )

    result = plane.run_once()

    assert result.update_only is True
    assert result.final_status is ExecutionStatus.IDLE
    assert [stage.stage for stage in result.stage_results] == [StageType.UPDATE]
    assert result.stage_results[0].status == "UPDATE_COMPLETE"
    assert parse_task_cards((workspace / "agents/tasksarchive.md").read_text(encoding="utf-8")) == []
    assert (workspace / "agents/status.md").read_text(encoding="utf-8") == "### IDLE\n"
    assert emitted[0][0] is EventType.BACKLOG_EMPTY
    assert emitted[0][1]["run_update_on_empty"] is True
    assert result.transition_history_path is not None
    records = read_transition_history(result.transition_history_path)
    assert len(records) == 1
    assert records[0].attributes["routing_mode"] == "fixed_v1_backlog_empty"


def test_execution_plane_does_not_synthesize_qa_success_without_marker(tmp_path: Path) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "golden_path")
    script = write_stage_driver(tmp_path)

    plane = configure_execution_plane(
        workspace,
        config_path,
        {
            StageType.BUILDER: [sys.executable, str(script), "builder"],
            StageType.INTEGRATION: [sys.executable, str(script), "integration"],
            StageType.QA: [sys.executable, str(script), "qa-missing"],
            StageType.UPDATE: [sys.executable, str(script), "update-idle"],
        },
    )

    try:
        plane.run_once()
    except Exception as exc:  # noqa: BLE001 - specific type is internal to the stage layer
        assert "qa exited 0 without a legal terminal marker" in str(exc).lower()
    else:
        raise AssertionError("QA missing-marker path should fail rather than synthesize success")


def test_execution_stage_loads_prompt_asset_contents_into_runner_prompt(tmp_path: Path) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "golden_path")
    script = write_stage_driver(tmp_path)
    loaded = load_engine_config(config_path)
    config = loaded.config
    config.paths.workspace = workspace
    config.paths.agents_dir = workspace / "agents"
    config.execution.integration_mode = "never"
    config.stages[StageType.BUILDER].runner = RunnerKind.SUBPROCESS
    config.stages[StageType.BUILDER].model = "fixture-model"
    config.stages[StageType.BUILDER].timeout_seconds = 30
    prompt_path = workspace / "agents" / "builder_prompt_fixture.md"
    prompt_path.write_text("# Prompt\n\nBuilder prompt fixture text\n", encoding="utf-8")
    config.stages[StageType.BUILDER].prompt_file = prompt_path

    plane = ExecutionPlane(
        config,
        build_runtime_paths(config),
        stage_commands={
            StageType.BUILDER: [sys.executable, str(script), "builder-check-prompt"],
        },
    )
    task = parse_task_cards((workspace / "agents/tasksbacklog.md").read_text(encoding="utf-8"))[0]

    result = plane._run_stage(StageType.BUILDER, task, "prompt-fixture-run")

    assert result.status == "BUILDER_COMPLETE"
    assert "procedure_injection" not in result.metadata


def test_execution_stage_injects_workspace_scoped_procedures_with_stage_rules_and_budget(
    tmp_path: Path,
) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "golden_path")
    script = write_stage_driver(tmp_path)
    loaded = load_engine_config(config_path)
    config = loaded.config
    config.paths.workspace = workspace
    config.paths.agents_dir = workspace / "agents"
    config.execution.integration_mode = "never"
    config.stages[StageType.BUILDER].runner = RunnerKind.SUBPROCESS
    config.stages[StageType.BUILDER].model = "fixture-model"
    config.stages[StageType.BUILDER].timeout_seconds = 30

    _write_compounding_procedure(
        workspace,
        filename="workspace-builder.json",
        procedure_id="proc.workspace.builder.selected",
        scope=ProcedureScope.WORKSPACE,
        source_stage=StageType.BUILDER,
        title="Workspace Builder Procedure",
        summary="Apply the known builder fix sequence before continuing.",
        procedure_markdown="# Workspace Builder Procedure\n\n" + ("Step: keep the working tree coherent.\n" * 180),
    )
    _write_compounding_procedure(
        workspace,
        filename="workspace-qa-ignored.json",
        procedure_id="proc.workspace.qa.ignored",
        scope=ProcedureScope.WORKSPACE,
        source_stage=StageType.QA,
        title="Workspace QA Procedure",
        summary="This should not be injected into builder.",
        procedure_markdown="Do not use this in builder.",
    )

    plane = ExecutionPlane(
        config,
        build_runtime_paths(config),
        stage_commands={
            StageType.BUILDER: [sys.executable, str(script), "builder-check-compounding-prompt"],
        },
    )
    task = parse_task_cards((workspace / "agents/tasksbacklog.md").read_text(encoding="utf-8"))[0]

    result = plane._run_stage(StageType.BUILDER, task, "builder-compounding-run")

    assert result.status == "BUILDER_COMPLETE"
    injection = result.metadata["procedure_injection"]
    assert injection["stage"] == "builder"
    assert injection["candidate_count"] == 1
    assert injection["selected_count"] == 1
    assert injection["truncated_count"] == 1
    assert injection["considered_procedures"][0]["procedure_id"] == "proc.workspace.builder.selected"
    assert injection["procedures"][0]["procedure_id"] == "proc.workspace.builder.selected"
    assert injection["procedures"][0]["source_stage"] == "builder"
    assert injection["procedures"][0]["scope"] == "workspace"


def test_execution_plane_injects_run_scoped_builder_candidate_into_later_qa_stage(tmp_path: Path) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "golden_path")
    script = write_stage_driver(tmp_path)

    plane = configure_execution_plane(
        workspace,
        config_path,
        {
            StageType.BUILDER: [sys.executable, str(script), "builder"],
            StageType.QA: [sys.executable, str(script), "qa-check-compounding-prompt"],
            StageType.UPDATE: [sys.executable, str(script), "update-idle"],
        },
        integration_mode="never",
    )

    result = plane.run_once()

    assert result.final_status is ExecutionStatus.IDLE
    qa_result = next(stage for stage in result.stage_results if stage.stage is StageType.QA)
    injection = qa_result.metadata["procedure_injection"]
    assert injection["stage"] == "qa"
    assert injection["candidate_count"] >= 1
    assert injection["selected_count"] >= 1
    assert injection["considered_procedures"][0]["source_stage"] == "builder"
    assert injection["procedures"][0]["source_stage"] == "builder"
    assert injection["procedures"][0]["scope"] == "run"


def test_control_run_provenance_reports_compounding_creation_and_selection_details(
    tmp_path: Path,
) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "golden_path")
    script = write_stage_driver(tmp_path)

    _write_compounding_procedure(
        workspace,
        filename="workspace-builder.json",
        procedure_id="proc.workspace.builder.selected",
        scope=ProcedureScope.WORKSPACE,
        source_stage=StageType.BUILDER,
        title="Workspace Builder Procedure",
        summary="Apply the known builder fix sequence before continuing.",
        procedure_markdown="# Workspace Builder Procedure\n\n" + ("Keep the working tree coherent.\n" * 180),
    )

    plane = configure_execution_plane(
        workspace,
        config_path,
        {
            StageType.BUILDER: [sys.executable, str(script), "builder"],
            StageType.QA: [sys.executable, str(script), "qa-check-compounding-prompt"],
            StageType.UPDATE: [sys.executable, str(script), "update-idle"],
        },
        integration_mode="never",
    )

    result = plane.run_once()

    assert result.run_id is not None
    report = EngineControl(config_path).run_provenance(result.run_id)

    assert report.compounding is not None
    assert [procedure.source_stage for procedure in report.compounding.created_procedures] == ["builder", "qa"]
    assert report.compounding.procedure_selections
    builder_selection = next(
        selection for selection in report.compounding.procedure_selections if selection.stage == "builder"
    )
    qa_selection = next(
        selection for selection in report.compounding.procedure_selections if selection.stage == "qa"
    )
    assert builder_selection.considered_count == 1
    assert builder_selection.injected_count == 1
    assert [procedure.procedure_id for procedure in builder_selection.considered_procedures] == [
        "proc.workspace.builder.selected"
    ]
    assert [procedure.procedure_id for procedure in builder_selection.injected_procedures] == [
        "proc.workspace.builder.selected"
    ]
    assert qa_selection.considered_count >= 1
    assert qa_selection.injected_count >= 1
    assert qa_selection.considered_procedures[0].scope.value == "run"
    assert qa_selection.injected_procedures[0].scope.value == "run"


def test_execution_stage_fails_deterministically_when_prompt_asset_is_missing(tmp_path: Path) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "golden_path")
    script = write_stage_driver(tmp_path)
    emitted: list[tuple[EventType, dict[str, object]]] = []
    loaded = load_engine_config(config_path)
    config = loaded.config
    config.paths.workspace = workspace
    config.paths.agents_dir = workspace / "agents"
    config.execution.integration_mode = "never"
    config.stages[StageType.BUILDER].runner = RunnerKind.SUBPROCESS
    config.stages[StageType.BUILDER].model = "fixture-model"
    config.stages[StageType.BUILDER].timeout_seconds = 30
    config.stages[StageType.BUILDER].prompt_file = workspace / "agents" / "missing_builder_prompt.md"

    plane = ExecutionPlane(
        config,
        build_runtime_paths(config),
        stage_commands={
            StageType.BUILDER: [sys.executable, str(script), "builder"],
        },
        emit_event=lambda event_type, payload: emitted.append((event_type, dict(payload))),
    )
    task = parse_task_cards((workspace / "agents/tasksbacklog.md").read_text(encoding="utf-8"))[0]

    with pytest.raises(StageExecutionError, match="prompt asset is missing"):
        plane._run_stage(StageType.BUILDER, task, "missing-prompt-run")
    assert [event_type for event_type, _ in emitted] == [
        EventType.STAGE_STARTED,
        EventType.STATUS_CHANGED,
        EventType.STAGE_FAILED,
    ]


def test_execution_plane_uses_packaged_prompt_fallback_when_workspace_prompt_is_missing(tmp_path: Path) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "golden_path")
    script = write_stage_driver(tmp_path)
    emitted: list[tuple[EventType, dict[str, object]]] = []
    prompt_path = workspace / "agents" / "_start.md"
    prompt_path.unlink()

    loaded = load_engine_config(config_path)
    config = loaded.config
    config.paths.workspace = workspace
    config.paths.agents_dir = workspace / "agents"
    config.execution.integration_mode = "never"
    config.stages[StageType.BUILDER].runner = RunnerKind.SUBPROCESS
    config.stages[StageType.BUILDER].model = "fixture-model"
    config.stages[StageType.BUILDER].timeout_seconds = 30
    config.stages[StageType.BUILDER].prompt_file = prompt_path

    plane = ExecutionPlane(
        config,
        build_runtime_paths(config),
        stage_commands={
            StageType.BUILDER: [sys.executable, str(script), "builder"],
        },
        emit_event=lambda event_type, payload: emitted.append((event_type, dict(payload))),
    )
    task = parse_task_cards((workspace / "agents/tasksbacklog.md").read_text(encoding="utf-8"))[0]

    result = plane._run_stage(StageType.BUILDER, task, "packaged-fallback-run")

    assert result.status == ExecutionStatus.BUILDER_COMPLETE.value
    assert result.metadata["asset_resolution"]["source_kind"] == "package"
    assert result.metadata["asset_resolution"]["resolved_ref"] == "package:agents/_start.md"
    assert [event_type for event_type, _ in emitted] == [
        EventType.STAGE_STARTED,
        EventType.STATUS_CHANGED,
        EventType.STATUS_CHANGED,
        EventType.STAGE_COMPLETED,
    ]


def test_execution_plane_recovers_via_quickfix_loop_and_archives_task(tmp_path: Path) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "quickfix_recovery")
    script = write_stage_driver(tmp_path)
    emitted: list[tuple[EventType, dict[str, object]]] = []

    plane = configure_execution_plane(
        workspace,
        config_path,
        {
            StageType.BUILDER: [sys.executable, str(script), "builder"],
            StageType.INTEGRATION: [sys.executable, str(script), "integration"],
            StageType.QA: [sys.executable, str(script), "qa-quickfix"],
            StageType.HOTFIX: [sys.executable, str(script), "hotfix-builder"],
            StageType.DOUBLECHECK: [sys.executable, str(script), "doublecheck-qa"],
            StageType.UPDATE: [sys.executable, str(script), "update-idle"],
        },
        emitted=emitted,
    )

    result = plane.run_once()

    assert result.final_status is ExecutionStatus.IDLE
    assert result.quickfix_attempts == 1
    assert [stage.stage for stage in result.stage_results] == [
        StageType.BUILDER,
        StageType.INTEGRATION,
        StageType.QA,
        StageType.HOTFIX,
        StageType.DOUBLECHECK,
        StageType.UPDATE,
    ]
    assert result.archived_task is not None
    assert result.archived_task.title == "Ship the happy path"
    assert parse_task_cards((workspace / "agents/tasks.md").read_text(encoding="utf-8")) == []
    archive_cards = parse_task_cards((workspace / "agents/tasksarchive.md").read_text(encoding="utf-8"))
    assert [card.title for card in archive_cards] == ["Ship the happy path"]
    assert (workspace / "agents/status.md").read_text(encoding="utf-8") == "### IDLE\n"
    event_types = [event_type for event_type, _ in emitted]
    assert EventType.STAGE_STARTED in event_types
    assert EventType.STAGE_COMPLETED in event_types
    assert EventType.STAGE_FAILED in event_types
    assert EventType.STATUS_CHANGED in event_types
    assert EventType.QUICKFIX_ATTEMPT in event_types
    quickfix_failure = next(
        payload
        for event_type, payload in emitted
        if event_type is EventType.STAGE_FAILED and payload.get("stage") == StageType.QA.value
    )
    assert quickfix_failure["status"] == ExecutionStatus.QUICKFIX_NEEDED.value
    status_change = next(
        payload
        for event_type, payload in emitted
        if event_type is EventType.STATUS_CHANGED and payload.get("status") == ExecutionStatus.QUICKFIX_NEEDED.value
    )
    assert status_change["previous_status"] == ExecutionStatus.QA_RUNNING.value
    quickfix_attempt = next(payload for event_type, payload in emitted if event_type is EventType.QUICKFIX_ATTEMPT)
    assert quickfix_attempt["attempt"] == 1
    assert quickfix_attempt["max_attempts"] == 2


def test_execution_plane_quickfix_loop_clears_active_artifact_after_success(tmp_path: Path) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "quickfix_recovery")
    quickfix_path = seed_active_quickfix_artifact(workspace)
    script = write_stage_driver(tmp_path)

    plane = configure_execution_plane(
        workspace,
        config_path,
        {
            StageType.BUILDER: [sys.executable, str(script), "builder"],
            StageType.INTEGRATION: [sys.executable, str(script), "integration"],
            StageType.QA: [sys.executable, str(script), "qa-quickfix"],
            StageType.HOTFIX: [sys.executable, str(script), "hotfix-builder"],
            StageType.DOUBLECHECK: [sys.executable, str(script), "doublecheck-qa"],
            StageType.UPDATE: [sys.executable, str(script), "update-idle"],
        },
    )

    result = plane.run_once()

    assert result.final_status is ExecutionStatus.IDLE
    assert result.archived_task is not None
    assert quickfix_path.read_text(encoding="utf-8") == "# Quickfix\n"


def test_execution_plane_unresolved_quickfix_preserves_active_artifact(tmp_path: Path) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "quickfix_recovery")
    quickfix_path = seed_active_quickfix_artifact(workspace)
    original_text = quickfix_path.read_text(encoding="utf-8")
    script = write_stage_driver(tmp_path)

    plane = configure_execution_plane(
        workspace,
        config_path,
        {
            StageType.BUILDER: [sys.executable, str(script), "builder"],
            StageType.INTEGRATION: [sys.executable, str(script), "integration"],
            StageType.QA: [sys.executable, str(script), "qa-quickfix"],
            StageType.HOTFIX: [sys.executable, str(script), "qa-blocked"],
            StageType.TROUBLESHOOT: [sys.executable, str(script), "troubleshoot-blocked"],
            StageType.CONSULT: [sys.executable, str(script), "consult-needs-research"],
        },
    )

    result = plane.run_once()

    assert result.archived_task is None
    assert result.quarantined_task is not None
    assert quickfix_path.read_text(encoding="utf-8") == original_text


def test_promotion_event_precedes_stage_start_and_archive(tmp_path: Path) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "golden_path")
    append_subprocess_stage_config(config_path)
    config_path.write_text(
        config_path.read_text(encoding="utf-8").replace('integration_mode = "large_only"', 'integration_mode = "always"', 1),
        encoding="utf-8",
    )
    script = write_stage_driver(tmp_path)
    task = parse_task_cards((workspace / "agents/tasksbacklog.md").read_text(encoding="utf-8"))[0]
    engine = MillraceEngine(
        config_path,
        stage_commands={
            StageType.BUILDER: [sys.executable, str(script), "builder"],
            StageType.INTEGRATION: [sys.executable, str(script), "integration"],
            StageType.QA: [sys.executable, str(script), "qa-complete"],
            StageType.UPDATE: [sys.executable, str(script), "update-idle"],
        },
    )

    engine.start(once=True)

    events = [
        json.loads(line)
        for line in (workspace / "agents" / "engine_events.log").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    event_types = [event["type"] for event in events]

    promoted_index = next(
        index
        for index, event in enumerate(events)
        if event["type"] == EventType.TASK_PROMOTED.value and event["payload"].get("task_id") == task.task_id
    )
    first_stage_started_index = next(
        index
        for index, event in enumerate(events)
        if event["type"] == EventType.STAGE_STARTED.value and event["payload"].get("task_id") == task.task_id
    )
    archived_index = next(
        index
        for index, event in enumerate(events)
        if event["type"] == EventType.TASK_ARCHIVED.value and event["payload"].get("task_id") == task.task_id
    )

    assert promoted_index < first_stage_started_index < archived_index
    assert event_types.count(EventType.TASK_PROMOTED.value) == 1


def test_execution_plane_routing_override_can_skip_integration_via_config(tmp_path: Path) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "golden_path")
    script = write_stage_driver(tmp_path)

    plane = configure_execution_plane(
        workspace,
        config_path,
        {
            StageType.BUILDER: [sys.executable, str(script), "builder"],
            StageType.QA: [sys.executable, str(script), "qa-complete"],
            StageType.UPDATE: [sys.executable, str(script), "update-idle"],
        },
        integration_mode="always",
    )
    plane.config.routing.builder_success_sequence_with_integration = (StageType.QA,)

    result = plane.run_once()

    assert result.final_status is ExecutionStatus.IDLE
    assert [stage.stage for stage in result.stage_results] == [
        StageType.BUILDER,
        StageType.QA,
        StageType.UPDATE,
    ]


def test_execution_plane_exhausted_quickfix_routes_through_troubleshoot_then_recovers(tmp_path: Path) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "quickfix_exhausted")
    script = write_stage_driver(tmp_path)
    emitted: list[tuple[EventType, dict[str, object]]] = []

    plane = configure_execution_plane(
        workspace,
        config_path,
        {
            StageType.BUILDER: [sys.executable, str(script), "builder"],
            StageType.INTEGRATION: [sys.executable, str(script), "integration"],
            StageType.QA: [sys.executable, str(script), "qa-quickfix-then-pass"],
            StageType.HOTFIX: [sys.executable, str(script), "hotfix-builder"],
            StageType.DOUBLECHECK: [sys.executable, str(script), "doublecheck-always-quickfix"],
            StageType.TROUBLESHOOT: [sys.executable, str(script), "troubleshoot-complete"],
            StageType.UPDATE: [sys.executable, str(script), "update-idle"],
        },
        quickfix_max_attempts=2,
        emitted=emitted,
    )

    result = plane.run_once()

    assert result.final_status is ExecutionStatus.IDLE
    assert result.quickfix_attempts == 0
    assert [stage.stage for stage in result.stage_results] == [
        StageType.BUILDER,
        StageType.INTEGRATION,
        StageType.QA,
        StageType.HOTFIX,
        StageType.DOUBLECHECK,
        StageType.HOTFIX,
        StageType.DOUBLECHECK,
        StageType.TROUBLESHOOT,
        StageType.BUILDER,
        StageType.INTEGRATION,
        StageType.QA,
        StageType.UPDATE,
    ]
    assert all(stage.stage is not StageType.CONSULT for stage in result.stage_results)
    assert result.archived_task is not None
    assert result.diagnostics_dir is not None
    assert result.diagnostics_dir.exists()
    failure_summary = (result.diagnostics_dir / "failure_summary.md").read_text(encoding="utf-8")
    assert "- **Stage:** doublecheck" in failure_summary
    assert "Quickfix attempts exhausted" in failure_summary
    archive_cards = parse_task_cards((workspace / "agents/tasksarchive.md").read_text(encoding="utf-8"))
    assert [card.title for card in archive_cards] == ["Ship the happy path"]
    assert (workspace / "agents/status.md").read_text(encoding="utf-8") == "### IDLE\n"
    exhausted_payload = next(
        payload for event_type, payload in emitted if event_type is EventType.QUICKFIX_EXHAUSTED
    )
    assert exhausted_payload["attempts"] == 2


def test_execution_plane_needs_research_quarantines_active_and_backlog_cards(tmp_path: Path) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "needs_research")
    script = write_stage_driver(tmp_path)

    plane = configure_execution_plane(
        workspace,
        config_path,
        {
            StageType.BUILDER: [sys.executable, str(script), "builder"],
            StageType.INTEGRATION: [sys.executable, str(script), "integration"],
            StageType.QA: [sys.executable, str(script), "qa-blocked"],
            StageType.TROUBLESHOOT: [sys.executable, str(script), "troubleshoot-blocked"],
            StageType.CONSULT: [sys.executable, str(script), "consult-needs-research"],
        },
    )

    result = plane.run_once()

    assert result.final_status is ExecutionStatus.IDLE
    assert result.archived_task is None
    assert result.quarantined_task is not None
    assert result.quarantined_task.title == "Ship the happy path"
    assert result.diagnostics_dir is not None
    assert result.diagnostics_dir.exists()
    assert parse_task_cards((workspace / "agents/tasks.md").read_text(encoding="utf-8")) == []
    assert parse_task_cards((workspace / "agents/tasksbacklog.md").read_text(encoding="utf-8")) == []

    backburner_text = (workspace / "agents/tasksbackburner.md").read_text(encoding="utf-8")
    assert "research_recovery:freeze:start" in backburner_text
    assert "Ship the happy path" in backburner_text
    assert "Research follow-up task" in backburner_text

    blocker_text = (workspace / "agents/tasksblocker.md").read_text(encoding="utf-8")
    assert "### NEEDS_RESEARCH" in blocker_text
    assert "INC-FIXTURE-001.md" in blocker_text
    assert "status=BLOCKED" in blocker_text

    latch = load_research_recovery_latch(workspace / "agents/.runtime/research_recovery_latch.json")
    assert latch is not None
    assert latch.frozen_backlog_cards == 1
    assert latch.incident_path == Path("agents/ideas/incidents/incoming/INC-FIXTURE-001.md")
    assert latch.diag_dir == result.diagnostics_dir
    assert result.research_handoff is not None
    assert result.transition_history_path is not None
    assert result.research_handoff.parent_run is not None
    assert result.research_handoff.parent_run.run_id == result.run_id
    assert result.research_handoff.parent_run.transition_history_path == result.transition_history_path
    assert result.research_handoff.stage == "Consult"
    assert result.research_handoff.incident_path == latch.incident_path
    assert latch.handoff == result.research_handoff

    failure_summary = (result.diagnostics_dir / "failure_summary.md").read_text(encoding="utf-8")
    assert "- **Stage:** qa" in failure_summary
    assert "status=BLOCKED" in failure_summary
    assert (workspace / "agents/status.md").read_text(encoding="utf-8") == "### IDLE\n"


@pytest.mark.parametrize(
    ("readiness", "allow_search", "search_enabled", "network_guard_enabled", "network_policy", "expected_status"),
    [
        (TransportReadiness.READY, True, True, False, "allow", ExecutionStatus.IDLE),
        (TransportReadiness.ENV_BLOCKED, False, True, False, "allow", ExecutionStatus.BLOCKED),
        (TransportReadiness.NET_WAIT, False, True, False, "allow", ExecutionStatus.NET_WAIT),
        (TransportReadiness.READY, True, False, False, "allow", ExecutionStatus.BLOCKED),
    ],
)
def test_execution_plane_preflight_policy_matrix(
    tmp_path: Path,
    readiness: TransportReadiness,
    allow_search: bool,
    search_enabled: bool,
    network_guard_enabled: bool,
    network_policy: str,
    expected_status: ExecutionStatus,
) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "golden_path")
    script, env_path = write_policy_stage_driver(tmp_path)

    plane = configure_execution_plane(
        workspace,
        config_path,
        {
            StageType.BUILDER: [sys.executable, str(script), "builder-record-policy-env"],
            StageType.QA: [sys.executable, str(script), "qa-complete"],
            StageType.UPDATE: [sys.executable, str(script), "update-idle"],
        },
        integration_mode="never",
        mutate_config=lambda config: (
            setattr(config.stages[StageType.BUILDER], "allow_search", allow_search),
            setattr(config.policies.search, "execution_enabled", search_enabled),
            setattr(config.policies.network_guard, "enabled", network_guard_enabled),
            setattr(config.policies.network_guard, "execution_policy", network_policy),
        ),
        transport_probe=StaticTransportProbe(
            TransportProbeResult(
                readiness=readiness,
                summary=f"transport={readiness.value}",
            )
        ),
    )

    with warnings.catch_warnings():
        warnings.simplefilter("error", UserWarning)
        result = plane.run_once()

    assert result.final_status is expected_status
    records = read_transition_history(result.transition_history_path)
    preflight_records = [
        record.policy_evaluation_record()
        for record in records
        if record.policy_evaluator == ExecutionPreflightEvaluator.evaluator_name
    ]
    assert preflight_records
    decision = preflight_records[0].decision
    expected_decision = {
        ExecutionStatus.IDLE: "pass",
        ExecutionStatus.BLOCKED: (
            "env_blocked" if readiness is TransportReadiness.ENV_BLOCKED else "policy_blocked"
        ),
        ExecutionStatus.NET_WAIT: "net_wait",
    }[expected_status]
    assert decision.value == expected_decision

    if expected_status is ExecutionStatus.IDLE:
        assert env_path.exists()
        env_payload = json.loads(env_path.read_text(encoding="utf-8"))
        assert env_payload == {"allow_network": "1", "allow_search": "1"}
        assert [stage.stage for stage in result.stage_results] == [
            StageType.BUILDER,
            StageType.QA,
            StageType.UPDATE,
        ]
        return

    assert len(result.stage_results) == 1
    assert result.stage_results[0].status == expected_status.value
    assert not env_path.exists()
    assert (workspace / "agents/status.md").read_text(encoding="utf-8") == expected_status.marker + "\n"


def test_execution_plane_preflight_clean_room_enforces_network_env_without_blocking(tmp_path: Path) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "golden_path")
    script, env_path = write_policy_stage_driver(tmp_path)

    plane = configure_execution_plane(
        workspace,
        config_path,
        {
            StageType.BUILDER: [sys.executable, str(script), "builder-record-policy-env"],
            StageType.QA: [sys.executable, str(script), "qa-complete"],
            StageType.UPDATE: [sys.executable, str(script), "update-idle"],
        },
        integration_mode="never",
        mutate_config=lambda config: (
            setattr(config.stages[StageType.BUILDER], "allow_search", False),
            setattr(config.policies.network_guard, "enabled", True),
            setattr(config.policies.network_guard, "execution_policy", "deny"),
        ),
        transport_probe=StaticTransportProbe(
            TransportProbeResult(
                readiness=TransportReadiness.READY,
                summary="transport=ready",
            )
        ),
    )

    result = plane.run_once()

    assert result.final_status is ExecutionStatus.IDLE
    env_payload = json.loads(env_path.read_text(encoding="utf-8"))
    assert env_payload == {"allow_network": "0", "allow_search": "0"}
    records = read_transition_history(result.transition_history_path)
    preflight_records = [
        record.policy_evaluation_record()
        for record in records
        if record.policy_evaluator == ExecutionPreflightEvaluator.evaluator_name
    ]
    assert preflight_records[0].decision.value == "pass"
    latest_evidence = preflight_records[0].evidence[-1]
    assert latest_evidence.details["effective_allow_network"] is False
    assert latest_evidence.details["effective_allow_search"] is False


@pytest.mark.parametrize(
    ("blocked_readiness", "expected_blocked_status"),
    [
        (TransportReadiness.ENV_BLOCKED, ExecutionStatus.BLOCKED),
        (TransportReadiness.NET_WAIT, ExecutionStatus.NET_WAIT),
    ],
)
def test_execution_plane_preflight_blocked_status_can_restart_once_transport_recovers(
    tmp_path: Path,
    blocked_readiness: TransportReadiness,
    expected_blocked_status: ExecutionStatus,
) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "golden_path")
    script, env_path = write_policy_stage_driver(tmp_path)
    commands = {
        StageType.BUILDER: [sys.executable, str(script), "builder-record-policy-env"],
        StageType.QA: [sys.executable, str(script), "qa-complete"],
        StageType.UPDATE: [sys.executable, str(script), "update-idle"],
    }

    blocked_plane = configure_execution_plane(
        workspace,
        config_path,
        commands,
        integration_mode="never",
        mutate_config=lambda config: (
            setattr(config.stages[StageType.BUILDER], "allow_search", True),
            setattr(config.policies.search, "execution_enabled", True),
        ),
        transport_probe=StaticTransportProbe(
            TransportProbeResult(
                readiness=blocked_readiness,
                summary=f"transport={blocked_readiness.value}",
            )
        ),
    )

    blocked_result = blocked_plane.run_once()

    assert blocked_result.final_status is expected_blocked_status
    assert not env_path.exists()
    assert (workspace / "agents/status.md").read_text(encoding="utf-8") == expected_blocked_status.marker + "\n"

    resumed_plane = configure_execution_plane(
        workspace,
        config_path,
        commands,
        integration_mode="never",
        mutate_config=lambda config: (
            setattr(config.stages[StageType.BUILDER], "allow_search", True),
            setattr(config.policies.search, "execution_enabled", True),
        ),
        transport_probe=StaticTransportProbe(
            TransportProbeResult(
                readiness=TransportReadiness.READY,
                summary="transport=ready",
            )
        ),
    )

    resumed_result = resumed_plane.run_once()

    assert resumed_result.final_status is ExecutionStatus.IDLE
    assert json.loads(env_path.read_text(encoding="utf-8")) == {
        "allow_network": "1",
        "allow_search": "1",
    }
    assert [stage.stage for stage in resumed_result.stage_results] == [
        StageType.BUILDER,
        StageType.QA,
        StageType.UPDATE,
    ]
    assert resumed_result.archived_task is not None
    assert "Adaptive Upscope" not in resumed_result.archived_task.raw_markdown


def test_execution_plane_policy_blocked_status_can_restart_without_adaptive_upscope(
    tmp_path: Path,
) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "golden_path")
    script, env_path = write_policy_stage_driver(tmp_path)
    commands = {
        StageType.BUILDER: [sys.executable, str(script), "builder-record-policy-env"],
        StageType.QA: [sys.executable, str(script), "qa-complete"],
        StageType.UPDATE: [sys.executable, str(script), "update-idle"],
    }

    blocked_plane = configure_execution_plane(
        workspace,
        config_path,
        commands,
        integration_mode="never",
        mutate_config=lambda config: (
            setattr(config.stages[StageType.BUILDER], "allow_search", True),
            setattr(config.policies.search, "execution_enabled", False),
        ),
        transport_probe=StaticTransportProbe(
            TransportProbeResult(
                readiness=TransportReadiness.READY,
                summary="transport=ready",
            )
        ),
    )

    blocked_result = blocked_plane.run_once()

    assert blocked_result.final_status is ExecutionStatus.BLOCKED
    assert not env_path.exists()
    assert (workspace / "agents/status.md").read_text(encoding="utf-8") == "### BLOCKED\n"

    resumed_plane = configure_execution_plane(
        workspace,
        config_path,
        commands,
        integration_mode="never",
        mutate_config=lambda config: (
            setattr(config.stages[StageType.BUILDER], "allow_search", True),
            setattr(config.policies.search, "execution_enabled", True),
        ),
        transport_probe=StaticTransportProbe(
            TransportProbeResult(
                readiness=TransportReadiness.READY,
                summary="transport=ready",
            )
        ),
    )

    resumed_result = resumed_plane.run_once()

    assert resumed_result.final_status is ExecutionStatus.IDLE
    assert json.loads(env_path.read_text(encoding="utf-8")) == {
        "allow_network": "1",
        "allow_search": "1",
    }
    assert [stage.stage for stage in resumed_result.stage_results] == [
        StageType.BUILDER,
        StageType.QA,
        StageType.UPDATE,
    ]
    assert resumed_result.archived_task is not None
    assert "Adaptive Upscope" not in resumed_result.archived_task.raw_markdown


@pytest.mark.parametrize(
    ("blocked_readiness", "expected_blocked_status"),
    [
        (TransportReadiness.ENV_BLOCKED, ExecutionStatus.BLOCKED),
        (TransportReadiness.NET_WAIT, ExecutionStatus.NET_WAIT),
    ],
)
def test_execution_plane_preflight_repeated_block_keeps_same_status_without_crashing(
    tmp_path: Path,
    blocked_readiness: TransportReadiness,
    expected_blocked_status: ExecutionStatus,
) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "golden_path")
    script, env_path = write_policy_stage_driver(tmp_path)
    commands = {
        StageType.BUILDER: [sys.executable, str(script), "builder-record-policy-env"],
        StageType.QA: [sys.executable, str(script), "qa-complete"],
        StageType.UPDATE: [sys.executable, str(script), "update-idle"],
    }

    plane = configure_execution_plane(
        workspace,
        config_path,
        commands,
        integration_mode="never",
        mutate_config=lambda config: (
            setattr(config.stages[StageType.BUILDER], "allow_search", True),
            setattr(config.policies.search, "execution_enabled", True),
        ),
        transport_probe=StaticTransportProbe(
            TransportProbeResult(
                readiness=blocked_readiness,
                summary=f"transport={blocked_readiness.value}",
            )
        ),
    )

    first = plane.run_once()
    second = plane.run_once()

    assert first.final_status is expected_blocked_status
    assert second.final_status is expected_blocked_status
    assert not env_path.exists()
    assert (workspace / "agents/status.md").read_text(encoding="utf-8") == expected_blocked_status.marker + "\n"
    assert len(second.stage_results) == 1
    assert second.stage_results[0].status == expected_blocked_status.value


@pytest.mark.parametrize(
    ("threshold_field", "current_value", "expected_pause"),
    [
        ("remaining_threshold", "9", True),
        ("remaining_threshold", "11", False),
        ("consumed_threshold", "11", True),
        ("consumed_threshold", "9", False),
    ],
)
def test_execution_plane_usage_autopause_thresholds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    threshold_field: str,
    current_value: str,
    expected_pause: bool,
) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "golden_path")
    script = write_stage_driver(tmp_path)
    monkeypatch.setenv("USAGE_SAMPLER_ORCH_CURRENT", current_value)

    def mutate_config(config) -> None:
        config.policies.usage.enabled = True
        config.policies.usage.provider = "env"
        config.policies.usage.execution.remaining_threshold = None
        config.policies.usage.execution.consumed_threshold = None
        setattr(config.policies.usage.execution, threshold_field, "10")
        config.execution.integration_mode = "never"

    plane = configure_execution_plane(
        workspace,
        config_path,
        {
            StageType.BUILDER: [sys.executable, str(script), "builder"],
            StageType.QA: [sys.executable, str(script), "qa-complete"],
            StageType.UPDATE: [sys.executable, str(script), "update-idle"],
        },
        integration_mode="never",
        update_on_empty=False,
        mutate_config=mutate_config,
    )

    result = plane.run_once()

    assert result.transition_history_path is not None
    records = read_transition_history(result.transition_history_path)
    usage_records = [
        record.policy_evaluation_record()
        for record in records
        if record.policy_evaluator == ExecutionUsageBudgetEvaluator.evaluator_name
    ]
    assert len(usage_records) == 1
    if expected_pause:
        assert result.pause_requested is True
        assert result.pause_reason is not None
        assert result.final_status is ExecutionStatus.IDLE
        assert result.stage_results == []
        assert usage_records[0].decision.value == "policy_blocked"
        assert all(record.event_name != "execution.stage.transition" for record in records)
        return

    assert result.pause_requested is False
    assert result.final_status is ExecutionStatus.IDLE
    assert [stage.stage for stage in result.stage_results] == [
        StageType.BUILDER,
        StageType.QA,
        StageType.UPDATE,
    ]
    assert usage_records[0].decision.value == "pass"


def test_execution_plane_applies_inter_task_delay_only_when_configured(tmp_path: Path) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "golden_path")
    script = write_stage_driver(tmp_path)

    def mutate_config(config) -> None:
        config.engine.inter_task_delay_seconds = 2
        config.execution.integration_mode = "never"

    plane = configure_execution_plane(
        workspace,
        config_path,
        {
            StageType.BUILDER: [sys.executable, str(script), "builder"],
            StageType.QA: [sys.executable, str(script), "qa-complete"],
            StageType.UPDATE: [sys.executable, str(script), "update-idle"],
        },
        integration_mode="never",
        mutate_config=mutate_config,
    )

    result = plane.run_once()

    assert result.final_status is ExecutionStatus.IDLE
    assert result.pacing_delay_seconds == 2
    assert result.stage_results[-1].metadata["pacing_delay_seconds_applied"] == 2
    records = read_transition_history(result.transition_history_path)
    pacing_records = [
        record.policy_evaluation_record()
        for record in records
        if record.policy_evaluator == ExecutionPacingEvaluator.evaluator_name
    ]
    assert len(pacing_records) == 1
    assert pacing_records[0].notes == ("Execution inter-task delay scheduled for 2 seconds.",)

    second_tmp = tmp_path / "no-delay"
    second_tmp.mkdir(parents=True, exist_ok=True)
    workspace, config_path = load_workspace_fixture(second_tmp, "golden_path")
    script = write_stage_driver(second_tmp)
    plane = configure_execution_plane(
        workspace,
        config_path,
        {
            StageType.BUILDER: [sys.executable, str(script), "builder"],
            StageType.QA: [sys.executable, str(script), "qa-complete"],
            StageType.UPDATE: [sys.executable, str(script), "update-idle"],
        },
        integration_mode="never",
        mutate_config=lambda config: setattr(config.engine, "inter_task_delay_seconds", 0),
    )

    result = plane.run_once()

    assert result.final_status is ExecutionStatus.IDLE
    assert result.pacing_delay_seconds == 0
    records = read_transition_history(result.transition_history_path)
    pacing_records = [
        record.policy_evaluation_record()
        for record in records
        if record.policy_evaluator == ExecutionPacingEvaluator.evaluator_name
    ]
    assert len(pacing_records) == 1
    assert pacing_records[0].notes == (
        "Execution inter-task delay skipped because engine.inter_task_delay_seconds is 0.",
    )


def test_execution_plane_settles_stale_needs_research_marker_without_active_task(tmp_path: Path) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "backlog_empty")
    (workspace / "agents/status.md").write_text("### NEEDS_RESEARCH\n", encoding="utf-8")
    (workspace / "agents/.runtime/research_recovery_latch.json").write_text(
        "\n".join(
            [
                "{",
                '  "state": "frozen",',
                '  "batch_id": "20260316T210000Z",',
                '  "frozen_at": "2026-03-16T21:00:00Z",',
                '  "diag_dir": "agents/diagnostics/diag-stale",',
                '  "failure_signature": "stale-needs-research",',
                '  "stage": "Consult",',
                '  "reason": "Crash after quarantine",',
                '  "frozen_backlog_cards": 1,',
                '  "retained_backlog_cards": 0,',
                '  "quarantine_reason": "consult_handoff"',
                "}",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    plane = configure_execution_plane(workspace, config_path, {}, update_on_empty=False)

    result = plane.run_once()

    assert result.final_status is ExecutionStatus.IDLE
    assert result.diagnostics_dir == Path("agents/diagnostics/diag-stale")
    assert parse_task_cards((workspace / "agents/tasks.md").read_text(encoding="utf-8")) == []
    assert (workspace / "agents/status.md").read_text(encoding="utf-8") == "### IDLE\n"


def test_outage_trigger_and_policy_record_reuse_frozen_net_wait_evidence(tmp_path: Path) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "golden_path")
    script, _ = write_policy_stage_driver(tmp_path)

    plane = configure_execution_plane(
        workspace,
        config_path,
        {
            StageType.BUILDER: [sys.executable, str(script), "builder-record-policy-env"],
            StageType.QA: [sys.executable, str(script), "qa-complete"],
            StageType.UPDATE: [sys.executable, str(script), "update-idle"],
        },
        integration_mode="never",
        mutate_config=lambda config: (
            setattr(config.policies.outage, "wait_initial_seconds", 0),
            setattr(config.policies.outage, "wait_max_seconds", 0),
            setattr(config.policies.outage, "max_probes", 1),
        ),
        transport_probe=StaticTransportProbe(
            TransportProbeResult(
                readiness=TransportReadiness.NET_WAIT,
                summary="transport=net_wait",
            )
        ),
    )

    result = plane.run_once()

    assert result.final_status is ExecutionStatus.NET_WAIT
    assert result.transition_history_path is not None
    trigger = OutageTrigger.from_history(result.transition_history_path)
    assert trigger.stage is StageType.BUILDER
    assert trigger.preflight.block_status is ExecutionStatus.NET_WAIT

    policy = OutagePolicySnapshot.from_config(plane.config)
    decision = evaluate_outage_attempt(
        policy,
        OutageAttempt(
            timestamp="2026-03-19T10:00:00Z",
            attempt=1,
            wait_seconds=0,
            probe=OutageProbeResult(
                readiness=TransportReadiness.NET_WAIT,
                summary="probe still waiting",
            ),
        ),
    )
    record = outage_policy_record(
        trigger=trigger,
        policy=policy,
        attempt=OutageAttempt(
            timestamp="2026-03-19T10:00:00Z",
            attempt=1,
            wait_seconds=0,
            probe=OutageProbeResult(
                readiness=TransportReadiness.NET_WAIT,
                summary="probe still waiting",
            ),
        ),
        decision=decision,
        transition_history_count=5,
        current_status=ExecutionStatus.NET_WAIT,
    )

    assert record.decision is not None
    assert record.decision.value == "net_wait"
    assert record.facts.plan is not None
    assert record.facts.stage is not None
    assert record.facts.stage.node_id == trigger.node_id
    assert {item.kind.value for item in record.evidence} == {"outage_policy", "outage_probe"}


def test_outage_trigger_rejects_history_without_persisted_net_wait_record(tmp_path: Path) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "golden_path")
    script, _ = write_policy_stage_driver(tmp_path)

    plane = configure_execution_plane(
        workspace,
        config_path,
        {
            StageType.BUILDER: [sys.executable, str(script), "builder-record-policy-env"],
            StageType.QA: [sys.executable, str(script), "qa-complete"],
            StageType.UPDATE: [sys.executable, str(script), "update-idle"],
        },
        integration_mode="never",
        transport_probe=StaticTransportProbe(
            TransportProbeResult(
                readiness=TransportReadiness.READY,
                summary="transport=ready",
            )
        ),
    )

    result = plane.run_once()

    assert result.final_status is ExecutionStatus.IDLE
    assert result.transition_history_path is not None
    with pytest.raises(OutagePolicyError, match="does not contain a persisted NET_WAIT preflight record"):
        OutageTrigger.from_history(result.transition_history_path)


def test_outage_policy_escalates_to_incident_or_blocker_when_probe_budget_exhausts() -> None:
    base = {
        "enabled": True,
        "wait_initial_seconds": 0,
        "wait_max_seconds": 0,
        "max_probes": 1,
        "probe_timeout_seconds": 1,
        "probe_host": "api.openai.com",
        "probe_port": 443,
        "probe_command": (),
        "route_to_blocker": False,
        "route_to_incident": False,
    }
    attempt = OutageAttempt(
        timestamp="2026-03-19T10:00:00Z",
        attempt=1,
        wait_seconds=0,
        probe=OutageProbeResult(
            readiness=TransportReadiness.NET_WAIT,
            summary="probe still waiting",
        ),
    )

    blocker_decision = evaluate_outage_attempt(
        OutagePolicySnapshot.model_validate(base | {"policy": OutageRoute.BLOCKER}),
        attempt,
    )
    incident_decision = evaluate_outage_attempt(
        OutagePolicySnapshot.model_validate(base | {"policy": OutageRoute.PAUSE_RESUME, "route_to_incident": True}),
        attempt,
    )

    assert blocker_decision.action is not None
    assert blocker_decision.action.value == "route_to_blocker"
    assert blocker_decision.policy_decision.value == "policy_blocked"
    assert incident_decision.action is not None
    assert incident_decision.action.value == "route_to_incident"
    assert incident_decision.route.value == "incident"
