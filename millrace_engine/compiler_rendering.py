"""Markdown rendering helpers for frozen-plan compiler artifacts."""

from __future__ import annotations

from .compiler_models import CompileTimeResolvedSnapshot, FrozenLoopPlan, FrozenRunPlan
from .contracts import RegistryObjectRef


def render_compile_time_resolved_snapshot_markdown(snapshot: CompileTimeResolvedSnapshot) -> str:
    """Render the human companion for one resolved compile-time snapshot."""

    lines = [
        "# Resolved Snapshot",
        "",
        "> Generated from `resolved_snapshot.json`. Treat the JSON artifact as canonical.",
        "> Compile-time provenance only. Runtime execution history is written separately to `transition_history.jsonl`.",
        "",
        f"- Snapshot ID: `{snapshot.snapshot_id}`",
        f"- Run ID: `{snapshot.run_id}`",
        f"- Created At: `{snapshot.created_at.isoformat().replace('+00:00', 'Z')}`",
        f"- Selection: `{_ref_string(snapshot.selection_ref)}`",
        f"- Frozen Plan ID: `{snapshot.frozen_plan.plan_id}`",
        f"- Frozen Plan Hash: `{snapshot.frozen_plan.content_hash}`",
    ]
    content = snapshot.content
    if content.selected_mode_ref is not None:
        lines.append(f"- Selected Mode: `{_ref_string(content.selected_mode_ref)}`")
    if content.selected_execution_loop_ref is not None:
        lines.append(f"- Execution Loop: `{_ref_string(content.selected_execution_loop_ref)}`")
    if content.selected_research_loop_ref is not None:
        lines.append(f"- Research Loop: `{_ref_string(content.selected_research_loop_ref)}`")
    lines.append(f"- Research Participation: `{content.research_participation.value}`")
    lines.extend(["", "## Parameter Rebinding Rules", ""])
    if content.parameter_rebinding_rules:
        for rule in content.parameter_rebinding_rules:
            lines.append(
                f"- `{rule.plane.value}.{rule.node_id}.{rule.field.value}` at `{rule.rebind_at_boundary.value}` "
                f"(current=`{rule.current_value!r}`)"
            )
    else:
        lines.append("- none")
    if content.execution_plan is not None:
        lines.extend(_render_loop_markdown(content.execution_plan, heading="Execution Plan"))
    if content.research_plan is not None:
        lines.extend(_render_loop_markdown(content.research_plan, heading="Research Plan"))
    lines.extend(["", "## Compile Diagnostics", ""])
    if snapshot.compile_diagnostics:
        for diagnostic in snapshot.compile_diagnostics:
            lines.append(
                f"- `{diagnostic.phase.value}` `{diagnostic.code}` at `{diagnostic.path}`: {diagnostic.message}"
            )
    else:
        lines.append("- none")
    lines.append("")
    return "\n".join(lines)


def render_frozen_run_plan_markdown(plan: FrozenRunPlan) -> str:
    """Render the human companion for one frozen run plan."""

    content = plan.content
    lines = [
        "# Frozen Run Plan",
        "",
        "> Generated from `frozen_run_plan.json`. Treat the JSON artifact as canonical.",
        "> Compile-time provenance only. Standard execution now routes against this frozen plan.",
        "> Runtime `transition_history.jsonl` records the selected edge, any explicit legacy seams, and the bound execution parameters actually used.",
        "",
        f"- Run ID: `{plan.run_id}`",
        f"- Compiled At: `{plan.compiled_at.isoformat().replace('+00:00', 'Z')}`",
        f"- Content Hash: `{plan.content_hash}`",
        f"- Compiler Version: `{content.compiler_version}`",
        f"- Selection: `{_ref_string(content.selection_ref)}`",
    ]
    if content.selected_mode_ref is not None:
        lines.append(f"- Selected Mode: `{_ref_string(content.selected_mode_ref)}`")
    if content.selected_execution_loop_ref is not None:
        lines.append(f"- Execution Loop: `{_ref_string(content.selected_execution_loop_ref)}`")
    if content.selected_research_loop_ref is not None:
        lines.append(f"- Research Loop: `{_ref_string(content.selected_research_loop_ref)}`")
    if content.task_authoring_profile_ref is not None:
        lines.append(f"- Task Authoring Profile: `{_ref_string(content.task_authoring_profile_ref)}`")
    if content.model_profile_ref is not None:
        lines.append(f"- Model Profile: `{_ref_string(content.model_profile_ref)}`")
    lines.append(f"- Research Participation: `{content.research_participation.value}`")

    if content.outline_policy is not None:
        lines.extend(
            [
                "",
                "## Outline Policy",
                "",
                f"- Mode: `{content.outline_policy.mode.value}`",
                f"- Shard Glob: `{content.outline_policy.shard_glob or 'n/a'}`",
            ]
        )

    lines.extend(["", "## Parameter Rebinding Rules", ""])
    if content.parameter_rebinding_rules:
        for rule in content.parameter_rebinding_rules:
            lines.append(
                f"- `{rule.plane.value}.{rule.node_id}.{rule.field.value}` may rebind at "
                f"`{rule.rebind_at_boundary.value}` from current value `{rule.current_value!r}` "
                f"(declared by `{_ref_string(rule.stage_kind_ref)}`)"
            )
    else:
        lines.append("- none")

    if content.execution_plan is not None:
        lines.extend(_render_loop_markdown(content.execution_plan, heading="Execution Plan"))
    if content.research_plan is not None:
        lines.extend(_render_loop_markdown(content.research_plan, heading="Research Plan"))

    lines.extend(["", "## Compile Diagnostics", ""])
    if plan.compile_diagnostics:
        for diagnostic in plan.compile_diagnostics:
            lines.append(
                f"- `{diagnostic.phase.value}` `{diagnostic.code}` at `{diagnostic.path}`: {diagnostic.message}"
            )
    else:
        lines.append("- none")

    lines.extend(["", "## Source Refs", ""])
    for source_ref in content.source_refs:
        lines.append(
            f"- `{source_ref.object_ref}` | kind=`{source_ref.kind.value}` "
            f"layer=`{source_ref.source_layer}` sha256=`{source_ref.sha256}`"
        )
    lines.append("")
    return "\n".join(lines)


def _render_loop_markdown(plan: FrozenLoopPlan, *, heading: str) -> list[str]:
    lines = [
        "",
        f"## {heading}",
        "",
        f"- Loop Ref: `{_ref_string(plan.requested_ref)}`",
        f"- Plane: `{plan.plane.value}`",
        f"- Entry Node: `{plan.entry_node_id}`",
    ]
    if plan.task_authoring_profile_ref is not None:
        lines.append(f"- Task Authoring Profile: `{_ref_string(plan.task_authoring_profile_ref)}`")
    if plan.model_profile_ref is not None:
        lines.append(f"- Model Profile: `{_ref_string(plan.model_profile_ref)}`")
    if plan.outline_policy is not None:
        lines.append(f"- Outline Mode: `{plan.outline_policy.mode.value}`")

    lines.extend(["", "### Stages", ""])
    for stage in plan.stages:
        lines.append(
            f"- `{stage.node_id}` -> `{stage.kind_id}` "
            f"(runner=`{stage.runner.value if stage.runner is not None else 'n/a'}`, "
            f"model=`{stage.model or 'n/a'}`, search=`{stage.allow_search}`)"
        )

    lines.extend(["", "### Transitions", ""])
    for transition in plan.transitions:
        target = transition.to_node_id or transition.terminal_state_id or "n/a"
        target_kind = "node" if transition.to_node_id is not None else "terminal"
        lines.append(
            f"- `{transition.edge_id}`: `{transition.from_node_id}` "
            f"--[{', '.join(transition.on_outcomes)}]--> {target_kind} `{target}`"
        )

    lines.extend(["", "### Resume States", ""])
    for resume_state in plan.resume_states:
        lines.append(
            f"- `{resume_state.status}` -> `{resume_state.terminal_state_id}` ({resume_state.terminal_class.value})"
        )
    return lines


def _ref_string(ref: RegistryObjectRef | None) -> str:
    if ref is None:
        return "n/a"
    return f"{ref.kind.value}:{ref.id}@{ref.version}"


__all__ = [
    "render_compile_time_resolved_snapshot_markdown",
    "render_frozen_run_plan_markdown",
]
