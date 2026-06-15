---
asset_type: skill
asset_id: updater-core
version: 1
description: Updater stage core factual reconciliation and doc hygiene habits.
advisory_only: true
capability_type: stage_core
recommended_for_stages:
  - updater
forbidden_claims:
  - queue_selection
  - routing
  - retry_thresholds
  - escalation_policy
  - status_persistence
  - terminal_results
  - required_artifacts
---

# Updater Core

## Purpose

Keep curated informational surfaces aligned with the implemented repo state after execution work. Treat the shared instructions already opened by the wrapper prompt, `workspace-map/index.md`, generated workspace-map freshness, impacted wiki pages, completed-task evidence, and relevant history entries as the standard reconciliation inputs.

## Quick Start

1. Read the shared instructions already opened by the wrapper prompt, then `workspace-map/index.md` and generated freshness.
2. Compare impacted curated wiki pages and relevant docs/source against completed-task evidence and the current repo structure.
3. Triage stale surfaces before changing any file.
4. Update only factual statements you can back with evidence.
5. Emit `workspace_map_update_request.json` as an advisory M1 refresh request instead of editing generated map files when generated data is stale; the runtime does not automatically consume this artifact.
6. If nothing is stale, say so explicitly and stop.

## Operating Constraints

- Stay in documentation reconciliation, not implementation.
- Prefer no-op honesty over speculative cleanup.
- Update only surfaces that are demonstrably stale.
- Edit only impacted curated wiki pages under `workspace-map/wiki/`; do not hand-edit generated workspace-map outputs.
- Treat `MILLRACE.md` as operator-owned shared instructions: read it for context only unless the active task explicitly requests shared-instruction changes.
- Completed evidence that shared instructions are stale is not enough by itself to authorize an edit; without an explicit shared-instruction update request, record the issue in the summary instead.
- Keep changes narrow, factual, and easy to trace back to repo evidence.

## Inputs This Skill Expects

- shared instructions already opened by the wrapper prompt
- `workspace-map/index.md`
- `workspace-map/generated/freshness.json`
- impacted curated wiki pages under `workspace-map/wiki/`
- completed-task evidence for the active pass
- current repo structure or nearby docs that prove the stale surface
- relevant rendered history entries under `history-log/` when they help anchor what already changed
- any request-provided summary or snapshot paths that explain the current run

## Output Contract

- A minimal set of factual doc edits when stale surfaces exist.
- A run-local advisory `workspace_map_update_request.json` when generated workspace-map output needs refresh.
- An explicit no-op statement when no update is needed.
- Clear evidence of why each changed surface was stale.
- No invented progress, scope, or architecture.

## Procedure

1. Read the shared instructions already opened by the wrapper prompt, then `workspace-map/index.md`, generated freshness, and the narrowest completed-task evidence.
2. Compare impacted curated wiki pages and adjacent docs/source with the execution evidence.
3. Mark only the surfaces that are actually stale.
4. Edit the smallest set of curated or source documentation statements needed to restore factual alignment.
5. If generated workspace-map output is stale, write a refresh request artifact instead of editing generated files.
6. If no stale surface exists, record that as an explicit no-op.
7. Keep any summary artifact short and evidence-backed.

## Pitfalls And Gotchas

- Updating docs before stale-surface triage.
- Rewriting healthy surfaces just because they are nearby.
- Smuggling in architecture or progress that the repo does not show.
- Hiding a no-op behind vague reconciliation language.

## Progressive Disclosure

Start with shared instructions, workspace-map index/freshness, and the narrowest completed-task evidence that could make a curated surface stale. Expand only to impacted wiki pages, relevant history entries, and overlapping docs/source.

## Verification Pattern

Verify each changed statement against a direct repo fact, completed-task artifact, or adjacent source doc. If no change was needed, verify that the current surfaces already match the evidence and report the no-op plainly.
