from __future__ import annotations

import ast
import hashlib
import json
import re
import subprocess
from graphlib import TopologicalSorter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
SESSION_ROOT = SRC / "millrace" / "adapters" / "cli"
FACADE = SESSION_ROOT / "session_coordinator.py"
GENERIC = tuple(
    SRC / path
    for path in (
        "millrace/adapters/runner_contract.py",
        "millrace/contracts/runner_events.py",
        "millrace/kernel/runner_sessions.py",
        "millrace/substrate/runner_session_events.py",
    )
)
FACADE_BINDINGS = set("""SessionCancellationRequestResult SessionExecutionResult
cooperative_cancel_grace_seconds execute_runner_session request_operator_cancellation
session_cancellation_token session_correlation_id terminate_grace_seconds""".split())
BANNED = set("""arbiter blueprint builder checker claude_code closure codex
execution idea learning millforge opencode openhands pi_rpc planner planning
probe recon root-spec spec task""".split())
APPEND = (188, 318, "e1e1447058757092a5c400c4edfc8707c311c00607b21c588e6532800f5f8e13")
def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(), filename=str(path))

def _sessions() -> list[Path]:
    return sorted(SESSION_ROOT.glob("session_*.py"))

def _imports(path: Path) -> set[str]:
    found = set()
    package = ".".join(path.relative_to(SRC).parts[:-1])
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            parts = package.split(".")[: -node.level + 1 or None]
            prefix = ".".join(parts) if node.level else ""
            base = ".".join(filter(None, (prefix, node.module or "")))
            found.add(base)
            found.update(
                ".".join(filter(None, (base, alias.name))) for alias in node.names
            )
    return found

def _functions(path: Path) -> list[tuple[str, ast.FunctionDef | ast.AsyncFunctionDef]]:
    tree = _tree(path)
    return [
        (f"{getattr(owner, 'name', '')}.{node.name}".lstrip("."), node)
        for owner in ast.walk(tree)
        if isinstance(getattr(owner, "body", None), list)
        for node in owner.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]

def test_session_file_and_test_size_limits() -> None:
    failures = []
    for path in _sessions():
        limit = 900 if path == FACADE else 800
        lines = len(path.read_text().splitlines())
        if lines > limit:
            failures.append(f"{path.relative_to(ROOT)}: {lines} > {limit}")
    tests = {
        path
        for pattern in ("test_session_*.py", "test_runner_session_*.py")
        for path in (ROOT / "tests").rglob(pattern)
    }
    tests.add(ROOT / "tests/guardrails/test_adapter_boundaries.py")
    failures.extend(
        f"{path.relative_to(ROOT)}: {len(path.read_text().splitlines())} > 1500"
        for path in tests
        if len(path.read_text().splitlines()) > 1_500
    )
    assert failures == []


def test_function_spans_and_exact_event_exception() -> None:
    event = GENERIC[3]
    failures = [
        f"{path.relative_to(ROOT)}::{name}: {node.end_lineno - node.lineno + 1}"
        for path in (*_sessions(), *GENERIC)
        for name, node in _functions(path)
        if node.end_lineno - node.lineno + 1 > 120 and
        (path, name) != (event, "RunnerSessionEventStore.append")
    ]
    _, node = next(
        x for x in _functions(event) if x[0] == "RunnerSessionEventStore.append"
    )
    lines = event.read_text().splitlines(keepends=True)
    actual = (
        node.lineno,
        node.end_lineno,
        hashlib.sha256(
            "".join(lines[node.lineno - 1 : node.end_lineno]).encode()
        ).hexdigest(),
    )
    if actual != APPEND and node.end_lineno - node.lineno + 1 > 120:
        failures.append(f"changed event append exception: {actual}")
    assert failures == []


def test_c901_target_and_exact_runner_contract_exception() -> None:
    result = subprocess.run(
        (
            "uv", "run", "ruff", "check", "--select", "C901",
            "--output-format", "json",
            *(str(path) for path in [*_sessions(), *GENERIC]),
        ),
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode in {0, 1}, result.stderr
    unexpected = []
    exceptions = 0
    for diagnostic in json.loads(result.stdout):
        message = diagnostic["message"]
        match = re.search(r"\((\d+) > \d+\)$", message)
        if (
            Path(diagnostic["filename"]).name == "runner_contract.py"
            and message.startswith("`_validate_selected_projection_coherence`")
            and match
            and int(match.group(1)) == 14
        ):
            exceptions += 1
        else:
            unexpected.append(diagnostic)
    assert exceptions == 1
    assert unexpected == []


def test_generic_import_and_branch_boundaries() -> None:
    failures = []
    for path in (*GENERIC, *_sessions()):
        if any(
            module.startswith(
                ("millrace.adapters.codex", "millrace.adapters.millforge")
            )
            for module in _imports(path)
        ):
            failures.append(f"{path.relative_to(ROOT)} imports concrete adapter")
        if path in _sessions() and "adapter.invoke(" in path.read_text():
            failures.append(f"{path.relative_to(ROOT)} invokes synchronously")
        for node in ast.walk(_tree(path)):
            subject = node.pattern if isinstance(node, ast.match_case) else None
            if isinstance(node, (ast.If, ast.IfExp, ast.While)):
                subject = node.test
            if subject and any(
                isinstance(value, ast.Constant)
                and isinstance(value.value, str)
                and value.value.casefold() in BANNED
                for value in ast.walk(subject)
            ):
                failures.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    assert failures == []


def test_facade_keeps_stable_bindings() -> None:
    facade = _tree(FACADE)
    bindings = {
        node.name
        for node in facade.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
    }
    bindings |= {
        target.id
        for node in facade.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }
    for node in facade.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            bindings.update(alias.asname or alias.name for alias in node.names)
    assert FACADE_BINDINGS <= bindings


def test_verified_live_handoff_is_the_only_transport_bundle() -> None:
    paths = _sessions()
    names = {path.stem for path in paths}
    graph = {
        path.stem: {
            module.rsplit(".", 1)[-1]
            for module in _imports(path)
            if module.rsplit(".", 1)[-1] in names
        }
        for path in paths
    }
    rank = {
        "session_persistence": 0,
        "session_completion": 1,
        "session_cancellation": 2,
        "session_reconciliation": 3,
        "session_coordinator": 4,
    }
    for source, targets in graph.items():
        assert source == "session_coordinator" or "session_coordinator" not in targets
        assert all(rank[source] > rank[target] for target in targets if target in rank)
    TopologicalSorter(graph).prepare()
    bundles = []
    for path in _sessions():
        for node in _tree(path).body if path != FACADE else ():
            if isinstance(node, ast.ClassDef):
                fields = [
                    child
                    for child in node.body
                    if isinstance(child, ast.AnnAssign)
                    and isinstance(child.target, ast.Name)
                ]
                if {"session", "request", "handle"} <= {x.target.id for x in fields}:
                    bundles.append(fields)
    assert len(bundles) <= 1
    if (SESSION_ROOT / "session_reconciliation.py").exists():
        assert [field.target.id for field in bundles[0]] == [
            "session", "request", "handle", "deadline"
        ]
        assert all(
            "Callable" not in ast.unparse(field.annotation)
            for field in bundles[0]
        )
