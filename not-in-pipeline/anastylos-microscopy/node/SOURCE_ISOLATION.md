# MIC-NODE-01 source provenance

## Owner-authorized appliance adaptation (2026-10-30)

For the Raspberry Pi appliance phase, the owner explicitly authorized
inspection and reuse of the owner's `valentin-englert/blobcam` repository.
The inspected reference was commit
`e180aac2480a2c038de5ee61b66702210633af87`. The one-owner GStreamer `tee`
design, untouched MJPEG appsink branch, decoded display branch, bounded
recovery approach and systemd deployment pattern informed the appliance
implementation in `src/anastylos_mic_node/pipeline.py`,
`src/anastylos_mic_node/appliance.py` and `deploy/`.

This is disclosed reuse, not a claim of independent authorship. The node has
no runtime import, submodule or package dependency on BlobCam. No unrelated
BlobCam service, UI, test, fixture or asset is included. The ANASTYLOS logo
asset is copied from the owner's existing `capture-companion` path and its
SHA-256 was verified equal at copy time. The only allowed code reference was
read at the pinned commit above; the current BlobCam deployment is not part of
the ANASTYLOS payload.

## Initial MIC-NODE-01 implementation provenance

The original file-based capture utility was authored for this repository from
the user-provided hardware and behavior requirements, the ANASTYLOS
capture-manifest schema and confinement implementation, and public Linux V4L2
/ `v4l2-ctl` documentation. The earlier statement that no other project was
inspected was accurate at that stage; the appliance adaptation above
supersedes it for the new pipeline and deployment files. No runtime import
from another capture application was added.

The operating-system utility `v4l2-ctl` is invoked as a subprocess. It is not
copied into the repository, linked into Python or included in a release
archive. Its exact binary licensing is determined by the target distribution's
package build and must be reviewed against that package metadata.

## Existing repository history and unresolved question

Earlier Core documents and source contain historical textual references to a
separately named capture project. Some records describe a concept reference
and an independent reimplementation. MIC-PURITY-01 and MIC-LINEAGE-02 retained
those records and did not establish package-wide authorship. In that earlier
audit, the task prohibited inspecting the other project; that restriction does
not describe this later, explicitly authorized appliance phase. This appliance
reuse does not resolve the separate historical Core-lineage question. Git
history and prior audit evidence remain unchanged.

## Retained explicit references

After removing unnecessary present-tense references from current integration,
hardware, dependency and calibration documentation, these older tracked items
still contain explicit references or compatibility identifiers:

- `documentation/audit-findings.md`, `documentation/inspection-schema.md` and
  `QA_FIX_REPORT.md`: prior source, compatibility-field and provenance notes.
- `qa-output/run-v012/QA_REPORT.md`: historical v0.1.2 QA assertions; retained
  unchanged as prior evidence.
- `schemas/microscopy-calibration.v1.schema.json` and
  `schemas/microscopy-inspection.v1.schema.json`: historical text / a fixed
  false runtime-dependency compatibility field. Schemas were not changed.
- `src/anastylos_microscopy/__init__.py`, `calibration.py`, `capture.py`, and
  `orchestrator.py`, plus `tests/test_evaluation_isolation.py`: existing Core
  comments, a compatibility field and its regression check. They were not
  modified for this node task.
- The generated inspection record under
  `qa-output/run-mic-node-01/inspection/` contains the same false runtime-
  dependency compatibility field because it was emitted by the unchanged
  Core schema. It is not a node dependency or imported implementation material.

The tracked-file search results are summarized above and in the MIC-NODE-01
handover. A clean text search is not a provenance proof.
