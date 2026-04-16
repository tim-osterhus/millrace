---
asset_type: skill
asset_id: fixer-core
version: 1
description: Fixer stage core remediation posture and regression awareness.
advisory_only: true
capability_type: stage_core
recommended_for_stages:
  - fixer
forbidden_claims:
  - queue_selection
  - routing
  - retry_thresholds
  - escalation_policy
  - status_persistence
  - terminal_results
  - required_artifacts
---

# Fixer Core

- Repair only the verified gap unless the fix requires a small adjacent change.
- Preserve working behavior while closing the known failure.
- Confirm that the applied fix matches the checker’s complaint exactly.
- Pull in optional skills only when the failure mode obviously benefits from them.
