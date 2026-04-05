# Research Spec Interview

> Generated from the canonical JSON registry object. Edit the JSON file, not this companion.

- Kind: `registered_stage_kind`
- Canonical ID: `research.spec-interview`
- Version: `1.0.0`
- Tier: `default`
- Status: `active`
- Source Kind: `packaged_default`
- Source Ref: `registry/stages/research.spec-interview__1.0.0.json`
- Aliases: `spec-interview`
- Labels: `baseline`, `goalspec`, `research`
- Extends: _none_
- Created At: 2026-04-04T00:00:00Z
- Updated At: 2026-04-04T00:00:00Z

## Summary

Packaged scaffold for optional one-question-at-a-time GoalSpec interview hardening.

## Payload

```json
{
  "allowed_overrides": [
    "model_profile_ref",
    "runner",
    "model",
    "effort",
    "allow_search",
    "prompt_asset_ref",
    "timeout_seconds"
  ],
  "context_schema_ref": "research.spec_interview.context.v1",
  "contract_version": "1.0.0",
  "handler_ref": "millrace_engine.research.dispatcher:SpecInterviewStage",
  "idempotence_policy": "retry_safe_with_key",
  "input_artifacts": [
    {
      "kind": "spec_family_state",
      "multiplicity": "one",
      "name": "spec_family_state",
      "required": true
    }
  ],
  "kind_id": "research.spec-interview",
  "legal_predecessors": [
    "research.spec-synthesis"
  ],
  "legal_successors": [
    "research.spec-review"
  ],
  "output_artifacts": [
    {
      "kind": "spec_family_state",
      "name": "spec_family_state",
      "persistence": "runtime_bundle",
      "required_on": []
    },
    {
      "kind": "interview_decision",
      "name": "interview_decision",
      "persistence": "runtime_bundle",
      "required_on": []
    },
    {
      "kind": "stage_summary",
      "name": "stage_summary",
      "persistence": "history",
      "required_on": []
    }
  ],
  "plane": "research",
  "queue_mutation_policy": "runtime_only",
  "result_schema_ref": "research.spec_interview.result.v1",
  "retry_policy": {
    "backoff_seconds": 5.0,
    "exhausted_outcome": "terminal_failure",
    "max_attempts": 1
  },
  "routing_outcomes": [
    "success",
    "blocked",
    "terminal_failure"
  ],
  "running_status": "SPEC_INTERVIEW_RUNNING",
  "success_statuses": [
    "GOALSPEC_RUNNING"
  ],
  "terminal_statuses": [
    "GOALSPEC_RUNNING",
    "BLOCKED",
    "NET_WAIT"
  ]
}
```
