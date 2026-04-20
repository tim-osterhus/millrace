# Millrace Runtime Lifecycle Diagram

This is the dense, implementation-accurate lifecycle chart for the shipped
default runtime configuration:

- mode: `standard_plain`
- planning loop: `planning.standard`
- execution loop: `execution.standard`

The README embeds a simplified version. This file keeps the fuller chart that
tracks startup, scheduling, result application, recovery routing, and Arbiter
activation more faithfully. It is rendered as `stateDiagram-v2` to keep the
full state machine denser than the README view.

```mermaid
stateDiagram-v2
    [*] --> BootstrapWorkspace

    state "Bootstrap workspace contract" as BootstrapWorkspace
    state "Load runtime config" as LoadConfig
    state "Acquire workspace lock" as AcquireLock
    state "Build watcher session" as BuildWatcher
    state "Compile active mode and loops into a frozen plan" as CompilePlan
    state "Load snapshot and recovery counters" as LoadSnapshot
    state "Reconcile stale or impossible state" as ReconcileStartup
    state "Persist running snapshot and startup events" as PersistStartup

    state "Drain mailbox commands" as DrainMailbox
    state "Config reload requested?" as ConfigReloadCheck <<choice>>
    state "Rebuild watcher session and recompile frozen plan" as RebuildWatcherAndRecompile
    state "Consume watcher events and normalize ideas inbox into queued specs" as ConsumeWatcher
    state "Refresh queue depths" as RefreshQueue1
    state "Stop requested?" as StopCheck <<choice>>
    state "Paused?" as PauseCheck <<choice>>
    state "Idle with paused outcome" as IdlePaused
    state "Run reconciliation" as ReconcileTick
    state "Refresh queue depths again" as RefreshQueue2
    state "Active stage already set?" as ActiveStageCheck <<choice>>
    state "Claim next work item\nincident -> spec -> task precedence" as ClaimWork <<choice>>
    state "Claimed spec is root spec with lineage?" as RootSpecCheck <<choice>>
    state "Open closure target and snapshot idea/root-spec contracts" as OpenClosureTarget
    state "Completion behavior eligible?" as CompletionEligible <<choice>>
    state "Active state invalid?" as InvalidActiveCheck <<choice>>
    state "Clear stale active state" as ClearStaleActive
    state "Active stage now set?" as FinalActiveCheck <<choice>>
    state "Idle with no_work outcome" as IdleNoWork
    state "Build stage request from active stage" as BuildStageRequest
    state "Persist snapshot, status, counters, and events" as PersistTick
    state "Reset idle state, release lock, and stop runtime" as StopRuntime

    state "auditor" as Auditor
    state "planner" as Planner
    state "manager" as Manager
    state "mechanic" as Mechanic
    state "Runtime applies planning result\nnormalize, persist, route, update state" as ApplyPlanningResult

    state "builder" as Builder
    state "checker" as Checker
    state "fixer" as Fixer
    state "doublechecker" as Doublechecker
    state "troubleshooter" as Troubleshooter
    state "consultant" as Consultant
    state "updater" as Updater
    state "Runtime applies execution result\nnormalize, persist, route, update state" as ApplyExecutionResult

    state "arbiter\nrequest_kind = closure_target" as Arbiter
    state "Runtime applies Arbiter result\npersist verdict paths, update target state, route next action" as ApplyArbiterResult

    BootstrapWorkspace --> LoadConfig
    LoadConfig --> AcquireLock
    AcquireLock --> BuildWatcher
    BuildWatcher --> CompilePlan
    CompilePlan --> LoadSnapshot
    LoadSnapshot --> ReconcileStartup
    ReconcileStartup --> PersistStartup
    PersistStartup --> DrainMailbox

    DrainMailbox --> ConfigReloadCheck
    ConfigReloadCheck --> RebuildWatcherAndRecompile: yes
    ConfigReloadCheck --> ConsumeWatcher: no
    RebuildWatcherAndRecompile --> ConsumeWatcher
    ConsumeWatcher --> RefreshQueue1
    RefreshQueue1 --> StopCheck
    StopCheck --> StopRuntime: stop requested
    StopCheck --> PauseCheck: continue
    PauseCheck --> IdlePaused: paused
    PauseCheck --> ReconcileTick: running
    IdlePaused --> PersistTick
    ReconcileTick --> RefreshQueue2
    RefreshQueue2 --> ActiveStageCheck
    ActiveStageCheck --> BuildStageRequest: yes
    ActiveStageCheck --> ClaimWork: no

    ClaimWork --> Auditor: planning incident
    ClaimWork --> RootSpecCheck: planning spec
    ClaimWork --> Builder: execution task
    ClaimWork --> CompletionEligible: nothing claimable

    RootSpecCheck --> OpenClosureTarget: root spec with lineage
    RootSpecCheck --> Planner: no closure target open
    OpenClosureTarget --> Planner

    CompletionEligible --> Arbiter: yes
    CompletionEligible --> InvalidActiveCheck: no
    InvalidActiveCheck --> ClearStaleActive: yes
    InvalidActiveCheck --> FinalActiveCheck: no
    ClearStaleActive --> FinalActiveCheck
    FinalActiveCheck --> BuildStageRequest: yes
    FinalActiveCheck --> IdleNoWork: no
    IdleNoWork --> PersistTick

    BuildStageRequest --> Auditor: active = auditor
    BuildStageRequest --> Planner: active = planner
    BuildStageRequest --> Manager: active = manager
    BuildStageRequest --> Mechanic: active = mechanic
    BuildStageRequest --> Builder: active = builder
    BuildStageRequest --> Checker: active = checker
    BuildStageRequest --> Fixer: active = fixer
    BuildStageRequest --> Doublechecker: active = doublechecker
    BuildStageRequest --> Troubleshooter: active = troubleshooter
    BuildStageRequest --> Consultant: active = consultant
    BuildStageRequest --> Updater: active = updater
    BuildStageRequest --> Arbiter: active = arbiter

    Auditor --> ApplyPlanningResult
    Planner --> ApplyPlanningResult
    Manager --> ApplyPlanningResult
    Mechanic --> ApplyPlanningResult

    ApplyPlanningResult --> PersistTick: AUDITOR_COMPLETE / set active = planner
    ApplyPlanningResult --> PersistTick: PLANNER_COMPLETE / set active = manager
    ApplyPlanningResult --> PersistTick: MANAGER_COMPLETE / clear active and return to idle boundary
    ApplyPlanningResult --> PersistTick: MECHANIC_COMPLETE / resume metadata stage (default planner)
    ApplyPlanningResult --> PersistTick: blocked planning / set active = mechanic if attempts remain
    ApplyPlanningResult --> PersistTick: blocked planning / clear active and persist blocked planning state when exhausted

    Builder --> ApplyExecutionResult
    Checker --> ApplyExecutionResult
    Fixer --> ApplyExecutionResult
    Doublechecker --> ApplyExecutionResult
    Troubleshooter --> ApplyExecutionResult
    Consultant --> ApplyExecutionResult
    Updater --> ApplyExecutionResult

    ApplyExecutionResult --> PersistTick: BUILDER_COMPLETE / set active = checker
    ApplyExecutionResult --> PersistTick: CHECKER_PASS / set active = updater
    ApplyExecutionResult --> PersistTick: CHECKER FIX_NEEDED / set active = fixer if budget remains
    ApplyExecutionResult --> PersistTick: CHECKER FIX_NEEDED / route recovery when fix budget is exhausted
    ApplyExecutionResult --> PersistTick: FIXER_COMPLETE / set active = doublechecker
    ApplyExecutionResult --> PersistTick: DOUBLECHECK_PASS / set active = updater
    ApplyExecutionResult --> PersistTick: DOUBLECHECK FIX_NEEDED / set active = fixer if budget remains
    ApplyExecutionResult --> PersistTick: DOUBLECHECK FIX_NEEDED / route recovery when fix budget is exhausted
    ApplyExecutionResult --> PersistTick: UPDATE_COMPLETE / clear active and return to idle boundary
    ApplyExecutionResult --> PersistTick: blocked execution / set active = troubleshooter if attempts remain
    ApplyExecutionResult --> PersistTick: blocked execution / set active = consultant when recovery is exhausted
    ApplyExecutionResult --> PersistTick: TROUBLESHOOT_COMPLETE / resume metadata stage (default builder)
    ApplyExecutionResult --> PersistTick: CONSULT_COMPLETE / resume metadata stage (default troubleshooter)
    ApplyExecutionResult --> PersistTick: CONSULT NEEDS_PLANNING / enqueue planning incident and clear active
    ApplyExecutionResult --> PersistTick: CONSULT BLOCKED / clear active and persist blocked execution state

    Arbiter --> ApplyArbiterResult
    ApplyArbiterResult --> PersistTick: ARBITER_COMPLETE / close target and clear active
    ApplyArbiterResult --> PersistTick: REMEDIATION_NEEDED / keep target open and enqueue planning incident
    ApplyArbiterResult --> PersistTick: BLOCKED / keep target open and persist blocked planning state

    PersistTick --> DrainMailbox
    StopRuntime --> [*]
```

Key invariants preserved by this chart:

- compile happens at startup and again only on explicit config reload
- planning and execution are separate claim domains inside one scheduler, not
  concurrent lanes
- the runtime applies stage results and mutates authoritative state after each
  execution; stages do not own queue mutation directly
- `manager`, `updater`, and successful Arbiter outcomes return the runtime to
  an idle or claim boundary for the next tick
- Arbiter is a completion-behavior activation path, not a normal queued work
  item handoff
