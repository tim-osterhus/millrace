# Repo Guardrails

Batch 23 run 01 establishes one repo-local command path for source and promoted-clean validation:

```bash
python tools/repo_guardrails.py <subcommand>
```

Available subcommands:

- `lint`
- `typecheck`
- `budgets`
- `cycles`
- `test -- <pytest args>`
- `all -- <optional pytest args>`

Recommended setup:

```bash
python -m pip install -e '.[dev]'
```

Current policy is intentionally conservative:

- linting and type-checking apply to the guardrail tooling itself first
- repo-wide backslide prevention is enforced immediately through:
  - file-size budgets
  - import-cycle baseline checks
  - the pytest entrypoint with `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`

Examples:

```bash
python tools/repo_guardrails.py lint
python tools/repo_guardrails.py typecheck
python tools/repo_guardrails.py budgets
python tools/repo_guardrails.py cycles
python tools/repo_guardrails.py test -- -q tests/test_baseline_assets.py tests/test_package_parity.py
```

After restaging `clean/` with `python3 work/migration/stage_clean_repo.py --apply`, the same command path should work inside the promoted repo when the interpreter has the dev dependencies available.
