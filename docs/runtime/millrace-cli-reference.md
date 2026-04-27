# Millrace CLI Reference

Installed command: `millrace`  
Module entrypoint: `python -m millrace_ai`

## Defaults

- `--workspace` points to the operator workspace root.
- Runtime config defaults to `<workspace>/millrace-agents/millrace.toml`.
- Runtime bootstrap/output stays under `<workspace>/millrace-agents/`.

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
- `conflict`
- `missing`

Use `millrace upgrade --apply` to apply only safe managed baseline updates.
Conflicts fail the apply and leave the workspace baseline unchanged.

Options:

- `--workspace PATH`
- `--apply`

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

The default monitor mode is `none`; `millrace run daemon` does not print live
monitor lines unless `--monitor basic` is passed explicitly. The existing
daemon summary output remains unchanged.

`--monitor basic` prints a compact terminal stream for visible daemon sessions:
startup lifecycle context, baseline/currentness identity, loop and concurrency
policy, status/queue snapshots, stage start and completion lines, router
decisions, usage-governance pause/resume/degraded events, run elapsed time, and
token usage. Monitor output is live-only and does not replace persisted runtime
events or run artifacts.

When the daemon is idle with `reason=no_work`, the basic monitor prints the
first idle line immediately and then treats repeated `no_work` idles as a
heartbeat. It emits that heartbeat at most once every 120 seconds while the
same idle condition continues. Any non-idle monitor event, or an idle event
with a different reason, resets the heartbeat.

## Status Commands

Canonical operator form: `millrace status`  
Explicit subcommand form: `millrace status show`

### `millrace status`

Prints runtime snapshot and queue depth for one workspace.
When a failure class is active, status also shows the current failure class plus non-zero retry counters.
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

## Planning Commands

### `millrace planning retry-active --reason "..."`

Requests a retry only when the active work is on the planning plane. If execution work is active instead, the runtime records a skipped retry action rather than mutating the wrong plane.

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

Installs a local skill directory or `SKILL.md` file into the selected skill
target.

Options:

- `--workspace PATH`
- `--target [workspace|source]`
- `--force`
- `--update`

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
- `millrace skills create <PROMPT>`
- `millrace skills improve <SKILL_ID>`
- `millrace skills promote <SKILL_ID>`
- `millrace skills export <SKILL_ID>`

Create/improve workflows require a learning-enabled mode such as
`learning_codex` or `learning_pi` because they enqueue learning requests for the
Analyst/Professor/Curator loop. Install/list/show/search can be used for the
deployed skill surface without changing the active runtime mode.

## Doctor Command

### `millrace doctor`

Runs workspace integrity diagnostics, including stale lock/ownership checks.
