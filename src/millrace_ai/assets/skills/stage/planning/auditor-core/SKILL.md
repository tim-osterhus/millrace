---
asset_type: skill
asset_id: auditor-core
version: 1
description: Auditor stage core intake posture, evidence linkage, and incident normalization habits.
advisory_only: true
capability_type: stage_core
recommended_for_stages:
  - auditor
forbidden_claims:
  - queue_selection
  - routing
  - retry_thresholds
  - escalation_policy
  - status_persistence
  - terminal_results
  - required_artifacts
---

# Auditor Core

- Normalize incident inputs without dropping evidence.
- Preserve links between failure symptoms, artifacts, and prior recovery attempts.
- Shape the intake so planning can act on it immediately.
- Pull optional skills only when they help clarify the normalized incident record.
