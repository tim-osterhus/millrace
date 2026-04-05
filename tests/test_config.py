from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json

import pytest
from pydantic import ValidationError

from millrace_engine.config import (
    ConfigApplyBoundary,
    ComplexityBand,
    StageConfig,
    WatchRoot,
    build_runtime_paths,
    default_stage_configs,
    diff_config_fields,
    load_engine_config,
)
from millrace_engine.contracts import RunnerKind, StageContext, StageType, load_objective_contract
from millrace_engine.paths import format_historylog_entry_name
from tests.support import runtime_workspace


def test_native_config_loads_from_runtime_workspace(tmp_path: Path) -> None:
    workspace_root, config_path = runtime_workspace(tmp_path)
    loaded = load_engine_config(config_path)

    assert loaded.source.kind == "native_toml"
    assert loaded.config.paths.workspace == workspace_root.resolve()
    assert loaded.config.paths.agents_dir == (workspace_root / "agents").resolve()
    assert loaded.config.stages[StageType.BUILDER].prompt_file == (workspace_root / "agents/_start.md").resolve()


def test_default_stage_configs_use_real_shipped_model_ids() -> None:
    stages = default_stage_configs()

    assert stages[StageType.BUILDER].runner.value == "codex"
    assert stages[StageType.BUILDER].model == "gpt-5.3-codex"
    assert stages[StageType.GOAL_INTAKE].model == "gpt-5.3-codex"
    assert stages[StageType.SPEC_SYNTHESIS].model == "gpt-5.2"
    assert stages[StageType.CLARIFY].model == "gpt-5.2"


def test_execution_stage_defaults_use_one_hour_timeout() -> None:
    stages = default_stage_configs()

    assert StageConfig().timeout_seconds == 3600
    assert stages[StageType.BUILDER].timeout_seconds == 3600
    assert stages[StageType.QA].timeout_seconds == 3600
    assert stages[StageType.INTEGRATION].timeout_seconds == 3600


def test_stage_context_default_timeout_matches_stage_defaults(tmp_path: Path) -> None:
    context = StageContext(
        stage=StageType.BUILDER,
        runner=RunnerKind.CODEX,
        model="gpt-5.3-codex",
        working_dir=tmp_path,
    )

    assert context.timeout_seconds == 3600


def test_runtime_paths_are_resolved_under_runtime_workspace(tmp_path: Path) -> None:
    workspace_root, config_path = runtime_workspace(tmp_path)
    loaded = load_engine_config(config_path)
    paths = build_runtime_paths(loaded.config)

    assert paths.root == workspace_root.resolve()
    assert paths.agents_dir == (workspace_root / "agents").resolve()
    assert paths.status_file == (workspace_root / "agents/status.md").resolve()
    assert paths.size_status_file == (workspace_root / "agents/size_status.md").resolve()
    assert paths.ideas_staging_dir == (workspace_root / "agents/ideas/staging").resolve()
    assert paths.ideas_specs_dir == (workspace_root / "agents/ideas/specs").resolve()
    assert paths.ideas_specs_reviewed_dir == (workspace_root / "agents/ideas/specs_reviewed").resolve()
    assert paths.objective_profile_sync_state_file == (
        workspace_root / "agents/objective/profile_sync_state.json"
    ).resolve()
    assert paths.audit_completion_manifest_file == (
        workspace_root / "agents/audit/completion_manifest.json"
    ).resolve()
    assert paths.completion_manifest_plan_file == (
        workspace_root / "agents/reports/completion_manifest_plan.md"
    ).resolve()
    assert paths.staging_manifest_file == (workspace_root / "agents/staging_manifest.yml").resolve()
    assert paths.goalspec_goal_intake_records_dir == (
        workspace_root / "agents/.research_runtime/goalspec/goal_intake"
    ).resolve()
    assert paths.goalspec_completion_manifest_records_dir == (
        workspace_root / "agents/.research_runtime/goalspec/completion_manifest"
    ).resolve()
    assert paths.goalspec_spec_synthesis_records_dir == (
        workspace_root / "agents/.research_runtime/goalspec/spec_synthesis"
    ).resolve()
    assert paths.goalspec_spec_interview_records_dir == (
        workspace_root / "agents/.research_runtime/goalspec/spec_interview"
    ).resolve()
    assert paths.goalspec_spec_review_records_dir == (
        workspace_root / "agents/.research_runtime/goalspec/spec_review"
    ).resolve()
    assert paths.goalspec_lineage_dir == (
        workspace_root / "agents/.research_runtime/goalspec/lineage"
    ).resolve()
    assert paths.specs_index_file == (workspace_root / "agents/specs/index.json").resolve()
    assert paths.specs_stable_golden_dir == (
        workspace_root / "agents/specs/stable/golden"
    ).resolve()
    assert paths.specs_stable_phase_dir == (
        workspace_root / "agents/specs/stable/phase"
    ).resolve()
    assert paths.specs_questions_dir == (workspace_root / "agents/specs/questions").resolve()
    assert paths.specs_decisions_dir == (workspace_root / "agents/specs/decisions").resolve()
    assert paths.staging_repo_dir == (workspace_root / "staging").resolve()
    assert paths.historylog_dir == (workspace_root / "agents/historylog").resolve()
    assert paths.research_state_file == (workspace_root / "agents/research_state.json").resolve()
    assert paths.runs_dir == (workspace_root / "agents/runs").resolve()
    assert paths.diagnostics_dir == (workspace_root / "agents/diagnostics").resolve()
    assert paths.commands_incoming_dir == (workspace_root / "agents/.runtime/commands/incoming").resolve()
    assert paths.research_recovery_latch_file == (
        workspace_root / "agents/.runtime/research_recovery_latch.json"
    ).resolve()
    assert paths.progress_watchdog_state_file == (
        workspace_root / "agents/.research_runtime/progress_watchdog_state.json"
    ).resolve()
    assert paths.progress_watchdog_report_file == (
        workspace_root / "agents/.tmp/progress_watchdog_report.json"
    ).resolve()
    assert paths.incident_recurrence_ledger_file == (
        workspace_root / "agents/.research_runtime/incidents/recurrence_ledger.json"
    ).resolve()


def test_historylog_entry_names_use_canonical_utc_format() -> None:
    filename = format_historylog_entry_name(
        datetime(2026, 3, 16, 21, 5, 33, tzinfo=timezone.utc),
        stage="QA",
        task="123",
    )

    assert filename == "2026-03-16T21-05-33Z__stage-qa__task-123.md"


def test_historylog_entry_names_truncate_long_labels_with_hash_suffix() -> None:
    filename = format_historylog_entry_name(
        datetime(2026, 3, 16, 21, 5, 33, tzinfo=timezone.utc),
        stage="stage-" + ("alpha-" * 20),
        task="task-" + ("very-long-label-" * 20),
    )

    assert filename.startswith("2026-03-16T21-05-33Z__stage-stage-alpha-alpha")
    assert "__task-task-very-long-label-very-long-label-" in filename
    assert filename.endswith(".md")
    assert len(filename) < 200


def test_objective_contract_loader_accepts_valid_typed_json() -> None:
    contract = load_objective_contract(
        json.dumps(
            {
                "schema_version": "1.0",
                "objective_id": "OBJ-TYPED-001",
                "objective_root": ".",
                "completion": {
                    "authoritative_decision_file": "agents/reports/completion_decision.json",
                    "fallback_decision_file": "agents/reports/audit_gate_decision.json",
                    "require_task_store_cards_zero": True,
                    "require_open_gaps_zero": True,
                },
                "objective_profile": {"profile_id": "typed-profile"},
            }
        )
    )

    assert contract.objective_id == "OBJ-TYPED-001"
    assert contract.objective_profile["profile_id"] == "typed-profile"


def test_objective_contract_loader_rejects_malformed_typed_json() -> None:
    with pytest.raises(ValidationError):
        load_objective_contract(
            json.dumps(
                {
                    "schema_version": "1.0",
                    "objective_id": "OBJ-BROKEN-001",
                    "objective_root": ".",
                    "completion": {},
                }
            )
        )


def test_invalid_native_config_fails_validation(tmp_path: Path) -> None:
    config_path = tmp_path / "millrace.toml"
    config_path.write_text(
        "\n".join(
            [
                "[engine]",
                'mode = "once"',
                "poll_interval_seconds = 0",
                "",
                "[unexpected]",
                'value = "boom"',
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValidationError):
        load_engine_config(config_path)


def test_native_config_loads_spec_interview_policy(tmp_path: Path) -> None:
    config_path = tmp_path / "millrace.toml"
    config_path.write_text(
        "\n".join(
            [
                "[engine]",
                'mode = "once"',
                "",
                "[paths]",
                'workspace = "."',
                'agents_dir = "agents"',
                "",
                "[research]",
                'mode = "goalspec"',
                'interview_policy = "always"',
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    loaded = load_engine_config(config_path)

    assert loaded.config.research.interview_policy.value == "always"


def test_native_config_loads_typed_complexity_routing_policy(tmp_path: Path) -> None:
    config_path = tmp_path / "millrace.toml"
    config_path.write_text(
        "\n".join(
            [
                "[engine]",
                'mode = "once"',
                "",
                "[paths]",
                'workspace = "."',
                'agents_dir = "agents"',
                "",
                "[policies.complexity]",
                "enabled = true",
                'default_band = "involved"',
                "",
                "[policies.complexity.profiles.moderate]",
                'kind = "model_profile"',
                'id = "model.workspace.moderate"',
                'version = "1.0.0"',
                "",
                "[policies.complexity.profiles.involved]",
                'kind = "model_profile"',
                'id = "model.workspace.involved"',
                'version = "1.0.0"',
                "",
                "[policies.complexity.profiles.complex]",
                'kind = "model_profile"',
                'id = "model.workspace.complex"',
                'version = "1.0.0"',
                "",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    loaded = load_engine_config(config_path)

    assert loaded.config.policies.complexity.enabled is True
    assert loaded.config.policies.complexity.default_band is ComplexityBand.INVOLVED
    assert loaded.config.policies.complexity.profiles.involved.id == "model.workspace.involved"
    assert loaded.config.boundaries.classify_field("policies.complexity") is ConfigApplyBoundary.STAGE_BOUNDARY


def test_native_config_loads_typed_watcher_settings(tmp_path: Path) -> None:
    config_path = tmp_path / "millrace.toml"
    config_path.write_text(
        "\n".join(
            [
                "[engine]",
                'mode = "once"',
                'idle_mode = "watch"',
                "poll_interval_seconds = 30",
                "inter_task_delay_seconds = 0",
                "",
                "[paths]",
                'workspace = "."',
                'agents_dir = "agents"',
                "",
                "[execution]",
                'integration_mode = "never"',
                "quickfix_max_attempts = 2",
                "run_update_on_empty = false",
                "",
                "[research]",
                'mode = "stub"',
                "",
                "[watchers]",
                "debounce_seconds = 1.25",
                'roots = ["config_file", "commands_incoming"]',
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    loaded = load_engine_config(config_path)

    assert loaded.config.watchers.debounce_seconds == pytest.approx(1.25)
    assert loaded.config.watchers.roots == (
        WatchRoot.CONFIG_FILE,
        WatchRoot.COMMANDS_INCOMING,
    )


def test_native_config_loads_typed_sizing_settings(tmp_path: Path) -> None:
    config_path = tmp_path / "millrace.toml"
    config_path.write_text(
        "\n".join(
            [
                "[engine]",
                'mode = "once"',
                "",
                "[paths]",
                'workspace = "."',
                'agents_dir = "agents"',
                "",
                "[sizing]",
                'mode = "hybrid"',
                "",
                "[sizing.repo]",
                "file_count_threshold = 12",
                "nonempty_line_count_threshold = 345",
                "",
                "[sizing.task]",
                "file_count_threshold = 3",
                "nonempty_line_count_threshold = 144",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    loaded = load_engine_config(config_path)

    assert loaded.config.sizing.mode == "hybrid"
    assert loaded.config.sizing.repo.file_count_threshold == 12
    assert loaded.config.sizing.repo.nonempty_line_count_threshold == 345
    assert loaded.config.sizing.task.file_count_threshold == 3
    assert loaded.config.sizing.task.nonempty_line_count_threshold == 144


def test_native_config_accepts_legacy_task_loc_threshold_key(tmp_path: Path) -> None:
    config_path = tmp_path / "millrace.toml"
    config_path.write_text(
        "\n".join(
            [
                "[engine]",
                'mode = "once"',
                "",
                "[paths]",
                'workspace = "."',
                'agents_dir = "agents"',
                "",
                "[sizing.task]",
                "body_nonempty_line_count_threshold = 77",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    loaded = load_engine_config(config_path)

    assert loaded.config.sizing.task.nonempty_line_count_threshold == 77


def test_config_boundaries_classify_changed_fields_by_runtime_application_point(tmp_path: Path) -> None:
    _, config_path = runtime_workspace(tmp_path)
    loaded = load_engine_config(config_path)

    stage_config = loaded.config.model_copy(deep=True)
    stage_config.execution.quickfix_max_attempts = 5
    stage_fields = diff_config_fields(loaded.config, stage_config)
    assert stage_fields == ("execution.quickfix_max_attempts",)
    assert loaded.config.boundaries.classify_fields(stage_fields) is ConfigApplyBoundary.STAGE_BOUNDARY

    cycle_config = loaded.config.model_copy(deep=True)
    cycle_config.engine.idle_mode = "poll"
    cycle_fields = diff_config_fields(loaded.config, cycle_config)
    assert cycle_fields == ("engine.idle_mode",)
    assert loaded.config.boundaries.classify_fields(cycle_fields) is ConfigApplyBoundary.CYCLE_BOUNDARY

    sizing_config = loaded.config.model_copy(deep=True)
    sizing_config.sizing.mode = "repo" if loaded.config.sizing.mode != "repo" else "task"
    sizing_fields = diff_config_fields(loaded.config, sizing_config)
    assert sizing_fields == ("sizing.mode",)
    assert loaded.config.boundaries.classify_fields(sizing_fields) is ConfigApplyBoundary.CYCLE_BOUNDARY

    watcher_config = loaded.config.model_copy(deep=True)
    watcher_config.watchers.roots = (WatchRoot.CONFIG_FILE,)
    watcher_fields = diff_config_fields(loaded.config, watcher_config)
    assert watcher_fields == ("watchers.roots",)
    assert loaded.config.boundaries.classify_fields(watcher_fields) is ConfigApplyBoundary.CYCLE_BOUNDARY

    live_config = loaded.config.model_copy(deep=True)
    live_config.engine.poll_interval_seconds = loaded.config.engine.poll_interval_seconds + 1
    live_fields = diff_config_fields(loaded.config, live_config)
    assert live_fields == ("engine.poll_interval_seconds",)
    assert loaded.config.boundaries.classify_fields(live_fields) is ConfigApplyBoundary.LIVE_IMMEDIATE

    startup_config = loaded.config.model_copy(deep=True)
    startup_config.paths.agents_dir = Path("other-agents")
    startup_fields = diff_config_fields(loaded.config, startup_config)
    assert startup_fields == ("paths.agents_dir",)
    assert loaded.config.boundaries.classify_fields(startup_fields) is ConfigApplyBoundary.STARTUP_ONLY
