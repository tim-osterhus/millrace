"""Thin CLI over the shared request protocol and authoritative projections."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import NoReturn

from . import control
from .control_requests import INSTANCE_OPS, PLANE_OPS, STOPS, envelope
from .execution import AdapterResolver
from .ownership import canonical_path, new_id
from .records import RouterError, mapping, strict_json, text

OPS = {
    "start": "start_run",
    "recover": "recover_run",
    "pause": "pause_run",
    "cancel": "cancel_run",
    "resume": "resume_run",
    "submit": "submit_revision",
    "accept": "accept_revision",
    "retry": "retry_invocation",
    "pause-plane": "pause_plane",
    "resume-plane": "resume_plane",
    "cancel-plane": "cancel_plane",
    "redirect-plane": "redirect_plane",
    "reconcile": "reconcile_invocation",
}


class Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise RouterError("usage_error", message)


def _home(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--state-home", type=Path, default=argparse.SUPPRESS)


def _mutation(parser: argparse.ArgumentParser, *, version: bool = True) -> None:
    parser.add_argument("--request-id")
    if version:
        parser.add_argument("--expected-state-version", type=int)


def _request(
    args: argparse.Namespace,
    home: Path,
    *,
    adapter_resolver: AdapterResolver | None = None,
) -> dict[str, object]:
    request_id = new_id() if args.request_id is None else args.request_id
    if args.command == "instance" and args.instance_command == "create":
        return {
            "format": "millrace-router-request/v2",
            "operation": "create_instance",
            "instance_id": None,
            "run_id": None,
            "plane_id": None,
            "request_id": request_id,
            "expected_state_version": args.expected_state_version
            if args.expected_state_version is not None
            else 0,
            "payload": {
                "workflow_file": str(canonical_path(args.workflow)),
                "project_root": str(canonical_path(args.project)),
                "path": list(
                    dict.fromkeys(
                        [str(Path(sys.executable).parent), *os.get_exec_path()]
                    )
                ),
                "env_allowlist": [],
                "host_paths": {
                    text(key): text(value)
                    for key, value in mapping(strict_json(args.host_paths)).items()
                },
            },
        }
    if args.command == "release-project-hold":
        return {
            "format": "millrace-router-request/v2",
            "operation": "release_project_hold",
            "instance_id": args.owner_instance,
            "run_id": args.owner_run,
            "plane_id": None,
            "request_id": request_id,
            "expected_state_version": None,
            "payload": {
                "project_root": str(canonical_path(args.project)),
                "hold_id": args.hold,
                "reason": args.reason,
                "stopped": args.stopped,
            },
        }
    op = "update_instance_config" if args.command == "instance" else OPS[args.command]
    run = None if op in INSTANCE_OPS else args.run
    if op in STOPS:
        version = None
    else:
        version = args.expected_state_version
        if version is None:
            view = (
                control.inspect_instance(
                    args.instance, state_home=home, adapter_resolver=adapter_resolver
                )
                if run is None
                else control.inspect_run(
                    args.instance,
                    run,
                    state_home=home,
                    adapter_resolver=adapter_resolver,
                )
            )
            version = view["state_version"]
    payload: dict[str, object] = {}
    if op == "start_run":
        payload["edit_mode"] = args.edit_mode
    elif op == "update_instance_config":
        payload["settings"] = mapping(strict_json(args.settings))
    elif op == "accept_revision":
        payload["candidate_id"] = args.candidate
    elif op in {"retry_invocation", "reconcile_invocation", "redirect_plane"}:
        payload["node_id" if op == "redirect_plane" else "invocation_id"] = (
            args.node if op == "redirect_plane" else args.invocation
        )
        payload["reason"] = args.reason
        if op == "reconcile_invocation":
            payload["stopped"] = args.stopped
    return envelope(
        op,
        args.instance,
        run,
        request_id,
        version,
        payload,
        plane_id=args.plane if op in PLANE_OPS else None,
    )


def main(
    argv: Sequence[str] | None = None,
    *,
    adapter_resolver: AdapterResolver | None = None,
) -> int:
    parser = Parser(
        prog="millrace router",
        description=(
            "Standalone command router. Any queue-draining mutator may become the "
            "foreground controller until pause/end. "
            "First SIGINT or SIGTERM pauses after active work, up to its node "
            "timeout or earlier run deadline. "
            "Second stop requests cancellation. Retain the printed request "
            "envelope for exact replay after unknown transport outcome; "
            "queued requests await their holder, and definite refusals require "
            "fresh explicit intent."
        ),
    )
    _home(parser)
    commands = parser.add_subparsers(dest="command", required=True)
    instance = commands.add_parser("instance")
    _home(instance)
    instance_commands = instance.add_subparsers(dest="instance_command", required=True)
    for name in ("create", "list", "inspect", "config"):
        child = instance_commands.add_parser(name)
        _home(child)
        if name in {"inspect", "config"}:
            child.add_argument("--instance", required=True)
        if name == "create":
            child.add_argument("--workflow", type=Path, required=True)
            child.add_argument("--project", type=Path, required=True)
            child.add_argument("--host-paths", default="{}", metavar="JSON")
        if name == "config":
            child.add_argument("--settings", required=True)
        if name in {"create", "config"}:
            _mutation(child)
    for name, op in OPS.items():
        child = commands.add_parser(name)
        _home(child)
        child.add_argument("--instance", required=True)
        if op not in INSTANCE_OPS:
            child.add_argument("--run", required=True)
        if op in PLANE_OPS:
            child.add_argument("--plane", required=True)
        _mutation(child, version=op not in STOPS)
        if name == "start":
            child.add_argument(
                "--edit-mode", choices=("locked", "live"), default="locked"
            )
        if name == "accept":
            child.add_argument("--candidate", required=True)
        if name in {"retry", "reconcile"}:
            child.add_argument("--invocation", required=True)
        if name == "redirect-plane":
            child.add_argument("--node", required=True)
        if name in {"retry", "reconcile", "redirect-plane"}:
            child.add_argument("--reason", required=True)
        if name == "reconcile":
            child.add_argument("--stopped", action="store_true", required=True)
    for name in ("validate", "status"):
        child = commands.add_parser(name)
        _home(child)
        child.add_argument("--instance", required=True)
        if name == "status":
            child.add_argument("--run", required=True)
        else:
            child.add_argument("--run")
            child.add_argument("--workflow", type=Path)
    replay = commands.add_parser("replay")
    _home(replay)
    replay.add_argument("--envelope", required=True)
    release = commands.add_parser("release-project-hold")
    _home(release)
    release.add_argument("--project", type=Path, required=True)
    release.add_argument("--hold", required=True)
    release.add_argument("--owner-instance", required=True)
    release.add_argument("--owner-run", required=True)
    release.add_argument("--reason", required=True)
    release.add_argument("--stopped", action="store_true", required=True)
    _mutation(release, version=False)
    try:
        args = parser.parse_args(argv)
        if args.command == "replay" and not hasattr(args, "state_home"):
            raise RouterError("state_home_required")
        home = getattr(args, "state_home", Path.home() / ".local/state/millrace-router")
        if args.command == "status":
            result: object = control.inspect_run(
                args.instance,
                args.run,
                state_home=home,
                adapter_resolver=adapter_resolver,
            )
        elif args.command == "validate":
            result = control.validate_revision(
                args.instance,
                state_home=home,
                run_id=args.run,
                workflow_file=args.workflow,
                adapter_resolver=adapter_resolver,
            )
        elif args.command == "instance" and args.instance_command in {
            "list",
            "inspect",
        }:
            result = (
                control.list_instances(
                    state_home=home, adapter_resolver=adapter_resolver
                )
                if args.instance_command == "list"
                else control.inspect_instance(
                    args.instance, state_home=home, adapter_resolver=adapter_resolver
                )
            )
        else:
            request = (
                mapping(strict_json(args.envelope))
                if args.command == "replay"
                else _request(args, home, adapter_resolver=adapter_resolver)
            )
            print(json.dumps({"request_envelope": request}), flush=True)
            result = control.execute_request(
                request,
                state_home=home,
                on_started=lambda ids: print(json.dumps(ids), flush=True),
                adapter_resolver=adapter_resolver,
            )
        print(json.dumps(result), flush=True)
        if isinstance(result, dict) and result.get("status") == "refused":
            print(
                json.dumps({"code": result["code"], "message": result["code"]}),
                file=sys.stderr,
            )
            return 2
        return 0
    except RouterError as exc:
        print(json.dumps({"code": exc.code, "message": str(exc)}), file=sys.stderr)
        return 2
    except OSError as exc:
        print(
            json.dumps({"code": "io_error", "message": type(exc).__name__}),
            file=sys.stderr,
        )
        return 2


def cli() -> None:
    """Console entrypoint retaining the explicit router namespace."""
    import sys

    args = sys.argv[1:]
    if not args or args[0] != "router":
        print("usage: millrace router ...", file=sys.stderr)
        raise SystemExit(2)
    raise SystemExit(main(args[1:]))


if __name__ == "__main__":
    raise SystemExit(main())
