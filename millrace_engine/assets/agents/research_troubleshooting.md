# Research Troubleshooting Guide

Use this guide when the research loop or stage contracts are not behaving as expected.

## 1) Fast triage

1. Validate script syntax:
   - `bash -n agents/research_loop.sh`
2. Check current research marker:
   - `cat agents/research_status.md`
3. Check latest events:
   - `tail -n 50 agents/research_events.md`
4. Check runtime checkpoint presence:
   - `test -f agents/.research_runtime/active_stage.env && cat agents/.research_runtime/active_stage.env`

## 2) Queue and scaffold integrity

- Confirm queue directories exist:
  - `find agents/ideas -maxdepth 3 -type d | sort`
- Confirm required templates exist:
  - `test -f agents/specs/templates/golden_spec_template.md`
  - `test -f agents/specs/templates/phase_spec_template.md`
  - `test -f agents/specs/templates/incident_spec_template.md`
  - `test -f agents/specs/templates/audit_template.md`
- Confirm supporting contracts:
  - `test -f agents/gaps.md`
  - `test -d agents/reference`

## 3) Common symptoms

### Symptom: Loop exits immediately with argument/config error
- Check `agents/options/workflow_config.md` and CLI flags.
- Re-run with explicit mode:
  - `bash agents/research_loop.sh --once --mode AUTO`

### Symptom: Stage repeatedly blocks
- Inspect latest stage logs under `agents/runs/research/`.
- Verify stage instructions still point to required templates.
- If failures persist, add/update incident artifact using:
  - `agents/specs/templates/incident_spec_template.md`

### Symptom: Audit queue is not progressing
- Verify `AUDIT_TRIGGER` behavior in workflow config.
- Ensure audit ticket format follows:
  - `agents/specs/templates/audit_template.md`
- Confirm `agents/taskspending.md` and `agents/tasksbacklog.md` are readable and non-corrupt.

### Symptom: Spec outputs are inconsistent across runs
- Ensure Clarify stage uses:
  - `agents/specs/templates/golden_spec_template.md`
- Track unresolved spec-quality issues in:
  - `agents/gaps.md`

### Symptom: Interrogation stopped before configured max rounds
- Check for explicit marker:
  - `rg -n "INTERROGATION_EARLY_STOP|no-material-delta|prefer early" agents/research_events.md -S`
- Conservative behavior is expected: the loop may prefer early-stop when consecutive rounds show no material delta in interrogation artifacts.
- Validate the latest round artifacts for the target source:
  - `ls -1 agents/specs/questions | tail -n 5`
  - `ls -1 agents/specs/decisions | tail -n 5`

## 4) Recovery actions

- To test non-destructive resume behavior:
  - Seed a checkpoint file in `agents/.research_runtime/active_stage.env`.
  - Run one cycle and verify stale partial outputs are removed.
- To pause safely:
  - `touch agents/STOP_AUTONOMY`

## 5) Escalate

When local retries are exhausted:
- Preserve evidence under `agents/runs/research/` and `agents/research_events.md`.
- Update incident queue files in `agents/ideas/incidents/`.
- Keep execution and research status signaling separated.
