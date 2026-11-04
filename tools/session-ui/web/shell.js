/* Shell of the session UI: menu, start screen, one page at a time, help overlay, welcome guide,
   launch animation. It only shows and hides the existing sections; the logic of every section stays in
   app.js. All text is assigned through textContent. */
"use strict";

(() => {
  const $ = (id) => document.getElementById(id);
  const make = (tag, className, text) => {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  };
  const store = {
    get(key) { try { return window.localStorage.getItem(key); } catch (error) { return null; } },
    set(key, value) { try { window.localStorage.setItem(key, value); } catch (error) { /* private mode */ } },
  };

  /* ------------------------------------------------------------------ pages */

  /* Visible steps follow the demo workflow. Pages flagged `retired` keep their section and code in the document
     but are not offered; add ?all-steps to the address to bring them back. */
  const PAGES = [
    { id: "phone", label: "Phone", blurb: "Pair the phone, name the session, see its videos" },
    { id: "session", label: "Session", blurb: "Open the processed session" },
    { id: "questions", label: "Questions", blurb: "Answer what the assistant asks and save to memory" },
    { id: "drawings", label: "Drawings", blurb: "Outline parts and see the video moments" },
    { id: "belong", label: "Where does it belong?", blurb: "Ask about a part in the explosion view", href: "/explosion.html" },
    { id: "rebuild", label: "Rebuild", blurb: "Put it back, step by step" },
    { id: "report", label: "Report", blurb: "Save a summary file" },
    { id: "guide", label: "Guide", blurb: "Ask where a part goes", retired: true },
    { id: "part", label: "Selected part", blurb: "Origin and storage of one part", retired: true },
    { id: "photos", label: "Photos needed", blurb: "See what is still missing", retired: true },
  ];
  const showRetired = new URLSearchParams(location.search).has("all-steps");
  const VISIBLE = PAGES.filter((page) => showRetired || !page.retired);
  const INFO = {
    home: ["Anastylos helps you take a device apart and put it back together. Photos and your answers are the proof.",
           "Answers come from stored facts. Muse is only used for your voice and for part drawings.",
           "Everything on these pages is shown as plain text. Nothing is changed unless you confirm it."],
  };
  const sections = new Map();
  for (const node of document.querySelectorAll("main > section[data-page]")) sections.set(node.dataset.page, node);

  function buildMenu() {
    const nav = $("main-nav");
    for (const page of VISIBLE) {
      const link = make("a", "menu-link", page.label);
      link.href = page.href || `#/${page.id}`;
      link.dataset.page = page.id;
      nav.append(link);
    }
    const cards = $("home-cards");
    VISIBLE.forEach((page, index) => {
      const card = make("a", "card");
      card.href = page.href || `#/${page.id}`;
      card.append(make("span", "card-number", String(index + 1).padStart(2, "0")), make("span", "card-title", page.label),
                  make("span", "card-blurb", page.blurb));
      cards.append(card);
    });
  }

  function decorateSections() {
    for (const [id, section] of sections) {
      if (id === "home") continue;
      const heading = section.querySelector("h2");
      heading.tabIndex = -1;
      const bar = make("div", "page-head");
      const back = make("a", "pill ghost back", "‹ Back");
      back.href = "#/";
      heading.before(bar);
      bar.append(back, heading);
      INFO[id] = [...section.querySelectorAll(":scope > .hint")].map((hint) => hint.textContent.trim());
    }
    const voiceHint = $("voice-hint");
    if (voiceHint) INFO.voice = [voiceHint.textContent.trim()];
    INFO.footer = [...document.querySelectorAll("#page-footer p")].map((p) => p.textContent.trim());
  }

  function currentPage() {
    const id = location.hash.replace(/^#\/?/, "");
    return sections.has(id) && VISIBLE.some((page) => page.id === id) ? id : "home";
  }

  function route() {
    const page = currentPage();
    for (const [id, section] of sections) section.hidden = id !== page;
    for (const link of document.querySelectorAll(".menu-link")) {
      const active = link.dataset.page === page;
      link.classList.toggle("active", active);
      if (active) link.setAttribute("aria-current", "page"); else link.removeAttribute("aria-current");
    }
    document.body.dataset.page = page;
    const heading = sections.get(page).querySelector("h1, h2");
    if (heading) heading.focus({ preventScroll: true });
    window.scrollTo(0, 0);
    closeInfo();
    refreshHome();
  }

  function refreshHome() {
    const object = $("fact-object").textContent.trim();
    const open = $("question-states").textContent.match(/OPEN: (\d+)/);
    const line = !object ? "No session is open yet. Start with step 1."
      : `${$("fact-name").textContent.trim() ? `${$("fact-name").textContent.trim()} · ` : ""}${object}${open ? ` · ${open[1]} questions still open` : ""}`;
    $("home-status").textContent = line;
  }

  /* ------------------------------------------------------------------ help overlay */

  function openInfo() {
    const body = $("info-body");
    body.replaceChildren();
    const page = currentPage();
    const add = (title, lines) => {
      if (!lines || !lines.length) return;
      body.append(make("h3", "", title));
      for (const line of lines) body.append(make("p", "", line));
    };
    const label = page === "home" ? "This page" : sections.get(page).querySelector("h2").textContent;
    add(label, INFO[page]);
    add("Voice", INFO.voice);
    $("info-panel").hidden = false;
    $("info-button").setAttribute("aria-expanded", "true");
    $("info-close").focus();
  }
  function closeInfo() {
    $("info-panel").hidden = true;
    $("info-button").setAttribute("aria-expanded", "false");
  }

  /* ------------------------------------------------------------------ Muse connection */

  async function checkConnection() {
    const button = $("muse-status");
    try {
      const response = await fetch("/api/connection");
      const { muse_key_set: ready } = await response.json();
      button.dataset.state = ready ? "ok" : "missing";
      $("muse-status-text").textContent = ready ? "Connected" : "Connect Muse";
      return ready;
    } catch (error) {
      button.dataset.state = "unknown";
      $("muse-status-text").textContent = "Muse";
      return false;
    }
  }

  /* ------------------------------------------------------------------ welcome guide */

  const KEY_COMMAND = 'setx MODEL_API_KEY "paste-your-key-here"';
  const steps = [
    { title: "Welcome to Anastylos", lines: [
      "You take a device apart. Anastylos watches, asks a few questions and remembers where every part came from.",
      "Later it shows you how to put everything back — one step at a time.",
      "This short guide takes about two minutes." ] },
    { title: "Find your way", lines: [
      "The round buttons at the top are the menu. Home shows all areas as cards, in the order you will use them.",
      "Every page has a Back button. The small “i” at the top right explains the page you are on.",
      "Nothing is saved until you press a button to confirm it." ] },
    { title: "Talk instead of typing", lines: [
      "Hold the “Hold to talk” button, or the space bar, and speak. Let go when you are done.",
      "Switch on “Wake word” to speak hands-free. The wake words are “Muse” and “Meta”: start with one of them, for example “Muse, next question”.",
      "Switch off “Speak” if you prefer to read the answers." ] },
    { title: "Connect Muse", connect: true, lines: [
      "Muse understands your voice and draws outlines of parts. It needs a personal key from Meta." ] },
    { title: "You are ready", lines: [
      "Start at Home, step 1: connect your phone.",
      "You can open this guide again any time from the “i” button." ] },
  ];
  let stepIndex = 0;

  function renderConnectStep(body) {
    const list = make("ol", "how-to");
    const item = (...nodes) => { const li = make("li"); li.append(...nodes); list.append(li); return li; };
    const link = make("a", "cta-link", "dev.meta.ai");
    link.href = "https://dev.meta.ai/";
    link.target = "_blank";
    link.rel = "noopener";
    const arrow = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    arrow.setAttribute("viewBox", "0 0 24 24");
    arrow.setAttribute("aria-hidden", "true");
    for (const d of ["M7 7h10v10", "M7 17 17 7"]) {
      const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
      path.setAttribute("d", d);
      arrow.append(path);
    }
    link.append(arrow);
    item(make("span", "", "Sign in at "), link, make("span", "", ". Create an account if you do not have one."));
    item(make("span", "", "Open the Dashboard, create an API key and copy it."));
    const command = make("code", "command", KEY_COMMAND);
    const copy = make("button", "pill ghost inline", "Copy command");
    copy.type = "button";
    copy.addEventListener("click", async () => {
      try { await navigator.clipboard.writeText(KEY_COMMAND); copy.textContent = "Copied"; }
      catch (error) { copy.textContent = "Select and copy by hand"; }
    });
    item(make("span", "", "Save the key on this computer. Open PowerShell, replace “paste-your-key-here” with your key and run:"),
         make("div", "command-row", ""));
    list.lastChild.lastChild.append(command, copy);
    list.lastChild.append(make("p", "small", "Or: Windows key → type “environment variables” → Edit environment variables for your account → New → name MODEL_API_KEY → paste the key → OK."));
    item(make("span", "", "Close Anastylos and start it again with its Start icon. A new key is only read at start."));
    body.append(list);
    const check = make("button", "pill primary", "Check connection");
    check.type = "button";
    const result = make("p", "check-result", "");
    result.setAttribute("role", "status");
    check.addEventListener("click", async () => {
      const ready = await checkConnection();
      result.dataset.state = ready ? "ok" : "missing";
      result.textContent = ready ? "Connected. Muse is ready." : "No key found yet. Finish step 3, then restart Anastylos.";
    });
    const row = make("div", "check-row");
    row.append(check, result);
    body.append(row, make("p", "small", "Your key stays on this computer. Anastylos never asks you to type it on these pages."));
  }

  function renderStep() {
    const step = steps[stepIndex];
    $("onboarding-step").textContent = `Step ${stepIndex + 1} of ${steps.length}`;
    $("onboarding-title").textContent = step.title;
    const body = $("onboarding-body");
    body.replaceChildren(...step.lines.map((line) => make("p", "", line)));
    if (step.connect) renderConnectStep(body);
    $("onboarding-back").hidden = stepIndex === 0;
    $("onboarding-skip").hidden = stepIndex === steps.length - 1;
    $("onboarding-next").textContent = stepIndex === steps.length - 1 ? "Start" : "Next";
  }

  function openOnboarding(at = 0) {
    stepIndex = at;
    $("onboarding-again").checked = store.get("anastylos.guide.hide") !== "1";
    renderStep();
    if (!$("onboarding").open) $("onboarding").showModal();
  }
  function closeOnboarding() {
    store.set("anastylos.guide.hide", $("onboarding-again").checked ? "0" : "1");
    if ($("onboarding").open) $("onboarding").close();
    checkConnection();
  }

  /* ------------------------------------------------------------------ launch animation */

  function playLauncher() {
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    let seen = false;
    try { seen = window.sessionStorage.getItem("anastylos.launched") === "1"; } catch (error) { /* ignore */ }
    if (reduced || seen) return Promise.resolve();
    try { window.sessionStorage.setItem("anastylos.launched", "1"); } catch (error) { /* ignore */ }
    const launcher = $("launcher");
    launcher.hidden = false;
    document.body.classList.add("launching");
    return new Promise((resolve) => {
      const done = () => { launcher.classList.add("leaving"); setTimeout(() => { launcher.hidden = true; document.body.classList.remove("launching"); resolve(); }, 500); };
      launcher.addEventListener("click", done, { once: true });
      setTimeout(done, 4800);
    });
  }

  /* ------------------------------------------------------------------ wiring */

  buildMenu();
  decorateSections();
  window.addEventListener("hashchange", route);
  route();
  new MutationObserver(refreshHome).observe($("session-status"), { subtree: true, childList: true, characterData: true, attributes: true });

  $("info-button").addEventListener("click", () => ($("info-panel").hidden ? openInfo() : closeInfo()));
  $("info-close").addEventListener("click", closeInfo);
  $("info-replay").addEventListener("click", () => { closeInfo(); openOnboarding(0); });
  $("info-connect").addEventListener("click", () => { closeInfo(); openOnboarding(3); });
  $("muse-status").addEventListener("click", () => openOnboarding(3));
  document.addEventListener("keydown", (event) => { if (event.key === "Escape") closeInfo(); });

  $("onboarding-next").addEventListener("click", () => {
    if (stepIndex === steps.length - 1) { closeOnboarding(); return; }
    stepIndex += 1;
    renderStep();
  });
  $("onboarding-back").addEventListener("click", () => { stepIndex = Math.max(0, stepIndex - 1); renderStep(); });
  $("onboarding-skip").addEventListener("click", closeOnboarding);
  $("onboarding").addEventListener("cancel", closeOnboarding);

  checkConnection();
  playLauncher().then(() => { if (store.get("anastylos.guide.hide") !== "1") openOnboarding(0); });
})();
