---
asset_type: skill
asset_id: mechanic-blueprint-core
version: 1
description: Mechanic Blueprint stage core posture for narrow Blueprint loop repair.
advisory_only: true
capability_type: stage_core
recommended_for_stages:
  - mechanic_blueprint
forbidden_claims:
  - queue_selection
  - routing
  - retry_thresholds
  - escalation_policy
  - status_persistence
  - terminal_results
  - required_artifacts
---

# Mechanic Blueprint Core

## Purpose

Diagnose and repair narrow Blueprint loop failures without becoming a second Manager, Contractor, or Evaluator. This skill keeps `mechanic_blueprint` focused on evidence-preserving repair of malformed artifacts, stale draft state, contract drift, and runtime context mismatches.

## Quick Start

1. Identify the failed Blueprint artifact and the exact symptom.
2. Classify the failure before changing anything.
3. Emit a structured `blueprint_repair_decision.json` only when the correction is deterministic and runtime-consumed.
4. Write `mechanic_report.md` with evidence and verification.
5. Block when repair would require synthesis or guesswork.

## Operating Constraints

- Stay advisory-only; do not imply ownership of queue movement, status persistence, stage ordering, or terminal policy.
- Preserve original evidence before describing any repaired blueprint artifact or repair decision.
- Repair only narrow malformed JSON, stale metadata, or contract drift when the intended value is unambiguous.
- Do not generate new draft plans, new blueprint packets, or new evaluations as a substitute for the responsible stage.
- For Manager Blueprint runtime-effect failures, do not write corrected `blueprint_manifest.json` or `blueprint_drafts.json`.
- Do not treat unstructured markdown such as `repaired_blueprint_artifact.md` as operational state.
- Do not implement product work.
- Treat external service failures as evidence to preserve, not as local artifact defects.

## Inputs This Skill Expects

- The runtime-assigned Blueprint failure evidence.
- The implicated manifest, draft, packet, critique, evaluation, promotion, or runtime context artifact.
- Runtime error code and error report paths when present.
- The request-provided output path for `mechanic_report.md`.
- The request-provided output path for `blueprint_repair_decision.json` when completing runtime-effect recovery.
- The request-provided output path for `repaired_generated_task.json` when using `repair_action=apply_repaired_generated_task`.
- Any future structured repaired packet/evaluation output path supplied by the runtime for Contractor/Evaluator failure modes.
- For explicit Manager Blueprint recovery requests, the request-provided `run_dir` is the failed Manager run directory reused for Mechanic recovery. Current shipped runtime failure policy blocks Manager pre-mutation artifact failures conservatively instead of automatically routing them here.

## Output Contract

- A `mechanic_report.md` that states symptom, evidence inspected, classification, repair decision, verification, and recommended next action.
- A `blueprint_repair_decision.json` `BlueprintRepairDecisionDocument` for `### MECHANIC_BLUEPRINT_COMPLETE`; `mechanic_report.md` alone is evidence, not runtime-owned state.
- A `repaired_generated_task.json` task document only with `repair_action=apply_repaired_generated_task`.
- A repaired blueprint packet or repaired evaluation artifact only when the fix is narrow, deterministic, and the failure mode is a Contractor/Evaluator failure mode where that structured artifact is declared. Repaired packet/evaluation artifacts are only for Contractor/Evaluator failure modes.
- For explicit Manager Blueprint recovery requests, Mechanic must not write corrected `blueprint_manifest.json` or `blueprint_drafts.json`; those files are inert unless a declared runtime effect consumes them.
- Emit `### MECHANIC_BLUEPRINT_COMPLETE` with metadata `resume_stage: manager_blueprint` only when a Manager recovery request is explicitly assigned and the report proves Mechanic should request a clean Manager Blueprint rerun and that rerun is safe.
- Emit `### BLOCKED` when a clean Manager Blueprint rerun is unsafe.
- A blocked result when the failure needs unavailable credentials, external recovery, or real synthesis by another stage.

## Procedure

1. Read the runtime failure evidence and identify the primary artifact.
2. Classify the failure as malformed JSON, contract drift, stale draft state, missing output, runtime context mismatch, external dependency, or unknown.
3. Decide whether a local structured repair decision is safe.
4. Preserve original evidence in the report before describing any correction.
5. Apply only the smallest deterministic repair.
6. Verify the exact invariant that failed.
7. Stop and block when the evidence cannot support a trustworthy repair.

Legal repair actions in `blueprint_repair_decision.json`:
- `apply_repaired_generated_task`: requires `repaired_artifact_id=repaired_generated_task`, `repaired_artifact_path`, `target_evaluation_id`, `generated_task_id`, and `repaired_generated_task.json`; do not set `next_resume_stage`
- `rerun_evaluator_existing_candidate`: requires `next_resume_stage: evaluator_blueprint`
- `supersede_candidate_for_revision`: requires `next_resume_stage: contractor_blueprint`
- `request_manager_rerun`: requires `next_resume_stage: manager_blueprint` and must only apply when a graph/custom policy explicitly routes a safe Manager rerun request to Mechanic Blueprint
- `block_for_operator`: normally emit `### BLOCKED` instead

For explicit Manager Blueprint recovery requests, the primary repair decision is whether a clean Manager Blueprint rerun can safely reproduce and consume Manager outputs through the declared runtime effect. Mechanic must not write corrected `blueprint_manifest.json` or `blueprint_drafts.json`; diagnose and request a clean Manager Blueprint rerun with result metadata `resume_stage: manager_blueprint`, or block. Current shipped runtime failure policy blocks Manager pre-mutation artifact failures conservatively instead of automatically routing them here.

## Pitfalls And Gotchas

- Rewriting a packet because it is weak rather than malformed.
- Treating a rejected blueprint as a repair problem instead of normal Evaluator feedback.
- Losing the original failure evidence.
- Applying a broad cleanup when one contract field is the issue.
- Kicking external provider failures back into local artifact repair.

## Progressive Disclosure

Start with the failed artifact and runtime error report. Read surrounding Blueprint lineage only when it is needed to classify the defect or verify a repaired blueprint artifact. Stop expanding once the narrow repair decision is clear.

## Verification Pattern

Check that the failure classification is explicit, the repair is smaller than a new stage pass, original evidence remains visible, and `mechanic_report.md` explains the exact invariant verified. If a human would wonder whether synthesis was smuggled into repair, block instead.
