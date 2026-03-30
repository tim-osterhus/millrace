# Execution Doublecheck

> Generated from the canonical JSON registry object. Edit the JSON file, not this companion.

- Kind: `registered_stage_kind`
- Canonical ID: `execution.doublecheck`
- Version: `1.0.0`
- Tier: `default`
- Status: `active`
- Source Kind: `packaged_default`
- Source Ref: `registry/stages/execution.doublecheck__1.0.0.json`
- Aliases: `doublecheck`
- Labels: `baseline`, `execution`, `standard`
- Extends: _none_
- Created At: 2026-03-19T00:00:00Z
- Updated At: 2026-03-19T00:00:00Z

## Summary

Packaged doublecheck stage registration for the standard execution path.

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
  "context_schema_ref": "execution.doublecheck.context.v1",
  "contract_version": "1.0.0",
  "handler_ref": "millrace_engine.stages.doublecheck:Stage",
  "idempotence_policy": "retry_safe_with_key",
  "input_artifacts": [
    {
      "kind": "task_card",
      "multiplicity": "one",
      "name": "task_card",
      "required": true
    }
  ],
  "kind_id": "execution.doublecheck",
  "legal_predecessors": [],
  "legal_successors": [],
  "output_artifacts": [
    {
      "kind": "stage_summary",
      "name": "stage_summary",
      "persistence": "history",
      "required_on": [
        "success",
        "QA_COMPLETE"
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
  "result_schema_ref": "execution.doublecheck.result.v1",
  "retry_policy": {
    "backoff_seconds": 0.0,
    "exhausted_outcome": "terminal_failure",
    "max_attempts": 2
  },
  "routing_outcomes": [
    "success",
    "quickfix_needed",
    "terminal_failure",
    "blocked"
  ],
  "running_status": "DOUBLECHECK_RUNNING",
  "success_statuses": [
    "QA_COMPLETE"
  ],
  "terminal_statuses": [
    "QA_COMPLETE",
    "QUICKFIX_NEEDED",
    "BLOCKED"
  ]
}
```
