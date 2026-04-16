---
asset_type: skill
asset_id: checker-core
version: 1
description: Checker stage core verification posture and report discipline.
advisory_only: true
capability_type: stage_core
recommended_for_stages:
  - checker
forbidden_claims:
  - queue_selection
  - routing
  - retry_thresholds
  - escalation_policy
  - status_persistence
  - terminal_results
  - required_artifacts
---

# Checker Core

- Verify against the task contract, not against hopeful intent.
- Prefer concrete failure evidence over vague commentary.
- Keep remediation requests narrow enough for a follow-up stage to execute directly.
- Load optional skills only when they materially improve the verification signal.
