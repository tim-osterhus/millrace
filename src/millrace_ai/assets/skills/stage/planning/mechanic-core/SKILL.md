---
asset_type: skill
asset_id: mechanic-core
version: 1
description: Mechanic stage core repair posture for planning-side inconsistencies.
advisory_only: true
capability_type: stage_core
recommended_for_stages:
  - mechanic
forbidden_claims:
  - queue_selection
  - routing
  - retry_thresholds
  - escalation_policy
  - status_persistence
  - terminal_results
  - required_artifacts
---

# Mechanic Core

## Purpose

Classify the planning-side blocker before attempting any repair, then apply the smallest safe correction that restores trustworthy forward progress. This skill stays narrow: it handles stale planning state, malformed planning artifacts, or contract drift, and it preserves evidence when a local repair is not trustworthy.

## Quick Start

1. Identify the concrete blocker symptom and the planning artifact it touches.
2. Decide whether the issue is safely repairable in place or whether evidence must be preserved instead.
3. Apply only the narrowest planning-side fix that removes the blocker.
4. Stop once the repair is trustworthy enough for the next planning step to proceed.

## Operating Constraints

- Stay advisory-only; do not imply ownership of runtime routing or persistence.
- Classify the blocker before changing anything.
- Prefer the smallest safe planning repair over broader cleanup.
- Focus on stale state, malformed artifacts, and contract drift.
- If local repair is not trustworthy, preserve evidence and leave the broader problem for a later seam.
- Do not turn this pass into a second planning synthesis cycle.

## Inputs This Skill Expects

- The active planning blocker or repair request.
- The implicated planning artifact, status note, or contract surface.
- Nearby evidence that explains whether the state is stale, malformed, or drifting.
- Prior recovery notes only when they clarify what changed and what still looks unreliable.

## Output Contract

- A clear blocker classification.
- The smallest safe planning repair, or a preserved evidence set if no safe repair exists.
- A concise note of what was inspected and why the chosen repair is narrow enough.
- The minimum verification needed to support the repair claim.
- A grounded next-step recommendation.

## Procedure

1. Read the blocker symptom and the nearest planning evidence.
2. Classify the failure as stale state, malformed artifact, contract drift, or another narrow planning-side issue.
3. Decide whether the issue can be repaired locally without weakening evidence.
4. Apply only the smallest correction that restores trust in the planning artifact.
5. Preserve original evidence before and after the repair when the local state is suspect.
6. Verify only enough to confirm the blocker is removed or that the evidence has been safely carried forward.
7. Stop if the repair would require broader synthesis, invention, or unrelated cleanup.

## Pitfalls And Gotchas

- Treating diagnosis as an excuse to expand into broader planning work.
- Rewriting a large structure when a narrow correction would do.
- Trusting repaired state without preserving the original evidence trail.
- Confusing uncertain cleanup with a trustworthy fix.
- Pulling in unrelated planning context just to make the narrative feel complete.

## Progressive Disclosure

Start with the blocker symptom and the nearest artifact. Read more only when the next classification decision depends on it. If the local repair path is becoming speculative, stop expanding and preserve the evidence instead of forcing a wider repair.

## Verification Pattern

Check that the blocker classification is explicit, the correction is the smallest safe one, the original evidence remains available, and the next step is grounded in what was actually repaired. If any of those are still unclear, the repair is not finished.
