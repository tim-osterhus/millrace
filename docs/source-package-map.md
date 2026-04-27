# Millrace Source Package Map

This document records the post-refactor source layout under `src/millrace_ai/`, the mirrored test tree under `tests/`, and the intentionally preserved compatibility facades that keep older imports stable during the transition.

## Current Layout

- importable package code lives under `src/millrace_ai/`
- tests mirror ownership under `tests/assets/`, `tests/cli/`, `tests/config/`, `tests/runners/`, `tests/runtime/`, `tests/workspace/`, and `tests/integration/`
- the package entrypoints are `src/millrace_ai/__main__.py` and the `src/millrace_ai/cli/` package

## Old-To-New Module Map

| Legacy surface | Current source home | Notes |
| --- | --- | --- |
| `millrace_ai/cli.py` | `src/millrace_ai/cli/app.py`, `src/millrace_ai/cli/shared.py`, `src/millrace_ai/cli/formatting.py`, `src/millrace_ai/cli/monitoring.py`, `src/millrace_ai/cli/commands/*` | `millrace_ai.cli` is now a package surface; command groups live in dedicated modules and daemon monitor formatting is isolated. |
| `millrace_ai/runtime.py` | `src/millrace_ai/runtime/engine.py` plus `lifecycle.py`, `tick_cycle.py`, `activation.py`, `mailbox_intake.py`, `reconciliation.py`, `result_application.py`, `result_counters.py`, `work_item_transitions.py`, `handoff_incidents.py`, `stage_result_persistence.py`, `learning_triggers.py`, `skill_evidence.py`, `snapshot_state.py`, `monitoring.py`, `pause_state.py`, `usage_governance.py`, `closure_transitions.py`, `stage_requests.py`, `watcher_intake.py`, and `inspection.py` | `millrace_ai.runtime` is now a package that re-exports `RuntimeEngine` and `RuntimeTickOutcome`; `engine.py` remains the stable façade while owned collaborators hold lifecycle, tick, learning-trigger, monitor, pause-source, usage-governance, and routed-mutation details. |
| `millrace_ai/control.py` | `src/millrace_ai/runtime/control.py`, `src/millrace_ai/runtime/control_mailbox.py`, `src/millrace_ai/runtime/control_mutations.py` | Root `control.py` remains a thin compatibility facade. |
| `millrace_ai/config.py` | `src/millrace_ai/config/models.py`, `src/millrace_ai/config/loading.py`, `src/millrace_ai/config/boundaries.py` | `millrace_ai.config` is now a package surface; usage-governance config models live in `models.py` and apply on next-tick boundaries. |
| `millrace_ai/entrypoints.py` | `src/millrace_ai/assets/entrypoints.py` | Root `entrypoints.py` remains a thin compatibility facade. |
| `millrace_ai/modes.py` | `src/millrace_ai/assets/modes.py` | Root `modes.py` remains a thin compatibility facade. |
| `millrace_ai/stage_kinds.py` | `src/millrace_ai/assets/architecture.py`, `src/millrace_ai/architecture/stage_kinds.py` | Root `stage_kinds.py` is the thin public facade for stage-kind registry loading. |
| `millrace_ai/loop_graphs.py` | `src/millrace_ai/assets/loop_graphs.py`, `src/millrace_ai/architecture/loop_graphs.py` | Root `loop_graphs.py` is the thin public facade for graph-loop loading. |
| `millrace_ai/runner.py` | `src/millrace_ai/runners/requests.py`, `src/millrace_ai/runners/normalization.py` | Root `runner.py` remains a thin compatibility facade over the `runners` package. |
| `millrace_ai/run_inspection.py` | `src/millrace_ai/runtime/inspection.py` | Root `run_inspection.py` remains a thin compatibility facade. |
| `millrace_ai/paths.py` | `src/millrace_ai/workspace/paths.py` | Root `paths.py` remains a thin compatibility facade. |
| workspace initialization/baseline | `src/millrace_ai/workspace/initialization.py`, `src/millrace_ai/workspace/baseline.py` | Explicit `millrace init` and managed baseline upgrade classification live in workspace-owned modules. |
| `millrace_ai/runtime_lock.py` | `src/millrace_ai/workspace/runtime_lock.py` | Root `runtime_lock.py` remains a thin compatibility facade. |
| `millrace_ai/mailbox.py` | `src/millrace_ai/workspace/mailbox.py` | Root `mailbox.py` remains a thin compatibility facade. |
| `millrace_ai/events.py` | `src/millrace_ai/workspace/events.py` | Root `events.py` remains a thin compatibility facade. |
| `millrace_ai/work_documents.py` | `src/millrace_ai/workspace/work_documents.py` | Root `work_documents.py` remains a thin compatibility facade. |
| `millrace_ai/queue_store.py` | `src/millrace_ai/workspace/queue_store.py`, `queue_selection.py`, `queue_transitions.py`, `queue_reconciliation.py` | Root `queue_store.py` remains a thin compatibility facade over the workspace queue package. |
| `millrace_ai/state_store.py` | `src/millrace_ai/workspace/state_store.py`, `state_reconciliation.py` | Root `state_store.py` remains a thin compatibility facade over the workspace state package. |

## Phase-1 And Phase-2 Architecture Scaffolding

The loop-configurable runtime work now has a dedicated additive package and
asset family:

- `src/millrace_ai/architecture/stage_kinds.py` defines typed stage-kind contracts
- `src/millrace_ai/architecture/loop_graphs.py` defines typed graph-loop contracts
- `src/millrace_ai/architecture/materialization.py` defines the graph-plan materialization contracts, including normalized compiled entry/transition indexes, runtime-authority flags, and legacy-equivalence compatibility reporting
- `src/millrace_ai/assets/architecture.py` loads stage-kind registry assets
- `src/millrace_ai/assets/loop_graphs.py` loads graph-loop assets
- `src/millrace_ai/assets/registry/stage_kinds/` ships the stage-kind registry JSON
- `src/millrace_ai/assets/graphs/` ships the graph-loop JSON
- `src/millrace_ai/assets/loops/learning/default.json` and
  `src/millrace_ai/assets/graphs/learning/standard.json` ship the learning
  loop alongside execution and planning
- `src/millrace_ai/assets/modes/learning_codex.json` and
  `src/millrace_ai/assets/modes/learning_pi.json` select execution, planning,
  and learning loops with compiler-frozen learning trigger rules

This scaffolding now owns the runtime control-flow authority surface. The
legacy loop and router modules still remain in the package as compatibility and
inspection surfaces.

## Intentionally Preserved Root Modules

These modules remain at the package root because they still have one coherent reason to change or they define foundational contracts used across the package:

- `src/millrace_ai/contracts.py`
- `src/millrace_ai/compiler.py`
- `src/millrace_ai/doctor.py`
- `src/millrace_ai/router.py`
- `src/millrace_ai/watchers.py`
- `src/millrace_ai/errors.py`

Additional thin compatibility facades also exist at the root for the new
phase-1 architecture surfaces:

- `src/millrace_ai/stage_kinds.py`
- `src/millrace_ai/loop_graphs.py`

## Runner Package Notes

The built-in runner package now contains two first-class adapter paths:

- `src/millrace_ai/runners/adapters/codex_cli.py`
- `src/millrace_ai/runners/adapters/pi_rpc.py`

Shared runner-owned helpers live alongside them:

- `src/millrace_ai/runners/adapters/_prompting.py`
- `src/millrace_ai/runners/adapters/pi_rpc_client.py`

Mode assets in `src/millrace_ai/assets/modes/` freeze those built-in harness
presets through canonical mode ids:

- `default_codex`
- `default_pi`
- `learning_codex`
- `learning_pi`

`standard_plain` is preserved only as a compatibility alias in the asset-loading
layer, not as a third duplicated mode asset file.

## Test Ownership Map

| Source area | Mirrored tests |
| --- | --- |
| `src/millrace_ai/assets/` | `tests/assets/` |
| `src/millrace_ai/cli/` | `tests/cli/` |
| `src/millrace_ai/config/` | `tests/config/` |
| `src/millrace_ai/runners/` | `tests/runners/` |
| `src/millrace_ai/runtime/` | `tests/runtime/` |
| `src/millrace_ai/workspace/` | `tests/workspace/` |
| Cross-cutting operator/runtime flows | `tests/integration/` |

## Verification Commands

Use the same commands locally, in review artifacts, and in CI:

```bash
uv run --extra dev python -m pytest -q
uv run --with ruff ruff check src/millrace_ai tests
uv run --with mypy mypy src/millrace_ai
```
