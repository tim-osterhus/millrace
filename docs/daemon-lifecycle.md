# Daemon Lifecycle And Signals

`millrace run daemon` is the public Core lifecycle boundary. It acquires the
existing exclusive diagnostic lock and validates storage before registering a
new incarnation or opening its owner-only Unix listener. The CLI has no switch
to disable that boundary. Internal `run_daemon_loop` calls remain the bounded
mechanics seam used by existing tests; they do not by themselves prove the public
lifecycle or independent process exit.

Public startup validates the selected plan, selected runner pin, configured
adapter and all retained session authority before publishing `ready_idle` or
`ready_active`. Classification does not invoke a runner. Created sessions drive
only after readiness and honor retained run holds. A paused or dispatch-suspended
runtime can be ready idle. Missing plan/adapter/pin or potentially live sessions
without supported reattachment refuse readiness. Old unknown aftermath blocks
relaunch; no automatic replacement or completion replay is inferred from liveness.
The existing authenticated completion application and cancellation mechanics
continue inside the bounded loop after successful classification.

Use the public inspector and exact stop request:

```bash
millrace --json daemon inspect
millrace --json daemon inspect --wait-for readiness
millrace --json daemon stop --request-json '<exact control request>'
millrace --json daemon inspect --wait-for exit
millrace --json daemon inspect --wait-for cleanup
```

Inspect supplies `target`; copy it into the normal durable control request with
`action="daemon.stop"`, caller/operation/actor/correlation IDs and reason.
A stop target includes persistent store scope, daemon ID/generation, random
process nonce, current default-plan fingerprint, exact source revision and the
runtime contract tuple. It must match at acceptance. A fresh inspect is required
after default-plan or source changes; an old operation key still replays its
original receipt. An accepted receipt is immutable `accepted_pending`. Query or
seal an uncertain operation through `operations show` or `operations resolve`;
never choose a new key merely because a response was lost.

The runtime tuple identifies `millrace.core.daemon` revision 1, distribution and
installed version, store schema, and the SHA-256 of `DAEMON_PUBLIC_SCHEMA` in
`contracts/daemon_control.py`. `build_identity` is explicitly null for this
source candidate. These declarations are not wheel provenance or released OS
qualification. The initial lifecycle implementation supports exact macOS process
birth/boot observation through libproc and sysctl, and same-UID peers through
getpeereid. Unsupported process observation remains unknown.

Inspect and stop acceptance have two-second caller bounds. The main-thread CLI
uses a 1.75-second real timer, leaving time for rendering; the listener uses its
own bounded socket/SQLite path and never uses that timer from a thread. Readiness,
exit and cleanup waits each observe for at most ten seconds. They are independent
observations, not promises that a blocked adapter has finished. Readiness or exit
timeout never grants permission to launch a replacement.

The private listener directory is 0700 and its socket is 0600. Supported paths
must be absolute, fit macOS `sun_path[104]`, and have owned/root-owned, non-symlink,
non-group/other-writable ancestors. Unsafe or overlong paths refuse; the daemon
does not move the endpoint to an unowned temporary location or adopt a stale
socket. This listener accepts only challenge, status and exact graceful stop.

## Durable Budget Epochs

The daemon accepts optional durable ceilings:

```bash
millrace run daemon \
  --budget-id bounded-run-1 \
  --max-wall-seconds 3600 \
  --max-invocations 8
```

`--max-total-tokens` is available when the resolved selected adapter declares
the reviewed token-usage mapping required for governance. Every limit must be a
positive integer and any limit requires a nonblank caller-selected
`--budget-id`. `--max-ticks` remains a loop/test bound; it is not an invocation
budget.

The first use pins the budget ID to the workspace, selected default plan,
limits, start time, and wall deadline in the fresh schema-11 candidate store. Reusing the same
ID with identical authority resumes its totals and deadline. Changed limits,
workspace, or plan are refused. Accepted runner start intents are counted once
per fenced runner session, and reviewed adapter usage is accumulated
monotonically against that same session identity whenever it is available,
including for epochs that enforce only wall or invocation limits. Missing or
contradictory usage under a token-governed epoch refuses the epoch rather than
treating usage as zero; without a token ceiling, absent usage does not replace
the runner's primary outcome with a usage-governance refusal.

When a total-token ceiling is selected, these normalized input, output, and
total counters enforce it. Otherwise they remain durable measurement evidence.
They are adapter-reported execution evidence, not billing, invoice, provider
spend, price, or provider rate-limit truth.

An explicit operator close is available for one existing epoch:

```bash
millrace run budget-stop --budget-id bounded-run-1
```

The command uses the global `--actor-id` and has no caller-selected terminal
reason. It refuses unless every session bound to the epoch is terminal, clean,
non-lost, completed, and backed by final governed usage evidence. A successful
close records status `stopped` with the exact reason `operator_completed` and
atomically suspends new dispatch. The success envelope contains the bounded
final budget projection and the suspension record needed by the existing
`dispatch resume` command. Replaying the exact stopped/operator-completed
epoch is idempotent, including after that suspension has legitimately been
resumed.

Wall expiry uses the existing `daemon_shutdown` runner-session cancellation
path. Invocation and completed-token exhaustion prevent another daemon unit.
The daemon summary, `status`, run/trace projections, and `doctor` expose
bounded budget identity, limits, counters, status, terminal reason, and
overshoot.

## Lock Ownership

The daemon holds `.millrace/daemon.lock` while it owns the loop. The lock is
diagnostic-only: it records bounded local ownership information but is not a
cross-process control channel. Public control uses the challenged lifecycle
endpoint. An ambiguous stale lock still requires explicit operator inspection;
Millrace never removes or adopts it automatically.

## SIGINT And SIGTERM

On POSIX systems, `SIGINT` and `SIGTERM` request an orderly daemon shutdown.
When a runner session is active, the coordinator first persists the primary
`daemon_shutdown` cancellation request, then performs the same truthful
cooperative/terminate/kill/transport-cleanup sequence used by other
cancellation reasons.

The daemon does not report a clean signal stop until owned worker and reader
work has reached `complete` or `not_required` cleanup. If cleanup cannot be
proved, the stop summary reports `runner_session_orphan_risk`. A second
terminal signal cannot replace the first accepted completion.

Signals are operator mechanics. Normal loop code records one coalesced synthetic
local-signal stop operation outside the signal handler; the handler itself only
sets flags. Public clients use `daemon stop`, never a raw PID fallback. The
accepted-stop fence is durable before event delivery and prevents new daemon
unit/claim/session/start admission even if the listener response or event is lost.
Already accepted work can complete, cancel and clean up; manual authority and
another runtime scope are not silently stopped.

## Stop Summaries

JSON stop output reports bounded counters, `stopped_reason`, `last_result`,
diagnostics, and the affected `runner_session` when available. That session
projection includes session identity and fencing, selected adapter kind,
cancellation reason/phase, cleanup, completion/application status, orphan risk,
mechanical grace constants, and the last persisted cancellation
operation/result when available. It does not claim static operation support
that a live handle has not proved. Human output remains compact.

Stop summaries are read-only projections. They do not repair state, replace a
session, or create workflow meaning.

Use `millrace status`, `millrace runs show RUN_ID`,
`millrace trace show RUN_ID`, and `millrace doctor` after shutdown. For event
history, use the finite
`millrace runs follow RUN_ID --after-sequence N` projection.

The public lifecycle summary is retained independently of the legacy command
output. Every owned or startup-reconciliation session has immutable fence and
cleanup rows, linked by complete counts and SHA-256 chains. Normalized rows avoid
an execution-count ceiling from a growing summary JSON object. Public session
pages contain at most 50 rows. Use `next_after_session` with
`--after-session N --expected-source-revision REV`; changed revisions refuse the
continuation. A `role` distinguishes newly owned work from startup reconciliation.
Each page exposes pending, complete, not-required, orphan-risk or unknown cleanup
without private runner locators, endpoint paths or payloads.

`shutdown_complete` means listener teardown and retained per-session cleanup
results have been recorded. It does not mean the process has exited, and unknown
cleanup remains explicit. `stopped_clean` additionally requires the exact stored
process birth to be absent and all retained cleanup to be complete/not-required.
Missing socket, missing lock, or another process with the same PID cannot prove
clean shutdown. Missing/current/history/result contradictions refuse observation
and admission without repair. Existing receipts survive exit and replacement;
replaying an old stop never signals or stops the replacement incarnation.

The candidate proves local process/socket and offline Codex-wrapper boundaries.
A foreign-UID predicate fixture is not a real second-account test. PID/birth
mismatch fixtures are not actual PID reuse. Real Millforge/provider and released
OS integration remain separate qualification gates.

Daemon record status, target/default-plan/source revision and affected-session
page are one durable store snapshot. Exact process and fresh listener observations
are separate; `consistency` states this distinction. `readiness` records the
durable state and its daemon/source revision, with a fresh challenge required.
`runtime_progress` captures the latest newly accepted workflow transition in a
daemon-owned runtime persistence transaction. Readiness, challenge, refused
transitions/signals, sidecar telemetry and unchanged state persistence do not
advance it. The capture and runtime rows commit or roll back together. Each
capture adds one daemon history revision per transaction, not per poll.

`last_runtime_progress_at` is the Unix nanosecond wall clock sampled during that
transaction, not a provider event timestamp or a claim that a tool is currently
healthy. Clock adjustments may move this time backwards; source/daemon revision
and transition order establish ordering. A crash before commit retains the prior
capture; a crash after an external effect but before durable observation cannot
claim that effect as progress. `observed_at_ns` remains the separate read time.

Evidence is scoped by the outer exact daemon identity and carries its capture
source/daemon revisions and zero-based retained transition order. SHA-256 over
canonical JSON arrays binds `(transition record ID, input ID, input kind)` and,
when applicable, `(run ID, run generation, run fencing token, plan fingerprint)`.
Digests avoid copying arbitrarily long input identities into lifecycle records.
`session_snapshot` is the associated run's current session fence at capture;
it describes that transaction snapshot, not provider telemetry or necessarily
the session targeted by every transition in a multi-transition transaction.
Retained reads validate these links against durable transition, governance, run
and session authority under the existing deadline and row/output limits. Long
linked identities are hashed in 4 KiB UTF-8 chunks, with a deadline check between
chunks; the retained projection never copies their full bytes.
Session presence is checked, not merely a non-null fence's contents. The latest
registered dispatch for the associated run before the capture source revision
must match `session_snapshot`; later registrations do not change old captures.
Capture registers an already-existing current session through the same immutable
session history before writing its progress event when needed. An unrelated
workflow transition has no run/session attribution. Claim transitions use the
immutable run creator input link because their governance record describes the
unclaimed activation and has no run ID. A run claim before any session exists
therefore carries a run fence with a legitimately null session snapshot.

New incarnations report `no_work_yet` with a null timestamp until capture. Legacy
records without the field remain `unavailable` (`legacy_capture_absent`), with
no migration or invented timestamps. An old incarnation can gain evidence only
on a newly captured transition. Stop acceptance alone does not advance progress;
accepted cleanup transitions may advance it until final shutdown. Wrong/stale
incarnation writers and runtime writes after final shutdown are rejected.
These are source semantics; installed and released qualification remains
separate.

Local macOS socket tests accept `MILLRACE_TEST_SOCKET_ROOT` as an owned short
fixture parent. Without that environment variable they use pytest's `tmp_path`.
Each case creates and removes its own random child directory. The Mac Mini check
launcher supplies this parent under its private workspace. The source tests do
not depend on an MVP sibling-directory layout. Exact process tests require
supported macOS primitives, and positive socket fixtures require a pathname that
fits `sun_path[104]`. Unsupported long paths are tested as typed refusals.
Public affected-session pages cap both item count and encoded bytes. Continuation
retains the complete source-revision fence even when wide Unicode IDs reduce the
number of items below 50. Internal final cleanup still reads every retained fence.

Repair1 validates the runtime identity and both nullable plan fingerprints in
every retained event before snapshot, clean classification or relaunch. Validation
checks required fields, primitive types, digest syntax and finite text/revision
bounds. Historical runtime versions and schema/build identities need not equal
the installed tuple. Missing or malformed evidence refuses without rewriting it.

Signal observation snapshots the event before durably recording received operator
signals. A signal arriving during the durable stop read therefore cannot produce
a true exit decision without recorded stop evidence. Every loop-context exit also
records any received signal after restoring the previous handlers. This includes
terminal results, readiness refusal, budget return and exceptions without another
stop read. Repeated received signals coalesce once. These normal-code checks do
not change the first accepted completion/cancellation result or perform SQLite
work in the signal handler.
