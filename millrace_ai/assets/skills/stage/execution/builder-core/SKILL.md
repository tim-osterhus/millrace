---
asset_type: skill
asset_id: builder-core
version: 1
description: Builder stage core posture and evidence habits.
advisory_only: true
capability_type: stage_core
recommended_for_stages:
  - builder
forbidden_claims:
  - queue_selection
  - routing
  - retry_thresholds
  - escalation_policy
  - status_persistence
  - terminal_results
  - required_artifacts
---

# Builder Core

- Keep scope tight to the assigned task and target paths.
- Prefer the smallest coherent implementation that satisfies the contract.
- Leave an evidence trail that makes downstream verification cheap.
- Reach for optional skills only when the task shape clearly calls for them.
