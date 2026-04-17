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

Keep informational surfaces aligned with the implemented repo state after execution work. Treat `outline.md` as the first place to check, then reconcile only the stale facts that the evidence actually supports.

## Quick Start

1. Read `outline.md` first.
2. Compare it against completed-task evidence and the current repo structure.
3. Triage stale surfaces before changing any file.
4. Update only factual statements you can back with evidence.
5. If nothing is stale, say so explicitly and stop.

## Operating Constraints

- Stay in documentation reconciliation, not implementation.
- Prefer no-op honesty over speculative cleanup.
- Update only surfaces that are demonstrably stale.
- Keep changes narrow, factual, and easy to trace back to repo evidence.

## Inputs This Skill Expects

- `outline.md`
- completed-task evidence for the active pass
- current repo structure or nearby docs that prove the stale surface
- `historylog.md` when it helps anchor what already changed
- any request-provided summary or snapshot paths that explain the current run

## Output Contract

- A minimal set of factual doc edits when stale surfaces exist.
- An explicit no-op statement when no update is needed.
- Clear evidence of why each changed surface was stale.
- No invented progress, scope, or architecture.

## Procedure

1. Read `outline.md` before any other informational surface.
2. Compare the outline and adjacent docs with the execution evidence.
3. Mark only the surfaces that are actually stale.
4. Edit the smallest set of statements needed to restore factual alignment.
5. If no stale surface exists, record that as an explicit no-op.
6. Keep any summary artifact short and evidence-backed.

## Pitfalls And Gotchas

- Updating docs before stale-surface triage.
- Rewriting healthy surfaces just because they are nearby.
- Smuggling in architecture or progress that the repo does not show.
- Hiding a no-op behind vague reconciliation language.

## Progressive Disclosure

Start with `outline.md` and the narrowest completed-task evidence that could make it stale. Expand only if the first pass shows that other informational surfaces share the same drift.

## Verification Pattern

Verify each changed statement against a direct repo fact, completed-task artifact, or adjacent source doc. If no change was needed, verify that the current surfaces already match the evidence and report the no-op plainly.
