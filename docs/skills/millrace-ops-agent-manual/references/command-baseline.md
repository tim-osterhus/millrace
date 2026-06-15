# Command Baseline

## Contents

- Command forms
- Package and workspace upgrades
- Compile/status/run inspection
- Queue, incident, control, approval, skills, and config commands

## Command Forms

During source development, module form is acceptable:

```bash
uv run --extra dev python -m millrace_ai <command>
```

For workspace-local E2E deliverables, prefer the Python executable available in
the target environment. If docs say `python` but the host only has `python3`,
run the equivalent `python3 -m ...` command and report the portability mismatch.

In installed environments, use CLI form:

```bash
millrace <command>
```

## Package Updates Versus Workspace Upgrades

Package updates and workspace baseline upgrades are separate:

1. Update the installed runtime package with the environment package manager,
   for example `pip install -U millrace-ai==<version>`.
2. Verify runtime code with `millrace --version` or `millrace version`.
3. Run `millrace upgrade` to preview/apply managed workspace baseline updates
   under `<workspace>/millrace-agents/`.
4. Run `millrace compile validate` before resuming daemon work.

`millrace upgrade --apply` does not install or update the Python package.

## Core Commands

```bash
millrace init --workspace <workspace>
millrace version
millrace upgrade --workspace <workspace>
millrace upgrade --apply --workspace <workspace>
millrace upgrade --localize-removed <managed/path> --workspace <workspace>
millrace compile validate --workspace <workspace>
millrace compile show --workspace <workspace>
millrace compile graph --workspace <workspace>
millrace status --workspace <workspace>
millrace queue ls --workspace <workspace>
millrace queue show <work_item_id> --workspace <workspace>
millrace run daemon --max-ticks 1 --workspace <workspace>
millrace run daemon --monitor basic --workspace <workspace>
millrace run daemon --monitor none --monitor-log <path> --workspace <workspace>
millrace status watch --workspace <workspace>
millrace runs ls --workspace <workspace>
millrace runs show <run_id> --workspace <workspace>
millrace runs tail <run_id> --workspace <workspace>
millrace runs trace <run_id> --workspace <workspace>
millrace doctor --workspace <workspace>
```

## Queue And Incident Commands

```bash
millrace queue add-task <task.md|task.json> --workspace <workspace>
millrace queue add-probe <probe.md|probe.json> --workspace <workspace>
millrace queue add-spec <spec.md|spec.json> --workspace <workspace>
millrace queue add-idea <idea.md> --workspace <workspace>
millrace queue retry-blocked <work_item_id> --family <family_id> --reason "<reason>" --workspace <workspace>
millrace queue cancel <work_item_id> --kind task --reason "<reason>" --workspace <workspace>
millrace queue archive-blocked <task_id> --reason "<reason>" --workspace <workspace>
millrace queue supersede <old_task_id> --replacement <new_task_id> --reason "<reason>" --workspace <workspace>
millrace queue retarget-dependency <task_id> --from <old_dependency_id> --to <new_dependency_id> --reason "<reason>" --workspace <workspace>
millrace incident resolve <incident_id> --reason "<reason>" --workspace <workspace>
millrace incident cancel <incident_id> --reason "<reason>" --workspace <workspace>
millrace incident archive-invalid <filename> --reason "<reason>" --workspace <workspace>
```

## Control And Approval Commands

```bash
millrace control pause --workspace <workspace>
millrace control resume --workspace <workspace>
millrace control stop --workspace <workspace>
millrace planning retry-active --reason "<reason>" --workspace <workspace>
millrace approvals ls --workspace <workspace>
millrace approvals show <approval_id> --workspace <workspace>
millrace approvals approve <approval_id> --reason "<reason>" --workspace <workspace>
millrace approvals deny <approval_id> --reason "<reason>" --workspace <workspace>
```

Approvals route through the mailbox when a daemon owns the workspace and apply
directly when no daemon owns it.

## Modes, Model Aliases, Skills, And Config

```bash
millrace modes list
millrace modes show <mode_id>
millrace model-aliases list --workspace <workspace>
millrace model-aliases set <alias> --model <model> --thinking-level <level> --workspace <workspace>
millrace model-aliases assign-global <alias> --workspace <workspace>
millrace model-aliases assign-loop <loop_id> <alias> --workspace <workspace>
millrace model-aliases assign-stage <stage> <alias> --workspace <workspace>
millrace compile validate --mode efficient_learning_lad_mixed --workspace <workspace>
millrace compile validate --mode lad_codex_integrated --workspace <workspace>
millrace compile validate --mode blueprint_lad_codex --workspace <workspace>
millrace compile validate --mode blueprint_learning_lad_codex --workspace <workspace>
millrace skills ls --workspace <workspace>
millrace skills show <skill_id> --workspace <workspace>
millrace skills search <query> --workspace <workspace>
millrace skills install <skill_ref> --workspace <workspace>
millrace skills refresh-remote-index --workspace <workspace>
millrace skills create "<prompt>" --mode learning_lad_codex --workspace <workspace>
millrace skills improve <skill_id> --mode learning_lad_codex --workspace <workspace>
millrace skills promote <skill_id> --workspace <workspace>
millrace skills export <skill_id> --workspace <workspace>
millrace config show --workspace <workspace>
millrace config validate --workspace <workspace>
millrace config reload --workspace <workspace>
```

Use `millrace skills ...` only for optional skills workflows and learning-plane
skill requests. Ordinary task intake belongs in `millrace queue ...`.
