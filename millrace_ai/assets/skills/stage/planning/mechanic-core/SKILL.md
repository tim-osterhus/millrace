---
asset_type: skill
asset_id: mechanic-core
version: 1
description: Mechanic stage core repair posture for planning-side inconsistencies.
advisory_only: true
capability_type: stage_core
recommended_for_stages:
  - mechanic
forbidden_claims:
  - queue_selection
  - routing
  - retry_thresholds
  - escalation_policy
  - status_persistence
  - terminal_results
  - required_artifacts
---

# Mechanic Core

- Repair planning artifacts with the smallest coherent correction.
- Focus on inconsistency, drift, or malformed state before broader cleanup.
- Leave artifacts easier to reason about than you found them.
- Load optional skills only when they directly support the planning repair.
