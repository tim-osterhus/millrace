import shutil
import subprocess
import sys
import venv
from pathlib import Path

import millrace_ai

RUNTIME_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[4]
MINIMUM_FUNCTIONALITY_WORKSPACE = REPO_ROOT / "workspaces" / "minimum-functionality"


def test_import_millrace_ai_namespace() -> None:
    assert millrace_ai.__version__


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
    script = shutil.which("millrace")
    assert script is not None

    result = subprocess.run(
        [script],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "Missing command" in result.stdout


def test_source_cli_verifies_root_minimum_functionality_workspace() -> None:
    runtime_subtree = MINIMUM_FUNCTIONALITY_WORKSPACE / "millrace-agents"
    shutil.rmtree(runtime_subtree, ignore_errors=True)

    compile_validate = subprocess.run(
        [
            sys.executable,
            "-m",
            "millrace_ai",
            "compile",
            "validate",
            "--workspace",
            str(MINIMUM_FUNCTIONALITY_WORKSPACE),
        ],
        check=False,
        capture_output=True,
        text=True,
        cwd=RUNTIME_ROOT,
    )
    run_once = subprocess.run(
        [
            sys.executable,
            "-m",
            "millrace_ai",
            "run",
            "once",
            "--workspace",
            str(MINIMUM_FUNCTIONALITY_WORKSPACE),
        ],
        check=False,
        capture_output=True,
        text=True,
        cwd=RUNTIME_ROOT,
    )
    status = subprocess.run(
        [
            sys.executable,
            "-m",
            "millrace_ai",
            "status",
            "--workspace",
            str(MINIMUM_FUNCTIONALITY_WORKSPACE),
        ],
        check=False,
        capture_output=True,
        text=True,
        cwd=RUNTIME_ROOT,
    )

    assert compile_validate.returncode == 0, compile_validate.stderr or compile_validate.stdout
    assert run_once.returncode == 0, run_once.stderr or run_once.stdout
    assert status.returncode == 0, status.stderr or status.stdout
    assert (runtime_subtree / "millrace.toml").is_file()
    assert "compiled_plan_id:" in status.stdout


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
    )
    assert pip_install.returncode == 0, pip_install.stderr or pip_install.stdout

    compile_validate = subprocess.run(
        [
            str(python_bin),
            "-m",
            "millrace_ai",
            "compile",
            "validate",
            "--workspace",
            str(MINIMUM_FUNCTIONALITY_WORKSPACE),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    run_once = subprocess.run(
        [
            str(python_bin),
            "-m",
            "millrace_ai",
            "run",
            "once",
            "--workspace",
            str(MINIMUM_FUNCTIONALITY_WORKSPACE),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    status = subprocess.run(
        [
            str(python_bin),
            "-m",
            "millrace_ai",
            "status",
            "--workspace",
            str(MINIMUM_FUNCTIONALITY_WORKSPACE),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert compile_validate.returncode == 0, compile_validate.stderr or compile_validate.stdout
    assert run_once.returncode == 0, run_once.stderr or run_once.stdout
    assert status.returncode == 0, status.stderr or status.stdout
    assert (runtime_subtree / "millrace.toml").is_file()
