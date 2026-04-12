"""Typer-based runtime control CLI."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any

import typer

from .cli_rendering import (
    _asset_inventory_lines,
    _legacy_policy_lines,
    _legacy_unmapped_lines,
    _selection_explanation_lines,
    _selection_lines,
    render_compounding_context_fact,
    render_compounding_context_facts,
    render_compounding_governance_summary,
    render_compounding_harness_benchmark,
    render_compounding_harness_benchmarks,
    render_compounding_harness_candidate,
    render_compounding_harness_candidates,
    render_compounding_harness_recommendation,
    render_compounding_harness_recommendations,
    render_compounding_lint,
    render_compounding_orientation,
    render_compounding_procedure,
    render_compounding_procedures,
    render_doctor,
    render_follow_event,
    render_health,
    render_log_events,
    render_operation,
    render_publish_commit,
    render_publish_preflight,
    render_queue,
    render_research_report,
    render_run_provenance,
    render_staging_sync,
    render_status,
    render_supervisor_report,
    render_upgrade_apply,
    render_upgrade_preview,
)
from .compounding.integrity import CompoundingIntegrityReport
from .control import ConfigShowReport, ControlError, EngineControl
from .control_models import (
    CompoundingContextFactListReport,
    CompoundingContextFactReport,
    CompoundingHarnessBenchmarkListReport,
    CompoundingHarnessBenchmarkReport,
    CompoundingHarnessCandidateListReport,
    CompoundingHarnessCandidateReport,
    CompoundingHarnessRecommendationListReport,
    CompoundingHarnessRecommendationReport,
    CompoundingOrientationReport,
    CompoundingProcedureListReport,
    CompoundingProcedureReport,
    InterviewListReport,
    InterviewMutationReport,
    InterviewQuestionReport,
    RecoveryRequestTarget,
)
from .events import EventRecord

app = typer.Typer(add_completion=False, help="Control the Millrace runtime.")
config_app = typer.Typer(help="Inspect or mutate runtime config.")
queue_app = typer.Typer(help="Inspect visible execution queues.")
queue_cleanup_app = typer.Typer(help="Remove or quarantine invalid queued work.")
active_task_app = typer.Typer(help="Clear or recover stale active-task state.")
compounding_app = typer.Typer(help="Inspect governed reusable procedures.")
compounding_facts_app = typer.Typer(help="Inspect governed durable context facts.")
compounding_procedures_app = typer.Typer(help="Inspect or mutate governed reusable procedures.")
compounding_harness_app = typer.Typer(help="Inspect governed harness candidates and benchmark results.")
compounding_harness_candidates_app = typer.Typer(help="Inspect governed harness candidates.")
compounding_harness_benchmarks_app = typer.Typer(help="Inspect or run governed harness benchmarks.")
compounding_harness_search_app = typer.Typer(help="Run bounded config/assets-only harness search.")
compounding_harness_recommendations_app = typer.Typer(help="Inspect bounded harness recommendations.")
research_app = typer.Typer(help="Inspect research runtime state and history.")
interview_app = typer.Typer(help="Inspect and resolve manual GoalSpec interview questions.")
publish_app = typer.Typer(help="Sync and publish the staging surface.")
recovery_app = typer.Typer(help="Queue high-privilege manual recovery requests.")
sentinel_app = typer.Typer(help="Run one-shot Sentinel diagnosis and inspect persisted Sentinel results.")
supervisor_app = typer.Typer(help="External supervisor report surfaces.")
supervisor_cleanup_app = typer.Typer(help="Remove or quarantine invalid queued work with issuer attribution.")
supervisor_active_task_app = typer.Typer(help="Supervisor-attributed active-task remediation.")
app.add_typer(config_app, name="config")
app.add_typer(queue_app, name="queue")
queue_app.add_typer(queue_cleanup_app, name="cleanup")
app.add_typer(active_task_app, name="active-task")
app.add_typer(compounding_app, name="compounding")
compounding_app.add_typer(compounding_facts_app, name="facts")
compounding_app.add_typer(compounding_procedures_app, name="procedures")
compounding_app.add_typer(compounding_harness_app, name="harness")
compounding_harness_app.add_typer(compounding_harness_candidates_app, name="candidates")
compounding_harness_app.add_typer(compounding_harness_benchmarks_app, name="benchmarks")
compounding_harness_app.add_typer(compounding_harness_search_app, name="search")
compounding_harness_app.add_typer(compounding_harness_recommendations_app, name="recommendations")
app.add_typer(research_app, name="research")
app.add_typer(interview_app, name="interview")
app.add_typer(publish_app, name="publish")
app.add_typer(recovery_app, name="recovery")
app.add_typer(sentinel_app, name="sentinel")
app.add_typer(supervisor_app, name="supervisor")
supervisor_app.add_typer(supervisor_cleanup_app, name="cleanup")
supervisor_app.add_typer(supervisor_active_task_app, name="active-task")


@dataclass(frozen=True, slots=True)
class CLIContext:
    config_path: Path


def _json_output(payload: Any, *, err: bool = False) -> None:
    typer.echo(json.dumps(payload, indent=2, sort_keys=True), err=err)


def _cli_context(ctx: typer.Context) -> CLIContext:
    cli_context = ctx.obj
    if not isinstance(cli_context, CLIContext):
        raise typer.Exit(code=1)
    return cli_context


def _control(ctx: typer.Context) -> EngineControl:
    return EngineControl(_cli_context(ctx).config_path)


def _config_path(ctx: typer.Context) -> Path:
    return _cli_context(ctx).config_path


def _exit_control_error(error: ControlError, *, json_mode: bool) -> None:
    if json_mode:
        _json_output({"error": str(error)}, err=True)
    else:
        typer.echo(str(error), err=True)
    raise typer.Exit(code=1)


def _run_expected(action: Callable[[], Any], *, json_mode: bool) -> Any:
    try:
        return action()
    except ControlError as exc:
        _exit_control_error(exc, json_mode=json_mode)


def _iter_expected(events: Any, *, json_mode: bool) -> Any:
    try:
        yield from events
    except ControlError as exc:
        _exit_control_error(exc, json_mode=json_mode)


def _render_interview_list(report: InterviewListReport, *, json_mode: bool) -> None:
    if json_mode:
        _json_output(report.model_dump(mode="json"))
        return
    if not report.questions:
        typer.echo("No interview questions.")
        return
    lines: list[str] = []
    for question in report.questions:
        lines.extend(
            [
                f"{question.question_id} [{question.status}] spec={question.spec_id}",
                f"  Title: {question.title}",
                f"  Question: {question.question}",
                f"  Blocking: {'yes' if question.blocking else 'no'}",
                f"  Source: {question.source_path}",
            ]
        )
    typer.echo("\n".join(lines))


def _render_interview_question(report: InterviewQuestionReport, *, json_mode: bool) -> None:
    if json_mode:
        _json_output(report.model_dump(mode="json"))
        return
    question = report.question
    lines = [
        f"Question ID: {question.question_id}",
        f"Status: {question.status}",
        f"Spec ID: {question.spec_id}",
    ]
    if question.idea_id:
        lines.append(f"Idea ID: {question.idea_id}")
    lines.extend(
        [
            f"Title: {question.title}",
            f"Source: {question.source_path}",
            f"Blocking: {'yes' if question.blocking else 'no'}",
            f"Question: {question.question}",
            f"Why this matters: {question.why_this_matters}",
            f"Recommended answer: {question.recommended_answer}",
            f"Answer source: {question.answer_source}",
            f"Question artifact: {report.question_path.as_posix()}",
        ]
    )
    if question.evidence:
        lines.append("Evidence:")
        lines.extend(f"- {item}" for item in question.evidence)
    if report.decision is not None and report.decision_path is not None:
        lines.extend(
            [
                f"Decision: {report.decision.decision}",
                f"Decision source: {report.decision.decision_source}",
                f"Decision artifact: {report.decision_path.as_posix()}",
            ]
        )
    typer.echo("\n".join(lines))


def _render_interview_mutation(report: InterviewMutationReport, *, json_mode: bool) -> None:
    if json_mode:
        _json_output(report.model_dump(mode="json"))
        return
    lines = [
        f"Action: {report.action}",
        f"Question ID: {report.question.question_id}",
        f"Status: {report.question.status}",
        f"Question artifact: {report.question_path.as_posix()}",
    ]
    if report.decision is not None and report.decision_path is not None:
        lines.extend(
            [
                f"Decision source: {report.decision.decision_source}",
                f"Decision artifact: {report.decision_path.as_posix()}",
            ]
        )
    typer.echo("\n".join(lines))


def _render_sentinel_status(report: Any, *, json_mode: bool) -> None:
    if json_mode:
        _json_output(report.model_dump(mode="json"))
        return
    lines = [
        f"Config enabled: {'yes' if report.config_enabled else 'no'}",
        f"Available: {'yes' if report.available else 'no'}",
        f"Reason: {report.reason}",
        f"State path: {report.state_path.as_posix()}",
        f"Summary path: {report.summary_path.as_posix()}",
        f"Latest report path: {report.latest_report_path.as_posix()}",
    ]
    if report.latest_check_path is not None:
        lines.append(f"Latest check path: {report.latest_check_path.as_posix()}")
    if report.report is not None:
        lines.extend(
            [
                f"Status: {report.report.status}",
                f"Report reason: {report.report.reason}",
            ]
        )
    typer.echo("\n".join(lines))


def _render_sentinel_check(report: Any, *, json_mode: bool) -> None:
    if json_mode:
        _json_output(report.model_dump(mode="json"))
        return
    lines = [
        f"Config enabled: {'yes' if report.config_enabled else 'no'}",
        f"Autonomous state applied: {'yes' if report.autonomous_state_applied else 'no'}",
        f"Status: {report.report.status}",
        f"Reason: {report.report.reason}",
        f"Check ID: {report.check.check_id}",
        f"State path: {report.state_path.as_posix()}",
        f"Summary path: {report.summary_path.as_posix()}",
        f"Latest report path: {report.latest_report_path.as_posix()}",
        f"Latest check path: {report.latest_check_path.as_posix()}",
    ]
    if report.supervisor_observation_error:
        lines.append(f"Supervisor observation error: {report.supervisor_observation_error}")
    typer.echo("\n".join(lines))


def _render_sentinel_watch(report: Any, *, json_mode: bool) -> None:
    if json_mode:
        _json_output(report.model_dump(mode="json"))
        return
    lines = [
        f"Config enabled: {'yes' if report.config_enabled else 'no'}",
        f"Autonomous state applied: {'yes' if report.autonomous_state_applied else 'no'}",
        f"Iterations completed: {report.iterations_completed}",
        f"Stop reason: {report.stop_reason}",
        f"Status: {report.report.status}",
        f"Reason: {report.report.reason}",
        f"Latest check path: {report.latest_check_path.as_posix()}",
    ]
    typer.echo("\n".join(lines))


def _render_sentinel_incident(report: Any, *, json_mode: bool) -> None:
    if json_mode:
        _json_output(report.model_dump(mode="json"))
        return
    lines = [
        f"Mode: {report.mode}",
        f"Applied: {'yes' if report.applied else 'no'}",
        f"Message: {report.message}",
        f"Incident path: {report.incident_path.as_posix()}",
        f"Bundle path: {report.bundle_path.as_posix()}",
        f"Incident ID: {report.bundle.incident_id}",
        f"Routing target: {report.bundle.payload.routing_target}",
    ]
    if report.bundle.payload.recovery_request_id:
        lines.append(f"Recovery request ID: {report.bundle.payload.recovery_request_id}")
    typer.echo("\n".join(lines))


@app.callback()
def root(
    ctx: typer.Context,
    config_path: Annotated[
        Path,
        typer.Option(
            "--config",
            file_okay=True,
            dir_okay=False,
            resolve_path=True,
            help="Path to millrace.toml.",
        ),
    ] = Path("millrace.toml"),
) -> None:
    """Prepare CLI-local config context."""

    ctx.obj = CLIContext(config_path=config_path)


@app.command("init")
def init_command(
    destination: Annotated[
        Path,
        typer.Argument(
            file_okay=False,
            dir_okay=True,
            resolve_path=True,
            help="Destination workspace directory.",
        ),
    ],
    force: Annotated[
        bool,
        typer.Option(
            "--force",
            help="Allow a non-empty destination and overwrite manifest-tracked files.",
        ),
    ] = False,
    json_mode: Annotated[bool, typer.Option("--json", help="Render JSON output.")] = False,
) -> None:
    """Initialize a workspace from the packaged baseline bundle."""

    try:
        result = EngineControl.init_workspace(destination, force=force)
    except ControlError as exc:
        raise typer.BadParameter(str(exc), param_hint="destination") from exc
    render_operation(result, json_mode=json_mode)


@app.command("upgrade")
def upgrade_command(
    ctx: typer.Context,
    apply: Annotated[
        bool,
        typer.Option(
            "--apply",
            help="Apply the manifest-tracked baseline refresh instead of previewing it.",
        ),
    ] = False,
    json_mode: Annotated[bool, typer.Option("--json", help="Render JSON output.")] = False,
) -> None:
    """Preview or apply manifest-tracked baseline refresh for an existing workspace."""

    if apply:
        result = _run_expected(lambda: _control(ctx).apply_workspace_upgrade(), json_mode=json_mode)
        render_upgrade_apply(result, json_mode=json_mode)
        return

    result = _run_expected(lambda: _control(ctx).preview_workspace_upgrade(), json_mode=json_mode)
    render_upgrade_preview(result, json_mode=json_mode)


@app.command("health")
def health_command(
    ctx: typer.Context,
    json_mode: Annotated[bool, typer.Option("--json", help="Render JSON output.")] = False,
) -> None:
    """Run workspace bootstrap and health checks."""

    report = _run_expected(lambda: EngineControl.health_report(_config_path(ctx)), json_mode=json_mode)
    render_health(report, json_mode=json_mode)
    if report.status.value == "fail":
        raise typer.Exit(code=1)


@app.command("doctor")
def doctor_command(
    ctx: typer.Context,
    json_mode: Annotated[bool, typer.Option("--json", help="Render JSON output.")] = False,
) -> None:
    """Run operator preflight for bootstrap and execution readiness."""

    report = _run_expected(lambda: EngineControl.health_report(_config_path(ctx)), json_mode=json_mode)
    render_doctor(report, json_mode=json_mode)
    if not report.bootstrap_ready or not report.execution_ready:
        raise typer.Exit(code=1)


@app.command("start")
def start_command(
    ctx: typer.Context,
    daemon: Annotated[bool, typer.Option("--daemon", help="Run the foreground daemon loop.")] = False,
    once: Annotated[
        bool,
        typer.Option(
            "--once",
            help=(
                "Run one foreground pass. If startup research sync creates new execution backlog from an empty "
                "execution queue, stop after that research pass and run --once again to execute the new task."
            ),
        ),
    ] = False,
    json_mode: Annotated[bool, typer.Option("--json", help="Render JSON output.")] = False,
) -> None:
    """Start the runtime in foreground once or daemon mode."""

    if daemon and once:
        raise typer.BadParameter("use only one of --daemon or --once")
    report = _run_expected(lambda: _control(ctx).start(daemon=daemon, once=once), json_mode=json_mode)
    if json_mode:
        _json_output(report.model_dump(mode="json"))
        return
    typer.echo(
        "\n".join(
            [
                f"Process: {'running' if report.process_running else 'stopped'}",
                f"Paused: {'yes' if report.paused else 'no'}",
                f"Execution status: {report.execution_status.value}",
                f"Research status: {report.research_status.value}",
                *(
                    [
                        "Execution status detail: IDLE is the execution plane's neutral state "
                        "(no execution stage active); it does not mean the daemon is stopped."
                    ]
                    if report.execution_status.value == "IDLE"
                    else []
                ),
            ]
        )
    )


@app.command("stop")
def stop_command(ctx: typer.Context, json_mode: Annotated[bool, typer.Option("--json")] = False) -> None:
    """Request daemon stop."""

    render_operation(_run_expected(lambda: _control(ctx).stop(), json_mode=json_mode), json_mode=json_mode)


@app.command("pause")
def pause_command(ctx: typer.Context, json_mode: Annotated[bool, typer.Option("--json")] = False) -> None:
    """Request daemon pause."""

    render_operation(_run_expected(lambda: _control(ctx).pause(), json_mode=json_mode), json_mode=json_mode)


@app.command("resume")
def resume_command(ctx: typer.Context, json_mode: Annotated[bool, typer.Option("--json")] = False) -> None:
    """Request daemon resume."""

    render_operation(_run_expected(lambda: _control(ctx).resume(), json_mode=json_mode), json_mode=json_mode)


@app.command("status")
def status_command(
    ctx: typer.Context,
    detail: Annotated[bool, typer.Option("--detail", help="Include queue detail.")] = False,
    json_mode: Annotated[bool, typer.Option("--json", help="Render JSON output.")] = False,
) -> None:
    """Show runtime status."""

    render_status(
        _run_expected(lambda: _control(ctx).status(detail=detail), json_mode=json_mode),
        json_mode=json_mode,
    )


@app.command("run-provenance")
def run_provenance_command(
    ctx: typer.Context,
    run_id: Annotated[str, typer.Argument(help="Run identifier under agents/runs/.")],
    json_mode: Annotated[bool, typer.Option("--json", help="Render JSON output.")] = False,
) -> None:
    """Show compile-time and runtime provenance for one run."""

    report = _run_expected(lambda: _control(ctx).run_provenance(run_id), json_mode=json_mode)
    render_run_provenance(report, json_mode=json_mode)


@sentinel_app.command("check")
def sentinel_check_command(
    ctx: typer.Context,
    json_mode: Annotated[bool, typer.Option("--json", help="Render JSON output.")] = False,
) -> None:
    """Run one bounded Sentinel diagnostic pass and persist the result."""

    _render_sentinel_check(
        _run_expected(lambda: _control(ctx).sentinel_check(), json_mode=json_mode),
        json_mode=json_mode,
    )


@sentinel_app.command("status")
def sentinel_status_command(
    ctx: typer.Context,
    json_mode: Annotated[bool, typer.Option("--json", help="Render JSON output.")] = False,
) -> None:
    """Show the latest persisted Sentinel state and report."""

    _render_sentinel_status(
        _run_expected(lambda: _control(ctx).sentinel_status(), json_mode=json_mode),
        json_mode=json_mode,
    )


@sentinel_app.command("watch")
def sentinel_watch_command(
    ctx: typer.Context,
    max_checks: Annotated[
        int | None,
        typer.Option("--max-checks", min=1, help="Optional bounded loop count for tests or controlled runs."),
    ] = None,
    json_mode: Annotated[bool, typer.Option("--json", help="Render JSON output.")] = False,
) -> None:
    """Run the standalone Sentinel companion watch loop."""

    _render_sentinel_watch(
        _run_expected(lambda: _control(ctx).sentinel_watch(max_checks=max_checks), json_mode=json_mode),
        json_mode=json_mode,
    )


@sentinel_app.command("acknowledge")
def sentinel_acknowledge_command(
    ctx: typer.Context,
    issuer: Annotated[str, typer.Option("--issuer", help="Issuer identity to record.")],
    reason: Annotated[str, typer.Option("--reason", help="Why the Sentinel state is being acknowledged.")],
    json_mode: Annotated[bool, typer.Option("--json", help="Render JSON output.")] = False,
) -> None:
    """Explicitly acknowledge the current Sentinel soft-cap or escalation state."""

    render_operation(
        _run_expected(
            lambda: _control(ctx).sentinel_acknowledge(issuer=issuer, reason=reason),
            json_mode=json_mode,
        ),
        json_mode=json_mode,
    )


@sentinel_app.command("incident")
def sentinel_incident_command(
    ctx: typer.Context,
    failure_signature: Annotated[str, typer.Option("--failure-signature", help="Failure signature for the incident.")],
    summary: Annotated[str, typer.Option("--summary", help="One-line incident summary.")],
    severity: Annotated[str, typer.Option("--severity", help="Incident severity class (S1-S4).")] = "S2",
    routing_target: Annotated[
        RecoveryRequestTarget,
        typer.Option("--routing-target", help="Recovery route this incident points at."),
    ] = RecoveryRequestTarget.TROUBLESHOOT,
    evidence: Annotated[
        list[str] | None,
        typer.Option("--evidence", help="Repeatable evidence pointer path or token."),
    ] = None,
    recovery_request_id: Annotated[
        str,
        typer.Option("--recovery-request-id", help="Linked recovery request id, if already queued."),
    ] = "",
    suggested_recovery: Annotated[
        str,
        typer.Option("--suggested-recovery", help="Optional bounded suggested recovery text."),
    ] = "",
    issuer: Annotated[str, typer.Option("--issuer", help="Issuer identity to record.")] = "sentinel",
    json_mode: Annotated[bool, typer.Option("--json", help="Render JSON output.")] = False,
) -> None:
    """Generate one compatible Sentinel incident in the incoming incident queue."""

    _render_sentinel_incident(
        _run_expected(
            lambda: _control(ctx).sentinel_incident(
                failure_signature=failure_signature,
                summary=summary,
                severity=severity,
                routing_target=routing_target.value,
                evidence_pointers=tuple(evidence or ()),
                recovery_request_id=recovery_request_id,
                suggested_recovery=suggested_recovery,
                issuer=issuer,
            ),
            json_mode=json_mode,
        ),
        json_mode=json_mode,
    )


@recovery_app.command("request")
def recovery_request_command(
    ctx: typer.Context,
    target: Annotated[RecoveryRequestTarget, typer.Argument(help="Recovery entrypoint to request.")],
    issuer: Annotated[str, typer.Option("--issuer", help="Issuer identity to record.")],
    reason: Annotated[str, typer.Option("--reason", help="Why the out-of-order recovery path is being requested.")],
    force_queue: Annotated[
        bool,
        typer.Option(
            "--force-queue",
            help="Explicitly authorize this high-privilege out-of-order recovery request.",
        ),
    ] = False,
    json_mode: Annotated[bool, typer.Option("--json", help="Render JSON output.")] = False,
) -> None:
    """Queue one high-privilege manual recovery request."""

    if not force_queue:
        raise typer.BadParameter("pass --force-queue to authorize a manual recovery request")
    render_operation(
        _run_expected(
            lambda: _control(ctx).recovery_request(
                target.value,
                reason=reason,
                issuer=issuer,
                force_queue=True,
            ),
            json_mode=json_mode,
        ),
        json_mode=json_mode,
    )


@config_app.command("show")
def config_show_command(
    ctx: typer.Context,
    json_mode: Annotated[bool, typer.Option("--json", help="Render JSON output.")] = False,
) -> None:
    """Show the loaded config."""

    report: ConfigShowReport = _run_expected(lambda: _control(ctx).config_show(), json_mode=json_mode)
    if json_mode:
        _json_output(report.model_dump(mode="json"))
        return
    lines = [
        f"Source kind: {report.source.kind}",
        f"Primary path: {report.source.primary_path}",
        f"Config hash: {report.config_hash}",
        f"Execution integration mode: {report.config.execution.integration_mode}",
        f"Quickfix max attempts: {report.config.execution.quickfix_max_attempts}",
        f"Sizing mode: {report.config.sizing.mode}",
        (
            "Repo size thresholds: "
            f"files>={report.config.sizing.repo.file_count_threshold} "
            f"nonempty_lines>={report.config.sizing.repo.nonempty_line_count_threshold}"
        ),
        (
            "Task size thresholds: "
            f"files_to_touch>={report.config.sizing.task.file_count_threshold} "
            f"nonempty_lines>={report.config.sizing.task.nonempty_line_count_threshold} "
            "promotion=2-of-3(files, loc, complexity)"
        ),
        f"Research mode: {report.config.research.mode.value}",
    ]
    if report.source.secondary_paths:
        lines.append("Secondary source paths:")
        lines.extend(f"- {path}" for path in report.source.secondary_paths)
    if report.source.legacy_policy_compatibility is not None:
        lines.extend(_legacy_policy_lines(report.source.legacy_policy_compatibility))
    lines.extend(_legacy_unmapped_lines(report.source.unmapped_keys))
    lines.extend(_selection_explanation_lines(report.selection_explanation))
    lines.extend(_selection_lines(report.selection))
    lines.extend(_asset_inventory_lines(report.assets))
    typer.echo("\n".join(lines))


@config_app.command("set")
def config_set_command(
    ctx: typer.Context,
    key: Annotated[str, typer.Argument(help="Dotted config key.")],
    value: Annotated[str, typer.Argument(help="New value.")],
    json_mode: Annotated[bool, typer.Option("--json", help="Render JSON output.")] = False,
) -> None:
    """Set one dotted config key."""

    render_operation(
        _run_expected(lambda: _control(ctx).config_set(key, value), json_mode=json_mode),
        json_mode=json_mode,
    )


@config_app.command("reload")
def config_reload_command(
    ctx: typer.Context,
    json_mode: Annotated[bool, typer.Option("--json", help="Render JSON output.")] = False,
) -> None:
    """Reload config from disk."""

    render_operation(_run_expected(lambda: _control(ctx).config_reload(), json_mode=json_mode), json_mode=json_mode)


@queue_app.command("inspect")
def queue_inspect_command(
    ctx: typer.Context,
    json_mode: Annotated[bool, typer.Option("--json", help="Render JSON output.")] = False,
) -> None:
    """Show active and backlog task detail."""

    render_queue(
        _run_expected(lambda: _control(ctx).queue_inspect(), json_mode=json_mode),
        json_mode=json_mode,
        detail=True,
    )


@queue_app.command("reorder")
def queue_reorder_command(
    ctx: typer.Context,
    task_ids: Annotated[list[str], typer.Argument(help="Backlog task IDs in final order.")],
    json_mode: Annotated[bool, typer.Option("--json", help="Render JSON output.")] = False,
) -> None:
    """Rewrite the backlog order exactly as provided."""

    if not task_ids:
        raise typer.BadParameter("provide at least one task id to reorder")
    render_operation(
        _run_expected(lambda: _control(ctx).queue_reorder(task_ids), json_mode=json_mode),
        json_mode=json_mode,
    )


@queue_cleanup_app.command("remove")
def queue_cleanup_remove_command(
    ctx: typer.Context,
    task_id: Annotated[str, typer.Argument(help="Visible active or backlog task id to remove.")],
    reason: Annotated[
        str,
        typer.Option("--reason", help="Why the queued task is being removed."),
    ],
    json_mode: Annotated[bool, typer.Option("--json", help="Render JSON output.")] = False,
) -> None:
    """Remove one visible queued task and retain a bounded cleanup trail."""

    render_operation(
        _run_expected(
            lambda: _control(ctx).queue_cleanup_remove(task_id, reason=reason),
            json_mode=json_mode,
        ),
        json_mode=json_mode,
    )


@queue_cleanup_app.command("quarantine")
def queue_cleanup_quarantine_command(
    ctx: typer.Context,
    task_id: Annotated[str, typer.Argument(help="Visible active or backlog task id to quarantine.")],
    reason: Annotated[
        str,
        typer.Option("--reason", help="Why the queued task is being quarantined."),
    ],
    json_mode: Annotated[bool, typer.Option("--json", help="Render JSON output.")] = False,
) -> None:
    """Quarantine one visible queued task into backburner with a cleanup trail."""

    render_operation(
        _run_expected(
            lambda: _control(ctx).queue_cleanup_quarantine(task_id, reason=reason),
            json_mode=json_mode,
        ),
        json_mode=json_mode,
    )


@active_task_app.command("clear")
def active_task_clear_command(
    ctx: typer.Context,
    reason: Annotated[
        str,
        typer.Option("--reason", help="Why the active task should be cleared."),
    ],
    json_mode: Annotated[bool, typer.Option("--json", help="Render JSON output.")] = False,
) -> None:
    """Clear the visible active task through the supported remediation surface."""

    render_operation(
        _run_expected(
            lambda: _control(ctx).active_task_clear(reason=reason),
            json_mode=json_mode,
        ),
        json_mode=json_mode,
    )


@active_task_app.command("recover")
def active_task_recover_command(
    ctx: typer.Context,
    reason: Annotated[
        str,
        typer.Option("--reason", help="Why the active task should be recovered."),
    ],
    json_mode: Annotated[bool, typer.Option("--json", help="Render JSON output.")] = False,
) -> None:
    """Recover stale active-task state through the supported remediation surface."""

    render_operation(
        _run_expected(
            lambda: _control(ctx).active_task_recover(reason=reason),
            json_mode=json_mode,
        ),
        json_mode=json_mode,
    )


@queue_app.callback(invoke_without_command=True)
def queue_root(
    ctx: typer.Context,
    json_mode: Annotated[bool, typer.Option("--json", help="Render JSON output.")] = False,
) -> None:
    """Show queue summary."""

    if ctx.invoked_subcommand is not None:
        return
    render_queue(
        _run_expected(lambda: _control(ctx).queue(), json_mode=json_mode),
        json_mode=json_mode,
        detail=False,
    )


@compounding_app.callback(invoke_without_command=True)
def compounding_root(
    ctx: typer.Context,
    json_mode: Annotated[bool, typer.Option("--json", help="Render JSON output.")] = False,
) -> None:
    """Show one compact compounding governance summary."""

    if ctx.invoked_subcommand is not None:
        return
    report = _run_expected(lambda: _control(ctx).compounding_governance_summary(), json_mode=json_mode)
    render_compounding_governance_summary(report, json_mode=json_mode)


@compounding_app.command("orient")
def compounding_orient_command(
    ctx: typer.Context,
    query: Annotated[
        str | None,
        typer.Option("--query", help="Optional text filter over the derived orientation artifacts."),
    ] = None,
    json_mode: Annotated[bool, typer.Option("--json", help="Render JSON output.")] = False,
) -> None:
    """Generate and inspect derived compounding index and relationship summaries."""

    report: CompoundingOrientationReport = _run_expected(
        lambda: _control(ctx).compounding_orientation(query=query),
        json_mode=json_mode,
    )
    render_compounding_orientation(report, json_mode=json_mode)


@compounding_app.command("lint")
def compounding_lint_command(
    ctx: typer.Context,
    json_mode: Annotated[bool, typer.Option("--json", help="Render JSON output.")] = False,
) -> None:
    """Run governed-store integrity lint for compounding artifacts."""

    report: CompoundingIntegrityReport = _run_expected(
        lambda: _control(ctx).compounding_lint(),
        json_mode=json_mode,
    )
    render_compounding_lint(report, json_mode=json_mode)


@compounding_facts_app.command("show")
def compounding_context_fact_show_command(
    ctx: typer.Context,
    fact_id: Annotated[str, typer.Argument(help="Context fact id to inspect.")],
    json_mode: Annotated[bool, typer.Option("--json", help="Render JSON output.")] = False,
) -> None:
    """Show one governed durable context fact."""

    report: CompoundingContextFactReport = _run_expected(
        lambda: _control(ctx).compounding_context_fact(fact_id),
        json_mode=json_mode,
    )
    render_compounding_context_fact(report, json_mode=json_mode)


@compounding_facts_app.callback(invoke_without_command=True)
def compounding_context_facts_root(
    ctx: typer.Context,
    json_mode: Annotated[bool, typer.Option("--json", help="Render JSON output.")] = False,
) -> None:
    """Show governed durable context facts and retrieval status."""

    if ctx.invoked_subcommand is not None:
        return
    report: CompoundingContextFactListReport = _run_expected(
        lambda: _control(ctx).compounding_context_facts(),
        json_mode=json_mode,
    )
    render_compounding_context_facts(report, json_mode=json_mode)


@compounding_procedures_app.command("show")
def compounding_procedure_show_command(
    ctx: typer.Context,
    procedure_id: Annotated[str, typer.Argument(help="Procedure id to inspect.")],
    json_mode: Annotated[bool, typer.Option("--json", help="Render JSON output.")] = False,
) -> None:
    """Show one governed reusable procedure plus its lifecycle history."""

    report: CompoundingProcedureReport = _run_expected(
        lambda: _control(ctx).compounding_procedure(procedure_id),
        json_mode=json_mode,
    )
    render_compounding_procedure(report, json_mode=json_mode)


@compounding_procedures_app.command("promote")
def compounding_procedure_promote_command(
    ctx: typer.Context,
    procedure_id: Annotated[str, typer.Argument(help="Run-scoped or workspace procedure id to promote.")],
    reason: Annotated[str, typer.Option("--reason", help="Why this procedure is approved for broader reuse.")],
    changed_by: Annotated[
        str,
        typer.Option("--changed-by", help="Audit actor token for this review decision."),
    ] = "cli",
    json_mode: Annotated[bool, typer.Option("--json", help="Render JSON output.")] = False,
) -> None:
    """Promote one reusable procedure into workspace-scope governed reuse."""

    render_operation(
        _run_expected(
            lambda: _control(ctx).compounding_promote(
                procedure_id,
                changed_by=changed_by,
                reason=reason,
            ),
            json_mode=json_mode,
        ),
        json_mode=json_mode,
    )


@compounding_procedures_app.command("deprecate")
def compounding_procedure_deprecate_command(
    ctx: typer.Context,
    procedure_id: Annotated[str, typer.Argument(help="Workspace procedure id to deprecate.")],
    reason: Annotated[str, typer.Option("--reason", help="Why this procedure should be withheld.")],
    replacement_procedure_id: Annotated[
        str | None,
        typer.Option("--replacement-procedure-id", help="Optional replacement workspace procedure id."),
    ] = None,
    changed_by: Annotated[
        str,
        typer.Option("--changed-by", help="Audit actor token for this review decision."),
    ] = "cli",
    json_mode: Annotated[bool, typer.Option("--json", help="Render JSON output.")] = False,
) -> None:
    """Deprecate one workspace-scope reusable procedure."""

    render_operation(
        _run_expected(
            lambda: _control(ctx).compounding_deprecate(
                procedure_id,
                changed_by=changed_by,
                reason=reason,
                replacement_procedure_id=replacement_procedure_id,
            ),
            json_mode=json_mode,
        ),
        json_mode=json_mode,
    )


@compounding_procedures_app.callback(invoke_without_command=True)
def compounding_procedures_root(
    ctx: typer.Context,
    json_mode: Annotated[bool, typer.Option("--json", help="Render JSON output.")] = False,
) -> None:
    """Show governed reusable procedures and lifecycle status."""

    if ctx.invoked_subcommand is not None:
        return
    report: CompoundingProcedureListReport = _run_expected(
        lambda: _control(ctx).compounding_procedures(),
        json_mode=json_mode,
    )
    render_compounding_procedures(report, json_mode=json_mode)


@compounding_harness_candidates_app.command("show")
def compounding_harness_candidate_show_command(
    ctx: typer.Context,
    candidate_id: Annotated[str, typer.Argument(help="Harness candidate id to inspect.")],
    json_mode: Annotated[bool, typer.Option("--json", help="Render JSON output.")] = False,
) -> None:
    """Show one governed harness candidate plus recent benchmark history."""

    report: CompoundingHarnessCandidateReport = _run_expected(
        lambda: _control(ctx).compounding_harness_candidate(candidate_id),
        json_mode=json_mode,
    )
    render_compounding_harness_candidate(report, json_mode=json_mode)


@compounding_harness_candidates_app.callback(invoke_without_command=True)
def compounding_harness_candidates_root(
    ctx: typer.Context,
    json_mode: Annotated[bool, typer.Option("--json", help="Render JSON output.")] = False,
) -> None:
    """Show governed harness candidates."""

    if ctx.invoked_subcommand is not None:
        return
    report: CompoundingHarnessCandidateListReport = _run_expected(
        lambda: _control(ctx).compounding_harness_candidates(),
        json_mode=json_mode,
    )
    render_compounding_harness_candidates(report, json_mode=json_mode)


@compounding_harness_benchmarks_app.command("run")
def compounding_harness_benchmark_run_command(
    ctx: typer.Context,
    candidate_id: Annotated[str, typer.Argument(help="Harness candidate id to benchmark.")],
    json_mode: Annotated[bool, typer.Option("--json", help="Render JSON output.")] = False,
) -> None:
    """Run one bounded benchmark for a governed harness candidate."""

    render_operation(
        _run_expected(
            lambda: _control(ctx).compounding_harness_run_benchmark(candidate_id),
            json_mode=json_mode,
        ),
        json_mode=json_mode,
    )


@compounding_harness_benchmarks_app.command("show")
def compounding_harness_benchmark_show_command(
    ctx: typer.Context,
    result_id: Annotated[str, typer.Argument(help="Harness benchmark result id to inspect.")],
    json_mode: Annotated[bool, typer.Option("--json", help="Render JSON output.")] = False,
) -> None:
    """Show one persisted governed harness benchmark result."""

    report: CompoundingHarnessBenchmarkReport = _run_expected(
        lambda: _control(ctx).compounding_harness_benchmark(result_id),
        json_mode=json_mode,
    )
    render_compounding_harness_benchmark(report, json_mode=json_mode)


@compounding_harness_benchmarks_app.callback(invoke_without_command=True)
def compounding_harness_benchmarks_root(
    ctx: typer.Context,
    candidate_id: Annotated[
        str | None,
        typer.Option("--candidate-id", help="Optional harness candidate id filter."),
    ] = None,
    json_mode: Annotated[bool, typer.Option("--json", help="Render JSON output.")] = False,
) -> None:
    """Show persisted governed harness benchmark results."""

    if ctx.invoked_subcommand is not None:
        return
    report: CompoundingHarnessBenchmarkListReport = _run_expected(
        lambda: _control(ctx).compounding_harness_benchmarks(candidate_id=candidate_id),
        json_mode=json_mode,
    )
    render_compounding_harness_benchmarks(report, json_mode=json_mode)


@compounding_harness_search_app.command("run")
def compounding_harness_search_run_command(
    ctx: typer.Context,
    created_by: Annotated[
        str,
        typer.Option("--created-by", help="Issuer recorded on generated search and recommendation artifacts."),
    ] = "cli.search",
    json_mode: Annotated[bool, typer.Option("--json", help="Render JSON output.")] = False,
) -> None:
    """Run one bounded config/assets-only harness search."""

    render_operation(
        _run_expected(
            lambda: _control(ctx).compounding_harness_run_search(created_by=created_by),
            json_mode=json_mode,
        ),
        json_mode=json_mode,
    )


@compounding_harness_recommendations_app.command("show")
def compounding_harness_recommendation_show_command(
    ctx: typer.Context,
    recommendation_id: Annotated[str, typer.Argument(help="Harness recommendation id to inspect.")],
    json_mode: Annotated[bool, typer.Option("--json", help="Render JSON output.")] = False,
) -> None:
    """Show one persisted bounded harness recommendation."""

    report: CompoundingHarnessRecommendationReport = _run_expected(
        lambda: _control(ctx).compounding_harness_recommendation(recommendation_id),
        json_mode=json_mode,
    )
    render_compounding_harness_recommendation(report, json_mode=json_mode)


@compounding_harness_recommendations_app.callback(invoke_without_command=True)
def compounding_harness_recommendations_root(
    ctx: typer.Context,
    json_mode: Annotated[bool, typer.Option("--json", help="Render JSON output.")] = False,
) -> None:
    """Show persisted bounded harness recommendations."""

    if ctx.invoked_subcommand is not None:
        return
    report: CompoundingHarnessRecommendationListReport = _run_expected(
        lambda: _control(ctx).compounding_harness_recommendations(),
        json_mode=json_mode,
    )
    render_compounding_harness_recommendations(report, json_mode=json_mode)


@research_app.command("history")
def research_history_command(
    ctx: typer.Context,
    limit: Annotated[int, typer.Option("--limit", min=0, help="Number of recent research events to show.")] = 20,
    json_mode: Annotated[bool, typer.Option("--json", help="Render JSON output.")] = False,
) -> None:
    """Show recent research-related events."""

    render_log_events(
        _run_expected(lambda: _control(ctx).research_history(limit=limit), json_mode=json_mode),
        json_mode=json_mode,
    )


@research_app.callback(invoke_without_command=True)
def research_root(
    ctx: typer.Context,
    json_mode: Annotated[bool, typer.Option("--json", help="Render JSON output.")] = False,
) -> None:
    """Show research runtime visibility."""

    if ctx.invoked_subcommand is not None:
        return
    report = _run_expected(lambda: _control(ctx).research_report(), json_mode=json_mode)
    render_research_report(report, json_mode=json_mode)


@supervisor_app.command("report")
def supervisor_report_command(
    ctx: typer.Context,
    json_mode: Annotated[bool, typer.Option("--json", help="Render JSON output.")] = False,
    recent_events: Annotated[
        int,
        typer.Option("--recent-events", min=0, help="Number of recent events to include."),
    ] = 10,
) -> None:
    """Show one machine-readable workspace report for external supervisors."""

    report = _run_expected(
        lambda: _control(ctx).supervisor_report(recent_event_limit=recent_events),
        json_mode=json_mode,
    )
    render_supervisor_report(report, json_mode=json_mode)


@supervisor_app.command("pause")
def supervisor_pause_command(
    ctx: typer.Context,
    issuer: Annotated[str, typer.Option("--issuer", help="Supervisor issuer identity to record.")],
    json_mode: Annotated[bool, typer.Option("--json", help="Render JSON output.")] = False,
) -> None:
    """Queue a supervisor-attributed pause request."""

    render_operation(
        _run_expected(lambda: _control(ctx).supervisor_pause(issuer=issuer), json_mode=json_mode),
        json_mode=json_mode,
    )


@supervisor_app.command("resume")
def supervisor_resume_command(
    ctx: typer.Context,
    issuer: Annotated[str, typer.Option("--issuer", help="Supervisor issuer identity to record.")],
    json_mode: Annotated[bool, typer.Option("--json", help="Render JSON output.")] = False,
) -> None:
    """Queue a supervisor-attributed resume request."""

    render_operation(
        _run_expected(lambda: _control(ctx).supervisor_resume(issuer=issuer), json_mode=json_mode),
        json_mode=json_mode,
    )


@supervisor_app.command("stop")
def supervisor_stop_command(
    ctx: typer.Context,
    issuer: Annotated[str, typer.Option("--issuer", help="Supervisor issuer identity to record.")],
    json_mode: Annotated[bool, typer.Option("--json", help="Render JSON output.")] = False,
) -> None:
    """Queue a supervisor-attributed stop request."""

    render_operation(
        _run_expected(lambda: _control(ctx).supervisor_stop(issuer=issuer), json_mode=json_mode),
        json_mode=json_mode,
    )


@supervisor_app.command("add-task")
def supervisor_add_task_command(
    ctx: typer.Context,
    title: Annotated[str, typer.Argument(help="Task title.")],
    issuer: Annotated[str, typer.Option("--issuer", help="Supervisor issuer identity to record.")],
    body: Annotated[str | None, typer.Option("--body", help="Optional markdown body.")] = None,
    spec_id: Annotated[str | None, typer.Option("--spec-id", help="Optional spec identifier.")] = None,
    json_mode: Annotated[bool, typer.Option("--json", help="Render JSON output.")] = False,
) -> None:
    """Add one task through the supervisor-safe mutation path."""

    render_operation(
        _run_expected(
            lambda: _control(ctx).supervisor_add_task(
                title,
                issuer=issuer,
                body=body,
                spec_id=spec_id,
            ),
            json_mode=json_mode,
        ),
        json_mode=json_mode,
    )


@supervisor_app.command("queue-reorder")
def supervisor_queue_reorder_command(
    ctx: typer.Context,
    task_ids: Annotated[list[str], typer.Argument(help="Backlog task IDs in final order.")],
    issuer: Annotated[str, typer.Option("--issuer", help="Supervisor issuer identity to record.")],
    json_mode: Annotated[bool, typer.Option("--json", help="Render JSON output.")] = False,
) -> None:
    """Rewrite backlog order through the supervisor-safe mutation path."""

    if not task_ids:
        raise typer.BadParameter("provide at least one task id to reorder")
    render_operation(
        _run_expected(
            lambda: _control(ctx).supervisor_queue_reorder(task_ids, issuer=issuer),
            json_mode=json_mode,
        ),
        json_mode=json_mode,
    )


@supervisor_cleanup_app.command("remove")
def supervisor_queue_cleanup_remove_command(
    ctx: typer.Context,
    task_id: Annotated[str, typer.Argument(help="Visible active or backlog task id to remove.")],
    issuer: Annotated[str, typer.Option("--issuer", help="Supervisor issuer identity to record.")],
    reason: Annotated[
        str,
        typer.Option("--reason", help="Why the queued task is being removed."),
    ],
    json_mode: Annotated[bool, typer.Option("--json", help="Render JSON output.")] = False,
) -> None:
    """Remove one visible queued task through the supervisor-safe cleanup path."""

    render_operation(
        _run_expected(
            lambda: _control(ctx).supervisor_queue_cleanup_remove(
                task_id,
                reason=reason,
                issuer=issuer,
            ),
            json_mode=json_mode,
        ),
        json_mode=json_mode,
    )


@supervisor_cleanup_app.command("quarantine")
def supervisor_queue_cleanup_quarantine_command(
    ctx: typer.Context,
    task_id: Annotated[str, typer.Argument(help="Visible active or backlog task id to quarantine.")],
    issuer: Annotated[str, typer.Option("--issuer", help="Supervisor issuer identity to record.")],
    reason: Annotated[
        str,
        typer.Option("--reason", help="Why the queued task is being quarantined."),
    ],
    json_mode: Annotated[bool, typer.Option("--json", help="Render JSON output.")] = False,
) -> None:
    """Quarantine one visible queued task through the supervisor-safe cleanup path."""

    render_operation(
        _run_expected(
            lambda: _control(ctx).supervisor_queue_cleanup_quarantine(
                task_id,
                reason=reason,
                issuer=issuer,
            ),
            json_mode=json_mode,
        ),
        json_mode=json_mode,
    )


@supervisor_active_task_app.command("clear")
def supervisor_active_task_clear_command(
    ctx: typer.Context,
    issuer: Annotated[str, typer.Option("--issuer", help="Supervisor issuer identity to record.")],
    reason: Annotated[
        str,
        typer.Option("--reason", help="Why the active task should be cleared."),
    ],
    json_mode: Annotated[bool, typer.Option("--json", help="Render JSON output.")] = False,
) -> None:
    """Clear the visible active task through the supervisor-safe remediation surface."""

    render_operation(
        _run_expected(
            lambda: _control(ctx).supervisor_active_task_clear(reason=reason, issuer=issuer),
            json_mode=json_mode,
        ),
        json_mode=json_mode,
    )


@supervisor_active_task_app.command("recover")
def supervisor_active_task_recover_command(
    ctx: typer.Context,
    issuer: Annotated[str, typer.Option("--issuer", help="Supervisor issuer identity to record.")],
    reason: Annotated[
        str,
        typer.Option("--reason", help="Why the active task should be recovered."),
    ],
    json_mode: Annotated[bool, typer.Option("--json", help="Render JSON output.")] = False,
) -> None:
    """Recover stale active-task state through the supervisor-safe remediation surface."""

    render_operation(
        _run_expected(
            lambda: _control(ctx).supervisor_active_task_recover(reason=reason, issuer=issuer),
            json_mode=json_mode,
        ),
        json_mode=json_mode,
    )


@interview_app.command("list")
def interview_list_command(
    ctx: typer.Context,
    json_mode: Annotated[bool, typer.Option("--json", help="Render JSON output.")] = False,
) -> None:
    """List persisted manual interview questions."""

    _render_interview_list(
        _run_expected(lambda: _control(ctx).interview_list(), json_mode=json_mode),
        json_mode=json_mode,
    )


@interview_app.command("show")
def interview_show_command(
    ctx: typer.Context,
    question_id: Annotated[str, typer.Argument(help="Interview question identifier.")],
    json_mode: Annotated[bool, typer.Option("--json", help="Render JSON output.")] = False,
) -> None:
    """Show one interview question plus any recorded decision."""

    _render_interview_question(
        _run_expected(lambda: _control(ctx).interview_show(question_id), json_mode=json_mode),
        json_mode=json_mode,
    )


@interview_app.command("create")
def interview_create_command(
    ctx: typer.Context,
    source_path: Annotated[
        Path,
        typer.Argument(
            help="Staged idea or synthesized spec path to interview.",
        ),
    ],
    question: Annotated[str, typer.Option("--question", help="Interview question text.")],
    why_this_matters: Annotated[str, typer.Option("--why-this-matters", help="Why the question matters.")],
    recommended_answer: Annotated[
        str,
        typer.Option("--recommended-answer", help="Recommended answer to accept when appropriate."),
    ],
    answer_source: Annotated[
        str,
        typer.Option("--answer-source", help="Recommended answer provenance: repo, operator, or assumption."),
    ] = "assumption",
    blocking: Annotated[
        bool,
        typer.Option("--blocking/--non-blocking", help="Whether the question is blocking."),
    ] = True,
    evidence: Annotated[
        list[str],
        typer.Option("--evidence", help="Repeatable evidence note for the question artifact."),
    ] = [],
    json_mode: Annotated[bool, typer.Option("--json", help="Render JSON output.")] = False,
) -> None:
    """Create one pending interview question for a staged idea or synthesized spec."""

    _render_interview_mutation(
        _run_expected(
            lambda: _control(ctx).interview_create(
                source_path=source_path,
                question=question,
                why_this_matters=why_this_matters,
                recommended_answer=recommended_answer,
                answer_source=answer_source,
                blocking=blocking,
                evidence=evidence,
            ),
            json_mode=json_mode,
        ),
        json_mode=json_mode,
    )


@interview_app.command("answer")
def interview_answer_command(
    ctx: typer.Context,
    question_id: Annotated[str, typer.Argument(help="Interview question identifier.")],
    text: Annotated[str, typer.Option("--text", help="Operator answer to persist.")],
    evidence: Annotated[
        list[str],
        typer.Option("--evidence", help="Repeatable evidence note for the decision artifact."),
    ] = [],
    json_mode: Annotated[bool, typer.Option("--json", help="Render JSON output.")] = False,
) -> None:
    """Answer one pending interview question."""

    _render_interview_mutation(
        _run_expected(
            lambda: _control(ctx).interview_answer(question_id, text=text, evidence=evidence),
            json_mode=json_mode,
        ),
        json_mode=json_mode,
    )


@interview_app.command("accept")
def interview_accept_command(
    ctx: typer.Context,
    question_id: Annotated[str, typer.Argument(help="Interview question identifier.")],
    evidence: Annotated[
        list[str],
        typer.Option("--evidence", help="Repeatable evidence note for the decision artifact."),
    ] = [],
    json_mode: Annotated[bool, typer.Option("--json", help="Render JSON output.")] = False,
) -> None:
    """Accept the recommended answer for one pending interview question."""

    _render_interview_mutation(
        _run_expected(
            lambda: _control(ctx).interview_accept(question_id, evidence=evidence),
            json_mode=json_mode,
        ),
        json_mode=json_mode,
    )


@interview_app.command("skip")
def interview_skip_command(
    ctx: typer.Context,
    question_id: Annotated[str, typer.Argument(help="Interview question identifier.")],
    reason: Annotated[str | None, typer.Option("--reason", help="Optional skip reason.")] = None,
    evidence: Annotated[
        list[str],
        typer.Option("--evidence", help="Repeatable evidence note for the decision artifact."),
    ] = [],
    json_mode: Annotated[bool, typer.Option("--json", help="Render JSON output.")] = False,
) -> None:
    """Skip one pending interview question with an assumption record."""

    _render_interview_mutation(
        _run_expected(
            lambda: _control(ctx).interview_skip(question_id, reason=reason, evidence=evidence),
            json_mode=json_mode,
        ),
        json_mode=json_mode,
    )


@publish_app.command("sync")
def publish_sync_command(
    ctx: typer.Context,
    staging_repo_dir: Annotated[
        Path | None,
        typer.Option(
            "--staging-repo-dir",
            file_okay=False,
            dir_okay=True,
            resolve_path=True,
            help="Override the default staging repo directory.",
        ),
    ] = None,
    json_mode: Annotated[bool, typer.Option("--json", help="Render JSON output.")] = False,
) -> None:
    """Sync manifest-selected files into the staging repo."""

    report = _run_expected(
        lambda: _control(ctx).publish_sync(staging_repo_dir=staging_repo_dir),
        json_mode=json_mode,
    )
    render_staging_sync(report, json_mode=json_mode)


@publish_app.command("preflight")
def publish_preflight_command(
    ctx: typer.Context,
    staging_repo_dir: Annotated[
        Path | None,
        typer.Option(
            "--staging-repo-dir",
            file_okay=False,
            dir_okay=True,
            resolve_path=True,
            help="Override the default staging repo directory.",
        ),
    ] = None,
    commit_message: Annotated[
        str | None,
        typer.Option("--message", help="Commit message to evaluate."),
    ] = None,
    push: Annotated[
        bool,
        typer.Option("--push/--no-push", help="Check whether a publish push would run."),
    ] = False,
    json_mode: Annotated[bool, typer.Option("--json", help="Render JSON output.")] = False,
) -> None:
    """Show staging commit/publish readiness without mutating git state."""

    report = _run_expected(
        lambda: _control(ctx).publish_preflight(
            staging_repo_dir=staging_repo_dir,
            commit_message=commit_message,
            push=push,
        ),
        json_mode=json_mode,
    )
    render_publish_preflight(report, json_mode=json_mode)


@publish_app.command("commit")
def publish_commit_command(
    ctx: typer.Context,
    staging_repo_dir: Annotated[
        Path | None,
        typer.Option(
            "--staging-repo-dir",
            file_okay=False,
            dir_okay=True,
            resolve_path=True,
            help="Override the default staging repo directory.",
        ),
    ] = None,
    commit_message: Annotated[
        str | None,
        typer.Option("--message", help="Commit message to use."),
    ] = None,
    push: Annotated[
        bool,
        typer.Option("--push/--no-push", help="Push to origin after commit."),
    ] = False,
    json_mode: Annotated[bool, typer.Option("--json", help="Render JSON output.")] = False,
) -> None:
    """Commit staging changes and optionally push them."""

    report = _run_expected(
        lambda: _control(ctx).publish_commit(
            staging_repo_dir=staging_repo_dir,
            commit_message=commit_message,
            push=push,
        ),
        json_mode=json_mode,
    )
    render_publish_commit(report, json_mode=json_mode)


@app.command("add-task")
def add_task_command(
    ctx: typer.Context,
    title: Annotated[str, typer.Argument(help="Task title.")],
    body: Annotated[str | None, typer.Option("--body", help="Optional markdown body.")] = None,
    spec_id: Annotated[str | None, typer.Option("--spec-id", help="Optional spec identifier.")] = None,
    json_mode: Annotated[bool, typer.Option("--json", help="Render JSON output.")] = False,
) -> None:
    """Add one task card to the backlog."""

    render_operation(
        _run_expected(lambda: _control(ctx).add_task(title, body=body, spec_id=spec_id), json_mode=json_mode),
        json_mode=json_mode,
    )


@app.command("add-idea")
def add_idea_command(
    ctx: typer.Context,
    file: Annotated[Path, typer.Argument(exists=True, file_okay=True, dir_okay=False, resolve_path=True)],
    json_mode: Annotated[bool, typer.Option("--json", help="Render JSON output.")] = False,
) -> None:
    """Queue one idea file into `agents/ideas/raw/`."""

    render_operation(_run_expected(lambda: _control(ctx).add_idea(file), json_mode=json_mode), json_mode=json_mode)


@app.command("logs")
def logs_command(
    ctx: typer.Context,
    tail: Annotated[int, typer.Option("--tail", min=0, help="Number of recent events to show.")] = 50,
    follow: Annotated[bool, typer.Option("--follow", help="Stream new events as they arrive.")] = False,
    limit: Annotated[
        int | None,
        typer.Option("--limit", min=1, help="Maximum number of followed events before exiting."),
    ] = None,
    idle_timeout: Annotated[
        float | None,
        typer.Option("--idle-timeout", min=0.1, help="Stop follow mode after this many idle seconds."),
    ] = None,
    json_mode: Annotated[bool, typer.Option("--json", help="Render JSON output.")] = False,
) -> None:
    """Show recent structured runtime events."""

    control = _run_expected(lambda: _control(ctx), json_mode=json_mode)
    if not follow:
        render_log_events(_run_expected(lambda: control.logs(n=tail), json_mode=json_mode), json_mode=json_mode)
        return

    if tail > 0:
        for event in _run_expected(lambda: control.logs(n=tail), json_mode=json_mode):
            render_follow_event(event, json_mode=json_mode)

    followed = 0
    try:
        events = _run_expected(
            lambda: control.events_subscribe(
                start_at_end=tail > 0,
                idle_timeout_seconds=idle_timeout,
            ),
            json_mode=json_mode,
        )
        for event in _iter_expected(events, json_mode=json_mode):
            render_follow_event(event, json_mode=json_mode)
            followed += 1
            if limit is not None and followed >= limit:
                break
    except KeyboardInterrupt as exc:
        raise typer.Exit(code=0) from exc


def main() -> None:
    """Run the Typer app."""

    app()
