# Examples - Small-Diff Discipline

This file stores detailed, real-world examples for the small-diff-discipline skill.

Append new examples to the END of this file and never change existing Example IDs.

---

## EX-2025-12-28-01: Failing test without changing behavior

**Tags**: `tests`, `debugging`, `minimal-change`

**Trigger phrases**:
- "fix failing test"
- "test broke but behavior is correct"
- "test needs update"
- "green to red test"

**Date**: 2025-12-28

**Problem**:
The `/query` endpoint test failed after a minor refactor, but the endpoint behavior was correct. Developer was tempted to refactor surrounding test code while fixing.

**Impact**:
Test failure blocked merge. Risk of scope creep if test refactor introduces new bugs.

**Cause**:
Test assertion expected old response format. Only the assertion needed updating, but developer initially planned to "clean up" 5 other test files.

**Fix**:
- Touched only `tests/test_rag_api.py` (the failing test)
- Updated assertion to match new format
- Ran `pytest tests/test_rag_api.py::test_query -v` → PASS
- Did NOT refactor other test files
- Did NOT add "helpful" logging
- Did NOT update unrelated docstrings

**Prevention**:
Added to SKILL.md procedure step 3:
- "Hard rule: if you touch >10 files, justify each file in the task summary"
- Reinforced: "Reject anything not needed for DONE checks"

**References**:
- `tests/test_rag_api.py:234`
- `pytest tests/test_rag_api.py::test_query -v`
- Commit: abc1234

---

## EX-2025-12-28-02: Retrieval quality improvement scope creep

**Tags**: `retrieval`, `feature-flag`, `rollback`, `precision`

**Trigger phrases**:
- "improve retrieval quality"
- "make search better"
- "citations not relevant"
- "ranking is off"

**Date**: 2025-12-28

**Problem**:
Task: "Improve retrieval quality." Developer started implementing: new chunking strategy, new embedding model, query rewriting, semantic caching, and relevance feedback. All at once.

**Impact**:
- Massive diff (15 files, 800 lines changed)
- Impossible to isolate which change helped/hurt
- No rollback path if quality regressed
- Merge conflicts with 3 other branches
- QA couldn't validate effectively

**Cause**:
Vague task card ("improve quality") + no constraint on scope. Developer interpreted as "fix everything possible."

**Fix**:
Rolled back entire branch. Created new approach:
1. Added `RERANK_ENABLED=false` env var (feature flag)
2. Implemented minimal reranker integration (3 files: `query.py`, `config.py`, `docker-compose.rag.yml`)
3. Ran precision@k report before/after on 50-query test set
4. Shipped only the reranker toggle
5. Documented rollback: `RERANK_ENABLED=false` in `.env`

Result: 4 files changed, 120 lines. Easy to review, test, and rollback if needed.

**Prevention**:
Updated task card authoring skill to require:
- Explicit scope boundaries
- DONE checks with exact validation commands
- "If multiple approaches exist, choose the simplest"

Updated this skill (small-diff-discipline):
- Added: "Prefer feature flags/toggles over behavior changes that can't be rolled back"
- Added to validation: "Ship with a precision@k report and rollback path" for retrieval changes

**References**:
- `rag_api/services/query.py:45-67`
- `rag_api/config.py:12`
- `infra/compose/docker-compose.rag.yml:28-32`
- `tests/precision_at_k_report.md`
- Commit: def5678

---

<!--
Add new examples below this line.
DO NOT insert examples above existing ones (breaks Example ID stability).
-->
