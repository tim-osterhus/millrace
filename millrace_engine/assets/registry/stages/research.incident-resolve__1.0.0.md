# Research Incident Resolve

> Generated from the canonical JSON registry object. Edit the JSON file, not this companion.

- Kind: `registered_stage_kind`
- Canonical ID: `research.incident-resolve`
- Version: `1.0.0`
- Tier: `default`
- Status: `active`
- Source Kind: `packaged_default`
- Source Ref: `registry/stages/research.incident-resolve__1.0.0.json`
- Aliases: `incident-resolve`
- Labels: `baseline`, `incident`, `research`
- Extends: _none_
- Created At: 2026-03-19T00:00:00Z
- Updated At: 2026-03-19T00:00:00Z

## Summary

Packaged incident remediation scaffold that resolves one normalized incident record.

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
  "context_schema_ref": "research.incident_resolve.context.v1",
  "contract_version": "1.0.0",
  "handler_ref": "millrace_engine.research.dispatcher:IncidentResolveStage",
  "idempotence_policy": "retry_safe_with_key",
  "input_artifacts": [
    {
      "kind": "incident_record",
      "multiplicity": "one",
      "name": "incident_record",
      "required": true
    }
  ],
  "kind_id": "research.incident-resolve",
  "legal_predecessors": [
    "research.incident-intake"
  ],
  "legal_successors": [
    "research.incident-archive"
  ],
  "output_artifacts": [
    {
      "kind": "incident_resolution",
      "name": "incident_resolution",
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
  "result_schema_ref": "research.incident_resolve.result.v1",
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
  "running_status": "INCIDENT_RESOLVE_RUNNING",
  "success_statuses": [
    "INCIDENT_RUNNING"
  ],
  "terminal_statuses": [
    "INCIDENT_RUNNING",
    "BLOCKED",
    "NET_WAIT"
  ]
}
```
