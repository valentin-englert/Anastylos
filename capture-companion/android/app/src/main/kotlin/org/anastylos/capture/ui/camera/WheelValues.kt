package org.anastylos.capture.ui.camera

import org.anastylos.capture.camera.CameraCapabilities
import org.anastylos.capture.camera.formatExposure
import org.anastylos.capture.camera.formatFocus
import kotlin.math.abs

/** One detent of a setting wheel: what is shown and the raw value it commits. */
data class WheelStop<T>(val label: String, val value: T)

private val ISO_STEPS = listOf(50, 100, 200, 400, 800, 1600, 3200, 6400)
private val SHUTTER_DENOMINATORS = listOf(8000, 4000, 2000, 1000, 500, 250, 125, 60, 30, 15, 8, 4)
private const val FOCUS_STOPS = 10

fun isoStops(caps: CameraCapabilities): List<WheelStop<Int>> {
    val range = caps.isoRange ?: return emptyList()
    val inRange = ISO_STEPS.filter { it in range.start..range.endInclusive }.ifEmpty { listOf(range.start) }
    return inRange.map { WheelStop(it.toString(), it) }
}

fun shutterStops(caps: CameraCapabilities): List<WheelStop<Long>> {
    val range = caps.exposureNsRange ?: return emptyList()
    val maxNs = minOf(range.endInclusive, caps.maxFrameDurationNs ?: range.endInclusive)
    val stops = SHUTTER_DENOMINATORS.map { 1_000_000_000L / it }.filter { it in range.start..maxNs }
    return stops.ifEmpty { listOf(range.start.coerceAtMost(maxNs)) }.map { WheelStop(formatExposure(it), it) }
}

fun evStops(caps: CameraCapabilities): List<WheelStop<Int>> {
    val range = caps.evStepsRange ?: return emptyList()
    val step = caps.evStepSize ?: return emptyList()
    return (range.start..range.endInclusive).map {
        WheelStop("%+.1f".format(java.util.Locale.US, it * step), it)
    }
}

/** Diopter detents from infinity to the nearest focus distance. */
fun focusStops(caps: CameraCapabilities): List<WheelStop<Float>> {
    val minFocus = caps.minFocusDistance?.takeIf { it > 0f } ?: return emptyList()
    return (0 until FOCUS_STOPS).map {
        val diopters = minFocus * it / (FOCUS_STOPS - 1)
        WheelStop(formatFocus(diopters), diopters)
    }
}

/** Index of the stop closest to [value], 0 when there are no stops. */
fun <T : Number> nearestStop(stops: List<WheelStop<T>>, value: T): Int =
    stops.indices.minByOrNull { abs(stops[it].value.toDouble() - value.toDouble()) } ?: 0
