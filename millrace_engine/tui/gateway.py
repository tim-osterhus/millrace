"""Fresh-per-call TUI gateway over the control plane."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..control import EngineControl
from ..control_reports import read_run_provenance
from .gateway_views import (
    action_result_view,
    commit_action_result_view,
    compounding_governance_view,
    config_overview_view,
    interview_action_result_view,
    publish_overview_view,
    queue_overview_view,
    research_overview_view,
    run_detail_view,
    runs_overview_view,
    runtime_overview_view,
    sync_action_result_view,
)
from .gateway_support import (
    event_log_view,
    execute_gateway_operation,
    input_failure,
    normalized_optional_text,
    resolve_config_path,
    utcnow,
)
from .models import ActionResultView, FailureCategory, GatewayFailure, GatewayResult, RefreshPayload

RESEARCH_ACTIVITY_LIMIT = 5


def _utcnow():
    return utcnow()


class RuntimeGateway:
    """Fresh-per-call gateway for shaping control-plane data into UI models."""

    def __init__(self, config_path: Path | str) -> None:
        self.config_path = resolve_config_path(config_path)

    def _new_control(self) -> EngineControl:
        return EngineControl(self.config_path)

    def load_workspace_snapshot(self, *, log_limit: int = 50) -> GatewayResult[RefreshPayload]:
        if log_limit < 0:
            return input_failure("refresh.workspace", "log_limit must be non-negative")

        def callback(control: EngineControl) -> RefreshPayload:
            refreshed_at = _utcnow()
            status = control.status(detail=False)
            config = control.config_show()
            queue = control.queue_inspect()
            research = control.research_report()
            compounding = control.compounding_governance_summary()
            interview = control.interview_list()
            research_activity = control.research_history(RESEARCH_ACTIVITY_LIMIT)
            events = control.logs(log_limit)
            return RefreshPayload(
                refreshed_at=refreshed_at,
                runtime=runtime_overview_view(status),
                config=config_overview_view(config),
                queue=queue_overview_view(queue),
                research=research_overview_view(
                    research,
                    recent_activity=research_activity,
                    interview_questions=interview.questions,
                ),
                compounding=compounding_governance_view(compounding),
                events=event_log_view(events, refreshed_at=refreshed_at),
                runs=runs_overview_view(control, observed_at=refreshed_at, read_provenance=read_run_provenance),
            )

        return execute_gateway_operation(self._new_control, "refresh.workspace", callback)

    def load_publish_status(
        self,
        *,
        commit_message: str | None = None,
        push: bool = False,
        staging_repo_dir: Path | str | None = None,
    ) -> GatewayResult[RefreshPayload]:
        normalized_commit_message = normalized_optional_text(commit_message)

        def callback(control: EngineControl) -> RefreshPayload:
            report = control.publish_preflight(
                staging_repo_dir=staging_repo_dir,
                commit_message=normalized_commit_message,
                push=push,
            )
            return RefreshPayload(
                refreshed_at=_utcnow(),
                publish=publish_overview_view(report),
            )

        return execute_gateway_operation(self._new_control, "refresh.publish", callback)

    def load_run_detail(self, run_id: str) -> GatewayResult[RefreshPayload]:
        normalized_run_id = " ".join(run_id.strip().split())
        if not normalized_run_id:
            return input_failure("refresh.run_detail", "run_id is required")

        def callback(control: EngineControl) -> RefreshPayload:
            report = control.run_provenance(normalized_run_id)
            return RefreshPayload(
                refreshed_at=_utcnow(),
                run_detail=run_detail_view(report),
            )

        return execute_gateway_operation(self._new_control, "refresh.run_detail", callback)

    def add_task(self, title: str, *, body: str | None = None, spec_id: str | None = None) -> GatewayResult[ActionResultView]:
        normalized_title = " ".join(title.strip().split())
        if not normalized_title:
            return input_failure("action.add_task", "task title is required")
        normalized_body = normalized_optional_text(body)
        normalized_spec_id = normalized_optional_text(spec_id)
        return execute_gateway_operation(
            self._new_control,
            "action.add_task",
            lambda control: action_result_view(
                "add_task",
                control.add_task(normalized_title, body=normalized_body, spec_id=normalized_spec_id),
            ),
        )

    def add_idea(self, source_file: Path | str) -> GatewayResult[ActionResultView]:
        source_path = Path(source_file).expanduser()
        return execute_gateway_operation(
            self._new_control,
            "action.add_idea",
            lambda control: action_result_view("add_idea", control.add_idea(source_path)),
        )

    def answer_interview(self, question_id: str, *, text: str) -> GatewayResult[ActionResultView]:
        return execute_gateway_operation(
            self._new_control,
            "action.interview_answer",
            lambda control: interview_action_result_view(
                "answer",
                control.interview_answer(question_id, text=text),
            ),
        )

    def accept_interview(self, question_id: str) -> GatewayResult[ActionResultView]:
        return execute_gateway_operation(
            self._new_control,
            "action.interview_accept",
            lambda control: interview_action_result_view("accept", control.interview_accept(question_id)),
        )

    def skip_interview(self, question_id: str, *, reason: str | None = None) -> GatewayResult[ActionResultView]:
        return execute_gateway_operation(
            self._new_control,
            "action.interview_skip",
            lambda control: interview_action_result_view(
                "skip",
                control.interview_skip(question_id, reason=reason),
            ),
        )

    def pause_runtime(self) -> GatewayResult[ActionResultView]:
        return execute_gateway_operation(
            self._new_control,
            "action.pause",
            lambda control: action_result_view("pause", control.pause()),
        )

    def resume_runtime(self) -> GatewayResult[ActionResultView]:
        return execute_gateway_operation(
            self._new_control,
            "action.resume",
            lambda control: action_result_view("resume", control.resume()),
        )

    def stop_runtime(self) -> GatewayResult[ActionResultView]:
        return execute_gateway_operation(
            self._new_control,
            "action.stop",
            lambda control: action_result_view("stop", control.stop()),
        )

    def reload_config(self) -> GatewayResult[ActionResultView]:
        return execute_gateway_operation(
            self._new_control,
            "action.reload_config",
            lambda control: action_result_view("reload_config", control.config_reload()),
        )

    def set_config(self, key: str, value: str) -> GatewayResult[ActionResultView]:
        normalized_key = " ".join(key.strip().split())
        if not normalized_key:
            return input_failure("action.set_config", "config key is required")
        return execute_gateway_operation(
            self._new_control,
            "action.set_config",
            lambda control: action_result_view("set_config", control.config_set(normalized_key, value)),
        )

    def reorder_queue(self, task_ids: list[str] | tuple[str, ...]) -> GatewayResult[ActionResultView]:
        normalized_ids = tuple(task_id.strip() for task_id in task_ids if task_id.strip())
        if not normalized_ids:
            return input_failure("action.reorder_queue", "at least one task id is required")
        return execute_gateway_operation(
            self._new_control,
            "action.reorder_queue",
            lambda control: action_result_view("reorder_queue", control.queue_reorder(normalized_ids)),
        )

    def publish_sync(self, *, staging_repo_dir: Path | str | None = None) -> GatewayResult[ActionResultView]:
        return execute_gateway_operation(
            self._new_control,
            "action.publish_sync",
            lambda control: sync_action_result_view(control.publish_sync(staging_repo_dir=staging_repo_dir)),
        )

    def publish_commit(
        self,
        *,
        commit_message: str | None = None,
        push: bool = False,
        staging_repo_dir: Path | str | None = None,
    ) -> GatewayResult[ActionResultView]:
        normalized_commit_message = normalized_optional_text(commit_message)
        return execute_gateway_operation(
            self._new_control,
            "action.publish_commit",
            lambda control: commit_action_result_view(
                control.publish_commit(
                    staging_repo_dir=staging_repo_dir,
                    commit_message=normalized_commit_message,
                    push=push,
                )
            ),
        )

    @staticmethod
    def _commit_result(report: object) -> ActionResultView:
        return commit_action_result_view(report)


__all__ = ["RuntimeGateway"]
