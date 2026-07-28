"""Thin public CLI parser and command dispatcher."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from importlib import metadata
from typing import NoReturn

from millrace.adapters.cli.output import (
    ExitCode,
    error_result,
    render_error,
    render_success,
    success_result,
)

PACKAGE_VERSION = metadata.version("millrace-ai")
DEFAULT_ACTOR_ID = "local_operator"
COMMAND_NOT_IMPLEMENTED = "Command is not implemented."
HELP_FLAGS = ("--help", "-h")
GLOBAL_OPTIONS_WITH_VALUES = {
    "--workspace",
    "--db",
    "--cas",
    "--actor-id",
    "--command-id",
    "--input-id",
    "--resource-root",
    "--from-path",
    "--output",
    "--workflow-id",
    "--workflow-version",
    "--entrypoint",
    "--compiled-plan-json",
    "--payload-json",
    "--payload-file",
    "--plan-fingerprint",
    "--claim-id",
    "--reason",
    "--quarantine-id",
    "--lineage-id",
    "--max-events",
    "--idle-sleep",
    "--max-ticks",
    "--activation-id",
    "--adapter-kind",
    "--adapter-config-json",
    "--monitor",
}
GLOBAL_FLAG_OPTIONS = {
    "--json",
    "--no-color",
    "--version",
    "--payload-stdin",
}
LOCKED_GROUPS = (
    "workspace",
    "package",
    "plan",
    "queue",
    "status",
    "runs",
    "trace",
    "waits",
    "interventions",
    "dispatch",
    "doctor",
    "run",
)
GROUP_HELP = {
    "workspace": "Initialize and inspect local runtime storage.",
    "package": "Import, inspect, verify, and manage workflow packages.",
    "plan": "Admit, select, and inspect compiled plans.",
    "queue": "Enqueue work and inspect selected queue families.",
    "status": "Project current workspace status.",
    "runs": "Inspect runtime runs.",
    "trace": "Inspect governance and execution traces.",
    "waits": "Inspect and resolve operator waits.",
    "interventions": "Inspect and apply operator interventions.",
    "dispatch": "Claim work and inspect dispatch envelopes.",
    "doctor": "Inspect workspace health.",
    "run": "Run the local execution daemon.",
}


class CliArgumentError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class CliParserExit(Exception):
    def __init__(self, status: int) -> None:
        super().__init__(status)
        self.status = status


class MillraceArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise CliArgumentError(message)

    def exit(self, status: int = 0, message: str | None = None) -> NoReturn:
        if message:
            self._print_message(message, sys.stderr)
        raise CliParserExit(status)


def main(argv: Sequence[str] | None = None) -> int:
    args = list(argv) if argv is not None else sys.argv[1:]
    json_mode = "--json" in args
    parser, help_parsers = _build_parser()

    if json_mode and _has_help_flag(args):
        return _render_json_help(args, parser=parser, help_parsers=help_parsers)

    try:
        namespace = parser.parse_args(args)
    except CliParserExit as exc:
        return exc.status
    except CliArgumentError as exc:
        return _render_cli_usage_error(exc.message, json_mode=json_mode)
    except Exception:
        return _render_internal_error(json_mode=json_mode)

    command = "version" if namespace.version else _command_from_namespace(namespace)
    actor_id = namespace.actor_id
    if not actor_id.strip():
        error = error_result(
            command=command,
            code="invalid_actor_id",
            message="--actor-id must be nonblank.",
            exit_code=ExitCode.CLI_USAGE,
        )
        return render_error(error, json_mode=namespace.json)

    if namespace.version:
        return _render_version(json_mode=namespace.json)

    if command == "cli":
        if namespace.json:
            return render_success(
                success_result(
                    command="cli",
                    code="help",
                    message="Help for millrace.",
                    data={"help": parser.format_help()},
                ),
                json_mode=True,
            )
        parser.print_help()
        return int(ExitCode.SUCCESS)

    if command.startswith("workspace."):
        return _dispatch_workspace(namespace)
    if command.startswith("package."):
        return _dispatch_package(namespace)
    if command.startswith("plan."):
        return _dispatch_plan(namespace)
    if command.startswith("queue."):
        return _dispatch_queue(namespace)
    if command in {"status", "waits.list", "interventions.list"} or command.startswith(
        ("runs.", "trace.")
    ):
        return _dispatch_status(namespace)
    if command.startswith(("waits.", "interventions.")):
        return _dispatch_intervention(namespace)
    if command.startswith("dispatch."):
        return _dispatch_dispatch(namespace)
    if command == "doctor":
        return _dispatch_doctor(namespace)
    if command.startswith("run."):
        return _dispatch_run(namespace)

    error = error_result(
        command=command,
        code="command_not_implemented",
        message=COMMAND_NOT_IMPLEMENTED,
        exit_code=ExitCode.DOMAIN_REFUSAL,
    )
    return render_error(error, json_mode=namespace.json)


def cli() -> None:
    raise SystemExit(main())


def _build_parser() -> tuple[
    MillraceArgumentParser,
    dict[str, argparse.ArgumentParser],
]:
    parser = MillraceArgumentParser(
        prog="millrace",
        description="Millrace local operator command line.",
    )
    _add_global_options(parser)
    subparsers = parser.add_subparsers(dest="group", metavar="command")
    help_parsers: dict[str, argparse.ArgumentParser] = {"cli": parser}

    for group in LOCKED_GROUPS:
        group_parser = subparsers.add_parser(group, help=GROUP_HELP[group])
        group_parser.set_defaults(command=group)
        help_parsers[group] = group_parser
        if group == "workspace":
            _add_workspace_commands(group_parser, help_parsers)
        elif group == "package":
            _add_package_commands(group_parser, help_parsers)
        elif group == "plan":
            _add_plan_commands(group_parser, help_parsers)
        elif group == "queue":
            _add_queue_commands(group_parser, help_parsers)
        elif group == "status":
            _add_status_options(group_parser)
        elif group == "runs":
            _add_runs_commands(group_parser, help_parsers)
        elif group == "trace":
            _add_trace_commands(group_parser, help_parsers)
        elif group == "waits":
            _add_wait_commands(group_parser, help_parsers)
        elif group == "interventions":
            _add_intervention_commands(group_parser, help_parsers)
        elif group == "dispatch":
            _add_dispatch_commands(group_parser, help_parsers)
        elif group == "run":
            run_subparsers = group_parser.add_subparsers(
                dest="run_command",
                metavar="command",
            )
            daemon_parser = run_subparsers.add_parser(
                "daemon",
                help="Run the local bounded execution daemon.",
            )
            daemon_parser.add_argument(
                "--idle-sleep",
                type=float,
                default=1.0,
                metavar="SECONDS",
                help="Seconds to sleep after an idle tick.",
            )
            daemon_parser.add_argument(
                "--max-ticks",
                type=int,
                metavar="N",
                help="Maximum bounded-unit calls before stopping.",
            )
            daemon_parser.add_argument(
                "--activation-id",
                metavar="ID",
                help="Explicit active activation retry; requires --max-ticks 1.",
            )
            daemon_parser.add_argument(
                "--adapter-kind",
                metavar="KIND",
                help="Require a selected local adapter kind.",
            )
            daemon_parser.add_argument(
                "--adapter-config-json",
                metavar="PATH",
                help="Path to local runner adapter JSON config.",
            )
            daemon_parser.add_argument(
                "--monitor",
                choices=("none", "basic"),
                default="none",
                help="Bounded local progress presentation.",
            )
            daemon_parser.set_defaults(command="run.daemon")
            help_parsers["run.daemon"] = daemon_parser

    parser.set_defaults(command="cli")
    return parser, help_parsers


def _add_workspace_commands(
    group_parser: argparse.ArgumentParser,
    help_parsers: dict[str, argparse.ArgumentParser],
) -> None:
    subparsers = group_parser.add_subparsers(
        dest="workspace_command",
        metavar="command",
    )
    init_parser = subparsers.add_parser(
        "init",
        help="Initialize or validate the local runtime store and CAS root.",
    )
    init_parser.add_argument(
        "--input-id",
        required=True,
        metavar="ID",
        help="Explicit replay-safe transition input ID for workspace init.",
    )
    init_parser.set_defaults(command="workspace.init")
    help_parsers["workspace.init"] = init_parser

    check_parser = subparsers.add_parser(
        "check",
        help="Read-only runtime store and CAS status check.",
    )
    check_parser.set_defaults(command="workspace.check")
    help_parsers["workspace.check"] = check_parser


def _add_package_commands(
    group_parser: argparse.ArgumentParser,
    help_parsers: dict[str, argparse.ArgumentParser],
) -> None:
    subparsers = group_parser.add_subparsers(
        dest="package_command",
        metavar="command",
    )

    import_path = subparsers.add_parser(
        "import-path",
        help="Import a workflow package from a local package root.",
    )
    import_path.add_argument("path", metavar="PATH")
    _add_command_id_option(import_path)
    import_path.set_defaults(command="package.import-path")
    help_parsers["package.import-path"] = import_path

    import_archive = subparsers.add_parser(
        "import-archive",
        help="Import a workflow package archive from a local path.",
    )
    import_archive.add_argument("path", metavar="PATH")
    _add_command_id_option(import_archive)
    import_archive.set_defaults(command="package.import-archive")
    help_parsers["package.import-archive"] = import_archive

    import_installed = subparsers.add_parser(
        "import-installed",
        help="Import an installed Python distribution workflow package.",
    )
    import_installed.add_argument("distribution", metavar="DISTRIBUTION")
    import_installed.add_argument(
        "--resource-root",
        required=True,
        metavar="ROOT",
        help="Installed distribution package-resource root.",
    )
    _add_command_id_option(import_installed)
    import_installed.set_defaults(command="package.import-installed")
    help_parsers["package.import-installed"] = import_installed

    update = subparsers.add_parser(
        "update",
        help="Update a workflow package from a local package root.",
    )
    _add_package_identity_arguments(update)
    update.add_argument(
        "--from-path",
        required=True,
        metavar="PATH",
        help="Package root containing the updated package source.",
    )
    _add_command_id_option(update)
    update.set_defaults(command="package.update")
    help_parsers["package.update"] = update

    list_parser = subparsers.add_parser(
        "list",
        help="List current workflow package registry records.",
    )
    _add_command_id_option(list_parser)
    list_parser.set_defaults(command="package.list")
    help_parsers["package.list"] = list_parser

    inspect = subparsers.add_parser(
        "inspect",
        help="Inspect one workflow package registry record.",
    )
    _add_package_identity_arguments(inspect)
    _add_command_id_option(inspect)
    inspect.set_defaults(command="package.inspect")
    help_parsers["package.inspect"] = inspect

    export_archive = subparsers.add_parser(
        "export-archive",
        help="Export a workflow package archive to a local path.",
    )
    _add_package_identity_arguments(export_archive)
    export_archive.add_argument(
        "--output",
        required=True,
        metavar="PATH",
        help="Archive output path. Archive bytes are never written to stdout.",
    )
    _add_command_id_option(export_archive)
    export_archive.set_defaults(command="package.export-archive")
    help_parsers["package.export-archive"] = export_archive

    for name in ("enable", "disable", "remove"):
        parser = subparsers.add_parser(
            name,
            help=f"{name.title()} one workflow package version.",
        )
        _add_package_identity_arguments(parser)
        _add_command_id_option(parser)
        parser.set_defaults(command=f"package.{name}")
        help_parsers[f"package.{name}"] = parser

    verify = subparsers.add_parser(
        "verify",
        help="Verify a workflow selection without admitting a plan.",
    )
    _add_package_identity_arguments(verify)
    _add_workflow_selection_arguments(verify)
    _add_command_id_option(verify)
    verify.set_defaults(command="package.verify")
    help_parsers["package.verify"] = verify

    select_workflow = subparsers.add_parser(
        "select-workflow",
        help="Compile a workflow package selection without admitting it.",
    )
    _add_package_identity_arguments(select_workflow)
    _add_workflow_selection_arguments(select_workflow)
    _add_command_id_option(select_workflow)
    select_workflow.set_defaults(command="package.select-workflow")
    help_parsers["package.select-workflow"] = select_workflow

    doctor = subparsers.add_parser(
        "doctor",
        help="Project read-only workflow package health diagnostics.",
    )
    _add_package_identity_arguments(doctor)
    _add_command_id_option(doctor)
    doctor.set_defaults(command="package.doctor")
    help_parsers["package.doctor"] = doctor


def _add_plan_commands(
    group_parser: argparse.ArgumentParser,
    help_parsers: dict[str, argparse.ArgumentParser],
) -> None:
    subparsers = group_parser.add_subparsers(dest="plan_command", metavar="command")

    admit = subparsers.add_parser(
        "admit",
        help="Admit a verified compiled-plan export through a control transition.",
    )
    admit.add_argument(
        "--compiled-plan-json",
        required=True,
        metavar="PATH",
        help="Path to canonical compiled-plan export JSON.",
    )
    _add_input_id_option(admit)
    admit.set_defaults(command="plan.admit")
    help_parsers["plan.admit"] = admit

    admit_package = subparsers.add_parser(
        "admit-package",
        help="Compile a workflow package selection and admit the selected plan.",
    )
    _add_package_identity_arguments(admit_package)
    _add_workflow_selection_arguments(admit_package)
    _add_command_id_option(admit_package)
    _add_input_id_option(admit_package)
    admit_package.set_defaults(command="plan.admit-package")
    help_parsers["plan.admit-package"] = admit_package

    select_default = subparsers.add_parser(
        "select-default",
        help="Select an admitted plan as the workspace default.",
    )
    select_default.add_argument("fingerprint", metavar="FINGERPRINT")
    _add_input_id_option(select_default)
    select_default.set_defaults(command="plan.select-default")
    help_parsers["plan.select-default"] = select_default

    show = subparsers.add_parser(
        "show",
        help="Show admitted plan metadata without raw selected-plan JSON.",
    )
    show.add_argument("fingerprint", nargs="?", metavar="FINGERPRINT")
    show.set_defaults(command="plan.show")
    help_parsers["plan.show"] = show


def _add_queue_commands(
    group_parser: argparse.ArgumentParser,
    help_parsers: dict[str, argparse.ArgumentParser],
) -> None:
    subparsers = group_parser.add_subparsers(dest="queue_command", metavar="command")

    enqueue = subparsers.add_parser(
        "enqueue",
        help="Enqueue local operator work through selected workflow authority.",
    )
    enqueue.add_argument("queue_family", metavar="QUEUE_FAMILY")
    _add_payload_source_options(enqueue)
    enqueue.add_argument(
        "--plan-fingerprint",
        metavar="FINGERPRINT",
        help="Require the selected plan authority fingerprint.",
    )
    _add_optional_input_id_option(enqueue)
    enqueue.set_defaults(command="queue.enqueue")
    help_parsers["queue.enqueue"] = enqueue

    list_parser = subparsers.add_parser(
        "list",
        help="List selected-plan queue family status.",
    )
    list_parser.set_defaults(command="queue.list")
    help_parsers["queue.list"] = list_parser


def _add_status_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--plan-fingerprint",
        metavar="FINGERPRINT",
        help="Project status for one admitted plan authority fingerprint.",
    )
    _add_max_events_option(parser)


def _add_runs_commands(
    group_parser: argparse.ArgumentParser,
    help_parsers: dict[str, argparse.ArgumentParser],
) -> None:
    subparsers = group_parser.add_subparsers(dest="runs_command", metavar="command")

    list_parser = subparsers.add_parser("list", help="List active runs.")
    list_parser.set_defaults(command="runs.list")
    help_parsers["runs.list"] = list_parser

    show = subparsers.add_parser("show", help="Show one run.")
    show.add_argument("run_id", metavar="RUN_ID")
    show.set_defaults(command="runs.show")
    help_parsers["runs.show"] = show

    cancel = subparsers.add_parser(
        "cancel",
        help="Request cancellation of the current runner session.",
    )
    cancel.add_argument("run_id", metavar="RUN_ID")
    cancel.add_argument(
        "--input-id",
        required=True,
        metavar="ID",
        help="Replay-safe durable cancellation request ID.",
    )
    cancel.set_defaults(command="runs.cancel")
    help_parsers["runs.cancel"] = cancel


def _add_trace_commands(
    group_parser: argparse.ArgumentParser,
    help_parsers: dict[str, argparse.ArgumentParser],
) -> None:
    subparsers = group_parser.add_subparsers(dest="trace_command", metavar="command")

    show = subparsers.add_parser("show", help="Show recent or run-specific trace.")
    show.add_argument("run_id", nargs="?", metavar="RUN_ID")
    _add_max_events_option(show)
    show.set_defaults(command="trace.show")
    help_parsers["trace.show"] = show


def _add_wait_commands(
    group_parser: argparse.ArgumentParser,
    help_parsers: dict[str, argparse.ArgumentParser],
) -> None:
    subparsers = group_parser.add_subparsers(dest="waits_command", metavar="command")

    list_parser = subparsers.add_parser("list", help="List operator waits.")
    list_parser.set_defaults(command="waits.list")
    help_parsers["waits.list"] = list_parser

    for name in ("resume", "close", "revise"):
        parser = subparsers.add_parser(name, help=f"{name.title()} an operator wait.")
        parser.add_argument("wait_id", metavar="WAIT_ID")
        _add_optional_payload_json(parser)
        _add_optional_input_id_option(parser)
        parser.set_defaults(command=f"waits.{name}")
        help_parsers[f"waits.{name}"] = parser


def _add_intervention_commands(
    group_parser: argparse.ArgumentParser,
    help_parsers: dict[str, argparse.ArgumentParser],
) -> None:
    subparsers = group_parser.add_subparsers(
        dest="interventions_command",
        metavar="command",
    )

    list_parser = subparsers.add_parser(
        "list",
        help="List operator interventions.",
    )
    list_parser.set_defaults(command="interventions.list")
    help_parsers["interventions.list"] = list_parser

    for name in ("resume-lineage", "close-lineage", "revise-lineage"):
        parser = subparsers.add_parser(
            name,
            help=f"{name.replace('-', ' ').title()} through operator intake.",
        )
        parser.add_argument("option_id", metavar="OPTION_ID")
        parser.add_argument("--quarantine-id", metavar="ID")
        parser.add_argument("--lineage-id", metavar="ID")
        parser.add_argument("--reason", required=True, metavar="TEXT")
        _add_optional_payload_json(parser)
        _add_optional_input_id_option(parser)
        parser.set_defaults(command=f"interventions.{name}")
        help_parsers[f"interventions.{name}"] = parser


def _add_dispatch_commands(
    group_parser: argparse.ArgumentParser,
    help_parsers: dict[str, argparse.ArgumentParser],
) -> None:
    subparsers = group_parser.add_subparsers(dest="dispatch_command", metavar="command")

    claim = subparsers.add_parser(
        "claim",
        help="Claim one ready activation and render its dispatch envelope.",
    )
    claim.add_argument("activation_id", metavar="ACTIVATION_ID")
    claim.add_argument("--claim-id", metavar="CLAIM_ID")
    _add_optional_input_id_option(claim)
    claim.set_defaults(command="dispatch.claim")
    help_parsers["dispatch.claim"] = claim

    show = subparsers.add_parser(
        "show",
        help="Show a read-only dispatch envelope for one claimed run.",
    )
    show.add_argument("run_id", metavar="RUN_ID")
    show.set_defaults(command="dispatch.show")
    help_parsers["dispatch.show"] = show


def _add_payload_source_options(parser: argparse.ArgumentParser) -> None:
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--payload-json", metavar="JSON")
    group.add_argument("--payload-file", metavar="PATH")
    group.add_argument("--payload-stdin", action="store_true")


def _add_optional_payload_json(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--payload-json", metavar="JSON")


def _add_optional_input_id_option(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--input-id",
        metavar="ID",
        help="Optional replay-safe transition input ID.",
    )


def _add_max_events_option(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--max-events",
        type=int,
        default=20,
        metavar="N",
        help="Maximum recent governance/trace events to project.",
    )


def _add_package_identity_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("package_id", metavar="PACKAGE_ID")
    parser.add_argument("package_version", metavar="PACKAGE_VERSION")


def _add_workflow_selection_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--workflow-id", required=True, metavar="ID")
    parser.add_argument("--workflow-version", required=True, metavar="VERSION")
    parser.add_argument("--entrypoint", required=True, metavar="NAME")


def _add_command_id_option(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--command-id",
        required=True,
        metavar="ID",
        help="Explicit replay-safe package command ID.",
    )


def _add_input_id_option(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--input-id",
        required=True,
        metavar="ID",
        help="Explicit replay-safe transition input ID.",
    )


def _add_global_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--version",
        action="store_true",
        help="Print the Millrace CLI version and exit.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Render one JSON object for success or expected failure.",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        help="Accepted no-op for deterministic terminals.",
    )
    parser.add_argument(
        "--workspace",
        metavar="PATH",
        help="Workspace root for local runtime operations.",
    )
    parser.add_argument(
        "--db",
        metavar="PATH",
        help="SQLite runtime database path.",
    )
    parser.add_argument(
        "--cas",
        metavar="PATH",
        help="Content-addressed storage root.",
    )
    parser.add_argument(
        "--actor-id",
        default=DEFAULT_ACTOR_ID,
        metavar="ACTOR",
        help="Local operator actor ID recorded by audited commands.",
    )


def _render_version(*, json_mode: bool) -> int:
    return render_success(
        success_result(
            command="version",
            code="ok",
            message=f"millrace {PACKAGE_VERSION}",
            data={"version": PACKAGE_VERSION},
        ),
        json_mode=json_mode,
    )


def _render_json_help(
    args: Sequence[str],
    *,
    parser: MillraceArgumentParser,
    help_parsers: dict[str, argparse.ArgumentParser],
) -> int:
    command = _help_command_from_args(args)
    try:
        namespace = parser.parse_args(_without_help_flags(args))
    except CliArgumentError as exc:
        return _render_cli_usage_error(exc.message, json_mode=True)
    except CliParserExit as exc:
        return exc.status

    actor_id = namespace.actor_id
    if not actor_id.strip():
        error = error_result(
            command=command,
            code="invalid_actor_id",
            message="--actor-id must be nonblank.",
            exit_code=ExitCode.CLI_USAGE,
        )
        return render_error(error, json_mode=True)

    help_parser = help_parsers.get(command, help_parsers["cli"])
    return render_success(
        success_result(
            command=command,
            code="help",
            message=f"Help for {help_parser.prog}.",
            data={"help": help_parser.format_help()},
        ),
        json_mode=True,
    )


def _has_help_flag(args: Sequence[str]) -> bool:
    return any(arg in HELP_FLAGS for arg in args)


def _without_help_flags(args: Sequence[str]) -> list[str]:
    return [arg for arg in args if arg not in HELP_FLAGS]


def _help_command_from_args(args: Sequence[str]) -> str:
    command_parts: list[str] = []
    skip_next = False
    for arg in args:
        if skip_next:
            skip_next = False
            continue
        if arg in HELP_FLAGS or arg in GLOBAL_FLAG_OPTIONS:
            continue
        if arg in GLOBAL_OPTIONS_WITH_VALUES:
            skip_next = True
            continue
        if arg.startswith("-"):
            continue
        command_parts.append(arg)
        if len(command_parts) == 2:
            break
        if command_parts[0] not in {
            "workspace",
            "package",
            "plan",
            "queue",
            "runs",
            "trace",
            "waits",
            "interventions",
            "dispatch",
            "run",
        }:
            break
    if command_parts == ["run", "daemon"]:
        return "run.daemon"
    if (
        len(command_parts) == 2
        and command_parts[0]
        in {
            "workspace",
            "package",
            "plan",
            "queue",
            "runs",
            "trace",
            "waits",
            "interventions",
            "dispatch",
        }
    ):
        return ".".join(command_parts)
    if len(command_parts) == 1 and command_parts[0] in LOCKED_GROUPS:
        return command_parts[0]
    return "cli"


def _command_from_namespace(namespace: argparse.Namespace) -> str:
    command = namespace.command
    if isinstance(command, str):
        return command
    return "cli"


def _render_cli_usage_error(message: str, *, json_mode: bool) -> int:
    error = error_result(
        command="cli",
        code="argument_parse_error",
        message=message,
        exit_code=ExitCode.CLI_USAGE,
    )
    return render_error(error, json_mode=json_mode)


def _render_internal_error(*, json_mode: bool) -> int:
    error = error_result(
        command="cli",
        code="internal_error",
        message="Unexpected internal CLI error.",
        exit_code=ExitCode.INTERNAL_ERROR,
    )
    return render_error(error, json_mode=json_mode)


def _dispatch_workspace(namespace: argparse.Namespace) -> int:
    try:
        from millrace.adapters.cli.workspace import handle_workspace_command

        result = handle_workspace_command(namespace)
    except Exception as exc:
        return _render_command_exception(
            exc,
            command=_command_from_namespace(namespace),
            json_mode=namespace.json,
        )
    return render_success(result, json_mode=namespace.json)


def _dispatch_package(namespace: argparse.Namespace) -> int:
    try:
        from millrace.adapters.cli.packages import handle_package_command

        result = handle_package_command(namespace)
    except Exception as exc:
        return _render_command_exception(
            exc,
            command=_command_from_namespace(namespace),
            json_mode=namespace.json,
        )
    return render_success(result, json_mode=namespace.json)


def _dispatch_plan(namespace: argparse.Namespace) -> int:
    try:
        from millrace.adapters.cli.plans import handle_plan_command

        result = handle_plan_command(namespace)
    except Exception as exc:
        return _render_command_exception(
            exc,
            command=_command_from_namespace(namespace),
            json_mode=namespace.json,
        )
    return render_success(result, json_mode=namespace.json)


def _dispatch_queue(namespace: argparse.Namespace) -> int:
    try:
        from millrace.adapters.cli.queue import handle_queue_command

        result = handle_queue_command(namespace)
    except Exception as exc:
        return _render_command_exception(
            exc,
            command=_command_from_namespace(namespace),
            json_mode=namespace.json,
        )
    return render_success(result, json_mode=namespace.json)


def _dispatch_status(namespace: argparse.Namespace) -> int:
    try:
        from millrace.adapters.cli.status import handle_status_command

        result = handle_status_command(namespace)
    except Exception as exc:
        return _render_command_exception(
            exc,
            command=_command_from_namespace(namespace),
            json_mode=namespace.json,
        )
    return render_success(result, json_mode=namespace.json)


def _dispatch_intervention(namespace: argparse.Namespace) -> int:
    try:
        from millrace.adapters.cli.interventions import handle_intervention_command

        result = handle_intervention_command(namespace)
    except Exception as exc:
        return _render_command_exception(
            exc,
            command=_command_from_namespace(namespace),
            json_mode=namespace.json,
        )
    return render_success(result, json_mode=namespace.json)


def _dispatch_dispatch(namespace: argparse.Namespace) -> int:
    try:
        from millrace.adapters.cli.dispatch import handle_dispatch_command

        result = handle_dispatch_command(namespace)
    except Exception as exc:
        return _render_command_exception(
            exc,
            command=_command_from_namespace(namespace),
            json_mode=namespace.json,
        )
    return render_success(result, json_mode=namespace.json)


def _dispatch_doctor(namespace: argparse.Namespace) -> int:
    try:
        from millrace.adapters.cli.doctor import handle_doctor_command

        result = handle_doctor_command(namespace)
    except Exception as exc:
        return _render_command_exception(
            exc,
            command=_command_from_namespace(namespace),
            json_mode=namespace.json,
        )
    return render_success(result, json_mode=namespace.json)


def _dispatch_run(namespace: argparse.Namespace) -> int:
    try:
        from millrace.adapters.cli.daemon import handle_daemon_command

        result = handle_daemon_command(namespace)
    except Exception as exc:
        return _render_command_exception(
            exc,
            command=_command_from_namespace(namespace),
            json_mode=namespace.json,
        )
    return render_success(result, json_mode=namespace.json)


def _render_command_exception(
    exc: Exception,
    *,
    command: str,
    json_mode: bool,
) -> int:
    from millrace.adapters.cli.context import CliCommandError
    from millrace.substrate.errors import (
        StoreNotInitialized,
        StoreSchemaUpgradeRequired,
        SubstrateError,
    )

    if isinstance(exc, CliCommandError):
        return render_error(exc.to_cli_error(), json_mode=json_mode)
    if isinstance(exc, StoreSchemaUpgradeRequired):
        error = error_result(
            command=command,
            code="workspace_upgrade_required",
            message="Workspace schema upgrade is required.",
            exit_code=ExitCode.PERSISTENCE_FAILURE,
            details={
                "current_schema_version": 6,
                "required_schema_version": 7,
            },
        )
        return render_error(error, json_mode=json_mode)
    if isinstance(exc, StoreNotInitialized):
        error = error_result(
            command=command,
            code="store_not_initialized",
            message="SQLite runtime store is not initialized.",
            exit_code=ExitCode.PERSISTENCE_FAILURE,
        )
        return render_error(error, json_mode=json_mode)
    if isinstance(exc, SubstrateError):
        error = error_result(
            command=command,
            code="substrate_error",
            message="Runtime store or CAS operation failed.",
            exit_code=ExitCode.PERSISTENCE_FAILURE,
            details={"error": str(exc)},
        )
        return render_error(error, json_mode=json_mode)
    return _render_internal_error(json_mode=json_mode)


if __name__ == "__main__":
    cli()
