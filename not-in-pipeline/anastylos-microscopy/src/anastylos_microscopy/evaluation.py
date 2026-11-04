"""MIC-01..MIC-05 evaluation harness (dataset anastylos-microscopy-dataset/1).

The harness has no tunable thresholds of its own: every verdict comes from
the unchanged readers. Splits:

* ``dev``     - free to inspect while developing.
* ``heldout`` - only scored with ``frozen_config`` equal to the current
  harness fingerprint (Core code + reader code hashes). Every held-out run is
  logged in the report; nothing is tuned from it.

A report never states hardware validation. Even a real-microscope dataset
of the MSI GTX 970 is reported as "real images scored", pending operator
review of the results; physical gates stay operator-owned.
"""

from __future__ import annotations

from pathlib import Path

from anastylos_microscopy import EVAL_REPORT_SCHEMA, __version__, capture, env, schema
from anastylos_microscopy.orchestrator import core_code_sha256, inspect_image
from anastylos_microscopy.util import (atomic_write_json, canonical_json, read_json, sha256_bytes,
                                       sha256_file, utc_now)

WORKFLOWS = {
    "MIC-01": "printed SMD resistor marking: decode or abstain",
    "MIC-02": "PCB silkscreen reference designators",
    "MIC-03": "semiconductor package markings without invented part numbers",
    "MIC-04": "fingerprint enrollment and retrieval, incl. look-alike negatives",
    "MIC-05": "unknown/unreadable/unfocused components must not yield specifications",
}
ABSTAIN_STATUSES = ("unknown", "ambiguous", "conflict", "recorded")


def harness_fingerprint(anastylos_root=None) -> str:
    root = env.resolve_root(anastylos_root)
    readers = {k: env.code_sha256(env.module_dir(root, k) / v[1])
               for k, v in env.READER_MODULES.items()}
    return sha256_bytes(canonical_json({"core": core_code_sha256(), "readers": readers,
                                        "core_version": __version__}))


def _norm(text):
    return "".join(str(text).upper().split()) if text is not None else None


def _reader(record, key):
    for block in record.get("recognizers") or []:
        if block["reader"] == key:
            return block
    return None


def score_item(item: dict, record: dict) -> dict:
    expected = item.get("expected") or {}
    overall = record["uncertainty"]["overall_status"]
    workflow = item["workflow"]
    result = {"id": item["id"], "workflow": workflow, "split": item["split"],
              "inspection_id": record["inspection_id"], "overall_status": overall,
              "outcome": None, "detail": None}
    if overall in ("unavailable", "error", "input_error"):
        result.update(outcome="unavailable" if overall == "unavailable" else "error",
                      detail=record["uncertainty"]["reasons"])
        return result
    abstain = bool(expected.get("abstain"))
    if workflow == "MIC-05" or abstain:
        if overall == "candidate":
            result.update(outcome="false_confident",
                          detail="a clear candidate was produced where abstention was expected")
        else:
            result.update(outcome="correct_abstention")
        return result
    if workflow in ("MIC-01", "MIC-03"):
        key = "smd" if workflow == "MIC-01" else "markings"
        block = _reader(record, key) or {}
        want = _norm(expected.get("visible_marking"))
        field = "code" if key == "smd" else "match"
        seen = [_norm(i.get(field)) for i in block.get("interpreted") or []]
        if block.get("status") == "ok" and overall == "candidate":
            ok = bool(seen) and seen[0] == want
            result.update(outcome="correct" if ok else "false_confident", detail={"top": seen[:1]})
        elif want in seen:
            result.update(outcome="undecided_truth_kept", detail={"candidates": seen})
        else:
            result.update(outcome="miss", detail={"candidates": seen})
        return result
    if workflow == "MIC-02":
        block = _reader(record, "pcb_ocr") or {}
        want = {_norm(d) for d in expected.get("designators") or []}
        got = {_norm(i.get("normalized")) for i in block.get("interpreted") or []}
        hits, extra = want & got, got - want
        result["detail"] = {"hits": sorted(hits), "missed": sorted(want - got),
                            "false_positive": sorted(extra)}
        result["outcome"] = "correct" if hits == want and not extra else (
            "partial" if hits else "miss")
        return result
    if workflow == "MIC-04":
        block = _reader(record, "fingerprints") or {}
        want = expected.get("gallery_id")
        top = [i.get("id") for i in block.get("interpreted") or []][:1]
        status = block.get("status")
        if want is None:
            result["outcome"] = "false_confident" if status == "ok" else "correct_abstention"
        elif status == "ok":
            result["outcome"] = "correct" if top == [want] else "false_confident"
        else:
            result["outcome"] = "undecided_truth_kept" if top == [want] else "miss"
        result["detail"] = {"top": top, "reader_status": status}
        return result
    result.update(outcome="error", detail=f"unsupported workflow {workflow}")
    return result


def evaluate(dataset_path, out_dir, *, split="dev", frozen_config=None, anastylos_root=None,
             ocr_backend=None, tesseract_cmd=None, clock=None) -> tuple[dict, Path]:
    dataset_path = Path(dataset_path)
    dataset = read_json(dataset_path)
    errors = schema.validate_named(dataset, "dataset")
    if errors:
        raise ValueError("dataset invalid: " + "; ".join(errors[:5]))
    fingerprint = harness_fingerprint(anastylos_root)
    if split == "heldout" and frozen_config != fingerprint:
        raise ValueError("held-out evaluation requires --frozen-config equal to the current harness "
                         f"fingerprint {fingerprint} (freeze code first; never tune on held-out data)")
    if split not in ("dev", "heldout"):
        raise ValueError("split must be 'dev' or 'heldout'")
    ids = [item["id"] for item in dataset["items"]]
    if len(ids) != len(set(ids)):
        raise ValueError("dataset item ids must be unique")
    out_dir = Path(out_dir)
    provenance = dataset["provenance"]
    vclass = provenance["kind"]
    rows = []
    for item in dataset["items"]:
        if item["split"] != split:
            continue
        rel = capture.safe_relative(item["image"])
        image_path = dataset_path.parent.joinpath(*rel.parts)
        try:  # v0.1.1: same confinement as capture manifests (dataset dir = root)
            data, _ = capture.read_frame(capture.capture_root(dataset_path), rel)
        except capture.ConfinementError as exc:
            raise ValueError(f"dataset item {item['id']}: {exc.code}: {exc}") from exc
        options = dict(item.get("options") or {})
        if options.get("gallery"):
            grel = capture.safe_relative(options["gallery"])
            options["gallery"] = str(dataset_path.parent.joinpath(*grel.parts))
        record, _ = inspect_image(
            image_path, out_dir / "inspections", mode=item["mode"], readers=item.get("readers"),
            roi=item.get("roi_xywh"), options=options, hypothesis=item.get("hypothesis"),
            capture={"origin": "file", "validation_class": vclass,
                     "optical_profile": item.get("optical_profile")},
            clock=clock, anastylos_root=anastylos_root, ocr_backend=ocr_backend,
            tesseract_cmd=tesseract_cmd, expected_sha256=item.get("sha256"), image_bytes=data)
        rows.append(score_item(item, record))
    per_workflow = {}
    for row in rows:
        bucket = per_workflow.setdefault(row["workflow"], {"items": 0})
        bucket["items"] += 1
        bucket[row["outcome"]] = bucket.get(row["outcome"], 0) + 1
    gtx = vclass == "real_microscope" and bool(provenance.get("is_msi_gtx970_capture"))
    report = {
        "schema": EVAL_REPORT_SCHEMA,
        "created_utc": clock or utc_now(),
        "dataset": {"path": str(dataset_path), "sha256": sha256_file(dataset_path),
                    "dataset_id": dataset["dataset_id"], "provenance": provenance},
        "split": split,
        "heldout_access": split == "heldout",
        "harness_fingerprint": fingerprint,
        "validation_class": vclass,
        "hardware_validation": "not_claimed",
        "gtx970_validation": ("real_images_scored_pending_operator_review" if gtx else "not_claimed"),
        "statement": ("Synthetic/sample results prove the harness and integration only; they say "
                      "nothing about real MUSTOOL/MSI GTX 970 accuracy."
                      if vclass != "real_microscope" else
                      "Scores on real microscope images; physical-gate status is set only by the "
                      "operator with evidence."),
        "workflows": {k: WORKFLOWS[k] for k in sorted(per_workflow)},
        "per_workflow": per_workflow,
        "items": rows,
    }
    path = out_dir / f"eval-{split}.json"
    atomic_write_json(path, report)
    return report, path
