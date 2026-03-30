# Narrow Authoring

> Generated from the canonical JSON registry object. Edit the JSON file, not this companion.

- Kind: `task_authoring_profile`
- Canonical ID: `task_authoring.narrow`
- Version: `1.0.0`
- Tier: `golden`
- Status: `active`
- Source Kind: `packaged_default`
- Source Ref: `registry/task_authoring/task_authoring.narrow__1.0.0.json`
- Aliases: `narrow`
- Labels: `baseline`, `task-authoring`
- Extends: _none_
- Created At: 2026-03-18T00:00:00Z
- Updated At: 2026-03-18T00:00:00Z

## Summary

Packaged high-rigor task-authoring profile.

## Payload

```json
{
  "acceptance_profile": "strict",
  "allowed_task_breadth": "focused",
  "decomposition_style": "narrow",
  "expected_card_count": {
    "max_cards": 6,
    "min_cards": 2
  },
  "gate_strictness": "strict",
  "required_metadata_fields": [
    "spec_id",
    "acceptance_ids"
  ],
  "research_assumption": "consult_if_ambiguous",
  "single_card_synthesis_allowed": false,
  "suitable_use_cases": [
    "High-risk code changes",
    "Cross-file refactors"
  ]
}
```
