#!/bin/bash
# Reproduce QA-02 (Linux emulation of the Windows junction: directory symlink) against package dir $1
PKG=$(cd "${1:?usage: $0 PACKAGE_DIR}" && pwd); PY=${PYTHON:-python}
W=$(mktemp -d); IMG=$PKG/examples/synthetic_mic/images
mkdir -p $W/outside $W/capture; cp $IMG/unknown_glare.png $W/outside/escape.jpg
ln -s $W/outside $W/capture/frames
SHA=$(sha256sum $W/outside/escape.jpg | cut -d' ' -f1)
cat > $W/capture/capture-manifest.json <<J
{"schema":"anastylos-microscopy-capture-manifest/1","source":{"camera_id":"qa"},"validation_class":"synthetic",
 "frames":[{"file":"frames/escape.jpg","sha256":"$SHA"}]}
J
cd $PKG; export PYTHONPATH=$PKG/src PYTHONDONTWRITEBYTECODE=1 ANASTYLOS_ROOT=${ANASTYLOS_ROOT:?set ANASTYLOS_ROOT}
echo "== validate --kind capture_manifest (frames/ -> symlinked dir outside capture root)"
$PY -m anastylos_microscopy validate --kind capture_manifest $W/capture/capture-manifest.json; echo "exit=$?"
echo "== inspect --manifest"
$PY -m anastylos_microscopy inspect --manifest $W/capture/capture-manifest.json --mode resistor --out $W/out; echo "exit=$?"
$PY - <<P
import json,glob; b=json.load(open("$W/out/batch.json")); print("entries:", [(e["file"],e["overall_status"],e["error"]) for e in b["entries"]])
for p in glob.glob("$W/out/mi-*/inspection.json"):
    r=json.load(open(p)); print("decoded image sha:", r["image"]["sha256"][:16], "path:", r["image"]["original_path"])
P
rm -rf $W
