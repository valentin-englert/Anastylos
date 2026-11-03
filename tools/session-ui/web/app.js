/* Session UI: thin client over the local JSON API. Every operator, guide or model
   string is assigned through textContent only — never interpreted as HTML. */
"use strict";

const $ = (id) => document.getElementById(id);

const state = { session: null, question: null, binding: null, claims: [] };

function setText(id, value) {
  $(id).textContent = value === null || value === undefined ? "" : String(value);
}

function showError(id, message) {
  const box = $(id);
  box.textContent = message || "";
  box.hidden = !message;
}

async function call(path, options) {
  const response = await fetch(path, options);
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.error || `Request failed (${response.status}).`);
  }
  return payload;
}

async function post(path, body, button) {
  /* Serializes mutating actions per control: while a request is in flight the
     button is disabled, so a double click cannot start a duplicate operation. */
  const control = button || null;
  if (control) control.disabled = true;
  try {
    return await call(path, { method: "POST", headers: { "Content-Type": "application/json" },
                              body: JSON.stringify(body) });
  } finally {
    if (control) control.disabled = false;
  }
}

function sourceBadge(source, outcome) {
  /* Honest origin labels. Muse/L1 can never appear: the server wires no proposer. */
  const map = {
    L0: ["L0 deterministic — answered from the package without any model", "good"],
    cache: ["Stored answer (cache) — a previously accepted answer, no new call", "good"],
    offline_claims: ["Stored demo claims — offline replay, not Muse", "warn"],
    provider_cache: ["Provider cache replay — not a new call", "warn"],
    refusal: ["Refusal — the package does not support an answer", "bad"],
  };
  const [label, tone] = map[source] || [`Unexpected source ${source}`, "warn"];
  const abstentions = { abstention: "the recorded value is missing, so the answer abstains",
                        model_abstention: "the proposal abstained with insufficient evidence",
                        verifier_rejection: "the claims check rejected the proposal; no text is returned",
                        selection_rejection: "the selection check rejected the proposal; no text is returned",
                        provider_error: "the provider failed; the local fallback stands",
                        answer_timeout: "the provider timed out; the local fallback stands" };
  const suffix = outcome && abstentions[outcome] ? ` — ${abstentions[outcome]}` : "";
  return { label: label + suffix, tone };
}

function paintBadge(badgeId, source, outcome) {
  const badge = $(badgeId);
  const { label, tone } = sourceBadge(source, outcome);
  badge.textContent = label;
  badge.className = `badge ${tone}`;
}

/* ------------------------------------------------------------------ session */

async function loadSessions() {
  setText("session-state", "Loading sessions …");
  showError("session-error", "");
  try {
    const { sessions } = await call("/api/sessions");
    const picker = $("session-picker");
    picker.replaceChildren();
    for (const entry of sessions) {
      const option = document.createElement("option");
      option.value = entry.id;
      option.textContent = entry.error ? `${entry.id} (${entry.error})`
        : `${entry.id} — ${entry.object}${entry.complete ? " (complete)" : ""}`;
      picker.append(option);
    }
    if (!sessions.length) {
      setText("session-state", "No prepared sessions in this workspace.");
      return;
    }
    const deepLink = new URLSearchParams(location.search).get("session");
    if (deepLink && [...picker.options].some((o) => o.value === deepLink)) {
      picker.value = deepLink;
      await openSession();
    } else {
      setText("session-state", sessions.length === 1
        ? "One prepared session. Open it to begin."
        : `${sessions.length} prepared sessions. Open one to begin.`);
    }
  } catch (error) {
    setText("session-state", "");
    showError("session-error", error.message);
  }
}

function fillList(listId, entries) {
  const list = $(listId);
  list.replaceChildren();
  for (const entry of entries) {
    const item = document.createElement("li");
    item.textContent = entry;
    list.append(item);
  }
}

async function loadStatus() {
  if (!state.session) return false;
  setText("session-state", "Loading session …");
  showError("session-error", "");
  try {
    const summary = await call(`/api/status?session=${encodeURIComponent(state.session)}`);
    $("session-status").hidden = false;
    setText("fact-object", summary.object);
    setText("fact-operator", summary.operator);
    setText("fact-complete", summary.complete ? "yes" : "no");
    fillList("phase-list", summary.phases.map(
      (p) => `${p.tag} — ${p.label} — ${p.status}${p.counts ? ` (${p.counts.questions} questions)` : ""}`));
    fillList("incomplete-list", summary.incomplete.length ? summary.incomplete : ["Nothing open."]);
    const stateEntries = Object.entries(summary.questions).map(([k, v]) => `${k}: ${v}`);
    fillList("question-states", stateEntries.length ? stateEntries : ["No question states recorded yet."]);
    const eventEntries = Object.entries(summary.memory_events).map(([k, v]) => `${k}: ${v}`);
    fillList("memory-events", eventEntries.length ? eventEntries : ["No memory events yet."]);
    if (summary.guide && summary.guide.usable === false) {
      setText("session-state", `Guide is not usable: ${summary.guide.reason}`);
    } else {
      setText("session-state", "");
    }
    return true;
  } catch (error) {
    $("session-status").hidden = true;
    setText("session-state", "");
    showError("session-error", error.message);
    return false;
  }
}

async function openSession() {
  const sessionId = $("session-picker").value;
  if (!sessionId) return;
  state.session = sessionId;
  state.question = null;
  state.binding = null;
  for (const id of ["prompt-card", "confirm-card", "guide-body", "resolution",
                    "assembly-body", "guide-answer", "selected-answer"]) $(id).hidden = true;
  if (!(await loadStatus())) return;
  await Promise.all([loadQuestions(true), loadGuide(), loadSelections(), loadAssembly(), loadGaps(), loadDrawings()]);
}

async function refreshAfterMutation() {
  /* Question and assembly answers change counts, states and freshness. */
  await loadStatus();
  await Promise.all([loadQuestions(true), loadGuide(), loadAssembly(), loadGaps()]);
}

/* ------------------------------------------------------------------ questions */

async function loadQuestions(quiet) {
  if (!state.session) return;
  if (!quiet) setText("questions-state", "Loading questions …");
  showError("questions-error", "");
  try {
    const { questions } = await call(`/api/questions?session=${encodeURIComponent(state.session)}`);
    const rows = $("question-rows");
    rows.replaceChildren();
    for (const q of questions) {
      const row = document.createElement("tr");
      for (const key of ["id", "kind", "phase", "state"]) {
        const cell = document.createElement("td");
        cell.textContent = q[key] === null || q[key] === undefined ? "—" : String(q[key]);
        row.append(cell);
      }
      rows.append(row);
    }
    $("question-table").hidden = !questions.length;
    setText("questions-state", questions.length ? "" : "No questions in this session.");
  } catch (error) {
    setText("questions-state", "");
    showError("questions-error", error.message);
  }
}

function fillPromptCard(payload) {
  $("confirm-card").hidden = true;
  $("prompt-card").hidden = false;
  setText("prompt-qid", `Question ${payload.question_id} (${payload.wording || "L0"} wording)`);
  setText("prompt-text", payload.text);
  speak(payload.text + ((payload.screen_options || []).length ? " Please look at the screen for the options." : ""));
  const options = $("screen-options");
  options.replaceChildren();
  for (const option of payload.screen_options || []) {
    const item = document.createElement("li");
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = option.token;
    button.addEventListener("click", () => {
      $("answer-input").value = "";
      sendAnswer(null, option.token, button);
    });
    item.append(button);
    const label = document.createElement("span");
    label.textContent = ` ${option.description || "marked on the screen"}` +
      (option.claimed_by ? ` (already named for “${option.claimed_by}”)` : "");
    item.append(label);
    options.append(item);
  }
  state.question = { qid: payload.question_id, stage: "answer" };
}

function fillConfirmCard(payload) {
  $("prompt-card").hidden = true;
  $("confirm-card").hidden = false;
  setText("confirm-qid", `Read-back for ${payload.question_id}`);
  setText("confirm-text", payload.read_back);
  speak(payload.read_back);
  state.question = { qid: payload.question_id, stage: "confirm" };
}

async function nextQuestion(button) {
  showError("questions-error", "");
  $("questions-notice").hidden = true;
  setText("questions-state", "Asking the next question …");
  try {
    const payload = await post("/api/questions/next", { session: state.session }, button);
    setText("questions-state", "");
    if (payload.done) {
      $("prompt-card").hidden = true;
      $("confirm-card").hidden = true;
      state.question = null;
      const notice = $("questions-notice");
      const unresolved = Object.entries(payload.left_unresolved || {});
      notice.textContent = unresolved.length
        ? `The question round ended with ${unresolved.length} unresolved question(s): ` +
          unresolved.map(([qid, reason]) => `${qid} (${reason})`).join("; ") +
          ". The session stays incomplete."
        : "The question round is finished. All questions are committed.";
      notice.hidden = false;
    } else if (payload.stage === "confirm") {
      fillConfirmCard(payload);
    } else {
      fillPromptCard(payload);
    }
    await refreshAfterMutation();
  } catch (error) {
    setText("questions-state", "");
    showError("questions-error", error.message);
  }
}

async function sendAnswer(text, choice, button) {
  if (!state.question) return;
  showError("questions-error", "");
  try {
    const payload = await post("/api/questions/answer",
      { session: state.session, question_id: state.question.qid, text, choice }, button);
    if (payload.stage === "confirm") {
      fillConfirmCard(payload);
    } else if (payload.stage === "deferred") {
      $("prompt-card").hidden = true;
      state.question = null;
      const notice = $("questions-notice");
      notice.textContent = payload.message;
      notice.hidden = false;
    } else {
      setText("prompt-text", payload.message);
    }
    $("answer-input").value = "";
    await refreshAfterMutation();
  } catch (error) {
    showError("questions-error", error.message);
  }
}

async function sendConfirm(button) {
  if (!state.question) return;
  showError("questions-error", "");
  try {
    const payload = await post("/api/questions/confirm",
      { session: state.session, question_id: state.question.qid,
        text: $("confirm-input").value }, button);
    if (payload.status === "committed" || payload.status === "already_committed") {
      $("confirm-card").hidden = true;
      state.question = null;
      const notice = $("questions-notice");
      notice.textContent = "Recorded.";
      speak("Recorded.");
      notice.hidden = false;
    } else if (payload.stage === "answer") {
      /* The read-back was rejected ("no"): the question is asked again. */
      await nextQuestion(null);
      return;
    } else if (payload.stage === "deferred") {
      $("confirm-card").hidden = true;
      state.question = null;
      const notice = $("questions-notice");
      notice.textContent = payload.message;
      notice.hidden = false;
    } else {
      setText("confirm-text", payload.read_back);
    }
    $("confirm-input").value = "";
    await refreshAfterMutation();
  } catch (error) {
    showError("questions-error", error.message);
  }
}

/* ------------------------------------------------------------------ guide */

async function loadGuide() {
  if (!state.session) return;
  setText("guide-state", "Loading guide …");
  showError("guide-error", "");
  $("guide-stale").hidden = true;
  try {
    const view = await call(`/api/guide?session=${encodeURIComponent(state.session)}`);
    if (!view.usable) {
      $("guide-body").hidden = true;
      $("guide-stale").hidden = false;
      setText("guide-stale-text", view.reason);
      $("build-button").textContent = view.last_build ? "Rebuild guide" : "Build guide";
      setText("guide-state", "");
      return;
    }
    $("guide-body").hidden = false;
    const rows = $("parts-rows");
    rows.replaceChildren();
    for (const part of view.parts) {
      const row = document.createElement("tr");
      for (const value of [part.id, part.operator_label, part.location, part.step, part.basis]) {
        const cell = document.createElement("td");
        cell.textContent = value === null || value === undefined ? "—" : String(value);
        row.append(cell);
      }
      rows.append(row);
    }
    const steps = $("guide-steps");
    steps.replaceChildren();
    for (const step of view.steps) {
      const item = document.createElement("li");
      item.textContent = step.text;
      steps.append(item);
    }
    setText("guide-state", `Package ${view.package_sha256.slice(0, 12)} — ${view.parts.length} parts.`);
  } catch (error) {
    $("guide-body").hidden = true;
    setText("guide-state", "");
    showError("guide-error", error.message);
  }
}

async function buildGuide(button) {
  showError("guide-error", "");
  setText("guide-state", "Building the guide package …");
  try {
    await post("/api/guide/build", { session: state.session }, button);
    await refreshAfterMutation();
  } catch (error) {
    setText("guide-state", "");
    showError("guide-error", error.message);
  }
}

async function askGuide(button) {
  showError("guide-error", "");
  $("guide-answer").hidden = true;
  try {
    const payload = await post("/api/guide/ask",
      { session: state.session, question: $("ask-input").value }, button);
    $("guide-answer").hidden = false;
    paintBadge("guide-source", payload.source, null);
    setText("guide-text", payload.text);
    speak(payload.accepted ? payload.text : "I cannot confirm that from the evidence.");
    setText("guide-provenance",
      `Package ${(payload.package_sha256 || "").slice(0, 12)} — accepted: ${payload.accepted ? "yes" : "no"}` +
      (payload.findings && payload.findings.length ? ` — ${payload.findings.join("; ")}` : ""));
  } catch (error) {
    showError("guide-error", error.message);
  }
}

/* ------------------------------------------------------------------ selection */

async function loadSelections() {
  if (!state.session) return;
  setText("select-state", "Loading bindings …");
  showError("select-error", "");
  try {
    const { bindings, stored_claims } = await call(
      `/api/selections?session=${encodeURIComponent(state.session)}`);
    const picker = $("binding-picker");
    picker.replaceChildren();
    for (const binding of bindings) {
      const option = document.createElement("option");
      option.value = binding.file;
      option.textContent = `${binding.drawing_element_id} → ${binding.event_id}`;
      picker.append(option);
    }
    if (!bindings.length) {
      const option = document.createElement("option");
      option.value = "";
      option.textContent = "No bindings available.";
      picker.append(option);
    }
    $("select-button").disabled = !bindings.length;
    state.claims = stored_claims;
    fillClaimsPicker(null);
    setText("select-state", bindings.length ? "" : "No selection bindings in this session.");
  } catch (error) {
    setText("select-state", "");
    showError("select-error", error.message);
  }
}

function fillClaimsPicker(eventId) {
  /* Stored claims name their part: only fixtures for the resolved event apply. */
  const picker = $("claims-picker");
  picker.replaceChildren();
  const matching = eventId ? state.claims.filter((f) => f.startsWith(`${eventId}-`)) : state.claims;
  for (const file of matching) {
    const option = document.createElement("option");
    option.value = file;
    option.textContent = file;
    picker.append(option);
  }
  if (!matching.length) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = eventId ? `No stored claims for ${eventId}.` : "No stored claims in this session.";
    picker.append(option);
  }
  $("claims-button").disabled = !matching.length;
}

async function resolveSelection(button) {
  showError("select-error", "");
  $("resolution").hidden = true;
  setText("select-state", "Resolving the bound part …");
  try {
    const payload = await post("/api/select",
      { session: state.session, binding: $("binding-picker").value }, button);
    state.binding = $("binding-picker").value;
    fillClaimsPicker(payload.event_id);
    setText("select-state", "");
    $("resolution").hidden = false;
    setText("res-drawing", payload.drawing_element_id);
    setText("res-event", payload.event_id);
    setText("res-label", payload.operator_label);
    const origin = payload.original_location || {};
    const stored = payload.stored_location || {};
    setText("res-origin", origin.value === null || origin.value === undefined
      ? "No origin was recorded for this part."
      : `${origin.value} (basis: ${origin.basis}, record ${origin.record_id})`);
    let storageBasis = stored.basis;
    if (storageBasis && typeof storageBasis === "object") {
      storageBasis = storageBasis.kind +
        (storageBasis.confirmed_by ? ` (confirmed by ${storageBasis.confirmed_by})` : "");
    }
    setText("res-storage", stored.value === null || stored.value === undefined
      ? "No storage location was recorded for this part."
      : `${stored.value} (record ${stored.record_id}, basis ${storageBasis})`);
    setText("res-missing", (payload.missing && payload.missing.length
      ? `Missing: ${payload.missing.join(", ")}. ` : "") +
      "Frame-to-record correspondence is not established by this selection.");
  } catch (error) {
    setText("select-state", "");
    showError("select-error", error.message);
  }
}

async function selectedAsk(intent, claimsFile, button) {
  showError("select-error", "");
  $("selected-answer").hidden = true;
  try {
    const payload = await post("/api/selected-ask",
      { session: state.session, binding: state.binding, intent, claims: claimsFile || null }, button);
    $("selected-answer").hidden = false;
    paintBadge("selected-source", payload.source, payload.outcome);
    setText("selected-text", payload.text);
    setText("selected-provenance",
      `Intent ${payload.intent} — outcome ${payload.outcome}` +
      (payload.original_source ? ` — originally ${payload.original_source}` : "") +
      (payload.findings && payload.findings.length ? ` — ${payload.findings.join("; ")}` : ""));
  } catch (error) {
    showError("select-error", error.message);
  }
}

/* ------------------------------------------------------------------ assembly */

async function loadAssembly() {
  if (!state.session) return;
  setText("assembly-state", "Loading assembly progress …");
  showError("assembly-error", "");
  try {
    const view = await call(`/api/assembly?session=${encodeURIComponent(state.session)}`);
    if (!view.usable) {
      $("assembly-body").hidden = true;
      setText("assembly-state", view.reason);
      return;
    }
    $("assembly-body").hidden = false;
    setText("assembly-progress",
      `${view.steps_done} of ${view.steps.length} steps done (package ${view.package_sha256.slice(0, 12)}).` +
      (view.progress_for_other_packages.length
        ? ` Older progress exists for superseded package(s): ${view.progress_for_other_packages.join(", ")}.` : ""));
    const current = view.steps.find((s) => s.step === view.current_step);
    $("current-step-card").hidden = !current;
    if (current) setText("current-step-text", current.text);
    $("assembly-complete-note").hidden = !view.complete;
    const steps = $("assembly-steps");
    steps.replaceChildren();
    for (const step of view.steps) {
      const item = document.createElement("li");
      const marker = (view.latest[String(step.step)] || {}).event;
      item.textContent = `${step.text}${marker ? ` [${marker}]` : ""}`;
      steps.append(item);
    }
    setText("assembly-state", "");
  } catch (error) {
    $("assembly-body").hidden = true;
    setText("assembly-state", "");
    showError("assembly-error", error.message);
  }
}

async function confirmStep(button) {
  showError("assembly-error", "");
  const current = state.session && $("current-step-text").textContent;
  if (!current) return;
  const match = /^Step (\d+) of/.exec(current);
  if (!match) return;
  try {
    await post("/api/assembly/done",
      { session: state.session, step: Number(match[1]) }, button);
    await refreshAfterMutation();
  } catch (error) {
    /* A serialized second click lands here: the step is no longer current, so
       nothing was recorded twice. Refresh to show the true progress. */
    await refreshAfterMutation();
    showError("assembly-error", error.message);
  }
}

/* ------------------------------------------------------------------ report */

function reportLinks() {
  const base = `/api/report?session=${encodeURIComponent(state.session)}`;
  $("report-open").href = base;
  $("report-download").href = `${base}&download=1`;
}

function showReportMeta(meta) {
  $("report-body").hidden = false;
  const assembly = meta.assembly
    ? `${meta.assembly.steps_done}/${meta.assembly.steps_total} steps done` : "no assembly plan";
  setText("report-meta",
    `${meta.file} — sha ${meta.report_sha256.slice(0, 12)}, guide ${meta.guide}, ` +
    `${meta.parts} parts, ${meta.open_questions.length} open / ` +
    `${meta.left_unresolved.length} unresolved questions, ${assembly}.`);
  reportLinks();
}

async function exportReport(button) {
  showError("report-error", "");
  $("report-body").hidden = true;
  setText("report-state", "Exporting the report …");
  try {
    showReportMeta(await post("/api/report/export", { session: state.session }, button));
    setText("report-state", "");
  } catch (error) {
    setText("report-state", "");
    showError("report-error", error.message);
  }
}

/* ------------------------------------------------------------------ capture needs */

function gapTone(status) {
  return status === "closed" ? "good" : status === "needs_review" ? "warn" : "";
}

async function loadGaps() {
  if (!state.session) return;
  setText("gaps-state", "Loading capture needs …");
  showError("gaps-error", "");
  $("gap-result").hidden = true;
  try {
    const view = await call(`/api/gaps?session=${encodeURIComponent(state.session)}`);
    if (!view.gaps.length) {
      $("gaps-body").hidden = true;
      setText("gaps-state", "No prepared capture needs in this session.");
      return;
    }
    $("gaps-body").hidden = false;
    setText("gaps-closure", view.closure);
    const list = $("gaps-list");
    list.replaceChildren();
    const picker = $("gap-picker");
    picker.replaceChildren();
    for (const gap of view.gaps) {
      const card = document.createElement("div");
      card.className = "gap-card";
      if (gap.error) {
        const para = document.createElement("p");
        para.className = "error";
        para.textContent = `${gap.file}: ${gap.error}`;
        card.append(para);
      } else {
        const title = document.createElement("p");
        const badge = document.createElement("span");
        badge.className = `badge ${gapTone(gap.status)}`;
        badge.textContent = gap.status;
        title.append(badge);
        const name = document.createElement("span");
        name.textContent = ` ${gap.gap_id} (${gap.kind})`;
        title.append(name);
        card.append(title);
        for (const [label, value] of [["Missing", gap.missing_information],
                                      ["Detail", gap.detail], ["Instruction", gap.instruction],
                                      ["Success", gap.content_criterion],
                                      ["State", gap.reason]]) {
          const para = document.createElement("p");
          para.textContent = `${label}: ${Array.isArray(value) ? value.join(" ") : value}`;
          card.append(para);
        }
        const option = document.createElement("option");
        option.value = gap.file;
        option.textContent = `${gap.gap_id} (${gap.status})`;
        picker.append(option);
      }
      list.append(card);
    }
    const evidence = $("evidence-list");
    evidence.replaceChildren();
    const candidates = $("evidence-picker");
    candidates.replaceChildren();
    const none = document.createElement("option");
    none.value = "";
    none.textContent = "No candidate (gap state only)";
    candidates.append(none);
    for (const item of view.evidence) {
      const row = document.createElement("li");
      const link = document.createElement("a");
      link.href = `/api/gaps/evidence?session=${encodeURIComponent(state.session)}` +
        `&file=${encodeURIComponent(item.file)}`;
      link.target = "_blank";
      link.rel = "noopener";
      link.textContent = item.file;
      row.append(link);
      const meta = document.createElement("span");
      meta.textContent = ` — ${item.bytes} bytes, sha ${item.image_sha256.slice(0, 12)}`;
      row.append(meta);
      evidence.append(row);
      const option = document.createElement("option");
      option.value = item.file;
      option.textContent = item.file;
      candidates.append(option);
    }
    if (!view.evidence.length) {
      const row = document.createElement("li");
      row.textContent = "No prepared evidence in this session.";
      evidence.append(row);
    }
    setText("gaps-state", "");
  } catch (error) {
    $("gaps-body").hidden = true;
    setText("gaps-state", "");
    showError("gaps-error", error.message);
  }
}

async function evaluateGap(button) {
  showError("gaps-error", "");
  $("gap-result").hidden = true;
  const gap = $("gap-picker").value;
  if (!gap) return;
  try {
    const result = await post("/api/gaps/evaluate",
      { session: state.session, gap, candidate: $("evidence-picker").value || null }, button);
    $("gap-result").hidden = false;
    const badge = $("gap-status");
    badge.textContent = result.status;
    badge.className = `badge ${gapTone(result.status)}`;
    setText("gap-reason", result.reason);
    setText("gap-detail",
      `Gap ${result.gap_id} (${result.kind}) — ` +
      (result.candidate ? `candidate ${result.candidate.evidence_id}. ` : "no candidate. ") +
      "Closure needs an explicit technical review and an explicit operator " +
      "confirmation; neither is collected in this build.");
  } catch (error) {
    showError("gaps-error", error.message);
  }
}

/* ------------------------------------------------------------------ part drawings */

const DRAWING_POLL_MS = 3000;
let drawingTimer = null;

function drawingUrl(job, file) {
  return `/api/drawing/file?session=${encodeURIComponent(state.session)}&job=${encodeURIComponent(job)}&file=${file}`;
}

const JOB_WORDS = {
  NEEDS_CAPTURE: "needs a new photo",
  NEEDS_CONFIRMATION: "ready, please check it",
  NEEDS_REVIEW: "needs a closer look",
  drawn_unconfirmed: "ready, please confirm it",
  running: "working",
  paused: "paused, can continue",
  failed: "failed",
};

function drawingCard(job) {
  const card = document.createElement("div");
  card.className = "drawing-job";
  card.dataset.state = job.state || "";
  card.dataset.status = job.status || "";
  card.dataset.binding = job.binding ? job.binding.decision : "";
  const head = document.createElement("p");
  const progress = job.state === "running" ? ` — ${job.stage || "starting"} (${job.done || 0}/${job.total || 7}, ${job.elapsed_s ?? 0} s)` : "";
  head.textContent = `${job.part || job.job}: ${JOB_WORDS[job.status] || JOB_WORDS[job.state] || job.state}${progress}`;
  card.append(head);
  if (job.error) {
    const error = document.createElement("p");
    error.className = "error";
    error.textContent = job.error;
    card.append(error);
  }
  if (job.reasons && job.reasons.length) {
    const list = document.createElement("ul");
    for (const reason of job.reasons) {
      const item = document.createElement("li");
      item.textContent = reason;
      list.append(item);
    }
    card.append(list);
  }
  if (job.choice) {
    const choice = document.createElement("p");
    choice.className = "provenance";
    choice.textContent = `Muse: ${job.choice.state}, ${job.choice.view_side}; ${job.choice.reason} Uncertain: ${job.choice.uncertainties}`;
    card.append(choice);
  }
  for (const file of job.files) {
    if (file === "contact_sheet.jpg") continue;
    const figure = document.createElement("figure");
    const image = document.createElement("img");
    image.src = drawingUrl(job.job, file);
    image.alt = file === "drawing.svg" ? "Unconfirmed outline drawing" : "Photo overlay: outline (green), opening candidates (red), Muse box (blue)";
    image.className = "drawing-image";
    const caption = document.createElement("figcaption");
    caption.textContent = file === "drawing.svg" ? "Drawing (unconfirmed)" : "Photo overlay for checking";
    figure.append(image, caption);
    card.append(figure);
  }
  if (job.state === "paused" || job.state === "failed") {
    const resume = document.createElement("button");
    resume.type = "button";
    resume.textContent = "Resume job";
    resume.addEventListener("click", async () => {
      try { await post("/api/drawing/resume", { session: state.session, job: job.job }, resume); await loadDrawings(); }
      catch (error) { showError("drawings-error", error.message); }
    });
    card.append(resume);
  }
  if (job.status === "NEEDS_CAPTURE" && job.state === "done") card.append(captureBlock(job));
  if (job.binding) {
    const bound = document.createElement("p");
    bound.className = "notice";
    bound.textContent = `Binding ${job.binding.decision}ed at ${job.binding.recorded_at}; memory unchanged.`;
    card.append(bound);
  } else if (job.state === "done" && (job.status === "drawn_unconfirmed" || job.status === "NEEDS_CONFIRMATION")) {
    const form = document.createElement("form");
    const question = document.createElement("p");
    question.textContent = `Does this drawing show the requested ${job.scope}: ${job.part}?`;
    const yes = document.createElement("button");
    yes.type = "button";
    yes.textContent = "Yes, bind this drawing";
    yes.addEventListener("click", () => bindDrawing(job.job, "confirm", yes));
    const no = document.createElement("button");
    no.type = "button";
    no.textContent = "No, reject";
    no.addEventListener("click", () => bindDrawing(job.job, "reject", no));
    form.append(question, yes, no);
    card.append(form);
  }
  return card;
}

const CAPTURE_TEXT = {
  requested: "Waiting for the photo from the phone",
  delivered: "Photo received, checking it",
  follow_up_starting: "Photo received; checking the drawing worker start",
  follow_up_started: "Photo received; drawing again from the new photo",
  follow_up_pending_recovery: "Photo received; drawing paused for recovery. Check the existing follow-up job before resuming",
  failed: "The phone could not take this view",
};

function captureBlock(job) {
  const block = document.createElement("div");
  block.className = "capture-request";
  const capture = job.capture;
  if (!capture || capture.state === "failed") {
    if (capture) {
      const failed = document.createElement("p");
      failed.className = "error";
      failed.textContent = `${CAPTURE_TEXT.failed} (${capture.failure}).`;
      block.append(failed);
    }
    const ask = document.createElement("button");
    ask.type = "button";
    ask.textContent = capture ? "Ask the phone again" : "Request this view on the phone";
    ask.addEventListener("click", () => requestCapture(job.job, ask));
    block.append(ask);
    return block;
  }
  const line = document.createElement("p");
  line.className = "notice";
  const phone = capture.phone_status ? ` (phone: ${capture.phone_status})` : "";
  line.textContent = `${capture.label}: ${CAPTURE_TEXT[capture.state] || capture.state}${phone}.`;
  block.append(line);
  if (capture.state === "follow_up_pending_recovery" && capture.failure) {
    const reason = document.createElement("p");
    reason.className = "notice";
    reason.textContent = capture.failure;
    block.append(reason);
  }
  if (capture.follow_up_job) {
    const follow = document.createElement("p");
    follow.className = "provenance";
    follow.textContent = `Follow-up job: ${capture.follow_up_job}. The photo does not prove which part it shows; you confirm that on the new drawing.`;
    block.append(follow);
  }
  return block;
}

async function requestCapture(job, button) {
  try {
    const result = await post("/api/capture/request", { session: state.session, job }, button);
    speak(`Please photograph the ${result.label}.`);
    await loadDrawings();
  } catch (error) {
    showError("drawings-error", error.message);
  }
}

async function pollCaptures(jobs) {
  const open = jobs.filter((job) => job.capture && ["requested", "delivered", "follow_up_starting"].includes(job.capture.state));
  for (const job of open) {
    const result = await post("/api/capture/poll", { session: state.session, job: job.job });
    if (result.state === "follow_up_started") speak("Photo received. I am drawing the part again.");
  }
  return open.length > 0;
}

async function loadPhoneLink(connect = false) {
  try {
    const link = await post(connect ? "/api/capture/connect" : "/api/capture/link", {}, connect ? $("phone-connect") : undefined);
    const text = {
      not_connected: "Phone: not connected.",
      waiting_for_phone: "Phone: scan this code in the app and arm the session.",
      connected: "Phone: connected for capture requests.",
    };
    setText("phone-link-state", text[link.state] || `Phone: ${link.state}`);
    $("phone-connect").hidden = link.state === "connected";
    const showQr = link.state === "waiting_for_phone";
    if (showQr && $("phone-qr-block").hidden) $("phone-qr").src = `/api/capture/qr?session=${encodeURIComponent(link.guided_session_id)}`;
    $("phone-qr-block").hidden = !showQr;
    if (link.state === "waiting_for_phone") setTimeout(loadPhoneLink, DRAWING_POLL_MS);
  } catch (error) {
    setText("phone-link-state", `Phone: ${error.message}`);
  }
}

let videoTimer = null;
const announcedVideos = new Set();

function videoCard(video) {
  const card = document.createElement("div");
  card.className = "drawing-job";
  card.dataset.state = video.job ? video.job.state : video.status;
  const size = video.size ? `${(video.size / 1048576).toFixed(0)} MB` : "";
  const job = video.job;
  const words = job
    ? { queued: "waiting to start", running: `processing — ${job.stage || "starting"}`, done: `processed — open session ${job.session}`, failed: "processing failed" }[job.state]
    : { complete: "uploaded, ready to process", receiving: "uploading from the phone…", failed: "upload failed, the phone will send it again" }[video.status];
  const head = document.createElement("p");
  const recorded = video.declared_at ? new Date(video.declared_at).toLocaleString("en-GB", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : "Unknown time";
  const parts = job && { finding: " · finding parts on the sheet…", found: " · parts drawn, see the explosion view", failed: " · finding parts failed" }[job.parts];
  head.textContent = `${recorded} · ${size} · ${words || video.status}${parts || ""} · id ${video.video.slice(0, 8)}`;
  card.append(head);
  if (job && job.error) {
    const error = document.createElement("p");
    error.className = "error";
    error.textContent = job.error;
    card.append(error);
  }
  if (video.status === "complete" && (!job || job.state === "failed")) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = job ? "Process again" : "Process";
    button.addEventListener("click", async () => {
      try { await post("/api/videos/process", { video: video.video }, button); await loadVideos(); }
      catch (error) { showError("videos-error", error.message); }
    });
    card.append(button);
  }
  return card;
}

async function loadVideos() {
  clearTimeout(videoTimer);
  showError("videos-error", "");
  try {
    const view = await call("/api/videos");
    setText("videos-state", !view.configured ? "Set up the capture receiver to list phone videos." : view.videos.length ? "" : "No videos from the phone yet.");
    $("video-list").replaceChildren(...view.videos.map(videoCard));
    const busy = view.videos.some((v) => v.status === "receiving" || (v.job && (v.job.state === "running" || v.job.state === "queued")));
    const finished = view.videos.filter((v) => v.job && v.job.state === "done" && !announcedVideos.has(v.video));
    if (finished.length) { finished.forEach((v) => announcedVideos.add(v.video)); loadSessions(); }
    videoTimer = setTimeout(loadVideos, busy ? 3000 : 10000);
  } catch (error) {
    showError("videos-error", error.message);
    videoTimer = setTimeout(loadVideos, 10000);
  }
}

let pairTimer = null;

async function loadPairRequests() {
  clearTimeout(pairTimer);
  try {
    const view = await call("/api/phone/requests");
    const list = $("pair-requests");
    list.replaceChildren(...view.requests.map((request) => pairRequestCard(request, view.certificate_sha256)));
    setText("pair-state", view.requests.length ? "" : "Waiting for the phone to scan the code…");
    showError("pair-error", "");
    if (!$("pair-qr-block").hidden || view.requests.length) pairTimer = setTimeout(loadPairRequests, 3000);
  } catch (error) {
    showError("pair-error", error.message);
  }
}

function pairRequestCard(request, certificate) {
  const card = document.createElement("div");
  card.className = "drawing-job";
  const head = document.createElement("p");
  head.textContent = `${request.device} wants to connect (request ${request.short_id}, ${request.expires_in_s} s left).`;
  card.append(head);
  if (certificate) {
    const detail = document.createElement("p");
    detail.className = "hint";
    detail.textContent = `Workstation certificate ${certificate.slice(0, 29)}…`;
    card.append(detail);
  }
  for (const [decision, label] of [["approve", "Approve"], ["reject", "Reject"]]) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = label;
    button.addEventListener("click", async () => {
      try {
        const result = await post("/api/phone/decision", { request: request.id, decision }, button);
        setText("pair-state", result.decision === "approved" ? "Phone approved and ready for guided capture." : "Request rejected.");
        if (result.decision === "approved") { $("pair-qr-block").hidden = true; loadPhoneLink(true); }
        await loadPairRequests();
      } catch (error) {
        showError("pair-error", error.message);
      }
    });
    card.append(button);
  }
  return card;
}

async function startPairing(button) {
  try {
    const result = await post("/api/phone/pair", {}, button);
    $("pair-qr").src = `/api/phone/pair-qr?t=${Date.now()}`;
    setText("pair-caption", `Scan with the app. Valid for ${result.ttl_minutes} minutes.`);
    $("pair-qr-block").hidden = false;
    await loadPairRequests();
  } catch (error) {
    showError("pair-error", error.message);
  }
}

async function loadDrawings() {
  if (!state.session) return;
  clearTimeout(drawingTimer);
  showError("drawings-error", "");
  try {
    $("explosion-link").href = `/explosion.html?session=${encodeURIComponent(state.session)}`;
    const view = await call(`/api/drawings?session=${encodeURIComponent(state.session)}`);
    const picker = $("drawing-spec");
    const selected = picker.value;
    picker.replaceChildren(...view.specs.map((name) => new Option(name, name)));
    if (view.specs.includes(selected)) picker.value = selected;
    $("drawing-start").disabled = !view.specs.length;
    setText("drawings-state", view.jobs.length ? `${view.jobs.length} drawing job(s).` : "No drawings in this session yet.");
    $("drawing-jobs").replaceChildren(...view.jobs.map(drawingCard));
    speakDrawingChanges(view.jobs);
    const waiting = await pollCaptures(view.jobs);
    if (waiting || view.jobs.some((job) => job.state === "running")) drawingTimer = setTimeout(loadDrawings, DRAWING_POLL_MS);
  } catch (error) {
    showError("drawings-error", error.message);
  }
}

async function startDrawing(button) {
  const spec = $("drawing-spec").value;
  if (!spec) return;
  try {
    const result = await post("/api/drawing/start", { session: state.session, spec }, button);
    setText("drawings-state", result.already_running ? `Job ${result.job} is already running.` : `Started ${result.job}.`);
    await loadDrawings();
  } catch (error) {
    showError("drawings-error", error.message);
  }
}

async function bindDrawing(job, decision, button) {
  try {
    await post("/api/drawing/bind", { session: state.session, job, decision }, button);
    await loadDrawings();
  } catch (error) {
    showError("drawings-error", error.message);
  }
}

/* ------------------------------------------------------------------ voice */

/* Speech output reads verified text only: template questions, read-backs, verified guide answers and
   fixed status sentences. Model prose is never spoken. */
const DRAWING_SPEECH = {
  NEEDS_CAPTURE: "I cannot see the whole part. Please place it alone in the work area and take your hands away.",
  NEEDS_CONFIRMATION: "The drawing is ready. Please confirm that it shows the right part.",
  NEEDS_REVIEW: "The drawing needs a review before it can be used.",
  drawn_unconfirmed: "The drawing is ready for your confirmation.",
};
const spokenJobs = new Map();
const voice = { recording: false, context: null, stream: null, chunks: [], source: null, processor: null, seq: 0 };

function setVoiceState(value) {
  $("voice-bar").dataset.state = value;
}

async function transcribeLatest(blob) {
  /* Each clip gets a sequence number; only the newest clip may be routed, a late older result is dropped. */
  const seq = ++voice.seq;
  setVoiceState("thinking");
  try {
    const result = await call("/api/voice/transcribe", { method: "POST", headers: { "Content-Type": "audio/wav" }, body: blob });
    return seq === voice.seq ? result : { status: "stale" };
  } finally {
    if (seq === voice.seq) setVoiceState("idle");
  }
}

function speak(text) {
  if (!text || !$("speak-toggle").checked || !window.speechSynthesis) return;
  window.speechSynthesis.cancel();
  const utterance = new SpeechSynthesisUtterance(text);
  utterance.lang = "en-US";
  utterance.onstart = () => setVoiceState("speaking");
  utterance.onend = () => setVoiceState("idle");
  const english = window.speechSynthesis.getVoices().find((v) => v.lang.startsWith("en"));
  if (english) utterance.voice = english;
  window.speechSynthesis.speak(utterance);
}

function speakDrawingChanges(jobs) {
  for (const job of jobs) {
    const key = `${job.state}/${job.status}`;
    const before = spokenJobs.get(job.job);
    spokenJobs.set(job.job, key);
    if (before === undefined || before === key) continue;
    if (job.state === "failed") speak("The drawing job failed.");
    else if (job.state === "done") speak(DRAWING_SPEECH[job.status]);
  }
}

function wavFromSamples(samples, inputRate, outputRate) {
  const ratio = inputRate / outputRate;
  const length = Math.floor(samples.length / ratio);
  const view = new DataView(new ArrayBuffer(44 + length * 2));
  const text = (offset, value) => { for (let i = 0; i < value.length; i++) view.setUint8(offset + i, value.charCodeAt(i)); };
  text(0, "RIFF"); view.setUint32(4, 36 + length * 2, true); text(8, "WAVE"); text(12, "fmt ");
  view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true);
  view.setUint32(24, outputRate, true); view.setUint32(28, outputRate * 2, true);
  view.setUint16(32, 2, true); view.setUint16(34, 16, true); text(36, "data"); view.setUint32(40, length * 2, true);
  for (let i = 0; i < length; i++) {
    const sample = Math.max(-1, Math.min(1, samples[Math.floor(i * ratio)]));
    view.setInt16(44 + i * 2, sample < 0 ? sample * 0x8000 : sample * 0x7fff, true);
  }
  return new Blob([view], { type: "audio/wav" });
}

async function startTalking() {
  if (voice.recording) return;
  voice.recording = true;
  try {
    voice.stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true } });
  } catch (error) {
    voice.recording = false;
    setText("voice-state", "Microphone not available: " + error.message);
    return;
  }
  if (window.speechSynthesis) window.speechSynthesis.cancel();
  voice.context = new AudioContext();
  voice.source = voice.context.createMediaStreamSource(voice.stream);
  voice.processor = voice.context.createScriptProcessor(4096, 1, 1);
  voice.chunks = [];
  voice.processor.onaudioprocess = (event) => voice.chunks.push(new Float32Array(event.inputBuffer.getChannelData(0)));
  voice.source.connect(voice.processor);
  voice.processor.connect(voice.context.destination);
  $("talk-button").classList.add("recording");
  setVoiceState("listening");
  setText("voice-state", "Listening …");
}

async function stopTalking() {
  if (!voice.recording || !voice.context) return;
  voice.recording = false;
  $("talk-button").classList.remove("recording");
  setVoiceState("idle");
  voice.processor.disconnect();
  voice.source.disconnect();
  voice.stream.getTracks().forEach((track) => track.stop());
  const rate = voice.context.sampleRate;
  await voice.context.close();
  voice.context = null;
  const total = voice.chunks.reduce((n, c) => n + c.length, 0);
  if (total < rate * 0.3) { setText("voice-state", "Too short. Hold while you speak."); return; }
  const samples = new Float32Array(total);
  let offset = 0;
  for (const chunk of voice.chunks) { samples.set(chunk, offset); offset += chunk.length; }
  setText("voice-state", "Transcribing …");
  try {
    const result = await transcribeLatest(wavFromSamples(samples, rate, 16000));
    if (result.status === "stale") return;
    if (result.status !== "ok" || !result.transcript) {
      setText("voice-state", `Not understood (${result.status}). Please try again or type.`);
      return;
    }
    setText("voice-state", `You said: “${result.transcript}” (${result.latency_s} s)`);
    routeTranscript(result.transcript);
  } catch (error) {
    setText("voice-state", "Transcription failed: " + error.message);
  }
}

function routeTranscript(text) {
  /* The transcript is operator input like typed text: it goes to whatever the dialog is waiting for.
     Two fixed commands work anywhere: "next question" and "repeat". */
  const command = text.toLowerCase().replace(/[^a-z' ]/g, "").replace(/^please |please$/g, "").trim();
  /* A negated command ("don't go to the next question") never runs a command; it is treated as plain input. */
  const negated = /\b(don't|dont|do not|not|never|no)\b/.test(command);
  if (!negated && (command === "next question" || command === "next")) { nextQuestion($("next-button")); return; }
  if (!negated && (command === "repeat" || command === "say that again")) {
    speak(!$("confirm-card").hidden ? $("confirm-text").textContent : $("prompt-text").textContent);
    return;
  }
  if (!$("confirm-card").hidden) { $("confirm-input").value = text; sendConfirm($("confirm-button")); return; }
  if (!$("prompt-card").hidden) { $("answer-input").value = text; sendAnswer(text, null, $("answer-button")); return; }
  $("ask-input").value = text.replace(/^\s*muse[,.!]?\s*/i, "");
  askGuide($("ask-button"));
}

/* ------------------------------------------------------------------ wake word */

/* Opt-in hands-free mode: a local energy detector cuts speech segments; each segment is transcribed and only
   segments that start with the wake word are acted on (or the one segment armed by a bare wake word, within
   VAD.armedMs). No segment is kept; silence is never sent. */
let WAKE = /^\W*(?:hey\s+|ok(?:ay)?\s+)?(?:muse|meta|mews)\b[\s,.!?:-]*(.*)$/i;

/* The agent name is the wake word (stored per workspace, also read by the phone). Mishearings come from the server. */
function applyDialogueSettings(view) {
  const words = [...view.wake_words, ...view.mishearings]
    .map((word) => word.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).sort((a, b) => b.length - a.length);
  WAKE = new RegExp(`^\\W*(?:hey\\s+|ok(?:ay)?\\s+)?(?:${words.join("|")})\\b[\\s,.!?:-]*(.*)$`, "i");
  $("agent-name").value = view.agent_name;
  $("persona-select").value = view.persona;
}
async function saveDialogueSettings() {
  try {
    applyDialogueSettings(await post("/api/dialogue/settings", { agent_name: $("agent-name").value, persona: $("persona-select").value }));
  } catch (error) { setText("voice-state", error.message); }
}
$("agent-name").addEventListener("change", saveDialogueSettings);
$("persona-select").addEventListener("change", saveDialogueSettings);
call("/api/dialogue/settings").then(applyDialogueSettings, () => {});
const VAD = { blockMs: 0, startFactor: 3.0, minFloor: 0.004, hangMs: 800, maxMs: 8000, minMs: 400, prerollBlocks: 3, armedMs: 6000 };
const listen = { on: false, stream: null, context: null, processor: null, source: null, floor: 0.01,
                 segment: null, quiet: 0, preroll: [], busy: false, armedUntil: 0 };

async function startListening() {
  try {
    listen.stream = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true } });
  } catch (error) {
    $("wake-toggle").checked = false;
    setText("voice-state", "Microphone not available: " + error.message);
    return;
  }
  listen.context = new AudioContext();
  listen.source = listen.context.createMediaStreamSource(listen.stream);
  listen.processor = listen.context.createScriptProcessor(4096, 1, 1);
  VAD.blockMs = 4096 / listen.context.sampleRate * 1000;
  listen.processor.onaudioprocess = (event) => onListenBlock(new Float32Array(event.inputBuffer.getChannelData(0)));
  listen.source.connect(listen.processor);
  listen.processor.connect(listen.context.destination);
  listen.on = true;
  listen.armedUntil = 0;
  setText("voice-state", `Listening for “${($("agent-name").value || "Muse")}” …`);
}

async function stopListening() {
  listen.on = false;
  listen.segment = null;
  listen.armedUntil = 0;
  if (listen.processor) { listen.processor.disconnect(); listen.source.disconnect(); }
  if (listen.stream) listen.stream.getTracks().forEach((track) => track.stop());
  if (listen.context) await listen.context.close();
  listen.context = listen.stream = listen.processor = listen.source = null;
  setText("voice-state", "");
}

function onListenBlock(block) {
  /* Deaf while the agent speaks, while a clip is transcribed and while push-to-talk records. */
  if (!listen.on || listen.busy || voice.recording || (window.speechSynthesis && window.speechSynthesis.speaking)) {
    listen.segment = null;
    return;
  }
  let energy = 0;
  for (const value of block) energy += value * value;
  const rms = Math.sqrt(energy / block.length);
  const threshold = Math.max(VAD.minFloor, listen.floor * VAD.startFactor);
  if (!listen.segment) {
    listen.floor = 0.95 * listen.floor + 0.05 * rms;   // background level, only learned outside speech
    listen.preroll.push(block);
    if (listen.preroll.length > VAD.prerollBlocks) listen.preroll.shift();
    if (rms > threshold) { listen.segment = [...listen.preroll]; listen.quiet = 0; }
    return;
  }
  listen.segment.push(block);
  listen.quiet = rms > threshold ? 0 : listen.quiet + VAD.blockMs;
  const length = listen.segment.length * VAD.blockMs;
  if (listen.quiet >= VAD.hangMs || length >= VAD.maxMs) {
    const segment = listen.segment;
    listen.segment = null;
    if (length - listen.quiet >= VAD.minMs) handleSegment(segment, listen.context.sampleRate, Date.now());
  }
}

async function handleSegment(blocks, rate, endedAt) {
  listen.busy = true;
  /* Same rule as the phone (VoiceController.handleClip): a bare "Muse" arms exactly ONE following segment, judged by when
     that segment ended. The arming is spent here, before transcription, so an empty or failed segment consumes it too. */
  const armed = endedAt < listen.armedUntil;
  listen.armedUntil = 0;
  try {
    const total = blocks.reduce((n, b) => n + b.length, 0);
    const samples = new Float32Array(total);
    let offset = 0;
    for (const b of blocks) { samples.set(b, offset); offset += b.length; }
    const result = await transcribeLatest(wavFromSamples(samples, rate, 16000));
    if (result.status !== "ok" || !result.transcript) return;
    const match = WAKE.exec(result.transcript.trim());
    if (!match && !armed) return;                       // not addressed to the agent: discarded, not shown
    const command = match ? match[1].trim() : result.transcript.trim();
    if (!command) {
      listen.armedUntil = Date.now() + VAD.armedMs;
      setText("voice-state", "Yes? (listening for your command)");
      speak("Yes?");
      return;
    }
    setText("voice-state", `You said: “${command}” (${result.latency_s} s)`);
    routeTranscript(command);
  } catch (error) {
    setText("voice-state", "Transcription failed: " + error.message);
  } finally {
    listen.busy = false;
  }
}

/* ------------------------------------------------------------------ wiring */

$("session-form").addEventListener("submit", (event) => { event.preventDefault(); openSession(); });
$("next-form").addEventListener("submit", (event) => { event.preventDefault(); nextQuestion($("next-button")); });
$("answer-form").addEventListener("submit", (event) => {
  event.preventDefault(); sendAnswer($("answer-input").value, null, $("answer-button"));
});
$("confirm-form").addEventListener("submit", (event) => { event.preventDefault(); sendConfirm($("confirm-button")); });
$("build-form").addEventListener("submit", (event) => { event.preventDefault(); buildGuide($("build-button")); });
$("ask-form").addEventListener("submit", (event) => { event.preventDefault(); askGuide($("ask-button")); });
$("select-form").addEventListener("submit", (event) => { event.preventDefault(); resolveSelection($("select-button")); });
$("origin-form").addEventListener("submit", (event) => { event.preventDefault(); selectedAsk("origin", null, $("origin-button")); });
$("storage-form").addEventListener("submit", (event) => { event.preventDefault(); selectedAsk("storage", null, $("storage-button")); });
$("claims-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const file = $("claims-picker").value;
  if (!file) return;
  const intent = /origin/.test(file) ? "origin" : "storage";
  selectedAsk(intent, file, $("claims-button"));
});
$("done-form").addEventListener("submit", (event) => { event.preventDefault(); confirmStep($("done-button")); });
$("export-form").addEventListener("submit", (event) => { event.preventDefault(); exportReport($("export-button")); });
$("phone-connect").addEventListener("click", () => loadPhoneLink(true));
loadPhoneLink();
loadVideos();
$("pair-start").addEventListener("click", () => startPairing($("pair-start")));
window.addEventListener("hashchange", () => {
  /* Opening the Phone page opens the guided session and shows its QR without another click. */
  if (location.hash === "#/phone") { loadPhoneLink(true); loadPairRequests(); }
});
$("drawing-form").addEventListener("submit", (event) => { event.preventDefault(); startDrawing($("drawing-start")); });
$("evaluate-form").addEventListener("submit", (event) => { event.preventDefault(); evaluateGap($("evaluate-button")); });

$("talk-button").addEventListener("pointerdown", (event) => { event.preventDefault(); startTalking(); });
$("talk-button").addEventListener("pointerup", stopTalking);
$("talk-button").addEventListener("pointerleave", stopTalking);
document.addEventListener("keydown", (event) => {
  if (event.code !== "Space" || event.repeat || /INPUT|TEXTAREA|SELECT|BUTTON/.test(document.activeElement.tagName)) return;
  event.preventDefault();
  startTalking();
});
document.addEventListener("keyup", (event) => { if (event.code === "Space" && voice.recording) { event.preventDefault(); stopTalking(); } });

/* Wake-word listening is OFF at every page load (a browser may restore a checked box); the demo voice path is the phone,
   push-to-talk stays the browser's default. */
$("wake-toggle").checked = false;
$("wake-toggle").addEventListener("change", (event) => (event.target.checked ? startListening() : stopListening()));
loadSessions();
