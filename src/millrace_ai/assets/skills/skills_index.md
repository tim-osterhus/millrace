---
asset_type: skill
asset_id: skills-index
version: 1
description: Runtime-shipped index of available and deferred skills.
advisory_only: true
capability_type: documentation
recommended_for_stages:
  - builder
  - checker
  - fixer
  - doublechecker
  - updater
  - troubleshooter
  - consultant
  - planner
  - manager
  - mechanic
  - auditor
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

# Skills Index

This is the runtime-shipped skills index for stage entrypoints.
Entrypoints select skills from this index; they do not infer runtime behavior from arbitrary skill metadata.
Shipped stage-core skills use the hybrid contract with thin manifest frontmatter for identity and structured markdown bodies for the actual guidance.
Deferred skill assets remain metadata-light until they are shipped into the same hybrid contract.

Usage contract:
- open this index before selecting discretionary skills
- prefer `required_skill_paths` supplied by the runtime when present
- if no fixed required skills are supplied, choose up to three relevant skills
- avoid loading irrelevant skills

## Stage-Core Skills

| Skill | Description | Tags | Path | Status |
| --- | --- | --- | --- | --- |
| `builder-core` | Builder posture, scope control, and implementation evidence habits. | `execution`, `stage-core` | `skills/stage/execution/builder-core/SKILL.md` | shipped |
| `checker-core` | Checker verification posture and failure-report discipline. | `execution`, `stage-core` | `skills/stage/execution/checker-core/SKILL.md` | shipped |
| `fixer-core` | Fixer remediation narrowness and regression awareness. | `execution`, `stage-core` | `skills/stage/execution/fixer-core/SKILL.md` | shipped |
| `doublechecker-core` | Doublechecker confirmation posture for previously failed work. | `execution`, `stage-core` | `skills/stage/execution/doublechecker-core/SKILL.md` | shipped |
| `updater-core` | Updater factual reconciliation and doc-hygiene habits. | `execution`, `stage-core` | `skills/stage/execution/updater-core/SKILL.md` | shipped |
| `troubleshooter-core` | Troubleshooter diagnosis and smallest-safe-fix heuristics. | `execution`, `stage-core` | `skills/stage/execution/troubleshooter-core/SKILL.md` | shipped |
| `consultant-core` | Consultant escalation judgment and evidence-preserving recovery posture. | `execution`, `stage-core` | `skills/stage/execution/consultant-core/SKILL.md` | shipped |
| `planner-core` | Planner synthesis posture, assumption marking, and spec focus. | `planning`, `stage-core` | `skills/stage/planning/planner-core/SKILL.md` | shipped |
| `manager-core` | Manager decomposition posture, ordering, and task-verifiability habits. | `planning`, `stage-core` | `skills/stage/planning/manager-core/SKILL.md` | shipped |
| `mechanic-core` | Mechanic repair posture for planning-side inconsistencies. | `planning`, `stage-core` | `skills/stage/planning/mechanic-core/SKILL.md` | shipped |
| `auditor-core` | Auditor intake posture, evidence linkage, and incident normalization habits. | `planning`, `stage-core` | `skills/stage/planning/auditor-core/SKILL.md` | shipped |
| `arbiter-core` | Arbiter rubric discipline, parity judgment, and remediation handoff posture. | `planning`, `stage-core` | `skills/stage/planning/arbiter-core/SKILL.md` | shipped |

## Shared And Deferred Skills

| Skill | Description | Tags | Path | Status |
| --- | --- | --- | --- | --- |
| `millrace-skill-creator` | Shipped package for authoring new skill assets in the same hybrid format used by runtime skills. | `documentation`, `authoring` | `skills/millrace-skill-creator/SKILL.md` | shipped |
| `marathon-qa-audit` | Shared deep-audit method for broad end-to-end QA, first-run closure audits, and evidence-depth handling. | `verification`, `audit` | `skills/shared/marathon-qa-audit/SKILL.md` | shipped |
| `skills-readme` | Runtime skill-pack rules and constraints. | `documentation`, `runtime` | `skills/README.md` | shipped |
| `small-diff-discipline` | Keep implementation changes narrow and auditable. | `implementation`, `scope` | `deferred/small-diff-discipline.md` | deferred (not shipped) |
| `historylog-entry-high-signal` | Write concise, evidence-first history entries. | `reporting`, `evidence` | `deferred/historylog-entry-high-signal.md` | deferred (not shipped) |
| `compose-stack-change-protocol` | Guard compose and topology edits. | `infra`, `compose` | `deferred/compose-stack-change-protocol.md` | deferred (not shipped) |
| `playwright-ui-verification` | Deterministic browser/UI verification pattern. | `qa`, `ui` | `deferred/playwright-ui-verification.md` | deferred (not shipped) |
| `frontend-review` | Frontend correctness and design review guidance. | `frontend`, `review` | `deferred/frontend-review.md` | deferred (not shipped) |
| `codebase-audit-doc` | Structured repo-audit notes and findings. | `audit`, `docs` | `deferred/codebase-audit-doc.md` | deferred (not shipped) |
| `spec-writing-research-core` | Spec authoring with assumptions and evidence links. | `planning`, `specs` | `deferred/spec-writing-research-core.md` | deferred (not shipped) |
| `acceptance-profile-contract` | Acceptance gate and milestone structuring. | `planning`, `acceptance` | `deferred/acceptance-profile-contract.md` | deferred (not shipped) |
| `task-card-authoring-repo-exact` | Deterministic task card output tied to repo paths. | `planning`, `tasks` | `deferred/task-card-authoring-repo-exact.md` | deferred (not shipped) |
