# One-Time Customization Prompt

You are onboarding this agentic framework to a new project repo. Your job is to generate a project spec sheet and fill in the project-specific files so the system becomes immediately usable.

## Key design: `agents/options/`

`agents/_customize.md` is the UX/menu layer.
Detailed option packets live under `agents/options/`.

Only open the option docs you need based on the user's choices.

## Output Targets

Create or update these files:
- `agents/spec.md` (new) - context-heavy spec sheet
- `README.md` - project overview, guardrails, workflow (Project Context + Non-negotiables)
- `agents/outline.md` - repo outline and stack
- `agents/tasks.md` - initial task entry or placeholder
- `agents/roadmap.md` - high-level roadmap template filled for this repo
- `agents/expectations.md` - success criteria and verification notes
- `agents/audit/completion_manifest.json` - project-local completion command contract
- `agents/options/model_config.md` - model + runner assignments for all cycles
- `agents/options/workflow_config.md` - workflow flags (flags only; keep this file tiny)

Optional (only if enabled by a chosen option):
- `agents/_orchestrate.md` - patched to install optional orchestrator behaviors
- `agents/objective/semantic_profile_seed.json` or `agents/objective/semantic_profile_seed.yaml` - semantic capability milestones when the project has a clear goal ladder
- Task-authoring guidance may be patched (e.g., `agents/prompts/decompose.md`, `agents/skills/task-card-authoring-repo-exact/SKILL.md`).

Keep all edits ASCII-only and minimal.

## Step 1: Repo Scan (fast)

Inspect the project repo to extract basics:
- Top-level folders and key entrypoints
- Primary languages/frameworks
- Build/test/lint commands (if present)
- Deployment or runtime constraints (from docs/configs)

If a README exists, skim it for setup and constraints.

If an `agents/outline.md` already exists, use it as the primary repo overview and only scan the repo directly if the outline is missing or outdated.

## Step 2: Ask for Missing Context

If any of the below are unknown, ask the user directly:
- One-sentence product description
- Deployment target (offline/online, regulated, SLAs)
- Data sensitivity and compliance constraints
- Review/approval gates (human review, QA, release)
- Critical non-negotiables (security, latency, cost)
- Canonical build/test/integration/regression commands if they cannot be discovered from the repo

Keep questions concise and in a single message.

## Step 2.5: Model + Runner Baseline (Required)

Default baseline is OpenAI models for all cycles (the "Default" active config in `agents/options/model_config.md`).

Ask the user:
- Keep the default model config as-is? (yes/no)

If yes:
- Leave `agents/options/model_config.md` Active config unchanged.

If no:
- Ask which preset they want: Hybrid / Hybrid Performance / All Codex / All Claude / Custom.
- If Custom: ask which active stage families they want to override, then collect runner/model ids for each overridden family.
  At minimum, cover Builder, QA, Hotfix, Doublecheck, Troubleshoot, Consult, Update, and any research stages they plan to run.
- Then edit **only** the `KEY=value` lines under "Active config" in `agents/options/model_config.md`.

For preset blocks and known-good model ids, see:
- `agents/options/model_config.md`

## Step 2.6: Integration Thoroughness (Required)

Integration is an **orchestrator option**.
- Manual mode is equivalent to opting out of orchestrated Integration.
- `agents/_integrate.md` still exists for manual use.

Ask which integration mode they want:
- Manual: no orchestrated Integration cycles
- Low: run Integration only when tasks are gated `INTEGRATION`
- Medium: run on `INTEGRATION` tasks and periodically every 3-6 tasks
- High: run Integration every other task

Then:
- Update flags in `agents/options/workflow_config.md` (flags only):
  - Set `## INITIALIZED=true`
  - Set `## INTEGRATION_MODE=<Manual|Low|Medium|High>`
  - Reset `## INTEGRATION_COUNT=0`
  - Set `## INTEGRATION_TARGET`:
    - Manual: 0
    - Low: 0
    - Medium: 4
    - High: 1
- If the mode is Low/Medium/High, open `agents/options/integrate/integrate_option.md` and follow its instructions.
- If the mode is Manual, do not add Integration instructions to `agents/_orchestrate.md`.

## Step 2.7: Headless Sub-Agent Permissions (Required)

This controls how permissive headless sub-agents are when run by the orchestrator templates.

Ask the user to choose one:
- Normal (recommended): default `--full-auto` for Codex; no extra Claude flags.
  - Example: docs-only repos, CI-only tests, no local servers or Docker.
- Elevated: add Codex `--sandbox danger-full-access`; Claude uses `--permission-mode acceptEdits`.
  - Example: local dev servers, IPC pipes, or Docker socket access required.
- Maximum: Codex `--dangerously-bypass-approvals-and-sandbox`; Claude `--dangerously-skip-permissions`.
  - Example: fully trusted environment where headless runs must never prompt.

Then:
- Set `## HEADLESS_PERMISSIONS=<Normal|Elevated|Maximum>` in `agents/options/workflow_config.md`.
- Open `agents/options/permission/perm_config.md` and follow its instructions.

## Step 2.8: Completion Contract Artifacts (Required)

The completion manifest is project-local and must not remain a generic placeholder.

Create or update:
- `agents/audit/completion_manifest.json`
- optionally `agents/objective/semantic_profile_seed.json` or `agents/objective/semantic_profile_seed.yaml`
- optionally `agents/objective/family_policy.json`

Rules:
- `completion_manifest.json` must list the real required completion commands for this project.
- Prefer commands already present in the repo or explicitly approved by the user.
- Keep the command set authoritative and minimal; do not include aspirational or redundant checks.
- If the repo has a clear capability ladder, add `semantic_profile_seed.json` or `semantic_profile_seed.yaml` with semantic milestones.
- `family_policy.json` is a hidden runtime governor artifact. If omitted, objective-profile sync may synthesize it adaptively from the goal and semantic seed.
- Only write `family_policy.json` directly when the project needs explicit caps, remediation-only capability tags, or other non-default governor behavior.
- If a semantic seed is not justified yet, note that explicitly in `agents/spec.md`.
- The baseline placeholder manifest is fail-closed scaffolding only. Do not leave it as `configured=true` with generic commands.

Manifest drafting flow:
- Start from the placeholder `agents/audit/completion_manifest.json`.
- First try to derive the real required completion commands from repo evidence.
- If the command set is still ambiguous, ask the user these questions:
  - Which commands are non-negotiable for final completion?
  - Which commands are only local convenience checks and should not gate completion?
  - Are there canonical wrapper scripts or environment variables that must appear in the exact command text?
  - What timeout is acceptable for each required command?
  - Do they want separate `harness`, `build`, `integration`, and `regression` proof commands, or do existing wrappers already cover those roles?
- If the user approves a dedicated drafting pass, run a bounded Codex command from repo root:
  - `codex exec -C <repo-root> "Open agents/_contractor.md and follow instructions."`
- Review the resulting manifest with the user or repo evidence before leaving `configured=true`.

## Step 2.9: QA Manual Verification Policy (Optional)

QA manual-policy option packs were pruned from the base framework. Keep policy simple:
- `ManualAllowed` remains the baseline.
- Use deterministic Playwright checks for UI verification when needed.

Then:
- No additional `workflow_config.md` key is required for this policy.

## Step 2.10: Troubleshoot + Consult Defaults

Troubleshoot and Consult are part of the baseline framework and are no longer optional install packs.

Do not ask whether to enable them.
Do not remove `agents/_troubleshoot.md` or downgrade to legacy no-troubleshooter variants during normal customization.

Only change Troubleshoot/Consult runner or model assignments if the user explicitly asks for different routing.

## Step 2.11: Complexity Routing Mode (Optional)

This controls whether the local foreground orchestrator loop
(`agents/orchestrate_loop.sh`) uses complexity-aware model routing.

Ask the user:
- Enable complexity-routing mode? (yes/no)

Then:
- Set `## COMPLEXITY_ROUTING=<On|Off>` in `agents/options/workflow_config.md`.

## Step 2.12: Update-on-Empty Mode (Optional)

This controls whether the local foreground orchestrator loop runs a final documentation
update cycle when it finds no remaining task cards in `agents/tasksbacklog.md`.

Ask the user:
- Enable update-on-empty mode? (yes/no)

Then:
- Set `## RUN_UPDATE_ON_EMPTY=<On|Off>` in `agents/options/workflow_config.md`.
- If enabling this mode, open `agents/options/update/update_option.md` and follow it.

## Step 2.13: Shell Templates (Required)

Ask the user:
- Which shell should Millrace use for all copy/paste command templates?
  - Bash/WSL (default)
  - PowerShell (Windows PowerShell 5.1 / PowerShell 7)

Then:
- Set `## SHELL_TEMPLATES=<Bash|PowerShell>` in `agents/options/workflow_config.md`.
- Wire docs/entrypoints to the selected templates by making minimal, mechanical reference edits:
  - If Bash:
    - Use `agents/options/orchestrate/orchestrate_options_bash.md`
  - If PowerShell:
    - Use `agents/options/orchestrate/orchestrate_options_powershell.md`

Wiring targets (update references to point at the selected shell variant):
- `agents/_orchestrate.md` (headless templates pointer)
- `README.md` (operator quick-start pointer)
- `OPERATOR_GUIDE.md` (operator shell/templates pointer)
- `ADVISOR.md` (agent-facing command-template pointer)

Do not list both shell variants in user-facing docs; reference only the selected one.

If the user later switches shells, they can:
- Update `## SHELL_TEMPLATES=...` and rerun this wiring step (or do the same mechanical reference swap by hand).

## Step 2.14: Orchestrator Templates (Optional)

Ask the user:
- Will you run orchestration headlessly? (yes/no)

If yes, templates live in the shell-specific file referenced by `agents/_orchestrate.md`.

No repo edits are required for this step.

## Step 2.15: UI Verification (Playwright) (Optional)

This enables deterministic UI verification artifacts using Playwright only.

Ask the user which UI verification preset they want:
- Off (default): no automated UI verification wiring.
- Deterministic Playwright: run automated UI checks and emit `PASS|FAIL|BLOCKED` artifacts.

Then update flags in `agents/options/workflow_config.md` (flags only):
- `## UI_VERIFY_MODE=<manual|deterministic>`
- `## UI_VERIFY_EXECUTOR=playwright`
- `## UI_VERIFY_ANALYZER=none`
- `## UI_VERIFY_COVERAGE=<smoke|standard|broad>`
- `## UI_VERIFY_QUOTA_GUARD=off`
- `## UI_VERIFY_BROWSER_PROFILE=playwright`

For details, open:
- `agents/options/ui-verify/ui_verify_option.md`

## Step 3: Write the Spec Sheet

Create `agents/spec.md` with this structure:

1) Project Summary (3-7 bullets)
2) Users and Use Cases
3) Runtime Constraints (offline/online, latency, cost)
4) Data and Compliance Requirements
5) Architecture Overview (services, databases, APIs)
6) Verification Commands (build/test/lint)
7) Operational Risks and Guardrails

Use concrete details from the repo or user answers. Mark unknowns explicitly as TODO.

## Step 4: Fill Project-Specific Files

Update the following using the spec sheet:
- `README.md`: update Project Context + Non-negotiables with the project summary, constraints, and guardrails.
- `agents/outline.md`: include repo structure, stack, and verification commands.
- `agents/tasks.md`: add a single starter task or placeholder that reflects current priorities.
- `agents/roadmap.md`: add 2-4 realistic themes and near-term goals.
- `agents/expectations.md`: list verification expectations, evidence types, and quality gates.
- `agents/audit/completion_manifest.json`: declare the required completion commands for this project.
- `agents/objective/semantic_profile_seed.json` or `agents/objective/semantic_profile_seed.yaml`: when appropriate, encode semantic capability milestones that should drive final goal-gap reasoning.

Use the user's answers to drive the edits:
- Product description, constraints, guardrails, review gates -> `README.md` Project Context + Non-negotiables.
- Repo scan results (stack, commands) -> `agents/outline.md` and `agents/expectations.md`.
- Completion commands and end-state proof expectations -> `agents/audit/completion_manifest.json`.
- Current priorities from the user -> `agents/tasks.md` and `agents/roadmap.md`.

Avoid changing files not listed above unless required by an enabled option.

## Step 5: Create Project-Specific Roles and Skills (Required)

1) If the repo needs new roles, use `agents/prompts/roleplay.md` to generate them.
2) If the repo needs new skills, use `agents/prompts/skill_issue.md` to generate them.
3) Update `agents/roles/` and `agents/skills/skills_index.md` accordingly.
4) If no new roles/skills are needed, write a short note in `agents/spec.md` explaining why.

## Step 6: Confirm Completion

Summarize what you updated and list the files touched. Ask if the user wants to revise any sections.
