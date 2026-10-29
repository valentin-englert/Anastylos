"""Human-readable Markdown summary of one inspection (derived from the JSON)."""

from __future__ import annotations

from pathlib import Path


def _fmt(value) -> str:
    return "—" if value is None else str(value)


def render(record: dict) -> str:
    image = record.get("image") or {}
    unc = record.get("uncertainty") or {}
    lines = [
        f"# Microscopy inspection {record['inspection_id']}",
        "",
        f"- Overall status: **{_fmt(unc.get('overall_status'))}** (human review: "
        f"{record['human_review']['status']})",
        f"- Image: `{_fmt(image.get('file_name'))}` sha256 `{_fmt(image.get('sha256'))}`",
        f"- Resolution: {_fmt((image.get('resolution') or {}).get('width'))} x "
        f"{_fmt((image.get('resolution') or {}).get('height'))}",
        f"- ROI: {_fmt(record['roi']['xywh'])} ({record['roi']['coordinates']})",
        f"- Calibration: {record['calibration']['status']}"
        + (f" ({record['calibration']['pixels_per_mm']} px/mm, {record['calibration'].get('provenance')})"
           if record["calibration"].get("valid") else ""),
        f"- Source: {record['source']['origin']}, camera {_fmt(record['source']['camera_id'])}, "
        f"validation class `{record['source']['validation_class']}`",
        f"- Mode: {record['selection']['mode']} -> readers {record['selection']['readers']}",
        "",
        "## Readers",
        "",
        "| Reader | Status (native) | Reason | Code matches pinned rev |",
        "|---|---|---|---|",
    ]
    for block in record.get("recognizers") or []:
        lines.append(f"| {block['reader']} | {block['status']} ({_fmt(block['native_status'])}) | "
                     f"{_fmt(block['reason'])} | {_fmt(block['matches_pinned_revision'])} |")
    for block in record.get("recognizers") or []:
        ca = block.get("core_assessment") or {}
        if ca.get("action") == "downgraded":
            lines.append(f"| {block['reader']} | Core assessment: native `ok` held as **ambiguous** "
                         f"(failed: {', '.join(ca.get('failed_checks') or [])}) | native candidate kept "
                         "for review | — |")
    for skipped in record["selection"].get("skipped") or []:
        lines.append(f"| {skipped['reader']} | not run | {skipped['reason']} | — |")
    lines += ["", "## Candidates (unconfirmed)", ""]
    if record.get("candidates"):
        for cand in record["candidates"]:
            extra = cand.get("value") or cand.get("score")
            lines.append(f"- [{cand['reader']}] {cand.get('identification')}"
                         + (f" — {extra}" if extra is not None else ""))
    else:
        lines.append("- none")
    conflicts = (record.get("reconciliation") or {}).get("conflicts") or []
    if conflicts:
        lines += ["", "## Conflicts", ""]
        for conflict in conflicts:
            lines.append(f"- {conflict['domain']}: " + ", ".join(
                f"{v['reader']}={v['value']}" for v in conflict["values"]))
    lines += ["", "## Uncertainty", ""]
    lines += [f"- {reason}" for reason in unc.get("reasons") or []]
    lines += [f"- quality flag `{f['flag']}`: {f['detail']}" for f in unc.get("quality_flags") or []]
    lines += ["", f"> {unc.get('statement', '')}", ""]
    return "\n".join(lines)


def write_report(record: dict, path: Path) -> None:
    Path(path).write_text(render(record), encoding="utf-8")
