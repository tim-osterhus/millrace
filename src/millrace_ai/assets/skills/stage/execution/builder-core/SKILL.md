---
asset_type: skill
asset_id: builder-core
version: 1
description: Builder stage core posture and evidence habits.
advisory_only: true
capability_type: stage_core
recommended_for_stages:
  - builder
forbidden_claims:
  - queue_selection
  - routing
  - retry_thresholds
  - escalation_policy
  - status_persistence
  - terminal_results
  - required_artifacts
---

# Builder Core

## Purpose

Classify the work shape first, then implement with the matching posture. Builder should decide whether the task is a foundational build, a feature slice on existing seams, or a small change or repair before it starts moving files.

## Quick Start

1. Read the task contract and identify the target paths.
2. Classify the work shape: foundational build, feature slice on existing seams, or small change or repair.
3. Choose the smallest coherent implementation posture that fits that shape.
4. Make the downstream verification path cheap and direct.

## Operating Constraints

- Keep scope tight to the assigned task and target paths.
- Avoid fake minimalism that leaves the real work half done.
- Avoid fake future-proofing that builds beyond the actual contract.
- Preserve target-path boundaries so follow-up work stays legible.

## Inputs This Skill Expects

- The active task or implementation contract.
- The explicitly owned file paths for the pass.
- The relevant nearby code or docs that define seams and boundaries.
- Any required verification commands or artifact expectations.

## Output Contract

- Working repo changes that satisfy the task contract.
- An evidence trail that makes downstream verification cheap and direct.
- No spillover into unrelated files or extra abstractions unless the contract truly needs them.
- Clear notes about blockers or residual risk when a complete pass is not possible.

## Procedure

1. Classify the work shape before changing code.
2. Identify the narrowest coherent seam that can satisfy the contract.
3. Implement directly against the target paths and keep boundary crossings explicit.
4. Verify the result with the smallest command set that still proves the change.
5. Stop when the contract is satisfied; do not widen into speculative cleanup.

## Pitfalls And Gotchas

- Fake minimalism that dodges the real behavior.
- Fake future-proofing that adds abstraction without a current need.
- Crossing target-path boundaries because the first idea was convenient.
- Leaving verification expensive for the next stage.

## Progressive Disclosure

Start with the smallest reading of the task that lets you classify the work shape, then expand only as far as needed to implement the chosen shape cleanly. Pull optional skills only when they materially improve the pass, not because they are available.

## Verification Pattern

Favor direct checks that prove the contract at the narrowest relevant surface. If the task is a small repair, verify the affected behavior directly. If it is a feature slice, verify the new seam and the adjacent boundary. If it is foundational, verify the assembled path that downstream stages will consume.
