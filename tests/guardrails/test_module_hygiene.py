from __future__ import annotations

import ast
import inspect
import sys
from collections.abc import Mapping
from pathlib import Path

import pytest

from millrace.compiler.compile import CompileResult, compile_workflow

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = PROJECT_ROOT / "src"
KERNEL_ROOT = SOURCE_ROOT / "millrace" / "kernel"
COMPILER_ROOT = SOURCE_ROOT / "millrace" / "compiler"
SUBSTRATE_ROOT = SOURCE_ROOT / "millrace" / "substrate"
OPERATOR_ROOT = SOURCE_ROOT / "millrace" / "operator"
ROOT_README = PROJECT_ROOT / "README.md"
CI_WORKFLOW = PROJECT_ROOT / ".github" / "workflows" / "ci.yml"
PYTEST_CONFTEST = PROJECT_ROOT / "tests" / "conftest.py"

TARGET_KERNEL_MODULES = {
    "audit",
    "decision",
    "errors",
    "fanout_policy",
    "join_policy",
    "lifecycle",
    "lookups",
    "mutations",
    "operator_waits",
    "terminal_actions",
    "transition",
}

ROOT_KERNEL_ALLOWED_API = {
    "StateConcurrencyError",
    "UnsupportedMutationError",
    "apply",
    "decide",
    "empty_runtime_state",
}

FANOUT_POLICY_HELPERS = {
    "_fanout_items",
    "_fanout_target_payload",
    "_read_artifact_path",
    "_read_item_path",
    "_read_mapping_path",
}

JOIN_POLICY_HELPERS = {
    "_artifact_payload_value",
    "_join_correlation_value",
    "_join_evidence_bundle_context",
    "_join_evidence_context",
    "_join_evidence_for_artifact",
    "_validate_join_ready_evidence",
    "_terminal_action_matches_artifact",
}

FANOUT_OWNER_FUNCTIONS = {
    "artifact_relevant_to_fanout",
    "assess_fanout",
    "fanout_items",
    "fanout_target_payload",
    "source_context_for_artifact",
}

JOIN_OWNER_FUNCTIONS = {
    "assess_join_group",
    "canonical_correlation_identity",
    "join_group_for_source",
    "join_groups_for_declaration",
    "logical_join_key",
}

TARGET_COMPILER_MODULES = {
    "authority",
    "build",
    "canonical",
    "compile",
    "diagnostics",
    "export",
    "identity",
    "operator_waits",
    "package_selection",
    "references",
    "runner_bindings",
    "schemas",
    "source",
    "terminal_actions",
    "workflow_package_manifest",
}

TARGET_OPERATOR_MODULES = {
    "intake",
    "package_doctor",
    "packages",
    "status",
}

PUBLIC_KERNEL_ENTRYPOINTS = {
    SOURCE_ROOT / "millrace" / "kernel" / "__init__.py",
    SOURCE_ROOT / "millrace" / "kernel" / "transition.py",
}

AUTHORIZED_KERNEL_INTERNAL_CONSUMERS = {
    SOURCE_ROOT / "millrace" / "adapters" / "cli" / "lifecycle.py",
    SOURCE_ROOT / "millrace" / "operator" / "dispatch.py",
    SOURCE_ROOT / "millrace" / "substrate" / "_sqlite_relations.py",
}

PUBLIC_COMPILER_ENTRYPOINTS = {
    SOURCE_ROOT / "millrace" / "compiler" / "__init__.py",
    SOURCE_ROOT / "millrace" / "compiler" / "compile.py",
    SOURCE_ROOT / "millrace" / "compiler" / "package_selection.py",
}

PACKAGE_LOCAL_IMPLEMENTATION_MODULES = {
    "millrace.kernel.audit",
    "millrace.kernel.decision",
    "millrace.kernel.errors",
    "millrace.kernel.fanout_policy",
    "millrace.kernel.joins",
    "millrace.kernel.join_policy",
    "millrace.kernel.lifecycle",
    "millrace.kernel.lookups",
    "millrace.kernel.mutations",
    "millrace.kernel.observation_policy",
    "millrace.kernel.operator_waits",
    "millrace.kernel.projection",
    "millrace.kernel.schema",
    "millrace.kernel.state",
    "millrace.kernel.terminal_actions",
}

PACKAGE_LOCAL_COMPILER_IMPLEMENTATION_MODULES = {
    "millrace.compiler.authority",
    "millrace.compiler.build",
    "millrace.compiler.canonical",
    "millrace.compiler.diagnostics",
    "millrace.compiler.export",
    "millrace.compiler.identity",
    "millrace.compiler.operator_waits",
    "millrace.compiler.references",
    "millrace.compiler.runner_bindings",
    "millrace.compiler.schemas",
    "millrace.compiler.source",
    "millrace.compiler.terminal_actions",
    "millrace.compiler.workflow_package_manifest",
}

FORBIDDEN_COMPILER_DEPENDENCY_PREFIXES = {
    "millrace.adapters",
    "millrace.extensions",
    "millrace.kernel",
    "millrace.operator",
    "millrace.substrate",
    "millrace.workflows",
}

FORBIDDEN_MODULE_BASENAMES = {"common.py", "helpers.py", "utils.py"}

STALE_ROOT_README_WORDING = {
    "contains no compiler passes",
    "contains no SQLite/CAS persistence",
    "contains no transition engine",
    "Future persistence packets must define versioned row/object codecs",
    "reserved for future kernel-owned persistence",
}

EXPECTED_PUBLIC_SUBSTRATE_API = (
    "CasDigestMismatch",
    "CasObjectKindMismatch",
    "CasObjectNotFound",
    "ContentAddressedByteStore",
    "InvalidCasDigest",
    "InvalidCasObject",
    "SQLiteRuntimeStore",
    "StoreNotInitialized",
    "StoreSchemaMetadata",
    "StorageIntegrityError",
    "SubstrateError",
    "UnsupportedCodec",
    "UnsupportedRecordKind",
    "UnsupportedSchemaVersion",
    "UnsupportedStoreSchemaVersion",
    "storage_digest_for_bytes",
)

EXPECTED_PUBLIC_COMPILER_API = (
    "AUTHORITY_FINGERPRINT_DOMAIN_PREFIX",
    "CanonicalAuthorityError",
    "CompiledPlanExportError",
    "CompileResult",
    "DEFAULT_SELECTED_RUNNER_ADAPTER_KIND",
    "DEFAULT_SELECTED_RUNNER_ADAPTER_POLICY",
    "VerifiedCompiledPlanExport",
    "RUNNER_ADAPTER_KIND_DEFAULTED",
    "SelectedRunnerAdapterPolicy",
    "authority_fingerprint",
    "canonical_authority_bytes",
    "compile_workflow",
    "compiled_plan_export_bytes",
    "compiled_plan_export_record",
    "verify_compiled_plan_export_bytes",
    "verify_compiled_plan_export_record",
)

EXPECTED_COMPILER_EXPORT_API = (
    "CANONICALIZATION_ALGORITHM",
    "COMPILED_PLAN_EXPORT_RECORD_KIND",
    "COMPILED_PLAN_EXPORT_SCHEMA_VERSION",
    "CompiledPlanExportError",
    "COMPILER_ID",
    "COMPILER_PROTOCOL_VERSION",
    "EXPORT_AUTHORITY_FINGERPRINT_DOMAIN",
    "EXPORT_HASH_ALGORITHM",
    "VerifiedCompiledPlanExport",
    "compiled_plan_export_bytes",
    "compiled_plan_export_record",
    "verify_compiled_plan_export_bytes",
    "verify_compiled_plan_export_record",
)

PYTEST_REPOSITORY_CLEANUP_CALLS = {
    "rmtree",
    "unlink",
}

ALLOWED_KERNEL_INTERNAL_IMPORTS = {
    "millrace.kernel": frozenset(
        {
            "millrace.kernel.lifecycle",
            "millrace.kernel.state",
            "millrace.kernel.transition",
        }
    ),
    "millrace.kernel.audit": frozenset[str](),
    "millrace.kernel.decision": frozenset(
        {
            "millrace.kernel.audit",
            "millrace.kernel.fanout_policy",
            "millrace.kernel.joins",
            "millrace.kernel.lookups",
            "millrace.kernel.operator_waits",
            "millrace.kernel.schema",
            "millrace.kernel.terminal_actions",
        }
    ),
    "millrace.kernel.errors": frozenset[str](),
    "millrace.kernel.joins": frozenset(
        {
            "millrace.kernel.fanout_policy",
            "millrace.kernel.join_policy",
            "millrace.kernel.lookups",
            "millrace.kernel.schema",
        }
    ),
    "millrace.kernel.fanout_policy": frozenset({"millrace.kernel.observation_policy"}),
    "millrace.kernel.join_policy": frozenset(
        {
            "millrace.kernel.fanout_policy",
        }
    ),
    "millrace.kernel.lifecycle": frozenset(
        {
            "millrace.kernel.fanout_policy",
            "millrace.kernel.join_policy",
        }
    ),
    "millrace.kernel.lookups": frozenset[str](),
    "millrace.kernel.mutations": frozenset(
        {
            "millrace.kernel.errors",
            "millrace.kernel.lookups",
        }
    ),
    "millrace.kernel.observation_policy": frozenset(
        {"millrace.kernel.lookups", "millrace.kernel.projection"}
    ),
    "millrace.kernel.operator_waits": frozenset(
        {
            "millrace.kernel.lookups",
            "millrace.kernel.schema",
        }
    ),
    "millrace.kernel.projection": frozenset[str](),
    "millrace.kernel.schema": frozenset[str](),
    "millrace.kernel.state": frozenset[str](),
    "millrace.kernel.terminal_actions": frozenset(
        {
            "millrace.kernel.lookups",
            "millrace.kernel.projection",
            "millrace.kernel.schema",
        }
    ),
    "millrace.kernel.transition": frozenset(
        {
            "millrace.kernel.decision",
            "millrace.kernel.mutations",
        }
    ),
}

ALLOWED_COMPILER_INTERNAL_IMPORTS = {
    "millrace.compiler": frozenset(
        {
            "millrace.compiler.canonical",
            "millrace.compiler.compile",
            "millrace.compiler.export",
            "millrace.compiler.runner_bindings",
        }
    ),
    "millrace.compiler.authority": frozenset(
        {
            "millrace.compiler.diagnostics",
            "millrace.compiler.references",
            "millrace.compiler.source",
        }
    ),
    "millrace.compiler.build": frozenset({"millrace.compiler.source"}),
    "millrace.compiler.canonical": frozenset[str](),
    "millrace.compiler.compile": frozenset(
        {
            "millrace.compiler.authority",
            "millrace.compiler.build",
            "millrace.compiler.identity",
            "millrace.compiler.operator_waits",
            "millrace.compiler.references",
            "millrace.compiler.runner_bindings",
            "millrace.compiler.schemas",
            "millrace.compiler.source",
            "millrace.compiler.terminal_actions",
        }
    ),
    "millrace.compiler.diagnostics": frozenset[str](),
    "millrace.compiler.export": frozenset[str](),
    "millrace.compiler.identity": frozenset(
        {
            "millrace.compiler.authority",
            "millrace.compiler.diagnostics",
            "millrace.compiler.source",
        }
    ),
    "millrace.compiler.operator_waits": frozenset(
        {
            "millrace.compiler.diagnostics",
            "millrace.compiler.references",
            "millrace.compiler.source",
        }
    ),
    "millrace.compiler.package_selection": frozenset(
        {
            "millrace.compiler.compile",
            "millrace.compiler.diagnostics",
            "millrace.compiler.runner_bindings",
            "millrace.compiler.workflow_package_manifest",
        }
    ),
    "millrace.compiler.references": frozenset(
        {
            "millrace.compiler.diagnostics",
            "millrace.compiler.source",
        }
    ),
    "millrace.compiler.runner_bindings": frozenset(
        {
            "millrace.compiler.diagnostics",
            "millrace.compiler.source",
        }
    ),
    "millrace.compiler.schemas": frozenset(
        {
            "millrace.compiler.diagnostics",
            "millrace.compiler.source",
        }
    ),
    "millrace.compiler.source": frozenset[str](),
    "millrace.compiler.terminal_actions": frozenset(
        {
            "millrace.compiler.diagnostics",
            "millrace.compiler.source",
        }
    ),
    "millrace.compiler.workflow_package_manifest": frozenset(
        {"millrace.compiler.diagnostics"}
    ),
    "millrace.compiler.workflow_package_sources": frozenset(
        {
            "millrace.compiler.diagnostics",
            "millrace.compiler.workflow_package_manifest",
        }
    ),
}

ALLOWED_SUBSTRATE_INTERNAL_IMPORTS = {
    "millrace.substrate": frozenset(
        {
            "millrace.substrate.cas",
            "millrace.substrate.errors",
            "millrace.substrate.sqlite",
        }
    ),
    "millrace.substrate._sqlite_load": frozenset(
        {
            "millrace.substrate._sqlite_relations",
            "millrace.substrate._sqlite_rows",
            "millrace.substrate.cas",
            "millrace.substrate.codecs",
            "millrace.substrate.errors",
            "millrace.substrate.records",
        }
    ),
    "millrace.substrate._sqlite_relations": frozenset(
        {
            "millrace.substrate._sqlite_rows",
            "millrace.substrate.errors",
        }
    ),
    "millrace.substrate._sqlite_write": frozenset(
        {
            "millrace.substrate._sqlite_relations",
            "millrace.substrate._sqlite_rows",
            "millrace.substrate.cas",
            "millrace.substrate.codecs",
            "millrace.substrate.errors",
            "millrace.substrate.records",
        }
    ),
    "millrace.substrate._sqlite_rows": frozenset(
        {
            "millrace.substrate.errors",
        }
    ),
    "millrace.substrate._sqlite_schema": frozenset(
        {
            "millrace.substrate.errors",
            "millrace.substrate.records",
        }
    ),
    "millrace.substrate._workflow_package_command_audit": frozenset(
        {
            "millrace.substrate.errors",
            "millrace.substrate.records",
        }
    ),
    "millrace.substrate.cas": frozenset({"millrace.substrate.errors"}),
    "millrace.substrate.codecs": frozenset(
        {
            "millrace.substrate.errors",
            "millrace.substrate.records",
        }
    ),
    "millrace.substrate.errors": frozenset[str](),
    "millrace.substrate.package_archives": frozenset[str](),
    "millrace.substrate.records": frozenset({"millrace.substrate.errors"}),
    "millrace.substrate.sqlite": frozenset(
        {
            "millrace.substrate._sqlite_load",
            "millrace.substrate._sqlite_schema",
            "millrace.substrate._workflow_package_command_audit",
            "millrace.substrate._sqlite_write",
            "millrace.substrate.cas",
            "millrace.substrate.errors",
            "millrace.substrate.records",
            "millrace.substrate.workflow_packages",
        }
    ),
    "millrace.substrate.workflow_packages": frozenset(
        {
            "millrace.substrate.cas",
            "millrace.substrate.errors",
            "millrace.substrate.package_archives",
            "millrace.substrate.records",
        }
    ),
}


def _module_name_for(path: Path) -> str:
    relative = path.relative_to(SOURCE_ROOT).with_suffix("")
    parts = list(relative.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _package_context_for(path: Path) -> list[str]:
    relative = path.relative_to(SOURCE_ROOT).with_suffix("")
    parts = list(relative.parts)
    if path.name == "__init__.py":
        return parts[:-1]
    return parts[:-1]


def _resolve_import_from(path: Path, node: ast.ImportFrom) -> set[str]:
    if node.level == 0:
        base = node.module or ""
    else:
        context = _package_context_for(path)
        prefix = context[: len(context) - node.level + 1]
        if node.module:
            prefix.extend(node.module.split("."))
        base = ".".join(prefix)

    names = {base} if base else set()
    for alias in node.names:
        if alias.name != "*":
            names.add(f"{base}.{alias.name}" if base else alias.name)
    return names


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imports.update(_resolve_import_from(path, node))
    return imports


def _is_same_or_child(module_name: str, prefix: str) -> bool:
    return module_name == prefix or module_name.startswith(f"{prefix}.")


def _source_modules_under(package_root: Path) -> dict[str, Path]:
    return {_module_name_for(path): path for path in sorted(package_root.rglob("*.py"))}


def _function_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return {node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)}


def _nearest_discovered_module(
    imported_module: str,
    discovered_modules: frozenset[str],
) -> str | None:
    matches = sorted(
        (
            module_name
            for module_name in discovered_modules
            if _is_same_or_child(imported_module, module_name)
        ),
        key=lambda module_name: (-len(module_name), module_name),
    )
    return matches[0] if matches else None


def _internal_imports_for_path(
    path: Path,
    module_paths: Mapping[str, Path],
) -> set[str]:
    discovered_modules = frozenset(module_paths)
    internal_imports: set[str] = set()
    for imported_module in _imported_modules(path):
        internal_module = _nearest_discovered_module(
            imported_module,
            discovered_modules,
        )
        if internal_module is not None:
            internal_imports.add(internal_module)
    return internal_imports


def _internal_import_graph(package_root: Path) -> dict[str, frozenset[str]]:
    module_paths = _source_modules_under(package_root)
    graph: dict[str, frozenset[str]] = {}
    for module_name, path in module_paths.items():
        imports = _internal_imports_for_path(path, module_paths)
        imports.discard(module_name)
        graph[module_name] = frozenset(imports)
    return graph


def _internal_import_cycles(
    graph: Mapping[str, frozenset[str]],
) -> list[tuple[str, ...]]:
    visited: set[str] = set()
    active: set[str] = set()
    stack: list[str] = []
    cycles: list[tuple[str, ...]] = []
    seen_cycles: set[tuple[str, ...]] = set()

    def visit(module_name: str) -> None:
        active.add(module_name)
        stack.append(module_name)
        for dependency in sorted(graph[module_name]):
            if dependency not in graph:
                continue
            if dependency in active:
                cycle = tuple(stack[stack.index(dependency) :] + [dependency])
                if cycle not in seen_cycles:
                    seen_cycles.add(cycle)
                    cycles.append(cycle)
                continue
            if dependency not in visited:
                visit(dependency)
        stack.pop()
        active.remove(module_name)
        visited.add(module_name)

    for module_name in sorted(graph):
        if module_name not in visited:
            visit(module_name)

    return cycles


def _internal_import_policy_violations(
    package_root: Path,
    allowed_imports: Mapping[str, frozenset[str]],
) -> list[str]:
    graph = _internal_import_graph(package_root)
    violations: list[str] = []

    missing_policy = sorted(set(graph) - set(allowed_imports))
    if missing_policy:
        violations.append(f"unowned internal modules: {missing_policy}")

    stale_policy = sorted(set(allowed_imports) - set(graph))
    if stale_policy:
        violations.append(f"policy entries for missing modules: {stale_policy}")

    for module_name in sorted(set(graph) & set(allowed_imports)):
        actual = graph[module_name]
        allowed = allowed_imports[module_name]
        unexpected = sorted(actual - allowed)
        if unexpected:
            violations.append(f"{module_name} imports {unexpected}")

        unknown_allowed = sorted(allowed - set(graph))
        if unknown_allowed:
            violations.append(
                f"{module_name} policy references missing modules: {unknown_allowed}"
            )

    for cycle in _internal_import_cycles(graph):
        violations.append(f"internal import cycle: {' -> '.join(cycle)}")

    return violations


def _kernel_internal_module_imports(path: Path) -> set[str]:
    return _internal_imports_for_path(path, _source_modules_under(KERNEL_ROOT))


def _compiler_internal_module_imports(path: Path) -> set[str]:
    return _internal_imports_for_path(path, _source_modules_under(COMPILER_ROOT))


def test_kernel_extraction_target_modules_exist() -> None:
    missing = sorted(
        module_name
        for module_name in TARGET_KERNEL_MODULES
        if not (KERNEL_ROOT / f"{module_name}.py").exists()
    )

    assert missing == []


def test_compiler_extraction_target_modules_exist() -> None:
    missing = sorted(
        module_name
        for module_name in TARGET_COMPILER_MODULES
        if not (COMPILER_ROOT / f"{module_name}.py").exists()
    )

    assert missing == []


def test_package_commands_are_isolated_from_intake_status() -> None:
    missing = sorted(
        module_name
        for module_name in TARGET_OPERATOR_MODULES
        if not (OPERATOR_ROOT / f"{module_name}.py").exists()
    )
    package_command_tokens = (
        "PackageMutationCommand",
        "PackageReadExportCommand",
        "PackageWorkflowSelectionCommand",
        "PackageWorkflowVerifyCommand",
        "PackageDoctorCommand",
        "execute_package_mutation_command",
        "execute_package_read_export_command",
        "execute_package_workflow_selection_command",
        "execute_package_verify_command",
        "execute_package_doctor_command",
        "workflow_package_command_audit_events",
        "package.import_path",
        "package.import_archive",
        "package.export_archive",
        "package.export_path",
        "package.list",
        "package.inspect",
        "package.select_workflow",
        "package.verify",
        "package.doctor",
        "package.update",
        "package.enable",
        "package.disable",
        "package.remove",
    )
    intake_status_matches = sorted(
        (path.name, token)
        for path in (OPERATOR_ROOT / "intake.py", OPERATOR_ROOT / "status.py")
        for token in package_command_tokens
        if token in path.read_text(encoding="utf-8")
    )

    assert missing == []
    assert intake_status_matches == []


def test_operator_package_doctor_has_no_sqlite_private_repair_imports() -> None:
    path = OPERATOR_ROOT / "package_doctor.py"
    imported_modules = _imported_modules(path)
    forbidden_imports = sorted(
        imported_module
        for imported_module in imported_modules
        if imported_module == "sqlite3"
        or imported_module.startswith("millrace.substrate._sqlite")
    )
    source = path.read_text(encoding="utf-8")
    forbidden_tokens = (
        "_connection",
        "workflow_package_audit_events",
        "SELECT ",
        "UPDATE ",
        "DELETE ",
        "INSERT ",
    )

    assert forbidden_imports == []
    assert [token for token in forbidden_tokens if token in source] == []


def test_operator_package_doctor_does_not_import_kernel_transition_modules() -> None:
    path = OPERATOR_ROOT / "package_doctor.py"
    imported_modules = _imported_modules(path)
    forbidden_prefixes = (
        "millrace.kernel.transition",
        "millrace.kernel.decision",
        "millrace.kernel.mutations",
        "millrace.kernel.terminal_actions",
        "millrace.operator.intake",
        "millrace.operator.status",
    )
    forbidden_imports = sorted(
        imported_module
        for imported_module in imported_modules
        for forbidden_prefix in forbidden_prefixes
        if _is_same_or_child(imported_module, forbidden_prefix)
    )

    assert forbidden_imports == []


def test_operator_package_doctor_uses_public_d3_verify_surface() -> None:
    path = OPERATOR_ROOT / "package_doctor.py"
    imported_modules = _imported_modules(path)
    forbidden_prefixes = (
        "millrace.compiler.package_selection",
        "millrace.compiler.compile",
    )
    forbidden_imports = sorted(
        imported_module
        for imported_module in imported_modules
        for forbidden_prefix in forbidden_prefixes
        if _is_same_or_child(imported_module, forbidden_prefix)
    )
    source = path.read_text(encoding="utf-8")

    assert forbidden_imports == []
    assert "PackageWorkflowVerifyCommand" in source
    assert "evaluate_package_workflow_verification" in source
    assert "execute_package_verify_command" not in source


def test_source_modules_do_not_use_catchall_names() -> None:
    forbidden = sorted(
        str(path.relative_to(PROJECT_ROOT))
        for path in SOURCE_ROOT.rglob("*.py")
        if path.name in FORBIDDEN_MODULE_BASENAMES
    )

    assert forbidden == []


def test_internal_source_modules_have_module_docstrings() -> None:
    missing_docstrings: list[str] = []
    for path in sorted(SOURCE_ROOT.rglob("*.py")):
        if path.name == "__init__.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        if ast.get_docstring(tree) is None:
            missing_docstrings.append(str(path.relative_to(PROJECT_ROOT)))

    assert missing_docstrings == []


def test_root_readme_does_not_use_stale_scaffold_wording() -> None:
    readme = ROOT_README.read_text(encoding="utf-8")
    stale = sorted(
        wording for wording in STALE_ROOT_README_WORDING if wording in readme
    )

    assert stale == []


def test_public_substrate_api_is_intentional_facade() -> None:
    import millrace.substrate as substrate

    assert substrate.__all__ == EXPECTED_PUBLIC_SUBSTRATE_API


def test_public_compiler_export_api_is_intentional_facade() -> None:
    import millrace.compiler as compiler
    import millrace.compiler.export as compiler_export

    assert compiler.__all__ == EXPECTED_PUBLIC_COMPILER_API
    assert compiler_export.__all__ == EXPECTED_COMPILER_EXPORT_API
    assert [name for name in compiler.__all__ if name.startswith("_")] == []
    assert [name for name in compiler_export.__all__ if name.startswith("_")] == []
    assert not hasattr(compiler, "import_compiled_plan_export")


def test_compiler_export_module_has_no_runtime_dependency_leaks() -> None:
    path = COMPILER_ROOT / "export.py"
    imported_modules = _imported_modules(path)
    forbidden_imports = sorted(
        imported_module
        for imported_module in imported_modules
        for forbidden_prefix in FORBIDDEN_COMPILER_DEPENDENCY_PREFIXES
        if _is_same_or_child(imported_module, forbidden_prefix)
    )
    source = path.read_text(encoding="utf-8")
    forbidden_tokens = (
        "millrace.substrate",
        "millrace.kernel",
        "millrace.workflows",
        "millrace.adapters",
        "millrace.operator",
        "millrace.extensions",
        "encode_selected_compiled_plan",
        "decode_selected_compiled_plan",
        "import_compiled_plan_export",
        "argparse",
        "click",
        "typer",
    )

    assert forbidden_imports == []
    assert [token for token in forbidden_tokens if token in source] == []


def test_sqlite_row_decoders_use_runtime_validation_not_static_casts() -> None:
    source = (SUBSTRATE_ROOT / "_sqlite_rows.py").read_text(encoding="utf-8")

    assert "from typing import cast" not in source
    assert "cast(" not in source


def test_pytest_configuration_does_not_delete_repository_artifacts() -> None:
    tree = ast.parse(PYTEST_CONFTEST.read_text(encoding="utf-8"))
    cleanup_calls: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            function = node.func
            if isinstance(function, ast.Attribute):
                name = function.attr
            elif isinstance(function, ast.Name):
                name = function.id
            else:
                continue
            if name in PYTEST_REPOSITORY_CLEANUP_CALLS:
                cleanup_calls.append(name)

    assert sorted(cleanup_calls) == []


def test_internal_import_guardrail_self_probe_rejects_unknown_module(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_root = tmp_path / "src"
    kernel_root = source_root / "millrace" / "kernel"
    nested_root = kernel_root / "nested"
    kernel_root.mkdir(parents=True)
    nested_root.mkdir()
    (source_root / "millrace" / "__init__.py").write_text("", encoding="utf-8")
    (kernel_root / "__init__.py").write_text("", encoding="utf-8")
    (nested_root / "__init__.py").write_text(
        '"""Nested probe package."""\n',
        encoding="utf-8",
    )
    (nested_root / "unowned.py").write_text(
        '"""Nested unowned probe module."""\n',
        encoding="utf-8",
    )
    module = sys.modules[__name__]
    monkeypatch.setattr(module, "SOURCE_ROOT", source_root)
    monkeypatch.setattr(module, "KERNEL_ROOT", kernel_root)
    monkeypatch.setattr(
        module,
        "ALLOWED_KERNEL_INTERNAL_IMPORTS",
        {
            "millrace.kernel": frozenset(),
            "millrace.kernel.nested": frozenset(),
        },
    )

    with pytest.raises(AssertionError, match="unowned"):
        test_kernel_internal_import_graph_is_acyclic_and_owned()


def test_internal_import_guardrail_self_probe_rejects_cycle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_root = tmp_path / "src"
    kernel_root = source_root / "millrace" / "kernel"
    kernel_root.mkdir(parents=True)
    (source_root / "millrace" / "__init__.py").write_text("", encoding="utf-8")
    (kernel_root / "__init__.py").write_text("", encoding="utf-8")
    (kernel_root / "alpha.py").write_text(
        '"""Alpha probe module."""\nimport millrace.kernel.beta\n',
        encoding="utf-8",
    )
    (kernel_root / "beta.py").write_text(
        '"""Beta probe module."""\nimport millrace.kernel.alpha\n',
        encoding="utf-8",
    )
    module = sys.modules[__name__]
    monkeypatch.setattr(module, "SOURCE_ROOT", source_root)
    monkeypatch.setattr(module, "KERNEL_ROOT", kernel_root)
    monkeypatch.setattr(
        module,
        "ALLOWED_KERNEL_INTERNAL_IMPORTS",
        {
            "millrace.kernel": frozenset(),
            "millrace.kernel.alpha": frozenset({"millrace.kernel.beta"}),
            "millrace.kernel.beta": frozenset({"millrace.kernel.alpha"}),
        },
    )

    with pytest.raises(AssertionError, match="cycle"):
        test_kernel_internal_import_graph_is_acyclic_and_owned()


def test_kernel_internal_import_graph_is_acyclic_and_owned() -> None:
    assert (
        _internal_import_policy_violations(
            KERNEL_ROOT,
            ALLOWED_KERNEL_INTERNAL_IMPORTS,
        )
        == []
    )


def test_compiler_internal_import_graph_is_acyclic_and_owned() -> None:
    assert (
        _internal_import_policy_violations(
            COMPILER_ROOT,
            ALLOWED_COMPILER_INTERNAL_IMPORTS,
        )
        == []
    )


def test_substrate_internal_import_graph_is_acyclic_and_owned() -> None:
    assert (
        _internal_import_policy_violations(
            SUBSTRATE_ROOT,
            ALLOWED_SUBSTRATE_INTERNAL_IMPORTS,
        )
        == []
    )


def test_ci_uses_frozen_uv_dependency_commands() -> None:
    workflow = CI_WORKFLOW.read_text(encoding="utf-8")
    assert "uv sync --frozen --group dev" in workflow
    unfrozen_run_commands = sorted(
        line.strip()
        for line in workflow.splitlines()
        if "run: uv run " in line and "run: uv run --frozen " not in line
    )
    assert unfrozen_run_commands == []


def test_public_compiler_api_has_contract_docstrings() -> None:
    result_doc = inspect.getdoc(CompileResult)
    workflow_doc = inspect.getdoc(compile_workflow)

    assert result_doc is not None
    assert "plan is None" in result_doc
    assert workflow_doc is not None
    assert "diagnostics" in workflow_doc
    assert "raises" not in workflow_doc.lower()


def test_kernel_internal_modules_do_not_import_transition_facade() -> None:
    violations: list[str] = []
    for path in sorted(KERNEL_ROOT.rglob("*.py")):
        if path in PUBLIC_KERNEL_ENTRYPOINTS:
            continue
        if "millrace.kernel.transition" in _kernel_internal_module_imports(path):
            violations.append(_module_name_for(path))

    assert violations == []


def test_compiler_internal_modules_do_not_import_compile_facade() -> None:
    violations: list[str] = []
    for path in sorted(COMPILER_ROOT.rglob("*.py")):
        if path in PUBLIC_COMPILER_ENTRYPOINTS:
            continue
        if "millrace.compiler.compile" in _compiler_internal_module_imports(path):
            violations.append(_module_name_for(path))

    assert violations == []


def test_terminal_actions_does_not_import_decision() -> None:
    path = KERNEL_ROOT / "terminal_actions.py"
    assert "millrace.kernel.decision" not in _kernel_internal_module_imports(path)


def test_compiler_internals_do_not_import_runtime_packages() -> None:
    violations: list[tuple[str, str]] = []
    for path in sorted(COMPILER_ROOT.rglob("*.py")):
        for imported_module in sorted(_imported_modules(path)):
            for forbidden_prefix in FORBIDDEN_COMPILER_DEPENDENCY_PREFIXES:
                if _is_same_or_child(imported_module, forbidden_prefix):
                    violations.append(
                        (
                            str(path.relative_to(SOURCE_ROOT)),
                            forbidden_prefix,
                        )
                    )

    assert violations == []


def test_compiler_default_policy_keeps_provider_authority_out_of_generic_logic() -> (
    None
):
    policy_path = COMPILER_ROOT / "runner_bindings.py"
    compiler_millforge_mentions = {
        path.relative_to(COMPILER_ROOT).as_posix()
        for path in COMPILER_ROOT.rglob("*.py")
        if "millforge" in path.read_text(encoding="utf-8").lower()
    }
    kernel_millforge_mentions = {
        path.relative_to(KERNEL_ROOT).as_posix()
        for path in KERNEL_ROOT.rglob("*.py")
        if "millforge" in path.read_text(encoding="utf-8").lower()
    }
    policy_source = policy_path.read_text(encoding="utf-8")
    forbidden_selected_authority = (
        "kernel_ping",
        "TASK_COMPLETE",
        "WORK_COMPLETE",
        "NEEDS_REVIEW",
        "0bace7b27871b03cd7ffe59951953348b3da3214536178d6f447a21de4403464",
        "d6b5c75f48565b939ee4d6e30b83e3ad203764b7bda02890ca515a9bfb3318f0",
    )

    assert compiler_millforge_mentions == {"runner_bindings.py"}
    assert kernel_millforge_mentions == set()
    assert "import millforge" not in policy_source
    assert [
        token for token in forbidden_selected_authority if token in policy_source
    ] == []


def test_source_outside_kernel_entrypoints_uses_public_kernel_api() -> None:
    violations: list[tuple[str, str]] = []
    for path in sorted(SOURCE_ROOT.rglob("*.py")):
        if path in PUBLIC_KERNEL_ENTRYPOINTS:
            continue
        if path in AUTHORIZED_KERNEL_INTERNAL_CONSUMERS:
            continue
        if path.is_relative_to(KERNEL_ROOT):
            continue
        for imported_module in sorted(_imported_modules(path)):
            for implementation_module in PACKAGE_LOCAL_IMPLEMENTATION_MODULES:
                if _is_same_or_child(imported_module, implementation_module):
                    violations.append(
                        (
                            str(path.relative_to(SOURCE_ROOT)),
                            implementation_module,
                        )
                    )

    assert violations == []


def test_operator_dispatch_is_only_privileged_policy_projection_consumer() -> None:
    dispatch_path = OPERATOR_ROOT / "dispatch.py"
    policy_owner_modules = {
        "millrace.kernel.fanout_policy",
        "millrace.kernel.join_policy",
    }

    assert {
        path
        for path in AUTHORIZED_KERNEL_INTERNAL_CONSUMERS
        if path.is_relative_to(OPERATOR_ROOT)
    } == {dispatch_path}
    assert {
        implementation_module
        for imported_module in _imported_modules(dispatch_path)
        for implementation_module in PACKAGE_LOCAL_IMPLEMENTATION_MODULES
        if _is_same_or_child(imported_module, implementation_module)
    } == policy_owner_modules
    assert {
        path
        for path in OPERATOR_ROOT.rglob("*.py")
        if any(
            _is_same_or_child(imported_module, owner_module)
            for imported_module in _imported_modules(path)
            for owner_module in policy_owner_modules
        )
    } == {dispatch_path}


def test_source_outside_compiler_entrypoints_uses_public_compiler_api() -> None:
    violations: list[tuple[str, str]] = []
    for path in sorted(SOURCE_ROOT.rglob("*.py")):
        if path in PUBLIC_COMPILER_ENTRYPOINTS:
            continue
        if path.is_relative_to(COMPILER_ROOT):
            continue
        for imported_module in sorted(_imported_modules(path)):
            for implementation_module in PACKAGE_LOCAL_COMPILER_IMPLEMENTATION_MODULES:
                if _is_same_or_child(imported_module, implementation_module):
                    violations.append(
                        (
                            str(path.relative_to(SOURCE_ROOT)),
                            implementation_module,
                        )
                    )

    assert violations == []


def test_root_kernel_api_omits_lifecycle_projection_dataclasses() -> None:
    import millrace.kernel as kernel

    exported = set(kernel.__all__)

    assert exported == ROOT_KERNEL_ALLOWED_API
    assert not hasattr(kernel, "project_next_lifecycle_transition")
    assert not hasattr(kernel, "LifecycleDiagnostic")
    assert not hasattr(kernel, "LifecycleProjection")
    assert not hasattr(kernel, "ProjectedLifecycleCandidate")


def test_lifecycle_projection_does_not_own_admission_policy_helpers() -> None:
    lifecycle_path = KERNEL_ROOT / "lifecycle.py"
    helper_names = _function_names(lifecycle_path)

    assert helper_names & (FANOUT_POLICY_HELPERS | JOIN_POLICY_HELPERS) == set()


def test_fanout_and_join_policy_have_one_shared_owner_each() -> None:
    fanout_owner = KERNEL_ROOT / "fanout_policy.py"
    join_owner = KERNEL_ROOT / "join_policy.py"
    owner_paths = (fanout_owner, join_owner)

    assert [path.name for path in owner_paths if not path.exists()] == []
    assert not (KERNEL_ROOT / "lifecycle_authority.py").exists()
    assert FANOUT_OWNER_FUNCTIONS <= _function_names(fanout_owner)
    assert JOIN_OWNER_FUNCTIONS <= _function_names(join_owner)

    consumer_helpers = {
        KERNEL_ROOT / "decision.py": FANOUT_POLICY_HELPERS,
        KERNEL_ROOT / "joins.py": JOIN_POLICY_HELPERS,
        KERNEL_ROOT / "lifecycle.py": FANOUT_POLICY_HELPERS | JOIN_POLICY_HELPERS,
        OPERATOR_ROOT / "dispatch.py": FANOUT_POLICY_HELPERS | JOIN_POLICY_HELPERS,
        SUBSTRATE_ROOT / "_sqlite_relations.py": JOIN_POLICY_HELPERS,
    }
    duplicates = {
        path.relative_to(SOURCE_ROOT).as_posix(): sorted(
            _function_names(path) & forbidden
        )
        for path, forbidden in consumer_helpers.items()
        if _function_names(path) & forbidden
    }
    assert duplicates == {}

    expected_consumers = {
        fanout_owner: {
            KERNEL_ROOT / "decision.py",
            KERNEL_ROOT / "join_policy.py",
            KERNEL_ROOT / "lifecycle.py",
            OPERATOR_ROOT / "dispatch.py",
            SUBSTRATE_ROOT / "_sqlite_relations.py",
        },
        join_owner: {
            KERNEL_ROOT / "joins.py",
            KERNEL_ROOT / "lifecycle.py",
            OPERATOR_ROOT / "dispatch.py",
            SUBSTRATE_ROOT / "_sqlite_relations.py",
        },
    }
    for owner_path, consumers in expected_consumers.items():
        owner_module = _module_name_for(owner_path)
        assert {
            path
            for path in consumers
            if not any(
                _is_same_or_child(imported_module, owner_module)
                for imported_module in _imported_modules(path)
            )
        } == set()

    assert "_validate_join_transition_route_completeness" not in _function_names(
        SUBSTRATE_ROOT / "_sqlite_relations.py"
    )


def test_lifecycle_projection_has_one_direct_production_consumer() -> None:
    lifecycle_module = "millrace.kernel.lifecycle"
    consumers = {
        path
        for path in SOURCE_ROOT.rglob("*.py")
        if path != KERNEL_ROOT / "lifecycle.py"
        and any(
            _is_same_or_child(imported_module, lifecycle_module)
            for imported_module in _imported_modules(path)
        )
    }

    assert consumers == {SOURCE_ROOT / "millrace" / "adapters" / "cli" / "lifecycle.py"}
