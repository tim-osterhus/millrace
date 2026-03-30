# Research Incident Intake

> Generated from the canonical JSON registry object. Edit the JSON file, not this companion.

- Kind: `registered_stage_kind`
- Canonical ID: `research.incident-intake`
- Version: `1.0.0`
- Tier: `default`
- Status: `active`
- Source Kind: `packaged_default`
- Source Ref: `registry/stages/research.incident-intake__1.0.0.json`
- Aliases: `incident-intake`
- Labels: `baseline`, `incident`, `research`
- Extends: _none_
- Created At: 2026-03-19T00:00:00Z
- Updated At: 2026-03-19T00:00:00Z

## Summary

Packaged incident intake scaffold that normalizes one incident request into a durable record.

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
  "context_schema_ref": "research.incident_intake.context.v1",
  "contract_version": "1.0.0",
  "handler_ref": "millrace_engine.research.dispatcher:IncidentIntakeStage",
  "idempotence_policy": "retry_safe_with_key",
  "input_artifacts": [
    {
      "kind": "research_request",
      "multiplicity": "one",
      "name": "research_request",
      "required": true
    }
  ],
  "kind_id": "research.incident-intake",
  "legal_predecessors": [],
  "legal_successors": [
    "research.incident-resolve"
  ],
  "output_artifacts": [
    {
      "kind": "incident_record",
      "name": "incident_record",
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
  "result_schema_ref": "research.incident_intake.result.v1",
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
  "running_status": "INCIDENT_INTAKE_RUNNING",
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
