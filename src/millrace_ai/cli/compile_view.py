"""Compile command view rendering."""

from __future__ import annotations

import typer

from millrace_ai.compiler import CompiledPlanCurrentness, CompileOutcome
from millrace_ai.contracts import CompileDiagnostics
from millrace_ai.paths import WorkspacePaths
from millrace_ai.workspace.baseline import BaselineManifest, load_baseline_manifest


def _render_compile_diagnostics(outcome: CompileOutcome) -> int:
    diagnostics: CompileDiagnostics = outcome.diagnostics
    typer.echo(f"ok: {'true' if diagnostics.ok else 'false'}")
    typer.echo(f"mode_id: {diagnostics.mode_id}")
    typer.echo(f"used_last_known_good: {'true' if outcome.used_last_known_good else 'false'}")
    if outcome.compile_input_fingerprint is not None:
        typer.echo(f"compile_input.mode_id: {outcome.compile_input_fingerprint.mode_id}")
        typer.echo(
            "compile_input.config_fingerprint: "
            f"{outcome.compile_input_fingerprint.config_fingerprint}"
        )
        typer.echo(
            "compile_input.assets_fingerprint: "
            f"{outcome.compile_input_fingerprint.assets_fingerprint}"
        )
    for warning in diagnostics.warnings:
        typer.echo(f"warning: {warning}")
    for error in diagnostics.errors:
        typer.echo(f"error: {error}")
    return 0 if diagnostics.ok else 1


def _render_compile_show_lines(paths: WorkspacePaths, outcome: CompileOutcome) -> tuple[str, ...]:
    plan = outcome.active_plan
    if plan is None:
        return ()

    baseline_manifest = _load_baseline_manifest_safe(paths)
    learning_graph = getattr(plan, "learning_graph", None)
    plan_fingerprint = getattr(plan, "compile_input_fingerprint", outcome.compile_input_fingerprint)
    currentness_state = (
        "current"
        if outcome.compile_input_fingerprint is None
        or plan_fingerprint == outcome.compile_input_fingerprint
        else "stale"
    )
    lines = [
        f"compiled_plan_currentness: {currentness_state}",
        f"compiled_plan_id: {plan.compiled_plan_id}",
        f"execution_loop_id: {plan.execution_loop_id}",
        f"planning_loop_id: {plan.planning_loop_id}",
    ]
    if getattr(plan, "learning_loop_id", None) is not None:
        lines.append(f"learning_loop_id: {plan.learning_loop_id}")
    lines.extend(_render_baseline_manifest_lines(baseline_manifest))
    expected_fingerprint = outcome.compile_input_fingerprint or plan_fingerprint
    if expected_fingerprint is None:
        lines.extend(_render_compile_currentness_lines(None, None))
    else:
        lines.extend(
            _render_compile_currentness_lines(
                CompiledPlanCurrentness(
                    state=currentness_state,
                    expected_fingerprint=expected_fingerprint,
                    persisted_plan_id=plan.compiled_plan_id,
                    persisted_fingerprint=plan_fingerprint,
                ),
                None,
            )
        )
    for entry in plan.execution_graph.compiled_entries:
        lines.append(f"entry: execution.{entry.entry_key.value} -> {entry.node_id}")
    for entry in plan.planning_graph.compiled_entries:
        lines.append(f"entry: planning.{entry.entry_key.value} -> {entry.node_id}")
    if learning_graph is not None:
        for entry in learning_graph.compiled_entries:
            lines.append(f"entry: learning.{entry.entry_key.value} -> {entry.node_id}")
    completion_entry = plan.planning_graph.compiled_completion_entry
    if completion_entry is not None:
        lines.append(f"completion: {completion_entry.entry_key.value} -> {completion_entry.node_id}")

    completion_behavior = getattr(plan.planning_graph, "completion_behavior", None)
    if completion_behavior is not None:
        lines.extend(
            (
                f"completion_behavior.trigger: {completion_behavior.trigger}",
                f"completion_behavior.readiness_rule: {completion_behavior.readiness_rule}",
                f"completion_behavior.request_kind: {completion_behavior.request_kind}",
                f"completion_behavior.target_selector: {completion_behavior.target_selector}",
                f"completion_behavior.rubric_policy: {completion_behavior.rubric_policy}",
                f"completion_behavior.blocked_work_policy: {completion_behavior.blocked_work_policy}",
                "completion_behavior.skip_if_already_closed: "
                f"{'true' if completion_behavior.skip_if_already_closed else 'false'}",
                "completion_behavior.on_pass_terminal_state_id: "
                f"{completion_behavior.on_pass_terminal_state_id}",
                "completion_behavior.on_gap_terminal_state_id: "
                f"{completion_behavior.on_gap_terminal_state_id}",
                "completion_behavior.create_incident_on_gap: "
                f"{'true' if completion_behavior.create_incident_on_gap else 'false'}",
            )
        )
    graph_nodes = sorted(
        (
            *plan.execution_graph.nodes,
            *plan.planning_graph.nodes,
            *(learning_graph.nodes if learning_graph is not None else ()),
        ),
        key=lambda item: (item.plane.value, item.node_id),
    )
    for stage_plan in graph_nodes:
        stage_kind_id = getattr(stage_plan, "stage_kind_id", stage_plan.node_id)
        running_status_marker = getattr(stage_plan, "running_status_marker", "none")
        lines.extend(
            (
                f"stage: {stage_plan.plane.value}.{stage_plan.node_id}",
                f"stage_kind_id: {stage_kind_id}",
                f"running_status_marker: {running_status_marker}",
                f"entrypoint_path: {stage_plan.entrypoint_path}",
                f"entrypoint_contract_id: {stage_plan.entrypoint_contract_id or 'none'}",
                "required_skills: "
                f"{', '.join(stage_plan.required_skill_paths) if stage_plan.required_skill_paths else 'none'}",
                "attached_skills: "
                f"{', '.join(stage_plan.attached_skill_additions) if stage_plan.attached_skill_additions else 'none'}",
                f"runner_name: {stage_plan.runner_name or 'none'}",
                f"model_name: {stage_plan.model_name or 'none'}",
                "model_reasoning_effort: "
                f"{getattr(stage_plan, 'model_reasoning_effort', None) or 'none'}",
                f"timeout_seconds: {stage_plan.timeout_seconds}",
            )
        )
    return tuple(lines)


def _render_baseline_manifest_lines(manifest: BaselineManifest | None) -> tuple[str, ...]:
    if manifest is None:
        return (
            "baseline_manifest_id: none",
            "baseline_seed_package_version: none",
        )
    return (
        f"baseline_manifest_id: {manifest.manifest_id}",
        f"baseline_seed_package_version: {manifest.seed_package_version}",
    )


def _render_compile_currentness_lines(
    currentness: CompiledPlanCurrentness | None,
    error: str | None,
) -> tuple[str, ...]:
    if currentness is None:
        return (
            "compile_input.mode_id: none",
            "compile_input.config_fingerprint: none",
            "compile_input.assets_fingerprint: none",
            f"compile_plan_currentness_error: {error or 'none'}",
        )
    lines = (
        f"compile_input.mode_id: {currentness.expected_fingerprint.mode_id}",
        (
            "compile_input.config_fingerprint: "
            f"{currentness.expected_fingerprint.config_fingerprint}"
        ),
        (
            "compile_input.assets_fingerprint: "
            f"{currentness.expected_fingerprint.assets_fingerprint}"
        ),
    )
    if currentness.persisted_fingerprint is None:
        persisted = (
            "persisted_compile_input.mode_id: none",
            "persisted_compile_input.config_fingerprint: none",
            "persisted_compile_input.assets_fingerprint: none",
        )
    else:
        persisted = (
            f"persisted_compile_input.mode_id: {currentness.persisted_fingerprint.mode_id}",
            (
                "persisted_compile_input.config_fingerprint: "
                f"{currentness.persisted_fingerprint.config_fingerprint}"
            ),
            (
                "persisted_compile_input.assets_fingerprint: "
                f"{currentness.persisted_fingerprint.assets_fingerprint}"
            ),
        )
    return lines + persisted


def _load_baseline_manifest_safe(paths: WorkspacePaths) -> BaselineManifest | None:
    try:
        return load_baseline_manifest(paths)
    except Exception:
        return None


__all__ = ["_render_compile_diagnostics", "_render_compile_show_lines"]
