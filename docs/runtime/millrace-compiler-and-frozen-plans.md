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
- per-node execution capability grants and grant warnings
- compiled transitions, resume policies, threshold policies, and completion
  behavior
- learning trigger rules, including direct-Curator destination metadata,
  Planner-to-Librarian optional-skill preparation, and scheduler
  lane/concurrency policy when the selected mode declares them
- workflow primitives: work-item families, document adapters, queue claim
  policies, terminal actions, lifecycle mutation plans, runtime effect handlers,
  recovery policies, runtime failure policies, runtime effect rules, and the
  active workspace schema epoch
- resolved asset references and content hashes

The runtime then consumes that compiled authority during startup, routing,
reconciliation, and run inspection.

Compile validation rejects learning trigger rules that target Curator directly
without `target_skill_id` or `preferred_output_paths`. Generic or vague learning
evidence should target Analyst so the learning plane can research, no-op, or
escalate without guessing a skill destination.
Learning-enabled shipped modes include a Planner-to-Librarian trigger for
`PLANNER_COMPLETE`; that target is safe because Librarian's destination is the
workspace-local optional-skill install surface, not a source-packaged skill.

## Authoritative Asset Surfaces

Current compile authority comes from:

- `modes/`
- `graphs/`
- `registry/stage_kinds/`
- workflow primitive registry assets under `registry/work_item_families/`,
  `registry/document_adapters/`, `registry/queue_claim_policies/`,
  `registry/terminal_actions/`, `registry/lifecycle_mutation_plans/`,
  `registry/runtime_effect_handlers/`, `registry/recovery_policies/`,
  `registry/runtime_failure_policies/`, and
  `registry/workspace_schema_epochs/`
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

- `millrace run daemon`
- daemon-safe config reload

Read-only commands such as `status`, `runs`, and queue inspection do not
compile implicitly.

Next-tick runtime config such as `usage_governance.*` does not change the
compiled plan and does not require recompile. Execution capability policy under
`execution_capabilities.*` does change the compiled plan and is treated as a
recompile boundary.

At runtime startup, Millrace invokes the same compiler path used by explicit
compile commands with `compile_if_needed=True`. If the persisted compiled plan
still matches current compile inputs, startup reuses it. If inputs changed,
startup recompiles before execution continues.

## Workflow Primitive Authority

Workflow primitives are data-driven runtime contracts, not advisory docs.
Their built-in assets define the work-item families Millrace can claim, the
document adapters used to parse them, per-plane queue claim policies, legal
terminal actions, source lifecycle mutation plans, runtime effect handlers, and
failure/recovery policy hooks. Artifact contracts are part of that same
authority surface: each declares an artifact id, canonical filename, accepted
legacy filenames, parser/schema, required outcomes, and consuming runtime
effect. Request-context rendering and runtime effects use those declarations
instead of stage-specific hard-coded filenames.

The architecture rationale is recorded in
`docs/adr/0010-compiler-validated-workflow-primitives-as-runtime-authority.md`.

The compiler loads those assets from the active asset root, includes their
content hashes in `resolved_assets`, validates cross-references, and persists
the selected primitive definitions into `compiled_plan.json`. Runtime modules
then read the compiled plan instead of maintaining separate hard-coded tables
for stage work-item ownership, queue claim policy, terminal lifecycle intent,
or effect-handler lookup.

Invalid primitive graphs fail at compile time. Examples include a queue claim
policy that references an unknown work-item family, a terminal action that
names a missing lifecycle plan or effect handler, a runtime effect rule that
targets a missing handler, an artifact contract referenced by an effect handler
but not declared, a runtime-effect failure policy that targets a node outside
the source plane, or a graph entry whose stage kind cannot own the declared
work-item family.

The compiler validates structure and cross-references. The runtime still owns
dynamic checks that require actual run artifacts or mutable queue state, such
as malformed canonical output files, partial effect mutations, stopped daemon
health, and closure blockers discovered after a stage finishes.

## Workspace Schema Epoch

Initialized workspaces carry a schema epoch marker at
`millrace-agents/state/workspace_schema_epoch.json`. The current compiled plan
also carries the active `workspace_schema_epoch` primitive. Runtime startup
checks the marker against the compiled epoch before loading mutable runtime
state.

If an old mutable state tree must cross an epoch boundary, the schema reset
helper refuses daemon-owned workspaces, moves mutable runtime directories under
`millrace-agents/archives/` by filesystem rename without parsing old JSON,
writes an archive manifest, initializes clean runtime state, writes the current
epoch marker atomically, and then compiles the active mode. If that post-reset
compile fails, the clean state and epoch marker remain inspectable and startup
still refuses to run invalid authority.

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
- stage/node request-binding details, including runner/model/thinking bindings
- execution capability summaries and per-node grants
- loop ids by plane
- scheduler lane policy, lane conflict policy, concurrency policy, and learning
  trigger rules when present
- `baseline_manifest_id`
- `compiled_plan_currentness`
- `completion_behavior.*` when present

`millrace compile graph` exports the compiled stage graph as a public contract
for one or more planes. Use it for legal topology inspection: node bindings,
entry surfaces, transitions, terminal states, loop ids, and source refs. It is
different from `millrace runs trace <run_id>`, which reads historical evidence
for one concrete run.

`millrace status` prints the live snapshot plus:

- `compiled_plan_id`
- `compiled_plan_fingerprint`
- pending compiled-plan identity when reload has compiled a plan that cannot
  yet replace active launch authority
- `compiled_plan_currentness`
- durable lane state and active-run launch-plan identity
- `active_node_id`
- `active_stage_kind_id`
- `compile_input.*`
- `persisted_compile_input.*`

Active runs keep the compiled-plan id and compact compile-input fingerprint
from the plan that launched them. If config reload compiles a newer plan while
work is active, that newer plan is recorded as pending and the active run
continues to route against its launch plan until it drains. That keeps result
application tied to the contract that produced the stage request.

## Why This Split Exists

Millrace is intentionally not "the runtime just trusts whatever files happen to
exist right now."

The product contract is:

- workspace assets form the mutable deployed baseline
- the compiler decides whether that baseline and config produce a valid
  compiled plan, including coherent workflow primitives
- the runtime executes from that compiled plan
- stale compile authority is refused instead of being treated as good enough
