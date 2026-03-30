# Offline Reference Pack Contract

This directory is the canonical local reference location for no-internet research mode.

When `RESEARCH_ALLOW_SEARCH=Off`, research stages should rely on:
1. Repo-local contracts/specs.
2. Files in `agents/reference/`.

## Minimum layout

- `agents/reference/README.md` (this contract)
- `agents/reference/index.md` (optional source index)
- `agents/reference/sources/` (optional extracted notes, docs, summaries)

## Source entry format (recommended)

For each reference note, include:
- `Title`
- `Origin` (where it came from)
- `Retrieved` (`YYYY-MM-DD`)
- `Relevance` (which stage/spec/task it informs)
- `Confidence` (high/medium/low)
- `Summary` (brief, factual)

## Usage rules

- Prefer local references before attempting network search.
- Keep references additive and versioned by filename/date.
- Do not store secrets, credentials, or regulated data.
- If a claim is uncertain, mark it in the consuming spec/incident/audit file.

