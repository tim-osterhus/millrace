---
asset_type: skill
asset_id: manager-blueprint-core
version: 1
description: Manager Blueprint stage core posture for strict-sequence manifest and draft generation.
advisory_only: true
capability_type: stage_core
recommended_for_stages:
  - manager_blueprint
forbidden_claims:
  - queue_selection
  - routing
  - retry_thresholds
  - escalation_policy
  - status_persistence
  - terminal_results
  - required_artifacts
---

# Manager Blueprint Core

## Purpose

Create a strict sequence of Blueprint drafts from one active planning source without taking over queue mechanics. This skill keeps `manager_blueprint` focused on durable `blueprint_manifest.json` and `blueprint_drafts.json` outputs that can be validated before any Contractor work begins.

## Quick Start

1. Read the active source artifact and capture root lineage ids exactly.
2. Choose the smallest meaningful draft sequence that covers the source scope.
3. Write one manifest and a complete draft list before declaring success.
4. Make every dependency point to an earlier draft.
5. Block when the source cannot support a truthful strict sequence.

## Operating Constraints

- Stay advisory-only; do not imply ownership of queue movement, status persistence, stage ordering, or terminal policy.
- Preserve `root_spec_id`, `root_idea_id`, `manifest_id`, and `source_spec_id` on every draft.
- Keep strict sequence semantics explicit. A later draft may depend only on earlier draft ids.
- Keep each draft large enough to be meaningful and small enough for one Contractor Blueprint pass.
- Do not create execution tasks or task markdown.
- Do not borrow requirements from unrelated queue items.

## Inputs This Skill Expects

- The active spec or incident supplied by the runtime.
- Root lineage ids from the active source artifact.
- The request-provided output paths for `blueprint_manifest.json` and `blueprint_drafts.json`.
- Local repo context only when it changes draft boundaries, target paths, acceptance, or verification intent.

## Output Contract

- A `BlueprintManifestDocument` written to `blueprint_manifest.json`.
- A JSON array of `BlueprintDraftDocument` records written to `blueprint_drafts.json`.
- Draft indexes that are contiguous from 1.
- Dependency ids that reference only earlier drafts.
- A concise summary of the strict sequence and any material risks.

## Procedure

1. Identify the active source artifact and verify root lineage ids are present.
2. Summarize the source scope in one sentence before splitting.
3. Choose draft boundaries around implementation outcomes, not around tiny substeps.
4. Order drafts so dependencies are visible and acyclic.
5. Fill every draft with title, summary, target paths, acceptance intent, verification intent, and a scoped context excerpt.
6. Validate that the manifest draft id list matches the draft records.
7. Stop and block if any draft would require invented requirements or missing lineage.

## Pitfalls And Gotchas

- Producing a manifest that names drafts not present in the draft file.
- Allowing a dependency to point forward in the strict sequence.
- Hiding required verification in prose instead of the draft contract.
- Splitting the work into tiny motion-only drafts.
- Letting a broad source become one vague draft.
- Treating uncertainty as accepted scope.

## Progressive Disclosure

Start with the active source and the minimum repo context needed to set draft boundaries. Read more only when a target path, acceptance criterion, or dependency cannot be grounded from the source. Stop once the manifest and draft list are complete enough for validation.

## Verification Pattern

Check that `blueprint_manifest.json` and `blueprint_drafts.json` agree, every draft preserves lineage, strict sequence dependencies point backward, and each draft has concrete target paths and verification intent. If a Contractor would need to guess the assignment, the draft is not ready.
