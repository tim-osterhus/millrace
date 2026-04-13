# Configuration Surfaces, Apply Boundaries, And Live Reload Semantics

## 1. Purpose And Scope

This document owns the runtime boundary that explains where Millrace configuration comes from, how changed fields are classified by runtime apply timing, and how the engine reloads, queues, applies, rejects, or rolls back config changes while it is running.

It covers typed native config loading in `millrace_engine/config.py`, apply-boundary taxonomy in `millrace_engine/config_runtime.py`, engine-owned reload/apply lifecycle in `millrace_engine/engine_config_coordinator.py`, and operator-facing runtime/report surfaces composed through `millrace_engine/control_runtime_surface.py`.

It does not own runner command semantics, stage legality, queue-state recovery outside config apply behavior, or arbitrary runtime mutation beyond the supported config-set and reload paths. Those remain with the runner, execution, and recovery docs.

## 2. Source-Of-Truth Surfaces

The authoritative surfaces for this boundary are:

- `millrace_engine/config.py`: defines the native TOML config model, resolves workspace-relative paths, loads config from disk, and computes changed dotted field names with `diff_config_fields()`.
- `millrace_engine/config_runtime.py`: defines `ConfigApplyBoundary`, `ConfigBoundaries`, and the exact field-prefix taxonomy for `live_immediate`, `stage_boundary`, `cycle_boundary`, and `startup_only`.
- `millrace_engine/engine_config_coordinator.py`: owns runtime reload, apply, queueing, pending-config state, rollback, reload retries, event emission, and watcher-restart detection.
- `millrace_engine/control_runtime_surface.py`: contributes operator-facing runtime and supervisor surfaces that expose the engine’s live state and pending work through supported report views.
- `tests/test_config.py`: proves typed config load and boundary classification across representative field families.
- `tests/test_cli.py`: proves daemon-time config mutation behavior, watched-file reload, startup-only rejection, pending cycle-boundary visibility, and stage-boundary apply timing.

Authority order for this boundary is:

1. typed config models and field classifiers in `config.py` and `config_runtime.py`
2. runtime apply lifecycle in `engine_config_coordinator.py`
3. operator-visible reports and CLI behavior proven by `tests/test_cli.py`
4. deep docs and portal links

If the docs disagree with the coordinator or tests, the coordinator and tests win.

## 3. Lifecycle And State Transitions

### 3.1 Native TOML Is The Supported Config Source

`load_engine_config()` only accepts native `millrace.toml`. If the path does not exist, it raises `FileNotFoundError`; this boundary no longer treats legacy markdown config as a runtime source of truth.

After validation, `_finalize_config()` resolves:

- `paths.workspace`
- `paths.agents_dir`
- every stage prompt path under `config.stages`

So the loaded config is already runtime-ready before the engine uses it.

### 3.2 Config Diffs Become Dotted Changed Fields

The coordinator does not compare raw text files. It compares typed config payloads through `diff_config_fields()`, which returns stable dotted field names such as:

- `engine.poll_interval_seconds`
- `execution.quickfix_max_attempts`
- `watchers.roots`
- `paths.agents_dir`

Those dotted fields are the input to runtime apply classification. This is why the apply contract is deterministic even when multiple config sections exist in the TOML.

### 3.3 Exact Apply-Boundary Taxonomy

`ConfigApplyBoundary` has four exact values:

- `live_immediate`
- `stage_boundary`
- `cycle_boundary`
- `startup_only`

`ConfigBoundaries` then maps field families into those buckets.

`startup_only` includes workspace-root ownership:

- `paths.workspace`
- `paths.agents_dir`

`live_immediate` includes the small set of fields the engine can update directly:

- `engine.poll_interval_seconds`
- `engine.inter_task_delay_seconds`

`stage_boundary` includes execution-affecting fields that must wait until a safe stage edge:

- `engine.mode`
- `execution.*`
- `routing`
- `policies.search`
- `policies.compounding`
- `policies.complexity`
- `policies.usage`
- `policies.network_guard`
- `policies.preflight`
- `policies.outage`
- `stages`

`cycle_boundary` includes fields that must wait until the next full cycle break:

- `engine.idle_mode`
- `sizing`
- `research.*`
- `sentinel`
- `watchers`

If a field is not matched explicitly, classification falls back to `cycle_boundary`, not to immediate mutation.

### 3.4 What The Coordinator Does With Each Boundary

`EngineConfigCoordinator.queue_or_apply_reloaded_config()` is the core lifecycle method.

If there are no changed fields, it returns a no-op operation result with `applied=False`.

If the strictest changed field is `startup_only`, it raises `ControlError`. Runtime mutation is rejected for those fields.

If the strictest boundary is `live_immediate`, the coordinator:

1. emits `CONFIG_CHANGED`
2. applies the loaded config immediately
3. emits `CONFIG_APPLIED`
4. optionally asks the engine to restart watchers when `engine.idle_mode` or `watchers.*` changed

If the strictest boundary is `stage_boundary` or `cycle_boundary`, the coordinator does not apply immediately. Instead it stores:

- `pending_loaded`
- `pending_boundary`
- `pending_changed_fields`

and returns an operation result that says the config was queued for that boundary.

### 3.5 When Pending Config Actually Applies

Queued config is applied through `apply_pending_config_if_due(boundary=...)`.

The coordinator uses `_boundary_allows()` with a strict order:

- `live_immediate`
- `stage_boundary`
- `cycle_boundary`
- `startup_only`

The practical effect is:

- a pending `stage_boundary` config may apply at a stage boundary
- a pending `cycle_boundary` config waits longer and does not apply at a mere stage boundary
- queued config clears only when the relevant boundary is reached and the new config is installed

This is why the runtime can truthfully expose pending config state between cycles. The active config hash stays unchanged until the queued boundary is actually reached.

### 3.6 Reload, Retry, And Rollback

`reload_config_from_disk()` is retrying and fail-closed. It:

- attempts native reload up to three times
- runs `_assert_reload_safe()` before accepting the reload
- emits a rejection event and returns `(False, False)` if reload never succeeds

Applied config also arms rollback state. `_apply_loaded_config_locked()` stores the previous loaded config, clears pending state, and marks rollback as armed. `rollback_active_config()` can then reinstall the previous loaded config, emit a rollback `CONFIG_APPLIED` event, and clear pending state.

## 4. Failure Modes And Recovery

### 4.1 Startup-Only Runtime Mutation Is Rejected

The clearest hard failure is a daemon-time attempt to mutate startup-only fields such as `paths.agents_dir`. `tests/test_cli.py` proves that this is rejected rather than silently queued or partially applied.

That is a safety contract, not a missing feature.

### 4.2 Invalid Or Unsafe Reloads Fail Closed

Reload is guarded by typed validation plus `_assert_reload_safe()`. If reloading from disk fails or produces an unsafe mutation, the coordinator does not crash the daemon and does not install a half-valid config. It emits a rejected config-change event instead.

### 4.3 Pending Config Is Not Active Config

A common operator mistake would be to assume that a successful `config set` always means the new config is already active. That is false for `stage_boundary` and `cycle_boundary` changes.

For queued changes:

- the active config hash stays on the old config
- pending config state is preserved separately
- reports may show `pending_config_hash` and `pending_config_boundary`

This is a feature, not drift.

### 4.4 Unsupported Mutation Paths

This boundary only blesses supported config change paths:

- native TOML load
- watched-file reload
- runtime config set / coordinator apply paths

It does not bless ad hoc file surgery against runtime-owned state files as a substitute for the coordinator contract.

## 5. Operator And Control Surfaces

The main operator-facing surfaces are:

- `millrace config show --json`, which exposes the typed runtime config
- daemon-time `config set` flows, which can apply, queue, or reject changes depending on the boundary
- status and supervisor/runtime state surfaces that expose pending config metadata during queued states
- event logs carrying `CONFIG_CHANGED` and `CONFIG_APPLIED`

Practical operator guidance:

- treat `startup_only` fields as restart-time changes only
- expect `live_immediate` changes such as `engine.poll_interval_seconds` to apply directly
- expect `stage_boundary` changes such as `stages.qa.model` to take effect before the next eligible stage, not in the middle of the current running stage
- expect `cycle_boundary` changes such as `research.idle_mode` or `engine.idle_mode` to remain pending until the next cycle boundary

Two CLI-proven examples matter:

- runtime-safe config changes can be issued while the daemon runs
- pending cycle-boundary config is visible between cycles before it becomes active

This is the supported deterministic mutation story for operators. Anything broader would overclaim current runtime behavior.

## 6. Proof Surface

The strongest proof for this boundary comes from:

- `tests/test_config.py`, which verifies:
  - typed native config load for complexity, compounding, watchers, and sizing
  - explicit boundary classification for representative stage, cycle, live, and startup fields
  - `ConfigApplyBoundary` values and re-exports stay stable
- `tests/test_cli.py`, which verifies:
  - runtime-safe daemon hotswap applies supported changes and rejects startup-only changes
  - watched config-file edits can trigger live-immediate reload
  - queued cycle-boundary config exposes pending-config state before application
  - stage-boundary config applies before the next stage and is reflected in run provenance

Packaging proof for this doc remains intentionally narrow:

- `tests/test_package_parity.py` must require the public and packaged Run 07 doc paths plus the IA and portal mirrors
- `tests/test_baseline_assets.py` must require the bundled path and key markers for boundary taxonomy, queued apply behavior, and startup-only rejection
- `millrace_engine/assets/manifest.json` must contain the correct SHA and size for the shipped packaged doc

Drift should fail proof when:

- the public and packaged docs diverge
- the IA still points at the stale Run 07 filename
- the doc claims queued config is already active
- the doc implies startup-only changes are live-mutable

## 7. Change Guidance

Update this doc when changes affect:

- native config load or finalization rules
- field-to-boundary classification
- queued apply timing
- pending-config visibility
- rollback or reload rejection behavior

Do not expand this doc to absorb:

- runner/model/permission semantics
- stage legality or stage transition ownership
- broader recovery behavior beyond config rollback/apply lifecycle

If a future change primarily alters runner behavior, route it to the runner boundary doc. If it primarily alters control mutation semantics unrelated to config ownership, route it to the control-plane doc. Keep this document focused on configuration surfaces, apply-boundary governance, and the current live-reload/deferred-apply contract.
