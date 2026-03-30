# Research Audit Intake

> Generated from the canonical JSON registry object. Edit the JSON file, not this companion.

- Kind: `registered_stage_kind`
- Canonical ID: `research.audit-intake`
- Version: `1.0.0`
- Tier: `default`
- Status: `active`
- Source Kind: `packaged_default`
- Source Ref: `registry/stages/research.audit-intake__1.0.0.json`
- Aliases: `audit-intake`
- Labels: `audit`, `baseline`, `research`
- Extends: _none_
- Created At: 2026-03-19T00:00:00Z
- Updated At: 2026-03-19T00:00:00Z

## Summary

Packaged audit intake scaffold that establishes one deterministic audit scope.

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
  "context_schema_ref": "research.audit_intake.context.v1",
  "contract_version": "1.0.0",
  "handler_ref": "millrace_engine.research.dispatcher:AuditIntakeStage",
  "idempotence_policy": "retry_safe_with_key",
  "input_artifacts": [
    {
      "kind": "research_request",
      "multiplicity": "one",
      "name": "research_request",
      "required": true
    }
  ],
  "kind_id": "research.audit-intake",
  "legal_predecessors": [],
  "legal_successors": [
    "research.audit-validate"
  ],
  "output_artifacts": [
    {
      "kind": "audit_scope",
      "name": "audit_scope",
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
  "result_schema_ref": "research.audit_intake.result.v1",
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
  "running_status": "AUDIT_INTAKE_RUNNING",
  "success_statuses": [
    "AUDIT_RUNNING"
  ],
  "terminal_statuses": [
    "AUDIT_RUNNING",
    "BLOCKED",
    "NET_WAIT"
  ]
}
```
