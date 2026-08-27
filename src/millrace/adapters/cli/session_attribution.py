"""Source-backed runner-session attribution persistence."""

from __future__ import annotations

from millrace.adapters.cli.context import OpenRuntimeContext
from millrace.contracts.context_checkout import decode_context_checkout_manifest
from millrace.contracts.state import (
    AttributionMetric,
    RunnerSessionAttributionRecord,
    RunnerSessionRecord,
)
from millrace.substrate.errors import SubstrateError

_ADAPTER_FIELDS = (
    "cached_input_tokens",
    "reasoning_tokens",
    "provider_event_count",
    "provider_event_bytes",
    "wrapper_input_bytes",
    "retained_result_bytes",
    "tool_call_event_count",
    "runner_wall_milliseconds",
)
_CONTEXT_FIELDS = (
    "manifest_bytes",
    "catalog_bytes",
    "hydrated_bytes",
    "catalog_file_count",
    "hydrated_file_count",
    "distinct_content_digest_count",
)


def persist_runner_session_attribution(
    runtime: OpenRuntimeContext,
    *,
    session: RunnerSessionRecord,
    attribution: object | None,
) -> bool:
    metrics = _adapter_metrics(attribution)
    try:
        metrics.update(_context_metrics(runtime, session=session))
        record = RunnerSessionAttributionRecord(
            session_id=session.session_id,
            dispatch_generation=session.dispatch_generation,
            fencing_token=session.session_fencing_token,
            final=True,
            metrics=metrics,
        )
        return runtime.store.record_runner_session_attribution(record) == record
    except (OSError, SubstrateError, TypeError, ValueError):
        return False


def _adapter_metrics(attribution: object | None) -> dict[str, AttributionMetric]:
    return {
        field_name: AttributionMetric(
            value=(
                None if attribution is None else getattr(attribution, field_name)
            ),
            source="adapter.direct",
            availability=(
                "unavailable"
                if attribution is None or getattr(attribution, field_name) is None
                else "observed"
            ),
        )
        for field_name in _ADAPTER_FIELDS
    }


def _context_metrics(
    runtime: OpenRuntimeContext,
    *,
    session: RunnerSessionRecord,
) -> dict[str, AttributionMetric]:
    digest = session.context_manifest_digest
    if digest is None:
        return {
            field_name: AttributionMetric(
                value=None,
                source="runtime.context",
                availability="unavailable",
            )
            for field_name in _CONTEXT_FIELDS
        }

    manifest_bytes = runtime.cas_store.get_bytes(digest)
    manifest = decode_context_checkout_manifest(manifest_bytes)
    if (
        manifest.session_id != session.session_id
        or manifest.dispatch_generation != session.dispatch_generation
    ):
        raise ValueError("context manifest authority drifted")
    receipts = runtime.store.load_context_hydration_receipts_authenticated(
        session.session_id,
        session.dispatch_generation,
        session.session_fencing_token,
    )
    if any(receipt.manifest_digest != digest for receipt in receipts):
        raise ValueError("context hydration receipt authority drifted")
    values = {
        "manifest_bytes": len(manifest_bytes),
        "catalog_bytes": sum(item.byte_length for item in manifest.catalog),
        "hydrated_bytes": sum(receipt.byte_length for receipt in receipts),
        "catalog_file_count": len(manifest.catalog),
        "hydrated_file_count": len(receipts),
        "distinct_content_digest_count": len(
            {receipt.content_digest for receipt in receipts}
        ),
    }
    return {
        field_name: AttributionMetric(
            value=value,
            source=(
                "runtime.context_manifest"
                if field_name
                in {"manifest_bytes", "catalog_bytes", "catalog_file_count"}
                else "runtime.hydration_receipts"
            ),
            availability=(
                "observed" if field_name == "manifest_bytes" else "derived"
            ),
        )
        for field_name, value in values.items()
    }


__all__ = ("persist_runner_session_attribution",)
