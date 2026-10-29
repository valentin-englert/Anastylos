package org.anastylos.capture.guidance

import org.anastylos.capture.guidance.GuidanceHypothesis1.MAX_BAD_EXPOSURE_FRAC
import org.anastylos.capture.guidance.GuidanceHypothesis1.MAX_MOTION_MEDIAN_PX
import org.anastylos.capture.guidance.GuidanceHypothesis1.MAX_MOTION_P90_PX
import org.anastylos.capture.guidance.GuidanceHypothesis1.MIN_SHARPNESS_RATIO

/**
 * Measured image-quality inputs for one view, in the units of the
 * H6 interim preregistration (all at 1280-equivalent width). Null
 * means unknown: no texture, no reference, or unmeasured — never a
 * pass. Pixel measurement itself is a Wave-2 device seam; this
 * verdict logic is pure and frozen-vector tested.
 */
data class ImageQualityInput(
    /** ROI sharpness, same fixed algorithm/patch as [sharpReference]. */
    val sharpness: Double?,
    /** Same-camera sharp calibration reference. */
    val sharpReference: Double?,
    /** ROI fraction darker than 5/255. */
    val darkFraction: Double?,
    /** ROI fraction brighter than 250/255. */
    val saturatedFraction: Double?,
    /** Median tracked-feature displacement px/100 ms. */
    val motionMedianPx: Double?,
    /** p90 tracked-feature displacement px/100 ms. */
    val motionP90Px: Double?,
)

sealed interface ImageQualityVerdict {
    data object Accepted : ImageQualityVerdict
    data class Rejected(val reasons: List<String>) : ImageQualityVerdict
    /** Nothing failed but something is unknown: automatic metric
     * acceptance stays blocked; an explicit manual capture may
     * proceed only as unqualified evidence (caller-side rule). */
    data object Unknown : ImageQualityVerdict
}

/**
 * H6 interim verdict: reject below 0.7x sharpness reference, at or
 * above 5% dark/saturated fraction, or at or above the motion
 * limits. There is no universal Laplacian threshold by design —
 * sharpness is always relative to the same camera's reference.
 */
fun evaluateImageQuality(
    input: ImageQualityInput,
    minSharpnessRatio: Double = MIN_SHARPNESS_RATIO,
    maxBadExposureFrac: Double = MAX_BAD_EXPOSURE_FRAC,
    maxMotionMedianPx: Double = MAX_MOTION_MEDIAN_PX,
    maxMotionP90Px: Double = MAX_MOTION_P90_PX,
): ImageQualityVerdict {
    val reasons = mutableListOf<String>()
    var unknown = false
    val sharpness = input.sharpness
    val reference = input.sharpReference
    if (sharpness == null || reference == null || reference <= 0.0) {
        unknown = true
    } else if (sharpness / reference < minSharpnessRatio) {
        reasons += "sharpness"
    }
    val dark = input.darkFraction
    if (dark == null) unknown = true else if (dark >= maxBadExposureFrac) reasons += "dark"
    val saturated = input.saturatedFraction
    if (saturated == null) unknown = true else if (saturated >= maxBadExposureFrac) reasons += "saturated"
    val median = input.motionMedianPx
    if (median == null) unknown = true else if (median >= maxMotionMedianPx) reasons += "motion"
    val p90 = input.motionP90Px
    if (p90 == null) unknown = true else if (p90 >= maxMotionP90Px) reasons += "motion"
    if (reasons.isNotEmpty()) return ImageQualityVerdict.Rejected(reasons.distinct())
    if (unknown) return ImageQualityVerdict.Unknown
    return ImageQualityVerdict.Accepted
}
