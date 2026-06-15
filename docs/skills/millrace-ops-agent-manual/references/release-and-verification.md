# Release And Verification

## Contents

- Workspace health claims
- Execution-progress claims
- Source release checks
- Common false claims

## Workspace Health Claims

Before claiming a Millrace workspace is healthy, verify at least:

```bash
millrace compile validate --workspace <workspace>
millrace status --workspace <workspace>
millrace queue ls --workspace <workspace>
millrace doctor --workspace <workspace>
```

If the workspace uses a managed baseline after a runtime package update, run
`millrace upgrade --apply --workspace <workspace>` when managed assets must be
refreshed, then validate compile again.

Do not claim readiness from file inspection alone when runtime surfaces are
available.

## Execution-Progress Claims

Before claiming execution progressed, verify run evidence:

```bash
millrace runs ls --workspace <workspace>
millrace runs show <run_id> --workspace <workspace>
millrace runs trace <run_id> --workspace <workspace>
```

For closure claims, also inspect Arbiter status and closure evidence. If
Arbiter generated remediation, confirm the runtime-created remediation incident
was resolved through same-lineage execution before claiming closure.

## Source Release Checks

For `dev/source/millrace/`, release readiness normally requires:

```bash
uv run --extra dev pytest -q
uv run --extra dev ruff check src tests
uv build
```

When `packages/millrace-web/` changes, also run:

```bash
PYTHONPATH=src:packages/millrace-web/src uv run --extra dev --with fastapi --with uvicorn --with httpx pytest packages/millrace-web/tests -q
uv run --with ruff ruff check packages/millrace-web
uv run --with build python -m build --wheel --outdir dist packages/millrace-web
```

For release artifacts, build both wheels and run `twine check`:

```bash
rm -rf build dist src/millrace_ai.egg-info packages/millrace-web/build packages/millrace-web/dist packages/millrace-web/src/millrace_web.egg-info
uv build --wheel
uv run --with build python -m build --wheel --outdir dist packages/millrace-web
uv run --with twine python -m twine check dist/*
```

PyPI publication is tag-driven through
`.github/workflows/publish-to-pypi.yml`. A release tag builds and publishes
both `millrace-ai` and `millrace-web`, so versions must be synchronized when
the sidecar is present.

## Common False Claims

- "The daemon is idle, so everything is done." Idle can mean no claimable work,
  an unresolved closure target, paused state, stale state, or blocker.
- "The stage artifact parsed, so the run succeeded." Check `runtime_outcome`.
- "The task is done because Builder finished." Checker, Updater, closure, and
  remediation may still be pending.
- "Config reload applied everything." Active runs keep their launch compiled
  plan while newer plans wait.
- "The web dashboard can fix it." `millrace-web` is read-only.
- "A direct file move is equivalent to an intervention command." It is not
  audited and may break runtime invariants.
