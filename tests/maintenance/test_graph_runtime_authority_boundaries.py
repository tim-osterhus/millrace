from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src" / "millrace_ai"

LEGACY_ROUTER_FUNCTIONS = {
    "build_consultant_escalation",
    "next_execution_step",
    "next_planning_step",
    "route_execution_recovery",
    "route_planning_recovery",
}
BUILTIN_TERMINAL_ENUMS = {
    "ExecutionTerminalResult",
    "LearningTerminalResult",
    "PlanningTerminalResult",
}
BLUEPRINT_OPERATION_RUNNER_FUNCTIONS = {
    "contractor_blueprint_candidate_persist",
    "evaluator_blueprint_approved_to_task",
    "evaluator_blueprint_rejected_to_draft_revision",
    "manager_blueprint_manifest_to_blueprint_drafts",
    "mechanic_blueprint_repair_apply",
}
TESTS_ROOT = REPO_ROOT / "tests"


def _python_files(*roots: Path) -> tuple[Path, ...]:
    return tuple(
        sorted(
            path
            for root in roots
            for path in root.rglob("*.py")
            if "__pycache__" not in path.parts
        )
    )


def _tree(path: Path) -> ast.AST:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _relative(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def _function_node(tree: ast.AST, name: str) -> ast.FunctionDef:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"missing function {name}")


def test_active_runtime_and_compilation_do_not_call_legacy_enum_router() -> None:
    scanned_files = _python_files(SRC_ROOT / "runtime", SRC_ROOT / "compilation")
    violations: list[str] = []

    for path in scanned_files:
        tree = _tree(path)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "millrace_ai.router":
                imported = {alias.name for alias in node.names}
                banned = sorted(imported & LEGACY_ROUTER_FUNCTIONS)
                if banned:
                    violations.append(f"{_relative(path)} imports {', '.join(banned)}")
            if isinstance(node, ast.Call):
                name = _call_name(node.func)
                if name in LEGACY_ROUTER_FUNCTIONS:
                    violations.append(f"{_relative(path)} calls {name}")

    assert violations == []


def test_graph_authority_does_not_cast_results_to_builtin_terminal_enums() -> None:
    scanned_files = _python_files(SRC_ROOT / "runtime" / "graph_authority")
    violations: list[str] = []

    for path in scanned_files:
        tree = _tree(path)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                imported = {alias.name for alias in node.names}
                banned = sorted(imported & BUILTIN_TERMINAL_ENUMS)
                if banned:
                    violations.append(f"{_relative(path)} imports {', '.join(banned)}")
            if isinstance(node, ast.Call):
                name = _call_name(node.func)
                if name in BUILTIN_TERMINAL_ENUMS:
                    violations.append(f"{_relative(path)} casts terminal result via {name}")

    assert violations == []


def test_recovery_counter_mutation_uses_router_decision_metadata_not_destination_stage() -> None:
    tree = _tree(SRC_ROOT / "runtime" / "result_counters.py")
    function = _function_node(tree, "increment_route_counters")
    forbidden_decision_fields = {
        "action",
        "next_node_id",
        "next_plane",
        "next_stage",
        "next_stage_kind_id",
    }
    violations = sorted(
        {
            node.attr
            for node in ast.walk(function)
            if isinstance(node, ast.Attribute)
            and node.attr in forbidden_decision_fields
            and isinstance(node.value, ast.Name)
            and node.value.id == "decision"
        }
    )

    assert violations == []


def test_runtime_effect_lifecycle_intent_comes_from_rule_metadata() -> None:
    tree = _tree(SRC_ROOT / "runtime" / "effect_execution.py")
    function = _function_node(tree, "_source_lifecycle_plan_id_for_effect_rule")
    source = ast.get_source_segment(
        (SRC_ROOT / "runtime" / "effect_execution.py").read_text(encoding="utf-8"),
        function,
    )
    assert source is not None

    assert "effect_rule" in source
    assert "source_completion_lifecycle_mutation_plan" in source
    assert "source_blocking_lifecycle_mutation_plan" in source
    assert "operation_id" not in source
    assert "runner_id" not in source
    assert "handler_id" not in source


def test_stage_kind_assets_declare_runtime_stage_and_required_skills() -> None:
    missing: list[str] = []
    for path in sorted((SRC_ROOT / "assets" / "registry" / "stage_kinds").rglob("*.json")):
        payload = path.read_text(encoding="utf-8")
        if '"runtime_stage"' not in payload:
            missing.append(f"{_relative(path)} missing runtime_stage")
        if '"required_skill_paths"' not in payload:
            missing.append(f"{_relative(path)} missing required_skill_paths")

    assert missing == []


def test_generic_named_tests_do_not_hide_blueprint_only_behavior_surfaces() -> None:
    violations: list[str] = []

    for path in _python_files(TESTS_ROOT):
        if "blueprint" in path.relative_to(TESTS_ROOT).as_posix():
            continue

        tree = _tree(path)
        imported_blueprint_documents: set[str] = set()
        imports_blueprint_state = False
        called_blueprint_operation_runners: set[str] = set()
        test_names: list[str] = []

        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                imported_names = {alias.name for alias in node.names}
                if node.module == "millrace_ai.contracts":
                    imported_blueprint_documents.update(
                        name
                        for name in imported_names
                        if name.startswith("Blueprint") and name.endswith("Document")
                    )
                if node.module == "millrace_ai.workspace.blueprint_state":
                    imports_blueprint_state = True
            elif isinstance(node, ast.Call):
                name = _call_name(node.func)
                if name in BLUEPRINT_OPERATION_RUNNER_FUNCTIONS:
                    called_blueprint_operation_runners.add(name)
            elif isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
                test_names.append(node.name)

        blueprint_named_tests = [
            name for name in test_names if "blueprint" in name.lower()
        ]
        mostly_blueprint_named_tests = bool(test_names) and (
            len(blueprint_named_tests) / len(test_names) >= 0.75
        )

        if called_blueprint_operation_runners:
            violations.append(
                f"{_relative(path)} calls Blueprint operation runners directly: "
                f"{', '.join(sorted(called_blueprint_operation_runners))}"
            )
        if (
            imports_blueprint_state
            and len(imported_blueprint_documents) >= 3
            and mostly_blueprint_named_tests
        ):
            violations.append(
                f"{_relative(path)} imports workspace.blueprint_state and "
                f"{len(imported_blueprint_documents)} Blueprint document contracts "
                "in a Blueprint-dedicated test module"
            )

    assert violations == []


GENERIC_FORMATTER_FILES = {
    "routing.py",
    "generic_router.py",
}
GENERIC_FORMATTER_FUNCTIONS = {
    # routing.py
    "_generic_terminal_reason",
    "_generic_terminal_failure_class",
    "_generic_threshold_failure_class_default",
    "_generic_threshold_reason",
    # generic_router.py
    "execution_terminal_reason",
    "planning_terminal_reason",
    "learning_terminal_reason",
    "execution_terminal_failure_class",
    "planning_terminal_failure_class",
    "learning_terminal_failure_class",
    "_threshold_failure_class_default",
    "_threshold_reason",
    "planning_threshold_reason",
}


def test_generic_formatters_do_not_use_source_stage_value_as_fallback_authority() -> None:
    """
    Guardrail: generic formatter callbacks in ``routing.py`` and
    ``generic_router.py`` must derive fallback route reasons and failure
    classes from ``stage_result.node_id`` (compiled node identity), not
    from ``source_stage.value`` (runtime stage-name string).

    Scans AST for any attribute access ``source_stage.value`` inside the
    body of known generic formatter functions and reports violations.
    """
    graph_authority = SRC_ROOT / "runtime" / "graph_authority"
    violations: list[str] = []

    for path in sorted(graph_authority.rglob("*.py")):
        if path.name not in GENERIC_FORMATTER_FILES:
            continue
        tree = _tree(path)
        relative = _relative(path)
        for func_node in ast.walk(tree):
            if not isinstance(func_node, ast.FunctionDef):
                continue
            if func_node.name not in GENERIC_FORMATTER_FUNCTIONS:
                continue
            # Walk the function body (not decorators) for banned attribute access
            for node in ast.walk(func_node):
                if (
                    isinstance(node, ast.Attribute)
                    and node.attr == "value"
                    and isinstance(node.value, ast.Name)
                    and node.value.id == "source_stage"
                ):
                    violations.append(
                        f"{relative} function {func_node.name} "
                        f"uses source_stage.value at line {node.lineno}"
                    )

    assert violations == [], f"{len(violations)} formatter(s) use source_stage.value:\n" + "\n".join(violations)


BANNED_PER_PLANE_WRAPPER_FUNCTIONS = {
    "route_execution_stage_result_from_graph",
    "route_planning_stage_result_from_graph",
    "route_learning_stage_result_from_graph",
}

BANNED_RECOVERY_KNOB_NAMES = {
    "max_fix_cycles",
    "max_troubleshoot_attempts_before_consult",
    "max_mechanic_attempts",
}

ACTIVE_ROUTING_SURFACE_FILES = (
    SRC_ROOT / "runtime" / "graph_authority" / "routing.py",
    SRC_ROOT / "runtime" / "result_application.py",
)


def test_active_routing_does_not_dispatch_through_per_plane_wrappers() -> None:
    """Active routing must dispatch through ``route_generic_stage_result_from_graph``,
    not through per-plane compatibility wrappers.

    Scans only the active routing entrypoint (``routing.py``) and its active
    runtime call site (``result_application.py``). The compatibility wrapper
    modules themselves (``execution.py``, ``planning.py``, ``learning.py`` in
    ``graph_authority/``) are NOT scanned — they remain compatibility surfaces.
    """
    violations: list[str] = []

    for path in ACTIVE_ROUTING_SURFACE_FILES:
        tree = _tree(path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = _call_name(node.func)
                if name in BANNED_PER_PLANE_WRAPPER_FUNCTIONS:
                    violations.append(
                        f"{_relative(path)} calls {name} at line {node.lineno}"
                    )

    assert violations == [], (
        f"{len(violations)} per-plane wrapper dispatch(es) in active routing:\n"
        + "\n".join(violations)
    )


def test_active_routing_does_not_use_route_time_recovery_knobs() -> None:
    """Active routing must not accept or pass route-time recovery knobs.

    Scans only the active routing surface (``routing.py`` and
    ``result_application.py``) for:

    - keyword arguments at call sites whose name is a banned recovery knob
    - function parameter names that match a banned recovery knob

    Compiler-side threshold-policy materialization paths (e.g.
    ``src/millrace_ai/compilation/``) are NOT scanned.
    """
    violations: list[str] = []

    for path in ACTIVE_ROUTING_SURFACE_FILES:
        tree = _tree(path)
        for node in ast.walk(tree):
            if isinstance(node, ast.keyword) and node.arg in BANNED_RECOVERY_KNOB_NAMES:
                violations.append(
                    f"{_relative(path)} uses route-time kwarg {node.arg!r} at line {node.lineno}"
                )
            if isinstance(node, ast.arg) and node.arg in BANNED_RECOVERY_KNOB_NAMES:
                violations.append(
                    f"{_relative(path)} declares route-time parameter {node.arg!r} at line {node.lineno}"
                )

    assert violations == [], (
        f"{len(violations)} route-time recovery knob reference(s) in active routing:\n"
        + "\n".join(violations)
    )


def _call_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None
