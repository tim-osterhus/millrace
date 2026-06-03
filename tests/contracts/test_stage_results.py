from __future__ import annotations

import json
from datetime import datetime, timezone

from millrace_ai.contracts import (
    ExecutionStageName,
    ExecutionTerminalResult,
    Plane,
    ResultClass,
    StageResultEnvelope,
    WorkItemKind,
)

NOW = datetime(2026, 6, 1, tzinfo=timezone.utc)


def test_stage_result_serializes_operator_visible_terminal_fields_as_values() -> None:
    result = StageResultEnvelope(
        run_id="run-characterization",
        plane="execution",
        stage="builder",
        work_item_kind="task",
        work_item_id="task-001",
        terminal_result="BUILDER_COMPLETE",
        result_class="success",
        summary_status_marker="### BUILDER_COMPLETE",
        success=True,
        started_at=NOW,
        completed_at=NOW,
    )

    payload = json.loads(result.model_dump_json())

    assert result.plane is Plane.EXECUTION
    assert result.stage is ExecutionStageName.BUILDER
    assert result.node_id == "builder"
    assert result.stage_kind_id == "builder"
    assert result.work_item_family_id == "task"
    assert result.work_item_kind is WorkItemKind.TASK
    assert result.terminal_result == ExecutionTerminalResult.BUILDER_COMPLETE
    assert result.terminal_result.value == "BUILDER_COMPLETE"
    assert result.result_class is ResultClass.SUCCESS
    assert payload["plane"] == "execution"
    assert payload["stage"] == "builder"
    assert payload["terminal_result"] == "BUILDER_COMPLETE"
    assert payload["result_class"] == "success"
    assert payload["summary_status_marker"] == "### BUILDER_COMPLETE"
