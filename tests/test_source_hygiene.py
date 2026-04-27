from __future__ import annotations

import ast
from pathlib import Path

SOURCE_ROOT = Path("src/millrace_ai")

_GENERIC_MODULE_ALLOWLIST = {
    Path("src/millrace_ai/architecture/common.py"),
}
_CONTRACT_FORBIDDEN_PREFIXES = (
    "millrace_ai.assets",
    "millrace_ai.cli",
    "millrace_ai.compiler",
    "millrace_ai.compilation",
    "millrace_ai.runners",
    "millrace_ai.runtime",
    "millrace_ai.workspace",
)
_PATHS_ALLOWED_IMPORT_ROOTS = {"__future__", "dataclasses", "pathlib", "typing"}


def test_lower_level_modules_do_not_import_cli() -> None:
    offenders: list[str] = []
    for path in _python_source_paths():
        if _is_under(path, SOURCE_ROOT / "cli") or path == SOURCE_ROOT / "__main__.py":
            continue
        if any(module == "millrace_ai.cli" or module.startswith("millrace_ai.cli.") for module in _imports(path)):
            offenders.append(str(path))

    assert offenders == []


def test_contract_modules_do_not_import_higher_level_domains() -> None:
    offenders: list[tuple[str, str]] = []
    for path in sorted((SOURCE_ROOT / "contracts").glob("*.py")):
        for module in _imports(path):
            if module.startswith(_CONTRACT_FORBIDDEN_PREFIXES):
                offenders.append((str(path), module))

    assert offenders == []


def test_workspace_paths_remains_path_only() -> None:
    path = SOURCE_ROOT / "workspace" / "paths.py"
    imported_roots = {module.split(".", 1)[0] for module in _imports(path)}

    assert imported_roots <= _PATHS_ALLOWED_IMPORT_ROOTS


def test_no_new_generic_helper_modules() -> None:
    offenders = [
        str(path)
        for path in _python_source_paths()
        if path.name in {"utils.py", "helpers.py", "common.py"}
        and path not in _GENERIC_MODULE_ALLOWLIST
    ]

    assert offenders == []


def test_source_avoids_wildcard_imports() -> None:
    offenders: list[str] = []
    for path in _python_source_paths():
        tree = _parse(path)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and any(alias.name == "*" for alias in node.names):
                offenders.append(str(path))
                break

    assert offenders == []


def _python_source_paths() -> tuple[Path, ...]:
    return tuple(sorted(SOURCE_ROOT.rglob("*.py")))


def _is_under(path: Path, directory: Path) -> bool:
    try:
        path.relative_to(directory)
    except ValueError:
        return False
    return True


def _imports(path: Path) -> set[str]:
    tree = _parse(path)
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = _resolve_import_from(node, path)
            if module:
                modules.add(module)
    return modules


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _resolve_import_from(node: ast.ImportFrom, path: Path) -> str:
    if node.level == 0:
        return node.module or ""

    module_name = ".".join(path.relative_to("src").with_suffix("").parts)
    parts = module_name.split(".")[: -node.level]
    if node.module:
        parts.extend(node.module.split("."))
    return ".".join(parts)
