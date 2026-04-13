# Stage Execution Pipeline And Plane Handoff Contracts

## 1. Purpose And Scope

This document owns the runtime boundary that explains how the execution plane turns one promoted task into a governed stage pipeline, records the resulting transitions, and hands execution-owned failure state across the plane boundary when local recovery is exhausted.

It covers the execution-plane composition shell in `millrace_engine/planes/execution.py`, the stage-runtime and transition-history helpers in `millrace_engine/planes/execution_runtime.py`, the per-stage legality contract in `millrace_engine/stages/base.py`, and the normalized handoff payload models in `millrace_engine/contract_runtime.py`.

It does not own control-plane command routing, engine lifecycle authority, runner adapter internals, or broader research-plane scheduling. Those remain with the control-plane, lifecycle, runner/model, and research deep docs. This boundary stops at the point where execution hands a typed payload to another plane or to an operator-facing recovery surface.

## 2. Source-Of-Truth Surfaces

The authoritative surfaces for this boundary are:

- `millrace_engine/planes/execution.py`: the public execution-plane composition shell. It defines `ExecutionCycleResult`, chooses run ids, records stage transitions, owns success/archive behavior, owns blocker and quarantine escalation, and constructs execution-to-research handoff payloads.
- `millrace_engine/planes/execution_runtime.py`: the helper layer for stage resolution, runtime parameter rebinding, prompt/runtime context construction, policy-blocked stage results, transition-history initialization, and persisted transition records.
- `millrace_engine/stages/base.py`: the shared execution-stage contract. It resolves prompt assets, builds `StageContext`, invokes the runner, enforces legal terminal markers, and emits normalized `StageResult` payloads.
- `millrace_engine/contract_runtime.py`: the runtime contract models that cross this boundary, especially `StageContext`, `CrossPlaneParentRun`, and `ExecutionResearchHandoff`.
- `tests/test_execution_plane.py`: the main proof surface for stage legality, transition-history persistence, quickfix/troubleshoot recovery loops, prompt provenance, policy-hook recording, and execution-to-research handoff continuity.

The authority order inside this boundary is:

1. stage-terminal legality enforced by `stages/base.py` and `status.validate_stage_terminal()`
2. execution-plane routing and transition recording in `execution.py` and `execution_runtime.py`
3. typed payload normalization in `contract_runtime.py`
4. operator-facing explanations in deep docs and portal links

When these surfaces disagree, the runtime behavior wins. The docs should describe the composition seam the code currently implements, not a simpler imagined pipeline.

## 3. Lifecycle And State Transitions

### 3.1 Cycle Entry And Run Identity

The execution plane is not a free-form stage launcher. `ExecutionPlane.run_once()` delegates one full execution-cycle decision to the execution-flow helpers, but the plane still owns the cycle-visible contract:

- the run id
- the final `ExecutionCycleResult`
- the active task before and after the cycle
- the stage-result list
- any diagnostics directory
- any quickfix-attempt count
- any transition-history path
- any `ExecutionResearchHandoff`

That matters because later control surfaces do not reconstruct a run from scattered artifacts. They rely on the execution plane to produce one coherent per-cycle result shape.

Run identity is created before stage execution with `_new_run_id()`. For task-backed work it uses the task id as the slug source; for backlog-empty maintenance it uses a label fallback. The run id then anchors all downstream artifacts under `agents/runs/<run_id>/`, especially `transition_history.jsonl`.

### 3.2 Transition-History Start And Stage Context Construction

`execution_runtime.start_transition_history()` resets the per-run transition-history file before a new run begins. That reset is deliberate: reused run ids rewrite their transition history instead of silently appending stale records from a previous attempt, and `tests/test_execution_plane.py` explicitly covers that contract.

Before each stage invocation, `execution_runtime.run_stage()` resolves the effective node id, stage handler, and any frozen-plan parameter bindings. `stages/base.py` then constructs `StageContext`, which is the normalized runner input for one stage execution. The current contract includes:

- `stage`, `runner`, `model`, and `effort`
- the full rendered prompt
- `working_dir`
- `run_id`
- `permission_profile`
- `timeout_seconds`
- the concrete `command`
- optional workspace prompt path and status fallback path
- `allow_search` and `allow_network`
- injected procedures and context facts when compounding is active

This means the stage-pipeline handoff is not just "call the runner with some text." It is a typed, runtime-normalized payload with explicit search/network/prompt provenance and bounded stage parameters.

### 3.3 Stage Execution And Legal Terminal Markers

`ExecutionStage.run()` applies one consistent stage contract:

1. transition the status store into the stage's running marker
2. resolve the prompt asset and render the final prompt
3. invoke the configured runner with the validated `StageContext`
4. determine the terminal marker through `_resolve_terminal_status()`
5. confirm the terminal transition and return a normalized `StageResult`

The legality check is strict. A stage succeeds only if:

- the runner emits a known marker and that marker is legal for the stage, or
- the current status file already holds a legal terminal marker for the stage, or
- a synthesized success marker is allowed for that stage under the current fallback rule

Otherwise `StageExecutionError` is raised. This is the core guardrail that keeps the execution pipeline from treating arbitrary runner output as a valid stage transition.

### 3.4 Recorded Stage Transitions

After a stage returns, `execution_runtime.record_stage_transition()` writes one runtime transition-history record when a history store is active. The record is richer than a stage/status pair. It includes:

- the node id and kind id
- status before and after
- selected routing edge id and human-readable reason
- selected terminal-state id when relevant
- active task before and after
- queue mutations applied
- bound execution parameters
- emitted artifacts
- routing-mode and compounding attributes

If a stage emitted compounding candidates, the same helper may append a follow-on `execution.compounding.flush` record. So the pipeline boundary is not only about stage ordering. It is also the point where execution makes durable claims about why a transition happened and what secondary artifacts that transition emitted.

### 3.5 Success, Quickfix, And Boundary Exit

The execution plane exposes a small set of visible stage-path families:

- backlog-empty maintenance via `_run_empty_backlog_sequence()`
- normal task execution via `_run_full_task_path()`
- builder-success follow-on routing via `_run_builder_success_sequence()`
- QA outcome handling via `_handle_qa_outcome()`
- quickfix and troubleshoot recovery via `_run_quickfix_loop()` and `_resume_after_recovery()`
- final archive/update completion via `_complete_success_path()`

The important contract is not the private helper names but the boundary behavior they preserve:

- builder, integration, QA, quickfix, troubleshoot, consult, and update all enter the same stage-runtime contract
- every branch that matters for operator truth records transition-history evidence
- quickfix attempts are counted explicitly in `ExecutionCycleResult`
- a resolved success path archives the task and returns execution to `IDLE`
- unresolved failures stay visible as blocked, quarantined, or handed off, not silently absorbed

### 3.6 Execution-To-Research Handoff

Execution does not directly become research. It constructs a typed boundary payload.

`ExecutionPlane._build_research_handoff()` creates `ExecutionResearchHandoff` with:

- a handoff id scoped to the execution run and recovery batch
- source plane `execution` and target plane `research`
- the parent task id and title
- the stage label that triggered the handoff
- the failure reason
- incident and diagnostics paths when present
- recovery-batch and failure-signature evidence
- a `CrossPlaneParentRun` carrying the execution run id, snapshot id, frozen-plan identity, and transition-history path

That `parent_run.transition_history_path` link is one of the most important handoff guarantees. Research and operator tooling can trace the handoff back to the exact execution-run history instead of inferring provenance from queue state alone. `tests/test_execution_plane.py` explicitly proves that this parent-run continuity is preserved.

## 4. Failure Modes And Recovery

### 4.1 Illegal Or Missing Stage Markers

The first failure class is an execution stage that exits without a legal terminal marker. `stages/base.py` raises `StageExecutionError` when:

- the emitted marker is unknown
- the marker is not legal for that stage
- the runner exits without a legal terminal marker and no synthesized success path applies

Recovery here is not a doc-level interpretation step. The runtime treats the stage as invalid and routes through the surrounding execution-path logic, which may block, quickfix, troubleshoot, or consult depending on the current edge.

### 4.2 Policy-Blocked Stage Entry

Not every failed stage boundary comes from the runner. `execution_runtime.run_stage()` may produce a policy-blocked `StageResult` before the stage runs at all when pre-stage policy evaluation returns a block status. In that case the boundary still emits a normalized failed-stage event and returns a stage result with policy execution context in metadata.

That means the stage pipeline supports two distinct kinds of negative edge:

- a runner-backed stage failure
- a policy-backed boundary refusal before the stage starts

The docs must preserve that difference because both appear in transition history and affect later routing.

### 4.3 Quickfix And Troubleshoot Escalation

Execution-plane recovery is intentionally bounded. A QA or doublecheck failure can enter the quickfix loop, but only up to the configured recovery limit. The active quickfix artifact is also managed as part of this boundary: successful recovery clears it back to the scaffold, while unresolved recovery preserves the active artifact for inspection.

If quickfix is exhausted, the runtime can escalate through troubleshoot and, if needed, consult or quarantine. `tests/test_execution_plane.py` covers both the successful quickfix path and the exhausted quickfix path that routes through troubleshoot before recovering.

### 4.4 Boundary Exit To Blocker Or Research

When local recovery is exhausted or a consult path emits `NEEDS_RESEARCH`, the execution plane leaves the execution boundary through explicit artifacts:

- blocker bundles and blocker entries for operator-visible blocked state
- quarantined task movement when the task cannot continue locally
- typed `ExecutionResearchHandoff` payloads when the problem is now owned by the research side

This is a real boundary, not a status synonym. The execution plane keeps ownership until it emits the handoff or blocker evidence. After that point, the next action belongs to the target recovery surface, not to the original stage runner.

## 5. Operator And Control Surfaces

Most operators do not call `ExecutionPlane` directly, but they still consume the truth it produces.

The main surfaces are:

- `millrace start --once` and daemon-driven execution loops, which call into the same execution-plane contract
- `run-provenance <run-id> --json` and adjacent report readers that depend on `transition_history.jsonl`
- blocker bundles, diagnostics directories, and run directories under `agents/runs/`
- status and supervisor reports that surface final execution status, active task, and any cross-plane attention state

The practical operator rules for this boundary are:

- treat `agents/runs/<run_id>/transition_history.jsonl` as the authoritative execution narrative for one run
- treat cross-plane handoff payloads and blocker bundles as boundary outputs, not as ad hoc notes
- do not infer stage legality from runner exit codes alone; the legal marker and recorded transition edge are the stronger truth surfaces

This boundary also explains why one visible `IDLE` state can hide meaningful prior complexity. By the time an operator sees `IDLE`, the execution plane may already have performed builder, integration, QA, quickfix, update, and archive transitions and written all of them into transition history.

## 6. Proof Surface

The strongest proof for this boundary comes from `tests/test_execution_plane.py`, especially the tests that cover:

- transition-history persistence with bound parameters and frozen-plan identity
- reused run ids rewriting transition-history state instead of appending stale records
- policy-hook recording around cycle and stage boundaries
- prompt asset resolution, including packaged fallback and missing-prompt failure
- quickfix-loop recovery, exhausted quickfix escalation, and quickfix artifact cleanup behavior
- consult-driven `NEEDS_RESEARCH` handoff with preserved `parent_run.transition_history_path`

Packaging proof for this doc remains intentionally narrow:

- `tests/test_package_parity.py` must require the public and packaged Run 05 doc paths, plus the runtime IA and portal mirrors
- `tests/test_baseline_assets.py` must require the bundled doc path and the key boundary markers that describe stage legality, transition history, and typed handoff continuity
- `millrace_engine/assets/manifest.json` must describe the shipped packaged doc with the correct SHA and size

Drift should fail proof when:

- the public and packaged docs diverge
- the IA or portal points at the wrong Run 05 filename
- the doc claims unsupported stage transitions
- the handoff section stops matching the typed `ExecutionResearchHandoff` and `CrossPlaneParentRun` contract

## 7. Change Guidance

Update this doc when changes affect:

- execution-cycle result shape
- stage-context normalization
- legal terminal-marker enforcement
- transition-history schema or selected-edge recording
- quickfix/troubleshoot boundary behavior
- execution-to-research handoff payload composition

Do not expand this doc to absorb:

- daemon lifecycle ownership
- CLI mailbox command semantics
- runner adapter implementation details
- broader research-plane scheduling or queue selection

If a future change primarily alters runner/model selection or permission profiles, route it to the runner boundary doc. If it primarily alters operator recovery flows after the pipeline boundary is crossed, route it to the failure-playbook doc. Keep this document focused on the execution-stage pipeline, its recorded boundary transitions, and the typed handoff out of that plane.
