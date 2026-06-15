# Intake And Delegation

## Contents

- Fit test
- Operating constraints
- Self-contained intake
- Workspace memory and shared instructions

## Fit Test

Prefer direct raw-harness work when all of these are true:

- the task is small, bounded, and likely to finish in one session
- durable queue state is unnecessary
- staged planning or execution gates are unnecessary
- interruption or retry cost is low
- no persisted run trail or closure pass is needed

Prefer Millrace when any of these matter:

- the work must survive pauses, context loss, or crashes
- durable queue state matters
- stage progression should be runtime-governed rather than conversational
- recovery routing matters more than raw one-shot speed
- persisted run artifacts, runtime snapshots, or diagnosable failure surfaces
  are useful
- closure should be based on runtime criteria rather than an agent's claim

Good Millrace examples include long-running implementation work,
planning-to-execution flows that need durable decomposition and auditability,
and repair-sensitive work where blockage should route through runtime recovery.

Poor Millrace examples include one-file direct bugfixes, short exploratory
spikes, ordinary repo edits where governance overhead exceeds the work, and
source-repo maintenance where no runtime workspace is actually being operated.

## Operating Constraints

- Treat the runtime as the source of truth for queue and run state.
- Prefer supported CLI commands over direct mutation of runtime-owned files.
- Keep runtime artifacts distinct from source edits.
- Treat `<workspace>/millrace-agents/` as runtime-owned unless a documented
  intake surface says otherwise.
- Treat `workspace-map/index.md` as seeded starter/index guidance.
- Treat `workspace-map/generated/` plus `workspace-map/manifest.json` as
  refreshable runtime artifacts.
- Treat `workspace-map/wiki/` pages as curated operator-maintained workspace
  memory.
- Treat run-local `history_entry.json` files as stage-produced history
  proposals. The runtime owns durable history append/render behavior.
- Treat `<workspace>/millrace-agents/MILLRACE.md` as operator-owned shared
  instructions after seeding. Read it for context unless the active task
  requests shared-instruction changes.
- Stage new ideas through
  `<workspace>/millrace-agents/intake/ideas/inbox/`.
- Keep operator-authored tasks, probes, specs, and ideas outcome-focused; do
  not hide routing instructions inside them.
- Do not describe this repo-local operator skill as a runtime-shipped stage
  asset.

## Self-Contained Intake

Queue intake commands are typed Millrace document imports:

- `queue add-task` imports a valid `TaskDocument`
- `queue add-probe` imports a valid `ProbeDocument`
- `queue add-spec` imports a valid `SpecDocument`
- `queue add-idea` stages idea-shaped markdown for later normalization

When supporting material is needed, package it inside the active workspace or
repo and reference it with repo-relative paths. Do not enqueue thin wrappers
that point to arbitrary local files outside the workspace. Stable public URLs
are acceptable only when the operator deliberately supplies them.

For non-trivial handoffs, prepare a workspace-local package such as:

```text
lab/intake/<intake-id>/
  probe.md
  architecture-spec.md
  supporting-notes.md
  reference-index.md
```

Then enqueue the typed work document. Example probe shape:

```markdown
# Millracer vNext Ops Gateway Recon Probe

Probe-ID: probe-millracer-vnext-ops-gateway
Title: Millracer vNext Ops Gateway Recon Probe
Summary: Inspect repo-local reference material before generating scoped work.
Request: Use the repo-local architecture spec and references to determine the
clean implementation plan, then emit generated specs or implementation tasks.
Created-At: 2026-05-26T06:36:36Z
Created-By: codex

Target-Paths:
- .
- src/millracer/
- tests/

References:
- lab/intake/millracer-vnext-ops-gateway/architecture-spec.md
- lab/intake/millracer-vnext-ops-gateway/reference-index.md
```

## Workspace Memory

Do not rewrite generated workspace-map files by hand. If an entrypoint emits
`workspace_map_update_request.json`, treat it as advisory until the runtime
applies supported workspace-map behavior. Keep `MILLRACE.md` as shared
operator-owned guidance and do not alias it with another root instruction file.
