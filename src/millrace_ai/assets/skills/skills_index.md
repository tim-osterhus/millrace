---
asset_type: skill
asset_id: skills-index
version: 1
description: Runtime-shipped index of available skills and supported optional skill sources.
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
  - analyst
  - professor
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

# Skills Index

This is the runtime-shipped skills index for stage entrypoints.
Entrypoints select discretionary skills from this index; they do not infer runtime behavior from arbitrary skill metadata.
Stage-core skills are runtime-assigned by entrypoints or `required_skill_paths`, not selected as discretionary add-ons.
Shipped skills use the hybrid contract with thin manifest frontmatter for identity and structured markdown bodies for the actual guidance.

Usage contract:
- open this index before selecting discretionary skills
- prefer `required_skill_paths` supplied by the runtime when present
- if no fixed required skills are supplied, choose up to three relevant non-core skills
- avoid loading irrelevant skills
- do not claim an optional skill is available unless it is installed locally or listed in the supported downloadable skills directory

## Stage-Core Skills

These skills are listed for package inventory and auditability. They are not a
general-purpose skill menu; use them only when the runtime or the stage
entrypoint explicitly assigns them.

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
| `analyst-core` | Analyst research posture for skill learning requests and evidence packets. | `learning`, `stage-core` | `skills/stage/learning/analyst-core/SKILL.md` | shipped |
| `professor-core` | Professor authoring posture for skill candidates from research packets. | `learning`, `stage-core` | `skills/stage/learning/professor-core/SKILL.md` | shipped |
| `curator-core` | Curator improvement posture for workspace-installed skill updates. | `learning`, `stage-core` | `skills/stage/learning/curator-core/SKILL.md` | shipped |

## Shared Runtime Skills

| Skill | Description | Tags | Path | Status |
| --- | --- | --- | --- | --- |
| `millrace-skill-creator` | Shipped package for authoring new skill assets in the same hybrid format used by runtime skills. | `documentation`, `authoring` | `skills/millrace-skill-creator/SKILL.md` | shipped |
| `marathon-qa-audit` | Shared deep-audit method for broad end-to-end QA, first-run closure audits, and evidence-depth handling. | `verification`, `audit` | `skills/shared/marathon-qa-audit/SKILL.md` | shipped |
| `skills-readme` | Runtime skill-pack rules and constraints. | `documentation`, `runtime` | `skills/README.md` | shipped |

## Supported Downloadable Skills

Optional non-core skills live outside the Millrace runtime package. The supported
downloadable skills directory is:
`https://github.com/tim-osterhus/millrace-skills/blob/main/index.md`

Use that public index to discover optional skills before installing them into a
workspace. Once installed, rely on the workspace-local `skills_index.md` and the
installed `SKILL.md` files as the source of availability truth.
