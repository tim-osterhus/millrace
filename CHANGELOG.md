# Changelog

All notable user-facing changes to Millrace are documented in this file.

This changelog is written for people first. It uses newest-first release order,
ISO `YYYY-MM-DD` dates, and change categories inspired by
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Version tags follow
SemVer-style numbering per [Semantic Versioning](https://semver.org/) while
Millrace is still in the pre-1.0 stabilization period; compatibility notes call
out operator-visible contract changes when they matter.

This file starts at `0.13.0`, the current documented public baseline.

## [Unreleased]

### Added

- Added trusted projection metadata for selected runtime authority and operator
  projections.
- Added selected closure-evidence snapshots with deterministic ordering and
  durable restart preservation.

### Fixed

- Made verdict admission and restart reconciliation strict. Invalid evidence
  now causes a durable refusal instead of a restart fallback.
- Added fail-closed capacity checks that reject oversized requests before
  external dispatch and enforce the 65536-byte Millforge instruction-envelope
  limit.

## [0.22.2] - 2026-08-05

### Added

- Added durable, plan-pinned daemon budget epochs for wall time, accepted
  starts, and reviewed adapter-reported token usage. Exhaustion suspends new
  dispatch atomically while preserving accepted runner-session authority.
- Added replay-safe `queue cancel` and atomic `queue cancel-lineage` controls.
  They reuse durable close-work transitions, refuse live or unresolved runner
  aftermath, and project bounded queue-closure audit evidence without deleting
  queue state or signaling adapters.
- Added replay-safe global dispatch suspension and exact-identity resumption.
  The distinct versioned record gates only new claim acceptance, preserves
  workflow pause semantics and accepted work, and projects bounded
  pre-suspension work across status, runs, trace, and doctor.
- Advanced the fresh workspace store to schema 8. Exact schema-6 and schema-7
  workspaces refuse unchanged as `workspace_upgrade_required`.
- Added durable, fenced runner sessions with replay-safe operator
  cancellation, restart reconciliation, bounded run/status/trace/doctor and
  daemon-stop projections, stable session refusal/diagnostic codes, and an
  explicit schema-6-to-7 `workspace_upgrade_required` boundary that leaves
  old database and CAS bytes unchanged.
- Added a separately versioned, bounded runner-session event sidecar and the
  finite `millrace runs follow RUN_ID --after-sequence N` projection. Live
  events are redacted across transport chunks, compact progress under fixed
  record, byte, stream, page, and rate ceilings, report explicit history gaps,
  and reconcile final status from durable runner-session completion rather
  than creating workflow authority.
- Added schema-3 Codex projections for canonical selected artifact schema
  declarations and marker-specific terminal artifact contracts. The
  deterministic projection refuses incoherent selected material before
  external invocation.
- Added read-only rejected runner-result inspection to `runs show`, including
  restart-safe adapter-error and refused-application metadata plus an explicit
  opt-in for already-redacted canonical evidence and bounded diagnostics.

### Fixed

- Preserved the exact `workspace_upgrade_required` public refusal when the
  daemon opens a schema-version-6 workspace instead of rewriting it as a
  generic daemon state-open failure.
- Replaced Millforge's temporary synchronous runner bridge with the generic
  runner-session lifecycle. Millforge now returns a live session handle,
  supports truthful cooperative cancellation for factory-created facades,
  closes owned facades exactly once, and reports orphan risk when execution or
  owned cleanup remains unresolved. Generic terminal completion now requires
  clean handle cleanup before persistence. Injected facades remain
  caller-owned, while their timeout, cancellation, and raised-execution paths
  now retain orphan risk because local thread exit cannot prove external work
  stopped.
- Hardened runner-session restart reconciliation so adapters must prove the
  complete selected dispatch authority, cleanup can resume only for the
  durably correlated handle, and missing or corrupt restart locators fail
  closed without mutating runtime state. Startup now classifies active sessions
  before resuming created work, live reconciled handles receive the same
  emergency-cleanup guard as initial starts, and versioned coordinator locators
  safely recognize pre-envelope locator metadata without granting cleanup
  authority. Raw handle identity is rejected before locator redaction, and a
  verified live legacy-running session durably upgrades its exact handle proof
  before polling or cleanup resumes.

## [0.22.0] - 2026-07-23

### Added

- Added a generic workflow-package compiler and immutable compiled-plan
  runtime.
- Added durable queues, runs, artifacts, waits, interventions, traces, daemon
  execution, and local operator commands.

### Changed

- Made Millforge the default runner for eligible newly compiled plans while
  keeping Codex explicitly selectable. Existing selected plans are not
  remapped.
- Split the ecosystem into the `millrace-ai` runtime, `millrace-plus` official
  workflows and authoring skills, the independent `millforge` execution
  harness, and the dependency-only `millrace` bundle.
- Published the current dependency-only bundle as `millrace==0.22.1`, retaining
  the tested `millrace-ai==0.22.0`, `millrace-plus==0.22.0`, and
  `millforge==0.1.0` member pins.

### Removed

- Removed the legacy `millrace-web` sidecar from the distributed product.
- Temporarily removed `Pi` support as a usable agent harness.

### Compatibility Notes

- v0.22 is a clean compatibility break and a full overhaul of how Millrace
  operates. It does not open or migrate v0.21 workspaces, state, configuration,
  snapshots, imports, or removed command surfaces. Start v0.22 with a new
  workspace and new runtime state.

## [0.21.1] - 2026-07-21

### Fixed

- Fixed Consultant `NEEDS_PLANNING` handoffs creating both the specific
  Consultant-authored incident and a second generic runtime incident. The
  runtime now discovers the declared incident from Consultant's decision,
  validates and registers a canonical authored incident when valid, and
  synthesizes a runtime fallback only when no valid authored incident exists.
- Made Consultant handoff registration durable and idempotent across result
  replay, daemon restart, incident lifecycle movement, and same-name queue
  collisions. Invalid or conflicting incoming artifacts are quarantined with
  audit evidence instead of remaining claimable by Auditor.
- Made runtime metadata updates to adopted Consultant incidents atomic and
  reset the registration cache on each runtime startup so sequential daemon
  owners observe the first durable registration without duplicating it.

## [0.21.0] - 2026-06-15

### Added

- Added conceptual config mappings (`config-mapping.md`) documenting the five
  tested configuration profiles and their resolution to shipped mode IDs,
  aliases, or fixture packages.
- Added config-swap behavior tests proving graph-only, recovery-policy-only,
  and required-extension declarations change runtime behavior without Python
  source edits.
- Added absence and maintenance guardrail tests proving minimal fixture configs
  do not load undeclared Recon, Blueprint, closure, or Learning domain behavior.
- Added structural AST guardrails that forbid family-ID branch tables, forbidden
  domain string literals as active-kernel control flow, and kernel-direct domain
  imports outside documented compatibility facades.

### Changed

- Restored the optional `millrace-web` sidecar package and synchronized
  release/CI smoke paths so `v0.21.0` publishes both `millrace-ai` and the
  read-only local dashboard while keeping web dependencies out of the base
  runtime package.
- Tightened Arbiter closure freshness discipline: Arbiter prompts now require
  reading the runtime-authored freshness window before old verdict/report
  artifacts, per-criterion evidence provenance, and runtime-owned closure
  remediation enqueueing. Shipped closure-capable modes now assign Arbiter via
  an explicit high-depth model alias so missing or downgraded assignment is
  visible in compile diagnostics.
- Made the pure graph-authority cleanup explicit as a breaking pre-1.0
  contract: runtime decisions require compiled graph, extension, policy,
  lifecycle, runtime-effect, queue-family, and artifact-contract metadata.
  Missing compiled policy now fails as an authority error instead of falling
  back to shipped defaults; stale workspaces may need managed asset refresh or
  reinitialization.
- Cleaned up legacy graph-authority authority paths: per-plane routing wrappers
  are now compatibility-only facades, route-time max-cycle recovery knobs are
  removed, and fallback route reasons/classes derive from compiled `node_id`
  rather than `source_stage.value`.
- Bounded routine runtime-event reads and idle-event growth: status, closure,
  and inspection surfaces now use streaming/recent/latest event helpers for normal
  operation, while durable no-work idle events are transition and heartbeat
  records instead of one append per idle tick. The default durable idle-event
  heartbeat is 6 hours, with explicit shorter operator configuration still
  supported.
- Removed the fixed Recon terminal-operation whitelist; Recon routing now
  resolves through compiled terminal-action `runtime_operation_id` metadata.
- Reworked `stage_metadata.py` to derive shipped stage legality from JSON
  stage-kind registry assets instead of hard-coded enums; fixture stage kinds
  remain discoverable without entering the shipped-stage facade.
- Route caller-level family-ID branching in active-kernel claim, lifecycle,
  status, and counter code through compiled policy and the generic
  `QueueFamilyInterpreter`.
- Updated shipped mode `required_extensions` declarations to list only the
  built-in extensions each mode actually uses; compile validation rejects
  undeclared domain vocabulary.
- Moved neutral router decision contracts to `millrace_ai.contracts.router`;
  `millrace_ai.router` remains importable only as a contract facade, and active
  runtime dispatch no longer imports it.

### Fixed

- Fixed compiled-plan currentness so persisted plans missing scheduler or
  selected recovery-policy authority are treated as stale and recompiled
  instead of being reused as incomplete modern plans.
- Fixed installed-wheel smoke failures by declaring `packaging>=24,<27` as a
  runtime dependency for extension validation's `packaging.version` import.
- Fixed daemon reconciliation so a lane's persisted active-run ownership is not
  replaced while the supervisor still owns a live or pending-completion worker
  for that same lane, and occupied-lane deferrals now emit an auditable
  `runtime_reconciliation_deferred` diagnostic decision.
- Fixed runtime recovery-counter reads so generic `counter_id` records are
  authoritative (including explicit `0` values) with no legacy fixed-field
  compatibility projection.
- Bounded basic terminal monitor run/display state so long daemon monitor
  sessions do not retain unbounded completed-run handles while active run
  handles remain stable.

### Documentation

- Reframed public documentation around Millrace as a loop
  engineering/runtime framework. LAD is now described as one shipped
  code-development loop family, with `lad_codex`, `lad_pi`,
  `learning_lad_codex`, `efficient_learning_lad_mixed`,
  `learning_lad_pi`, `lad_codex_integrated`,
  `learning_lad_codex_integrated`, `blueprint_lad_codex`, and
  `blueprint_learning_lad_codex` as the recommended mode names.
- Updated graph and mode documentation to use `execution.lad`,
  `execution.lad_integrator`, and `planning.lad` as canonical loop IDs while
  documenting the former `execution.standard`, `execution.with_integrator`,
  `planning.standard`, `default_*`, old unqualified `learning_*`,
  `efficient_learning_mixed`, and conceptual `standard_*` names as
  compatibility aliases.
- Documented closure freshness windows, historical versus current Arbiter
  evidence, runtime-owned remediation incidents, repeated-remediation guard
  interpretation, and operator diagnosis for `closure_repeated_remediation_blocked`.
- Updated `README.md`, `ROADMAP.md`, graph docs, runtime docs, source package
  map, and maintenance registers to accurately describe the generic engine
  boundary, extension-backed domains, compatibility-only surfaces, fixture
  mode limitations, and unsupported topologies (arbitrary plane IDs and
  arbitrary runtime stages remain deferred).
- Clarified the public README and runner docs comparison with Claude Code and
  other coding harnesses: Millrace does not claim those harnesses lack hooks,
  skills, subagents, memory, or workflow helpers; the distinction is that
  Millrace owns the external compiled-plan, queue, daemon, recovery, and
  closure layer around bounded harness invocations.
- Documented the runtime idle/event-log memory-bounds contract: durable idle
  suppression is in-memory only and separate from live terminal monitor idle
  output throttling; `read_runtime_events()` is reserved for explicit
  full-history audit/debug use.

### Compatibility Notes

- Package-root compatibility facades (`router.py`, `compiler.py`,
  `queue_store.py`, `runner.py`, `paths.py`, `state_store.py`,
  `stage_kinds.py`, `loop_graphs.py`) remain importable; active runtime
  authority derives from the compiled plan and compiled workflow primitives.
- Blueprint remains available through `blueprint_lad_codex` and
  `blueprint_learning_lad_codex`, but it is extension-backed graph configuration.
  Generic packages do not export Blueprint compatibility APIs; old Python
  imports of retired Blueprint facades such as
  `millrace_ai.contracts.blueprint`, `millrace_ai.cli.status.blueprint`,
  `millrace_ai.runtime.context.blueprint`, and
  `millrace_ai.workspace.blueprint_state` may now raise `ImportError`.
- `standard_plain`, `standard_millrace`, and `learning_enabled_millrace` remain
  compatibility aliases that resolve to `lad_codex`, `lad_pi`, and
  `learning_lad_pi` respectively.

## [0.20.3] - 2026-05-28

### Added

- Added `efficient_learning_lad_mixed`, a learning-enabled LAD mode that keeps
  standard Execution, Planning, and Learning topology, leaves Integrator off by
  default, and carries a mode-local mixed Codex/Pi stage alias profile.

### Changed

- Completed a broad maintainability refactor across compiler validation,
  workflow primitive contracts, runtime effect operations, runtime recovery,
  request context, workspace family adapters, and compatibility facades without
  intentionally changing shipped runtime behavior.
- Reworked the public `README.md` so the first screen explains Millrace
  directly, compares it to Claude Code, LangGraph, and Archon, and moves dense
  implementation detail into the technical docs.
- Updated the technical overview and graph/runtime/operator docs for Blueprint
  Planning, JSON-backed Blueprint drafts, stale-plan refusal, mode-local model
  aliases, current operator surfaces, and the current source ownership map.

### Fixed

- Restored structured Blueprint closure-blocker reporting after the
  maintainability refactor so Blueprint lineage blockers remain visible to
  runtime status and diagnosis surfaces.

### Compatibility Notes

- This is a prerelease stabilization build for the experimental `0.20.x`
  workflow-graph and Blueprint Planning line. It has source, package, and
  release-smoke coverage, but it has not yet received the same real-runner E2E
  efficacy validation as `0.19.0`.
- `millrace-ai` and `millrace-web` are released together as `0.20.3`;
  `millrace-web` now depends on `millrace-ai>=0.20.3`.

## [0.20.2] - 2026-05-28

### Added

- Added graph-declared default runtime repair routing so unclassified Planning
  and Execution runtime blockers route to the active graph's Mechanic or
  Troubleshooter stage when no more specific policy overrides them.
- Added compiler-owned model assignment aliases with default `fast`,
  `standard`, and `deep` aliases, plus `millrace model-aliases ...` commands
  for workspace-local alias and assignment management.

### Changed

- Changed invalid Recon handoffs to route to the active Planning repair node
  while attempts remain, instead of always hard-blocking the probe immediately.
- Updated Manager task instructions to preserve probe-root intake labels and to
  omit `Root-Idea-ID` when the active spec has no idea lineage.
- Alias assignments now supersede graph node defaults, `stages.<stage>.model`,
  and mode `stage_model_bindings`; invalid selected aliases emit compile
  warnings and fall back instead of blocking daemon startup. Alias changes are
  recompile changes and use existing pending-plan reload semantics for active
  daemons.

### Compatibility Notes

- This is a prerelease stabilization build for the experimental `0.20.x`
  workflow-graph and Blueprint Planning line. It has source and package smoke
  coverage, but it has not yet received the same real-runner E2E efficacy
  validation as `0.19.0`.
- `millrace-ai` and `millrace-web` are released together as `0.20.2`;
  `millrace-web` now depends on `millrace-ai>=0.20.2`.

## [0.20.1] - 2026-05-22

### Added

- Added structured Blueprint repair diagnostics so repairable approval-effect
  failures preserve the failed generated task, Blueprint packet, evaluation,
  runtime effect, and acceptance mismatch context for Mechanic Blueprint.
- Added family-aware blocked work-item retry support for graph-owned queue
  families, while preserving task retry compatibility.
- Added doctor/status visibility for Blueprint recovery context, latest
  runtime-effect failure metadata, and closure-lineage blockers.

### Fixed

- Fixed Blueprint approval recovery so invalid or missing generated task
  artifacts route through Mechanic Blueprint and can promote a repaired task
  without losing the original failed approval context.
- Fixed Blueprint candidate and manifest replay handling so equivalent
  durable artifacts are treated idempotently, true conflicts block
  conservatively, and same-root remediation manifests use their own
  `manifest_id` instead of colliding with root-spec filenames.
- Fixed Blueprint artifact-contract drift between generated task JSON and
  markdown renderings, including stricter canonical JSON validation before
  runtime effects mutate queue state.
- Fixed closure readiness around canceled/superseded Blueprint lineage
  artifacts so Arbiter can run once all real same-lineage work has drained.
- Fixed stopped-daemon and blocked-runtime diagnostics so operators get a clear
  recovery path instead of a quiet idle workspace.

### Compatibility Notes

- This is a stabilization patch for the experimental `0.20.x` workflow-graph
  and Blueprint Planning line. `0.19.0` remains the last stable baseline while
  the `0.20.x` surface is validated through longer dogfood runs.
- `millrace-ai` and `millrace-web` are released together as `0.20.1`;
  `millrace-web` now depends on `millrace-ai>=0.20.1`.

## [0.20.0] - 2026-05-21

### Added

- Added compiler-validated workflow primitive assets for work-item families,
  document adapters, queue claim policies, terminal actions, lifecycle mutation
  plans, runtime effect handlers, recovery/failure policies, and the workspace
  schema epoch. Compiled plans now carry those primitives as runtime authority.
- Added workspace schema epoch markers and archive/reset helpers that refuse
  daemon-owned workspaces, move old mutable runtime state under
  `millrace-agents/archives/` without parsing stale JSON, initialize clean
  state, and recompile before work resumes.
- Added generic built-in work-item adapters and a queue lifecycle interpreter
  so terminal source lifecycle movement is applied from runtime-owned intent
  objects.
- Added compiler-backed scheduler lanes, durable lane runtime state, lane
  conflict validation, and lane-keyed daemon dispatch. Runtime status now
  reports lane state, active-run launch-plan authority, pending compiled plans,
  and latest runtime failure origin.
- Added deterministic per-request context bundles and rendered prompt-context
  artifacts. Runner normalization and `millrace runs show` now surface the
  context render plan, context bundle path, visible context refs, and failure
  origin metadata.
- Added opt-in `blueprint_codex` and `blueprint_learning_codex` modes with
  Manager/Contractor/Evaluator Blueprint Planning, runtime-owned
  draft/packet/evaluation/critique/promotion effects, generated task
  promotion, and Arbiter closure readiness across the full Blueprint lineage.
  The learning-enabled Blueprint mode keeps the existing Planner-complete
  Librarian trigger.
- Added Blueprint operator visibility in `millrace status` and
  `millrace runs show`, including draft queues, packets, critiques,
  evaluations, promotions, generated task ids, runtime-effect created paths,
  and source lifecycle intent.
- Added run-inspection fields that distinguish artifact parse validity from
  runtime route/effect outcome. `runs ls/show` keep the compatibility
  `status` key while adding `artifact_status`, `runtime_outcome`,
  `runtime_effect_decision`, and runtime-effect failure class.
- Added family-aware doctor/dashboard visibility for graph-owned work such as
  Blueprint draft queues and future custom work-item families.

### Changed

- Removed the public `millrace run once` surface. Use
  `millrace run daemon --max-ticks 1` for bounded one-tick operation.
- Stage work-item ownership, terminal lifecycle action, queue claim policy, and
  runtime effect interpretation now come from compiled workflow authority for
  built-in defaults instead of loose runtime tables.
- Config reload with active work now preserves each active run's original
  compiled launch plan and records the newly compiled plan as pending until
  active work drains.

### Fixed

- Fixed bounded daemon restarts so a selected but not-yet-running next stage is
  treated as resumable work instead of stale active ownership.
- Fixed runtime recovery lookup for custom stage kinds, allowing graph-owned
  stages such as `mechanic_blueprint` to satisfy the canonical planning
  `mechanic` recovery role.
- Fixed Blueprint runtime-effect dispatch so effects are selected from the
  compiled plan's runtime-effect rules instead of a static stage/terminal
  table.
- Fixed compiler validation so duplicate runtime-effect bindings and
  runtime-effect handlers without source-packaged implementations are rejected
  before a custom workflow configuration can run.
- Fixed artifact contract drift in Blueprint promotion/rejection effects so
  canonical JSON outputs are authoritative, malformed canonical outputs block
  instead of falling back, and declared legacy filenames are only used when the
  canonical artifact is absent.
- Fixed Blueprint manifest identity so Arbiter remediation can create a second
  same-root manifest with a distinct `manifest_id`. New manifests are
  manifest-id-keyed, legacy root-keyed manifests remain readable by embedded
  `manifest_id`, Evaluator context resolves manifests from `draft.manifest_id`,
  and duplicate manifest-id conflicts are reported separately from normal
  same-root lineage.
- Fixed Blueprint runtime-effect recovery semantics: Manager pre-mutation
  artifact failures block conservatively with matched policy metadata,
  Evaluator generated-task missing/invalid failures route to Mechanic
  Blueprint repair, idempotent replay can complete after source
  completion/resolution when durable outputs are equivalent, and Planner
  disposition prevents emitted child specs from also being decomposed through
  Manager.
- Fixed stopped-daemon diagnostics so `doctor` warns when daemon mode is not
  running while open closure/backlog work still exists.

### Compatibility Notes

- This release intentionally breaks the removed public `millrace run once`
  command. Use `millrace run daemon --max-ticks 1` for the bounded one-tick
  path.
- Workspaces with stale mutable runtime state from older schema epochs must be
  upgraded or reset through the supported schema epoch/archive flow before
  daemon startup resumes.
- `millrace-ai` and `millrace-web` are released together as `0.20.0`;
  `millrace-web` now depends on `millrace-ai>=0.20.0`.

## [0.19.0] - 2026-05-16

### Added

- Added typed execution capability requests and compiled per-stage capability
  grants. Compiled plans now carry grant decisions, enforcement/advisory state,
  evidence requirements, and grant warnings for operator inspection.
- Added runtime pre-dispatch capability gates. Denied or unsupported required
  grants block before runner invocation, approval-required grants create durable
  approval objects, and `millrace runs show` reports compact grant/support
  summaries for completed or blocked stage results.
- Added `millrace approvals ls/show/approve/deny` for operator-mediated
  capability decisions, with the same direct-vs-mailbox routing behavior as
  other runtime control actions.
- Added documentation for execution capability policy, approval workflows,
  runner support reporting, compile boundaries, and advisory enforcement
  language.

### Changed

- Runner adapters now report contextual capability support so Millrace can
  distinguish runtime-enforced, adapter-enforced, unsupported, and advisory-only
  boundaries instead of implying stronger enforcement than the selected runner
  can actually provide.
- `millrace config show`, `compile show`, run inspection, runtime docs, and the
  shipped ops skill now surface execution capability state as part of the normal
  operator evidence trail.

### Fixed

- Capability-gated runs now normalize missing capability evidence into a
  recoverable runtime failure instead of relying on generic runner-error
  handling.

## [0.18.6] - 2026-05-12

### Added

- Watcher-seeded idea specs now preserve the original idea markdown under
  `millrace-agents/intake/ideas/<root_idea_id>.md` and reference that
  runtime-owned artifact before transient `ideas/inbox/` source files.

### Fixed

- Closure-target creation now prefers the durable idea source artifact before
  legacy references, so Arbiter setup no longer depends on an inbox markdown
  file remaining in place after watcher intake.
- Backlog-drain closure recovery now marks Planning blocked with
  `missing_root_idea_source` and emits `root_idea_source_missing` when every
  source candidate is unavailable, instead of terminating the daemon loop.

## [0.18.5] - 2026-05-12

### Added

- Added audited operator intervention commands for bad intake cleanup:
  `millrace queue cancel`, `queue archive-blocked`, `queue supersede`,
  `queue retarget-dependency`, `incident resolve`, `incident cancel`, and
  `incident archive-invalid`. The commands archive rather than delete runtime
  artifacts, write intervention ledgers/runtime events, refresh queue-depth
  snapshots, and mailbox-route when a daemon owns the workspace.

## [0.18.4] - 2026-05-12

### Added

- Added conservative blocked dependency auto-recovery. When a daemon is idle
  with queued same-lineage execution work stranded behind a blocked predecessor,
  it can requeue the predecessor only if blocked metadata classifies the latest
  failure as `network_unavailable`, `provider_unavailable`,
  `provider_rate_limited`, or `runner_timeout` and cooldown/retry-budget gates
  pass.
- Added `millrace queue retry-blocked <TASK_ID> --reason "..."` as an audited
  manual blocked-task recovery command, with `--root-spec-id` and explicit
  `--force` override support.

### Changed

- Runner normalization now records blocked recovery metadata such as
  `blocked_origin`, `failure_scope`, `auto_requeue_candidate`, and a classifier
  code on failure envelopes.

## [0.18.3] - 2026-05-12

### Added

- Added a Librarian Learning stage that runs after Planner in learning-enabled
  modes, checks Planner output against local and remote skill indexes, and
  installs up to eight relevant uninstalled remote optional skills into the
  workspace.
- Added shipped-skill lint regression coverage so every packaged `SKILL.md`
  asset must satisfy the current skill package contract.

### Changed

- Curator guidance now permits safe format-only migration of a touched
  workspace-installed skill when an evidence-backed behavior patch is already
  being applied and the current linter reports package-shape drift.

### Fixed

- Migrated the shipped `marathon-qa-audit` skill to the current required
  section contract so it passes the packaged skill linter.

## [0.18.2] - 2026-05-10

### Added

- Added an opt-in Integrator execution stage and `execution.with_integrator`
  graph loop for high-assurance Codex runs. The new
  `default_codex_integrated` and `learning_codex_integrated` modes run
  `builder -> integrator -> checker`, with Integrator writing
  `integration_report.md` before normal Checker QA.
- `millrace status` and `millrace status show` now support `--format json`,
  including blocked-idle diagnostics and the latest runtime error report path.

### Fixed

- Invalid Recon handoff artifacts now block the active probe with
  `recon_handoff_invalid` instead of letting malformed probe output fall into
  ordinary planning recovery.
- Graph-loop asset validation now rejects Recon handoff outcomes wired directly
  to stage nodes; typed Recon promotion must remain runtime-owned.
- Stage-request construction now refuses stale stage/work-item pairings before
  runner invocation, preventing Manager from receiving probe work.

## [0.18.1] - 2026-05-08

### Added

- Added probe intake and a Planning-plane Recon stage. Operators can enqueue
  `ProbeDocument` work with `millrace add-probe` or `millrace queue add-probe`;
  the runtime claims probes before ordinary specs, runs Recon, persists a recon
  packet, and routes the probe to a generated task, generated spec, no-op, or
  blocked state.

### Changed

- Updated the shipped Planning graph, default Codex/Pi modes, runtime docs, and
  operator skill guidance so probe intake is available across the four default
  loop configurations.

## [0.18.0] - 2026-05-05

### Added

- Added stable compiled-stage-graph export contracts and
  `millrace compile graph` for inspecting legal runtime topology by plane.
- Added per-run `run_trace.json` artifacts plus `millrace runs trace <run_id>`
  for graph-shaped run history, router decisions, artifacts, and spawned work.
- Added fallback run-trace inspection for older run directories that only have
  stage-result artifacts.
- Added read-only `millrace-web` compiled graph and run-trace API routes and
  Flow view trace outcome overlays.
- Added the repo-local `millrace-autonomous-delegation` external-agent skill as
  a compact, opinionated decision layer for trusted sessions that can choose
  whether substantial work should enter Millrace.

### Changed

- Refocused the repo-local `millrace-ops-agent-manual` skill as the procedural
  runbook for CLI operation, daemon monitoring, workspace validation, and safe
  intervention after Millrace is requested or selected.

## [0.17.4] - 2026-05-03

### Added

- Added first-class learning no-op terminal outcomes for Analyst, Professor,
  and Curator so reviewed no-change learning requests close as done instead of
  appearing blocked.

### Changed

- Changed built-in learning-mode generic Doublechecker success learning to start
  at Analyst instead of direct Curator, keeping vague success evidence in the
  research stage.

### Fixed

- Compile validation now rejects direct Curator learning trigger rules unless
  they include `target_skill_id` or `preferred_output_paths`, preventing
  runtime-generated Curator requests that have no safe skill destination.

## [0.17.3] - 2026-05-03

### Added

- Added runner-neutral compiled stage `thinking_level` bindings across stage
  config, mode `stage_thinking_bindings`, graph-loop node definitions,
  compiled plans, stage requests, runner artifacts, persisted stage results,
  `compile show`, and run inspection. Codex maps the value to
  `model_reasoning_effort`; Pi maps it to `--thinking`.

### Changed

- Kept `stages.<stage>.model_reasoning_effort` as a Codex compatibility alias
  while making `stages.<stage>.thinking_level` the preferred stage config
  surface. Explicit `null` mode thinking bindings now mean compiled default.
- Reduced repeated basic daemon monitor `idle reason=no_work` output from a
  120-second heartbeat to a 6-hour heartbeat while preserving the immediate
  first idle line and reset behavior after runtime activity or a different idle
  reason.

## [0.17.2] - 2026-05-03

### Fixed

- Improved the optional `millrace-web` Flow view so graph polling no longer
  rebuilds animated lane DOM on every one-second workspace summary refresh,
  preventing visible animation resets during idle monitoring.
- Reworked Flow view particle effects to use non-repeating, slow random-walk
  scatter layers instead of visibly tiled linear background motion.

### Changed

- Polished the optional dashboard Detail and Flow views for cleaner dense
  layouts, shorter long identifiers, better Flow lane wrapping, and more
  legible read-only workspace inspection.
- Updated the shipped Millrace ops skill and web package docs with the
  `millrace-web serve` command surface, multi-workspace usage, Detail/Flow
  roles, and read-only/no-lock safety boundary.

## [0.17.1] - 2026-05-03

### Changed

- Restored the normal release workflow path for publishing the full built
  distribution set to PyPI now that the separate `millrace-web` pending trusted
  publisher is configured.
- Updated `millrace-web` to `0.17.1` with a synchronized
  `millrace-ai>=0.17.1` dependency so the optional web dashboard can ship from
  PyPI alongside the base runtime in normal releases.
- Updated the public README proof point to document the autonomous Rust
  Millrace parity implementation campaign instead of the earlier comparative
  benchmark.

## [0.17.0] - 2026-05-02

### Added

- Added the optional `millrace-web` source distribution under
  `packages/millrace-web/`. It provides the read-only `millrace-web serve`
  local dashboard with Detail and Flow views over the same workspace summary,
  queue, run, compiled-plan, Arbiter, usage-governance, and event DTOs.
- Added read-only Web UI service tests, CLI smoke coverage, static shell
  coverage, and package-boundary checks proving the base `millrace-ai` wheel
  does not contain web modules or web static assets.

### Fixed

- Closure targets that are open only because lineage work is blocked no longer
  count as actionable closure targets for new root-spec activation. That lets
  unrelated queued root specs start once the active closure target is blocked
  by its own lineage work instead of falsely reporting multiple open targets as
  corrupt state.
- `millrace status` now prefers the actionable open closure target when older
  lineage-blocked target records remain in the workspace.

### Changed

- The release workflow now builds and validates both the base `millrace-ai`
  distribution and the optional `millrace-web` distribution while keeping their
  package data separate.
- Updated README guidance, the shipped Millrace ops skill, and runtime docs to
  describe the optional read-only dashboard, local-only safety model, and
  separate package boundary.

## [0.16.3] - 2026-04-29

### Added

- Added task lifecycle duplicate diagnostics to `millrace doctor`; workspaces
  now report `duplicate_task_lifecycle_state` when the same task id appears in
  more than one task lifecycle directory.

### Fixed

- Same-id execution continuations that reach `tasks/done/` now safely retire a
  same-root stale predecessor from `tasks/blocked/` into
  `tasks/blocked/superseded/`, preserving an audit record while unblocking
  closure readiness.
- Manager guidance now explicitly avoids writing a task card whose `Task-ID`
  already exists in task lifecycle state unless the runtime has requeued the
  original task.

## [0.16.2] - 2026-04-28

### Fixed

- Runtime-created Consultant `NEEDS_PLANNING` handoff incidents now inherit
  root lineage from their source work item. Under an open closure target, the
  generated planning incident remains same-lineage claimable instead of being
  filtered out while the source task sits blocked.
- Incident markdown now accepts `Status-Hint: incoming|active|blocked|resolved`,
  so Consultant-authored diagnostic incidents with explicit incident state are
  not quarantined solely because they carry a status hint.

### Changed

- Runtime handoff events now include the resolved root/source lineage fields
  used for the generated planning incident.
- Updated runtime documentation, Consultant guidance, and the shipped
  Millrace ops skill to describe closure-safe planning handoffs.

## [0.16.1] - 2026-04-28

### Added

- Added supported remote optional-skill discovery through
  `millrace skills refresh-remote-index`, which caches the public
  `tim-osterhus/millrace-skills` index into
  `millrace-agents/skills/remote_skills_index.md`.
- Extended `millrace skills install <skill_ref>` so listed remote skill ids can
  be installed into a workspace as normal local skills, including nested
  `references/` and `scripts/` files plus `remote_source.json` audit metadata.

### Changed

- Analyst now refreshes and uses the supported remote skills index during
  Learning when downloadable optional skills may improve the request.
- Stage entrypoints may select up to three relevant installed optional skills
  instead of two.
- Updated runtime docs, CLI reference, packaged skills guidance, and the
  shipped Millrace ops skill for the remote optional-skills workflow.

## [0.16.0] - 2026-04-28

### Added

- Added daemon-mode plane-concurrent scheduling. Default modes remain serial,
  while learning-enabled modes can run one Learning stage concurrently with one
  permitted foreground Planning or Execution stage under the compiled
  concurrency policy.
- Added plane-indexed active-run status output and queue active counts for
  learning requests.

### Changed

- Runtime active state is now canonicalized in `active_runs_by_plane`; legacy
  `active_*` snapshot fields remain as a foreground compatibility projection.
- Daemon result application remains single-writer even when stage runner
  workers are running concurrently.
- Config reloads are deferred while active planes exist and are applied after
  active runs drain.

## [0.15.9] - 2026-04-28

`0.15.9` adds closure lineage drift diagnostics and repair, and improves the
daemon monitor's human-readable output.

### Added

- Added closure lineage drift diagnostics and `millrace queue repair-lineage`
  so mismatched root-spec queue artifacts block clearly instead of sending
  Arbiter through repeated planning-only remediation.

### Changed

- Clarified `millrace upgrade` documentation and the shipped ops skill so
  operators do not confuse workspace baseline refreshes with installed
  `millrace-ai` package updates.
- Made the basic daemon monitor more human-readable by compacting redundant
  stage identity, shortening long live run ids, rendering route transitions
  directly, suppressing routine terminal-to-idle status noise, and omitting
  unknown token filler.

## [0.15.8] - 2026-04-27

`0.15.8` hardens upgrade/config surfaces and serializes bulk watcher root-spec
intake behind the v1 one-open-closure-target policy.

### Added

- Added `millrace --version` and `millrace version` for package-version
  visibility in installed and module-entrypoint environments.
- Added `millrace run daemon --monitor-log PATH` to write basic monitor output
  to a file while keeping stdout monitor mode independently selectable.
- Added first-class Codex `model_reasoning_effort` config on
  `runners.codex` and per-stage `stages.<stage>.model_reasoning_effort`, with
  compile, request, runner artifact, and run-inspection visibility.

### Changed

- Bulk watcher intake now respects the v1 one-open-closure-target policy as
  queue backpressure: unrelated root specs remain queued while the active root
  lineage runs or reaches Arbiter, status reports the deferred-root count, and
  direct stale-state recovery preserves the open closure target.
- `stages.<stage>` and `runners.codex.permission_by_stage` now accept learning
  stages such as `professor`.
- Workspace doctor now resolves the selected mode against workspace-local
  deployed assets, matching runtime compile/startup behavior for custom modes.
- `millrace upgrade` can intentionally localize removed managed assets through
  `--localize-removed` or `--localize-removed-from`, and runtime asset
  manifests now ignore cache artifacts such as `__pycache__` and `*.pyc`.
- Runtime shutdown now clears `process_running`, `status` suppresses stale
  `process_running` truth when no active ownership lock exists, and
  `clear-stale-state` reports applied when it only clears that stale process
  bit.

## [0.15.7] - 2026-04-27

`0.15.7` completes the post-usage-governance package-boundary cleanup, keeping
public imports stable while splitting high-risk runtime, compiler, contract,
entrypoint, and runner internals into focused owners.

### Added

- Added ADR-0007, ADR-0008, and ADR-0009 to record the runtime authority
  package, contract facade, and stage metadata decisions behind the cleanup.
- Added source hygiene guardrails for dependency direction, contract-layer
  imports, path-only workspace modeling, generic helper modules, and wildcard
  imports.

### Changed

- Clarified usage-governance documentation now that the auto-pause/resume
  feature has shipped, including the next-tick status/monitor visibility model
  for governance changes after `config reload`.
- Split workspace bootstrap payloads and asset deployment out of
  `workspace.paths`, and split CLI status/run/config/compile views out of
  `cli.formatting`, preserving public imports while removing the real workspace
  and CLI import cycles that blocked later cleanup work.
- Moved the shared `RuntimeTickOutcome` contract behind
  `runtime/outcomes.py`, keeping the public runtime facade stable while
  removing the remaining concrete runtime submodule cycle.
- Added an import-cycle guardrail test so future concrete `millrace_ai.*`
  module cycles are caught by the normal pytest suite.
- Converted `runtime/usage_governance.py` into a package facade with separate
  ownership for models, state persistence, ledger repair, runtime-token window
  evaluation, subscription quota telemetry, monitor events, and engine-facing
  pause application.
- Converted `runtime/graph_authority.py` into a package facade with separate
  ownership for activation, validation, policy lookup, recovery counters, stage
  mapping, and execution/planning/learning routing.
- Split compiler internals into the `compilation/` package while keeping
  `millrace_ai.compiler` as the stable public facade for compile, preview, and
  currentness APIs.
- Converted `contracts.py` into the `contracts/` package facade, preserving
  `millrace_ai.contracts` imports while separating enums, stage metadata,
  work documents, stage results, loop/mode contracts, runtime snapshots,
  mailbox envelopes, compiler diagnostics, and recovery counters.
- Centralized stage plane, legal terminal marker, running marker, and
  result-class policy truth in `contracts/stage_metadata.py`; runner requests,
  normalization helpers, entrypoint linting, graph stage lookup, and built-in
  stage-kind asset validation now derive from that registry.
- Converted `assets/entrypoints.py` into the `assets/entrypoints/` package,
  preserving the public facade while separating manifest models, path
  discovery, markdown parsing, advisory skill-reference checks, lint policy,
  and diagnostic rendering.
- Split Codex CLI runner internals so the public `CodexCliRunnerAdapter`
  delegates command construction, artifact materialization/timeout
  reconciliation, and token-usage extraction to focused adapter modules.

## [0.15.6] - 2026-04-27

`0.15.6` tightens daemon monitor idle output and adds architecture records for
the compiled-plan and workspace-baseline decisions that now govern the runtime.

### Added

- Added ADR-0005 for `compiled_plan.json` as the runtime-authoritative graph
  plan.
- Added ADR-0006 for explicit workspace baselines and managed upgrade
  behavior.

### Changed

- Throttled repeated `runtime_idle reason=no_work` basic monitor lines into a
  120-second heartbeat while preserving the first idle line and resetting after
  runtime activity or a different idle reason.
- Expanded CLI reference coverage for every `millrace skills` subcommand and
  its core options.
- Updated the operator manual with `compile show`, queue inspection, modes,
  skills commands, and basic-monitor idle-heartbeat guidance.

## [0.15.5] - 2026-04-27

`0.15.5` carries forward the runtime docs, entrypoint assets, and asset-policy
coverage that were intended to ship with the usage-governance release, and fixes
the type annotations caught by the main-branch guardrails.

### Added

- Added opt-in usage governance configuration and runtime evaluation for
  between-stage token and subscription-quota pause rules.
- Added `pause_sources` so operator pauses and usage-governance pauses can
  coexist without one clearing the other accidentally.
- Added `usage_governance_state.json` and `usage_governance_ledger.jsonl` state
  artifacts for durable usage-governance status and token accounting.
- Added usage-governance fields to `millrace status`, `millrace config show`,
  and the basic daemon monitor.

### Changed

- Updated shipped entrypoint advisory text so it no longer references unshipped
  optional skills.
- Updated the runtime skills index to list only packaged skills and to point
  operators at the supported downloadable optional-skills directory:
  `https://github.com/tim-osterhus/millrace-skills/blob/main/index.md`.
- Clarified that stage-core skills are runtime-assigned by their compiled
  entrypoints, while optional secondary skills must be present in the packaged
  or installed skills surface before entrypoints can reference them.
- Refreshed public docs for learning modes, explicit workspace init/upgrade,
  daemon monitoring, `millrace skills`, and the optional skills directory.

### Fixed

- Tightened entrypoint asset lint so unknown optional secondary skill
  references fail instead of being accepted as placeholders.
- Fixed usage-governance pause-source type annotations so the package passes
  the repository MyPy guardrail on Python 3.11 and 3.12.

## [0.15.4] - 2026-04-27

`0.15.4` adds default-off runtime-owned usage governance so operators can let
Millrace automatically pause between stages when configured token or subscription
quota limits are reached.

### Added

- Added `[usage_governance]` config with runtime token rules, optional
  subscription quota rules, auto-resume behavior, and next-tick apply
  boundaries.
- Added durable usage governance state and ledger artifacts under
  `millrace-agents/state/`.
- Added runtime-owned pause source tracking so operator pauses and governance
  pauses can coexist without overwriting each other.
- Added status and live daemon monitor output for active usage blockers,
  subscription telemetry degradation, governance pause, and governance resume
  events.

### Changed

- Runtime ticks now evaluate usage governance before launching a stage and after
  persisting a stage result, preserving the between-stage execution boundary.
- `millrace control resume` now clears operator pause intent without bypassing an
  active usage-governance blocker.
- Stage-result token usage can be reconciled back into the governance ledger
  after restart if a ledger write was missing.

## [0.15.3] - 2026-04-26

`0.15.3` adds an opt-in live terminal monitor for daemon operators without
changing the quiet default daemon behavior.

### Added

- Added `millrace run daemon --monitor basic` for concise live lifecycle,
  status, stage, router, elapsed-time, and token-usage output.
- Added a runtime monitor event contract so daemon progress is emitted from the
  runtime path that owns lifecycle, status-marker, stage, and routing state.
- Added learning-plane and compiled concurrency-policy visibility to daemon
  monitor startup and stage output.

### Changed

- Centralized daemon-owned status marker updates so live monitor events and
  persisted marker files stay aligned across execution, planning, and learning.
- Documented the explicit `--monitor [none|basic]` daemon option in the CLI
  reference, including the quiet default behavior.

## [0.15.2] - 2026-04-26

`0.15.2` hardens compiled-plan authority and workspace lifecycle behavior while
moving repository-local skills-pipeline infrastructure out of the core Millrace
package.

### Added

- Added explicit workspace initialization and baseline manifest tracking for
  deployed runtime assets.
- Added workspace baseline upgrade preview/apply support through the CLI.
- Added compile-input fingerprinting and currentness reporting so operators can
  see whether a persisted compiled plan still matches current config and assets.
- Added richer compiled node contracts, including allowed result classes by
  outcome and frozen skill/entrypoint asset references.
- Added generic workspace-local mode and graph discovery so specialized
  workflows can provide their own assets without shipping them in the Millrace
  package.

### Changed

- Runtime consumers now drive stage requests, routing, recovery, and result
  validation from the compiled plan instead of reconstructing authority from
  mutable source assets.
- `millrace status` now surfaces compile currentness and baseline lifecycle
  metadata.
- Repository-local skills-pipeline mode, loop, graph, and entrypoint assets are
  no longer packaged with Millrace; they belong in their owning workspace or
  lab overlay.

### Fixed

- Fixed stage-result normalization and validation so runner outputs must match
  the compiled stage request contract.
- Fixed baseline manifest seeding and stale compile handling so a failed
  recompile preserves the last known-good plan.

## [0.15.1] - 2026-04-25

`0.15.1` completes the Learning plane control surface by making runtime
learning requests fully distinguishable, targetable, auditable, and visible from
operator status output.

### Added

- Added `Target-Stage` and `Trigger-Metadata` fields to learning request
  documents so runtime-generated learning work can target a specific learning
  stage with durable trigger context.
- Added runtime evaluation of compiler-frozen learning trigger rules, including
  automatic enqueueing of targeted learning requests after matching execution
  stage outcomes.
- Added direct activation for targeted learning requests, allowing a generated
  Curator request to start at Curator instead of replaying the full learning
  loop.
- Added learning queue depth and learning status marker output to
  `millrace status show`.

### Fixed

- Fixed learning stage runner requests so they use
  `request_kind = "learning_request"` instead of the generic active-work-item
  request kind.
- Fixed skill revision evidence persistence so each stage request writes a
  request-specific evidence file instead of overwriting a single run-level file.

## [0.15.0] - 2026-04-25

`0.15.0` introduces the Learning plane with Analyst, Professor, and Curator
stages, packaged learning modes, and the `millrace skills` operator commands.

### Added

- Added the learning queue, learning status, and learning request document
  surfaces.
- Added learning graph, loop, stage-kind, entrypoint, and stage-core skill
  assets.
- Added `learning_codex` and `learning_pi` built-in modes.
- Added `millrace skills` commands for install, create, improve, promote,
  export, list, show, and search workflows.
- Added skill revision evidence snapshots for stage requests when a compiled
  learning graph is active.

## [0.14.1] - 2026-04-25

`0.14.1` packages the repository-local skills pipeline mode alongside a smaller,
more sustainable Pi event-log contract and cleans up asset-policy lint so
`millrace doctor` reports a clean workspace by default.

### Added

- Added the specialized `skills_pipeline_codex` built-in mode.
- Added `execution.skills_pipeline` and `planning.skills_pipeline` loop assets.
- Added pipeline-specific planning and execution entrypoints for the skills
  production flow.
- Added integration and asset coverage proving the compiler materializes the
  skills pipeline mode contract.

### Changed

- Added `runners.pi.event_log_policy` so Pi raw event-log retention is
  configurable.
- Changed Pi runner persistence to keep full raw `runner_events` only on failed
  runs by default.
- Filtered redundant Pi `message_update` snapshots out of persisted
  `runner_events` even when full logging is enabled.
- Updated runner and compiler documentation to describe the new skills pipeline
  mode and the slimmer Pi event-log behavior.

### Fixed

- Fixed asset-policy lint warnings in the `mechanic` planning entrypoint and the
  skill-creator reference assets so `millrace doctor` no longer reports those
  false-positive or incomplete-manifest warnings.

## [0.14.0] - 2026-04-24

`0.14.0` expands the compiler from a frozen stage-plan generator into the
runtime-authoritative graph compiler. The runtime still ships the same
`default_codex` and `default_pi` harness modes introduced in `0.13.0`, but the
compiled plan now owns the loop graph, node bindings, activation entries,
recovery policy, completion behavior, and post-stage routing semantics.

### Added

- Added the typed `millrace_ai.architecture` contract package for stage kinds,
  graph loops, and compiled graph materialization.
- Added packaged stage-kind registry assets for every shipped execution and
  planning stage.
- Added packaged graph-loop assets for `execution.standard` and
  `planning.standard`.
- Added `CompiledRunPlan` as the canonical persisted plan model in
  `compiled_plan.json`.
- Added compiled graph entry surfaces for task, spec, incident, and
  closure-target activation.
- Added compiled resume and threshold policy surfaces for fix-cycle exhaustion,
  blocked-stage recovery, consultant escalation, mechanic recovery, and
  closure-target completion behavior.
- Added `preview_graph_loop_plan()` so maintainers can materialize discovered
  graph loops without promoting them into the shipped runtime plan.

### Changed

- Rebuilt the compiler to materialize `execution_graph` and `planning_graph`
  from built-in mode, graph-loop, and stage-kind assets.
- Moved live runtime activation, stage-request binding, closure-target
  activation, recovery decisions, and post-stage routing onto the compiled graph
  plan.
- Kept legacy loop assets in the package as aligned reference assets, while the
  graph-loop and stage-kind surfaces are now the runtime authority for shipped
  defaults.
- Updated `millrace compile show` to print compiled entries, closure activation,
  completion behavior, node entrypoint contracts, required skills, attached
  skills, runner bindings, model bindings, and timeouts from the compiled graph.
- Canonicalized `standard_plain` to `default_codex` before diagnostics,
  persisted plan IDs, and runtime snapshot state are written.
- Updated compiler and runtime documentation around the single compiled-plan
  authority model.

### Removed

- Removed the old `FrozenRunPlan` / `FrozenStagePlan` contract surface from the
  public runtime contracts module.
- Removed the temporary shadow graph-plan artifact path; `compiled_plan.json` is
  now the single canonical compiled runtime plan.

### Compatibility Notes

- Existing operator mode selection remains compatible: `default_codex` and
  `default_pi` are still the canonical modes, and `standard_plain` still aliases
  to `default_codex`.
- Tooling that reads `compiled_plan.json` directly must expect the
  `CompiledRunPlan` graph shape instead of the old frozen stage-plan list.
- Workspaces should be recompiled after upgrading so runtime state points at the
  current compiled graph plan.

## [0.13.0] - 2026-04-20

`0.13.0` is the baseline described by this changelog. At this point Millrace
already bootstrapped workspaces, compiled selected modes and loops into a
persisted plan, and executed stages through the runner dispatcher. Codex CLI
remained the default runtime harness, and this release packaged the Pi harness
as a first-class alternative instead of treating it as an out-of-band runner.

### Added

- Added canonical `default_codex` and `default_pi` built-in modes.
- Added the `pi_rpc` runner adapter as a first-class built-in runner.
- Added a focused Pi JSONL RPC client that invokes `pi --mode rpc --no-session`,
  sends Millrace-owned stage prompts, persists streamed events, reads final
  assistant text, captures session stats when available, and maps transport,
  provider, timeout, and empty-output failures into standard runner results.
- Added `[runners.pi]` configuration for the Pi command, extra args, provider,
  thinking posture, environment, and deterministic context/skill-discovery
  defaults.
- Added Pi runner artifacts alongside the existing runner artifact family,
  including persisted invocation, completion, stdout/stderr, prompt, and event
  log files.
- Added workspace doctor checks that warn when the runner binary required by
  the resolved mode is unavailable.

### Changed

- New workspaces now bootstrap with `runtime.default_mode = "default_codex"`.
- `default_codex` binds every shipped stage to `codex_cli`; `default_pi` binds
  every shipped stage to `pi_rpc`.
- Shared stage prompt construction between Codex and Pi runners so both harnesses
  receive the same Millrace-owned request context and legal terminal-marker
  contract.
- Updated `millrace modes list` and `millrace modes show` to surface canonical
  modes and compatibility aliases.
- Updated runner and mode documentation to describe Codex and Pi as supported
  packaged harness postures.

### Removed

- Removed `standard_plain` as the canonical packaged mode asset.

### Compatibility Notes

- Existing `standard_plain` configs continue to work because `standard_plain`
  resolves as a compatibility alias for `default_codex`.
- Switching from `default_codex` to `default_pi` changes only compiled runner
  bindings. The shipped execution and planning loop topology remains the same.

[Unreleased]: https://github.com/tim-osterhus/millrace/compare/v0.21.1...HEAD
[0.21.1]: https://github.com/tim-osterhus/millrace/compare/v0.21.0...v0.21.1
[0.21.0]: https://github.com/tim-osterhus/millrace/compare/v0.20.3...v0.21.0
[0.20.3]: https://github.com/tim-osterhus/millrace/compare/v0.20.2...v0.20.3
[0.20.2]: https://github.com/tim-osterhus/millrace/compare/v0.20.1...v0.20.2
[0.20.1]: https://github.com/tim-osterhus/millrace/compare/v0.20.0...v0.20.1
[0.20.0]: https://github.com/tim-osterhus/millrace/compare/v0.19.0...v0.20.0
[0.19.0]: https://github.com/tim-osterhus/millrace/compare/v0.18.6...v0.19.0
[0.18.6]: https://github.com/tim-osterhus/millrace/compare/v0.18.5...v0.18.6
[0.18.5]: https://github.com/tim-osterhus/millrace/compare/v0.18.4...v0.18.5
[0.18.4]: https://github.com/tim-osterhus/millrace/compare/v0.18.3...v0.18.4
[0.18.3]: https://github.com/tim-osterhus/millrace/compare/v0.18.2...v0.18.3
[0.18.2]: https://github.com/tim-osterhus/millrace/compare/v0.18.1...v0.18.2
[0.18.1]: https://github.com/tim-osterhus/millrace/compare/v0.18.0...v0.18.1
[0.18.0]: https://github.com/tim-osterhus/millrace/compare/v0.17.4...v0.18.0
[0.17.4]: https://github.com/tim-osterhus/millrace/compare/v0.17.3...v0.17.4
[0.17.3]: https://github.com/tim-osterhus/millrace/compare/v0.17.2...v0.17.3
[0.17.2]: https://github.com/tim-osterhus/millrace/compare/v0.17.1...v0.17.2
[0.17.1]: https://github.com/tim-osterhus/millrace/compare/v0.17.0...v0.17.1
[0.17.0]: https://github.com/tim-osterhus/millrace/compare/v0.16.3...v0.17.0
[0.16.3]: https://github.com/tim-osterhus/millrace/compare/v0.16.2...v0.16.3
[0.16.2]: https://github.com/tim-osterhus/millrace/compare/v0.16.1...v0.16.2
[0.16.1]: https://github.com/tim-osterhus/millrace/compare/v0.16.0...v0.16.1
[0.16.0]: https://github.com/tim-osterhus/millrace/compare/v0.15.9...v0.16.0
[0.15.9]: https://github.com/tim-osterhus/millrace/compare/v0.15.8...v0.15.9
[0.15.8]: https://github.com/tim-osterhus/millrace/compare/v0.15.7...v0.15.8
[0.15.7]: https://github.com/tim-osterhus/millrace/compare/v0.15.6...v0.15.7
[0.15.6]: https://github.com/tim-osterhus/millrace/compare/v0.15.5...v0.15.6
[0.15.5]: https://github.com/tim-osterhus/millrace/compare/v0.15.4...v0.15.5
[0.15.4]: https://github.com/tim-osterhus/millrace/compare/v0.15.3...v0.15.4
[0.15.3]: https://github.com/tim-osterhus/millrace/compare/v0.15.2...v0.15.3
[0.15.2]: https://github.com/tim-osterhus/millrace/compare/v0.15.1...v0.15.2
[0.15.1]: https://github.com/tim-osterhus/millrace/compare/v0.15.0...v0.15.1
[0.15.0]: https://github.com/tim-osterhus/millrace/compare/v0.14.1...v0.15.0
[0.14.1]: https://github.com/tim-osterhus/millrace/compare/v0.14.0...v0.14.1
[0.14.0]: https://github.com/tim-osterhus/millrace/compare/v0.13.0...v0.14.0
[0.13.0]: https://github.com/tim-osterhus/millrace/compare/v0.12.5...v0.13.0
