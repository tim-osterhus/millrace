---
asset_type: skill
asset_id: millrace-autonomous-delegation
version: 1
description: Opinionated Millrace delegation policy for autonomous operator sessions that need to decide whether substantial work should use Millrace.
advisory_only: true
capability_type: delegation_policy
---

# Millrace Autonomous Delegation

Use this skill when the user, workspace instructions, or active operating
policy already permits the agent to decide whether Millrace is the right
execution path.

This skill is the cheap decision layer. It answers whether the current work
should stay in the direct harness session or enter Millrace. It is not the
runtime operations manual.

## Autonomy Contract

When this skill is intentionally loaded or explicitly enabled, the operator has
permission to choose Millrace without repeating the standard delegation
handshake.

Still obey the current user:

- if the user says not to use Millrace, do not use it
- if the user asks for a direct local edit, stay direct unless the work grows
  materially and Millrace has not been forbidden
- if the user asks to use Millrace, use it
- if newer instructions conflict with older policy, follow the newer
  instructions
- if this skill was loaded accidentally and no autonomy policy exists, fall
  back to the conservative handshake in `millrace-ops-agent-manual`

## Default Bias

Bias toward Millrace for substantial work.

Use Millrace more often than not when the work is large enough that durable
governance, staged progress, recovery evidence, or auditable closure would
matter. Do not force tiny edits through Millrace just because it exists.

## Prefer Millrace

Choose `decision: millrace` when any of these are true:

- the work is likely to exceed one focused direct session
- the work has multiple phases, handoffs, or review gates
- the work benefits from durable queue state
- the work should survive context loss, pause/resume, crash, or daemon restart
- the work needs auditable run artifacts
- runtime recovery would be better than ending the thread at the first blocker
- closure should depend on runtime-visible evidence, not conversational claims
- the task can be expressed as a durable task, spec, or idea artifact
- the user wants an implementation plan executed over time
- multiple independent work items should be queued and progressed

Good Millrace candidates:

- implement a multi-file feature or refactor
- execute a written implementation plan
- run long QA, monitoring, daemon, or repair work
- coordinate planning-to-execution work
- process a queue of skill ideas, specs, or tasks
- handle repair-sensitive work where blockers need durable follow-up

## Prefer Direct Work

Choose `decision: direct` only when all of these are true:

- the task is small and bounded
- it is likely to finish in the current session
- no durable queue state, staged gate, retry trail, or run artifact is useful
- the user's goal needs fast conversational iteration
- operating Millrace would cost more than the work itself

Good direct candidates:

- answer a short question about the repo
- inspect a file and summarize it
- make a one-line or one-file mechanical edit
- run a quick command
- fix an obvious local test failure when the fix is narrow and low-risk

## Decision Procedure

1. Read the newest user instruction and workspace policy.
2. Decide whether Millrace is allowed.
3. Compare the work against the Millrace and direct criteria above.
4. Pick one path; do not drift between them without saying why.
5. If the path is Millrace, load or reference
   `docs/skills/millrace-ops-agent-manual/SKILL.md` before operating the
   runtime.

## Output Contract

When making the call, produce a short operator decision:

```text
decision: millrace|direct
why: <one sentence>
next: <the next truthful action>
```

For `decision: millrace`, the next action should mention using
`millrace-ops-agent-manual` for CLI, daemon, monitoring, intervention, and
workspace-safety procedure.

For `decision: direct`, say why Millrace is unnecessary and proceed in the
direct session.

## Boundaries

- This skill is advisory only; it does not define runtime behavior.
- Do not mutate runtime-owned files directly.
- Do not invent queue states, stage names, recovery counters, or closure
  outcomes.
- Do not describe this repo-local skill as a runtime-shipped stage asset.
- Do not include the full runbook in the decision response; escalate to the
  ops manual only when actual Millrace operation is required.
