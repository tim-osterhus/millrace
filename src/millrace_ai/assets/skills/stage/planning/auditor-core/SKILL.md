---
asset_type: skill
asset_id: auditor-core
version: 1
description: Auditor stage core intake posture, evidence linkage, and incident normalization habits.
advisory_only: true
capability_type: stage_core
recommended_for_stages:
  - auditor
forbidden_claims:
  - queue_selection
  - routing
  - retry_thresholds
  - escalation_policy
  - status_persistence
  - terminal_results
  - required_artifacts
---

# Auditor Core

## Purpose

Normalize one execution-recovery incident into a clean planning input while preserving the evidence chain that supports it. This skill stays on intake and enrichment: it clarifies the record, keeps unresolved assumptions visible, and does not decompose or solve the work.

## Quick Start

1. Read the single assigned incident artifact.
2. Inspect the evidence it references before changing the incident record.
3. Normalize the incident so the structure, links, and blocker description are easy to follow.
4. Surface unresolved assumptions explicitly and stop short of decomposition or solution work.

## Operating Constraints

- Handle one incident at a time.
- Preserve evidence linkage and source identity.
- Normalize and enrich the record, but do not decompose the work.
- Do not solve the incident or rewrite history into a different story.
- Do not upgrade hypotheses into facts unless the evidence already supports them.
- State unresolved assumptions explicitly instead of burying them in prose.
- Keep the intake scoped to the assigned incident only.

## Inputs This Skill Expects

- The assigned incident artifact.
- Evidence paths referenced by that incident.
- Planning status or history context when it clarifies how the incident should be normalized.
- Only the extra repo context needed to keep the evidence chain and blocker description coherent.

## Output Contract

- A normalized incident record with evidence links intact.
- A concise intake summary artifact.
- Clear unresolved assumptions and any remaining scope limits.
- Enough structure for Planner to consume the incident without another intake pass.

## Procedure

1. Read the assigned incident and confirm it is the only incident in scope.
2. Inspect the referenced evidence and verify the links are coherent.
3. Normalize the incident structure so symptoms, evidence, and context line up cleanly.
4. Enrich the record with explicit assumptions, gaps, and any remaining uncertainty without promoting suspected causes into facts.
5. Preserve original evidence references while avoiding decomposition or root-cause solving.
6. Verify that the incident still reads as one incident and not a hidden task breakdown.
7. Stop once the intake is clean enough for the next planning step.

## Pitfalls And Gotchas

- Pulling the incident apart into tasks or subproblems.
- Dropping evidence links while rewriting the narrative.
- Hiding unresolved assumptions inside confident-sounding prose.
- Turning a suspected root cause into a normalized fact without evidence.
- Expanding scope beyond the assigned incident.
- Solving the incident instead of preparing it for planning.

## Progressive Disclosure

Start from the assigned incident artifact and its direct evidence chain. Read more context only when it is needed to preserve linkage or clarify an assumption. If the record is still ambiguous, expose the ambiguity instead of trying to resolve it by decomposition.

## Verification Pattern

Check that exactly one incident is being handled, the evidence chain is intact, unresolved assumptions are visible, and the output is normalized rather than solved. If the record still needs another intake pass, it is not ready.
