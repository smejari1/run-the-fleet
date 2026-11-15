"use strict";

const $ = (id) => document.getElementById(id);
const NS = "http://www.w3.org/2000/svg";
const colors = ["#cb8744", "#548998", "#8675aa"];
let state = null;
let selection = null;
let busy = false;
let actionQueue = Promise.resolve();
let pendingActions = 0;
let actionRevision = 0;
let actionError = "";
let generation = null;
let comparisonBusy = false;
let mapReady = false;
let mapSignature = "";
let project;
let whyJob = null;
let whyRequest = 0;
let feedBusy = false;
const tractorElements = new Map();
const fieldElements = new Map();

function svg(tag, attributes = {}, text = "") {
  const element = document.createElementNS(NS, tag);
  for (const [key, value] of Object.entries(attributes)) element.setAttribute(key, value);
  if (text) element.textContent = text;
  return element;
}
function textElement(tag, text, className) {
  const element = document.createElement(tag);
  element.textContent = text;
  if (className) element.className = className;
  return element;
}
function timeLabel(minutes) {
  return `${String(Math.floor(minutes / 60)).padStart(2, "0")}:${String(Math.floor(minutes % 60)).padStart(2, "0")}`;
}
function select(kind, id) {
  selection = { kind, id };
  render(state);
}
function clickable(element, label, handler) {
  element.setAttribute("tabindex", "0");
  element.setAttribute("role", "button");
  element.setAttribute("aria-label", label);
  element.addEventListener("click", handler);
  element.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") { event.preventDefault(); handler(); }
  });
}
function buildMap(data) {
  for (const id of ["roads", "fields", "depot", "routes", "tractors"]) $(id).replaceChildren();
  tractorElements.clear(); fieldElements.clear();
  const nodes = [data.depot, ...data.fields];
  const minX = Math.min(...nodes.map((n) => n.x)), maxX = Math.max(...nodes.map((n) => n.x));
  const minY = Math.min(...nodes.map((n) => n.y)), maxY = Math.max(...nodes.map((n) => n.y));
  const scale = Math.min(820 / Math.max(1, maxX - minX), 520 / Math.max(1, maxY - minY));
  const offsetX = (1000 - (maxX - minX) * scale) / 2;
  const offsetY = (700 - (maxY - minY) * scale) / 2;
  project = (node) => [offsetX + (node.x - minX) * scale, offsetY + (node.y - minY) * scale];
  for (const edge of data.edges) {
    const [x1, y1] = project(data.nodes[edge.a]), [x2, y2] = project(data.nodes[edge.b]);
    $("roads").append(svg("line", { x1, y1, x2, y2, stroke: "#d1d8c6", "stroke-width": 8, "stroke-linecap": "round" }), svg("line", { x1, y1, x2, y2, stroke: edge.blocked ? "#bf5d49" : "#e7ebdf", "stroke-width": edge.blocked ? 5 : 4, "stroke-dasharray": edge.blocked ? "10 7" : "none" }));
  }
  for (const field of data.fields) {
    const [x, y] = project(field);
    const group = svg("g", { transform: `translate(${x} ${y})`, class: "field-node", "data-field": field.id });
    const base = svg("rect", { x: -49, y: -31, width: 98, height: 62, rx: 6, fill: "#d5dfbc", stroke: "#a9bc8d", "stroke-width": 1.3, class: "field-base" });
    const progress = svg("rect", { x: -47, y: -29, width: 0, height: 58, rx: 4, fill: "#9fbc7e" });
    const label = svg("text", { x: 0, y: -3, "text-anchor": "middle", fill: "#4d6340", "font-size": 14, "font-weight": 700 }, field.id);
    const percent = svg("text", { x: 0, y: 16, "text-anchor": "middle", fill: "#6f815b", "font-size": 10 }, "0% worked");
    group.append(base, progress, svg("rect", { x: -47, y: -29, width: 94, height: 58, rx: 4, fill: "url(#furrows)", "pointer-events": "none" }), label, percent,
      svg("text", { x: 0, y: 46, "text-anchor": "middle", fill: "#79876b", "font-size": 10 }, field.name));
    clickable(group, `${field.id}, ${field.name}`, () => select("field", field.id));
    $("fields").append(group); fieldElements.set(field.id, { group, progress, percent });
  }
  const [dx, dy] = project(data.depot);
  const depot = svg("g", { transform: `translate(${dx} ${dy})` });
  depot.append(svg("rect", { x: -28, y: -22, width: 56, height: 44, rx: 7, fill: "#fffdf5", stroke: "#b8bca5", "stroke-width": 1.5 }),
    svg("path", { d: "M-15 -4L0 -15L15 -4V12H-15ZM-5 12V0H5V12", fill: "#a09878", stroke: "#807b62", "stroke-width": 1.5 }),
    svg("text", { x: 0, y: 39, "text-anchor": "middle", fill: "#7b7e62", "font-size": 10, "letter-spacing": 1 }, "DEPOT"));
  $("depot").append(depot);
  for (const [index, tractor] of data.tractors.entries()) {
    const group = svg("g", { class: "tractor-node tractor-position", "data-tractor": tractor.id, color: colors[index % colors.length] });
    const title = svg("title");
    const broken = svg("text", { x: 17, y: -16, fill: "#ba4939", "font-size": 24, "font-weight": 700, visibility: "hidden" }, "×");
    group.append(title, svg("circle", { r: 29, fill: "white", opacity: .75, class: "selection-ring" }),
      svg("circle", { r: 22, fill: "#ffffffe8", stroke: colors[index % colors.length], "stroke-width": 1.5 }),
      svg("use", { href: "#tractor-symbol", x: -19, y: -17, width: 38, height: 34 }),
      svg("rect", { x: -24, y: 24, width: 48, height: 18, rx: 4, fill: "white", stroke: "#dce2d2" }),
      svg("text", { x: 0, y: 37, "text-anchor": "middle", fill: "#43533a", "font-size": 10, "font-weight": 700 }, tractor.id), broken);
    clickable(group, `${tractor.id}, ${tractor.name}`, () => select("tractor", tractor.id));
    $("tractors").append(group); tractorElements.set(tractor.id, { group, title, broken });
  }
  mapReady = true;
}

function fillOptions(id, items) {
  const select = $(id);
  const signature = JSON.stringify(items);
  if (select.dataset.options === signature) return;
  const previous = select.value;
  select.replaceChildren(...items.map(([value, label]) => {
    const option = textElement("option", label);
    option.value = value;
    return option;
  }));
  if (items.some(([value]) => value === previous)) select.value = previous;
  select.dataset.options = signature;
}

function renderQueue(data) {
  const rows = $("job-rows"); rows.replaceChildren();
  const filter = $("queue-filter").value;
  const rank = { PENDING: 0, ASSIGNED: 1, IN_PROGRESS: 2, DONE: 3 };
  const jobs = [...data.jobs].filter((job) => filter === "all" || (filter === "done" ? job.state === "DONE" : job.state !== "DONE"));
  jobs.sort((a, b) => (rank[a.state] - rank[b.state]) || (a.submitted_at - b.submitted_at) || a.id.localeCompare(b.id));
  $("queue-count").textContent = `${data.jobs.filter((job) => job.state !== "DONE").length} open / ${data.jobs.length} total`;
  for (const job of jobs) {
    const row = document.createElement("tr");
    let status = job.submitted_at > data.time ? "Scheduled" : job.state.toLowerCase().replaceAll("_", " ");
    if (job.state === "DONE" && job.late) status = "Done · late";
    else if (job.at_risk && job.state !== "DONE") status += " · at risk";
    for (const value of [job.id, job.type, job.field, job.priority, timeLabel(job.deadline), job.tractor || "—"]) row.append(textElement("td", String(value)));
    const whyButton = textElement("button", "Why?", "why-button");
    whyButton.type = "button"; whyButton.title = `Why is ${job.id} planned this way?`;
    whyButton.addEventListener("click", () => showWhy(job.id));
    row.firstChild.append(whyButton);
    const progressCell = document.createElement("td");
    const progress = document.createElement("progress");
    progress.max = job.acres_total; progress.value = job.acres_done;
    progress.setAttribute("aria-label", `${job.id} acres complete`);
    progressCell.append(progress, textElement("span", `${job.acres_done.toFixed(1)} / ${job.acres_total} ac`, "progress-caption"));
    const statusCell = document.createElement("td");
    const badge = textElement("span", status, "job-status");
    badge.classList.toggle("at-risk", job.at_risk && job.state !== "DONE");
    badge.classList.toggle("done", job.state === "DONE");
    statusCell.append(badge);
    if (job.recovery_count) {
      const recovery = job.recovery;
      const label = recovery.to_tractor ? `Resumed by ${recovery.to_tractor}` : "Awaiting recovery";
      const recoveryBadge = textElement("span", label, "recovery-badge");
      recoveryBadge.title = `${recovery.from_tractor} stopped at minute ${recovery.at}; ${recovery.acres_done.toFixed(1)} completed acres preserved. ${job.recovery_count} breakdown(s).`;
      statusCell.append(recoveryBadge);
    }
    row.append(progressCell, statusCell); rows.append(row);
  }
  if (!jobs.length) {
    const row = document.createElement("tr"), cell = textElement("td", "No jobs in this view.", "empty-queue");
    cell.colSpan = 8; row.append(cell); rows.append(row);
  }
}

function renderOperator(data) {
  fillOptions("event-tractor", data.tractors.map((tractor) => [tractor.id, tractor.name]));
  const pendingJobs = data.jobs.filter((job) => job.state === "PENDING" && job.submitted_at <= data.time);
  fillOptions("priority-job", pendingJobs.map((job) => [job.id, `${job.id} · ${job.type} ${job.field} · priority ${job.priority}`]));
  fillOptions("event-road", data.edges.map((edge) => [JSON.stringify([edge.a, edge.b]), `${edge.a} ↔ ${edge.b}${edge.blocked ? " · blocked" : " · open"}`]));
  const road = data.edges.find((edge) => JSON.stringify([edge.a, edge.b]) === $("event-road").value);
  const blocked = data.edges.filter((edge) => edge.blocked).length;
  $("road-count").textContent = blocked ? `${blocked} of ${data.edges.length} roads blocked` : "All roads open";
  const selectedTractor = data.tractors.find((tractor) => tractor.id === $("event-tractor").value);
  const ended = data.time >= data.horizon;
  $("block-road").disabled = ended || !road || road.blocked;
  $("reopen-road").disabled = ended || !road || !road.blocked;
  $("breakdown").disabled = ended || !selectedTractor || selectedTractor.state === "BROKEN";
  $("repair").disabled = ended || !selectedTractor || selectedTractor.state !== "BROKEN";
  $("apply-priority").disabled = ended || pendingJobs.length === 0;
  const notice = data.notice?.startsWith("Ready.")
    ? "Demo jobs loaded. Start playback or inject an event." : (data.notice || "");
  if ($("operator-notice").textContent !== notice) $("operator-notice").textContent = notice;
  renderQueue(data);
}

function renderComparison(result) {
  const container = $("comparison-results"); container.replaceChildren(); container.hidden = false;
  const table = document.createElement("table"); table.className = "job-table";
  const head = document.createElement("thead"), heading = document.createElement("tr");
  for (const title of ["Metric", "FCFS", "Nearest feasible", "Coordinator", "Δ vs FCFS", "Δ vs nearest"]) heading.append(textElement("th", title));
  head.append(heading); table.append(head);
  const body = document.createElement("tbody");
  const metrics = { jobs_completed: "Jobs completed", jobs_on_time: "Jobs on time", deadline_misses: "Deadline misses", priority_weighted_on_time: "Priority-weighted on time", acres_completed: "Acres worked", travel_minutes: "Fleet travel minutes", idle_minutes: "Fleet idle minutes", makespan: "Last completion minute", safety_violations: "Rejected unsafe proposals", duplicate_or_lost_work: "Work consistency errors" };
  const format = (number) => number === null ? "—" : Number.isInteger(number) ? String(number) : number.toFixed(2);
  for (const [key, label] of Object.entries(metrics)) {
    const row = document.createElement("tr");
    const percentages = ["fcfs", "nearest"].map((baseline) => result.baseline_changes[baseline][key].percent);
    const changes = percentages.map((percent) => percent === null ? "n/a" : `${percent > 0 ? "+" : ""}${percent.toFixed(1)}%`);
    for (const value of [label, format(result.fcfs[key]), format(result.nearest[key]), format(result.coordinator[key]), ...changes]) row.append(textElement("td", value));
    body.append(row);
  }
  table.append(body); container.append(table);
  $("comparison-note").textContent = `Full shift replay, including operator actions captured through shift ${timeLabel(result.captured_at)}. ${result.summary}`;
}

function renderFleet(data) {
  $("fleet-cards").replaceChildren();
  for (const [index, tractor] of data.tractors.entries()) {
    const color = colors[index % colors.length];
    const card = textElement("button", "", "tractor-card");
    card.classList.toggle("selected", selection?.kind === "tractor" && selection.id === tractor.id);
    card.addEventListener("click", () => select("tractor", tractor.id));
    const title = textElement("div", "", "tractor-title");
    const icon = svg("svg", { viewBox: "-20 -17 40 34", style: `color:${color}`, "aria-hidden": "true" });
    icon.append(svg("use", { href: "#tractor-symbol", x: -20, y: -17, width: 40, height: 34 }));
    const status = textElement("span", tractor.state.toLowerCase(), "status");
    status.classList.toggle("broken", tractor.state === "BROKEN");
    title.append(icon, textElement("strong", tractor.name), status);
    if (tractor.name !== tractor.id) title.insertBefore(textElement("span", tractor.id, "tractor-id"), status);
    const job = data.jobs.find((item) => item.id === tractor.job);
    const description = tractor.waiting_reason || (job ? `${job.id} · ${job.type} ${job.field}` : tractor.action === "REFUEL" ? "Refuelling at depot" : tractor.state === "BROKEN" ? "Stopped · work released" : "Waiting for work");
    const fuel = textElement("div", "", "fuel-row"), track = textElement("div", "", "fuel-track"), fill = textElement("div", "", "fuel-fill");
    const fraction = Math.max(0, Math.min(100, tractor.fuel / tractor.fuel_capacity * 100));
    fill.style.width = `${fraction}%`; fill.style.background = fraction < 25 ? "#c79c54" : color;
    track.append(fill); fuel.append(textElement("span", "Fuel"), track, textElement("span", `${Math.round(fraction)}%`, "fuel-number"));
    card.append(title, textElement("p", description, "tractor-job"), fuel); $("fleet-cards").append(card);
  }
}

function renderSelection(data) {
  if (!selection) return;
  const panel = $("selection"); panel.replaceChildren(textElement("p", "ON THE MAP", "eyebrow"));
  if (selection.kind === "field") {
    const field = data.fields.find((item) => item.id === selection.id);
    if (!field) return;
    panel.append(textElement("h3", `${field.id} · ${field.name}`),
      textElement("p", `${field.acres} acres · ${Math.round(field.progress * 100)}% of planned work complete`));
    for (const job of field.jobs) panel.append(textElement("p", `${job.id} ${job.type}: ${job.acres_done.toFixed(1)} / ${job.acres_total} acres · ${job.submitted_at > data.time ? "scheduled" : job.state.toLowerCase().replaceAll("_", " ")}`));
  } else {
    const tractor = data.tractors.find((item) => item.id === selection.id);
    const job = data.jobs.find((item) => item.id === tractor.job);
    const name = tractor.name === tractor.id ? tractor.name : `${tractor.id} · ${tractor.name}`;
    panel.append(textElement("h3", name), textElement("p", `Equipment: ${tractor.implements.map((item) => item.replaceAll("_", " ")).join(", ")}`),
      textElement("p", job ? `${job.id} at ${job.field}: ${job.acres_done.toFixed(1)} / ${job.acres_total} acres complete.` : tractor.state === "BROKEN" ? "Broken down. Its unfinished work is available for another compatible tractor." : "No active field job."));
    if (tractor.est_finish !== null) panel.append(textElement("p", `Planned finish: shift ${timeLabel(tractor.est_finish)}.`));
    if (tractor.waiting_reason) panel.append(textElement("p", tractor.waiting_reason));
  }
}

function render(data) {
  if (!data) return;
  const signature = JSON.stringify([data.depot, data.fields.map((field) => [field.id, field.x, field.y]), data.tractors.map((tractor) => [tractor.id, tractor.name]), data.edges]);
  if (!mapReady || signature !== mapSignature) { buildMap(data); mapSignature = signature; }
  state = data;
  $("safety-active").textContent = data.safety?.enabled ? "Independent Safety Agent active" : "Checking Safety Agent…";
  $("safety-rejections").textContent = `Rejected proposals: ${data.metrics.safety_violations}`;
  $("safety-warnings").textContent = `Warnings: ${data.safety?.warnings ?? 0}`;
  if (generation !== data.generation) {
    generation = data.generation; selection = null;
    closeWhy();
    $("plan-feed").replaceChildren(textElement("li", "Loading plan updates…", "plan-empty"));
    $("dispatcher").value = data.dispatcher;
    $("comparison-results").hidden = true;
    $("comparison-note").textContent = "Replay the full shift with all three dispatchers, including every job, breakdown and road closure added so far. This does not change your live run.";
    $("selection").replaceChildren(textElement("p", "ON THE MAP", "eyebrow"), textElement("h3", "A working view of your farm"), textElement("p", "Select a field or tractor to inspect it. Run the demo jobs and inject incidents below."));
  }
  $("dispatcher-label").textContent = { coordinator: "Coordinator", fcfs: "FCFS baseline", nearest: "Nearest feasible" }[data.dispatcher];
  $("clock").replaceChildren(document.createTextNode(`${timeLabel(data.time)} `), textElement("small", `/ ${timeLabel(data.horizon)}`));
  $("shift-progress").style.width = `${data.time / data.horizon * 100}%`;
  $("play").textContent = data.running ? "Ⅱ Pause" : data.time >= data.horizon ? "Shift finished" : "▶ Start";
  $("play").disabled = data.time >= data.horizon;
  $("step").disabled = data.running || data.time >= data.horizon;
  $("reset").disabled = false;
  $("playback-status").textContent = data.time >= data.horizon ? "Shift complete" : data.running ? "● Running" : "Ⅱ Paused";
  $("map-count").textContent = `${data.fields.length} fields`;
  const operations = [...new Set(data.jobs.map((job) => job.type))].sort();
  $("job-types").replaceChildren(...operations.map((name) => textElement("span", name, "operation-chip")));
  $("speed-label").textContent = `${data.speed} min/s`;
  if (document.activeElement !== $("speed")) $("speed").value = data.speed;
  for (const field of data.fields) {
    const element = fieldElements.get(field.id);
    element.progress.setAttribute("width", Math.max(0, Math.min(94, field.progress * 94)));
    element.percent.textContent = `${Math.round(field.progress * 100)}% worked`;
    element.group.classList.toggle("selected", selection?.kind === "field" && selection.id === field.id);
  }
  $("routes").replaceChildren();
  for (const [index, tractor] of data.tractors.entries()) {
    const element = tractorElements.get(tractor.id);
    let [x, y] = project(tractor);
    // Fan out icons sharing a location, so all three remain selectable.
    const colocated = data.tractors.filter((item) => Math.hypot(item.x - tractor.x, item.y - tractor.y) < 3);
    if (colocated.length > 1) { const rank = colocated.findIndex((item) => item.id === tractor.id); x += (rank - (colocated.length - 1) / 2) * 46; y -= 30; }
    element.group.style.transform = `translate(${x}px, ${y}px)`;
    element.group.classList.toggle("selected", selection?.kind === "tractor" && selection.id === tractor.id);
    element.broken.setAttribute("visibility", tractor.state === "BROKEN" ? "visible" : "hidden");
    element.title.textContent = `${tractor.id} ${tractor.name} · ${tractor.state} · ${Math.round(tractor.fuel)}% fuel`;
    if (tractor.route.length > 1) {
      const points = [project(tractor), ...tractor.route.slice(1).map((id) => project(data.nodes[id]))];
      $("routes").append(svg("polyline", { points: points.map((p) => p.join(",")).join(" "), fill: "none", stroke: colors[index % colors.length], "stroke-width": 3, "stroke-dasharray": "7 6", opacity: .7, "stroke-linecap": "round" }));
    }
  }
  renderFleet(data); renderSelection(data); renderOperator(data);
  $("jobs-completed").replaceChildren(document.createTextNode(`${data.metrics.jobs_completed} `), textElement("small", `/ ${data.jobs.length}`));
  $("acres").textContent = data.metrics.acres_completed.toFixed(1);
  $("available").replaceChildren(document.createTextNode(`${data.tractors.filter((item) => item.state !== "BROKEN").length} `), textElement("small", `/ ${data.tractors.length}`));
  $("travel").textContent = data.metrics.travel_minutes.toFixed(1);
  $("error").hidden = !(data.error || actionError); $("error").textContent = data.error || actionError;
  $("connection").textContent = "Local simulation connected"; $("connection-dot").className = "connected";
}

function showError(error) {
  $("connection").textContent = "Connection interrupted"; $("connection-dot").className = "";
  $("error").hidden = false; $("error").textContent = error.message;
}
async function postApi(path, body) {
  const response = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || "The action could not be completed.");
  return data;
}
function enqueueAction(task) {
  pendingActions += 1; actionRevision += 1;
  actionQueue = actionQueue.then(async () => {
    actionError = "";
    try { await task(); }
    catch (error) { actionError = error.message; $("error").hidden = false; $("error").textContent = actionError; }
    finally { pendingActions -= 1; }
  });
  return actionQueue;
}
function control(action, extra = {}) {
  return enqueueAction(async () => {
    const data = await postApi("/api/control", { action, ...extra });
    if (action === "reset") { mapReady = false; selection = null; }
    render(data);
  });
}
async function refresh() {
  if (!busy && pendingActions === 0) {
    busy = true;
    const revision = actionRevision;
    try {
      const response = await fetch("/api/state", { cache: "no-store" });
      if (!response.ok) throw new Error("Cannot reach the simulation. Check that the local server is running.");
      const data = await response.json();
      if (revision === actionRevision) render(data);
    } catch (error) { showError(error); }
    finally { busy = false; }
  }
  setTimeout(refresh, 300);
}
$("play").addEventListener("click", () => control(state?.running ? "pause" : "start"));
$("step").addEventListener("click", () => control("step"));
$("reset").addEventListener("click", () => control("reset", { dispatcher: $("dispatcher").value }));
$("speed").addEventListener("input", () => { $("speed-label").textContent = `${$("speed").value} min/s`; });
$("speed").addEventListener("change", () => control("speed", { speed: Number($("speed").value) }));
$("queue-filter").addEventListener("change", () => { if (state) renderQueue(state); });
$("event-tractor").addEventListener("change", () => { if (state) renderOperator(state); });
$("event-road").addEventListener("change", () => { if (state) renderOperator(state); });
$("apply-priority").addEventListener("click", () => {
  const job = $("priority-job").value, priority = Number($("priority-value").value);
  enqueueAction(async () => render(await postApi("/api/event", { type: "change_priority", job, priority })));
});
for (const [id, type] of [["block-road", "block_route"], ["reopen-road", "unblock_route"]]) {
  $(id).addEventListener("click", () => {
    const [a, b] = JSON.parse($("event-road").value);
    enqueueAction(async () => render(await postApi("/api/event", { type, a, b })));
  });
}
for (const [id, type] of [["breakdown", "breakdown"], ["repair", "repair"]]) {
  $(id).addEventListener("click", () => {
    const tractor = $("event-tractor").value;
    enqueueAction(async () => render(await postApi("/api/event", { type, tractor })));
  });
}
$("compare").addEventListener("click", () => {
  if (comparisonBusy) return;
  comparisonBusy = true; $("compare").disabled = true; $("compare").textContent = "Comparing…";
  enqueueAction(async () => {
    try { renderComparison(await postApi("/api/compare", {})); }
    finally { comparisonBusy = false; $("compare").disabled = false; $("compare").textContent = "Run comparison"; }
  });
});
refresh();

// Phase 5: plain-language explanations ("Why?" and the plan-updates feed).
function closeWhy() {
  whyJob = null;
  whyRequest += 1;
  $("why-panel").hidden = true;
}
function whyHeader(title) {
  const close = textElement("button", "Close"); close.type = "button";
  close.addEventListener("click", closeWhy);
  return [close, textElement("h3", title)];
}
async function showWhy(jobId) {
  whyJob = jobId;
  const panel = $("why-panel");
  panel.replaceChildren(...whyHeader(`Why? · ${jobId}`), textElement("p", "Loading explanation…"));
  panel.hidden = false;
  await renderWhy();
  if (whyJob === jobId) panel.scrollIntoView({ behavior: "smooth", block: "nearest" });
}
async function renderWhy() {
  if (!whyJob) return;
  const jobId = whyJob, requestId = ++whyRequest, requestedGeneration = generation;
  const panel = $("why-panel");
  const isCurrent = () => whyJob === jobId && requestId === whyRequest && requestedGeneration === generation;
  try {
    const response = await fetch(`/api/why/${encodeURIComponent(jobId)}`, { cache: "no-store" });
    if (!response.ok) throw new Error(response.status === 404
      ? "The explanation is unavailable. The running server may need updating, or this job was removed by Reset."
      : "The explanation could not be loaded. Please retry.");
    const data = await response.json();
    if (!isCurrent() || data.generation !== requestedGeneration) return;
    panel.replaceChildren(...whyHeader(`Why? · ${data.title}`), textElement("p", data.status, "why-status"),
      ...data.lines.map((line) => textElement("p", line)));
    panel.hidden = false;
  } catch (error) {
    if (!isCurrent()) return;
    const retry = textElement("button", "Retry"); retry.type = "button";
    retry.addEventListener("click", renderWhy);
    panel.replaceChildren(...whyHeader(`Why? · ${jobId}`), textElement("p", error.message), retry);
    panel.hidden = false;
  }
}
async function refreshExplanations() {
  if (!feedBusy) {
    feedBusy = true;
    try {
      const response = await fetch("/api/explanations", { cache: "no-store" });
      if (!response.ok) throw new Error("Plan updates are unavailable from the running server. Retrying…");
      {
        const data = await response.json();
        const list = $("plan-feed");
        if (data.generation === generation) list.replaceChildren(...(data.feed.length ? data.feed.map((item) => {
          const li = document.createElement("li");
          if (item.highlight) li.classList.add("highlight");
          li.append(textElement("time", item.clock), textElement("span", item.text));
          return li;
        }) : [textElement("li", "No decisions yet. Press Start to run the shift.", "plan-empty")]));
        await renderWhy();
      }
    } catch (error) {
      $("plan-feed").replaceChildren(textElement("li", error.message, "plan-empty"));
      await renderWhy();
    }
    finally { feedBusy = false; }
  }
  setTimeout(refreshExplanations, 1000);
}
refreshExplanations();

// Phase 6: comparison headline + bar chart, drawn after the comparison table.
function renderComparisonChart(result) {
  const container = $("comparison-results");
  const names = [["fcfs", "FCFS"], ["nearest", "Nearest feasible"], ["coordinator", "Coordinator"]];
  const coord = result.coordinator, fcfs = result.fcfs;
  const pct = (a, b) => (b ? Math.round((100 * (a - b)) / Math.abs(b)) : 0);
  const signed = (n) => `${n > 0 ? "+" : ""}${n}`;
  const minutes = (d) => `${d > 0 ? "+" : d < 0 ? "−" : ""}${Math.abs(d).toFixed(Math.abs(d) < 10 ? 1 : 0)} min`;
  const headline = document.createElement("div"); headline.className = "compare-headline";
  const cards = [
    ["Jobs completed", `${coord.jobs_completed}`, `FCFS ${fcfs.jobs_completed}`, coord.jobs_completed >= fcfs.jobs_completed],
    ["On time", `${coord.jobs_on_time}`, `FCFS ${fcfs.jobs_on_time}`, coord.jobs_on_time >= fcfs.jobs_on_time],
    ["Travel", minutes(coord.travel_minutes - fcfs.travel_minutes), `${signed(pct(coord.travel_minutes, fcfs.travel_minutes))}% vs FCFS`, coord.travel_minutes <= fcfs.travel_minutes],
    ["Idle", minutes(coord.idle_minutes - fcfs.idle_minutes), `${signed(pct(coord.idle_minutes, fcfs.idle_minutes))}% vs FCFS`, coord.idle_minutes <= fcfs.idle_minutes],
  ];
  for (const [label, value, note, good] of cards) {
    const card = document.createElement("div"); card.className = `compare-card ${good ? "good" : "bad"}`;
    card.append(textElement("span", label), textElement("strong", value), textElement("small", note));
    headline.append(card);
  }
  const chart = document.createElement("div"); chart.className = "compare-chart";
  const groups = [["jobs_on_time", "Jobs on time (higher is better)"], ["travel_minutes", "Fleet travel minutes (lower is better)"], ["idle_minutes", "Fleet idle minutes (lower is better)"]];
  for (const [key, label] of groups) {
    const group = document.createElement("div"); group.className = "bar-group";
    group.append(textElement("p", label, "bar-title"));
    const max = Math.max(...names.map(([n]) => result[n][key] || 0), 1);
    for (const [n, title] of names) {
      const value = result[n][key] || 0;
      const row = document.createElement("div"); row.className = `bar-row ${n}`;
      const track = document.createElement("div"); track.className = "bar-track";
      const fill = document.createElement("div"); fill.className = "bar-fill"; fill.style.width = `${(100 * value) / max}%`;
      track.append(fill);
      row.append(textElement("span", title, "bar-label"), track, textElement("span", Number.isInteger(value) ? String(value) : value.toFixed(0), "bar-value"));
      group.append(row);
    }
    chart.append(group);
  }
  container.prepend(headline, chart);
}
{
  const drawTable = renderComparison;
  // Keep the existing table renderer and add the chart above it.
  renderComparison = (result) => { drawTable(result); renderComparisonChart(result); };
}
