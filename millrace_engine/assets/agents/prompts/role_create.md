# Role Creation Prompt

You are creating a new project-specific role for this repo. Your job is to add a role file that matches the existing role conventions.

## Inputs You Need

Confirm the following before writing:
- Role name and filename (kebab-case)
- Purpose and scope of the role
- Typical tasks this role should handle
- Expected deliverables

If anything is missing, ask the user concise questions in a single message.

## Required File

Create a new role file under `agents/roles/<role-name>.md`.
Use existing role files in `agents/roles/` as the format reference.

## Workflow

1) Read 1-2 existing role files to match tone and structure.
2) Draft the new role with clear focus areas and deliverables.
3) Keep it short and actionable.

## Guardrails

- Keep changes minimal and consistent with existing roles.
- Do not modify other roles unless explicitly asked.

## Completion

Summarize what you created and list the files touched. If any inputs were assumed, list them explicitly.
