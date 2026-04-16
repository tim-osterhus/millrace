# Millrace CLI Reference

Installed command: `millrace`  
Module entrypoint: `python -m millrace_ai`

## Defaults

- `--workspace` points to the operator workspace root.
- Runtime config defaults to `<workspace>/millrace-agents/millrace.toml`.
- Runtime bootstrap/output stays under `<workspace>/millrace-agents/`.

## Primary Command Groups

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

- `millrace add-task`, `millrace add-spec`
- `millrace pause`, `millrace resume`, `millrace stop`
- `millrace retry-active`, `millrace clear-stale-state`, `millrace reload-config`

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

### `millrace status show`

Prints runtime snapshot and queue depth for one workspace.
When a failure class is active, status also shows the current failure class plus non-zero retry counters.

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

Prints one run summary, including work item identity, failure class, stage results, stdout/stderr paths, and troubleshoot report path when present.

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

## Control Commands

- `millrace control pause`
- `millrace control resume`
- `millrace control stop`
- `millrace control retry-active --reason "..."`
- `millrace control clear-stale-state`
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

Requests a daemon-safe config reload. The runtime keeps the last-known-good compiled plan active if recompile fails and records the failure in snapshot state and runtime events.

## Compile + Modes Commands

### `millrace compile validate [--mode MODE_ID]`

Compiles active mode and emits diagnostics (`ok`, warnings/errors, fallback usage).

### `millrace compile show [--mode MODE_ID]`

Compiles and prints operator inspectability surface:

- `compiled_plan_id`
- execution/planning loop IDs
- stage ordering
- entrypoint path per stage
- `required_skills`, `attached_skills`

Entrypoint advisory model:

- `Required Stage-Core Skill`
- `Optional Secondary Skills`

### `millrace modes list`

Lists built-in modes and loop references.

### `millrace modes show --mode MODE_ID`

Prints one mode definition summary.

## Doctor Command

### `millrace doctor`

Runs workspace integrity diagnostics, including stale lock/ownership checks.
