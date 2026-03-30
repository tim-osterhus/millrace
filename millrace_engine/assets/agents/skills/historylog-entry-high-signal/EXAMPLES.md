# Examples - Historylog Entry (High Signal)

This file stores detailed, real-world examples for the historylog-entry-high-signal skill.

Append new examples to the END of this file and never change existing Example IDs.

---

## EX-2025-12-28-01: UI-to-API integration fix

**Tags**: `historylog`, `integration`, `endpoints`

**Trigger phrases**:
- "UI not calling backend"
- "frontend not hitting API"
- "endpoint config fix"

**Date**: 2025-12-28

**Problem**:
Historylog entry was too vague: "Fixed UI integration." No details about what was wrong or how to verify.

**Impact**:
Future developer couldn't reproduce verification or understand what changed. QA couldn't validate.

**Cause**:
Agent didn't document exact config/endpoint changed or verification commands.

**Fix**:
Rewrote historylog entry with high signal:

```markdown
# [2025-12-28] Builder • UI → API endpoint integration

- Scope: Added custom endpoint in UI config to route chat to the API
- Changes:
  - ui/config.yaml: Added `custom-api` endpoint (baseURL: http://api:8000/v1)
  - api/main.py: Added /v1/chat/completions proxy endpoint
- Verification:
  - curl -X POST http://localhost:8001/v1/chat/completions -d '{"messages":[...]}' → returns citations
  - docker logs api-service | rg "POST /v1/chat/completions" → shows UI traffic
  - UI: Select "Custom API" endpoint, send query → see audit log entry
- Files touched: ui/config.yaml, api/main.py
- Follow-ups: Test with tenant-scoped queries
```

**Prevention**:
Updated SKILL.md to require:
- "Name exact config/endpoint changed"
- "Include curl/compose commands to verify"
- "Show how to check logs for UI→container traffic"

**References**:
- `agents/historylog.md:33`
- `ui/config.yaml:45-52`
- `api/main.py:234-267`

---

## EX-2025-12-28-02: Chunking defaults change

**Tags**: `historylog`, `chunking`, `embedding`, `backfill`

**Trigger phrases**:
- "changed chunking defaults"
- "embedding model update"
- "corpus re-embedding"
- "chunk size change"

**Date**: 2025-12-28

**Problem**:
Entry said "Changed chunking defaults" without old/new values or backfill requirement.

**Impact**:
QA didn't know backfill was needed. Production corpus still had old chunk size until manual discovery.

**Cause**:
Entry didn't document:
- Old vs new values
- Backfill requirement
- Corpus re-embedding impact

**Fix**:
Rewrote entry:

```markdown
# [2025-12-28] Builder • Chunk size increase (256 → 512 tokens)

- Scope: Increase MAX_CHUNK_SIZE to reduce document fragmentation
- Changes:
  - api/config.py: MAX_CHUNK_SIZE = 256 → 512
  - Old behavior: docs split across 3-4 chunks
  - New behavior: docs fit in 1-2 chunks
- Backfill requirement: **REQUIRED for existing corpora**
  - Run: `python scripts/backfill_embeddings.py --tenant <id> --max-chunk 512`
  - Impact: All existing embeddings invalidated, must re-embed
- Verification:
  - Query "example term" → citations reference fewer, longer chunks
  - Before: avg 3.2 chunks/answer, After: avg 1.8 chunks/answer
- Files touched: api/config.py, scripts/backfill_embeddings.py
- Follow-ups: Schedule backfill for prod (est. 2 hours)
```

**Prevention**:
Updated SKILL.md to require:
- "Include old/new values for config changes"
- "Explicit backfill requirement with command"
- "Note corpus re-embedding impact and duration estimate"

**References**:
- `agents/historylog.md:67`
- `api/config.py:34`
- `scripts/backfill_embeddings.py`

---

<!--
Add new examples below this line.
DO NOT insert examples above existing ones (breaks Example ID stability).
-->
