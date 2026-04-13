# Spec Governance Artifacts

This folder stores governance state for GoalSpec quality gates and versioning.

- `golden_versions.json`: per-spec golden version registry keyed by `spec_id`, bumped when the authoritative goal/source artifact for the active spec family changes.
- `spec_quality_state.json`: per-spec quality score outcomes, threshold/failure counters, and PHASE_00 fallback emission flags.
- `decision_log_schema.json`: required decision-log fields (`decision_id`, `phase_key`, `phase_priority`, `status`, `owner`, `rationale`, `timestamp`) and allowed phase priorities (`P0`-`P3`).

These artifacts are maintained by the Python research runtime and should remain deterministic, local-only, and schema-stable.
