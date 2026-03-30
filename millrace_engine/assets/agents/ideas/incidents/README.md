# Incident Intake

Use this directory for `### NEEDS_RESEARCH` incident artifacts only.
Each incident file must be additive and deduplicated by task fingerprint + failure signature.
Use `agents/specs/templates/incident_spec_template.md` as the default structure.

## Required Schema Fields

- `Incident-ID`
- `Fingerprint` (card/task fingerprint used for dedupe)
- `Failure signature`
- `Attempt Count` and attempt history entries
- `Diagnostics` pointers (diagnostics folder and run folder)
- `Hypothesis` plus `Alternative Hypotheses` with explicit statuses
- `Unsupported Hypotheses` with evidence pointers
- `Severity Class` (`S1|S2|S3|S4`) and explicit preemption behavior
- `minimal-unblock-first path`
- `rewrite task card path` for malformed/overscoped task inputs
- `spec addendum backflow` for spec-level root causes
- `regression test requirement` (+ evidence) for bug-class incidents
- `framework-level routing` for tool/script contract failures
- `fix_spec` (`Fix Spec ID` + `Fix Spec Path`)
- `Task Handoff` pointer to `agents/taskspending.md`
- `Closeout Artifact` block before archival

## Suggested Artifact Shape

```md
Copy the full scaffold from:
`agents/specs/templates/incident_spec_template.md`
```
