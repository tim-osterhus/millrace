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

Scout briefly, classify only enough to size the seam correctly, then implement against the smallest architecturally coherent boundary that can satisfy the task honestly. Builder should use work-shape labels to calibrate seam size, abstraction budget, and proof burden, not as a ritual that replaces seam-finding.

## Quick Start

1. Read the task contract and identify the target paths.
2. Do a short scouting pass to find the real seam of change.
3. Classify the work shape only to calibrate seam size: foundational build, feature slice on existing seams, or small change or repair.
4. Choose the smallest architecturally coherent seam that fits that shape.
5. Make the downstream verification path cheap and direct.

## Operating Constraints

- Keep scope tight to the assigned task and target paths.
- Let work-shape classification calibrate seam size; do not let shape labeling replace seam selection.
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

1. Read the contract and do a short scouting pass before changing code.
2. Classify the work shape to calibrate seam size, abstraction budget, and proof surface.
3. Identify the smallest architecturally coherent seam that can satisfy the contract honestly.
4. Implement directly against that seam and keep boundary crossings explicit.
5. Verify the result at the proof surface that matches the work shape.
6. Stop when the contract is satisfied; do not widen into speculative cleanup.

## Pitfalls And Gotchas

- Fake minimalism that dodges the real behavior.
- Fake future-proofing that adds abstraction without a current need.
- Letting taxonomy replace seam-finding.
- Crossing target-path boundaries because the first idea was convenient.
- Leaving verification expensive for the next stage.

## Progressive Disclosure

Start with the smallest reading of the task that lets you scout the real seam, then expand only as far as needed to size and implement that seam cleanly. Pull optional skills only when they materially improve the pass, not because they are available.

## Verification Pattern

Favor direct checks that prove the contract at the proof surface implied by the chosen seam. If the task is a small repair, verify the affected behavior directly. If it is a feature slice, verify the new seam and the adjacent boundary. If it is foundational, verify the assembled path that downstream stages will consume.
