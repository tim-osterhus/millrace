# Audit Template

Use this template for audit tickets under:
- `agents/ideas/audit/incoming/`
- `agents/ideas/audit/working/`
- `agents/ideas/audit/passed/`
- `agents/ideas/audit/failed/`

```yaml
---
audit_id: AUD-<id>
scope: <repo area or contract>
trigger: queue_empty|manual|incident_followup|other
status: incoming|working|passed|failed
owner: <role-or-team>
created_at: <ISO8601>
updated_at: <ISO8601>
---
```

## Objective
- What this audit is validating.

## Inputs
- Files/contracts examined.

## Checks
- `check 1`
- `check 2`
- `check 3`

## Findings
- Result summary and notable exceptions.

## Evidence
- Command outputs, logs, diagnostics, and file paths.

## Decision
- `PASS` or `FAIL`
- Rationale and residual risk.

## Follow-ups
- Required quickfix/task cards if failed.

