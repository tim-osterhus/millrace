---
asset_type: skill
asset_id: donor-synthesis-reference
version: 1
description: Reference notes on donor-synthesis posture for skill package authoring.
advisory_only: true
capability_type: documentation
forbidden_claims:
  - queue_selection
  - routing
  - retry_thresholds
  - escalation_policy
  - status_persistence
  - terminal_results
  - required_artifacts
---

# Donor Synthesis

Use donor synthesis to assemble a new skill from proven source material.

1. Pick one donor that matches the desired runtime posture.
2. Keep the required section contract intact.
3. Preserve only the behavior that stays truthful after the merge.
4. Re-run the local lint and evaluation scripts before shipping.
