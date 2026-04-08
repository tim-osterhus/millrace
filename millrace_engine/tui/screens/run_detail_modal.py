"""Focused run-provenance drilldown for one selected run."""

from __future__ import annotations

from pathlib import Path

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Static
from textual.worker import Worker, WorkerState

from ..formatting import (
    format_timestamp,
    run_integration_summary_lines,
    run_policy_summary_lines,
    run_transition_summary_lines,
)
from ..gateway import RuntimeGateway
from ..models import FailureCategory, GatewayFailure, GatewayResult, RunDetailView
from ..workers import gateway_failure_from_exception

RUN_DETAIL_WORKER_NAME = "run-detail.load"
RUN_DETAIL_WORKER_GROUP = "run-detail"


class RunDetailModal(ModalScreen[None]):
    """Load and render concise run-provenance detail for one run id."""

    BINDINGS = [
        ("escape", "close", "Close"),
    ]

    def __init__(self, *, config_path: Path, run_id: str) -> None:
        super().__init__()
        self._config_path = config_path
        self._run_id = " ".join(run_id.split())
        self._detail: RunDetailView | None = None
        self._failure: GatewayFailure | None = None
        self._loading = True

    def compose(self) -> ComposeResult:
        with Vertical(classes="modal-dialog"):
            yield Static("Run Detail", classes="modal-title")
            yield Static("", id="run-detail-body", classes="modal-copy")
            with Horizontal(classes="modal-actions"):
                yield Button("Close", id="run-detail-close", variant="primary")

    def on_mount(self) -> None:
        self.query_one("#run-detail-close", Button).focus()
        self._render_body()
        if not self._run_id:
            self._set_failure(
                GatewayFailure(
                    operation="refresh.run_detail",
                    category=FailureCategory.INPUT,
                    message="run_id is required",
                    exception_type="ValueError",
                    retryable=False,
                )
            )
            return
        self.run_worker(
            lambda: RuntimeGateway(self._config_path).load_run_detail(self._run_id),
            name=RUN_DETAIL_WORKER_NAME,
            group=RUN_DETAIL_WORKER_GROUP,
            exclusive=True,
            exit_on_error=False,
            thread=True,
        )

    def action_close(self) -> None:
        self.dismiss(None)

    @on(Button.Pressed, "#run-detail-close")
    def _handle_close_button(self, event: Button.Pressed) -> None:
        event.stop()
        self.action_close()

    @on(Worker.StateChanged)
    def _handle_worker_state_changed(self, message: Worker.StateChanged) -> None:
        if message.worker.name != RUN_DETAIL_WORKER_NAME:
            return
        if message.state == WorkerState.SUCCESS:
            result = message.worker.result
            if isinstance(result, GatewayResult):
                if result.ok and result.value is not None and result.value.run_detail is not None:
                    self._detail = result.value.run_detail
                    self._failure = None
                    self._loading = False
                    self._render_body()
                    return
                if result.failure is not None:
                    self._set_failure(result.failure)
                    return
            self._set_failure(
                GatewayFailure(
                    operation="refresh.run_detail",
                    category=FailureCategory.UNEXPECTED,
                    message="run detail payload was missing",
                    exception_type="RuntimeError",
                )
            )
            return
        if message.state == WorkerState.ERROR and isinstance(message.worker.error, Exception):
            self._set_failure(gateway_failure_from_exception("refresh.run_detail", message.worker.error))

    def _set_failure(self, failure: GatewayFailure) -> None:
        self._detail = None
        self._failure = failure
        self._loading = False
        self._render_body()

    def _render_body(self) -> None:
        self.query_one("#run-detail-body", Static).update(self._render_text())

    def _render_text(self) -> str:
        if self._loading:
            return f"Loading run provenance for {self._run_id or 'unknown'}."
        if self._failure is not None:
            return "\n".join(
                [
                    f"RUN DETAIL unavailable: {self._failure.message}",
                    "",
                    f"Requested run id: {self._run_id or 'none'}",
                    "The shell stays alive so you can return to the recent-runs list or retry from another panel.",
                ]
            )
        if self._detail is None:
            return "Run detail is unavailable."

        detail = self._detail
        lines = [
            "RUN     "
            f"{detail.run_id}"
            f" | compiled {format_timestamp(detail.compiled_at)}"
            f" | stages {detail.stage_count if detail.stage_count is not None else 'unknown'}",
            f"PLAN    {detail.frozen_plan_id or 'none'}",
            f"HASH    {detail.frozen_plan_hash or 'none'}",
        ]
        selection = detail.selection
        decision = detail.selection_decision
        if selection is None:
            lines.append("SELECT  unavailable")
        else:
            lines.append(
                "SELECT  "
                f"{selection.selection_ref}"
                f" | scope {selection.scope}"
                f" | research {selection.research_participation}"
            )
            if decision is not None:
                lines.append(
                    "ROUTE   "
                    f"size {decision.selected_size}"
                    f" | decision {decision.route_decision}"
                    f" | profile {decision.large_profile_decision}"
                )
                lines.append(f"WHY     {decision.route_reason}")
            if selection.stage_labels:
                lines.append(f"STAGES  {', '.join(selection.stage_labels)}")

        if detail.current_preview_error:
            lines.append(f"PREVIEW {detail.current_preview_error}")
        elif detail.current_preview is not None:
            preview = detail.current_preview
            lines.append(
                "PREVIEW "
                f"{preview.selection_ref}"
                f" | scope {preview.scope}"
                f" | plan {preview.frozen_plan_id}"
            )

        lines.append(
            "HISTORY "
            f"transitions {len(detail.transitions)}"
            f" | routes {', '.join(detail.routing_modes) if detail.routing_modes else 'none'}"
        )
        if detail.compounding is not None:
            compounding = detail.compounding
            lines.append(
                "COMPOUND "
                f"created {compounding.created_count}"
                f" | procedures {compounding.procedure_selection_count}/{compounding.injected_procedure_count}"
                f" | facts {compounding.context_fact_selection_count}/{compounding.injected_context_fact_count}"
            )
            for created in compounding.created_procedures[:3]:
                lines.append(f"CREATED  {created.procedure_id} [{created.scope}] stage {created.source_stage}")
            for selection_summary in compounding.procedure_selections[:3]:
                injected = ", ".join(selection_summary.injected_ids) if selection_summary.injected_ids else "none"
                lines.append(
                    f"PROC     {selection_summary.stage} ({selection_summary.node_id}) "
                    f"considered {selection_summary.considered_count} | injected {injected}"
                )
            for selection_summary in compounding.context_fact_selections[:3]:
                injected = ", ".join(selection_summary.injected_ids) if selection_summary.injected_ids else "none"
                lines.append(
                    f"FACTS    {selection_summary.stage} ({selection_summary.node_id}) "
                    f"considered {selection_summary.considered_count} | injected {injected}"
                )
        lines.extend(run_transition_summary_lines(detail.transitions))
        lines.extend(
            run_policy_summary_lines(
                detail.latest_policy_evidence,
                hook_count=detail.policy_hook_count,
                latest_decision=detail.latest_policy_decision,
            )
        )
        lines.extend(run_integration_summary_lines(detail.integration_policy))
        if detail.snapshot_path:
            lines.append(f"SNAPSHOT {detail.snapshot_path}")
        if detail.transition_history_path:
            lines.append(f"TRACE   {detail.transition_history_path}")
        return "\n".join(lines)


__all__ = ["RunDetailModal"]
