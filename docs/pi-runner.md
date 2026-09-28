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
content-free counts. It does not retain prompts, tool output, credentials,
stderr text, paths, or process IDs. These facts help identify which closure
check failed; they do not relax cleanup or permit retry of a lost session.

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
group. A shell's known-present group may be disposed at this boundary; an
unknown group probe, signalled shell, unexpected process control, or damaged
observer stream remains execution uncertainty. The profile cannot be selected
on Darwin or with an unpinned supervisor.

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
success. Unknown tools or events, errors, abort, retry or compaction, lost
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
