---
name: playwright-ui-verification
description: >
  Implements and maintains deterministic UI verification using Playwright for Millrace's UI_VERIFY gating, emphasizing flake-resistant locator/wait patterns, evidence capture, and PASS/FAIL/BLOCKED artifact emission.
tags: ["ui-verify", "playwright", "qa", "e2e", "millrace"]
allowed-tools: Read, Grep, Glob, Bash, Write
compatibility:
  runners: ["codex-cli", "claude-code"]
  tools: ["Read", "Grep", "Bash", "Write"]
  offline_ok: true
---

# Playwright UI Verification (Deterministic, Millrace-aligned)

## Purpose
Replace manual UI verification with deterministic Playwright automation that emits Millrace UI_VERIFY artifacts (PASS/FAIL/BLOCKED) plus auditable evidence.

## Quick start
Goal:
- Produce a deterministic UI_VERIFY result (PASS/FAIL/BLOCKED) with a complete evidence bundle, using Playwright.

Use when (triggers):
- Millrace UI_VERIFY option pack is enabled and you need Playwright-based gating.
- A repo needs a new Playwright UI suite, or an existing suite is flaky/unreliable.
- Manual UI verification is the bottleneck and you need repeatable PASS/FAIL evidence.
- You need standardized UI verification artifacts under agents/diagnostics/ui_verify/<bundle>/.

Do NOT use when (non-goals):
- You need exploratory/manual UX review (not pass/fail automation).
- The environment cannot run browsers and there is no supported remote runner available.
- The task requires bypassing auth/security, adding unsafe broad permissions, or inventing new contracts.

## Operating constraints (must follow)
- Determinism over coverage: prefer a small, stable suite that always yields an outcome.
- No arbitrary sleeps: do not use waitForTimeout/sleep except as a last resort with justification.
- Use stable locators: prefer getByRole (with accessible name), then getByTestId, then semantic locators.
- Flake policy: allow at most 1 retry; if it still fails, fix the cause or classify correctly.
- Evidence first: on any failure, capture screenshot + trace (+ video if enabled) and link them in report.md.
- Minimal diffs: do not restructure Millrace; keep changes surgical and reversible.

## Inputs this Skill expects
Required (Millrace side):
- agents/options/ui-verify/ui_verify_option.md
- agents/options/ui-verify/run_playwright_ui_verify.sh
- agents/options/ui-verify/run_playwright_ui_verify.ps1
- agents/ui_verification_spec.yaml

Required (repo under test):
- repo root path (cwd for running Playwright)
- a deterministic way to start/target the app under test (dev server or deployed base URL)
- a deterministic test account or auth bootstrap method (prefer storageState)

If required inputs are missing:
- Default to BLOCKED and provide (1) the minimum missing inputs and (2) a minimal unblocking plan.

## Output contract (Millrace UI_VERIFY)
Your work must result in:

1) A bundle folder:
- agents/diagnostics/ui_verify/<bundle_id>/
  - result.json
  - report.md
  - evidence/ (screenshots, videos, traces, logs)
  - meta/ (command, env, repo revision, timestamps)

2) These "latest" pointers updated:
- agents/ui_verification_result.json
- agents/ui_verification_report.md

Status rules:
- PASS: tests ran and all required checks passed.
- FAIL: tests ran and assertions failed (true product failure) after <= 1 retry.
- BLOCKED: tests could not run deterministically due to environment/auth/dependency issues.

Minimum fields for result.json:
- status: PASS|FAIL|BLOCKED
- executor: "playwright"
- analyzer: none
- coverage: smoke|standard|broad
- started_at / ended_at: ISO 8601 timestamps
- evidence_dir: path
- checks: list (may be empty, but prefer per-check outcomes when possible)
- errors: list (typed)
- quota: object|null

Optional (recommended) fields:
- command: the executed command string
- exit_code: integer
- summary: { total, passed, failed, skipped, retries }
- artifacts: { report_md, evidence_dir, trace_zips, screenshots, videos }

Definition of DONE (objective checks):
- [ ] The UI_VERIFY bundle exists and matches the contract (result.json + report.md + evidence + meta)
- [ ] PASS/FAIL/BLOCKED is classified deterministically (no "manual verification required" without a BLOCKED rationale)
- [ ] On FAIL/BLOCKED, report.md links to the exact evidence paths (screenshots/traces/videos/logs)
- [ ] The "latest" pointers are updated as required by the option pack (ui_verification_result.json + ui_verification_report.md)

## Procedure
1) Confirm the Millrace contract and runner integration points
- Read ui_verify_option.md and the run_playwright_ui_verify.* scripts.
- Identify how the runner expects to be configured (cwd, command, env, base URL, output paths).
- Do not change contracts. Only extend if the runner already supports it.

2) Detect Playwright in the repo under test
- Check package.json for @playwright/test.
- Check for playwright.config.(ts|js|mjs|cjs) and a test directory.
- If missing, choose the smallest viable setup: 1-3 smoke tests covering the critical flow.

3) Minimal install/add steps (if Playwright missing)
Bash:
```bash
npm i -D @playwright/test
npx playwright install
```

PowerShell:
```powershell
npm i -D @playwright/test
npx playwright install
```

Notes:
- Prefer the repo's existing package manager (npm/pnpm/yarn).
- If your CI runner needs system deps, follow the Millrace runner's documented install approach.

4) Configure Playwright for flake resistance + evidence
In playwright.config.(ts|js):
- retries: 1 for automation/CI runs (0 locally is optional)
- use.baseURL: configurable via env (example: PW_BASE_URL)
- use.trace: "on-first-retry"
- use.screenshot: "only-on-failure"
- use.video: "retain-on-failure" (or "on-first-retry" if storage is tight)
- outputDir: configurable via env (example: PW_OUTPUT_DIR) so Millrace can place artifacts into the bundle
- expect.toHaveScreenshot defaults: animations disabled; consider stylePath for volatile UIs

5) Author tests using stable locators and deterministic waits
Preferred patterns:
- page.getByRole(role, { name: "..." }) (most stable)
- page.getByTestId("...")
- filtered locators (hasText, etc.)

Avoid:
- brittle CSS chains, nth-child, XPath
- force clicks unless you prove it's correct
- waitForTimeout/sleep

Assertions should verify user-visible state:
- await expect(locator).toBeVisible()
- await expect(page).toHaveURL(...)
- await expect(locator).toHaveText(...)

6) Auth/session management
Prefer storageState:
- a one-time "setup" test logs in and writes storageState
- normal tests reuse it with test.use({ storageState: ... })

If auth fails due to missing secrets, MFA, or an account lock:
- classify as BLOCKED (not FAIL)

7) Visual regression (optional, only when it adds value)
- Use expect(page).toHaveScreenshot() for stable screens.
- Mask dynamic regions with the mask option, or use stylePath to hide volatile elements.
- Treat baseline updates as code review items (explicit update process).

8) Hook into Millrace UI_VERIFY runner scripts
- Put the "one true command" where the runner expects it (typically ui_verification_spec.yaml).
- Ensure the runner sets:
  - PW_BASE_URL
  - PW_OUTPUT_DIR (points into agents/diagnostics/ui_verify/<bundle_id>/evidence/playwright/)
  - any required auth/env (without embedding secrets in the repo)
- Ensure the runner always writes Millrace result.json + report.md and updates the "latest" pointers.

9) Classify outcomes deterministically
If Playwright exits non-zero:
- If the failure is a test assertion -> FAIL.
- If the error is missing browser/deps, server not reachable, or auth not possible -> BLOCKED.
- Always link evidence paths in report.md.

## Pitfalls / gotchas
- Increasing timeouts/retries is usually hiding a real locator/synchronization problem.
- Visual snapshots vary across OS/browser/fonts; generate baselines in the same environment used by gating.
- storageState does not solve server-side session invalidation or shared mutable state between tests.

See EXAMPLES.md for concrete patterns and failure modes (search by "Trigger phrases" or Example ID).
