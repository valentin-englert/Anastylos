"""ANASTYLOS trusted CAD worker: package marker.

Submodules:
  validate -- stdlib-only cad-feature-model-v1 schema-shape + semantic checks.
  compile  -- stdlib-only deterministic construction-script generation.
  builder  -- allowlisted CadQuery kernel calls (worker venv only).
  preview  -- stdlib-only SVG projection from tessellation data.
  evidence -- stdlib-only evidence/validation/manifest writers.
  worker   -- CLI entry (fixed args, per-job workdir, bounded inputs).
"""
