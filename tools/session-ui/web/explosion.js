"use strict";

// Explosion view page. The drawing is composed on the server (tools/explosion); this script only shows it, toggles
// assembled/exploded from data attributes, shows the selected part's evidence and posts the operator's layout decisions.
// Selection is announced on window as "anastylos:part-selected" with detail {job, label} (both null when cleared).

const params = new URLSearchParams(window.location.search);
const sessionId = params.get("session") || "";
let requestedJob = params.get("job") || "";
const state = { view: null, mode: "exploded", selected: null, busy: false };

const $ = (id) => document.getElementById(id);

async function call(path, body) {
  const response = await fetch(path, body === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ session: sessionId, ...body }),
  });
  const type = response.headers.get("Content-Type") || "";
  const payload = type.includes("json") ? await response.json() : await response.text();
  if (!response.ok) throw new Error((payload && payload.error) || `Request failed (${response.status}).`);
  return payload;
}

function fileUrl(name, job, mode) {
  const query = new URLSearchParams({ session: sessionId, name });
  if (job) query.set("job", job);
  if (mode) query.set("mode", mode);
  return `/api/explosion/file?${query}`;
}

function setState(text, isError = false) {
  const node = $("view-state");
  node.textContent = text;
  node.classList.toggle("error", isError);
}

function layerOf(job) {
  return state.view && state.view.manifest ? state.view.manifest.layers.find((l) => l.job === job) : null;
}

function announce() {
  const layer = state.selected && layerOf(state.selected);
  window.dispatchEvent(new CustomEvent("anastylos:part-selected", {
    detail: { job: layer ? layer.job : null, label: layer ? layer.label : null },
  }));
}

function select(job) {
  state.selected = job && layerOf(job) ? job : null;
  const url = new URL(window.location.href);
  if (state.selected) {
    url.searchParams.set("job", state.selected);
    requestedJob = state.selected;
  } else {
    url.searchParams.delete("job");
    requestedJob = "";
  }
  window.history.replaceState(null, "", url);
  for (const node of document.querySelectorAll(".ax-part, .ax-legend")) {
    node.classList.toggle("ax-selected", node.dataset.job === state.selected);
  }
  renderPanel();
  announce();
}

function applyMode() {
  const svg = $("svg-host").querySelector("svg");
  for (const mode of ["exploded", "assembled"]) $(`mode-${mode}`).setAttribute("aria-pressed", String(state.mode === mode));
  if (!svg) return;
  svg.dataset.mode = state.mode;
  for (const part of svg.querySelectorAll(".ax-part")) {
    const [x, y] = part.getAttribute(`data-${state.mode}-translate`).split(",");
    part.setAttribute("transform", `translate(${x} ${y})`);
  }
}

function showSvg(text) {
  const host = $("svg-host");
  host.replaceChildren();
  if (!text) return;
  const parsed = new DOMParser().parseFromString(text, "image/svg+xml");
  if (parsed.querySelector("parsererror")) throw new Error("The composed drawing could not be read.");
  host.appendChild(document.importNode(parsed.documentElement, true));
  applyMode();
}

function renderExcluded() {
  const list = $("excluded-list");
  list.replaceChildren();
  const manifest = state.view.manifest;
  for (const item of manifest ? manifest.excluded : []) {
    const li = document.createElement("li");
    li.textContent = `Not drawn: ${item.label} (${item.job}) - ${item.reason}`;
    list.appendChild(li);
  }
  for (const warning of manifest ? manifest.warnings : []) {
    const li = document.createElement("li");
    li.textContent = `Warning: ${warning}`;
    list.appendChild(li);
  }
}

function renderPanel() {
  const layer = state.selected && layerOf(state.selected);
  $("panel-empty").hidden = Boolean(layer);
  $("panel-body").hidden = !layer;
  if (!layer) return;
  const files = state.view.files[layer.job] || [];
  $("part-label").textContent = layer.label;
  const badge = $("part-status");
  badge.textContent = layer.status === "confirmed" ? "Confirmed" : "UNCONFIRMED";
  badge.className = `badge ${layer.status}`;
  const reasons = $("part-reasons");
  reasons.replaceChildren(...layer.reasons.map((reason) => Object.assign(document.createElement("li"), { textContent: reason })));
  $("part-binding").textContent = { confirmed: "confirmed by the operator", none: "not bound", stale: "stale (drawing changed)", unreadable: "unreadable", rejected: "rejected" }[layer.binding] || layer.binding;
  $("part-view-side").textContent = `${layer.view_side || "not set"}${layer.job_view_side && layer.job_view_side !== layer.view_side ? ` (drawing job: ${layer.job_view_side})` : ""}`;
  $("part-flip").textContent = layer.flip_horizontal ? "yes" : "no";
  $("part-rotation").textContent = `${layer.rotation_deg || 0}°`;
  $("part-part").textContent = `${layer.part || "-"} (${layer.job})`;
  $("part-sha").textContent = (layer.inputs["drawing.svg"] || {}).sha256 || "-";
  for (const [id, name] of [["link-rectified", "rectified.png"], ["link-overlay", "overlay.jpg"], ["link-drawing", "drawing.svg"]]) {
    const link = $(id);
    link.hidden = !files.includes(name);
    if (!link.hidden) link.href = fileUrl(name, layer.job);
  }
  const thumb = $("thumb-rectified");
  thumb.hidden = !files.includes("rectified.png");
  if (!thumb.hidden) thumb.src = fileUrl("rectified.png", layer.job);
  $("field-label").value = layer.label;
  $("field-view-side").value = layer.view_side || "";
  $("field-flip").checked = layer.flip_horizontal;
  const index = state.view.layout.layers.findIndex((l) => l.job === layer.job);
  $("move-up").disabled = index <= 0;
  $("move-down").disabled = index < 0 || index >= state.view.layout.layers.length - 1;
  $("unconfirm-button").disabled = !layer.confirmed_by_operator;
}

async function load(view) {
  state.view = view || await call(`/api/explosion?session=${encodeURIComponent(sessionId)}`);
  const { layout, manifest } = state.view;
  const empty = $("empty-note");
  $("propose-button").disabled = state.busy || !state.view.proposable_jobs.length;
  $("propose-button").textContent = layout ? "Add new drawings to the layout" : "Propose layout from drawings";
  renderExcluded();
  if (state.view.error) {
    showSvg("");
    empty.hidden = false;
    empty.textContent = state.view.error;
    return setState("The layout could not be used.", true);
  }
  if (!layout || !manifest.layers.length) {
    showSvg("");
    empty.hidden = false;
    empty.textContent = !state.view.proposable_jobs.length
      ? "No finished part drawing in this session yet. Nothing is drawn that has not been captured."
      : "No layout yet. Propose one from the existing drawings, then confirm each layer.";
    return setState("");
  }
  empty.hidden = true;
  showSvg(await call(fileUrl("explosion.svg", "", "exploded")));
  const open = manifest.layers.filter((l) => l.status !== "confirmed").length;
  setState(open ? `${open} of ${manifest.layers.length} layers unconfirmed.` : "All drawn layers confirmed.");
  if (state.selected && !layerOf(state.selected)) state.selected = null;
  if (!state.selected && requestedJob && layerOf(requestedJob)) state.selected = requestedJob;
  select(state.selected);
}

async function act(body) {
  state.busy = true;
  setState("Saving …");
  try {
    await load(await call("/api/explosion/layout", body));
  } catch (error) {
    setState(error.message, true);
  } finally {
    state.busy = false;
    $("propose-button").disabled = !state.view.proposable_jobs.length;
  }
}

function jobFromEvent(event) {
  const node = event.target.closest(".ax-part, .ax-legend");
  return node ? node.dataset.job : null;
}

function wire() {
  const host = $("svg-host");
  host.addEventListener("click", (event) => select(jobFromEvent(event)));
  host.addEventListener("keydown", (event) => {
    if ((event.key === "Enter" || event.key === " ") && jobFromEvent(event)) {
      event.preventDefault();
      select(jobFromEvent(event));
    }
  });
  for (const mode of ["exploded", "assembled"]) {
    $(`mode-${mode}`).addEventListener("click", () => { state.mode = mode; applyMode(); });
  }
  $("propose-button").addEventListener("click", () => act({ action: "propose" }));
  $("rotate-button").addEventListener("click", () => act({ action: "rotate", job: state.selected }));
  $("outlines-button").addEventListener("click", async () => {
    $("outlines-button").disabled = true;
    setState("Looking for parts on the sheet. This can take a minute.");
    try {
      const found = await call("/api/explosion/outlines", {});
      await load(found);
      const missing = found.not_drawn || [];
      if (missing.length) setState(`${missing.length} more thing(s) on the sheet could not be outlined; nothing was drawn for them.`, true);
    } catch (error) {
      setState(error.message, true);
    } finally {
      $("outlines-button").disabled = false;
    }
  });
  $("confirm-form").addEventListener("submit", (event) => {
    event.preventDefault();
    act({ action: "confirm", job: state.selected, label: $("field-label").value, view_side: $("field-view-side").value,
          flip_horizontal: $("field-flip").checked, confirmed: true });
  });
  $("unconfirm-button").addEventListener("click", () => act({ action: "confirm", job: state.selected, confirmed: false }));
  for (const [id, step] of [["move-up", -1], ["move-down", 1]]) {
    $(id).addEventListener("click", () => {
      const jobs = state.view.layout.layers.map((l) => l.job);
      const at = jobs.indexOf(state.selected);
      jobs.splice(at + step, 0, jobs.splice(at, 1)[0]);
      act({ action: "reorder", jobs });
    });
  }
}

async function start() {
  wire();
  window.anastylosExplosion = { selectedJob: () => state.selected };
  if (!sessionId) {
    setState("Open this page with ?session=<id>.", true);
    $("propose-button").disabled = true;
    return;
  }
  $("session-name").textContent = `Session: ${sessionId}`;
  $("back-link").href = `/?session=${encodeURIComponent(sessionId)}`;
  try {
    await load();
  } catch (error) {
    setState(error.message, true);
  }
}

start();
