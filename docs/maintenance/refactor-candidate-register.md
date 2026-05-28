# Refactor Candidate Register

This register is the stable source of truth for maintainability refactor
tracks. Future packets should reference these candidate ids instead of keeping
separate target lists.

Line counts and import breadth are treated as signals to inspect, not as
reasons to split on their own. A candidate is actionable only when it has a
structural reason to change, a known blast radius, and enough characterization
coverage to keep behavior stable.

## Batch Labels

- Batch A: dedicated Blueprint/effect decoupling specification.
- Batch B: low-risk presentation and diagnostic splits.
- Batch C: compiler, contract, and runtime-boundary splits.
- Batch D: recovery and request-context follow-up work after Blueprint coupling
  is inventoried.
- Batch E: daemon supervisor work after lower-level runtime seams are stable.

Follow-up wave labels:

- FU-0: public API inventory and baseline guardrails.
- FU-1: operation-id-first runtime effect dispatch authority.
- FU-2: runtime effect operation runner decomposition.
- FU-3: request context and runtime-stage materialization.
- FU-4: work-family queue and lifecycle adapters.
- FU-5: generic repair closure validation.
- FU-6: compiler validation package decomposition.
- FU-7: workflow primitive contract package decomposition.
- FU-8: recovery decomposition and supervisor boundary readiness.
- FU-9: final docs, negative checks, verification, and handoff.

## Follow-Up Wave Ownership

The original maintainability-refactor wave completed the audit, characterization,
low-risk package splits, and Blueprint effect migration through Batch 4. The
remaining debt is now owned by
`mac-handoff/lab/for-codex/maintainability-follow-up-refactor/`:

| Candidate | Follow-up owner | Notes |
| --- | --- | --- |
| MR-MAINT-001 | Complete | FU-2 moved Blueprint runtime-effect behavior into `runtime/effects/operation_runners/`; `runtime/blueprint_effects.py` and `runtime/effects/operations.py` are compatibility facades. |
| MR-MAINT-002 | Complete | FU-1 landed operation-id-first effect dispatch and failure-policy matching with legacy handler ids kept as compatibility metadata only. |
| MR-MAINT-003 | Complete | FU-6 split compiler validation into focused validator-family modules behind the stable package facade. |
| MR-MAINT-004 | Complete | FU-7 split workflow primitive contracts into package family modules while preserving the public facade exports. |
| MR-MAINT-005 | Complete | FU-4 and FU-8 moved blocked-recovery mutation domains under `runtime/recovery/` with family-adapter-backed retry/lineage behavior and a thin compatibility facade. |
| MR-MAINT-006 | Complete | FU-8 extracted error-context/report/repair-route helpers into `runtime/recovery/`; `runtime/error_recovery.py` remains the public orchestration entry module. |
| MR-MAINT-007 | Complete | FU-3 moved provider/render-plan behavior to `runtime/context/` and kept `runtime/request_context.py` as a compatibility facade. |
| MR-MAINT-008 | Complete | Completed in the previous wave's low-risk package splits. |
| MR-MAINT-009 | Complete | Completed in the previous wave's Doctor package split. |
| MR-MAINT-010 | Complete | Completed in the previous wave's runner-normalization package split. |
| MR-MAINT-011 | Deferred | FU-8 landed direct lifecycle characterization tests, but a structural split is still gated by tightly-coupled dispatch/completion/event lifecycle boundaries. |

## Summary

| ID | Source | Main reason to change | Dependency risk | Suggested batch | Dedicated spec | FU-9 status |
| --- | --- | --- | --- | --- | --- | --- |
| MR-MAINT-001 | `src/millrace_ai/runtime/effects/operation_runners/` | Blueprint durable mutation moved to focused operation-runner modules; public legacy facades remain for import compatibility. | Medium | Batch A | Required | Complete: FU-2 landed runner decomposition and preserved facade imports. |
| MR-MAINT-002 | `src/millrace_ai/runtime/effect_execution.py` | Effect dispatch needed operation-id-first authority instead of legacy handler-id-first branching. | High | Batch A | Required | Complete: FU-1 switched dispatch/policy matching to operation ids, with legacy ids retained as compatibility metadata. |
| MR-MAINT-003 | `src/millrace_ai/compilation/validation/` | Compiler rule families required decomposition behind one stable package surface. | Complete | Batch C | Complete | Complete: FU-6 replaced the monolith with validator-family modules. |
| MR-MAINT-004 | `src/millrace_ai/architecture/workflow_primitives/` | Workflow primitive contracts needed contract-family modularization under a stable facade. | Medium-high | Batch C | Required | Complete: FU-7 split family modules and preserved exported symbols. |
| MR-MAINT-005 | `src/millrace_ai/runtime/recovery/` (compat facade `runtime/blocked_recovery.py`) | Blocked metadata/retry/queue mutation behavior needed domain seams and adapter-backed family logic. | High | Batch D | Optional, recommended | Complete: FU-4/FU-8 moved logic into `runtime/recovery/` and family adapters; module now acts as a facade. |
| MR-MAINT-006 | `src/millrace_ai/runtime/recovery/` plus `runtime/error_recovery.py` | Exception-context, repair-route, and reporting concerns needed focused recovery subdomains. | High | Batch D | Optional, recommended | Complete: FU-8 extracted focused helpers into `runtime/recovery/`; `error_recovery.py` remains orchestration entrypoint. |
| MR-MAINT-007 | `src/millrace_ai/runtime/context/` (compat facade `runtime/request_context.py`) | Generic rendering needed separation from Blueprint/provider-specific behavior. | High | Batch D | Required for Blueprint half | Complete: FU-3 moved provider/render-plan logic into `runtime/context/`; file is now a compatibility facade. |
| MR-MAINT-008 | `src/millrace_ai/cli/status_view.py` | Status collection/rendering needed decomposition with output compatibility preserved. | Medium | Batch B | Not required | Complete: landed in previous wave as `cli/status/` with facade compatibility. |
| MR-MAINT-009 | `src/millrace_ai/doctor/` | Doctor checks needed package-level organization by check family. | Medium | Batch B | Not required | Complete: landed in previous wave with check registry split. |
| MR-MAINT-010 | `src/millrace_ai/runners/normalization/` | Runner normalization concerns needed package separation. | Medium | Batch B | Not required | Complete: landed in previous wave as `runners/normalization/` package split. |
| MR-MAINT-011 | `src/millrace_ai/runtime/supervisor.py` | Daemon orchestration still combines dispatch, completion, recovery, and event projection. | Very high | Batch E | Required | Deferred: FU-8 characterization improved confidence, but structural split remains lifecycle-risky. |

Batch B status: MR-MAINT-008, MR-MAINT-009, and MR-MAINT-010 landed in
Batch 3 as behavior-preserving package splits with compatibility facades.

Historical repo-shape snapshot from FU-2 Packet 03:

- Largest source modules at that point: `compilation/validation.py` (1494 lines),
  `architecture/workflow_primitives.py` (1408 lines),
  `runtime/effects/operation_runners/blueprint_evaluator.py` (1253 lines),
  `runtime/blocked_recovery.py` (1159 lines), and
  `runtime/request_context.py` (987 lines).
- Effect-operation debt moved from a monolithic operations module to focused
  runner modules. The remaining hotspot is
  `operation_runners/blueprint_evaluator.py`, which still combines approval,
  rejection, promotion, critique persistence, checksum/idempotency, and repair
  support helpers.

## Candidates

### MR-MAINT-001: Blueprint Runtime Effects

Source: `src/millrace_ai/runtime/effects/operation_runners/`, especially the
Blueprint runner modules, with compatibility facades in
`src/millrace_ai/runtime/blueprint_effects.py` and
`src/millrace_ai/runtime/effects/operations.py`.

Reason to change: Manager, Contractor, Evaluator, and Mechanic Blueprint
durable mutations now run through focused modules in
`runtime/effects/operation_runners/` and compiled operation assets. The old
Blueprint and operations modules remain compatibility facades for old imports,
legacy handler-id names, and diagnostics that should now patch focused runner
modules for implementation behavior.

Blast radius: Blueprint workspace state, runtime effect failure classes,
runtime failure policy routing, repair artifacts, generated task promotion,
queue lifecycle mutation, run traces, status and doctor diagnostics, and old
compiled-plan compatibility.

Owned tests: `tests/blueprint/test_effects.py`,
`tests/integration/test_blueprint_planning_loop.py`,
`tests/runtime/test_runtime_effects.py`, and Blueprint-adjacent CLI/status
coverage in `tests/cli/test_cli.py`.

Missing characterization: Remaining direct gaps are divergent duplicate
Mechanic repair outputs, unsupported repair actions, and full-suite coverage
after the operation split. Operation-id and handler-id compatibility tests,
facade-to-runner parity tests, and one non-Blueprint declarative effect fixture
are now present.

Likely extraction seams: future extraction should split the remaining large
Evaluator runner into approval and rejection subflows, shared candidate-state
loading, critique/promotion persistence, checksum/idempotency checks,
work-item enqueue/update helpers, mutation journaling, and repair compatibility
support.

Dependency risk: Medium. The facades no longer mutate durable workspace state,
but public imports, legacy handler-id compatibility, and old diagnostic patch
points still depend on the exported names.

Suggested batch: Batch A. Execute under the declarative runtime effects and
Blueprint decoupling specification, one Blueprint effect at a time.

Dedicated implementation spec required: Yes. This is not generic cleanup.

### MR-MAINT-002: Runtime Effect Execution

Source: `src/millrace_ai/runtime/effect_execution.py`

Reason to change: Core runtime effect dispatch now resolves operation runners
through a registry seam, but it still carries legacy handler-id compatibility
metadata and combines selection, runner execution, failure-policy routing,
source lifecycle application, stage-result annotation, spawned-work projection,
and event output.

Blast radius: Every compiled runtime effect, router decision after stage
completion, runtime failure policy routing, default repair fallback, blocked
source metadata, stage-result metadata, monitor events, and spawned-work traces.

Owned tests: `tests/runtime/test_runtime_effects.py`,
`tests/runtime/test_result_application.py`, `tests/runtime/test_run_traces.py`,
`tests/runtime/test_runtime_failure_policy.py`, and integration coverage through
`tests/integration/test_blueprint_planning_loop.py`.

Missing characterization: Operation-id and legacy-handler-id dual-key behavior,
registry failure cases, non-Blueprint declarative effect execution, exact event
payload stability after operation metadata is added, and old plan/repair
artifact compatibility.

Likely extraction seams: effect rule selection, operation runner registry,
failure-policy input construction, default runtime repair routing,
stage-result annotation, runtime effect events, spawned-work projection, and
source lifecycle clearing.

Dependency risk: Very high. It is on the supervisor/tick completion path and
has bidirectional conceptual coupling with compiler validation, runtime
failure policy, run traces, and Blueprint effects.

Suggested batch: Batch A. Start by adding an operation registry boundary without
behavior changes, then migrate handlers incrementally.

Dedicated implementation spec required: Yes. It is part of the Blueprint/effect
decoupling track.

### MR-MAINT-003: Compiler Validation

Source: `src/millrace_ai/compilation/validation/`

Status: Complete. The previous monolithic module was replaced by a package
facade with focused validator-family modules for mode maps, workflow primitive
closure, graph topology, queue claim policy, artifact contracts,
request-context profiles, lifecycle plans, runtime effects, recovery policies,
failure policies, lane conflicts, and generic repair-route closure.

Blast radius: All mode compilation, shipped graph validation, custom workflow
assets, compile diagnostics, stale-plan safety, loop authoring docs, and CLI
`compile validate` behavior.

Owned tests: `tests/compilation/test_workflow_validation.py`,
`tests/integration/test_compiler.py`, `tests/assets/test_workflow_assets.py`,
and `tests/assets/test_blueprint_assets.py`.

Missing characterization: A validator-family inventory, direct tests for each
future validator module, diagnostic substring stability expectations per group,
and direct negative coverage for explicit repair-closure mapping drift and
out-of-scope mapping pairs.

Likely extraction seams: graph topology, stage/stage-kind validation, artifact
contracts, request-context profiles, queue policies, lifecycle and terminal
actions, runtime effects, runtime failure policies, lane conflicts, model
assignments, and diagnostics helpers.

Dependency risk: High. The compiler is a startup and release boundary; moving
validators can subtly change diagnostic order or message text.

Suggested batch: Batch C, after the register and characterization inventory are
complete.

Dedicated implementation spec required: Yes. The split should name validator
families and diagnostic contracts before code moves.

### MR-MAINT-004: Workflow Primitive Contracts

Source: `src/millrace_ai/architecture/workflow_primitives/`

Reason to change: The module is a cohesive schema authority, but it now covers
many contract families: artifact contracts, work-item families, document
adapters, queue policies, lanes, lane conflicts, terminal actions, lifecycle
mutation, runtime effects, request-context profiles, completion behavior,
recovery/failure policies, operator controls, and schema epochs.

Blast radius: Asset loading, compiler materialization, graph authoring,
workflow primitive exports, Pydantic validation behavior, `architecture`
package exports, and authoring docs.

Owned tests: `tests/architecture/test_workflow_primitives.py`,
`tests/assets/test_workflow_assets.py`, `tests/compilation/test_workflow_validation.py`,
and compiler integration tests that round-trip shipped assets.

Missing characterization: Contract-family export inventory, per-family
round-trip tests after package extraction, import-compatibility checks for
`millrace_ai.architecture`, and validation-message stability for public
authoring errors.

Likely extraction seams: identifiers, artifact contracts, work-item families,
document adapters, lifecycle, concurrency, completion behavior, recovery
policies, operator controls, schema epochs, and shared validation helpers.

Dependency risk: Medium-high. The module is mostly declarative schemas, but its
public exports are widely imported by compiler, assets, runtime, and tests.

Suggested batch: Batch C, after compiler validation seams are named.

Dedicated implementation spec required: Yes. The target package shape and
compatibility re-exports should be specified before moving classes.

### MR-MAINT-005: Blocked Recovery

Source: `src/millrace_ai/runtime/recovery/` with compatibility facade
`src/millrace_ai/runtime/blocked_recovery.py`

Reason to change: The module combines blocked metadata contracts, failure-scope
classification, family resolution, queue document parsing, retry budget
enforcement, snapshot queue-depth refresh, stranded dependency detection,
diagnostic writing, event emission, and daemon auto-recovery.

Blast radius: `queue retry-blocked`, runtime blocked-source metadata, automatic
stranded dependency recovery, queue mutation, status/doctor interpretation of
blocked work, and runtime events.

Owned tests: `tests/runtime/test_blocked_recovery.py`,
`tests/runtime/test_supervisor.py`, `tests/runtime/test_completion_behavior.py`,
and CLI retry-blocked coverage in `tests/cli/test_cli.py`.

Missing characterization: Focused tests for generic family resolution with
compiled-plan fallback, malformed generic JSON/markdown queue documents,
diagnostic payload stability, event payload stability, and auto-recovery
cooldown/budget edge cases independent of the supervisor.

Likely extraction seams: blocked metadata model and IO, retry family
resolution, queue document parsing, retry eligibility/budget policy, stranded
dependency scan, auto-recovery diagnostics, and retry event emission.

Dependency risk: High. It mutates queue state and is called from CLI,
supervisor, runtime effect blocking, and work-item transitions.

Suggested batch: Batch D, after status and doctor diagnostics have clearer
view-model boundaries.

Dedicated implementation spec required: Optional, recommended for any source
movement beyond pure helper extraction.

### MR-MAINT-006: Runtime Error Recovery

Source: `src/millrace_ai/runtime/recovery/` plus orchestration entrypoint
`src/millrace_ai/runtime/error_recovery.py`

Reason to change: The module owns runtime error context IO, error-code
classification, pre-dispatch and post-stage recovery scheduling, repair route
lookup, snapshot mutation, blocked learning request handling, report rendering,
and runtime event emission.

Blast radius: Runtime exception recovery, default repair routing, runtime error
reports, stage request context fields, runtime snapshots, learning pre-dispatch
failure handling, status output, doctor warnings, and runtime error-code docs.

Owned tests: `tests/runtime/test_runtime_failure_policy.py`,
`tests/runtime/test_completion_behavior.py`, `tests/runtime/test_runtime.py`,
`tests/runtime/test_request_context.py`, and CLI/status coverage in
`tests/cli/test_cli.py`.

Missing characterization: Separate pre-dispatch and post-stage behavior
contracts, report text stability, learning-plane blocked mutation coverage,
repair-attempt threshold edge cases, and tests that isolate route lookup from
snapshot mutation.

Likely extraction seams: runtime error context storage, error-code
classification, repair route resolution, pre-dispatch scheduling,
post-stage scheduling, report rendering, and snapshot/event mutation helpers.

Dependency risk: High. This path protects runtime failures; regressions can
leave work items active, blocked, or routed incorrectly.

Suggested batch: Batch D, after compiler/runtime failure-policy boundaries are
stable.

Dedicated implementation spec required: Optional, recommended because the
module controls failure recovery.

### MR-MAINT-007: Runtime Request Context

Source: `src/millrace_ai/runtime/context/` with compatibility facade
`src/millrace_ai/runtime/request_context.py`

Reason to change: Generic deterministic request-context rendering lives beside
Blueprint-specific context plans for Manager, Contractor, Evaluator, and
Mechanic, including runtime-effect repair evidence lookup.

Blast radius: Runner request prompts, run artifacts, context bundle manifests,
Blueprint planning behavior, Mechanic repair inputs, artifact contract
preferences, model provenance visibility, and runner adapter prompts.

Owned tests: `tests/runtime/test_request_context.py`,
`tests/runtime/test_blueprint_request_context.py`,
`tests/runners/test_runner.py`, `tests/runners/test_runners_codex_adapter.py`,
and Blueprint integration coverage.

Missing characterization: Operation-id-aware Blueprint repair context and a
coupling inventory that decides which Blueprint context logic becomes assets
versus a registered provider.

Likely extraction seams: render-plan models, generic context renderer,
artifact-contract lookup, generic active-work providers, Blueprint context
providers, runtime-effect failure evidence selectors, and path/ref formatting.

Dependency risk: High. The request context is prompt-visible behavior and
feeds both stage agents and repair workflows.

Suggested batch: Batch D. Generic rendering seams may move earlier, but
Blueprint-specific context should wait for the Blueprint decoupling inventory.

Dedicated implementation spec required: Yes for the Blueprint-specific half;
generic renderer extraction can use a smaller track if tests are direct.

### MR-MAINT-008: CLI Status View

Source: `src/millrace_ai/cli/status_view.py`

Reason to change: Status data loading, derived status calculations, text line
rendering, JSON payload construction, Blueprint status scanning, closure
status, currentness, lanes, active runs, usage governance, and intervention
visibility live in one CLI module.

Blast radius: `millrace status`, `millrace status --json`, status watch output,
operator workflows, Blueprint diagnostics visibility, usage-governance status,
and CLI tests that assert specific output fragments.

Owned tests: Status-focused sections of `tests/cli/test_cli.py`, especially
active mode/currentness, queue depths, active runs, usage governance, runtime
effect metadata, Blueprint repair diagnostics, closure targets, Blueprint
operator state, custom family visibility, and status watch.

Missing characterization: A stable status view-model contract independent of
terminal text, JSON schema expectations for status payloads, focused tests for
Blueprint status scanning, and fixtures that distinguish absent data from
malformed data.

Likely extraction seams: workspace data collection, status view model,
terminal line rendering, JSON serialization, Blueprint status collector,
closure status collector, and runtime-effect diagnostic projection.

Dependency risk: Medium. It is operator-facing but mostly read-only; output
compatibility is the main risk.

Suggested batch: Batch B. Completed in Batch 3 with a `cli/status/`
collection/rendering package behind the `cli/status_view.py` compatibility
facade.

Dedicated implementation spec required: No. A small implementation checklist
with output stability expectations should be enough.

### MR-MAINT-009: Workspace Doctor

Source: `src/millrace_ai/doctor/`

Reason to change: Doctor checks tend to grow by appending another validation
function. The module now spans workspace layout, baseline manifests, runtime
state reconciliation, locks, queue parseability, Blueprint diagnostics, closure
lineage, task lifecycle uniqueness, stopped-daemon warnings, mode/loop assets,
entrypoint lint, compile resolution, and runner availability.

Blast radius: `millrace doctor`, operator diagnostics, CLI command output,
workspace integrity checks, shipped asset validation, baseline upgrade
confidence, and ops-agent guidance.

Owned tests: Doctor sections of `tests/cli/test_cli.py`, supporting workspace
and state tests, asset lint tests, and compiler integration tests for resolved
mode validity.

Missing characterization: Unit tests for individual check groups independent
of CLI rendering, issue-code stability expectations, registry ordering rules,
and tests for check selection if a registry is introduced.

Likely extraction seams: diagnostic result models, check registry,
workspace-layout checks, runtime-state checks, queue-artifact checks,
Blueprint/closure checks, asset checks, compile/runner checks, and CLI
formatting.

Dependency risk: Medium. Doctor is read-only, but operators rely on stable
codes and deterministic ordering.

Suggested batch: Batch B. Completed in Batch 3 with a `doctor/` check registry
package behind the stable `millrace_ai.doctor` facade.

Dedicated implementation spec required: No. Keep commits grouped by check
family and preserve issue codes.

### MR-MAINT-010: Runner Normalization

Source: `src/millrace_ai/runners/normalization/`

Reason to change: Runner normalization combines raw result identity checks,
transport failure classification, stdout terminal-token parsing, structured
terminal result parsing, artifact-path safety, terminal-result/result-class
resolution, failure envelope construction, provenance metadata, token usage,
and request-context metadata preservation.

Blast radius: Every stage result envelope, runtime failure classification,
blocked metadata, retry eligibility, run inspection, model assignment
provenance, runner adapter contracts, and capability evidence failures.

Owned tests: `tests/runners/test_runner.py`,
`tests/runners/test_capability_support.py`,
`tests/runners/test_runners_codex_adapter.py`,
`tests/runtime/test_stage_metadata.py`, and integration tests that depend on
normalized terminal results.

Missing characterization: Direct parser tests for structured result file edge
cases, classifier table expectations, provenance-preservation tests for every
failure path, adapter-specific normalization hook behavior if introduced, and
determinism fixtures after extraction.

Likely extraction seams: structured terminal parser, stdout token parser,
transport failure classifier, terminal/result-class resolver, artifact safety
checks, envelope builders, and request/provenance metadata projection.

Dependency risk: Medium. The module is pure-ish but sits on the boundary
between external runner behavior and runtime state.

Suggested batch: Batch B. Completed in Batch 3 with a
`runners/normalization/` package behind the stable normalization import.

Dedicated implementation spec required: No. Require a short behavior contract
inventory before movement.

### MR-MAINT-011: Runtime Supervisor

Source: `src/millrace_ai/runtime/supervisor.py`

Reason to change: The supervisor owns plane-concurrent dispatch, active worker
tasks, lane conflict checks, stage request construction, capability gates,
worker execution, completion draining, result application, runtime effects,
learning promotions, recovery scheduling, idle handling, daemon stop handling,
snapshot updates, run traces, and monitor/runtime events.

Blast radius: Daemon behavior, active-run lifecycle, lane scheduling,
foreground/Learning concurrency, capability gates, result application,
runtime effect application, auto-recovery, stop/reload behavior, run traces,
and monitor output.

Owned tests: `tests/runtime/test_supervisor.py`,
`tests/runtime/test_completion_behavior.py`,
`tests/runtime/test_runtime_failure_policy.py`,
`tests/runtime/test_result_application.py`, `tests/runtime/test_runtime.py`,
and CLI daemon tests in `tests/cli/test_cli.py`.

Missing characterization: Multi-worker stop/restart contracts when completion
draining, mailbox mutations, and daemon shutdown all happen in adjacent cycles;
and stricter event-payload freeze coverage across monitor/runtime outputs.

Likely extraction seams: worker task lifecycle, dispatch loop, claim activation
and pre-dispatch recovery, completion application, event emission, idle/stop
handling, and possibly a narrow supervisor state object.

Dependency risk: Very high. This is the daemon orchestration path and should
not be split until lower-level runtime effect, recovery, and request-context
boundaries are clearer.

Suggested batch: Batch E. Defer until Batch B-D refactors have reduced the
number of responsibilities imported into the supervisor.

Dedicated implementation spec required: Yes. The implementation spec should
start with behavior contracts and targeted async/lifecycle tests.

FU-8 Batch 8 Packet 02 status: direct supervisor boundary tests now cover
worker cancellation active-run cleanup, cancellation durability expectations,
max-tick stop lock/snapshot cleanliness, reload/pending-plan boundary ordering,
compiled-plan authority for in-flight result application, exact-once completion
draining, and Learning/foreground concurrency checks. No `supervisor_parts`
split landed in this packet because dispatch, completion mutation, and
event/monitor projection still share one tightly-coupled lifecycle boundary.

## Deferred Or Gated Work

- MR-MAINT-011 remains the only open deferred candidate after FU-9. FU-8 added
  direct supervisor boundary characterization for cancellation, reload ordering,
  and completion-drain durability, but a structural split is still deferred
  because dispatch, completion mutation, and event projection remain tightly
  coupled lifecycle boundaries.
- MR-MAINT-001 through MR-MAINT-010 are complete for this follow-up wave. New
  maintainability work should be filed as fresh candidates against current
  hotspots (for example `runtime/effects/operation_runners/blueprint_evaluator.py`,
  `runtime/completion_behavior.py`, and `runtime/context/blueprint.py`) rather
  than re-opening the finished packet ids.
