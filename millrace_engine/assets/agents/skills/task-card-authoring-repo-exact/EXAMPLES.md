# Examples - Task Card Authoring (Repo-Exact)

This file stores detailed, real-world examples for the task-card-authoring-repo-exact skill.

Append new examples to the END of this file and never change existing Example IDs.

---

## EX-2025-12-28-01: Promote backlog item into active task

**Tags**: `backlog`, `task-promotion`, `scope-refinement`

**Trigger phrases**:
- "promote backlog item"
- "move task to active"
- "make backlog item 1-cycle sized"
- "turn backlog into task card"

**Date**: 2025-12-28

**Problem**:
Backlog item was vague: "Add user authentication." Developer didn't refine it before starting work, leading to scope creep and confusion about what to actually implement.

**Impact**:
- Builder spent 3 hours researching JWT vs session auth vs OAuth
- No clear DONE criteria, so QA couldn't validate
- Work spanned 8 files across RAG API and LibreChat
- Merge blocked because scope was unclear

**Cause**:
Task card lacked:
- Explicit file paths (`rag_api/auth.py`, `librechat/middleware/`)
- Concrete DONE checks with commands
- Scope boundaries (what's IN vs OUT)

**Fix**:
Rewrote task card with repo-exact format:
```markdown
## Add JWT token validation to RAG API

**Scope**: Add token validation middleware to `/query` and `/chat` endpoints.

**Files to change**:
- `rag_api/auth.py` (new file)
- `rag_api/main.py` (add middleware)
- `rag_api/config.py` (add JWT_SECRET env var)
- `tests/test_auth.py` (new file)

**DONE checks**:
- [ ] `curl -H "Authorization: Bearer invalid" http://localhost:8001/query` → 401
- [ ] `curl -H "Authorization: Bearer valid-token" http://localhost:8001/query` → 200
- [ ] `pytest tests/test_auth.py -v` → PASS

**Prompt artifact**:
- `agents/prompts/tasks/001-add-jwt-token-validation.md`

**Out of scope** (defer to future tasks):
- OAuth integration
- User registration/login UI
- Token refresh logic
```

**Prevention**:
Updated SKILL.md to require:
- "Reference concrete paths under `rag_api/`, `infra/`, or `librechat/`"
- "No 'update the backend' phrasing"
- "Task card includes exact validation commands"

**References**:
- `agents/tasks.md`
- `agents/tasksbacklog.md`
- Commit: ghi9012

---

## EX-2025-12-28-02: Split vague multi-feature request

**Tags**: `task-splitting`, `retrieval`, `scope-definition`

**Trigger phrases**:
- "improve retrieval"
- "add hybrid retrieval and reranking"
- "multiple features in one task"
- "too many changes"

**Date**: 2025-12-28

**Problem**:
User request: "Add hybrid retrieval and reranking improvements."

Developer created single task card that included:
- pg_trgm extension install
- Lexical (BM25) scoring
- Dense vector retrieval
- Hybrid fusion algorithm
- GPU reranker service
- Reranker API integration
- Precision@k validation

Result: 18 files changed, 4-hour implementation, impossible to test atomically.

**Impact**:
- Builder couldn't complete in one cycle
- QA couldn't isolate which change affected quality
- Merge blocked by complexity
- Rollback would lose all progress

**Cause**:
Task card didn't enforce "1-cycle deliverable" constraint. Multiple independent features bundled together.

**Fix**:
Split into 2 separate task cards:

**Task Card 1: Enable hybrid lexical+dense retrieval**
```markdown
## Enable pg_trgm + lexical scoring

**Scope**: Install pg_trgm extension, add BM25 scoring, merge with dense results.

**Files to change**:
- `scripts/init_pg_trgm.sql` (new)
- `rag_api/services/query.py` (add lexical_search function)
- `rag_api/config.py` (add HYBRID_ENABLED env var)
- `tests/test_hybrid.py` (new)

**DONE checks**:
- [ ] `psql -c "SELECT * FROM pg_extension WHERE extname='pg_trgm';"` → 1 row
- [ ] `curl -X POST http://localhost:8001/query -d '{"query":"testator","hybrid_enabled":true}'` → includes lexical+dense results
- [ ] `pytest tests/test_hybrid.py -v` → PASS
- [ ] Run precision@k report on 50-query test set, compare hybrid vs dense-only

**Prompt artifact**:
- `agents/prompts/tasks/002-enable-pg-trgm-lexical.md`

**Out of scope**: Reranking (separate task)
```

**Task Card 2: Add GPU reranker toggle**
```markdown
## Add reranker service integration

**Scope**: Add reranker service, integrate with query flow, add toggle.

**Files to change**:
- `infra/compose/docker-compose.reranker.yml` (new)
- `rag_api/services/rerank.py` (new)
- `rag_api/config.py` (add RERANK_ENABLED env var)
- `tests/test_reranker.py` (new)

**DONE checks**:
- [ ] `docker compose -f docker-compose.reranker.yml up -d` → reranker service running
- [ ] `curl -X POST http://localhost:8001/query -d '{"query":"testator","rerank_enabled":true}'` → reranked results
- [ ] `pytest tests/test_reranker.py -v` → PASS
- [ ] Run precision@k report, compare reranked vs not-reranked

**Prompt artifact**:
- `agents/prompts/tasks/003-add-reranker-integration.md`

**Out of scope**: Hybrid retrieval (handled in separate task)
```

Each task is now 1-cycle sized, independently testable, and reversible.

**Prevention**:
Updated SKILL.md to require:
- "Split by subsystem (RAG API vs LibreChat vs infra)"
- "Each card includes a retrieval report requirement (precision@k sample set)"
- "If multiple approaches exist, choose the simplest"

**References**:
- `agents/tasks.md`
- `agents/prompts/builder_cycle.md`
- Commits: jkl3456, mno7890

---

<!--
Add new examples below this line.
DO NOT insert examples above existing ones (breaks Example ID stability).
-->
