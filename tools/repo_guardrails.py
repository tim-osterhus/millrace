from __future__ import annotations

import argparse
import ast
import os
import subprocess
import sys
import tomllib
from collections import defaultdict
from pathlib import Path
from typing import TypedDict, cast

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = Path(__file__).with_name("repo_guardrails.toml")


class LintConfig(TypedDict):
    paths: list[str]
    scope: str
    rationale: str


class TypecheckConfig(TypedDict):
    paths: list[str]
    args: list[str]
    scope: str
    rationale: str


class BudgetExceptions(TypedDict):
    runtime: list[str]
    tests: list[str]


class BudgetConfig(TypedDict):
    runtime_limit: int
    test_limit: int
    exceptions: BudgetExceptions


class AllowedCycleConfig(TypedDict):
    modules: list[str]


class CycleConfig(TypedDict):
    allowed: list[AllowedCycleConfig]


class GuardrailConfig(TypedDict):
    lint: LintConfig
    typecheck: TypecheckConfig
    budgets: BudgetConfig
    cycles: CycleConfig


def _load_config() -> GuardrailConfig:
    return cast(GuardrailConfig, tomllib.loads(CONFIG_PATH.read_text(encoding="utf-8")))


def _run(cmd: list[str], *, cwd: Path = ROOT, env: dict[str, str] | None = None) -> int:
    completed = subprocess.run(cmd, cwd=cwd, env=env, check=False)
    return completed.returncode


def _python_module(module: str, *args: str) -> list[str]:
    return [sys.executable, "-m", module, *args]


def _line_count(path: Path) -> int:
    with path.open("r", encoding="utf-8") as handle:
        return sum(1 for _ in handle)


def _iter_python_files(root: Path) -> list[Path]:
    return [
        path
        for path in sorted(root.rglob("*.py"))
        if "__pycache__" not in path.parts and "build" not in path.parts
    ]


def _run_lint(config: GuardrailConfig) -> int:
    paths = [str(ROOT / value) for value in config["lint"]["paths"]]
    for args in (["format", "--check", *paths], ["check", *paths]):
        code = _run(_python_module("ruff", *args))
        if code:
            return code
    return 0


def _run_typecheck(config: GuardrailConfig) -> int:
    typecheck_config = config["typecheck"]
    paths = [str(ROOT / value) for value in typecheck_config["paths"]]
    return _run(_python_module("mypy", *typecheck_config["args"], *paths))


def _budget_failures(
    group_root: Path, *, limit: int, exceptions: set[str]
) -> tuple[list[str], list[str]]:
    failures: list[str] = []
    stale_exceptions: list[str] = []
    for path in _iter_python_files(group_root):
        relative = path.relative_to(ROOT).as_posix()
        over_budget = _line_count(path) > limit
        if over_budget and relative not in exceptions:
            failures.append(relative)
        if not over_budget and relative in exceptions:
            stale_exceptions.append(relative)
    missing = sorted(path for path in exceptions if not (ROOT / path).is_file())
    return failures + [f"missing:{path}" for path in missing], stale_exceptions


def _run_budgets(config: GuardrailConfig) -> int:
    budget_config = config["budgets"]
    exception_config = budget_config["exceptions"]
    runtime_failures, runtime_stale = _budget_failures(
        ROOT / "millrace_engine",
        limit=int(budget_config["runtime_limit"]),
        exceptions=set(exception_config["runtime"]),
    )
    test_failures, test_stale = _budget_failures(
        ROOT / "tests",
        limit=int(budget_config["test_limit"]),
        exceptions=set(exception_config["tests"]),
    )
    stale = runtime_stale + test_stale
    for path in stale:
        print(f"stale size-budget exception: {path}", file=sys.stderr)
    failures = runtime_failures + test_failures
    if failures:
        print("size-budget violations:", file=sys.stderr)
        for path in failures:
            print(f"  - {path}", file=sys.stderr)
        return 1
    return 0


def _module_name(path: Path) -> str:
    return ".".join(path.relative_to(ROOT).with_suffix("").parts)


def _resolve_import_targets(
    node: ast.ImportFrom,
    *,
    current_module: str,
    modules: set[str],
) -> set[str]:
    targets: set[str] = set()
    package_parts = current_module.split(".")[:-1]
    if node.level:
        anchor_parts = package_parts[: len(package_parts) - (node.level - 1)]
        if node.module:
            anchor_parts += node.module.split(".")
    else:
        anchor_parts = node.module.split(".") if node.module else []
    if anchor_parts:
        anchor = ".".join(anchor_parts)
        if anchor in modules:
            targets.add(anchor)
    for alias in node.names:
        if alias.name == "*":
            continue
        candidate_parts = [*anchor_parts, *alias.name.split(".")]
        candidate = ".".join(candidate_parts)
        if candidate in modules:
            targets.add(candidate)
    return targets


def _module_graph() -> tuple[set[str], dict[str, set[str]]]:
    modules = {_module_name(path) for path in _iter_python_files(ROOT / "millrace_engine")}
    edges: dict[str, set[str]] = defaultdict(set)
    for path in _iter_python_files(ROOT / "millrace_engine"):
        module = _module_name(path)
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in modules:
                        edges[module].add(alias.name)
            elif isinstance(node, ast.ImportFrom):
                edges[module].update(
                    _resolve_import_targets(node, current_module=module, modules=modules)
                )
    return modules, edges


def _tarjan_scc(nodes: set[str], edges: dict[str, set[str]]) -> list[tuple[str, ...]]:
    index = 0
    stack: list[str] = []
    on_stack: set[str] = set()
    indexes: dict[str, int] = {}
    lowlinks: dict[str, int] = {}
    components: list[tuple[str, ...]] = []

    def visit(node: str) -> None:
        nonlocal index
        indexes[node] = index
        lowlinks[node] = index
        index += 1
        stack.append(node)
        on_stack.add(node)
        for target in sorted(edges.get(node, ())):
            if target not in indexes:
                visit(target)
                lowlinks[node] = min(lowlinks[node], lowlinks[target])
            elif target in on_stack:
                lowlinks[node] = min(lowlinks[node], indexes[target])
        if lowlinks[node] == indexes[node]:
            component: list[str] = []
            while True:
                member = stack.pop()
                on_stack.remove(member)
                component.append(member)
                if member == node:
                    break
            if len(component) > 1:
                components.append(tuple(sorted(component)))

    for node in sorted(nodes):
        if node not in indexes:
            visit(node)
    return sorted(components, key=lambda item: (-len(item), item))


def _run_cycles(config: GuardrailConfig) -> int:
    allowed = {tuple(sorted(entry["modules"])) for entry in config["cycles"]["allowed"]}
    modules, edges = _module_graph()
    observed = set(_tarjan_scc(modules, edges))
    failures = sorted(observed - allowed)
    stale = sorted(allowed - observed)
    for component in stale:
        print(
            "stale cycle exception: " + ", ".join(component),
            file=sys.stderr,
        )
    if failures:
        print("unexpected import cycles:", file=sys.stderr)
        for component in failures:
            print("  - " + ", ".join(component), file=sys.stderr)
        return 1
    return 0


def _run_tests(pytest_args: list[str]) -> int:
    env = os.environ.copy()
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    args = pytest_args[1:] if pytest_args and pytest_args[0] == "--" else pytest_args
    args = args or ["-q", "tests"]
    return _run([sys.executable, "-m", "pytest", *args], env=env)


def _run_all(config: GuardrailConfig, pytest_args: list[str]) -> int:
    for code in (
        _run_lint(config),
        _run_typecheck(config),
        _run_budgets(config),
        _run_cycles(config),
    ):
        if code:
            return code
    if pytest_args:
        return _run_tests(pytest_args)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Run repo-local Millrace guardrails.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("lint")
    subparsers.add_parser("typecheck")
    subparsers.add_parser("budgets")
    subparsers.add_parser("cycles")
    test_parser = subparsers.add_parser("test")
    test_parser.add_argument("pytest_args", nargs=argparse.REMAINDER)
    all_parser = subparsers.add_parser("all")
    all_parser.add_argument("pytest_args", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    config = _load_config()

    if args.command == "lint":
        return _run_lint(config)
    if args.command == "typecheck":
        return _run_typecheck(config)
    if args.command == "budgets":
        return _run_budgets(config)
    if args.command == "cycles":
        return _run_cycles(config)
    if args.command == "test":
        return _run_tests(args.pytest_args)
    if args.command == "all":
        return _run_all(config, args.pytest_args)
    raise AssertionError(f"unsupported command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
