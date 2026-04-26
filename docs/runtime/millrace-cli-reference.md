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

## Status Commands

Canonical operator form: `millrace status`  
Explicit subcommand form: `millrace status show`

### `millrace status`

Prints runtime snapshot and queue depth for one workspace.
When a failure class is active, status also shows the current failure class plus non-zero retry counters.
The `execution_status_marker` and `planning_status_marker` fields show the
currently running stage marker while a stage is executing, then fall back to
the latest terminal marker or `### IDLE` when no stage is active on that plane.
Status now also surfaces compiled-plan and managed-baseline identity:

- `compiled_plan_id`
- `compiled_plan_currentness` (`current`, `stale`, `missing`, or `unknown`)
- `active_node_id`
- `active_stage_kind_id`
- `baseline_manifest_id`
- `baseline_seed_package_version`
- `compile_input.*`
- `persisted_compile_input.*`

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

Prints queue/active counts for execution and planning surfaces.

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

## Planning Commands

### `millrace planning retry-active --reason "..."`

Requests a retry only when the active work is on the planning plane. If execution work is active instead, the runtime records a skipped retry action rather than mutating the wrong plane.

## Config Commands

### `millrace config show`

Prints the effective runtime defaults plus the snapshot-exposed config version and last reload outcome/error state.

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
- execution/planning loop IDs
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

### `millrace modes list`

Lists built-in modes and loop references.

### `millrace modes show MODE_ID`

Prints one mode definition summary.

## Doctor Command

### `millrace doctor`

Runs workspace integrity diagnostics, including stale lock/ownership checks.
