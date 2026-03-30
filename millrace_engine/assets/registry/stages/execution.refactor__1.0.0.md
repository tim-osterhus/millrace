# Execution Refactor

> Generated from the canonical JSON registry object. Edit the JSON file, not this companion.

- Kind: `registered_stage_kind`
- Canonical ID: `execution.refactor`
- Version: `1.0.0`
- Tier: `default`
- Status: `active`
- Source Kind: `packaged_default`
- Source Ref: `registry/stages/execution.refactor__1.0.0.json`
- Aliases: `refactor`, `large-refactor`
- Labels: `execution`, `large`, `thorough`
- Extends: _none_
- Created At: 2026-03-19T00:00:00Z
- Updated At: 2026-03-19T00:00:00Z

## Summary

Packaged LARGE refactor stage registration for ordered thorough execution.

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
  "context_schema_ref": "execution.refactor.context.v1",
  "contract_version": "1.0.0",
  "handler_ref": "millrace_engine.stages.refactor:Stage",
  "idempotence_policy": "single_attempt_only",
  "input_artifacts": [
    {
      "kind": "task_card",
      "multiplicity": "one",
      "name": "task_card",
      "required": true
    }
  ],
  "kind_id": "execution.refactor",
  "legal_predecessors": [
    "execution.reassess"
  ],
  "legal_successors": [
    "execution.qa",
    "execution.update"
  ],
  "output_artifacts": [
    {
      "kind": "stage_summary",
      "name": "stage_summary",
      "persistence": "history",
      "required_on": [
        "success",
        "LARGE_REFACTOR_COMPLETE"
      ]
    },
    {
      "kind": "run_bundle",
      "name": "run_bundle",
      "persistence": "runtime_bundle",
      "required_on": [
        "success"
      ]
    }
  ],
  "plane": "execution",
  "queue_mutation_policy": "runtime_only",
  "result_schema_ref": "execution.refactor.result.v1",
  "retry_policy": {
    "backoff_seconds": 0.0,
    "exhausted_outcome": "blocked",
    "max_attempts": 0
  },
  "routing_outcomes": [
    "success",
    "blocked"
  ],
  "running_status": "BUILDER_RUNNING",
  "success_statuses": [
    "LARGE_REFACTOR_COMPLETE"
  ],
  "terminal_statuses": [
    "LARGE_REFACTOR_COMPLETE",
    "BLOCKED"
  ]
}
```
