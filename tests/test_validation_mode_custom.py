from __future__ import annotations

from pathlib import Path
import json
import sys

import millrace_engine.engine as engine_module
from typer.testing import CliRunner

from millrace_engine.cli import app
from millrace_engine.compiler import CompileStatus, FrozenRunCompiler
from millrace_engine.config import build_runtime_paths, load_engine_config
from millrace_engine.contracts import PersistedObjectKind, RegistryObjectRef, StageType
from millrace_engine.markdown import parse_task_cards
from millrace_engine.provenance import read_transition_history
from millrace_engine.registry import discover_registry_state
from tests.support import load_workspace_fixture


RUNNER = CliRunner()
CUSTOM_LOOP_REF = RegistryObjectRef(
    kind=PersistedObjectKind.LOOP_CONFIG,
    id="execution.validation_mode_custom",
    version="1.0.0",
)


def _append_subprocess_stage_config(config_path: Path) -> None:
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
                "[stages.update]",
                'runner = "subprocess"',
                'model = "update-model"',
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
            ]
        ),
        encoding="utf-8",
    )


def _write_stage_driver(tmp_path: Path) -> Path:
    script = tmp_path / "validation_mode_custom_stage_driver.py"
    script.write_text(
        "\n".join(
            [
                "from __future__ import annotations",
                "",
                "import sys",
                "",
                "mode = sys.argv[1]",
                "if mode == 'builder':",
                "    print('Builder completed for validation fixture')",
                "    print('### BUILDER_COMPLETE')",
                "    raise SystemExit(0)",
                "if mode == 'update':",
                "    print('Update completed for validation fixture')",
                "    raise SystemExit(0)",
                "raise SystemExit(7)",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return script


def _patch_cli_engine(monkeypatch, stage_commands: dict[StageType, list[str]]) -> None:
    original = engine_module.MillraceEngine

    class PatchedMillraceEngine(original):
        def __init__(self, config_path: Path | str = "millrace.toml", **kwargs) -> None:
            kwargs.setdefault("stage_commands", stage_commands)
            super().__init__(config_path, **kwargs)

    monkeypatch.setattr(engine_module, "MillraceEngine", PatchedMillraceEngine)


def _latest_run_id(workspace: Path) -> str:
    run_dirs = [
        run_dir
        for run_dir in (workspace / "agents" / "runs").iterdir()
        if run_dir.is_dir() and run_dir.name != ".gitkeep"
    ]
    assert run_dirs
    return sorted(run_dirs)[-1].name


def test_validation_mode_custom_fixture_shadows_default_autonomous_with_custom_loop(tmp_path: Path) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "validation_mode_custom")
    discovery = discover_registry_state(workspace, validate_catalog=False)
    effective_documents = {document.key: document for document in discovery.effective}

    shadowed_mode = effective_documents[("mode", "mode.default_autonomous", "1.0.0")]
    assert shadowed_mode.layer.value == "workspace"
    assert shadowed_mode.definition.payload.execution_loop_ref == CUSTOM_LOOP_REF

    loaded = load_engine_config(config_path)
    config = loaded.config
    config.paths.workspace = workspace
    config.paths.agents_dir = workspace / "agents"
    paths = build_runtime_paths(config)

    compile_result = FrozenRunCompiler(paths).compile_mode(
        RegistryObjectRef(
            kind=PersistedObjectKind.MODE,
            id="mode.default_autonomous",
            version="1.0.0",
        ),
        run_id="validation-mode-custom-compile",
    )

    assert compile_result.status is CompileStatus.OK
    assert compile_result.plan is not None
    assert compile_result.snapshot is not None
    assert compile_result.snapshot.content.selected_mode_ref.id == "mode.default_autonomous"
    assert compile_result.snapshot.content.selected_execution_loop_ref == CUSTOM_LOOP_REF
    assert [
        stage.node_id for stage in compile_result.plan.content.execution_plan.stages
    ] == ["builder", "update"]


def test_validation_mode_custom_cli_start_runs_custom_loop(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace, config_path = load_workspace_fixture(tmp_path, "validation_mode_custom")
    _append_subprocess_stage_config(config_path)
    script = _write_stage_driver(tmp_path)
    _patch_cli_engine(
        monkeypatch,
        {
            StageType.BUILDER: [sys.executable, str(script), "builder"],
            StageType.UPDATE: [sys.executable, str(script), "update"],
        },
    )

    start_result = RUNNER.invoke(
        app,
        ["--config", str(config_path), "start", "--once", "--json"],
    )

    assert start_result.exit_code == 0, start_result.output
    start_payload = json.loads(start_result.stdout)
    assert start_payload["process_running"] is False
    assert start_payload["execution_status"] == "IDLE"

    archived_cards = parse_task_cards((workspace / "agents" / "tasksarchive.md").read_text(encoding="utf-8"))
    assert [card.title for card in archived_cards] == ["Validate workspace custom loop"]

    run_id = _latest_run_id(workspace)
    provenance_result = RUNNER.invoke(
        app,
        ["--config", str(config_path), "run-provenance", run_id, "--json"],
    )

    assert provenance_result.exit_code == 0, provenance_result.output
    payload = json.loads(provenance_result.stdout)
    assert payload["selection"]["mode"]["ref"]["id"] == "mode.standard"
    assert payload["selection"]["mode"]["registry_layer"] == "workspace"
    assert payload["selection"]["execution_loop"]["ref"]["id"] == CUSTOM_LOOP_REF.id
    assert payload["selection"]["execution_loop"]["registry_layer"] == "workspace"
    assert [binding["node_id"] for binding in payload["selection"]["stage_bindings"]] == [
        "builder",
        "update",
    ]

    transition_history = read_transition_history(workspace / "agents" / "runs" / run_id / "transition_history.jsonl")
    assert [
        record.node_id
        for record in transition_history
        if record.event_name == "execution.stage.transition"
    ] == ["builder", "update"]
