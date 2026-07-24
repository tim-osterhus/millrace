from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = PROJECT_ROOT / "src" / "millrace"

GENERIC_RUNTIME_PACKAGE_NAMES = (
    "compiler",
    "contracts",
    "kernel",
    "operator",
    "substrate",
    "testing",
)
OPTIONAL_GENERIC_RUNTIME_PACKAGE_NAMES = ("runtime",)

LEGACY_IMPORT_NAMES = frozenset(
    {
        "Plane",
        "PlanningStageName",
        "StageName",
        "WorkItemKind",
    }
)
LEGACY_TYPE_REFERENCE_NAMES = frozenset(
    {
        "PlanningStageName",
        "StageName",
    }
)
LEGACY_ENUM_AUTHORITY_MEMBERS = frozenset(
    {
        ("Plane", "PLANNING"),
        ("WorkItemKind", "SPEC"),
        ("WorkItemKind", "PROBE"),
        ("WorkItemKind", "INCIDENT"),
        ("WorkItemKind", "TASK"),
    }
)

LEGACY_MODE_AND_ALIAS_LITERALS = frozenset(
    {
        "lad_codex",
        "lad_pi",
        "default_codex",
        "standard_plain",
    }
)
LEGACY_MODE_AND_ALIAS_PREFIXES = ("learning_lad_",)

LEGACY_PLANNING_PATH_FRAGMENTS = (
    "assets/loops/planning/lad.json",
    "assets/loops/planning",
    "loops/planning/lad.json",
    "millrace-agents/specs",
    "millrace-agents/probes",
    "millrace-agents/incidents",
    "millrace-agents/arbiter",
    "planning_status.md",
    "closure_target.json",
    "closure_targets.json",
)

LAD_PLANNING_BRANCH_STRINGS = frozenset(
    {
        "planning",
        "execution",
        "spec",
        "probe",
        "incident",
        "task",
        "recon",
        "planner",
        "manager",
        "mechanic",
        "auditor",
        "arbiter",
        "lad_planner",
        "lad_manager",
        "lad_mechanic",
        "lad_auditor",
        "lad_arbiter",
    }
)


@dataclass(frozen=True, slots=True)
class AuthorityFinding:
    path: Path
    line: int
    kind: str
    evidence: str


def _generic_runtime_package_roots(
    package_root: Path,
    *,
    package_names: tuple[str, ...] = GENERIC_RUNTIME_PACKAGE_NAMES,
) -> list[Path]:
    roots: list[Path] = []
    for package_name in package_names:
        package_path = package_root / package_name
        assert package_path.exists(), f"missing package: {package_path}"
        roots.append(package_path)

    for package_name in OPTIONAL_GENERIC_RUNTIME_PACKAGE_NAMES:
        package_path = package_root / package_name
        if package_path.exists():
            roots.append(package_path)

    return roots


def _runtime_python_files(
    package_root: Path,
    *,
    package_names: tuple[str, ...] = GENERIC_RUNTIME_PACKAGE_NAMES,
) -> list[Path]:
    paths: list[Path] = []
    for package_path in _generic_runtime_package_roots(
        package_root,
        package_names=package_names,
    ):
        paths.extend(package_path.rglob("*.py"))
    return sorted(paths)


def _dotted_parts(node: ast.AST) -> tuple[str, ...]:
    if isinstance(node, ast.Name):
        return (node.id,)
    if isinstance(node, ast.Attribute):
        return (*_dotted_parts(node.value), node.attr)
    return ()


def _imported_legacy_aliases(tree: ast.AST) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        for alias in node.names:
            if alias.name in LEGACY_IMPORT_NAMES:
                aliases[alias.asname or alias.name] = alias.name
    return aliases


def _normalized_parts(
    parts: tuple[str, ...],
    imported_aliases: dict[str, str],
) -> tuple[str, ...]:
    if parts and parts[0] in imported_aliases:
        return (imported_aliases[parts[0]], *parts[1:])
    return parts


def _enum_authority_member(
    parts: tuple[str, ...],
    imported_aliases: dict[str, str],
) -> str | None:
    normalized = _normalized_parts(parts, imported_aliases)
    for index in range(len(normalized) - 1):
        pair = (normalized[index], normalized[index + 1])
        if pair in LEGACY_ENUM_AUTHORITY_MEMBERS:
            return ".".join(pair)
    return None


def _legacy_string_authorities(value: str) -> list[str]:
    findings: list[str] = []
    if value in LEGACY_MODE_AND_ALIAS_LITERALS:
        findings.append(value)
    if any(value.startswith(prefix) for prefix in LEGACY_MODE_AND_ALIAS_PREFIXES):
        findings.append(value)
    for fragment in LEGACY_PLANNING_PATH_FRAGMENTS:
        if fragment in value:
            findings.append(fragment)
    return findings


def _string_constants(node: ast.AST) -> list[tuple[str, int]]:
    strings: list[tuple[str, int]] = []
    for child in ast.walk(node):
        if isinstance(child, ast.Constant) and isinstance(child.value, str):
            strings.append((child.value, child.lineno))
    return strings


def _branch_literal_findings(
    node: ast.AST,
    relative_path: Path,
) -> list[AuthorityFinding]:
    findings: list[AuthorityFinding] = []
    branch_nodes: list[ast.AST] = []
    if isinstance(node, (ast.If, ast.IfExp, ast.While, ast.Assert)):
        branch_nodes.append(node.test)
    elif isinstance(node, ast.Match):
        branch_nodes.extend(case.pattern for case in node.cases)

    for branch_node in branch_nodes:
        for value, line in _string_constants(branch_node):
            if value in LAD_PLANNING_BRANCH_STRINGS:
                findings.append(
                    AuthorityFinding(
                        path=relative_path,
                        line=line,
                        kind="lad_planning_name_branch",
                        evidence=value,
                    )
                )
    return findings


def _authority_findings_for_file(
    path: Path,
    package_root: Path,
) -> list[AuthorityFinding]:
    relative_path = path.relative_to(package_root)
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported_aliases = _imported_legacy_aliases(tree)
    findings: list[AuthorityFinding] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name in LEGACY_IMPORT_NAMES:
                    findings.append(
                        AuthorityFinding(
                            path=relative_path,
                            line=node.lineno,
                            kind="legacy_lad_authority_import",
                            evidence=alias.name,
                        )
                    )
            continue

        if isinstance(node, ast.Attribute):
            parts = _dotted_parts(node)
            enum_member = _enum_authority_member(parts, imported_aliases)
            if enum_member is not None:
                findings.append(
                    AuthorityFinding(
                        path=relative_path,
                        line=node.lineno,
                        kind="legacy_lad_enum_authority",
                        evidence=enum_member,
                    )
                )
            if any(part in LEGACY_TYPE_REFERENCE_NAMES for part in parts):
                findings.append(
                    AuthorityFinding(
                        path=relative_path,
                        line=node.lineno,
                        kind="legacy_lad_type_authority",
                        evidence=".".join(parts),
                    )
                )
            continue

        if isinstance(node, ast.Name):
            normalized = imported_aliases.get(node.id, node.id)
            if normalized in LEGACY_TYPE_REFERENCE_NAMES:
                findings.append(
                    AuthorityFinding(
                        path=relative_path,
                        line=node.lineno,
                        kind="legacy_lad_type_authority",
                        evidence=normalized,
                    )
                )
            continue

        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            for legacy_string in _legacy_string_authorities(node.value):
                findings.append(
                    AuthorityFinding(
                        path=relative_path,
                        line=node.lineno,
                        kind="legacy_lad_mode_or_path_authority",
                        evidence=legacy_string,
                    )
                )
            continue

        findings.extend(_branch_literal_findings(node, relative_path))

    return findings


def _runtime_authority_findings(
    package_root: Path,
    *,
    package_names: tuple[str, ...] = GENERIC_RUNTIME_PACKAGE_NAMES,
) -> list[AuthorityFinding]:
    findings: list[AuthorityFinding] = []
    for path in _runtime_python_files(package_root, package_names=package_names):
        findings.extend(_authority_findings_for_file(path, package_root))
    return findings


def _write_probe_package(tmp_path: Path, source: str) -> Path:
    package_root = tmp_path / "src" / "millrace"
    package_path = package_root / "kernel"
    package_path.mkdir(parents=True)
    (package_path / "__init__.py").write_text("", encoding="utf-8")
    (package_path / "probe.py").write_text(source, encoding="utf-8")
    return package_root


def test_generic_runtime_omits_lad_b_legacy_compatibility_authority() -> None:
    assert _runtime_authority_findings(PACKAGE_ROOT) == []


def test_selected_lad_planning_workflow_data_is_outside_guardrail() -> None:
    workflow_path = PACKAGE_ROOT / "workflows" / "lad_planning.py"
    workflow_text = workflow_path.read_text(encoding="utf-8")

    assert "planning.lad" in workflow_text
    assert "lad_planner" in workflow_text
    assert "spec" in workflow_text
    assert _runtime_authority_findings(PACKAGE_ROOT) == []


def test_detector_catches_legacy_planning_enum_authority_probe(
    tmp_path: Path,
) -> None:
    package_root = _write_probe_package(
        tmp_path,
        "\n".join(
            (
                "from legacy_lad import Plane as RuntimePlane",
                "from legacy_lad import PlanningStageName",
                "from legacy_lad import WorkItemKind as Kind",
                "",
                "def select(stage_kind, work_item_kind):",
                "    if RuntimePlane.PLANNING:",
                "        return PlanningStageName.MANAGER",
                "    if work_item_kind == Kind.SPEC:",
                "        return stage_kind",
                "    return None",
            )
        ),
    )

    findings = _runtime_authority_findings(package_root, package_names=("kernel",))
    finding_keys = {(finding.kind, finding.evidence) for finding in findings}

    assert ("legacy_lad_authority_import", "Plane") in finding_keys
    assert ("legacy_lad_authority_import", "PlanningStageName") in finding_keys
    assert ("legacy_lad_authority_import", "WorkItemKind") in finding_keys
    assert ("legacy_lad_enum_authority", "Plane.PLANNING") in finding_keys
    assert ("legacy_lad_enum_authority", "WorkItemKind.SPEC") in finding_keys
    assert ("legacy_lad_type_authority", "PlanningStageName") in finding_keys


def test_detector_catches_hidden_modes_old_aliases_and_paths_probe(
    tmp_path: Path,
) -> None:
    package_root = _write_probe_package(
        tmp_path,
        "\n".join(
            (
                'IMPLICIT_WORKFLOWS = {"lad_codex": "planning.lad"}',
                'PI_MODE = "lad_pi"',
                'LEARNING_MODE = "learning_lad_researcher"',
                'DEFAULT_MODE = "standard_plain"',
                'OLD_MODE = "default_codex"',
                'OLD_LOOP_PATH = "assets/loops/planning/lad.json"',
                'OLD_STATUS = "millrace-agents/specs/active/planning_status.md"',
                'OLD_CLOSURE = "millrace-agents/arbiter/closure_target.json"',
            )
        ),
    )

    findings = _runtime_authority_findings(package_root, package_names=("kernel",))
    evidence = {finding.evidence for finding in findings}

    assert {
        "lad_codex",
        "lad_pi",
        "learning_lad_researcher",
        "standard_plain",
        "default_codex",
        "assets/loops/planning/lad.json",
        "millrace-agents/specs",
        "millrace-agents/arbiter",
        "planning_status.md",
        "closure_target.json",
    } <= evidence


def test_detector_catches_lad_planning_name_branch_probe(tmp_path: Path) -> None:
    package_root = _write_probe_package(
        tmp_path,
        "\n".join(
            (
                "def dispatch(stage_kind_id, queue_family_id):",
                '    if stage_kind_id == "planner":',
                '        return "selected_id_required"',
                '    if queue_family_id in {"spec", "probe"}:',
                '        return "selected_route_required"',
                "    match stage_kind_id:",
                '        case "arbiter":',
                '            return "selected_closure_policy_required"',
                "    return None",
            )
        ),
    )

    findings = _runtime_authority_findings(package_root, package_names=("kernel",))
    branch_evidence = {
        finding.evidence
        for finding in findings
        if finding.kind == "lad_planning_name_branch"
    }

    assert {"planner", "spec", "probe", "arbiter"} <= branch_evidence


def test_detector_allows_opaque_selected_id_comparisons_probe(
    tmp_path: Path,
) -> None:
    package_root = _write_probe_package(
        tmp_path,
        "\n".join(
            (
                "def is_selected_stage(stage_kind_id, selected_stage_kind_id):",
                "    if stage_kind_id == selected_stage_kind_id:",
                "        return True",
                "    return False",
            )
        ),
    )

    assert _runtime_authority_findings(
        package_root,
        package_names=("kernel",),
    ) == []
