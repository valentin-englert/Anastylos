package org.anastylos.capture.guidance

/**
 * `guidance-hypothesis-2` (TL decision on R12, 2026-11-02): the shutter rule
 * for workstation requests. The phone takes the photo; strictness lives in the
 * evidence label, never in the shutter.
 *
 * Blocks only on: the sheet is tracked and the phone was held still for the
 * dwell ([HackathonMvp.DWELL_MS]). Target offset, sharpness, exposure and
 * motion do not block; they are measured against the unchanged
 * [GuidanceHypothesis1] thresholds and reported in the sidecar, so such a
 * photo stays `package_only`. H1 stays frozen and selectable for local arming.
 */
object GuidanceHypothesis2 {
    const val PROFILE_ID = "guidance-hypothesis-2"
}
