from __future__ import annotations

from pathlib import Path
import json
import sys

from typer.testing import CliRunner

from millrace_engine.cli import app
from millrace_engine.compiler import CompileStatus, FrozenRunCompiler
from millrace_engine.config import build_runtime_paths, load_engine_config
from millrace_engine.control import EngineControl
from millrace_engine.contracts import ExecutionStatus, PersistedObjectKind, RegistryObjectRef, RunnerKind, StageType
from millrace_engine.markdown import parse_task_cards
from millrace_engine.planes.execution import ExecutionPlane
from millrace_engine.provenance import read_transition_history
from millrace_engine.registry import RegistryLayer, discover_registry_state
from millrace_engine.standard_runtime import execution_node_ids_for_mode
from millrace_engine.standard_runtime_overrides import mode_overrides_for_execution_nodes
from tests.support import load_workspace_fixture


RUNNER = CliRunner()


def _default_autonomous_mode_ref() -> RegistryObjectRef:
    return RegistryObjectRef(
        kind=PersistedObjectKind.MODE,
        id="mode.default_autonomous",
        version="1.0.0",
    )


def _standard_loop_ref() -> RegistryObjectRef:
    return RegistryObjectRef(
        kind=PersistedObjectKind.LOOP_CONFIG,
        id="execution.standard",
        version="1.0.0",
    )


def _workspace_paths(config_path: Path, workspace: Path):
    loaded = load_engine_config(config_path)
    config = loaded.config
    config.paths.workspace = workspace
    config.paths.agents_dir = workspace / "agents"
    return config, build_runtime_paths(config)


def _write_stage_driver(tmp_path: Path) -> Path:
    script = tmp_path / "validation_mode_standard_driver.py"
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
                "def emit(marker: str, message: str) -> None:",
                "    print(message)",
                "    print(f'### {marker}')",
                "    last_path.write_text(f'{message}\\n### {marker}\\n', encoding='utf-8')",
                "    raise SystemExit(0)",
                "",
                "if mode == 'builder':",
                "    emit('BUILDER_COMPLETE', 'Builder finished')",
                "if mode == 'integration':",
                "    emit('INTEGRATION_COMPLETE', 'Integration finished')",
                "if mode == 'qa':",
                "    emit('QA_COMPLETE', 'QA finished')",
                "if mode == 'update':",
                "    print('Update complete')",
                "    raise SystemExit(0)",
                "",
                "raise SystemExit(9)",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return script


def _configure_execution_plane(workspace: Path, config_path: Path, script: Path) -> ExecutionPlane:
    loaded = load_engine_config(config_path)
    config = loaded.config
    config.paths.workspace = workspace
    config.paths.agents_dir = workspace / "agents"
    config.execution.integration_mode = "always"
    config.execution.run_update_on_empty = False

    for stage_type in (StageType.BUILDER, StageType.INTEGRATION, StageType.QA, StageType.UPDATE):
        stage_config = config.stages[stage_type]
        stage_config.runner = RunnerKind.SUBPROCESS
        stage_config.model = "fixture-model"
        stage_config.timeout_seconds = 30

    return ExecutionPlane(
        config,
        build_runtime_paths(config),
        stage_commands={
            StageType.BUILDER: [sys.executable, str(script), "builder"],
            StageType.INTEGRATION: [sys.executable, str(script), "integration"],
            StageType.QA: [sys.executable, str(script), "qa"],
            StageType.UPDATE: [sys.executable, str(script), "update"],
        },
    )


def _stage_transition_records(records):
    return [record for record in records if record.event_name == "execution.stage.transition"]


def test_validation_mode_standard_fixture_materializes_and_cli_reports_execution_standard(
    tmp_path: Path,
) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "validation_mode_standard")
    shadow_path = workspace / "agents" / "registry" / "modes" / "mode.default_autonomous__1.0.0.json"

    assert shadow_path.exists()

    state = discover_registry_state(workspace)
    effective_mode = next(
        document for document in state.effective if document.key == ("mode", "mode.default_autonomous", "1.0.0")
    )

    assert effective_mode.layer is RegistryLayer.WORKSPACE
    assert effective_mode.definition.payload.execution_loop_ref == _standard_loop_ref()

    config, paths = _workspace_paths(config_path, workspace)
    run_id = "validation-mode-standard-cli"
    compile_result = FrozenRunCompiler(paths).compile_mode(_default_autonomous_mode_ref(), run_id=run_id)

    assert compile_result.status is CompileStatus.OK
    assert compile_result.plan is not None
    assert compile_result.snapshot is not None
    assert compile_result.plan.content.selected_mode_ref == _default_autonomous_mode_ref()
    assert compile_result.plan.content.selected_execution_loop_ref == _standard_loop_ref()

    result = RUNNER.invoke(app, ["--config", str(config_path), "run-provenance", run_id, "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["selection"]["scope"] == "frozen_run"
    assert payload["selection"]["mode"]["ref"]["id"] == "mode.default_autonomous"
    assert payload["selection"]["mode"]["registry_layer"] == "workspace"
    assert payload["selection"]["execution_loop"]["ref"]["id"] == "execution.standard"
    assert payload["selection"]["execution_loop"]["registry_layer"] == "packaged"
    assert payload["current_preview"]["mode"]["ref"]["id"] == "mode.standard"


def test_validation_mode_standard_execution_plane_runs_minimal_task_via_shadowed_mode(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "validation_mode_standard")
    script = _write_stage_driver(tmp_path)
    plane = _configure_execution_plane(workspace, config_path, script)

    def _compile_shadowed_default_autonomous(
        config,
        paths,
        *,
        run_id: str,
        size_latch: str | None = None,
        current_status: ExecutionStatus | None = None,
        task_complexity: str | None = None,
        resolve_assets: bool = True,
    ):
        del size_latch, current_status
        mode_ref = _default_autonomous_mode_ref()
        node_ids = execution_node_ids_for_mode(paths, mode_ref)
        return FrozenRunCompiler(paths).compile_mode(
            mode_ref,
            run_id=run_id,
            overrides=mode_overrides_for_execution_nodes(
                config,
                node_ids,
                task_complexity=task_complexity,
            ),
            resolve_assets=resolve_assets,
        )

    monkeypatch.setattr(
        "millrace_engine.planes.execution.compile_execution_runtime_selection",
        _compile_shadowed_default_autonomous,
    )

    result = plane.run_once()

    assert result.final_status is ExecutionStatus.IDLE
    assert result.promoted_task is not None
    assert result.promoted_task.title == "Validate the shadowed autonomous loop"
    assert result.archived_task is not None
    assert result.archived_task.title == "Validate the shadowed autonomous loop"
    assert result.transition_history_path is not None

    records = _stage_transition_records(read_transition_history(result.transition_history_path))
    assert [record.node_id for record in records] == ["builder", "integration", "qa", "update"]

    report = EngineControl(config_path).run_provenance(result.run_id or "")

    assert report.compile_snapshot is not None
    assert report.compile_snapshot.content.selected_mode_ref == _default_autonomous_mode_ref()
    assert report.compile_snapshot.content.selected_execution_loop_ref == _standard_loop_ref()
    assert report.selection is not None
    assert report.selection.mode is not None
    assert report.selection.mode.ref.id == "mode.default_autonomous"
    assert report.selection.mode.registry_layer == "workspace"
    assert report.selection.execution_loop is not None
    assert report.selection.execution_loop.ref.id == "execution.standard"
    assert parse_task_cards((workspace / "agents" / "tasksbacklog.md").read_text(encoding="utf-8")) == []
