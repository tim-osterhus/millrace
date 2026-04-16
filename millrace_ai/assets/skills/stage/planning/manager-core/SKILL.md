---
asset_type: skill
asset_id: manager-core
version: 1
description: Manager stage core decomposition posture and task-verifiability habits.
advisory_only: true
capability_type: stage_core
recommended_for_stages:
  - manager
forbidden_claims:
  - queue_selection
  - routing
  - retry_thresholds
  - escalation_policy
  - status_persistence
  - terminal_results
  - required_artifacts
---

# Manager Core

- Break specs into execution-ready tasks with clear acceptance and checks.
- Order work so dependencies are explicit and safe to execute.
- Keep each task concrete enough that downstream implementation can verify completion.
- Consult optional skills only when they improve decomposition quality.
