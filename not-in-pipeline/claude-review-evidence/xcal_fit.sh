#!/bin/sh
# Usage: xcal_fit.sh <charuco_dir> <a3_fit_dir> <a3_valid_dir> <out_dir>
set -e
R=/c/Users/HP/Documents/Anastylos-integration-spatial
PY=$R/calibration/.venv/Scripts/python.exe
CFG=$R/calibration/calibration-config.json
mkdir -p "$4"
cd $R/calibration
$PY cal01_joint.py --charuco "$1/*.jpg" --aruco "$2/*.jpg" --config $CFG \
  --scale-source "operator Mahr/Talmeter (CAL-01 boards); XCAL M2101K6G 40cm MF" \
  --camera-id 0 --sensor-mode "0-1920x1080-still-MF40cm-AE-EV0" --focus-diopters 2.5 \
  --out "$4/xcal-profile-unknown.json" --diag "$4/xcal-diag.json"
$PY cal01_scale_check.py --profile "$4/xcal-profile-unknown.json" --images "$3/*.jpg" --config $CFG \
  --nominal-mm 480.98 --measured-mm 481.0 --uncertainty-mm 0.2 \
  --scale-what "surface_a3 diagonal marker10-outer-TL to marker12-outer-BR (CAL-01 Talmeter)" \
  --out "$4/xcal-profile.json" --diag "$4/xcal-scale-diag.json"
$PY spatial/validate.py profile --profile "$4/xcal-profile.json"
