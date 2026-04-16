---
asset_type: skill
asset_id: consultant-core
version: 1
description: Consultant stage core escalation judgment and evidence-preserving recovery posture.
advisory_only: true
capability_type: stage_core
recommended_for_stages:
  - consultant
forbidden_claims:
  - queue_selection
  - routing
  - retry_thresholds
  - escalation_policy
  - status_persistence
  - terminal_results
  - required_artifacts
---

# Consultant Core

- Decide whether local continuation is credible before escalating.
- Preserve the evidence chain when repeated local recovery has already failed.
- Frame recovery options so the next stage can act without guesswork.
- Load optional skills only when they clarify the recovery or escalation choice.
