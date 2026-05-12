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

## Operating Constraints

- Use this skill to strengthen evidence, not to manufacture findings.
- Prefer the deepest honest check available without inventing unavailable
  validation.
- Treat missing maximum-depth checks as reduced evidence quality, not automatic
  proof of a gap.
- Require affirmative failure evidence before reporting a failure.
- Keep uncertainty explicit when evidence is too thin to judge honestly.

## Inputs This Skill Expects

- A concrete contract surface such as task acceptance, an Arbiter rubric, a
  closure target, or explicit QA criteria.
- Available implementation evidence, run artifacts, logs, commands, reports, or
  user-observable behavior.
- Any known limits on what can be run locally or inspected directly.
- Prior pass/fail, weak-evidence, or uncertain criteria when this is a retest.

## Output Contract

- A criterion-by-criterion audit result.
- The highest evidence depth actually reached for each important claim.
- Deeper checks that were desired or attempted but unavailable.
- Real failures backed by affirmative evidence.
- Explicit uncertainty where evidence remains too weak.
- A clear distinction between failure, pass with reduced confidence, and no
  honest judgment possible.

## Procedure

### Choose Audit Mode

Use full-band audit when:

- Arbiter is creating a rubric for the first time.
- The earlier evidence surface is too weak or too narrow to trust.
- Checker is validating work whose correctness depends on broad final-state or
  end-to-end behavior.

Full-band behavior:

- Cover the full contract surface.
- Do not stop at the first shallow pass/fail signal.
- Prefer breadth plus depth over minimal closure.

Use targeted retest with regression sweep when:

- A stable rubric or expectations surface already exists.
- The main need is to retest failed, uncertain, or weak-evidence criteria.

Targeted retest behavior:

- Retest failed criteria first.
- Then retest uncertain or weak-evidence criteria.
- Then sweep adjacent or high-risk areas for regression.

### Apply The Evidence-Depth Ladder

Attempt the deepest honest check that is realistically available:

1. live manual or interactive runtime validation
2. fresh local spin-up and direct interaction
3. automated end-to-end or integration execution
4. build, test, and log inspection
5. static code or artifact inspection
6. structural or traceability inspection only

Rules:

- Prefer the deepest honest check available.
- When a deeper preferred check is unavailable, continue downward instead of
  fabricating a failure.
- Record the highest depth actually achieved.
- Record deeper checks that were desired or attempted but unavailable.

### Apply Decision Rules

Unavailable maximum-depth checks should reduce evidence quality, not
automatically create parity gaps or QA failures. Use the strongest credible
substitute evidence available and report the limit explicitly.

A real failure requires affirmative failure evidence. Examples include:

- direct observed failure;
- missing required behavior or artifact;
- contradictory runtime or test evidence;
- clear mismatch between the shipped state and the explicit criterion.

If the available evidence is weaker than preferred but still credibly supports
the criterion, say so and record lower confidence. If the evidence is too thin
to judge honestly either way, surface that uncertainty rather than pretending it
is either a failure or a pass.

## Pitfalls And Gotchas

- Converting unavailable interactive validation into a false failure.
- Treating a green unit test as enough evidence for broad end-to-end behavior
  when the contract requires more.
- Stopping at the first passing signal when full-band audit breadth is needed.
- Reporting a gap without affirmative failure evidence.
- Hiding uncertainty behind confident language.

## Progressive Disclosure

Start with the explicit contract surface and the most direct available evidence.
Open broader logs, history, adjacent code, or integration artifacts only when
the contract surface or observed risk requires deeper investigation.

## Verification Pattern

Before finalizing the run, check:

- whether the audit breadth matched the needs of the task or closure target;
- whether reduced evidence quality was recorded honestly instead of converted
  into a false gap;
- whether every claimed failure is backed by affirmative failure evidence;
- whether uncertainty is explicit wherever deeper checks were unavailable.
