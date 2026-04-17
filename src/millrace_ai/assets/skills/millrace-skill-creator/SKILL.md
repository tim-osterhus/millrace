---
asset_type: skill
asset_id: millrace-skill-creator
version: 1
description: General-purpose skill package creator and validation toolkit.
advisory_only: true
capability_type: documentation
recommended_for_stages:
  - builder
  - planner
forbidden_claims:
  - queue_selection
  - routing
  - retry_thresholds
  - escalation_policy
  - status_persistence
  - terminal_results
  - required_artifacts
---

# Millrace Skill Creator

## Purpose
Create skill packages that stay portable by default and become Millrace-opinionated only when the runtime contract needs to ship.

## Quick Start
```bash
python scripts/scaffold_skill.py /path/to/new-skill --profile millrace-opinionated
python scripts/lint_skill.py /path/to/the/package
python scripts/evaluate_skill.py /path/to/the/package
```

## Operating Constraints
- Stay local.
- no network access.
- no model calls.
- Keep metadata limited to the current shipped Millrace fields.
- Treat references as guidance docs, not shipped assets.

## Inputs This Skill Expects
- A destination directory for scaffolding.
- A profile choice: `portable` or `millrace-opinionated`.
- A package root to lint or evaluate.

## Output Contract
- `scaffold_skill.py` writes the full package layout.
- `lint_skill.py` reports local shape issues and exits non-zero on failure.
- `evaluate_skill.py` runs deterministic fixture cases and supports `--case-id`.

## Procedure
1. Scaffold the package.
2. Inspect `SKILL.md` and the references.
3. Run lint.
4. Run evaluation.
5. Ship only after the local checks are green.

## Pitfalls And Gotchas
- Do not lint `references/*.md` as if they were shipped skill assets.
- Do not add ornamental frontmatter fields.
- Do not rely on hidden state, network access, or model calls.
- Preserve the exact section order.

## Progressive Disclosure
Start with the portable profile when you want the lightest transferable artifact.
Use the opinionated profile when the skill should match Millrace's shipped asset shape.
Keep donor synthesis and hybrid-format guidance nearby, but do not inline all of it into the main skill body.

## Verification Pattern
Run the local lint first, then evaluation with or without `--case-id`.
If the package ships to a wheel, confirm the markdown, JSON, and Python assets are present in the archive.
