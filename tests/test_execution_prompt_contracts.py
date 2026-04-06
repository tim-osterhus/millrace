from __future__ import annotations

from pathlib import Path

import pytest

from millrace_engine.execution_prompt_contracts import iter_critical_execution_prompt_contracts

MILLRACE_ROOT = Path(__file__).resolve().parents[1]
AGENTS_ASSETS = MILLRACE_ROOT / "millrace_engine" / "assets" / "agents"

@pytest.mark.parametrize("contract", iter_critical_execution_prompt_contracts())
def test_critical_execution_entrypoints_retain_structured_contract(contract) -> None:
    prompt_path = contract.prompt_path

    assert prompt_path.is_file(), contract.prompt_asset
    contents = prompt_path.read_text(encoding="utf-8")
    nonempty_lines = [line for line in contents.splitlines() if line.strip()]

    assert len(nonempty_lines) >= contract.minimum_nonempty_lines, contract.prompt_asset
    required_markers = (
        contract.required_subordinate_docs
        + contract.required_artifacts
        + contract.required_report_outputs
        + contract.terminal_marker_lines
        + contract.required_phrases
    )
    for marker in required_markers:
        assert marker in contents, f"{contract.prompt_asset} missing {marker!r}"


@pytest.mark.parametrize(
    ("relative_path", "minimum_nonempty_lines", "required_markers"),
    (
        (
            "_hotfix.md",
            20,
            (
                "agents/quickfix.md",
                "agents/prompts/quickfix.md",
                "agents/historylog.md",
                "agents/reports/",
                "### BUILDER_COMPLETE",
                "### BLOCKED",
            ),
        ),
        (
            "_doublecheck.md",
            30,
            (
                "agents/quickfix.md",
                "agents/expectations.md",
                "agents/prompts/qa_cycle.md",
                "agents/roles/qa-test-engineer.md",
                "agents/historylog.md",
                "### QA_COMPLETE",
                "### QUICKFIX_NEEDED",
                "### BLOCKED",
            ),
        ),
        (
            "_troubleshoot.md",
            35,
            (
                "agents/status_contract.md",
                "agents/historylog.md",
                "agents/runs/",
                "agents/diagnostics/",
                "MILLRACE_RUN_DIR",
                "agents/reports/troubleshoot_report.md",
                "### TROUBLESHOOT_COMPLETE",
                "### BLOCKED",
            ),
        ),
        (
            "_consult.md",
            25,
            (
                "agents/prompts/consult_cycle.md",
                "agents/tasksblocker.md",
                "agents/ideas/incidents/",
                "agents/status_contract.md",
                "### CONSULT_COMPLETE",
                "### NEEDS_RESEARCH",
                "### BLOCKED",
            ),
        ),
        (
            "_update.md",
            25,
            (
                "agents/tasksarchive.md",
                "agents/tasksbacklog.md",
                "agents/historylog.md",
                "agents/roadmap.md",
                "agents/roadmapchecklist.md",
                "agents/status_contract.md",
                "### UPDATE_COMPLETE",
                "### BLOCKED",
            ),
        ),
    ),
)
def test_noncritical_execution_entrypoints_retain_controller_contract(
    relative_path: str,
    minimum_nonempty_lines: int,
    required_markers: tuple[str, ...],
) -> None:
    prompt_path = AGENTS_ASSETS / relative_path

    assert prompt_path.is_file(), relative_path
    contents = prompt_path.read_text(encoding="utf-8")
    nonempty_lines = [line for line in contents.splitlines() if line.strip()]

    assert len(nonempty_lines) >= minimum_nonempty_lines, relative_path
    for marker in required_markers:
        assert marker in contents, f"{relative_path} missing {marker!r}"


@pytest.mark.parametrize(
    ("relative_path", "required_markers"),
    (
        (
            "prompts/builder_cycle.md",
            (
                "agents/skills/skills_index.md",
                "agents/status.md",
                "last repo mutation",
                "end your final response with the same marker",
                "Do not run more commands, edit more files, or notify another agent after the marker.",
            ),
        ),
        (
            "prompts/run_prompt.md",
            (
                "agents/prompts/completed/",
                "Do not notify another agent directly.",
                "Do not start another planning cycle",
                "agents/historylog.md",
            ),
        ),
        (
            "prompts/qa_cycle.md",
            (
                "agents/expectations.md",
                "agents/historylog.md",
                "last repo mutation",
                "end your final response with the same marker",
                "Do not run more commands, edit more files, or notify another agent after the marker.",
            ),
        ),
    ),
)
def test_shared_execution_prompt_helpers_retain_terminal_contract(
    relative_path: str,
    required_markers: tuple[str, ...],
) -> None:
    prompt_path = AGENTS_ASSETS / relative_path

    assert prompt_path.is_file(), relative_path
    contents = prompt_path.read_text(encoding="utf-8")

    for marker in required_markers:
        assert marker in contents, f"{relative_path} missing {marker!r}"
