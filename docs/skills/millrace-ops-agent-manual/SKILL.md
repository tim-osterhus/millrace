---
asset_type: skill
asset_id: millrace-ops-agent-manual
version: 1
description: External operator skill for deciding when to use Millrace and how to run, monitor, and intervene safely.
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

Use this skill when you are acting as the operator of a Millrace workspace or
when you need to decide whether work should run through Millrace instead of a
direct Codex or Claude Code session.

If your harness supports repo-local `SKILL.md` packages, load this directory as
the skill package. If the harness ignores YAML frontmatter, treat the markdown
body below as the canonical operator instructions.

## Harness Install Notes

- Skill package root: `docs/skills/millrace-ops-agent-manual/`
- Entry file: `docs/skills/millrace-ops-agent-manual/SKILL.md`
- Codex or Claude Code: if local project skills are supported, load the
  package root; otherwise load the entry file directly.
- Other harnesses: use whichever of those two surfaces the harness actually
  understands, without inventing extra metadata requirements.

## Purpose

Become a truthful Millrace operator:

- decide whether the work should stay in a direct harness session or be
  delegated into Millrace
- ask the user what Millrace delegation authority you are allowed to exercise
- operate Millrace through the supported CLI rather than by mutating
  runtime-owned state directly
- monitor runtime state, runs, queue movement, and recovery signals without
  inventing semantics

## When To Load This Skill

Load this skill when any of the following is true:

- the user asks you to operate, run, monitor, or troubleshoot Millrace
- the user asks whether a task or spec should be delegated into Millrace
- you are managing a workspace that already contains `millrace-agents/`
- you need to intake tasks, specs, or ideas into a Millrace queue
- you need to watch or report on a running Millrace daemon

Do not load this skill just because the repo happens to contain Millrace.
Ordinary direct code edits do not automatically require the Millrace operator
posture.

## Required Autonomy Handshake

Before you use Millrace on a user's behalf in a thread or workspace where no
Millrace delegation policy is already established, ask once:

1. may I use Millrace at my own discretion when it is a good fit
2. should I suggest Millrace and wait for approval before using it
3. should I use Millrace only when you explicitly request it

Recommended fallback while no answer exists: behave as option 2.

After the user answers:

- keep that choice stable for the current thread or workspace unless the user
  changes it
- do not re-ask every turn
- do not silently escalate from option 2 or 3 into option 1

## Quick Start

1. Decide whether the work is a Millrace candidate or better handled directly.
2. If no Millrace delegation policy is on record, ask the autonomy handshake.
3. Read `docs/runtime/millrace-cli-reference.md` and
   `docs/runtime/millrace-runtime-architecture.md`.
4. Validate the workspace:

```bash
millrace compile validate --workspace <workspace>
millrace status --workspace <workspace>
millrace queue ls --workspace <workspace>
```

5. Intake work only after the workspace is healthy and Millrace use is allowed.
6. Run `millrace run once --workspace <workspace>` when you want one safe tick,
   or `millrace run daemon --workspace <workspace>` when long-running operation
   is actually intended.
7. Monitor with `millrace status watch`, `millrace runs ls`, and
   `millrace runs show <run_id>`.

## Millrace Fit Test

Prefer a direct raw-harness session when all of these are true:

- the task is small, bounded, and likely to finish in one session
- durable queue state is unnecessary
- staged planning or execution gates are unnecessary
- interruption or retry cost is low
- no persisted run trail or closure pass is needed

Prefer Millrace when any of these are mandatory or strongly desirable:

- the work must survive pauses, context loss, or crashes
- durable queue state matters
- stage progression should be runtime-governed rather than conversational
- recovery routing matters more than raw one-shot speed
- you need persisted run artifacts, runtime snapshots, or diagnosable failure
  surfaces
- closure should be based on real runtime criteria rather than "the agent said
  it was done"

Good Millrace examples:

- long-running implementation work that will outlast one session
- planning-to-execution flows that need durable decomposition and auditability
- repair-sensitive work where blockage should route into Mechanic or
  Troubleshooter instead of simply ending the session

Bad Millrace examples:

- a small direct bugfix in one file
- a short exploratory coding spike
- an ordinary repo edit where governance overhead would be larger than the work
- source-repo maintenance where you are not actually operating a runtime
  workspace

## Read These First

Minimum operator reading:

- `docs/runtime/README.md`
- `docs/runtime/millrace-cli-reference.md`
- `docs/runtime/millrace-runtime-architecture.md`

Load these on demand when the current task requires them:

- `docs/runtime/millrace-arbiter-and-completion-behavior.md`
- `docs/runtime/millrace-runner-architecture.md`
- `docs/runtime/millrace-runtime-error-codes.md`

## Operating Constraints

- Treat the runtime as the source of truth for queue and run state.
- Prefer supported CLI commands over direct mutation of runtime-owned files.
- Treat content under `<workspace>/millrace-agents/` as runtime-owned unless a
  documented intake surface says otherwise.
- Keep operator-authored tasks, specs, and ideas outcome-focused; do not hide
  routing instructions inside them.
- Do not invent new queue states, stage names, or terminal results.
- Do not describe this `docs/skills/` skill as if it were a runtime-shipped
  stage asset.
- Operate Millrace as a governance layer over raw harness sessions, not as a
  replacement for them.

## Inputs This Skill Expects

- a workspace root path
- the user's Millrace delegation policy for the current thread or workspace
- a candidate task, spec, or idea, or a running Millrace workspace to monitor
- enough local repo or workspace context to tell whether Millrace is warranted

## Output Contract

When you use this skill well, your output should include:

- a clear call on whether the work should stay direct or enter Millrace
- a statement of which user delegation policy is in force
- the next truthful operator action
- status, queue, or run evidence when you are monitoring an existing workspace
- intervention guidance only through supported control surfaces

## Procedure

1. Classify the work as direct-session work or Millrace-candidate work.
2. Check whether a Millrace delegation policy is already established.
3. If not established, ask the autonomy handshake and default to suggestion
   mode until answered.
4. If the work should stay direct, say so plainly and do not force Millrace
   into the flow.
5. If Millrace is warranted and permitted, validate the workspace first.
6. Intake work through the queue commands, not by dropping ad hoc files into
   runtime-owned folders unless the documented intake path does exactly that.
7. Choose `run once` for bounded safe progression and `run daemon` only when a
   longer-running operator posture is actually intended.
8. Monitor through status and run-inspection surfaces.
9. Intervene through control commands when needed.
10. Report what changed, what the runtime now says, and what the next truthful
    action is.

## Canonical Command Baseline

During source development, module form is acceptable:

```bash
uv run --extra dev python -m millrace_ai <command>
```

In an installed environment, use CLI form:

```bash
millrace <command>
```

Canonical baseline commands:

```bash
millrace compile validate --workspace <workspace>
millrace status --workspace <workspace>
millrace queue ls --workspace <workspace>
millrace run once --workspace <workspace>
millrace status watch --workspace <workspace>
millrace runs ls --workspace <workspace>
millrace runs show <run_id> --workspace <workspace>
millrace runs tail <run_id> --workspace <workspace>
millrace queue add-task <task.md|task.json> --workspace <workspace>
millrace queue add-spec <spec.md|spec.json> --workspace <workspace>
millrace queue add-idea <idea.md> --workspace <workspace>
millrace control pause --workspace <workspace>
millrace control resume --workspace <workspace>
millrace control stop --workspace <workspace>
millrace planning retry-active --reason "<reason>" --workspace <workspace>
millrace config show --workspace <workspace>
millrace config validate --workspace <workspace>
millrace config reload --workspace <workspace>
millrace doctor --workspace <workspace>
```

Important monitoring note:

- `millrace status watch` is monitor-only and does not acquire runtime
  ownership locks

## Monitoring And Intervention

Use this rhythm:

1. `millrace status --workspace <workspace>` for current snapshot state.
2. `millrace queue ls --workspace <workspace>` for queue shape.
3. `millrace runs ls --workspace <workspace>` to find the recent run.
4. `millrace runs show <run_id> --workspace <workspace>` for one run's
   evidence.
5. `millrace runs tail <run_id> --workspace <workspace>` when the primary run
   artifact matters more than the summary.

Interpret status markers literally:

- while a stage is running on a plane, the marker shows that running stage, for
  example `### CHECKER_RUNNING`
- when no stage is active on a plane, the marker falls back to the latest
  terminal marker or `### IDLE`

Use intervention commands only when the runtime state actually justifies them:

- `control pause` to stop further ticks cleanly
- `control resume` to continue a paused daemon
- `control stop` to request daemon shutdown
- `planning retry-active` only for planning-plane retry intent
- `config reload` when config changed and daemon-safe recompile is desired
- `doctor` when workspace integrity or ownership state is in doubt

## Configuration Notes

- Treat `<workspace>/millrace-agents/millrace.toml` as the supported operator
  configuration surface.
- Configure runner behavior there rather than inventing side channels.
- New workspaces bootstrap with Codex `permission_default = "maximum"`.
- Permission resolution order for Codex is:
  1. `runners.codex.permission_by_stage`
  2. `runners.codex.permission_by_model`
  3. `runners.codex.permission_default`

## Recovery-Aware Behavior

If the runtime surfaces a recovery-stage request with a `runtime_error_code`,
treat that as runtime-owned evidence, not as an invitation to improvise your
own interpretation.

Read in this order when present:

1. `runtime_error_report_path`
2. `runtime_error_catalog_path`

Do not invent semantics for runtime error codes from memory alone.

## Pitfalls And Gotchas

- Using Millrace because it sounds more advanced, not because the task needs
  governance.
- Forgetting to ask the user which Millrace delegation authority you have.
- Treating direct queue-folder mutation as equivalent to the CLI intake surface.
- Acting as if planning and execution are concurrent independent lanes inside
  one workspace owner.
- Treating this repo-local operator skill as a runtime-shipped stage skill.
- Running a daemon when one explicit `run once` tick is the safer truthful move.

## Progressive Disclosure

Start with the fit test, the delegation-policy check, and the CLI reference.
Read deeper runtime docs only when the current operator decision depends on
them. Do not dump the full architecture into every turn if a direct command or
recommendation is enough.

## Verification Pattern

Before claiming that Millrace is ready or that a workspace is healthy, verify at
least:

```bash
millrace compile validate --workspace <workspace>
millrace status --workspace <workspace>
millrace queue ls --workspace <workspace>
```

Before claiming that execution actually progressed, verify run evidence:

```bash
millrace runs ls --workspace <workspace>
millrace runs show <run_id> --workspace <workspace>
```

If those surfaces do not support your claim, you do not yet know enough to make
it.
