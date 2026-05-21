---
asset_type: skill
asset_id: evaluator-blueprint-core
version: 1
description: Evaluator Blueprint stage core posture for approval, critique, and generated task readiness.
advisory_only: true
capability_type: stage_core
recommended_for_stages:
  - evaluator_blueprint
forbidden_claims:
  - queue_selection
  - routing
  - retry_thresholds
  - escalation_policy
  - status_persistence
  - terminal_results
  - required_artifacts
---

# Evaluator Blueprint Core

## Purpose

Evaluate one Contractor Blueprint candidate against its draft, manifest, lineage, and task-readiness requirements. This skill keeps `evaluator_blueprint` focused on a defensible `blueprint_evaluation.json`, a precise critique when needed, and a generated task payload only when approval is justified.

## Quick Start

1. Check lineage and revision consistency before reviewing design quality.
2. Compare the packet against the active draft, manifest, source context, and prior approvals.
3. Approve only when a generated task can be emitted without hidden assumptions.
4. Reject with `blueprint_critique.json` when Contractor can revise the same draft.
5. Block when the evidence cannot support a trustworthy decision.

## Operating Constraints

- Stay advisory-only; do not imply ownership of queue movement, status persistence, stage ordering, or terminal policy.
- Do not rewrite the Contractor packet.
- Do not implement generated task work.
- Do not create markdown task queue files directly.
- Keep rejection critique items specific enough for the next Contractor pass.
- Keep approved generated task content aligned with the Blueprint packet and original draft acceptance.

## Inputs This Skill Expects

- The active `BlueprintDraftDocument`.
- The candidate `BlueprintPacketDocument` and matching markdown blueprint.
- The full manifest, original source spec, all drafts, prior critiques, prior evaluations, and prior approved Blueprint refs supplied by runtime context.
- The request-provided output paths for `blueprint_evaluation.json`, `blueprint_critique.json`, `generated_task.json`, and `evaluator_blueprint_report.md`.

## Output Contract

- A `BlueprintEvaluationDocument` written to `blueprint_evaluation.json`.
- A `BlueprintCritiqueDocument` written to `blueprint_critique.json` when rejecting.
- A generated task payload written to `generated_task.json` when approving.
- An evaluator report written to `evaluator_blueprint_report.md`.
- Scope, dependency, verification, acceptance, and risk findings that support the decision.
- A generated task that preserves lineage, Blueprint references, target paths, acceptance, required checks, and risk notes.

## Procedure

1. Validate draft, manifest, and packet lineage ids.
2. Confirm the candidate revision matches the draft revision expectation.
3. Review scope and file intent against the active draft.
4. Review dependency assumptions against prior approved work and draft order.
5. Review verification and acceptance for task readiness.
6. Write an approval evaluation plus generated task only if all required task fields are present.
7. Write a rejection evaluation plus `blueprint_critique.json` when changes are needed.
8. Block when missing or contradictory evidence prevents honest evaluation.

## Pitfalls And Gotchas

- Approving a packet because it sounds plausible while verification is weak.
- Rejecting with vague critique language that cannot guide revision.
- Letting prior approved blueprints drift out of view.
- Creating a generated task that drops root lineage or Blueprint references.
- Confusing an open question with an acceptable implementation assumption.

## Progressive Disclosure

Start with lineage, revision, and the candidate packet. Expand to manifest, source spec, all drafts, and prior approvals when the decision depends on cross-draft consistency. Stop when the evaluation, critique, or generated task is defensible from written evidence.

## Verification Pattern

Check that `blueprint_evaluation.json` decision and terminal result agree, any rejection has a concrete `blueprint_critique.json`, and any approval has a generated task with lineage, Blueprint refs, acceptance, required checks, and risks. If task promotion would require guesswork, approve nothing.
