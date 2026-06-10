# Config Mapping: Conceptual Configs To Mode IDs

This document inventories the current mode config assets and defines the mapping
from the five conceptual configuration profiles to public mode IDs, aliases,
fixture packages, or documented relationships.

## Shipped Mode Inventory

Twelve mode config assets live in `src/millrace_ai/assets/modes/`. Nine are
publicly declared in `SHIPPED_MODE_IDS`; three (`minimal_three_plane`,
`recovery_heavy_millrace`, and `generic_two_plane_fixture`) are fixtures
discoverable through asset discovery but not in the shipped defaults list.

### Two-Plane Standard Modes (execution + planning)

| # | Mode ID | Loops | Runner | Extensions | Shipped? |
|---|---------|-------|--------|------------|----------|
| 1 | `default_codex` | execution.standard, planning.standard | codex_cli | generic, recon, closure | Yes |
| 2 | `default_pi` | execution.standard, planning.standard | pi_rpc | generic, recon, closure | Yes |
| 3 | `default_codex_integrated` | execution.with_integrator, planning.standard | codex_cli | generic, recon, closure | Yes |
| 4 | `blueprint_codex` | execution.standard, planning.blueprint | codex_cli | generic, recon, closure, blueprint | Yes |

### Three-Plane Learning Modes (execution + planning + learning)

| # | Mode ID | Loops | Runner | Extensions | Shipped? |
|---|---------|-------|--------|------------|----------|
| 5 | `learning_codex` | execution.standard, planning.standard, learning.standard | codex_cli | generic, recon, closure, learning | Yes |
| 6 | `learning_pi` | execution.standard, planning.standard, learning.standard | pi_rpc | generic, recon, closure, learning | Yes |
| 7 | `efficient_learning_mixed` | execution.standard, planning.standard, learning.standard | mixed | generic, recon, closure, learning | Yes |
| 8 | `learning_codex_integrated` | execution.with_integrator, planning.standard, learning.standard | codex_cli | generic, recon, closure, learning | Yes |
| 9 | `blueprint_learning_codex` | execution.standard, planning.blueprint, learning.standard | codex_cli | generic, recon, closure, blueprint, learning | Yes |

### Fixture Modes (Discoverable, Not In SHIPPED_MODE_IDS)

| # | Mode ID | Loops | Runner | Extensions | Shipped? |
|---|---------|-------|--------|------------|----------|
| 10 | `minimal_three_plane` | execution.minimal_three_plane, planning.minimal_three_plane, learning.minimal_three_plane | pi_rpc | generic | No (fixture) |
| 11 | `recovery_heavy_millrace` | execution.standard, planning.standard | pi_rpc | generic, recon, closure | No (fixture) |
| 12 | `generic_two_plane_fixture` | execution.minimal_three_plane, planning.minimal_three_plane | pi_rpc | generic | No (fixture) |

## Conceptual Config Mapping

Five conceptual configuration profiles are defined for config-swap testing and
fixture-based behavior proof. Each maps to existing shipped mode IDs, aliases,
or dedicated fixture packages:

### 1. `minimal_three_plane`

- **Status**: Preserved fixture; already shipped as a discoverable mode asset.
- **Mapping**: Direct fixture ID `minimal_three_plane`.
- **Description**: Three-plane architecture proof fixture using custom minimal
  graph loops and stage kinds. One node per plane: `basic_worker` (execution),
  `basic_planner` (planning), `basic_learner` (learning). Only requires
  `millrace.generic`. Uses only generic lifecycle terminal actions
  (`complete_work_item`, `block_work_item`, `no_op_complete_work_item`).
- **Not a shipped product mode**: Not listed in `SHIPPED_MODE_IDS`.

### 2. `standard_millrace`

- **Status**: Documented alias.
- **Mapping**: Alias `standard_millrace` → canonical mode ID `default_pi`.
- **Description**: Standard two-plane (execution + planning) Millrace
  configuration using the `pi_rpc` runner. Selects `execution.standard` and
  `planning.standard` loops. Requires `millrace.generic`, `millrace.recon`,
  and `millrace.closure` extensions.
- **Alternative**: For `codex_cli` runner, use `standard_plain` →
  `default_codex`. For integrator-based execution, use
  `default_codex_integrated`.

### 3. `learning_enabled_millrace`

- **Status**: Documented alias.
- **Mapping**: Alias `learning_enabled_millrace` → canonical mode ID
  `learning_pi`.
- **Description**: Three-plane (execution + planning + learning) Millrace
  configuration using the `pi_rpc` runner. Selects `execution.standard`,
  `planning.standard`, and `learning.standard` loops. Learning trigger rules
  route Doublechecker passes and Troubleshooter/Consultant recovery events to
  the Analyst, and Planner completes to the Librarian. Requires
  `millrace.learning` in addition to the standard extension set.
- **Alternative**: For `codex_cli` runner, use `learning_codex`. For mixed
  runner profiles, use `efficient_learning_mixed`.

### 4. `recovery_heavy_millrace`

- **Status**: Fixture config (not a shipped product mode).
- **Mapping**: Direct fixture ID `recovery_heavy_millrace`.
- **Description**: Two-plane (execution + planning) configuration with
  aggressive recovery thresholds. Uses `execution.standard` and
  `planning.standard` loops with `pi_rpc` runner. Recovery behavior is
  expressed through mode-selected registry asset data, not mode-level code
  edits.
- **Recovery-policy data**: See
  `src/millrace_ai/assets/registry/recovery_policies/recovery_heavy_policies.json`.
  Provides alternative recovery policies with lower thresholds. Compared to the
  default recovery policies:
  - `execution.blocked_recovery_heavy`: threshold **1** (default `execution.blocked_recovery`: 3)
  - `planning.blocked_recovery_heavy`: threshold **1** (default `planning.blocked_recovery`: 2)
- **Mode-owned selection**: The mode asset declares `recovery_policy_ids` for
  `execution.blocked_recovery_heavy` and
  `planning.blocked_recovery_heavy`. Compilation resolves those workflow
  recovery policies from registry data and applies their lower threshold
  policies before runtime threshold resolution.
- **Distinction from product modes**: This fixture uses the same standard
  loops as `default_pi` but is not a shipped product mode. The recovery-heavy
  behavior comes from registry-level recovery-policy asset data with lower
  retry thresholds. Config-swap tests load this mode with the same default
  `RuntimeConfig` shape used by standard mode and prove the same kernel makes
  different recovery-routing decisions from mode-owned recovery-policy data.

### 5. `generic_two_plane_fixture`

- **Status**: Fixture config (not a shipped product mode).
- **Mapping**: Direct fixture ID `generic_two_plane_fixture`.
- **Description**: Minimal generic proof fixture that uses
  `execution.minimal_three_plane` and `planning.minimal_three_plane` loops
  with `basic_worker` and `basic_planner` stage kinds. Only requires
  `millrace.generic`. Contains no execution/planning/learning domain
  vocabulary: no Recon, Blueprint, closure, Arbiter, Manager, Mechanic,
  Planner disposition, or Learning promotion.
  The `basic_worker` → `builder` and `basic_planner` → `planner`
  runtime-stage bindings are a runner-contract and workspace-contract
  compatibility layer required by the current canonical `StageName` and
  `WorkItemKind` infrastructure; they do not imply arbitrary stage or
  family support.
- **Purpose**: Proves that a minimal config with only the generic extension
  can be expressed as data-only JSON and discovered through the asset system.
  It is the smallest config the current framework permits: two planes, one
  node each, no domain vocabulary, only `millrace.generic`.
- **Design decision**: True single-plane mode support is intentionally
  deferred. The current `ModeDefinition` validator requires both `execution`
  and `planning` planes. The fixture name accurately describes the actual
  two-plane requirement.
- **Not a shipped product mode**: Not listed in `SHIPPED_MODE_IDS`.

## Alias Summary

| Conceptual Config | Resolution | Canonical Mode ID | Kind |
|---|---|---|---|
| `standard_plain` | Alias | `default_codex` | Shipped product mode |
| `standard_millrace` | Alias | `default_pi` | Shipped product mode |
| `learning_enabled_millrace` | Alias | `learning_pi` | Shipped product mode |
| `minimal_three_plane` | Direct | `minimal_three_plane` | Fixture |
| `recovery_heavy_millrace` | Direct | `recovery_heavy_millrace` | Fixture |
| `generic_two_plane_fixture` | Direct | `generic_two_plane_fixture` | Fixture |

## Product Mode vs. Fixture Distinction

**Product modes** (in `SHIPPED_MODE_IDS`): Default configurations shipped as
the public Millrace product surface. These are the modes operators select via
`runtime.default_mode` in workspace config. They are subject to the
same-graph-safety rule and are validated on every workspace compile.

**Fixture modes** (not in `SHIPPED_MODE_IDS`): Discoverable config assets that
prove the runtime can compile and execute configurations beyond the default
product surface. They are outside the shipped defaults list but remain
available through explicit mode resolution, `load_builtin_mode_definition()`,
and `load_builtin_mode_bundle()` for testing and config-swap proof purposes.

## Verification

Asset discovery, compilation, config-swap, and behavior proof coverage for
these configs lives in:

- `tests/assets/test_modes.py` — mode loading, alias resolution, asset
  discovery, config-swap compilation, and extension/learning/recovery/generic
  fixture comparisons.
- `tests/integration/test_compiler.py` — end-to-end compilation tests across
  the conceptual config set.
- `tests/compilation/test_config_swap.py` — compilation-level config-swap
  tests for all five conceptual configs.
- `tests/runtime/test_config_swap_runtime.py` — `RuntimeEngine` startup and
  distinct compiled-plan checks across all five conceptual configs.
- `tests/assets/test_loop_graphs.py` — graph loop discovery and fixture graph
  validation.
- `tests/runtime/test_recovery_counter_thresholds.py`,
  `tests/runtime/test_runtime.py`, `tests/runtime/test_supervisor.py`,
  `tests/compilation/test_workflow_validation.py`, and
  `tests/compilation/test_extension_validation.py` — config-driven behavior
  tests that prove threshold, Learning, graph, and extension changes come from
  config data.
- `tests/assets/test_modes.py` and `tests/integration/test_compiler.py` also
  cover `recovery_heavy_millrace` using `RuntimeConfig()` without recovery
  overrides, proving mode-owned recovery-policy selection.

The implementation evidence for this verification is recorded in:

- `task-config-cleanup-swap-tests`
- `task-config-cleanup-behavior-tests`
- `task-recovery-policy-wire-remediation`
