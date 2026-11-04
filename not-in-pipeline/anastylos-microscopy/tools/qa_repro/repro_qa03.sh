#!/bin/bash
# Reproduce QA-03 against package dir $1
PKG=$(cd "${1:?usage: $0 PACKAGE_DIR}" && pwd); PY=${PYTHON:-python}; W=$(mktemp -d)
cd $PKG; export PYTHONPATH=$PKG/src PYTHONDONTWRITEBYTECODE=1 ANASTYLOS_ROOT=${ANASTYLOS_ROOT:?set ANASTYLOS_ROOT}
$PY -m anastylos_microscopy inspect --image examples/synthetic_mic/images/unknown_glare.png --mode resistor --clock 2026-10-29T12:00:00Z --out $W/o; echo "exit=$?"
$PY - <<P
import json,glob
r=json.load(open(glob.glob("$W/o/mi-*/inspection.json")[0])); b=r["recognizers"][0]
print("overall:", r["uncertainty"]["overall_status"], "| reader status:", b["status"], "native:", b["native_status"])
c=b["native_result"]["candidates"][0]; print("native top:", c["resistance"], c["score"], c["sequence"])
print("core glare_fraction:", r["quality"]["glare_fraction"], "flags:", r["quality"]["flags"])
print("core_assessment:", json.dumps(b.get("core_assessment"), indent=1))
P
rm -rf $W
