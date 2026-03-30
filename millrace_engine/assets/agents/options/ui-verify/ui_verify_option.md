# UI Verification Option (Playwright)

This option is intended to be used by `agents/_customize.md`.

Goal: enable deterministic, automated UI verification using Playwright with stable
artifacts and explicit `PASS|FAIL|BLOCKED` outcomes.

## What this option changes

When enabled, Millrace uses:
- Workflow flags in `agents/options/workflow_config.md` for UI verification mode.
- A canonical artifact contract under `agents/diagnostics/ui_verify/`.
- A reusable YAML spec at `agents/ui_verification_spec.yaml`.
- Deterministic runner scripts:
  - `agents/options/ui-verify/run_playwright_ui_verify.sh`
  - `agents/options/ui-verify/run_playwright_ui_verify.ps1`

## Modes

Set in `agents/options/workflow_config.md`:

- `UI_VERIFY_MODE=manual`
  - No automated UI verification run is required.

- `UI_VERIFY_MODE=deterministic`
  - Run Playwright automation and emit UI verification artifacts.

## Required config flags

All flags live in `agents/options/workflow_config.md` as `## KEY=value` lines.

- `UI_VERIFY_MODE=manual|deterministic`
- `UI_VERIFY_EXECUTOR=playwright`
- `UI_VERIFY_ANALYZER=none`
- `UI_VERIFY_COVERAGE=smoke|standard|broad`
- `UI_VERIFY_QUOTA_GUARD=off`
- `UI_VERIFY_BROWSER_PROFILE=playwright`

## Artifact contract

Every UI verification attempt must create:

- `agents/diagnostics/ui_verify/<YYYYMMDD-HHMMSS>-<task_slug>/`
  - `result.json` (machine output; authoritative status)
  - `report.md` (human-readable summary with evidence links)
  - `evidence/` (screenshots/logs/traces)
  - `meta/` (resolved spec + environment metadata)

And update latest pointers:
- `agents/ui_verification_result.json`
- `agents/ui_verification_report.md`

`result.json` minimum schema:
- `status`: `PASS|FAIL|BLOCKED`
- `executor`: `playwright`
- `analyzer`: `none`
- `coverage`: `smoke|standard|broad`
- `started_at`, `ended_at` (ISO8601)
- `evidence_dir`
- `checks[]`
- `errors[]`

## Artifact retention and archival

- Keep latest pointers only as current-state markers.
- Keep historical bundles under `agents/diagnostics/ui_verify/`.
- Move consumed bundles to `agents/diagnostics/ui_verify/archived/` when no longer
  needed for active debugging or audit trails.
- Do not leave ad hoc UI artifacts in other `agents/` paths.

## Spec format (YAML)

Create (or maintain) a runnable spec at:
- `agents/ui_verification_spec.yaml`

The spec should support multiple suites so `UI_VERIFY_COVERAGE=broad` is feasible.

## Playwright invocation (copy/paste)

Bash:
```bash
agents/options/ui-verify/run_playwright_ui_verify.sh \
  --out "agents/diagnostics/ui_verify/<bundle_id>" \
  --coverage "<smoke|standard|broad>" \
  --update-latest \
  --cmd "npx playwright test"
```

PowerShell:
```powershell
powershell -File agents/options/ui-verify/run_playwright_ui_verify.ps1 `
  -OutDir "agents/diagnostics/ui_verify/<bundle_id>" `
  -Coverage "<smoke|standard|broad>" `
  -UpdateLatest `
  -Cmd "npx playwright test"
```

## Failure handling

- `PASS`: tests ran and passed.
- `FAIL`: tests ran and assertions failed.
- `BLOCKED`: tests could not run deterministically due to tooling/environment setup.

## Files this option may touch

- `agents/options/workflow_config.md` (flags only)
- `agents/_customize.md` (toggle + flag writes)
- `agents/ui_verification_spec.yaml` (template/spec)
- `agents/options/ui-verify/run_playwright_ui_verify.sh`
- `agents/options/ui-verify/run_playwright_ui_verify.ps1`

## Verification checklist

- `agents/options/workflow_config.md` contains UI_VERIFY flags as `## KEY=value`.
- Playwright runner scripts emit bundle artifacts in `agents/diagnostics/ui_verify/`.
- Latest pointers are updated deterministically.
- UI verification artifacts are archived under `agents/diagnostics/ui_verify/` when complete.
