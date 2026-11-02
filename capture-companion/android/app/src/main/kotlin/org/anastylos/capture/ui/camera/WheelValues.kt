package org.anastylos.capture.ui.camera

import org.anastylos.capture.camera.CameraCapabilities
import org.anastylos.capture.camera.exposureNsToSlider
import org.anastylos.capture.camera.exposureSliderToNs
import org.anastylos.capture.camera.formatExposure
import org.anastylos.capture.camera.formatFocus
import kotlin.math.exp
import kotlin.math.ln
import kotlin.math.roundToInt

/** Maps one camera setting onto 0..1 so a stepless ruler can drive it. */
class WheelScale<T>(
    val toFraction: (T) -> Float,
    val fromFraction: (Float) -> T,
    val format: (T) -> String,
)

/** ISO is perceived in stops, so the ruler is logarithmic. */
fun isoScale(caps: CameraCapabilities): WheelScale<Int>? {
    val range = caps.isoRange ?: return null
    val low = range.start.coerceAtLeast(1).toDouble()
    val high = range.endInclusive.toDouble()
    if (high <= low) return null
    val span = ln(high / low)
    return WheelScale(
        toFraction = { (ln(it.coerceIn(range.start, range.endInclusive) / low) / span).toFloat() },
        fromFraction = { (low * exp(span * it.coerceIn(0f, 1f))).roundToInt().coerceIn(range.start, range.endInclusive) },
        format = { it.toString() },
    )
}

fun shutterScale(caps: CameraCapabilities): WheelScale<Long>? {
    val range = caps.exposureNsRange ?: return null
    val maxNs = minOf(range.endInclusive, caps.maxFrameDurationNs ?: range.endInclusive)
    if (maxNs <= range.start) return null
    return WheelScale(
        toFraction = { exposureNsToSlider(it, range.start, maxNs) },
        fromFraction = { exposureSliderToNs(it, range.start, maxNs) },
        format = { formatExposure(it) },
    )
}

/** Continuous drag, committed as the nearest device exposure-compensation step. */
fun evScale(caps: CameraCapabilities): WheelScale<Int>? {
    val range = caps.evStepsRange ?: return null
    val step = caps.evStepSize ?: return null
    val width = range.endInclusive - range.start
    if (width <= 0) return null
    return WheelScale(
        toFraction = { (it.coerceIn(range.start, range.endInclusive) - range.start).toFloat() / width },
        fromFraction = { (range.start + it.coerceIn(0f, 1f) * width).roundToInt() },
        format = { "%+.1f".format(java.util.Locale.US, it * step) },
    )
}

/** Diopters from infinity (0) to the nearest focus distance. */
fun focusScale(caps: CameraCapabilities): WheelScale<Float>? {
    val minFocus = caps.minFocusDistance?.takeIf { it > 0f } ?: return null
    return WheelScale(
        toFraction = { it.coerceIn(0f, minFocus) / minFocus },
        fromFraction = { it.coerceIn(0f, 1f) * minFocus },
        format = { formatFocus(it) },
    )
}
