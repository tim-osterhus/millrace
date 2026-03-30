# Examples (Compose Stack Changes)

Append new examples to the END of this file and never change existing Example IDs.

---

## EX-2026-02-04-01: Port change (low risk)

**Tags**: `infra`, `compose`, `ports`, `low-risk`

**Trigger phrases**:
- "change port"
- "port already allocated"
- "service not reachable on localhost"
- "published port mismatch"

**Date**: 2026-02-04

**Problem**:
A UI/service port needs to change (host port), but you must not break service names, container ports, or other compose wiring.

**Cause**:
Common mistakes:
- Changing the container port instead of only the published host port
- Renaming the service while touching ports (breaking scripts/tests)
- Editing multiple compose files at once without verifying the rendered config

**Fix**:
1) Change ONLY the published port mapping, keep the container port unchanged.
2) Validate the compose schema:
   - `docker compose -f infra/compose/<file>.yml config >/dev/null`
3) Bring up the stack:
   - `docker compose -f infra/compose/<file>.yml up -d --build`
4) Verify reachability at the new port:
   - `curl -I http://localhost:<new-port>/health` (or the repo's equivalent)
5) Verify no unexpected service renames:
   - `docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}' | head`

**Prevention**:
- Treat compose service names as API (do not rename casually).
- Keep port changes isolated to one file and one mapping unless the task explicitly requires more.
- Always run `docker compose ... config` and skim the rendered ports section before `up`.

**References**:
- `infra/compose/<file>.yml`
- `docker compose -f infra/compose/<file>.yml config`

---

## EX-2026-02-04-02: Optional helper service via override file (medium risk)

**Tags**: `infra`, `compose`, `override`, `dependencies`, `medium-risk`

**Trigger phrases**:
- "add a helper service"
- "add redis" / "add qdrant" / "add reranker"
- "compose override file"
- "optional service"

**Date**: 2026-02-04

**Problem**:
You need to add an optional helper service without destabilizing the base stack or changing production defaults.

**Cause**:
Typical drift sources:
- Editing the base compose file directly when an override is enough
- Introducing implicit dependencies (base stack now fails without the helper)
- Changing networks/volumes in ways that break existing bring-up scripts

**Fix**:
1) Add the helper service in a NEW override compose file (do not modify base file unless required):
   - `infra/compose/<override>.yml`
2) Ensure the base stack still works without the override:
   - `docker compose -f infra/compose/<base>.yml config >/dev/null`
3) Validate the combined stack:
   - `docker compose -f infra/compose/<base>.yml -f infra/compose/<override>.yml config >/dev/null`
4) Bring up the combined stack:
   - `docker compose -f infra/compose/<base>.yml -f infra/compose/<override>.yml up -d --build`
5) Verify the helper is healthy and doesn't break core services:
   - `docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}' | head`
   - Run the repo's smallest smoke check that touches the helper path (if applicable)

**Prevention**:
- Prefer override files for optional services.
- Avoid modifying base service defaults unless the task card explicitly requires it.
- Document how to run the override (exact `docker compose -f ... -f ...` command) in any affected docs.

**References**:
- `infra/compose/<base>.yml`
- `infra/compose/<override>.yml`

