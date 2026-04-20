---
asset_type: skill
asset_id: manager-core
version: 1
description: Manager stage core decomposition posture and task-verifiability habits.
advisory_only: true
capability_type: stage_core
recommended_for_stages:
  - manager
forbidden_claims:
  - queue_selection
  - routing
  - retry_thresholds
  - escalation_policy
  - status_persistence
  - terminal_results
  - required_artifacts
---

# Manager Core

## Purpose

Choose the decomposition shape before writing tasks. This skill keeps manager posture narrow and advisory-only: it sharpens how the active spec is split, ordered, and bounded so the result is useful to downstream work without pretending to own runtime behavior.

## Quick Start

1. Read the active spec as the only source of truth for the pass.
2. Classify the work as one of three shapes: a single execution slice, an ordered dependency chain, or a parallel fan-out with a clear integration boundary.
3. Prefer fewer meaningful tasks over trivia.
4. Make dependency order explicit before you write task text.
5. Load optional skills only when they materially improve task boundaries or acceptance quality.

## Operating Constraints

- Stay advisory-only; do not imply queue ownership, routing, persistence, or other runtime behavior.
- Treat the active spec as the decomposition target and do not borrow structure from unrelated items.
- Preserve root lineage ids on every emitted task so downstream closure and remediation logic stay attached to the active spec family.
- Keep decomposition honest. If task boundaries would require invention, say so and block rather than fabricating detail.
- Keep the task set lean. Extra tasks are only justified when they improve verifiability or ordering.
- Make dependencies visible in the task shape itself instead of hiding them in prose.

## Inputs This Skill Expects

- The active spec and its acceptance notes.
- Any explicit constraints that affect task boundaries or ordering.
- Nearby repo context only when it changes the decomposition shape.
- Optional skills that materially improve boundary quality, acceptance quality, or dependency clarity.

## Output Contract

- A decomposition recommendation that states the chosen shape and why it fits.
- Task boundaries that are concrete enough for downstream writing without becoming trivia.
- Explicit dependency ordering for chains and an explicit integration boundary for fan-out work.
- A truthful block when the spec cannot be decomposed without inventing requirements or dependencies.

## Procedure

1. Read the active spec and identify the smallest meaningful unit of work it supports.
2. Choose the shape first: single execution slice, ordered dependency chain, or parallel fan-out with a clear integration boundary.
3. Draw boundaries around real outcomes, not around every substep you can imagine.
4. For chains, order the steps so each dependency is visible and justified.
5. For fan-out, define the integration boundary before splitting so parallel pieces stay reconcilable.
6. Remove trivia. Keep only tasks that improve verifiability, sequencing, or acceptance.
7. If a boundary depends on missing facts that cannot be inferred honestly, block instead of inventing structure.
8. Pull optional skills only when they improve decomposition quality in a way the active spec can feel.

## Pitfalls And Gotchas

- Splitting before the shape is clear.
- Producing many tiny tasks that add noise instead of value.
- Letting dependency order stay implicit.
- Fan-out without a real integration boundary.
- Loading optional skills because they are available rather than because they improve the boundary or acceptance.
- Treating uncertainty as if it were structure.

## Progressive Disclosure

Start with the active spec and the lightest context needed to decide shape. Expand only when the next boundary decision depends on more evidence. Pull optional skills only when they materially improve task boundaries or acceptance quality, and stop reading once the decomposition is honest and usable.

## Verification Pattern

Check that the decomposition answers three questions clearly: what shape is this, what depends on what, and what would make the work honestly blocked. If any task exists mainly to create motion, collapse it. If the work fans out, confirm the integration boundary is explicit. If the answer requires invention, the decomposition is not ready.
