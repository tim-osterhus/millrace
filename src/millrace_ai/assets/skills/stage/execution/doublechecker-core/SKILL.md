---
asset_type: skill
asset_id: doublechecker-core
version: 1
description: Doublechecker stage core confirmation posture for repaired work.
advisory_only: true
capability_type: stage_core
recommended_for_stages:
  - doublechecker
forbidden_claims:
  - queue_selection
  - routing
  - retry_thresholds
  - escalation_policy
  - status_persistence
  - terminal_results
  - required_artifacts
---

# Doublechecker Core

## Purpose

Re-validate the repaired work against the active fix contract, not against a generic sense of completeness. This posture is advisory only: it keeps the follow-up focused on whether the known gaps are truly gone, whether they were merely displaced, and whether a renewed deterministic fix contract is needed.

## Quick Start

1. Read the active fix contract and restate the expected repaired state.
2. Write the expectations before looking at fixer evidence or implementation details.
3. Compare the repaired state against those expectations, not against the fixer narrative.
4. Renew the fix contract if the known gap still exists or has moved.

## Operating Constraints

- Stay contract-first and expectations-first.
- Focus on the known gap set, not on a generic rerun of the checker stage.
- Distinguish a true repair from a displaced symptom or partial cleanup.
- Keep any renewed fix contract deterministic enough for the next fix pass to act on directly.

## Inputs This Skill Expects

- The active fix contract and the original checker complaint.
- The fixer evidence and repaired repo state after expectations are written.
- The verification command or artifact path named by the contract.
- Enough nearby context to judge whether the repaired behavior actually satisfies the contract.

## Output Contract

- A concrete judgment about whether the known gaps are resolved.
- A renewed fix contract when any gap remains open, displaced, or ambiguous.
- Clear evidence that ties the outcome to the active contract.
- No generic rerun language, no silent acceptance, and no broadening into new checker work.

## Procedure

1. Restate the expected repaired state from the active fix contract.
2. Write the expectations artifact before inspecting fixer notes or implementation details.
3. Validate the repaired state against the expectations and the named checks.
4. Decide whether the known gaps are gone, displaced, or still open.
5. If gaps remain, rewrite the fix contract with the smallest deterministic next step.

## Pitfalls And Gotchas

- Treating a changed symptom as proof that the original gap is gone.
- Re-running the checker mechanically without re-establishing expectations.
- Accepting partial repairs that leave the contract unresolved.
- Renewing the fix contract in vague terms that force another interpretation round.

## Progressive Disclosure

Start with the smallest contract reading that lets you define the repaired state and the regression surface. Expand only when a specific expectation depends on additional context.

## Verification Pattern

Write expectations first, then compare the repaired state against them with the smallest relevant proof set. If the known gap is gone, report that explicitly; if not, turn the remaining issue into a renewed deterministic fix contract with exact scope and follow-up validation.
