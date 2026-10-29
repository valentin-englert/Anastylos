package org.anastylos.capture.guidance

/**
 * Frozen quality profile `guidance-hypothesis-1`: the numerical
 * hypotheses every Wave-1 guidance gate evaluates against.
 *
 * Sources (frozen, read-only): END-TO-END-ACCEPTANCE.md "Canonical
 * initial profile" + FROZEN PREREGISTRATION DEFAULTS, and
 * SPATIAL-TRACKING-DESIGN.md §8 (stable capture gate). These are
 * hypotheses requiring held-out validation — changing any value
 * after scoring requires a new profile version plus a new held-out
 * set, never a silent edit.
 */
object GuidanceHypothesis1 {
    const val PROFILE_ID = "guidance-hypothesis-1"

    // Registration gates (tracking §5-6).
    const val MIN_MARKERS = 3
    const val MIN_CORNERS = 12
    const val CORNER_SPREAD_FRAC = 0.20
    const val HULL_AREA_FRAC = 0.03
    const val LIVE_RMS_PX = 1.2
    const val LIVE_MAX_PX = 3.0
    const val REF_WIDTH_PX = 1280

    // Pose freshness (acceptance profile + reference UI latency limits).
    const val POSE_AGE_MS = 150L
    const val STALE_CLEAR_MS = 200L

    // Reacquisition after marker loss (acceptance profile).
    const val REACQUIRE_FRAMES = 5
    const val REACQUIRE_SPAN_MS = 300L

    // Capture ellipsoid and angular tolerance (acceptance profile).
    const val POS_TOL_X_MM = 15.0
    const val POS_TOL_Y_MM = 15.0
    const val POS_TOL_Z_MM = 20.0
    const val ANG_TOL_DEG = 5.0

    // Stable dwell (tracking §8).
    const val HOLD_MS = 1000L
    const val HOLD_SAMPLES = 12
    const val HOLD_GAP_MS = 150L
    const val MAX_SPEED_MM_S = 8.0
    const val MAX_ANGULAR_SPEED_DEG_S = 1.5

    // Attempt budget (tracking §8).
    const val MAX_AUTO_RETAKES = 2
    const val MAX_ATTEMPTS = 3

    // Saved/live image quality, H6 interim preregistration + §8.
    const val MIN_SHARPNESS_RATIO = 0.7
    const val DARK_LEVEL = 5
    const val SATURATED_LEVEL = 250
    const val MAX_BAD_EXPOSURE_FRAC = 0.05
    const val MAX_MOTION_MEDIAN_PX = 0.7
    const val MAX_MOTION_P90_PX = 1.5
    const val MOTION_WINDOW_MS = 100L

    // Post-shutter pose association (tracking §8-9, Wave-2 device seam).
    const val JPEG_BRACKET_MS = 50L
    const val JPEG_REPROJ_RMS_PX = 1.5
}
