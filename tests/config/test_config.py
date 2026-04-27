from __future__ import annotations

import importlib
from pathlib import Path

import pytest
from pydantic import ValidationError

from millrace_ai.config import (
    ApplyBoundary,
    CodexPermissionLevel,
    CodexReasoningEffort,
    PiEventLogPolicy,
    PiRunnerSection,
    RuntimeConfig,
    StageConfig,
    UsageGovernanceDegradedPolicy,
    UsageGovernanceSubscriptionProvider,
    apply_boundary_for_field,
    iter_config_field_paths,
    load_runtime_config,
    recompile_boundary_changes,
    summarize_config_changes,
)


def test_config_import_surface_moves_to_package_directory() -> None:
    config_module = importlib.import_module("millrace_ai.config")
    boundaries_module = importlib.import_module("millrace_ai.config.boundaries")
    loading_module = importlib.import_module("millrace_ai.config.loading")
    models_module = importlib.import_module("millrace_ai.config.models")

    assert Path(config_module.__file__).as_posix().endswith("/config/__init__.py")
    assert RuntimeConfig.__module__ == "millrace_ai.config.models"
    assert load_runtime_config is loading_module.load_runtime_config
    assert apply_boundary_for_field is boundaries_module.apply_boundary_for_field
    assert StageConfig is models_module.StageConfig


def test_runtime_config_schema_uses_draft_categories() -> None:
    config = RuntimeConfig()
    assert set(config.model_dump(mode="python")) == {
        "runtime",
        "runners",
        "recovery",
        "watchers",
        "usage_governance",
        "stages",
    }


def test_stage_config_uses_one_hour_default_timeout() -> None:
    assert StageConfig().timeout_seconds == 3600


def test_runtime_config_defaults_codex_permissions_to_maximum() -> None:
    assert RuntimeConfig().runners.codex.permission_default is CodexPermissionLevel.MAXIMUM


def test_runtime_config_defaults_canonical_mode_and_pi_determinism_flags() -> None:
    config = RuntimeConfig()

    assert config.runtime.default_mode == "default_codex"
    assert config.runners.pi.disable_context_files is True
    assert config.runners.pi.disable_skills is True
    assert config.runners.pi.event_log_policy is PiEventLogPolicy.FAILURE_FULL


def test_runtime_config_accepts_explicit_full_pi_event_log_policy() -> None:
    config = RuntimeConfig(runners={"pi": {"event_log_policy": "full"}})

    assert config.runners.pi.event_log_policy is PiEventLogPolicy.FULL


def test_pi_runner_section_rejects_reserved_transport_flags() -> None:
    with pytest.raises(ValidationError, match="reserved pi runner flags"):
        PiRunnerSection(args=("--mode", "rpc"))


def test_runtime_config_enables_watchers_and_seeded_idea_intake_by_default() -> None:
    config = RuntimeConfig()

    assert config.watchers.enabled is True
    assert config.watchers.watch_ideas_inbox is True


def test_usage_governance_is_inert_by_default() -> None:
    config = RuntimeConfig()

    assert config.usage_governance.enabled is False
    assert config.usage_governance.auto_resume is True
    assert config.usage_governance.runtime_token_rules.enabled is True
    assert config.usage_governance.subscription_quota_rules.enabled is False


def test_usage_governance_installs_documented_default_rules_when_enabled() -> None:
    config = RuntimeConfig(usage_governance={"enabled": True})

    runtime_rules = config.usage_governance.runtime_token_rules.rules
    assert [rule.rule_id for rule in runtime_rules] == [
        "rolling-5h-default",
        "calendar-week-default",
    ]
    assert runtime_rules[0].window.value == "rolling_5h"
    assert runtime_rules[0].metric.value == "total_tokens"
    assert runtime_rules[0].threshold == 750_000
    assert runtime_rules[1].window.value == "calendar_week"
    assert runtime_rules[1].threshold == 5_000_000


def test_subscription_quota_defaults_are_codex_specific_and_fail_open() -> None:
    config = RuntimeConfig(
        usage_governance={
            "enabled": True,
            "subscription_quota_rules": {"enabled": True},
        }
    )

    quota = config.usage_governance.subscription_quota_rules
    assert quota.provider is UsageGovernanceSubscriptionProvider.CODEX_CHATGPT_OAUTH
    assert quota.degraded_policy is UsageGovernanceDegradedPolicy.FAIL_OPEN
    assert quota.refresh_interval_seconds == 60
    assert [rule.rule_id for rule in quota.rules] == [
        "codex-five-hour-default",
        "codex-weekly-default",
    ]
    assert quota.rules[0].window.value == "five_hour"
    assert quota.rules[0].pause_at_percent_used == 95


def test_runtime_config_rejects_unknown_stage_name() -> None:
    with pytest.raises(ValidationError, match="unknown stage"):
        RuntimeConfig(stages={"unknown_stage": StageConfig()})


def test_codex_permission_by_stage_rejects_unknown_stage_name() -> None:
    with pytest.raises(ValidationError, match="unknown stage names in runners\\.codex\\.permission_by_stage"):
        RuntimeConfig(
            runners={
                "codex": {
                    "permission_by_stage": {
                        "unknown_stage": CodexPermissionLevel.BASIC,
                    }
                }
            }
        )


def test_learning_stage_config_and_codex_stage_permissions_are_supported() -> None:
    config = RuntimeConfig(
        runners={
            "codex": {
                "permission_by_stage": {
                    "professor": "elevated",
                }
            }
        },
        stages={
            "professor": {
                "model": "gpt-5.4",
                "model_reasoning_effort": "high",
            },
        },
    )

    assert config.stages["professor"].model == "gpt-5.4"
    assert config.stages["professor"].model_reasoning_effort is CodexReasoningEffort.HIGH
    assert config.runners.codex.permission_by_stage["professor"] is CodexPermissionLevel.ELEVATED


def test_load_runtime_config_precedence_cli_over_mailbox_over_file_over_defaults(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "millrace.toml"
    config_path.write_text(
        "\n".join(
            [
                "[runtime]",
                'default_mode = "file_mode"',
                "",
                "[watchers]",
                "enabled = true",
                "",
                "[recovery]",
                "max_fix_cycles = 5",
            ]
        ),
        encoding="utf-8",
    )

    config = load_runtime_config(
        config_path=config_path,
        mailbox_overrides={
            "runtime.default_mode": "mailbox_mode",
            "recovery.max_fix_cycles": 8,
        },
        cli_overrides={
            "runtime.default_mode": "cli_mode",
        },
    )

    assert config.runtime.default_mode == "cli_mode"
    assert config.recovery.max_fix_cycles == 8
    assert config.watchers.enabled is True
    assert config.runtime.run_style.value == "daemon"


def test_each_config_field_has_an_apply_boundary() -> None:
    config = RuntimeConfig(
        stages={
            "builder": StageConfig(
                runner="codex",
                model="gpt-5",
                model_reasoning_effort=CodexReasoningEffort.HIGH,
                timeout_seconds=180,
            )
        }
    )

    missing: list[str] = []
    for key in iter_config_field_paths(config):
        try:
            apply_boundary_for_field(key)
        except KeyError:
            missing.append(key)

    assert missing == []
    assert apply_boundary_for_field("runtime.idle_sleep_seconds") is ApplyBoundary.NEXT_TICK
    assert apply_boundary_for_field("watchers.enabled") is ApplyBoundary.NEXT_TICK
    assert apply_boundary_for_field("usage_governance.enabled") is ApplyBoundary.NEXT_TICK
    assert (
        apply_boundary_for_field("usage_governance.runtime_token_rules")
        is ApplyBoundary.NEXT_TICK
    )
    assert apply_boundary_for_field("runtime.default_mode") is ApplyBoundary.RECOMPILE
    assert apply_boundary_for_field("stages.builder.model") is ApplyBoundary.RECOMPILE
    assert apply_boundary_for_field("stages.builder.model_reasoning_effort") is ApplyBoundary.RECOMPILE


def test_apply_boundary_rejects_unknown_stage_name() -> None:
    with pytest.raises(KeyError, match="Unknown stage name"):
        apply_boundary_for_field("stages.unknown.model")


def test_load_runtime_config_rejects_invalid_dotted_override_key(tmp_path: Path) -> None:
    config_path = tmp_path / "millrace.toml"
    config_path.write_text("", encoding="utf-8")

    with pytest.raises(ValueError, match="Invalid dotted config key"):
        load_runtime_config(
            config_path=config_path,
            cli_overrides={"runtime..idle_sleep_seconds": 1},
        )


def test_load_runtime_config_preserves_nested_mapping_when_dotted_overrides_apply(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "millrace.toml"
    config_path.write_text(
        "\n".join(
            [
                "[watchers]",
                "enabled = true",
                "watch_ideas_inbox = true",
            ]
        ),
        encoding="utf-8",
    )

    config = load_runtime_config(
        config_path=config_path,
        mailbox_overrides={"watchers.enabled": False},
    )

    assert config.watchers.enabled is False
    assert config.watchers.watch_ideas_inbox is True


def test_recompile_boundary_helpers_surface_recompile_keys() -> None:
    current = RuntimeConfig()
    candidate_payload = current.model_dump(mode="python")
    candidate_payload["runtime"]["default_mode"] = "role_augmented"
    candidate_payload["watchers"]["enabled"] = False
    candidate_payload["stages"]["builder"] = {
        "runner": "codex",
        "model": "gpt-5",
        "model_reasoning_effort": "high",
        "timeout_seconds": 200,
    }
    candidate = RuntimeConfig.model_validate(candidate_payload)

    summary = summarize_config_changes(current, candidate)

    assert "runtime.default_mode" in summary.changed_keys
    assert "watchers.enabled" in summary.changed_keys
    assert "stages.builder.runner" in summary.changed_keys
    assert "stages.builder.model_reasoning_effort" in summary.changed_keys
    assert summary.requires_recompile is True
    assert summary.highest_boundary is ApplyBoundary.RECOMPILE
    assert tuple(sorted(summary.recompile_keys)) == (
        "runtime.default_mode",
        "stages.builder.model",
        "stages.builder.model_reasoning_effort",
        "stages.builder.runner",
        "stages.builder.timeout_seconds",
    )
    assert recompile_boundary_changes(current, candidate) == summary.recompile_keys


def test_load_runtime_config_rejects_removed_compile_and_queue_sections(tmp_path: Path) -> None:
    config_path = tmp_path / "millrace.toml"
    config_path.write_text(
        "\n".join(
            [
                "[compile]",
                'default_execution_loop = "execution.standard"',
                "",
                "[queue]",
                "poll_interval_seconds = 2",
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValidationError):
        load_runtime_config(config_path=config_path)
