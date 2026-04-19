#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
SRC_ROOT = REPO / "src"
REQUIRED_STAGE_PATH = ("planner", "manager", "builder", "checker", "updater", "arbiter")
ARBITER_TERMINALS = {"ARBITER_COMPLETE", "REMEDIATION_NEEDED"}


def _load_millrace_symbols() -> tuple[type[Any], Any]:
    if str(SRC_ROOT) not in sys.path:
        sys.path.insert(0, str(SRC_ROOT))

    from millrace_ai.contracts import TaskDocument
    from millrace_ai.work_documents import render_work_document

    return TaskDocument, render_work_document


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run the canonical true E2E efficacy harness from the source checkout and write "
            "an efficacy report without treating intermediate BLOCKED stages as terminal."
        )
    )
    parser.add_argument("--workspace", type=Path, required=True, help="Workspace path to create/reset")
    parser.add_argument("--seed-prompt", type=Path, required=True, help="Seed idea markdown path")
    parser.add_argument("--mid-task-id", required=True, help="Task id for the seeded mid-execution task")
    parser.add_argument(
        "--report-path",
        type=Path,
        help="Optional explicit report path; defaults to <workspace>/e2e_efficacy_report.json",
    )
    parser.add_argument(
        "--timebox-minutes",
        type=int,
        default=90,
        help="Maximum runtime before the harness fails the run",
    )
    return parser.parse_args()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def run_cli(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    cmd = [sys.executable, "-m", "millrace_ai", *args]
    result = subprocess.run(cmd, cwd=repo, text=True, capture_output=True, check=False)
    if check and result.returncode != 0:
        raise RuntimeError(f"command failed: {cmd}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}")
    return result


def write_config(workspace: Path) -> None:
    config = """[runtime]
default_mode = "standard_plain"
run_style = "daemon"
idle_sleep_seconds = 0.5

[runners]
default_runner = "codex_cli"

[runners.codex]
command = "codex"
args = ["exec", "--ephemeral"]
permission_default = "basic"
permission_by_stage = { manager = "elevated", checker = "maximum" }
permission_by_model = { "gpt-5.4-mini" = "elevated" }
skip_git_repo_check = true

[watchers]
enabled = true
watch_ideas_inbox = true
watch_specs_queue = true
debounce_ms = 100

[stages.planner]
model = "gpt-5.4"
timeout_seconds = 900

[stages.manager]
model = "gpt-5.4"
timeout_seconds = 900

[stages.mechanic]
model = "gpt-5.4"
timeout_seconds = 900

[stages.auditor]
model = "gpt-5.4"
timeout_seconds = 900

[stages.builder]
model = "gpt-5.4-mini"
timeout_seconds = 900

[stages.checker]
model = "gpt-5.4-mini"
timeout_seconds = 900

[stages.fixer]
model = "gpt-5.4-mini"
timeout_seconds = 900

[stages.doublechecker]
model = "gpt-5.4-mini"
timeout_seconds = 900

[stages.updater]
model = "gpt-5.4-mini"
timeout_seconds = 900

[stages.troubleshooter]
model = "gpt-5.4-mini"
timeout_seconds = 900

[stages.consultant]
model = "gpt-5.4-mini"
timeout_seconds = 900

[stages.arbiter]
model = "gpt-5.4"
timeout_seconds = 900
"""
    (workspace / "millrace-agents" / "millrace.toml").write_text(config, encoding="utf-8")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def parse_events(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    items: list[dict[str, Any]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            items.append(json.loads(raw))
        except json.JSONDecodeError:
            continue
    return items


def queue_depths(workspace: Path) -> dict[str, int]:
    runtime_root = workspace / "millrace-agents"
    return {
        "tasks_queue": len(list((runtime_root / "tasks" / "queue").glob("*.md"))),
        "tasks_active": len(list((runtime_root / "tasks" / "active").glob("*.md"))),
        "specs_queue": len(list((runtime_root / "specs" / "queue").glob("*.md"))),
        "specs_active": len(list((runtime_root / "specs" / "active").glob("*.md"))),
        "incidents_incoming": len(list((runtime_root / "incidents" / "incoming").glob("*.md"))),
        "incidents_active": len(list((runtime_root / "incidents" / "active").glob("*.md"))),
    }


def task_state_counts(workspace: Path) -> dict[str, int]:
    tasks_root = workspace / "millrace-agents" / "tasks"
    return {
        "queue": len(list((tasks_root / "queue").glob("*.md"))),
        "active": len(list((tasks_root / "active").glob("*.md"))),
        "done": len(list((tasks_root / "done").glob("*.md"))),
        "blocked": len(list((tasks_root / "blocked").glob("*.md"))),
    }


def invalid_artifacts_present(workspace: Path) -> bool:
    runtime_root = workspace / "millrace-agents"
    for root in (runtime_root / "tasks", runtime_root / "specs", runtime_root / "incidents"):
        for path in root.rglob("*.invalid"):
            if path.is_file():
                return True
    return False


def stop_daemon(repo: Path, workspace: Path) -> None:
    try:
        run_cli(repo, "control", "stop", "--workspace", str(workspace), check=True)
    except Exception:
        pass


def list_relative_files(workspace: Path, root: Path, limit: int = 200) -> list[str]:
    if not root.exists():
        return []
    out: list[str] = []
    for path in sorted(root.rglob("*")):
        if path.is_file():
            out.append(str(path.relative_to(workspace)))
            if len(out) >= limit:
                break
    return out


def collect_work_docs(folder: Path, limit: int = 20) -> list[str]:
    if not folder.exists():
        return []
    return [path.name for path in sorted(folder.glob("*.md"))[:limit]]


def closure_target_states(workspace: Path) -> list[dict[str, Any]]:
    targets_dir = workspace / "millrace-agents" / "arbiter" / "targets"
    states: list[dict[str, Any]] = []
    if not targets_dir.exists():
        return states
    for path in sorted(targets_dir.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            states.append(payload)
    return states


def arbiter_incident_records(workspace: Path) -> list[dict[str, Any]]:
    incidents_root = workspace / "millrace-agents" / "incidents"
    records: list[dict[str, Any]] = []
    for bucket in ("incoming", "active", "resolved"):
        folder = incidents_root / bucket
        if not folder.exists():
            continue
        for path in sorted(folder.glob("*.md")):
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            if "Source-Stage: arbiter" not in text and not path.name.startswith("arbiter-gap-"):
                continue
            records.append(
                {
                    "path": str(path.relative_to(workspace)),
                    "bucket": bucket,
                    "has_root_spec_id": "Root-Spec-ID:" in text,
                    "has_root_idea_id": "Root-Idea-ID:" in text,
                    "has_source_stage": "Source-Stage: arbiter" in text,
                }
            )
    return records


def collect_blocked_stage_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()

    for event in events:
        if event.get("event_type") != "stage_completed":
            continue
        data = event.get("data", {})
        if str(data.get("terminal_result", "")) != "BLOCKED":
            continue
        record = {
            "run_id": str(data.get("run_id", "")),
            "stage": str(data.get("stage", "")),
            "work_item_kind": str(data.get("work_item_kind", "")),
            "work_item_id": str(data.get("work_item_id", "")),
            "failure_class": str(data.get("failure_class", "")),
        }
        key = (
            record["run_id"],
            record["stage"],
            record["work_item_kind"],
            record["work_item_id"],
        )
        if key in seen:
            continue
        seen.add(key)
        records.append(record)
    return records


def build_mid_task(mid_task_id: str) -> Any:
    task_document_type, _ = _load_millrace_symbols()
    return task_document_type(
        task_id=mid_task_id,
        title="Mid Execution Seeded Task (Efficacy)",
        summary="Create deliverables/mid-seeded-ack.md containing exactly `mid seeded efficacy ok`.",
        target_paths=("deliverables/mid-seeded-ack.md",),
        acceptance=("File exists with exact literal content.",),
        required_checks=("cat deliverables/mid-seeded-ack.md",),
        references=("runtime mailbox E2E efficacy",),
        risk=("low",),
        created_at=datetime.now(timezone.utc),
        created_by="e2e-efficacy-harness",
    )


def main() -> int:
    args = parse_args()
    workspace = args.workspace.expanduser().resolve()
    seed_prompt = args.seed_prompt.expanduser().resolve()
    report_path = (args.report_path.expanduser().resolve() if args.report_path else workspace / "e2e_efficacy_report.json")
    daemon_log_path = workspace / "daemon.log"

    if not seed_prompt.exists():
        raise FileNotFoundError(f"missing seed prompt: {seed_prompt}")

    if workspace.exists():
        shutil.rmtree(workspace)
    workspace.mkdir(parents=True, exist_ok=True)

    print(f"[{now_iso()}] bootstrap workspace: {workspace}", flush=True)
    run_cli(REPO, "status", "show", "--workspace", str(workspace), check=True)
    write_config(workspace)

    daemon_cmd = [sys.executable, "-m", "millrace_ai", "run", "daemon", "--workspace", str(workspace)]
    with daemon_log_path.open("w", encoding="utf-8") as handle:
        daemon = subprocess.Popen(daemon_cmd, cwd=REPO, text=True, stdout=handle, stderr=subprocess.STDOUT)

    snapshot_path = workspace / "millrace-agents" / "state" / "runtime_snapshot.json"
    events_path = workspace / "millrace-agents" / "logs" / "runtime_events.jsonl"

    start_deadline = time.time() + 60
    while time.time() < start_deadline:
        if daemon.poll() is not None:
            raise RuntimeError(f"daemon exited early with code {daemon.returncode}")
        if snapshot_path.exists() and read_json(snapshot_path).get("process_running"):
            break
        time.sleep(0.5)
    else:
        raise RuntimeError("daemon did not reach running state")

    print(f"[{now_iso()}] daemon started", flush=True)

    idea_seed = workspace / "seed-idea.md"
    shutil.copyfile(seed_prompt, idea_seed)
    add_idea = run_cli(REPO, "queue", "add-idea", str(idea_seed), "--workspace", str(workspace), check=True)
    print(f"[{now_iso()}] seeded efficacy idea via queue add-idea", flush=True)

    mid_task_seed_path = workspace / "seed-mid-task.md"
    _, render_work_document = _load_millrace_symbols()
    mid_task_seed_path.write_text(render_work_document(build_mid_task(args.mid_task_id)), encoding="utf-8")

    seen_stages: list[str] = []
    seen_keys: set[tuple[str, str]] = set()
    stage_counts: dict[str, int] = {}
    arbiter_terminal_result: str | None = None
    arbiter_started_queue_depths: dict[str, int] | None = None
    seeded_mid_task = False
    last_print = 0.0

    report: dict[str, Any] = {
        "workspace": str(workspace),
        "started_at": now_iso(),
        "daemon_pid": daemon.pid,
        "seed_prompt_source": str(seed_prompt),
        "add_idea_stdout": add_idea.stdout,
        "add_idea_stderr": add_idea.stderr,
    }

    deadline = time.time() + (args.timebox_minutes * 60)
    failure_reason: str | None = None

    try:
        while time.time() < deadline:
            if daemon.poll() is not None:
                failure_reason = f"daemon exited unexpectedly with code {daemon.returncode}"
                break

            events = parse_events(events_path)
            for event in events:
                if event.get("event_type") == "stage_started":
                    data = event.get("data", {})
                    stage = str(data.get("stage", ""))
                    work_id = str(data.get("work_item_id", ""))
                    stage_counts[stage] = stage_counts.get(stage, 0) + 1
                    key = (stage, work_id)
                    if key not in seen_keys:
                        seen_keys.add(key)
                        seen_stages.append(stage)
                    if stage == "arbiter" and arbiter_started_queue_depths is None:
                        arbiter_started_queue_depths = queue_depths(workspace)
                    continue

                if event.get("event_type") == "stage_completed":
                    data = event.get("data", {})
                    stage = str(data.get("stage", ""))
                    terminal = str(data.get("terminal_result", ""))
                    if stage == "arbiter" and terminal:
                        arbiter_terminal_result = terminal

            blocked_stage_events = collect_blocked_stage_events(events)
            intermediate_blocked_events = [
                event for event in blocked_stage_events if event["stage"] != "arbiter"
            ]
            if arbiter_terminal_result == "BLOCKED":
                failure_reason = "arbiter reported BLOCKED"
                break

            if invalid_artifacts_present(workspace):
                failure_reason = "invalid queue artifacts (*.invalid) observed"
                break

            if (not seeded_mid_task) and any(stage in seen_stages for stage in ("manager", "builder")):
                add_task = run_cli(REPO, "queue", "add-task", str(mid_task_seed_path), "--workspace", str(workspace), check=True)
                report["add_mid_task_stdout"] = add_task.stdout
                report["add_mid_task_stderr"] = add_task.stderr
                seeded_mid_task = True
                print(f"[{now_iso()}] seeded mid-execution task via queue add-task", flush=True)

            depths = queue_depths(workspace)
            task_counts = task_state_counts(workspace)
            mid_done = (workspace / "millrace-agents" / "tasks" / "done" / f"{args.mid_task_id}.md").is_file()
            current_time = time.time()
            if current_time - last_print > 10:
                last_print = current_time
                print(
                    f"[{now_iso()}] progress unique_stages={seen_stages} "
                    f"stage_counts={stage_counts} depths={depths} tasks={task_counts} "
                    f"mid_task_done={mid_done} arbiter_terminal={arbiter_terminal_result} "
                    f"intermediate_blocked_events={len(intermediate_blocked_events)}",
                    flush=True,
                )

            if arbiter_terminal_result in ARBITER_TERMINALS:
                if arbiter_terminal_result == "REMEDIATION_NEEDED" and not arbiter_incident_records(workspace):
                    time.sleep(1.0)
                    continue
                report["completion_detected_at"] = now_iso()
                report["final_queue_depths"] = depths
                break

            time.sleep(1.0)
        else:
            failure_reason = "deadline exceeded before completion criteria"
    finally:
        stop_daemon(REPO, workspace)
        wait_deadline = time.time() + 120
        while time.time() < wait_deadline:
            code = daemon.poll()
            if code is not None:
                report["daemon_exit_code"] = code
                break
            time.sleep(0.5)
        else:
            daemon.terminate()
            time.sleep(1)
            if daemon.poll() is None:
                daemon.kill()
            report["daemon_exit_code"] = daemon.wait(timeout=20)
            report["daemon_forced_shutdown"] = True

    events = parse_events(events_path)
    blocked_stage_events = collect_blocked_stage_events(events)
    intermediate_blocked_events = [event for event in blocked_stage_events if event["stage"] != "arbiter"]
    report["ended_at"] = now_iso()
    report["seen_stages_order"] = seen_stages
    report["stage_counts"] = stage_counts
    report["arbiter_terminal_result"] = arbiter_terminal_result
    report["arbiter_started_queue_depths"] = arbiter_started_queue_depths
    report["failure_reason"] = failure_reason
    report["blocked_stage_events"] = blocked_stage_events
    report["intermediate_blocked_events"] = intermediate_blocked_events

    counts: dict[str, int] = {}
    for event in events:
        event_type = str(event.get("event_type", "unknown"))
        counts[event_type] = counts.get(event_type, 0) + 1
    report["event_counts"] = counts

    completion_files = sorted((workspace / "millrace-agents" / "runs").glob("**/runner_completion.*.json"))
    completions = [json.loads(path.read_text(encoding="utf-8")) for path in completion_files]
    bad_completions = [
        record
        for record in completions
        if record.get("exit_kind") != "completed" or record.get("exit_code") not in (0, None) or record.get("failure_class")
    ]

    daemon_log = daemon_log_path.read_text(encoding="utf-8") if daemon_log_path.exists() else ""
    daemon_error_lines = [line for line in daemon_log.splitlines() if "error:" in line.lower()]

    report["runner_completion_count"] = len(completions)
    report["runner_failed_completions"] = bad_completions
    report["daemon_log_tail"] = "\n".join(daemon_log.splitlines()[-100:])
    report["daemon_error_lines"] = daemon_error_lines

    runtime_root = workspace / "millrace-agents"
    closure_states = closure_target_states(workspace)
    arbiter_incidents = arbiter_incident_records(workspace)
    arbiter_rubrics = list_relative_files(workspace, runtime_root / "arbiter" / "rubrics")
    arbiter_verdicts = list_relative_files(workspace, runtime_root / "arbiter" / "verdicts")
    arbiter_reports = list_relative_files(workspace, runtime_root / "arbiter" / "reports")
    run_artifacts_snapshot = list_relative_files(workspace, runtime_root / "runs")

    report["doc_inventory"] = {
        "specs_done": collect_work_docs(runtime_root / "specs" / "done"),
        "tasks_done": collect_work_docs(runtime_root / "tasks" / "done"),
        "tasks_blocked": collect_work_docs(runtime_root / "tasks" / "blocked"),
        "tasks_queue": collect_work_docs(runtime_root / "tasks" / "queue"),
        "incidents_incoming": collect_work_docs(runtime_root / "incidents" / "incoming"),
        "incidents_active": collect_work_docs(runtime_root / "incidents" / "active"),
        "incidents_resolved": collect_work_docs(runtime_root / "incidents" / "resolved"),
    }
    report["deliverables_files"] = list_relative_files(workspace, workspace / "deliverables")
    report["arbiter_artifacts"] = {
        "rubrics": arbiter_rubrics,
        "verdicts": arbiter_verdicts,
        "reports": arbiter_reports,
        "run_artifacts_snapshot": run_artifacts_snapshot[:200],
    }
    report["closure_target_states"] = closure_states
    report["arbiter_incidents"] = arbiter_incidents

    mid_task_mailbox_applied = any(
        event.get("event_type") == "mailbox_add_task_applied"
        and event.get("data", {}).get("task_id") == args.mid_task_id
        for event in events
    )

    pass_outcome = arbiter_terminal_result == "ARBITER_COMPLETE"
    remediation_outcome = arbiter_terminal_result == "REMEDIATION_NEEDED"
    closure_closed = bool(closure_states) and all(not bool(state.get("closure_open", True)) for state in closure_states)
    remediation_incident_valid = bool(arbiter_incidents) and all(
        bool(item.get("has_root_spec_id")) and bool(item.get("has_root_idea_id")) and bool(item.get("has_source_stage"))
        for item in arbiter_incidents
    )
    arbiter_outputs_present = bool(arbiter_rubrics) and bool(arbiter_verdicts)
    outcome_behavior_valid = (pass_outcome and closure_closed) or (remediation_outcome and remediation_incident_valid)

    report["assertions"] = {
        "required_stage_path_seen": all(stage in seen_stages for stage in REQUIRED_STAGE_PATH),
        "idea_mailbox_applied": any(event.get("event_type") == "mailbox_add_idea_applied" for event in events),
        "idea_normalized_to_spec": any(event.get("event_type") == "idea_normalized_to_spec" for event in events),
        "mid_task_mailbox_applied": mid_task_mailbox_applied,
        "mid_task_done_file": (workspace / "millrace-agents" / "tasks" / "done" / f"{args.mid_task_id}.md").is_file(),
        "arbiter_terminal_observed": arbiter_terminal_result in ARBITER_TERMINALS,
        "arbiter_started_after_queue_drain": (
            isinstance(arbiter_started_queue_depths, dict)
            and arbiter_started_queue_depths.get("tasks_queue", 1) == 0
            and arbiter_started_queue_depths.get("tasks_active", 1) == 0
            and arbiter_started_queue_depths.get("specs_queue", 1) == 0
            and arbiter_started_queue_depths.get("specs_active", 1) == 0
        ),
        "arbiter_outputs_present": arbiter_outputs_present,
        "arbiter_outcome_behavior_valid": outcome_behavior_valid,
        "no_invalid_artifacts": not invalid_artifacts_present(workspace),
        "no_runner_failures": len(bad_completions) == 0,
        "no_daemon_error_lines": len(daemon_error_lines) == 0,
        "add_idea_mailbox_mode": "mode: mailbox" in report.get("add_idea_stdout", ""),
        "add_task_mailbox_mode": "mode: mailbox" in report.get("add_mid_task_stdout", ""),
        "no_failure_reason": failure_reason is None,
    }

    report["success"] = all(bool(value) for value in report["assertions"].values())

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"[{now_iso()}] report written: {report_path}", flush=True)
    print(f"[{now_iso()}] success={report['success']}", flush=True)

    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
