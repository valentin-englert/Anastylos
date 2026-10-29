package org.anastylos.capture.guidance

import org.anastylos.capture.guidance.GuidanceHypothesis1.DARK_LEVEL
import org.anastylos.capture.guidance.GuidanceHypothesis1.SATURATED_LEVEL

/**
 * Pure-JVM luma-frame quality measurement. Operates on a tight gray
 * buffer (see [YuvFrame]); full-frame ROI for Wave-2 v1 (documented
 * limitation: a board-filling view is assumed, board-ROI masking is
 * future work). Feeds [evaluateImageQuality]; see [ImageQualityInput].
 */
object FrameQuality {
    data class Measurement(
        /** Fraction of samples at or below [DARK_LEVEL]. */
        val darkFraction: Double,
        /** Fraction of samples at or above [SATURATED_LEVEL]. */
        val saturatedFraction: Double,
        /** Variance of the 4-neighbourhood Laplacian (sharpness). */
        val sharpness: Double,
    )

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
