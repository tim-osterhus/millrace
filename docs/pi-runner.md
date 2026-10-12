# Pi RPC runner in the private v0.22.4 candidate

The coordinated Core, Plus, and meta source candidate targets 0.22.4 and adds
explicit Pi RPC selection. Published v0.22.3 remains the public release; v0.22.4
is unpublished and held for real-model Campaign Monitor results and final
release checks. Millforge remains the default runner.

The installed scripted-provider evidence belongs to retained predecessor
artifacts: Core 0.22.4.dev6+pi.local01 and Plus 0.22.4.dev5+pi.local01. Those
runs cover Darwin and one pinned Ubuntu 24.04 x86_64 installation, including one
five-stage LAD route. They do not establish qualification of final 0.22.4
artifacts, every LAD stage, real-model behavior, production token accounting, or
a general sandbox. The Campaign Monitor's current real-model work is still a
separate gate.

## Selected authority and installation

Pi is an external installation. Core does not install or update Node or Pi and
imports without either. A workflow must explicitly select adapter kind pi_rpc;
this does not change workflow authority or the Millforge default. The Plus
candidate includes an opt-in Pi LAD workflow. A component-free Pi binding must
declare the complete supported and granted unrestricted filesystem-read,
filesystem-write, and process-execution requirements. The optional bounded
runner_report is narrative observation only. The selected plan continues to own
legal terminal outcomes, routes, conditions, and exact artifact schemas.

The pinned comparison installation uses @earendil-works/pi-coding-agent 0.87.0,
Node 24.18.0, protocol pi-0.87.0-jsonl.v1, the upstream read, bash, edit, and
write tools, no tool approvals, RPC without session persistence, xhigh thinking,
and a fresh process for every attempt. The exact Pi package tree, shrinkwrap,
Node binary, loader, runtime, templates, and selected profile are hash-checked.
The pin identifies bytes; it does not authenticate a publisher or create a
sandbox.

Under explicit v2/v3 group accounting, a successful stock edit may contain
both an `edits` array and a legacy `oldText`/`newText` pair. Pinned Pi appends
that pair to a prepared copy after emitting the original tool-call arguments.
Core checks the combined replacement count against the exact native success
sentence, while retaining declaration/result correlation and strict edit
shapes. Malformed successful results remain uncertain, and acceptance still
requires the complete settled lifecycle and process-closure evidence.

Under these explicit group-accounting profiles, a completed read, edit, or
write error with exactly one text block is tool feedback. Missing-file reads,
edit mismatches, permission failures, and native argument-validation errors
can be followed by further work and a valid final result. The original error
bit and text stay intact; no same-path retry or error-message matching is
required. This supersedes the earlier permanent file-error taint. It does not
establish task success: result correlation, settlement, final candidate
validation, and process closure still apply. Legacy v1 file errors retain
their existing uncertainty rule.

Keep the existing settings exactly as pinned. Discovery, extensions, packages,
skills, prompt templates, themes, project trust, telemetry, analytics, and cache
warming are disabled. Upstream retries and compaction remain enabled as recorded
in settings. The adapter adds no repair prompt, follow-up, session restore, or
automatic replacement run. Tool access is unrestricted at the user's account
level. Do not describe this profile as a sandbox.

The examples in examples/pi-profile.json and examples/pi-adapters.json are
relocation templates for the scripted-provider baseline. They contain host paths
that must be replaced and a path-bound digest that must be recomputed. They do
not represent the local Bonsai profile or the installed v2 observer. Keep the
credential value as a reference to the PI_RPC_API_KEY environment variable;
never write its value into a profile, package asset, work item, or command line.
Core passes only the fixed environment allowlist, derived attempt directories,
and the named credential to Pi.

The local adapter config and profile must be regular files within their byte
bounds. Profile, template, Pi package, Node, and observer identities are checked
before spawn. Templates must be exactly auth.json, models.json, and settings.json
without symlinks; auth is an empty object and settings match the selected
profile. Core creates a fresh private attempt directory per call with separate
home, agent, session, and temporary directories. It removes only its
identity-checked child. Drift, ambient attempt contents, missing files or
credentials, and unsafe paths refuse.

## Foreground process-group profile

The foreground qualification profile is pi-rpc-profile.v2 with the exact
trusted-posix-group-v1 observer installed by Core. Its platform is the selected
Darwin or Linux host and its bridge identity must match the installed package.
The observer is added explicitly through Node's preload argument and a private
inherited pipe; environment variables cannot silently enable it. The shell's
ordinary stdio does not expose that private descriptor.

For every bash call, the observer must correlate Pi RPC evidence with the actual
spawn and declared command, record a numeric root status and genuine stdout and
stderr ends, and show the covered POSIX process group absent both at root exit
and at an independent parent observation. A survivor, ambiguous call, timeout,
cancellation, corrupt or incomplete event stream, or premature stream close
disqualifies successful workflow-result application. A later passing command
cannot repair uncertainty. A normal numeric nonzero exit can be task feedback
when all other evidence is complete.

Cleanup is evaluated separately from accepted work. A failed or cancelled
attempt may be fully disposed when Pi is reaped, readers are joined, known
covered groups are absent, the private channel is complete, and the owned
attempt directory is removed. Missing ownership evidence can leave orphan_risk
and retained attempt material. The observer never signals an unverified group or
infers ownership from a reused PID.

The authored transport ceilings permit at most 500,000 events and 256 MiB of
cumulative stdout or private observer data per stream. The per-event limit is
separate. A profile selects its actual, possibly lower limits, and Core passes
the same limits to the Pi RPC reader and trusted observer. Exceeding any limit
still refuses completion and can leave orphan risk; larger ceilings do not
change the ownership or closure proof.

The local Pi invocation timeout accepts 1–9,600 seconds. The effective timeout
is the lower of that local ceiling and the selected runner binding's timeout;
the outer work deadline may stop an invocation sooner.

An orphan-risk completion retains a bounded diagnostic with cleanup predicate
booleans, stream byte/event counts and fixed error codes, and the observer's
content-free counts. Cleanup facts contain no prompts, tool output, credentials,
stderr text, paths, or process IDs. A validated adapter-error diagnostic can
also be retained, including the rejected Pi final-candidate evidence described
below. These diagnostics help identify the failed boundary; they do not relax
cleanup or permit retry of a lost session.

This is trusted-workspace accounting for covered process groups, not arbitrary
descendant containment. Descendants that escape the covered group, remote jobs,
external side effects, and malicious interference by the same user are outside
the guarantee. Benign children that briefly outlive a shell can also cause
refusal. Pi reconciliation, active native pause/resume controls, and owner-loss
reattachment or continuation are unsupported.

The separate opt-in `pi-rpc-profile.v3` is Linux-only. It retains the same
stock Pi, Node, bundle, observer, Bash invocation, and RPC checks, and pins a
private Python subreaper by exact installed path and SHA-256. The subreaper
starts before Pi, reports Pi's actual PID on a private channel, and remains the
sole reaper for Pi and adopted local descendants. After Pi exits, it signals
only current direct children using pidfds, waits for all children to be reaped,
and reports bounded disposal facts. It never signals a saved numeric process
group. A shell's known-present group may be disposed at this boundary. An
unknown group probe, unexpected process control, or damaged observer stream
remains execution uncertainty. A signalled shell also remains uncertain,
except for the explicitly selected guarded timeout described below.
The profile cannot be selected
on Darwin or with an unpinned supervisor.

An optional `bash_timeout` field in a v3 profile selects the qualified Bash
timeout extension by canonical path and exact SHA-256
`09a688ec90c18afb52aa7a028a3ec02a0ae74cb374df962e0f133d802483d775`.
Its shape is `{"extension": {"path": "/selected/bash_timeout.mjs",
"sha256": "..."}, "audit_path": "/selected/timeout-audit.jsonl"}`. The
audit must already be a canonical regular file owned by the invoking account,
with mode 0600, outside the attempt root. The adapter appends exactly
`-e <selected path>` after the fixed managed arguments and derives exactly
one extra environment key, `D_TIMEOUT_AUDIT_PATH`. Every prespawn rechecks
the extension bytes and audit identity. Discovery stays disabled and settings
extensions stay empty; arbitrary extension arguments and environment keys
remain refused. The extension preserves shorter positive Bash timeouts,
caps larger timeouts at 1800 seconds, and defaults missing or invalid timeouts
to 1800. For this selected guard only, one stock timeout control may qualify
as task feedback: its pinned timeout stack must target the active shell's
negative PID with SIGKILL before the tool ends, the shell must exit and close
with SIGKILL, both streams must end, and the error result must end with the
native timeout sentence for the exact effective seconds. Ordered observer/RPC
agreement and complete supervisor disposal remain mandatory. Duplicate,
abort, unknown, late, or wrong-target controls, unknown groups, premature
stream destruction, and unrelated uncertainty still refuse. No shared
uncertainty is cleared. This guard does not change the session invocation
deadline or v3
cleanup contract. It is currently a private campaign variant, not a claim
that the public 0.22.4 wheel supports this field.

V3 requires the supervisor's ordered, complete private report even for a
text-only attempt with no Bash spawns. A valid resource-disposal report may
support cleanup after cancellation or forced Pi termination, despite missing
observer bye or a nonzero Pi exit. It does not validate the aborted candidate
output. Normal candidate acceptance still requires Pi exit zero, complete
observer and RPC evidence, coherent settled lifecycle, unchanged model and
installation identity, and no uncertainty. The invocation deadline ends model
and tool work; a separate finite cleanup grace permits only stream draining,
termination, and reaping. Local descendants that leave this supervisor's
ancestry, remote jobs, external effects, and hostile same-user interference are
outside the guarantee.

## Local Bonsai profile shape under evaluation

The source config accepts one explicit local Bonsai shape under
qualification_scope loopback-bonsai2-openai-completions. This validation only
accepts a configuration shape; it is not evidence that the model, tools,
settlement, or usage have qualified. Campaign Monitor must complete its real
model checks before those claims are made.

The profile retains the same pinned Pi and Linux observer as the scripted
profile; v3 additionally requires its pinned Linux subreaper. It uses a numeric loopback endpoint of the form
http://127.0.0.1:<port>/v1, provider local, model id bonsai2-27b-pq2, context
window 262144, maximum output tokens 262144, and the fixed
bonsai2-pinned-1.0-0.95-20 sampling profile. The model service side is separately
pinned to Bonsai 2 27B PQ2_0 weights, Q4_0 key/value cache, one request slot,
262144-token context, and matching KV-bias rotation state.

The immutable models template keeps the local provider identity, text input,
zero-cost fields, and thinking-level map. Its provider compatibility declares
supportsDeveloperRole false and supportsReasoningEffort false. The model
compatibility uses max_tokens, chat-template thinking, and the enable_thinking
template variable; sampling parameters are temperature 1.0, top_p 0.95, and
top_k 20. Pi's xhigh setting enables the template's thinking variable. It is not
a native Bonsai reasoning grade. Recompute template and profile hashes after
relocating paths. The local endpoint must not include credentials, query strings,
or fragments.

The config validator intentionally restricts this special scope to those
provider, model, context, sampling, endpoint, and template semantics. A local
profile that passes validation is still unqualified until the real model has
completed the Monitor's stock-tool, settlement, usage, and near-window checks.
The code does not establish actual Bonsai tool quality or production provider
token accounting. The scripted usage mapping remains the only reviewed Pi token
mapping; Pi-reported counters are not billing truth. No Qwen profile is part of
this candidate's qualification.

## Results, budgets, and lifecycle

Only the exact final assistant JSON envelope is considered for result parsing:
marker, artifact_payload_candidate, and observation_payload_candidate. The
selected workflow and Core continue to validate markers, artifacts, routing,
conditions, and writeback. Model output cannot supply session identity,
capabilities, grants, routes, or schemas. A runner_report inside the bounded
observation is narrative evidence, not authority. Partial text, tool-only output,
invalid or oversized JSON, duplicate or unknown fields, and malformed lifecycle
evidence refuse.

Failures after successful Pi settlement retain a fixed `failure_phase` code
(`candidate_parse` or `evidence_normalization`) and a content-free
`failure_reason` code in the existing session completion diagnostic. Malformed
JSON, duplicate keys, wrong envelopes, invalid marker/payload/report types,
JSON bounds, and evidence-redaction key collisions remain distinguishable.
Other evidence-construction failures use `evidence_normalization_failed`;
exception messages and stack traces are not retained. The existing
`result_parse_failed`, `output_too_large`, and `cancelled` classifications stay
unchanged.

Native invocation failures retain `failure_phase=invocation`, an allowlisted
protocol reason (or an unclassified code), numeric stream/lifecycle counters,
and up to eight adapter-owned source frames. No exception messages, local
variables, raw stderr, or RPC contents enter this diagnostic. The existing
session-bound CAS preserves it after attempt cleanup and runtime reload;
classification, cancellation, strict parsing, and cleanup requirements stay
unchanged.

`lifecycle_uncertainties` includes the fixed process-observer refusal codes,
such as incomplete group evidence, premature stream disposal, and uncertain
process ownership. `lifecycle_uncertainty_count` counts all distinct reasons;
`lifecycle_unclassified_uncertainty_count` counts reasons outside the fixed
code list without retaining their text. These diagnostic counts supply no
execution or cleanup authority. A settled final event cannot identify an
earlier offending tool result when its raw events were not retained.

The native diagnostic also retains the last known event type and a small
structural history, recorded before lifecycle validation. It records only
fixed event/recovery enums, exact booleans, bounded counters, presence/type
flags, and the pre-event lifecycle state. Unknown event names and arbitrary
payloads are omitted. The history keeps at most eight recent events and is
capped at 4096 serialized bytes inside the existing 16-KiB bound for the full
completion diagnostic. Its shape status is not evidence that the lifecycle
accepted the event. No prompt, summary, message body, tool arguments/result,
or exception/error-message content is retained in this history.

### Intrinsic recovery boundaries

The parser follows the pinned Pi 0.87.0 lifecycle. Threshold compaction may
occur between a completed tool turn and the next turn while the same agent
run remains open. Its start and matching end must finish before another turn
or the agent end. Manual and overflow compaction require a closed agent run.
A successful overflow end may authorize one new run; threshold, aborted,
and failed compaction do not grant that continuation. Pi's exhausted overflow
recovery reports a failure end without another start after its single
compact-and-retry attempt; this exception requires the earlier overflow
attempt and the failed continuation.

Transient retry starts after a failed agent end with `willRetry=true`.
Attempts progress within one retry cycle. A successful retry end follows the
continued assistant's message end, before turn end; failed or cancelled
backoff closes the cycle without accepting earlier text. A later provider
failure can start a new cycle. Summarization retries are paired inside an
active compaction; branch-summary recovery is outside this single-prompt
adapter's command scope. Settlement refuses open recovery, unused
continuation, or unfinished tool/message evidence.

Recovery disables fallback usage accounting in every profile. The existing
admitted Linux supervisor profile additionally tracks bounded recovery receipts
and distinct assistant failures. A successful threshold compaction can follow
a completed tool turn in the same open run, then close its next successful
assistant/tool turn. It can also follow the successful final `agent_end`, then
settle with the same final assistant and no resumed turn. Both paths require a
paired stock result without abort, error, retry or summary retry. A transient
retry must cover each exact error assistant through progressive matched attempts,
a successful native retry end and complete resumed tool/turn aftermath. Failed,
aborted, unfinished, manual, other post-run, overflow and summary cycles refuse the
result. V1/V2 profiles retain sticky recovery refusal.

For retry, the exact source-owned null `context_edit` must omit the immediately
preceding failed assistant. After settlement, the client issues one supported
read-only `get_entries` query under the original transport and work limits.
It validates the two initial model/thinking metadata entries, exact observed
message and edit digests, the active ancestry and the failed target. Any
compaction entry must match the exact typed stock threshold result, including
summary, retained-entry link, token counts, usage and file metadata. The
compaction must copy the current observed system message, when present,
with its timestamp rebased to the exact compaction entry epoch milliseconds.
Threshold recovery also requires this final query and persisted compaction binding; its
actual summary usage contributes to the minimum final accounting check. An
unrelated session mutation refuses. The transcript remains memory-only and is discarded after
binding. No event-name or uncertainty-label allowlist supplies acceptance.

Parser damage permanently taints eligibility. The shared uncertainty set is
never cleared or reduced; every unrelated tool, observer, session, cancellation
and invocation uncertainty keeps its existing effect. Both initial and repeated
closure use the same predicate, require actual available/nonconflicting final
usage after recovery, and retain a failed closure permanently. Finite disposal
can finish process cleanup after the work deadline; it cannot finish recovery or
supply result eligibility. The strict candidate parser and selected-plan,
compiler, dispatch and durable evidence contracts are unchanged.

This implementation is source preparation with synthetic tests and independent
source review, pending installed positive qualification. Actual installed Linux A4
negative characterization completed threshold recovery and exposed the retry
omission gap. Its failed archive remains evidence. A7 validated the exact native
failed-assistant null edit, active branch, system message and RPC/observer links
against the unchanged prior artifact. New-artifact positive completion,
CAS/reload/replay, hostile refusal, cancellation/deadline controls and
model/campaign qualification remain separate gates. C02's original triggering
event was not retained and remains unknown.

Failure diagnostics include a bounded content-free recovery summary: fixed
reasons, counts, taint, unfinished-cycle and persisted-binding facts. It uses the
existing session-bound diagnostic CAS and supplies no result authority. Native
entry IDs, messages, error bodies and transcript contents are omitted.

When safe, `candidate_text` contains redacted final text as diagnostic evidence,
with `candidate_evidence_status` indicating complete or truncated retention.
JSON string literals are decoded for credential redaction and re-encoded;
duplicate keys and malformed outer structure can therefore remain visible
without converting the text into an accepted candidate. Redaction covers the
request policy, current configured credential, and credential of the
materialized attempt, before truncation. The excerpt is capped at 4096 bytes
of serialized JSON text; it is omitted for oversized results, ambiguous string
encoding, or diagnostic-redaction failure. A mapping-key collision in diagnostic
redaction uses a single `result_failure` list with phase/reason/omission codes.

The coordinator stores this evidence in its existing typed, session-bound CAS
diagnostic after disposal, including orphan-risk completion. It survives owned
attempt-directory removal and runtime reload. Rejected text supplies no marker,
artifact, observation, routing, retry, or cleanup authority. Raw RPC events,
prompts, credentials, and arbitrary exception contents are not saved by this
repair; narrative final-candidate text can contain project information and is
still subject to the configured redaction policy. A future failure can become
more diagnosable, but the unavailable sixth response from the closed October C
campaign cannot be reconstructed or assigned an exact trigger.

For the reviewed scripted profile, Pi input plus cache-read and cache-write
counts map to Core input, and Pi output maps to Core output; reasoning is already
included. Snapshots are not counted twice. Missing, malformed, or all-zero
post-prompt usage is unavailable. This mapping was reviewed for the scripted
profile only. The local Bonsai configuration shape does not extend it to real
provider accounting. Budgets remain admission and accounting controls, not a
guaranteed provider-side cutoff or invoice.

The attempt deadline covers setup, readiness, inference, final text, and
statistics. Event and output bounds apply throughout. Prompt acknowledgement
and agent_end are not completion; Pi must reach a coherent settled boundary,
match the final assistant text, and provide correlated statistics. Error,
aborted, or length-final messages cannot recover an earlier successful-looking
response.

Positive cleanup under v2 requires a complete lifecycle, actual Pi exit, pipe
EOF, joined readers, removed owned attempt resources, and complete evidence for
every covered bash process group. Safe built-in text reads, canonical edits, and
writes retain their own argument and result checks. A missing, ambiguous, or
surviving group disqualifies success. The older v1 profile has no group observer
and retains its narrower text-only success rule; bash does not qualify a v1
success. Unknown tools or events, unqualified tool results, abort, retry or compaction, lost
evidence, and failed closure make uncertainty sticky. This is not executable
test or build verification, rollback, or protection from arbitrary same-user
changes. Cancellation requests a correlated abort, then uses bounded
termination and disposal; an abort acknowledgement or parent exit does not
prove arbitrary descendant cleanup. A cancelled or failed attempt may already
have changed files.

The adapter does not reattach by PID, continue after owner death, or support
active Pi reconciliation. Existing eligible unstarted scheduler pause/resume is
separate from Pi active-session controls. See [runner-session architecture](runner-session-architecture.md)
and [compatibility](v0.22-compatibility.md) for the durable session and refusal
boundaries.

## Workflow recovery after a clean invocation timeout

Pi intrinsic recovery remains bounded by its selected adapter profile. If an
invocation still ends with a clean adapter timeout, the workflow may explicitly
select the generic runtime failure mapping described in
`runtime-failure-recovery.md`. That recovery uses the existing selected policy,
preserves failed-session diagnostics and creates ordinary recovery work without
manufacturing Pi output. Cancellation, lost sessions, incomplete cleanup and
unmapped failures retain their existing behavior.
