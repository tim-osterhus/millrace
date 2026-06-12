"""Queue inspection and enqueue command group."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError

from millrace_ai.cli.errors import _print_error
from millrace_ai.cli.formatting import _print_control_result
from millrace_ai.cli.shared import (
    WorkspaceOption,
    _cli_api,
    _load_probe_document,
    _load_spec_document,
    _load_task_document,
    _queue_lookup,
    _require_paths,
    _validate_work_item_id,
)
from millrace_ai.compilation.persistence import load_existing_plan
from millrace_ai.config import load_runtime_config
from millrace_ai.contracts import IncidentDocument, Plane, ProbeDocument, SpecDocument, TaskDocument, WorkItemKind
from millrace_ai.errors import ControlRoutingError, QueueStateError
from millrace_ai.events import write_runtime_event
from millrace_ai.runtime.blocked_recovery import retry_blocked_work_item
from millrace_ai.runtime_lock import inspect_runtime_ownership_lock
from millrace_ai.state_store import load_snapshot, save_snapshot
from millrace_ai.work_documents import parse_work_document_as
from millrace_ai.workspace.arbiter_state import load_closure_target_state
from millrace_ai.workspace.lineage_integrity import (
    apply_lineage_repair_plan,
    build_lineage_repair_plan,
    write_lineage_repair_report,
)
from millrace_ai.workspace.queue_family_interpreter import QueueFamilyInterpreter
from millrace_ai.workspace.work_inventory import active_counts_by_plane, family_counts, queue_depths_by_plane

queue_app = typer.Typer(add_completion=False, no_args_is_help=True)


@queue_app.command("ls")
def queue_ls(workspace: WorkspaceOption = Path(".")) -> None:
    paths = _require_paths(workspace)
    compiled_plan = load_existing_plan(paths.state_dir / "compiled_plan.json")
    counts = family_counts(paths, compiled_plan=compiled_plan)
    queue_depths = queue_depths_by_plane(
        paths,
        compiled_plan=compiled_plan,
        family_counts_by_id=counts,
    )
    active_counts = active_counts_by_plane(
        paths,
        compiled_plan=compiled_plan,
        family_counts_by_id=counts,
    )
    execution_queue_depth = queue_depths[Plane.EXECUTION]
    planning_queue_depth = queue_depths[Plane.PLANNING]
    learning_queue_depth = queue_depths[Plane.LEARNING]
    execution_active = active_counts[Plane.EXECUTION]
    planning_active = active_counts[Plane.PLANNING]
    learning_active = active_counts[Plane.LEARNING]
    cancelled_task_count = _count_markdown(paths.tasks_queue_dir / "cancelled") + _count_markdown(
        paths.tasks_blocked_dir / "cancelled"
    )
    superseded_task_count = _count_markdown(paths.tasks_queue_dir / "superseded") + _count_markdown(
        paths.tasks_blocked_dir / "superseded"
    )
    cancelled_incident_count = (
        _count_markdown(paths.incidents_incoming_dir / "cancelled")
        + _count_markdown(paths.incidents_active_dir / "cancelled")
        + _count_markdown(paths.incidents_blocked_dir / "cancelled")
    )
    operator_resolved_incident_count = _count_markdown(paths.incidents_resolved_dir / "operator")

    typer.echo(f"execution_queue_depth: {execution_queue_depth}")
    typer.echo(f"planning_queue_depth: {planning_queue_depth}")
    typer.echo(f"learning_queue_depth: {learning_queue_depth}")
    typer.echo(f"execution_active: {execution_active}")
    typer.echo(f"planning_active: {planning_active}")
    typer.echo(f"learning_active: {learning_active}")
    for family_id in sorted(counts):
        family_state_counts = counts[family_id]
        typer.echo(f"{family_id}_queue_depth: {family_state_counts.get('queue', 0)}")
        typer.echo(f"active_{family_id}_count: {family_state_counts.get('active', 0)}")
        typer.echo(f"blocked_{family_id}_count: {family_state_counts.get('blocked', 0)}")
    typer.echo(f"cancelled_task_count: {cancelled_task_count}")
    typer.echo(f"superseded_task_count: {superseded_task_count}")
    typer.echo(f"cancelled_incident_count: {cancelled_incident_count}")
    typer.echo(f"operator_resolved_incident_count: {operator_resolved_incident_count}")

    # Emit canonical family-keyed depths via the shared interpreter.
    families = (
        tuple(compiled_plan.work_item_families_by_id.values())
        if compiled_plan is not None
        else None
    )
    family_interpreter = QueueFamilyInterpreter(paths, families=families)
    for family_id, depth in sorted(family_interpreter.queue_depths_by_family().items()):
        typer.echo(f"{family_id}_queue_depth: {depth}")


@queue_app.command("show")
def queue_show(
    work_item_id: Annotated[str, typer.Argument(help="Task/spec/incident ID to inspect.")],
    workspace: WorkspaceOption = Path("."),
) -> None:
    paths = _require_paths(workspace)
    try:
        validated_work_item_id = _validate_work_item_id(work_item_id)
    except ValueError as exc:
        raise typer.Exit(code=_print_error(f"invalid work item id: {exc}")) from exc

    located = _queue_lookup(paths, work_item_id=validated_work_item_id)
    if located is None:
        raise typer.Exit(code=_print_error(f"work item not found: {validated_work_item_id}"))
    work_item_kind, state, path = located

    document: TaskDocument | ProbeDocument | SpecDocument | IncidentDocument
    if work_item_kind == "task":
        document = parse_work_document_as(
            path.read_text(encoding="utf-8"),
            model=TaskDocument,
            path=path,
        )
    elif work_item_kind == "probe":
        document = parse_work_document_as(
            path.read_text(encoding="utf-8"),
            model=ProbeDocument,
            path=path,
        )
    elif work_item_kind == "spec":
        document = parse_work_document_as(
            path.read_text(encoding="utf-8"),
            model=SpecDocument,
            path=path,
        )
    else:
        document = parse_work_document_as(
            path.read_text(encoding="utf-8"),
            model=IncidentDocument,
            path=path,
        )

    typer.echo(f"work_item_id: {validated_work_item_id}")
    typer.echo(f"work_item_kind: {work_item_kind}")
    typer.echo(f"work_item_state: {state}")
    typer.echo(f"path: {path}")
    typer.echo(f"title: {getattr(document, 'title', 'unknown')}")


@queue_app.command("add-task")
def queue_add_task(
    task_path: Annotated[Path, typer.Argument(exists=True, file_okay=True, dir_okay=False, resolve_path=True)],
    workspace: WorkspaceOption = Path("."),
) -> None:
    paths = _require_paths(workspace)
    try:
        document = _load_task_document(task_path)
        result = _cli_api().RuntimeControl(paths).add_task(document)
    except (OSError, ValidationError, ValueError) as exc:
        raise typer.Exit(code=_print_error(f"failed to add task: {exc}")) from exc
    if result.mode == "mailbox":
        _print_control_result(result)
        return
    if result.artifact_path is None:
        raise typer.Exit(code=_print_error("failed to add task: missing artifact path"))
    typer.echo(f"enqueued_task: {result.artifact_path}")


@queue_app.command("add-probe")
def queue_add_probe(
    probe_path: Annotated[Path, typer.Argument(exists=True, file_okay=True, dir_okay=False, resolve_path=True)],
    workspace: WorkspaceOption = Path("."),
) -> None:
    paths = _require_paths(workspace)
    try:
        document = _load_probe_document(probe_path)
        result = _cli_api().RuntimeControl(paths).add_probe(document)
    except (OSError, ValidationError, ValueError) as exc:
        raise typer.Exit(code=_print_error(f"failed to add probe: {exc}")) from exc
    if result.mode == "mailbox":
        _print_control_result(result)
        return
    if result.artifact_path is None:
        raise typer.Exit(code=_print_error("failed to add probe: missing artifact path"))
    typer.echo(f"enqueued_probe: {result.artifact_path}")


@queue_app.command("add-spec")
def queue_add_spec(
    spec_path: Annotated[Path, typer.Argument(exists=True, file_okay=True, dir_okay=False, resolve_path=True)],
    workspace: WorkspaceOption = Path("."),
) -> None:
    paths = _require_paths(workspace)
    try:
        document = _load_spec_document(spec_path)
        result = _cli_api().RuntimeControl(paths).add_spec(document)
    except (OSError, ValidationError, ValueError) as exc:
        raise typer.Exit(code=_print_error(f"failed to add spec: {exc}")) from exc
    if result.mode == "mailbox":
        _print_control_result(result)
        return
    if result.artifact_path is None:
        raise typer.Exit(code=_print_error("failed to add spec: missing artifact path"))
    typer.echo(f"enqueued_spec: {result.artifact_path}")


@queue_app.command("add-idea")
def queue_add_idea(
    idea_path: Annotated[Path, typer.Argument(exists=True, file_okay=True, dir_okay=False, resolve_path=True)],
    workspace: WorkspaceOption = Path("."),
) -> None:
    paths = _require_paths(workspace)
    try:
        markdown = idea_path.read_text(encoding="utf-8")
        result = (
            _cli_api()
            .RuntimeControl(paths)
            .add_idea_markdown(
                source_name=idea_path.name,
                markdown=markdown,
            )
        )
    except (OSError, ValueError) as exc:
        raise typer.Exit(code=_print_error(f"failed to add idea: {exc}")) from exc
    if result.mode == "mailbox":
        _print_control_result(result)
        return
    if result.artifact_path is None:
        raise typer.Exit(code=_print_error("failed to add idea: missing artifact path"))
    typer.echo(f"enqueued_idea: {result.artifact_path}")


@queue_app.command("cancel")
def queue_cancel(
    work_item_id: Annotated[str, typer.Argument(help="Queued or blocked work item ID to cancel.")],
    workspace: WorkspaceOption = Path("."),
    kind: Annotated[
        str | None,
        typer.Option("--kind", help="Optional legacy work item kind."),
    ] = None,
    family: Annotated[
        str | None,
        typer.Option("--family", help="Optional graph work item family id."),
    ] = None,
    reason: Annotated[
        str,
        typer.Option("--reason", help="Audit reason for cancelling the work item."),
    ] = "",
    force: Annotated[
        bool,
        typer.Option("--force", help="Reserved override flag for future duplicate/lineage warnings."),
    ] = False,
) -> None:
    paths = _require_paths(workspace)
    try:
        validated_work_item_id = _validate_work_item_id(work_item_id)
        work_item_kind = _parse_optional_work_item_kind(kind)
        result = _cli_api().RuntimeControl(paths).cancel_work_item(
            work_item_id=validated_work_item_id,
            work_item_family_id=family,
            work_item_kind=work_item_kind,
            reason=reason,
            force=force,
        )
    except (OSError, ControlRoutingError, QueueStateError, ValidationError, ValueError) as exc:
        raise typer.Exit(code=_print_error(f"failed to cancel work item: {exc}")) from exc
    _print_control_result(result)


@queue_app.command("archive-blocked")
def queue_archive_blocked(
    task_id: Annotated[str, typer.Argument(help="Blocked task ID to archive without retrying.")],
    workspace: WorkspaceOption = Path("."),
    reason: Annotated[
        str,
        typer.Option("--reason", help="Audit reason for archiving the blocked task."),
    ] = "",
) -> None:
    paths = _require_paths(workspace)
    try:
        validated_task_id = _validate_work_item_id(task_id)
        result = _cli_api().RuntimeControl(paths).archive_blocked_task(
            task_id=validated_task_id,
            reason=reason,
        )
    except (OSError, ControlRoutingError, QueueStateError, ValidationError, ValueError) as exc:
        raise typer.Exit(code=_print_error(f"failed to archive blocked task: {exc}")) from exc
    _print_control_result(result)


@queue_app.command("supersede")
def queue_supersede(
    old_task_id: Annotated[str, typer.Argument(help="Queued or blocked task ID to supersede.")],
    workspace: WorkspaceOption = Path("."),
    replacement: Annotated[
        str,
        typer.Option("--replacement", help="Existing queued, active, or done replacement task ID."),
    ] = "",
    reason: Annotated[
        str,
        typer.Option("--reason", help="Audit reason for superseding the task."),
    ] = "",
    cascade: Annotated[
        str,
        typer.Option("--cascade", help="Dependent handling: none, retarget, or cancel."),
    ] = "none",
) -> None:
    paths = _require_paths(workspace)
    try:
        validated_old_task_id = _validate_work_item_id(old_task_id)
        validated_replacement = _validate_work_item_id(replacement)
        result = _cli_api().RuntimeControl(paths).supersede_task(
            old_task_id=validated_old_task_id,
            replacement_task_id=validated_replacement,
            reason=reason,
            cascade=cascade,
        )
    except (OSError, ControlRoutingError, QueueStateError, ValidationError, ValueError) as exc:
        raise typer.Exit(code=_print_error(f"failed to supersede task: {exc}")) from exc
    _print_control_result(result)


@queue_app.command("retarget-dependency")
def queue_retarget_dependency(
    task_id: Annotated[str, typer.Argument(help="Queued task ID whose dependency should be rewritten.")],
    workspace: WorkspaceOption = Path("."),
    old_dependency: Annotated[
        str,
        typer.Option("--from", "--old-dependency", help="Existing dependency ID to replace."),
    ] = "",
    new_dependency: Annotated[
        str,
        typer.Option("--to", "--new-dependency", help="Replacement dependency ID."),
    ] = "",
    reason: Annotated[
        str,
        typer.Option("--reason", help="Audit reason for rewriting the dependency."),
    ] = "",
) -> None:
    paths = _require_paths(workspace)
    try:
        validated_task_id = _validate_work_item_id(task_id)
        validated_old_dependency = _validate_work_item_id(old_dependency)
        validated_new_dependency = _validate_work_item_id(new_dependency)
        result = _cli_api().RuntimeControl(paths).retarget_task_dependency(
            task_id=validated_task_id,
            old_dependency_id=validated_old_dependency,
            new_dependency_id=validated_new_dependency,
            reason=reason,
        )
    except (OSError, ControlRoutingError, QueueStateError, ValidationError, ValueError) as exc:
        raise typer.Exit(code=_print_error(f"failed to retarget task dependency: {exc}")) from exc
    _print_control_result(result)


@queue_app.command("retry-blocked")
def queue_retry_blocked(
    work_item_id: Annotated[str, typer.Argument(help="Blocked work item ID to move back to queue.")],
    workspace: WorkspaceOption = Path("."),
    kind: Annotated[
        str | None,
        typer.Option("--kind", help="Optional built-in work item kind."),
    ] = None,
    family: Annotated[
        str | None,
        typer.Option("--family", help="Optional graph work item family id."),
    ] = None,
    reason: Annotated[
        str,
        typer.Option("--reason", help="Audit reason for retrying the blocked work item."),
    ] = "",
    root_spec_id: Annotated[
        str,
        typer.Option("--root-spec-id", help="Optional root-spec guard for the blocked work item."),
    ] = "",
    force: Annotated[
        bool,
        typer.Option("--force", help="Override retryability and retry-budget checks."),
    ] = False,
) -> None:
    paths = _require_paths(workspace)
    try:
        validated_work_item_id = _validate_work_item_id(work_item_id)
        validated_root_spec_id = _validate_work_item_id(root_spec_id) if root_spec_id.strip() else None
        work_item_kind = _parse_optional_work_item_kind(kind)
        lock_status = inspect_runtime_ownership_lock(paths)
        if lock_status.state == "active":
            raise QueueStateError("active runtime ownership lock prevents blocked retry")
        config = load_runtime_config(paths.runtime_root / "millrace.toml")
        result = retry_blocked_work_item(
            paths,
            work_item_id=validated_work_item_id,
            work_item_family_id=family,
            work_item_kind=work_item_kind,
            reason=reason,
            actor="operator",
            auto=False,
            force=force,
            root_spec_id=validated_root_spec_id,
            config=config,
        )
    except (OSError, QueueStateError, ValidationError, ValueError) as exc:
        raise typer.Exit(code=_print_error(f"failed to retry blocked work item: {exc}")) from exc

    if result.work_item_family_id == WorkItemKind.TASK.value:
        typer.echo(f"requeued_task: {result.work_item_id}")
        typer.echo(f"source_state: {result.source_state}")
        typer.echo(f"destination_state: {result.destination_state}")
        typer.echo(f"source_path: {result.source_path}")
        typer.echo(f"destination_path: {result.destination_path}")
        typer.echo(f"actor: {result.actor}")
        typer.echo(f"auto: {'true' if result.auto else 'false'}")
        typer.echo(f"attempt_number: {result.attempt_number}")
        typer.echo(f"failure_class: {result.failure_class or 'none'}")
        return

    typer.echo(f"requeued_work_item: {result.work_item_id}")
    typer.echo(f"work_item_family_id: {result.work_item_family_id}")
    typer.echo(f"work_item_kind: {result.work_item_kind.value if result.work_item_kind is not None else 'none'}")
    typer.echo(f"source_state: {result.source_state}")
    typer.echo(f"destination_state: {result.destination_state}")
    typer.echo(f"source_path: {result.source_path}")
    typer.echo(f"destination_path: {result.destination_path}")
    typer.echo(f"actor: {result.actor}")
    typer.echo(f"auto: {'true' if result.auto else 'false'}")
    typer.echo(f"attempt_number: {result.attempt_number}")
    typer.echo(f"failure_class: {result.failure_class or 'none'}")


@queue_app.command("repair-lineage")
def queue_repair_lineage(
    workspace: WorkspaceOption = Path("."),
    root_spec_id: Annotated[
        str,
        typer.Option("--root-spec-id", help="Canonical open closure root spec to inspect or repair."),
    ] = "",
    apply: Annotated[
        bool,
        typer.Option("--apply", help="Apply safe queued/blocked work-document repairs."),
    ] = False,
) -> None:
    paths = _require_paths(workspace)
    try:
        validated_root_spec_id = _validate_work_item_id(root_spec_id)
    except ValueError as exc:
        raise typer.Exit(code=_print_error(f"invalid root spec id: {exc}")) from exc

    try:
        target = load_closure_target_state(paths, root_spec_id=validated_root_spec_id)
    except (OSError, ValidationError, ValueError) as exc:
        raise typer.Exit(code=_print_error(f"failed to load closure target: {exc}")) from exc

    plan = build_lineage_repair_plan(paths, target)
    report_path = write_lineage_repair_report(paths, plan, applied=False)
    repaired_count = 0

    if apply:
        lock_status = inspect_runtime_ownership_lock(paths)
        if lock_status.state == "active":
            raise typer.Exit(code=_print_error("active runtime ownership lock prevents lineage repair"))
        snapshot = load_snapshot(paths)
        if snapshot.active_stage is not None:
            raise typer.Exit(code=_print_error("active runtime stage prevents lineage repair"))
        repaired_count = apply_lineage_repair_plan(paths, plan)
        compiled_plan = load_existing_plan(paths.state_dir / "compiled_plan.json")
        queue_depths = queue_depths_by_plane(paths, compiled_plan=compiled_plan)
        families = (
            tuple(compiled_plan.work_item_families_by_id.values())
            if compiled_plan is not None
            else None
        )
        family_interpreter = QueueFamilyInterpreter(paths, families=families)
        queue_depths_by_family = family_interpreter.queue_depths_by_family()
        snapshot = load_snapshot(paths).model_copy(
            update={
                "queue_depth_execution": queue_depths[Plane.EXECUTION],
                "queue_depth_planning": queue_depths[Plane.PLANNING],
                "queue_depth_learning": queue_depths[Plane.LEARNING],
                "queue_depths_by_plane": queue_depths,
                "queue_depths_by_family": queue_depths_by_family,
            }
        )
        save_snapshot(paths, snapshot)
        report_path = write_lineage_repair_report(paths, plan, applied=True)
        write_runtime_event(
            paths,
            event_type="closure_lineage_repaired",
            data={
                "root_spec_id": target.root_spec_id,
                "root_source_kind": target.root_source.kind,
                "root_source_id": target.root_source.id,
                "repair_count": repaired_count,
                "repair_report_path": str(report_path.relative_to(paths.root)),
            },
        )

    typer.echo(f"root_spec_id: {target.root_spec_id}")
    typer.echo(f"root_source_kind: {target.root_source.kind}")
    typer.echo(f"root_source_id: {target.root_source.id}")
    typer.echo(f"root_source_path: {target.root_source.path}")
    typer.echo(f"apply: {'true' if apply else 'false'}")
    repair_count = len({(change.work_item_kind, change.work_item_id, change.path) for change in plan.changes})
    typer.echo(f"repair_count: {repair_count}")
    typer.echo(f"change_count: {len(plan.changes)}")
    typer.echo(f"repaired_count: {repaired_count}")
    typer.echo(f"skipped_count: {len(plan.skipped_findings)}")
    typer.echo(f"repair_report: {report_path}")
    for change in plan.changes:
        typer.echo(
            "change: "
            f"{change.work_item_kind.value} {change.work_item_id} {change.field_name} "
            f"{change.old_value} -> {change.new_value}"
        )
    for finding in plan.skipped_findings:
        typer.echo(f"skipped: {finding.work_item_kind.value} {finding.work_item_id} state={finding.state}")


def _parse_optional_work_item_kind(value: str | None) -> WorkItemKind | None:
    if value is None or not value.strip():
        return None
    try:
        return WorkItemKind(value.strip())
    except ValueError as exc:
        allowed = ", ".join(kind.value for kind in WorkItemKind)
        raise ValueError(f"kind must be one of: {allowed}") from exc


def _count_markdown(directory: Path) -> int:
    return len(tuple(directory.glob("*.md")))


def add_task(
    task_path: Annotated[Path, typer.Argument(exists=True, file_okay=True, dir_okay=False, resolve_path=True)],
    workspace: WorkspaceOption = Path("."),
) -> None:
    queue_add_task(task_path=task_path, workspace=workspace)


def add_spec(
    spec_path: Annotated[Path, typer.Argument(exists=True, file_okay=True, dir_okay=False, resolve_path=True)],
    workspace: WorkspaceOption = Path("."),
) -> None:
    queue_add_spec(spec_path=spec_path, workspace=workspace)


def add_probe(
    probe_path: Annotated[Path, typer.Argument(exists=True, file_okay=True, dir_okay=False, resolve_path=True)],
    workspace: WorkspaceOption = Path("."),
) -> None:
    queue_add_probe(probe_path=probe_path, workspace=workspace)
