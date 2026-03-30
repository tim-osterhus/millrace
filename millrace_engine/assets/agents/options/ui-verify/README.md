# UI Verify (Option Pack)

This option pack adds a structured, repeatable Playwright UI verification pipeline to Millrace.

It is designed to be installed/wired by `agents/_customize.md`:
- `_customize.md` is the UX/menu layer.
- Full instructions and file packets live here.

Key idea:
- Deterministic outputs (`PASS|FAIL|BLOCKED`) come from Playwright automation.
- Historical bundles live under `agents/diagnostics/ui_verify/` and should be archived there.

Start here: `agents/options/ui-verify/ui_verify_option.md`.
