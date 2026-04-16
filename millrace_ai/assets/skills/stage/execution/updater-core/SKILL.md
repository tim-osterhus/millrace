---
asset_type: skill
asset_id: updater-core
version: 1
description: Updater stage core factual reconciliation and doc hygiene habits.
advisory_only: true
capability_type: stage_core
recommended_for_stages:
  - updater
forbidden_claims:
  - queue_selection
  - routing
  - retry_thresholds
  - escalation_policy
  - status_persistence
  - terminal_results
  - required_artifacts
---

# Updater Core

- Reconcile informational surfaces to the implemented state only.
- Prefer concise factual updates over narrative flourish.
- Do not invent progress, scope, or architecture that the repo does not show.
- Load optional skills only when they help keep the update accurate and bounded.
