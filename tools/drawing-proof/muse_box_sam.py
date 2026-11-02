"""r3: Muse sees the rectified image and returns a tight part box; SAM segments that box. Sanitized traces only."""
import base64, json, subprocess, sys, time
from pathlib import Path
import cv2
sys.path.insert(0, r"C:\Users\HP\Documents\Anastylos\sam-tracking")
from anastylos_sam import client

R = Path(r"C:\tmp\anastylos-drawing-proof")
SAM = r"C:\Users\HP\Documents\Anastylos\sam-tracking\sam.py"
PHRASE = {"F0-front-p3-f300": "front shell of the game controller (the large grey dog-bone part)",
          "T1-front-p3-f240": "front shell of the game controller (the large grey dog-bone part)",
          "T2-front-p3-f360": "front shell of the game controller (the large grey dog-bone part)",
          "T3-front-empty-aftermath": "front shell of the game controller (the large grey dog-bone part)",
          "T4-dpad-membrane-aftermath": "white D-pad rubber membrane", "T5-shoulder-piece-p3-f300": "grey shoulder button piece"}
DIRS = {"F0-front-p3-f300": R / "run-front-r1"} | {t: R / "transfer-r1" / t for t in list(PHRASE)[1:]}
SCHEMA = {"type": "object", "additionalProperties": False, "required": ["found", "box", "fully_visible", "reason"],
          "properties": {"found": {"type": "boolean"},
                         "box": {"type": "array", "items": {"type": "integer"}, "minItems": 4, "maxItems": 4},
                         "fully_visible": {"type": "boolean"}, "reason": {"type": "string", "maxLength": 300}}}
key = client.load_key()
rows = []
for name, d in DIRS.items():
    image = cv2.imread(str(d / "rectified.png")); h, w = image.shape[:2]
    scale = min(1.0, 1024 / max(h, w)); small = cv2.resize(image, (round(w * scale), round(h * scale)), interpolation=cv2.INTER_AREA)
    ok, jpg = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, 90]); sw, sh = small.shape[1], small.shape[0]
    prompt = (f"Image size {sw}x{sh} px (top-down photo of parts on a printed calibration sheet). Find the {PHRASE[name]}. "
              "Return the tightest axis-aligned box [left, top, right, bottom] in pixels of this image around that one part only "
              "(exclude shadows, printed lines and other parts). Set fully_visible false if any part of its outer edge is cut off or hidden. "
              "If the part is not in the image, set found false and box [0,0,0,0].")
    payload = {"model": "muse-spark-1.3-contributor", "reasoning": {"effort": "max"},
               "input": [{"role": "user", "content": [{"type": "input_text", "text": prompt},
                          {"type": "input_image", "image_url": "data:image/jpeg;base64," + base64.b64encode(jpg.tobytes()).decode()}]}],
               "text": {"format": {"type": "json_schema", "name": "part_box", "strict": True, "schema": SCHEMA}}}
    row = {"id": name, "image_sent": [sw, sh]}
    t = time.perf_counter()
    try:
        data, meta = client.post_responses_once(payload, key, timeout_s=300)
        text = next(c.get("text") for o in data.get("output", []) if o.get("type") == "message" for c in o.get("content", []))
        answer = json.loads(text)
        row.update(muse_http=meta["http"], muse_latency_s=meta["latency_s"], usage=data.get("usage"),
                   effort_returned=(data.get("reasoning") or {}).get("effort"), muse=answer)
    except Exception as exc:  # recorded, not hidden
        row.update(muse_error=type(exc).__name__, detail=str(exc)[:300]); rows.append(row); print(json.dumps(row)); continue
    box = answer["box"]
    valid = answer["found"] and 0 <= box[0] < box[2] <= sw and 0 <= box[1] < box[3] <= sh
    row["box_valid"] = valid
    if valid:
        full = [round(v / scale) for v in box]
        out = R / "sam-r3" / name; out.mkdir(parents=True, exist_ok=True)
        (out / "objects.json").write_text(json.dumps({"width": w, "height": h, "objects": [{"id": 1, "box": full}]}))
        p = subprocess.run([sys.executable, "-B", SAM, "--live", "--upload-ok", "--budget-usd", "2.0", "--ledger", str(R / "sam-r3" / "ledger.jsonl"),
                            "segment-image", "--image", str(d / "rectified.png"), "--objects-json", str(out / "objects.json"), "--out", str(out)],
                           capture_output=True, text=True, encoding="utf-8", timeout=600)
        row.update(box_full_px=full, sam_exit=p.returncode)
    row["wall_s"] = round(time.perf_counter() - t, 2)
    rows.append(row); print(json.dumps(row), flush=True)
(R / "sam-r3").mkdir(parents=True, exist_ok=True)
(R / "sam-r3" / "calls.json").write_text(json.dumps(rows, indent=1))
