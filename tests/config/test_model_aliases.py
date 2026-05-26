from __future__ import annotations

from millrace_ai.config import RuntimeConfig
from millrace_ai.config.boundaries import ApplyBoundary, apply_boundary_for_field, iter_config_field_paths


def test_runtime_config_defaults_model_aliases_and_assignment_policy() -> None:
    config = RuntimeConfig()

    assert config.model_aliases["fast"].model == "gpt-5.4-mini"
    assert config.model_aliases["fast"].thinking_level == "high"
    assert config.model_aliases["standard"].model == "gpt-5.5"
    assert config.model_aliases["standard"].thinking_level == "medium"
    assert config.model_aliases["deep"].model == "gpt-5.5"
    assert config.model_aliases["deep"].thinking_level == "xhigh"
    assert config.model_assignment.enabled is True
    assert config.model_assignment.default_alias == "standard"
    assert config.model_assignment.invalid_assignment_policy == "warn_fallback"


def test_runtime_config_accepts_loop_and_stage_alias_assignments() -> None:
    config = RuntimeConfig(
        model_assignment={
            "by_loop": {"planning.blueprint": "deep"},
            "by_stage": {"contractor_blueprint": "fast"},
        }
    )

    assert config.model_assignment.by_loop["planning.blueprint"] == "deep"
    assert config.model_assignment.by_stage["contractor_blueprint"] == "fast"


def test_runtime_config_accepts_invalid_alias_payload_for_compile_warning() -> None:
    config = RuntimeConfig(
        model_aliases={
            "broken": {"model": "", "thinking_level": "  "},
        },
        model_assignment={"default_alias": "broken"},
    )

    assert config.model_aliases["broken"].model == ""
    assert config.model_aliases["broken"].thinking_level == "  "


def test_model_alias_config_paths_are_recompile_boundaries() -> None:
    config = RuntimeConfig(
        model_aliases={
            "audit": {"model": "gpt-5.5", "thinking_level": "high"},
        },
        model_assignment={
            "by_loop": {"planning.blueprint": "deep"},
            "by_stage": {"contractor_blueprint": "fast"},
        },
    )

    paths = iter_config_field_paths(config)

    assert "model_aliases.audit.model" in paths
    assert "model_aliases.audit.thinking_level" in paths
    assert "model_assignment.enabled" in paths
    assert "model_assignment.default_alias" in paths
    assert "model_assignment.invalid_assignment_policy" in paths
    assert "model_assignment.by_loop.planning.blueprint" in paths
    assert "model_assignment.by_stage.contractor_blueprint" in paths
    assert apply_boundary_for_field("model_aliases.audit.model") is ApplyBoundary.RECOMPILE
    assert apply_boundary_for_field("model_assignment.by_loop.planning.blueprint") is ApplyBoundary.RECOMPILE


def test_dotted_model_alias_ids_are_still_recompile_boundaries() -> None:
    config = RuntimeConfig(
        model_aliases={
            "audit.prod": {"model": "gpt-5.5", "thinking_level": "high"},
        },
    )

    assert "model_aliases.audit.prod.model" in iter_config_field_paths(config)
    assert apply_boundary_for_field("model_aliases.audit.prod.model") is ApplyBoundary.RECOMPILE
