---
asset_type: skill
asset_id: planner-core
version: 1
description: Planner stage core synthesis posture, assumption marking, and spec focus.
advisory_only: true
capability_type: stage_core
recommended_for_stages:
  - planner
forbidden_claims:
  - queue_selection
  - routing
  - retry_thresholds
  - escalation_policy
  - status_persistence
  - terminal_results
  - required_artifacts
---

# Planner Core

- Convert rough inputs into specific, testable specs.
- Mark assumptions explicitly when the source material is incomplete.
- Keep the spec scoped tightly enough for decomposition and execution.
- Use optional skills only when they materially improve the planning output.
