#!/bin/bash
# Reproduce QA-01 against a given package dir ($1)
PKG=$(cd "${1:?usage: $0 PACKAGE_DIR}" && pwd); PY=${PYTHON:-python}
W=$(mktemp -d); IMG=$PKG/examples/synthetic_mic/images
mkdir -p $W/b3 $W/mixed; cp $IMG/unknown_glare.png $IMG/unknown_blurred_resistor.png $IMG/unknown_uniform.png $W/b3/
cp $IMG/unknown_glare.png $W/mixed/; printf 'not an image' > $W/mixed/corrupt.png
cd $PKG; export PYTHONPATH=$PKG/src PYTHONDONTWRITEBYTECODE=1
echo "== case A: ANASTYLOS_ROOT unset, batch of 3"
env -u ANASTYLOS_ROOT $PY -m anastylos_microscopy inspect --batch-dir $W/b3 --mode resistor --out $W/outA; echo "exit=$?"
echo "== case B: mixed batch valid+corrupt"
ANASTYLOS_ROOT=${ANASTYLOS_ROOT:?set ANASTYLOS_ROOT} $PY -m anastylos_microscopy inspect --batch-dir $W/mixed --mode resistor --out $W/outB; echo "exit=$?"
rm -rf $W
