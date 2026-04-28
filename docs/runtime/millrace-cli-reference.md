# Millrace CLI Reference

Installed command: `millrace`  
Module entrypoint: `python -m millrace_ai`

## Defaults

- `--workspace` points to the operator workspace root.
- Runtime config defaults to `<workspace>/millrace-agents/millrace.toml`.
- Runtime bootstrap/output stays under `<workspace>/millrace-agents/`.
- Use `millrace --version` or `millrace version` to print the installed
  package version.

## Primary Command Groups

- `millrace init`
- `millrace upgrade`
- `millrace run ...`
- `millrace status ...`
- `millrace runs ...`
- `millrace queue ...`
- `millrace planning ...`
- `millrace config ...`
- `millrace control ...`
- `millrace compile ...`
- `millrace modes ...`
- `millrace skills ...`
- `millrace doctor`
- `millrace version`

Compatibility aliases remain for top-level operator commands:

- `millrace add-task`, `millrace add-spec`, `millrace add-idea`
- `millrace pause`, `millrace resume`, `millrace stop`
- `millrace retry-active`, `millrace clear-stale-state`, `millrace reload-config`

These top-level aliases use the same flags and behavior as their grouped forms.

## Workspace Setup Commands

### `millrace init`

Creates the canonical workspace baseline under `<workspace>/millrace-agents/`.
This is explicit now: most operator commands require an initialized workspace
and will tell you to run `millrace init --workspace <path>` first if the
baseline is missing.

Options:

- `--workspace PATH`

### `millrace upgrade`

Previews packaged managed-file updates against the workspace baseline manifest.
This command refreshes managed workspace assets only; it does not install or
upgrade the `millrace-ai` Python package that provides the runtime code. Update
the installed package through the deployment environment first, then run
`millrace upgrade` when the workspace baseline should be refreshed from that
installed package.
Default output is preview-only and prints:

- `applied`
- `baseline_manifest_id`
- `candidate_manifest_id`
- counts by disposition
- one `entry: <relative_path> <disposition>` line per managed file

When `--apply` succeeds, the command also prints `result_manifest_id`.

Dispositions currently exposed by the command:

- `unchanged`
- `safe_package_update`
- `local_only_modification`
- `already_converged`
- `localized_removed`
- `conflict`
- `missing`

Use `millrace upgrade --apply` to apply only safe managed baseline updates.
Conflicts fail the apply and leave the workspace baseline unchanged. If a
package release removes a managed asset that you intentionally want to keep as
workspace-local content, preview and apply with `--localize-removed PATH`.
For multiple paths, repeat the flag or use `--localize-removed-from FILE` with
one workspace-relative managed asset path per line.

Options:

- `--workspace PATH`
- `--apply`
- `--localize-removed PATH`
- `--localize-removed-from FILE`

## Run Commands

### `millrace run once`

Runs one deterministic startup+tick cycle.

Options:

- `--workspace PATH`
- `--mode MODE_ID`
- `--config PATH`

### `millrace run daemon`

Runs repeated ticks until stop/interrupt, or until `--max-ticks` is reached.

Options:

- `--workspace PATH`
- `--mode MODE_ID`
- `--config PATH`
- `--max-ticks N`
- `--monitor [none|basic]`
- `--monitor-log PATH`

The default monitor mode is `none`; `millrace run daemon` does not print live
monitor lines unless `--monitor basic` is passed explicitly. The existing
daemon summary output remains unchanged.

Daemon mode uses a compiled plane scheduler. Default modes remain serial.
Learning-enabled modes may run one Learning stage concurrently with one
permitted foreground Planning or Execution stage. Runtime-owned queue,
snapshot, counter, status, and router mutation remains single-writer and
serialized by the daemon supervisor.

`--monitor basic` prints a compact terminal stream for visible daemon sessions:
startup lifecycle context, baseline/currentness identity, loop and concurrency
policy, status/queue snapshots, stage start and completion lines, router
decisions, usage-governance pause/resume/degraded events, run elapsed time, and
known token usage. The basic monitor is optimized for human scanning: redundant
stage/node/kind identity is collapsed, long run ids are shortened to stable
display handles, intentionally absent route targets are not rendered as
`unknown`, and unknown token usage is omitted. Monitor output is live-only and
does not replace persisted runtime events, run artifacts, `millrace runs ls`,
or `millrace runs show`.

When the daemon is idle with `reason=no_work`, the basic monitor prints the
first idle line immediately and then treats repeated `no_work` idles as a
heartbeat. It emits that heartbeat at most once every 120 seconds while the
same idle condition continues. Any non-idle monitor event, or an idle event
with a different reason, resets the heartbeat.

`--monitor-log PATH` writes the same basic monitor format to a file. It can be
used with `--monitor none` for a quiet foreground daemon that still leaves a
clean monitor trail, or with `--monitor basic` to mirror the same live stream
to both stdout and a file.

## Status Commands

Canonical operator form: `millrace status`  
Explicit subcommand form: `millrace status show`

### `millrace status`

`millrace status` prints both the legacy foreground active projection and the
canonical `active_run` lines for every active plane. When Learning is running
beside a foreground lane, expect `active_run_count: 2` plus one line per plane.

Prints runtime snapshot and queue depth for one workspace.
When a failure class is active, status also shows the current failure class plus non-zero retry counters.
`process_running` is reported as true only when the snapshot says the runtime
is running and the workspace ownership lock is currently active. The
`runtime_ownership_lock` line reports the lock state separately.
The `execution_status_marker` and `planning_status_marker` fields show the
currently running stage marker while a stage is executing, then fall back to
the latest terminal marker or `### IDLE` when no stage is active on that plane.
When a learning-enabled mode is active, status also includes
`learning_status_marker` and `queue_depth_learning`.
Status now also surfaces compiled-plan and managed-baseline identity:

- `compiled_plan_id`
- `compiled_plan_currentness` (`current`, `stale`, `missing`, or `unknown`)
- `active_node_id`
- `active_stage_kind_id`
- `baseline_manifest_id`
- `baseline_seed_package_version`
- `compile_input.*`
- `persisted_compile_input.*`

Status also surfaces pause and usage-governance context:

- `pause_sources`
- `usage_governance_enabled`
- `usage_governance_paused`
- `usage_governance_blocker_count`
- `usage_governance_auto_resume_possible`
- `usage_governance_next_auto_resume_at`
- `usage_governance_subscription_status`
- `usage_governance_subscription_detail` when present
- `usage_governance_blocker: source=... rule=... window=... observed=... threshold=...`

When Arbiter closure is active, status surfaces closure-target backpressure:

- `closure_target_root_spec_id`
- `closure_target_open`
- `closure_target_blocked_by_lineage_work`
- `planning_root_specs_deferred_by_closure_target`
- `closure_target_latest_verdict_path`
- `closure_target_latest_report_path`

`millrace status show` is an explicit alias for the same output.

### `millrace status watch`

Polls runtime status repeatedly.

Options:

- `--workspace PATH` (repeatable; monitors multiple workspaces in one session)
- `--max-updates N`
- `--interval-seconds FLOAT`

`status watch` is monitor-only and does not acquire runtime ownership locks.

## Run Inspection Commands

### `millrace runs ls`

Lists persisted run summaries from `millrace-agents/runs/`.

### `millrace runs show <RUN_ID>`

Prints one run summary, including work item identity, compiled identity, failure
class, run-level elapsed time, aggregated token usage, per-stage elapsed time,
stdout/stderr paths, and troubleshoot report path when present.

Top-level run fields now include:

- `compiled_plan_id`
- `mode_id`
- `request_kind`
- `closure_target_root_spec_id`

Each stage-result block now includes:

- `compiled_plan_id`
- `mode_id`
- `node_id`
- `stage_kind_id`
- `request_kind`
- `closure_target_root_spec_id`

### `millrace runs tail <RUN_ID>`

Prints the primary tailable artifact for one run. Millrace prefers the troubleshoot report first, then stdout/stderr artifacts.

## Queue Commands

### `millrace queue ls`

Prints queue/active counts for execution, planning, and learning surfaces.

### `millrace queue show <WORK_ITEM_ID>`

Finds and prints one task/spec/incident document summary by ID.

### `millrace queue add-task <task.md|task.json>`

Imports `TaskDocument`. Canonical queue artifacts are markdown (`.md`); JSON is import-only.

### `millrace queue add-spec <spec.md|spec.json>`

Imports `SpecDocument`. Canonical queue artifacts are markdown (`.md`); JSON is import-only.

### `millrace queue add-idea <idea.md>`

Drops idea markdown into planning intake.

Top-level convenience alias:

- `millrace add-idea <idea.md>`

### `millrace queue repair-lineage --root-spec-id <ROOT_SPEC_ID>`

Previews safe queued/blocked work-document repairs when an open Arbiter closure
target has closure lineage drift. This is the supported recovery path when a
task/spec/incident is tied to the same root idea but has a mismatched
`Root-Spec-ID`.

Use `--apply` only while the daemon is stopped:

```bash
millrace queue repair-lineage --workspace <workspace> --root-spec-id <ROOT_SPEC_ID>
millrace queue repair-lineage --workspace <workspace> --root-spec-id <ROOT_SPEC_ID> --apply
```

Apply mode refuses a live daemon ownership lock or an active stage. It repairs
safe queued/blocked task lineage fields, writes a repair report under
`millrace-agents/arbiter/diagnostics/lineage-repairs/`, and emits a runtime
event.

## Control Commands

- `millrace control pause`
- `millrace control resume`
- `millrace control stop`
- `millrace control retry-active --reason "..."`
- `millrace control clear-stale-state --reason "..."`
- `millrace control reload-config`

Control routing behavior:

- If daemon owns the workspace: command is mailbox-routed.
- If no daemon owns the workspace: command applies directly.
Pause/resume behavior:

- `pause` adds the operator pause source.
- `resume` clears the operator pause source.
- `resume` does not bypass an active `usage_governance` pause source; the
  command reports that resume is blocked by usage governance until the active
  blocker clears or governance config changes.
- `reload-config` does not print a governance-specific cleared/remained
  summary. Governance changes are evaluated on the next runtime tick and are
  visible through `millrace status` and the basic daemon monitor.
- `reload-config` is deferred while active planes exist; the daemon requeues
  the reload command and applies it at the next safe boundary after active
  runs drain.
- unscoped `retry-active` is valid only when exactly one retryable active work
  item exists. If multiple planes are active, use a plane-scoped retry surface.
- `clear-stale-state` is the supported recovery command after an old
  closure-target invariant failure leaves an unrelated root spec half-claimed.
  It requeues active task, spec, incident, and learning-request artifacts,
  clears `active_runs_by_plane`, and preserves the open closure target.

## Planning Commands

### `millrace planning retry-active --reason "..."`

Requests a retry only when the active work is on the planning plane. If
execution or learning work is active instead, the runtime records a skipped
retry action rather than mutating the wrong plane. If Planning and Learning are
both active, the planning retry requeues only the Planning work item and leaves
the Learning lane active.

## Config Commands

### `millrace config show`

Prints the effective runtime defaults plus the snapshot-exposed config version and last reload outcome/error state.
The output includes `usage_governance.enabled`.

Usage governance is configured under `[usage_governance]` in
`millrace-agents/millrace.toml`. It is default-off. When enabled, runtime token
rules are evaluated between stages and can automatically pause/resume the
workspace without changing the compiled plan. See
`docs/runtime/millrace-usage-governance.md` for the full config shape and
state artifacts.

### `millrace config validate [--mode MODE_ID]`

Loads the effective config, compiles the selected mode, and prints compile diagnostics. This is the preferred operator-facing config validation command.

### `millrace config reload`

Requests a daemon-safe config reload. The runtime records reload failures in
snapshot state and runtime events. If recompile fails but the last-known-good
plan still matches current compile inputs, Millrace keeps that plan active. If
current compile inputs have drifted and the last-known-good plan is stale, the
reload is refused instead of continuing on the stale plan.

Usage-governance config is next-tick runtime state. A successful reload makes
new governance settings available to the next tick; `millrace status` and the
basic daemon monitor show whether a governance-owned pause cleared, remained,
or was newly applied.

Config changes that affect compile inputs, including `runtime.default_mode`
and `stages.<stage>.*`, are recompile changes. When a daemon owns the
workspace, `millrace config reload` is mailbox-routed and the daemon applies
the new compiled plan on the next tick. If the daemon was started with an
explicit `--mode`, that mode override remains pinned across reloads; start
without `--mode`, or with the intended mode, when config-driven mode selection
should take effect.

## Compile + Modes Commands

### `millrace compile validate [--mode MODE_ID]`

Compiles active mode and emits diagnostics (`ok`, warnings/errors,
last-known-good usage). Diagnostics now surface compile-input fingerprints:

- `compile_input.mode_id`
- `compile_input.config_fingerprint`
- `compile_input.assets_fingerprint`

### `millrace compile show [--mode MODE_ID]`

Compiles and prints operator inspectability surface:

- `compiled_plan_currentness`
- graph authority flags and graph entry surfaces
- graph node request-binding surfaces
- `compiled_plan_id`
- loop IDs by plane
- `baseline_manifest_id`
- `baseline_seed_package_version`
- `compile_input.*`
- `persisted_compile_input.*`
- frozen `completion_behavior.*` fields when the selected planning loop defines one
- stage ordering
- per-stage `model_reasoning_effort` when configured
- entrypoint path per stage
- `stage_kind_id`
- `running_status_marker`
- `runner_name`, `model_name`, `timeout_seconds`
- `required_skills`, `attached_skills`

Entrypoint advisory model:

- `Required Stage-Core Skill`
- `Optional Secondary Skills`

Currentness interpretation:

- `current`: persisted compiled plan matches the current mode/config/assets fingerprint
- `stale`: persisted compiled plan exists but does not match current compile inputs
- `missing`: no persisted compiled plan exists yet

## Runtime / Compile Lifecycle Notes

- `millrace init` is the explicit workspace bootstrap step.
- `millrace compile validate` and `millrace compile show` both persist fresh compile diagnostics.
- `compile_if_needed` style runtime paths reuse the persisted compiled plan only when its compile-input fingerprint still matches current inputs.
- Runtime startup and `config reload` refuse to continue on a stale last-known-good plan when compile inputs have changed and recompilation fails.
- `usage_governance.*` config fields apply on the next tick and do not require
  a recompile.

## Usage Governance Config

Usage governance is disabled by default. When enabled, Millrace evaluates usage
rules between stages and can pause the runtime with the `usage_governance`
pause source.

Top-level fields:

- `usage_governance.enabled`
- `usage_governance.auto_resume`
- `usage_governance.evaluation_boundary` (`between_stages`)
- `usage_governance.calendar_timezone`

Runtime token rules:

- `usage_governance.runtime_token_rules.enabled`
- `usage_governance.runtime_token_rules.rules`

Supported runtime token windows are `rolling_5h`, `calendar_week`,
`daemon_session`, and `per_run`. The supported metric is `total_tokens`.
Default enabled rules pause at `750000` total tokens over the rolling five-hour
window and `5000000` total tokens over the configured calendar week.

Subscription quota rules:

- `usage_governance.subscription_quota_rules.enabled`
- `usage_governance.subscription_quota_rules.provider`
- `usage_governance.subscription_quota_rules.degraded_policy`
- `usage_governance.subscription_quota_rules.refresh_interval_seconds`
- `usage_governance.subscription_quota_rules.rules`

The current subscription provider is `codex_chatgpt_oauth`, which reads
best-effort local Codex token-count telemetry. Subscription quota checks are
disabled by default and fail open by default when telemetry is unavailable.
Default subscription rules, when enabled, pause at 95 percent usage for the
`five_hour` and `weekly` windows.

### `millrace modes list`

Lists built-in modes and loop references.

### `millrace modes show MODE_ID`

Prints one mode definition summary.

## Skills Commands

The `millrace skills` command group manages the optional skill workflow and the
learning-plane skill-improvement surface.

### `millrace skills ls`

Lists installed workspace skills.

Options:

- `--workspace PATH`

### `millrace skills show <SKILL_ID>`

Prints one installed workspace skill's identity, path, and first markdown
heading when present.

Options:

- `--workspace PATH`

### `millrace skills search <QUERY>`

Searches installed workspace skill ids and skill markdown text.

Options:

- `--workspace PATH`

### `millrace skills install <SKILL_REF>`

Installs a local skill directory, local `SKILL.md` file, or supported remote
skill id into the selected skill target. Remote ids are resolved through the
public `tim-osterhus/millrace-skills` index and installed into the workspace as
normal local skills.

Options:

- `--workspace PATH`
- `--target [workspace|source]`
- `--force`
- `--update`

### `millrace skills refresh-remote-index`

Fetches the supported optional skill index from
`github.com/tim-osterhus/millrace-skills` and writes it to
`millrace-agents/skills/remote_skills_index.md`.

Options:

- `--workspace PATH`

### `millrace skills create <PROMPT>`

Queues a learning-plane request to create a new skill. The selected mode must
support the learning plane.

Options:

- `--workspace PATH`
- `--mode MODE_ID`
- `--foreground`

### `millrace skills improve <SKILL_ID>`

Queues a learning-plane request to improve an installed skill. The selected
mode must support the learning plane.

Options:

- `--workspace PATH`
- `--mode MODE_ID`
- `--foreground`

### `millrace skills promote <SKILL_ID>`

Copies a workspace skill into the source skill asset surface when running from
a source checkout.

Options:

- `--workspace PATH`

### `millrace skills export <SKILL_ID>`

Exports one installed workspace skill as a zip archive.

Options:

- `--workspace PATH`
- `--output PATH`

Command summary:

- `millrace skills ls`
- `millrace skills show <SKILL_ID>`
- `millrace skills search <QUERY>`
- `millrace skills install <SKILL_REF>`
- `millrace skills refresh-remote-index`
- `millrace skills create <PROMPT>`
- `millrace skills improve <SKILL_ID>`
- `millrace skills promote <SKILL_ID>`
- `millrace skills export <SKILL_ID>`

Create/improve workflows require a learning-enabled mode such as
`learning_codex` or `learning_pi` because they enqueue learning requests for the
Analyst/Professor/Curator loop. Install/list/show/search/refresh can be used
for the deployed skill surface without changing the active runtime mode.

## Doctor Command

### `millrace doctor`

Runs workspace integrity diagnostics, including stale lock/ownership checks.
Doctor also reports `closure_lineage_drift` when an open closure target has
same-root queued/active/blocked work under a different effective root spec.
