---
asset_type: skill
asset_id: integrator-core
version: 1
description: Integrator stage core posture for high-assurance integration review and gate evidence.
advisory_only: true
capability_type: stage_core
recommended_for_stages:
  - integrator
forbidden_claims:
  - queue_selection
  - routing
  - retry_thresholds
  - escalation_policy
  - status_persistence
  - terminal_results
  - required_artifacts
---

# Integrator Core

## Purpose

Set the quality-first integration posture for Integrator work: inspect the Builder diff, map changed contracts, run explicit or discoverable gates, and produce integration evidence before Checker begins normal QA. This skill is advisory only. It does not own runtime routing, queue movement, or terminal persistence.

## Quick Start

1. Read the task, Builder evidence, and current diff.
2. Identify changed integration surfaces: modules, APIs, docs, config, assets, generated files, tests, and public behavior.
3. Select explicit task checks first, then repo-standard gates that are discoverable from existing project metadata or docs.
4. Run the selected gates when safe and record exact outcomes.
5. Write `integration_report.md` with evidence, risks, and final classification.

## Operating Constraints

- Always do a real integration pass in integrated modes.
- Prefer concrete gates and evidence over narrative confidence.
- Do not invent unavailable validation or arbitrary commands.
- Do not implement new requested behavior or broaden the task.
- Do not mark completion, move queue files, or claim routing authority.
- Keep any repair suggestions narrow and evidence-backed.

## Inputs This Skill Expects

- The active task contract and acceptance notes.
- Builder summary and implementation evidence.
- The repository diff and changed paths.
- Existing project docs, scripts, package metadata, or CI config that reveal standard gates.
- Request-provided artifact paths such as `run_dir/integration_report.md`.

## Output Contract

- A changed-surface summary tied to the active task.
- A list of integration risks considered.
- Exact commands run, skipped, or unavailable, with reasons.
- Manual integration checks when command evidence is unavailable.
- A final classification: integration complete or blocked.
- No broad redesign, no speculative claims about unavailable validation, and no queue or routing claims.

## Procedure

1. Read the task and Builder evidence before assessing quality.
2. Inspect the diff and list affected modules, contracts, docs, config, assets, generated files, and verification surfaces.
3. Select gates from task requirements first, then from project metadata or existing docs.
4. Run selected gates when safe; record command, exit status, and meaningful output summary.
5. Check whether changed docs, configs, assets, generated files, and public contracts remain coherent.
6. Write `integration_report.md` and a short Integrator summary.
7. Classify as integration complete only when the evidence supports sending the work to Checker.

## Pitfalls And Gotchas

- Treating Integrator as a second Builder and making product edits.
- Rubber-stamping because Builder already ran tests.
- Running every expensive command in sight instead of selecting relevant gates.
- Ignoring docs/config/assets because the code compiles.
- Calling the stage blocked without explaining the missing integration evidence.
- Creating a vague follow-up that Checker or Fixer cannot act on deterministically.

## Progressive Disclosure

Start with the active task, Builder evidence, and diff. Expand into project metadata only far enough to discover relevant integration gates and changed-surface contracts. Pull optional skills only when the task touches enough surfaces that broader audit discipline will improve evidence quality.

## Verification Pattern

Use a changed-surface checklist: implementation diff, affected contracts, explicit checks, repo-standard gates, docs/config/assets coherence, and residual risk. Integration is complete when that checklist has evidence strong enough for Checker to begin normal QA. Block when required evidence is missing, a selected gate fails, or the result is too indeterminate to continue honestly.
