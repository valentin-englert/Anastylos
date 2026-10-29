"""ANASTYLOS Microscopy Core v0.1.2.

Orchestrates the existing ANASTYLOS recognition readers (imported, never
rewritten) behind one evidence-preserving inspection contract. The Core
runs offline on files; it never controls a camera, never talks to a
Raspberry Pi, never imports BlobCam and never writes Assembly Memory.
"""

__version__ = "0.1.2"
INSPECTION_SCHEMA = "anastylos-microscopy-inspection/1"
CALIBRATION_SCHEMA = "anastylos-microscopy-calibration/1"
CAPTURE_MANIFEST_SCHEMA = "anastylos-microscopy-capture-manifest/1"
DATASET_SCHEMA = "anastylos-microscopy-dataset/1"
REVIEW_SCHEMA = "anastylos-microscopy-review/1"
BATCH_SCHEMA = "anastylos-microscopy-batch/1"
EVAL_REPORT_SCHEMA = "anastylos-microscopy-eval-report/1"

# Anastylos revision this release was developed and regression-tested against.
PINNED_ANASTYLOS_REVISION = "a172fbb9e127e7a3a93b128516b6592bfbbc81ef"
