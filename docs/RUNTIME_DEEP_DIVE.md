# Millrace Runtime Deep Dive

This document explains how the Python-based Millrace v1 runtime works in concrete terms.

It is intentionally implementation-oriented. It describes:

- the runtime's major concepts
- the on-disk contract under `millrace/agents/`
- the package/module layout under `millrace/millrace_engine/`
- the end-to-end control flow for the CLI, the TUI, and foreground or daemon execution
- the explicit one-workspace external supervisor contract used by OpenClaw-style harnesses
- how status, queues, events, watchers, diagnostics, and tests fit together
- what is intentionally deferred or scoped in v1

For the operator-facing workflow, see `../OPERATOR_GUIDE.md`. For the packaged CLI-agent prompt surface, see `../ADVISOR.md`.

## 1. What This Runtime Is

The v1 runtime is a file-backed local control plane for Millrace.

Its job is to:

- load runtime configuration
- watch or poll local runtime inputs
- promote tasks from backlog into active execution
- run deterministic execution stages
- validate status markers and legal transitions
- archive completed work
- quarantine research-blocked work
- expose a control surface through a CLI, a TUI, and mailbox-backed daemon commands
- persist runtime artifacts, event logs, diagnostics, and history

It is not:

- a web service
- a distributed job system
- a complete port of every legacy shell behavior

External supervisors stay outside the core runtime. The explicit compatibility seam is the CLI-first one-workspace supervisor contract: observe with `millrace --config millrace.toml supervisor report --json`, then act through issuer-attributed `millrace --config millrace.toml supervisor ... --issuer <name> --json` commands when intervention is required.

The research side in v1 now includes a real supervisor path (`auto`, `goalspec`, `incident`, `audit`) plus a compatibility `stub` mode that records deferred breadcrumbs.

## 2. Package Repo And Workspace

The public `millrace/` repo is package-first, not a pre-seeded runtime workspace.

Primary top-level contents:

- `millrace_engine/`: the runtime package
- `docs/`: longer-form reference material, including this document
- `tests/`: the unit and integration suite
- `README.md`: product overview and quick start
- `ADVISOR.md`: packaged CLI-based advisor artifact
- `OPERATOR_GUIDE.md`: human operator guide

Important note:

- `millrace/.venv/` is a local development/runtime environment convenience when created; it is not part of the Millrace runtime contract itself.

An initialized workspace created by `millrace init /absolute/path/to/workspace` contains the runtime-owned surfaces:

- `millrace.toml`: the active workspace config
- `agents/`: the runtime workspace surface
- `docs/RUNTIME_DEEP_DIVE.md`: this document copied into the workspace docs tree

## 3. Runtime Package Map

### 3.1 Top-Level Package

`millrace_engine/` is the main package.

Key modules:

- `__main__.py`: module entrypoint for `python -m millrace_engine`
- `cli.py`: Typer CLI surface
- `control.py`: operator-facing control API and runtime-state helpers
- `health.py`: deterministic workspace bootstrap/system-check reporting for operator preflight
- `workspace_init.py`: packaged-bundle workspace scaffolding helpers used by `millrace init`
- `engine.py`: async-owned runtime supervisor
- `config.py`: typed config model plus native and legacy config loading
- `paths.py`: canonical resolved path model rooted at the configured workspace
- `contracts.py`: shared enums and immutable contract models, plus the stable re-export surface for Phase 01B loop-architecture schemas
- `loop_architecture.py`: additive Phase 01B persisted-object schemas for registered stage kinds, loop DAGs, modes, profiles, and structured stage results
- `materialization.py`: Phase 01B lookup/materialization layer that resolves registry objects, loop inheritance, mode composition, stage/model precedence, and final asset bindings without entering runtime execution
- `baseline_assets.py`: packaged baseline manifest and resource lookup helpers
- `assets/resolver.py`: workspace-first asset resolution and additive family enumeration
- `markdown.py`: markdown parsing and atomic file-writing helpers
- `status.py`: status marker parsing, transition rules, and validation
- `queue.py`: lock-backed task-store mutation layer
- `runner.py`: runner abstraction and artifact capture
- `telemetry.py`: Codex usage extraction helpers
- `diagnostics.py`: diagnostics-bundle and run-artifact helpers
- `events.py`: event bus plus durable subscribers
- `registry.py`: packaged/workspace registry discovery plus workspace object persistence/promotion
- `compiler.py`: Phase 01B frozen-plan compiler and compile-artifact emission
- `standard_runtime.py`: standard/large runtime selection and preview helpers
- `provenance.py`: run transition history and bound-parameter provenance helpers

### 3.2 Subpackages

- `assets/`
  - packaged read-only baseline docs, config, and agent workspace seed files
  - `resolver.py`: workspace-first asset resolution and additive family enumeration
- `adapters/`
  - `control_mailbox.py`: file-backed daemon command mailbox
  - `file_watcher.py`: watchdog/poll local intake adapter
- `planes/`
  - `base.py`: shared plane/runtime abstractions
  - `execution.py`: execution-plane routing state machine
  - `research.py`: compiled-dispatch research supervisor with `stub` compatibility mode
- `stages/`
  - `base.py`: stage execution framework
  - execution stage handlers including `builder`, `integrate`, `qa`, `hotfix`, `doublecheck`, `troubleshoot`, `consult`, `update`, `large_plan`, `large_execute`, `reassess`, and `refactor`
- `research/`
  - research queue discovery, dispatcher, supervisor lifecycle/progression/request helpers, and executed research-stage implementations (goalspec, incident, audit, taskmaster, taskaudit, governance, provenance)
- `publishing/`
  - staging-manifest parsing plus sync/preflight/commit helpers for the publish surface
- `tui/`
  - Textual operator shell, health gate, runtime gateway/store/workers, modal screens, and panel widgets
- `legacy/`
  - `workflow_loader.py`, `model_loader.py`: compatibility loaders for reference-framework markdown configs
- `policies/`
  - typed policy fact/evidence contracts plus execution preflight, clean-room network, and outage-recovery helpers

### 3.3 Packaged Baseline Asset Bundle

The runtime now ships a packaged baseline bundle under `millrace_engine/assets/`.

That bundle is:

- read-only package data
- a baseline default surface for later workspace scaffolding
- described deterministically by `assets/manifest.json`

The manifest records:

- `bundle_version`
- directory entries for the baseline workspace shape
- file entries with category/family metadata plus SHA-256 and size data

`baseline_assets.py` is the narrow manifest/package-data helper layer.

`assets/resolver.py` is the runtime resolver layer that sits on top of that bundle metadata.

Phase 01B registry defaults are intentionally split from the workspace seed surface:

- packaged canonical objects live under `millrace_engine/assets/registry/`
- mutable workspace overlays live under `agents/registry/`
- `millrace init` creates the empty workspace registry directories from manifest metadata, but does not copy the packaged registry objects into the workspace

Current behavior in this run:

- `millrace init` can materialize a baseline workspace from the packaged bundle
- `millrace upgrade` is currently a preview-only surface over that same manifest metadata; it reports manifest-tracked creates/updates/unchanged files plus preserved runtime-owned and operator-owned paths without mutating the workspace
- the engine still reads the configured realized workspace files under `millrace/` or another initialized workspace
- workspace prompt files take precedence over packaged baseline assets when present
- packaged assets are the fallback when the workspace copy is absent
- open families like roles and skills are additive across workspace plus package data
- `health` validates config loadability, required workspace contracts, and required asset resolution for cutover/operator preflight
- `status --detail` and `config show` expose the active `bundle_version` plus resolved prompt provenance

The bundle remains a baseline default surface, not a closed universe. Health and policy checks should continue to build on this resolver layer rather than reintroducing direct path assumptions.

## 4. Core Concepts

The runtime is organized around a small set of persistent concepts.

Phase 01B now carries the standard execution path end-to-end. The runtime compiles the packaged/workspace `mode.standard` selection into a frozen per-run snapshot before executing the standard task loop, and the control surfaces render that same loop, mode, and plan selection as an operator-visible preview. Explicit fixed-v1 fallback remains only for empty-backlog maintenance and legacy status-marker resume seams.

### 4.1 Control Planes

There are two logical planes:

- execution plane
- research plane

Execution is real in v1.

Research is now a real supervisor in v1:

- it accepts selected handoff and intake events
- persists a typed runtime snapshot in `agents/research_state.json`
- compiles and owns research-plane dispatch/queue selection
- executes the supported GoalSpec stages (`goal_intake`, `objective_profile_sync`, `spec_synthesis`, optional `spec_interview`, `spec_review`, `taskmaster`, plus `taskaudit` when family-complete)
- executes the supported incident stages (`incident_intake`, `incident_resolve`, `incident_archive`) plus deterministic remediation task generation
- executes the packaged audit intake/validate/gatekeeper stages
- writes durable audit outcomes to `agents/audit_history.md`, `agents/audit_summary.json`, and the audit runtime artifact tree
- can enqueue deterministic audit remediation work when the completion gate fails
- still keeps broader governance and multi-family concurrency intentionally bounded

### 4.2 File-Backed Truth

The system is intentionally file-native. The core truth surfaces are files, not a database.

Examples:

- `agents/status.md`
- `agents/research_status.md`
- `agents/audit_history.md`
- `agents/audit_summary.json`
- `agents/tasks.md`
- `agents/tasksbacklog.md`
- `agents/tasksarchive.md`
- `agents/tasksbackburner.md`
- `agents/tasksblocker.md`
- `agents/.runtime/state.json`
- `agents/.runtime/commands/incoming/*.json`
- `agents/engine_events.log`

### 4.3 Authoritative Status Markers

Status files use exactly one authoritative marker line.

Examples:

```md
### IDLE
```

```md
### QUICKFIX_NEEDED
```

The runtime validates:

- file shape
- marker ownership by plane
- legal transitions
- legal stage-terminal markers

### 4.4 Task Cards

Task stores are markdown documents containing zero or more task cards.

Each card has a heading like:

```md
## 2026-03-19 - Ship the happy path
```

The parser extracts:

- task date
- title
- task id
- structured fields such as `Spec-ID`
- the full markdown body

The task id is derived from date plus slugified title.

### 4.5 Run Groups

A logical execution cycle gets a `run_id`, and stage outputs land under:

- `agents/runs/<run_id>/`

This gives one grouped artifact location for all stages associated with a task cycle or maintenance cycle.

## 5. On-Disk Contract Under `agents/`

The runtime assumes a specific workspace layout under `millrace/agents/`.

That realized workspace is still the live runtime surface. The packaged baseline bundle now provides both the default seed surface for `millrace init` and the packaged fallback for supported asset lookups when a workspace copy is absent.

### 5.1 Status and Queue Files

- `status.md`: execution plane status
- `research_status.md`: research plane status
- `tasks.md`: at most one active task card
- `tasksbacklog.md`: queued execution tasks
- `tasksarchive.md`: completed tasks
- `tasksbackburner.md`: frozen or deferred tasks, especially research freeze blocks
- `tasksblocker.md`: structured blocker entries
- `taskspending.md`: reserved pending-task surface

### 5.2 History Surfaces

History is hybrid by design:

- `historylog.md`: short compatibility index
- `historylog/`: durable detailed history entries
- `research_state.json`: typed research-plane runtime snapshot and deferred-request queue
- `runs/<run_id>/resolved_snapshot.json` plus `resolved_snapshot.md`: immutable compile-time resolved snapshot emitted by the Phase 01B compiler
- `runs/<run_id>/transition_history.jsonl`: append-only runtime transition history with stage-transition records plus any persisted policy-hook evaluation records, including selected route decisions and bound execution parameters
- copied diagnostics snapshots preserve that append-only history shape but redact sensitive policy-evidence details in the copied `transition_history.jsonl`
- daemon-side `NET_WAIT` recovery appends outage probe/evaluation records to the original blocked run's `transition_history.jsonl`; if the daemon later resumes the task, that resumed execution uses a new `run_id`

Detail filenames use the canonical UTC form:

```text
2026-03-16T21-05-33Z__stage-qa__task-123.md
```

### 5.3 Runtime Internals

- `.runtime/state.json`: persisted runtime snapshot
- `.runtime/research_recovery_latch.json`: frozen-batch recovery metadata
- `.runtime/commands/incoming/`: mailbox commands waiting to be processed
- `.runtime/commands/processed/`: archived successful mailbox commands
- `.runtime/commands/failed/`: archived failed mailbox commands

### 5.4 Artifact Directories

- `runs/`: stage runner output grouped by `run_id`
- `diagnostics/`: copied failure bundles and manifests
- `.deferred/`: research breadcrumbs (active primarily for `research.mode = "stub"` compatibility)
- `.locks/queue.lock`: filesystem lock protecting queue mutations
- `ideas/raw/`: raw ideas intake
- `ideas/incidents/incoming/`: incident references, especially for research handoff

### 5.5 Registry Overlay

Phase 01B persisted loop-architecture objects use a split registry surface:

- `agents/registry/`: mutable workspace overlay for operator-created, autosaved, promoted, and legacy objects
- `millrace_engine/assets/registry/`: packaged canonical defaults shipped with the runtime

Resolution is whole-object by canonical `(kind, id, version)`: if the workspace registry contains that key, it shadows the packaged object completely. The workspace scaffold intentionally starts empty so packaged defaults remain a separate durability layer.

The intended Phase 01B flow is now explicit:

- `discover_registry_state(...)` selects the effective packaged-plus-workspace object set
- `ArchitectureMaterializer` resolves loop inheritance, mode composition, and allowed override precedence into one materialized loop or mode snapshot
- `AssetResolver.resolve_ref(...)` performs the final asset-body binding pass for any resulting prompt or artifact refs

That separation is deliberate. Registry lookup chooses objects, object materialization chooses effective values, and asset resolution chooses file bodies. The live v1 runtime still executes on its existing routing/config surface.

### 5.6 Prompt Files

The default stage config points many execution stages at prompt files under `agents/`, such as:

- `_start.md`
- `_integrate.md`
- `_check.md`
- `_hotfix.md`
- `_doublecheck.md`
- `_troubleshoot.md`
- `_consult.md`
- `_update.md`

Those prompt paths are part of stage configuration, not hardcoded in the execution plane.

Packaged copies of these prompt families now also ship under `millrace_engine.assets/agents/` as baseline defaults.

Run 06 changes that behavior slightly but importantly:

- stage execution now resolves prompt assets through the workspace-first resolver
- when the configured workspace prompt path is missing, packaged prompt assets are the deterministic fallback if the relative path exists in the bundled manifest
- stage metadata and operator inspection surfaces report whether the prompt resolved from `workspace:...` or `package:...`

## 6. Configuration System

`config.py` defines the runtime config model and loading behavior.

### 6.1 Native Config

The primary configuration source is native TOML:

- `millrace/millrace.toml`

Config sections:

- `[engine]`
- `[paths]`
- `[execution]`
- `[sizing]`
- `[research]`
- `[watchers]`
- `[routing]`
- `[policies]`
- `[stages]`

### 6.2 Legacy Config Compatibility

If native TOML is not present, the runtime can fall back to legacy markdown-based reference-framework config extraction using:

- `legacy/workflow_loader.py`
- `legacy/model_loader.py`

This lets the runtime ingest selected values from the reference framework while preserving a Python-native config model internally.

When that legacy fallback is active, the control/CLI config surface carries two explicit visibility hooks:

- an audited legacy policy compatibility report for mapped, partially mapped, deprecated, and unsupported policy knobs
- a deterministic `unmapped_keys` list for legacy settings that still have no native translation

### 6.3 Config Boundaries

The runtime explicitly distinguishes:

- live-immediate fields
- stage-boundary fields
- cycle-boundary fields
- startup-only fields

Startup-only fields currently include:

- `paths.workspace`
- `paths.agents_dir`

These cannot be changed at runtime through daemon config mutation.

Examples of live-immediate fields:

- `engine.poll_interval_seconds`
- `engine.inter_task_delay_seconds`

Examples of stage-boundary fields:

- `execution.quickfix_max_attempts`
- `execution.run_update_on_empty`
- `execution.integration_mode`
- `routing`
- `policies.*`
- `stages`

Examples of cycle-boundary fields:

- `engine.idle_mode`
- `sizing`
- `research.mode`
- `research.idle_mode`
- `watchers`

### 6.4 Config Hash

The runtime computes a stable SHA-256 hash of the active config and stores it in:

- runtime status output
- `state.json`
- event payloads

This helps correlate runtime behavior to a concrete config snapshot.

## 7. Path Resolution

`paths.py` centralizes runtime path derivation.

`RuntimePaths.from_workspace(...)` resolves:

- the workspace root
- the agents directory
- every status/queue file
- runtime dirs
- diagnostics and run dirs
- history surfaces
- deferred dir
- queue lock path

The rest of the runtime operates on this resolved path object rather than rebuilding paths ad hoc.

This matters because it keeps:

- CLI behavior
- daemon behavior
- tests
- config reloads

all aligned to the same filesystem contract.

## 8. Contract Types

`contracts.py` is the shared vocabulary layer.

Major enums:

- `StageType`
- `ExecutionStatus`
- `ResearchStatus`
- `RunnerKind`
- `ReasoningEffort`
- `ResearchMode`

Important models:

- `TaskCard`
- `StageContext`
- `RunnerResult`
- `StageResult`
- `CodexUsageSummary`
- `ResearchRecoveryLatch`
- `BlockerEntry`

The runtime uses immutable Pydantic contracts widely so stage/run/event/state data stays validated and predictable.

The contract policy is explicit:

- execution-kernel stage names are the public v1 stage surface
- detailed research stage names and non-kernel execution vocabulary are forward-compatible only unless promoted later
- `TaskCard` keeps the v1-facing fields (`task_id`, `title`, `spec_id`, `body`, `depends_on`, `blocks`, `provides`, `complexity`, `metadata`, `source_file`) while also carrying richer parser helpers like `heading` and `raw_markdown`
- `StageResult` is the public summary contract, and `runner_result` is the richer attached artifact when deeper runner detail is needed

## 9. Markdown Task Store Model

`markdown.py` provides the parser/writer utilities used throughout queue and control operations.

Key behaviors:

- preserve non-card preamble text
- parse cards by heading pattern
- preserve full card markdown body
- re-render task stores deterministically
- append or insert markdown blocks with controlled spacing
- rewrite files atomically using temp file + `os.replace`

The atomic write rule is one of the main reliability tools in the system. It reduces the risk of partial rewrites on key control-plane files.

## 10. Status System

`status.py` owns status-file correctness.

### 10.1 Shape Rules

A status file must contain exactly one authoritative marker line.

If a file is malformed, the runtime raises explicit status errors rather than guessing.

### 10.2 Plane Ownership

Execution markers are only valid in the execution plane.

Research markers are only valid in the research plane.

Cross-plane marker misuse is rejected.

### 10.3 Transition Validation

The module defines explicit legal transition maps for:

- execution statuses
- research statuses

This prevents a stage from writing nonsensical progress states.

### 10.4 Terminal Marker Validation

Each stage also has an allowed set of terminal statuses.

Examples:

- `builder` may end as `BUILDER_COMPLETE` or `BLOCKED`
- `qa` may end as `QA_COMPLETE`, `QUICKFIX_NEEDED`, or `BLOCKED`
- `consult` may end as `CONSULT_COMPLETE`, `NEEDS_RESEARCH`, or `BLOCKED`

This is how the runtime rejects cases like:

- a stage exiting 0 without a legal marker
- a stage emitting a valid status that belongs to the wrong stage outcome

## 11. Queue System

`queue.py` is the file-backed task mutation layer.

### 11.1 Locking

All mutating queue operations run under an exclusive filesystem lock:

- `.locks/queue.lock`

The implementation uses `fcntl.flock(...)` with retry and timeout behavior.

This serializes file-backed queue changes and protects invariants like:

- only one active task
- deterministic promotion
- non-overlapping archive/quarantine writes

### 11.2 Core Operations

Main operations:

- `promote_next()`
- `archive()`
- `demote()`
- `quarantine()`
- `thaw()`
- `active_task()`
- `peek_next()`
- `backlog_empty()`
- `backlog_depth()`

### 11.3 Promotion

Promotion:

1. verifies there is no active task
2. reads the next backlog card
3. removes it from `tasksbacklog.md`
4. writes it into `tasks.md`

### 11.4 Archival

Archival:

1. verifies the active task matches the expected task
2. appends it to `tasksarchive.md`
3. clears `tasks.md`

### 11.5 Quarantine and Research Freeze

Quarantine is the most specialized queue operation.

When execution needs research:

1. the active task is resolved
2. backlog cards may be split into retained cards and frozen cards
3. the active card plus frozen backlog cards are wrapped into a freeze block
4. that block is appended to `tasksbackburner.md`
5. a structured blocker entry is inserted into `tasksblocker.md`
6. a `ResearchRecoveryLatch` is written to `.runtime/research_recovery_latch.json`
7. `tasks.md` is cleared
8. `tasksbacklog.md` is rewritten with only retained cards

The latch records:

- batch id
- freeze timestamp
- diagnostics dir
- incident path
- stage
- reason
- frozen backlog counts

### 11.6 Thaw

`thaw()` rehydrates previously frozen cards only after the recovery decision exists and visible remediation work matching that decision is still present in the live task stores.

It:

1. locates the freeze block in `tasksbackburner.md`
2. parses frozen cards back into task cards
3. appends them back into `tasksbacklog.md`
4. removes the freeze block
5. deletes the latch file

## 12. Execution Plane

`planes/execution.py` is the core execution state machine.

It is where task routing decisions live.

### 12.1 High-Level Job

For one execution cycle, it decides:

- which frozen mode/loop plan applies for this run (`standard` vs `large` route selection and overrides)
- whether to run update-only maintenance
- whether to promote a task
- which stages to run
- whether to archive
- whether to quickfix
- whether to troubleshoot
- whether to consult
- whether to quarantine for research

### 12.2 Core Standard Happy Path

Normal execution path:

1. if idle and no active task, promote the next backlog task
2. run `builder`
3. optionally run `integration` if integration mode says so
4. run `qa`
5. if QA completes, run `update`
6. archive the task
7. return to `IDLE`

When the frozen selection resolves to the large route, the ordered chain is:

1. `large_plan`
2. `large_execute`
3. `reassess`
4. `refactor`
5. `qa` + quickfix/escalation handling
6. `update` + archive on success

### 12.3 Empty Backlog Path

If the backlog is empty and:

- `run_update_on_empty = true`

the plane runs `update` with no active task as an update-only maintenance cycle.

If `run_update_on_empty = false`, it just remains idle.

### 12.4 Integration Decision

Integration is now a policy decision derived from frozen-plan routing plus task metadata.

Current behavior:

- the execution loop still uses the configured integration policy mode as its baseline
- task cards can require integration with `**Gates:** INTEGRATION`
- task cards can also override the baseline with `**Integration:** force|skip|inherit`
- the execution plane freezes the effective decision at the next cycle boundary and records it as persisted policy evidence
- `run-provenance` then reports both the selected route and the reason the policy chose it

That keeps integration routing honest in two ways:

- live config edits still only affect future cycle-boundary decisions, not already-frozen runs
- historical provenance does not have to infer why integration ran or was skipped from the loop shape after the fact

### 12.5 QA and Quickfix Loop

If QA returns `QUICKFIX_NEEDED`:

1. status is set to `QUICKFIX_NEEDED`
2. the plane enters `_run_quickfix_loop(...)`
3. it runs `hotfix`
4. it runs `doublecheck`
5. if doublecheck returns `QA_COMPLETE`, the task resumes normal archive/update completion
6. if doublecheck still returns `QUICKFIX_NEEDED`, the loop continues

The loop is bounded by:

- `execution.quickfix_max_attempts`

### 12.6 Recovery Escalation

If a stage fails outside the quickfix path, or quickfix exhausts, the plane tries local recovery:

1. create a diagnostics bundle
2. run `troubleshoot`
3. if troubleshoot completes, resume execution
4. otherwise run `consult`
5. if consult completes, resume execution
6. otherwise quarantine for research

Local recovery rounds are bounded by:

- `MAX_LOCAL_RECOVERY_ROUNDS = 2`

### 12.7 Diagnostics During Recovery

Before escalation, the execution plane captures a blocker bundle that snapshots:

- relevant queue/status files
- run dir, if present
- stdout/stderr, if present
- active config hashes
- a failure summary
- `policy_evidence.json` when the run already has persisted policy evidence, with sensitive details redacted and classified for operator use

These bundles land under `agents/diagnostics/`.

### 12.8 Incident Path Extraction

If consult output references an incident path under:

- `agents/ideas/incidents/...`

the execution plane extracts and persists it into the recovery latch and blocker state.

If no explicit incident path is found, it synthesizes a fallback incident reference.

### 12.9 Restart / Resume Edges

The plane contains specific restart-handling logic:

- if status is `NEEDS_RESEARCH` and there is no active task, it settles back to `IDLE` and surfaces the latch diagnostics dir
- if status is `NEEDS_RESEARCH` and an active task is still present at loop start, it quarantines that task immediately
- some completed statuses such as `BUILDER_COMPLETE`, `INTEGRATION_COMPLETE`, `QA_COMPLETE`, and `UPDATE_COMPLETE` have explicit resume branches

This is how the runtime avoids getting wedged after partial progress or process interruption.

## 13. Stages

Execution stages live in `stages/`.

They are intentionally thin.

Each stage is responsible for:

- building its `StageContext`
- invoking the appropriate runner
- validating marker/status behavior
- returning a normalized `StageResult`

That `StageResult` always exposes the public summary fields (`stdout`, `stderr`, `duration_seconds`, `runner_used`, `model_used`, `artifacts`, `metadata`) even when the stage implementation is working from a richer attached `RunnerResult`.

The execution plane owns the routing logic.

The stages own the per-stage invocation contract.

## 14. Runner System

`runner.py` handles runner invocation and artifact normalization.

### 14.1 Supported Runner Kinds

- `subprocess`
- `codex`
- `claude`

### 14.2 Common Runner Behavior

All runners:

1. allocate or reuse a run directory
2. compute artifact paths
3. build the command
4. set a normalized environment
5. execute the subprocess
6. capture stdout/stderr
7. detect the last terminal marker
8. write runner notes
9. return a `RunnerResult`

### 14.3 Run Artifacts

For each stage, the runtime writes:

- `<stage>.stdout.log`
- `<stage>.stderr.log`
- `<stage>.last.md`
- `runner_notes.md`

All of these live under:

- `agents/runs/<run_id>/`

### 14.4 Marker Detection

The runner searches for the last line matching:

```text
### SOME_MARKER
```

It first checks stdout.

If needed, it checks the rendered last-response file.

### 14.5 Environment Passed to Runners

Runners receive normalized environment variables including:

- `MILLRACE_PROMPT`
- `MILLRACE_STAGE`
- `MILLRACE_MODEL`
- `MILLRACE_RUN_DIR`
- `MILLRACE_STDOUT_PATH`
- `MILLRACE_STDERR_PATH`
- `MILLRACE_LAST_RESPONSE_PATH`
- `MILLRACE_ALLOW_SEARCH`
- `MILLRACE_ALLOW_NETWORK`
- `MILLRACE_REASONING_EFFORT`

This keeps runner implementations consistent even when stage execution comes from different sources.

### 14.6 Codex Telemetry

The Codex runner can parse JSONL usage output and normalize token-usage summaries into a `CodexUsageSummary`.

That summary is written into runner notes alongside the stage-result summary.

Execution usage-budget policy reuses the telemetry layer through `sample_weekly_usage(...)`. With `provider = "codex"`, the sampler tries the local Codex app-server path inside an isolated HOME populated from the configured auth directory or `~/.codex`; if that local install/auth layout is missing or incompatible, the runtime records a failed sample with warnings, falls back to command or env sources when available, and otherwise continues without auto-pause rather than treating the sampler itself as a hard execution failure.

## 15. Diagnostics

`diagnostics.py` owns diagnostics bundle creation.

Each bundle contains:

- copied snapshots of selected files and directories
- `manifest.json` with per-file SHA-256 hashes
- `failure_summary.md`
- `policy_evidence.json` when the source run has persisted policy-hook evidence

The failure summary records:

- stage
- marker
- run dir
- stdout log
- stderr log
- freeform note
- active config hashes

The point of the bundle is to preserve enough local evidence for post-failure analysis without needing the original transient runtime state to still exist. When transition history or the standalone policy-evidence snapshot includes sensitive command/env/header-style detail fields, the diagnostics copy redacts those values instead of rewriting the source run history.

## 16. Event System

`events.py` is a simple in-process fanout system with durable subscribers.

### 16.1 Event Bus

`EventBus.emit(...)`:

1. normalizes the event into an `EventRecord`
2. stamps UTC time
3. JSON-normalizes the payload
4. synchronously calls all subscribers

### 16.2 Event Types

Current event vocabulary includes:

- engine lifecycle
- control mailbox receipt/application
- task promotion/archive/quarantine
- stage completion/failure
- config changes
- `handoff.needs_research`
- `handoff.backlog_empty_audit`
- `handoff.audit_requested`
- `handoff.backlog_repopulated`
- `handoff.idea_submitted`
- `research.received`
- `research.deferred`
- `research.scan.completed`
- `research.mode.selected`
- `research.dispatch.compiled`
- `research.checkpoint.resumed`
- `research.idle`
- `research.blocked`
- `research.retry.scheduled`
- `research.lock.acquired`
- `research.lock.released`

### 16.3 Durable Subscribers

Current subscribers at engine construction time:

- `JsonlEventSubscriber`
- `HistorySubscriber`
- `ResearchPlane` (with `ResearchStubPlane` alias compatibility)

### 16.4 JSONL Log

`JsonlEventSubscriber` appends every event to:

- `agents/engine_events.log`

This is the structured event ledger.

### 16.5 Hybrid History Log

`HistorySubscriber` writes two surfaces:

- a concise line into `historylog.md`
- a full detail file into `historylog/`

That keeps compatibility with the old human-facing single file while avoiding unbounded growth in one markdown document.

## 17. Research Plane

`planes/research.py` is a compiled-dispatch supervisor with a `stub` compatibility mode.

### 17.1 Accepted Inputs

The research plane accepts:

- `handoff.needs_research`
- `handoff.backlog_empty_audit`
- `handoff.audit_requested`
- `handoff.idea_submitted`

Unsupported events are ignored.

### 17.2 Runtime Modes

Configured `research.mode` can be:

- `stub`: defer-only compatibility path
- `auto`: dispatch by ready queue family selection
- `goalspec`: forced goalspec dispatch
- `incident`: forced incident/blocker dispatch
- `audit`: forced audit dispatch

### 17.3 Dispatch and Checkpoint Flow

In non-stub modes, the supervisor:

1. scans research queues
2. resolves one dispatch selection
3. compiles the selected research mode into a frozen run plan
4. acquires a restart-safe lock and persists checkpoint state
5. executes supported stages for the selected family
6. advances or completes checkpoint state
7. emits lifecycle events for scan/mode/dispatch/lock/retry/idle/blocked transitions

AUTO mode intentionally allows only one ready queue group at a time (incident, goalspec, or audit) to keep dispatch deterministic.

### 17.4 Stub Compatibility Path

In `stub` mode, accepted events are still persisted as deferred requests, and breadcrumb JSON files under `.deferred/` remain available for compatibility/migration.

The active non-stub path does not depend on breadcrumbs.

## 18. File Watcher and Poll Intake

`adapters/file_watcher.py` converts local filesystem changes into normalized runtime input events.

### 18.1 Runtime Input Kinds

- `RuntimeInputKind.IDEA_SUBMITTED`
- `RuntimeInputKind.BACKLOG_CHANGED`
- `RuntimeInputKind.CONFIG_CHANGED`
- `RuntimeInputKind.STOP_AUTONOMY`
- `RuntimeInputKind.AUTONOMY_COMPLETE`
- `RuntimeInputKind.CONTROL_COMMAND_AVAILABLE`

### 18.2 Watch Mode

If watchdog is available and config requests `watch`, the adapter:

- starts an observer
- watches:
  - `agents/ideas/raw/`
  - `agents/`
  - `.runtime/commands/incoming/`
  - `millrace.toml` (or configured config path)
- normalizes raw filesystem callbacks into `RuntimeInputEvent`
- debounces duplicates
- schedules the event into the engine loop with `loop.call_soon_threadsafe(...)`

This thread-safe handoff is important because watchdog callbacks run off the asyncio event loop thread.

### 18.3 Poll Mode

If watch mode is unavailable or disabled, the adapter polls:

- `tasksbacklog.md`
- config file changes
- `STOP_AUTONOMY`
- `AUTONOMY_COMPLETE`
- idea files
- incoming command files

It tracks signatures as `(mtime_ns, size)` and emits only newly changed logical events.

### 18.4 Duplicate Suppression

The router suppresses repeated logical events for the same `(kind, path)` inside a short debounce window.

This protects the runtime from noisy editor or filesystem event bursts.

## 19. Control Mailbox

`adapters/control_mailbox.py` implements daemon commands as JSON files.

### 19.1 Commands

Current command set:

- `stop`
- `pause`
- `resume`
- `reload_config`
- `set_config`
- `add_task`
- `add_idea`
- `queue_reorder`

### 19.2 Command Lifecycle

1. CLI or control API writes a JSON command envelope into `incoming/`
2. daemon engine discovers it
3. engine parses it into `ControlCommandEnvelope`
4. engine applies the command
5. result is archived into `processed/` or `failed/`
6. the incoming file is removed

This gives deterministic and inspectable command handling without sockets or RPC.

## 20. Control API

`control.py` is the operator-facing library surface.

It is the thin layer between the CLI and the engine/filesystem.

CLI-based advisor agents are expected to use this surface through the packaged guidance in `ADVISOR.md`, not by writing runtime state files directly.

### 20.1 Main Responsibilities

- load config and resolve paths
- expose status and queue inspection
- expose direct operations when daemon is not running
- route mutating operations through mailbox when daemon is running
- manage config mutation/reload boundaries
- build runtime-state snapshots
- expose resolver-backed asset inspection views for status and config reporting

### 20.2 Direct vs Mailbox Mode

If the daemon is not running, some commands act directly.

Examples:

- `config set`
- `config reload`
- `add-task`
- `add-idea`
- `queue reorder`

If the daemon is running, these become mailbox commands instead so one owner process stays in control of live state.

### 20.3 Runtime Snapshot

`RuntimeState` persists:

- whether the process is running
- whether it is paused
- execution status
- research status
- active task id
- backlog depth
- deferred queue size
- uptime
- config hash
- asset bundle version
- start/update times
- mode (`once` or `daemon`)

## 21. Engine Supervisor

`engine.py` is the async-owned runtime supervisor.

### 21.1 Construction

At initialization the engine:

1. resolves `config_path`
2. loads config
3. builds runtime paths
4. constructs `ExecutionPlane`
5. constructs `ResearchPlane`
6. constructs an `EventBus`
7. wires the research plane back into the event bus

### 21.2 Daemon-Owned State

The engine owns:

- `started_at`
- `paused`
- `stop_requested`
- `input_queue`
- `file_watcher`

### 21.3 Input Handling

The engine handles normalized watcher/poll input events:

- backlog change: reconcile/thaw any completed research-recovery latch state
- config change: validated reload with boundary rules
- control command available: process mailbox
- idea submitted: emit `handoff.idea_submitted`
- stop/completion markers: request daemon stop

### 21.4 Main Daemon Loop

In daemon mode the loop does:

1. drain pending input queue items
2. ingest poll fallback events when in poll mode
3. process mailbox commands
4. apply any due cycle-boundary config changes
5. sync ready research dispatch work
6. write runtime state
7. if not paused, run one execution cycle in a worker thread
8. handle post-cycle controls (usage-budget pause, `NET_WAIT` recovery, pacing delay)
9. write runtime state again
10. wait for watcher wakeup or poll timeout

The execution plane runs under `asyncio.to_thread(...)` because the engine loop is async-owned but the execution plane itself is synchronous and file/subprocess heavy.

### 21.5 Shutdown

On shutdown the engine:

1. stops the watcher if present
2. shuts down the research plane lock/checkpoint surface cleanly
3. writes final `process_running=false` state
4. emits `engine.stopped`

This is deliberately ordered so the persisted runtime state matches process lifecycle.

## 22. Operator Surfaces

### 22.1 CLI Surface

`cli.py` is a thin Typer shell over `EngineControl`.

Commands:

- `init`
- `health`
- `start`
- `stop`
- `pause`
- `resume`
- `status`
- `run-provenance`
- `config show`
- `config set`
- `config reload`
- `queue`
- `queue inspect`
- `queue reorder`
- `add-task`
- `add-idea`
- `logs`
- `research`
- `publish`

Important design choice:

- the CLI does not implement runtime behavior itself
- it only validates arguments, builds context, calls `EngineControl`, and renders output

This keeps CLI behavior deterministic and easy to test.

### 22.2 TUI Surface

The TUI lives under `millrace_engine/tui/`.

The entrypoint is:

```bash
python3 -m millrace_engine.tui --config millrace.toml
```

The TUI is an operator shell, not a second runtime engine.

Its architecture is intentionally narrow:

- `__main__.py` resolves the config path and launches the app
- `app.py` owns the top-level Textual application, system commands, and screen installation
- `screens/health_gate.py` runs the deterministic workspace health check before the shell becomes interactive
- `screens/shell.py` owns the persistent operator shell, sidebar navigation, panel switching, modal launch points, and action dispatch
- `gateway.py` adapts `EngineControl` and publish helpers into typed TUI view payloads
- `store.py` and `messages.py` hold the immutable shell snapshot and internal message contracts
- `workers.py` owns threaded refresh, threaded health checks, and streamed event subscription
- `widgets/` renders the focusable Overview, Queue, Runs, Research, Logs, Config, and Publish panels
- `screens/` also contains guided modal flows for add-task, add-idea, config edit, run detail, help, and confirmation dialogs

Important design choices:

- the TUI does not bypass `EngineControl`
- direct engine hosting stays out of the shell; `start --once` and `start --daemon` still cross the supported CLI subprocess boundary
- refresh and event streaming run in Textual workers so the shell stays responsive
- daemon mutations follow the same mailbox-safe rules as the CLI
- the shell treats files under `agents/` as runtime outputs to observe through the control layer, not files to rewrite directly

Operationally, the TUI gives the operator:

- a startup health gate with retry plus config and log recovery previews
- one persistent shell with a sidebar, panel shortcuts, a command palette, and `?` help
- overview snapshots of runtime, config, queue, and research state
- queue visibility and reorder workflows
- research status, queue, governance, and recent-activity visibility
- live event log tailing plus filtering and run-id handoff into run detail
- concise recent-runs browsing backed by `run-provenance`
- guided config edits, reload, and publish confirmation flows

The TUI is therefore a denser presentation and control layer over the same runtime contract, not an alternate control plane.

### 22.3 External Supervisor Surface

OpenClaw or another external supervisor harness should treat Millrace as a one-workspace control plane.

The supported compatibility seam is intentionally narrow:

- `millrace --config millrace.toml supervisor report --json` for consolidated observation
- `millrace --config millrace.toml supervisor ... --issuer <name> --json` for attributable control actions

That report collapses health, readiness, runtime status, research status, queue depth, recent events, and machine-readable attention reasons without requiring the harness to synthesize raw runtime files.

Scheduling, messaging, wakeups, and multi-workspace portfolio logic stay outside the core runtime. External harnesses must not write `agents/.runtime/commands/incoming/` or other engine-owned files directly during normal supervision.

## 23. Foreground vs Daemon Behavior

### 23.1 `start --once`

Foreground once mode:

- builds the engine
- syncs ready research work before the execution cycle when non-stub research mode is active
- runs one execution cycle
- writes final state
- exits

This is the easiest entrypoint for:

- smoke tests
- CI checks
- deterministic local execution
- TUI-triggered single-cycle execution when the operator chooses Start Once from inside the shell

### 23.2 `start --daemon`

Foreground daemon mode:

- starts the watcher if needed
- owns mailbox processing
- writes `state.json`
- remains alive until stopped

This is the local long-running runtime mode.

When the operator starts daemon mode from the TUI, the shell launches the same supported path and then returns to observation, refresh, and mailbox-safe control.

## 24. Tests and Fixtures

The test suite is under `millrace/tests/`.

### 24.1 Test Helper Layer

`tests/support.py` provides:

- fixture materialization
- workspace loader
- runtime path loader
- state reader
- wait helper

### 24.2 Fixture Library

`tests/fixtures/` contains reusable scenario workspaces:

- `base`
- `golden_path`
- `quickfix_recovery`
- `quickfix_exhausted`
- `needs_research`
- `backlog_empty`
- `config_hotswap`
- `control_mailbox`
- `watcher_stop_completion`

These are real filesystem overlays, not only programmatic temp builders.

### 24.3 Coverage Areas

The tests cover:

- config loading
- scaffold/path expectations
- registry/materialization/compiler frozen-plan contracts
- status validation
- queue behavior
- runner artifact capture
- execution happy path
- quickfix and escalation flows
- research freeze behavior
- policy hooks and outage routing behavior
- CLI direct and daemon control
- watcher intake
- research dispatch/supervisor behavior (stub and non-stub modes)
- publish staging and preflight/commit surfaces
- TUI runtime gateway/store/panel behavior
- TUI modal actions, command flows, and run-detail drilldown
- TUI snapshots for the shell, panels, and health gate
- module entrypoint smoke path

## 25. End-to-End Examples

### 25.1 Empty Backlog Smoke Run

With the default `millrace/millrace.toml`:

- `integration_mode = "never"`
- `run_update_on_empty = false`

So:

```bash
python -m millrace_engine --config millrace.toml start --once
```

can return cleanly without needing stage runners or external tools.

### 25.2 Typical Daemon Control Flow

1. operator runs `start --daemon`
2. engine writes `state.json`
3. CLI `pause` writes a mailbox command
4. watcher or poll detects the new command file
5. engine processes the command and archives it
6. CLI `resume` repeats the same pattern
7. CLI `stop` sets `stop_requested`
8. engine stops watcher, writes final state, and exits

### 25.3 Typical Research Handoff

1. execution cannot recover locally
2. consult returns `NEEDS_RESEARCH`
3. queue quarantines the active task
4. freeze block is appended to `tasksbackburner.md`
5. blocker entry is written to `tasksblocker.md`
6. latch is written to `.runtime/research_recovery_latch.json`
7. event bus emits `handoff.needs_research`
8. research plane either records a stub breadcrumb (`research.mode = "stub"`) or compiles/runs a real dispatch (`auto|goalspec|incident|audit`)

## 26. Intentional v1 Limits

Some parts of the type/config vocabulary are broader than current v1 behavior.

Examples:

- `large_only` integration policy is present, but integration routing remains intentionally simple and policy-bounded
- AUTO research dispatch intentionally rejects simultaneous ready queue groups instead of multiplexing families in one cycle
- the stage/status vocabulary is broader than the currently executed kernel paths (some contracts remain forward-compatible placeholders)
- execution-side usage-budget auto-pause and post-update pacing are enforced, but the budget decision still depends on local sampling availability; a missing or incompatible `provider=codex` environment degrades to recorded "continue without auto-pause" evidence rather than a hard fault
- incident/audit/goalspec flows are implemented but intentionally still evolve on top of the same blocker/quarantine and file-backed recovery foundations

This is deliberate. The v1 runtime aims for a solid local execution core with explicit extension points, not a partially-finished everything-system.

## 27. How To Extend This Runtime Safely

If extending the system, preserve these invariants:

- keep `millrace/` as the product root
- keep file-backed truth surfaces authoritative
- validate status transitions instead of bypassing them
- route live daemon mutations through `EngineControl` and mailbox commands
- keep watcher callbacks intake-only
- keep the execution plane responsible for routing decisions
- keep stages thin and runners normalized
- keep history hybrid: short index plus detailed files
- prefer additive extension points over changing core file contracts

## 28. Summary

The Millrace v1 runtime is a local, Python-native orchestration kernel built around:

- typed config
- canonical paths
- markdown-backed tasks
- validated status markers
- file-locked queue mutation
- deterministic execution-stage routing
- artifact-backed runner execution
- structured events plus hybrid history
- mailbox and watcher-based daemon control
- a Textual TUI operator shell over the same control layer
- a compiled research supervisor with `stub` compatibility mode

That combination gives Millrace a coherent runtime core without depending on the legacy shell implementation as the execution engine.
