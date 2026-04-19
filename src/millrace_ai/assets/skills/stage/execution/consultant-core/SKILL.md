---
asset_type: skill
asset_id: consultant-core
version: 1
description: Consultant stage core escalation judgment and evidence-preserving recovery posture.
advisory_only: true
capability_type: stage_core
recommended_for_stages:
  - consultant
forbidden_claims:
  - queue_selection
  - routing
  - retry_thresholds
  - escalation_policy
  - status_persistence
  - terminal_results
  - required_artifacts
---

# Consultant Core

## Purpose

Decide whether trustworthy local continuation still exists. Preserve evidence before deciding, make any continuing work explicitly bounded, and escalate with planning-quality clarity when local recovery is exhausted. Do not bias toward keeping execution local at all costs.

## Quick Start

1. Preserve the current evidence chain before drawing conclusions.
2. Read the latest troubleshoot material and the active work item, if present.
3. Decide whether the remaining local path is still credible.
4. If it is, state the continuation in explicit bounds.
5. If it is not, prepare an incident-quality escalation path.

## Operating Constraints

- Keep the decision evidence-driven and honest.
- Do not assume local continuation is preferable if the evidence says otherwise.
- Treat a continuation as credible only when it changes the decision basis: new evidence, a narrower bounded intervention, or a materially different recovery path.
- Do not improvise broad implementation or decomposition work.
- Preserve logs, reports, and prior conclusions before changing the decision frame.
- Make the next action explicit enough for the next stage to act without guesswork.

## Inputs This Skill Expects

- the latest troubleshoot report or equivalent failure summary
- `active_work_item_path` when present
- `runtime_snapshot_path`
- current run evidence and diagnostics
- prior checker or fix-contract evidence when it helps judge credibility

## Output Contract

- A clear local-continuation-or-escalation decision.
- The evidence used to make that decision.
- A bounded next step when local continuation remains credible.
- A planning-quality incident path when local recovery is exhausted.

## Procedure

1. Preserve evidence before deciding whether to continue locally.
2. Assess the failure pattern against the latest trustworthy artifacts.
3. Decide whether a local continuation is still credible or merely a restatement of the failing path.
4. If yes, describe the continuation with explicit scope, limits, and what materially changed the decision basis.
5. If no, create an escalation path that is concrete enough for planning intake.
6. If the evidence is too weak to decide honestly, block instead of guessing.

## Pitfalls And Gotchas

- Keeping execution local at all costs.
- Recommending a continuation path that is materially the same as the one that already failed.
- Escalating without enough preserved evidence.
- Writing a vague next step that still leaves the next stage guessing.
- Re-deciding without first anchoring the current evidence chain.

## Progressive Disclosure

Start from the failure pattern and the most recent trustworthy artifacts. Expand only until the continuation-versus-escalation choice is clear enough to state without ambiguity.

## Verification Pattern

Check that the decision is supported by preserved evidence and that the next action is actionable. A local continuation should be explicitly bounded and materially different from the failed path it replaces; an escalation should be incident-ready and concrete.
