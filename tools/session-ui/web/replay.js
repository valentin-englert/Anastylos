"use strict";
// Evidence replay for the part selected in the explosion view. Frames are rectified JPEGs prepared by the server
// from the stored video and played from images (no video element, no seeking); every sentence shown is a fixed
// server string, never free model text.

(() => {
  const $ = (id) => document.getElementById(id);
  const session = new URLSearchParams(window.location.search).get("session") || "";
  let revision = 0;
  let timer = null;
  let images = [];
  let frames = [];
  let index = 0;
  let rateFps = 10;

  const fileUrl = (key, name) =>
    `/api/replay/file?session=${encodeURIComponent(session)}&key=${encodeURIComponent(key)}&name=${encodeURIComponent(name)}`;

  async function request(path, options) {
    const response = await fetch(path, options);
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
    return payload;
  }

  function stop() {
    if (timer !== null) window.clearInterval(timer);
    timer = null;
  }

  function reset() {
    stop();
    revision += 1;
    images = [];
    frames = [];
    $("part-replay").hidden = true;
    $("part-replay-list").replaceChildren();
    $("part-replay-state").textContent = "";
    $("part-replay-player").hidden = true;
  }

  function showFrame(next) {
    index = Math.max(0, Math.min(frames.length - 1, next));
    $("part-replay-frame").src = images[index].src;
    $("part-replay-scrub").value = String(index);
    $("part-replay-time").textContent =
      `Frame ${index + 1} of ${frames.length} · observed video time ${frames[index].t_s.toFixed(1)} s`;
  }

  function play(step) {
    stop();
    if (frames.length === 0) return;
    if ((step < 0 && index === 0) || (step > 0 && index === frames.length - 1)) showFrame(step < 0 ? frames.length - 1 : 0);
    timer = window.setInterval(() => {
      const next = index + step;
      if (next < 0 || next >= frames.length) {
        stop();
        return;
      }
      showFrame(next);
    }, 1000 / rateFps);
  }

  function showCompare(moment, player) {
    const compare = $("part-replay-compare");
    compare.hidden = !(player.before && player.after);
    if (compare.hidden) return;
    $("part-replay-before").src = fileUrl(moment.key, player.before.file);
    $("part-replay-after").src = fileUrl(moment.key, player.after.file);
    $("part-replay-split").value = "50";
    $("part-replay-after").style.clipPath = "inset(0 0 0 50%)";
    $("part-replay-compare-times").textContent =
      `Left: earlier view, observed video time ${player.before.t_s.toFixed(1)} s. ` +
      `Right: later view, observed video time ${player.after.t_s.toFixed(1)} s.`;
  }

  function showPlayer(moment) {
    const player = moment.player;
    frames = player.frames;
    rateFps = player.rate_fps;
    images = frames.map((frame) => {
      const image = new Image();
      image.src = fileUrl(moment.key, frame.file);
      return image;
    });
    $("part-replay-label").textContent = moment.label_fixed;
    $("part-replay-meaning").textContent = moment.meaning_text +
      (player.skipped_frames ? ` ${player.skipped_frames} frame(s) around it failed sheet registration and are not shown.` : "");
    $("part-replay-scrub").max = String(frames.length - 1);
    showCompare(moment, player);
    $("part-replay-player").hidden = false;
    $("part-replay-player").focus();
    showFrame(frames.length - 1);
  }

  $("part-replay-close").addEventListener("click", () => {
    stop();
    $("part-replay-player").hidden = true;
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !$("part-replay-player").hidden) $("part-replay-close").click();
  });

  async function open(partId, current) {
    $("part-replay-state").textContent = "Preparing the recorded frames from the stored video…";
    $("part-replay-player").hidden = true;
    stop();
    try {
      const moment = await request("/api/replay/prepare", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ session, part: partId }),
      });
      if (current !== revision) return;
      if (!moment.player) {
        $("part-replay-state").textContent = moment.message || "No recorded moment can be shown for this part.";
        return;
      }
      $("part-replay-state").textContent = "";
      showPlayer(moment);
    } catch (error) {
      if (current === revision) $("part-replay-state").textContent = error.message;
    }
  }

  function entry(moment, label, current) {
    const item = document.createElement("li");
    const name = `${label || moment.label || "Part"} (${moment.part_id})`;
    if (moment.status === "no_moment" || moment.status === "unregistered" || !moment.key) {
      item.textContent = `${name}: ${moment.message}`;
      return item;
    }
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = "Show recorded moment";
    button.addEventListener("click", () => open(moment.part_id, current));
    item.append(`${name} — observed video time ${moment.observed_s.toFixed(1)} s`, button);
    return item;
  }

  function targets(result) {
    const groups = result.belongs_here || [];
    if (groups.length) {
      return groups.flatMap((group) => group.record_ids.map((id) => ({ id, label: group.label })));
    }
    return result.origin && result.origin.record_id ? [{ id: result.origin.record_id, label: null }] : [];
  }

  async function onResult(event) {
    reset();
    const current = revision;
    const wanted = targets(event.detail || {});
    if (wanted.length === 0) return;
    try {
      const view = await request(
        `/api/replay?session=${encodeURIComponent(session)}&parts=${encodeURIComponent(wanted.map((t) => t.id).join(","))}`);
      if (current !== revision) return;
      const labels = new Map(wanted.map((t) => [t.id, t.label]));
      $("part-replay-list").replaceChildren(...view.moments.map((m) => entry(m, labels.get(m.part_id), current)));
      $("part-replay").hidden = false;
    } catch (error) {
      if (current === revision) {
        $("part-replay-state").textContent = error.message;
        $("part-replay").hidden = false;
      }
    }
  }

  window.addEventListener("anastylos:part-selected", reset);
  window.addEventListener("anastylos:part-help-result", onResult);
  $("part-replay-reverse").addEventListener("click", () => play(-1));
  $("part-replay-forward").addEventListener("click", () => play(1));
  $("part-replay-pause").addEventListener("click", stop);
  $("part-replay-scrub").addEventListener("input", (event) => {
    stop();
    showFrame(Number(event.target.value));
  });
  $("part-replay-split").addEventListener("input", (event) => {
    $("part-replay-after").style.clipPath = `inset(0 0 0 ${event.target.value}%)`;
  });
})();
