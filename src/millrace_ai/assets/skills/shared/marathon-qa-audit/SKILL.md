---
asset_type: skill
asset_id: marathon-qa-audit
version: 1
description: Shared deep-audit method for broad end-to-end QA, first-run closure audits, and evidence-depth handling when narrow checks are not enough.
advisory_only: true
capability_type: verification
recommended_for_stages:
  - checker
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

# Marathon QA Audit

## Purpose

Provide a serious deep-audit method for stages that need a broader pass than a
normal narrow contract check. This skill is reusable. It can support Arbiter's
first-run closure audits and it can also support Checker's broader final-state
QA when a task genuinely needs that level of validation.

## Quick Start

1. Start from the explicit contract surface: task acceptance, rubric, or other
   concrete criteria.
2. Decide whether this run needs a full-band audit or a narrower targeted
   retest.
3. Evaluate criterion by criterion using the deepest honest checks that are
   realistically available.
4. Record evidence depth, unavailable deeper checks, and residual uncertainty.
5. Distinguish real failures from merely reduced evidence quality.

## Audit Modes

### Full-Band Audit

Use this when:

- Arbiter is creating a rubric for the first time
- the earlier evidence surface is too weak or too narrow to trust
- Checker is validating work whose correctness depends on broad final-state or
  end-to-end behavior

Behavior:

- cover the full contract surface
- do not stop at the first shallow pass/fail signal
- prefer breadth plus depth over minimal closure

### Targeted Retest With Regression Sweep

Use this when:

- a stable rubric or expectations surface already exists
- the main need is to retest failed, uncertain, or weak-evidence criteria

Behavior:

- retest failed criteria first
- then retest uncertain or weak-evidence criteria
- then sweep adjacent or high-risk areas for regression

## Evidence-Depth Ladder

Attempt the deepest honest check that is realistically available:

1. live manual or interactive runtime validation
2. fresh local spin-up and direct interaction
3. automated end-to-end or integration execution
4. build, test, and log inspection
5. static code or artifact inspection
6. structural or traceability inspection only

Rules:

- prefer the deepest honest check available
- when a deeper preferred check is unavailable, continue downward instead of
  fabricating a failure
- record the highest depth actually achieved
- record deeper checks that were desired or attempted but unavailable

## Decision Rules

### Missing Deep Checks Do Not Automatically Create A Gap

Unavailable maximum-depth checks should reduce evidence quality, not
automatically create parity gaps or QA failures.

Use the strongest credible substitute evidence available and report the limit
explicitly.

### A Real Failure Requires Affirmative Failure Evidence

Use this skill to strengthen evidence, not to manufacture findings.

Examples of affirmative failure evidence:

- direct observed failure
- missing required behavior or artifact
- contradictory runtime or test evidence
- clear mismatch between the shipped state and the explicit criterion

### Uncertainty Must Stay Explicit

If the available evidence is weaker than preferred but still credibly supports
the criterion, say so and record lower confidence.

If the evidence is too thin to judge honestly either way, surface that
uncertainty rather than pretending it is either a failure or a pass.

## Verification Pattern

Before finalizing the run, check:

- whether the audit breadth matched the needs of the task or closure target
- whether reduced evidence quality was recorded honestly instead of converted
  into a false gap
- whether every claimed failure is backed by affirmative failure evidence
- whether uncertainty is explicit wherever deeper checks were unavailable
