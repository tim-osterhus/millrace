---
asset_type: skill
asset_id: millrace-ops-agent-manual
version: 1
description: External operator runbook for running, monitoring, and intervening in Millrace workspaces safely after Millrace use is requested or selected.
advisory_only: true
capability_type: operator_manual
forbidden_claims:
  - queue_selection
  - routing
  - retry_thresholds
  - escalation_policy
  - status_persistence
  - terminal_results
  - required_artifacts
---

# Millrace Operator

Use this skill when operating an initialized Millrace workspace, deciding how
to intake work after Millrace has been requested or selected, monitoring a
daemon, or intervening through supported operator controls.

This file is intentionally a compact router. Load the references below only
when the current operator action needs that detail.

## Core Rule

Operate Millrace through its supported CLI and runtime surfaces. Treat
`<workspace>/millrace-agents/` as runtime-owned state unless a documented
intake or operator-intervention command says otherwise. Do not invent queue
states, stage names, terminal results, recovery rules, routing semantics, or
status meanings.

Millrace is a governance layer over raw harnesses. It owns queue progression,
compiled plans, stage dispatch, runtime mutation, durable evidence, recovery
routing, and closure behavior. The external operator owns whether to delegate,
when to pause/resume/stop, and how to report the runtime's evidence truthfully.

## Load This Skill When

- The user asks you to operate, run, monitor, or troubleshoot Millrace.
- An active delegation policy selected Millrace for the work.
- You need to intake a task, probe, spec, or idea into a Millrace workspace.
- You are checking status for a workspace with `millrace-agents/`.
- You need supported intervention commands for paused, blocked, stale, or
  unhealthy runtime state.

Do not load it just because a repository contains Millrace. Direct source edits
do not automatically require the Millrace operator posture.

## Autonomy Handshake

If `millrace-autonomous-delegation` or an equivalent user/workspace policy is
already active, follow it and do not ask again.

If no Millrace delegation policy exists and Millrace was not explicitly
requested, ask once:

1. May I use Millrace at my own discretion when it is a good fit?
2. Should I suggest Millrace and wait for approval before using it?
3. Should I use Millrace only when explicitly requested?

Until answered, behave as option 2. Keep the user's answer stable for the
current thread or workspace unless they change it.

## Quick Start

1. Confirm Millrace is requested, selected, or permitted.
2. If the work is only a candidate, use the fit test in
   `references/intake-and-delegation.md`.
3. Validate or initialize the workspace:

```bash
millrace init --workspace <workspace>
millrace compile validate --workspace <workspace>
millrace status --workspace <workspace>
millrace queue ls --workspace <workspace>
```

4. Intake work through typed commands, not arbitrary queue-folder edits.
5. Use one bounded tick for cautious progression:

```bash
millrace run daemon --max-ticks 1 --workspace <workspace>
```

6. Use an unbounded daemon only when long-running operation is intended:

```bash
millrace run daemon --monitor basic --workspace <workspace>
```

If persistence beyond the current harness matters, run the daemon in `tmux`.
Use `--monitor-log <path>` when a durable human-facing monitor stream is
needed.

## Reference Map

- `references/intake-and-delegation.md`: fit test, autonomy posture, source
  boundaries, self-contained intake, and workspace-memory rules.
- `references/command-baseline.md`: command forms, package upgrade versus
  workspace upgrade, queue/incident/control/skills/model-alias commands.
- `references/modes-and-configuration.md`: `0.21.x` graph-authority contract,
  shipped LAD/Blueprint/Learning modes, config reload, runners, model aliases,
  capabilities, Pi, and usage governance.
- `references/monitoring-and-intervention.md`: status/runs/watch rhythm,
  basic monitor behavior, read-only `millrace-web`, approval flow, and operator
  intervention commands.
- `references/recovery-and-blueprint.md`: runtime error reports, retryable
  blockers, closure/Arbiter freshness, Blueprint diagnostics, and recovery
  pitfalls.
- `references/release-and-verification.md`: release-oriented source checks,
  workspace health claims, and evidence required before saying work progressed.

## Minimal Operating Commands

```bash
millrace version
millrace compile validate --workspace <workspace>
millrace compile show --workspace <workspace>
millrace compile graph --workspace <workspace>
millrace status --workspace <workspace>
millrace queue ls --workspace <workspace>
millrace runs ls --workspace <workspace>
millrace runs show <run_id> --workspace <workspace>
millrace runs trace <run_id> --workspace <workspace>
millrace doctor --workspace <workspace>
```

For source-development shells, module form is acceptable:

```bash
uv run --extra dev python -m millrace_ai <command>
```

In installed environments, prefer:

```bash
millrace <command>
```

## Monitoring Rhythm

Use this sequence before diagnosing by hand:

1. `millrace status --workspace <workspace>`
2. `millrace queue ls --workspace <workspace>`
3. `millrace runs ls --workspace <workspace>`
4. `millrace runs show <run_id> --workspace <workspace>`
5. `millrace runs trace <run_id> --workspace <workspace>`
6. `millrace doctor --workspace <workspace>`

Interpret those surfaces literally. If they do not support your claim, say you
do not yet know.

## Intervention Boundary

Use supported commands for intervention:

```bash
millrace control pause --workspace <workspace>
millrace control resume --workspace <workspace>
millrace control stop --workspace <workspace>
millrace queue retry-blocked <work_item_id> --family <family_id> --reason "<reason>" --workspace <workspace>
millrace incident resolve <incident_id> --reason "<reason>" --workspace <workspace>
millrace approvals ls --workspace <workspace>
```

Do not directly move, delete, or rewrite runtime-owned active/blocked/done
files unless the user explicitly requests low-level surgery after supported
paths are considered.

## Output Contract

When operating Millrace, report:

- which delegation policy is in force
- what workspace and mode/config posture you are using
- what command or supported operator action you took
- what `status`, `queue`, `runs`, `trace`, `doctor`, or approval evidence says
- whether the next action is to run, wait, pause, resume, retry, repair, or
  return to direct source work

Keep explanations tied to runtime evidence. Do not report inferred completion
without run or closure evidence.
