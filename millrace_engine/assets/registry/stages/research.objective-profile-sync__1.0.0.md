# Research Objective Profile Sync

> Generated from the canonical JSON registry object. Edit the JSON file, not this companion.

- Kind: `registered_stage_kind`
- Canonical ID: `research.objective-profile-sync`
- Version: `1.0.0`
- Tier: `default`
- Status: `active`
- Source Kind: `packaged_default`
- Source Ref: `registry/stages/research.objective-profile-sync__1.0.0.json`
- Aliases: `objective-profile-sync`
- Labels: `baseline`, `goalspec`, `research`
- Extends: _none_
- Created At: 2026-03-19T00:00:00Z
- Updated At: 2026-03-19T00:00:00Z

## Summary

Packaged scaffold for synchronizing objective-profile state before spec synthesis begins.

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
  "context_schema_ref": "research.objective_profile_sync.context.v1",
  "contract_version": "1.0.0",
  "handler_ref": "millrace_engine.research.dispatcher:ObjectiveProfileSyncStage",
  "idempotence_policy": "retry_safe_with_key",
  "input_artifacts": [
    {
      "kind": "research_brief",
      "multiplicity": "one",
      "name": "research_brief",
      "required": true
    }
  ],
  "kind_id": "research.objective-profile-sync",
  "legal_predecessors": [
    "research.goal-intake"
  ],
  "legal_successors": [
    "research.spec-synthesis"
  ],
  "output_artifacts": [
    {
      "kind": "objective_profile",
      "name": "objective_profile",
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
  "result_schema_ref": "research.objective_profile_sync.result.v1",
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
  "running_status": "OBJECTIVE_PROFILE_SYNC_RUNNING",
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
