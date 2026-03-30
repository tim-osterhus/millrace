# Research Taskmaster

> Generated from the canonical JSON registry object. Edit the JSON file, not this companion.

- Kind: `registered_stage_kind`
- Canonical ID: `research.taskmaster`
- Version: `1.0.0`
- Tier: `default`
- Status: `active`
- Source Kind: `packaged_default`
- Source Ref: `registry/stages/research.taskmaster__1.0.0.json`
- Aliases: `taskmaster`
- Labels: `baseline`, `goalspec`, `research`
- Extends: _none_
- Created At: 2026-03-19T00:00:00Z
- Updated At: 2026-03-19T00:00:00Z

## Summary

Packaged scaffold for emitting execution-ready task plans from reviewed GoalSpec artifacts.

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
  "context_schema_ref": "research.taskmaster.context.v1",
  "contract_version": "1.0.0",
  "handler_ref": "millrace_engine.research.dispatcher:TaskmasterStage",
  "idempotence_policy": "retry_safe_with_key",
  "input_artifacts": [
    {
      "kind": "approved_spec",
      "multiplicity": "one",
      "name": "approved_spec",
      "required": true
    }
  ],
  "kind_id": "research.taskmaster",
  "legal_predecessors": [
    "research.spec-review"
  ],
  "legal_successors": [],
  "output_artifacts": [
    {
      "kind": "task_plan",
      "name": "task_plan",
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
  "result_schema_ref": "research.taskmaster.result.v1",
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
  "running_status": "TASKMASTER_RUNNING",
  "success_statuses": [
    "IDLE"
  ],
  "terminal_statuses": [
    "IDLE",
    "BLOCKED",
    "NET_WAIT"
  ]
}
```
