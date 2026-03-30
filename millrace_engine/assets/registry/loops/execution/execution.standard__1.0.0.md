# Standard Execution Loop

> Generated from the canonical JSON registry object. Edit the JSON file, not this companion.

- Kind: `loop_config`
- Canonical ID: `execution.standard`
- Version: `1.0.0`
- Tier: `default`
- Status: `active`
- Source Kind: `packaged_default`
- Source Ref: `registry/loops/execution/execution.standard__1.0.0.json`
- Aliases: `standard`, `builder-qa-loop`
- Labels: `baseline`, `execution`, `standard`
- Extends: _none_
- Created At: 2026-03-19T00:00:00Z
- Updated At: 2026-03-19T00:00:00Z

## Summary

Packaged standard execution loop matching the current v1 task path.

## Payload

```json
{
  "edges": [
    {
      "condition": {
        "fact": "builder_success_target",
        "kind": "fact_equals",
        "value": "integration"
      },
      "description": null,
      "edge_id": "execution.builder.success.integration",
      "from_node_id": "builder",
      "kind": "normal",
      "max_attempts": null,
      "on_outcomes": [
        "success"
      ],
      "priority": 100,
      "terminal_state_id": null,
      "to_node_id": "integration"
    },
    {
      "condition": {
        "fact": "builder_success_target",
        "kind": "fact_equals",
        "value": "qa"
      },
      "description": null,
      "edge_id": "execution.builder.success.qa",
      "from_node_id": "builder",
      "kind": "normal",
      "max_attempts": null,
      "on_outcomes": [
        "success"
      ],
      "priority": 200,
      "terminal_state_id": null,
      "to_node_id": "qa"
    },
    {
      "condition": null,
      "description": null,
      "edge_id": "execution.builder.failure.escalate",
      "from_node_id": "builder",
      "kind": "escalation",
      "max_attempts": null,
      "on_outcomes": [
        "blocked",
        "terminal_failure"
      ],
      "priority": 100,
      "terminal_state_id": null,
      "to_node_id": "troubleshoot"
    },
    {
      "condition": null,
      "description": null,
      "edge_id": "execution.integration.success.qa",
      "from_node_id": "integration",
      "kind": "normal",
      "max_attempts": null,
      "on_outcomes": [
        "success"
      ],
      "priority": 100,
      "terminal_state_id": null,
      "to_node_id": "qa"
    },
    {
      "condition": null,
      "description": null,
      "edge_id": "execution.integration.failure.escalate",
      "from_node_id": "integration",
      "kind": "escalation",
      "max_attempts": null,
      "on_outcomes": [
        "blocked",
        "terminal_failure"
      ],
      "priority": 100,
      "terminal_state_id": null,
      "to_node_id": "troubleshoot"
    },
    {
      "condition": null,
      "description": null,
      "edge_id": "execution.qa.success.update",
      "from_node_id": "qa",
      "kind": "normal",
      "max_attempts": null,
      "on_outcomes": [
        "success"
      ],
      "priority": 100,
      "terminal_state_id": null,
      "to_node_id": "update"
    },
    {
      "condition": null,
      "description": null,
      "edge_id": "execution.qa.quickfix.hotfix",
      "from_node_id": "qa",
      "kind": "normal",
      "max_attempts": null,
      "on_outcomes": [
        "quickfix_needed"
      ],
      "priority": 100,
      "terminal_state_id": null,
      "to_node_id": "hotfix"
    },
    {
      "condition": null,
      "description": null,
      "edge_id": "execution.qa.failure.escalate",
      "from_node_id": "qa",
      "kind": "escalation",
      "max_attempts": null,
      "on_outcomes": [
        "blocked",
        "terminal_failure"
      ],
      "priority": 100,
      "terminal_state_id": null,
      "to_node_id": "troubleshoot"
    },
    {
      "condition": null,
      "description": null,
      "edge_id": "execution.hotfix.success.doublecheck",
      "from_node_id": "hotfix",
      "kind": "normal",
      "max_attempts": null,
      "on_outcomes": [
        "success"
      ],
      "priority": 100,
      "terminal_state_id": null,
      "to_node_id": "doublecheck"
    },
    {
      "condition": null,
      "description": null,
      "edge_id": "execution.hotfix.failure.escalate",
      "from_node_id": "hotfix",
      "kind": "escalation",
      "max_attempts": null,
      "on_outcomes": [
        "blocked",
        "terminal_failure"
      ],
      "priority": 100,
      "terminal_state_id": null,
      "to_node_id": "troubleshoot"
    },
    {
      "condition": null,
      "description": null,
      "edge_id": "execution.doublecheck.success.update",
      "from_node_id": "doublecheck",
      "kind": "normal",
      "max_attempts": null,
      "on_outcomes": [
        "success"
      ],
      "priority": 100,
      "terminal_state_id": null,
      "to_node_id": "update"
    },
    {
      "condition": null,
      "description": null,
      "edge_id": "execution.doublecheck.quickfix.retry",
      "from_node_id": "doublecheck",
      "kind": "normal",
      "max_attempts": null,
      "on_outcomes": [
        "quickfix_needed"
      ],
      "priority": 100,
      "terminal_state_id": null,
      "to_node_id": "hotfix"
    },
    {
      "condition": null,
      "description": null,
      "edge_id": "execution.doublecheck.failure.escalate",
      "from_node_id": "doublecheck",
      "kind": "escalation",
      "max_attempts": null,
      "on_outcomes": [
        "blocked",
        "terminal_failure"
      ],
      "priority": 100,
      "terminal_state_id": null,
      "to_node_id": "troubleshoot"
    },
    {
      "condition": null,
      "description": null,
      "edge_id": "execution.troubleshoot.success.resume",
      "from_node_id": "troubleshoot",
      "kind": "normal",
      "max_attempts": null,
      "on_outcomes": [
        "success"
      ],
      "priority": 100,
      "terminal_state_id": null,
      "to_node_id": "builder"
    },
    {
      "condition": null,
      "description": null,
      "edge_id": "execution.troubleshoot.blocked.consult",
      "from_node_id": "troubleshoot",
      "kind": "escalation",
      "max_attempts": null,
      "on_outcomes": [
        "blocked",
        "terminal_failure"
      ],
      "priority": 100,
      "terminal_state_id": null,
      "to_node_id": "consult"
    },
    {
      "condition": null,
      "description": null,
      "edge_id": "execution.consult.success.resume",
      "from_node_id": "consult",
      "kind": "normal",
      "max_attempts": null,
      "on_outcomes": [
        "success"
      ],
      "priority": 100,
      "terminal_state_id": null,
      "to_node_id": "builder"
    },
    {
      "condition": null,
      "description": null,
      "edge_id": "execution.consult.handoff.needs_research",
      "from_node_id": "consult",
      "kind": "handoff",
      "max_attempts": null,
      "on_outcomes": [
        "handoff",
        "blocked",
        "terminal_failure"
      ],
      "priority": 100,
      "terminal_state_id": "needs_research",
      "to_node_id": null
    },
    {
      "condition": null,
      "description": null,
      "edge_id": "execution.update.success.archive",
      "from_node_id": "update",
      "kind": "terminal",
      "max_attempts": null,
      "on_outcomes": [
        "success"
      ],
      "priority": 100,
      "terminal_state_id": "idle",
      "to_node_id": null
    }
  ],
  "entry_node_id": "builder",
  "model_profile_ref": {
    "id": "model.default",
    "kind": "model_profile",
    "version": "1.0.0"
  },
  "nodes": [
    {
      "artifact_bindings": [],
      "kind_id": "execution.builder",
      "node_id": "builder",
      "overrides": {
        "allow_search": null,
        "effort": null,
        "model": null,
        "model_profile_ref": null,
        "prompt_asset_ref": null,
        "runner": null,
        "timeout_seconds": null
      }
    },
    {
      "artifact_bindings": [],
      "kind_id": "execution.integration",
      "node_id": "integration",
      "overrides": {
        "allow_search": null,
        "effort": null,
        "model": null,
        "model_profile_ref": null,
        "prompt_asset_ref": null,
        "runner": null,
        "timeout_seconds": null
      }
    },
    {
      "artifact_bindings": [],
      "kind_id": "execution.qa",
      "node_id": "qa",
      "overrides": {
        "allow_search": null,
        "effort": null,
        "model": null,
        "model_profile_ref": null,
        "prompt_asset_ref": null,
        "runner": null,
        "timeout_seconds": null
      }
    },
    {
      "artifact_bindings": [],
      "kind_id": "execution.hotfix",
      "node_id": "hotfix",
      "overrides": {
        "allow_search": null,
        "effort": null,
        "model": null,
        "model_profile_ref": null,
        "prompt_asset_ref": null,
        "runner": null,
        "timeout_seconds": null
      }
    },
    {
      "artifact_bindings": [],
      "kind_id": "execution.doublecheck",
      "node_id": "doublecheck",
      "overrides": {
        "allow_search": null,
        "effort": null,
        "model": null,
        "model_profile_ref": null,
        "prompt_asset_ref": null,
        "runner": null,
        "timeout_seconds": null
      }
    },
    {
      "artifact_bindings": [],
      "kind_id": "execution.troubleshoot",
      "node_id": "troubleshoot",
      "overrides": {
        "allow_search": null,
        "effort": null,
        "model": null,
        "model_profile_ref": null,
        "prompt_asset_ref": null,
        "runner": null,
        "timeout_seconds": null
      }
    },
    {
      "artifact_bindings": [],
      "kind_id": "execution.consult",
      "node_id": "consult",
      "overrides": {
        "allow_search": null,
        "effort": null,
        "model": null,
        "model_profile_ref": null,
        "prompt_asset_ref": null,
        "runner": null,
        "timeout_seconds": null
      }
    },
    {
      "artifact_bindings": [],
      "kind_id": "execution.update",
      "node_id": "update",
      "overrides": {
        "allow_search": null,
        "effort": null,
        "model": null,
        "model_profile_ref": null,
        "prompt_asset_ref": null,
        "runner": null,
        "timeout_seconds": null
      }
    }
  ],
  "outline_policy": {
    "mode": "hybrid",
    "shard_glob": null
  },
  "plane": "execution",
  "task_authoring_profile_ref": {
    "id": "task_authoring.narrow",
    "kind": "task_authoring_profile",
    "version": "1.0.0"
  },
  "task_authoring_required": true,
  "terminal_states": [
    {
      "emits_artifacts": [
        "run_bundle",
        "stage_summary"
      ],
      "ends_plane_run": true,
      "terminal_class": "success",
      "terminal_state_id": "idle",
      "writes_status": "UPDATE_COMPLETE"
    },
    {
      "emits_artifacts": [
        "stage_summary"
      ],
      "ends_plane_run": true,
      "terminal_class": "handoff",
      "terminal_state_id": "needs_research",
      "writes_status": "NEEDS_RESEARCH"
    }
  ]
}
```
