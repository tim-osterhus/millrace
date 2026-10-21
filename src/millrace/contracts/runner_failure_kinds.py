"""Mechanical failure vocabulary shared by compiler and runtime admission."""

ADAPTER_RUNTIME_FAILURE_KINDS = frozenset(
    {"timeout", "invocation_failed", "result_parse_failed", "output_too_large"}
)
RUNNER_RUNTIME_FAILURE_KINDS = ADAPTER_RUNTIME_FAILURE_KINDS | {"result_rejected"}
RECOVERABLE_RESULT_REFUSALS = frozenset(
    {
        "undeclared_terminal_outcome",
        "recovery_action_context_missing",
        "invalid_artifact_payload",
        "invalid_dynamic_route_target",
        "invalid_route_projection",
        "closure_verdict_schema_invalid",
        "closure_verdict_target_mismatch",
        "closure_root_contract_digest_mismatch",
        "closure_freshness_anchor_mismatch",
        "closure_rubric_mismatch",
        "closure_criterion_set_mismatch",
        "closure_verdict_evidence_mismatch",
        "closure_marker_status_mismatch",
        "invalid_fanout_payload",
        "invalid_remediation_payload",
        "counter_threshold_requires_escalation",
        "counter_threshold_not_reached",
    }
)
