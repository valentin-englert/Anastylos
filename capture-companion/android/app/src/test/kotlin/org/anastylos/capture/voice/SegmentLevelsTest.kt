package org.anastylos.capture.voice

import com.google.common.truth.Truth.assertThat
import org.junit.Test

class SegmentLevelsTest {
    @Test
    fun reportsPeakRmsAndClippingOfASegment() {
        val levels = SegmentLevels.of(shortArrayOf(0, 16384, -16384, 32767))
        assertThat(levels.peak).isWithin(0.001).of(1.0)
        assertThat(levels.rms).isWithin(0.01).of(0.612)
        assertThat(levels.clippedSamples).isEqualTo(1)
    }

    @Test
    fun aQuietClipShowsLowLevelsAndAnEmptyOneIsZero() {
        val quiet = SegmentLevels.of(ShortArray(1000) { if (it % 2 == 0) 100 else -100 })
        assertThat(quiet.peak).isLessThan(0.01)
        assertThat(quiet.clippedSamples).isEqualTo(0)
        assertThat(SegmentLevels.of(ShortArray(0))).isEqualTo(SegmentLevels(0.0, 0.0, 0))
    }
}
