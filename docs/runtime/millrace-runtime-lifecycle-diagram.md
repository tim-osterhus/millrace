# Millrace Runtime Lifecycle Diagram

This is the dense, implementation-accurate lifecycle chart for the shipped
default runtime configuration:

- mode: `default_codex`
- planning loop: `planning.standard`
- execution loop: `execution.standard`

Learning-enabled modes (`learning_codex`, `learning_pi`) use the same planning
and execution topology and add `learning.standard`; this default-mode chart
omits that optional claim path except where noted.

The README embeds a simplified version. This file keeps the fuller chart that
tracks startup, scheduling, result application, recovery routing, and Arbiter
activation more faithfully.

## Overview

```mermaid
%%{init: {"flowchart": {"nodeSpacing": 24, "rankSpacing": 30, "curve": "linear"}} }%%
flowchart TB
    S1["Require initialized workspace baseline"] --> S2["Load runtime config"] --> S3["Acquire workspace lock"] --> S4["Build watcher session"] --> S5["Compile active mode + loops into compiled plan"] --> S6["Load snapshot + recovery counters"] --> S7["Reconcile stale or impossible state"] --> S8["Persist running snapshot + startup events"]

    S8 --> T1["Drain mailbox commands"] --> T2["Consume watcher events"] --> T3["Refresh queue depths"] --> T4{"Stop requested?"}
    T4 -- yes --> TStop["Release lock + stop"]
    T4 -- no --> TGov1["Evaluate usage governance"] --> T5{"Paused?"}
    T5 -- yes --> TPaused["Paused idle outcome"]
    T5 -- no --> T6["Run reconciliation"] --> T7["Refresh queue depths again"] --> T8{"Select next work"}

    T8 -- incident claim --> PIncident["Planning entry:<br/>auditor"]
    T8 -- spec claim --> TRoot{"Root spec with lineage?"}
    TRoot -- yes --> TOpen["Open closure target + snapshot contracts"] --> PSpec["Planning entry:<br/>planner"]
    TRoot -- no --> PSpec
    T8 -- active planning stage --> TGov2

    T8 -- task claim --> ETask["Execution entry:<br/>builder"]
    T8 -- active execution stage --> TGov2

    T8 -- learning request claim<br/>(learning modes) --> LEntry["Learning entry:<br/>analyst or targeted stage"]
    T8 -- active learning stage<br/>(learning modes) --> TGov2
    T8 -- nothing claimable --> T9{"Arbiter ready?"}
    T9 -- yes --> TGov2
    T9 -- no --> T10{"Active state invalid?"}
    T10 -- yes --> TClear["Clear stale active state"] --> T11{"Still active?"}
    T10 -- no --> T11
    T11 -- planning --> TGov2["Evaluate usage governance before dispatch"]
    T11 -- execution --> TGov2
    T11 -- arbiter --> TGov2
    T11 -- learning --> TGov2
    T11 -- none --> TIdle["no_work idle outcome"]
    TGov2 --> T12{"Paused?"}
    T12 -- yes --> TPaused
    T12 -- no, planning --> PActive
    T12 -- no, execution --> EActive
    T12 -- no, learning --> LActive
    T12 -- no, arbiter --> AEntry["Arbiter entry:<br/>closure_target"]

    PIncident --> TGov2
    PSpec --> TGov2
    PActive --> PApply["Apply planning result"]

    ETask --> TGov2
    EActive --> EApply["Apply execution result"]

    LEntry --> TGov2
    LActive --> LApply["Apply learning result"]

    AEntry --> AApply["Apply arbiter result"]

    PApply --> R1["Write stage-result artifacts"] --> R2["Route terminal result + apply router decision"] --> RUsage["Record usage + apply governance"] --> R3["Persist snapshot / status / counters / events"]
    EApply --> R1
    LApply --> R1
    AApply --> R1
    TPaused --> R3
    TIdle --> R3
    R3 --> T1
```

## Planning Loop Detail

```mermaid
stateDiagram-v2
    direction LR
    [*] --> ClaimIncident
    [*] --> ClaimSpec
    [*] --> ResumePlanning

    ClaimIncident --> Auditor
    ClaimSpec --> Planner
    ResumePlanning --> Manager
    ResumePlanning --> Mechanic

    Auditor --> Planner: AUDITOR_COMPLETE
    Auditor --> Mechanic: BLOCKED and mechanic attempts remain
    Auditor --> BlockedPlanning: BLOCKED and mechanic attempts exhausted

    Planner --> Manager: PLANNER_COMPLETE
    Planner --> Mechanic: BLOCKED and mechanic attempts remain
    Planner --> BlockedPlanning: BLOCKED and mechanic attempts exhausted

    Manager --> IdleBoundary: MANAGER_COMPLETE
    Manager --> Mechanic: BLOCKED and mechanic attempts remain
    Manager --> BlockedPlanning: BLOCKED and mechanic attempts exhausted

    Mechanic --> Planner: MECHANIC_COMPLETE resume metadata target default planner
    Mechanic --> Mechanic: BLOCKED and retry budget remains
    Mechanic --> BlockedPlanning: BLOCKED and retry budget exhausted

    IdleBoundary --> [*]
    BlockedPlanning --> [*]
```

## Execution Loop Detail

```mermaid
stateDiagram-v2
    direction LR
    [*] --> ClaimTask
    [*] --> ResumeExecution

    ClaimTask --> Builder
    ResumeExecution --> Checker
    ResumeExecution --> Fixer
    ResumeExecution --> Doublechecker
    ResumeExecution --> Troubleshooter
    ResumeExecution --> Consultant
    ResumeExecution --> Updater

    Builder --> Checker: BUILDER_COMPLETE
    Builder --> Troubleshooter: BLOCKED and troubleshoot attempts remain
    Builder --> Consultant: BLOCKED and troubleshoot budget exhausted

    Checker --> Updater: CHECKER_PASS
    Checker --> Fixer: FIX_NEEDED and fix budget remains
    Checker --> Troubleshooter: FIX_NEEDED or BLOCKED and recovery routing engages
    Checker --> Consultant: recovery budget exhausted

    Fixer --> Doublechecker: FIXER_COMPLETE
    Fixer --> Troubleshooter: BLOCKED and troubleshoot attempts remain
    Fixer --> Consultant: BLOCKED and troubleshoot budget exhausted

    Doublechecker --> Updater: DOUBLECHECK_PASS
    Doublechecker --> Fixer: FIX_NEEDED and fix budget remains
    Doublechecker --> Troubleshooter: FIX_NEEDED or BLOCKED and recovery routing engages
    Doublechecker --> Consultant: recovery budget exhausted

    Troubleshooter --> Builder: TROUBLESHOOT_COMPLETE resume metadata target default builder
    Troubleshooter --> Troubleshooter: BLOCKED and troubleshoot retry remains
    Troubleshooter --> Consultant: BLOCKED and troubleshoot budget exhausted

    Consultant --> Troubleshooter: CONSULT_COMPLETE resume metadata target default troubleshooter
    Consultant --> NeedsPlanning: NEEDS_PLANNING
    Consultant --> BlockedExecution: BLOCKED

    Updater --> IdleBoundary: UPDATE_COMPLETE
    Updater --> Troubleshooter: BLOCKED and troubleshoot attempts remain
    Updater --> Consultant: BLOCKED and troubleshoot budget exhausted

    NeedsPlanning --> [*]
    IdleBoundary --> [*]
    BlockedExecution --> [*]
```

## Arbiter Detail

```mermaid
flowchart LR
    A0["No planning claimable<br/>No execution claimable<br/>Completion behavior compiled<br/>Closure target ready<br/>No lineage work remains"] --> A1["arbiter<br/>request_kind = closure_target"]
    A1 -- ARBITER_COMPLETE --> A2["Close target<br/>stamp closed_at<br/>clear active stage"]
    A1 -- REMEDIATION_NEEDED --> A3["Keep target open<br/>persist verdict paths<br/>enqueue planning incident"]
    A1 -- BLOCKED --> A4["Keep target open<br/>persist verdict paths<br/>leave blocked planning state"]
```

## Notes

1. Require an initialized workspace baseline; create it with `millrace init`.
2. Load runtime config.
3. Acquire workspace lock.
4. Build watcher session.
5. Compile active mode and loops into a compiled plan.
6. Load snapshot and recovery counters.
7. Reconcile stale or impossible state.
8. Persist running snapshot and startup events.

- Drain mailbox commands first on every tick.
- Explicit config reload is what recompiles the compiled plan.
- Consume watcher events and normalize ideas into queued specs.
- Refresh queue depths, run stop and usage-governance pause checks, then
  reconcile.
- Refresh queue depths again before claim or activation.
- Re-check usage governance immediately before stage dispatch.
- Exactly one stage runs per tick at most.
- Usage governance can pause before claim/dispatch and records stage-result
  token usage after routing.
- Active stages can bypass fresh claim and go straight to request build.
- Claim precedence is planning incident -> planning spec -> execution task,
  then learning request when a learning loop is active.
- Root-spec claim opens the closure target and snapshots contracts.
- Arbiter activates only when no lineage work remains and closure is ready.
- Invalid active state is cleared before the runtime settles on `no_work`.
- Normalize and persist the stage result.
- Write stage-result artifacts.
- Route terminal status.
- Mark tasks, specs, or incidents done or blocked.
- Mark learning requests done or blocked when the learning plane is active.
- Update recovery counters and closure-target state.
- Record stage token usage into the governance ledger and apply any resulting
  between-stage pause.
- The runtime, not the stage, owns authoritative state mutation.

Key invariants preserved by this chart:

- compile happens at startup and again only on explicit config reload
- planning and execution are separate claim domains inside one scheduler, not
  concurrent lanes
- learning-enabled modes add learning requests and status markers, while the
  current tick executor still runs at most one active stage per tick
- the runtime applies stage results and mutates authoritative state after each
  execution; stages do not own queue mutation directly
- `manager`, `updater`, and successful Arbiter outcomes return the runtime to
  an idle or claim boundary for the next tick
- Arbiter is a completion-behavior activation path, not a normal queued work
  item handoff
