"""Structured contracts for critical execution prompt assets."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .contracts import StageType
from .stages.builder import BuilderStage
from .stages.integrate import IntegrationStage
from .stages.qa import QAStage


AGENTS_ASSETS_DIR = Path(__file__).resolve().parent / "assets" / "agents"


@dataclass(frozen=True, slots=True)
class ExecutionPromptContract:
    """Machine-checkable contract for a critical execution prompt asset."""

    stage_type: StageType
    prompt_asset: str
    minimum_nonempty_lines: int
    required_subordinate_docs: tuple[str, ...]
    required_artifacts: tuple[str, ...]
    required_report_outputs: tuple[str, ...]
    required_phrases: tuple[str, ...] = ()

    @property
    def prompt_path(self) -> Path:
        return AGENTS_ASSETS_DIR / self.prompt_asset

    @property
    def terminal_marker_lines(self) -> tuple[str, ...]:
        stage_cls = _STAGE_CLASSES[self.stage_type]
        return tuple(f"### {status}" for status in stage_cls.terminal_statuses)


_STAGE_CLASSES = {
    StageType.BUILDER: BuilderStage,
    StageType.INTEGRATION: IntegrationStage,
    StageType.QA: QAStage,
}


CRITICAL_EXECUTION_PROMPT_CONTRACTS: tuple[ExecutionPromptContract, ...] = (
    ExecutionPromptContract(
        stage_type=StageType.BUILDER,
        prompt_asset="_start.md",
        minimum_nonempty_lines=30,
        required_subordinate_docs=(
            "agents/prompts/create_prompt.md",
            "agents/prompts/run_prompt.md",
            "agents/prompts/builder_cycle.md",
            "agents/roles/planner-architect.md",
            "agents/status_contract.md",
        ),
        required_artifacts=(
            "agents/prompts/tasks/###-slug.md",
            "agents/historylog.md",
            "agents/status.md",
        ),
        required_report_outputs=("agents/reports/",),
        required_phrases=(
            "Writing the status marker is the last repo mutation",
            "End your final response with the same marker",
        ),
    ),
    ExecutionPromptContract(
        stage_type=StageType.INTEGRATION,
        prompt_asset="_integrate.md",
        minimum_nonempty_lines=35,
        required_subordinate_docs=(
            "agents/status_contract.md",
            "agents/roles/integration-steward.md",
            "MILLRACE_RUN_DIR",
        ),
        required_artifacts=(
            "agents/historylog.md",
            "agents/status.md",
        ),
        required_report_outputs=(
            "agents/integration_report.md",
            "agents/reports/integration_",
            "integration_report.md",
        ),
        required_phrases=(
            "Write the report to the deterministic report path",
            "If no commands were defined, still write the full report",
        ),
    ),
    ExecutionPromptContract(
        stage_type=StageType.QA,
        prompt_asset="_check.md",
        minimum_nonempty_lines=35,
        required_subordinate_docs=(
            "agents/status_contract.md",
            "agents/roles/qa-test-engineer.md",
            "agents/prompts/qa_cycle.md",
            "agents/runs/<RUN_ID>/integration_report.md",
            "agents/integration_report.md",
        ),
        required_artifacts=(
            "agents/expectations.md",
            "agents/quickfix.md",
            "agents/historylog.md",
            "agents/status.md",
        ),
        required_report_outputs=(),
        required_phrases=(
            "Do not read `agents/historylog.md`.",
            "Writing the status marker is the last repo mutation",
            "End your final response with the same marker",
        ),
    ),
)


def iter_critical_execution_prompt_contracts() -> tuple[ExecutionPromptContract, ...]:
    """Return the covered execution prompt contracts in stable order."""

    return CRITICAL_EXECUTION_PROMPT_CONTRACTS
