"""Config command view rendering."""

from __future__ import annotations

from millrace_ai.config import RuntimeConfig
from millrace_ai.paths import WorkspacePaths
from millrace_ai.state_store import load_snapshot

from .formatting import _value


def _render_config_show_lines(paths: WorkspacePaths, config: RuntimeConfig) -> tuple[str, ...]:
    snapshot = load_snapshot(paths)
    return (
        f"default_mode: {config.runtime.default_mode}",
        f"run_style: {config.runtime.run_style.value}",
        f"idle_sleep_seconds: {config.runtime.idle_sleep_seconds}",
        f"watchers.enabled: {'true' if config.watchers.enabled else 'false'}",
        f"auto_recovery.enabled: {'true' if config.auto_recovery.enabled else 'false'}",
        f"usage_governance.enabled: {'true' if config.usage_governance.enabled else 'false'}",
        f"execution_capabilities.enabled: {'true' if config.execution_capabilities.enabled else 'false'}",
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


__all__ = ["_render_config_show_lines"]
