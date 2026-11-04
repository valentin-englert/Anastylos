"use strict";
// Assembly help for the part selected in the explosion view. Shows only fields the server verified
// (claim-checked guide text, stored origin/storage, fixed follow-up wording); never free model text.

(() => {
  const NO_CURRENT_MATCH = "__no_current_match__";
  const $ = (id) => document.getElementById(id);
  const session = new URLSearchParams(window.location.search).get("session") || "";
  let selected = null;
  let selectionRevision = 0;
  let activeLinkState = null;

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

  function selectedJobIs(job, revision) {
    return selected === job && selectionRevision === revision;
  }

  function linkCandidateText(candidate) {
    const label = candidate.label || "Unlabelled part";
    const match = candidate.suggested ? "label word match; verify against the drawing" : "no label word match";
    return `${label} (${candidate.part_id}) — ${match}`;
  }

  function updateLinkControls() {
    const candidate = $("part-help-candidate").value;
    const noMatch = candidate === NO_CURRENT_MATCH;
    const stale = activeLinkState === "link_stale";
    const replace = $("part-help-replace").checked;
    $("part-help-link-button").disabled = !selected || !candidate || noMatch || (stale && !replace);
    let message = "";
    if (noMatch) {
      message = stale
        ? "No replacement was selected. The stale link remains unchanged."
        : "No current part was selected. This drawing stays unlinked.";
    } else if (stale && !replace) {
      message = "The old link stays in place until you explicitly choose to replace it.";
    }
    $("part-help-link-state").textContent = message;
  }

  function linkGuidance(status, candidates) {
    if (status === "link_stale") {
      return candidates.length === 0
        ? "No current memory parts are available. The stale link remains unchanged until a current matching record exists."
        : "The previous link is stale. Choose a current part and explicitly confirm replacement; old or superseded IDs are not offered.";
    }
    if (candidates.length === 0) {
      return "No current memory parts are available. Leave this drawing unlinked until a matching current record exists.";
    }
    if (candidates.some((candidate) => candidate.suggested)) {
      return "Label word matches are suggestions only. Check the drawing itself and choose only the same current recorded part.";
    }
    return "No memory label matches this drawing. Check the drawing itself; leave it unlinked if no current record is the same part.";
  }

  function renderLink(result) {
    const candidates = result.candidates || [];
    const showLink = result.status === "needs_link" || result.status === "link_stale";
    const stale = result.status === "link_stale";
    const options = [
      new Option("Choose a current recorded part…", ""),
      new Option("No current record matches — leave this drawing unlinked", NO_CURRENT_MATCH),
      ...candidates.map((candidate) => new Option(linkCandidateText(candidate), candidate.part_id)),
    ];

    activeLinkState = result.status;
    $("part-help-link").hidden = !showLink;
    $("part-help-candidate").replaceChildren(...options);
    $("part-help-candidate").value = "";
    $("part-help-replace-label").hidden = !stale;
    $("part-help-replace").checked = false;
    $("part-help-link-button").textContent = stale
      ? "Replace stale link with selected current part"
      : "Link drawing to selected current part";
    $("part-help-link-guidance").textContent = linkGuidance(result.status, candidates);
    updateLinkControls();
  }

  function show(result) {
    const answer = result.answer && result.answer.accepted ? result.answer.text : null;
    $("part-help-answer").textContent = answer || result.message || result.status;
    const details = [];
    if (result.layer) {
      const { position, of, above, below } = result.layer;
      const around = [above ? `above it: ${above}` : null, below ? `below it: ${below}` : null].filter(Boolean).join(", ");
      details.push(line(`Layer ${position} of ${of} in your confirmed order${around ? ` (${around})` : ""}.`));
    }
    for (const group of result.belongs_here || []) {
      details.push(line(`Belongs here: ${group.count} × ${group.label}, now in ${group.storage_location || "an unrecorded place"}`));
    }
    if (result.origin) {
      const origin = result.origin;
      details.push(line(`Came from: ${origin.description || "not recorded"} (${origin.precision || "precision unknown"})`));
      const source = [
        origin.record_id ? `record ${origin.record_id}` : "record id unavailable",
        origin.phase && origin.phase.label ? `phase ${origin.phase.label}` : null,
        origin.recorded_at ? `recorded ${origin.recorded_at}` : null,
        origin.declared_by ? `stated by ${origin.declared_by}` : null,
      ].filter(Boolean).join(" · ");
      details.push(line(`Origin source: ${source}.`));
      if (origin.evidence_note) details.push(line(`Origin note: ${origin.evidence_note}`));
      if (origin.install_point_verified === false) details.push(line("Install point is not image-verified."));
    }
    if (result.storage) {
      const storage = result.storage;
      details.push(line(`Stored now: ${storage.location || "not recorded"}`));
      if (storage.record_id) {
        const source = [
          `record ${storage.record_id}`,
          storage.basis ? `basis ${storage.basis}` : null,
          storage.confirmed_by ? `confirmed by ${storage.confirmed_by}` : null,
          storage.recorded_at ? `recorded ${storage.recorded_at}` : null,
        ].filter(Boolean).join(" · ");
        details.push(line(`Storage source: ${source}.`));
      }
    }
    if (result.follow_up) details.push(line(`Follow-up: ${result.follow_up.question || result.follow_up.text}`));

    const evidence = [
      ...(result.origin && result.origin.evidence_refs || []),
      ...(result.evidence_to_show || []),
    ];
    const seenEvidence = new Set();
    for (const item of evidence) {
      const key = `${item.kind || "file"}:${item.file || item.pair_id || ""}:${item.sha256 || item.result_sha256 || ""}`;
      if (seenEvidence.has(key)) continue;
      seenEvidence.add(key);
      const name = item.file || item.pair_id || item.region || "reference";
      const digest = item.sha256 || item.result_sha256;
      details.push(line(`Evidence: ${item.kind || "file"} ${name}${digest ? ` · sha256 ${digest.slice(0, 12)}…` : ""}`));
    }
    if (result.package_sha256) details.push(line(`Guide package sha256 ${result.package_sha256.slice(0, 12)}…`));
    if (result.memory_sha256) details.push(line(`Memory sha256 ${result.memory_sha256.slice(0, 12)}…`));
    $("part-help-details").replaceChildren(...details);
    renderLink(result);
    window.dispatchEvent(new CustomEvent("anastylos:part-help-result", { detail: result }));
  }

  async function ask(event, target = selected, revision = selectionRevision) {
    if (event) event.preventDefault();
    if (!target) return;
    try {
      const result = await post("/api/parts/ask", { target, question: $("part-help-question").value });
      if (!selectedJobIs(target, revision)) return;
      show(result);
    } catch (error) {
      if (selectedJobIs(target, revision)) $("part-help-answer").textContent = error.message;
    }
  }

  async function link() {
    const job = selected;
    const revision = selectionRevision;
    const part = $("part-help-candidate").value;
    const supersede = activeLinkState === "link_stale" && $("part-help-replace").checked;
    if (!job || !part || part === NO_CURRENT_MATCH || (activeLinkState === "link_stale" && !supersede)) return;
    $("part-help-link-button").disabled = true;
    try {
      await post("/api/parts/link", { job, part, supersede });
      if (selectedJobIs(job, revision)) await ask(null, job, revision);
    } catch (error) {
      if (selectedJobIs(job, revision)) $("part-help-answer").textContent = error.message;
    } finally {
      if (selectedJobIs(job, revision)) updateLinkControls();
    }
  }

  // Voice follows the desktop: the server learns which session and part are selected (server memory only), and a spoken
  // answer for it comes back through the same panel. Only fields the server verified are shown.
  let lastVoiceAt = null;

  async function reportFocus() {
    if (!session) return;
    try {
      await post("/api/focus", { job: selected });
    } catch (error) {
      // the voice follow-up is optional; the panel keeps working without it
    }
  }

  async function pollVoice() {
    if (!session || document.hidden) return;
    try {
      const response = await fetch(`/api/focus?session=${encodeURIComponent(session)}`);
      const view = await response.json();
      const voice = response.ok ? view.voice : null;
      const at = voice ? voice.at : 0;
      if (lastVoiceAt === null) {  // an answer from before the page opened is not replayed
        lastVoiceAt = at;
        return;
      }
      if (!voice || at <= lastVoiceAt) return;
      lastVoiceAt = at;
      if (voice.target_job !== selected) return;
      if (voice.result) show(voice.result);
      $("part-help-answer").textContent = `Voice: ${voice.spoken}`;
    } catch (error) {
      // polling is best effort
    }
  }

  window.addEventListener("anastylos:part-selected", (event) => {
    selectionRevision += 1;
    const detail = event.detail || {};
    selected = typeof detail.job === "string" ? detail.job : null;
    reportFocus();
    $("part-help-answer").textContent = "";
    $("part-help-details").replaceChildren();
    $("part-help-link").hidden = true;
    $("part-help-link-state").textContent = "";
    $("part-help-ask").disabled = !selected;
  });
  $("part-help-candidate").addEventListener("change", updateLinkControls);
  $("part-help-replace").addEventListener("change", updateLinkControls);
  $("part-help-form").addEventListener("submit", ask);
  $("part-help-link-button").addEventListener("click", link);
  reportFocus().then(pollVoice);
  window.setInterval(pollVoice, 2000);
})();
