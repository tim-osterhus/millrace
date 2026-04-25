---
asset_type: skill
asset_id: hybrid-format-reference
version: 1
description: Reference notes on the portable versus Millrace-opinionated skill format split.
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

# Hybrid Format

This package supports two shapes:

- `portable`: body-only markdown, no Millrace frontmatter required.
- `millrace-opinionated`: body plus the current shipped Millrace skill frontmatter.

Prefer the portable profile when you want the smallest transferable artifact.
Switch to the opinionated profile when the skill is meant to ship inside Millrace.
