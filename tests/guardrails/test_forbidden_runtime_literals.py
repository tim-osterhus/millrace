from __future__ import annotations

import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = PROJECT_ROOT / "src" / "millrace"

FORBIDDEN_KERNEL_LITERALS = (
    "execution",
    "planning",
    "learning",
    "task",
    "spec",
    "probe",
    "idea",
    "builder",
    "checker",
    "planner",
    "arbiter",
    "craft",
    "prompt",
    "task_artifact",
    "task_incident",
    "BLOCKED",
    "NEEDS_REVIEW",
    "TASK_COMPLETE",
    "WORK_COMPLETE",
    "Taskmaster",
    "Worker",
    "kernel_ping",
    "simple_loop",
    "LAD",
)

HOSTED_WORKFLOW_LITERALS = (
    "kernel_ping",
    "Taskmaster",
    "Worker",
    "TASK_COMPLETE",
    "WORK_COMPLETE",
    "NEEDS_REVIEW",
    "BLOCKED",
    "craft",
    "prompt",
    "task_artifact",
    "task_incident",
)

LEGACY_HOSTED_WORKFLOW_LITERALS = tuple(
    literal for literal in HOSTED_WORKFLOW_LITERALS if literal != "prompt"
)

SIMPLE_LOOP_HOSTED_LITERALS = (
    "simple_loop",
    "simple_loop.manager",
    "simple_loop.worker",
    "simple_loop.reviewer",
    "simple_loop.troubleshooter",
    "work_prompt",
    "work_packet",
    "gap_packet",
    "incident_report",
    "completion_definition",
)

SIMPLE_LOOP_ROLE_LITERALS = (
    "manager",
    "worker",
    "reviewer",
    "troubleshooter",
)

LAD_B_HOSTED_LITERALS = (
    "planning",
    "spec",
    "probe",
    "incident",
    "recon",
    "planner",
    "manager",
    "mechanic",
    "auditor",
    "arbiter",
)

LAD_B_COMPOUND_GENERIC_LITERALS = (
    "root_spec_id",
    "ClosureArbiterActivationRecord",
    "closure_arbiter_activations",
    "RecordClosureArbiterActivation",
    "mutation.record_closure_arbiter_activation",
    "ClosureArbiterActivationRow",
    "closure_arbiter_activation",
    "RemediationIncidentRecord",
    "remediation_incidents",
    "RecordRemediationIncident",
    "mutation.record_remediation_incident",
    "RemediationIncidentRow",
    "remediation_incident",
)

PLANE_DIRECTED_TERMINAL_ACTION_LITERALS = (
    "escalate_to_planning",
    "planning_escalation",
    "close_to_planning",
    "route_to_planning",
)

VENDOR_SELECTION_HOSTED_LITERALS = (
    "vendor_selection",
    "request_intake",
    "candidate_packager",
    "rubric_evaluator",
    "conflict_checker",
    "award_decider",
    "vendor_selection.award_operator_wait",
)

TEXT_FILE_SUFFIXES = frozenset({".md", ".py", ".rst", ".txt"})


def _kernel_package() -> Path:
    package_path = PACKAGE_ROOT / "kernel"
    assert package_path.exists(), f"missing package: {package_path}"
    return package_path


def _text_files(root: Path) -> list[Path]:
    return sorted(
        path
        for path in root.rglob("*")
        if path.is_file() and path.suffix in TEXT_FILE_SUFFIXES
    )


def _literal_matches(
    root: Path,
    literals: tuple[str, ...] = FORBIDDEN_KERNEL_LITERALS,
) -> list[tuple[Path, str]]:
    matches: list[tuple[Path, str]] = []
    for path in _text_files(root):
        text = path.read_text(encoding="utf-8")
        for literal in literals:
            pattern = re.compile(
                rf"(?<![A-Za-z0-9_]){re.escape(literal)}(?![A-Za-z0-9_])"
            )
            if pattern.search(text):
                matches.append((path.relative_to(root), literal))
    return matches


def test_lad_b_literal_guardrail_still_catches_probe_in_generic_source(
    tmp_path: Path,
) -> None:
    source_root = tmp_path / "operator"
    source_root.mkdir()
    (source_root / "generic.py").write_text(
        'SYNTHETIC = "probe"\n',
        encoding="utf-8",
    )

    assert _literal_matches(source_root, LAD_B_HOSTED_LITERALS) == [
        (Path("generic.py"), "probe")
    ]


def test_kernel_package_omits_workflow_fixture_and_legacy_literals() -> None:
    assert _literal_matches(_kernel_package()) == []


def test_hosted_workflow_docs_are_outside_kernel_literal_guardrail() -> None:
    workflows_readme = PACKAGE_ROOT / "workflows" / "README.md"
    assert workflows_readme.exists(), (
        f"missing hosted workflow docs: {workflows_readme}"
    )
    workflow_docs = workflows_readme.read_text(encoding="utf-8")
    assert "kernel_ping" in workflow_docs
    assert "simple_loop" in workflow_docs
    assert "LAD" in workflow_docs
    assert _literal_matches(_kernel_package()) == []


def test_runtime_source_has_no_hosted_workflow_branches() -> None:
    matches: list[tuple[Path, str]] = []
    for package_name in ("compiler", "contracts", "kernel", "substrate", "operator"):
        package_path = PACKAGE_ROOT / package_name
        for relative_path, literal in _literal_matches(
            package_path,
            SIMPLE_LOOP_HOSTED_LITERALS,
        ):
            matches.append((Path(package_name) / relative_path, literal))

    assert matches == []


def test_runtime_boundaries_omit_hosted_role_literals() -> None:
    matches: list[tuple[Path, str]] = []
    for package_name in ("compiler", "contracts", "kernel", "substrate", "operator"):
        package_path = PACKAGE_ROOT / package_name
        for relative_path, literal in _literal_matches(
            package_path,
            SIMPLE_LOOP_ROLE_LITERALS,
        ):
            matches.append((Path(package_name) / relative_path, literal))

    assert matches == []


def test_runtime_boundaries_omit_lad_b_hosted_literals() -> None:
    matches: list[tuple[Path, str]] = []
    for package_name in ("compiler", "contracts", "kernel", "substrate", "operator"):
        package_path = PACKAGE_ROOT / package_name
        for relative_path, literal in _literal_matches(
            package_path,
            LAD_B_HOSTED_LITERALS,
        ):
            matches.append((Path(package_name) / relative_path, literal))

    assert matches == []


def test_runtime_boundaries_omit_lad_b_compound_generic_literals() -> None:
    matches: list[tuple[Path, str]] = []
    for package_name in ("compiler", "contracts", "kernel", "substrate", "operator"):
        package_path = PACKAGE_ROOT / package_name
        for relative_path, literal in _literal_matches(
            package_path,
            LAD_B_COMPOUND_GENERIC_LITERALS,
        ):
            matches.append((Path(package_name) / relative_path, literal))

    assert matches == []


def test_generic_packages_omit_plane_directed_terminal_action_literals() -> None:
    matches: list[tuple[Path, str]] = []
    for package_name in ("compiler", "contracts", "kernel", "substrate", "operator"):
        package_path = PACKAGE_ROOT / package_name
        for relative_path, literal in _literal_matches(
            package_path,
            PLANE_DIRECTED_TERMINAL_ACTION_LITERALS,
        ):
            matches.append((Path(package_name) / relative_path, literal))

    assert matches == []


def test_no_vendor_selection_branches_in_generic_source() -> None:
    matches: list[tuple[Path, str]] = []
    for package_name in ("compiler", "contracts", "kernel", "substrate", "operator"):
        package_path = PACKAGE_ROOT / package_name
        for relative_path, literal in _literal_matches(
            package_path,
            VENDOR_SELECTION_HOSTED_LITERALS,
        ):
            matches.append((Path(package_name) / relative_path, literal))

    assert matches == []


def test_vendor_selection_literal_guardrail_covers_evaluator_stage_literals() -> None:
    assert "rubric_evaluator" in VENDOR_SELECTION_HOSTED_LITERALS
    assert "conflict_checker" in VENDOR_SELECTION_HOSTED_LITERALS


def test_compiler_contracts_and_kernel_omit_legacy_hosted_literals() -> None:
    matches: list[tuple[Path, str]] = []
    for package_name in ("compiler", "contracts", "kernel"):
        package_path = PACKAGE_ROOT / package_name
        for relative_path, literal in _literal_matches(
            package_path,
            LEGACY_HOSTED_WORKFLOW_LITERALS,
        ):
            matches.append((Path(package_name) / relative_path, literal))

    assert matches == []


def test_forbidden_literal_detector_catches_taskmaster_probe(tmp_path: Path) -> None:
    kernel_root = tmp_path / "kernel"
    kernel_root.mkdir()
    (kernel_root / "probe.py").write_text("Taskmaster\n", encoding="utf-8")

    assert _literal_matches(kernel_root) == [(Path("probe.py"), "Taskmaster")]


def test_forbidden_literal_detector_catches_worker_probe(tmp_path: Path) -> None:
    kernel_root = tmp_path / "kernel"
    kernel_root.mkdir()
    (kernel_root / "probe.py").write_text("Worker\n", encoding="utf-8")

    assert _literal_matches(kernel_root) == [(Path("probe.py"), "Worker")]


def test_forbidden_literal_detector_catches_simple_loop_role_probe(
    tmp_path: Path,
) -> None:
    kernel_root = tmp_path / "kernel"
    kernel_root.mkdir()
    (kernel_root / "probe.py").write_text(
        "manager\nworker\nreviewer\ntroubleshooter\n",
        encoding="utf-8",
    )

    assert _literal_matches(kernel_root, SIMPLE_LOOP_ROLE_LITERALS) == [
        (Path("probe.py"), "manager"),
        (Path("probe.py"), "worker"),
        (Path("probe.py"), "reviewer"),
        (Path("probe.py"), "troubleshooter"),
    ]


def test_forbidden_literal_detector_ignores_non_source_files(
    tmp_path: Path,
) -> None:
    kernel_root = tmp_path / "kernel"
    kernel_root.mkdir()
    (kernel_root / "probe.py").write_text("Taskmaster\n", encoding="utf-8")
    pycache = kernel_root / "__pycache__"
    pycache.mkdir()
    (pycache / "probe.pyc").write_bytes(b"\x00Taskmaster\xff")
    (kernel_root / "binary.dat").write_bytes(b"\x00Worker\xff")

    assert _literal_matches(kernel_root) == [(Path("probe.py"), "Taskmaster")]


def test_forbidden_literal_detector_catches_craft_and_prompt_probes(
    tmp_path: Path,
) -> None:
    kernel_root = tmp_path / "kernel"
    kernel_root.mkdir()
    (kernel_root / "probe.py").write_text(
        '"craft"\n"prompt"\n"task_artifact"\n"task_incident"\n',
        encoding="utf-8",
    )

    assert _literal_matches(
        kernel_root,
        ("craft", "prompt", "task_artifact", "task_incident"),
    ) == [
        (Path("probe.py"), "craft"),
        (Path("probe.py"), "prompt"),
        (Path("probe.py"), "task_artifact"),
        (Path("probe.py"), "task_incident"),
    ]


def test_forbidden_literal_detector_catches_plane_directed_terminal_action_probes(
    tmp_path: Path,
) -> None:
    kernel_root = tmp_path / "kernel"
    kernel_root.mkdir()
    (kernel_root / "probe.py").write_text(
        "\n".join(PLANE_DIRECTED_TERMINAL_ACTION_LITERALS),
        encoding="utf-8",
    )

    assert _literal_matches(
        kernel_root,
        PLANE_DIRECTED_TERMINAL_ACTION_LITERALS,
    ) == [
        (Path("probe.py"), "escalate_to_planning"),
        (Path("probe.py"), "planning_escalation"),
        (Path("probe.py"), "close_to_planning"),
        (Path("probe.py"), "route_to_planning"),
    ]


def test_forbidden_literal_detector_avoids_noisy_substring_matches(
    tmp_path: Path,
) -> None:
    kernel_root = tmp_path / "kernel"
    kernel_root.mkdir()
    (kernel_root / "probe.py").write_text(
        "craftsmanship\nprompt_id\ntask_artifact_id\n",
        encoding="utf-8",
    )

    assert (
        _literal_matches(
            kernel_root,
            ("craft", "prompt", "task_artifact"),
        )
        == []
    )
