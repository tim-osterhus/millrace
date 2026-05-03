const state = {
  workspaces: [],
  activeWorkspaceId: null,
  summary: null,
  view: new URLSearchParams(window.location.search).get("view") === "flow" ? "flow" : "detail",
};

const $ = (id) => document.getElementById(id);

function setView(view) {
  state.view = view;
  $("app").dataset.view = view;
  $("detail-button").classList.toggle("active", view === "detail");
  $("flow-button").classList.toggle("active", view === "flow");
}

async function loadWorkspaces() {
  const response = await fetch("/api/workspaces");
  const payload = await response.json();
  state.workspaces = payload.workspaces || [];
  if (!state.activeWorkspaceId && state.workspaces.length > 0) {
    state.activeWorkspaceId = state.workspaces[0].id;
  }
  renderWorkspaces();
}

async function loadSummary() {
  if (!state.activeWorkspaceId) return;
  const response = await fetch(`/api/workspaces/${state.activeWorkspaceId}/summary`);
  state.summary = await response.json();
  renderSummary();
}

function renderWorkspaces() {
  $("workspace-list").innerHTML = state.workspaces
    .map((workspace) => {
      const active = workspace.id === state.activeWorkspaceId ? " active" : "";
      return `<button class="workspace-card${active}" data-workspace="${workspace.id}"><strong>${escapeHtml(workspace.name)}</strong><span>${escapeHtml(workspace.path)}</span></button>`;
    })
    .join("");
  document.querySelectorAll("[data-workspace]").forEach((button) => {
    button.addEventListener("click", () => {
      state.activeWorkspaceId = button.dataset.workspace;
      renderWorkspaces();
      loadSummary();
    });
  });
}

function renderSummary() {
  const summary = state.summary;
  if (!summary) return;
  $("status-workspace").textContent = summary.workspace.name;
  $("status-mode").textContent = summary.runtime.mode_id || "unknown";
  $("status-daemon").textContent = summary.daemon.state;
  $("status-plan").textContent = summary.compiled_plan.currentness;
  $("status-stage").textContent = summary.runtime.active_stage || "none";
  $("status-queue").textContent = totalIncoming(summary.queues);
  $("active-fields").innerHTML = fields({
    Daemon: summary.daemon.state,
    "Compiled plan": summary.compiled_plan.currentness,
    "Active stage": summary.runtime.active_stage || "none",
    "Active plane": summary.runtime.active_plane || "none",
    "Active item": summary.runtime.active_work_item_id || "none",
    "Active run": summary.runtime.active_run_id || "none",
    "Runtime elapsed": formatDuration(summary.runtime.elapsed_seconds),
    Baseline: summary.baseline.state,
  });
  $("queue-table").innerHTML = queueRows(summary.queues);
  renderGraph("detail-graph", summary, "detail");
  renderGraph("flow-graph", summary, "flow");
  $("runs-table").innerHTML = runRows(summary.recent_runs || []);
  $("active-run").innerHTML = fields({
    "Run ID": summary.runtime.active_run_id || "none",
    Plane: summary.runtime.active_plane || "none",
    Node: summary.runtime.active_node_id || "none",
    Stage: summary.runtime.active_stage_kind_id || "none",
  });
  $("work-item").innerHTML = fields({
    Kind: summary.runtime.active_work_item_kind || "none",
    ID: summary.runtime.active_work_item_id || "none",
  });
  $("artifact-list").innerHTML = artifactList(summary.recent_runs || []);
  $("governance").innerHTML = governanceFields(summary.usage_governance);
  $("arbiter").innerHTML = fields({
    "Closure open": String(summary.arbiter.closure_target_open),
    "Latest result": summary.arbiter.latest_result || "none",
    "Next stage": summary.arbiter.next_stage || "none",
    Status: summary.arbiter.status,
  });
  $("flow-plane").textContent = `Plane: ${summary.runtime.active_plane || "idle"}`;
  $("metric-queue").textContent = totalIncoming(summary.queues);
  $("metric-elapsed").textContent = formatDuration(summary.runtime.elapsed_seconds);
  $("metric-tokens").textContent = totalTokens(summary.recent_runs || []);
  $("metric-result").textContent = latestResult(summary.recent_runs || []);
  $("flow-intel").innerHTML = fields({
    "Active run": summary.runtime.active_run_id || "none",
    Node: summary.runtime.active_node_id || "none",
    Stage: summary.runtime.active_stage || "none",
    "Work item": summary.runtime.active_work_item_id || "none",
  });
  $("flow-plan").innerHTML = fields({
    "Plan ID": summary.compiled_plan.id || "none",
    Currentness: summary.compiled_plan.currentness,
    Mode: summary.compiled_plan.mode_id || summary.runtime.mode_id || "unknown",
  });
  $("flow-governance").innerHTML = governanceFields(summary.usage_governance);
  renderEvents(summary.events || []);
}

function fields(items) {
  return Object.entries(items)
    .map(([key, value]) => `<div><dt>${escapeHtml(key)}</dt><dd>${escapeHtml(String(value))}</dd></div>`)
    .join("");
}

function governanceFields(governance) {
  return fields({
    Enabled: String(governance.enabled),
    Paused: String(governance.paused),
    Blockers: String(governance.blocker_count),
    "Auto-resume": governance.auto_resume_possible ? "yes" : "no",
    Budget: governance.budget_status,
  });
}

function queueRows(queues) {
  return Object.entries(queues)
    .map(([kind, bucket]) => `<tr><td>${kind}</td><td>${bucket.incoming}</td><td>${bucket.active}</td><td>${bucket.done}</td><td>${bucket.blocked}</td></tr>`)
    .join("");
}

function renderGraph(targetId, summary, variant) {
  const activeRuns = new Map((summary.runtime.active_runs_by_plane || []).map((run) => [run.node_id, run]));
  const graphs = summary.graphs && summary.graphs.length ? summary.graphs : fallbackGraphs(summary);
  $(targetId).innerHTML = graphs
    .map((graph) => {
      const nodes = graph.nodes
        .map((node, index) => {
          const active = activeRuns.has(node.node_id) || node.node_id === summary.runtime.active_node_id;
          const edge = index < graph.nodes.length - 1 ? '<span class="edge">→</span>' : "";
          return `<span class="node${active ? " active" : ""}">${escapeHtml(node.label || node.node_id)}</span>${edge}`;
        })
        .join("");
      return `<section class="lane ${variant}"><div class="lane-title">${escapeHtml(graph.plane)} lane</div><div class="node-row">${nodes || "No graph data"}</div></section>`;
    })
    .join("");
}

function fallbackGraphs(summary) {
  const plane = summary.runtime.active_plane || "execution";
  const stage = summary.runtime.active_stage || "idle";
  return [{
    plane,
    nodes: [
      { node_id: "builder", label: "builder" },
      { node_id: stage, label: stage },
      { node_id: "updater", label: "updater" },
    ],
  }];
}

function runRows(runs) {
  return runs
    .map((run) => `<tr><td>${escapeHtml(run.run_id)}</td><td>${escapeHtml(run.stage || "none")}</td><td>${escapeHtml(run.result || run.status)}</td><td>${formatDuration(run.duration_seconds)}</td><td>${run.total_tokens || 0}</td></tr>`)
    .join("");
}

function artifactList(runs) {
  const artifacts = runs.flatMap((run) => run.artifacts || []).slice(0, 8);
  if (!artifacts.length) return "<li>No artifacts yet</li>";
  return artifacts.map((artifact) => `<li>${escapeHtml(artifact.path)}</li>`).join("");
}

function renderEvents(events) {
  $("event-count").textContent = `${events.length} events`;
  $("event-list").innerHTML = events
    .slice()
    .reverse()
    .map((event) => {
      const time = new Date(event.occurred_at).toLocaleTimeString();
      const subject = [event.plane, event.stage].filter(Boolean).join(".") || event.workspace_id;
      return `<div class="event-row"><span>${time}</span><b>${escapeHtml(event.event_type)}</b><span>${escapeHtml(subject)} ${escapeHtml(event.details || "")}</span></div>`;
    })
    .join("");
}

function totalIncoming(queues) {
  return Object.values(queues).reduce((total, bucket) => total + bucket.incoming, 0);
}

function totalTokens(runs) {
  return runs.reduce((total, run) => total + (run.total_tokens || 0), 0);
}

function latestResult(runs) {
  return runs.length ? runs[0].result || runs[0].status : "none";
}

function formatDuration(seconds) {
  if (seconds === null || seconds === undefined) return "idle";
  const safe = Math.max(0, Math.floor(seconds));
  const hours = String(Math.floor(safe / 3600)).padStart(2, "0");
  const minutes = String(Math.floor((safe % 3600) / 60)).padStart(2, "0");
  const secs = String(safe % 60).padStart(2, "0");
  return `${hours}:${minutes}:${secs}`;
}

function escapeHtml(value) {
  return value.replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#039;",
  }[char]));
}

$("detail-button").addEventListener("click", () => setView("detail"));
$("flow-button").addEventListener("click", () => setView("flow"));

setView(state.view);
loadWorkspaces().then(loadSummary);
setInterval(loadSummary, 1000);

