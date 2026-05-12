---
asset_type: skill
asset_id: curator-core
version: 1
description: Curator stage core posture for skill updates and evidence curation.
advisory_only: true
capability_type: stage_core
recommended_for_stages:
  - curator
forbidden_claims:
  - queue_selection
  - routing
  - retry_thresholds
  - escalation_policy
  - status_persistence
  - terminal_results
  - required_artifacts
---

# Curator Core

## Purpose

Curate skill improvements from runtime evidence, research packets, or Professor
drafts. Curator decides whether the proposed change fits the skill's scope,
keeps workspace-installed skills discoverable, and records why the accepted or
rejected update is justified.

## Quick Start

1. Read the learning request, linked evidence, and candidate package or patch.
2. Compare the proposed change with the current skill scope.
3. Accept only improvements that are supported by evidence.
4. Apply the smallest safe workspace-installed skill update when destination is clear.
5. Treat lint failures as remediation signals, not automatic blockers.
6. Record the decision in `curator_decision.md` with residual risk and promotion notes.

## Operating Constraints

- Preserve the skill's existing scope unless the evidence justifies widening it.
- Keep skill improvements small and reviewable.
- Do not publish speculative guidance that lacks runtime evidence.
- Avoid changing unrelated skills during the same curation pass.
- Keep source-of-truth and workspace-installed destinations explicit.
- Treat source promotion as a later operator command, not Curator-owned behavior.
- Perform format-only migration only for the same workspace-installed skill that
  is already receiving a supported behavior patch.
- Keep behavior changes distinct from mechanical section-contract migration in
  the decision record.

## Inputs This Skill Expects

- The active learning request.
- Runtime evidence, research packets, or Professor skill candidates.
- `run_dir/professor_skill_candidate/` or `run_dir/professor_skill_patch.md` when present.
- The current skill package and any installed workspace copy.
- Destination rules from `target_skill_id` or `preferred_output_paths`.

## Output Contract

- `run_dir/curator_decision.md` recording accepted, rejected, or blocked curation.
- `run_dir/curator_skill_update_summary.md` when workspace-installed skills change.
- A curated workspace skill update, rejected candidate, or blocked decision.
- A short explanation of evidence and scope fit.
- Separate notes for evidence-backed behavior changes and format-only migration.
- Notes that make later source promotion or rollback auditable.
- A no-op rationale in `run_dir/curator_decision.md` when the entrypoint terminal
  contract permits `CURATOR_NOOP` and no workspace-installed mutation is warranted.

## Procedure

1. Identify the candidate, patch, or direct evidence improvement being curated.
2. Resolve the workspace-installed skill destination before editing.
3. Check whether the evidence supports the behavior change.
4. Verify the change stays inside the skill's declared scope.
5. Apply or prepare the smallest skill improvement that addresses the evidence.
6. Run skill lint when the touched package includes a usable skill-lint surface.
7. If lint failures are limited to package shape or section contract, migrate the
   touched skill to the current format without adding unsupported behavior.
8. Rerun lint after a format-only migration and record pass/fail output.
9. Update discovery metadata only when trigger behavior changes.
10. Use the no-op terminal path (`CURATOR_NOOP`) when a concrete destination or
   candidate was reviewed and the supported action is to leave the workspace
   skill unchanged.
11. Record what changed, why it changed, and what was deliberately left out.

## Pitfalls And Gotchas

- Accepting a skill candidate because it is polished rather than evidenced.
- Widening scope until the skill becomes hard to trigger correctly.
- Losing the audit trail between evidence and edits.
- Mixing workspace experiments with source promotion.
- Editing source-packaged skills directly from a Curator pass.
- Blocking a request solely because no mutation was warranted after adequate
  destination and evidence review.
- Treating format-only migration as permission to rewrite skill behavior.
- Leaving a touched skill with a mechanical section-contract lint failure when
  the safe migration is straightforward.

## Progressive Disclosure

Start with the candidate, current skill, and evidence. Open broader references
only when the curation decision depends on package conventions or when the scope
boundary is ambiguous.

## Verification Pattern

Check that every accepted skill improvement points back to evidence, stays inside
scope, updates metadata only when justified, records the workspace-installed
skill destination, distinguishes behavior changes from format-only migration,
records skill-lint pass/fail output, and leaves enough context for future source
promotion or rollback.
