---
asset_type: skill
asset_id: planner-core
version: 1
description: Planner stage core synthesis posture, assumption marking, and spec focus.
advisory_only: true
capability_type: stage_core
recommended_for_stages:
  - planner
forbidden_claims:
  - queue_selection
  - routing
  - retry_thresholds
  - escalation_policy
  - status_persistence
  - terminal_results
  - required_artifacts
---

# Planner Core

## Purpose

Turn rough inputs into explicit, testable planning work. This skill keeps planning honest about what is known, what is assumed, and what downstream decomposition can safely rely on. It uses direct assumption marking instead of hidden inference and keeps scope refinement before decomposition.

## Quick Start

1. Read the active planning input and the nearest repo context.
2. Use direct assumption marking for missing facts before they become hidden premises.
3. Scope refinement before decomposition comes before splitting.
4. Emit the smallest planning artifact that a downstream stage can actually use.

## Operating Constraints

- Keep the manifest thin and metadata-light; the body carries the actual guidance.
- Stay grounded in evidence instead of inventing certainty.
- Split only when justified.
- Keep an anti research-theater posture; reading more is only useful when it changes the plan.

## Inputs This Skill Expects

- The active planning input or draft spec.
- Adjacent repo context that affects scope or feasibility.
- Runtime-supplied required skill guidance.
- Explicit constraints, acceptance notes, and recovery evidence when present.

## Output Contract

- One grounded planning artifact that is concrete enough for downstream decomposition or implementation.
- Explicit assumptions, risks, and scope boundaries.
- A clear statement when the work is pass-through versus when refinement is needed.
- Additional child specs only when the work truly fans out.

## Procedure

1. Classify the input as pass-through, refine-in-place, or fan-out.
2. Mark missing facts directly as assumptions.
3. Refine scope before decomposition.
4. Split only when justified and the resulting pieces are clearly independent and testable.
5. Write the smallest spec that still exposes the checks a downstream stage needs.

## Pitfalls And Gotchas

- Research theater that expands reading without improving the plan.
- Premature decomposition that creates artificial tasks.
- Treating incomplete evidence as if it were complete.
- Smuggling implementation details into a planning pass.

## Progressive Disclosure

Start from the smallest useful reading of the input and expand only when a decision depends on more context. Pull optional skills only when they materially improve the plan, and stop once the spec is testable rather than continuing to polish the narrative.

## Verification Pattern

Check that the resulting plan answers: what is being changed, what is assumed, what is deliberately out of scope, and what downstream stage can verify. If any answer is still fuzzy, refine scope again before handing it off.
