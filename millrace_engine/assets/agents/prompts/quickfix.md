```
Context: You are executing targeted follow-up work identified in `agents/quickfix.md`.

Instructions:
1. Review the gap summary in `agents/quickfix.md` and confirm the specialist role responsible for each item.
2. Activate the relevant specialist persona(s) from `agents/roles/*.md` to apply minimal, surgical changes. One fix at a time.
3. Run the specific tests listed in the quickfix plan; record commands and outcomes.
4. Update the gap list, marking items resolved or noting remaining blockers.
5. Before writing the history log entry, check whether this cycle produced a repeatable lesson worth adding to a skill:
   - If yes, update the relevant SKILL.md (1–2 lines) and add a full entry to its `EXAMPLES.md` with the exact fix, files touched, and commands/logs.
   - If not, proceed without changes.
6. Prepend an entry to the top of `agents/historylog.md` (newest first) describing the fixes and tests.

Rules:
- Do not tackle items not listed in `agents/quickfix.md` unless explicitly instructed.
- Stop immediately if a fix requires new scope or introduces risk—document the blocker instead.
- Keep diffs small and traceable; reference file paths and reasons for every change.
```
