# Research GoalSpec Loop

> Generated from the canonical JSON registry object. Edit the JSON file, not this companion.

- Kind: `loop_config`
- Canonical ID: `research.goalspec`
- Version: `1.0.0`
- Tier: `default`
- Status: `active`
- Source Kind: `packaged_default`
- Source Ref: `registry/loops/research/research.goalspec__1.0.0.json`
- Aliases: `goalspec`, `research-goalspec`
- Labels: `baseline`, `goalspec`, `research`
- Extends: _none_
- Created At: 2026-03-19T00:00:00Z
- Updated At: 2026-03-19T00:00:00Z

## Summary

Packaged GoalSpec research loop scaffold for the current runtime: broad bounded family synthesis, sibling-specific sizing, bounded agentic Spec Review, and runtime-owned local repair or remediation-family escalation around blocked review remain outside the canonical DAG but inside the shipped GoalSpec contract.

## Payload

```json
{
  "edges": [
    {
      "condition": null,
      "description": null,
      "edge_id": "research.goalspec.goal_intake.success.objective_profile_sync",
      "from_node_id": "goal_intake",
      "kind": "normal",
      "max_attempts": null,
      "on_outcomes": [
        "success"
      ],
      "priority": 100,
      "terminal_state_id": null,
      "to_node_id": "objective_profile_sync"
    },
    {
      "condition": null,
      "description": null,
      "edge_id": "research.goalspec.goal_intake.failure.blocked",
      "from_node_id": "goal_intake",
      "kind": "terminal",
      "max_attempts": null,
      "on_outcomes": [
        "blocked",
        "terminal_failure"
      ],
      "priority": 100,
      "terminal_state_id": "blocked",
      "to_node_id": null
    },
    {
      "condition": null,
      "description": null,
      "edge_id": "research.goalspec.objective_profile_sync.success.spec_synthesis",
      "from_node_id": "objective_profile_sync",
      "kind": "normal",
      "max_attempts": null,
      "on_outcomes": [
        "success"
      ],
      "priority": 100,
      "terminal_state_id": null,
      "to_node_id": "spec_synthesis"
    },
    {
      "condition": null,
      "description": null,
      "edge_id": "research.goalspec.objective_profile_sync.failure.blocked",
      "from_node_id": "objective_profile_sync",
      "kind": "terminal",
      "max_attempts": null,
      "on_outcomes": [
        "blocked",
        "terminal_failure"
      ],
      "priority": 100,
      "terminal_state_id": "blocked",
      "to_node_id": null
    },
    {
      "condition": null,
      "description": null,
      "edge_id": "research.goalspec.spec_synthesis.success.spec_interview",
      "from_node_id": "spec_synthesis",
      "kind": "normal",
      "max_attempts": null,
      "on_outcomes": [
        "success"
      ],
      "priority": 100,
      "terminal_state_id": null,
      "to_node_id": "spec_interview"
    },
    {
      "condition": null,
      "description": null,
      "edge_id": "research.goalspec.spec_synthesis.failure.blocked",
      "from_node_id": "spec_synthesis",
      "kind": "terminal",
      "max_attempts": null,
      "on_outcomes": [
        "blocked",
        "terminal_failure"
      ],
      "priority": 100,
      "terminal_state_id": "blocked",
      "to_node_id": null
    },
    {
      "condition": null,
      "description": null,
      "edge_id": "research.goalspec.spec_interview.success.spec_review",
      "from_node_id": "spec_interview",
      "kind": "normal",
      "max_attempts": null,
      "on_outcomes": [
        "success"
      ],
      "priority": 100,
      "terminal_state_id": null,
      "to_node_id": "spec_review"
    },
    {
      "condition": null,
      "description": null,
      "edge_id": "research.goalspec.spec_interview.failure.blocked",
      "from_node_id": "spec_interview",
      "kind": "terminal",
      "max_attempts": null,
      "on_outcomes": [
        "blocked",
        "terminal_failure"
      ],
      "priority": 100,
      "terminal_state_id": "blocked",
      "to_node_id": null
    },
    {
      "condition": null,
      "description": null,
      "edge_id": "research.goalspec.spec_review.success.taskmaster",
      "from_node_id": "spec_review",
      "kind": "normal",
      "max_attempts": null,
      "on_outcomes": [
        "success"
      ],
      "priority": 100,
      "terminal_state_id": null,
      "to_node_id": "taskmaster"
    },
    {
      "condition": null,
      "description": null,
      "edge_id": "research.goalspec.spec_review.failure.blocked",
      "from_node_id": "spec_review",
      "kind": "terminal",
      "max_attempts": null,
      "on_outcomes": [
        "blocked",
        "terminal_failure"
      ],
      "priority": 100,
      "terminal_state_id": "blocked",
      "to_node_id": null
    },
    {
      "condition": null,
      "description": null,
      "edge_id": "research.goalspec.taskmaster.success.idle",
      "from_node_id": "taskmaster",
      "kind": "terminal",
      "max_attempts": null,
      "on_outcomes": [
        "success"
      ],
      "priority": 100,
      "terminal_state_id": "idle",
      "to_node_id": null
    },
    {
      "condition": null,
      "description": null,
      "edge_id": "research.goalspec.taskmaster.failure.blocked",
      "from_node_id": "taskmaster",
      "kind": "terminal",
      "max_attempts": null,
      "on_outcomes": [
        "blocked",
        "terminal_failure"
      ],
      "priority": 100,
      "terminal_state_id": "blocked",
      "to_node_id": null
    }
  ],
  "entry_node_id": "goal_intake",
  "model_profile_ref": {
    "id": "model.default",
    "kind": "model_profile",
    "version": "1.0.0"
  },
  "nodes": [
    {
      "artifact_bindings": [],
      "kind_id": "research.goal-intake",
      "node_id": "goal_intake",
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
      "artifact_bindings": [
        {
          "input_artifact": "research_brief",
          "source_artifact": "research_brief",
          "source_node_id": "goal_intake"
        }
      ],
      "kind_id": "research.objective-profile-sync",
      "node_id": "objective_profile_sync",
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
      "artifact_bindings": [
        {
          "input_artifact": "objective_profile",
          "source_artifact": "objective_profile",
          "source_node_id": "objective_profile_sync"
        }
      ],
      "kind_id": "research.spec-synthesis",
      "node_id": "spec_synthesis",
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
      "artifact_bindings": [
        {
          "input_artifact": "spec_family_state",
          "source_artifact": "spec_family_state",
          "source_node_id": "spec_synthesis"
        }
      ],
      "kind_id": "research.spec-interview",
      "node_id": "spec_interview",
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
      "artifact_bindings": [
        {
          "input_artifact": "spec_family_state",
          "source_artifact": "spec_family_state",
          "source_node_id": "spec_interview"
        }
      ],
      "kind_id": "research.spec-review",
      "node_id": "spec_review",
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
      "artifact_bindings": [
        {
          "input_artifact": "approved_spec",
          "source_artifact": "approved_spec",
          "source_node_id": "spec_review"
        }
      ],
      "kind_id": "research.taskmaster",
      "node_id": "taskmaster",
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
  "outline_policy": null,
  "plane": "research",
  "task_authoring_profile_ref": null,
  "task_authoring_required": false,
  "terminal_states": [
    {
      "emits_artifacts": [
        "stage_summary"
      ],
      "ends_plane_run": true,
      "terminal_class": "success",
      "terminal_state_id": "idle",
      "writes_status": "IDLE"
    },
    {
      "emits_artifacts": [
        "stage_summary"
      ],
      "ends_plane_run": true,
      "terminal_class": "blocked",
      "terminal_state_id": "blocked",
      "writes_status": "BLOCKED"
    }
  ]
}
```
