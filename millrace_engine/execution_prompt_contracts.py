"""Structured contracts for critical execution prompt assets.

This module owns only the machine-checkable execution-stage prompt policy:

- required section layout for the critical Builder / Integration / QA entrypoints
- required subordinate-doc references, runtime artifact/report references, and
  completion-marker obligations inside those sections
- stage terminal markers derived from runtime stage policy

The markdown assets remain the instruction layer for operator-facing prose,
persona wording, examples, and the exact explanatory detail inside each section.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .contracts import StageType
from .stages.builder import BuilderStage
from .stages.integrate import IntegrationStage
from .stages.qa import QAStage


AGENTS_ASSETS_DIR = Path(__file__).resolve().parent / "assets" / "agents"


@dataclass(frozen=True, slots=True)
class PromptSectionContract:
    """Structured obligations that must appear within one prompt section."""

    heading: str
    subordinate_docs: tuple[str, ...] = ()
    artifacts: tuple[str, ...] = ()
    report_outputs: tuple[str, ...] = ()
    literals: tuple[str, ...] = ()
    require_terminal_markers: bool = False


@dataclass(frozen=True, slots=True)
class MarkdownSection:
    """One parsed markdown heading and its body."""

    heading: str
    body: str
    level: int


@dataclass(frozen=True, slots=True)
class ExecutionPromptContract:
    """Machine-checkable contract for a critical execution prompt asset."""

    stage_type: StageType
    prompt_asset: str
    policy_sections: tuple[PromptSectionContract, ...]

    @property
    def prompt_path(self) -> Path:
        return AGENTS_ASSETS_DIR / self.prompt_asset

    @property
    def terminal_marker_lines(self) -> tuple[str, ...]:
        stage_cls = _STAGE_CLASSES[self.stage_type]
        return tuple(f"### {status}" for status in stage_cls.terminal_statuses)

    @property
    def required_section_headings(self) -> tuple[str, ...]:
        return tuple(section.heading for section in self.policy_sections)

    @property
    def required_subordinate_docs(self) -> tuple[str, ...]:
        return tuple(
            doc
            for section in self.policy_sections
            for doc in section.subordinate_docs
        )

    @property
    def required_artifacts(self) -> tuple[str, ...]:
        return tuple(
            artifact
            for section in self.policy_sections
            for artifact in section.artifacts
        )

    @property
    def required_report_outputs(self) -> tuple[str, ...]:
        return tuple(
            output
            for section in self.policy_sections
            for output in section.report_outputs
        )


_STAGE_CLASSES = {
    StageType.BUILDER: BuilderStage,
    StageType.INTEGRATION: IntegrationStage,
    StageType.QA: QAStage,
}


CRITICAL_EXECUTION_PROMPT_CONTRACTS: tuple[ExecutionPromptContract, ...] = (
    ExecutionPromptContract(
        stage_type=StageType.BUILDER,
        prompt_asset="_start.md",
        policy_sections=(
            PromptSectionContract(
                heading="Inputs (read in order)",
                subordinate_docs=(
                    "agents/outline.md",
                    "agents/tasks.md",
                    "README.md",
                    "agents/status_contract.md",
                ),
            ),
            PromptSectionContract(
                heading="Prompt artifact handling (always before implementation)",
                subordinate_docs=("agents/prompts/create_prompt.md",),
                artifacts=(
                    "agents/prompts/tasks/###-slug.md",
                    "agents/historylog.md",
                ),
                literals=("### BLOCKED",),
            ),
            PromptSectionContract(
                heading="Specialist workflow (strict order)",
                subordinate_docs=(
                    "agents/roles/planner-architect.md",
                    "agents/prompts/run_prompt.md",
                    "agents/prompts/builder_cycle.md",
                ),
            ),
            PromptSectionContract(
                heading="Artifact and reporting contract",
                subordinate_docs=("agents/tasks.md",),
                artifacts=("agents/historylog.md",),
                report_outputs=("agents/reports/",),
            ),
            PromptSectionContract(
                heading="Completion signaling",
                subordinate_docs=("agents/status_contract.md",),
                literals=(
                    "agents/status.md",
                    "last repo mutation",
                    "End your final response with the same marker",
                ),
                require_terminal_markers=True,
            ),
        ),
    ),
    ExecutionPromptContract(
        stage_type=StageType.INTEGRATION,
        prompt_asset="_integrate.md",
        policy_sections=(
            PromptSectionContract(
                heading="Inputs (read in order)",
                subordinate_docs=(
                    "agents/outline.md",
                    "agents/tasks.md",
                    "agents/status_contract.md",
                    "agents/historylog.md",
                    "agents/roles/integration-steward.md",
                ),
            ),
            PromptSectionContract(
                heading="Phase 0 — Deterministic report path",
                report_outputs=(
                    "integration_report.md",
                    "agents/integration_report.md",
                    "agents/reports/integration_",
                ),
                literals=("MILLRACE_RUN_DIR",),
            ),
            PromptSectionContract(
                heading="Phase 1 — Gate discovery",
                subordinate_docs=(
                    "agents/tasks.md",
                    "agents/outline.md",
                ),
                literals=("Do not invent commands.",),
            ),
            PromptSectionContract(
                heading="Phase 2 — Execute only explicit checks",
                literals=("PASS, FAIL, or BLOCKED",),
            ),
            PromptSectionContract(
                heading="Phase 3 — Write the Integration Report",
                artifacts=("agents/historylog.md",),
                report_outputs=("# Integration Report",),
                literals=("no-command decision",),
            ),
            PromptSectionContract(
                heading="Completion signaling",
                subordinate_docs=("agents/status_contract.md",),
                literals=("agents/status.md",),
                require_terminal_markers=True,
            ),
        ),
    ),
    ExecutionPromptContract(
        stage_type=StageType.QA,
        prompt_asset="_check.md",
        policy_sections=(
            PromptSectionContract(
                heading="Phase 1 — Understand requirements before inspecting implementation",
                subordinate_docs=(
                    "agents/outline.md",
                    "agents/tasks.md",
                    "README.md",
                    "agents/status_contract.md",
                    "agents/roles/qa-test-engineer.md",
                ),
                literals=(
                    "Do not read `agents/historylog.md`.",
                    "Do not inspect diffs, `git status`, or prior test output.",
                    "Do not read builder notes yet.",
                ),
            ),
            PromptSectionContract(
                heading="Phase 2 — Write expectations first",
                artifacts=("agents/expectations.md",),
                report_outputs=(
                    "agents/runs/<RUN_ID>/integration_report.md",
                    "agents/integration_report.md",
                ),
                literals=("### BLOCKED",),
            ),
            PromptSectionContract(
                heading="Phase 3 — Validate against reality",
                subordinate_docs=("agents/prompts/qa_cycle.md",),
                artifacts=(
                    "agents/historylog.md",
                    "agents/quickfix.md",
                ),
            ),
            PromptSectionContract(
                heading="Output requirements",
                artifacts=(
                    "agents/expectations.md",
                    "agents/historylog.md",
                    "agents/quickfix.md",
                ),
            ),
            PromptSectionContract(
                heading="Completion signaling",
                subordinate_docs=("agents/status_contract.md",),
                literals=(
                    "agents/status.md",
                    "last repo mutation",
                    "End your final response with the same marker",
                ),
                require_terminal_markers=True,
            ),
        ),
    ),
)


def parse_markdown_sections(markdown_text: str) -> tuple[MarkdownSection, ...]:
    """Parse headings and bodies from a markdown document."""

    sections: list[MarkdownSection] = []
    current_heading: str | None = None
    current_level: int | None = None
    current_body: list[str] = []

    for line in markdown_text.splitlines():
        if line.startswith("#"):
            stripped = line.lstrip("#")
            level = len(line) - len(stripped)
            heading = stripped.strip()
            if heading:
                if current_heading is not None and current_level is not None:
                    sections.append(
                        MarkdownSection(
                            heading=current_heading,
                            body="\n".join(current_body).strip(),
                            level=current_level,
                        )
                    )
                current_heading = heading
                current_level = level
                current_body = []
                continue
        if current_heading is not None:
            current_body.append(line)

    if current_heading is not None and current_level is not None:
        sections.append(
            MarkdownSection(
                heading=current_heading,
                body="\n".join(current_body).strip(),
                level=current_level,
            )
        )

    return tuple(sections)


def iter_critical_execution_prompt_contracts() -> tuple[ExecutionPromptContract, ...]:
    """Return the covered execution prompt contracts in stable order."""

    return CRITICAL_EXECUTION_PROMPT_CONTRACTS
