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

Use this skill when you are acting as the operator of a Millrace workspace or
when another active delegation policy has selected Millrace as the execution
path.

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

- honor the active Millrace delegation policy, or use the fallback fit test
  when this runbook is loaded by itself
- ask the user what Millrace delegation authority you are allowed to exercise
  only when no policy is already established
- operate Millrace through the supported CLI rather than by mutating
  runtime-owned state directly
- monitor runtime state, runs, queue movement, and recovery signals without
  inventing semantics

## When To Load This Skill

Load this skill when any of the following is true:

- the user asks you to operate, run, monitor, or troubleshoot Millrace
- `millrace-autonomous-delegation` or another active policy has selected
  Millrace for the current work
- the user asks whether a task, probe, or spec should be delegated into Millrace and no
  separate delegation skill is available
- you are managing a workspace that already contains `millrace-agents/`
- you need to intake tasks, probes, specs, or ideas into a Millrace queue
- you need to watch or report on a running Millrace daemon

Do not load this skill just because the repo happens to contain Millrace.
Ordinary direct code edits do not automatically require the Millrace operator
posture.

## Required Autonomy Handshake

If `millrace-autonomous-delegation` is active, or an equivalent workspace or
user policy already permits autonomous Millrace delegation, do not ask the
handshake again. Follow that policy.

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

1. Confirm that Millrace is requested, selected, or permitted. If
   `millrace-autonomous-delegation` is active, use its decision; otherwise use
   the fallback fit test below.
2. If no Millrace delegation policy is on record and Millrace was not
   explicitly requested, ask the autonomy handshake.
3. Read `docs/runtime/millrace-cli-reference.md` and
   `docs/runtime/millrace-runtime-architecture.md`.
4. Initialize the workspace if the managed baseline is missing:

```bash
millrace init --workspace <workspace>
```

5. Validate the workspace:

```bash
millrace compile validate --workspace <workspace>
millrace compile show --workspace <workspace>
millrace compile graph --workspace <workspace>
millrace status --workspace <workspace>
millrace queue ls --workspace <workspace>
```

Know which shipped harness posture you are validating:

- `default_codex` is the canonical bootstrap baseline
- `default_pi` keeps the same loops and stage semantics, but swaps every stage
  to the Pi RPC adapter
- `learning_codex` and `learning_pi` add the
  Analyst/Professor/Curator/Librarian learning plane for runtime learning
  requests, skill-improvement workflows, and post-Planner optional-skill
  preparation
- `default_codex_integrated` and `learning_codex_integrated` are opt-in
  quality loops. They use Codex and select `execution.with_integrator`, so a
  successful Builder result always runs through Integrator before Checker.
- `blueprint_codex` is an opt-in Blueprint Planning posture. It keeps standard
  execution, but routes Planner output through Manager Blueprint, Contractor
  Blueprint, and Evaluator Blueprint before generated tasks enter Execution.
  Contractor proposes plans only; it must not implement source changes.
- `blueprint_learning_codex` keeps that same Blueprint Planning posture and
  adds the Learning plane. Planner-complete still triggers Librarian before
  Blueprint Planning continues into Manager Blueprint.
- probes enter Planning through Recon before becoming generated tasks,
  generated specs, no-ops, or blocked probe artifacts
- learning requests may close as no-op/done when evidence was reviewed and no
  skill update is warranted; this is not the same as a blocked learning request
- `standard_plain` remains accepted only as a compatibility alias for
  `default_codex`

Daemon mode uses a compiled lane scheduler. Default modes remain one active
lane per plane, and shipped policies keep Planning and Execution mutually
exclusive. Runtime-owned mutation remains single-writer and serialized by the
daemon supervisor.
Generic success-triggered learning starts at Analyst. Direct Curator trigger
rules are only valid when a compiled mode names a safe destination such as
`target_skill_id` or `preferred_output_paths`.
Curator may perform a format-only migration of a touched workspace-installed
skill only when it is already applying an evidence-backed behavior patch, the
current skill linter reports a package/section-shape problem, and the migration
preserves existing semantics. It must record the behavior patch separately from
the format migration and must not edit source-packaged skills or promote them.
In learning-enabled shipped modes, successful Planner runs enqueue Librarian as
a targeted Learning request. Librarian reads Planner output and the installed
skill index, refreshes/checks the supported remote skill index, installs up to
eight relevant remote optional skills that are not already installed, and exits
as a clean no-op when no relevant uninstalled remote skill is available. This is
non-blocking Learning work; Planning and Execution do not wait on Librarian.

6. Intake work only after the workspace is healthy and Millrace use is allowed.
7. Run `millrace run daemon --max-ticks 1 --workspace <workspace>` when you
   want one bounded safe tick, or `millrace run daemon --workspace <workspace>`
   when long-running operation is actually intended.
   Use `millrace run daemon --monitor basic --workspace <workspace>` when you
   need concise live terminal visibility from the daemon itself. The basic
   monitor uses short run handles and compact stage labels for scanning; use
   `millrace runs ls` and `millrace runs show <run_id>` for full run ids,
   artifacts, capability grant/support summaries, and durable details.
   The basic monitor prints the first `idle reason=no_work` line immediately,
   then treats repeated `no_work` idles as a 6-hour heartbeat until runtime
   activity or a different idle reason appears.
   Use `--monitor-log <path>` when you need the same clean monitor stream
   persisted to a file without necessarily printing it to stdout.
   If you need the daemon to persist beyond the current harness process, spawn
   it inside a `tmux` pane rather than as an ordinary shell background process.
8. Monitor with `millrace status watch`, `millrace runs ls`, and
   `millrace runs show <run_id>`.
   Use `millrace status show --format json --workspace <workspace>` when you
   need machine-readable diagnostics such as `blocked_idle`,
   `current_failure_class`, or `latest_runtime_error_report_path`.
   Use `millrace compile graph --workspace <workspace>` when you need the legal
   compiled topology, and `millrace runs trace <run_id> --workspace <workspace>`
   when you need the graph-shaped path one concrete run followed.
9. For a browser dashboard, install the separate optional `millrace-web`
   package and run `millrace-web serve --workspace <workspace>`. Use Detail for
   dense state inspection and Flow for visual plane/lane monitoring. Treat it
   as read-only observability, not as a control surface.
10. Use `millrace skills ...` commands only for the optional skills workflow and
   learning-plane skill requests; ordinary task intake still belongs in
   `millrace queue ...`.

## Fallback Millrace Fit Test

Prefer `docs/skills/millrace-autonomous-delegation/SKILL.md` for the dedicated
decision layer when the harness can load it. Use this shorter fit test when
this runbook is the only available Millrace skill.

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

- `docs/doc-index.md`
- `docs/runtime/README.md`
- `docs/runtime/millrace-cli-reference.md`
- `docs/runtime/millrace-runtime-architecture.md`

Load these on demand when the current task requires them:

- `docs/runtime/millrace-arbiter-and-completion-behavior.md`
- `docs/runtime/millrace-compiled-stage-graphs-and-run-traces.md`
- `docs/runtime/millrace-execution-capabilities.md`
- `docs/runtime/millrace-runner-architecture.md`
- `docs/runtime/millrace-runtime-error-codes.md`
- `docs/runtime/millrace-modes-and-loops.md`
- `docs/graphs/graphs-index.md`

## Operating Constraints

- Treat the runtime as the source of truth for queue and run state.
- Prefer supported CLI commands over direct mutation of runtime-owned files.
- Treat content under `<workspace>/millrace-agents/` as runtime-owned unless a
  documented intake surface says otherwise.
- Keep operator-authored tasks, probes, specs, and ideas outcome-focused; do not hide
  routing instructions inside them.
- Do not invent new queue states, stage names, or terminal results.
- Do not describe this `docs/skills/` skill as if it were a runtime-shipped
  stage asset.
- Operate Millrace as a governance layer over raw harness sessions, not as a
  replacement for them.
- Treat `runners.default_runner` as a generic fallback, not as the definition
  of the shipped baseline mode posture.

## Inputs This Skill Expects

- a workspace root path
- the user's Millrace delegation policy for the current thread or workspace,
  unless Millrace was explicitly requested for the current task
- a candidate task, spec, or idea, or a running Millrace workspace to monitor
- enough local repo or workspace context to tell whether Millrace is warranted

## Output Contract

When you use this skill well, your output should include:

- a clear call on whether the work should stay direct or enter Millrace
- a statement of which user or workspace delegation policy is in force when
  that policy affects the next operator action
- the next truthful operator action
- status, queue, or run evidence when you are monitoring an existing workspace
- intervention guidance only through supported control surfaces

## Procedure

1. Confirm whether Millrace was explicitly requested, selected by an active
   delegation policy, or still only a candidate.
2. If the work is still only a candidate and
   `millrace-autonomous-delegation` is available, use that skill for the
   decision.
3. If no delegation policy is established, ask the autonomy handshake and
   default to suggestion mode until answered.
4. If the work should stay direct, say so plainly and do not force Millrace
   into the flow.
5. If Millrace is warranted and permitted, validate the workspace first.
6. Intake work through the queue commands, not by dropping ad hoc files into
   runtime-owned folders unless the documented intake path does exactly that.
7. Choose `run daemon --max-ticks 1` for bounded safe progression and
   unbounded `run daemon` only when a longer-running operator posture is
   actually intended. If daemon persistence matters, launch it inside a `tmux`
   pane, not as a normal background process.
8. Monitor through status and run-inspection surfaces.
9. Intervene through control commands when needed.
10. Report what changed, what the runtime now says, and what the next truthful
    action is.

## Canonical Command Baseline

During source development, module form is acceptable:

```bash
uv run --extra dev python -m millrace_ai <command>
```

For workspace-local E2E deliverables, prefer the Python executable that exists
in the target environment. If a generated README says `python` but the host
only has `python3`, run the equivalent `python3 -m ...` command and report that
portability mismatch as E2E evidence.

In an installed environment, use CLI form:

```bash
millrace <command>
```

Package updates and workspace baseline upgrades are separate:

- update the installed Millrace package with the environment's package manager
  first, for example `pip install -U millrace-ai==<version>`
- verify the runtime package with `millrace --version` or `millrace version`
- then use `millrace upgrade` to preview/apply managed workspace baseline asset
  updates under `<workspace>/millrace-agents/`
- `millrace upgrade --apply` does not install or update the Python package that
  provides the runtime code
- after applying workspace baseline updates, run `millrace compile validate`
  before resuming runtime work

Canonical baseline commands:

```bash
millrace init --workspace <workspace>
millrace version
millrace upgrade --workspace <workspace>
millrace upgrade --apply --workspace <workspace>
millrace upgrade --localize-removed <managed/path> --workspace <workspace>
millrace compile validate --workspace <workspace>
millrace compile show --workspace <workspace>
millrace compile graph --workspace <workspace>
millrace status --workspace <workspace>
millrace queue ls --workspace <workspace>
millrace queue show <work_item_id> --workspace <workspace>
millrace run daemon --max-ticks 1 --workspace <workspace>
millrace run daemon --monitor basic --workspace <workspace>
millrace run daemon --monitor none --monitor-log <path> --workspace <workspace>
millrace status watch --workspace <workspace>
millrace runs ls --workspace <workspace>
millrace runs show <run_id> --workspace <workspace>
millrace runs tail <run_id> --workspace <workspace>
millrace runs trace <run_id> --workspace <workspace>
millrace approvals ls --workspace <workspace>
millrace approvals show <approval_id> --workspace <workspace>
millrace approvals approve <approval_id> --reason "<reason>" --workspace <workspace>
millrace approvals deny <approval_id> --reason "<reason>" --workspace <workspace>
millrace modes list
millrace modes show <mode_id>
millrace model-aliases list --workspace <workspace>
millrace model-aliases set <alias> --model <model> --thinking-level <level> --workspace <workspace>
millrace model-aliases assign-global <alias> --workspace <workspace>
millrace model-aliases assign-loop <loop_id> <alias> --workspace <workspace>
millrace model-aliases assign-stage <stage> <alias> --workspace <workspace>
millrace compile validate --mode default_codex_integrated --workspace <workspace>
millrace compile validate --mode blueprint_codex --workspace <workspace>
millrace compile validate --mode blueprint_learning_codex --workspace <workspace>
millrace skills ls --workspace <workspace>
millrace skills show <skill_id> --workspace <workspace>
millrace skills search <query> --workspace <workspace>
millrace skills install <skill_ref> --workspace <workspace>
millrace skills refresh-remote-index --workspace <workspace>
millrace skills create "<prompt>" --mode learning_codex --workspace <workspace>
millrace skills improve <skill_id> --mode learning_codex --workspace <workspace>
millrace skills promote <skill_id> --workspace <workspace>
millrace skills export <skill_id> --workspace <workspace>
millrace queue add-task <task.md|task.json> --workspace <workspace>
millrace queue add-probe <probe.md|probe.json> --workspace <workspace>
millrace queue add-spec <spec.md|spec.json> --workspace <workspace>
millrace queue add-idea <idea.md> --workspace <workspace>
millrace queue retry-blocked <work_item_id> --family <family_id> --reason "<reason>" --workspace <workspace>
millrace queue cancel <work_item_id> --kind task --reason "<reason>" --workspace <workspace>
millrace queue archive-blocked <task_id> --reason "<reason>" --workspace <workspace>
millrace queue supersede <old_task_id> --replacement <new_task_id> --reason "<reason>" --workspace <workspace>
millrace queue retarget-dependency <task_id> --from <old_dependency_id> --to <new_dependency_id> --reason "<reason>" --workspace <workspace>
millrace incident resolve <incident_id> --reason "<reason>" --workspace <workspace>
millrace incident cancel <incident_id> --reason "<reason>" --workspace <workspace>
millrace incident archive-invalid <filename> --reason "<reason>" --workspace <workspace>
millrace control pause --workspace <workspace>
millrace control resume --workspace <workspace>
millrace control stop --workspace <workspace>
millrace planning retry-active --reason "<reason>" --workspace <workspace>
millrace config show --workspace <workspace>
millrace config validate --workspace <workspace>
millrace config reload --workspace <workspace>
millrace doctor --workspace <workspace>
millrace-web serve --workspace <workspace>
millrace-web serve --workspace <workspace-a> --workspace <workspace-b> --view flow
```

### Self-Contained Intake Rule

Queue intake commands are typed Millrace document imports, not generic markdown
ingestion commands. `queue add-task` imports a valid `TaskDocument`,
`queue add-probe` imports a valid `ProbeDocument`, `queue add-spec` imports a
valid `SpecDocument`, and `queue add-idea` stages idea-shaped markdown.

When supporting material is needed, package it inside the active workspace or
repo and reference it with repo-relative paths. Do not enqueue thin wrappers
that point to arbitrary local files outside the workspace. Stable public URLs
are acceptable when the operator deliberately supplies them, but local absolute
paths outside the workspace are not acceptable intake dependencies.

For non-trivial probe/spec handoffs, prepare a workspace-local package such as:

```text
lab/intake/<intake-id>/
  probe.md
  architecture-spec.md
  supporting-notes.md
  reference-index.md
```

Then enqueue the typed work document, for example:

```markdown
# Millracer vNext Ops Gateway Recon Probe

Probe-ID: probe-millracer-vnext-ops-gateway
Title: Millracer vNext Ops Gateway Recon Probe
Summary: Inspect repo-local reference material before generating scoped work.
Request: Use the repo-local architecture spec and references to determine the
clean implementation plan, then emit generated specs or implementation tasks.
Created-At: 2026-05-26T06:36:36Z
Created-By: codex

Target-Paths:
- .
- src/millracer/
- tests/

References:
- lab/intake/millracer-vnext-ops-gateway/architecture-spec.md
- lab/intake/millracer-vnext-ops-gateway/reference-index.md
```

Important monitoring note:

- `millrace status watch` is monitor-only and does not acquire runtime
  ownership locks
- `millrace run daemon --monitor basic` is live-only output; repeated
  `idle reason=no_work` lines are throttled to one heartbeat every 6 hours
  until runtime activity or a different idle reason resets the heartbeat
- the basic monitor is intentionally human-facing: stage labels are compact,
  long run ids are shortened for display, unknown token usage is omitted, and
  full details remain available through `millrace runs ...` commands and
  persisted runtime artifacts
- `millrace compile graph` shows the compiled legal topology; `millrace runs
  trace <run_id>` shows concrete stage instances, router decisions, and
  spawned-work references for one run. Older runs without `run_trace.json` are
  still inspectable through stage-result fallback derivation.
- `millrace run daemon --monitor-log <path>` writes basic monitor output to a
  file; combine it with `--monitor none` for quiet foreground operation
- optional local dashboard monitoring lives in the separate `millrace-web`
  package: install it separately and run
  `millrace-web serve --workspace <workspace>`. It binds to
  `127.0.0.1:8765` by default, accepts repeated `--workspace` options, and
  supports `--view detail|flow` plus `--poll-interval-seconds <seconds>`.
  Detail is the dense operator view for current runtime state, queues, recent
  runs, artifacts, compiled plan, Arbiter, and usage governance. Flow is the
  visual plane/lane view over the same read-only DTOs. `millrace-web` does not
  ship inside `millrace-ai`, does not acquire daemon ownership locks, does not
  expose queue/control mutation routes, and must not be treated as a runtime
  control surface.
- `millrace doctor` is the quick integrity check for mode assets and resolved
  runner posture, including missing harness binaries and closure lineage drift
- `millrace status` exposes `pause_sources` and usage-governance blockers when
  usage governance is enabled or has persisted state
- `millrace runs show <run_id>` exposes compact `capability_grant` and
  `capability_support` lines when a stage result contains execution capability
  metadata; full structured details remain in run artifacts
- `millrace approvals ...` is the supported operator path for
  approval-required execution capability grants. Approve/deny routes through
  the mailbox when a daemon owns the workspace and applies directly when no
  daemon owns it.

Blueprint monitoring checklist:

- Use `millrace status --workspace <workspace>` to inspect
  `blueprint_draft_*`, `blueprint_packet_*`, `blueprint_critique_*`,
  `blueprint_evaluation_count`, and `blueprint_promotion_count` before opening
  raw files.
- Use `millrace runs show <run_id>` on Blueprint stage runs to inspect
  `runtime_effect_handler_id`, `runtime_effect_operation_id`,
  `runtime_effect_runner_id`, `runtime_effect_legacy_handler_id`,
  `runtime_effect_decision`, `runtime_effect_failure_class`,
  `runtime_effect_failure_message`,
  `runtime_effect_mutation_phase`, `runtime_effect_failure_policy_id`,
  `runtime_effect_recovery_action`, `runtime_effect_source_lifecycle_*`, and
  `runtime_effect_created_path` lines.
- Use `millrace status` and `millrace doctor` to inspect the latest repairable
  Evaluator approval generated-task failure context, structured repair
  contract, replay conflict classes, inert-artifact guard, and runtime
  ownership boundary. These diagnostics keep the original Evaluator failure
  context visible after a successful `mechanic_blueprint_repair_apply` effect.
- Compare `artifact_status` and `runtime_outcome` before trusting a run as
  clean. `artifact_status: valid` only means the stage-result artifact parsed;
  `runtime_outcome: blocked` means routing or a runtime effect still failed.
- For artifact contract drift, inspect the run directory for the declared
  canonical filename first. Canonical JSON outputs win over legacy fallback
  filenames; malformed canonical files are intentional blockers, not a reason
  to hand-edit fallback markdown into place.
- Treat Blueprint lineage ids as metadata, not storage keys. New manifests are
  stored by `manifest_id`; legacy root-keyed manifests are resolved by their
  embedded `manifest_id`. Same-root remediation manifests are expected when an
  Arbiter gap triggers another Manager Blueprint pass under the original
  `root_spec_id`.
- Diagnose `blueprint_manifest_duplicate` by comparing `manifest_id` and
  normalized manifest content. Do not block or edit solely because two
  manifests share `root_spec_id`; that is normal same-lineage remediation.
- Manager Blueprint runtime-effect failures route by class. The shipped policy
  blocks missing, malformed, schema-invalid, or manifest/draft-mismatched
  pre-mutation outputs conservatively, the same as duplicate manifest ids,
  duplicate draft ids, invalid source lifecycle, and partial mutations.
- Evaluator Blueprint approval pre-mutation `generated_task_missing` and
  `generated_task_invalid` failures route to `mechanic_blueprint`; other
  approval replay conflicts and partial mutations remain conservative blockers
  unless a declared reconciliation handler proves equivalent durable state.
- Mechanic Blueprint runtime-effect recovery is structured. It receives failed
  runtime-effect context and emits `MECHANIC_BLUEPRINT_COMPLETE` only with a
  `blueprint_repair_decision.json` repair decision; `mechanic_report.md` alone
  is evidence, not operational state. Clean Manager rerun decisions use
  `next_resume_stage: manager_blueprint` in the repair decision JSON and
  `resume_stage: manager_blueprint` only as terminal-result metadata, but the
  shipped runtime failure policy automatically routes only the Evaluator
  generated-task missing/invalid class to Mechanic Blueprint.
  `repaired_generated_task.json` is valid only with
  `repair_action=apply_repaired_generated_task`; unsafe recovery must emit
  `BLOCKED`.
- Manager Blueprint replay is idempotent when all durable manifest and draft
  outputs already exist with equivalent content. If the source spec is already
  done or the source incident already resolved, the replay is a no-op success;
  blocked, missing, partial, or divergent state remains an operator-visible
  failure.
- Contractor Blueprint candidate replay is idempotent only when existing
  candidate packet and markdown outputs are equivalent to the new run output.
  Divergent packet or markdown collisions surface as
  `blueprint_candidate_duplicate_conflict` or
  `blueprint_candidate_markdown_conflict` and should not be overwritten by
  hand.
- Evaluator Blueprint approval replay is idempotent only when existing
  evaluation, approved packet, approved markdown, generated task, and promotion
  outputs are equivalent. Divergent or unverifiable approval collisions surface
  as `blueprint_evaluation_duplicate_conflict`,
  `blueprint_approved_packet_conflict`, `blueprint_approved_markdown_conflict`,
  `blueprint_task_duplicate`, or `blueprint_promotion_duplicate_conflict` and
  should not be overwritten by hand.
- Planner disposition controls whether the active source continues to Manager.
  `active_source_ready_for_manager` continues the same source,
  `emitted_child_specs` resolves/completes the source after validating child
  specs exist and does not also send it to Manager, and `blocked` preserves
  normal blocked recovery. Missing disposition is a source blocker, not an
  invitation to infer intent from prose.
- A rejected Blueprint is not a failed daemon run by itself. Evaluator rejection
  should leave an open critique and route the same active draft back to
  Contractor for a revised packet.
- An approved Blueprint should create an approved packet, evaluation,
  promotion record, and generated task. Arbiter should remain suppressed until
  that generated task has completed or blocked.
- `millrace status` exposes the open closure target and
  `planning_root_specs_deferred_by_closure_target` when bulk root-spec intake
  is backpressured behind the v1 one-open-target policy
- Arbiter closure is rooted in a generic root source plus a root spec. Use
  `Root-Idea-ID` only for idea-rooted work. Probe-rooted work should preserve
  `Root-Intake-Kind: probe` and `Root-Intake-ID: <probe-id>` rather than
  inventing an idea id to satisfy closure. If closure blocks on root source
  resolution, inspect `status`, `doctor`, `queue ls`, and the Arbiter target
  state before attempting direct repair.
- If `millrace doctor` reports `daemon_stopped_with_open_graph_work`, the
  daemon is stopped while an open closure target still has compiled-family
  backlog or blockers. Confirm `process_running`, inspect `queue ls` family
  counters, then restart the daemon unless a separate provider/network outage
  explains the stop.
- Consultant `NEEDS_PLANNING` handoffs generated by runtime should now produce
  same-lineage planning incidents under an open closure target. If an older
  workspace idles with a blocked source task and a lineage-less incoming
  incident, inspect the incident and source task before clearing or skipping the
  closure target; the supported fix is to repair the incident lineage, not to
  bypass the root.
- `millrace skills create` and `millrace skills improve` require a
  learning-enabled mode such as `learning_codex`, `learning_pi`, or
  `learning_codex_integrated`, or `blueprint_learning_codex`

## Monitoring And Intervention

Use this rhythm:

1. `millrace status --workspace <workspace>` for current snapshot state.
2. `millrace queue ls --workspace <workspace>` for queue shape.
3. `millrace queue show <work_item_id> --workspace <workspace>` when one queued
   item needs inspection.
4. `millrace runs ls --workspace <workspace>` to find the recent run.
5. `millrace runs show <run_id> --workspace <workspace>` for one run's
   evidence.
6. `millrace runs trace <run_id> --workspace <workspace>` when the concrete
   stage path and router decisions matter.
7. `millrace runs tail <run_id> --workspace <workspace>` when the primary run
   artifact matters more than the summary.

Interpret status markers literally:

- while a stage is running on a plane, the marker shows that running stage, for
  example `### CHECKER_RUNNING`
- when no stage is active on a plane, the marker falls back to the latest
  terminal marker or `### IDLE`
- learning-enabled workspaces also expose learning queue depth and
  `learning_status_marker`
- `lane: ...` lines show durable scheduler-lane state, including lane plan
  identity, active run ids, active work refs, last terminal outcome, and
  pause/drain/stop flags
- `active_run_count` and `active_run: ...` lines show the canonical active
  lanes plus the launch-plan id/fingerprint each active run must continue to
  use; the older `active_plane`/`active_stage` fields are only the foreground
  projection
- `latest_runtime_failure_origin` is runtime-owned diagnostic evidence for
  where the latest edge failure appears to have originated
- `pause_sources: operator` means an operator pause is still in force
- `pause_sources: usage_governance` means an opt-in usage rule is blocking
  further stage dispatch

Use intervention commands only when the runtime state actually justifies them:

- `control pause` to stop further ticks cleanly
- `control resume` to clear the operator pause source; it does not override an
  active usage-governance blocker
- `control stop` to request daemon shutdown
- unscoped `control retry-active` only when exactly one active work item exists
- `planning retry-active` only for planning-plane retry intent
- `clear-stale-state` to recover stale active files, including older
  closure-target invariant failures that left an unrelated root spec
  half-claimed; preserve the open closure target and avoid manual file moves
- `queue retry-blocked <WORK_ITEM_ID> --family <FAMILY_ID> --reason
  "<reason>"` to requeue one blocked task, probe, spec, incident,
  learning-request, or parseable graph-family artifact through the audited
  recovery path after verifying the blocker is retryable. Task-only usage
  remains compatible when the blocked id is unambiguous. Use `--force` only
  after inspecting the artifact and accepting the override. The command refuses
  a live daemon ownership lock, malformed blocked documents, ambiguous ids
  without `--family`, destination collisions, and `--root-spec-id` mismatches;
  stop the daemon first or let daemon auto-recovery handle qualifying transient
  blockers. If the blocked artifact is semantically bad, prefer cancellation
  plus fresh corrected intake over replaying it.
- `queue cancel <WORK_ITEM_ID> --kind task|probe|spec|incident --reason
  "<reason>"` when queued or blocked work is bad intake and should not run.
  This archives the document as cancelled; it is not completion.
- `queue supersede <OLD_TASK_ID> --replacement <NEW_TASK_ID> --reason
  "<reason>"` when a corrected queued, active, or done task should carry the
  work forward. Use `--cascade retarget` only when every queued dependent
  should point to the replacement; use `--cascade cancel` only when those
  dependents are also invalid.
- `queue retarget-dependency <TASK_ID> --from <OLD> --to <NEW> --reason
  "<reason>"` for one precise queued dependent rewrite.
- `incident resolve <INCIDENT_ID> --reason "<reason>"` when an operator has
  confirmed no more planning work is needed for an incoming, active, or blocked
  incident.
- `incident cancel <INCIDENT_ID> --reason "<reason>"` when the incident was
  generated from known-bad intake or is no longer valid planning input.
- `incident archive-invalid <FILENAME> --reason "<reason>"` for a single
  invalid incoming incident artifact that cannot be parsed as an incident
  document.
- Operator intervention commands archive rather than delete files, append
  `interventions.jsonl`, emit runtime events, and refresh queue-depth
  snapshots. When a daemon owns the workspace they mailbox-route and apply at
  a safe no-active-run boundary; they do not kill a running stage.
- Bad-intake cleanup flow: pause if needed, add or confirm the corrected
  replacement task, supersede the bad blocked/queued task, retarget or cancel
  stale queued dependents, cancel stale planning incidents, inspect `queue ls`
  and `status`, then resume only when the remaining claimable work is correct.
- `queue repair-lineage --root-spec-id <ROOT_SPEC_ID>` to preview a stopped-daemon
  repair when doctor reports `closure_lineage_drift`; add `--apply` only after
  confirming there is no live ownership lock or active stage
- `approvals approve <APPROVAL_ID> --reason "<reason>"` or
  `approvals deny <APPROVAL_ID> --reason "<reason>"` only after inspecting the
  pending execution capability approval and accepting the operator consequence
- if `doctor` reports `duplicate_task_lifecycle_state`, inspect the named task
  across `tasks/queue/`, `tasks/active/`, `tasks/done/`, and `tasks/blocked/`;
  same-root blocked predecessors are automatically retired only after a same-ID
  continuation reaches `done`
- `config reload` when config changed and daemon-safe recompile is desired
- `doctor` when workspace integrity or ownership state is in doubt

## Configuration Notes

- Treat `<workspace>/millrace-agents/millrace.toml` as the supported operator
  configuration surface.
- Configure runner behavior there rather than inventing side channels.
- Usage governance is disabled by default. When enabled, it evaluates between
  stages, can pause via the `usage_governance` pause source, and can auto-resume
  only when active governance blockers clear.
- Blocked dependency auto-recovery is enabled by default but conservative. It
  acts only when queued same-lineage execution work is stranded behind a
  blocked predecessor whose latest blocked metadata classifies the failure as
  `network_unavailable`, `provider_unavailable`, `provider_rate_limited`, or
  `runner_timeout`, and only after cooldown/budget gates pass. Missing runner
  binaries, auth failures, malformed terminal output, stage-authored blocked
  states, and unknown transport failures require operator review or explicit
  `queue retry-blocked --force`.
- Governance config changes apply through `config reload`, then become visible
  on the next runtime tick through `millrace status` and basic-monitor
  governance lines. Do not expect `config reload` itself to summarize whether a
  governance pause cleared or remained.
- Execution capability policy lives under `[execution_capabilities]`.
  Grant-affecting changes are recompile changes, not next-tick runtime-only
  changes. Defaults keep rollout compatible: advisory grants are allowed,
  strict required-advisory failure is disabled, network access is denied, and
  package install plus git mutate grants require operator approval.
- Model aliases live under `[model_aliases.<alias>]` and assignment policy
  lives under `[model_assignment]`. Defaults are `fast`, `standard`, and
  `deep`, with `standard` selected globally. Use `millrace model-aliases ...`
  commands instead of hand-editing when possible; they preserve TOML comments
  where possible and request daemon-safe reload by default.
- Config reload recompiles changes such as `runtime.default_mode` and
  `stages.<stage>.*`, `model_aliases.*`, and `model_assignment.*` on the
  daemon's next tick when a daemon owns the workspace. If the daemon was
  started with an explicit `--mode`, that override remains pinned across
  reloads. Active runs keep their launch compiled plan while a newer alias
  plan waits as pending.
- Stage config supports learning stages such as `professor` and `librarian`,
  including `model`, runner-neutral `thinking_level`, legacy Codex
  `model_reasoning_effort`, and `timeout_seconds`.
- New workspaces bootstrap with `runtime.default_mode = "default_codex"` and
  `runners.default_runner = "codex_cli"`.
- To switch a managed workspace into the quality loop after a package update,
  run `millrace upgrade --apply --workspace <workspace>`, set
  `runtime.default_mode = "default_codex_integrated"` or
  `"learning_codex_integrated"`, then run
  `millrace config reload --workspace <workspace>`. If the daemon was started
  with an explicit `--mode`, restart it without that override or with the
  intended integrated mode.
- New workspaces bootstrap with Codex `permission_default = "maximum"`.
- Pi defaults to disabling Pi-native context-file and skill discovery so the
  shipped `default_pi` posture remains deterministic.
- Permission resolution order for Codex is:
  1. `runners.codex.permission_by_stage`
  2. `runners.codex.permission_by_model`
  3. `runners.codex.permission_default`
- Do not describe advisory execution capability grants as enforced. Codex
  `maximum` and broad Pi RPC operation may be operationally powerful without
  giving Millrace a narrow enforceable boundary.

## Recovery-Aware Behavior

If the runtime surfaces a recovery-stage request with a `runtime_error_code`,
treat that as runtime-owned evidence, not as an invitation to improvise your
own interpretation.

Read in this order when present:

1. `runtime_error_report_path`
2. `runtime_error_catalog_path`

Do not invent semantics for runtime error codes from memory alone.

Recon handoff failures are a special blocked-probe case. If status shows
`current_failure_class: recon_handoff_invalid`, run:

```bash
millrace status show --format json --workspace <workspace>
```

Then read `latest_runtime_error_report_path` and inspect the run's
`recon_packet.md` plus any generated task/spec artifact. The supported
diagnosis is that Recon emitted an invalid typed handoff; do not manually route
that probe into Planner, Manager, or Mechanic.

## Pitfalls And Gotchas

- Using Millrace because it sounds more advanced, not because the task needs
  governance.
- Forgetting to ask the user which Millrace delegation authority you have when
  no autonomous policy or explicit Millrace request is already in force.
- Treating direct queue-folder mutation as equivalent to the CLI intake surface.
- Passing arbitrary markdown to `queue add-probe`; convert it into a valid
  `ProbeDocument` and keep supporting local files inside the active workspace.
- Acting as if Planning and Execution can overlap in shipped modes; Learning is
  the opportunistic concurrent lane, and only when the compiled policy permits
  it.
- Treating Contractor Blueprint as an implementation role. Contractor emits a
  Blueprint packet; Builder performs source edits only after Evaluator approval
  promotes a generated task into Execution.
- Starting a daemon as a plain background job when you actually need it to keep
  running; use a `tmux` pane for persistent daemon operation.
- Treating this repo-local operator skill as a runtime-shipped stage skill.
- Running an unbounded daemon when one explicit `run daemon --max-ticks 1` tick
  is the safer truthful move.
- Expecting Librarian-installed optional skills to appear in the base package;
  Librarian installs remote skills into the active workspace only.

## Progressive Disclosure

Start with the active delegation policy, the fallback fit test if needed, and
the CLI reference. Read deeper runtime docs only when the current operator
decision depends on them. Do not dump the full architecture into every turn if
a direct command or recommendation is enough.

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
millrace runs trace <run_id> --workspace <workspace>
```

If those surfaces do not support your claim, you do not yet know enough to make
it.
