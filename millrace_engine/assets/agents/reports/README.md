# Reports Directory Contract

Use this directory for new report-style artifacts created during Builder/QA cycles.

## Canonical Location
- Store new report/context artifacts under `agents/reports/`.
- Do not scatter report files directly under `agents/`.

## Deterministic Path Pattern
- Preferred path: `agents/reports/<stage_or_topic>/<YYYY-MM-DD_HHMMSS>_<slug>.md`
- Create the topic subdirectory when missing.

## Out of Scope (keep existing locations)
- `agents/tasks.md`, `agents/tasksbacklog.md`, `agents/tasksarchive.md`
- `agents/status.md`, `agents/quickfix.md`, `agents/expectations.md`, `agents/historylog.md`
- `agents/runs/` (run logs), `agents/diagnostics/` (diagnostic bundles)

## Logging Requirement
- When a run creates report artifacts, include their paths in `agents/historylog.md`.
