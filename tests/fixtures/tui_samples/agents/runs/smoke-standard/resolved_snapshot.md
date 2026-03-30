# Resolved Snapshot

> Generated from `resolved_snapshot.json`. Treat the JSON artifact as canonical.
> Compile-time provenance only. Runtime execution history is written separately to `transition_history.jsonl`.

- Snapshot ID: `resolved-snapshot:smoke-standard:dfd14ef4748d`
- Run ID: `smoke-standard`
- Created At: `2026-03-19T12:01:06.229110Z`
- Selection: `mode:mode.standard@1.0.0`
- Frozen Plan ID: `frozen-plan:dfd14ef4748d6792cff8bba75b8cdd93e906294faf070ca57033612b4d4eeef2`
- Frozen Plan Hash: `dfd14ef4748d6792cff8bba75b8cdd93e906294faf070ca57033612b4d4eeef2`
- Selected Mode: `mode:mode.standard@1.0.0`
- Execution Loop: `loop_config:execution.standard@1.0.0`
- Research Participation: `none`

## Parameter Rebinding Rules

- `execution.builder.allow_search` at `stage_boundary` (current=`True`)
- `execution.builder.effort` at `stage_boundary` (current=`'high'`)
- `execution.builder.model` at `stage_boundary` (current=`'gpt-5.3-codex'`)
- `execution.builder.model_profile_ref` at `stage_boundary` (current=`{'kind': 'model_profile', 'id': 'model.default', 'version': '1.0.0'}`)
- `execution.builder.runner` at `stage_boundary` (current=`'codex'`)
- `execution.builder.timeout_seconds` at `stage_boundary` (current=`None`)
- `execution.consult.allow_search` at `stage_boundary` (current=`False`)
- `execution.consult.effort` at `stage_boundary` (current=`'medium'`)
- `execution.consult.model` at `stage_boundary` (current=`'gpt-5.3-codex'`)
- `execution.consult.model_profile_ref` at `stage_boundary` (current=`{'kind': 'model_profile', 'id': 'model.default', 'version': '1.0.0'}`)
- `execution.consult.runner` at `stage_boundary` (current=`'codex'`)
- `execution.consult.timeout_seconds` at `stage_boundary` (current=`None`)
- `execution.doublecheck.allow_search` at `stage_boundary` (current=`False`)
- `execution.doublecheck.effort` at `stage_boundary` (current=`'medium'`)
- `execution.doublecheck.model` at `stage_boundary` (current=`'gpt-5.3-codex'`)
- `execution.doublecheck.model_profile_ref` at `stage_boundary` (current=`{'kind': 'model_profile', 'id': 'model.default', 'version': '1.0.0'}`)
- `execution.doublecheck.runner` at `stage_boundary` (current=`'codex'`)
- `execution.doublecheck.timeout_seconds` at `stage_boundary` (current=`None`)
- `execution.hotfix.allow_search` at `stage_boundary` (current=`False`)
- `execution.hotfix.effort` at `stage_boundary` (current=`'medium'`)
- `execution.hotfix.model` at `stage_boundary` (current=`'gpt-5.3-codex'`)
- `execution.hotfix.model_profile_ref` at `stage_boundary` (current=`{'kind': 'model_profile', 'id': 'model.default', 'version': '1.0.0'}`)
- `execution.hotfix.runner` at `stage_boundary` (current=`'codex'`)
- `execution.hotfix.timeout_seconds` at `stage_boundary` (current=`None`)
- `execution.integration.allow_search` at `stage_boundary` (current=`False`)
- `execution.integration.effort` at `stage_boundary` (current=`'medium'`)
- `execution.integration.model` at `stage_boundary` (current=`'gpt-5.3-codex'`)
- `execution.integration.model_profile_ref` at `stage_boundary` (current=`{'kind': 'model_profile', 'id': 'model.default', 'version': '1.0.0'}`)
- `execution.integration.runner` at `stage_boundary` (current=`'codex'`)
- `execution.integration.timeout_seconds` at `stage_boundary` (current=`None`)
- `execution.qa.allow_search` at `stage_boundary` (current=`False`)
- `execution.qa.effort` at `stage_boundary` (current=`'medium'`)
- `execution.qa.model` at `stage_boundary` (current=`'gpt-5.3-codex'`)
- `execution.qa.model_profile_ref` at `stage_boundary` (current=`{'kind': 'model_profile', 'id': 'model.default', 'version': '1.0.0'}`)
- `execution.qa.runner` at `stage_boundary` (current=`'codex'`)
- `execution.qa.timeout_seconds` at `stage_boundary` (current=`None`)
- `execution.troubleshoot.allow_search` at `stage_boundary` (current=`False`)
- `execution.troubleshoot.effort` at `stage_boundary` (current=`'medium'`)
- `execution.troubleshoot.model` at `stage_boundary` (current=`'gpt-5.3-codex'`)
- `execution.troubleshoot.model_profile_ref` at `stage_boundary` (current=`{'kind': 'model_profile', 'id': 'model.default', 'version': '1.0.0'}`)
- `execution.troubleshoot.runner` at `stage_boundary` (current=`'codex'`)
- `execution.troubleshoot.timeout_seconds` at `stage_boundary` (current=`None`)
- `execution.update.allow_search` at `stage_boundary` (current=`False`)
- `execution.update.effort` at `stage_boundary` (current=`'medium'`)
- `execution.update.model` at `stage_boundary` (current=`'gpt-5.3-codex'`)
- `execution.update.model_profile_ref` at `stage_boundary` (current=`{'kind': 'model_profile', 'id': 'model.default', 'version': '1.0.0'}`)
- `execution.update.runner` at `stage_boundary` (current=`'codex'`)
- `execution.update.timeout_seconds` at `stage_boundary` (current=`None`)

## Execution Plan

- Loop Ref: `loop_config:execution.standard@1.0.0`
- Plane: `execution`
- Entry Node: `builder`
- Task Authoring Profile: `task_authoring_profile:task_authoring.narrow@1.0.0`
- Model Profile: `model_profile:model.default@1.0.0`
- Outline Mode: `hybrid`

### Stages

- `builder` -> `execution.builder` (runner=`codex`, model=`gpt-5.3-codex`, search=`True`)
- `consult` -> `execution.consult` (runner=`codex`, model=`gpt-5.3-codex`, search=`False`)
- `doublecheck` -> `execution.doublecheck` (runner=`codex`, model=`gpt-5.3-codex`, search=`False`)
- `hotfix` -> `execution.hotfix` (runner=`codex`, model=`gpt-5.3-codex`, search=`False`)
- `integration` -> `execution.integration` (runner=`codex`, model=`gpt-5.3-codex`, search=`False`)
- `qa` -> `execution.qa` (runner=`codex`, model=`gpt-5.3-codex`, search=`False`)
- `troubleshoot` -> `execution.troubleshoot` (runner=`codex`, model=`gpt-5.3-codex`, search=`False`)
- `update` -> `execution.update` (runner=`codex`, model=`gpt-5.3-codex`, search=`False`)

### Transitions

- `execution.builder.failure.escalate`: `builder` --[blocked, terminal_failure]--> node `troubleshoot`
- `execution.builder.success.integration`: `builder` --[success]--> node `integration`
- `execution.builder.success.qa`: `builder` --[success]--> node `qa`
- `execution.consult.handoff.needs_research`: `consult` --[handoff, blocked, terminal_failure]--> terminal `needs_research`
- `execution.consult.success.resume`: `consult` --[success]--> node `builder`
- `execution.doublecheck.failure.escalate`: `doublecheck` --[blocked, terminal_failure]--> node `troubleshoot`
- `execution.doublecheck.quickfix.retry`: `doublecheck` --[quickfix_needed]--> node `hotfix`
- `execution.doublecheck.success.update`: `doublecheck` --[success]--> node `update`
- `execution.hotfix.failure.escalate`: `hotfix` --[blocked, terminal_failure]--> node `troubleshoot`
- `execution.hotfix.success.doublecheck`: `hotfix` --[success]--> node `doublecheck`
- `execution.integration.failure.escalate`: `integration` --[blocked, terminal_failure]--> node `troubleshoot`
- `execution.integration.success.qa`: `integration` --[success]--> node `qa`
- `execution.qa.failure.escalate`: `qa` --[blocked, terminal_failure]--> node `troubleshoot`
- `execution.qa.quickfix.hotfix`: `qa` --[quickfix_needed]--> node `hotfix`
- `execution.qa.success.update`: `qa` --[success]--> node `update`
- `execution.troubleshoot.blocked.consult`: `troubleshoot` --[blocked, terminal_failure]--> node `consult`
- `execution.troubleshoot.success.resume`: `troubleshoot` --[success]--> node `builder`
- `execution.update.success.archive`: `update` --[success]--> terminal `idle`

### Resume States

- `NEEDS_RESEARCH` -> `needs_research` (handoff)
- `UPDATE_COMPLETE` -> `idle` (success)

## Compile Diagnostics

- none
