"""Diagnostics and run-artifact helpers."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any
import json
import shutil

from pydantic import Field, field_validator

from .contracts import ContractModel, StageType
from .markdown import write_text_atomic
from .paths import RuntimePaths
from .policies.hooks import PolicyEvaluationRecord, PolicyEvidenceKind
from .provenance import RuntimeTransitionRecord, latest_policy_transition_record, read_transition_history

_REDACTED_VALUE = "<redacted>"
_DEFAULT_POLICY_EVIDENCE_SENSITIVE_KEYS = frozenset(
    {
        "authorization",
        "command",
        "cookie",
        "env",
        "environment",
        "headers",
        "probe_command",
    }
)
_DEFAULT_POLICY_EVIDENCE_SENSITIVE_FRAGMENTS = ("api_key", "password", "secret", "token")


class DiagnosticsRedactionReport(ContractModel):
    """Summary of the redaction pass applied to a diagnostics payload."""

    redacted: bool
    redacted_paths: tuple[str, ...] = ()
    summary: str

    @field_validator("summary")
    @classmethod
    def validate_summary(cls, value: str) -> str:
        normalized = " ".join(value.strip().split())
        if not normalized:
            raise ValueError("summary may not be empty")
        return normalized

    @field_validator("redacted_paths", mode="before")
    @classmethod
    def normalize_redacted_paths(cls, value: tuple[str, ...] | list[str] | None) -> tuple[str, ...]:
        if not value:
            return ()
        normalized: list[str] = []
        seen: set[str] = set()
        for item in value:
            text = str(item).strip()
            if not text or text in seen:
                continue
            seen.add(text)
            normalized.append(text)
        return tuple(normalized)


class DiagnosticsClassification(ContractModel):
    """Classifier output for one diagnostics attachment."""

    label: str
    summary: str
    details: dict[str, Any] = Field(default_factory=dict)

    @field_validator("label", "summary")
    @classmethod
    def validate_text(cls, value: str, info: object) -> str:
        normalized = " ".join(value.strip().split())
        if not normalized:
            field_name = getattr(info, "field_name", "value")
            raise ValueError(f"{field_name} may not be empty")
        return normalized

    @field_validator("details", mode="before")
    @classmethod
    def normalize_details(cls, value: Mapping[str, Any] | None) -> dict[str, Any]:
        return _json_safe(dict(value or {}))


class DiagnosticsPolicyEvidenceItem(ContractModel):
    """One redaction-safe policy evidence item for operator/diagnostics reporting."""

    kind: PolicyEvidenceKind
    summary: str
    details: dict[str, Any] = Field(default_factory=dict)

    @field_validator("summary")
    @classmethod
    def validate_summary(cls, value: str) -> str:
        normalized = " ".join(value.strip().split())
        if not normalized:
            raise ValueError("summary may not be empty")
        return normalized

    @field_validator("details", mode="before")
    @classmethod
    def normalize_details(cls, value: Mapping[str, Any] | None) -> dict[str, Any]:
        return _json_safe(dict(value or {}))


class DiagnosticsPolicyEvidenceSnapshot(ContractModel):
    """Latest persisted policy evidence rendered for diagnostics and control surfaces."""

    schema_version: str = "1.0"
    source: str = "runtime_transition_history"
    run_id: str
    event_id: str
    event_name: str
    timestamp: datetime
    node_id: str
    routing_mode: str | None = None
    hook: str
    evaluator: str
    decision: str
    notes: tuple[str, ...] = ()
    evidence: tuple[DiagnosticsPolicyEvidenceItem, ...] = ()
    redaction: DiagnosticsRedactionReport
    classification: DiagnosticsClassification | None = None

    @field_validator(
        "schema_version",
        "source",
        "run_id",
        "event_id",
        "event_name",
        "node_id",
        "hook",
        "evaluator",
        "decision",
        "routing_mode",
    )
    @classmethod
    def validate_optional_text(cls, value: str | None, info: object) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            field_name = getattr(info, "field_name", "value")
            raise ValueError(f"{field_name} may not be empty")
        return normalized

    @field_validator("timestamp", mode="before")
    @classmethod
    def normalize_timestamp(cls, value: datetime | str) -> datetime:
        if isinstance(value, datetime):
            moment = value
        else:
            moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        return moment.astimezone(timezone.utc)

    @field_validator("notes", mode="before")
    @classmethod
    def normalize_notes(cls, value: tuple[str, ...] | list[str] | None) -> tuple[str, ...]:
        if not value:
            return ()
        normalized: list[str] = []
        seen: set[str] = set()
        for item in value:
            text = " ".join(str(item).strip().split())
            if not text or text in seen:
                continue
            seen.add(text)
            normalized.append(text)
        return tuple(normalized)


DiagnosticsPolicyEvidenceRedactor = Callable[
    [DiagnosticsPolicyEvidenceSnapshot],
    DiagnosticsPolicyEvidenceSnapshot,
]
DiagnosticsPolicyEvidenceClassifier = Callable[
    [DiagnosticsPolicyEvidenceSnapshot],
    DiagnosticsClassification | None,
]


def ensure_directory(path: Path) -> Path:
    """Create a directory tree and return the path."""

    path.mkdir(parents=True, exist_ok=True)
    return path


def _display_path(root: Path, path: Path | None) -> str:
    if path is None:
        return "n/a"
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _default_run_id(stage: StageType) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{timestamp}__{stage.value}"


def _json_safe(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, datetime):
        moment = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
        return moment.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(value, Path):
        return value.as_posix()
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_safe(item) for item in value]
    if hasattr(value, "value"):
        return getattr(value, "value")
    if hasattr(value, "model_dump"):
        return _json_safe(value.model_dump(mode="json"))
    return str(value)


def _redaction_report(redacted_paths: tuple[str, ...] | list[str]) -> DiagnosticsRedactionReport:
    materialized = tuple(redacted_paths)
    if materialized:
        summary = f"Redacted {len(materialized)} sensitive policy-evidence field(s)."
        return DiagnosticsRedactionReport(redacted=True, redacted_paths=materialized, summary=summary)
    return DiagnosticsRedactionReport(
        redacted=False,
        redacted_paths=(),
        summary="No sensitive policy-evidence fields required redaction.",
    )


def _is_sensitive_policy_detail_key(key: str) -> bool:
    normalized = key.strip().lower()
    if not normalized:
        return False
    if normalized in _DEFAULT_POLICY_EVIDENCE_SENSITIVE_KEYS:
        return True
    return any(fragment in normalized for fragment in _DEFAULT_POLICY_EVIDENCE_SENSITIVE_FRAGMENTS)


def _redact_policy_detail(value: Any, *, path: str, redacted_paths: list[str]) -> Any:
    if hasattr(value, "model_dump"):
        return _redact_policy_detail(value.model_dump(mode="json"), path=path, redacted_paths=redacted_paths)
    if isinstance(value, Mapping):
        redacted: dict[str, Any] = {}
        for raw_key, raw_item in value.items():
            key = str(raw_key)
            child_path = f"{path}.{key}" if path else key
            if _is_sensitive_policy_detail_key(key):
                redacted_paths.append(child_path)
                redacted[key] = _REDACTED_VALUE
                continue
            redacted[key] = _redact_policy_detail(raw_item, path=child_path, redacted_paths=redacted_paths)
        return redacted
    if isinstance(value, (list, tuple, set, frozenset)):
        return [
            _redact_policy_detail(item, path=f"{path}[{index}]", redacted_paths=redacted_paths)
            for index, item in enumerate(value)
        ]
    return _json_safe(value)


def _default_policy_evidence_redactor(
    snapshot: DiagnosticsPolicyEvidenceSnapshot,
) -> DiagnosticsPolicyEvidenceSnapshot:
    redacted_paths: list[str] = list(snapshot.redaction.redacted_paths)
    evidence: list[DiagnosticsPolicyEvidenceItem] = []
    for index, item in enumerate(snapshot.evidence):
        details_path = f"evidence[{index}].details"
        evidence.append(
            item.model_copy(
                update={
                    "details": _redact_policy_detail(item.details, path=details_path, redacted_paths=redacted_paths)
                }
            )
        )
    return snapshot.model_copy(
        update={
            "evidence": tuple(evidence),
            "redaction": _redaction_report(redacted_paths),
        }
    )


def _redact_policy_evaluation_record(
    record: PolicyEvaluationRecord,
) -> tuple[PolicyEvaluationRecord, DiagnosticsRedactionReport]:
    redacted_paths: list[str] = []
    evidence = []
    for index, item in enumerate(record.evidence):
        evidence.append(
            item.model_copy(
                update={
                    "details": _redact_policy_detail(
                        item.details,
                        path=f"policy_evaluation.evidence[{index}].details",
                        redacted_paths=redacted_paths,
                    )
                }
            )
        )
    return record.model_copy(update={"evidence": tuple(evidence)}), _redaction_report(redacted_paths)


def _default_policy_evidence_classification(
    snapshot: DiagnosticsPolicyEvidenceSnapshot,
) -> DiagnosticsClassification:
    return DiagnosticsClassification(
        label=f"{snapshot.hook}:{snapshot.evaluator}:{snapshot.decision}",
        summary=(
            f"Latest policy evidence captured from {snapshot.evaluator} at the {snapshot.hook} hook."
        ),
        details={
            "decision": snapshot.decision,
            "evidence_kinds": [item.kind.value for item in snapshot.evidence],
            "redacted": snapshot.redaction.redacted,
        },
    )


def build_policy_evidence_snapshot(
    record: RuntimeTransitionRecord | None,
    *,
    redactor: DiagnosticsPolicyEvidenceRedactor | None = None,
    classifier: DiagnosticsPolicyEvidenceClassifier | None = None,
) -> DiagnosticsPolicyEvidenceSnapshot | None:
    """Render one transition-history policy evaluation as a diagnostics-safe snapshot."""

    if record is None:
        return None
    evaluation = record.policy_evaluation_record()
    if evaluation is None:
        return None
    snapshot = DiagnosticsPolicyEvidenceSnapshot(
        run_id=record.run_id,
        event_id=record.event_id,
        event_name=record.event_name,
        timestamp=record.timestamp,
        node_id=record.node_id,
        routing_mode=record.routing_mode,
        hook=evaluation.hook.value,
        evaluator=evaluation.evaluator,
        decision=evaluation.decision.value,
        notes=evaluation.notes,
        evidence=tuple(
            DiagnosticsPolicyEvidenceItem(
                kind=item.kind,
                summary=item.summary,
                details=item.details,
            )
            for item in evaluation.evidence
        ),
        redaction=_redaction_report(()),
    )
    redacted_snapshot = _default_policy_evidence_redactor(snapshot)
    if redactor is not None:
        redacted_snapshot = redactor(redacted_snapshot)
    classification_result = _default_policy_evidence_classification(redacted_snapshot)
    if classifier is not None:
        classification_result = classifier(redacted_snapshot) or classification_result
    return redacted_snapshot.model_copy(update={"classification": classification_result})


def latest_policy_evidence_snapshot(
    run_dir: Path | None,
    *,
    redactor: DiagnosticsPolicyEvidenceRedactor | None = None,
    classifier: DiagnosticsPolicyEvidenceClassifier | None = None,
) -> DiagnosticsPolicyEvidenceSnapshot | None:
    """Read and render the latest persisted policy evidence for one run directory."""

    if run_dir is None:
        return None
    history_path = run_dir / "transition_history.jsonl"
    latest_record = latest_policy_transition_record(read_transition_history(history_path))
    return build_policy_evidence_snapshot(latest_record, redactor=redactor, classifier=classifier)


def _sanitize_transition_history_copy(path: Path) -> None:
    if path.name != "transition_history.jsonl" or not path.exists():
        return
    sanitized_lines: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text:
            continue
        record = RuntimeTransitionRecord.model_validate_json(text)
        evaluation = record.policy_evaluation_record()
        if evaluation is not None:
            redacted_evaluation, redaction_report = _redact_policy_evaluation_record(evaluation)
            attributes = dict(record.attributes)
            if redaction_report.redacted:
                attributes["policy_evidence_redaction"] = redaction_report.model_dump(mode="json")
            record = record.model_copy(
                update={
                    "policy_evaluation": redacted_evaluation.model_dump(mode="json"),
                    "attributes": attributes,
                }
            )
        sanitized_lines.append(record.model_dump_json())
    write_text_atomic(path, "\n".join(sanitized_lines).rstrip("\n") + ("\n" if sanitized_lines else ""))


def allocate_run_directory(paths: RuntimePaths, *, stage: StageType, run_id: str | None = None) -> Path:
    """Create or reuse a run group directory for the stage invocation."""

    resolved_run_id = run_id or _default_run_id(stage)
    run_dir = paths.runs_dir / resolved_run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def build_stage_artifact_paths(run_dir: Path, stage: StageType) -> tuple[Path, Path, Path, Path]:
    """Return stdout, stderr, rendered-response, and notes paths for a stage."""

    stem = stage.value
    return (
        run_dir / f"{stem}.stdout.log",
        run_dir / f"{stem}.stderr.log",
        run_dir / f"{stem}.last.md",
        run_dir / "runner_notes.md",
    )


def create_diagnostics_bundle(
    paths: RuntimePaths,
    *,
    stage: StageType,
    marker: str | None,
    run_dir: Path | None,
    snapshot_paths: Sequence[Path],
    stdout_path: Path | None = None,
    stderr_path: Path | None = None,
    config_hashes: Mapping[str, str] | None = None,
    note: str | None = None,
    bundle_name: str | None = None,
    policy_record: RuntimeTransitionRecord | None = None,
    policy_evidence_redactor: DiagnosticsPolicyEvidenceRedactor | None = None,
    policy_evidence_classifier: DiagnosticsPolicyEvidenceClassifier | None = None,
) -> Path:
    """Snapshot files into a diagnostics bundle and write a short failure summary."""

    ensure_directory(paths.diagnostics_dir)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    bundle_dir = paths.diagnostics_dir / (bundle_name or f"diag-{timestamp}")
    bundle_dir.mkdir(parents=True, exist_ok=False)

    manifest_entries: list[dict[str, str]] = []
    for item in snapshot_paths:
        if not item.exists():
            raise FileNotFoundError(f"diagnostics input path does not exist: {item}")
        target = bundle_dir / item.name
        if item.is_dir():
            shutil.copytree(item, target)
            for copied in sorted(target.rglob("*")):
                if copied.is_file():
                    _sanitize_transition_history_copy(copied)
                    manifest_entries.append(
                        {
                            "path": copied.relative_to(bundle_dir).as_posix(),
                            "sha256": _sha256_file(copied),
                        }
                    )
        else:
            shutil.copy2(item, target)
            _sanitize_transition_history_copy(target)
            manifest_entries.append(
                {
                    "path": target.relative_to(bundle_dir).as_posix(),
                    "sha256": _sha256_file(target),
                }
            )

    policy_evidence = build_policy_evidence_snapshot(
        policy_record,
        redactor=policy_evidence_redactor,
        classifier=policy_evidence_classifier,
    )
    if policy_evidence is None:
        policy_evidence = latest_policy_evidence_snapshot(
            run_dir,
            redactor=policy_evidence_redactor,
            classifier=policy_evidence_classifier,
        )
    if policy_evidence is not None:
        policy_evidence_path = bundle_dir / "policy_evidence.json"
        write_text_atomic(policy_evidence_path, policy_evidence.model_dump_json(indent=2) + "\n")
        manifest_entries.append(
            {
                "path": policy_evidence_path.relative_to(bundle_dir).as_posix(),
                "sha256": _sha256_file(policy_evidence_path),
            }
        )

    manifest_payload = {
        "schema_version": "1.0",
        "generated_at": timestamp,
        "bundle": _display_path(paths.root, bundle_dir),
        "files": sorted(manifest_entries, key=lambda item: item["path"]),
    }
    write_text_atomic(bundle_dir / "manifest.json", json.dumps(manifest_payload, indent=2, sort_keys=True) + "\n")

    config_lines = [f"- `{key}`: `{value}`" for key, value in sorted((config_hashes or {}).items())]
    summary_lines = [
        "# Failure Summary",
        "",
        f"- **Stage:** {stage.value}",
        f"- **Marker:** `{marker or 'missing'}`",
        f"- **Run dir:** `{_display_path(paths.root, run_dir)}`",
        f"- **Stdout log:** `{_display_path(paths.root, stdout_path)}`",
        f"- **Stderr log:** `{_display_path(paths.root, stderr_path)}`",
    ]
    if note:
        summary_lines.append(f"- **Note:** {note}")
    if policy_evidence is not None:
        summary_lines.extend(
            [
                "- **Latest policy evidence:** `policy_evidence.json`",
                (
                    f"- **Policy decision:** `{policy_evidence.decision}` "
                    f"via `{policy_evidence.evaluator}` at `{policy_evidence.hook}`"
                ),
                f"- **Policy evidence redaction:** {policy_evidence.redaction.summary}",
            ]
        )
        if policy_evidence.classification is not None:
            summary_lines.append(
                (
                    f"- **Policy evidence classification:** "
                    f"`{policy_evidence.classification.label}` {policy_evidence.classification.summary}"
                )
            )
    summary_lines.append("- **Active config hashes:**")
    if config_lines:
        summary_lines.extend(config_lines)
    else:
        summary_lines.append("  - unavailable")

    write_text_atomic(bundle_dir / "failure_summary.md", "\n".join(summary_lines).rstrip("\n") + "\n")
    return bundle_dir
