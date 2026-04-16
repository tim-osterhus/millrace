---
asset_type: skill
asset_id: doublechecker-core
version: 1
description: Doublechecker stage core confirmation posture for repaired work.
advisory_only: true
capability_type: stage_core
recommended_for_stages:
  - doublechecker
forbidden_claims:
  - queue_selection
  - routing
  - retry_thresholds
  - escalation_policy
  - status_persistence
  - terminal_results
  - required_artifacts
---

# Doublechecker Core

- Verify that the prior defect is actually gone, not merely shifted.
- Focus on regression resistance and completion confidence.
- Report remaining gaps precisely when a repair is incomplete.
- Use optional skills only when they sharpen the follow-up verification pass.
