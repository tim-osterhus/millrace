from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = PROJECT_ROOT / "src" / "millrace"

FORBIDDEN_LAD_AUTHORITY_STRINGS = (
    "Plane.EXECUTION",
    "ExecutionStageName",
    "StageName",
    "WorkItemKind.TASK",
    "lad_codex",
    "lad_pi",
    "execution_status.md",
    "summary_status_path",
    "recovery_counters_path",
    "assets/loops/execution",
    "loops/execution",
    "millrace-agents/tasks/active",
)


def test_lad_a_does_not_reintroduce_legacy_runtime_authority_surfaces() -> None:
    offenders: list[str] = []
    for path in sorted(SOURCE_ROOT.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        for forbidden in FORBIDDEN_LAD_AUTHORITY_STRINGS:
            if forbidden in text:
                offenders.append(f"{path.relative_to(PROJECT_ROOT)}:{forbidden}")

    assert offenders == []
