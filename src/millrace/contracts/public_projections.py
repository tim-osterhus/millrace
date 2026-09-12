"""Finite public read schema and opaque, identity-bound continuation tokens."""

from __future__ import annotations

import base64
import hashlib
import json
from typing import Any

CONTRACT_ID = "millrace.core.read"
CONTRACT_REVISION = 1
MAX_ITEM_BYTES = 16 * 1024
MAX_PAGE_BYTES = 128 * 1024
PUBLIC_SCHEMA: dict[str, Any] = {
    "contract_id": CONTRACT_ID,
    "revision": CONTRACT_REVISION,
    "page": {"default": 50, "max": 100, "bytes": MAX_PAGE_BYTES},
    "item_bytes": MAX_ITEM_BYTES,
    "cursor": ["contract_revision", "identity", "filter_digest", "pin", "last_key"],
    "consistency": ["transaction_snapshot", "immutable_selected_authority"],
    "relationships": [
        "declaration_kind",
        "declaration_id",
        "relation_role",
        "source",
        "target",
    ],
    "history": ["immutable_control", "lossy_session_telemetry"],
    "redaction_policy_id": "core-public-read-omit-v1",
}

# Explicit versioned field/type declaration. The digest identifies this read
# contract, including referenced controls/daemon contracts; it is not a wheel hash.
PUBLIC_SCHEMA["types"] = {
    "envelope": {
        "ok": "bool",
        "command": "text",
        "code": "text",
        "message": "safe-text",
        "data": "read-page",
    },
    "read-page": {
        "identity": "identity",
        "observation": "observation",
        "compatibility": "compatibility",
        "redaction_policy_id": "text",
        "records": "record[]",
        "filters": "exact-filter",
        "has_more": "bool",
        "next_cursor": "opaque-text|null",
    },
    "identity": {
        "workspace_id": "uuid",
        "instance_id": "uuid",
        "store_epoch": "uuid",
        "location_status": "registered",
    },
    "observation": {
        "observed_at": "utc-iso8601",
        "source_revision": "nonnegative-int64",
        "store_epoch": "uuid",
        "consistency": "transaction_snapshot|immutable_selected_authority",
    },
    "compatibility": {
        "public_contract_id": "millrace.core.read",
        "public_contract_revision": "1",
        "public_schema_digest": "sha256",
        "runtime_distribution": "millrace-ai",
        "runtime_version": "text",
        "build_identity": "null-until-attributed",
        "build_identity_status": "unavailable_until_attributable_installation",
        "store_schema_version": "integer",
        "daemon_runtime": "millrace.core.daemon/revision1.runtime_identity",
        "bounded_commands": "command[]",
        "bounded_option": "--bounded",
        "page_default": "50",
        "page_max": "100",
        "page_max_bytes": "131072",
        "item_max_bytes": "16384",
        "caller_deadline_seconds": "5",
        "native_control_qualification": "unqualified",
        "lifecycle_platform": "macOS",
        "other_platform_lifecycle": "unknown",
    },
    "unavailable": {"availability": "unavailable", "reason": "code"},
    "typed-endpoint": {
        "kind": "node|stage|action|route|selected_scope|terminal",
        "id": "text|null",
    },
    "relationship": {
        "kind": "relationship",
        "id": "sha256",
        "edge_id": "sha256",
        "plan_fingerprint": "sha256-prefixed",
        "declaration_kind": "text",
        "declaration_id": "text",
        "relation_role": "text",
        "source": "typed-endpoint",
        "target": "typed-endpoint",
        "condition_kind": (
            "unconditional|artifact_conditional|readiness|"
            "operator_resolution|artifact_dependency"
        ),
        "availability": (
            "available|dynamic_unresolved|unavailable_endpoint|unsupported_topology"
        ),
        "reason": "code|null",
        "traversal": "unsupported-count-null",
        "declaration_metadata": "explicit selected ID/kind fields only",
    },
    "node": {
        "kind": "node",
        "id": "text",
        "node_id": "text",
        "graph_ids": "text[]",
        "stage_bindings": "text[]",
        "membership": "availability+reason+ambiguous-bool",
    },
    "run": {
        "kind": "run",
        "id": "text",
        "run_id": "text",
        "work_item_id": "text",
        "activation_id": "text",
        "claim_id": "text",
        "run_generation": "int",
        "run_fencing_token": "text",
        "plan_fingerprint": "sha256-prefixed",
        "last_dispatch_generation": "int",
        "stage_kind_id": "text",
        "runner_binding_id": "text",
        "graph_node_id": "text|null",
        "queue_family_id": "text|null",
        "expected_session": "session-fence|null",
        "control_revision": "int",
        "execution_hold": "retained-run-control|null",
        "execution_state": (
            "runnable_unstarted|paused|unknown|"
            "retained-session-state|eligibility-refusal-code"
        ),
        "runner_activity": "not_started|unknown",
        "pause_control": "pause-control|null",
        "operation_status": "retained-result-stage|not_requested",
        "budget_binding": "budget-id|null",
        "blocking_reasons": "code[]",
        "runner_session": "session|null",
        "capability_profile": "capability-profile",
        "native_control": "unavailable|native-control",
        "quiescence_evidence": "unavailable|native-witness-snapshot",
        "checkpoint_status": "unavailable",
        "authority_fence": "run-plan-session-fence",
        "effect_status": "unavailable|native-control",
        "control_receipt_result_references": "immutable-record-reference[]",
    },
    "pause-control": {
        "pause_id": "uuid|null",
        "state": (
            "paused|resumed|superseded|unknown|pause_pending|resume_pending|"
            "recover_pending|retired|unqualified"
        ),
        "operation_id": "text|null",
        "profile_id": "text|null",
    },
    "capability-profile": {
        "qualification_status": (
            "unqualified|recorded_exact_profile_live_admission_required"
        ),
        "profile_id": "text|null",
        "profile_digest": "sha256|null",
        "selected_adapter": "text|null",
        "native_states": "empty-list|text[]",
        "effect_boundary": "text|null",
        "descendant_boundary": "text|null",
    },
    "session-fence": {
        "session_id": "text",
        "dispatch_generation": "int",
        "session_fencing_token": "text",
        "state": "retained-session-state",
    },
    "session": {
        "session_id": "text",
        "run_id": "text",
        "dispatch_generation": "int",
        "session_fencing_token": "text",
        "state": "retained-session-state",
        "created_at": "unix-seconds",
        "start_intent_at": "unix-seconds|null",
        "started_at": "unix-seconds|null",
        "ended_at": "unix-seconds|null",
        "context_manifest_digest": "sha256|null",
        "cleanup_disposition": "retained-cleanup-state",
        "budget_binding": "text|null",
        "budget": "budget|null",
        "completion_id": "text|null",
        "completion_input_id": "text|null",
        "completion_input_status": (
            "verified_payload_digest_and_trace|unavailable|not_completed"
        ),
        "application_input_id": "text|null",
        "completion_persisted": "bool",
        "application_persisted": "bool",
        "application_status": "not_completed|not_applicable|pending|applied|refused",
        "completion_terminal_state": "text|null",
        "completion_exit_kind": "text|null",
        "primary_cancellation_request_id": "text|null",
        "primary_cancellation_reason": "cancellation-reason|null",
        "orphan_risk": "bool",
        "effect_certainty": "unavailable|native-control",
        "descendant_aftermath": "unavailable|native-descendant-aftermath",
        "owner_liveness": "unavailable|historical-native-owner-observation",
        "recovery_status": "unavailable|native-local-retirement",
        "safe_recovery_reference": "operation-key|null",
        "attribution": "evidence-status-and-metrics",
        "context_cleanup": "evidence-status-and-counts",
        "usage_evidence": "evidence-status-and-tokens",
    },
    "runner-history": {
        "history_status": (
            "not_started|available|history_gap|history_unavailable|history_corrupt"
        ),
        "scope": "session-cursor-scope|null",
        "events": "safe-runner-event[]",
        "gap": "sequence-gap|null",
        "last_sequence": "int|null",
        "earliest_retained_sequence": "int|null",
        "next_after_sequence": "int",
        "next_cursor": "opaque-text|null",
        "has_more": "bool",
        "runner_session": "session|null",
        "durable_final": "session|null",
        "capture": "capture|null",
        "backing_store": "backing-store",
        "consistency": "separate_sidecar_snapshot_non_authoritative",
    },
    "capture": {
        "captured_at_ns": "unix-nanoseconds",
        "age_seconds": "nonnegative-number",
        "current_backing_health_proven": "false",
        "source": "writer_published_event_snapshot",
        "consistency": "committed_sidecar_capture",
        "durable_source_revision_applies": "false",
    },
    "control-history": {
        "history_status": "available",
        "history_kind": "immutable_control",
        "nonacceptance_proven": "false",
        "fresh_state": "run|null",
        "earliest_retained_sequence": "source-revision|null",
        "last_sequence": "source-revision|null",
        "gap": "null",
        "sequence_basis": "durable_source_revision",
        "record": (
            "unchanged millrace.core.controls/revision1 receipt or append-only result"
        ),
        "replayed": "true",
    },
    "daemon-history": {
        "record": "validated millrace.core.daemon/revision1 retained record",
        "process_observation": "unavailable",
        "provider_heartbeat": "unavailable",
        "daemon_session": "sequence+role+exact-session-fence+cleanup",
        "last_known_durable_progress": "availability+observed_at+source+scope",
    },
    "diagnostic": {
        "code": "text",
        "severity": "error",
        "source": "public_read",
        "status": "unavailable",
        "evidence_digest": "sha256",
        "redaction_policy_id": "core-public-read-omit-v1",
        "truncation": "detail_omitted-true",
    },
}


PUBLIC_SCHEMA["declaration_kind"] = "explicit-field-contract; not JSON Schema"
PUBLIC_SCHEMA["types"].update(
    {
        "queue-closure": {
            "kind": "queue_closure",
            "id": "closure-id",
            "plan_fingerprint": "sha256",
            "target_kind": "text",
            "target_id": "id",
            "created_by_input_id": "input-id",
        },
        "queue-closure-member": {
            "kind": "queue_closure_member",
            "id": "sha256",
            "closure_id": "id",
            "role": "closed_work_item_ids|closed_activation_ids|closed_run_ids",
            "member_id": "id",
            "plan_fingerprint": "sha256",
        },
        "cooldown-wait": {
            "kind": "cooldown_waits",
            "id": "wait-id",
            "wait_id": "id",
            "policy_id": "id",
            "lineage_id": "id",
            "recovery_attempt_record_id": "id",
            "plan_fingerprint": "sha256",
            "attempt_count": "int",
            "source_run_id": "id",
            "source_work_item_id": "id",
            "source_activation_id": "id",
            "recovery_action_id": "id",
            "target_stage_kind_id": "id",
            "target_graph_node_id": "id",
            "target_runner_binding_id": "id",
            "created_input_id": "id",
            "created_at": "int",
            "due_at": "int",
            "consumed_input_id": "id|null",
            "consumed_at": "int|null",
            "resulting_recovery_activation_id": "id|null",
        },
        "backing-store": {
            "presence": "present|missing|unknown",
            "validation": "not_checked|header_only|invalid_header",
            "health": "unknown|corrupt",
        },
        "completion-input": {
            "completion_input_id": (
                "unique-accepted-digest-and-trace-matched-input-id|null"
            ),
            "completion_input_status": (
                "not_completed|verified_payload_digest_and_trace|unavailable"
            ),
        },
    }
)


PUBLIC_SCHEMA["types"]["session"].update(
    {
        "cancellation_phase": "null|retained-session-state|cancellation-operation",
        "cancellation_last_operation": (
            "null|cooperative_cancel|terminate|kill|transport_cleanup"
        ),
        "cancellation_last_result": "null|succeeded|failed|timed_out|unsupported",
        "cooperative_cancel_grace_seconds": "nonnegative-number",
        "terminate_grace_seconds": "nonnegative-number",
        "completion_diagnostic_digest": "sha256|null",
        "diagnostic_status": "not_present|available|missing|corrupt|digest_mismatch",
        "diagnostics": "diagnostic[]",
    }
)
PUBLIC_SCHEMA["types"]["diagnostic"].update(
    {
        "kind": "diagnostic",
        "id": "sha256",
        "code": "safe-code",
        "severity": "info|error|non_candidate|policy_refusal|corrupt_authority",
        "source": "public_read|ready_dispatch|runner_session|runner_session_completion",
        "status": "unavailable|available|missing|corrupt|digest_mismatch|not_present",
        "run_id": "optional-id",
        "session_id": "optional-id",
        "dispatch_generation": "optional-int",
        "activation_id": "optional-id|null",
        "work_item_id": "optional-id|null",
        "plan_fingerprint": "optional-sha256|null",
    }
)
PUBLIC_SCHEMA["runner_pagination_terminal"] = (
    "no retained successor: explicit gap, unchanged after-sequence, "
    "has_more=false, next_cursor=null"
)

PUBLIC_SCHEMA["types"]["native-control"] = {
    "control_state": (
        "pause_pending|paused|resume_pending|resumed|unknown|"
        "recover_pending|retired|unqualified"
    ),
    "owner": "exact-process-birth-and-native-owner",
    "profile": "exact-native-profile",
    "witness_digest": "sha256",
    "witness_source_revision": "positive-int64",
    "fresh_admission_required": "true",
    "aftermath_reason": "code|null",
    "cleanup": "not_observed|pending|not_required",
    "daemon_cleanup": "unproved",
    "workflow_recovery": "not_granted",
    "continuation": "text",
}


# A native snapshot is represented once per run item. All other retained control
# fields keep their durable values; this reference is not a partial witness.
PUBLIC_SCHEMA["types"]["retained-run-control"] = {
    "run_id": "text",
    "control_revision": "int",
    "pause_id": "uuid|null",
    "state": "retained-control-state",
    "cause": "code",
    "operation_key": "exact-five-part-operation-key|null",
    "source_revision": "positive-int64",
    "recorded_at": "utc-iso8601",
    "native": "retained-native-evidence-with-snapshot-reference|null",
}
PUBLIC_SCHEMA["types"]["retained-native-evidence-with-snapshot-reference"] = {
    "attempt": "exact-retained-session-fence",
    "kind": "retained-native-evidence-kind",
    "owner": "exact-process-birth-and-native-owner",
    "profile": "exact-native-profile",
    "digest": "retained-operation-request-sha256-not-snapshot-digest",
    "snapshot_reference": "same-run-record-snapshot-reference",
    "aftermath_reason": "optional-retained-value",
    "recovery_witness": "optional-retained-value",
    "owner_observation": "optional-retained-value",
    "cleanup": "optional-retained-value",
    "daemon_cleanup": "optional-retained-value",
    "continuation": "optional-retained-value",
    "basis": "optional-retained-value",
}
PUBLIC_SCHEMA["types"]["same-run-record-snapshot-reference"] = {
    "scope": "same_run_record",
    "json_pointer": "/quiescence_evidence/witness",
}


def wire(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()


def digest(value: object) -> str:
    return hashlib.sha256(wire(value)).hexdigest()


PUBLIC_SCHEMA_DIGEST = digest(PUBLIC_SCHEMA)


def encode_cursor(value: dict[str, Any]) -> str:
    return base64.urlsafe_b64encode(wire(value)).decode().rstrip("=")


def decode_cursor(value: str) -> dict[str, Any]:
    if not isinstance(value, str) or len(value) > 16384:
        raise ValueError("invalid_cursor")
    try:
        raw = base64.b64decode(
            value + "=" * (-len(value) % 4), altchars=b"-_", validate=True
        )
        result = json.loads(raw)
        if not isinstance(result, dict) or encode_cursor(result) != value:
            raise ValueError
        return result
    except (ValueError, TypeError, RecursionError) as exc:
        raise ValueError("invalid_cursor") from exc
