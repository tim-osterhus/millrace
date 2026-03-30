# Frozen Run Plan

> Generated from `frozen_run_plan.json`. Treat the JSON artifact as canonical.
> Compile-time provenance only. Standard execution now routes against this frozen plan.
> Runtime `transition_history.jsonl` records the selected edge, any explicit legacy seams, and the bound execution parameters actually used.

- Run ID: `smoke-standard`
- Compiled At: `2026-03-19T12:01:06.229110Z`
- Content Hash: `dfd14ef4748d6792cff8bba75b8cdd93e906294faf070ca57033612b4d4eeef2`
- Compiler Version: `01b-core`
- Selection: `mode:mode.standard@1.0.0`
- Selected Mode: `mode:mode.standard@1.0.0`
- Execution Loop: `loop_config:execution.standard@1.0.0`
- Task Authoring Profile: `task_authoring_profile:task_authoring.narrow@1.0.0`
- Model Profile: `model_profile:model.default@1.0.0`
- Research Participation: `none`

## Outline Policy

- Mode: `hybrid`
- Shard Glob: `n/a`

## Parameter Rebinding Rules

- `execution.builder.allow_search` may rebind at `stage_boundary` from current value `True` (declared by `registered_stage_kind:execution.builder@1.0.0`)
- `execution.builder.effort` may rebind at `stage_boundary` from current value `'high'` (declared by `registered_stage_kind:execution.builder@1.0.0`)
- `execution.builder.model` may rebind at `stage_boundary` from current value `'gpt-5.3-codex'` (declared by `registered_stage_kind:execution.builder@1.0.0`)
- `execution.builder.model_profile_ref` may rebind at `stage_boundary` from current value `{'kind': 'model_profile', 'id': 'model.default', 'version': '1.0.0'}` (declared by `registered_stage_kind:execution.builder@1.0.0`)
- `execution.builder.runner` may rebind at `stage_boundary` from current value `'codex'` (declared by `registered_stage_kind:execution.builder@1.0.0`)
- `execution.builder.timeout_seconds` may rebind at `stage_boundary` from current value `None` (declared by `registered_stage_kind:execution.builder@1.0.0`)
- `execution.consult.allow_search` may rebind at `stage_boundary` from current value `False` (declared by `registered_stage_kind:execution.consult@1.0.0`)
- `execution.consult.effort` may rebind at `stage_boundary` from current value `'medium'` (declared by `registered_stage_kind:execution.consult@1.0.0`)
- `execution.consult.model` may rebind at `stage_boundary` from current value `'gpt-5.3-codex'` (declared by `registered_stage_kind:execution.consult@1.0.0`)
- `execution.consult.model_profile_ref` may rebind at `stage_boundary` from current value `{'kind': 'model_profile', 'id': 'model.default', 'version': '1.0.0'}` (declared by `registered_stage_kind:execution.consult@1.0.0`)
- `execution.consult.runner` may rebind at `stage_boundary` from current value `'codex'` (declared by `registered_stage_kind:execution.consult@1.0.0`)
- `execution.consult.timeout_seconds` may rebind at `stage_boundary` from current value `None` (declared by `registered_stage_kind:execution.consult@1.0.0`)
- `execution.doublecheck.allow_search` may rebind at `stage_boundary` from current value `False` (declared by `registered_stage_kind:execution.doublecheck@1.0.0`)
- `execution.doublecheck.effort` may rebind at `stage_boundary` from current value `'medium'` (declared by `registered_stage_kind:execution.doublecheck@1.0.0`)
- `execution.doublecheck.model` may rebind at `stage_boundary` from current value `'gpt-5.3-codex'` (declared by `registered_stage_kind:execution.doublecheck@1.0.0`)
- `execution.doublecheck.model_profile_ref` may rebind at `stage_boundary` from current value `{'kind': 'model_profile', 'id': 'model.default', 'version': '1.0.0'}` (declared by `registered_stage_kind:execution.doublecheck@1.0.0`)
- `execution.doublecheck.runner` may rebind at `stage_boundary` from current value `'codex'` (declared by `registered_stage_kind:execution.doublecheck@1.0.0`)
- `execution.doublecheck.timeout_seconds` may rebind at `stage_boundary` from current value `None` (declared by `registered_stage_kind:execution.doublecheck@1.0.0`)
- `execution.hotfix.allow_search` may rebind at `stage_boundary` from current value `False` (declared by `registered_stage_kind:execution.hotfix@1.0.0`)
- `execution.hotfix.effort` may rebind at `stage_boundary` from current value `'medium'` (declared by `registered_stage_kind:execution.hotfix@1.0.0`)
- `execution.hotfix.model` may rebind at `stage_boundary` from current value `'gpt-5.3-codex'` (declared by `registered_stage_kind:execution.hotfix@1.0.0`)
- `execution.hotfix.model_profile_ref` may rebind at `stage_boundary` from current value `{'kind': 'model_profile', 'id': 'model.default', 'version': '1.0.0'}` (declared by `registered_stage_kind:execution.hotfix@1.0.0`)
- `execution.hotfix.runner` may rebind at `stage_boundary` from current value `'codex'` (declared by `registered_stage_kind:execution.hotfix@1.0.0`)
- `execution.hotfix.timeout_seconds` may rebind at `stage_boundary` from current value `None` (declared by `registered_stage_kind:execution.hotfix@1.0.0`)
- `execution.integration.allow_search` may rebind at `stage_boundary` from current value `False` (declared by `registered_stage_kind:execution.integration@1.0.0`)
- `execution.integration.effort` may rebind at `stage_boundary` from current value `'medium'` (declared by `registered_stage_kind:execution.integration@1.0.0`)
- `execution.integration.model` may rebind at `stage_boundary` from current value `'gpt-5.3-codex'` (declared by `registered_stage_kind:execution.integration@1.0.0`)
- `execution.integration.model_profile_ref` may rebind at `stage_boundary` from current value `{'kind': 'model_profile', 'id': 'model.default', 'version': '1.0.0'}` (declared by `registered_stage_kind:execution.integration@1.0.0`)
- `execution.integration.runner` may rebind at `stage_boundary` from current value `'codex'` (declared by `registered_stage_kind:execution.integration@1.0.0`)
- `execution.integration.timeout_seconds` may rebind at `stage_boundary` from current value `None` (declared by `registered_stage_kind:execution.integration@1.0.0`)
- `execution.qa.allow_search` may rebind at `stage_boundary` from current value `False` (declared by `registered_stage_kind:execution.qa@1.0.0`)
- `execution.qa.effort` may rebind at `stage_boundary` from current value `'medium'` (declared by `registered_stage_kind:execution.qa@1.0.0`)
- `execution.qa.model` may rebind at `stage_boundary` from current value `'gpt-5.3-codex'` (declared by `registered_stage_kind:execution.qa@1.0.0`)
- `execution.qa.model_profile_ref` may rebind at `stage_boundary` from current value `{'kind': 'model_profile', 'id': 'model.default', 'version': '1.0.0'}` (declared by `registered_stage_kind:execution.qa@1.0.0`)
- `execution.qa.runner` may rebind at `stage_boundary` from current value `'codex'` (declared by `registered_stage_kind:execution.qa@1.0.0`)
- `execution.qa.timeout_seconds` may rebind at `stage_boundary` from current value `None` (declared by `registered_stage_kind:execution.qa@1.0.0`)
- `execution.troubleshoot.allow_search` may rebind at `stage_boundary` from current value `False` (declared by `registered_stage_kind:execution.troubleshoot@1.0.0`)
- `execution.troubleshoot.effort` may rebind at `stage_boundary` from current value `'medium'` (declared by `registered_stage_kind:execution.troubleshoot@1.0.0`)
- `execution.troubleshoot.model` may rebind at `stage_boundary` from current value `'gpt-5.3-codex'` (declared by `registered_stage_kind:execution.troubleshoot@1.0.0`)
- `execution.troubleshoot.model_profile_ref` may rebind at `stage_boundary` from current value `{'kind': 'model_profile', 'id': 'model.default', 'version': '1.0.0'}` (declared by `registered_stage_kind:execution.troubleshoot@1.0.0`)
- `execution.troubleshoot.runner` may rebind at `stage_boundary` from current value `'codex'` (declared by `registered_stage_kind:execution.troubleshoot@1.0.0`)
- `execution.troubleshoot.timeout_seconds` may rebind at `stage_boundary` from current value `None` (declared by `registered_stage_kind:execution.troubleshoot@1.0.0`)
- `execution.update.allow_search` may rebind at `stage_boundary` from current value `False` (declared by `registered_stage_kind:execution.update@1.0.0`)
- `execution.update.effort` may rebind at `stage_boundary` from current value `'medium'` (declared by `registered_stage_kind:execution.update@1.0.0`)
- `execution.update.model` may rebind at `stage_boundary` from current value `'gpt-5.3-codex'` (declared by `registered_stage_kind:execution.update@1.0.0`)
- `execution.update.model_profile_ref` may rebind at `stage_boundary` from current value `{'kind': 'model_profile', 'id': 'model.default', 'version': '1.0.0'}` (declared by `registered_stage_kind:execution.update@1.0.0`)
- `execution.update.runner` may rebind at `stage_boundary` from current value `'codex'` (declared by `registered_stage_kind:execution.update@1.0.0`)
- `execution.update.timeout_seconds` may rebind at `stage_boundary` from current value `None` (declared by `registered_stage_kind:execution.update@1.0.0`)

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

## Source Refs

- `loop_config:execution.standard@1.0.0` | kind=`registry` layer=`packaged` sha256=`396b35075c31a7e06c09f9b231b2eebf056b8f04b3c9a6020fb8da400df468b0`
- `mode:mode.standard@1.0.0` | kind=`registry` layer=`packaged` sha256=`3751ddeeeed4a1874b7bdaf82893ccb2932008cf9f6f1d712390a83b215818b7`
- `model_profile:model.default@1.0.0` | kind=`registry` layer=`packaged` sha256=`c6ed7591105c96afe3a01731ce756554d9aa0622e07ab3d8c8843d9b5d0f953e`
- `registered_stage_kind:execution.builder@1.0.0` | kind=`registry` layer=`packaged` sha256=`4958930f7e5024519d1f240e9b0428ba96c25e7778a557cba07f849802f753b0`
- `registered_stage_kind:execution.consult@1.0.0` | kind=`registry` layer=`packaged` sha256=`e973c797f56072b17ff1fae0b53660895d00b26dba50ae70947328b50a4f50fc`
- `registered_stage_kind:execution.doublecheck@1.0.0` | kind=`registry` layer=`packaged` sha256=`0da3cb3b4e5cc59f0146e56216bd690aa6c858329aa29b5cc03e71d91336cf02`
- `registered_stage_kind:execution.hotfix@1.0.0` | kind=`registry` layer=`packaged` sha256=`9fa09acb85420ab06a55023424ffbf26d9084056531309f43450a9230ca2a1a0`
- `registered_stage_kind:execution.integration@1.0.0` | kind=`registry` layer=`packaged` sha256=`215b08557919ebc9419abceb9876bccc29040cbbf5f39973e78a62d37c1fd498`
- `registered_stage_kind:execution.qa@1.0.0` | kind=`registry` layer=`packaged` sha256=`bc9b04995539e36515ef725bbeb53ebf8e6c3d16f085bab9fc11b3258347ced3`
- `registered_stage_kind:execution.troubleshoot@1.0.0` | kind=`registry` layer=`packaged` sha256=`241c743d2185b89456c83ab8063b042866a4a77de8c870a3400a7a4e893b1379`
- `registered_stage_kind:execution.update@1.0.0` | kind=`registry` layer=`packaged` sha256=`b55988e735b44f09883fdb07821081dfba92d71b814a6164a592f2ea8a601051`
- `task_authoring_profile:task_authoring.narrow@1.0.0` | kind=`registry` layer=`packaged` sha256=`2b0021e27913a27ead4e592d42d83ecf82081f0ba6e8ce4242ff2ba3a6211839`
