# Research Audit Validate

> Generated from the canonical JSON registry object. Edit the JSON file, not this companion.

- Kind: `registered_stage_kind`
- Canonical ID: `research.audit-validate`
- Version: `1.0.0`
- Tier: `default`
- Status: `active`
- Source Kind: `packaged_default`
- Source Ref: `registry/stages/research.audit-validate__1.0.0.json`
- Aliases: `audit-validate`
- Labels: `audit`, `baseline`, `research`
- Extends: _none_
- Created At: 2026-03-19T00:00:00Z
- Updated At: 2026-03-19T00:00:00Z

## Summary

Packaged audit validation scaffold that produces a durable audit report for gating.

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
  "context_schema_ref": "research.audit_validate.context.v1",
  "contract_version": "1.0.0",
  "handler_ref": "millrace_engine.research.dispatcher:AuditValidateStage",
  "idempotence_policy": "retry_safe_with_key",
  "input_artifacts": [
    {
      "kind": "audit_scope",
      "multiplicity": "one",
      "name": "audit_scope",
      "required": true
    }
  ],
  "kind_id": "research.audit-validate",
  "legal_predecessors": [
    "research.audit-intake"
  ],
  "legal_successors": [
    "research.audit-gatekeeper"
  ],
  "output_artifacts": [
    {
      "kind": "audit_report",
      "name": "audit_report",
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
  "result_schema_ref": "research.audit_validate.result.v1",
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
  "running_status": "AUDIT_VALIDATE_RUNNING",
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
