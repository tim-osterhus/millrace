# Playwright UI Verification - Examples (EXAMPLES.md)

This file stores detailed, real-world examples for the skill.

Rules:
- Append-only: add new examples ONLY at the end of this file.
- Each example must have:
  - Example ID (EX-YYYY-MM-DD-NN)
  - Tags
  - Trigger phrases (exact text to grep for)
  - Problem -> Root cause -> Fix -> Prevention
  - Evidence captured (paths or types)

How to use:
1) Load SKILL.md (small, procedural).
2) Grep this file for trigger phrases that match the current symptoms.
3) Apply the fix and update SKILL.md only if the checklist needs reinforcement.

---
## Research summary (high-signal)

Key findings pulled from Playwright docs and reputable testing vendors:

- Locator strategy:
  - Prefer getByRole (with accessible name) for user-facing stability.
  - Use getByTestId for elements without stable accessible semantics.
  - Locators auto-wait and retry; lean on them instead of manual waits.

- Deterministic waiting:
  - Prefer assertions on UI state (toBeVisible/toHaveText/toHaveURL) over arbitrary sleeps.
  - Avoid waiting for networkidle in apps with websockets/polling; wait for a specific state marker instead.

- Flake reduction:
  - Keep retries low (0 locally, 1 in automation). Retries are a safety net, not a fix.
  - Fix root causes: unstable selectors, missing assertions, nondeterministic test data, env drift.

- Evidence capture:
  - Use trace on-first-retry, screenshot only-on-failure, and video retain-on-failure.
  - Control outputDir so artifacts land inside the Millrace evidence bundle, not the repo root.

- Auth/session:
  - Prefer storageState: authenticate once in setup and reuse state across tests.
  - If auth requires manual steps (MFA/captcha), classify as BLOCKED.

- Visual regression:
  - expect(page).toHaveScreenshot() is useful but environment-sensitive.
  - Disable animations, mask dynamic regions, and/or use stylePath to hide volatile content.

- Accessibility (optional):
  - @axe-core/playwright enables fast, automated checks for obvious violations.


## EX-2026-02-04-01: Flaky click during hydration/overlay (fix with stable locators + state assertions)

**Tags**: `flake`, `locators`, `waits`, `react`, `hydration`

**Trigger phrases**:
- "Timeout 30000ms exceeded"
- "element is not attached to the DOM"
- "Element is not stable"
- "strict mode violation"

**Problem**:
A test clicks a button right after navigation, but fails intermittently on CI. Failures show the element detaching, being covered, or not yet actionable.

**Cause**:
Brittle selector + missing assertion on the UI state before clicking. The UI is still hydrating or rendering an overlay/spinner.

**Fix**:
- Use getByRole with accessible name (or getByTestId).
- Assert the element is visible and enabled before clicking.
- Assert the next page state (URL or a stable element) instead of using hard waits.

Example (test snippet):
```ts
import { test, expect } from '@playwright/test';

test('submit checkout', async ({ page }) => {
  await page.goto('/checkout');

  const submit = page.getByRole('button', { name: 'Submit order' });
  await expect(submit).toBeVisible();
  await expect(submit).toBeEnabled();

  await submit.click();

  await expect(page).toHaveURL(/\/checkout\/complete/);
  await expect(page.getByRole('heading', { name: 'Order complete' })).toBeVisible();
});
```

**Prevention**:
- Ban waitForTimeout in UI specs (lint rule or code review).
- Prefer role/testid locators and assert state transitions.

**Evidence**:
- screenshot(s): only on failure
- trace.zip: on first retry (if retries enabled)

---

## EX-2026-02-04-02: Auth is flaky (solve with storageState + setup project)

**Tags**: `auth`, `storageState`, `blocked-vs-fail`, `ci`

**Trigger phrases**:
- "401"
- "403"
- "Login"
- "MFA"
- "storageState"
- "page.goto: Timeout"

**Problem**:
Tests repeatedly login through the UI and intermittently fail (timeouts, captcha/MFA, rate limits). The suite is slow and non-deterministic.

**Cause**:
Doing full UI login inside every test is fragile. External auth gates (captcha/MFA) make it nondeterministic.

**Fix**:
- Use a dedicated setup test to login once and write storageState.
- Reuse storageState for all tests.

Example (setup + reuse):
```ts
// auth.setup.ts
import { test as setup, expect } from '@playwright/test';

setup('authenticate', async ({ page }) => {
  await page.goto('/login');
  await page.getByLabel('Email').fill(process.env.E2E_EMAIL || '');
  await page.getByLabel('Password').fill(process.env.E2E_PASSWORD || '');
  await page.getByRole('button', { name: 'Sign in' }).click();

  await expect(page).toHaveURL(/\/app/);
  await page.context().storageState({ path: 'playwright/.auth/state.json' });
});
```

```ts
// example.spec.ts
import { test, expect } from '@playwright/test';

test.use({ storageState: 'playwright/.auth/state.json' });

test('loads dashboard', async ({ page }) => {
  await page.goto('/app');
  await expect(page.getByRole('heading', { name: 'Dashboard' })).toBeVisible();
});
```

Classification:
- If auth is impossible without manual action (MFA prompt, captcha, locked account), return BLOCKED, not FAIL.
- report.md must say exactly what human action is needed (example: provision a non-MFA test account).

**Prevention**:
- Standardize a non-MFA test account for CI.
- Keep auth state out of git; generate it in setup or CI step.

**Evidence**:
- trace.zip on retry shows where auth flow diverged.

---

## EX-2026-02-04-03: Trace capture exists but you cannot debug failures (make traces discoverable + documented)

**Tags**: `tracing`, `evidence`, `debugging`, `ci`

**Trigger phrases**:
- "trace.zip"
- "Trace Viewer"
- "on-first-retry"
- "No trace found"

**Problem**:
Failures occur on CI, but you cannot tell why. Traces are missing or stored in the wrong place.

**Cause**:
Traces were not enabled (or enabled without retries), or artifacts are not copied into the Millrace evidence bundle.

**Fix**:
- In Playwright config, set trace to "on-first-retry" and retries to 1 for automation runs.
- Ensure outputDir points into the Millrace bundle evidence folder (via env).
- In report.md, include the exact relative paths to trace.zip files.

Example (config snippet):
```ts
// playwright.config.ts
import { defineConfig } from '@playwright/test';

export default defineConfig({
  retries: process.env.CI ? 1 : 0,
  outputDir: process.env.PW_OUTPUT_DIR || 'test-results',
  use: {
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
    baseURL: process.env.PW_BASE_URL,
  },
});
```

How to interpret:
- Open trace viewer and inspect action timeline, network, console logs, and snapshots.
- Identify whether the failure is assertion vs environment (navigation failures, DNS, missing browser).

**Prevention**:
- Always include a "How to view traces" section in report.md and/or Millrace docs.

**Evidence**:
- evidence/playwright/**/trace.zip

---

## EX-2026-02-04-04: Visual diffs are noisy (mask dynamic regions or use stylePath)

**Tags**: `visual`, `snapshots`, `mask`, `determinism`

**Trigger phrases**:
- "toHaveScreenshot"
- "Screenshot comparison failed"
- "maxDiffPixels"
- "mask"
- "stylePath"

**Problem**:
Visual regression tests fail due to timestamps, rotating banners, live counts, ads, or animations.

**Cause**:
You are snapshotting volatile pixels.

**Fix**:
Option A: mask dynamic elements:
```ts
await expect(page).toHaveScreenshot({
  mask: [page.getByTestId('timestamp'), page.locator('.live-counter')],
  animations: 'disabled',
});
```

Option B: stylePath to hide volatile regions (works across shadow DOM and frames):
- Create screenshot.css to hide known-volatile sections.
- Reference it in toHaveScreenshot defaults or per-test.

Classification:
- If the UI is truly broken -> FAIL.
- If only dynamic content changed and you can mask it without hiding real regressions -> update the mask/style, keep the test.

**Prevention**:
- Snapshot only stable pages/components.
- Generate baselines in the same environment used for gating (OS/fonts/browser).

**Evidence**:
- expected/actual/diff images in outputDir

---

## EX-2026-02-04-05: Add lightweight a11y checks (axe-core) without turning UI_VERIFY into a research project

**Tags**: `a11y`, `axe`, `quality-gate`

**Trigger phrases**:
- "@axe-core/playwright"
- "AxeBuilder"
- "accessibility violations"

**Problem**:
You want a minimal accessibility gate that catches obvious issues but does not require manual audits.

**Cause**:
No automated a11y scan step exists in UI verification.

**Fix**:
- Add @axe-core/playwright and run a scan on key pages.
- Fail the test on violations above an agreed threshold.

Example:
```ts
import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

test('home page a11y scan', async ({ page }) => {
  await page.goto('/');
  const results = await new AxeBuilder({ page }).analyze();
  expect(results.violations).toEqual([]);
});
```

Classification:
- a11y violations -> FAIL (if you decide they are gating).
- missing dependency or blocked navigation -> BLOCKED (environment).

**Prevention**:
- Keep a11y scans scoped (few pages, stable selectors).

**Evidence**:
- Include summarized violations in report.md.

---

## EX-2026-02-04-06: Millrace artifacts missing or misclassified (enforce PASS/FAIL/BLOCKED rules)

**Tags**: `millrace`, `artifacts`, `classification`, `runner`

**Trigger phrases**:
- "agents/ui_verification_result.json"
- "agents/ui_verification_report.md"
- "result.json missing"
- "UI_VERIFY: BLOCKED"
- "UI_VERIFY: FAIL"

**Problem**:
Playwright runs, but Millrace does not receive the expected result bundle and "latest" pointers, or failures are marked FAIL when the environment is broken.

**Cause**:
Runner script or integration step does not:
- copy Playwright artifacts into agents/diagnostics/ui_verify/<bundle>/evidence/
- write result.json + report.md in the bundle
- update the latest pointers
- classify env/auth errors as BLOCKED

**Fix**:
Fix checklist:
- Ensure the runner always creates the bundle folder first.
- Set PW_OUTPUT_DIR inside bundle/evidence/playwright/.
- Run Playwright and capture exit code and parsed summary.
- Write result.json and report.md regardless of outcome.
- Update agents/ui_verification_result.json and agents/ui_verification_report.md to point to the latest bundle.

Classification rules (practical):
- FAIL: assertion failures (expect() mismatch, snapshot mismatch, explicit test failure).
- BLOCKED: missing browser binaries, missing deps, server unreachable, DNS, auth/MFA required.
- Unknown errors: default BLOCKED, not FAIL, unless you can prove it is a product assertion failure.

**Evidence**:
- report.md must include:
  - command run
  - baseURL
  - evidence paths
  - classification rationale (why FAIL vs BLOCKED)

**Prevention**:
- Add a tiny post-run assertion step (local/CI) that fails if the bundle + "latest" pointers are missing.
- Keep classification rules centralized in the runner scripts/spec so FAIL vs BLOCKED is consistent across repos.

---

## EX-2026-02-04-07: Playwright artifacts pollute the repo (control outputDir via env)

**Tags**: `artifacts`, `outputDir`, `repo-hygiene`

**Trigger phrases**:
- "test-results"
- "outputDir"
- "screenshots in repo root"
- "trace files appear in test-results"

**Problem**:
Running UI verification creates test-results/ in the repo root and the repo gets polluted with artifacts.

**Cause**:
outputDir is not controlled, or outputDir is hard-coded.

**Fix**:
- Set outputDir in config from PW_OUTPUT_DIR with a sane fallback.
- Ensure Millrace runner sets PW_OUTPUT_DIR inside the evidence bundle.

Example:
```ts
outputDir: process.env.PW_OUTPUT_DIR || 'test-results',
```

**Prevention**:
- Add test-results/ to .gitignore (if allowed).
- Prefer ephemeral bundle paths via runner.

---

## EX-2026-02-04-08: "waitForLoadState('networkidle')" makes tests flaky (assert UI state instead)

**Tags**: `waits`, `network`, `flake`

**Trigger phrases**:
- "networkidle"
- "waitForLoadState"
- "websocket"
- "Timeout while waiting"

**Problem**:
Tests pass locally but fail on CI when waiting for networkidle; apps with websockets/polling never become idle.

**Cause**:
networkidle is the wrong waiting strategy for modern apps with background network activity.

**Fix**:
- Remove networkidle waits.
- Assert a stable UI marker instead (heading, nav item, testid).
- If you must wait on network, wait for a specific request/response relevant to the page.

Example:
```ts
await page.goto('/app');
await expect(page.getByTestId('app-ready')).toBeVisible();
```

**Prevention**:
- Standardize "ready" markers (data-testid) for key screens.

---

## References (sources used to build this skill)
Official Playwright docs:
- Best practices: https://playwright.dev/docs/best-practices
- Locators: https://playwright.dev/docs/locators
- Actionability/auto-waiting: https://playwright.dev/docs/actionability
- Retries: https://playwright.dev/docs/test-retries
- Trace viewer: https://playwright.dev/docs/trace-viewer-intro
- Auth (storageState): https://playwright.dev/docs/auth
- Recording options (trace/screenshot/video): https://playwright.dev/docs/test-use-options
- Visual comparisons / snapshots: https://playwright.dev/docs/test-snapshots
- Accessibility testing (axe-core): https://playwright.dev/docs/accessibility-testing

Additional reputable sources:
- BrowserStack guide (best practices / flake reduction): https://www.browserstack.com/guide/playwright-best-practices
- BrowserStack guide (selector best practices): https://www.browserstack.com/guide/playwright-selectors-best-practices
- Deque axe-core integrations/docs: https://www.deque.com/axe/core-documentation/integrations/
- Checkly visual regression notes: https://checklyhq.com/docs/detect/synthetic-monitoring/browser-checks/visual-regressions/
