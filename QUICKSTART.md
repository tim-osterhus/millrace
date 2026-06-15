# Millrace Quickstart For Human Operators

Millrace is a local loop engineering framework. It helps you run long agent
work with durable state, visible progress, and recovery paths.

You can use Millrace directly, but the normal path is to ask an AI agent to set
it up and operate it for you. The agent handles the commands. You provide the
goal, the workspace, and the judgment calls.

## What Millrace Is

Millrace is installed as a Python package. It can be installed globally or in a
Python virtual environment.

After installation, Millrace is initialized inside a folder. That folder becomes
a Millrace workspace. The workspace contains the queue, runtime state, run
artifacts, agent instructions, approvals, and evidence files.

Millrace does not replace tools like Codex, Claude Code, or Pi. Those tools run
the agent stages. Millrace runs the loop around them.

## How You Use It

You choose a folder and a goal. Then you ask an agent to initialize Millrace in
that folder, validate the workspace, add the work, and start the daemon.

The daemon is the long-running Millrace process. It claims one eligible stage,
hands that stage to the right agent runner, records what happened, and then
decides what can run next.

The daemon should run outside the AI agent chat session. If the chat ends, the
daemon and workspace state should still exist.

## What Your Agent Does

Your agent should treat Millrace as an operator tool. It should set up the
workspace, start the daemon, monitor status, inspect run artifacts, handle
approvals, and report real blockers.

For longer runs, the agent should start the daemon inside a durable terminal
session such as `tmux`. This keeps the process alive and makes it easier to
reattach later.

A Millrace-specific durable terminal helper is planned, but `tmux` is the
normal practical choice today.

## What You Should Tell The Agent

Give the agent the information a careful operator would need:

- the folder or repository where Millrace should run;
- the goal you want Millrace to pursue;
- the workflow or mode to use, if you know it;
- any files, specs, or docs that matter;
- what the agent is allowed to change;
- what should require your approval;
- how often you want status updates.

You do not need to provide every command. The agent should use the Millrace
runtime docs and operator manual for that.

## What To Watch For

Millrace is governed automation, not magic autonomy. It gives agents structure,
state, and recovery paths, but it still needs good inputs and operator
judgment.

Watch for:

- stale or unclear goals;
- a workspace initialized in the wrong folder;
- approvals waiting for you;
- blocked work that needs a decision;
- daemon output that says validation failed;
- tasks that should be split before they run.

The useful thing about Millrace is that these states are visible. A blocked
loop should leave evidence, not disappear inside a chat transcript.

## Where To Go Next

Ask your agent to use these docs when operating Millrace:

- `docs/skills/millrace-ops-agent-manual/SKILL.md`
- `docs/skills/millrace-autonomous-delegation/SKILL.md`
- `docs/runtime/README.md`
- `docs/runtime/millrace-cli-reference.md`

For a deeper explanation of the runtime, read
`docs/millrace-technical-overview.md`.
