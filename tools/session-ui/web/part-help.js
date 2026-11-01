"use strict";
// Assembly help for the part selected in the explosion view. Shows only fields the server verified
// (claim-checked guide text, stored origin/storage, fixed follow-up wording); never free model text.

(() => {
  const $ = (id) => document.getElementById(id);
  const session = new URLSearchParams(window.location.search).get("session") || "";
  let selected = null;

  async function post(path, body) {
    const response = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" },
                                         body: JSON.stringify({ session, ...body }) });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
    return payload;
  }

  function line(text) {
    const item = document.createElement("li");
    item.textContent = text;
    return item;
  }

  function show(result) {
    const answer = result.answer && result.answer.accepted ? result.answer.text : null;
    $("part-help-answer").textContent = answer || result.message || result.status;
    const details = [];
    if (result.origin) details.push(line(`Came from: ${result.origin.description || "not recorded"} (${result.origin.precision || "precision unknown"})`));
    if (result.storage) details.push(line(`Stored now: ${result.storage.location || "not recorded"}`));
    if (result.follow_up) details.push(line(`Follow-up: ${result.follow_up.question || result.follow_up.text}`));
    for (const evidence of result.evidence_to_show || []) details.push(line(`Evidence: ${evidence.kind || "file"} ${evidence.file || ""} ${(evidence.sha256 || "").slice(0, 12)}`));
    if (result.package_sha256) details.push(line(`Guide package ${result.package_sha256.slice(0, 12)}…`));
    $("part-help-details").replaceChildren(...details);
    const candidates = result.status === "needs_link" ? result.candidates || [] : [];
    $("part-help-link").hidden = candidates.length === 0;
    $("part-help-candidate").replaceChildren(...candidates.map((c) => new Option(`${c.label} (${c.part_id})`, c.part_id)));
  }

  async function ask(event) {
    if (event) event.preventDefault();
    if (!selected) return;
    try {
      show(await post("/api/parts/ask", { target: selected, question: $("part-help-question").value }));
    } catch (error) {
      $("part-help-answer").textContent = error.message;
    }
  }

  async function link() {
    try {
      await post("/api/parts/link", { job: selected, part: $("part-help-candidate").value });
      await ask();
    } catch (error) {
      $("part-help-answer").textContent = error.message;
    }
  }

  window.addEventListener("anastylos:part-selected", (event) => {
    selected = event.detail && event.detail.job;
    $("part-help-answer").textContent = "";
    $("part-help-details").replaceChildren();
    $("part-help-link").hidden = true;
  });
  $("part-help-form").addEventListener("submit", ask);
  $("part-help-link-button").addEventListener("click", link);
})();
