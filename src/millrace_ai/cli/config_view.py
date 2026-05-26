"""Config command view rendering."""

from __future__ import annotations

from millrace_ai.config import RuntimeConfig
from millrace_ai.paths import WorkspacePaths
from millrace_ai.state_store import load_snapshot

from .formatting import _value


def _render_config_show_lines(paths: WorkspacePaths, config: RuntimeConfig) -> tuple[str, ...]:
    snapshot = load_snapshot(paths)
    lines = [
        f"default_mode: {config.runtime.default_mode}",
        f"run_style: {config.runtime.run_style.value}",
        f"idle_sleep_seconds: {config.runtime.idle_sleep_seconds}",
        f"watchers.enabled: {'true' if config.watchers.enabled else 'false'}",
        f"auto_recovery.enabled: {'true' if config.auto_recovery.enabled else 'false'}",
        f"usage_governance.enabled: {'true' if config.usage_governance.enabled else 'false'}",
        f"model_assignment.enabled: {'true' if config.model_assignment.enabled else 'false'}",
        f"model_assignment.default_alias: {config.model_assignment.default_alias}",
        (
            "model_assignment.invalid_assignment_policy: "
            f"{config.model_assignment.invalid_assignment_policy.value}"
        ),
    ]
    for alias_id in sorted(config.model_aliases):
        alias = config.model_aliases[alias_id]
        lines.append(
            f"model_alias.{alias_id}: model={alias.model or 'none'} "
            f"thinking_level={alias.thinking_level or 'none'}"
        )
    for loop_id in sorted(config.model_assignment.by_loop):
        lines.append(
            f"model_assignment.by_loop.{loop_id}: {config.model_assignment.by_loop[loop_id]}"
        )
    for stage in sorted(config.model_assignment.by_stage):
        lines.append(
            f"model_assignment.by_stage.{stage}: {config.model_assignment.by_stage[stage]}"
        )
    lines.extend(
        (
            (
                "execution_capabilities.enabled: "
                f"{'true' if config.execution_capabilities.enabled else 'false'}"
            ),
            (
                "execution_capabilities.allow_advisory_grants: "
                f"{'true' if config.execution_capabilities.allow_advisory_grants else 'false'}"
            ),
            (
                "execution_capabilities.fail_required_advisory: "
                f"{'true' if config.execution_capabilities.fail_required_advisory else 'false'}"
            ),
            f"config_version: {snapshot.config_version}",
            f"last_reload_outcome: {_value(snapshot.last_reload_outcome)}",
            f"last_reload_error: {_value(snapshot.last_reload_error)}",
        )
    )
    return tuple(lines)


__all__ = ["_render_config_show_lines"]
