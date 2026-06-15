"""Workspace map generation and validation."""

from __future__ import annotations

import ast
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from millrace_ai.workspace.paths import WorkspacePaths

SCHEMA_VERSION = "1.0"
MAX_TEXT_BYTES = 1_000_000
TEXT_SUFFIXES = {
    ".css",
    ".html",
    ".js",
    ".jsx",
    ".json",
    ".md",
    ".py",
    ".pyi",
    ".rst",
    ".sh",
    ".ts",
    ".tsx",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}
DOC_SUFFIXES = {".md", ".rst", ".txt"}
EXCLUDED_DIR_NAMES = {
    ".git",
    ".hg",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".svn",
    ".tox",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "venv",
}
ROOT_EXCLUDED_DIR_NAMES = {
    "workspaces",
}
GENERATED_FILENAMES = {
    "file-tree.md",
    "repo-map.compact.md",
    "symbols.jsonl",
    "imports.json",
    "reverse-imports.json",
    "public-api.jsonl",
    "tests-map.jsonl",
    "docs-references.jsonl",
    "freshness.json",
}
PATH_TOKEN_PATTERN = re.compile(
    r"`([^`\s]+\.(?:css|html|js|jsx|json|md|py|pyi|rst|sh|toml|ts|tsx|txt|yaml|yml))`|"
    r"\(([^)\s]+\.(?:css|html|js|jsx|json|md|py|pyi|rst|sh|toml|ts|tsx|txt|yaml|yml))\)"
)


@dataclass(frozen=True, slots=True)
class FileRecord:
    path: str
    size: int
    sha256: str
    suffix: str
    kind: str
    line_count: int


@dataclass(frozen=True, slots=True)
class WorkspaceMapSnapshot:
    files: tuple[FileRecord, ...]
    symbols: tuple[dict[str, Any], ...]
    imports: dict[str, list[str]]
    reverse_imports: dict[str, list[str]]
    public_api: tuple[dict[str, Any], ...]
    tests_map: tuple[dict[str, Any], ...]
    docs_references: tuple[dict[str, Any], ...]
    warnings: tuple[str, ...]
    fingerprint: str


@dataclass(frozen=True, slots=True)
class RefreshResult:
    files_written: tuple[str, ...]
    file_count: int
    warning_count: int
    fingerprint: str


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    code: str
    path: str
    message: str


def refresh_workspace_map(paths: WorkspacePaths) -> RefreshResult:
    """Fully rebuild generated workspace-map outputs."""

    snapshot = _scan_workspace(paths)
    payloads = _render_payloads(paths, snapshot)
    for output_path, payload in payloads.items():
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(payload, encoding="utf-8")
    return RefreshResult(
        files_written=tuple(_workspace_relative(paths, path) for path in sorted(payloads)),
        file_count=len(snapshot.files),
        warning_count=len(snapshot.warnings),
        fingerprint=snapshot.fingerprint,
    )


def validate_workspace_map(paths: WorkspacePaths) -> tuple[ValidationIssue, ...]:
    """Validate generated workspace-map outputs without rewriting files."""

    issues: list[ValidationIssue] = []
    expected = _render_payloads(paths, _scan_workspace(paths))
    for output_path, payload in expected.items():
        relative_path = _workspace_relative(paths, output_path)
        if not output_path.is_file():
            issues.append(ValidationIssue("missing", relative_path, "generated output is missing"))
            continue
        try:
            actual = output_path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            issues.append(ValidationIssue("malformed", relative_path, str(exc)))
            continue
        if actual != payload:
            issues.append(ValidationIssue("stale", relative_path, "generated output differs from a fresh rebuild"))

    for json_path in (
        paths.workspace_map_manifest_file,
        paths.workspace_map_generated_imports_file,
        paths.workspace_map_generated_reverse_imports_file,
        paths.workspace_map_generated_freshness_file,
    ):
        issues.extend(_validate_json_file(paths, json_path))
    for jsonl_path in (
        paths.workspace_map_generated_symbols_file,
        paths.workspace_map_generated_public_api_file,
        paths.workspace_map_generated_tests_map_file,
        paths.workspace_map_generated_docs_references_file,
    ):
        issues.extend(_validate_jsonl_file(paths, jsonl_path))
    return tuple(sorted(issues, key=lambda issue: (issue.path, issue.code, issue.message)))


def show_workspace_map(paths: WorkspacePaths) -> str:
    """Render a compact deterministic summary from generated map outputs."""

    manifest = _read_json(paths.workspace_map_manifest_file)
    freshness = _read_json(paths.workspace_map_generated_freshness_file)
    imports = _read_json(paths.workspace_map_generated_imports_file)
    symbols_count = _count_jsonl_records(paths.workspace_map_generated_symbols_file)
    public_api_count = _count_jsonl_records(paths.workspace_map_generated_public_api_file)
    tests_count = _count_jsonl_records(paths.workspace_map_generated_tests_map_file)
    docs_count = _count_jsonl_records(paths.workspace_map_generated_docs_references_file)
    return "\n".join(
        (
            "workspace-map:",
            f"  status: {freshness.get('status', 'unknown')}",
            f"  files: {manifest.get('file_count', 0)}",
            f"  python_files: {manifest.get('python_file_count', 0)}",
            f"  symbols: {symbols_count}",
            f"  public_api: {public_api_count}",
            f"  imports: {len(imports.get('records', []))}",
            f"  tests: {tests_count}",
            f"  docs_references: {docs_count}",
            f"  fingerprint: {manifest.get('fingerprint', 'unknown')}",
        )
    ) + "\n"


def _scan_workspace(paths: WorkspacePaths) -> WorkspaceMapSnapshot:
    warnings: list[str] = []
    files: list[FileRecord] = []
    symbols: list[dict[str, Any]] = []
    imports: dict[str, list[str]] = {}
    public_api: list[dict[str, Any]] = []
    tests_map: list[dict[str, Any]] = []
    docs_references: list[dict[str, Any]] = []

    for file_path in _iter_supported_files(paths, warnings):
        relative_path = _workspace_relative(paths, file_path)
        raw = file_path.read_bytes()
        text = raw.decode("utf-8", errors="replace")
        record = FileRecord(
            path=relative_path,
            size=len(raw),
            sha256=hashlib.sha256(raw).hexdigest(),
            suffix=file_path.suffix.lower(),
            kind=_file_kind(relative_path, file_path.suffix.lower()),
            line_count=0 if text == "" else text.count("\n") + (0 if text.endswith("\n") else 1),
        )
        files.append(record)
        if file_path.suffix in {".py", ".pyi"}:
            py_symbols, py_imports = _parse_python_file(relative_path, text, warnings)
            symbols.extend(py_symbols)
            imports[relative_path] = sorted(py_imports)
            public_api.extend(_public_symbols(py_symbols))
            if _is_test_path(relative_path):
                tests_map.append(
                    {
                        "path": relative_path,
                        "imports": sorted(py_imports),
                        "test_symbols": sorted(symbol["qualname"] for symbol in py_symbols if symbol["name"].startswith("test_")),
                    }
                )
        elif _is_test_path(relative_path):
            tests_map.append({"path": relative_path, "imports": [], "test_symbols": []})
        if file_path.suffix.lower() in DOC_SUFFIXES:
            docs_references.extend(_extract_doc_references(paths, relative_path, text))

    files.sort(key=lambda record: record.path)
    symbols.sort(key=lambda item: (item["path"], item["line"], item["kind"], item["qualname"]))
    public_api.sort(key=lambda item: (item["path"], item["line"], item["qualname"]))
    tests_map.sort(key=lambda item: item["path"])
    docs_references.sort(key=lambda item: (item["path"], item["target"]))
    reverse_imports = _reverse_imports(imports)
    fingerprint = _fingerprint(files)
    return WorkspaceMapSnapshot(
        files=tuple(files),
        symbols=tuple(symbols),
        imports=dict(sorted(imports.items())),
        reverse_imports=reverse_imports,
        public_api=tuple(public_api),
        tests_map=tuple(tests_map),
        docs_references=tuple(docs_references),
        warnings=tuple(sorted(set(warnings))),
        fingerprint=fingerprint,
    )


def _iter_supported_files(paths: WorkspacePaths, warnings: list[str]) -> tuple[Path, ...]:
    discovered: list[Path] = []
    stack = [paths.root]
    excluded_runtime_roots = {
        paths.runtime_root,
        paths.state_dir,
        paths.runs_dir,
        paths.logs_dir,
        paths.workspace_map_generated_dir,
        paths.workspace_map_snapshots_dir,
        paths.history_log_entries_dir,
        paths.history_log_daily_dir,
    }
    excluded_runtime_files = {
        paths.workspace_map_manifest_file,
    }
    while stack:
        current = stack.pop()
        children = sorted(current.iterdir(), key=lambda item: item.name)
        for child in children:
            if child.is_symlink():
                if child.is_dir():
                    warnings.append(f"skipped directory symlink: {_workspace_lexical_relative(paths, child)}")
                continue
            if child.is_dir():
                if (
                    child.name in EXCLUDED_DIR_NAMES
                    or child in excluded_runtime_roots
                    or (current == paths.root and child.name in ROOT_EXCLUDED_DIR_NAMES)
                ):
                    continue
                stack.append(child)
                continue
            if not child.is_file():
                continue
            if child in excluded_runtime_files:
                continue
            if child.name == ".DS_Store" or child.suffix.lower() not in TEXT_SUFFIXES:
                continue
            size = child.stat().st_size
            if size > MAX_TEXT_BYTES:
                warnings.append(f"skipped oversized file: {_workspace_relative(paths, child)}")
                continue
            if _looks_binary(child):
                warnings.append(f"skipped binary file: {_workspace_relative(paths, child)}")
                continue
            discovered.append(child)
    return tuple(sorted(discovered, key=lambda path: _workspace_relative(paths, path)))


def _render_payloads(paths: WorkspacePaths, snapshot: WorkspaceMapSnapshot) -> dict[Path, str]:
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "generator": "millrace_ai.workspace_map",
        "mode": "full-rebuild",
        "file_count": len(snapshot.files),
        "python_file_count": sum(1 for file in snapshot.files if file.suffix in {".py", ".pyi"}),
        "symbol_count": len(snapshot.symbols),
        "public_api_count": len(snapshot.public_api),
        "test_file_count": len(snapshot.tests_map),
        "docs_reference_count": len(snapshot.docs_references),
        "warning_count": len(snapshot.warnings),
        "fingerprint": snapshot.fingerprint,
        "outputs": sorted(f"millrace-agents/workspace-map/generated/{name}" for name in GENERATED_FILENAMES)
        + ["millrace-agents/workspace-map/manifest.json"],
    }
    freshness = {
        "schema_version": SCHEMA_VERSION,
        "status": "fresh",
        "mode": "full-rebuild",
        "fingerprint": snapshot.fingerprint,
        "file_count": len(snapshot.files),
        "warnings": list(snapshot.warnings),
    }
    imports = {
        "schema_version": SCHEMA_VERSION,
        "records": [{"path": path, "imports": imported_modules} for path, imported_modules in sorted(snapshot.imports.items())],
    }
    reverse_imports = {
        "schema_version": SCHEMA_VERSION,
        "records": [
            {"module": module, "imported_by": imported_by}
            for module, imported_by in sorted(snapshot.reverse_imports.items())
        ],
    }
    return {
        paths.workspace_map_generated_file_tree_file: _render_file_tree(snapshot.files),
        paths.workspace_map_generated_repo_map_file: _render_compact_map(snapshot.files, snapshot.symbols),
        paths.workspace_map_generated_symbols_file: _jsonl(_schema_versioned_records(snapshot.symbols)),
        paths.workspace_map_generated_imports_file: _json(imports),
        paths.workspace_map_generated_reverse_imports_file: _json(reverse_imports),
        paths.workspace_map_generated_public_api_file: _jsonl(_schema_versioned_records(snapshot.public_api)),
        paths.workspace_map_generated_tests_map_file: _jsonl(_schema_versioned_records(snapshot.tests_map)),
        paths.workspace_map_generated_docs_references_file: _jsonl(_schema_versioned_records(snapshot.docs_references)),
        paths.workspace_map_generated_freshness_file: _json(freshness),
        paths.workspace_map_manifest_file: _json(manifest),
    }


def _render_file_tree(files: tuple[FileRecord, ...]) -> str:
    lines = ["# Generated File Tree", ""]
    lines.extend(f"- {record.path}" for record in files)
    return "\n".join(lines) + "\n"


def _render_compact_map(files: tuple[FileRecord, ...], symbols: tuple[dict[str, Any], ...]) -> str:
    symbol_counts: dict[str, int] = {}
    for symbol in symbols:
        symbol_counts[symbol["path"]] = symbol_counts.get(symbol["path"], 0) + 1
    lines = [
        "# Compact Workspace Map",
        "",
        "path | kind | bytes | lines | symbols | sha256",
        "--- | --- | ---: | ---: | ---: | ---",
    ]
    for record in files:
        lines.append(
            f"{record.path} | {record.kind} | {record.size} | {record.line_count} | "
            f"{symbol_counts.get(record.path, 0)} | {record.sha256[:12]}"
        )
    return "\n".join(lines) + "\n"


def _parse_python_file(relative_path: str, text: str, warnings: list[str]) -> tuple[list[dict[str, Any]], set[str]]:
    try:
        tree = ast.parse(text)
    except SyntaxError as exc:
        warnings.append(f"python parse failed: {relative_path}:{exc.lineno or 0}")
        return [], set()
    symbols: list[dict[str, Any]] = []
    imports: set[str] = set()

    class Visitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.parents: list[str] = []

        def visit_ClassDef(self, node: ast.ClassDef) -> Any:
            self._add_symbol(node.name, "class", node.lineno)
            self.parents.append(node.name)
            self.generic_visit(node)
            self.parents.pop()

        def visit_FunctionDef(self, node: ast.FunctionDef) -> Any:
            self._add_symbol(node.name, "function", node.lineno)
            self.parents.append(node.name)
            self.generic_visit(node)
            self.parents.pop()

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> Any:
            self._add_symbol(node.name, "async_function", node.lineno)
            self.parents.append(node.name)
            self.generic_visit(node)
            self.parents.pop()

        def visit_Import(self, node: ast.Import) -> Any:
            for alias in node.names:
                imports.add(alias.name.split(".", maxsplit=1)[0])

        def visit_ImportFrom(self, node: ast.ImportFrom) -> Any:
            if node.module:
                imports.add("." * node.level + node.module if node.level else node.module)
            elif node.level:
                imports.add("." * node.level)

        def _add_symbol(self, name: str, kind: str, line: int) -> None:
            qualname = ".".join((*self.parents, name))
            symbols.append({"path": relative_path, "name": name, "qualname": qualname, "kind": kind, "line": line})

    Visitor().visit(tree)
    return symbols, imports


def _public_symbols(symbols: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "path": symbol["path"],
            "name": symbol["name"],
            "qualname": symbol["qualname"],
            "kind": symbol["kind"],
            "line": symbol["line"],
        }
        for symbol in symbols
        if not symbol["name"].startswith("_")
    ]


def _extract_doc_references(paths: WorkspacePaths, relative_path: str, text: str) -> list[dict[str, Any]]:
    references: set[str] = set()
    for match in PATH_TOKEN_PATTERN.finditer(text):
        candidate = (match.group(1) or match.group(2) or "").strip()
        if not candidate or candidate.startswith(("http://", "https://", "#")):
            continue
        clean = candidate.split("#", maxsplit=1)[0]
        if not _is_workspace_confined_posix(clean):
            continue
        if (paths.root / clean).exists():
            references.add(clean)
    return [{"path": relative_path, "target": target} for target in sorted(references)]


def _reverse_imports(imports: dict[str, list[str]]) -> dict[str, list[str]]:
    reverse: dict[str, list[str]] = {}
    for source, modules in imports.items():
        for module in modules:
            reverse.setdefault(module, []).append(source)
    return {module: sorted(sources) for module, sources in sorted(reverse.items())}


def _validate_json_file(paths: WorkspacePaths, file_path: Path) -> tuple[ValidationIssue, ...]:
    relative_path = _workspace_relative(paths, file_path)
    if not file_path.is_file():
        return ()
    try:
        payload = _read_json(file_path)
    except (OSError, json.JSONDecodeError) as exc:
        return (ValidationIssue("malformed", relative_path, str(exc)),)
    if not isinstance(payload, dict):
        return (ValidationIssue("schema_invalid", relative_path, "JSON payload must be an object"),)
    schema_version = payload.get("schema_version")
    if schema_version != SCHEMA_VERSION:
        return (ValidationIssue("schema_invalid", relative_path, "schema_version must be 1.0"),)
    if file_path.name in {"imports.json", "reverse-imports.json"}:
        records = payload.get("records")
        if not isinstance(records, list):
            return (ValidationIssue("schema_invalid", relative_path, "records must be a list"),)
        issues: list[ValidationIssue] = []
        for index, record in enumerate(records, start=1):
            if not isinstance(record, dict):
                issues.append(ValidationIssue("schema_invalid", relative_path, f"record {index}: must be an object"))
                continue
            issues.extend(_validate_record_paths(relative_path, record, f"record {index}"))
        return tuple(issues)
    return ()


def _validate_jsonl_file(paths: WorkspacePaths, file_path: Path) -> tuple[ValidationIssue, ...]:
    relative_path = _workspace_relative(paths, file_path)
    if not file_path.is_file():
        return ()
    issues: list[ValidationIssue] = []
    for index, line in enumerate(file_path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError as exc:
            issues.append(ValidationIssue("malformed", relative_path, f"line {index}: {exc}"))
            continue
        if not isinstance(payload, dict) or "path" not in payload:
            issues.append(ValidationIssue("schema_invalid", relative_path, f"line {index}: record must include path"))
            continue
        if payload.get("schema_version") != SCHEMA_VERSION:
            issues.append(ValidationIssue("schema_invalid", relative_path, f"line {index}: schema_version must be 1.0"))
        issues.extend(_validate_record_paths(relative_path, payload, f"line {index}"))
    return tuple(issues)


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def _count_jsonl_records(path: Path) -> int:
    if not path.is_file():
        return 0
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


def _json(payload: Any) -> str:
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def _jsonl(records: tuple[dict[str, Any], ...] | list[dict[str, Any]]) -> str:
    return "".join(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n" for record in records)


def _schema_versioned_records(records: tuple[dict[str, Any], ...]) -> tuple[dict[str, Any], ...]:
    return tuple({"schema_version": SCHEMA_VERSION, **record} for record in records)


def _validate_record_paths(relative_path: str, record: dict[str, Any], location: str) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for field in ("path", "target"):
        value = record.get(field)
        if value is not None and not _is_workspace_confined_posix(value):
            issues.append(ValidationIssue("non_workspace_confined", relative_path, f"{location}: invalid {field}"))
    imported_by = record.get("imported_by")
    if imported_by is not None:
        if not isinstance(imported_by, list):
            issues.append(ValidationIssue("schema_invalid", relative_path, f"{location}: imported_by must be a list"))
        else:
            for imported_by_path in imported_by:
                if not _is_workspace_confined_posix(imported_by_path):
                    issues.append(ValidationIssue("non_workspace_confined", relative_path, f"{location}: invalid imported_by"))
                    break
    return issues


def _fingerprint(files: list[FileRecord]) -> str:
    digest = hashlib.sha256()
    for record in files:
        digest.update(record.path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(record.sha256.encode("ascii"))
        digest.update(b"\0")
    return digest.hexdigest()


def _looks_binary(path: Path) -> bool:
    chunk = path.read_bytes()[:4096]
    return b"\0" in chunk


def _file_kind(relative_path: str, suffix: str) -> str:
    if _is_test_path(relative_path):
        return "test"
    if suffix == ".py":
        return "python"
    if suffix in DOC_SUFFIXES:
        return "docs"
    return "text"


def _is_test_path(relative_path: str) -> bool:
    return relative_path.startswith("tests/") or "/tests/" in relative_path or Path(relative_path).name.startswith("test_")


def _workspace_relative(paths: WorkspacePaths, path: Path) -> str:
    return path.resolve().relative_to(paths.root).as_posix()


def _workspace_lexical_relative(paths: WorkspacePaths, path: Path) -> str:
    # Preserve the workspace-lexical symlink path when reporting skips.
    return path.relative_to(paths.root).as_posix()


def _is_workspace_confined_posix(value: Any) -> bool:
    if not isinstance(value, str) or not value or "\\" in value:
        return False
    pure = Path(value)
    return not pure.is_absolute() and ".." not in pure.parts
