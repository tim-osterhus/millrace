"""Runner failure classification helpers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from millrace_ai.contracts import RuntimeFailureOrigin
from millrace_ai.runners.requests import RunnerRawResult

_MAX_CLASSIFIER_TEXT_CHARS = 16_000


@dataclass(frozen=True, slots=True)
class FailureClassification:
    failure_class: str
    blocked_origin: str
    failure_scope: str
    auto_requeue_candidate: bool
    classifier_code: str


def classify_raw_exit_failure(raw_result: RunnerRawResult) -> FailureClassification | None:
    if raw_result.failure_class:
        return classification_for_failure_class(raw_result.failure_class)
    if raw_result.exit_kind == "completed" and raw_result.exit_code in (None, 0):
        return None
    if raw_result.exit_kind == "timeout":
        return FailureClassification(
            failure_class="runner_timeout",
            blocked_origin="runner_failure",
            failure_scope="environment",
            auto_requeue_candidate=True,
            classifier_code="exit_timeout",
        )

    evidence = raw_failure_evidence(raw_result)
    provider_or_runner = "provider" if raw_result.exit_kind == "provider_error" else "runner"
    classified = classify_failure_evidence(evidence, default_origin="runner_failure")
    if classified is not None:
        return classified
    if raw_result.exit_kind == "provider_error":
        return FailureClassification(
            failure_class="provider_unavailable",
            blocked_origin="runner_failure",
            failure_scope="provider",
            auto_requeue_candidate=True,
            classifier_code=f"{provider_or_runner}_default_unavailable",
        )
    return classification_for_failure_class("runner_transport_failure")


def raw_failure_evidence(raw_result: RunnerRawResult) -> str:
    parts: list[str] = []
    for raw_path in (raw_result.stderr_path, raw_result.stdout_path):
        if not raw_path:
            continue
        path = Path(raw_path)
        try:
            if path.is_file():
                parts.append(path.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
    return "\n".join(parts)[-_MAX_CLASSIFIER_TEXT_CHARS:].lower()


def classify_failure_evidence(
    evidence: str,
    *,
    default_origin: str,
) -> FailureClassification | None:
    if not evidence.strip():
        return None

    if _contains_any(
        evidence,
        (
            "runner binary not found",
            "executable missing",
            "no such file or directory",
            "command not found",
        ),
    ):
        return FailureClassification(
            failure_class="runner_binary_missing",
            blocked_origin=default_origin,
            failure_scope="local_configuration",
            auto_requeue_candidate=False,
            classifier_code="runner_binary_missing",
        )
    if _contains_any(
        evidence,
        (
            "unauthorized",
            "authentication",
            "not authenticated",
            "login required",
            "invalid api key",
            "api key",
            "401",
        ),
    ):
        return FailureClassification(
            failure_class="auth_missing_or_invalid",
            blocked_origin=default_origin,
            failure_scope="local_configuration",
            auto_requeue_candidate=False,
            classifier_code="auth_missing_or_invalid",
        )
    if _contains_any(evidence, ("rate limit", "rate_limit", "too many requests", "429")):
        return FailureClassification(
            failure_class="provider_rate_limited",
            blocked_origin=default_origin,
            failure_scope="provider",
            auto_requeue_candidate=True,
            classifier_code="provider_rate_limited",
        )
    if _contains_any(
        evidence,
        (
            "could not resolve",
            "temporary failure in name resolution",
            "dns",
            "network is unreachable",
            "connection refused",
            "connection reset",
            "no route to host",
            "offline",
            "internet",
        ),
    ):
        return FailureClassification(
            failure_class="network_unavailable",
            blocked_origin=default_origin,
            failure_scope="environment",
            auto_requeue_candidate=True,
            classifier_code="network_unavailable",
        )
    if _contains_any(
        evidence,
        (
            "service unavailable",
            "temporarily unavailable",
            "provider unavailable",
            "provider overloaded",
            "overloaded",
            "503",
        ),
    ):
        return FailureClassification(
            failure_class="provider_unavailable",
            blocked_origin=default_origin,
            failure_scope="provider",
            auto_requeue_candidate=True,
            classifier_code="provider_unavailable",
        )
    return None


def classification_for_failure_class(failure_class: str) -> FailureClassification:
    if failure_class == "runner_timeout":
        return FailureClassification(
            failure_class=failure_class,
            blocked_origin="runner_failure",
            failure_scope="environment",
            auto_requeue_candidate=True,
            classifier_code="runner_timeout",
        )
    if failure_class in {"network_unavailable"}:
        return FailureClassification(
            failure_class=failure_class,
            blocked_origin="runner_failure",
            failure_scope="environment",
            auto_requeue_candidate=True,
            classifier_code=failure_class,
        )
    if failure_class in {"provider_unavailable", "provider_rate_limited"}:
        return FailureClassification(
            failure_class=failure_class,
            blocked_origin="runner_failure",
            failure_scope="provider",
            auto_requeue_candidate=True,
            classifier_code=failure_class,
        )
    if failure_class in {"runner_binary_missing", "auth_missing_or_invalid"}:
        return FailureClassification(
            failure_class=failure_class,
            blocked_origin="runner_failure",
            failure_scope="local_configuration",
            auto_requeue_candidate=False,
            classifier_code=failure_class,
        )
    if failure_class in {
        "missing_terminal_result",
        "illegal_terminal_result",
        "conflicting_terminal_results",
        "missing_required_artifact",
    }:
        return FailureClassification(
            failure_class=failure_class,
            blocked_origin="stage_terminal",
            failure_scope="contract",
            auto_requeue_candidate=False,
            classifier_code=failure_class,
        )
    if failure_class.startswith("capability_"):
        return FailureClassification(
            failure_class=failure_class,
            blocked_origin="runtime_capability_gate",
            failure_scope="runtime_policy",
            auto_requeue_candidate=False,
            classifier_code=failure_class,
        )
    return FailureClassification(
        failure_class=failure_class,
        blocked_origin="runner_failure",
        failure_scope="unknown",
        auto_requeue_candidate=False,
        classifier_code="unclassified_failure",
    )


def raw_exit_kind(raw_result: RunnerRawResult) -> str:
    return raw_result.observed_exit_kind or raw_result.exit_kind


def raw_exit_code(raw_result: RunnerRawResult) -> int | None:
    if raw_result.observed_exit_code is not None:
        return raw_result.observed_exit_code
    return raw_result.exit_code


def timeout_reconciled(raw_result: RunnerRawResult) -> bool:
    return raw_result.observed_exit_kind == "timeout" and raw_result.exit_kind == "completed"


def transport_reconciliation_notes(raw_result: RunnerRawResult) -> tuple[str, ...]:
    if not timeout_reconciled(raw_result):
        return ()
    return ("runner timeout was reconciled after a final terminal marker was captured",)


def failure_origin_for_failure_class(failure_class: str) -> str | None:
    mapped_failure_classes = {
        "provider_unavailable": RuntimeFailureOrigin.MODEL_PROVIDER_UNAVAILABLE,
        "provider_rate_limited": RuntimeFailureOrigin.MODEL_PROVIDER_UNAVAILABLE,
    }
    mapped = mapped_failure_classes.get(failure_class)
    if mapped is not None:
        return mapped.value
    try:
        return RuntimeFailureOrigin(failure_class).value
    except ValueError:
        return None


def _contains_any(text: str, needles: tuple[str, ...]) -> bool:
    return any(needle in text for needle in needles)


__all__ = [
    "FailureClassification",
    "classification_for_failure_class",
    "classify_failure_evidence",
    "classify_raw_exit_failure",
    "failure_origin_for_failure_class",
    "raw_exit_code",
    "raw_exit_kind",
    "raw_failure_evidence",
    "timeout_reconciled",
    "transport_reconciliation_notes",
]
