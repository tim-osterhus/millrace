---
asset_type: skill
asset_id: troubleshooter-core
version: 1
description: Troubleshooter stage core diagnosis and smallest-safe-fix heuristics.
advisory_only: true
capability_type: stage_core
recommended_for_stages:
  - troubleshooter
forbidden_claims:
  - queue_selection
  - routing
  - retry_thresholds
  - escalation_policy
  - status_persistence
  - terminal_results
  - required_artifacts
---

# Troubleshooter Core

- Isolate the failure mode before changing code or environment.
- Prefer the smallest safe local fix that restores forward movement.
- Preserve enough evidence for future recovery if the first attempt fails.
- Reach for optional skills only when they clearly accelerate diagnosis.
