from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = PROJECT_ROOT / "src"
PACKAGE_ROOT = SOURCE_ROOT / "millrace"

PACKAGE_DEPENDENCY_MATRIX: dict[str, tuple[str, ...]] = {
    "__root__": ("millrace",),
    "contracts": ("millrace.contracts",),
    "kernel": ("millrace.contracts", "millrace.kernel"),
    "compiler": ("millrace.contracts", "millrace.compiler"),
    "substrate": (
        "millrace.contracts",
        "millrace.substrate",
    ),
    "workflows": ("millrace.contracts", "millrace.workflows"),
    "adapters": (
        "millrace.contracts",
        "millrace.adapters",
        "millrace.compiler",
        "millrace.kernel",
        "millrace.operator",
        "millrace.substrate",
    ),
    "operator": (
        "millrace.contracts",
        "millrace.kernel",
        "millrace.compiler",
        "millrace.substrate",
        "millrace.operator",
    ),
    "extensions": ("millrace.contracts", "millrace.extensions"),
    "testing": (
        "millrace",
        "millrace.adapters",
        "millrace.compiler",
        "millrace.contracts",
        "millrace.extensions",
        "millrace.kernel",
        "millrace.operator",
        "millrace.substrate",
        "millrace.testing",
        "millrace.workflows",
    ),
}

SUBSTRATE_KERNEL_POLICY_MODULES = (
    "millrace.kernel.fanout_policy",
    "millrace.kernel.join_policy",
    "millrace.kernel.observation_policy",
)
SUBSTRATE_KERNEL_POLICY_CONSUMER = Path(
    "millrace/substrate/_sqlite_relations.py"
)
REMOVED_DONOR_MODULE_NAMES = (
    "lad_execution",
    "lad_learning",
    "lad_planning",
    "simple_loop",
    "vendor_selection",
)


@dataclass(frozen=True, slots=True)
class ImportViolation:
    package_name: str
    path: Path
    imported_module: str


def _assert_millrace_package_root(source_root: Path) -> Path:
    package_root = source_root / "millrace"
    assert package_root.exists(), f"missing package root: {package_root}"
    return package_root


def _package_names(source_root: Path) -> list[str]:
    package_root = _assert_millrace_package_root(source_root)
    return sorted(
        path.name
        for path in package_root.iterdir()
        if path.is_dir() and (path / "__init__.py").exists()
    )


def _python_files(source_root: Path, package_name: str) -> list[Path]:
    if package_name == "__root__":
        package_init = _assert_millrace_package_root(source_root) / "__init__.py"
        assert package_init.exists(), f"missing package root marker: {package_init}"
        return [package_init]
    package_path = _assert_millrace_package_root(source_root) / package_name
    assert package_path.exists(), f"missing package: {package_path}"
    return sorted(package_path.rglob("*.py"))


def _module_parts_for(source_root: Path, path: Path) -> list[str]:
    relative = path.relative_to(source_root).with_suffix("")
    parts = list(relative.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return parts


def _package_context_for(source_root: Path, path: Path) -> list[str]:
    parts = _module_parts_for(source_root, path)
    if path.name == "__init__.py":
        return parts
    return parts[:-1]


def _resolve_import_from(
    source_root: Path, path: Path, node: ast.ImportFrom
) -> set[str]:
    if node.level == 0:
        base = node.module or ""
    else:
        context = _package_context_for(source_root, path)
        prefix = context[: len(context) - node.level + 1]
        if node.module:
            prefix.extend(node.module.split("."))
        base = ".".join(prefix)

    names = {base} if base else set()
    for alias in node.names:
        if alias.name != "*":
            names.add(f"{base}.{alias.name}" if base else alias.name)
    return names


def _imported_modules(source_root: Path, path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.update(_resolve_import_from(source_root, path, node))
    return imports


def test_tests_do_not_import_removed_workflow_donors_or_helpers() -> None:
    offenders: list[tuple[str, str]] = []
    for path in sorted((PROJECT_ROOT / "tests").rglob("*.py")):
        for imported_module in sorted(_imported_modules(PROJECT_ROOT, path)):
            if any(
                imported_module.startswith(prefix)
                for donor_name in REMOVED_DONOR_MODULE_NAMES
                for prefix in (
                    f"millrace.workflows.{donor_name}",
                    f"support.{donor_name}",
                    f"tests.support.{donor_name}",
                )
            ):
                offenders.append(
                    (path.relative_to(PROJECT_ROOT).as_posix(), imported_module)
                )

    assert offenders == []


def _is_millrace_import(module_name: str) -> bool:
    return module_name == "millrace" or module_name.startswith("millrace.")


def _is_allowed_import(module_name: str, allowed_prefixes: tuple[str, ...]) -> bool:
    for prefix in allowed_prefixes:
        if module_name == prefix:
            return True
        if prefix != "millrace" and module_name.startswith(f"{prefix}."):
            return True
    return False


def _is_allowed_path_specific_import(
    package_name: str,
    path: Path,
    module_name: str,
) -> bool:
    return (
        package_name == "substrate"
        and path == SUBSTRATE_KERNEL_POLICY_CONSUMER
        and any(
            module_name == policy_module
            or module_name.startswith(f"{policy_module}.")
            for policy_module in SUBSTRATE_KERNEL_POLICY_MODULES
        )
    )


def _dependency_matrix_violations(source_root: Path) -> list[ImportViolation]:
    violations: list[ImportViolation] = []
    for package_name in ["__root__", *_package_names(source_root)]:
        assert package_name in PACKAGE_DEPENDENCY_MATRIX, (
            f"missing dependency matrix entry for package: {package_name}"
        )
        allowed_prefixes = PACKAGE_DEPENDENCY_MATRIX[package_name]
        for path in _python_files(source_root, package_name):
            relative_path = path.relative_to(source_root)
            for imported_module in sorted(_imported_modules(source_root, path)):
                if not _is_millrace_import(imported_module):
                    continue
                if _is_allowed_import(imported_module, allowed_prefixes):
                    continue
                if _is_allowed_path_specific_import(
                    package_name,
                    relative_path,
                    imported_module,
                ):
                    continue
                violations.append(
                    ImportViolation(
                        package_name=package_name,
                        path=relative_path,
                        imported_module=imported_module,
                    )
                )
    return violations


def _write_probe_packages(
    tmp_path: Path,
    package_sources: dict[str, str],
    *,
    root_source: str = "",
) -> Path:
    source_root = tmp_path / "src"
    package_root = source_root / "millrace"
    package_root.mkdir(parents=True)
    (package_root / "__init__.py").write_text(root_source, encoding="utf-8")
    for package_name, source in package_sources.items():
        package_path = package_root / package_name
        package_path.mkdir()
        (package_path / "__init__.py").write_text("", encoding="utf-8")
        (package_path / "probe.py").write_text(source, encoding="utf-8")
    return source_root


def test_dependency_matrix_rejects_representative_forbidden_imports(
    tmp_path: Path,
) -> None:
    source_root = _write_probe_packages(
        tmp_path,
        {
            "kernel": "import millrace.substrate\nimport millrace.compiler\n",
            "compiler": "import millrace.kernel\n",
            "contracts": "import millrace.kernel\n",
            "substrate": (
                "import millrace.kernel.ports\n"
                "import millrace.workflows\n"
            ),
        },
        root_source="import millrace.substrate\n",
    )

    violations = _dependency_matrix_violations(source_root)
    violation_keys = {
        (violation.package_name, violation.imported_module)
        for violation in violations
    }

    assert ("kernel", "millrace.substrate") in violation_keys
    assert ("kernel", "millrace.compiler") in violation_keys
    assert ("compiler", "millrace.kernel") in violation_keys
    assert ("contracts", "millrace.kernel") in violation_keys
    assert ("substrate", "millrace.kernel.ports") in violation_keys
    assert ("substrate", "millrace.workflows") in violation_keys
    assert ("__root__", "millrace.substrate") in violation_keys


def test_substrate_join_policy_import_is_forbidden_outside_sqlite_relations(
    tmp_path: Path,
) -> None:
    source_root = _write_probe_packages(
        tmp_path,
        {"substrate": "import millrace.kernel.join_policy\n"},
    )

    violations = _dependency_matrix_violations(source_root)

    assert ImportViolation(
        package_name="substrate",
        path=Path("millrace/substrate/probe.py"),
        imported_module="millrace.kernel.join_policy",
    ) in violations


def test_substrate_fanout_policy_import_is_forbidden_outside_sqlite_relations(
    tmp_path: Path,
) -> None:
    source_root = _write_probe_packages(
        tmp_path,
        {"substrate": "import millrace.kernel.fanout_policy\n"},
    )

    violations = _dependency_matrix_violations(source_root)

    assert ImportViolation(
        package_name="substrate",
        path=Path("millrace/substrate/probe.py"),
        imported_module="millrace.kernel.fanout_policy",
    ) in violations


def test_substrate_observation_policy_import_is_forbidden_outside_sqlite_relations(
    tmp_path: Path,
) -> None:
    source_root = _write_probe_packages(
        tmp_path,
        {"substrate": "import millrace.kernel.observation_policy\n"},
    )

    violations = _dependency_matrix_violations(source_root)

    assert ImportViolation(
        package_name="substrate",
        path=Path("millrace/substrate/probe.py"),
        imported_module="millrace.kernel.observation_policy",
    ) in violations


def test_dependency_matrix_rejects_root_import_bypass(tmp_path: Path) -> None:
    source_root = _write_probe_packages(
        tmp_path,
        {
            "kernel": "import millrace\n",
            "compiler": "import millrace\n",
            "contracts": "import millrace\n",
        },
    )

    violations = _dependency_matrix_violations(source_root)
    violation_keys = {
        (violation.package_name, violation.imported_module)
        for violation in violations
    }

    assert ("kernel", "millrace") in violation_keys
    assert ("compiler", "millrace") in violation_keys
    assert ("contracts", "millrace") in violation_keys


def test_dependency_matrix_rejects_compiler_hosted_workflow_fixture_import(
    tmp_path: Path,
) -> None:
    source_root = _write_probe_packages(
        tmp_path,
        {
            "compiler": (
                "from millrace.workflows import kernel_ping\n"
                "from millrace.workflows import simple_loop\n"
            ),
        },
    )

    violations = _dependency_matrix_violations(source_root)
    imported_modules = {violation.imported_module for violation in violations}

    assert "millrace.workflows" in imported_modules
    assert "millrace.workflows.simple_loop" in imported_modules


def test_adapter_dependency_matrix_forbids_testing_and_workflow_imports() -> None:
    allowed_prefixes = PACKAGE_DEPENDENCY_MATRIX["adapters"]
    forbidden_prefixes = {
        "millrace.workflows",
        "millrace.testing",
    }

    assert not (set(allowed_prefixes) & forbidden_prefixes)


def test_runner_adapter_modules_do_not_import_prompt_material_or_package_assets(
) -> None:
    runner_adapter_paths = [
        path
        for path in sorted((PACKAGE_ROOT / "adapters").rglob("*.py"))
        if path.relative_to(PACKAGE_ROOT / "adapters").parts[:1] != ("cli",)
    ]
    forbidden_prefixes = (
        "millrace.operator.prompt_material",
        "millrace.contracts.workflow_package",
        "millrace.substrate.workflow_packages",
    )

    offenders: list[tuple[str, str]] = []
    for path in runner_adapter_paths:
        for imported_module in sorted(_imported_modules(SOURCE_ROOT, path)):
            if imported_module.startswith(forbidden_prefixes):
                offenders.append(
                    (path.relative_to(SOURCE_ROOT).as_posix(), imported_module)
                )

    assert offenders == []


def test_dependency_matrix_accepts_allowed_package_imports(tmp_path: Path) -> None:
    source_root = _write_probe_packages(
        tmp_path,
        {
            "contracts": "import millrace.contracts\n",
            "kernel": (
                "import millrace.contracts\n"
                "import millrace.kernel\n"
                "from . import local\n"
            ),
            "compiler": "import millrace.contracts\nimport millrace.compiler\n",
            "substrate": (
                "import millrace.contracts\n"
                "import millrace.substrate\n"
            ),
            "workflows": "import millrace.contracts\nimport millrace.workflows\n",
            "adapters": (
                "import millrace.contracts\n"
                "import millrace.adapters\n"
            ),
            "operator": (
                "import millrace.contracts\n"
                "import millrace.kernel\n"
                "import millrace.compiler\n"
                "import millrace.substrate\n"
                "import millrace.operator\n"
            ),
            "extensions": "import millrace.contracts\nimport millrace.extensions\n",
            "testing": (
                "import millrace.contracts\n"
                "import millrace.kernel\n"
                "import millrace.compiler\n"
                "import millrace.substrate\n"
                "import millrace.adapters\n"
                "import millrace.operator\n"
                "import millrace.extensions\n"
                "import millrace.workflows\n"
                "import millrace.testing\n"
            ),
        },
    )

    assert _dependency_matrix_violations(source_root) == []


def test_scaffold_contains_every_dependency_matrix_package() -> None:
    assert {"__root__", *_package_names(SOURCE_ROOT)} == set(
        PACKAGE_DEPENDENCY_MATRIX
    )


def test_scaffold_imports_obey_dependency_matrix() -> None:
    assert _dependency_matrix_violations(SOURCE_ROOT) == []


def test_installed_package_discovery_does_not_live_under_contracts() -> None:
    contract_paths = sorted((PACKAGE_ROOT / "contracts").rglob("*.py"))

    offenders = [
        path.relative_to(SOURCE_ROOT).as_posix()
        for path in contract_paths
        if "installed_workflow_package" in path.read_text(encoding="utf-8")
        or "importlib.metadata" in path.read_text(encoding="utf-8")
    ]

    assert offenders == []


def test_installed_package_discovery_does_not_import_workflow_package_modules() -> None:
    source_path = PACKAGE_ROOT / "compiler" / "workflow_package_sources.py"
    source = source_path.read_text(encoding="utf-8")

    forbidden = (
        "importlib.import_module",
        "from importlib import import_module",
        "__import__(",
    )

    assert [item for item in forbidden if item in source] == []


def test_only_compiler_package_source_modules_import_importlib_metadata() -> None:
    importers: list[str] = []
    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        if "importlib.metadata" in path.read_text(encoding="utf-8"):
            importers.append(path.relative_to(PACKAGE_ROOT).as_posix())

    assert importers == ["compiler/workflow_package_sources.py"]


def test_installed_package_discovery_does_not_call_entry_points_or_importlib_resources(
) -> None:
    source_path = PACKAGE_ROOT / "compiler" / "workflow_package_sources.py"
    source = source_path.read_text(encoding="utf-8")

    forbidden = (
        "entry_points(",
        "load_entry_point",
        "importlib.resources",
        "resources.files",
    )

    assert [item for item in forbidden if item in source] == []
