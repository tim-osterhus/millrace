from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any


def _load_repo_guardrails_module() -> ModuleType:
    module_path = Path(__file__).resolve().parents[1] / "tools" / "repo_guardrails.py"
    spec = importlib.util.spec_from_file_location("test_repo_guardrails_module", module_path)
    if spec is None or spec.loader is None:
        raise AssertionError(f"unable to load module from {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_python_file(path: Path, line_count: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "".join(f"value_{index} = {index}\n" for index in range(line_count))
    path.write_text(body, encoding="utf-8")


def _git(repo_root: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args],
        cwd=repo_root,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _init_repo(repo_root: Path) -> None:
    _git(repo_root, "init")
    _git(repo_root, "config", "user.name", "Millrace Tests")
    _git(repo_root, "config", "user.email", "millrace-tests@example.com")


def _commit_all(repo_root: Path, message: str) -> None:
    _git(repo_root, "add", ".")
    _git(repo_root, "commit", "-m", message)


def _budget_config(*, temporary_runtime: list[dict[str, str]] | None = None) -> dict[str, Any]:
    return {
        "lint": {"paths": [], "scope": "test", "rationale": "test"},
        "typecheck": {"paths": [], "args": [], "scope": "test", "rationale": "test"},
        "budgets": {
            "runtime_limit": 500,
            "test_limit": 800,
            "exceptions": {
                "runtime": ["millrace_engine/planes/execution.py"],
                "tests": [],
            },
            "ratchet": {
                "runtime": ["millrace_engine/planes/execution.py", "millrace_engine/engine_runtime_loop.py"],
                "diff_base": "HEAD",
            },
            "temporary_exceptions": {
                "runtime": temporary_runtime or [],
            },
        },
        "cycles": {"allowed": []},
    }


def _cycle_config(*allowed: tuple[str, ...]) -> dict[str, Any]:
    config = _budget_config()
    config["cycles"] = {"allowed": [{"modules": list(component)} for component in allowed]}
    return config


def _make_repo_tree(repo_root: Path) -> None:
    (repo_root / "tests").mkdir(parents=True, exist_ok=True)
    _write_python_file(repo_root / "millrace_engine" / "planes" / "execution.py", 772)
    _write_python_file(repo_root / "millrace_engine" / "engine.py", 319)
    _write_python_file(repo_root / "millrace_engine" / "research" / "goalspec_stage_support.py", 132)


def test_run_budgets_allows_temporary_new_oversized_file(tmp_path: Path, monkeypatch, capsys) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _init_repo(repo_root)
    _make_repo_tree(repo_root)
    _commit_all(repo_root, "baseline")

    _write_python_file(repo_root / "millrace_engine" / "engine_runtime_loop.py", 589)

    module = _load_repo_guardrails_module()
    monkeypatch.setattr(module, "ROOT", repo_root)

    config = _budget_config(
        temporary_runtime=[
            {
                "path": "millrace_engine/engine_runtime_loop.py",
                "owner": "batch-25-run-09",
                "rationale": "runtime-loop seam still needs a later split",
            }
        ]
    )

    assert module._run_budgets(config) == 0

    captured = capsys.readouterr()
    assert "stale size-budget exception" not in captured.err
    assert "active size-budget exception: millrace_engine/planes/execution.py (772>500)" in captured.err
    assert "temporary size-budget exception: millrace_engine/engine_runtime_loop.py (589>500)" in captured.err


def test_run_budgets_flags_same_change_ratchet_violation(tmp_path: Path, monkeypatch, capsys) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _init_repo(repo_root)
    _make_repo_tree(repo_root)
    _commit_all(repo_root, "baseline")

    _write_python_file(repo_root / "millrace_engine" / "planes" / "execution.py", 780)

    module = _load_repo_guardrails_module()
    monkeypatch.setattr(module, "ROOT", repo_root)

    assert module._run_budgets(_budget_config()) == 1

    captured = capsys.readouterr()
    assert (
        "same-change ratchet violation: millrace_engine/planes/execution.py "
        "(current 780 lines, baseline 772 lines)"
    ) in captured.err


def test_run_budgets_allows_touched_exception_with_paydown(tmp_path: Path, monkeypatch, capsys) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _init_repo(repo_root)
    _make_repo_tree(repo_root)
    _commit_all(repo_root, "baseline")

    _write_python_file(repo_root / "millrace_engine" / "planes" / "execution.py", 760)

    module = _load_repo_guardrails_module()
    monkeypatch.setattr(module, "ROOT", repo_root)

    assert module._run_budgets(_budget_config()) == 0

    captured = capsys.readouterr()
    assert "same-change ratchet violation" not in captured.err


def test_cycle_status_separates_unexpected_and_stale_allowed_components(
    monkeypatch,
) -> None:
    module = _load_repo_guardrails_module()
    monkeypatch.setattr(
        module,
        "_module_graph",
        lambda: (
            {
                "pkg.live_alpha",
                "pkg.live_beta",
                "pkg.unexpected_alpha",
                "pkg.unexpected_beta",
            },
            {
                "pkg.live_alpha": {"pkg.live_beta"},
                "pkg.live_beta": {"pkg.live_alpha"},
                "pkg.unexpected_alpha": {"pkg.unexpected_beta"},
                "pkg.unexpected_beta": {"pkg.unexpected_alpha"},
            },
        ),
    )

    status = module._cycle_status(
        _cycle_config(
            ("pkg.live_alpha", "pkg.live_beta"),
            ("pkg.stale_alpha", "pkg.stale_beta"),
        )
    )

    assert status.unexpected == (("pkg.unexpected_alpha", "pkg.unexpected_beta"),)
    assert status.stale_allowed == (("pkg.stale_alpha", "pkg.stale_beta"),)


def test_run_cycles_reports_stale_allowed_and_sorted_unexpected_components(
    monkeypatch, capsys
) -> None:
    module = _load_repo_guardrails_module()
    monkeypatch.setattr(
        module,
        "_module_graph",
        lambda: (
            {
                "pkg.zeta",
                "pkg.gamma",
                "pkg.beta",
                "pkg.alpha",
            },
            {
                "pkg.zeta": {"pkg.gamma"},
                "pkg.gamma": {"pkg.zeta"},
                "pkg.beta": {"pkg.alpha"},
                "pkg.alpha": {"pkg.beta"},
            },
        ),
    )

    assert (
        module._run_cycles(
            _cycle_config(
                ("pkg.stale_alpha", "pkg.stale_beta"),
            )
        )
        == 1
    )

    captured = capsys.readouterr()
    assert captured.err.splitlines() == [
        "stale allowed cycle: pkg.stale_alpha, pkg.stale_beta",
        "unexpected import cycles:",
        "  - pkg.alpha, pkg.beta",
        "  - pkg.gamma, pkg.zeta",
    ]


def test_run_cycles_passes_when_only_live_allowed_cycle_remains(monkeypatch, capsys) -> None:
    module = _load_repo_guardrails_module()
    monkeypatch.setattr(
        module,
        "_module_graph",
        lambda: (
            {"pkg.compiler_models", "pkg.compiler_rebinding"},
            {
                "pkg.compiler_models": {"pkg.compiler_rebinding"},
                "pkg.compiler_rebinding": {"pkg.compiler_models"},
            },
        ),
    )

    assert (
        module._run_cycles(
            _cycle_config(
                ("pkg.compiler_models", "pkg.compiler_rebinding"),
            )
        )
        == 0
    )

    captured = capsys.readouterr()
    assert captured.err == ""
