"""Runtime recovery subpackage."""

from .blocked_metadata import (
    BlockedItemMetadata,
    blocked_metadata_path,
    build_blocked_item_metadata,
    load_blocked_metadata,
    write_blocked_item_metadata,
)
from .environmental import (
    BlockedOrigin,
    FailureScope,
    auto_retryable_scope,
    blocked_origin_from_metadata,
    failure_scope_from_metadata,
)
from .error_context import (
    clear_runtime_error_context,
    load_runtime_error_context,
    save_runtime_error_context,
)
from .queue_mutation import (
    BlockedTaskRequeueResult,
    BlockedWorkItemRetryResult,
    StrandedBlockedDependency,
    attempt_stranded_dependency_auto_recovery,
    retry_blocked_task,
    retry_blocked_work_item,
)
from .repair_routes import (
    RuntimeRepairRoute,
    incremented_repair_counter,
    runtime_repair_attempts_exhausted,
    runtime_repair_route_for_plane,
)
from .reports import (
    build_runtime_error_request_fields,
    runtime_error_catalog_path,
    write_runtime_error_report,
)
from .retry_policy import AUTO_REQUEUE_FAILURE_CLASSES, count_auto_requeues, metadata_allows_auto_requeue

__all__ = [
    "AUTO_REQUEUE_FAILURE_CLASSES",
    "BlockedItemMetadata",
    "BlockedOrigin",
    "BlockedTaskRequeueResult",
    "BlockedWorkItemRetryResult",
    "FailureScope",
    "RuntimeRepairRoute",
    "StrandedBlockedDependency",
    "attempt_stranded_dependency_auto_recovery",
    "auto_retryable_scope",
    "blocked_metadata_path",
    "blocked_origin_from_metadata",
    "build_blocked_item_metadata",
    "build_runtime_error_request_fields",
    "clear_runtime_error_context",
    "count_auto_requeues",
    "failure_scope_from_metadata",
    "incremented_repair_counter",
    "load_blocked_metadata",
    "load_runtime_error_context",
    "metadata_allows_auto_requeue",
    "retry_blocked_task",
    "retry_blocked_work_item",
    "runtime_error_catalog_path",
    "runtime_repair_attempts_exhausted",
    "runtime_repair_route_for_plane",
    "save_runtime_error_context",
    "write_blocked_item_metadata",
    "write_runtime_error_report",
]
