from __future__ import annotations

import ast
from pathlib import Path

SOURCE_ROOT = Path(__file__).resolve().parents[2] / "src"
ADAPTER_ROOT = SOURCE_ROOT / "millrace" / "adapters"

FORBIDDEN_ADAPTER_IMPORT_PREFIXES = (
    "millrace.compiler",
    "millrace.kernel",
    "millrace.operator",
    "millrace.substrate",
    "millrace.testing",
    "millrace.workflows",
)

ALLOWED_ADAPTER_IMPORT_PREFIXES = (
    "millrace.adapters",
    "millrace.contracts.compiled_plan",
    "millrace.contracts.runner",
    "millrace.contracts.schema",
)

CLI_FORBIDDEN_IMPORT_PREFIXES = (
    "millrace.adapters.codex",
    "millrace.adapters.runner_contract",
    "millrace.adapters.subprocess_transport",
    "millrace.testing",
    "millrace.workflows",
)

CLI_ALLOWED_IMPORT_PREFIXES = (
    "millrace.adapters.cli",
    "millrace.compiler",
    "millrace.contracts",
    "millrace.kernel",
    "millrace.operator",
    "millrace.substrate",
)

CLI_REVIEWED_RUNNER_IMPORT_PREFIXES_BY_FILE = {
    # The isolated demo uses the same coordinator and session contract.
    "millrace/adapters/cli/demo.py": ("millrace.adapters.runner_contract",),
    "millrace/adapters/cli/demo_adapter.py": ("millrace.adapters.runner_contract",),
    "millrace/adapters/cli/daemon.py": ("millrace.adapters.runner_contract",),
    "millrace/adapters/cli/session_coordinator.py": (
        "millrace.adapters.runner_contract",
    ),
    "millrace/adapters/cli/session_reconciliation.py": (
        "millrace.adapters.runner_contract",
    ),
    "millrace/adapters/cli/session_cancellation.py": (
        "millrace.adapters.runner_contract",
    ),
    "millrace/adapters/cli/session_diagnostics.py": (
        "millrace.adapters.runner_contract",
    ),
    "millrace/adapters/cli/session_completion.py": (
        "millrace.adapters.runner_contract",
    ),
    "millrace/adapters/cli/session_persistence.py": (
        "millrace.adapters.runner_contract",
    ),
    "millrace/adapters/cli/session_supervision.py": (
        "millrace.adapters.runner_contract",
    ),
    "millrace/adapters/cli/run.py": (
        "millrace.adapters.codex",
        "millrace.adapters.millforge",
        "millrace.adapters.pi_rpc",
        "millrace.adapters.pi_rpc_config",
        "millrace.adapters.runner_contract",
    ),
}

FORBIDDEN_ADAPTER_CALL_NAMES = {
    "__import__",
    "eval",
    "exec",
    "open",
}

FORBIDDEN_ADAPTER_ATTRIBUTE_CALLS = {
    "entry_points",
    "files",
    "import_module",
    "load_entry_point",
    "open",
    "read_bytes",
    "read_text",
    "write_bytes",
    "write_text",
}


def _adapter_python_files(*, source_root: Path = SOURCE_ROOT) -> list[Path]:
    adapter_root = source_root / "millrace" / "adapters"
    return sorted(
        path for path in adapter_root.rglob("*.py") if "__pycache__" not in path.parts
    )


def _is_cli_adapter_file(path: Path, *, source_root: Path = SOURCE_ROOT) -> bool:
    adapter_root = source_root / "millrace" / "adapters"
    return path.relative_to(adapter_root).parts[:1] == ("cli",)


def _runner_adapter_python_files(*, source_root: Path = SOURCE_ROOT) -> list[Path]:
    return [
        path
        for path in _adapter_python_files(source_root=source_root)
        if not _is_cli_adapter_file(path, source_root=source_root)
    ]


def _cli_adapter_python_files(*, source_root: Path = SOURCE_ROOT) -> list[Path]:
    return [
        path
        for path in _adapter_python_files(source_root=source_root)
        if _is_cli_adapter_file(path, source_root=source_root)
    ]


def _module_name_for_path(path: Path, source_root: Path = SOURCE_ROOT) -> str:
    relative = path.relative_to(source_root).with_suffix("")
    parts = list(relative.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _package_name_for_path(path: Path, source_root: Path = SOURCE_ROOT) -> str:
    module_name = _module_name_for_path(path, source_root)
    if path.name == "__init__.py":
        return module_name
    return module_name.rsplit(".", 1)[0]


def _resolve_import_from(
    path: Path,
    node: ast.ImportFrom,
    *,
    source_root: Path = SOURCE_ROOT,
) -> list[str]:
    if node.level == 0:
        base = node.module or ""
    else:
        package_parts = _package_name_for_path(path, source_root).split(".")
        prefix = package_parts[: len(package_parts) - node.level + 1]
        if node.module:
            prefix.extend(node.module.split("."))
        base = ".".join(prefix)
    imports = [base] if base else []
    imports.extend(
        f"{base}.{alias.name}" if base and alias.name != "*" else base
        for alias in node.names
        if alias.name != "*"
    )
    return imports


def _imported_modules(
    path: Path,
    tree: ast.AST,
    *,
    source_root: Path = SOURCE_ROOT,
) -> list[str]:
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            # Pi capacity uses this pure serializer, never transition decisions.
            if (
                path.relative_to(source_root).as_posix()
                == "millrace/adapters/pi_rpc.py"
                and node.level == 0
                and node.module == "millrace.contracts.transition"
                and [alias.name for alias in node.names]
                == ["canonical_authority_mapping_bytes"]
            ):
                continue
            imports.extend(_resolve_import_from(path, node, source_root=source_root))
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "import_module"
            and len(node.args) == 1
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            imports.append(node.args[0].value)
    return imports


def _call_names(
    tree: ast.AST, *, reviewed_calls: set[ast.Call] | None = None
) -> tuple[list[str], list[str]]:
    names: list[str] = []
    attrs: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if reviewed_calls and node in reviewed_calls:
                continue
            if isinstance(node.func, ast.Name):
                names.append(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                attrs.append(node.func.attr)
    return names, attrs


def _reviewed_pi_config_calls(path: Path, tree: ast.AST) -> set[ast.Call]:
    if path.relative_to(SOURCE_ROOT).as_posix() != "millrace/adapters/pi_rpc_config.py":
        return set()
    # These bounded, no-follow opens read pinned config and write owned templates.
    # Ordinary asset discovery and file access elsewhere remain forbidden.
    functions = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "regular_bytes"
    ]
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "PiRpcConfig":
            functions.extend(
                child
                for child in node.body
                if isinstance(child, ast.FunctionDef) and child.name == "materialize"
            )
    return {
        call
        for function in functions
        for call in ast.walk(function)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and isinstance(call.func.value, ast.Name)
        and call.func.value.id == "os"
        and call.func.attr == "open"
    }


def _reviewed_pi_supervisor_calls(path: Path, tree: ast.AST) -> set[ast.Call]:
    if path.relative_to(SOURCE_ROOT).as_posix() != (
        "millrace/adapters/pi_rpc_supervisor.py"
    ):
        return set()
    # Only these kernel process-state reads and stdio detachment belong to the
    # pinned Linux reaper. Other adapter asset/file access remains forbidden.
    allowed = {
        "open(f'/proc/{pid}/task/{pid}/children', encoding='ascii')",
        "open(f'/proc/{pid}/status', encoding='ascii')",
        "os.open(os.devnull, os.O_RDWR)",
    }
    return {
        call
        for call in ast.walk(tree)
        if isinstance(call, ast.Call) and ast.unparse(call) in allowed
    }


def _adapter_import_violations_for_file(
    path: Path,
    *,
    source_root: Path = SOURCE_ROOT,
    forbidden_prefixes: tuple[str, ...] = FORBIDDEN_ADAPTER_IMPORT_PREFIXES,
    allowed_prefixes: tuple[str, ...] = ALLOWED_ADAPTER_IMPORT_PREFIXES,
) -> list[tuple[str, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    violations: list[tuple[str, str]] = []
    for imported_module in _imported_modules(path, tree, source_root=source_root):
        if not imported_module.startswith("millrace."):
            continue
        if imported_module.startswith(forbidden_prefixes):
            violations.append(
                (path.relative_to(source_root).as_posix(), imported_module),
            )
        elif not imported_module.startswith(allowed_prefixes):
            violations.append(
                (path.relative_to(source_root).as_posix(), imported_module),
            )
    return violations


def _cli_import_violations_for_file(
    path: Path,
    *,
    source_root: Path = SOURCE_ROOT,
) -> list[tuple[str, str]]:
    violations = _adapter_import_violations_for_file(
        path,
        source_root=source_root,
        forbidden_prefixes=CLI_FORBIDDEN_IMPORT_PREFIXES,
        allowed_prefixes=CLI_ALLOWED_IMPORT_PREFIXES,
    )
    reviewed_prefixes = CLI_REVIEWED_RUNNER_IMPORT_PREFIXES_BY_FILE.get(
        path.relative_to(source_root).as_posix(),
        (),
    )
    return [
        violation
        for violation in violations
        if not _matches_reviewed_prefix(violation[1], reviewed_prefixes)
    ]


def _matches_reviewed_prefix(
    imported_module: str,
    reviewed_prefixes: tuple[str, ...],
) -> bool:
    return any(
        imported_module == prefix or imported_module.startswith(f"{prefix}.")
        for prefix in reviewed_prefixes
    )


def test_adapter_modules_do_not_import_runtime_authority_packages() -> None:
    violations: list[tuple[str, str]] = []
    for path in _runner_adapter_python_files():
        violations.extend(_adapter_import_violations_for_file(path))

    assert violations == []


def test_adapter_import_guardrail_catches_relative_runtime_authority_imports(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "src"
    adapter_path = source_root / "millrace" / "adapters" / "probe.py"
    adapter_path.parent.mkdir(parents=True)
    adapter_path.write_text(
        "\n".join(
            (
                "from ..contracts.state import RuntimeState",
                "from ..kernel import transition",
                "from ..contracts.runner import RunnerDispatchEnvelope",
            )
        ),
        encoding="utf-8",
    )

    tree = ast.parse(
        adapter_path.read_text(encoding="utf-8"),
        filename=str(adapter_path),
    )
    imported_modules = _imported_modules(
        adapter_path,
        tree,
        source_root=source_root,
    )

    assert "millrace.contracts.state" in imported_modules
    assert "millrace.contracts.state.RuntimeState" in imported_modules
    assert "millrace.kernel.transition" in imported_modules
    assert "millrace.contracts.runner.RunnerDispatchEnvelope" in imported_modules
    assert _adapter_import_violations_for_file(
        adapter_path,
        source_root=source_root,
    ) == [
        ("millrace/adapters/probe.py", "millrace.contracts.state"),
        ("millrace/adapters/probe.py", "millrace.contracts.state.RuntimeState"),
        ("millrace/adapters/probe.py", "millrace.kernel"),
        ("millrace/adapters/probe.py", "millrace.kernel.transition"),
    ]


def test_adapter_modules_do_not_discover_packages_or_touch_asset_files() -> None:
    violations: list[tuple[str, str]] = []
    for path in _runner_adapter_python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        call_names, attribute_calls = _call_names(
            tree,
            reviewed_calls=(
                _reviewed_pi_config_calls(path, tree)
                | _reviewed_pi_supervisor_calls(path, tree)
            ),
        )
        for name in call_names:
            if name in FORBIDDEN_ADAPTER_CALL_NAMES:
                violations.append((path.relative_to(SOURCE_ROOT).as_posix(), name))
        for attr in attribute_calls:
            if attr in FORBIDDEN_ADAPTER_ATTRIBUTE_CALLS:
                violations.append((path.relative_to(SOURCE_ROOT).as_posix(), attr))

    assert violations == []


def test_pi_serializer_exception_does_not_allow_transition_authority(
    tmp_path: Path,
) -> None:
    source = tmp_path / "src"
    path = source / "millrace/adapters/pi_rpc.py"
    path.parent.mkdir(parents=True)
    path.write_text(
        "from millrace.contracts.transition import canonical_authority_mapping_bytes\n"
        "from millrace.contracts.transition import InitializeWorkspace\n"
        "from millrace.contracts.transition import *\n"
    )
    violations = _adapter_import_violations_for_file(path, source_root=source)
    assert (
        "millrace/adapters/pi_rpc.py",
        "millrace.contracts.transition.InitializeWorkspace",
    ) in violations
    assert (
        "millrace/adapters/pi_rpc.py",
        "millrace.contracts.transition",
    ) in violations


def test_pi_config_exception_is_limited_to_owned_config_operations() -> None:
    tree = ast.parse(
        "def regular_bytes():\n    os.open('pinned', 0)\n    open('forbidden')\n"
        "class PiRpcConfig:\n"
        "    def materialize(self):\n        os.open('owned', 0)\n"
        "    def discover(self):\n        os.open('forbidden', 0)\n"
    )
    path = SOURCE_ROOT / "millrace/adapters/pi_rpc_config.py"
    calls, attributes = _call_names(
        tree, reviewed_calls=_reviewed_pi_config_calls(path, tree)
    )
    assert calls == ["open"]
    assert attributes == ["open"]


def test_pi_supervisor_exception_is_limited_to_kernel_process_state() -> None:
    tree = ast.parse(
        "open(f'/proc/{pid}/task/{pid}/children', encoding='ascii')\n"
        "open(f'/proc/{pid}/status', encoding='ascii')\n"
        "os.open(os.devnull, os.O_RDWR)\n"
        "open('asset')\n"
        "os.open('asset', os.O_RDONLY)\n"
    )
    path = SOURCE_ROOT / "millrace/adapters/pi_rpc_supervisor.py"
    calls, attributes = _call_names(
        tree, reviewed_calls=_reviewed_pi_supervisor_calls(path, tree)
    )
    assert calls == ["open"]
    assert attributes == ["open"]


def test_runner_adapters_still_do_not_import_prompt_materializer_or_package_assets(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "src"
    adapter_path = source_root / "millrace" / "adapters" / "probe.py"
    adapter_path.parent.mkdir(parents=True)
    adapter_path.write_text(
        "\n".join(
            (
                "from ..operator.prompt_material import build_selected_asset_material",
                "from ..contracts.workflow_package import asset_digest_for_bytes",
                "from ..substrate.workflow_packages import "
                "load_workflow_package_registry",
            )
        ),
        encoding="utf-8",
    )

    tree = ast.parse(
        adapter_path.read_text(encoding="utf-8"),
        filename=str(adapter_path),
    )
    imported_modules = _imported_modules(
        adapter_path,
        tree,
        source_root=source_root,
    )

    assert "millrace.operator.prompt_material" in imported_modules
    assert (
        "millrace.operator.prompt_material.build_selected_asset_material"
        in imported_modules
    )
    assert "millrace.contracts.workflow_package" in imported_modules
    assert "millrace.substrate.workflow_packages" in imported_modules
    assert _adapter_import_violations_for_file(
        adapter_path,
        source_root=source_root,
    ) == [
        ("millrace/adapters/probe.py", "millrace.operator.prompt_material"),
        (
            "millrace/adapters/probe.py",
            "millrace.operator.prompt_material.build_selected_asset_material",
        ),
        ("millrace/adapters/probe.py", "millrace.contracts.workflow_package"),
        (
            "millrace/adapters/probe.py",
            "millrace.contracts.workflow_package.asset_digest_for_bytes",
        ),
        ("millrace/adapters/probe.py", "millrace.substrate.workflow_packages"),
        (
            "millrace/adapters/probe.py",
            "millrace.substrate.workflow_packages.load_workflow_package_registry",
        ),
    ]


def test_cli_guardrails_distinguish_runner_adapters_from_cli_adapter(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "src"
    runner_path = source_root / "millrace" / "adapters" / "probe.py"
    cli_path = source_root / "millrace" / "adapters" / "cli" / "probe.py"
    bad_cli_path = source_root / "millrace" / "adapters" / "cli" / "bad.py"
    cli_path.parent.mkdir(parents=True)
    runner_path.write_text("import millrace.kernel\n", encoding="utf-8")
    cli_path.write_text("from millrace.adapters.cli import output\n", encoding="utf-8")
    bad_cli_path.write_text(
        "\n".join(
            (
                "import millrace.testing",
                "import millrace.adapters.codex",
                "import millrace.adapters.subprocess_transport",
            )
        ),
        encoding="utf-8",
    )

    runner_files = _runner_adapter_python_files(source_root=source_root)
    cli_files = _cli_adapter_python_files(source_root=source_root)

    assert runner_path in runner_files
    assert cli_path not in runner_files
    assert cli_path in cli_files
    assert _adapter_import_violations_for_file(
        runner_path,
        source_root=source_root,
    ) == [("millrace/adapters/probe.py", "millrace.kernel")]
    assert (
        _cli_import_violations_for_file(
            cli_path,
            source_root=source_root,
        )
        == []
    )
    assert _cli_import_violations_for_file(
        bad_cli_path,
        source_root=source_root,
    ) == [
        ("millrace/adapters/cli/bad.py", "millrace.testing"),
        ("millrace/adapters/cli/bad.py", "millrace.adapters.codex"),
        ("millrace/adapters/cli/bad.py", "millrace.adapters.subprocess_transport"),
    ]


def test_cli_modules_do_not_import_testing_or_runner_adapters() -> None:
    violations: list[tuple[str, str]] = []
    cli_paths = _cli_adapter_python_files()
    assert cli_paths != []

    for path in cli_paths:
        violations.extend(_cli_import_violations_for_file(path))

    assert violations == []
    run_path = ADAPTER_ROOT / "cli" / "run.py"
    run_tree = ast.parse(run_path.read_text(encoding="utf-8"), filename=str(run_path))
    run_imports = _imported_modules(run_path, run_tree)
    assert "millrace.compiler.DEFAULT_SELECTED_RUNNER_ADAPTER_POLICY" in run_imports
    assert not any(
        imported.startswith("millrace.compiler.runner_bindings")
        for imported in run_imports
    )


def test_session_runtime_has_no_synchronous_adapter_invoke_consumer() -> None:
    run_source = (ADAPTER_ROOT / "cli" / "run.py").read_text(encoding="utf-8")
    session_sources = [
        path.read_text(encoding="utf-8")
        for path in (ADAPTER_ROOT / "cli").glob("session_*.py")
    ]
    codex_source = (ADAPTER_ROOT / "codex.py").read_text(encoding="utf-8")
    millforge_source = (ADAPTER_ROOT / "millforge.py").read_text(encoding="utf-8")

    assert "adapter.invoke(" not in run_source
    assert all("adapter.invoke(" not in source for source in session_sources)
    assert "self.invoke(" not in codex_source
    assert "def invoke(" not in millforge_source
    assert "_CompletedMillforgeCompatibilityHandle" not in millforge_source
    assert "temporary_synchronous_compatibility_shim" not in millforge_source


def test_production_modules_do_not_import_testing_or_deterministic_context() -> None:
    violations: list[tuple[str, str]] = []
    production_roots = (
        SOURCE_ROOT / "millrace" / "adapters" / "cli",
        SOURCE_ROOT / "millrace" / "operator",
    )
    for root in production_roots:
        for path in sorted(root.rglob("*.py")):
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))
            for imported_module in _imported_modules(path, tree):
                if imported_module == "millrace.testing" or imported_module.startswith(
                    "millrace.testing."
                ):
                    violations.append(
                        (path.relative_to(SOURCE_ROOT).as_posix(), imported_module)
                    )
            if "deterministic_context" in source:
                violations.append(
                    (path.relative_to(SOURCE_ROOT).as_posix(), "deterministic_context")
                )

    assert violations == []


def test_adapter_file_touch_guardrail_catches_path_open_probe() -> None:
    tree = ast.parse(
        "from pathlib import Path\n"
        "Path('selected_asset.md').open('r')\n"
        "open('selected_asset.md')\n",
    )
    call_names, attribute_calls = _call_names(tree)

    assert "open" in call_names
    assert "open" in attribute_calls


def test_millforge_adapter_may_validate_schema_but_cannot_import_runtime_authority() -> (  # noqa: E501
    None
):
    adapter_path = ADAPTER_ROOT / "millforge.py"
    assert adapter_path.is_file()
    tree = ast.parse(
        adapter_path.read_text(encoding="utf-8"), filename=str(adapter_path)
    )
    imports = _imported_modules(adapter_path, tree)

    assert "millrace.contracts.schema.validate_schema" in imports
    assert _adapter_import_violations_for_file(adapter_path) == []
    assert not any(
        imported.startswith(
            (
                "millrace.kernel",
                "millrace.operator",
                "millrace.substrate",
                "millrace.workflows",
            )
        )
        for imported in imports
    )


def test_millforge_adapter_uses_only_public_optional_millforge_imports() -> None:
    adapter_path = ADAPTER_ROOT / "millforge.py"
    tree = ast.parse(
        adapter_path.read_text(encoding="utf-8"), filename=str(adapter_path)
    )
    imports = _imported_modules(adapter_path, tree)

    assert any(imported == "millforge" for imported in imports)
    assert all(
        not imported.startswith(("millforge.model_backend", "millforge._forge"))
        for imported in imports
    )
    assert not any(
        isinstance(node, (ast.Import, ast.ImportFrom))
        and (
            getattr(node, "module", "") == "millforge"
            or any(alias.name == "millforge" for alias in getattr(node, "names", ()))
        )
        for node in tree.body
    )
    assert set(
        imported
        for imported in imports
        if imported == "millforge" or imported.startswith("millforge.")
    ) == {"millforge", "millforge.pause_control"}
    top_level = ast.Module(
        body=[
            node
            for node in tree.body
            if not isinstance(
                node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
            )
        ],
        type_ignores=[],
    )
    assert not any(
        imported == "millforge" or imported.startswith("millforge.")
        for imported in _imported_modules(adapter_path, top_level)
    )
    source = adapter_path.read_text(encoding="utf-8")
    assert "self._live_runner" not in source
    assert source.count("threading.Thread(") == 1
    assert "Executor" not in source


def test_restart_pin_proof_adds_no_kernel_or_substrate_millforge_branch() -> None:
    forbidden = (
        "millforge",
        "millforge-base",
        "codex",
        "TASK_COMPLETE",
        "WORK_COMPLETE",
        "NEEDS_REVIEW",
    )
    violations: list[tuple[str, str]] = []
    for package in ("kernel", "substrate"):
        package_root = SOURCE_ROOT / "millrace" / package
        for path in sorted(package_root.rglob("*.py")):
            source = path.read_text(encoding="utf-8")
            folded = source.casefold()
            violations.extend(
                (
                    path.relative_to(SOURCE_ROOT).as_posix(),
                    marker,
                )
                for marker in forbidden
                if (
                    marker.casefold() in folded
                    if marker in {"millforge", "millforge-base", "codex"}
                    else marker in source
                )
            )

    assert violations == []
