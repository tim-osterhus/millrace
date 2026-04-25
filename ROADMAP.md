# Roadmap

This roadmap describes Millrace's planned and exploratory direction. It is a
directional planning document, not a release log. Shipped changes belong in
`CHANGELOG.md`.

Millrace is still in a pre-1.0 stabilization period. The main priority is to
make the runtime reliable, inspectable, and honest about what it owns before
expanding the public surface area.

## Status Legend

- **Active**: work that is currently being implemented, validated, or prepared
  for the next packaged release.
- **Planned**: work that is fully intended to be implemented, although exact
  sequencing and release boundaries may change.
- **Maybe**: ideas that sound useful or interesting, but are not committed yet.
- **Unlikely**: ideas that are not expected to fit the project direction in the
  foreseeable future.

## Current Focus

Millrace is focused on stabilizing the pre-1.0 runtime line around three
contracts:

- the compiled graph plan as the runtime-authoritative structure
- first-class runner harnesses with clear diagnostics and artifacts
- durable local workspaces that can be inspected, resumed, and debugged without
  guessing what happened

## Active

### Compiler And Runtime Authority

The compiler is being hardened as the source of runtime structure. The goal is
for activation, request binding, recovery policy, completion behavior, and
post-stage routing to come from one compiled graph plan instead of scattered
runtime tables or prompt prose.

Expected user impact:

- more predictable workspace behavior after configuration or mode changes
- clearer `millrace compile show` output
- fewer hidden differences between what the compiler reports and what the
  runtime actually executes

### Runner Harness Validation

Codex and Pi are the current first-class runner harnesses. The active work is to
keep `default_codex` and `default_pi` aligned around the same Millrace-owned
stage prompt contract while preserving runner-specific diagnostics, timeout
handling, and persisted artifacts.

Expected user impact:

- easier selection between Codex and Pi modes
- clearer failure modes when a runner binary, transport, provider, or terminal
  marker fails
- stronger confidence that compiler changes still work through at least one
  known-good runner path

### Public Release Documentation

The public repo documentation is being kept in lockstep with packaged behavior.
That includes the README, runtime reference docs, changelog, and this roadmap.

Expected user impact:

- less stale public guidance
- clearer distinction between shipped facts, planned work, and speculative ideas
- easier evaluation of whether Millrace is ready for a given workspace

### Installed-Package-Centric Operation

Millrace is being overhauled from a workspace-centric runtime posture toward an
installed-package-centric one. Workspaces should remain durable runtime state
locations, but normal operation should depend on the installed package's CLI,
assets, docs, and defaults rather than on source-tree assumptions.

Expected user impact:

- cleaner packaged installs
- fewer source-checkout assumptions during runtime evaluation
- a clearer boundary between package-owned runtime assets and workspace-owned
  state

### Basic Daemon Logging

Daemon mode should be able to log basic runtime events when an operator passes
the relevant flag. The intent is a small, useful operational log, not a broad
analytics system.

Expected user impact:

- easier debugging of daemon startup, tick progression, idle state, runner
  dispatch, pause/resume/stop handling, and shutdown
- less reliance on ad hoc terminal capture during long-running operation

### Usage Counting And Auto-Pause Controls

Millrace needs automated token and usage counting that can support robust,
easily configurable auto-pause and resume behavior. The goal is to let operators
set practical usage boundaries without manually watching every runner session.

Expected user impact:

- safer long-running operation
- clearer usage accounting across runner invocations
- configurable pause/resume behavior when usage thresholds are reached or reset

### v1.0.0 Shape Finalization

Millrace's public runtime shape needs to be cemented before `v1.0.0`. That
means deciding which CLI surfaces, workspace contracts, package assets, runner
contracts, compiler outputs, and documentation promises are stable enough to
carry forward.

Expected user impact:

- fewer breaking changes after the 1.0 line starts
- a clearer upgrade path for pre-1.0 users
- a sharper distinction between baseline runtime commitments and future
  extension ideas

## Planned

### Pre-1.0 Runtime Stabilization

Millrace is intended to reach a stable pre-1.0 runtime baseline before widening
its feature set. That means keeping the CLI, workspace layout, compiled plan
shape, runner artifacts, and operator docs coherent enough that users can
reason about failures without reading the implementation.

### E2E Efficacy Discipline

End-to-end runtime evaluation should remain a first-class release practice.
The intended direction is to keep source and packaged-install runs comparable,
preserve evidence from failed harness runs, and fall back to a known-good runner
when needed to isolate compiler/runtime regressions from runner-specific issues.

### Operator Diagnostics

The operator surface should keep improving around `doctor`, `status`,
`status watch`, `runs ls`, `runs show`, `runs tail`, compile diagnostics, and
runner artifacts.

The goal is not a decorative dashboard. The goal is enough evidence to answer:

- what is active
- what ran
- what changed state
- why a stage failed, blocked, retried, escalated, or completed

### Workspace Durability

Workspace ownership, locking, pause/resume/stop behavior, stale-state recovery,
last-known-good compiled plans, and queue transitions are expected to keep
getting stricter.

The goal is for Millrace to survive long-running work, interruption, restart,
and operator inspection without corrupting workspace state or requiring manual
state surgery.

### Stage And Loop Authoring Guardrails

Stage kinds, graph loops, mode maps, entrypoint contracts, and stage-core skills
are intended to stay data-driven and compiler-valid. Future authoring support
should make it easier to extend those surfaces without blurring runtime-owned
behavior with advisory prompt text.

### Agent Event Hooks

Millrace is intended to support OpenClaw and similar agents through first-class
event hooks. Adding hooks should be easy enough that external agents can be
notified when important runtime events happen, such as task completion, Arbiter
pass/fail outcomes, or Consultant incident creation.

### Meta-Harness Improvement Loop

Millrace is intended to gain meta-harness capabilities that let it improve with
usage over time. This should be grounded in concrete runtime evidence and
operator-controlled policies rather than vague self-modification.

### Rust Runtime Port

A Rust version of Millrace is planned after the Python runtime shape is stable
enough to justify porting. New runtime updates should eventually be carried into
that Rust line as well.

## Maybe

### Additional Runner Harnesses

Additional non-CLI or tightly controlled runner adapters may make sense if they
can preserve the same `StageRunRequest -> RunnerRawResult` contract and produce
diagnosable artifacts. This is distinct from broad first-class support for
arbitrary external CLI coding harnesses.

### Public Extension Surface

Millrace may eventually expose a narrower extension story for custom stage
kinds, graph loops, or mode overlays. This is not committed yet because the
core shipped graph contract needs to stay small and understandable first.

### Specialized Audit Skills

More target-specific audit or review skills may be useful when a workspace has
clear domain needs. These should remain optional advisory assets rather than
new hidden routing behavior.

### First-Class TUI

A first-class terminal UI may be useful for a friendlier direct human operation
experience. This is not committed yet, and any TUI would need to preserve the
CLI and persisted workspace artifacts as the underlying source of truth.

## Unlikely

### External Pull Request Development

Millrace is not expected to accept general external pull requests. The project
is intentionally maintained with direct codebase control by the maintainer.

### Broad Analytics Dashboard

Millrace is not expected to become a general analytics or reporting product.
Runtime inspection should remain focused on operational evidence.

### Arbitrary User-Scripted Compiler Hooks

Arbitrary user scripts inside compiler materialization are unlikely because they
would make compiled runtime behavior harder to validate, reproduce, and audit.

### Sentinel Fleets Or Heavy Governance Families

Large sentinel systems, goalspec governance families, and broad registry
governance are unlikely to enter the baseline runtime unless the project proves
a concrete need that cannot be solved with the current runtime contracts.

### Other CLI Harness Runners

First-class runner support for other CLI harnesses such as Claude Code or
Gemini CLI is unlikely. Millrace's supported runner surface should stay narrow,
deterministic, and easy to diagnose rather than becoming a general wrapper over
every coding-agent CLI.

## How To Contribute

Accepted public contribution channels are limited to:

- suggested features or ideas
- bug reports

Feature suggestions should explain the concrete workflow or failure mode they
would improve. Bug reports should include the Millrace version, operating
environment, command sequence, expected behavior, actual behavior, and any
relevant runtime artifacts or logs.

General external pull requests are not accepted at this time. Please do not
open PRs unless the maintainer explicitly asks for one. This keeps architectural
control, release sequencing, and code ownership centralized.

## Disclaimer

This roadmap is directional and subject to change. It does not represent a
commitment, guarantee, obligation, or promise to deliver any specific feature,
behavior, or release by any specific date.
