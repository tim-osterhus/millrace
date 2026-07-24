# Errors And Refusals

Millrace reports expected failures as bounded errors or governed refusals.
Use `--json` for automation and branch on `code`, `reason`, or `error_kind`
instead of human-readable messages.

## CLI Envelope

Successful JSON results contain exactly `ok`, `command`, `code`, `message`,
and `data`. Error results contain exactly `ok`, `command`, `code`, `message`,
and `details`.

| Exit | Meaning | State changed? | Retry? | Operator action |
| --- | --- | --- | --- | --- |
| `0` | Command completed | Depends on the command | Not needed | Inspect returned data. A doctor command can complete while reporting unhealthy findings. |
| `1` | Unexpected CLI failure | Unknown | Once | Retry once, then report the bounded error without parsing traceback text. |
| `2` | Invalid command or input shape | No | After correction | Fix arguments, JSON, actor identity, or local options. |
| `3` | Governed domain refusal | Normally no new requested mutation | After state/input changes | Inspect the exact code and current status. |
| `4` | Missing, unsupported, or corrupt persistence | No requested mutation | Not blindly | Preserve files; run workspace/package/status/doctor checks and restore known-good state. |
| `5` | Runner or evidence failure | A claim may already be durable; no terminal result was accepted | Only when safe | Repair adapter/provider/output configuration, then inspect the claimed run. |

Example:

```json
{"ok":false,"command":"workspace.check","code":"store_not_initialized","message":"SQLite runtime store is not initialized.","details":{}}
```

## Operator Reference

| Family | Representative codes | Meaning | State changed? | Retry? | Operator action |
| --- | --- | --- | --- | --- | --- |
| CLI usage | `argument_parse_error`, `invalid_actor_id`, `invalid_payload_json`, `invalid_max_events` | The command could not form a valid request. | No | After correction | Use command help and correct the request. |
| Workspace and CAS | `store_not_initialized`, `cas_root_not_initialized`, `substrate_error` | Required local state is absent or inconsistent. | No requested mutation | Not blindly | Initialize a new workspace or inspect known-good state. |
| Plans and packages | `plan_not_admitted`, `compiled_plan_export_invalid`, `plan_fingerprint_drift`, `package_archive_unreadable`, package-selection diagnostic codes | Package or selected-plan authority is unavailable or invalid. | Failed operations do not partially select a plan | After correction | Verify package identity, digests, selection, and fingerprint. |
| Queue and operator input | `invalid_payload_source`, `unknown_queue_family`, `queue_family_not_external`, `invalid_payload_schema` | The selected plan does not accept this intake. | No | After correction | Use a declared external queue and valid payload. |
| Dispatch and readiness | `unknown_run`, `already_claimed`, `stale_generation`, `workspace_paused`, `lineage_quarantined`, `operator_wait_active`, `dependency_not_ready` | Work is absent, stale, gated, or corrupt. | Read-only queries do not mutate; claim success does | Re-query first | Inspect status, waits, dependencies, and current run identity. |
| Waits and interventions | `unknown_operator_wait`, `operator_wait_resolution_forbidden`, `invalid_operator_wait_target`, `unknown_intervention_option` | The requested operator action is not selected or is stale. | No | After refresh | List current waits/interventions and choose a declared option. |
| Compiler diagnostics | `missing_id`, `duplicate_id`, `missing_reference`, `invalid_artifact_schema`, `unsupported_terminal_action_kind`, `unsupported_compatibility_profile` | Authored workflow/package authority is invalid. | No runtime mutation | After source correction | Fix all error diagnostics before admission. |
| Daemon lifecycle | `no_ready_work`, `ready_state_refused`, `ready_state_corrupt`, `lifecycle_transition_refused`, `observation_refused` | The daemon found no work or could not legally advance it. | Depends on the last accepted transition | After inspection | Read status, runs, trace, waits, and doctor output before retrying. |
| Adapter and evidence | `timeout`, `cancelled`, `missing_opt_in_config`, `invocation_failed`, `result_parse_failed`, `input_too_large`, `output_too_large`, `redaction_refused`, `selected_authority_refused` | The runner attempt failed or its output could not become candidate evidence. | A claim may exist; no terminal route was accepted | Only when category permits | Repair local configuration, selected authority, wrapper output, or bounds. |
| Package doctor | `workflow_package_registry_load_refused`, `active_pin_selected_plan_corrupt`, and finding categories such as `manifest_digest_mismatch` | Package health is unhealthy or unknown. | No; doctor is read-only | After repair | Repair the package source/registry or preserve an active pin as reported. |

## Stable Fields

- Compiler errors expose diagnostic `code`.
- Kernel transition refusals expose `reason`.
- CLI results expose `code`.
- Package command audit exposes `error_code`.
- Runner adapters expose `error_kind`.
- Doctor output exposes `overall_status`, finding `category`, and diagnostic
  `code`.

Messages, local paths, provider responses, internal exception names, and
unspecified diagnostic detail are not stable automation fields.

## Important Boundaries

Runner success is candidate evidence, not completion authority. A failed
adapter attempt creates no terminal action, route, artifact, or closure,
although the preceding claim may already be recorded.

Persistence corruption is not automatically repaired. Preserve the workspace
and inspect it through supported read-only commands before restoring from a
known-good source.

An absent v0.21 command normally produces `argument_parse_error` or
`command_not_implemented`; v0.22 does not provide dedicated migration codes.
Authored compatibility profiles produce `unsupported_compatibility_profile`.
Unsupported selected runner authority can produce `adapter_kind_refused`.
