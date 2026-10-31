# Session report export

Read-only export of one agent session to a standalone English HTML file:
embedded CSS, no network, no scripts, no model calls. Open it in a browser,
print it, share it. Inputs are never modified or repaired.

## Stable call

CLI (exit 0 exported, 1 stopped, 2 usage error):

```powershell
python tools/agent/report_cli.py --session <dir> --out <report.html>
```

Python (for the UI agent; `tools/agent` on `sys.path`):

```python
from report import export
result = export(session_dir, out_html)  # raises ReportError / SessionError
```

Result (`anastylos-session-report-export@1`): `report` (path as given),
`report_sha256` (bytes of the written HTML), `session` (folder name only),
`object`, `memory_sha256`, `package_sha256` (`None` without a guide),
`guide` (`"fresh"` or `"none"`), `parts`, `install_steps` (`None` without a
guide), `open_questions`, `left_unresolved`, `assembly` (`None` without a
guide, else `complete` / `steps_done` / `steps_total` / `current_step`).

## Freshness (fail-closed)

- A recorded guide is checked with the existing loaders before anything is
  rendered: package bytes vs the build record, current memory vs the memory
  the package was built from.
- A stale or tampered guide stops the export (`STOPPED: ... run
  `guide build` again.`) and writes nothing; an existing report file is left
  untouched.
- A session whose guide was never built exports without an install plan,
  dependency statement or unknowns list, with a visible notice. Nothing is
  derived to fill the gap.

## Sections (HTML anchors)

`data-state` (session, phases, bound videos, memory and build hashes),
`parts` (origin kept separate from storage, per-part basis and missing
fields), `removal-order`, `assembly-plan` (recorded steps plus a "not a
verified assembly sequence" notice, or an explicit no-plan notice),
`open-questions` (open/unresolved questions, unknowns, missing fields),
`progress` (operator `done` vs return-checked `verified` / mismatch),
`provenance` (file hashes, per-part evidence references), `figures` (why no
images are embedded).

## Limits (binding)

- No images are embedded: the session binds evidence by pair, region and
  hash, not by confirmed frame pictures. References are cited as stored;
  files are never opened at export and no frame-to-record confirmation is
  claimed.
- The install order is the reverse of the recorded removal order unless a
  dependency was confirmed (none is); it is labelled a proposal, never a
  mechanically checked sequence. Part identity is never verified.
- Operator prose is quoted verbatim and stays an operator statement.
  Automatic links carry their rule (`inferred_by`, producer, assumptions) or
  say the conclusion is unrecorded. Missing data is shown as missing.
- Absolute paths never reach the report (file names and short hashes only).
  All input text is HTML-escaped. Repeated exports of unchanged inputs are
  byte-identical (no export timestamp).

## Reproducible flow (existing commands)

From a prepared agent session outside the repo to the report:

```powershell
python tools/agent/anastylos.py status --session <dir>
python tools/agent/anastylos.py guide build --session <dir>
python tools/agent/report_cli.py --session <dir> --out <dir>-report.html
python tools/agent/report_cli.py --session <dir> --out <dir>-report-2.html
fc /b <dir>-report.html <dir>-report-2.html   # identical bytes expected
```

Open the HTML in a browser and print it from there. If the export stops
with a stale guide, run `guide build` again (explicit rebuild; old answers
and progress stay in their logs) and re-export. The full session flow
(`new`, `phase`, `ask`, `declare`, `guide`, `assemble`) is unchanged; see
`tools/agent/README.md`.

## Tests

```powershell
cd tools/agent
python -m unittest tests.test_report
python -m unittest discover -s tests   # full agent suite
```
