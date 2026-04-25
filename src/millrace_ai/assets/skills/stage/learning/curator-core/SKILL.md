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
keeps metadata discoverable, and records why the accepted update is justified.

## Quick Start

1. Read the learning request, linked evidence, and candidate package or patch.
2. Compare the proposed change with the current skill scope.
3. Accept only improvements that are supported by evidence.
4. Update examples, references, and discovery metadata when needed.
5. Record the curation decision and residual risk.

## Operating Constraints

- Preserve the skill's existing scope unless the evidence justifies widening it.
- Keep skill improvements small and reviewable.
- Do not publish speculative guidance that lacks runtime evidence.
- Avoid changing unrelated skills during the same curation pass.
- Keep source-of-truth and workspace-installed destinations explicit.

## Inputs This Skill Expects

- The active learning request.
- Runtime evidence, research packets, or Professor skill candidates.
- The current skill package and any installed workspace copy.
- Destination rules for workspace-only updates versus promotable source assets.

## Output Contract

- A curated skill update, rejected candidate, or blocked decision.
- A short explanation of evidence and scope fit.
- Updated examples, references, or metadata when they are part of the change.
- Notes that make later promotion or rollback auditable.

## Procedure

1. Identify the skill package and the proposed improvement.
2. Check whether the evidence supports the behavior change.
3. Verify the change stays inside the skill's declared scope.
4. Apply or prepare the smallest skill improvement that addresses the evidence.
5. Update discovery metadata only when trigger behavior changes.
6. Record what changed, why it changed, and what was deliberately left out.

## Pitfalls And Gotchas

- Accepting a skill candidate because it is polished rather than evidenced.
- Widening scope until the skill becomes hard to trigger correctly.
- Losing the audit trail between evidence and edits.
- Mixing workspace experiments with source-of-truth promotion.

## Progressive Disclosure

Start with the candidate, current skill, and evidence. Open broader references
only when the curation decision depends on package conventions or when the scope
boundary is ambiguous.

## Verification Pattern

Check that every accepted skill improvement points back to evidence, stays inside
scope, updates metadata only when justified, and records enough context for a
future operator to understand the curation decision.
