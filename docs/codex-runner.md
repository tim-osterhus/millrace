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
    "wrapper_protocol_version": 3,
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

`wrapper_protocol_version` is optional and defaults to `3`. The only accepted
values are the integers `3` and `4`; booleans and all other values are
refused. Protocol 3 is the compatibility path: it retains the current exact
invocation and result wire bytes, uses bundle schema version 3, has no
reviewed token-usage capability, and does not add a `context_checkout` prompt
key. An unbound protocol-3 session remains legal. A bound context session
must use protocol 4 and is refused before checkout capture or external runner
work when configured for protocol 3.

Protocol 4 is the selected-context and reviewed-usage path. It uses bundle
schema version 4, is the only Codex protocol eligible for a daemon
`--max-total-tokens` budget, and is required by configurations that use
selected context or durable usage evidence.

Duplicate-key rejection is a protocol-4 rule; protocol 3 retains legacy JSON
parsing compatibility.

## Wrapper Protocol

The wrapper reads exactly one UTF-8 JSON object from stdin and writes exactly
one UTF-8 JSON object to stdout. Extra stdout prose, multiple JSON values,
missing keys, or unknown keys are refused.

### Invocation bundle

The invocation object has
`record_kind == "codex_adapter_invocation_bundle"`. Protocol 3 has
`schema_version == 3`; protocol 4 has `schema_version == 4`. Every field
listed below is required in both versions unless a version-specific rule says
otherwise.

| Field | Type | Meaning |
| --- | --- | --- |
| `record_kind` | string | Exact constant `codex_adapter_invocation_bundle`. |
| `schema_version` | integer | Exact value `3` for protocol 3 or `4` for protocol 4. |
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
| `selected_artifact_schemas` | array of objects | Canonical selected `ArtifactSchemaDeclaration` records required by every artifact-bearing selected terminal option. |
| `selected_asset_material` | object | Mapping from selected asset ID to selected material. |
| `entrypoint_asset_ref` | string or null | Selected entrypoint asset ID. |
| `skill_asset_refs` | array of strings | Selected stage-core skill IDs. |
| `legal_terminal_markers` | array of strings | Markers accepted for this dispatch. |
| `selected_asset_refs` | object | `entrypoint_asset_id` (string or null), `skill_asset_ids` (array of strings), and `artifact_schema_ids` (array of strings). |
| `prompt` | object | Normalized prompt material described below. Protocol 4 adds exactly one `context_checkout` field. |

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
  schema-v7 `dispatch_envelope` plus `selected_adapter_kind`.

The `prompt` object has exactly the selected context the wrapper needs:

| Field | Type |
| --- | --- |
| `instructions` | string |
| `dispatch_identity` | object containing `run_id`, `session_id`, `dispatch_generation`, `session_fencing_token`, `plan_id`, `claim_id`, `generation`, `fencing_token`, `plan_fingerprint`, `stage_kind_id`, `graph_node_id`, `runner_binding_id`, and `correlation_id` |
| `work_item_payload` | JSON value selected for the work item |
| `governance_context` | JSON object |
| `selected_join_evidence` | JSON value |
| `selected_wait_evidence` | JSON value |
| `selected_entrypoint` | object with nullable string `asset_id` and nullable selected `material` |
| `selected_stage_core_skills` | array of objects with string `asset_id` and nullable selected `material` |
| `legal_terminal_options` | array of selected terminal-option objects |
| `legal_terminal_markers` | array of strings |
| `artifact_schema_expectations` | array of artifact-schema ID strings |
| `terminal_artifact_contracts` | array of objects | One mechanically selected contract per terminal result mapping. |

Protocol 4 adds `prompt.context_checkout`. It is either `null` for an
unbound dispatch or the authenticated compact descriptor copied from
`dispatch_envelope.context_checkout`. A non-null descriptor has exactly these
five fields: `manifest_digest`, `binding_id`, `router_asset_id`,
`checkout_relative_path`, and `router_relative_path`. The wrapper receives
paths and identity only; Millrace does not read or inline checkout files or
file bodies into the bundle. The wrapper may use ordinary filesystem tools
from the selected `cwd` to read the selected `router_relative_path` (the
materialized `CONTEXT.md`). It must not discover ambient context or replace
the authenticated descriptor with another path.

Each `selected_artifact_schemas` record contains `record_kind`,
`schema_version`, `id`, and the exact JSON `schema` from the authenticated
selected declaration. Presentation metadata is not part of the canonical
authority record. Each `terminal_artifact_contracts` record contains
`outcome_id`, `marker`, `action_id`, `action_kind`, `artifact_schema_id`, and
`json_schema`. The last two fields are both `null` for a terminal branch that
does not emit an artifact. Every artifact-bearing selected terminal option must
have exactly one matching selected schema declaration; null-artifact options do
not require one, and a partial mapping is valid. The adapter derives schema
records from selected terminal options and selected declarations, and derives
contracts only from selected terminal result mappings plus those schema
records. Duplicate, missing, unknown, unselected, or incoherent material is
refused before the external invocation starts. This projection grants no route
or action authority to the runner.

### Success result

For protocol 3, the result must contain exactly these keys:

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

Protocol 4 adds exactly one top-level key to that envelope:

| `token_usage` | object | Required and non-null. Exactly `input_tokens`, `output_tokens`, and `total_tokens`, all non-negative durable integers with `total_tokens == input_tokens + output_tokens`. |

The wrapper must not infer runtime authority. `marker`,
`artifact_payload_candidate`, and `observation_payload_candidate` are
candidates only. Millrace authenticates the dispatch echo, validates marker
and schema authority, converts accepted evidence, and applies the selected
runtime transition. Captured output and diagnostics never become terminal
authority.

### Error result

For protocol 3, the wrapper may instead return an exact error envelope, selected by
`outcome_kind == "error"`. It contains only these keys:

| Field | Type | Requirement |
| --- | --- | --- |
| `outcome_kind` | string | Exact value `error`. |
| `adapter_id` | string | Must equal the invocation adapter identity. |
| `error_kind` | string | A supported `AdapterErrorResult` error kind. |
| `redaction_policy_id` | string | Must equal the configured policy ID. |
| `dispatch_echo` | object | The complete, unchanged thirteen-field dispatch echo. |
| `diagnostics` | object | JSON-compatible authority values; Millrace applies the existing typed redaction and bounds. |

Protocol 4 adds exactly one top-level `token_usage` key to the error envelope.
An authenticated error after provider work must carry a non-null usage object
with the same exact three fields and total invariant as a success. A null
value is permitted only for `missing_opt_in_config`. That wrapper envelope is
the pinned fail-closed preflight case: it must refuse before any `codex exec`
or provider work. Other pre-provider refusal semantics are adapter-generated
outcomes, not protocol-4 wrapper envelopes. Transport, redaction, and adapter
refusal outcomes therefore do not acquire wrapper usage by implication; when
external work occurred and usage is absent, the existing budget path refuses
the evidence and suspends rather than counting zero or completing. A success
with null usage, any other error kind with null usage, missing or extra usage
fields, duplicate JSON keys, booleans, negatives, values above the durable
int64 bound, or a contradictory total is `result_parse_failed`. Cached-input
and reasoning-output subdivisions may remain bounded diagnostics, but they
never change the three durable totals.

The object must contain exactly these keys, and every dispatch-echo field is
authenticated against the expected dispatch. Missing or extra keys, malformed
diagnostics, unsupported error kinds, stale identity, unknown outcome kinds, or
trailing JSON are `result_parse_failed`. If configured secret text appears
anywhere in wrapper stdout, transport redaction takes precedence and the
outcome is `redaction_refused`, even when the error envelope is otherwise
valid.

### Minimal exchange

This abbreviated example retains every required top-level field. Real
`dispatch_envelope` and prompt values contain the selected workflow data.

```json
{
  "record_kind": "codex_adapter_invocation_bundle",
  "schema_version": 3,
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
  "dispatch_envelope": {"work_item_payload": {}, "governance_context": {}, "selected_join_evidence": null, "selected_wait_evidence": null, "terminal_options": []},
  "dispatch_echo": {"run_id":"run-1","session_id":"session-1","dispatch_generation":1,"session_fencing_token":"session-fence-1","claim_id":"claim-1","generation":1,"fencing_token":"fence-1","plan_fingerprint":"sha256:example","stage_kind_id":"stage.worker","graph_node_id":"worker.start","runner_binding_id":"runner.worker","correlation_id":"corr-1","selected_authority_digest":"sha256:0000000000000000000000000000000000000000000000000000000000000000"},
  "selected_artifact_schemas": [],
  "selected_asset_material": {},
  "entrypoint_asset_ref": null,
  "skill_asset_refs": [],
  "legal_terminal_markers": ["WORK_COMPLETE"],
  "selected_asset_refs": {"entrypoint_asset_id":null,"skill_asset_ids":[],"artifact_schema_ids":[]},
  "prompt": {"instructions":"Return candidate evidence.","dispatch_identity":{"run_id":"run-1","session_id":"session-1","dispatch_generation":1,"session_fencing_token":"session-fence-1","plan_id":"plan-1","claim_id":"claim-1","generation":1,"fencing_token":"fence-1","plan_fingerprint":"sha256:example","stage_kind_id":"stage.worker","graph_node_id":"worker.start","runner_binding_id":"runner.worker","correlation_id":"corr-1"},"work_item_payload":{},"governance_context":{},"selected_join_evidence":null,"selected_wait_evidence":null,"selected_entrypoint":{"asset_id":null,"material":null},"selected_stage_core_skills":[],"legal_terminal_options":[],"legal_terminal_markers":["WORK_COMPLETE"],"artifact_schema_expectations":[],"terminal_artifact_contracts":[]}
}
```

A minimal exchange above is protocol 3 and is intentionally unchanged: it has
no `prompt.context_checkout` and no `token_usage` key. A protocol-4 exchange
uses the same surrounding envelope with `schema_version: 4`, adds
`prompt.context_checkout` (the five-field descriptor or `null`), and requires
`token_usage` in the wrapper result.

A populated artifact-bearing and null-artifact projection is shaped like this:

```json
{
  "selected_artifact_schemas": [
    {"record_kind":"artifact_schema_declaration","schema_version":1,"id":"artifact.example","schema":{"type":"object","required":["value"],"properties":{"value":{"type":"string"}}}}
  ],
  "prompt": {
    "terminal_artifact_contracts": [
      {"outcome_id":"stage.complete","marker":"ARTIFACT_READY","action_id":"action.route","action_kind":"route","artifact_schema_id":"artifact.example","json_schema":{"type":"object","required":["value"],"properties":{"value":{"type":"string"}}}},
      {"outcome_id":"stage.no_artifact","marker":"NO_ARTIFACT","action_id":"action.close","action_kind":"close","artifact_schema_id":null,"json_schema":null}
    ]
  }
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

## Session Lifecycle And Cancellation

Codex uses the generic durable runner-session lifecycle. The wrapper bundle
uses protocol 3/schema 3 by default or protocol 4/schema 4 when selected,
while its schema-7 dispatch envelope and dispatch echo carry the required
`session_id`, `dispatch_generation`, and `session_fencing_token`. Millrace
derives the session-unique correlation and cancellation identities; once a
session is active they are non-null and may not be supplied as alternate
authority by wrapper output. Only protocol 4 exposes the reviewed usage
mapping marker. When a token budget is selected, daemon startup refuses a
protocol-3 Codex configuration before creating the budget epoch; a started
protocol-4 session whose authenticated result lacks usage refuses the budget
evidence and suspends the dispatch rather than completing silently.

`millrace runs cancel RUN_ID --input-id ID` records a durable operator request.
The coordinator signals only the exact owned subprocess session, then records
cooperative, terminate, kill, and transport-cleanup evidence as applicable.
Restart reattachment for a local subprocess is unsupported, so potentially
live work after process restart becomes lost/orphan risk instead of being
restarted or reported clean.

Use `runs show`, `trace show RUN_ID`, `status`, `doctor`, and the finite
`runs follow RUN_ID --after-sequence N` projection to inspect the session.
See [Runner-session architecture](runner-session-architecture.md) and
[Daemon lifecycle](daemon-lifecycle.md).
