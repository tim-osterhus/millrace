from __future__ import annotations

import ast
import re
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

SOURCE_ROOT = Path("src/millrace_ai")
TEST_ROOT = Path("tests")

REPORT_LIMIT = 10
BROAD_IMPORT_LIMIT = 10

SUSPICIOUS_MODULE_NAMES = {"common.py", "helpers.py", "utils.py"}

# Keep this aligned with tests/test_source_hygiene.py.
GENERIC_MODULE_ALLOWLIST = {
    Path("src/millrace_ai/architecture/common.py"),
}

TRACKED_ARTIFACT_DIR_NAMES = {
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "htmlcov",
    "node_modules",
    "venv",
}
TRACKED_ARTIFACT_SUFFIXES = {
    ".coverage",
    ".DS_Store",
    ".dll",
    ".dylib",
    ".pyo",
    ".pyc",
    ".so",
}
TRACKED_ARTIFACT_SUFFIX_PARTS = (".egg-info",)

SOURCE_PATH_REFERENCE_RE = re.compile(r"src/millrace_ai/[A-Za-z0-9_./-]+")


@dataclass(frozen=True)
class ModuleSize:
    path: Path
    lines: int


@dataclass(frozen=True)
class ImportBreadth:
    module: str
    count: int


@dataclass(frozen=True)
class MissingDocReference:
    doc_path: Path
    source_path: Path


@dataclass(frozen=True)
class RepoShape:
    largest_source_modules: tuple[ModuleSize, ...]
    largest_test_modules: tuple[ModuleSize, ...]
    fan_out: tuple[ImportBreadth, ...]
    fan_in: tuple[ImportBreadth, ...]
    import_cycles: tuple[tuple[str, ...], ...]
    suspicious_names: tuple[Path, ...]
    missing_doc_references: tuple[MissingDocReference, ...]
    tracked_artifacts: tuple[Path, ...]
    ignored_artifacts: tuple[Path, ...]

    @property
    def integrity_failures(self) -> tuple[str, ...]:
        failures: list[str] = []
        failures.extend(f"import cycle: {' -> '.join(cycle)}" for cycle in self.import_cycles)
        failures.extend(f"tracked build/local artifact: {format_path(path)}" for path in self.tracked_artifacts)
        return tuple(failures)


def main(argv: Sequence[str] | None = None) -> int:
    args = tuple(argv if argv is not None else sys.argv[1:])
    if args:
        print("usage: python scripts/maintenance/repo_shape_report.py", file=sys.stderr)
        return 2

    repo_root = Path.cwd()
    tracked_paths = git_ls_files(repo_root)
    ignored_paths = git_ignored_files(repo_root)
    shape = collect_repo_shape(repo_root, tracked_paths, ignored_paths)
    print(render_report(shape))
    return 1 if shape.integrity_failures else 0


def collect_repo_shape(repo_root: Path, tracked_paths: Sequence[Path], ignored_paths: Sequence[Path] = ()) -> RepoShape:
    normalized_tracked = tuple(sorted(normalize_path(path) for path in tracked_paths))
    source_paths = python_source_paths(normalized_tracked)
    test_paths = python_test_paths(normalized_tracked)
    import_graph = build_import_graph(repo_root, source_paths)

    return RepoShape(
        largest_source_modules=largest_modules(repo_root, source_paths),
        largest_test_modules=largest_modules(repo_root, test_paths),
        fan_out=broad_fan_out(import_graph),
        fan_in=broad_fan_in(import_graph),
        import_cycles=tuple(_strongly_connected_components(import_graph)),
        suspicious_names=suspicious_source_names(source_paths),
        missing_doc_references=find_missing_doc_source_references(repo_root, normalized_tracked),
        tracked_artifacts=tracked_artifact_paths(normalized_tracked),
        ignored_artifacts=ignored_artifact_paths(ignored_paths),
    )


def git_ls_files(repo_root: Path) -> tuple[Path, ...]:
    return _git_path_output(repo_root, "ls-files", "-z")


def git_ignored_files(repo_root: Path) -> tuple[Path, ...]:
    try:
        return _git_path_output(repo_root, "ls-files", "--others", "--ignored", "--exclude-standard", "-z")
    except subprocess.CalledProcessError:
        return ()


def _git_path_output(repo_root: Path, *args: str) -> tuple[Path, ...]:
    completed = subprocess.run(
        ("git", *args),
        cwd=repo_root,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return tuple(Path(part) for part in completed.stdout.decode("utf-8").split("\0") if part)


def python_source_paths(tracked_paths: Sequence[Path]) -> tuple[Path, ...]:
    return tuple(path for path in tracked_paths if path.suffix == ".py" and _is_under(path, SOURCE_ROOT))


def python_test_paths(tracked_paths: Sequence[Path]) -> tuple[Path, ...]:
    return tuple(path for path in tracked_paths if path.suffix == ".py" and _is_test_path(path))


def largest_modules(repo_root: Path, paths: Sequence[Path], limit: int = REPORT_LIMIT) -> tuple[ModuleSize, ...]:
    sizes = tuple(ModuleSize(path=path, lines=count_lines(repo_root / path)) for path in paths)
    return tuple(sorted(sizes, key=lambda size: (-size.lines, format_path(size.path)))[:limit])


def count_lines(path: Path) -> int:
    return len(path.read_text(encoding="utf-8").splitlines())


def build_import_graph(repo_root: Path, source_paths: Sequence[Path]) -> dict[str, set[str]]:
    known_modules = {_module_name(path) for path in source_paths}
    return {
        module_name: _concrete_millrace_imports(repo_root / path, module_name, known_modules)
        for path in source_paths
        for module_name in (_module_name(path),)
    }


def broad_fan_out(graph: dict[str, set[str]], limit: int = BROAD_IMPORT_LIMIT) -> tuple[ImportBreadth, ...]:
    breadth = (ImportBreadth(module=module, count=len(imports)) for module, imports in graph.items() if imports)
    return tuple(sorted(breadth, key=lambda item: (-item.count, item.module))[:limit])


def broad_fan_in(graph: dict[str, set[str]], limit: int = BROAD_IMPORT_LIMIT) -> tuple[ImportBreadth, ...]:
    counts: Counter[str] = Counter()
    for imports in graph.values():
        counts.update(imports)
    breadth = (ImportBreadth(module=module, count=count) for module, count in counts.items())
    return tuple(sorted(breadth, key=lambda item: (-item.count, item.module))[:limit])


def suspicious_source_names(source_paths: Sequence[Path]) -> tuple[Path, ...]:
    return tuple(
        sorted(
            path
            for path in source_paths
            if path.name in SUSPICIOUS_MODULE_NAMES and path not in GENERIC_MODULE_ALLOWLIST
        )
    )


def find_missing_doc_source_references(repo_root: Path, tracked_paths: Sequence[Path]) -> tuple[MissingDocReference, ...]:
    tracked_set = set(tracked_paths)
    doc_paths = tuple(path for path in tracked_paths if path.suffix == ".md")
    missing: set[MissingDocReference] = set()
    for doc_path in doc_paths:
        text = (repo_root / doc_path).read_text(encoding="utf-8")
        for match in SOURCE_PATH_REFERENCE_RE.finditer(text):
            source_path = normalize_path(Path(_clean_reference(match.group(0))))
            if not tracked_path_exists(source_path, tracked_set):
                missing.add(MissingDocReference(doc_path=doc_path, source_path=source_path))
    return tuple(sorted(missing, key=lambda item: (format_path(item.doc_path), format_path(item.source_path))))


def tracked_path_exists(path: Path, tracked_paths: set[Path]) -> bool:
    if path in tracked_paths:
        return True
    prefix = f"{format_path(path).rstrip('/')}/"
    return any(format_path(candidate).startswith(prefix) for candidate in tracked_paths)


def tracked_artifact_paths(tracked_paths: Sequence[Path]) -> tuple[Path, ...]:
    return tuple(path for path in tracked_paths if is_artifact_path(path))


def ignored_artifact_paths(ignored_paths: Sequence[Path]) -> tuple[Path, ...]:
    return tuple(
        sorted(
            {
                artifact_cleanup_root(normalize_path(path))
                for path in ignored_paths
                if is_artifact_path(normalize_path(path))
            }
        )
    )


def is_artifact_path(path: Path) -> bool:
    parts = path.parts
    if any(part in TRACKED_ARTIFACT_DIR_NAMES for part in parts):
        return True
    if any(part.endswith(TRACKED_ARTIFACT_SUFFIX_PARTS) for part in parts):
        return True
    return path.name in TRACKED_ARTIFACT_SUFFIXES or path.suffix in TRACKED_ARTIFACT_SUFFIXES


def artifact_cleanup_root(path: Path) -> Path:
    parts = path.parts
    for index, part in enumerate(parts):
        if part in TRACKED_ARTIFACT_DIR_NAMES or part.endswith(TRACKED_ARTIFACT_SUFFIX_PARTS):
            return Path(*parts[: index + 1])
    return path


def render_report(shape: RepoShape) -> str:
    sections = [
        "# Millrace Repository Shape Report",
        "",
        "Advisory metrics are review prompts. Hard failures are limited to concrete integrity issues.",
        "",
        render_integrity_section(shape),
        render_module_size_section("Largest Source Modules", shape.largest_source_modules),
        render_module_size_section("Largest Test Modules", shape.largest_test_modules),
        render_import_breadth_section("Broad Import Fan-Out", shape.fan_out),
        render_import_breadth_section("Broad Import Fan-In", shape.fan_in),
        render_path_section("Suspicious Source Module Names", shape.suspicious_names, empty="none"),
        render_missing_doc_reference_section(shape.missing_doc_references),
        render_path_section("Ignored Local Artifacts", shape.ignored_artifacts, empty="none"),
    ]
    return "\n".join(sections).rstrip() + "\n"


def render_integrity_section(shape: RepoShape) -> str:
    lines = ["## Integrity Checks"]
    if not shape.integrity_failures:
        lines.append("- pass")
    else:
        lines.extend(f"- FAIL: {failure}" for failure in shape.integrity_failures)
    return "\n".join(lines)


def render_module_size_section(title: str, modules: Sequence[ModuleSize]) -> str:
    lines = [f"## {title}"]
    if not modules:
        lines.append("- none")
    else:
        lines.extend(f"- {module.lines:>5} lines  {format_path(module.path)}" for module in modules)
    return "\n".join(lines)


def render_import_breadth_section(title: str, breadth: Sequence[ImportBreadth]) -> str:
    lines = [f"## {title}"]
    if not breadth:
        lines.append("- none")
    else:
        lines.extend(f"- {item.count:>3}  {item.module}" for item in breadth)
    return "\n".join(lines)


def render_path_section(title: str, paths: Sequence[Path], *, empty: str) -> str:
    lines = [f"## {title}"]
    if not paths:
        lines.append(f"- {empty}")
    else:
        lines.extend(f"- {format_path(path)}" for path in paths)
    return "\n".join(lines)


def render_missing_doc_reference_section(references: Sequence[MissingDocReference]) -> str:
    lines = ["## Docs References To Missing Source Paths"]
    if not references:
        lines.append("- none")
    else:
        lines.extend(
            f"- {format_path(reference.doc_path)} -> {format_path(reference.source_path)}" for reference in references
        )
    return "\n".join(lines)


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


def _module_name(path: Path) -> str:
    return ".".join(path.relative_to("src").with_suffix("").parts)


def _is_test_path(path: Path) -> bool:
    return _is_under(path, TEST_ROOT) or "tests" in path.parts


def _is_under(path: Path, directory: Path) -> bool:
    try:
        path.relative_to(directory)
    except ValueError:
        return False
    return True


def _clean_reference(reference: str) -> str:
    return reference.rstrip(".,;:)]}`'\"")


def normalize_path(path: Path) -> Path:
    return Path(format_path(path))


def format_path(path: Path) -> str:
    return path.as_posix()


if __name__ == "__main__":
    raise SystemExit(main())
