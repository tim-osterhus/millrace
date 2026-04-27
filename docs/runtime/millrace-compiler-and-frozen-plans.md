# Millrace Compiler And Frozen Plans

## Scope

This document describes the compiler-owned authority model for Millrace:

- explicit compile surfaces
- compile-if-needed runtime startup behavior
- compile-input fingerprints and current-vs-stale status
- the persisted `compiled_plan.json` artifact
- stale-plan refusal when compile inputs drift
- the `millrace_ai.compiler` public facade over `src/millrace_ai/compilation/`
  internals

Use `docs/runtime/millrace-cli-reference.md` for command syntax and
`docs/runtime/millrace-modes-and-loops.md` for the shipped mode and graph
surfaces that feed the compiler.

The stable import surface is `millrace_ai.compiler`. Internally, compiler
ownership is split under `src/millrace_ai/compilation/`: workspace-plan
orchestration, preview materialization, mode/path resolution, node and graph
materialization, transition/completion/policy compilation, entrypoint override
validation, learning-trigger validation, asset resolution, fingerprints,
persistence, and currentness inspection live in named modules behind that
facade.

## What The Compiler Freezes

Millrace does not execute directly from loose workspace assets. The compiler
materializes one frozen run contract into
`<workspace>/millrace-agents/state/compiled_plan.json`.

That compiled plan freezes:

- one deterministic `compiled_plan_id`
- one selected `mode_id`
- graph authority for execution, planning, and optional learning
- selected loop ids by plane
- per-node `node_id` and `stage_kind_id`
- per-node entrypoint path, skill bindings, runner, model, and timeout
- compiled transitions, resume policies, threshold policies, and completion
  behavior
- learning trigger rules and plane concurrency policy when the selected mode
  declares them
- resolved asset references and content hashes

The runtime then consumes that compiled authority during startup, routing,
reconciliation, and run inspection.

## Authoritative Asset Surfaces

Current compile authority comes from:

- `modes/`
- `graphs/`
- `registry/stage_kinds/`
- `entrypoints/`
- `skills/`

`graphs/` is the authoritative topology surface.
`loops/` remains a compatibility and inspection surface only; it is not the
primary runtime authority.

## Compile Lifecycle

Millrace now treats compile as an explicit lifecycle with one authoritative
path.

Explicit operator commands:

- `millrace compile validate`
- `millrace compile show`
- `millrace config validate`

Runtime-owned compile-if-needed surfaces:

- `millrace run once`
- `millrace run daemon`
- daemon-safe config reload

Read-only commands such as `status`, `runs`, and queue inspection do not
compile implicitly.

Next-tick runtime config such as `usage_governance.*` does not change the
compiled plan and does not require recompile.

At runtime startup, Millrace invokes the same compiler path used by explicit
compile commands with `compile_if_needed=True`. If the persisted compiled plan
still matches current compile inputs, startup reuses it. If inputs changed,
startup recompiles before execution continues.

## Compile Input Fingerprint

Each compile attempt computes one `compile_input_fingerprint` with:

- `mode_id`
- `config_fingerprint`
- `assets_fingerprint`

`config_fingerprint` comes from the effective runtime config.
`assets_fingerprint` comes from the authoritative asset families listed above.

The persisted compiled plan stores this fingerprint. CLI surfaces then compare
the persisted fingerprint to the current expected fingerprint to decide whether
the plan is `current`, `stale`, or `missing`.

## Current Vs Stale Plan Status

Millrace exposes currentness through `millrace status` and related compile
inspection output.

`current` means:

- a persisted `compiled_plan.json` exists
- the plan's stored `compile_input_fingerprint` matches current expected inputs

`stale` means:

- a persisted plan exists
- current compile inputs no longer match that plan

`missing` means:

- no persisted plan exists yet

`unknown` is used by status surfaces when currentness could not be determined
cleanly, for example because config loading failed.

The CLI also prints:

- `compile_input.*` for the current expected fingerprint
- `persisted_compile_input.*` for the persisted plan fingerprint

## Baseline Manifest Identity

Compile currentness is related to, but distinct from, workspace baseline
identity.

The initialized workspace baseline stores
`<workspace>/millrace-agents/state/baseline_manifest.json`, which records the
managed deployed asset set and its original hashes.

Operator surfaces can show both:

- `baseline_manifest_id`
- `baseline_seed_package_version`
- compile-input fingerprints

That split matters:

- the baseline manifest identifies the deployed workspace baseline
- the compile fingerprint identifies whether the persisted compiled plan still
  matches current config and assets

## Stale-Plan Refusal

Millrace keeps compile failure diagnostics on disk for inspection, but it does
not allow a stale last-known-good plan to remain executable authority after
compile inputs drift.

The runtime rule is:

- if recompile fails and the persisted last-known-good plan still matches the
  current compile fingerprint, Millrace may keep using it
- if recompile fails and the persisted plan is stale, startup and config reload
  refuse to continue on that stale plan

That refusal is what preserves long-horizon stability without pretending that a
mismatched plan is still authoritative.

## Operator Surfaces

`millrace compile validate` prints:

- `ok`
- warnings/errors
- `used_last_known_good`
- `compile_input.*`

`millrace compile show` prints the same diagnostics plus:

- `compiled_plan_id`
- loop and graph identity
- stage/node request-binding details
- loop ids by plane
- concurrency policy and learning trigger rules when present
- `baseline_manifest_id`
- `compiled_plan_currentness`
- `completion_behavior.*` when present

`millrace status` prints the live snapshot plus:

- `compiled_plan_id`
- `compiled_plan_currentness`
- `active_node_id`
- `active_stage_kind_id`
- `compile_input.*`
- `persisted_compile_input.*`

## Why This Split Exists

Millrace is intentionally not "the runtime just trusts whatever files happen to
exist right now."

The product contract is:

- workspace assets form the mutable deployed baseline
- the compiler decides whether that baseline and config produce a valid
  compiled plan
- the runtime executes from that compiled plan
- stale compile authority is refused instead of being treated as good enough
