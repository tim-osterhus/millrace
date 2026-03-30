# Prompt Artifact Staging (`agents/prompts/tasks/`)

Generated task-specific prompts live here until they are executed.

## Numbering convention
- Use zero-padded numbering with short slug: `001-context-badge.md`, `002-dictation-setup.md`, etc.
- Increment sequentially regardless of branch or model.
- Each file should follow the XML-tag structure defined in `agents/prompts/create_prompt.md`.

## Lifecycle
1. `create_prompt` meta-agent writes the prompt into this folder.
2. Builder/QA agents load the prompt verbatim (per `agents/prompts/run_prompt.md`) and execute it.
3. After execution, move the file into `agents/prompts/completed/` and note the filename at the top of `agents/historylog.md` (newest first).

Keep only active prompts in this directory. If a prompt becomes obsolete, archive it to `completed/` with a note explaining why it was skipped.
