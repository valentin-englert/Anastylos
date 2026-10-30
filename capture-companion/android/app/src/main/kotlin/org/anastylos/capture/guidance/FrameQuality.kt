package org.anastylos.capture.guidance

import org.anastylos.capture.guidance.GuidanceHypothesis1.DARK_LEVEL
import org.anastylos.capture.guidance.GuidanceHypothesis1.SATURATED_LEVEL

/**
 * Pure-JVM luma-frame quality measurement. Operates on a tight gray
 * buffer (see [YuvFrame]). Sharpness is always full-frame (its
 * same-camera reference is full-frame). Exposure fractions start
 * full-frame and are narrowed to the board ROI by [withBoardExposure]
 * once markers are detected: a white board on a black bench is a
 * high-dynamic-range scene where full-frame fractions reject every
 * exposure (GUIDE-01: background `dark` at EV0, board `saturated` at
 * EV+), although the evidence region itself is well exposed.
 * Feeds [evaluateImageQuality]; see [ImageQualityInput].
 */
object FrameQuality {
    /** Exposure-fraction ROI identifiers (recorded in provenance). */
    const val ROI_FULL_FRAME = "full-frame"
    const val ROI_MARKER_HULL = "marker-hull"

    data class Measurement(
        /** Fraction of ROI samples at or below [DARK_LEVEL]. */
        val darkFraction: Double,
        /** Fraction of ROI samples at or above [SATURATED_LEVEL]. */
        val saturatedFraction: Double,
        /** Variance of the 4-neighbourhood Laplacian (sharpness). */
        val sharpness: Double,
        /** Which ROI the exposure fractions cover. */
        val exposureRoi: String = ROI_FULL_FRAME,
        /** Full-frame fractions, kept for diagnostics when ROI-narrowed. */
        val fullFrameDarkFraction: Double = darkFraction,
        val fullFrameSaturatedFraction: Double = saturatedFraction,
    )

    /**
     * Narrows [measurement]'s exposure fractions to the convex hull of
     * [points] (detected marker corners, analysis pixels). Returns the
     * input unchanged when the hull is degenerate or covers no pixel
     * centre, so a failed narrowing never loosens the gate.
     */
    fun withBoardExposure(
        measurement: Measurement,
        yPlane: ByteArray,
        width: Int,
        height: Int,
        points: List<List<Double>>,
    ): Measurement {
        val hull = convexHull(points.filter { it.size >= 2 && it[0].isFinite() && it[1].isFinite() })
        if (hull.size < 3) return measurement
        var dark = 0L
        var saturated = 0L
        var total = 0L
        val minV = hull.minOf { it.second }.let { kotlin.math.ceil(it - 0.5).toInt() }.coerceAtLeast(0)
        val maxV = hull.maxOf { it.second }.let { kotlin.math.floor(it - 0.5).toInt() }.coerceAtMost(height - 1)
        for (v in minV..maxV) {
            val y = v + 0.5
            var left = Double.POSITIVE_INFINITY
            var right = Double.NEGATIVE_INFINITY
            for (i in hull.indices) {
                val (x0, y0) = hull[i]
                val (x1, y1) = hull[(i + 1) % hull.size]
                if ((y0 <= y && y < y1) || (y1 <= y && y < y0)) {
                    val x = x0 + (y - y0) * (x1 - x0) / (y1 - y0)
                    if (x < left) left = x
                    if (x > right) right = x
                }
            }
            if (left > right) continue
            val minU = kotlin.math.ceil(left - 0.5).toInt().coerceAtLeast(0)
            val maxU = kotlin.math.floor(right - 0.5).toInt().coerceAtMost(width - 1)
            val row = v * width
            for (u in minU..maxU) {
                val sample = yPlane[row + u].toInt() and 0xFF
                if (sample <= DARK_LEVEL) dark++
                if (sample >= SATURATED_LEVEL) saturated++
                total++
            }
        }
        if (total == 0L) return measurement
        return measurement.copy(
            darkFraction = dark.toDouble() / total,
            saturatedFraction = saturated.toDouble() / total,
            exposureRoi = ROI_MARKER_HULL,
            fullFrameDarkFraction = measurement.fullFrameDarkFraction,
            fullFrameSaturatedFraction = measurement.fullFrameSaturatedFraction,
        )
    }

    /** Andrew's monotone chain; counter-clockwise, no collinear points. */
    private fun convexHull(points: List<List<Double>>): List<Pair<Double, Double>> {
        val sorted = points.map { it[0] to it[1] }.distinct()
            .sortedWith(compareBy({ it.first }, { it.second }))
        if (sorted.size < 3) return sorted
        fun cross(o: Pair<Double, Double>, a: Pair<Double, Double>, b: Pair<Double, Double>) =
            (a.first - o.first) * (b.second - o.second) - (a.second - o.second) * (b.first - o.first)
        val lower = mutableListOf<Pair<Double, Double>>()
        for (p in sorted) {
            while (lower.size >= 2 && cross(lower[lower.size - 2], lower.last(), p) <= 0) lower.removeAt(lower.size - 1)
            lower += p
        }
        val upper = mutableListOf<Pair<Double, Double>>()
        for (p in sorted.asReversed()) {
            while (upper.size >= 2 && cross(upper[upper.size - 2], upper.last(), p) <= 0) upper.removeAt(upper.size - 1)
            upper += p
        }
        return lower.dropLast(1) + upper.dropLast(1)
    }

    /**
     * Measures every pixel (stride 1). At 1280x720 this is ~1M
     * operations — well inside the analysis frame budget on a
     * bounded worker, and never on the UI thread.
     */
    fun measure(yPlane: ByteArray, width: Int, height: Int): Measurement {
        require(yPlane.size == width * height) { "need a tight gray buffer" }
        require(width >= 3 && height >= 3) { "need at least 3x3" }
        var dark = 0L
        var saturated = 0L
        val total = yPlane.size.toLong()
        for (byte in yPlane) {
            val sample = byte.toInt() and 0xFF
            if (sample <= DARK_LEVEL) dark++
            if (sample >= SATURATED_LEVEL) saturated++
        }
        // Laplacian variance over interior pixels (border excluded).
        var sum = 0.0
        var sumSq = 0.0
        var count = 0L
        for (v in 1 until height - 1) {
            for (u in 1 until width - 1) {
                val center = (yPlane[v * width + u].toInt() and 0xFF).toDouble()
                val laplacian = (yPlane[(v - 1) * width + u].toInt() and 0xFF) +
                    (yPlane[(v + 1) * width + u].toInt() and 0xFF) +
                    (yPlane[v * width + u - 1].toInt() and 0xFF) +
                    (yPlane[v * width + u + 1].toInt() and 0xFF) - 4.0 * center
                sum += laplacian
                sumSq += laplacian * laplacian
                count++
            }
        }
        val mean = sum / count
        val variance = sumSq / count - mean * mean
        return Measurement(
            darkFraction = dark.toDouble() / total,
            saturatedFraction = saturated.toDouble() / total,
            sharpness = variance.coerceAtLeast(0.0),
        )
    }
}
