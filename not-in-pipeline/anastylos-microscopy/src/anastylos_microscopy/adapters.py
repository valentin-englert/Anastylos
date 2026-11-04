"""Recognition adapters: thin wrappers around the EXISTING reader APIs.

Each adapter calls the same library function the module's own CLI calls
(`vision.analyze`, `pipeline.analyze`, `describe.fingerprint` +
`match.match_gallery`, ...), keeps the native result verbatim, and adds a
small normalized view. No reader logic is reimplemented here; normalizing
only *reads* native fields.

Normalized vocabulary (per reader):

* ``observed``    - visual evidence the reader measured (text boxes, bands,
                    markers, geometry). Not an interpretation.
* ``interpreted`` - deterministic algorithmic readings of that evidence
                    (decoded codes, classes, measurements).
* ``candidates``  - candidate identifications (never confirmed).
* ``claims``      - minimal facts used only for cross-reader reconciliation.
"""

from __future__ import annotations

import importlib
from pathlib import Path

from anastylos_microscopy import calibration as cal
from anastylos_microscopy.util import atomic_write_json

CORE_STATUSES = ("ok", "ambiguous", "unknown", "unavailable", "rejected", "reader_error",
                 "input_error", "not_applicable", "enrolled", "observed")

OCR_READERS = ("smd", "pcb_ocr", "markings")


class ReaderUnavailable(Exception):
    """Dependency missing (import failure, OCR engine, gallery, option)."""


class ReaderInputError(Exception):
    """Operator input the reader rejects (missing required option)."""


def _imp(name: str):
    try:
        return importlib.import_module(name)
    except Exception as exc:  # ImportError, missing cv2, syntax drift, ...
        raise ReaderUnavailable(f"cannot import {name}: {type(exc).__name__}: {exc}") from exc


def _roi_tuple(roi):
    return tuple(roi) if roi else None


def _status(native: dict) -> str | None:
    value = native.get("status", native.get("verdict"))
    return value if isinstance(value, str) else None


class Adapter:
    key = ""
    calibration_spelling: str | None = None  # None = reader takes no calibration
    requires_ocr = False
    description = ""

    def run(self, image, roi, ctx) -> dict:
        raise NotImplementedError

    def annotate(self, image, native, roi):
        return None

    def normalize(self, native: dict) -> dict:
        return {"observed": [], "interpreted": [], "candidates": [], "claims": []}

    # helpers ---------------------------------------------------------
    def reader_calibration(self, ctx):
        if self.calibration_spelling is None:
            return None, {"applied": False, "spelling": None,
                          "reason": "reader does not consume calibration"}
        return cal.for_reader(ctx["calibration"], self.calibration_spelling)

    def ocr_backend(self, ctx):
        backend = ctx.get("ocr_backend")
        if backend is not None:
            return backend
        backends = _imp("anastylos_pcb.backends")
        options = {"command": ctx["tesseract_cmd"]} if ctx.get("tesseract_cmd") else {}
        return backends.get_backend("tesseract", **options)


# ---------------------------------------------------------------- resistor
class ResistorAdapter(Adapter):
    key = "resistor"
    description = "axial resistor colour bands (resistor-reader vision.analyze)"

    def run(self, image, roi, ctx):
        vision = _imp("anastylos_resistor.vision")
        result = vision.analyze(image, _roi_tuple(roi))
        return {"image": ctx["image_name"], "roi": list(roi) if roi else None, **result}

    def annotate(self, image, native, roi):
        return _imp("anastylos_resistor.vision").annotate(image, native)

    def normalize(self, native):
        observed = [{"kind": "color_band", "bbox_xywh": b.get("bbox_xywh"),
                     "color_candidates": b.get("color_candidates")}
                    for b in native.get("band_boxes") or []]
        interpreted = [{"kind": "color_band_decode", "sequence": c.get("sequence"),
                        "orientation": c.get("orientation"),
                        "resistance_ohms": c.get("resistance_ohms"),
                        "resistance": c.get("resistance"),
                        "band_tolerance_percent": c.get("tolerance_percent"),
                        "score": c.get("score")} for c in native.get("candidates") or []]
        candidates, claims = [], []
        status = _status(native)
        for item in interpreted:
            candidates.append({"identification": "axial resistor (assumed by reader; operator hypothesis)",
                               "value": item["resistance"], "score": item["score"],
                               "basis": "colour-band decode", "confirmed": False})
        if status == "ok" and interpreted:
            claims.append({"domain": "resistance_ohms", "value": interpreted[0]["resistance_ohms"]})
            claims.append({"domain": "component_category", "value": "resistor"})
        return {"observed": observed, "interpreted": interpreted,
                "candidates": candidates, "claims": claims}


# ---------------------------------------------------------------- smd
class SmdAdapter(Adapter):
    key = "smd"
    requires_ocr = True
    description = "printed SMD resistor code via pcb-ocr + deterministic decoder (smd-reader)"

    def run(self, image, roi, ctx):
        vision = _imp("anastylos_smd.vision")
        aliases = ctx["options"].get("smd_aliases")
        result = vision.analyze(image, self.ocr_backend(ctx), _roi_tuple(roi), aliases,
                                ctx["image_name"])
        return {"image": ctx["image_name"], "roi": list(roi) if roi else None, **result}

    def annotate(self, image, native, roi):
        return _imp("anastylos_smd.annotate").annotate(image, native)

    def normalize(self, native):
        observed = [{"kind": "ocr_text", "raw_text": o.get("raw_text"), "bbox_xywh": o.get("bbox_xywh"),
                     "ocr_confidence": o.get("ocr_confidence"),
                     "orientations_seen": o.get("orientations_seen")}
                    for o in native.get("observations") or []]
        interpreted = [{"kind": "smd_code_decode", "code": c.get("code"), "schemes": c.get("schemes"),
                        "resistance_ohms_exact": c.get("resistance_ohms_exact"),
                        "repaired": c.get("repaired"), "substitutions": c.get("subs"),
                        "tolerance_percent": c.get("tolerance_percent")}
                       for c in native.get("candidates") or []]
        candidates = [{"identification": "SMD resistor (conditional on operator-verified resistor hypothesis)",
                       "value": f"{i['resistance_ohms_exact']} ohm" if i["resistance_ohms_exact"] is not None else None,
                       "basis": f"code {i['code']!r}", "confirmed": False} for i in interpreted]
        claims = []
        if _status(native) == "ok" and interpreted:
            exact = interpreted[0]["resistance_ohms_exact"]
            claims.append({"domain": "resistance_ohms", "value": float(exact) if exact is not None else None})
            claims.append({"domain": "component_category", "value": "resistor"})
        return {"observed": observed, "interpreted": interpreted,
                "candidates": candidates, "claims": claims}


# ---------------------------------------------------------------- pcb-ocr
class PcbOcrAdapter(Adapter):
    key = "pcb_ocr"
    requires_ocr = True
    calibration_spelling = "px_per_mm"
    description = "silkscreen reference designators (pcb-ocr pipeline.analyze)"

    def run(self, image, roi, ctx):
        pipeline = _imp("anastylos_pcb.pipeline")
        frame_meta, _ = self.reader_calibration(ctx)
        result = pipeline.analyze(image, self.ocr_backend(ctx), _roi_tuple(roi), frame_meta)
        return {"image": ctx["image_name"], "roi": list(roi) if roi else None, **result}

    def annotate(self, image, native, roi):
        return _imp("anastylos_pcb.annotate").annotate(image, native)

    def normalize(self, native):
        observed = [{"kind": "ocr_text", "raw_text": c.get("raw_text"), "bbox_xywh": c.get("bbox_xywh"),
                     "bbox_mm": c.get("bbox_mm"), "ocr_confidence": c.get("ocr_confidence"),
                     "orientation": c.get("orientation")}
                    for c in native.get("candidates") or []]
        observed += [{"kind": "ocr_text_ignored", **{k: i.get(k) for k in ("raw_text", "bbox_xywh", "ocr_confidence")}}
                     for i in native.get("ignored_text") or []]
        interpreted = [{"kind": "designator", "id": c.get("id"), "normalized": c.get("normalized"),
                        "verdict": c.get("verdict"), "verdict_reason": c.get("verdict_reason"),
                        "corrections": c.get("corrections"), "alternates": c.get("alternates"),
                        "duplicate_label": c.get("duplicate_label")}
                       for c in native.get("candidates") or []]
        candidates = [{"identification": f"silkscreen designator {c.get('normalized')}",
                       "suggested_type": c.get("suggested_type"), "type_note": c.get("type_note"),
                       "bbox_xywh": c.get("bbox_xywh"), "verdict": c.get("verdict"), "confirmed": False}
                      for c in native.get("candidates") or []]
        return {"observed": observed, "interpreted": interpreted, "candidates": candidates, "claims": []}


# ---------------------------------------------------------------- markings
class MarkingsAdapter(Adapter):
    key = "markings"
    requires_ocr = True
    description = "component top markings via pcb-ocr + deterministic parser (component-markings)"

    def run(self, image, roi, ctx):
        vision = _imp("anastylos_markings.vision")
        mapping = None
        if ctx["options"].get("markings_mapping"):
            parse = _imp("anastylos_markings.parse")
            mapping = parse.load_mapping(ctx["options"]["markings_mapping"])
        result = vision.analyze(image, self.ocr_backend(ctx), _roi_tuple(roi), mapping,
                                image_name=ctx["image_name"])
        return {"image": ctx["image_name"], "roi": list(roi) if roi else None, **result}

    def annotate(self, image, native, roi):
        return _imp("anastylos_markings.annotate").annotate(image, native)

    def normalize(self, native):
        observed = [{"kind": "ocr_text", **{k: o.get(k) for k in ("raw_text", "bbox_xywh", "ocr_confidence")}}
                    for o in native.get("observations") or []]
        interpreted, candidates = [], []
        for c in native.get("candidates") or []:
            interpreted.append({"kind": "marking_line", "line": c.get("line"), "text": c.get("text"),
                                "match": c.get("match"), "parsed": c.get("parsed"),
                                "repaired": c.get("repaired"), "flags": c.get("flags")})
            identity = c.get("identity") or {}
            candidates.append({"identification": f"marking {identity.get('text') or c.get('text')!r}",
                               "identity_class": identity.get("class"),
                               "resolved": identity.get("resolved"),
                               "resolution_source": identity.get("resolution_source"),
                               "verified_spec": c.get("verified_spec"),
                               "note": identity.get("note"), "confirmed": False})
        claims = []
        if _status(native) == "ok" and interpreted:
            claims.append({"domain": "marking_text", "value": interpreted[0]["match"]})
        return {"observed": observed, "interpreted": interpreted, "candidates": candidates, "claims": claims}


# ---------------------------------------------------------------- polarity
class PolarityAdapter(Adapter):
    key = "polarity"
    calibration_spelling = "pixels_per_mm"
    description = "polarity / orientation markers (polarity-reader vision.analyze)"

    def run(self, image, roi, ctx):
        vision = _imp("anastylos_polarity.vision")
        frame_meta, _ = self.reader_calibration(ctx)
        options = ctx["options"]
        reference = None
        if options.get("reference_kind") is not None or options.get("reference_angle") is not None:
            if options.get("reference_kind") is None or options.get("reference_angle") is None:
                raise ReaderInputError("reference needs both reference_kind and reference_angle")
            reference = {"kind": options["reference_kind"], "angle_deg": float(options["reference_angle"])}
        result = vision.analyze(image, _roi_tuple(roi), family=options.get("polarity_family"),
                                reference=reference, frame_metadata=frame_meta)
        return {"image": ctx["image_name"], "roi": list(roi) if roi else None,
                "family_hint": options.get("polarity_family"), **result}

    def annotate(self, image, native, roi):
        return _imp("anastylos_polarity.annotate").annotate(image, native)

    def normalize(self, native):
        observed = [{"kind": "polarity_marker", "marker_id": m.get("marker_id"),
                     "marker_type": m.get("marker_type"), "bbox_xywh": m.get("bbox_xywh"),
                     "centroid_xy": m.get("centroid_xy"), "strength": m.get("strength")}
                    for m in native.get("markers") or []]
        interpreted = [{"kind": "polarity_interpretation", **{k: c.get(k) for k in (
            "marker_ref", "marker_type", "family", "interpretation", "polarity",
            "orientation_angle_deg", "confidence")}} for c in native.get("candidates") or []]
        claims = []
        if _status(native) == "ok" and interpreted:
            claims.append({"domain": "polarity", "value": interpreted[0]["interpretation"]})
        return {"observed": observed, "interpreted": interpreted,
                "candidates": [], "claims": claims,
                "installation": native.get("installation")}


# ---------------------------------------------------------------- screw head
class ScrewAdapter(Adapter):
    key = "screw"
    description = "screw-head drive classification (screw-reader vision.analyze)"

    def run(self, image, roi, ctx):
        vision = _imp("anastylos_screw.vision")
        result = vision.analyze(image, _roi_tuple(roi))
        return {"image": ctx["image_name"], "roi": list(roi) if roi else None, **result}

    def annotate(self, image, native, roi):
        return _imp("anastylos_screw.annotate").annotate(image, native)

    def normalize(self, native):
        head = native.get("head")
        observed = ([{"kind": "screw_head", **head}] if head else []) + \
                   ([{"kind": "recess", "bbox_xywh": native["recess_bbox_xywh"]}]
                    if native.get("recess_bbox_xywh") else [])
        interpreted = [{"kind": "drive_class", "class": c.get("class"), "family": c.get("family"),
                        "score": c.get("score")} for c in native.get("candidates") or []]
        candidates = [{"identification": f"{i['class']} drive (visual class only; no size/thread claim)",
                       "score": i["score"], "confirmed": False} for i in interpreted]
        claims = []
        if _status(native) == "ok" and interpreted:
            claims += [{"domain": "component_category", "value": "screw"},
                       {"domain": "screw_drive", "value": interpreted[0]["class"]}]
        return {"observed": observed, "interpreted": interpreted, "candidates": candidates, "claims": claims}


# ---------------------------------------------------------------- screw measurement
class ScrewMeasureAdapter(Adapter):
    key = "screw_measure"
    calibration_spelling = "screw_measurement_file"
    description = "calibrated screw geometry, top/side (screw-measurement vision.analyze)"

    def run(self, image, roi, ctx):
        vision = _imp("anastylos_screw_measure.vision")
        calib_mod = _imp("anastylos_screw_measure.calibration")
        view = ctx["options"].get("view")
        if view is None:
            raise ReaderInputError("screw_measure needs an explicit view ('top' or 'side')")
        native_cal, note = self.reader_calibration(ctx)
        if native_cal is None:
            calibration = calib_mod.blank(f"no valid calibration: {note['reason']}; pixel-only output")
        else:
            # Same path as the module CLI: a calibration *file* through load_calibration.
            path = Path(ctx["reader_dir"]) / "screw_measurement_calibration.json"
            atomic_write_json(path, native_cal)
            calibration = calib_mod.load_calibration(path)
        result = vision.analyze(image, mode=view, roi=_roi_tuple(roi), calibration=calibration)
        return {"image": ctx["image_name"], "source": ctx["image_name"], **result}

    def annotate(self, image, native, roi):
        return _imp("anastylos_screw_measure.annotate").annotate(image, native)

    def normalize(self, native):
        geometry = native.get("geometry")
        observed = [{"kind": "screw_geometry", **geometry}] if geometry else []
        interpreted = [{"kind": "measurement", **{k: m.get(k) for k in (
            "name", "value_px", "px_std", "value_mm", "mm_interval", "status", "reasons")}}
            for m in native.get("measurements") or []]
        return {"observed": observed, "interpreted": interpreted, "candidates": [], "claims": [],
                "not_estimated": native.get("not_estimated")}


# ---------------------------------------------------------------- washer / spacer
class WasherAdapter(Adapter):
    key = "washer"
    calibration_spelling = "pixels_per_mm"
    description = "washer / spacer / standoff geometry (washer-spacer vision.analyze)"

    def run(self, image, roi, ctx):
        vision = _imp("anastylos_washer.vision")
        view = ctx["options"].get("view")
        if view is None:
            raise ReaderInputError("washer needs an explicit view ('top' or 'side')")
        frame_meta, _ = self.reader_calibration(ctx)
        result = vision.analyze(image, view, _roi_tuple(roi), family=ctx["options"].get("washer_family"),
                                frame_metadata=frame_meta)
        return {"image": ctx["image_name"], "roi": list(roi) if roi else None,
                "family_hint": ctx["options"].get("washer_family"), **result}

    def annotate(self, image, native, roi):
        return _imp("anastylos_washer.annotate").annotate(image, native)

    def normalize(self, native):
        observed, interpreted, candidates = [], [], []
        for o in native.get("observations") or []:
            observed.append({"kind": "part_outline", "observation_id": o.get("observation_id"),
                             "bbox_xywh": o.get("bbox_xywh"), "geometry": o.get("geometry")})
            interpreted.append({"kind": "family_ranking", "observation_id": o.get("observation_id"),
                                "status": o.get("status"), "candidates": o.get("candidates")})
            if o.get("top_family"):
                candidates.append({"identification": f"{o['top_family']} (visual family hypothesis)",
                                   "observation_id": o.get("observation_id"), "confirmed": False})
        claims = []
        obs = native.get("observations") or []
        if _status(native) == "ok" and len(obs) == 1 and obs[0].get("top_family"):
            claims.append({"domain": "component_category", "value": obs[0]["top_family"]})
        return {"observed": observed, "interpreted": interpreted, "candidates": candidates,
                "claims": claims, "geometry_mm": native.get("geometry_mm"),
                "calibration_error": native.get("calibration_error")}


# ---------------------------------------------------------------- fingerprints
class FingerprintAdapter(Adapter):
    key = "fingerprints"
    calibration_spelling = "px_per_mm"
    description = "visual fingerprint gallery match/enroll (component-fingerprints)"

    def run(self, image, roi, ctx):
        describe = _imp("anastylos_fingerprints.describe")
        store = _imp("anastylos_fingerprints.store")
        match = _imp("anastylos_fingerprints.match")
        options = ctx["options"]
        gallery_dir = options.get("gallery")
        action = options.get("fingerprint_action", "match")
        frame_meta, _ = self.reader_calibration(ctx)
        if not gallery_dir:
            raise ReaderUnavailable("no fingerprint gallery supplied (--gallery)")
        source = {"image": ctx["image_name"], "image_sha256": ctx["image_sha256"]}
        if action == "enroll":
            record_id = options.get("enroll_id")
            if not record_id:
                raise ReaderInputError("fingerprint enroll needs --enroll-id")
            record = describe.fingerprint(image, _roi_tuple(roi), frame_meta, marking=options.get("marking"),
                                          source=source, record_id=record_id, now=ctx["reader_clock"])
            saved = store.save_record(gallery_dir, record, overwrite=bool(options.get("force")))
            return {"status": "enrolled", "id": record_id, "gallery_record": Path(saved).name,
                    "quality": record["quality"], "size_source": record["size_source"],
                    "candidates": [], "record": record}
        if not Path(gallery_dir).is_dir():
            raise ReaderUnavailable(f"fingerprint gallery not found: {gallery_dir}")
        weights = match.resolve_weights(options.get("weights"))
        query = describe.fingerprint(image, _roi_tuple(roi), frame_meta, marking=options.get("marking"),
                                     source=source, now=ctx["reader_clock"])
        gallery = store.load_gallery(gallery_dir)
        result = match.match_gallery(query, gallery["records"], gallery["excluded"],
                                     top_k=int(options.get("top_k", 5)), weights=weights)
        return {"image": ctx["image_name"], "roi": list(roi) if roi else None,
                "gallery_ids": [r["id"] for r in gallery["records"]], **result}

    def annotate(self, image, native, roi):
        return _imp("anastylos_fingerprints.annotate").annotate(image, native, _roi_tuple(roi))

    def normalize(self, native):
        quality = native.get("query_quality") or native.get("quality")
        observed = [{"kind": "fingerprint_query_quality", **quality}] if quality else []
        interpreted = [{"kind": "gallery_similarity", "id": c.get("id"), "score": c.get("score"),
                        "used": c.get("used"), "size_source": c.get("size_source")}
                       for c in native.get("candidates") or []]
        candidates = [{"identification": f"same visual appearance as enrolled '{i['id']}'",
                       "score": i["score"], "note": native.get("identity_note"), "confirmed": False}
                      for i in interpreted]
        claims = []
        if _status(native) == "ok" and interpreted:
            claims.append({"domain": "gallery_identity", "value": interpreted[0]["id"]})
        return {"observed": observed, "interpreted": interpreted, "candidates": candidates, "claims": claims}


# ---------------------------------------------------------------- connectors
class ConnectorAdapter(Adapter):
    key = "connectors"
    calibration_spelling = "px_per_mm"
    description = "connector mating-geometry observe/match (connector-matching)"

    def run(self, image, roi, ctx):
        observe = _imp("anastylos_connectors.observe")
        store = _imp("anastylos_connectors.store")
        match = _imp("anastylos_connectors.match")
        options = ctx["options"]
        frame_meta, _ = self.reader_calibration(ctx)
        gallery_dir = options.get("gallery")
        source = {"image": ctx["image_name"], "image_sha256": ctx["image_sha256"]}
        action = options.get("connector_action", "match")
        if action == "enroll":
            record_id = options.get("enroll_id")
            if not record_id or not gallery_dir:
                raise ReaderInputError("connector enroll needs --enroll-id and --gallery")
            record = observe.observe(image, _roi_tuple(roi), frame_meta, marking=options.get("marking"),
                                     source=source, record_id=record_id, now=ctx["reader_clock"])
            saved = store.save_record(gallery_dir, record, overwrite=bool(options.get("force")))
            return {"status": "enrolled", "id": record_id, "gallery_record": Path(saved).name,
                    "quality": record["quality"], "role": record["role"], "observation": record,
                    "candidates": []}
        query = observe.observe(image, _roi_tuple(roi), frame_meta, marking=options.get("marking"),
                                source=source, now=ctx["reader_clock"])
        if not gallery_dir:
            return {"status": "observed", "id": None, "gallery": None, "quality": query["quality"],
                    "role": query["role"], "contact_count": query["contact_count"],
                    "mating_face_visible": query["mating_face_visible"],
                    "observation": query, "candidates": []}
        if not Path(gallery_dir).is_dir():
            raise ReaderUnavailable(f"connector gallery not found: {gallery_dir}")
        weights = match.resolve_weights(options.get("weights"))
        gallery = store.load_gallery(gallery_dir)
        if gallery["records"] or not gallery["excluded"]:
            result = match.match_gallery(query, gallery["records"], gallery["excluded"],
                                         top_k=int(options.get("top_k", 5)), weights=weights)
        else:
            result = {"status": "unavailable", "reason": "gallery holds no comparable observations",
                      "candidates": [], "excluded": gallery["excluded"], "query_quality": query["quality"]}
        return {"image": ctx["image_name"], "roi": list(roi) if roi else None,
                "query_observation": query, **result}

    def annotate(self, image, native, roi):
        observation = native.get("query_observation") or native.get("observation")
        return _imp("anastylos_connectors.annotate").annotate(image, native, _roi_tuple(roi), observation)

    def normalize(self, native):
        obs = native.get("query_observation") or native.get("observation") or {}
        observed = [{"kind": "connector_face", "role": obs.get("role"),
                     "contact_count": obs.get("contact_count"),
                     "mating_face_visible": obs.get("mating_face_visible"),
                     "quality": obs.get("quality")}] if obs else []
        interpreted = [{"kind": "mating_score", "id": c.get("id"), "score": c.get("score"),
                        "visual_similarity": c.get("visual_similarity"), "gates": c.get("gates"),
                        "verified_mechanical": c.get("verified_mechanical"),
                        "verified_electrical": c.get("verified_electrical")}
                       for c in native.get("candidates") or []]
        candidates = [{"identification": f"possible mating counterpart '{i['id']}'",
                       "score": i["score"], "note": native.get("compat_note"), "confirmed": False}
                      for i in interpreted]
        claims = []
        if _status(native) == "ok" and interpreted:
            claims.append({"domain": "connector_mate", "value": interpreted[0]["id"]})
        return {"observed": observed, "interpreted": interpreted, "candidates": candidates, "claims": claims}


REGISTRY: dict[str, Adapter] = {a.key: a for a in (
    ResistorAdapter(), SmdAdapter(), PcbOcrAdapter(), MarkingsAdapter(), PolarityAdapter(),
    ScrewAdapter(), ScrewMeasureAdapter(), WasherAdapter(), FingerprintAdapter(), ConnectorAdapter())}


def core_status(native_status: str | None) -> str:
    if native_status in CORE_STATUSES:
        return native_status
    return "unknown" if native_status is None else "unknown"
