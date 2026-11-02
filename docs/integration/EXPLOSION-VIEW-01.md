# EXPLOSION-VIEW-01 — first usable explosion view

Branch `claude/explosion-view-5ad7ef` (cut from `integration/northstar`).
Technical contracts stay in [`ARCHITECTURE.md`](../../ARCHITECTURE.md); module docs:
[`tools/explosion/README.md`](../../tools/explosion/README.md).

## 1. Verdict on the explosion-sketch-m1 work (`assembly-reconstruction/`)

Assessed 2026-11-01 against the actual code (69 tests pass). Rule applied: the
new view must be built from **image-backed, operator-bound part drawings**; the
old work is memory-only.

| Function (commits fc454bde, 918318e7) | Verdict | Why |
|---|---|---|
| `schematic.py` / `controller-schematic-v1` (three assemblies, `profile_mm`, `explode_offset_mm`, `schematic` CLI) | **superseded, not integrated** | Profiles and offsets are hand-authored numbers in a package file (`geometry_status: schematic_from_observed`); no outline from any image, so it says "observed" about invented shapes. The new composer draws only drawing-proof outlines. Kept idea: one part model with an explosion factor (0 = assembled), which the viewer reproduces by toggling placement. |
| `render.py` technical explosion sketch (cards per observation, `SUBSTITUTE` boxes for declared parts, balloons, `layout_px`) | **superseded** | A review of memory events, not of parts: N observations are not N parts, declared removals have no geometry and get placeholder cards. Fine as an observation review; wrong basis for an explosion drawing. |
| `contour.py` (mask PNG to scaled contour) | **superseded** | Contours are scaled to a card size (no mm). `tools/drawing-proof` already yields SAM outlines in mm with a stated deviation. |
| `projector.py` declared-removal projection (`kind: declared_removal`, `declaration.phase/operator_label`, `storage`, `compatible_groups`) | **reusable later, not now** | It is the right *source* for removal order, labels and phases once a job is linked to a declared removal. No such link exists (`binding.json` carries only the part text), and removal order is not the physical layer order. Not wired: it would add a guessed mapping. Follow-up: an operator-confirmed `job -> declared removal` link, then `propose_layout` can pre-fill labels and order from it. |
| ASSEMBLY_STATE_V1 schema / `schema.py` | **not needed** | The explosion manifest is a derived view with its own small schema (`anastylos-explosion-manifest@1`); no Assembly State input. |
| Text/XML helper patterns (`_text`, escape, deterministic number formatting) | **pattern reused, no import** | Re-implemented in 10 lines in `compose.py`; keeps `tools/explosion` free of a dependency on a module with an unrelated contract. |

Nothing from the old branch was imported. `assembly-reconstruction/` is unchanged.

## 2. What was built

- `tools/explosion/` — composer, layout spec and operator functions (README there).
- `tools/session-ui/explosion.py`, `web/explosion.html|js|css` — read-only viewer.
- Spec file `<session>/drawings/explosion.json` (anastylos-explosion-layout@1);
  outputs `<session>/drawings/explosion/{explosion.svg,manifest.json}` when
  written by the CLI (the viewer composes in memory and writes only layout
  decisions).

## 3. Routes for the TL (`server.py`; not wired by this branch)

All use the existing helpers (`_session_param`, `_send_json`, `_send_bytes`,
`_read_json`, `_mutate`, `AdapterError`). `explosion` is `tools/session-ui/explosion.py`.

```python
# STATIC += {"explosion.html": "text/html; charset=utf-8", "explosion.js": "text/javascript; charset=utf-8",
#            "explosion.css": "text/css; charset=utf-8"}
# do_GET routes: "/explosion.html", "/explosion.js", "/explosion.css" -> self._send_static(name)

# GET /api/explosion?session=<id>
def _api_explosion(self):
    self._send_json(explosion.explosion_view(Handler.workspace_root, self._session_param(self._query())))

# GET /api/explosion/file?session=<id>&name=<name>[&job=<job>][&mode=exploded|assembled]
def _api_explosion_file(self):
    q = self._query()
    body, content_type = explosion.explosion_bytes(Handler.workspace_root, self._session_param(q), q.get("name", ""),
                                                   q.get("job", ""), q.get("mode", "exploded"))
    self._send_bytes(body, content_type)

# POST /api/explosion/layout   {"session", "action": "propose"|"confirm"|"reorder", ...}
def _api_explosion_layout(self):
    body = self._read_json(); session_id = body.get("session", "")
    if not session_id: raise AdapterError("The session parameter is missing.", 400)
    self._mutate(session_id, lambda: explosion.layout_action(Handler.workspace_root, session_id, body))
```

Signatures: `explosion_view(root, session_id) -> dict`,
`explosion_bytes(root, session_id, name, job="", mode="exploded") -> (bytes, content_type)`
(`name=explosion.svg` without job is the composed drawing; with a job:
`drawing.svg`, `overlay.jpg`, `rectified.png`), `layout_action(root, session_id, body) -> dict`
(`propose` {} · `confirm` {job, label?, view_side?, flip_horizontal?, confirmed?=true} ·
`reorder` {jobs: [all layer jobs top to bottom]}; every answer is the refreshed view plus `action`).
A scratch harness that wires exactly these onto `server.Handler` was used for the
browser check (see notes folder `explosion-view-01.md`).

**Hook for the assembly-help lane:** the page dispatches
`window` event `anastylos:part-selected` with `detail {job, label}` (both `null`
when cleared); `window.anastylosExplosion.selectedJob()` returns the current job.
Link from the main page: `/explosion.html?session=<id>`.

**Confirmation flow the TL can drive instead of the page's own panel:** call
`explosion.layout_action` with `propose`, then `confirm` per layer after the
operator confirmed label, view side (and mirroring) in the UI; `reorder`
withdraws the confirmation of moved layers. Part binding stays the existing
`/api/drawing/bind`.

## 4. Verification (2026-11-01)

- `tools/explosion`: `python -m unittest discover -s tests` — 25 pass.
- `tools/session-ui`: full suite and the 7 new explosion tests — see report.
- Browser check on a copy of the demo workspace (scratch harness, port 8072):
  propose → click part → side panel with status/binding/links/thumbnail →
  assembled toggle; plus a synthetic three-layer session (confirmed, unbound,
  mirrored). Preview of the real front-shell job alone: `C:\tmp\anastylos-explosion-view\`
  (`front-shell-only-{exploded,assembled}.svg`, manifest). Demo workspace not
  modified (spec kept in memory for the preview).
- Not validated: any real multi-part run (only one real drawing exists, and it
  is unbound), touch use, large layer counts (>8).

## 5. Open points

- Spec and manifest are different files in different folders (`drawings/explosion.json`
  vs `drawings/explosion/manifest.json`) because the brief named both `explosion.json`.
- `rectified.png` in the brief is `chosen_rectified.png` in real job folders;
  the reader accepts both (`rectified.png` first).
- Initial layer order is a front/back word heuristic and always unconfirmed.
- No height/thickness exists in any drawing: the view has no depth information
  by design; do not add any without a measured source.
- Suggested status line for `ARCHITECTURE.md` (integration owner applies it):
  | `tools/explosion/` | schematic explosion drawing from operator-bound part drawings + operator-confirmed layer layout; viewer in `tools/session-ui/explosion.py` | `compose.py` | branch `claude/explosion-view-5ad7ef` | synthetic tests; one real front-shell drawing previewed; spacing schematic; no real multi-part run |
