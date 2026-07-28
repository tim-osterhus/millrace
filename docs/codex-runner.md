# Codex Runner

Millrace supports Codex through an operator-provided wrapper. A selected
workflow names the `codex` adapter kind; local configuration supplies the
wrapper command, environment, limits, and redaction policy. Codex is never an
automatic fallback from Millforge or from a failed runner attempt.

## Adapter Configuration

Pass a JSON configuration file to `millrace run daemon`:

```json
{
  "codex": {
    "adapter_id": "local-codex",
    "wrapper_mode": "local_argv",
    "wrapper_argv": ["/absolute/path/to/codex-wrapper"],
    "cwd": "/absolute/path/to/workspace",
    "env_allowlist": {},
    "timeout_seconds": 3600,
    "max_input_bundle_bytes": 65536,
    "max_stdout_bytes": 65536,
    "max_stderr_diagnostic_bytes": 4096,
    "redaction_policy": {
      "policy_id": "local-default",
      "secret_tokens": []
    },
    "live_test_opt_in_env_flags": []
  }
}
```

Millrace starts `wrapper_argv` directly without a shell. Use an absolute
wrapper path and working directory. Add only required environment variables
to `env_allowlist`; credentials do not belong in workflows, prompts, skills,
or retained diagnostics.

The adapter uses the lower of the local `timeout_seconds` and the selected
workflow timeout. Local configuration cannot enlarge selected authority.

## Wrapper Protocol

The wrapper reads exactly one UTF-8 JSON object from stdin and writes exactly
one UTF-8 JSON object to stdout. Extra stdout prose, multiple JSON values,
missing keys, or unknown keys are refused.

### Invocation bundle

The invocation object has
`record_kind == "codex_adapter_invocation_bundle"` and
`schema_version == 2`. Every field below is required.

| Field | Type | Meaning |
| --- | --- | --- |
| `record_kind` | string | Exact constant `codex_adapter_invocation_bundle`. |
| `schema_version` | integer | Exact value `2`. |
| `adapter_id` | string | Local adapter identity; echo it in the result. |
| `selected_runner_binding_id` | string | Selected runner binding. |
| `selected_adapter_kind` | string | Exact selected adapter kind. |
| `timeout_seconds` | number | Effective lower timeout used for invocation. |
| `request_timeout_seconds` | number | Timeout selected by workflow authority. |
| `correlation_id` | string | Invocation correlation identity. |
| `environment_policy_ref` | string or null | Selected environment-policy reference. |
| `local_config_ref` | string or null | Local configuration reference. |
| `cancellation_token` | string or null | Optional cancellation identity. |
| `redaction_policy` | object | Exact `policy_id` string and `secret_tokens` array of strings. |
| `dispatch_envelope` | object | Selected dispatch payload, including work, governance, terminal options, assets, and schemas. |
| `dispatch_echo` | object | Exact dispatch identity that must be returned unchanged. |
| `selected_asset_material` | object | Mapping from selected asset ID to selected material. |
| `entrypoint_asset_ref` | string or null | Selected entrypoint asset ID. |
| `skill_asset_refs` | array of strings | Selected stage-core skill IDs. |
| `legal_terminal_markers` | array of strings | Markers accepted for this dispatch. |
| `selected_asset_refs` | object | `entrypoint_asset_id` (string or null), `skill_asset_ids` (array of strings), and `artifact_schema_ids` (array of strings). |
| `prompt` | object | Normalized prompt material described below. |

`dispatch_echo` has exactly these keys:

- `run_id`: nonblank string;
- `session_id`: nonblank string;
- `dispatch_generation`: positive integer;
- `session_fencing_token`: nonblank string;
- `claim_id`: nonblank string;
- `generation`: integer;
- `fencing_token`: nonblank string;
- `plan_fingerprint`: nonblank string;
- `stage_kind_id`: nonblank string;
- `graph_node_id`: nonblank string;
- `runner_binding_id`: nonblank string;
- `correlation_id`: nonblank string;
- `selected_authority_digest`: `sha256:` digest over the complete canonical
  schema-v5 `dispatch_envelope` plus `selected_adapter_kind`.

The `prompt` object has exactly the selected context the wrapper needs:

| Field | Type |
| --- | --- |
| `instructions` | string |
| `dispatch_identity` | object containing `run_id`, `plan_id`, `claim_id`, `generation`, `fencing_token`, `plan_fingerprint`, `stage_kind_id`, `graph_node_id`, `runner_binding_id`, and `correlation_id` |
| `work_item_payload` | JSON value selected for the work item |
| `governance_context` | JSON object |
| `selected_join_evidence` | JSON value |
| `selected_entrypoint` | object with nullable string `asset_id` and nullable selected `material` |
| `selected_stage_core_skills` | array of objects with string `asset_id` and nullable selected `material` |
| `legal_terminal_options` | array of selected terminal-option objects |
| `legal_terminal_markers` | array of strings |
| `artifact_schema_expectations` | array of artifact-schema ID strings |

### Success result

The result must contain exactly these keys:

| Field | Type | Requirement |
| --- | --- | --- |
| `outcome_kind` | string | Exact value `success`. |
| `adapter_id` | string | Must equal the invocation `adapter_id`. |
| `dispatch_echo` | object | Must contain exactly the thirteen unchanged echo keys, including the complete selected-authority digest. |
| `redaction_policy_id` | string | Must equal the configured policy ID. |
| `marker` | string or null | Candidate terminal marker selected from the legal markers. |
| `captured_stdout` | string or null | Bounded provider output retained as evidence. |
| `captured_stderr` | string or null | Bounded provider diagnostic output. |
| `structured_provider_response` | object | Required JSON-compatible provider response. |
| `artifact_payload_candidate` | object or null | Candidate payload validated against selected artifact authority. |
| `observation_payload_candidate` | object or null | Candidate observation payload. |
| `evidence_construction_diagnostics` | object | JSON-compatible diagnostics; use `{}` when empty. |

The wrapper must not infer runtime authority. `marker`,
`artifact_payload_candidate`, and `observation_payload_candidate` are
candidates only. Millrace authenticates the dispatch echo, validates marker
and schema authority, converts accepted evidence, and applies the selected
runtime transition. Captured output and diagnostics never become terminal
authority.

### Minimal exchange

This abbreviated example retains every required top-level field. Real
`dispatch_envelope` and prompt values contain the selected workflow data.

```json
{
  "record_kind": "codex_adapter_invocation_bundle",
  "schema_version": 2,
  "adapter_id": "local-codex",
  "selected_runner_binding_id": "runner.worker",
  "selected_adapter_kind": "codex",
  "timeout_seconds": 600,
  "request_timeout_seconds": 600,
  "correlation_id": "corr-1",
  "environment_policy_ref": null,
  "local_config_ref": null,
  "cancellation_token": null,
  "redaction_policy": {"policy_id": "local-default", "secret_tokens": []},
  "dispatch_envelope": {"work_item_payload": {}, "governance_context": {}, "selected_join_evidence": null, "terminal_options": []},
  "dispatch_echo": {"run_id":"run-1","session_id":"session-1","dispatch_generation":1,"session_fencing_token":"session-fence-1","claim_id":"claim-1","generation":1,"fencing_token":"fence-1","plan_fingerprint":"sha256:example","stage_kind_id":"stage.worker","graph_node_id":"worker.start","runner_binding_id":"runner.worker","correlation_id":"corr-1","selected_authority_digest":"sha256:0000000000000000000000000000000000000000000000000000000000000000"},
  "selected_asset_material": {},
  "entrypoint_asset_ref": null,
  "skill_asset_refs": [],
  "legal_terminal_markers": ["WORK_COMPLETE"],
  "selected_asset_refs": {"entrypoint_asset_id":null,"skill_asset_ids":[],"artifact_schema_ids":[]},
  "prompt": {"instructions":"Return candidate evidence.","dispatch_identity":{"run_id":"run-1","plan_id":"plan-1","claim_id":"claim-1","generation":1,"fencing_token":"fence-1","plan_fingerprint":"sha256:example","stage_kind_id":"stage.worker","graph_node_id":"worker.start","runner_binding_id":"runner.worker","correlation_id":"corr-1"},"work_item_payload":{},"governance_context":{},"selected_join_evidence":null,"selected_entrypoint":{"asset_id":null,"material":null},"selected_stage_core_skills":[],"legal_terminal_options":[],"legal_terminal_markers":["WORK_COMPLETE"],"artifact_schema_expectations":[]}
}
```

```json
{
  "outcome_kind": "success",
  "adapter_id": "local-codex",
  "dispatch_echo": {"run_id":"run-1","session_id":"session-1","dispatch_generation":1,"session_fencing_token":"session-fence-1","claim_id":"claim-1","generation":1,"fencing_token":"fence-1","plan_fingerprint":"sha256:example","stage_kind_id":"stage.worker","graph_node_id":"worker.start","runner_binding_id":"runner.worker","correlation_id":"corr-1","selected_authority_digest":"sha256:0000000000000000000000000000000000000000000000000000000000000000"},
  "redaction_policy_id": "local-default",
  "marker": "WORK_COMPLETE",
  "captured_stdout": null,
  "captured_stderr": null,
  "structured_provider_response": {},
  "artifact_payload_candidate": null,
  "observation_payload_candidate": null,
  "evidence_construction_diagnostics": {}
}
```

## Run The Daemon

```bash
millrace --workspace /absolute/path/to/workspace run daemon \
  --adapter-kind codex \
  --adapter-config-json /absolute/path/to/codex-adapter.json \
  --monitor basic
```

Add `--max-ticks N` for a bounded validation run. Omit it for a daemon that
continues polling until stopped.
