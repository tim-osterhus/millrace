from __future__ import annotations

import ast
import importlib
import json
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO_ROOT / "src" / "millrace_ai"

GENERIC_SCAN_ROOTS = (
    SRC_ROOT / "assets" / "entrypoints",
    SRC_ROOT / "compilation",
    SRC_ROOT / "cli",
    SRC_ROOT / "doctor",
    SRC_ROOT / "runtime",
    SRC_ROOT / "workspace",
)

SOURCE_ALLOWLIST = {
}

COMPATIBILITY_SHIM_ALLOWLIST = {
    "src/millrace_ai/contracts/blueprint.py",
    "src/millrace_ai/contracts/recovery.py",
    "src/millrace_ai/cli/status/blueprint.py",
    "src/millrace_ai/runtime/context/blueprint.py",
    "src/millrace_ai/runtime/graph_authority/execution.py",
    "src/millrace_ai/runtime/graph_authority/learning.py",
    "src/millrace_ai/runtime/graph_authority/planning.py",
    "src/millrace_ai/workspace/blueprint_state.py",
    "src/millrace_ai/workspace/families/blueprint.py",
    "src/millrace_ai/workspace/state_reconciliation.py",
}

BLUEPRINT_OPERATION_RUNNER_MODULES = {
    "millrace_ai.runtime.effects.operation_runners",
    "millrace_ai.runtime.effects.operation_runners.candidate_evaluation",
    "millrace_ai.runtime.effects.operation_runners.candidate_packet",
    "millrace_ai.runtime.effects.operation_runners.decomposition_manifest",
    "millrace_ai.runtime.effects.operation_runners.repair_application",
}
BLUEPRINT_OPERATION_RUNNER_RELATIVE_MODULES = {
    "candidate_evaluation",
    "candidate_packet",
    "decomposition_manifest",
    "repair_application",
}

DIRECT_DOMAIN_MODULES = {
    "millrace_ai.cli.status.blueprint",
    "millrace_ai.contracts.blueprint",
    "millrace_ai.extensions.builtin.blueprint",
    "millrace_ai.runtime.context.blueprint",
    "millrace_ai.workspace.state_reconciliation",
    "millrace_ai.workspace.blueprint_state",
    "millrace_ai.workspace.families.blueprint",
}
DIRECT_DOMAIN_MODULE_PREFIXES = (
    "millrace_ai.extensions.builtin.blueprint.",
)
BLUEPRINT_STATUS_IMPL_MODULE = "millrace_ai.extensions.builtin.blueprint.status"
DOMAIN_OWNED_EXTENSION_ITEM_KINDS = {
    "doctor_diagnostic",
    "status_projection",
}
DOMAIN_OWNED_FORBIDDEN_IMPLEMENTATION_ROOTS = (
    "millrace_ai.cli",
    "millrace_ai.doctor",
    "millrace_ai.workspace",
)

HARDCODED_AUTHORITY_NAMES = {
    "CUSTOM_PLANNING_STAGES",
    "_LEGACY_COUNTER_IDS",
    "idle_stage_for_no_work",
}

HARDCODED_BLUEPRINT_BRANCH_LITERALS = {
    "blueprint",
    "blueprint.",
    "_blueprint",
    "_blueprint_",
    "blueprint_draft",
    "planning.blueprint_draft.adapter",
}

BLUEPRINT_IMPL_MODULE_PREFIX = "millrace_ai.extensions.builtin.blueprint"

REQUEST_CONTEXT_RESOLVER_FORBIDDEN_STRING_CONSTANTS = {
    "built_in_blueprint_provider_registrations",
}

DOCTOR_RESOLVER_FORBIDDEN_STRING_CONSTANTS = {
    "check_blueprint_manifest_diagnostics",
    "blueprint.manifest.diagnostics",
    "millrace_ai.doctor.workspace_checks",
}


def _is_forbidden_authority_name(name: str) -> bool:
    return name in HARDCODED_AUTHORITY_NAMES


def _is_forbidden_authority_wrapper(name: str) -> bool:
    return any(
        name == forbidden or name.endswith(f"_{forbidden}")
        for forbidden in HARDCODED_AUTHORITY_NAMES
    )


def _is_forbidden_domain_owned_implementation_path(path: str) -> bool:
    return any(
        path == root or path.startswith(f"{root}.")
        for root in DOMAIN_OWNED_FORBIDDEN_IMPLEMENTATION_ROOTS
    )


def _python_files(*roots: Path) -> tuple[Path, ...]:
    return tuple(
        sorted(
            path
            for root in roots
            for path in root.rglob("*.py")
            if "__pycache__" not in path.parts
        )
    )


def _relative(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def _tree(path: Path) -> ast.AST:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _package_name(path: Path) -> str:
    relative = path.relative_to(SRC_ROOT).with_suffix("")
    parts = list(relative.parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    elif parts:
        parts.pop()
    return ".".join(("millrace_ai", *parts))


def _resolved_import_from(path: Path, node: ast.ImportFrom) -> str:
    if node.level == 0:
        return node.module or ""

    package_parts = _package_name(path).split(".")
    if node.level > 1:
        package_parts = package_parts[: max(len(package_parts) - (node.level - 1), 0)]
    if node.module:
        return ".".join((*package_parts, node.module))
    return ".".join(package_parts)


def _import_aliases(tree: ast.AST) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for alias in node.names:
                local_name = alias.asname or alias.name
                aliases[local_name] = (
                    f"{module}.{alias.name}" if module else alias.name
                )
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname is None:
                    continue
                aliases[alias.asname] = alias.name
    return aliases


def _qualified_name(node: ast.AST, aliases: dict[str, str]) -> str | None:
    if isinstance(node, ast.Name):
        return aliases.get(node.id, node.id)
    if isinstance(node, ast.Attribute):
        parent = _qualified_name(node.value, aliases)
        if parent is None:
            return None
        return f"{parent}.{node.attr}"
    return None


def _is_allowed_source(path: Path) -> bool:
    rel = _relative(path)
    if rel in SOURCE_ALLOWLIST or rel in COMPATIBILITY_SHIM_ALLOWLIST:
        return True
    if rel.startswith("src/millrace_ai/assets/registry/"):
        return True
    return False


def test_no_explicit_non_graph_authority_symbols_in_generic_source() -> None:
    violations: list[str] = []

    for path in _python_files(*GENERIC_SCAN_ROOTS):
        if _is_allowed_source(path):
            continue
        tree = _tree(path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and _is_forbidden_authority_name(node.id):
                violations.append(f"{_relative(path)} references {node.id} at line {node.lineno}")
            elif isinstance(node, ast.Attribute) and _is_forbidden_authority_name(node.attr):
                violations.append(
                    f"{_relative(path)} references {node.attr} at line {node.lineno}"
                )
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and _is_forbidden_authority_wrapper(node.name):
                violations.append(f"{_relative(path)} defines {node.name} at line {node.lineno}")

    assert violations == []


def test_no_blueprint_string_membership_or_blueprint_draft_branches_in_generic_source() -> None:
    violations: list[str] = []

    for path in _python_files(*GENERIC_SCAN_ROOTS):
        if _is_allowed_source(path):
            continue
        tree = _tree(path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Compare):
                literals = {
                    value
                    for child in (node.left, *node.comparators)
                    if isinstance(child, ast.Constant)
                    and isinstance((value := child.value), str)
                }
                if literals & HARDCODED_BLUEPRINT_BRANCH_LITERALS:
                    violations.append(
                        f"{_relative(path)} compares against "
                        f"{', '.join(sorted(literals & HARDCODED_BLUEPRINT_BRANCH_LITERALS))} "
                        f"at line {node.lineno}"
                    )
            elif (
                isinstance(node, ast.If)
                and isinstance(node.test, ast.Compare)
                and isinstance(node.test.left, ast.Constant)
                and node.test.left.value == "blueprint"
                and any(isinstance(op, ast.In) for op in node.test.ops)
            ):
                violations.append(
                    f"{_relative(path)} branches on literal blueprint membership "
                    f"at line {node.lineno}"
                )
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                if node.value == "blueprint_draft":
                    violations.append(
                        f"{_relative(path)} hardcodes blueprint_draft literal at line {node.lineno}"
                    )

    assert violations == []


def test_no_runtime_effect_blueprint_spelling_heuristics_in_generic_source() -> None:
    violations: list[str] = []
    path = SRC_ROOT / "runtime" / "effect_execution.py"

    tree = _tree(path)
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            literals = {
                value
                for child in (node.left, *node.comparators)
                if isinstance(child, ast.Constant)
                and isinstance((value := child.value), str)
            }
            banned = literals & {"_blueprint_", "_blueprint"}
            if banned:
                violations.append(
                    f"{_relative(path)} uses Blueprint spelling heuristic "
                    f"{', '.join(sorted(banned))} at line {node.lineno}"
                )
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in {"endswith", "startswith"}
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value in {"_blueprint", "_blueprint_", "blueprint."}
        ):
            violations.append(
                f"{_relative(path)} uses {node.func.attr} Blueprint spelling heuristic "
                f"{node.args[0].value!r} at line {node.lineno}"
            )

    assert violations == []


def test_no_runtime_effect_blueprint_package_filter_in_generic_source() -> None:
    violations: list[str] = []
    path = SRC_ROOT / "runtime" / "effect_execution.py"

    tree = _tree(path)
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == "_BLUEPRINT_RUNTIME_EFFECT_PACKAGE_ID":
            violations.append(
                f"{_relative(path)} references {node.id} at line {node.lineno}"
            )
        elif isinstance(node, ast.Constant) and node.value == "millrace.blueprint":
            violations.append(
                f"{_relative(path)} hardcodes millrace.blueprint at line {node.lineno}"
            )

    assert violations == []


def test_no_request_context_blueprint_prefix_provider_loading_in_generic_source() -> None:
    violations: list[str] = []
    path = SRC_ROOT / "runtime" / "context" / "providers.py"

    tree = _tree(path)
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "startswith"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and node.args[0].value == "blueprint."
        ):
            violations.append(
                f"{_relative(path)} loads provider implementation from blueprint. prefix "
                f"at line {node.lineno}"
            )

    assert violations == []


def test_request_context_provider_resolver_has_no_blueprint_only_authority_symbols() -> None:
    violations: list[str] = []
    path = SRC_ROOT / "runtime" / "context" / "providers.py"

    tree = _tree(path)
    aliases = _import_aliases(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "") == "millrace_ai.extensions":
            imported = {alias.name for alias in node.names}
            if "ExtensionDomain" in imported:
                violations.append(
                    f"{_relative(path)} imports ExtensionDomain at line {node.lineno}"
                )
        elif isinstance(node, ast.Attribute):
            qualified_name = _qualified_name(node, aliases)
            if qualified_name and qualified_name.endswith("ExtensionDomain.BLUEPRINT"):
                violations.append(
                    f"{_relative(path)} keys ownership to ExtensionDomain.BLUEPRINT "
                    f"at line {node.lineno}"
                )
        elif isinstance(node, ast.Name) and "blueprint" in node.id.lower():
            violations.append(
                f"{_relative(path)} references Blueprint-only resolver symbol "
                f"{node.id} at line {node.lineno}"
            )
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and "blueprint" in node.name.lower():
            violations.append(
                f"{_relative(path)} defines Blueprint-only resolver symbol "
                f"{node.name} at line {node.lineno}"
            )
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node.value in REQUEST_CONTEXT_RESOLVER_FORBIDDEN_STRING_CONSTANTS
        ):
            violations.append(
                f"{_relative(path)} hardcodes request-context resolver hook "
                f"{node.value} at line {node.lineno}"
            )

    assert violations == []


def test_retained_recovery_counter_compatibility_shim_is_documented() -> None:
    rel = "src/millrace_ai/contracts/recovery.py"
    assert rel in COMPATIBILITY_SHIM_ALLOWLIST
    for docs_rel in (
        "docs/source-package-map.md",
        "docs/maintenance/public-api-compatibility-inventory.md",
    ):
        text = (REPO_ROOT / docs_rel).read_text(encoding="utf-8")
        assert rel in text, f"{docs_rel} is missing the retained shim entry for {rel}"


@pytest.mark.parametrize(
    "rel",
    [
        "src/millrace_ai/cli/status/blueprint.py",
        "src/millrace_ai/workspace/blueprint_state.py",
    ],
)
def test_retained_blueprint_status_and_doctor_facades_are_documented(rel: str) -> None:
    for docs_rel in (
        "docs/adr/0016-extension-boundary-compatibility-facades.md",
        "docs/maintenance/public-api-compatibility-inventory.md",
        "docs/source-package-map.md",
    ):
        text = (REPO_ROOT / docs_rel).read_text(encoding="utf-8")
        assert rel in text, f"{docs_rel} is missing the retained facade entry for {rel}"


def test_generic_paths_do_not_import_domain_implementation_modules_directly() -> None:
    violations: list[str] = []

    for path in _python_files(*GENERIC_SCAN_ROOTS):
        if _is_allowed_source(path):
            continue
        tree = _tree(path)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = _resolved_import_from(path, node)
                if module in DIRECT_DOMAIN_MODULES or module.startswith(
                    DIRECT_DOMAIN_MODULE_PREFIXES
                ):
                    violations.append(
                        f"{_relative(path)} imports {module} at line {node.lineno}"
                    )
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    module = alias.name
                    if module in DIRECT_DOMAIN_MODULES or module.startswith(
                        DIRECT_DOMAIN_MODULE_PREFIXES
                    ):
                        violations.append(
                            f"{_relative(path)} imports {module} at line {node.lineno}"
                        )

    assert violations == []


def test_generic_status_collection_and_rendering_do_not_call_blueprint_status_api() -> None:
    violations: list[str] = []

    for rel in (
        "src/millrace_ai/cli/status/collection.py",
        "src/millrace_ai/cli/status/projections.py",
        "src/millrace_ai/cli/status/rendering.py",
    ):
        path = REPO_ROOT / rel
        tree = _tree(path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and node.value == "millrace_ai.cli.status.blueprint":
                violations.append(f"{rel} hardcodes blueprint status module at line {node.lineno}")
            elif isinstance(node, ast.Attribute) and node.attr in {
                "collect_blueprint_status",
                "render_blueprint_status_lines",
            }:
                violations.append(f"{rel} calls {node.attr} at line {node.lineno}")
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "getattr"
                and len(node.args) >= 2
                and isinstance(node.args[1], ast.Constant)
                and node.args[1].value in {
                    "collect_blueprint_status",
                    "render_blueprint_status_lines",
                }
            ):
                violations.append(
                    f"{rel} routes blueprint status via getattr at line {node.lineno}"
                )

    assert violations == []


def test_generic_status_projection_defaults_are_not_blueprint_branches() -> None:
    violations: list[str] = []
    rel = "src/millrace_ai/cli/status/projections.py"
    path = REPO_ROOT / rel
    tree = _tree(path)

    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            literals = {
                value
                for child in (node.left, *node.comparators)
                if isinstance(child, ast.Constant)
                and isinstance((value := child.value), str)
            }
            if "blueprints" in literals:
                violations.append(
                    f"{rel} branches on blueprints projection id at line {node.lineno}"
                )
        elif isinstance(node, ast.Constant) and node.value == "blueprints":
            violations.append(f"{rel} hardcodes blueprints at line {node.lineno}")

    assert violations == []


def test_generic_builtin_family_adapters_do_not_register_blueprint_draft_directly() -> None:
    violations: list[str] = []
    path = SRC_ROOT / "workspace" / "families" / "builtin.py"

    tree = _tree(path)
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == "BLUEPRINT_DRAFT":
            violations.append(
                f"{_relative(path)} references WorkItemKind.BLUEPRINT_DRAFT at line {node.lineno}"
            )
        elif (
            isinstance(node, ast.Constant)
            and node.value == "planning.blueprint_draft.adapter"
        ):
            violations.append(
                f"{_relative(path)} hardcodes planning.blueprint_draft.adapter "
                f"at line {node.lineno}"
            )

    assert violations == []


def test_default_doctor_checks_do_not_register_blueprint_diagnostics_directly() -> None:
    violations: list[str] = []
    path = SRC_ROOT / "doctor" / "checks.py"

    tree = _tree(path)
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == "check_blueprint_manifest_diagnostics":
            violations.append(
                f"{_relative(path)} registers check_blueprint_manifest_diagnostics "
                f"at line {node.lineno}"
            )
        elif isinstance(node, ast.Attribute) and node.attr == "check_blueprint_manifest_diagnostics":
            violations.append(
                f"{_relative(path)} references check_blueprint_manifest_diagnostics "
                f"through an attribute at line {node.lineno}"
            )
        elif (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and node.value in DOCTOR_RESOLVER_FORBIDDEN_STRING_CONSTANTS
        ):
            violations.append(
                f"{_relative(path)} hardcodes doctor routing string {node.value} "
                f"at line {node.lineno}"
            )
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "getattr"
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and node.args[1].value == "check_blueprint_manifest_diagnostics"
        ):
            violations.append(
                f"{_relative(path)} routes blueprint diagnostics via getattr "
                f"at line {node.lineno}"
            )

    assert violations == []


def test_blueprint_manifest_doctor_diagnostic_is_extension_owned() -> None:
    manifest_path = SRC_ROOT / "assets" / "registry" / "extensions" / "millrace_blueprint.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    diagnostics = [
        item
        for item in payload["items"]
        if item.get("item_kind") == "doctor_diagnostic"
        and item.get("item_id") == "blueprint.manifest.diagnostics"
    ]

    assert diagnostics == [
        {
            "item_id": "blueprint.manifest.diagnostics",
            "item_kind": "doctor_diagnostic",
            "implementation_path": "millrace_ai.extensions.builtin.blueprint.doctor",
            "version": "1.0.0",
        }
    ]


def test_blueprint_manifest_status_projection_is_extension_owned() -> None:
    manifest_path = SRC_ROOT / "assets" / "registry" / "extensions" / "millrace_blueprint.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    projections = [
        item
        for item in payload["items"]
        if item.get("item_kind") == "status_projection"
        and item.get("item_id") == "blueprints"
    ]

    assert projections == [
        {
            "item_id": "blueprints",
            "item_kind": "status_projection",
            "implementation_path": BLUEPRINT_STATUS_IMPL_MODULE,
            "version": "1.0.0",
        }
    ]


def test_domain_owned_doctor_and_status_manifest_items_do_not_point_to_generic_modules() -> None:
    violations: list[str] = []
    manifest_root = SRC_ROOT / "assets" / "registry" / "extensions"

    for manifest_path in sorted(manifest_root.glob("*.json")):
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        domain = payload.get("domain")
        if domain == "generic":
            continue
        package_id = payload.get("package_id", manifest_path.name)
        for item in payload.get("items", []):
            item_kind = item.get("item_kind")
            if item_kind not in DOMAIN_OWNED_EXTENSION_ITEM_KINDS:
                continue
            implementation_path = item.get("implementation_path")
            if not isinstance(implementation_path, str):
                continue
            if _is_forbidden_domain_owned_implementation_path(implementation_path):
                violations.append(
                    f"{manifest_path.name} {package_id}:{item.get('item_id')} "
                    f"{item_kind} points to generic implementation path "
                    f"{implementation_path}"
                )

    assert violations == []


@pytest.mark.parametrize(
    ("implementation_path", "expected"),
    [
        ("millrace_ai.cli", True),
        ("millrace_ai.cli.status", True),
        ("millrace_ai.doctor", True),
        ("millrace_ai.doctor.checks", True),
        ("millrace_ai.workspace", True),
        ("millrace_ai.workspace.blueprint_state", True),
        ("millrace_ai.extensions.builtin.blueprint.doctor", False),
        ("millrace_ai.extensions.builtin.blueprint.status", False),
    ],
)
def test_forbidden_domain_owned_implementation_path_helper_matches_package_roots_and_children(
    implementation_path: str,
    expected: bool,
) -> None:
    assert _is_forbidden_domain_owned_implementation_path(implementation_path) is expected


def test_runtime_effect_registry_initialization_does_not_import_blueprint_runners_directly() -> None:
    violations: list[str] = []

    for rel in (
        "src/millrace_ai/runtime/effects/legacy.py",
        "src/millrace_ai/runtime/effects/registry.py",
        "src/millrace_ai/runtime/effects/operation_runners/__init__.py",
    ):
        path = REPO_ROOT / rel
        tree = _tree(path)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "") in BLUEPRINT_OPERATION_RUNNER_MODULES:
                violations.append(f"{rel} imports {node.module} at line {node.lineno}")
            elif (
                isinstance(node, ast.ImportFrom)
                and rel.endswith("runtime/effects/operation_runners/__init__.py")
                and node.level == 1
            ):
                imported = {alias.name for alias in node.names}
                banned = sorted(imported & BLUEPRINT_OPERATION_RUNNER_RELATIVE_MODULES)
                if banned:
                    violations.append(
                        f"{rel} imports {', '.join(banned)} at line {node.lineno}"
                    )
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in BLUEPRINT_OPERATION_RUNNER_MODULES:
                        violations.append(f"{rel} imports {alias.name} at line {node.lineno}")

    assert violations == []


@contextmanager
def _isolated_millrace_modules() -> Iterator[None]:
    preserved = {
        name: sys.modules[name]
        for name in list(sys.modules)
        if name == "millrace_ai" or name.startswith("millrace_ai.")
    }
    for name in preserved:
        del sys.modules[name]
    try:
        yield
    finally:
        for name in list(sys.modules):
            if name == "millrace_ai" or name.startswith("millrace_ai."):
                del sys.modules[name]
        sys.modules.update(preserved)


def _loaded_blueprint_impl_modules() -> list[str]:
    return sorted(
        name for name in sys.modules if name.startswith(BLUEPRINT_IMPL_MODULE_PREFIX)
    )


@pytest.mark.parametrize(
    "module_name",
    [
        "millrace_ai.cli",
        "millrace_ai.cli.commands.compile",
        "millrace_ai.cli.commands.queue",
        "millrace_ai.cli.commands.status",
        "millrace_ai.cli.status.collection",
        "millrace_ai.cli.status.rendering",
        "millrace_ai.doctor",
        "millrace_ai.doctor.queue_checks",
        "millrace_ai.contracts",
        "millrace_ai.runtime.context.providers",
        "millrace_ai.runtime.effect_execution",
        "millrace_ai.workspace.family_adapters",
    ],
)
def test_generic_import_surfaces_do_not_load_blueprint_impl_modules(
    module_name: str,
) -> None:
    with _isolated_millrace_modules():
        importlib.import_module(module_name)
        assert _loaded_blueprint_impl_modules() == [], (
            f"{module_name} loaded blueprint implementation modules: "
            f"{_loaded_blueprint_impl_modules()}"
        )


def test_generic_status_collection_uses_extension_owned_blueprint_defaults(
    tmp_path: Path,
) -> None:
    from millrace_ai.paths import bootstrap_workspace, workspace_paths

    workspace_root = tmp_path / "workspace"
    bootstrap_workspace(workspace_paths(workspace_root))

    with _isolated_millrace_modules():
        paths_module = importlib.import_module("millrace_ai.paths")
        paths = paths_module.workspace_paths(workspace_root)
        collection_module = importlib.import_module("millrace_ai.cli.status.collection")
        view_model = collection_module.collect_status_view_model(paths)

        assert BLUEPRINT_STATUS_IMPL_MODULE in _loaded_blueprint_impl_modules()
        assert view_model.blueprint_status["draft_counts"] == {
            "queue": 0,
            "active": 0,
            "blocked": 0,
            "approved": 0,
            "canceled": 0,
            "superseded": 0,
        }


def test_blueprint_status_projection_reads_operator_json_from_extension_boundary(
    tmp_path: Path,
) -> None:
    from millrace_ai.paths import bootstrap_workspace, workspace_paths

    workspace_root = tmp_path / "workspace"
    bootstrap_workspace(workspace_paths(workspace_root))
    runtime_root = workspace_root / "millrace-agents"
    (runtime_root / "blueprints" / "drafts" / "active").mkdir(parents=True)
    (runtime_root / "blueprints" / "packets" / "candidates").mkdir(parents=True)
    (runtime_root / "blueprints" / "critiques" / "open").mkdir(parents=True)
    (runtime_root / "blueprints" / "evaluations").mkdir(parents=True)
    (runtime_root / "blueprints" / "promotions").mkdir(parents=True)
    (runtime_root / "blueprints" / "drafts" / "active" / "draft-custom-001.json").write_text(
        json.dumps(
            {
                "draft_id": "draft-custom-001",
                "root_spec_id": "spec-custom-001",
                "draft_index": 1,
                "current_revision": 1,
                "latest_blueprint_id": "blueprint-draft-custom-001-r1",
                "latest_critique_id": "critique-custom-001",
            }
        ),
        encoding="utf-8",
    )
    (
        runtime_root
        / "blueprints"
        / "packets"
        / "candidates"
        / "blueprint-draft-custom-001-r1.json"
    ).write_text(
        json.dumps(
            {
                "blueprint_id": "blueprint-draft-custom-001-r1",
                "draft_id": "draft-custom-001",
                "root_spec_id": "spec-custom-001",
                "revision": 1,
            }
        ),
        encoding="utf-8",
    )
    (runtime_root / "blueprints" / "critiques" / "open" / "critique-custom-001.json").write_text(
        json.dumps(
            {
                "critique_id": "critique-custom-001",
                "blueprint_id": "blueprint-draft-custom-001-r1",
                "draft_id": "draft-custom-001",
                "root_spec_id": "spec-custom-001",
            }
        ),
        encoding="utf-8",
    )
    (runtime_root / "blueprints" / "evaluations" / "evaluation-custom-001.json").write_text(
        json.dumps(
            {
                "evaluation_id": "evaluation-custom-001",
                "decision": "approved",
                "blueprint_id": "blueprint-draft-custom-001-r1",
                "draft_id": "draft-custom-001",
                "root_spec_id": "spec-custom-001",
                "critique_id": "critique-custom-001",
            }
        ),
        encoding="utf-8",
    )
    (runtime_root / "blueprints" / "promotions" / "promotion-custom-001.json").write_text(
        json.dumps(
            {
                "promotion_id": "promotion-custom-001",
                "blueprint_id": "blueprint-draft-custom-001-r1",
                "evaluation_id": "evaluation-custom-001",
                "draft_id": "draft-custom-001",
                "root_spec_id": "spec-custom-001",
                "generated_task_id": "task-custom-001",
                "generated_task_path": "millrace-agents/tasks/queue/task-custom-001.md",
            }
        ),
        encoding="utf-8",
    )

    with _isolated_millrace_modules():
        paths_module = importlib.import_module("millrace_ai.paths")
        status_module = importlib.import_module(BLUEPRINT_STATUS_IMPL_MODULE)
        paths = paths_module.workspace_paths(workspace_root)
        status = status_module.collect_blueprint_status(
            paths,
            active_mode_id="blueprint_codex",
            persisted_mode_id=None,
        )

        assert BLUEPRINT_STATUS_IMPL_MODULE in _loaded_blueprint_impl_modules()
        assert status["draft_counts"]["active"] == 1
        assert status["drafts"][0]["latest_critique_id"] == "critique-custom-001"
        assert status["packets"][0]["blueprint_id"] == "blueprint-draft-custom-001-r1"
        assert status["promotions"][0]["generated_task_id"] == "task-custom-001"
