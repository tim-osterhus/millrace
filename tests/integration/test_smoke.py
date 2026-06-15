import json
import os
import shutil
import subprocess
import sys
import venv
from pathlib import Path

import millrace_ai

RUNTIME_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = Path(__file__).resolve().parents[5]
MINIMUM_FUNCTIONALITY_WORKSPACE = REPO_ROOT / "workspaces" / "minimum-functionality"
CODEX_SMOKE_SHIM = RUNTIME_ROOT / "tests" / "integration" / "codex_smoke_shim.py"
SMOKE_TASK_ID = "smoke-task-001"
CONSOLE_SCRIPT_FALLBACK = "import sys; from millrace_ai.cli import main; raise SystemExit(main(sys.argv[1:]))"


def test_import_millrace_ai_namespace() -> None:
    assert millrace_ai.__version__
    resolved = Path(millrace_ai.__file__).resolve().as_posix()
    assert "/src/millrace_ai/" in resolved


def test_legacy_namespace_not_importable() -> None:
    result = subprocess.run(
        [sys.executable, "-I", "-c", "import millrace"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "ModuleNotFoundError" in result.stderr


def test_python_module_entrypoint_runs() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "millrace_ai"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "Missing command" in result.stdout


def test_console_script_entrypoint_runs() -> None:
    command_prefix = _console_script_command_prefix()

    result = subprocess.run(
        [*command_prefix],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "Missing command" in result.stdout


def test_workspace_map_source_and_console_script_entrypoints_run(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    command_prefix = _console_script_command_prefix()

    init = subprocess.run(
        [sys.executable, "-m", "millrace_ai", "init", "--workspace", str(workspace)],
        check=False,
        capture_output=True,
        text=True,
        cwd=RUNTIME_ROOT,
    )
    assert init.returncode == 0, init.stderr or init.stdout

    workspace.joinpath("module.py").write_text("def public():\n    return 1\n", encoding="utf-8")
    source_refresh = subprocess.run(
        [sys.executable, "-m", "millrace_ai", "workspace-map", "refresh", "--workspace", str(workspace)],
        check=False,
        capture_output=True,
        text=True,
        cwd=RUNTIME_ROOT,
    )
    console_validate = subprocess.run(
        [*command_prefix, "workspace-map", "validate", "--workspace", str(workspace)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert source_refresh.returncode == 0, source_refresh.stderr or source_refresh.stdout
    assert "workspace_map_refreshed: true" in source_refresh.stdout
    assert console_validate.returncode == 0, console_validate.stderr or console_validate.stdout
    assert "ok: true" in console_validate.stdout


def _console_script_command_prefix() -> tuple[str, ...]:
    script = shutil.which("millrace")
    if script is not None:
        return (script,)
    return (sys.executable, "-c", CONSOLE_SCRIPT_FALLBACK)


def _venv_console_script_path(python_bin: Path, script_name: str) -> Path:
    result = subprocess.run(
        [
            str(python_bin),
            "-c",
            "import pathlib, sysconfig; print(pathlib.Path(sysconfig.get_path('scripts')) / %r)" % script_name,
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    return Path(result.stdout.strip())


def _without_pythonpath() -> dict[str, str]:
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    return env


def _run_cli(
    command_prefix: tuple[str, ...],
    *args: str,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [*command_prefix, *args],
        check=False,
        capture_output=True,
        text=True,
        cwd=cwd,
        env=env,
    )


def _write_smoke_task_document(path: Path) -> None:
    payload = {
        "task_id": SMOKE_TASK_ID,
        "title": "Smoke Task 001",
        "summary": "Deterministic queued-work smoke task",
        "target_paths": ["README.md"],
        "acceptance": ["One builder stage executes through the shipped CLI"],
        "required_checks": ["uv run --extra dev python -m pytest tests/integration/test_smoke.py -q"],
        "references": ["tests/integration/test_smoke.py"],
        "risk": ["none"],
        "created_at": "2026-04-15T12:00:00Z",
        "created_by": "tests",
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def _configure_codex_smoke_runner(workspace: Path, *, runner_python: Path) -> None:
    config_path = workspace / "millrace-agents" / "millrace.toml"
    config_path.write_text(
        config_path.read_text(encoding="utf-8")
        + "\n"
        + f"command = {json.dumps(str(runner_python))}\n"
        + f"args = [{json.dumps(str(CODEX_SMOKE_SHIM))}]\n",
        encoding="utf-8",
    )


def _exercise_minimum_functionality_workspace(
    *,
    command_prefix: tuple[str, ...],
    runner_python: Path,
    workspace: Path,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> None:
    init = _run_cli(
        command_prefix,
        "init",
        "--workspace",
        str(workspace),
        cwd=cwd,
        env=env,
    )
    compile_validate = _run_cli(
        command_prefix,
        "compile",
        "validate",
        "--workspace",
        str(workspace),
        cwd=cwd,
        env=env,
    )
    compile_show = _run_cli(
        command_prefix,
        "compile",
        "show",
        "--workspace",
        str(workspace),
        cwd=cwd,
        env=env,
    )
    no_work_bounded_daemon = _run_cli(
        command_prefix,
        "run",
        "daemon",
        "--workspace",
        str(workspace),
        "--max-ticks",
        "1",
        cwd=cwd,
        env=env,
    )
    status = _run_cli(
        command_prefix,
        "status",
        "--workspace",
        str(workspace),
        cwd=cwd,
        env=env,
    )

    assert init.returncode == 0, init.stderr or init.stdout
    assert compile_validate.returncode == 0, compile_validate.stderr or compile_validate.stdout
    assert compile_show.returncode == 0, compile_show.stderr or compile_show.stdout
    assert no_work_bounded_daemon.returncode == 0, no_work_bounded_daemon.stderr or no_work_bounded_daemon.stdout
    assert status.returncode == 0, status.stderr or status.stdout
    assert (workspace / "millrace-agents" / "millrace.toml").is_file()
    assert "completion_behavior.request_kind: closure_target" in compile_show.stdout
    assert "run_mode: daemon" in no_work_bounded_daemon.stdout
    assert "ticks: 1" in no_work_bounded_daemon.stdout
    assert "compiled_plan_id:" in status.stdout

    _configure_codex_smoke_runner(workspace, runner_python=runner_python)
    task_document_path = workspace / f"{SMOKE_TASK_ID}.json"
    _write_smoke_task_document(task_document_path)

    queue_add_task = _run_cli(
        command_prefix,
        "queue",
        "add-task",
        str(task_document_path),
        "--workspace",
        str(workspace),
        cwd=cwd,
        env=env,
    )
    queued_bounded_daemon = _run_cli(
        command_prefix,
        "run",
        "daemon",
        "--workspace",
        str(workspace),
        "--max-ticks",
        "1",
        cwd=cwd,
        env=env,
    )
    runs_ls = _run_cli(
        command_prefix,
        "runs",
        "ls",
        "--workspace",
        str(workspace),
        cwd=cwd,
        env=env,
    )

    assert queue_add_task.returncode == 0, queue_add_task.stderr or queue_add_task.stdout
    assert queued_bounded_daemon.returncode == 0, queued_bounded_daemon.stderr or queued_bounded_daemon.stdout
    assert runs_ls.returncode == 0, runs_ls.stderr or runs_ls.stdout
    assert "enqueued_task:" in queue_add_task.stdout
    assert "run_mode: daemon" in queued_bounded_daemon.stdout
    assert "ticks: 1" in queued_bounded_daemon.stdout
    assert f"work_item_id: {SMOKE_TASK_ID}" in runs_ls.stdout

    run_id = next(
        line.partition(": ")[2]
        for line in runs_ls.stdout.splitlines()
        if line.startswith("run_id: ")
    )
    runs_show = _run_cli(
        command_prefix,
        "runs",
        "show",
        run_id,
        "--workspace",
        str(workspace),
        cwd=cwd,
        env=env,
    )

    assert runs_show.returncode == 0, runs_show.stderr or runs_show.stdout
    assert f"work_item_id: {SMOKE_TASK_ID}" in runs_show.stdout
    assert "stage: builder" in runs_show.stdout
    assert "terminal_result: BUILDER_COMPLETE" in runs_show.stdout
    assert "runner_name: codex_cli" in runs_show.stdout

    prompt_paths = sorted((workspace / "millrace-agents" / "runs").glob("**/runner_prompt.*.md"))
    assert prompt_paths
    for prompt_path in prompt_paths:
        prompt_text = prompt_path.read_text(encoding="utf-8")
        assert "### TOKEN" not in prompt_text


def test_source_cli_verifies_root_minimum_functionality_workspace() -> None:
    runtime_subtree = MINIMUM_FUNCTIONALITY_WORKSPACE / "millrace-agents"
    shutil.rmtree(runtime_subtree, ignore_errors=True)
    _exercise_minimum_functionality_workspace(
        command_prefix=(sys.executable, "-m", "millrace_ai"),
        runner_python=Path(sys.executable),
        workspace=MINIMUM_FUNCTIONALITY_WORKSPACE,
        cwd=RUNTIME_ROOT,
    )


def test_installed_wheel_verifies_root_minimum_functionality_workspace(tmp_path: Path) -> None:
    runtime_subtree = MINIMUM_FUNCTIONALITY_WORKSPACE / "millrace-agents"
    shutil.rmtree(runtime_subtree, ignore_errors=True)

    dist_dir = tmp_path / "dist"
    dist_dir.mkdir(parents=True, exist_ok=True)
    build = subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(dist_dir)],
        check=False,
        capture_output=True,
        text=True,
        cwd=RUNTIME_ROOT,
    )
    assert build.returncode == 0, build.stderr or build.stdout

    wheel = next(dist_dir.glob("millrace_ai-*.whl"))
    venv_dir = tmp_path / "venv"
    venv.EnvBuilder(with_pip=True).create(venv_dir)
    python_bin = venv_dir / "bin" / "python"
    pip_install = subprocess.run(
        [str(python_bin), "-m", "pip", "install", str(wheel)],
        check=False,
        capture_output=True,
        text=True,
        env=_without_pythonpath(),
    )
    assert pip_install.returncode == 0, pip_install.stderr or pip_install.stdout
    millrace_bin = _venv_console_script_path(python_bin, "millrace")
    assert millrace_bin.is_file()

    _exercise_minimum_functionality_workspace(
        command_prefix=(str(millrace_bin),),
        runner_python=python_bin,
        workspace=MINIMUM_FUNCTIONALITY_WORKSPACE,
        env=_without_pythonpath(),
    )
