# UI notes for the TL — `web/styles.css` redesign

`styles.css` was rewritten CSS-only against the existing class names and ids.
Some states cannot be told apart from the current markup; the stylesheet works
around that with structural `:has()` selectors and also contains **inert
selectors for future attributes**. Requested markup changes (none applied here):

## 1. Voice bar states (`index.html` / `app.js`)

Today only `#talk-button.recording` exists, and the wake toggle is a checkbox.
`thinking` ("Transcribing …") and `speaking` cannot be seen from CSS.

- Set `data-state="idle|listening|thinking|speaking"` on `#voice-bar`
  (`startTalking` → listening, between `stopTalking` and the transcript →
  thinking, while `speechSynthesis.speaking` → speaking, otherwise idle).
- CSS is ready: `#voice-bar[data-state=…]` colours the state dot and border
  (listening red, thinking amber, speaking blue, each pulsing unless
  `prefers-reduced-motion`).

## 2. Drawing job state hooks (`drawingCard` in `app.js`)

Job cards are styled from their children (`> form` = unconfirmed, `> button`
= paused/failed, `> .error` = failed, `> ul` = needs capture, `> .notice` =
decision recorded, none of these = running). That is exact for today's
`drawingCard`, but fragile and cannot express `NEEDS_REVIEW`, a rejected vs a
confirmed binding, or `drawn_unconfirmed` vs `NEEDS_CONFIRMATION`.

- Add `card.dataset.state = job.state` and `card.dataset.status = job.status`
  (and `card.dataset.binding = job.binding.decision` when bound).
- CSS already honours `.drawing-job[data-state="running|paused|failed|confirmed"]`
  and `[data-status="NEEDS_CAPTURE|NEEDS_CONFIRMATION|drawn_unconfirmed"]`.
  Once the attributes exist, the chip labels (currently generated with
  `::before { content }`) should become real text, because generated content
  is announced inconsistently by screen readers.

## 3. Generated labels that should be real markup

`#prompt-card::before` ("Current question"), `#confirm-card::before`
("Read-back · nothing is recorded without yes"), `#current-step-card::before`
("Current step") and the `.drawing-job::before` state chips are CSS-generated
English strings. They are correct for today's structure; move them into the
markup if the wording should be translatable or tested.

## 4. Optional

- `#question-states` and `#question-rows` cells: a `data-state` on the `<td>`
  (OPEN / ASKED / COMMITTED) would allow state colours in the question table.
- Section order: the layout places sections by
  `section[aria-labelledby=…]`; if sections are added or renamed the grid
  placement block in `styles.css` ("page grid") needs the new heading id.

## Layout facts

- Two columns from 64 rem: open question | session; part drawings full width;
  guide | selected part; reassembly | capture needs; report full width.
  Below 64 rem everything stacks.
- Inside "Open questions" the current question / read-back card is reordered
  above the question table with flex `order`; the table is a short scroll box.
- Drawing jobs are sorted by state with flex `order`: running, unconfirmed,
  paused/failed, others, recorded decisions last. Viewers are capped to
  `100vh - 13rem` so drawing, photo overlay and decision buttons share a screen
  at 1920×1080.
- Voice bar is sticky; its long hint is a popover shown on hover/focus
  (`aria-describedby` on the talk button is unchanged).
- Root font size is 16 px, 18 px from 1700 px width.
