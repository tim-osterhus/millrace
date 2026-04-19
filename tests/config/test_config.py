from __future__ import annotations

import importlib
from pathlib import Path

import pytest
from pydantic import ValidationError

from millrace_ai.config import (
    ApplyBoundary,
    CodexPermissionLevel,
    RuntimeConfig,
    StageConfig,
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
        "stages",
    }


def test_stage_config_uses_one_hour_default_timeout() -> None:
    assert StageConfig().timeout_seconds == 3600


def test_runtime_config_defaults_codex_permissions_to_maximum() -> None:
    assert RuntimeConfig().runners.codex.permission_default is CodexPermissionLevel.MAXIMUM


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
    assert apply_boundary_for_field("runtime.default_mode") is ApplyBoundary.RECOMPILE
    assert apply_boundary_for_field("stages.builder.model") is ApplyBoundary.RECOMPILE


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
    candidate_payload["watchers"]["enabled"] = True
    candidate_payload["stages"]["builder"] = {
        "runner": "codex",
        "model": "gpt-5",
        "timeout_seconds": 200,
    }
    candidate = RuntimeConfig.model_validate(candidate_payload)

    summary = summarize_config_changes(current, candidate)

    assert "runtime.default_mode" in summary.changed_keys
    assert "watchers.enabled" in summary.changed_keys
    assert "stages.builder.runner" in summary.changed_keys
    assert summary.requires_recompile is True
    assert summary.highest_boundary is ApplyBoundary.RECOMPILE
    assert tuple(sorted(summary.recompile_keys)) == (
        "runtime.default_mode",
        "stages.builder.model",
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
