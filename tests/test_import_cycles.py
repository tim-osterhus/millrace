from __future__ import annotations

import ast
from pathlib import Path


def test_millrace_ai_modules_do_not_have_concrete_import_cycles() -> None:
    source_root = Path("src/millrace_ai")
    module_paths = tuple(source_root.rglob("*.py"))
    known_modules = {
        ".".join(path.relative_to("src").with_suffix("").parts) for path in module_paths
    }
    graph = {
        module_name: _concrete_millrace_imports(path, module_name, known_modules)
        for path in module_paths
        for module_name in (".".join(path.relative_to("src").with_suffix("").parts),)
    }

    cycles = _strongly_connected_components(graph)

    assert cycles == []


def _concrete_millrace_imports(
    path: Path,
    module_name: str,
    known_modules: set[str],
) -> set[str]:
    visitor = _MillraceImportVisitor(module_name)
    visitor.visit(ast.parse(path.read_text(encoding="utf-8"), filename=str(path)))
    resolved: set[str] = set()
    for imported_module in visitor.imports:
        parts = imported_module.split(".")
        while parts:
            candidate = ".".join(parts)
            if candidate in known_modules and candidate != module_name:
                resolved.add(candidate)
                break
            parts.pop()
    return resolved


class _MillraceImportVisitor(ast.NodeVisitor):
    def __init__(self, module_name: str) -> None:
        self.module_name = module_name
        self.imports: set[str] = set()
        self._type_checking_depth = 0

    def visit_If(self, node: ast.If) -> None:
        if _is_type_checking_test(node.test):
            self._type_checking_depth += 1
            for child in node.body:
                self.visit(child)
            self._type_checking_depth -= 1
            for child in node.orelse:
                self.visit(child)
            return
        self.generic_visit(node)

    def visit_Import(self, node: ast.Import) -> None:
        if self._type_checking_depth:
            return
        for alias in node.names:
            if alias.name == "millrace_ai" or alias.name.startswith("millrace_ai."):
                self.imports.add(alias.name)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if self._type_checking_depth:
            return
        imported_module = _resolve_import_from_module(node, self.module_name)
        if imported_module == "millrace_ai" or imported_module.startswith("millrace_ai."):
            self.imports.add(imported_module)


def _is_type_checking_test(node: ast.AST) -> bool:
    if isinstance(node, ast.Name):
        return node.id == "TYPE_CHECKING"
    if isinstance(node, ast.Attribute):
        return node.attr == "TYPE_CHECKING"
    return False


def _resolve_import_from_module(node: ast.ImportFrom, module_name: str) -> str:
    if node.module is None and node.level == 0:
        return ""
    if node.level:
        parts = module_name.split(".")[: -node.level]
        if node.module:
            parts.extend(node.module.split("."))
        return ".".join(parts)
    return node.module or ""


def _strongly_connected_components(graph: dict[str, set[str]]) -> list[tuple[str, ...]]:
    index = 0
    stack: list[str] = []
    indices: dict[str, int] = {}
    lowlinks: dict[str, int] = {}
    on_stack: set[str] = set()
    components: list[tuple[str, ...]] = []

    def strongconnect(module_name: str) -> None:
        nonlocal index
        indices[module_name] = index
        lowlinks[module_name] = index
        index += 1
        stack.append(module_name)
        on_stack.add(module_name)

        for imported_module in graph[module_name]:
            if imported_module not in indices:
                strongconnect(imported_module)
                lowlinks[module_name] = min(lowlinks[module_name], lowlinks[imported_module])
            elif imported_module in on_stack:
                lowlinks[module_name] = min(lowlinks[module_name], indices[imported_module])

        if lowlinks[module_name] == indices[module_name]:
            component: list[str] = []
            while True:
                imported_module = stack.pop()
                on_stack.remove(imported_module)
                component.append(imported_module)
                if imported_module == module_name:
                    break
            if len(component) > 1:
                components.append(tuple(sorted(component)))

    for module_name in sorted(graph):
        if module_name not in indices:
            strongconnect(module_name)
    return sorted(components)
