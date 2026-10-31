package org.anastylos.capture.guidance

/**
 * Hackathon demo overrides, applied only by [org.anastylos.capture.ui.guided.GuidanceViewModel].
 * `guidance-hypothesis-1` (1 s / 12 samples, motion median 0.7 px/100 ms as a
 * hard gate) is unchanged. Motion pixel noise is a warning here. A stale pose
 * is ignored: it is not alignment evidence and it does not wipe the dwell.
 */
object HackathonMvp {
    const val DWELL_MS = 500L
    const val DWELL_SAMPLES = 5
}
