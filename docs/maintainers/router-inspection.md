# Bounded router inspection

`millrace.router.inspection` (`INSPECTION_CONTRACT =
"millrace-router-inspection/v1"`) gives operator clients bounded, read-only
projections of one state home. The five functions are re-exported from
`millrace.router.control`, so the Plus control facade can forward them with its
adapter resolver. `INSPECTION_CONTRACT` is a plain attribute of both modules; it
is not in `control.__all__`.

```python
list_instances_page(*, state_home, limit=50, cursor=None, adapter_resolver=None)
inspect_instance_page(instance_id, *, state_home, limit=50, cursor=None,
                      adapter_resolver=None)
inspect_run_page(instance_id, run_id, *, state_home, section="summary", limit=50,
                 cursor=None, request_id=None, invocation_id=None,
                 adapter_resolver=None)
inspect_revision_graph(instance_id, run_id, revision_id, *, state_home,
                       adapter_resolver=None)
validate_revision_summary(instance_id, *, state_home, run_id=None,
                          adapter_resolver=None)
```

These functions read only. They create no run, receipt, candidate, revision or
lock owner. They capture no draft from the run's workflow files, and they make
no adapter readiness or provider probe. Existing `inspect_run`,
`inspect_instance`, `list_instances` and `validate_revision` are unchanged and
remain the unbounded lifecycle reads.

## Arguments

- `section` is `summary`, `invocations`, `transitions` or `controls`.
- `limit` is an integer from 1 to 50; booleans are refused.
- `cursor` is opaque, at most 128 characters from `[A-Za-z0-9_-]`.
- `summary` refuses a cursor.
- `request_id` is allowed only with `controls` and no cursor. It selects zero or
  one exact receipt; `run_id` may be `None` only for this lookup.
- `invocation_id` is allowed only with `invocations`, needs a run and refuses a
  cursor. It selects zero or one invocation of that run. A row from another run
  gives zero items.
- IDs are 32 lowercase hex characters. A revision ID is 64.

Shape errors raise `RouterError("invalid_request")`, or the existing
`invalid_id` for an ID.

## Result envelope

Every result has exactly these keys: `format`, `kind`, `instance_id`, `run_id`,
`state_version`, `items`, `next_cursor`, `data`.

| Kind | Version | Items | Data |
| --- | --- | --- | --- |
| `instances` | `null` | instances | `{}` |
| `instance` | settings version | runs, newest first | instance |
| `run-summary` | run version | `[]` | summary |
| `invocations`, `transitions`, `controls` | run version (settings version for a null-run receipt lookup) | history, newest first | `{}` |
| `graph` | run version | `[]` | graph |
| `validation` | run version, or settings version | `[]` | validation |

The encoded result (compact UTF-8 JSON) is at most 1 MiB, or the call refuses
`inspection_too_large`.

## Frozen projection keys

Downstream UI consumers rely on these exact keys. Renaming one needs an
amendment.

- **Inventory item:** `instance_id`, `settings_version`, `project_root`,
  `workflow_file`, `store_format`, `error_code`. A refused item keeps its ID and
  error code; every other value is `null`.
- **Instance data:** `instance_id`, `project_root`, `workflow_file`,
  `settings_version`, `runtime_defaults` (`binding`, `timeout_seconds` only),
  `operator_policy` (`binding_pin`, `timeout_ceiling_seconds` only).
- **Run item:** `run_id`, `status`, `mode`, `state_version`,
  `active_revision_id`, `pause_reason`.
- **Summary:**
  - Top level: `run_id`, `status`, `mode`, `state_version`, `run_gate`,
    `pause_reason`, `cancel_requested`, `invocation_count`, `limits`,
    `startup_revision_id`, `active_revision_id`, `pending_candidate`,
    `quiescent`, `inflight_invocations`, `writer_counts`, `planes`, `seams`.
  - `limits`: `max_invocations`, `max_elapsed_seconds`,
    `startup_max_invocations`, `startup_max_elapsed_seconds`, `started_at`,
    `deadline`, `startup_deadline`.
  - `pending_candidate`: `seq`, `candidate_id`, `revision_id`, `diagnostic`,
    `changed_paths`, `withdrawn`, `supersedes`, `version`, `request_id`.
  - `writer_counts`: `recorded` (completion writers) and `effective`
    (`Store.writer`), each mapping a writer state to a count.
  - Each plane: `status`, `cursor`, `active_invocation`, `pause_reason`,
    `resume_target`, `dependencies` (`plane_id`, `status`), `selected`,
    `progress`, `pending_completion_ids`, `last_event` (an event item or
    `null`).
  - Each seam: `latest_occurrence_sequence`, `latest_selection_sequence`,
    `latest_acknowledgement_sequence`; `null` means no recorded row.
- **Invocation item:**
  - `invocation_id`, `attempt`, `plane_id`, `node_id`, `status`,
    `producing_revision_id`.
  - `created_at`, `deadline`, `run_deadline`, `started_version`,
    `completed_version`, `parsed_version`, `result_contract`,
    `uncertainty_diagnostic`.
  - `writer`: `original`, `effective`.
  - `completion`: `transport`, `exit_code`, `classification`, `outcome`,
    `diagnostic`; or `null`.
  - `reconciliation`: `request_id`, `version`, `writer`; or `null`.
  - `settings`: the captured `launch_settings.common`.
  - `unavailable_reason`: maps a withheld field (`uncertainty_diagnostic` or
    `completion.diagnostic`) to `not_a_code`. Diagnostics that are not
    code-shaped are returned as `null` and listed here; the map is empty
    otherwise.
- **Transition item:** `event_kind: "transition"`, `event_id`, `completion_id`,
  `plane_id`, `node_id`, `producing_revision_id`, `routing_revision_id`,
  `outcome`, `action`, `target`, `retry_delta`, `version`.
- **Move item:** `event_kind: "move"`, `event_id`, `move_kind`, `completion_id`,
  `source_invocation_id`, `plane_id`, `target`, `routing_revision_id`,
  `request_id`, `version`. The free-text move reason is omitted.
- **Control item:** the stored receipt/v2 object, unchanged.
- **Graph:**
  - Top level: `revision_id`, `graph_format`, `limits` (`max_invocations`,
    `max_elapsed_seconds`, `max_parallel_invocations`), `bindings` (IDs only),
    `defaults`, `planes`, `seams`.
  - Plane: `start`, `defaults`, `nodes`.
  - Node: `binding`, `timeout_seconds`, `outcomes`, `routes`, `on_error`,
    `on_unknown`, `requires_terminal`.
  - Action: `kind`, `target`, `reason`, `limit`, `exhausted`.
  - Seam: `producer_plane`, `producer_node`, `outcome`, `consumer_plane`,
    `consumer_node`.
  - `defaults` keep only `binding` and `timeout_seconds`.
- **Validation:**
  - `valid`, `revision_id`, `candidate` (`candidate_id`, `revision_id`,
    `diagnostic`, `changed_paths`; `changed_paths` is `null` without a run).
  - `diagnostics` (stable codes), `diagnostic_details`, `warnings`,
    `validation_context`.
  - `diagnostic_details` entries are exactly `{code, paths}`: a stable code
    plus any missing relative control-file paths. Unlike public
    `validate_revision`, the bounded summary never copies exception text,
    which can carry raw authored values such as an invalid `cwd`.
  - `resolved_nodes`: plane → node → `format`, `common`.

The projections never include argv, cwd, pid, environment names, host paths,
executable paths, launch or adapter settings, adapter evidence, prompts, raw
stdout/stderr, base64 file content, workspace paths or the hold ID.

Ordering:
- Inventory: instance ID ascending.
- Instance runs: persisted row order, newest first.
- Invocations: `attempt DESC`.
- Controls: `seq DESC`.
- Transitions and moves: `version DESC, plane_id ASC, event_kind ASC,
  event_id ASC`.

A completion is pending only when neither a transition nor a move routes it.

## Cursors

A history or instance-run cursor is `c<B>_<key>`. `B` is 32 hex characters of a
digest over kind, instance, run, section and the observed state version. The
key is the last returned stable row key:
- invocations: attempt;
- controls: seq;
- transitions/moves: `<version>_<t|m>_<event_id>` (the plane ID is read back from
  that row);
- instance runs: run ID.

Continuation reads rows strictly after that key. A row added later, for example
a queued or refused control that changes no run version, sorts before the key, so
pages neither repeat nor skip rows. The following return `inspection_changed`:
- a different target or section;
- a changed run or settings version (note that `start_run` advances the settings
  version);
- a key row that no longer matches.

An inventory cursor is `i<B>_<last instance ID>`. Its pages are independent
observations, not a global snapshot.

## Budgets and accounting

These are fixed reader limits, not workflow or runtime capacity:

| Limit | Value |
| --- | --- |
| Page size | 50 rows |
| Inventory entries | 4,096 |
| Planes | 128 |
| Graph nodes | 512 |
| Graph routes, dependencies and seams | 4,096 |
| Validation or changed-path lists | 256 entries each |
| Seam, `selected` and `progress` entries | 4,096 each |
| Result size | 1 MiB |
| Rows per instance | 100,000 |
| Stored octets per instance | 32 MiB |
| Stored revision or validation input | 4 MiB each |
| Inventory page work | 200,000 rows and 64 MiB |

Each bounded read uses two explicit read-only snapshots:

1. **Private bounded load.** It opens `instance.json` once, nonblocking, and
   refuses a nonregular target (`instance_integrity`) without waiting. It charges
   the descriptor's `fstat` size before reading and reads at most one byte past
   it, so a record that grew after sizing refuses `inspection_too_large` without
   being loaded. It then opens one connection, runs `BEGIN`, accounts the store,
   and decodes metadata and revisions in that transaction. It uses the same
   identity and dependency checks as `load_instance`.
2. **Store snapshot.** `Store(..., read_budget=...)` accounts the store as the
   first statement after its existing `BEGIN`. Normal validation and the
   projection run in that same snapshot.

Accounting counts every row of every table, plus the stored octets of every TEXT
and BLOB value via `length(CAST(value AS BLOB))`. This is UTF-8 bytes, not
characters. Each revision body is also checked against the per-revision limit.
Scans stop when a limit is exceeded and parse no JSON. An exceeded limit raises
`inspection_too_large` before validation decodes anything.

An inventory page charges each instance the larger of its two snapshot scans. A
further instance starts only while a full per-instance budget remains;
otherwise the page stops early with a cursor. An over-budget or corrupt instance
becomes its own refused item.

The validation summary also limits each folder read of the current workflow
controls to 4 MiB through `capture_candidate(..., read_limit=...)`. Exceeding it
refuses `inspection_too_large` rather than producing a validation diagnostic.

## Exact lookup cost

An exact invocation or receipt lookup is one primary-key or unique-index read.
It runs only after full validation of the whole owning instance, not just the
selected run. The work per call is:

- the bounded load snapshot: accounting, `instance.json`, metadata and every
  revision decoded;
- the Store snapshot: accounting, `PRAGMA quick_check`, `foreign_key_check`,
  every control, every revision and every run's history, including completion
  stdout;
- the row read.

Unrelated history in the same instance can therefore make a lookup refuse
`inspection_too_large`. `quick_check` page work is proportional to the store
file, including free pages, and has no separate limit. These are reader
limitations, not capacity claims.

## Errors

| Code | Meaning |
| --- | --- |
| `invalid_request` | Bad arguments or a malformed cursor. |
| `inspection_changed` | Stale or cross-target cursor. |
| `inspection_too_large` | A reader budget was exceeded. |
| `unknown_revision` | The revision does not belong to the run. |
| `request_target_mismatch` | The receipt is not associated with the supplied run. |

The existing identity, format, integrity and `unknown_run` codes are unchanged.
A revision belongs to a run if it is:
- the run's startup or active revision;
- the pending valid candidate;
- in the run's activation history;
- the revision of one of its invocations.
