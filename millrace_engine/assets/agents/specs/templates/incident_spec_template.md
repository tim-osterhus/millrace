# Incident Spec Template

Use this template for structured incident intake files in:
- `agents/ideas/incidents/incoming/`
- `agents/ideas/incidents/working/`
- `agents/ideas/incidents/resolved/`
- `agents/ideas/incidents/archived/`

```yaml
---
incident_id: INC-<id>
fingerprint: <sha256>
failure_signature: <sha256>
status: incoming|working|resolved|archived
severity: S1|S2|S3|S4
source_task: <task heading or id>
opened_at: <ISO8601>
updated_at: <ISO8601>
---
```

## Summary
- What failed and where.

## Impact
- User/system impact and blast radius.

## Trigger
- Event or condition that caused this incident.

## Evidence
- Diagnostics bundle path(s).
- Run artifact path(s).
- Relevant status/quickfix/history pointers.

## Reproduction
1. Step 1
2. Step 2
3. Step 3

## Hypothesis
- Root-cause hypothesis and confidence.

## Alternative Hypotheses
- AH-01: <alternative explanation>
  - Status: candidate|supported|unsupported|inconclusive
  - Evidence: <path or excerpt proving status>
- AH-02: <alternative explanation>
  - Status: candidate|supported|unsupported|inconclusive
  - Evidence: <path or excerpt proving status>

## Investigation
- Steps:
  1. <investigation step>
  2. <investigation step>
- Findings:
  - <what was confirmed or falsified>

## Governance Routing
- Severity Class: S1|S2|S3|S4
- preemption behavior: S1 preempt-all incident work; S2 preempt S3/S4; S3/S4 continue FIFO.
- Incident Class: bug-class|spec-level|framework-level|task-card-quality|other
- minimal-unblock-first path: <smallest safe unblock step>
- rewrite task card path: <required when task is malformed/overscoped>
- spec addendum backflow: <required when root cause is spec-level; include addendum + reconciliation task>
- regression test requirement: <required for bug-class incidents>
- regression test evidence: <required for bug-class incidents>
- framework-level routing: <required for tool/script contract failures>

## Unsupported Hypotheses
- AH-<id>
  - Status: unsupported
  - Evidence: <counter-evidence path or excerpt>

## fix_spec
- Fix Spec ID: <spec_id>
- Fix Spec Path: `agents/ideas/specs/<spec_id>__<slug>.md`
- Scope summary: <one paragraph>
- Severity Class: <S1|S2|S3|S4>
- preemption behavior: <copied from Governance Routing>
- minimal-unblock-first path: <required>
- rewrite task card path: <required when malformed/overscoped>
- spec addendum backflow: <required when spec-level>
- regression test requirement: <required for bug-class incidents>
- regression test evidence: <required for bug-class incidents>
- framework-level routing: <required for tool/script contract failures>

## Task Handoff
- taskspending target: `agents/taskspending.md`
- decomposition stage: `agents/_taskmaster.md`
- handoff status: pending|emitted

## Attempt History
- `<timestamp> | Attempt <n> | Stage=<stage> | Outcome=<pass|fail> | Evidence=<path>`

## Resolution Criteria
- What must be true before moving to `resolved`.

## Closeout Artifact
- Close timestamp: <ISO8601>
- Final fix_spec path: `<path>`
- taskspending checkpoint: `<path>`
- Unsupported hypotheses preserved with evidence: yes|no
- Closeout decision: archived|deferred

## Follow-ups
- Remaining tasks/spec updates after resolution.
