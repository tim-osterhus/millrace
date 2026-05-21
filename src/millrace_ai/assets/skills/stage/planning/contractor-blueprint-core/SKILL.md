---
asset_type: skill
asset_id: contractor-blueprint-core
version: 1
description: Contractor Blueprint stage core posture for one-draft implementation blueprints.
advisory_only: true
capability_type: stage_core
recommended_for_stages:
  - contractor_blueprint
forbidden_claims:
  - queue_selection
  - routing
  - retry_thresholds
  - escalation_policy
  - status_persistence
  - terminal_results
  - required_artifacts
---

# Contractor Blueprint Core

## Purpose

Write a complete implementation blueprint for a single draft without approving, queueing, or implementing it. This skill keeps `contractor_blueprint` focused on a coherent `blueprint_packet.json` plus matching `blueprint.md`, with critique handling when the draft is being revised.

## Quick Start

1. Treat the active Blueprint draft as the entire assignment.
2. Read the latest critique before designing a revision.
3. Produce a `BlueprintPacketDocument` with concrete scope, files, decisions, checks, and risks.
4. Mirror the JSON packet in `blueprint.md`.
5. Block if the draft lacks enough context for a trustworthy single draft plan.

## Operating Constraints

- Stay advisory-only; do not imply ownership of queue movement, status persistence, stage ordering, or terminal policy.
- Work on one active draft only.
- Do not inspect unrelated queued drafts unless the runtime context includes them.
- Do not create execution tasks.
- Do not approve or reject the candidate.
- Resolve critique items in the packet rather than burying them in prose.

## Inputs This Skill Expects

- The active `BlueprintDraftDocument`.
- The scoped context excerpt and target paths supplied for that draft.
- The latest critique packet when this is a revision.
- The request-provided output paths for `blueprint_packet.json` and `blueprint.md`.
- Optional research context only when explicitly available and relevant.

## Output Contract

- A `BlueprintPacketDocument` written to `blueprint_packet.json`.
- A human-readable `blueprint.md` that matches the packet.
- A revision number that matches the active draft revision expectation.
- Concrete intended files, design decisions, task acceptance, required checks, and risk notes.
- Open questions only when they are not blockers.

## Procedure

1. Read the active draft and confirm lineage ids.
2. If a critique packet exists, list each required change before revising.
3. Define implementation scope for the single draft.
4. Identify intended files and design decisions with enough precision for evaluation.
5. Write the verification plan and task acceptance as execution-ready criteria.
6. Capture risk notes honestly.
7. Write `blueprint_packet.json`, then write matching `blueprint.md`.
8. Block if the packet would depend on hidden assumptions.

## Pitfalls And Gotchas

- Expanding beyond the single draft.
- Treating a critique as optional advice when it requires a revision.
- Producing a blueprint packet that has a plan but no checks.
- Listing files that are not tied to the draft acceptance.
- Hiding uncertainty instead of making it a blocked condition or an open question.

## Progressive Disclosure

Start with the active draft, latest critique, and scoped context. Pull in repo context only to make file choices, dependency assumptions, or verification steps concrete. Stop once the packet is evaluable without further explanation.

## Verification Pattern

Check that `blueprint_packet.json` and `blueprint.md` describe the same single draft, every critique item has been addressed or explicitly blocked, and the verification plan could become task checks without interpretation. If Evaluator would need to infer the plan, the packet is not ready.
