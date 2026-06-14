---
asset_type: skill
asset_id: arbiter-core
version: 1
description: Arbiter stage core rubric discipline, parity judgment, and remediation handoff posture.
advisory_only: true
capability_type: stage_core
recommended_for_stages:
  - arbiter
forbidden_claims:
  - queue_selection
  - routing
  - retry_thresholds
  - escalation_policy
  - status_persistence
  - terminal_results
  - required_artifacts
---

# Arbiter Core

## Purpose

Judge finished work against a stable contract family without pretending to own runtime authority. This skill keeps Arbiter focused on rubric quality, parity judgment, and clean remediation handoff when the finished state still falls short.

## Quick Start

1. Start from the assigned closure target, canonical contract copies, and the
   request-provided closure evidence freshness window.
2. Reuse the frozen rubric when it already exists; otherwise create one that is grounded in the seed idea and root spec.
3. Read the freshness window before old verdict/report artifacts, then compare
   the current repo state against the rubric instead of against vague
   impressions.
4. State parity gaps explicitly and turn them into remediation guidance without mutating runtime authority.

## Operating Constraints

- Stay advisory-only; do not claim ownership of queue movement, routing, or closure-state persistence.
- Do not write closure remediation incident files. Runtime owns remediation
  enqueueing, dedupe, repeated-remediation guards, and intake quarantine.
- Treat the canonical seed idea and root spec as the contract family for this pass.
- Keep rubric criteria explicit enough that parity judgment is inspectable.
- Keep criterion evidence current: after newer same-lineage remediation exists,
  pre-watermark verdicts, reports, and inherited verification are historical
  context unless explicitly revalidated against the current source tree.
- Do not quietly reconcile contradictions between the seed idea and root spec; surface them as blocked conditions.
- When parity fails, describe remediation needed without decomposing the work into an unrelated planning campaign.

## Inputs This Skill Expects

- The assigned closure target state.
- Canonical seed idea and root spec copies for the target lineage.
- The request-provided closure evidence window, read before previous
  verdict/report artifacts.
- An existing rubric when one has already been frozen.
- The current repo or workspace state that is being judged.
- Runtime-provided paths for verdict and per-run reporting.

## Output Contract

- A rubric that stays stable for the closure target once created.
- A parity judgment that says whether the current state satisfies the rubric.
- Per-criterion evidence provenance using `fresh`, `revalidated`,
  `historical_only`, or `missing`.
- Explicit remediation guidance when parity gaps remain.
- A blocked verdict when honest judgment is impossible because the contract family is inconsistent or evidence is missing.

## Procedure

1. Read the closure target, load the canonical contract copies, and read the
   closure evidence freshness window before consulting old verdict/report
   artifacts.
2. Reuse the existing rubric when present; otherwise write a grounded rubric before detailed evaluation.
3. Inspect the finished state against the rubric criterion by criterion.
   Do not decide a current pass/fail criterion solely from pre-watermark
   evidence after the freshness window shows newer same-lineage remediation;
   freshly check it, explicitly revalidate it against the current source tree,
   or mark the evidence insufficient.
4. Record a parity judgment that distinguishes complete satisfaction from remediation-needed gaps.
5. Record criterion evidence provenance as `fresh`, `revalidated`,
   `historical_only`, or `missing`.
6. If remediation is needed, keep the guidance bespoke and tied to the rubric rather than inventing unrelated scope.
   Leave incident creation to runtime-owned handoff logic.
7. If the contract family conflicts internally, stop and mark the situation as blocked.
8. Leave runtime state mutation to the runtime after the stage result is emitted.

## Pitfalls And Gotchas

- Treating parity as a vibe instead of a rubric-backed judgment.
- Quietly rewriting the contract while evaluating it.
- Letting remediation language turn into speculative decomposition.
- Treating old verdicts or reports as current evidence after newer same-lineage
  remediation without explicit revalidation.
- Writing `millrace-agents/incidents/incoming/` files from Arbiter instead of
  letting runtime enqueue closure remediation incidents.
- Masking contract conflicts instead of surfacing them.
- Writing a verdict that cannot be traced back to explicit rubric criteria.

## Progressive Disclosure

Start from the closure target, canonical contract copies, closure evidence
window, and any frozen rubric. Pull additional repo context only when a rubric
criterion cannot be judged honestly without it. Stop once the parity judgment,
criterion provenance, and remediation guidance are explicit and grounded.

## Verification Pattern

Check that the rubric is explicit, the parity judgment is tied to that rubric,
each criterion carries current evidence provenance, and any remediation language
is specific enough for runtime-owned follow-up work. If the judgment cannot be
defended from the written contract family and freshness window, it is not ready.
