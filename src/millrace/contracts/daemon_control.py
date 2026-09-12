"""Public daemon runtime fence, independent of installed-artifact qualification."""

from __future__ import annotations

import hashlib
from typing import Any

from millrace.contracts.controls import canonical_json

DAEMON_PUBLIC_SCHEMA = {
    "contract_id": "millrace.core.daemon",
    "contract_revision": 1,
    "methods": ["challenge", "status", "stop"],
    "target": {
        "workspace_id": "uuid",
        "instance_id": "uuid",
        "store_epoch": "uuid",
        "daemon_id": "uuid",
        "daemon_generation": "nonnegative-int64",
        "process_nonce": "uuid",
        "plan_fingerprint": "sha256-prefixed-or-null",
        "expected_source_revision": "nonnegative-int64",
        "expected_runtime": "runtime-identity",
    },
    "runtime_identity": {
        "public_contract_id": "text",
        "public_contract_revision": "nonnegative-int64",
        "runtime_distribution": "text",
        "runtime_version": "text",
        "build_identity": "sha256-or-null",
        "store_schema_version": "nonnegative-int64",
        "public_schema_digest": "sha256",
    },
    "process": {
        "pid": "positive-integer",
        "uid": "nonnegative-integer",
        "birth": "seconds-microseconds",
        "boot": "seconds-microseconds",
    },
    "lifecycle_states": [
        "initializing",
        "ready_idle",
        "ready_active",
        "not_ready",
        "stop_requested",
        "shutdown_complete",
        "stopped_clean",
        "unknown",
    ],
    "session_fence": [
        "session_id",
        "run_id",
        "dispatch_generation",
        "session_fencing_token",
    ],
    "runtime_progress": {
        "availability": ["available", "no_work_yet", "unavailable"],
        "last_runtime_progress_at": "unix-nanoseconds-or-null",
        "basis": "accepted_durable_workflow_transition",
        "evidence": [
            "captured_at_ns",
            "source_revision",
            "daemon_revision",
            "transition_order",
            "transition_digest",
            "run_fence_digest",
            "session_snapshot",
        ],
    },
    "cleanup": ["complete", "not_required", "pending", "orphan_risk", "unknown"],
    "page": {"limit": 50, "cursor": ["daemon_id", "source_revision", "after_session"]},
    "request_max_bytes": 16384,
    "response_max_bytes": 122880,
    "inspect_stop_seconds": 2,
    "readiness_exit_cleanup_observation_seconds": 10,
}
PUBLIC_SCHEMA_DIGEST = hashlib.sha256(
    canonical_json(DAEMON_PUBLIC_SCHEMA).encode()
).hexdigest()


def runtime_identity(version: str, schema_version: int) -> dict[str, Any]:
    return {
        "public_contract_id": "millrace.core.daemon",
        "public_contract_revision": 1,
        "runtime_distribution": "millrace-ai",
        "runtime_version": version,
        "build_identity": None,
        "store_schema_version": schema_version,
        "public_schema_digest": PUBLIC_SCHEMA_DIGEST,
    }


def session_chain(previous: str, fence: dict[str, Any]) -> str:
    return hashlib.sha256((previous + canonical_json(fence)).encode()).hexdigest()
