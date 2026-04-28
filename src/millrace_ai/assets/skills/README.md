---
asset_type: skill
asset_id: skills-readme
version: 1
description: Index and authoring notes for skill assets.
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

# Skills Assets

This directory holds advisory skill assets used by stage bundle composition.

Rules:
- Skills remain advisory-only.
- Skills must not claim runtime-owned behavior.
- Skills should be reusable across stages.

Runtime shipping notes:
- Runtime selection is still driven by entrypoints and `skills_index.md`, not by arbitrary skill metadata.
- `skills_index.md` is the canonical runtime index consumed by entrypoints.
- `README.md` remains the shared runtime baseline skill asset (`skills-readme`).
- The shipped `millrace-skill-creator` package is the runtime-facing authoring surface for new skill assets.
- Shipped shared reusable skills live under `skills/shared/<skill-id>/SKILL.md`.
- `marathon-qa-audit` is the shipped shared deep-audit skill currently used by `checker` and `arbiter` when a normal narrow pass is not enough.
- Shipped stage-core skills use the hybrid format with thin manifest frontmatter for identity and structured markdown sections in the body for the actual guidance.
- Stage-core skills live under `skills/stage/<plane>/<stage>-core/SKILL.md`.
- Each stage-core skill should stay narrow: posture, heuristics, traps, evidence discipline, and optional-skill triggers only.
- Additional optional skills should be referenced only after they are shipped in the runtime package or installed into the active workspace.
- Supported downloadable optional skills are listed at `https://github.com/tim-osterhus/millrace-skills/blob/main/index.md`.
- `millrace skills refresh-remote-index` writes the remote listing to
  `millrace-agents/skills/remote_skills_index.md`; `millrace skills install
  <skill_id>` installs an available remote skill as a workspace-local skill.
- Arbiter stage-core guidance should stay focused on rubric discipline, parity judgment, and remediation handoff posture rather than runtime authority.
