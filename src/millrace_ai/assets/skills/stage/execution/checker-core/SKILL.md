---
asset_type: skill
asset_id: checker-core
version: 1
description: Checker stage core verification posture and report discipline.
advisory_only: true
capability_type: stage_core
recommended_for_stages:
  - checker
forbidden_claims:
  - queue_selection
  - routing
  - retry_thresholds
  - escalation_policy
  - status_persistence
  - terminal_results
  - required_artifacts
---

# Checker Core

## Purpose

Set the QA posture for checker work: validate the task contract first, then inspect evidence against that contract with disciplined, concrete findings. This skill is advisory only. It sharpens validation heuristics; it does not own runtime behavior, routing, or follow-up execution.

## Quick Start

1. Read the task contract and write the expected outcome before opening builder evidence or implementation details.
2. Convert the contract into concrete checks, outputs, and failure evidence you expect to see.
3. Inspect the implementation only after the expectations are written.
4. Classify the result as pass, fix-needed, or blocked using the task contract as the source of truth.

## Operating Constraints

- Stay narrow: verify the requested slice, not the builder's intent or broader repository goals.
- Prefer evidence that can be shown, reproduced, or pointed to directly.
- Keep findings deterministic so a follow-up fix can act on them without interpretation churn.
- Load optional skills only when they materially improve verification signal or reduce ambiguity.
- Do not drift into implementation ownership, queue behavior, or runtime decisions.

## Inputs This Skill Expects

- The active task contract and acceptance notes.
- The checker entrypoint guidance and any required verification command.
- Builder evidence only after expectations are written.
- Implementation details only after the contract-based checks exist.
- Optional supporting skills when they materially sharpen the review.

## Output Contract

- A short expectations-first validation frame.
- A concrete result classification: pass, fix-needed, or blocked.
- Failure evidence tied to the contract, not to generalized preference.
- Narrow fix language that names the exact missing behavior or artifact.
- No speculative redesign, no broad refactor advice, and no vague "needs improvement" language.

## Procedure

1. Translate the task contract into explicit expectations before reading builder notes or diffs.
2. Write down what success, failure, and blockage would look like in observable terms.
3. Inspect the implementation against those expectations, not against builder rationale.
4. Capture concrete evidence for every mismatch, including the smallest reproducible symptom.
5. If the task fails, turn the gap into a deterministic fix contract with exact scope and verification.
6. If the task is blocked, state the missing prerequisite and stop instead of guessing.

## Pitfalls And Gotchas

- Reviewing for polish while skipping the actual contract.
- Letting builder narrative replace evidence.
- Calling something "close enough" when the observable output still misses the requirement.
- Writing a fix request that is broad enough to require another round of interpretation.
- Treating uncertainty as a pass instead of blocked.

## Progressive Disclosure

Start with the smallest contract reading that lets you define the pass/fail surface. Expand only when an expectation depends on more context. Pull optional skills only when they materially increase verification signal; otherwise keep the review lean and contract-bound.

## Verification Pattern

Write expectations first, then verify the implementation against them. Check for concrete evidence that the task contract is satisfied or violated. Use clear result classification: pass when the contract is met, fix-needed when a narrow deterministic repair is possible, blocked when the evidence or prerequisites are insufficient. If a fix is needed, specify the exact gap, the smallest required change, and the command or artifact that should prove the repair.
