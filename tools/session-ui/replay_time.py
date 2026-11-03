"""Which video time a part's replay is built around: the storage cell's settle time, or the detector's window.

The detector's first deposit window in a cell can be a false hit (the green button showed a screw being removed, the blue
button a membrane). ``cell_settle_worker.py`` measures once per video when each storage cell of the sheet changed for good
against the empty sheet at the start of the video; this module decides, from that measurement, the time a part is shown at.
Memory is never changed: ``t_s`` stays as recorded and the replay states where its time comes from.

Basis of the time (``decide``):

- ``storage_cell_settle``: the part is the only one in its cell; the time is the cell's settle time, measured.
- ``detector_fitting_cell``: several parts share the cell; the time is the start of the detector deposit window that fits
  best at or after the cell's first lasting change (a part's own window if it fits, else the earliest unclaimed one of the
  cell's windows, parts taken in id order). With no such window the part's moment is *not known*.
- ``detector_estimate``: nothing can check the detector time (no measurement yet, no usable empty-sheet reference, no cell,
  the cell was not empty at the start, or the recorded time is a removal window the operator chose); the text says so.
- ``unknown``: the cell shows no lasting change, or no window fits it: no clip is shown instead of a wrong one.
"""

from __future__ import annotations

import json
from pathlib import Path

SETTLE_DIR = "cell-settle"
SETTLE_VERSION = 2  # 2: cells carry first_s, the first lasting change
SETTLE_SCHEMA = "anastylos-cell-settle@1"
FIT_TOLERANCE_S = 2.0  # the cell is sampled once per second; a window may start this much before the measured change

SETTLED = ("Video time {t:.1f} s: the storage cell {cell} changed for good against the empty sheet here, so this part had "
           "been put down by then. Time measured from the storage cell; the recorded detector time {d:.1f} s is not used.")
FITTING = ("Video time {t:.1f} s is the start of the detector window that fits the storage cell {cell} (its content first changed "
           "for good at {s:.1f} s; several parts share this cell).")
ESTIMATE = " This time is a detector estimate; it could not be checked against the storage cell: {reason}."
REASONS = {
    "not_measured": "the storage cells of this video have not been measured yet",
    "no_reference": "the video has no usable empty-sheet reference ({detail})",
    "no_cell": "this part has no storage cell on the sheet",
    "cell_not_empty": "its storage cell was not empty at the start of the video",
    "removal_time": "the recorded time is a removal window the operator chose, which a storage cell cannot check",
}
UNKNOWN_EMPTY = "This part's moment is not known: its storage cell {cell} shows no lasting change against the empty sheet."
UNKNOWN_NO_WINDOW = ("This part's moment is not known: no detector window fits the lasting change of its storage cell "
                     "{cell} ({s:.1f} s).")


def settle_path(session_dir: Path, video_sha256: str) -> Path:
    return session_dir / "evidence" / SETTLE_DIR / f"{video_sha256[:12]}-v{SETTLE_VERSION}.json"


def read_settle(session_dir: Path, video_sha256: str) -> dict | None:
    """The cached measurement of this video, or None when missing, unreadable or taken from another video."""
    try:
        document = json.loads(settle_path(session_dir, video_sha256).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    ok = isinstance(document, dict) and document.get("schema") == SETTLE_SCHEMA and document["video"]["sha256"] == video_sha256
    return document if ok else None


def _estimate(reason: str, anchor_s: float, base_text: str, **detail) -> dict:
    return {"basis": "detector_estimate", "anchor_s": anchor_s, "settled_s": None,
            "text": base_text + ESTIMATE.format(reason=REASONS[reason].format(**detail)), "message": ""}


def decide(*, settle: dict | None, cell: str | None, meaning: str, detector_s: float, base_text: str, this_id: str,
           cell_parts: dict[str, tuple | None], pool: list[tuple]) -> dict:
    """The basis, anchor time (None when unknown) and text for one part.

    ``cell_parts`` maps every current part stored in the cell to its own deposit window ``(start, end)`` (None when it has
    none), and ``pool`` lists the deposit windows of all questions about the cell; ``base_text`` is the text for the detector
    time, used when the time cannot be checked. A window fits when it ends at or after the cell's first lasting change (the
    deposit happens somewhere inside it); the time is then the later of its start and that change.
    """
    if settle is None:
        return _estimate("not_measured", detector_s, base_text)
    reference = settle["reference"]
    if reference["status"] != "ok":
        return _estimate("no_reference", detector_s, base_text, detail=reference.get("reason") or "none found")
    if cell is None or cell not in settle["cells"]:
        return _estimate("no_cell", detector_s, base_text)
    measured = settle["cells"][cell]
    if not measured["reference_ok"]:
        return _estimate("cell_not_empty", detector_s, base_text)
    if meaning != "deposit_window_start":
        return _estimate("removal_time", detector_s, base_text)
    if not measured["occupied"] or measured["settled_s"] is None:
        return {"basis": "unknown", "anchor_s": None, "settled_s": None, "text": "", "message": UNKNOWN_EMPTY.format(cell=cell)}
    settled = measured["settled_s"]
    if len(cell_parts) <= 1:
        return {"basis": "storage_cell_settle", "anchor_s": settled, "settled_s": settled, "message": "",
                "text": SETTLED.format(t=settled, cell=cell, d=detector_s)}
    first = measured.get("first_s") or settled
    limit = first - FIT_TOLERANCE_S
    own = {pid: tuple(w) for pid, w in cell_parts.items() if w is not None and w[1] >= limit}
    free = sorted(w for w in {tuple(w) for w in pool} if w[1] >= limit and w not in own.values())
    needing = sorted(pid for pid in cell_parts if pid not in own)
    window = own.get(this_id, dict(zip(needing, free)).get(this_id))
    if window is None:
        return {"basis": "unknown", "anchor_s": None, "settled_s": settled, "text": "",
                "message": UNKNOWN_NO_WINDOW.format(cell=cell, s=first)}
    anchor = round(max(window[0], first), 3)
    return {"basis": "detector_fitting_cell", "anchor_s": anchor, "settled_s": settled, "message": "", "window": list(window),
            "text": FITTING.format(t=anchor, cell=cell, s=first)}
