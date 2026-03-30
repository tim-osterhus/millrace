# Feature Specs

This folder is for detailed, single-feature spec sheets created during scoping.

Typical workflow:

- Run `agents/prompts/decompose.md` with a raw idea (via the Advisor).
- The Decomposer writes a spec here (e.g., `chat-anon-messaging.md`).
- It also injects ordered, executable task cards into `agents/tasksbacklog.md`.
- For research-loop generated specs, use `agents/specs/stable/` as the immutable
  long-lived spec path once tasks reference it.
- Golden specs should be written to `agents/specs/stable/golden/`.
- Phase specs should be written to `agents/specs/stable/phase/`.
- Governance runtime artifacts are stored in `agents/specs/governance/`.
- `agents/ideas/specs/` is a transient queue for decomposition and should be
  archived after processing.

Specs are not required for every task, but they help when you want to turn an ambiguous idea into a clean backlog.
