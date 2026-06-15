# Millrace Agent Skills Docs

This directory holds public, repo-level guidance for external agents that need to
understand how to use or extend Millrace well.

These documents are not the same thing as the runtime's shipped internal skill
assets under `src/millrace_ai/assets/skills/`.

The distinction matters:

- `docs/skills/` is for external agents reading the repository and learning how
  to operate Millrace or author Millrace-compatible loop and stage changes.
- `src/millrace_ai/assets/skills/` is for runtime-shipped advisory assets that
  get copied into `millrace-agents/skills/` for stage execution.

Public docs in this directory may explain runtime behavior, operator posture,
and authoring rules. They do not define runtime-owned routing, queue mutation,
or stage-transition semantics. Those remain owned by the runtime code and its
typed contracts.

Current public agent docs:

- `millrace-autonomous-delegation/SKILL.md`: how an authorized external agent
  should decide whether substantial work should enter Millrace or stay direct
- `millrace-ops-agent-manual/SKILL.md`: how a dedicated ops agent should
  deploy, configure, monitor, and operate Millrace after Millrace is requested
  or selected. The entry file is intentionally compact and links to
  `millrace-ops-agent-manual/references/` for detailed command, monitoring,
  recovery, mode/configuration, and verification guidance.
- `millrace-loop-authoring/SKILL.md`: how to reason about loops, stages, modes, and
  compiler-valid authoring when extending Millrace

If you are an external agent approaching this repo for the first time, start
with `millrace-autonomous-delegation/SKILL.md` when you are authorized to
choose the execution path, then use `millrace-ops-agent-manual/SKILL.md` before
operating Millrace.
