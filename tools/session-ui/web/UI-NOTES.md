# UI notes for the TL — Anastylos desktop shell (branch `claude/desktop-ui-d8d8ea`)

Replaces the CSS-only notes of b96e5401. The owner asked for a different UX, so `index.html`, `styles.css`,
a new `shell.js` and small additions to `server.py` changed. `app.js` is **untouched**; every id/class it uses
still exists, so the capture-loop / voice / explosion lanes only conflict in `index.html` (structure) and `server.py`.

## What changed
- **Shell:** top bar (logo + "Anastylos", voice pill, Muse status pill, help "i") and a horizontal pill menu;
  one page at a time (`#/questions`, `#/drawings`, …), Home = start screen with numbered cards, every page has a Back pill.
  `shell.js` only shows/hides the existing `<section data-page=…>` elements; routing is `location.hash`.
- **Less text:** every `.hint`, the offline banner and the footer are hidden on the pages; `shell.js` moves their text into the
  "i" help overlay (small panel, no new screen). Voice hint text lives there too.
- **Theme:** dark palette of `AnastylosTheme.kt` (ochre accent, paper text), Khula (woff2 latin 400/600/700 + OFL text in
  `web/fonts/`), pills and soft radii, no grid layout. Job-state colours: running = Muse blue, paused = lilac,
  needs capture / unconfirmed = ochre (unconfirmed dashed), decision recorded = sage, failed = brick.
- **Welcome guide** (`<dialog id="onboarding">`, English, 5 steps): welcome, navigation, voice, **Connect Muse**, done.
  "Show this guide when I open Anastylos" is a checkbox (localStorage `anastylos.guide.hide`); "i → Replay the welcome guide"
  and the Muse pill reopen it. Connect Muse: link to https://dev.meta.ai/, copyable `setx MODEL_API_KEY "…"` command,
  the click-path through the Windows environment dialog, restart hint and a **Check connection** button.
- **Launch animation:** the five logo fragments from the website (`logo-fragments.ts`, same scatter offsets and
  base-first order) assemble in CSS keyframes, once per browser session, skipped with `prefers-reduced-motion`, click to skip.
  Favicon = `assets/favicon.svg` (same fragments on the app-icon ground).
- **Server:** `/api/connection` → `{"muse_key_set": bool}` (reads `MODEL_API_KEY` presence only, never the value);
  static serving of `shell.js`, `web/assets/*`, `web/fonts/*` (fixed extensions, no traversal). Tests added (47 pass).
- **1-click start:** `tools/session-ui/launch/` — `Start-Anastylos.bat/.ps1`, `Stop-Anastylos.bat/.ps1`,
  `Create-Shortcuts.bat/.ps1` (desktop icons with `anastylos.ico`). Env overrides `ANASTYLOS_WORKSPACE`, `ANASTYLOS_PORT`
  (default 8071). Start was tested on port 8092; the Stop dialog and the shortcut creation were not run.

## Needs the TL / other lanes
1. **Receiver start:** `Start-Anastylos.ps1` runs an optional `launch/receiver.cmd` if present. The receiver needs init, certificate,
   host and the new QR pairing from the northstar branch — I did not guess its command. Please add `receiver.cmd` (and a stop
   rule) there.
2. **Compliance:** Khula is already in northstar's SPDX/NOTICES for the APK. Please extend that entry's comment with
   `tools/session-ui/web/fonts/` (OFL text is bundled next to the fonts). I did not touch `spdx.json`/notices to avoid conflicts.
3. **Voice states:** still only `#talk-button.recording` and the wake checkbox. For thinking/speaking add
   `data-state="idle|listening|thinking|speaking"` to `#voice-bar`; CSS is ready.
4. **Drawing job hooks:** state chips are derived from the card's children. Add `dataset.state`, `dataset.status`,
   `dataset.binding` in `drawingCard`; CSS already honours them. Chip text and the card eyebrows ("Your turn",
   "Did we hear you right?", "Current step") are CSS-generated English strings — move into markup if they must be testable.
5. **Screenshots for the API-key guide are not made.** The guide is text + a copyable command. Real screenshots of the
   dev.meta.ai key page and the Windows dialog need the owner's account/screen; place them in `web/assets/` and reference
   them from `renderConnectStep` in `shell.js`.
6. **Phone pairing:** nothing in this UI mentions pairing; the QR flow lives on the northstar branch.
7. **Wording:** the drawing job head line still shows raw `job: state / status` (e.g. `done / NEEDS_CONFIRMATION`) from `app.js`.
   Plain-language labels would help the target audience.
8. **Not verified:** real microphone, a running drawing job, Windows high-contrast mode, screen reader pass.
