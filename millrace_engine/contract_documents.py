"""Task and report document contract family."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import Field, field_validator, model_validator

from .contract_core import (
    ACCEPTANCE_ID_RE,
    CARD_HEADING_RE,
    REQUIREMENT_ID_RE,
    TOKEN_RE,
    ContractModel,
    ExecutionStatus,
    StageType,
    _extract_field_block_lines,
    _extract_field_tokens,
    _extract_field_value,
    _normalize_datetime,
    _normalize_integration_preference,
    _normalize_path,
    _normalize_sequence,
    _normalize_tokens,
    _slugify_task_id,
)
from .contract_runtime import RunnerResult


class TaskCard(ContractModel):
    """Normalized markdown task card."""

    task_id: str
    title: str
    body: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    heading: str
    raw_markdown: str
    date: str | None = None
    source_file: Path | None = None
    spec_id: str | None = None
    complexity: str | None = None
    gates: tuple[str, ...] = ()
    integration_preference: Literal["force", "skip", "inherit"] | None = None
    depends_on: tuple[str, ...] = ()
    blocks: tuple[str, ...] = ()
    provides: tuple[str, ...] = ()
    requirement_ids: tuple[str, ...] = ()
    acceptance_ids: tuple[str, ...] = ()

    @model_validator(mode="before")
    @classmethod
    def populate_derived_fields(cls, value: Any) -> Any:
        if isinstance(value, cls):
            return value
        if not isinstance(value, dict):
            raise TypeError("TaskCard input must be a mapping")

        payload = dict(value)
        heading = str(payload.get("heading", "")).strip()
        body = str(payload.get("body", "")).rstrip("\n")

        if heading:
            match = CARD_HEADING_RE.match(heading)
            if match:
                payload.setdefault("date", match.group(1))
                payload.setdefault("title", match.group(2).strip())

        title = str(payload.get("title", "")).strip()
        if not title:
            raise ValueError("task card title may not be empty")
        payload["title"] = title

        date_value = payload.get("date")
        if not heading:
            if isinstance(date_value, str) and date_value.strip():
                payload["heading"] = f"## {date_value.strip()} - {title}"
            else:
                raise ValueError("task card heading or date is required")

        spec_id = payload.get("spec_id") or _extract_field_value(body, "Spec-ID")
        complexity = (
            payload.get("complexity")
            or _extract_field_value(body, "Complexity")
            or _extract_field_value(body, "Effort")
        )
        task_id = payload.get("task_id") or _extract_field_value(body, "Task-ID")
        if task_id is None:
            task_id = _slugify_task_id(payload.get("date"), title)

        payload["task_id"] = str(task_id).strip()
        payload["body"] = body
        payload["spec_id"] = spec_id.strip() if isinstance(spec_id, str) and spec_id.strip() else spec_id
        payload["complexity"] = (
            complexity.strip() if isinstance(complexity, str) and complexity.strip() else complexity
        )
        metadata = dict(payload.get("metadata") or {})
        payload["metadata"] = metadata
        payload["gates"] = _normalize_tokens(
            list(payload.get("gates") or metadata.get("gates") or _extract_field_tokens(body, "Gates"))
        )
        payload["integration_preference"] = _normalize_integration_preference(
            payload.get("integration_preference")
            or metadata.get("integration")
            or _extract_field_value(body, "Integration")
        )
        payload["depends_on"] = _normalize_sequence(
            list(payload.get("depends_on") or _extract_field_block_lines(body, "Dependencies"))
        )
        payload["blocks"] = _normalize_sequence(
            list(
                payload.get("blocks")
                or _extract_field_block_lines(body, "Blocks")
                or _extract_field_block_lines(body, "Enables")
            )
        )
        payload["provides"] = _normalize_sequence(
            list(payload.get("provides") or _extract_field_block_lines(body, "Provides"))
        )
        payload["requirement_ids"] = _normalize_sequence(
            list(payload.get("requirement_ids") or REQUIREMENT_ID_RE.findall(body))
        )
        payload["acceptance_ids"] = _normalize_sequence(
            list(payload.get("acceptance_ids") or ACCEPTANCE_ID_RE.findall(body))
        )

        raw_markdown = payload.get("raw_markdown")
        if raw_markdown is None:
            payload["raw_markdown"] = cls.render_from_parts(payload["heading"], body)

        return payload

    @field_validator("source_file", mode="before")
    @classmethod
    def normalize_source_file(cls, value: str | Path | None) -> Path | None:
        return _normalize_path(value)

    @field_validator("gates", mode="before")
    @classmethod
    def normalize_gates(cls, value: tuple[str, ...] | list[str] | str | None) -> tuple[str, ...]:
        if value in (None, ""):
            return ()
        if isinstance(value, str):
            return _normalize_tokens(TOKEN_RE.findall(value))
        return _normalize_tokens([str(item) for item in value])

    @field_validator("integration_preference", mode="before")
    @classmethod
    def normalize_integration_preference_field(
        cls,
        value: object,
    ) -> Literal["force", "skip", "inherit"] | None:
        return _normalize_integration_preference(value)

    @classmethod
    def render_from_parts(cls, heading: str, body: str) -> str:
        cleaned_heading = heading.rstrip()
        cleaned_body = body.rstrip("\n")
        if cleaned_body:
            return f"{cleaned_heading}\n{cleaned_body}"
        return cleaned_heading

    @classmethod
    def from_markdown(cls, raw_markdown: str, *, source_file: Path | None = None) -> Self:
        lines = raw_markdown.rstrip("\n").splitlines()
        if not lines:
            raise ValueError("task card markdown may not be empty")
        heading = lines[0].strip()
        body = "\n".join(lines[1:]).rstrip("\n")
        return cls.model_validate(
            {
                "heading": heading,
                "body": body,
                "raw_markdown": raw_markdown.rstrip("\n"),
                "source_file": source_file,
            }
        )

    def render_markdown(self) -> str:
        return self.raw_markdown.rstrip("\n")


class ObjectiveCompletionPolicy(ContractModel):
    """Completion-specific policy block from the objective contract."""

    authoritative_decision_file: Path
    fallback_decision_file: Path
    require_task_store_cards_zero: bool = True
    require_open_gaps_zero: bool = True

    @field_validator("authoritative_decision_file", "fallback_decision_file", mode="before")
    @classmethod
    def normalize_paths(cls, value: str | Path) -> Path:
        path = _normalize_path(value)
        if path is None:
            raise ValueError("decision path may not be empty")
        return path


class ObjectiveContract(ContractModel):
    """Typed objective-contract surface for completion gate enforcement."""

    schema_version: Literal["1.0"] = "1.0"
    objective_id: str
    objective_root: str
    completion: ObjectiveCompletionPolicy
    seed_state: dict[str, Any] = Field(default_factory=dict)
    gate_integrity: dict[str, Any] = Field(default_factory=dict)
    artifacts: dict[str, Any] = Field(default_factory=dict)
    objective_profile: dict[str, Any] = Field(default_factory=dict)

    @field_validator("objective_id", "objective_root")
    @classmethod
    def validate_text(cls, value: str, info: object) -> str:
        field_name = getattr(info, "field_name", "value")
        normalized = " ".join(value.strip().split())
        if not normalized:
            raise ValueError(f"{field_name} may not be empty")
        return normalized


_DEFAULT_OBJECTIVE_COMPLETION_POLICY = {
    "authoritative_decision_file": "agents/reports/completion_decision.json",
    "fallback_decision_file": "agents/reports/audit_gate_decision.json",
    "require_task_store_cards_zero": True,
    "require_open_gaps_zero": True,
}


def _parse_legacy_objective_contract_text(raw_text: str) -> dict[str, str]:
    payload: dict[str, str] = {}
    for line_number, raw_line in enumerate(raw_text.splitlines(), start=1):
        stripped = raw_line.strip()
        if not stripped:
            continue
        if ":" not in raw_line:
            raise ValueError(f"legacy objective contract line {line_number} is missing ':'")
        key, value = raw_line.split(":", 1)
        normalized_key = key.strip()
        if not normalized_key:
            raise ValueError(f"legacy objective contract line {line_number} has an empty key")
        normalized_value = value.strip()
        if normalized_value[:1] in {"'", '"'} and normalized_value[-1:] == normalized_value[:1]:
            normalized_value = normalized_value[1:-1]
        payload[normalized_key] = normalized_value
    return payload


def _legacy_objective_contract_payload(payload: dict[str, Any]) -> dict[str, Any] | None:
    objective_id = str(payload.get("objective_id") or payload.get("goal_id") or payload.get("profile_id") or "").strip()
    if not objective_id:
        return None

    completion_payload = payload.get("completion")
    if isinstance(completion_payload, dict):
        normalized_completion = dict(completion_payload)
    else:
        normalized_completion = {}
    for key, value in _DEFAULT_OBJECTIVE_COMPLETION_POLICY.items():
        normalized_completion.setdefault(key, value)

    objective_profile = payload.get("objective_profile")
    if isinstance(objective_profile, dict):
        normalized_profile = dict(objective_profile)
    else:
        normalized_profile = {}
    for key in (
        "profile_id",
        "goal_id",
        "title",
        "source_path",
        "updated_at",
        "profile_path",
        "profile_markdown_path",
        "research_brief_path",
        "report_path",
        "goal_intake_record_path",
    ):
        value = payload.get(key)
        if value not in (None, ""):
            normalized_profile.setdefault(key, value)

    seed_state = payload.get("seed_state")
    if isinstance(seed_state, dict):
        normalized_seed_state = dict(seed_state)
    else:
        normalized_seed_state = {}
    source_path = payload.get("source_path")
    if source_path not in (None, ""):
        normalized_seed_state.setdefault("source_path", source_path)

    gate_integrity = payload.get("gate_integrity")
    normalized_gate_integrity = dict(gate_integrity) if isinstance(gate_integrity, dict) else {}

    artifacts = payload.get("artifacts")
    normalized_artifacts = dict(artifacts) if isinstance(artifacts, dict) else {}

    return {
        "schema_version": str(payload.get("schema_version") or "1.0").strip() or "1.0",
        "objective_id": objective_id,
        "objective_root": str(payload.get("objective_root") or ".").strip() or ".",
        "completion": normalized_completion,
        "seed_state": normalized_seed_state,
        "gate_integrity": normalized_gate_integrity,
        "artifacts": normalized_artifacts,
        "objective_profile": normalized_profile,
    }


def load_objective_contract(raw_text: str) -> ObjectiveContract:
    stripped = raw_text.strip()
    if not stripped:
        raise ValueError("objective contract may not be empty")

    parsed_as_legacy_text = False
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError:
        payload = _parse_legacy_objective_contract_text(stripped)
        parsed_as_legacy_text = True

    if not isinstance(payload, dict):
        raise ValueError("objective contract must contain an object")

    if not parsed_as_legacy_text:
        return ObjectiveContract.model_validate(payload)

    legacy_payload = _legacy_objective_contract_payload(payload)
    if legacy_payload is None:
        return ObjectiveContract.model_validate(payload)
    return ObjectiveContract.model_validate(legacy_payload)


class AuditContract(ContractModel):
    """Strict audit command-contract policy loaded from packaged/workspace JSON."""

    schema_version: Literal["1.0"] = "1.0"
    contract_id: str
    enabled: bool = True
    description: str | None = None
    required_command_substrings: tuple[str, ...] = ()
    forbidden_command_markers: tuple[str, ...] = ()
    required_summaries: tuple[str, ...] = ()

    @field_validator("contract_id")
    @classmethod
    def validate_contract_id(cls, value: str) -> str:
        normalized = " ".join(value.strip().split())
        if not normalized:
            raise ValueError("contract_id may not be empty")
        return normalized

    @field_validator("description")
    @classmethod
    def normalize_description(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = " ".join(value.strip().split())
        return normalized or None

    @field_validator(
        "required_command_substrings",
        "forbidden_command_markers",
        "required_summaries",
        mode="before",
    )
    @classmethod
    def normalize_text_sequences(
        cls,
        value: tuple[str, ...] | list[str] | str | None,
    ) -> tuple[str, ...]:
        if value in (None, ""):
            return ()
        if isinstance(value, str):
            return _normalize_sequence([value])
        return _normalize_sequence([str(item) for item in value])


class AuditExecutionFinding(ContractModel):
    """One deterministic command-contract finding captured during audit validate."""

    kind: Literal[
        "missing_required_command_substring",
        "forbidden_command_marker",
        "missing_required_summary",
    ]
    expected: str
    message: str
    observed: tuple[str, ...] = ()

    @field_validator("expected", "message")
    @classmethod
    def validate_required_text(cls, value: str, info: object) -> str:
        field_name = getattr(info, "field_name", "value")
        normalized = " ".join(value.strip().split())
        if not normalized:
            raise ValueError(f"{field_name} may not be empty")
        return normalized

    @field_validator("observed", mode="before")
    @classmethod
    def normalize_observed(cls, value: tuple[str, ...] | list[str] | str | None) -> tuple[str, ...]:
        if value in (None, ""):
            return ()
        if isinstance(value, str):
            return _normalize_sequence([value])
        return _normalize_sequence([str(item) for item in value])


class AuditExecutionReport(ContractModel):
    """Durable command-contract evidence persisted by audit validate."""

    schema_version: Literal["1.0"] = "1.0"
    artifact_type: Literal["audit_execution_report"] = "audit_execution_report"
    run_id: str
    emitted_at: datetime
    audit_id: str
    working_path: str
    contract_id: str
    strict_contract_path: str
    strict_contract_enabled: bool = True
    observed_commands: tuple[str, ...] = ()
    observed_summaries: tuple[str, ...] = ()
    command_count: int = Field(default=0, ge=0)
    summary_count: int = Field(default=0, ge=0)
    finding_count: int = Field(default=0, ge=0)
    findings: tuple[AuditExecutionFinding, ...] = ()
    passed: bool = True

    @field_validator("emitted_at", mode="before")
    @classmethod
    def normalize_emitted_at(cls, value: datetime | str) -> datetime:
        return _normalize_datetime(value)

    @field_validator("run_id", "audit_id", "working_path", "contract_id", "strict_contract_path")
    @classmethod
    def validate_text_fields(cls, value: str, info: object) -> str:
        field_name = getattr(info, "field_name", "value")
        normalized = " ".join(value.strip().split())
        if not normalized:
            raise ValueError(f"{field_name} may not be empty")
        return normalized

    @field_validator("observed_commands", "observed_summaries", mode="before")
    @classmethod
    def normalize_sequences(
        cls,
        value: tuple[str, ...] | list[str] | str | None,
    ) -> tuple[str, ...]:
        if value in (None, ""):
            return ()
        if isinstance(value, str):
            return _normalize_sequence([value])
        return _normalize_sequence([str(item) for item in value])

    @field_validator("findings", mode="before")
    @classmethod
    def normalize_findings(
        cls,
        value: tuple[AuditExecutionFinding, ...] | list[AuditExecutionFinding | dict[str, Any]] | None,
    ) -> tuple[AuditExecutionFinding, ...]:
        if not value:
            return ()
        return tuple(
            item if isinstance(item, AuditExecutionFinding) else AuditExecutionFinding.model_validate(item)
            for item in value
        )

    @model_validator(mode="after")
    def validate_counts(self) -> "AuditExecutionReport":
        if self.command_count != len(self.observed_commands):
            raise ValueError("command_count must match observed_commands")
        if self.summary_count != len(self.observed_summaries):
            raise ValueError("summary_count must match observed_summaries")
        if self.finding_count != len(self.findings):
            raise ValueError("finding_count must match findings")
        if self.passed != (self.finding_count == 0):
            raise ValueError("passed must align with finding_count")
        return self


class CompletionManifestCommand(ContractModel):
    """One required completion command declared by the completion manifest."""

    id: str
    required: bool = True
    category: str
    timeout_secs: int = Field(gt=0)
    command: str

    @field_validator("id", "category", "command")
    @classmethod
    def validate_required_text(cls, value: str, info: object) -> str:
        field_name = getattr(info, "field_name", "value")
        normalized = " ".join(value.strip().split())
        if not normalized:
            raise ValueError(f"{field_name} may not be empty")
        return normalized


class CompletionManifest(ContractModel):
    """Typed completion-manifest contract loaded from packaged/workspace JSON."""

    schema_version: Literal["1.0"] = "1.0"
    profile_id: str
    configured: bool = False
    notes: tuple[str, ...] = ()
    required_completion_commands: tuple[CompletionManifestCommand, ...] = ()

    @field_validator("profile_id")
    @classmethod
    def validate_profile_id(cls, value: str) -> str:
        normalized = " ".join(value.strip().split())
        if not normalized:
            raise ValueError("profile_id may not be empty")
        return normalized

    @field_validator("notes", mode="before")
    @classmethod
    def normalize_notes(cls, value: tuple[str, ...] | list[str] | str | None) -> tuple[str, ...]:
        if value in (None, ""):
            return ()
        if isinstance(value, str):
            return _normalize_sequence([value])
        return _normalize_sequence([str(item) for item in value])

    @field_validator("required_completion_commands", mode="before")
    @classmethod
    def normalize_required_completion_commands(
        cls,
        value: tuple[CompletionManifestCommand, ...]
        | list[CompletionManifestCommand | dict[str, Any]]
        | None,
    ) -> tuple[CompletionManifestCommand, ...]:
        if not value:
            return ()
        return tuple(
            item if isinstance(item, CompletionManifestCommand) else CompletionManifestCommand.model_validate(item)
            for item in value
        )

    @model_validator(mode="after")
    def validate_required_commands(self) -> "CompletionManifest":
        required_commands = tuple(command for command in self.required_completion_commands if command.required)
        if self.configured and not required_commands:
            raise ValueError("configured completion manifest requires at least one required completion command")
        command_ids = [command.id for command in self.required_completion_commands]
        if len(command_ids) != len(set(command_ids)):
            raise ValueError("completion manifest command ids must be unique")
        return self

    def required_commands(self) -> tuple[CompletionManifestCommand, ...]:
        return tuple(command for command in self.required_completion_commands if command.required)


class AuditGateDecisionCounts(ContractModel):
    """Deterministic counts persisted with gate and completion decisions."""

    required_total: int = Field(default=0, ge=0)
    required_pass: int = Field(default=0, ge=0)
    required_fail: int = Field(default=0, ge=0)
    required_blocked: int = Field(default=0, ge=0)
    completion_required: int = Field(default=0, ge=0)
    completion_pass: int = Field(default=0, ge=0)
    open_gaps: int = Field(default=0, ge=0)
    task_store_cards: int = Field(default=0, ge=0)
    active_task_cards: int = Field(default=0, ge=0)
    backlog_cards: int = Field(default=0, ge=0)
    pending_task_cards: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_counts(self) -> "AuditGateDecisionCounts":
        if self.required_pass + self.required_fail + self.required_blocked != self.required_total:
            raise ValueError("required counts must sum to required_total")
        if self.completion_pass > self.completion_required:
            raise ValueError("completion_pass may not exceed completion_required")
        if self.active_task_cards + self.backlog_cards + self.pending_task_cards != self.task_store_cards:
            raise ValueError("task-store breakdown must sum to task_store_cards")
        return self


class AuditGateDecision(ContractModel):
    """Operator-facing fallback gate decision persisted by the audit gatekeeper."""

    schema_version: Literal["1.0"] = "1.0"
    artifact_type: Literal["audit_gate_decision_report"] = "audit_gate_decision_report"
    run_id: str
    audit_id: str
    generated_at: datetime
    decision: Literal["PASS", "FAIL"]
    reasons: tuple[str, ...] = ()
    counts: AuditGateDecisionCounts
    gate_decision_path: str
    objective_contract_path: str
    completion_manifest_path: str
    execution_report_path: str
    validate_record_path: str

    @field_validator("generated_at", mode="before")
    @classmethod
    def normalize_generated_at(cls, value: datetime | str) -> datetime:
        return _normalize_datetime(value)

    @field_validator(
        "run_id",
        "audit_id",
        "gate_decision_path",
        "objective_contract_path",
        "completion_manifest_path",
        "execution_report_path",
        "validate_record_path",
    )
    @classmethod
    def validate_text_fields(cls, value: str, info: object) -> str:
        field_name = getattr(info, "field_name", "value")
        normalized = " ".join(value.strip().split())
        if not normalized:
            raise ValueError(f"{field_name} may not be empty")
        return normalized

    @field_validator("reasons", mode="before")
    @classmethod
    def normalize_reasons(cls, value: tuple[str, ...] | list[str] | str | None) -> tuple[str, ...]:
        if value in (None, ""):
            return ()
        if isinstance(value, str):
            return _normalize_sequence([value])
        return _normalize_sequence([str(item) for item in value])

    @model_validator(mode="after")
    def validate_decision(self) -> "AuditGateDecision":
        if self.decision == "PASS" and self.reasons:
            raise ValueError("PASS gate decisions may not include reasons")
        if self.decision == "FAIL" and not self.reasons:
            raise ValueError("FAIL gate decisions require at least one reason")
        return self


class CompletionDecision(ContractModel):
    """Authoritative completion decision persisted by the audit gatekeeper."""

    schema_version: Literal["1.0"] = "1.0"
    artifact_type: Literal["completion_decision"] = "completion_decision"
    run_id: str
    audit_id: str
    generated_at: datetime
    decision: Literal["PASS", "FAIL"]
    reasons: tuple[str, ...] = ()
    counts: AuditGateDecisionCounts
    completion_decision_path: str
    gate_decision_path: str
    objective_contract_path: str

    @field_validator("generated_at", mode="before")
    @classmethod
    def normalize_generated_at(cls, value: datetime | str) -> datetime:
        return _normalize_datetime(value)

    @field_validator(
        "run_id",
        "audit_id",
        "completion_decision_path",
        "gate_decision_path",
        "objective_contract_path",
    )
    @classmethod
    def validate_text_fields(cls, value: str, info: object) -> str:
        field_name = getattr(info, "field_name", "value")
        normalized = " ".join(value.strip().split())
        if not normalized:
            raise ValueError(f"{field_name} may not be empty")
        return normalized

    @field_validator("reasons", mode="before")
    @classmethod
    def normalize_reasons(cls, value: tuple[str, ...] | list[str] | str | None) -> tuple[str, ...]:
        if value in (None, ""):
            return ()
        if isinstance(value, str):
            return _normalize_sequence([value])
        return _normalize_sequence([str(item) for item in value])

    @model_validator(mode="after")
    def validate_decision(self) -> "CompletionDecision":
        if self.decision == "PASS" and self.reasons:
            raise ValueError("PASS completion decisions may not include reasons")
        if self.decision == "FAIL" and not self.reasons:
            raise ValueError("FAIL completion decisions require at least one reason")
        return self


class BlockerEntry(ContractModel):
    """Human-readable blocker ledger entry."""

    occurred_at: datetime
    task_title: str
    status: ExecutionStatus
    stage_blocked: str
    source_task: str
    prompt_artifact: Path | None = None
    run_dir: Path | None = None
    diagnostics_dir: Path | None = None
    root_cause_summary: str
    next_action: str
    incident_path: Path | None = None
    notes: str | None = None

    @field_validator("occurred_at", mode="before")
    @classmethod
    def normalize_occurred_at(cls, value: datetime | str) -> datetime:
        return _normalize_datetime(value)

    @field_validator("prompt_artifact", "run_dir", "diagnostics_dir", "incident_path", mode="before")
    @classmethod
    def normalize_paths(cls, value: str | Path | None) -> Path | None:
        return _normalize_path(value)

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: ExecutionStatus) -> ExecutionStatus:
        allowed = {
            ExecutionStatus.BLOCKED,
            ExecutionStatus.CONSULT_COMPLETE,
            ExecutionStatus.NEEDS_RESEARCH,
        }
        if value not in allowed:
            raise ValueError(f"blocker entries do not support {value.value}")
        return value

    def render_markdown(self) -> str:
        timestamp = self.occurred_at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        lines = [
            f"## {timestamp} — {self.task_title}",
            "",
            f"- **Status:** `{self.status.marker}`",
            f"- **Stage blocked:** {self.stage_blocked}",
            f"- **Source task card:** {self.source_task}",
            f"- **Prompt artifact:** {self.prompt_artifact or 'n/a'}",
            "- **Evidence:**",
            f"  - Runs: `{self.run_dir or 'n/a'}`",
            f"  - Diagnostics: `{self.diagnostics_dir or 'n/a'}`",
            "  - Quickfix/expectations: n/a",
            f"- **Root-cause summary:** {self.root_cause_summary}",
            f"- **Deterministic next action:** {self.next_action}",
            f"- **Incident intake:** `{self.incident_path}`" if self.incident_path else "- **Incident intake:** n/a",
            f"- **Notes:** {self.notes or 'n/a'}",
        ]
        return "\n".join(lines).rstrip("\n")


class StageResult(ContractModel):
    """Canonical public stage-result contract with an optional richer runner artifact."""

    stage: StageType
    status: str
    exit_code: int = 0
    stdout: str = ""
    stderr: str = ""
    duration_seconds: float = Field(default=0, ge=0)
    runner_used: str | None = None
    model_used: str | None = None
    artifacts: tuple[Path, ...] = ()
    metadata: dict[str, Any] = Field(default_factory=dict)
    runner_result: RunnerResult | None = None

    @model_validator(mode="before")
    @classmethod
    def populate_public_fields_from_runner_result(cls, value: Any) -> Any:
        if isinstance(value, cls):
            return value
        if not isinstance(value, dict):
            raise TypeError("StageResult input must be a mapping")

        payload = dict(value)
        runner_result = payload.get("runner_result")
        if runner_result is None:
            payload.setdefault("metadata", dict(payload.get("metadata") or {}))
            return payload

        if not isinstance(runner_result, RunnerResult):
            runner_result = RunnerResult.model_validate(runner_result)
            payload["runner_result"] = runner_result

        payload.setdefault("exit_code", runner_result.exit_code)
        payload.setdefault("stdout", runner_result.stdout)
        payload.setdefault("stderr", runner_result.stderr)
        payload.setdefault("duration_seconds", runner_result.duration_seconds)
        payload.setdefault("runner_used", runner_result.runner.value)
        payload.setdefault("model_used", runner_result.model)

        artifact_candidates = payload.get("artifacts")
        if not artifact_candidates:
            artifact_candidates = [
                runner_result.stdout_path,
                runner_result.stderr_path,
                runner_result.last_response_path,
                runner_result.runner_notes_path,
            ]
        deduped_artifacts: list[Path] = []
        seen_artifacts: set[Path] = set()
        for candidate in artifact_candidates:
            path = _normalize_path(candidate)
            if path is None or path in seen_artifacts:
                continue
            seen_artifacts.add(path)
            deduped_artifacts.append(path)
        payload["artifacts"] = tuple(deduped_artifacts)

        default_metadata: dict[str, Any] = {
            "command": list(runner_result.command),
            "detected_marker": runner_result.detected_marker,
            "raw_marker_line": runner_result.raw_marker_line,
            "run_dir": runner_result.run_dir,
            "started_at": runner_result.started_at,
            "completed_at": runner_result.completed_at,
        }
        if runner_result.usage_summary is not None:
            default_metadata["usage_summary"] = runner_result.usage_summary.model_dump(mode="json")
        metadata = dict(default_metadata)
        metadata.update(dict(payload.get("metadata") or {}))
        payload["metadata"] = metadata
        return payload

    @field_validator("artifacts", mode="before")
    @classmethod
    def normalize_artifacts(
        cls,
        value: tuple[Path, ...] | list[Path | str] | None,
    ) -> tuple[Path, ...]:
        if not value:
            return ()
        artifacts: list[Path] = []
        seen: set[Path] = set()
        for item in value:
            path = _normalize_path(item)
            if path is None or path in seen:
                continue
            seen.add(path)
            artifacts.append(path)
        return tuple(artifacts)
