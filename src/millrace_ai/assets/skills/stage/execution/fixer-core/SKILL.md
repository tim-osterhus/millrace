---
asset_type: skill
asset_id: fixer-core
version: 1
description: Fixer stage core remediation posture and regression awareness.
advisory_only: true
capability_type: stage_core
recommended_for_stages:
  - fixer
forbidden_claims:
  - queue_selection
  - routing
  - retry_thresholds
  - escalation_policy
  - status_persistence
  - terminal_results
  - required_artifacts
---

# Fixer Core

## Purpose

Fix against the active contract first, then make the smallest coherent repair that closes the verified gap without widening the task. This posture is advisory only: it keeps the repair narrow, regression-aware, and honest when the requested change really belongs to a different task.

## Quick Start

1. Read the active fix contract and restate the failure in concrete terms.
2. Identify the smallest repair that fully addresses that failure.
3. Check whether the repair touches adjacent behavior that could regress.
4. Stop and report blocked if the needed change would broaden the task.

## Operating Constraints

- Keep the repair contract-bound; do not drift into cleanup, redesign, or opportunistic refactors.
- Prefer the smallest coherent change that actually fixes the verified gap.
- Preserve existing working behavior unless the contract requires a narrow adjacent adjustment.
- Be explicit when the repair needs a different task, more context, or a broader scope than Fixer should take on.

## Inputs This Skill Expects

- The active fix contract and the original checker complaint.
- The minimal nearby code or docs needed to understand the failing path.
- Any required verification command or evidence target named by the contract.

## Output Contract

- A repair that closes the contract-defined gap as narrowly as possible.
- A clear note when the issue is blocked, out of scope, or requires a different task.
- Regression-aware evidence that makes the follow-up check straightforward.
- No silent scope drift, no speculative cleanup, and no implied ownership of runtime behavior.

## Procedure

1. Restate the fix contract in terms of the observable failure.
2. Find the narrowest coherent seam that can satisfy that contract.
3. Apply the repair directly at that seam and keep any adjacent edits explicit.
4. Verify the repaired path with the smallest command set that still proves the fix.
5. If the needed change expands beyond the contract, stop and report that honestly.

## Pitfalls And Gotchas

- Fixing the symptom while leaving the contract gap intact.
- Expanding into unrelated cleanup because it is nearby.
- Overfitting to the checker wording instead of the actual broken behavior.
- Hiding blockers that really mean the work belongs to another task.

## Progressive Disclosure

Start from the narrowest reading of the active contract, then expand only enough to understand the repair path and regression surface. Pull in more context only when it changes the correctness of the fix.

## Verification Pattern

Verify the specific broken behavior first, then check the adjacent behavior that could regress from the repair. If the contract names a command or artifact, use that as the primary proof. If the repair cannot be validated without broadening scope, return a blocked result instead of guessing.
