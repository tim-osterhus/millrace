"""SQLite runtime store public facade."""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager, nullcontext
from pathlib import Path
from typing import Any

import millrace.substrate._sqlite_controls as controls
import millrace.substrate._sqlite_daemon as daemon_controls
import millrace.substrate._sqlite_run_controls as run_controls
from millrace.contracts.controls import ControlRequest
from millrace.contracts.fingerprints import AuthorityFingerprint
from millrace.contracts.state import (
    DURABLE_INT64_MAX,
    RUNNER_SESSION_TEXT_MAX_BYTES,
    ContextCleanupReceipt,
    ContextHydrationReceipt,
    DaemonBudgetEpochRecord,
    PlanRef,
    RunnerSessionAttributionRecord,
    RunnerSessionRecord,
    RunnerSessionUsageRecord,
    RuntimeState,
)
from millrace.substrate._sqlite_controls import ControlDecision
from millrace.substrate._sqlite_load import (
    load_context_cleanup_receipt,
    load_context_cleanup_receipt_authenticated,
    load_context_cleanup_receipts,
    load_context_hydration_receipts,
    load_context_hydration_receipts_authenticated,
    load_runner_session_attribution,
    load_runner_session_attribution_authenticated,
    load_runner_session_attributions,
    load_runtime_state_rows,
)
from millrace.substrate._sqlite_schema import (
    StoreSchemaMetadata,
    configure_connection,
    initialize_schema,
    read_metadata,
    table_names,
    validate_metadata,
    validate_schema_shape,
)
from millrace.substrate._sqlite_write import (
    persist_context_cleanup_receipt,
    persist_context_hydration_receipt,
    persist_runner_session_attribution,
    persist_runtime_state_rows,
)
from millrace.substrate._workflow_package_command_audit import (
    append_workflow_package_command_audit_event,
    load_workflow_package_command_audit_events,
    workflow_package_command_id_exists,
)
from millrace.substrate.cas import ContentAddressedByteStore
from millrace.substrate.errors import ControlOperationError, StoreNotInitialized
from millrace.substrate.records import (
    WorkflowPackageCommandAuditEventRecord,
    WorkflowPackageRegistryRecord,
    WorkflowPackageRegistrySnapshot,
)
from millrace.substrate.workflow_packages import (
    WorkflowPackageSource,
    disable_workflow_package,
    enable_workflow_package,
    export_workflow_package_archive,
    import_workflow_package_source,
    load_workflow_package_registry,
    remove_workflow_package,
)

_MAX_DAEMON_BUDGET_CLOCK_ADVANCE_SECONDS = 86_400


class SQLiteRuntimeStore:
    """Owns one SQLite connection for the v0.22.0 runtime store."""

    def __init__(
        self, connection: sqlite3.Connection, paths: tuple[str, str, str]
    ) -> None:
        self._connection = connection
        self._control_paths = paths
        self.daemon_scope: dict[str, Any] | None = None

    @classmethod
    def initialize(
        cls,
        path: str | Path,
        *,
        workspace_path: str | Path | None = None,
        cas_path: str | Path | None = None,
    ) -> SQLiteRuntimeStore:
        paths = controls.location_paths(path, workspace_path, cas_path)
        connection = sqlite3.connect(paths[1], timeout=1.0)
        try:
            configure_connection(connection)
            existing_tables = table_names(connection)
            if existing_tables:
                if "store_metadata" not in existing_tables:
                    raise StoreNotInitialized(
                        "SQLite store is missing initialization marker"
                    )
                validate_metadata(read_metadata(connection))
                validate_schema_shape(connection)
            else:
                initialize_schema(connection, paths)
            controls.identity(connection, paths)
            validate_metadata(read_metadata(connection))
            validate_schema_shape(connection)
            controls.identity(connection, paths)
        except Exception:
            connection.close()
            raise
        return cls(connection, paths)

    @classmethod
    def open(
        cls,
        path: str | Path,
        *,
        workspace_path: str | Path | None = None,
        cas_path: str | Path | None = None,
    ) -> SQLiteRuntimeStore:
        paths = controls.location_paths(path, workspace_path, cas_path)
        db_path = Path(paths[1])
        if not db_path.exists():
            raise StoreNotInitialized(
                f"SQLite store is missing initialization marker: {db_path}"
            )
        connection = sqlite3.connect(
            db_path.as_uri() + "?mode=rw", uri=True, timeout=1.0
        )
        try:
            configure_connection(connection)
            validate_metadata(read_metadata(connection))
            validate_schema_shape(connection)
            controls.identity(connection, paths)
        except Exception:
            connection.close()
            raise
        return cls(connection, paths)

    @classmethod
    def open_readonly(
        cls,
        path: str | Path,
        *,
        workspace_path: str | Path | None = None,
        cas_path: str | Path | None = None,
    ) -> SQLiteRuntimeStore:
        paths = controls.location_paths(path, workspace_path, cas_path)
        if not Path(paths[1]).is_file():
            raise StoreNotInitialized("store_not_initialized")
        connection = sqlite3.connect(
            Path(paths[1]).as_uri() + "?mode=ro", uri=True, timeout=0.1
        )
        try:
            connection.execute("PRAGMA query_only=ON")
            validate_metadata(read_metadata(connection))
            validate_schema_shape(connection)
            controls.identity(connection, paths)
        except BaseException:
            connection.close()
            raise
        return cls(connection, paths)

    def read_transaction(self) -> AbstractContextManager[None]:
        return controls.read_transaction(self._connection)

    def control_history_records(
        self, *, run_id: str | None = None
    ) -> list[dict[str, Any]]:
        return controls.control_history_records(
            self._connection,
            snapshot_revision=self.control_identity()["source_revision"],
            run_id=run_id,
        )

    def retained_daemon_records(self) -> list[dict[str, Any]]:
        return daemon_controls.load_records(self._connection)

    def retained_daemon_sessions(self, daemon_id: str) -> list[dict[str, Any]]:
        records = daemon_controls.load_records(self._connection)
        record = next(
            (item for item in records if item["daemon_id"] == daemon_id), None
        )
        if record is None:
            raise ControlOperationError("daemon_not_found")
        return daemon_controls.session_page(
            self._connection, daemon_id, limit=record["session_count"], wire_limit=None
        )

    def admit_daemon_unit(self) -> None:
        if self.daemon_scope is not None:
            daemon_controls.admit_unit(self._connection, self.daemon_scope)

    def daemon_default_plan(self) -> str | None:
        return daemon_controls.plan_fingerprint(self._connection)

    def daemon_records(self) -> list[dict[str, Any]]:
        with controls.control_transaction(
            self._connection, deadline=time.monotonic() + 1
        ):
            return daemon_controls.load_records(self._connection)

    def register_daemon(
        self, record: dict[str, Any], previous_revision: int
    ) -> dict[str, Any]:
        return daemon_controls.register(
            self._connection, self._control_paths, record, previous_revision
        )

    def update_daemon(self, scope: dict[str, Any], **changes: Any) -> dict[str, Any]:
        return daemon_controls.update(self._connection, scope, **changes)

    def accept_daemon_stop(
        self, scope: dict[str, Any], request: ControlRequest, *, deadline: float
    ) -> dict[str, Any]:
        return daemon_controls.accept_stop(
            self._connection, self._control_paths, scope, request, deadline=deadline
        )

    def signal_daemon_stop(self, scope: dict[str, Any]) -> None:
        daemon_controls.accept_signal_stop(self._connection, self._control_paths, scope)

    def attach_daemon_sessions(
        self, scope: dict[str, Any], fences: list[dict[str, Any]], role: str = "owned"
    ) -> dict[str, Any]:
        return daemon_controls.attach_sessions(self._connection, scope, fences, role)

    def daemon_snapshot(
        self,
        *,
        daemon_id: str | None = None,
        after_session: int = 0,
        expected_revision: int | None = None,
    ) -> dict[str, Any]:
        """One durable snapshot; callers observe processes/network only after return."""
        with controls.control_transaction(
            self._connection, deadline=time.monotonic() + 1
        ):
            records = daemon_controls.load_records(self._connection)
            identity = controls.identity(self._connection, self._control_paths)
            revision = identity["source_revision"]
            if expected_revision is not None and expected_revision != revision:
                raise ControlOperationError("daemon_snapshot_changed")
            record = (
                next((item for item in records if item["daemon_id"] == daemon_id), None)
                if daemon_id is not None
                else records[-1]
                if records
                else None
            )
            page = (
                []
                if record is None
                else daemon_controls.session_page(
                    self._connection, record["daemon_id"], after_session
                )
            )
            count = 0 if record is None else record["session_count"]
            return {
                "record": record,
                "identity": identity,
                "default_plan": daemon_controls.plan_fingerprint(self._connection),
                "page": {
                    "runner_sessions": page,
                    "session_count": count,
                    "next_after_session": page[-1]["sequence"]
                    if page and page[-1]["sequence"] < count
                    else None,
                    "source_revision": revision,
                },
            }

    def daemon_session_page(
        self, daemon_id: str, after: int = 0, expected_revision: int | None = None
    ) -> dict[str, Any]:
        snapshot = self.daemon_snapshot(
            daemon_id=daemon_id,
            after_session=after,
            expected_revision=expected_revision,
        )
        return dict(snapshot["page"])

    def daemon_owned_sessions(self, scope: dict[str, Any]) -> list[dict[str, Any]]:
        with controls.control_transaction(
            self._connection, deadline=time.monotonic() + 1
        ):
            record = daemon_controls.exact_record(self._connection, scope)
            return daemon_controls.session_page(
                self._connection,
                record["daemon_id"],
                limit=record["session_count"],
                wire_limit=None,
            )

    def finish_daemon(
        self,
        scope: dict[str, Any],
        summary: dict[str, Any],
        results: list[dict[str, Any]],
        expected_revision: int,
    ) -> dict[str, Any]:
        return daemon_controls.finish(
            self._connection, scope, summary, results, expected_revision
        )

    def control_identity(self) -> dict[str, Any]:
        return controls.identity(self._connection, self._control_paths)

    def control_transaction(self) -> AbstractContextManager[None]:
        """Short begin-before-load transaction; no external/native/context I/O."""
        return controls.control_transaction(self._connection)

    def show_operation(self, request: ControlRequest) -> dict[str, Any]:
        return controls.show_operation(self._connection, self._control_paths, request)

    def resolve_operation(self, request: ControlRequest) -> dict[str, Any]:
        return controls.resolve_operation(
            self._connection, self._control_paths, request
        )

    def execute_operation(
        self,
        request: ControlRequest,
        decide: Callable[[], ControlDecision],
        *,
        deadline: float | None = None,
    ) -> dict[str, Any]:
        return controls.execute_operation(
            self._connection, self._control_paths, request, decide, deadline=deadline
        )

    def append_operation_result(
        self,
        request: ControlRequest,
        *,
        result_id: str,
        stage: str,
        evidence: dict[str, Any],
    ) -> dict[str, Any]:
        return controls.append_operation_result(
            self._connection,
            self._control_paths,
            request,
            result_id=result_id,
            stage=stage,
            evidence=evidence,
        )

    def execute_run_control(
        self,
        request: ControlRequest,
        cas_store: ContentAddressedByteStore,
        *,
        supported_adapter_kinds: frozenset[str] = frozenset(),
        deadline: float | None = None,
    ) -> dict[str, Any]:
        deadline = (
            min(time.monotonic() + 4, deadline)
            if deadline is not None
            else time.monotonic() + 4
        )
        # Replay remains receipt-only even if current runtime context is unusable.
        history = self.show_operation(request)
        run_controls.check_deadline(deadline)
        if history["receipt"] is not None:
            return history
        revision = self.control_identity()["source_revision"]
        self._connection.set_progress_handler(
            lambda: int(time.monotonic() >= deadline), 1000
        )
        try:
            state = self.load_runtime_state(
                run_controls.BoundedControlCAS(cas_store, deadline), _optimistic=True
            )
        finally:
            self._connection.set_progress_handler(None, 0)
        run_controls.check_deadline(deadline)
        return controls.execute_operation(
            self._connection,
            self._control_paths,
            request,
            lambda: run_controls.decide_run_control(
                self._connection,
                state,
                request,
                revision,
                supported_adapter_kinds,
            ),
            deadline=deadline,
        )

    def accept_native_control(
        self,
        request: ControlRequest,
        state: RuntimeState,
        revision: int,
        owner: dict[str, Any],
        snapshot: dict[str, Any],
        deadline: float,
    ) -> dict[str, Any]:
        return controls.execute_operation(
            self._connection,
            self._control_paths,
            request,
            lambda: run_controls.native_intent(
                self._connection, state, request, revision, owner, snapshot
            ),
            deadline=deadline,
        )

    def update_native_control(
        self,
        run_id: str,
        owner_id: str,
        snapshot: dict[str, Any],
        *,
        reason: str | None = None,
    ) -> None:
        with controls.control_transaction(
            self._connection, deadline=time.monotonic() + 0.8
        ):
            run_controls.native_update(
                self._connection, run_id, owner_id, snapshot, reason=reason
            )

    def accept_native_recovery(
        self,
        request: ControlRequest,
        state: RuntimeState,
        revision: int,
        deadline: float,
        *,
        observe_loss: bool = False,
    ) -> dict[str, Any]:
        return controls.execute_operation(
            self._connection,
            self._control_paths,
            request,
            lambda: (
                run_controls.native_recovery_observe_loss
                if observe_loss
                else run_controls.native_recovery_intent
            )(self._connection, request, state, revision),
            deadline=deadline,
        )

    def finish_native_recovery(self, request: ControlRequest, deadline: float) -> None:
        with controls.control_transaction(self._connection, deadline=deadline):
            run_controls.native_recovery_finish(self._connection, request)

    def persist_runner_start(
        self,
        state: RuntimeState,
        cas_store: ContentAddressedByteStore,
        session: RunnerSessionRecord,
        driving_budget_id: str | None,
        driving_budget_clock: Callable[[], float] | None = None,
    ) -> None:
        """Stage CAS; commit intent and explicit epoch reservation together."""
        if session.state != "starting" or session.start_intent_at is None:
            raise ValueError("runner_start_intent_required")

        def reserve() -> None:
            if (
                driving_budget_id is None
                and self.daemon_budget_id_for_session(session.session_id) is not None
            ):
                raise ValueError("runner_start_budget_required")
            if driving_budget_id is not None:
                self.reserve_budgeted_runner_start(
                    driving_budget_id,
                    session,
                    observed_at=int((driving_budget_clock or time.time)()),
                )

        persist_runtime_state_rows(
            self._connection,
            state,
            cas_store,
            _before_sqlite_commit=reserve,
            daemon_scope=self.daemon_scope,
        )

    def schema_metadata(self) -> StoreSchemaMetadata:
        return read_metadata(self._connection)

    def persist_runtime_state(
        self,
        state: RuntimeState,
        cas_store: ContentAddressedByteStore,
    ) -> None:
        if self.daemon_scope is None:
            persist_runtime_state_rows(self._connection, state, cas_store)
        else:
            persist_runtime_state_rows(
                self._connection, state, cas_store, daemon_scope=self.daemon_scope
            )

    def load_runtime_state(
        self,
        cas_store: ContentAddressedByteStore,
        *,
        _optimistic: bool = False,
    ) -> RuntimeState:
        return load_runtime_state_rows(
            self._connection, cas_store, _optimistic=_optimistic
        )

    def load_runtime_state_for_rejected_result_inspection(
        self,
        cas_store: ContentAddressedByteStore,
        run_id: str,
    ) -> RuntimeState:
        return load_runtime_state_rows(
            self._connection,
            cas_store,
            rejected_result_inspection_run_id=run_id,
        )

    def load_daemon_budget_epoch(
        self,
        budget_id: str,
    ) -> DaemonBudgetEpochRecord | None:
        row = self._connection.execute(
            """
            SELECT
                budget_id, workspace_path, plan_id,
                plan_authority_fingerprint, plan_format_version,
                max_wall_seconds, max_invocations, max_total_tokens,
                started_at, wall_deadline, last_observed_at,
                accepted_start_count, cumulative_input_tokens,
                cumulative_output_tokens, cumulative_total_tokens,
                status, terminal_reason, schema_version
            FROM daemon_budget_epochs
            WHERE budget_id = ?
            """,
            (budget_id,),
        ).fetchone()
        if row is None:
            return None
        if type(row[17]) is not int or row[17] != 1:
            raise ValueError("daemon budget epoch schema version is unsupported")
        if (
            not all(isinstance(row[index], str) for index in (0, 1, 2, 3, 15))
            or (row[16] is not None and not isinstance(row[16], str))
            or any(type(row[index]) is not int for index in (4, 8, 10, 11, 12, 13, 14))
        ):
            raise ValueError("invalid daemon budget epoch row")
        admitted_plan = self._connection.execute(
            """
            SELECT plan_id, plan_format_version
            FROM admitted_plan_pins
            WHERE authority_fingerprint = ?
            """,
            (row[3],),
        ).fetchone()
        if admitted_plan != (row[2], row[4]):
            raise ValueError("daemon budget plan pin is invalid")
        try:
            epoch = DaemonBudgetEpochRecord(
                budget_id=row[0],
                workspace_path=row[1],
                selected_plan_ref=PlanRef(
                    plan_id=row[2],
                    authority_fingerprint=AuthorityFingerprint(row[3]),
                    plan_format_version=row[4],
                ),
                max_wall_seconds=_optional_int(row[5]),
                max_invocations=_optional_int(row[6]),
                max_total_tokens=_optional_int(row[7]),
                started_at=row[8],
                wall_deadline=_optional_int(row[9]),
                last_observed_at=row[10],
                accepted_start_count=row[11],
                cumulative_input_tokens=row[12],
                cumulative_output_tokens=row[13],
                cumulative_total_tokens=row[14],
                status=row[15],
                terminal_reason=row[16],
            )
        except (TypeError, ValueError, AttributeError) as exc:
            raise ValueError(f"invalid daemon budget epoch row: {exc}") from exc
        accepted_count = self._connection.execute(
            """
            SELECT COUNT(*)
            FROM daemon_budget_sessions
            WHERE budget_id = ? AND accepted_at IS NOT NULL
            """,
            (epoch.budget_id,),
        ).fetchone()[0]
        usage_totals = [0, 0, 0]
        usage_rows = self._connection.execute(
            """
            SELECT input_tokens, output_tokens, total_tokens
            FROM runner_session_usage
            WHERE budget_id = ?
            """,
            (epoch.budget_id,),
        )
        for usage_row in usage_rows:
            if not all(type(value) is int for value in usage_row):
                raise ValueError("invalid daemon budget aggregate authority")
            for index, value in enumerate(usage_row):
                if value < 0 or value > DURABLE_INT64_MAX - usage_totals[index]:
                    raise ValueError("invalid daemon budget aggregate authority")
                usage_totals[index] += value
        expected_aggregates = (
            epoch.accepted_start_count,
            epoch.cumulative_input_tokens,
            epoch.cumulative_output_tokens,
            epoch.cumulative_total_tokens,
        )
        durable_aggregates = (accepted_count, *usage_totals)
        if (
            not all(type(value) is int for value in durable_aggregates)
            or durable_aggregates != expected_aggregates
        ):
            raise ValueError("invalid daemon budget aggregate authority")
        return epoch

    def list_daemon_budget_epochs(
        self,
        *,
        limit: int = 100,
    ) -> tuple[DaemonBudgetEpochRecord, ...]:
        if type(limit) is not int or limit < 1 or limit > 100:
            raise ValueError("daemon budget epoch limit must be between 1 and 100")
        budget_ids = tuple(
            str(row[0])
            for row in self._connection.execute(
                """
                SELECT budget_id
                FROM daemon_budget_epochs
                ORDER BY started_at DESC, budget_id
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        )
        records = tuple(
            self.load_daemon_budget_epoch(budget_id) for budget_id in budget_ids
        )
        if any(record is None for record in records):
            raise ValueError("daemon budget epoch disappeared during projection")
        return tuple(record for record in records if record is not None)

    def create_or_resume_daemon_budget_epoch(
        self,
        epoch: DaemonBudgetEpochRecord,
    ) -> DaemonBudgetEpochRecord:
        existing = self.load_daemon_budget_epoch(epoch.budget_id)
        if existing is not None:
            immutable_existing = (
                existing.workspace_path,
                existing.selected_plan_ref,
                existing.max_wall_seconds,
                existing.max_invocations,
                existing.max_total_tokens,
                existing.started_at,
                existing.wall_deadline,
            )
            immutable_candidate = (
                epoch.workspace_path,
                epoch.selected_plan_ref,
                epoch.max_wall_seconds,
                epoch.max_invocations,
                epoch.max_total_tokens,
                epoch.started_at,
                epoch.wall_deadline,
            )
            if immutable_existing != immutable_candidate:
                raise ValueError("daemon_budget_immutable_limits_changed")
            if existing.status != "active":
                return existing
            if epoch.last_observed_at < existing.last_observed_at:
                raise ValueError("daemon_budget_clock_discontinuity")
            if (
                epoch.last_observed_at - existing.last_observed_at
                > _MAX_DAEMON_BUDGET_CLOCK_ADVANCE_SECONDS
            ):
                raise ValueError("daemon_budget_clock_discontinuity")
            self._connection.execute(
                """
                UPDATE daemon_budget_epochs
                SET last_observed_at = ?
                WHERE budget_id = ? AND last_observed_at = ?
                """,
                (
                    epoch.last_observed_at,
                    epoch.budget_id,
                    existing.last_observed_at,
                ),
            )
            self._connection.commit()
            return self.load_daemon_budget_epoch(epoch.budget_id) or existing
        if (
            epoch.accepted_start_count != 0
            or epoch.cumulative_input_tokens != 0
            or epoch.cumulative_output_tokens != 0
            or epoch.cumulative_total_tokens != 0
        ):
            raise ValueError("new daemon budget epoch counters must be zero")
        if epoch.status != "active":
            raise ValueError("new daemon budget epoch must be active")
        self._connection.execute(
            """
            INSERT INTO daemon_budget_epochs (
                budget_id, schema_version, workspace_path, plan_id,
                plan_authority_fingerprint, plan_format_version,
                max_wall_seconds, max_invocations, max_total_tokens,
                started_at, wall_deadline, last_observed_at,
                accepted_start_count, cumulative_input_tokens,
                cumulative_output_tokens, cumulative_total_tokens,
                status, terminal_reason
            ) VALUES (?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                epoch.budget_id,
                epoch.workspace_path,
                epoch.selected_plan_ref.plan_id,
                epoch.selected_plan_ref.authority_fingerprint,
                epoch.selected_plan_ref.plan_format_version,
                epoch.max_wall_seconds,
                epoch.max_invocations,
                epoch.max_total_tokens,
                epoch.started_at,
                epoch.wall_deadline,
                epoch.last_observed_at,
                epoch.accepted_start_count,
                epoch.cumulative_input_tokens,
                epoch.cumulative_output_tokens,
                epoch.cumulative_total_tokens,
                epoch.status,
                epoch.terminal_reason,
            ),
        )
        self._connection.commit()
        return epoch

    def reserve_budgeted_runner_start(
        self,
        budget_id: str,
        session: RunnerSessionRecord,
        *,
        observed_at: int | None = None,
    ) -> DaemonBudgetEpochRecord:
        with (
            nullcontext()
            if self._connection.in_transaction
            else self.control_transaction()
        ):
            run_controls.guard_budget_reservation(self._connection, session.run_id)
            epoch = self.load_daemon_budget_epoch(budget_id)
            if epoch is None or epoch.status != "active":
                raise ValueError("daemon_budget_not_active")
            self._validate_budget_session_authority(epoch, session)
            existing = self._connection.execute(
                """
                SELECT budget_id, run_id, dispatch_generation,
                       session_fencing_token, accepted_at
                FROM daemon_budget_sessions
                WHERE session_id = ?
                """,
                (session.session_id,),
            ).fetchone()
            expected = (
                budget_id,
                session.run_id,
                session.dispatch_generation,
                session.session_fencing_token,
            )
            if existing is not None:
                if tuple(existing[:4]) != expected:
                    raise ValueError("runner_session_budget_identity_mismatch")
                if existing[4] is not None:
                    if existing[4] != session.start_intent_at:
                        raise ValueError("runner_session_budget_identity_mismatch")
                    return epoch
            observed_at = epoch.last_observed_at if observed_at is None else observed_at
            if observed_at < epoch.last_observed_at:
                raise ValueError("daemon_budget_clock_discontinuity")
            if epoch.wall_deadline is not None and observed_at >= epoch.wall_deadline:
                raise ValueError("daemon_budget_wall_exhausted")
            if (
                epoch.max_total_tokens is not None
                and epoch.cumulative_total_tokens >= epoch.max_total_tokens
            ):
                raise ValueError("daemon_budget_tokens_exhausted")
            if epoch.max_invocations is not None:
                pending_count = self._connection.execute(
                    """
                    SELECT COUNT(*)
                    FROM daemon_budget_sessions
                    WHERE budget_id = ? AND accepted_at IS NULL AND session_id != ?
                    """,
                    (budget_id, session.session_id),
                ).fetchone()[0]
                if epoch.accepted_start_count + pending_count >= epoch.max_invocations:
                    raise ValueError("daemon_budget_invocations_exhausted")
            if existing is not None:
                return epoch
            self._connection.execute(
                """
                INSERT INTO daemon_budget_sessions (
                    session_id, schema_version, budget_id, run_id,
                    dispatch_generation, session_fencing_token, accepted_at
                ) VALUES (?, 1, ?, ?, ?, ?, NULL)
                """,
                (
                    session.session_id,
                    budget_id,
                    session.run_id,
                    session.dispatch_generation,
                    session.session_fencing_token,
                ),
            )
        return self.load_daemon_budget_epoch(budget_id) or epoch

    def pending_budgeted_runner_start_session_ids(
        self,
        budget_id: str,
    ) -> tuple[str, ...]:
        rows = self._connection.execute(
            """
            SELECT session_id
            FROM daemon_budget_sessions
            WHERE budget_id = ? AND accepted_at IS NULL
            ORDER BY session_id
            """,
            (budget_id,),
        ).fetchall()
        session_ids = tuple(row[0] for row in rows)
        if not all(
            isinstance(session_id, str) and session_id for session_id in session_ids
        ):
            raise ValueError("invalid daemon budget session row")
        return session_ids

    def daemon_budget_session_ids(
        self,
        budget_id: str,
        *,
        limit: int = 100,
    ) -> tuple[int, tuple[str, ...]]:
        if type(limit) is not int or limit < 1 or limit > 100:
            raise ValueError("daemon budget session limit must be between 1 and 100")
        count = self._connection.execute(
            """
            SELECT COUNT(*)
            FROM daemon_budget_sessions
            WHERE budget_id = ?
            """,
            (budget_id,),
        ).fetchone()[0]
        rows = self._connection.execute(
            """
            SELECT session_id
            FROM daemon_budget_sessions
            WHERE budget_id = ?
            ORDER BY session_id
            LIMIT ?
            """,
            (budget_id, limit),
        ).fetchall()
        session_ids = tuple(row[0] for row in rows)
        if type(count) is not int or not all(
            isinstance(session_id, str) and session_id for session_id in session_ids
        ):
            raise ValueError("invalid daemon budget session row")
        return count, session_ids

    def record_budgeted_runner_start(
        self,
        budget_id: str,
        session: RunnerSessionRecord,
    ) -> DaemonBudgetEpochRecord:
        if session.start_intent_at is None:
            raise ValueError("runner session has no accepted start intent")
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            epoch = self.load_daemon_budget_epoch(budget_id)
            if epoch is None or epoch.status != "active":
                raise ValueError("daemon_budget_not_active")
            self._validate_budget_session_authority(epoch, session)
            existing = self._connection.execute(
                """
                SELECT budget_id, run_id, dispatch_generation,
                       session_fencing_token, accepted_at
                FROM daemon_budget_sessions
                WHERE session_id = ?
                """,
                (session.session_id,),
            ).fetchone()
            expected = (
                budget_id,
                session.run_id,
                session.dispatch_generation,
                session.session_fencing_token,
            )
            if existing is None:
                raise ValueError("runner_session_budget_start_not_reserved")
            if tuple(existing[:4]) != expected:
                raise ValueError("runner_session_budget_identity_mismatch")
            if existing[4] is not None:
                if existing[4] != session.start_intent_at:
                    raise ValueError("runner_session_budget_identity_mismatch")
                self._connection.commit()
                return epoch
            if (
                epoch.max_invocations is not None
                and epoch.accepted_start_count >= epoch.max_invocations
            ):
                raise ValueError("daemon_budget_invocations_exhausted")
            if epoch.accepted_start_count >= DURABLE_INT64_MAX:
                raise ValueError("daemon_budget_counter_overflow")
            accepted = self._connection.execute(
                """
                UPDATE daemon_budget_sessions
                SET accepted_at = ?
                WHERE session_id = ? AND budget_id = ? AND run_id = ?
                  AND dispatch_generation = ? AND session_fencing_token = ?
                  AND accepted_at IS NULL
                """,
                (
                    session.start_intent_at,
                    session.session_id,
                    budget_id,
                    session.run_id,
                    session.dispatch_generation,
                    session.session_fencing_token,
                ),
            )
            if accepted.rowcount != 1:
                raise ValueError("runner_session_budget_start_not_reserved")
            self._connection.execute(
                """
                UPDATE daemon_budget_epochs
                SET accepted_start_count = accepted_start_count + 1
                WHERE budget_id = ? AND status = 'active'
                """,
                (budget_id,),
            )
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise
        return self.load_daemon_budget_epoch(budget_id) or epoch

    def record_context_hydration_receipt(
        self,
        receipt: ContextHydrationReceipt,
    ) -> ContextHydrationReceipt:
        return persist_context_hydration_receipt(self._connection, receipt)

    def load_context_hydration_receipts(
        self,
        session_id: str,
    ) -> tuple[Mapping[str, object], ...]:
        return load_context_hydration_receipts(self._connection, session_id)

    def load_context_hydration_receipts_authenticated(
        self,
        session_id: str,
        dispatch_generation: int,
        fencing_token: str,
    ) -> tuple[ContextHydrationReceipt, ...]:
        return load_context_hydration_receipts_authenticated(
            self._connection,
            session_id,
            dispatch_generation,
            fencing_token,
        )

    def record_runner_session_attribution(
        self,
        record: RunnerSessionAttributionRecord,
    ) -> RunnerSessionAttributionRecord:
        return persist_runner_session_attribution(self._connection, record)

    def load_runner_session_attribution(
        self,
        session_id: str,
        dispatch_generation: int,
    ) -> Mapping[str, object] | None:
        return load_runner_session_attribution(
            self._connection,
            session_id,
            dispatch_generation,
        )

    def load_runner_session_attribution_authenticated(
        self,
        session_id: str,
        dispatch_generation: int,
        fencing_token: str,
    ) -> RunnerSessionAttributionRecord | None:
        return load_runner_session_attribution_authenticated(
            self._connection,
            session_id,
            dispatch_generation,
            fencing_token,
        )

    def load_runner_session_attributions(
        self,
        session_id: str,
    ) -> tuple[Mapping[str, object], ...]:
        return load_runner_session_attributions(self._connection, session_id)

    def record_context_cleanup_receipt(
        self,
        receipt: ContextCleanupReceipt,
    ) -> ContextCleanupReceipt:
        return persist_context_cleanup_receipt(self._connection, receipt)

    def load_context_cleanup_receipt(
        self,
        session_id: str,
        dispatch_generation: int,
        manifest_digest: str,
    ) -> Mapping[str, object] | None:
        return load_context_cleanup_receipt(
            self._connection,
            session_id,
            dispatch_generation,
            manifest_digest,
        )

    def load_context_cleanup_receipt_authenticated(
        self,
        session_id: str,
        dispatch_generation: int,
        manifest_digest: str,
        fencing_token: str,
    ) -> ContextCleanupReceipt | None:
        return load_context_cleanup_receipt_authenticated(
            self._connection,
            session_id,
            dispatch_generation,
            manifest_digest,
            fencing_token,
        )

    def load_context_cleanup_receipts(
        self,
        session_id: str,
    ) -> tuple[Mapping[str, object], ...]:
        return load_context_cleanup_receipts(self._connection, session_id)

    def record_runner_session_usage(
        self,
        usage: RunnerSessionUsageRecord,
    ) -> DaemonBudgetEpochRecord:
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            epoch = self.load_daemon_budget_epoch(usage.budget_id)
            if epoch is None:
                raise ValueError("daemon budget epoch is missing")
            self._validate_runner_usage_authority(
                budget_id=usage.budget_id,
                session_id=usage.session_id,
            )
            binding = self._connection.execute(
                """
                SELECT run_id, dispatch_generation, session_fencing_token,
                       accepted_at
                FROM daemon_budget_sessions
                WHERE session_id = ? AND budget_id = ?
                """,
                (usage.session_id, usage.budget_id),
            ).fetchone()
            if (
                binding is None
                or tuple(binding[:3])
                != (
                    usage.run_id,
                    usage.dispatch_generation,
                    usage.session_fencing_token,
                )
                or binding[3] is None
            ):
                raise ValueError("runner_usage_evidence_refused")
            prior = self._connection.execute(
                """
                SELECT budget_id, run_id, dispatch_generation, session_fencing_token,
                       input_tokens, output_tokens, total_tokens, observed_at, final
                FROM runner_session_usage WHERE session_id = ?
                """,
                (usage.session_id,),
            ).fetchone()
            if prior is not None:
                if prior[:4] != (
                    usage.budget_id,
                    usage.run_id,
                    usage.dispatch_generation,
                    usage.session_fencing_token,
                ):
                    raise ValueError("runner_usage_evidence_refused")
                prior_values = tuple(int(value) for value in prior[4:])
                current_values = (
                    usage.input_tokens,
                    usage.output_tokens,
                    usage.total_tokens,
                    usage.observed_at,
                    int(usage.final),
                )
                if current_values == prior_values:
                    self._connection.commit()
                    return epoch
                if (
                    usage.input_tokens < prior_values[0]
                    or usage.output_tokens < prior_values[1]
                    or usage.total_tokens < prior_values[2]
                    or usage.observed_at < prior_values[3]
                    or prior_values[4] == 1
                ):
                    raise ValueError("runner_usage_evidence_refused")
            previous_input = 0 if prior is None else int(prior[4])
            previous_output = 0 if prior is None else int(prior[5])
            input_delta = usage.input_tokens - previous_input
            output_delta = usage.output_tokens - previous_output
            next_input = epoch.cumulative_input_tokens + input_delta
            next_output = epoch.cumulative_output_tokens + output_delta
            next_total = next_input + next_output
            if any(
                value > DURABLE_INT64_MAX
                for value in (next_input, next_output, next_total)
            ):
                raise ValueError("runner_usage_evidence_refused")
            self._connection.execute(
                """
                INSERT INTO runner_session_usage (
                    session_id, schema_version, budget_id, run_id,
                    dispatch_generation, session_fencing_token,
                    input_tokens, output_tokens, total_tokens, observed_at, final
                ) VALUES (?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    input_tokens = excluded.input_tokens,
                    output_tokens = excluded.output_tokens,
                    total_tokens = excluded.total_tokens,
                    observed_at = excluded.observed_at,
                    final = excluded.final
                """,
                (
                    usage.session_id,
                    usage.budget_id,
                    usage.run_id,
                    usage.dispatch_generation,
                    usage.session_fencing_token,
                    usage.input_tokens,
                    usage.output_tokens,
                    usage.total_tokens,
                    usage.observed_at,
                    int(usage.final),
                ),
            )
            self._connection.execute(
                """
                UPDATE daemon_budget_epochs
                SET cumulative_input_tokens = cumulative_input_tokens + ?,
                    cumulative_output_tokens = cumulative_output_tokens + ?,
                    cumulative_total_tokens = cumulative_total_tokens + ? + ?,
                    last_observed_at = MAX(last_observed_at, ?)
                WHERE budget_id = ?
                """,
                (
                    input_delta,
                    output_delta,
                    input_delta,
                    output_delta,
                    usage.observed_at,
                    usage.budget_id,
                ),
            )
            self._connection.commit()
        except Exception:
            self._connection.rollback()
            raise
        return self.load_daemon_budget_epoch(usage.budget_id) or epoch

    def load_runner_session_usage(
        self,
        session_id: str,
    ) -> RunnerSessionUsageRecord | None:
        row = self._connection.execute(
            """
            SELECT
                session_id, schema_version, budget_id, run_id,
                dispatch_generation, session_fencing_token,
                input_tokens, output_tokens, total_tokens, observed_at, final
            FROM runner_session_usage
            WHERE session_id = ?
            """,
            (session_id,),
        ).fetchone()
        if row is None:
            return None
        if type(row[1]) is not int or row[1] != 1:
            raise ValueError("runner session usage schema version is unsupported")
        if (
            not all(isinstance(row[index], str) for index in (0, 2, 3, 5))
            or any(type(row[index]) is not int for index in (4, 6, 7, 8, 9))
            or type(row[10]) is not int
            or row[10] not in {0, 1}
        ):
            raise ValueError("invalid runner session usage row")
        expected = (row[3], row[4], row[5])
        binding = self._connection.execute(
            """
            SELECT run_id, dispatch_generation, session_fencing_token,
                   accepted_at
            FROM daemon_budget_sessions
            WHERE session_id = ? AND budget_id = ?
            """,
            (row[0], row[2]),
        ).fetchone()
        if binding is None or tuple(binding[:3]) != expected or binding[3] is None:
            raise ValueError("runner_usage_evidence_refused")
        try:
            record = RunnerSessionUsageRecord(
                budget_id=row[2],
                session_id=row[0],
                run_id=row[3],
                dispatch_generation=row[4],
                session_fencing_token=row[5],
                input_tokens=row[6],
                output_tokens=row[7],
                total_tokens=row[8],
                observed_at=row[9],
                final=bool(row[10]),
            )
        except (TypeError, ValueError, AttributeError) as exc:
            raise ValueError(f"invalid runner session usage row: {exc}") from exc
        self._validate_runner_usage_authority(
            budget_id=record.budget_id,
            session_id=record.session_id,
        )
        return record

    def daemon_budget_id_for_session(self, session_id: str) -> str | None:
        row = self._connection.execute(
            """
            SELECT schema_version, budget_id, run_id, dispatch_generation,
                   session_fencing_token, accepted_at
            FROM daemon_budget_sessions
            WHERE session_id = ?
            """,
            (session_id,),
        ).fetchone()
        if row is None:
            return None
        (
            schema_version,
            budget_id,
            run_id,
            dispatch_generation,
            session_fencing_token,
            accepted_at,
        ) = row
        if (
            type(schema_version) is not int
            or schema_version != 1
            or not isinstance(budget_id, str)
            or not isinstance(run_id, str)
            or not isinstance(session_fencing_token, str)
        ):
            raise ValueError("invalid daemon budget session binding")
        if any(
            not value.strip()
            or len(value.encode("utf-8")) > RUNNER_SESSION_TEXT_MAX_BYTES
            for value in (budget_id, run_id, session_fencing_token)
        ):
            raise ValueError("invalid daemon budget session binding")
        if (
            type(dispatch_generation) is not int
            or dispatch_generation < 1
            or dispatch_generation > DURABLE_INT64_MAX
            or (
                accepted_at is not None
                and (
                    type(accepted_at) is not int
                    or accepted_at < 0
                    or accepted_at > DURABLE_INT64_MAX
                )
            )
        ):
            raise ValueError("invalid daemon budget session binding")
        session_identity = self._connection.execute(
            """
            SELECT run_id, dispatch_generation, session_fencing_token,
                   start_intent_at
            FROM runner_sessions
            WHERE session_id = ?
            """,
            (session_id,),
        ).fetchone()
        if session_identity is None or tuple(session_identity[:3]) != (
            run_id,
            dispatch_generation,
            session_fencing_token,
        ):
            raise ValueError("runner_session_budget_identity_mismatch")
        if accepted_at is not None and accepted_at != session_identity[3]:
            raise ValueError("runner_session_budget_identity_mismatch")
        epoch = self.load_daemon_budget_epoch(budget_id)
        if epoch is None:
            raise ValueError("daemon budget epoch is missing")
        run_plan = self._connection.execute(
            """
            SELECT plan_id, plan_authority_fingerprint, plan_format_version
            FROM runs
            WHERE run_id = ?
            """,
            (run_id,),
        ).fetchone()
        if run_plan != (
            epoch.selected_plan_ref.plan_id,
            epoch.selected_plan_ref.authority_fingerprint,
            epoch.selected_plan_ref.plan_format_version,
        ):
            raise ValueError("runner_session_budget_identity_mismatch")
        return budget_id

    def _validate_runner_usage_authority(
        self,
        *,
        budget_id: str,
        session_id: str,
    ) -> None:
        try:
            bound_budget_id = self.daemon_budget_id_for_session(session_id)
        except ValueError as exc:
            raise ValueError("runner_usage_evidence_refused") from exc
        if bound_budget_id != budget_id:
            raise ValueError("runner_usage_evidence_refused")

    def _validate_budget_session_authority(
        self,
        epoch: DaemonBudgetEpochRecord,
        session: RunnerSessionRecord,
    ) -> None:
        session_identity = self._connection.execute(
            """
            SELECT run_id, dispatch_generation, session_fencing_token
            FROM runner_sessions
            WHERE session_id = ?
            """,
            (session.session_id,),
        ).fetchone()
        if session_identity != (
            session.run_id,
            session.dispatch_generation,
            session.session_fencing_token,
        ):
            raise ValueError("runner_session_budget_identity_mismatch")
        run_plan = self._connection.execute(
            """
            SELECT plan_id, plan_authority_fingerprint, plan_format_version
            FROM runs
            WHERE run_id = ?
            """,
            (session.run_id,),
        ).fetchone()
        if run_plan != (
            epoch.selected_plan_ref.plan_id,
            epoch.selected_plan_ref.authority_fingerprint,
            epoch.selected_plan_ref.plan_format_version,
        ):
            raise ValueError("runner_session_budget_identity_mismatch")

    def _stop_daemon_budget_epoch(
        self,
        budget_id: str,
        *,
        observed_at: int,
        status: str,
        reason: str,
    ) -> DaemonBudgetEpochRecord:
        if status not in {"exhausted", "refused", "stopped"}:
            raise ValueError("unsupported terminal daemon budget status")
        epoch = self.load_daemon_budget_epoch(budget_id)
        if epoch is None:
            raise ValueError("daemon budget epoch is missing")
        if observed_at < epoch.last_observed_at:
            raise ValueError("daemon_budget_clock_discontinuity")
        if epoch.status == "active":
            commit_when_done = not self._connection.in_transaction
            self._connection.execute(
                """
                UPDATE daemon_budget_epochs
                SET status = ?, terminal_reason = ?, last_observed_at = ?
                WHERE budget_id = ? AND status = 'active'
                """,
                (status, reason, observed_at, budget_id),
            )
            if commit_when_done:
                self._connection.commit()
        elif epoch.status != status or epoch.terminal_reason != reason:
            raise ValueError("daemon budget epoch is already terminal")
        return self.load_daemon_budget_epoch(budget_id) or epoch

    def import_workflow_package_source(
        self,
        cas_store: ContentAddressedByteStore,
        source: WorkflowPackageSource,
        *,
        actor_id: str,
        update: bool = False,
        _before_sqlite_commit: Callable[[], None] | None = None,
        _before_sqlite_commit_with_record: (
            Callable[[WorkflowPackageRegistryRecord], None] | None
        ) = None,
    ) -> WorkflowPackageRegistryRecord:
        return import_workflow_package_source(
            self._connection,
            cas_store,
            source,
            actor_id=actor_id,
            update=update,
            _before_sqlite_commit=_before_sqlite_commit,
            _before_sqlite_commit_with_record=_before_sqlite_commit_with_record,
        )

    def load_workflow_package_registry(
        self,
        cas_store: ContentAddressedByteStore,
    ) -> WorkflowPackageRegistrySnapshot:
        return load_workflow_package_registry(self._connection, cas_store)

    def enable_workflow_package(
        self,
        package_id: str,
        package_version: str,
        *,
        actor_id: str,
        _before_sqlite_commit: Callable[[], None] | None = None,
        _before_sqlite_commit_with_record: (
            Callable[[WorkflowPackageRegistryRecord], None] | None
        ) = None,
    ) -> WorkflowPackageRegistryRecord:
        return enable_workflow_package(
            self._connection,
            package_id,
            package_version,
            actor_id=actor_id,
            _before_sqlite_commit=_before_sqlite_commit,
            _before_sqlite_commit_with_record=_before_sqlite_commit_with_record,
        )

    def disable_workflow_package(
        self,
        package_id: str,
        package_version: str,
        *,
        actor_id: str,
        _before_sqlite_commit: Callable[[], None] | None = None,
        _before_sqlite_commit_with_record: (
            Callable[[WorkflowPackageRegistryRecord], None] | None
        ) = None,
    ) -> WorkflowPackageRegistryRecord:
        return disable_workflow_package(
            self._connection,
            package_id,
            package_version,
            actor_id=actor_id,
            _before_sqlite_commit=_before_sqlite_commit,
            _before_sqlite_commit_with_record=_before_sqlite_commit_with_record,
        )

    def remove_workflow_package(
        self,
        package_id: str,
        package_version: str,
        *,
        actor_id: str,
        _before_sqlite_commit: Callable[[], None] | None = None,
        _before_sqlite_commit_with_record: (
            Callable[[WorkflowPackageRegistryRecord], None] | None
        ) = None,
    ) -> WorkflowPackageRegistryRecord:
        return remove_workflow_package(
            self._connection,
            package_id,
            package_version,
            actor_id=actor_id,
            _before_sqlite_commit=_before_sqlite_commit,
            _before_sqlite_commit_with_record=_before_sqlite_commit_with_record,
        )

    def export_workflow_package_archive(
        self,
        cas_store: ContentAddressedByteStore,
        package_id: str,
        package_version: str,
    ) -> bytes:
        return export_workflow_package_archive(
            self._connection,
            cas_store,
            package_id,
            package_version,
        )

    def append_workflow_package_command_audit_event(
        self,
        event: WorkflowPackageCommandAuditEventRecord,
    ) -> None:
        append_workflow_package_command_audit_event(self._connection, event)

    def load_workflow_package_command_audit_events(
        self,
    ) -> tuple[WorkflowPackageCommandAuditEventRecord, ...]:
        return load_workflow_package_command_audit_events(self._connection)

    def workflow_package_command_id_exists(self, command_id: str) -> bool:
        return workflow_package_command_id_exists(self._connection, command_id)

    def close(self) -> None:
        self._connection.close()


__all__ = (
    "ControlDecision",
    "SQLiteRuntimeStore",
    "StoreSchemaMetadata",
)


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    if type(value) is not int:
        raise ValueError("expected SQLite integer")
    return value
