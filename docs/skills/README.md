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

- `millrace-ops-agent-manual.md`: when to use Millrace, when not to use it, and
  how a dedicated ops agent should deploy, configure, and operate it
- `millrace-loop-authoring.md`: how to reason about loops, stages, modes, and
  compiler-valid authoring when extending Millrace

If you are an external agent approaching this repo for the first time, start
with `millrace-ops-agent-manual.md`.
