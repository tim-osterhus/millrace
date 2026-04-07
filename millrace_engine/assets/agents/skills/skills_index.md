# Skills Index

Use this registry to discover and apply relevant skills. Pick up to 3 per task.

Workflow note for Millrace control-plane tasks: when updating autonomy/escalation/audit/reporting contracts, prefer `Codebase Audit + Documentation` + `Small-Diff Discipline` + `Historylog Entry (High Signal)`.

| Skill | When to use (triggers) | Inputs | Outputs |
| --- | --- | --- | --- |
| [Task Card Authoring (Repo-Exact)](./task-card-authoring-repo-exact/SKILL.md) | Converting vague requests into a single task card | `agents/tasks.md`, `agents/tasksbacklog.md` | Updated `agents/tasks.md` with DONE checks |
| [Spec Writing Research Core](./spec-writing-research-core/SKILL.md) | Writing/reviewing GoalSpecs and incident fix_specs in research stages (Clarify/Critic/Designer/Taskmaster/Taskaudit/Incident Resolve) with strict REQ/AC traceability and ambiguity controls | Stage entrypoints under `agents/_*.md`, specs in `agents/specs/` and `agents/ideas/specs/` | Spec artifacts and decomposition outputs that are verifiable, traceable, and deterministic |
| [Acceptance Profile Contract](./acceptance-profile-contract/SKILL.md) | Normalizing prompt-level acceptance blocks into reusable milestone/gate profiles and preserving traceability through Clarify/Taskmaster outputs | Seed prompt/goal artifact, stage entrypoint contract, optional existing acceptance profile artifacts | `agents/reports/acceptance_profiles/<profile_id>.json` + mapped milestone/gate traceability in spec/task outputs |
| [Advisor Plan Pushback](./advisor-plan-pushback/SKILL.md) | Plan critique, pushback, identifying brittle assumptions and lowest-risk sequence | Plan/spec (`agents/tasks.md` or message), `README.md`, `agents/outline.md` | 1-3 hard objections + risk scan + safer sequence |
| [Advisor Architecture Sanity Check](./advisor-architecture-sanity-check/SKILL.md) | Architecture/design sanity-check (reliability/operability/security) | System description, `README.md`, `agents/outline.md` | Risks + missing decisions + simplest viable architecture |
| [Small-Diff Discipline](./small-diff-discipline/SKILL.md) | Any code change; especially infra/librechat/rag_api | Scope, target files | Minimal diff plan + change set |
| [Historylog Entry (High Signal)](./historylog-entry-high-signal/SKILL.md) | After any builder/QA run | Change summary, commands run | Prepend to `agents/historylog.md` |
| [Millrace Operator Intake Control](./millrace-operator-intake-control/SKILL.md) | Choosing Advisor vs Supervisor commands, seeding ideas/tasks, queue reordering, or correcting over-specified Millrace intake bodies | Role context, draft command or intake body, entrypoint doc | Supported command choice plus a cleaned what-not-how intake seed |
| [Compose Stack Change Protocol](./compose-stack-change-protocol/SKILL.md) | Docker compose or infra changes | `infra/compose/`, compose commands | Updated compose + validation |
| [Codebase Safe Cleanup (Strict)](./codebase-safe-cleanup/SKILL.md) | Behavior-preserving cleanup/refactor with verification gates | Build/test commands, target areas | Cleanup plan + verified change batches |
| [Codebase Audit + Documentation](./codebase-audit-doc/SKILL.md) | Security/correctness/maintainability audit and doc-only plan, including control-plane runbook alignment (autonomy/escalation/incidents/audit/reports) | Repo context, run commands | Audit report + doc-only patch plan |
| [Playwright UI Verification](./playwright-ui-verification/SKILL.md) | Deterministic UI verification/gating via Playwright; replace manual UI checks with PASS/FAIL/BLOCKED artifacts | `agents/options/ui-verify/`, `agents/ui_verification_spec.yaml`, repo run URL/auth | UI_VERIFY artifact bundle + evidence |
| [Frontend Review](./frontend-review/SKILL.md) | Phase 1 design handoff review + Phase 2 design-system code compliance review | Design handoff (or diff), token/component source of truth | ui_review artifacts + compliance report |
