package org.anastylos.capture.voice

import kotlin.math.abs

/**
 * Loudness of one speech segment for the local log: numbers only, never audio. Tells a clip that was
 * too quiet for the speech recogniser (low peak and RMS) from one that was clipped or empty of speech.
 */
data class SegmentLevels(val peak: Double, val rms: Double, val clippedSamples: Int) {
    companion object {
        private const val CLIP_LEVEL = 32_000

        fun of(samples: ShortArray): SegmentLevels {
            if (samples.isEmpty()) return SegmentLevels(0.0, 0.0, 0)
            var peak = 0
            var clipped = 0
            var sum = 0.0
            for (s in samples) {
                val magnitude = abs(s.toInt())
                if (magnitude > peak) peak = magnitude
                if (magnitude >= CLIP_LEVEL) clipped++
                val v = s / 32768.0
                sum += v * v
            }
            return SegmentLevels(peak / 32768.0, kotlin.math.sqrt(sum / samples.size), clipped)
        }
    }
}
